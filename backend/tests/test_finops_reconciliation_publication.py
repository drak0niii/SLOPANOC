import asyncio
from decimal import Decimal
from copy import deepcopy
from datetime import timedelta
import pytest
from sqlalchemy import select,func
from backend.tests._m12_reconciliation import *
from backend.observability.finops.reconciliation_models import GenerationRow,InputRow,RegistryRow
from backend.observability.finops.reconciliation import reconcile
from backend.observability.finops.billing_contracts import SourceError

@pytest.mark.asyncio
async def test_real_sources_preserved_snapshot_corrections_idempotent_generation(system):
    s,g=await seed(system)
    original=deepcopy(g.body);assert g.body['result']['amounts'][0]['net']=='10'
    assert await system.worker.run_period(WINDOW,NOW)=='IDENTICAL'
    assert len(await system.repo.history(s.key))==1
    from backend.observability.finops.models import LedgerRow
    async with system.db.sessions() as db:
        ledger=(await db.scalars(select(LedgerRow))).one();ledger_before=ledger.metadata_safe.copy()
        billing_before=await db.get(__import__('backend.observability.finops.billing_models',fromlist=['PublicationRow']).PublicationRow,
            next(iter(g.body['source_vector'].values())))
        source_before=deepcopy(billing_before.controls)
    system.a.rows=(charge('10'),charge('-10',cost_type='adjustment'),charge('5'))
    assert await system.ingestion.ingest('DETAILED_BILLING',WINDOW)=='PUBLISHED';await system.worker.drain()
    assert await system.worker.run_period(WINDOW,NOW)=='PUBLISHED'
    history=await system.repo.history(s.key);assert len(history)==2
    assert history[0].body==original and history[-1].body['result']['amounts'][0]['net']=='5'
    async with system.db.sessions() as db:
        assert (await db.get(LedgerRow,ledger.event_id)).metadata_safe==ledger_before
        assert (await db.get(type(billing_before),billing_before.generation)).controls==source_before

@pytest.mark.asyncio
async def test_control_failure_cannot_publish_and_previous_survives(system,monkeypatch):
    s,g=await seed(system)
    import backend.observability.finops.reconciliation_worker as module
    real=module.reconcile
    def broken(*args,**kwargs):
        result=real(*args,**kwargs);result['classified']=[];return result
    monkeypatch.setattr(module,'reconcile',broken)
    assert await system.worker.run_period(WINDOW,NOW)=='FAILED'
    current=(await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0]
    assert current[0].generation==g.identity and current[0].error=='FINANCIAL_IMBALANCE'
    assert len(await system.repo.history(s.key))==1

@pytest.mark.asyncio
async def test_job_timeout_preserves_previous_and_recovery(system,monkeypatch):
    from backend.observability.finops.reconciliation_contracts import Policy
    s,g=await seed(system);original=system.worker.runtime_provider.snapshot
    async def delayed(*args):
        await asyncio.sleep(2)
        return await original(*args)
    system.worker.policy=Policy(job_seconds=1)
    monkeypatch.setattr(system.worker.runtime_provider,'snapshot',delayed)
    assert await system.worker.run_period(WINDOW,NOW)=='FAILED'
    assert (await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][0].generation==g.identity
    monkeypatch.setattr(system.worker.runtime_provider,'snapshot',original)
    assert await system.worker.run_period(WINDOW,NOW)=='IDENTICAL'
    assert len(await system.repo.history(s.key))==1

@pytest.mark.asyncio
async def test_summary_tampering_rollback_fencing_and_concurrency(system):
    s,g=await seed(system)
    key,old=await system.repo.reserve('development',SCOPE,WINDOW.partition)
    _,new=await system.repo.reserve('development',SCOPE,WINDOW.partition)
    assert await system.repo.publish(key,old,g.registry,g.body['input_ids'],g.body['result'],g.body['source_vector'])=='SUPERSEDED'
    altered=deepcopy(g.body['result']);altered['amounts'][0]['net']='999'
    with pytest.raises(SourceError):await system.repo.publish(key,new,g.registry,g.body['input_ids'],altered,g.body['source_vector'])
    assert (await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][0].generation==g.identity
    results=await asyncio.gather(*(system.worker.run_period(WINDOW,NOW) for _ in range(10)))
    assert 'FAILED' not in results and len(await system.repo.history(key))==1
    assert any(x=='IDENTICAL' for x in results)

@pytest.mark.asyncio
async def test_obsolete_source_vector_cannot_replace_current(system):
    s,g=await seed(system)
    system.a.rows=(charge('20'),);await system.ingestion.ingest('DETAILED_BILLING',WINDOW);await system.worker.drain()
    key,rev=await system.repo.reserve('development',SCOPE,WINDOW.partition)
    assert await system.repo.publish(key,rev,g.registry,g.body['input_ids'],g.body['result'],g.body['source_vector'])=='SUPERSEDED'
    assert (await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][0].generation==g.identity

@pytest.mark.asyncio
async def test_registry_immutable_versions_overlap_repository_boundary(system):
    await system.repo.registry(registry())
    m=registry().mappings[0];different=registry().model_copy(update={'runtime_scope':None})
    with pytest.raises(SourceError):await system.repo.registry(different)
    with pytest.raises(Exception):await system.repo.registry(registry().model_copy(update={'mappings':(m,m)}))
    revised=registry(version='map-v2');assert await system.repo.registry(revised)!=registry().identity

