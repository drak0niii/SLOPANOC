import asyncio
from copy import deepcopy
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from backend.tests._m14_controls import *
from backend.observability.finops.control_repository import calculate
from backend.observability.finops.control_models import GenerationRow as ControlGeneration,PublicationRow as ControlHead
from backend.observability.finops.billing_contracts import SourceError

async def setup(system):
    for i in range(1,8):await financial(system,date(2026,10,i))
    inputs=await system.adapter.load('development',BINDING,'2026-10','EUR',ISSUE)
    reg=Registry(budgets=(budget('4000'),));result=calculate(inputs,reg,POLICY,'FORECAST')
    return inputs,reg,result

@pytest.mark.asyncio
async def test_replay_concurrent_workers_history_and_sources_unchanged(controls):
    inp,reg,result=await setup(controls)
    async with controls.db.sessions() as s:before=[(g.identity,deepcopy(g.body)) for g in (await s.scalars(select(GenerationRow))).all()]
    key,rev=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
    assert await controls.repo.publish(key,rev,inp,reg,POLICY,'FORECAST',result)=='PUBLISHED'
    async def competing():
        key,rev=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
        return await controls.repo.publish(key,rev,inp,reg,POLICY,'FORECAST',result)
    statuses=await asyncio.gather(competing(),competing())
    assert all(s in ('IDENTICAL','SUPERSEDED') for s in statuses)
    async with controls.db.sessions() as s:
        assert len((await s.scalars(select(ControlGeneration))).all())==1
        assert before==[(g.identity,g.body) for g in (await s.scalars(select(GenerationRow))).all()]
    await financial(controls,date(2026,10,5),'300',known=ISSUE+timedelta(days=5),correction=1)
    new=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE+timedelta(days=6))
    key,rev=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
    assert await controls.repo.publish(key,rev,new,reg,POLICY,'FORECAST',calculate(new,reg,POLICY,'FORECAST'))=='PUBLISHED'
    history=await controls.repo.history('development',BINDING,'FORECAST')
    assert len(history)==2 and history[-1].body['result']==result

