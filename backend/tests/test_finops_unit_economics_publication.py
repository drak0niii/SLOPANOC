from copy import deepcopy
import pytest
from sqlalchemy import select
from backend.tests._m13_unit_economics import *
from backend.observability.finops.unit_economics_models import UnitEconomicsBase,GenerationRow,PublicationRow
from backend.observability.finops.unit_economics_repository import Repository,turn_scope
from backend.observability.finops.unit_economics_worker import Worker
from backend.observability.finops.unit_denominators import ModelProvider,utcnow
from backend.observability.finops.unit_economics import calculate
from backend.observability.finops.unit_registry import Registry as Units
from backend.observability.finops.billing_contracts import SourceError

async def setup(system):
    await seed(system)
    async with system.db.engine.begin() as c:await c.run_sync(UnitEconomicsBase.metadata.create_all)
    repo=Repository(system.db.sessions)
    worker=Worker(repo,None,ModelProvider(system.db.sessions,'development'),'development',SCOPE)
    return repo,worker

@pytest.mark.asyncio
async def test_publication_replay_restart_and_m12_correction_preserve_history(system):
    repo,worker=await setup(system)
    assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    first=(await repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][1]
    assert await worker.run_period(WINDOW,utcnow())=='IDENTICAL'
    restarted=Worker(Repository(system.db.sessions),None,ModelProvider(system.db.sessions,'development'),'development',SCOPE)
    assert await restarted.run_period(WINDOW,utcnow())=='IDENTICAL'
    system.a.rows=(charge('20'),)
    assert await system.ingestion.ingest('DETAILED_BILLING',WINDOW)=='PUBLISHED'
    await system.worker.drain();assert await system.worker.run_period(WINDOW,NOW)=='PUBLISHED'
    assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    async with system.db.sessions() as s:
        rows=(await s.scalars(select(GenerationRow))).all();assert len(rows)==2
        assert (await s.get(GenerationRow,first.identity)).body==first.body

@pytest.mark.asyncio
async def test_tampered_result_and_old_worker_cannot_change_current(system):
    repo,worker=await setup(system);assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    original=(await repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][1]
    f=await repo.financial('development',SCOPE,WINDOW.partition)
    _,snap=await repo.snapshot('development',SCOPE,WINDOW.partition,'M10')
    regs=await repo.registries(Units(),worker.drivers,SCOPE)
    result=calculate(f['body']['result'],f['billing'],f['runtime'],f['registry'],[snap],worker.drivers)
    key,rev=await repo.reserve('development',SCOPE,WINDOW.partition)
    poisoned=deepcopy(result);poisoned['results'][0]['direct_amount']='999'
    with pytest.raises(SourceError):await repo.publish(key,rev,f['generation'],{'M10':snap.identity},regs,poisoned)
    _,later=await repo.reserve('development',SCOPE,WINDOW.partition)
    assert await repo.publish(key,rev,f['generation'],{'M10':snap.identity},regs,result)=='SUPERSEDED'
    assert (await repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][1].identity==original.identity

@pytest.mark.asyncio
async def test_concurrent_reservations_identical_generation_one_current(system):
    import asyncio
    repo,worker=await setup(system)
    # Two independent workers compete; CAS makes the stale reservation noncurrent.
    other=Worker(Repository(system.db.sessions),None,ModelProvider(system.db.sessions,'development'),'development',SCOPE)
    statuses=await asyncio.gather(worker.run_period(WINDOW,utcnow()),other.run_period(WINDOW,utcnow()))
    assert 'PUBLISHED' in statuses and all(s in ('PUBLISHED','IDENTICAL','SUPERSEDED') for s in statuses)
    async with system.db.sessions() as s:
        assert len((await s.scalars(select(GenerationRow))).all())==1

@pytest.mark.asyncio
async def test_snapshot_job_does_not_wait_for_billing_source(system):
    async with system.db.engine.begin() as c:await c.run_sync(UnitEconomicsBase.metadata.create_all)
    snap=economics_inputs()['snapshots'][0]
    class Source:
        async def snapshot(self,window,now):return snap.model_copy(update={'period':window.partition})
    repo=Repository(system.db.sessions);worker=Worker(repo,Source(),None,'development')
    await worker.cycle(NOW)
    _,stored=await repo.snapshot('development',turn_scope('development'),WINDOW.partition,'M9')
    assert stored==snap

@pytest.mark.asyncio
async def test_atomic_crash_after_insert_rolls_back_and_preserves_current(system,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    repo,worker=await setup(system)
    assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    prior=(await repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][1]
    f=await repo.financial('development',SCOPE,WINDOW.partition)
    _,snap=await repo.snapshot('development',SCOPE,WINDOW.partition,'M10')
    changed=snap.model_copy(update={'source_digest':'e'*64})
    mk,mr=await repo.reserve('development',SCOPE,WINDOW.partition,'M10')
    await repo.save_snapshot(mk,mr,changed)
    registries=await repo.registries(Units(),worker.drivers,SCOPE)
    result=calculate(f['body']['result'],f['billing'],f['runtime'],f['registry'],[changed],worker.drivers)
    key,rev=await repo.reserve('development',SCOPE,WINDOW.partition)
    execute=AsyncSession.execute
    async def crash(s,statement,*args,**kwargs):
        if statement.is_update and statement.table.name==PublicationRow.__tablename__ and 'generation' in str(statement):
            raise RuntimeError('isolated publication crash')
        return await execute(s,statement,*args,**kwargs)
    with monkeypatch.context() as m:
        m.setattr(AsyncSession,'execute',crash)
        with pytest.raises(RuntimeError):await repo.publish(key,rev,f['generation'],{'M10':changed.identity},registries,result)
    async with system.db.sessions() as s:
        assert len((await s.scalars(select(GenerationRow))).all())==1
    assert (await repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][1].identity==prior.identity

@pytest.mark.asyncio
async def test_m11_source_moves_before_publication_old_financial_vector_rejected(system):
    repo,worker=await setup(system)
    assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    f=await repo.financial('development',SCOPE,WINDOW.partition)
    _,snap=await repo.snapshot('development',SCOPE,WINDOW.partition,'M10')
    registries=await repo.registries(Units(),worker.drivers,SCOPE)
    result=calculate(f['body']['result'],f['billing'],f['runtime'],f['registry'],[snap],worker.drivers)
    key,rev=await repo.reserve('development',SCOPE,WINDOW.partition)
    system.a.rows=(charge('30'),)
    assert await system.ingestion.ingest('DETAILED_BILLING',WINDOW)=='PUBLISHED'
    assert await repo.publish(key,rev,f['generation'],{'M10':snap.identity},registries,result)=='SUPERSEDED'

@pytest.mark.asyncio
async def test_source_tables_identical_after_success_and_failure(system,monkeypatch):
    from backend.observability.finops.models import FinOpsBase
    from backend.observability.finops.billing_models import BillingBase
    from backend.observability.finops.reconciliation_models import ReconciliationBase
    repo,worker=await setup(system)
    async def source_rows():
        out={}
        async with system.db.sessions() as s:
            for base in (FinOpsBase,BillingBase,ReconciliationBase):
                for table in base.metadata.sorted_tables:
                    out[table.name]=[dict(row) for row in (await s.execute(select(table))).mappings()]
        return out
    before=await source_rows()
    assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    assert await source_rows()==before
    async def fail(*args,**kwargs):raise SourceError('FINANCIAL_IMBALANCE')
    monkeypatch.setattr(repo,'publish',fail)
    assert await worker.run_period(WINDOW,utcnow())=='FAILED'
    assert await source_rows()==before
