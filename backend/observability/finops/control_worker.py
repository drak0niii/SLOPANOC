"""Opt-in off-path bounded worker. No cloud, provider or business execution clients."""
import asyncio
from datetime import datetime,timezone
from .control_contracts import Policy
from .budget_registry import Registry
from .control_repository import calculate

class Worker:
    def __init__(self,repository,sources,registry=None,policy=None,metrics=None,environment=None,accounting=None):
        self.repository=repository;self.sources=sources;self.registry=registry or Registry();self.policy=policy or Policy();self.metrics=metrics
        self.environment=environment;self.accounting=accounting;self._task=None;self._stop=asyncio.Event();self._offset=0;self._last={}
    async def evaluate(self,env,binding,month,currency,issue,scope='TOTAL',kind='BUDGET'):
        from .billing_contracts import instant
        issue=instant(issue)
        key,revision=await self.repository.reserve(env,binding,month,kind,scope+':'+currency)
        try:
            async with asyncio.timeout(self.policy.job_seconds):
                inputs=await self.repository.adapter.load(env,binding,month,currency,issue,scope=scope,policy=self.policy)
                if not await self.repository.adapter.fresh(inputs,issue,self.policy):inputs=inputs.model_copy(update={'current_health':'STALE'})
                if self.accounting:
                    health=await self.accounting.repository.health(env)
                    if health['debt'] or health['pending'] or health['conflicts'] or health['updated_at'] is None or (issue-health['updated_at']).total_seconds()>300:
                        inputs=inputs.model_copy(update={'current_health':'STALE'})
                quality=await self.repository.quality(env,binding,scope,currency,issue,self.policy)
                result=calculate(inputs,self.registry,self.policy,kind,quality)
                status=await self.repository.publish(key,revision,inputs,self.registry,self.policy,kind,result)
                if self.metrics:
                    self.metrics.record({'BUDGET':'budget_evaluations','FORECAST':'forecast_generations','ANOMALY':'anomaly_evaluations'}[kind])
                    if kind=='BUDGET':
                        for item in result['items']:self.metrics.record('budget_state_'+item['state'].lower())
                    elif kind=='ANOMALY':
                        for item in result['results']:
                            self.metrics.record('anomaly_type_'+item['type'].lower());self.metrics.record('anomaly_severity_'+item['severity'].lower())
                    from .finops_iac import dry_run,RULES
                    matched=dry_run(result.get('items',[]) if kind=='BUDGET' else [],result.get('items',[]) if kind=='FORECAST' else [],result if kind=='ANOMALY' else {'results':[]})
                    for signal in matched:self.metrics.record(RULES[signal][0])
                    if kind=='FORECAST' and any(i['confidence']=='LOW' for i in result['items']):self.metrics.record('forecast_quality_unavailable')
                if self.metrics:self.metrics.record('publications' if status=='PUBLISHED' else 'superseded' if status=='SUPERSEDED' else 'identical_generations')
                return status
        except Exception:
            await self.repository.fail(key,revision)
            if self.metrics:self.metrics.record('publication_failures')
            return 'FAILED'
    async def cycle(self,issue=None):
        from .billing_contracts import instant
        issue=instant(issue or datetime.now(timezone.utc));adapter=self.sources.adapters.get('DETAILED_BILLING')
        if not adapter:return
        env=self.environment
        if not env:return
        month=issue.strftime('%Y-%m')
        from sqlalchemy import select
        from .control_models import PublicationRow
        # Prior published control months remain eligible for correction re-evaluation.
        async with self.repository.sessions() as session:
            previous=(await session.scalars(select(PublicationRow.month).where(PublicationRow.environment==env,
                PublicationRow.scope==adapter.scope).distinct().order_by(PublicationRow.month.desc()).limit(24))).all()
        months=sorted(set([month,*previous]),reverse=True)
        work=[]
        for target in months:
            budgets=self.registry.active(env,target,issue)
            jobs=[(b.scope,b.currency) for b in budgets if b.financial_binding==adapter.scope] or [('TOTAL','EUR')]
            kinds=['BUDGET','ANOMALY']+(['BACKTEST'] if target==month else [])+(['FORECAST'] if issue.hour>=self.policy.forecast_hour_utc else [])
            work.extend((target,scope,currency,kind) for scope,currency in sorted(set(jobs)) for kind in kinds)
        signature=await self.repository.scheduling_vector(env,adapter.scope,issue,self.policy)
        for i in range(min(len(work),self.policy.cycle_partitions)):
            target,scope,currency,kind=work[(self._offset+i)%len(work)]
            job=(target,scope,currency,kind)
            from datetime import timedelta
            marker=((issue.date(),(issue-timedelta(hours=self.policy.maturity_hours)).date()) if kind=='BUDGET' else issue.date(),signature,self.registry.model_dump_json(),self.policy.identity)
            if self._last.get(job)==marker:continue
            if kind=='BACKTEST':
                await self.backtest(env,adapter.scope,currency,scope,issue)
                self._last[job]=marker
                continue
            status=await self.evaluate(env,adapter.scope,target,currency,issue,scope,kind)
            if status in ('PUBLISHED','IDENTICAL'):self._last[job]=marker
        self._offset=(self._offset+self.policy.cycle_partitions)%len(work)
    async def backtest(self,env,binding,currency,scope,issue):
        from .forecast_backtesting import walk_forward
        from .control_contracts import month_bounds
        from datetime import timedelta
        month=issue.strftime('%Y-%m');start,_=month_bounds(month);origins=[]
        # Prior two months, three issue horizons each, strictly earlier known truth.
        for _ in range(2):
            end=start;start,_=month_bounds((end-timedelta(days=1)).strftime('%Y-%m'))
            origins.extend(datetime(start.year,start.month,d,6,tzinfo=timezone.utc) for d in (10,17,24))
        async with asyncio.timeout(self.policy.job_seconds):
            experiment=await walk_forward(self.repository.adapter,origins,issue,env,binding,currency,scope,self.policy)
            for score in experiment['scores']:await self.repository.save_backtest(env,binding,score,issue)
    def start(self):
        async def run():
            while not self._stop.is_set():
                try:await self.cycle()
                except Exception:
                    if self.metrics:self.metrics.record('detector_failures')
                try:await asyncio.wait_for(self._stop.wait(),self.policy.cadence_seconds)
                except asyncio.TimeoutError:pass
        self._task=asyncio.create_task(run())
    async def close(self):
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:await self._task
            except asyncio.CancelledError:pass
