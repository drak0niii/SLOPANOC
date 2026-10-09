"""Bounded source-independent M12 worker; no model or BigQuery execution."""
import asyncio
from datetime import datetime,timezone,timedelta
from .billing_contracts import Window,SourceError,digest
from .reconciliation_inputs import billing_input
from .reconciliation_mapping import Registry
from .reconciliation_contracts import Policy
from .reconciliation import reconcile

class Worker:
    def __init__(self,repository,runtime_provider,financial_sources,registry=None,policy=None,metrics=None):
        self.repository=repository;self.runtime_provider=runtime_provider;self.sources=financial_sources
        self.policy=policy or Policy();self.registry=registry or Registry();self.metrics=metrics
        self.task=None;self.materializers=set();self.lock=asyncio.Lock()
    def signal(self,name,value=1):
        if self.metrics:self.metrics.record(name,value)
    def offer(self,environment,adapter,extraction,projection,mapping):
        if adapter.source!='DETAILED_BILLING':return
        if len(self.materializers)>=self.policy.cycle_partitions:
            self.signal('failures');return
        # M11 has already published. M12 failure cannot mark that source failed.
        async def materialize():
            key=revision=None
            try:
                async with asyncio.timeout(self.policy.job_seconds):
                    key,revision=await self.repository.reserve(environment,adapter.scope,extraction.window.partition)
                    from .billing_models import PublicationRow
                    generation=digest([digest([environment,adapter.source,adapter.scope,extraction.window.partition]),projection.fingerprint,extraction.schema_version])
                    async with self.repository.sessions() as s:manifest=await s.get(PublicationRow,generation)
                    if manifest is None:raise SourceError('INPUT_DETAIL_UNAVAILABLE')
                    body=await asyncio.to_thread(billing_input,extraction,manifest,mapping,adapter.scope,environment,self.policy)
                    await self.repository.input(body,adapter.scope)
            except asyncio.CancelledError:raise
            except Exception as exc:
                if key:await self.repository.fail(key,revision,exc.code if isinstance(exc,SourceError) else 'PUBLICATION_FAILED')
                self.signal('failures')
        task=asyncio.create_task(materialize(),name='finops-reconciliation-input')
        self.materializers.add(task);task.add_done_callback(self.materializers.discard)
    async def drain(self):
        if self.materializers:await asyncio.gather(*tuple(self.materializers))
    async def run_period(self,window,now=None):
        import time
        began=time.monotonic();now=now or datetime.now(timezone.utc)
        adapter=self.sources.adapters.get('DETAILED_BILLING')
        if adapter is None:return 'SOURCE_UNAVAILABLE'
        env=self.registry.environment;scope=adapter.scope;key,revision=await self.repository.reserve(env,scope,window.partition)
        self.signal('runs')
        try:
            async with asyncio.timeout(self.policy.job_seconds):
                if self.runtime_provider is None:raise SourceError('SOURCE_UNAVAILABLE')
                runtime=await self.runtime_provider.snapshot(env,window)
                rows=await self.sources.rows(env,'DETAILED_BILLING',False,window.start.date(),window.end.date())
                if len(rows)!=1 or rows[0][1] is None:raise SourceError('SOURCE_UNAVAILABLE')
                state,manifest,_=rows[0];vector={state.key:manifest.generation}
                b=await self.repository.billing(env,scope,window.partition,manifest.generation)
                if b is None:raise SourceError('INPUT_DETAIL_UNAVAILABLE')
                prices=await self.sources.rows(env,'PRICING_EXPORT',True,window.start.date(),window.end.date())
                pricing_health=self.sources.health('PRICING_EXPORT',prices,now)
                billing_health=self.sources.health('DETAILED_BILLING',rows,now)
                tariffs=[];price_ids=[]
                for ps,pm,cache in prices:
                    if pm and cache:
                        vector[ps.key]=pm.generation;price_ids.append(pm.generation);tariffs.extend(cache.prices)
                price_adapter=self.sources.adapters.get('PRICING_EXPORT')
                billing_binding=getattr(adapter,'binding',None);pricing_binding=getattr(price_adapter,'binding',None)
                price_scope_proven=bool(price_adapter and ((adapter.mode==price_adapter.mode=='TEST_FIXTURE' and adapter.scope==price_adapter.scope) or
                    (billing_binding and pricing_binding and billing_binding.billing_account==pricing_binding.billing_account)))
                if not price_scope_proven:tariffs=[]
                quality=dict(billing=billing_health.state,pricing=pricing_health.state if price_scope_proven else 'UNAVAILABLE')
                pricing=dict(kind='PRICING',environment=env,period=window.partition,prices=tariffs,
                    generations=sorted(price_ids),quality=quality)
                registry=Registry.model_validate(self.registry.model_dump())
                rid=await self.repository.registry(registry)
                ids=[await self.repository.input(runtime,scope),b.identity,await self.repository.input(pricing,scope)]
                result=await asyncio.to_thread(reconcile,runtime,b.body,pricing,registry,quality,self.policy)
                published=await self.repository.publish(key,revision,rid,ids,result,vector)
                self.signal('identical_generations' if published=='IDENTICAL' else 'publications' if published=='PUBLISHED' else 'superseded')
                for name,field in [('mapping_coverage','mapping_amount'),('runtime_coverage','ledger_completeness'),('allocation_coverage','allocation')]:
                    value=result['coverage'][field]
                    if value is not None:self.signal(name,float(value))
                self.signal('source_stale',int(billing_health.state!='FRESH'))
                self.signal('accounting_debt',int(bool(runtime['quality']['debt'] or runtime['quality'].get('global_debt'))))
                self.signal('unresolved_periods',int(result['status']!='RECONCILED'))
                for amount in result['amounts']:
                    # Ratios are per-currency; do not export a mixed total. Only
                    # native EUR gets the fixed bounded ratio instrument family.
                    if amount['currency']=='EUR':
                        for name,value in amount['ratios'].items():
                            if value is not None:self.signal(name+'_ratio',float(value))
                self.signal('lag_seconds',max(0,(now-window.end).total_seconds()))
                return published
        except asyncio.CancelledError:raise
        except Exception as exc:
            error=exc.code if isinstance(exc,SourceError) else 'PUBLICATION_FAILED'
            await self.repository.fail(key,revision,error);self.signal('failures')
            if error=='FINANCIAL_IMBALANCE':self.signal('control_failures')
            return 'FAILED'
        finally:self.signal('duration_seconds',time.monotonic()-began)
    async def cycle(self,now=None):
        now=now or datetime.now(timezone.utc);today=now.replace(hour=0,minute=0,second=0,microsecond=0)
        adapter=self.sources.adapters.get('DETAILED_BILLING')
        if not adapter:return
        async with self.lock:
            # current() is bounded to32 for HTTP; worker scheduling uses a separate
            # bounded731-row state read so old periods cannot be starved by restart.
            from sqlalchemy import select
            from .reconciliation_models import StateRow
            async with self.repository.sessions() as s:
                states=(await s.scalars(select(StateRow).where(StateRow.environment==self.registry.environment,
                    StateRow.scope==adapter.scope,StateRow.period>=(today-timedelta(days=self.policy.retention_days-1)).date().isoformat())
                    .order_by(StateRow.period.desc()).limit(self.policy.retention_days))).all()
            checked={r.period:r.last_attempt for r in states}
            days=[today-timedelta(days=i) for i in range(self.policy.retention_days)]
            due=[d for d in days if checked.get(d.date().isoformat()) is None or
                 (now-checked[d.date().isoformat()]).total_seconds()>=self.policy.cadence_seconds]
            rank=lambda d:checked.get(d.date().isoformat()) or datetime.min.replace(tzinfo=timezone.utc)
            recent=sorted((d for d in due if (today-d).days<self.policy.overlap_days),key=rank)
            old=sorted((d for d in due if (today-d).days>=self.policy.overlap_days),key=rank)
            chosen=recent[:max(1,self.policy.cycle_partitions//2)]+old[:self.policy.cycle_partitions//2]
            chosen+=( [d for d in recent+old if d not in chosen])[:self.policy.cycle_partitions-len(chosen)]
            for day in chosen:await self.run_period(Window(day,day+timedelta(days=1)),now)
    def start(self):
        async def drive():
            while True:
                try:await self.cycle()
                except asyncio.CancelledError:raise
                except Exception:self.signal('failures')
                await asyncio.sleep(self.policy.cadence_seconds)
        self.task=asyncio.create_task(drive(),name='finops-reconciliation')
    async def close(self):
        tasks=tuple(self.materializers)+( (self.task,) if self.task else ())
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
