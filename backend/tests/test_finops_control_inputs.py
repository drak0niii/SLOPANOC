import pytest
from backend.tests._m14_controls import *

@pytest.mark.asyncio
async def test_point_in_time_later_rows_and_october_correction_cannot_rewrite(controls):
    for i in range(1,10):await financial(controls,date(2026,10,i))
    before=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE)
    assert sum(d.eligible for d in before.days if d.day.month==10)==7
    assert next(d for d in before.days if d.day==date(2026,10,10)).state=='OPEN'
    await financial(controls,date(2026,10,5),'300',known=ISSUE+timedelta(days=5),correction=1)
    after=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE)
    assert before==after
    newer=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE+timedelta(days=6))
    assert next(d for d in newer.days if d.day==date(2026,10,5)).values['TOTAL:EUR']=='300'
    assert not await controls.adapter.fresh(before,ISSUE+timedelta(days=6))

@pytest.mark.asyncio
async def test_manifest_knowledge_clock_blocks_false_old_generation(controls):
    gid=await financial(controls,date(2026,10,1),known=ISSUE+timedelta(days=1))
    async with controls.db.sessions() as s,s.begin():(await s.get(GenerationRow,gid)).published_at=ISSUE-timedelta(days=1)
    result=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE)
    assert next(d for d in result.days if d.day==date(2026,10,1)).state=='MISSING'

@pytest.mark.asyncio
async def test_m13_owning_publication_clock_coverage_and_population(controls):
    from backend.observability.finops.unit_economics_models import DriverRow,DefinitionRow,GenerationRow as UnitGeneration
    from backend.observability.finops.unit_drivers import Registry as Drivers
    from backend.observability.finops.unit_registry import Registry as Definitions
    gid=await financial(controls,date(2026,10,1))
    drivers=Drivers(environment='development',financial_scope=BINDING);definitions=Definitions();uid=digest('future-m13')
    view=dict(unit_id='PROVIDER_ATTEMPT',dimension='ALL',currency='EUR',state='AVAILABLE',direct_state='AVAILABLE',financial_freshness='FRESH',
        unit_count=100,direct_cost_per_unit='1',coverage={'numerator_scope':'1.000000000000','denominator':'1.000000000000'})
    async with controls.db.sessions() as s,s.begin():
        s.add(DriverRow(identity=drivers.identity,environment='development',scope=BINDING,version=drivers.version,body=drivers.model_dump(mode='json')))
        s.add(DefinitionRow(identity=definitions.identity,version=definitions.version,body=definitions.model_dump(mode='json')));await s.flush()
        s.add(UnitGeneration(identity=uid,definitions=definitions.identity,drivers=drivers.identity,reconciliation=gid,body={'result':{'results':[view]}},published_at=ISSUE+timedelta(days=5)))
    before=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE)
    assert all(not d.units for d in before.days)
    after=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE+timedelta(days=6))
    assert next(d for d in after.days if d.day==date(2026,10,1)).units['PROVIDER_ATTEMPT:ALL:EUR']=='1'
    # A partial later unit generation cannot authorize an apparent unit improvement.
    async with controls.db.sessions() as s,s.begin():
        s.add(UnitGeneration(identity=digest('partial-m13'),definitions=definitions.identity,drivers=drivers.identity,reconciliation=gid,
            body={'result':{'results':[dict(view,state='PARTIAL',direct_cost_per_unit='.4')]}},published_at=ISSUE+timedelta(days=7)))
    partial=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE+timedelta(days=8))
    assert all(not d.units for d in partial.days)
    assert before==await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE)

@pytest.mark.asyncio
async def test_actual_m12_category_is_not_full_observability_without_attestation(tmp_path):
    from backend.tests._m12_reconciliation import system as system_fixture,seed as source_seed
    # Reuse real predecessor fixtures instead of deriving money from runtime quantities.
    from backend.observability.finops.unit_economics_models import UnitEconomicsBase
    # Fixture generator is explicitly isolated and owns its cleanup.
    generator=system_fixture.__wrapped__(tmp_path)
    system=await generator.__anext__()
    try:
        state,g=await source_seed(system)
        async with system.db.engine.begin() as c:await c.run_sync(UnitEconomicsBase.metadata.create_all)
        async with system.db.sessions() as s:b=await s.get(InputRow,g.billing_input)
        bucket=b.body['buckets'][0];selector=digest([bucket[k] for k in ('service','sku','project','region')])
        issue=datetime.now(timezone.utc)+timedelta(hours=1)
        adapter=Adapter(system.db.sessions)
        default=await adapter.load('development',state.scope,'2026-01','EUR',issue)
        assert all('OBSERVABILITY:EUR' not in d.values for d in default.days)
        policy=Policy(observability_binding=state.scope,observability_ownership=(selector,),observability_population_complete=True)
        approved=await adapter.load('development',state.scope,'2026-01','EUR',issue,policy=policy)
        assert next(d for d in approved.days if d.generation==g.identity).values['OBSERVABILITY:EUR']=='10'
    finally:
        try:await generator.__anext__()
        except StopAsyncIteration:pass
