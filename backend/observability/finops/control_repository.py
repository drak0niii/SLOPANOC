"""Deterministic recomputation, immutable inputs/results and atomic source-fenced CAS."""
from datetime import datetime,timezone
from sqlalchemy import select,update
from .repository import insert_for
from .billing_contracts import digest,SourceError
from .control_contracts import Inputs,Policy,bounded
from .budget_registry import Registry
from .control_models import DefinitionRow,InputRow,GenerationRow,PublicationRow,TransitionRow,BacktestRow
from .reconciliation_models import StateRow as FinancialState
from .billing_models import StateRow as SourceState
from .unit_economics_models import PublicationRow as UnitState
from .control_inputs import Adapter

def now():return datetime.now(timezone.utc)
def key_for(env,scope,month,kind,selection='TOTAL:EUR'):return digest([env,scope,month,kind,selection])

def calculate(inputs,registry,policy,kind,quality=None):
    from .budgets import evaluate
    from .forecasting import forecast
    from .anomalies import evaluate as detect
    budgets=registry.active(inputs.environment,inputs.month,inputs.issue)
    compatible=[b for b in budgets if b.financial_binding==inputs.binding and b.scope==inputs.scope and b.currency==inputs.currency]
    if kind=='BUDGET':return dict(state='AVAILABLE' if budgets else 'NOT_CONFIGURED',items=[evaluate(b,inputs,policy) for b in budgets if b.scope==inputs.scope and b.currency==inputs.currency])
    if kind=='FORECAST':return dict(state='AVAILABLE',items=[forecast(inputs,b,policy,quality) for b in compatible] or [forecast(inputs,policy=policy,quality=quality)])
    if kind=='ANOMALY':return detect(inputs,policy)
    raise SourceError('PUBLICATION_FAILED')

