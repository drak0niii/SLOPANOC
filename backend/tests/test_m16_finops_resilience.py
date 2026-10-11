"""Stress existing atomic financial publication controls, never dispatch business work."""
from copy import deepcopy
import asyncio
import pytest
from backend.tests._m12_reconciliation import system,SCOPE,WINDOW,NOW
from backend.tests.test_finops_reconciliation_publication import seed
from backend.observability.finops.billing_contracts import SourceError

@pytest.mark.asyncio
@pytest.mark.parametrize('control',['signed_net','components','absolute','count'])
async def test_invalid_control_cannot_publish_over_current_generation(system,control):
    state,generation=await seed(system)
    original=generation.identity
    for _ in range(3):
        poisoned=deepcopy(generation.body['result'])
        if control=='signed_net':poisoned['amounts'][0]['net']='999'
        elif control=='absolute':poisoned['amounts'][0]['absolute']='999'
        elif control=='count':poisoned['amounts'][0]['count']=999
        else:poisoned['classified'][0]['amount']='999'
        key,revision=await system.repo.reserve('development',SCOPE,WINDOW.partition)
        with pytest.raises(SourceError):
            await system.repo.publish(key,revision,generation.registry,generation.body['input_ids'],poisoned,generation.body['source_vector'])
        current=(await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0]
        assert current[0].generation==original
        assert len(await system.repo.history(state.key))==1

import asyncio
import time
from copy import deepcopy
from sqlalchemy import select
from backend.tests._m14_controls import controls
from backend.tests.test_finops_unit_economics_publication import setup as economics_setup
from backend.tests.test_finops_control_publication import setup as controls_setup
from backend.tests._m14_controls import BINDING,POLICY as CONTROL_POLICY
from backend.observability.finops.control_models import GenerationRow as ControlGeneration
from backend.observability.finops.reconciliation_models import GenerationRow as ReconciliationGeneration
from backend.observability.finops.unit_economics_models import GenerationRow as EconomicsGeneration
from backend.observability.finops.unit_denominators import utcnow
from backend.tests._m16_harness import envelope

@pytest.mark.asyncio
async def test_mixed_financial_reads_generations_and_source_integrity(system,controls):
    economics_repo,economics_worker=await economics_setup(system)
    assert await economics_worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    inp,reg,result=await controls_setup(controls)
    key,rev=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
    assert await controls.repo.publish(key,rev,inp,reg,CONTROL_POLICY,'FORECAST',result)=='PUBLISHED'
    async def immutable(db,model):
        async with db.sessions() as session:
            rows=(await session.scalars(select(model).order_by(model.identity))).all()
            return [(row.identity,deepcopy(row.body)) for row in rows]
    before=(await immutable(system.db,ReconciliationGeneration),await immutable(system.db,EconomicsGeneration),await immutable(controls.db,ControlGeneration))
    latencies=[]
    async def read():
        begin=time.monotonic()
        assert await economics_repo.current('development',SCOPE,WINDOW.partition,'2026-01-11')
        assert await controls.repo.current('development',BINDING,'FORECAST')
        latencies.append(time.monotonic()-begin)
    async def background():
        for _ in range(8):
            assert await system.worker.run_period(WINDOW,NOW)=='IDENTICAL'
            assert await economics_worker.run_period(WINDOW,utcnow())=='IDENTICAL'
    await asyncio.gather(background(),*(read() for _ in range(24)))
    assert before==(await immutable(system.db,ReconciliationGeneration),await immutable(system.db,EconomicsGeneration),await immutable(controls.db,ControlGeneration))
    assert system.db.engine.pool.checkedout()==controls.db.engine.pool.checkedout()==0
    envelope('mixed_financial_sqlite',latencies,concurrent_reads=24,background_generations=16,
             immutable_m12_m13_m14=True,actual_pool='AsyncAdaptedQueuePool',business_dispatches=0)