@pytest.mark.asyncio
async def test_tamper_old_worker_failure_crash_preserves_current(controls,monkeypatch):
    inp,reg,result=await setup(controls);key,rev=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
    assert await controls.repo.publish(key,rev,inp,reg,POLICY,'FORECAST',result)=='PUBLISHED'
    original=(await controls.repo.current('development',BINDING,'FORECAST'))[0][1].identity
    key,rev=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
    poison=deepcopy(result);poison['items'][0]['forecast']='999'
    with pytest.raises(SourceError):await controls.repo.publish(key,rev,inp,reg,POLICY,'FORECAST',poison)
    key,newrev=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
    assert await controls.repo.publish(key,rev,inp,reg,POLICY,'FORECAST',result)=='SUPERSEDED'
    later=inp.model_copy(update={'issue':ISSUE+timedelta(hours=1),'cutoff':ISSUE+timedelta(hours=1)})
    later=await controls.adapter.load('development',BINDING,'2026-10','EUR',later.issue)
    execute=AsyncSession.execute
    async def crash(session,stmt,*args,**kwargs):
        if stmt.is_update and stmt.table.name==ControlHead.__tablename__ and 'generation' in str(stmt):raise RuntimeError('fixture crash')
        return await execute(session,stmt,*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(AsyncSession,'execute',crash)
        with pytest.raises(RuntimeError):await controls.repo.publish(key,newrev,later,reg,POLICY,'FORECAST',calculate(later,reg,POLICY,'FORECAST'))
    assert (await controls.repo.current('development',BINDING,'FORECAST'))[0][1].identity==original
    assert len(await controls.repo.history('development',BINDING,'FORECAST'))==1
    await controls.repo.fail(key,newrev)
    assert (await controls.repo.current('development',BINDING,'FORECAST'))[0][0].error=='PUBLICATION_FAILED'

@pytest.mark.asyncio
async def test_correction_source_movement_fences_old_results(controls):
    inp,reg,result=await setup(controls);key,rev=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
    await financial(controls,date(2026,10,5),'300',known=ISSUE+timedelta(days=5))
    assert await controls.repo.publish(key,rev,inp,reg,POLICY,'FORECAST',result)=='SUPERSEDED'
    assert not await controls.repo.history('development',BINDING,'FORECAST')

@pytest.mark.asyncio
async def test_worker_budget_breach_and_anomaly_never_calls_external_hooks(controls):
    from backend.observability.finops.control_worker import Worker
    from backend.observability.finops.billing_service import FinancialSources
    from backend.observability.finops.billing_sources import BillingSourceAdapter
    inp,reg,result=await setup(controls)
    calls=dict(provider=0,routing=0,scaling=0,command=0,extract=0)
    class NoExternal(BillingSourceAdapter):
        async def extract(self,*a,**kw):calls['extract']+=1;raise AssertionError('External hook')
    w=Worker(controls.repo,FinancialSources(adapters=[NoExternal(scope='approved-synthetic-scope')]),Registry(budgets=(budget('100'),)),environment='development')
    assert await w.evaluate('development',BINDING,'2026-10','EUR',ISSUE,kind='BUDGET')=='PUBLISHED'
    assert await w.evaluate('development',BINDING,'2026-10','EUR',ISSUE,kind='FORECAST')=='PUBLISHED'
    assert await w.evaluate('development',BINDING,'2026-10','EUR',ISSUE,kind='ANOMALY')=='PUBLISHED'
    assert all(v==0 for v in calls.values())
    assert (await controls.repo.current('development',BINDING,'BUDGET'))[0][1].body['result']['items'][0]['state']=='EXCEEDED'

@pytest.mark.asyncio
async def test_immutable_anomalies_missing_never_resolves_and_correction_appends_resolution(controls):
    from backend.observability.finops.control_models import TransitionRow
    start=date(2026,8,1)
    for i in range((date(2026,10,8)-start).days):
        d=start+timedelta(days=i);await financial(controls,d,'300' if d in (date(2026,10,5),date(2026,10,6)) else '100')
    reg=Registry()
    inp=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE)
    result=calculate(inp,reg,POLICY,'ANOMALY');k,r=await controls.repo.reserve('development',BINDING,'2026-10','ANOMALY')
    assert await controls.repo.publish(k,r,inp,reg,POLICY,'ANOMALY',result)=='PUBLISHED'
    original=(await controls.repo.current('development',BINDING,'ANOMALY'))[0][1]
    await financial(controls,date(2026,10,6),'40',known=ISSUE+timedelta(days=5),coverage='.4',status='PARTIALLY_RECONCILED')
    new=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE+timedelta(days=6));k,r=await controls.repo.reserve('development',BINDING,'2026-10','ANOMALY')
    assert await controls.repo.publish(k,r,new,reg,POLICY,'ANOMALY',calculate(new,reg,POLICY,'ANOMALY'))=='PUBLISHED'
    active=await controls.repo.active('development',BINDING)
    assert any(g.identity==original.identity for g,aid,state in active)
    async with controls.db.sessions() as s:
        assert (await s.get(ControlGeneration,original.identity)).body==original.body
        assert not any(t.state=='RESOLVED' for t in (await s.scalars(select(TransitionRow))).all())
    await financial(controls,date(2026,10,6),'100',known=ISSUE+timedelta(days=7),correction=1)
    corrected=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE+timedelta(days=8));k,r=await controls.repo.reserve('development',BINDING,'2026-10','ANOMALY')
    assert await controls.repo.publish(k,r,corrected,reg,POLICY,'ANOMALY',calculate(corrected,reg,POLICY,'ANOMALY'))=='PUBLISHED'
    async with controls.db.sessions() as s:
        assert (await s.get(ControlGeneration,original.identity)).body==original.body
        assert any(t.state=='RESOLVED' for t in (await s.scalars(select(TransitionRow))).all())

@pytest.mark.asyncio
async def test_worker_cycle_bounded_fair_unchanged_skip_and_maturity_refresh(controls,monkeypatch):
    from backend.observability.finops.control_worker import Worker
    from backend.observability.finops.billing_service import FinancialSources
    from backend.observability.finops.billing_sources import BillingSourceAdapter
    await setup(controls)
    worker=Worker(controls.repo,FinancialSources(adapters=[BillingSourceAdapter(scope='approved-synthetic-scope')]),environment='development')
    calls=[]
    async def evaluate(*args,**kwargs):calls.append(('evaluate',args));return 'PUBLISHED'
    async def backtest(*args,**kwargs):calls.append(('backtest',args))
    monkeypatch.setattr(worker,'evaluate',evaluate);monkeypatch.setattr(worker,'backtest',backtest)
    issue=ISSUE+timedelta(hours=6)
    for _ in range(2):
        before=len(calls);await worker.cycle(issue);assert len(calls)-before<=2
    assert len(calls)==4
    before=len(calls);await worker.cycle(issue);assert len(calls)==before
    await worker.cycle(issue+timedelta(days=1));await worker.cycle(issue+timedelta(days=1))
    assert len(calls)>before