class Repository:
    def __init__(self,sessions,adapter=None):self.sessions=sessions;self.adapter=adapter or Adapter(sessions)
    async def reserve(self,env,scope,month,kind,selection='TOTAL:EUR'):
        key=key_for(env,scope,month,kind,selection)
        async with self.sessions() as s,s.begin():
            await s.execute(insert_for(s,PublicationRow).values(key=key,environment=env,scope=scope,month=month,kind=kind,selection=selection,revision=0).on_conflict_do_nothing())
            rev=await s.scalar(update(PublicationRow).where(PublicationRow.key==key).values(revision=PublicationRow.revision+1,last_attempt=now()).returning(PublicationRow.revision))
        return key,rev
    async def fail(self,key,revision):
        async with self.sessions() as s,s.begin():
            await s.execute(update(PublicationRow).where(PublicationRow.key==key,PublicationRow.revision==revision).values(error='PUBLICATION_FAILED'))
    async def publish(self,key,revision,inputs,registry,policy,kind,result):
        inputs=Inputs.model_validate(inputs.model_dump());registry=Registry.model_validate(registry.model_dump());policy=Policy.model_validate(policy.model_dump())
        bounded(inputs.model_dump(mode='json'),policy);bounded(result,policy)
        # Re-read immutable links at identical knowledge cutoff. Caller cannot invent spend.
        expected_inputs=await self.adapter.load(inputs.environment,inputs.binding,inputs.month,inputs.currency,inputs.issue,scope=inputs.scope,policy=policy)
        if expected_inputs.model_copy(update={'current_health':inputs.current_health})!=inputs:raise SourceError('FINANCIAL_IMBALANCE')
        quality=await self.quality(inputs.environment,inputs.binding,inputs.scope,inputs.currency,inputs.issue,policy)
        if calculate(inputs,registry,policy,kind,quality)!=result:raise SourceError('FINANCIAL_IMBALANCE')
        input_body=dict(inputs=inputs.model_dump(mode='json'),policy=policy.model_dump(mode='json'),quality=quality)
        input_id=digest(input_body);definition_id=digest(registry.model_dump(mode='json'))
        body=dict(result=result,policy_version=policy.version,policy_digest=policy.identity,forecast_algorithm='eligible-run-rate-v1/weekday-median-v1',anomaly_algorithm='robust-daily-v1',currency=inputs.currency)
        identity=digest([input_id,definition_id,kind,body])
        async with self.sessions() as s,s.begin():
            fence=await s.execute(update(PublicationRow).where(PublicationRow.key==key,PublicationRow.revision==revision).values(revision=revision))
            if fence.rowcount!=1:return 'SUPERSEDED'
            head=await s.get(PublicationRow,key)
            if (head.environment,head.scope,head.month,head.kind,head.selection)!=(inputs.environment,inputs.binding,inputs.month,kind,inputs.scope+':'+inputs.currency):raise SourceError('SCOPE_UNPROVEN')
            # Stable locking order across all writers; these locks never modify source truth.
            for model,vector in ((UnitState,inputs.unit_fences),(FinancialState,inputs.fences),(SourceState,inputs.source_vector)):
                for source_key,gid in sorted(vector.items()):
                    row=await s.get(model,source_key,with_for_update=True)
                    if not row or row.generation!=gid or row.error:return 'SUPERSEDED'
            previous=head.generation
            await s.execute(insert_for(s,DefinitionRow).values(identity=definition_id,version=registry.version,body=registry.model_dump(mode='json'),recorded_at=now()).on_conflict_do_nothing())
            registered=await s.scalar(select(DefinitionRow).where(DefinitionRow.version==registry.version))
            if registered.identity!=definition_id:raise SourceError('MAPPING_INVALID')
            await s.execute(insert_for(s,InputRow).values(identity=input_id,environment=inputs.environment,scope=inputs.binding,month=inputs.month,cutoff=inputs.cutoff,body=input_body).on_conflict_do_nothing())
            await s.execute(insert_for(s,GenerationRow).values(identity=identity,definitions=definition_id,inputs=input_id,environment=inputs.environment,scope=inputs.binding,month=inputs.month,kind=kind,
                issue=inputs.issue,body=body,published_at=now()).on_conflict_do_nothing())
            if (await s.get(InputRow,input_id)).body!=input_body or (await s.get(GenerationRow,identity)).body!=body:raise SourceError('FINANCIAL_IMBALANCE')
            if kind=='ANOMALY' and previous!=identity:
                old=await s.get(GenerationRow,previous) if previous else None
                old_results={(r['type'],r['dimension'],r['day']):r for r in old.body['result']['results']} if old else {}
                for r in result['results']:
                    prior=old_results.get((r['type'],r['dimension'],r['day']))
                    anomaly=digest([inputs.environment,inputs.binding,inputs.currency,r['type'],r['dimension'],r['day'],'robust-daily-v1'])
                    last=await s.scalar(select(TransitionRow).where(TransitionRow.anomaly==anomaly).order_by(TransitionRow.at.desc(),TransitionRow.identity.desc()).limit(1).with_for_update())
                    state=r['state'] if r['state'] in ('ACTIVE','OBSERVED') else 'RESOLVED' if last and last.state in ('ACTIVE','OBSERVED') and r['confidence']=='HIGH' else None
                    if state is None:continue
                    tid=digest([anomaly,identity,state])
                    await s.execute(insert_for(s,TransitionRow).values(identity=tid,anomaly=anomaly,generation=identity,previous=previous,state=state,at=inputs.issue).on_conflict_do_nothing())
            await s.execute(update(PublicationRow).where(PublicationRow.key==key,PublicationRow.revision==revision).values(generation=identity,error=None,last_success=now()))
            return 'IDENTICAL' if previous==identity else 'PUBLISHED'
    async def current(self,env,scope,kind,month=None):
        async with self.sessions() as s:
            q=select(PublicationRow,GenerationRow).outerjoin(GenerationRow,PublicationRow.generation==GenerationRow.identity).where(PublicationRow.environment==env,PublicationRow.scope==scope,PublicationRow.kind==kind)
            if month:q=q.where(PublicationRow.month==month)
            return list((await s.execute(q.order_by(PublicationRow.month.desc()).limit(10000))).all())
    async def history(self,env,scope,kind,month=None,limit=101):
        async with self.sessions() as s:
            q=select(GenerationRow).where(GenerationRow.environment==env,GenerationRow.scope==scope,GenerationRow.kind==kind)
            if month:q=q.where(GenerationRow.month==month)
            return list((await s.scalars(q.order_by(GenerationRow.issue.desc(),GenerationRow.identity).limit(limit))).all())
    async def save_backtest(self,environment,scope,score,known_at):
        # Separate immutable corrected scoring truth; never updates original prediction.
        body=dict(score,known_at=known_at.isoformat());identity=digest(body)
        async with self.sessions() as s,s.begin():
            await s.execute(insert_for(s,BacktestRow).values(identity=identity,environment=environment,scope=scope,forecast=score['forecast_generation'],truth=digest(score['truth_vector']),
                scoring_version=score['scoring_version'],known_at=known_at,body=body).on_conflict_do_nothing())
        return identity

    async def quality(self,environment,binding,scope,currency,issue,policy):
        from .forecast_backtesting import metrics
        from .control_contracts import quotient
        from .billing_contracts import decimal
        async with self.sessions() as s:
            rows=(await s.scalars(select(BacktestRow).where(BacktestRow.environment==environment,BacktestRow.scope==binding,BacktestRow.known_at<=issue,
                BacktestRow.body['scope'].as_string()==scope,BacktestRow.body['currency'].as_string()==currency,BacktestRow.body['policy_id'].as_string()==policy.identity)
                .order_by(BacktestRow.known_at,BacktestRow.identity).limit(10000))).all()
        # Latest separately scored truth per origin, all known before forecast issue.
        folds={}
        for row in rows:
            b=row.body
            if b.get('scope')==scope and b.get('currency')==currency and b.get('actual') is not None and b.get('tier1') is not None and b.get('tier2') is not None:
                folds[b['issue']]=b
        if not folds:return None
        first=metrics([dict(b,prediction=b['tier1']) for b in folds.values()]);second=metrics([dict(b,prediction=b['tier2']) for b in folds.values()])
        improvement=quotient(decimal(first['wape'])-decimal(second['wape']),first['wape']) if first['wape'] is not None else None
        return dict(second,known_at=max(b['known_at'] for b in folds.values()),relative_improvement=improvement,same_folds=True)

    async def scheduling_vector(self,environment,binding,issue,policy):
        from datetime import timedelta
        start=(issue.date()-timedelta(days=policy.max_days)).isoformat()
        async with self.sessions() as s:
            financial=(await s.execute(select(FinancialState.key,FinancialState.generation,FinancialState.error).where(
                FinancialState.environment==environment,FinancialState.scope==binding,FinancialState.period>=start).order_by(FinancialState.key).limit(policy.max_features+1))).all()
            sources=(await s.execute(select(SourceState.key,SourceState.generation,SourceState.error,SourceState.last_success).where(
                SourceState.environment==environment,SourceState.partition>=start).order_by(SourceState.key).limit(policy.max_features+1))).all()
            units=(await s.execute(select(UnitState.key,UnitState.generation,UnitState.error).where(UnitState.environment==environment,
                UnitState.scope==binding,UnitState.kind=='ECONOMICS',UnitState.period>=start).order_by(UnitState.key).limit(policy.max_features+1))).all()
        if max(len(financial),len(sources),len(units))>policy.max_features:raise SourceError('BATCH_LIMIT')
        return digest([list(map(tuple,financial)),[(k,g,e,not t or (issue-t).total_seconds()>policy.current_source_stale_seconds) for k,g,e,t in sources],list(map(tuple,units))])

    async def active(self,environment,scope,month=None):
        from sqlalchemy import func
        # Latest lifecycle per opaque anomaly: retained active intervals cannot disappear.
        async with self.sessions() as s:
            q=select(TransitionRow.generation,TransitionRow.anomaly,TransitionRow.state,func.row_number().over(
                partition_by=TransitionRow.anomaly,order_by=(TransitionRow.at.desc(),TransitionRow.identity.desc())).label('rank')).join(
                    GenerationRow,TransitionRow.generation==GenerationRow.identity).where(GenerationRow.environment==environment,GenerationRow.scope==scope)
            if month:q=q.where(GenerationRow.month==month)
            latest=q.subquery()
            return list((await s.execute(select(GenerationRow,latest.c.anomaly,latest.c.state).join(latest,GenerationRow.identity==latest.c.generation)
                .where(latest.c.rank==1,latest.c.state.in_(('OBSERVED','ACTIVE'))).order_by(GenerationRow.issue.desc()).limit(1000))).all())