@pytest.mark.asyncio
async def test_missing_detail_and_optional_consumer_failure_cannot_corrupt_m11(system):
    await capture(system.runtime)
    system.ingestion.reconciliation_consumer=None
    assert await system.ingestion.ingest('DETAILED_BILLING',WINDOW)=='PUBLISHED'
    assert await system.worker.run_period(WINDOW,NOW)=='FAILED'
    current=(await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0]
    assert current[1] is None and current[0].error=='INPUT_DETAIL_UNAVAILABLE'
    def broken(*args):raise ValueError('SQL SECRET_SENTINEL')
    system.ingestion.reconciliation_consumer=broken
    assert await system.ingestion.ingest('DETAILED_BILLING',WINDOW)=='IDENTICAL'
    assert (await system.source.current('development','DETAILED_BILLING',SCOPE))[0][0].error is None

@pytest.mark.asyncio
async def test_reopen_and_mapping_change_replays_frozen_inputs(system):
    s,g=await seed(system)
    await system.db.close()
    assert (await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][1].identity==g.identity
    system.worker.registry=registry(version='map-v2')
    assert await system.worker.run_period(WINDOW,NOW)=='PUBLISHED'
    assert len(await system.repo.history(s.key))==2
    async with system.db.sessions() as db:
        inputs_=[await db.get(InputRow,x) for x in g.body['input_ids']];reg=await db.get(RegistryRow,g.registry)
    assert reconcile(inputs_[0].body,inputs_[1].body,inputs_[2].body,Registry.model_validate(reg.body),inputs_[2].body['quality'])==g.body['result']

@pytest.mark.asyncio
async def test_billing_materialization_preserves_signed_components_and_counts(system):
    system.a.rows=(charge('100',credits=[dict(amount='-100',id='c',type='DISCOUNT')]),charge(),charge())
    await system.ingestion.ingest('DETAILED_BILLING',WINDOW);await system.worker.drain()
    generation=(await system.source.current('development','DETAILED_BILLING',SCOPE))[0][1].generation
    row=await system.repo.billing('development',SCOPE,WINDOW.partition,generation)
    assert sum(c['count'] for c in row.body['components'])==4
    assert sum(Decimal(c['absolute']) for c in row.body['components'])==220

@pytest.mark.asyncio
async def test_crash_inside_publication_transaction_rolls_back_generation(system,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    s,g=await seed(system)
    system.a.rows=(charge('20'),);await system.ingestion.ingest('DETAILED_BILLING',WINDOW);await system.worker.drain()
    original=AsyncSession.execute
    async def fail(session,statement,*args,**kwargs):
        sql=str(statement)
        if sql.startswith('UPDATE finops_reconciliation_state SET') and 'generation=' in sql:
            raise OSError('SECRET_SQL_TOKEN')
        return await original(session,statement,*args,**kwargs)
    monkeypatch.setattr(AsyncSession,'execute',fail)
    assert await system.worker.run_period(WINDOW,NOW)=='FAILED'
    current=(await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0]
    assert current[0].generation==g.identity and len(await system.repo.history(s.key))==1

@pytest.mark.asyncio
async def test_input_vector_cannot_omit_billing_authority(system):
    s,g=await seed(system);key,revision=await system.repo.reserve('development',SCOPE,WINDOW.partition)
    with pytest.raises(SourceError,match='INPUT_DETAIL_UNAVAILABLE'):
        await system.repo.publish(key,revision,g.registry,g.body['input_ids'],g.body['result'],{})

@pytest.mark.asyncio
async def test_pricing_generation_change_does_not_change_actual_money(system):
    system.a.rows=(charge(consumption_model={'id':'DEFAULT'}),)
    system.worker.registry=registry().model_copy(update={'mappings':(registry().mappings[0].model_copy(update={'consumption_model':digest('DEFAULT')}),)})
    s,g=await seed(system)
    row=catalog(pricing_unit='tokens');row['list_price']['tiered_rates']=row['list_price']['tiered_rates'][:1]
    system.p.rows=(row,);await system.ingestion.ingest('PRICING_EXPORT',WINDOW)
    assert await system.worker.run_period(WINDOW,NOW)=='PUBLISHED'
    current=(await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][1]
    assert current.body['result']['amounts'][0]['net']==g.body['result']['amounts'][0]['net']=='10'
    assert Decimal(current.body['result']['amounts'][0]['catalog_reference'])==100000
    assert len(await system.repo.history(s.key))==2

@pytest.mark.asyncio
async def test_bounded_cycle_visits_retained_day_and_restart(system,monkeypatch):
    from backend.observability.finops.reconciliation_contracts import Policy
    visited=[]
    async def track(window,now):visited.append(window.partition)
    system.worker.policy=Policy(retention_days=4,overlap_days=2,cycle_partitions=2)
    monkeypatch.setattr(system.worker,'run_period',track)
    await system.worker.cycle(NOW)
    assert len(visited)==2 and WINDOW.partition in visited
    assert any(day<'2026-01-09' for day in visited)
