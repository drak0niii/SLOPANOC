"""One local M11→M14 lineage, corrected heads, immutable issued histories."""
import asyncio
from copy import deepcopy
from datetime import timedelta,date
from decimal import Decimal
import pytest
from sqlalchemy import select
from backend.tests._m12_reconciliation import system,SCOPE,WINDOW,NOW,charge
from backend.tests.test_finops_unit_economics_publication import setup
from backend.observability.finops.billing_models import StateRow as BillingState,PublicationRow as BillingPublication,CacheRow
from backend.observability.finops.reconciliation_models import StateRow as FinancialHead,GenerationRow as FinancialGeneration,InputRow,RegistryRow
from backend.observability.finops.unit_economics_models import PublicationRow as UnitHead,GenerationRow as UnitGeneration
from backend.observability.finops.control_models import ControlBase,PublicationRow as ControlHead,GenerationRow as ControlGeneration
from backend.observability.finops.control_inputs import Adapter
from backend.observability.finops.control_repository import Repository as Controls,calculate
from backend.observability.finops.control_contracts import Policy
from backend.observability.finops.budget_registry import Registry
from backend.observability.projection import utcnow
from backend.tests._m16_harness import envelope
import time

def financial_registry():
    from backend.tests._m14_controls import budget
    return Registry(budgets=(budget('100',financial_binding=SCOPE,
        effective_from=date(2026,1,1),effective_to=date(2026,2,1)),))

async def audit(db):
    immutable={}
    async with db.sessions() as s:
        for model,key,body in ((BillingPublication,'generation','controls'),(FinancialGeneration,'identity','body'),(UnitGeneration,'identity','body'),(ControlGeneration,'identity','body')):
            immutable[model.__tablename__]={getattr(row,key):deepcopy(getattr(row,body)) for row in (await s.scalars(select(model))).all()}
        for head in (await s.scalars(select(BillingState))).all():
            if head.generation:
                pub=await s.get(BillingPublication,head.generation);assert pub and pub.state_key==head.key and pub.status=='PUBLISHED'
                assert await s.get(CacheRow,head.generation)
        for head in (await s.scalars(select(FinancialHead))).all():
            if not head.generation:continue
            gen=await s.get(FinancialGeneration,head.generation);assert gen
            assert await s.get(RegistryRow,gen.registry)
            for identity in (gen.runtime_input,gen.billing_input,gen.pricing_input):assert await s.get(InputRow,identity)
            for identity in gen.body['source_vector'].values():assert await s.get(BillingPublication,identity)
            assert all(v=='PASS' for v in gen.body['result']['controls'].values())
            for amount in gen.body['result']['amounts']:
                classified=[v for v in gen.body['result']['classified'] if v['currency']==amount['currency']]
                assert sum((Decimal(v['amount']) for v in classified),Decimal(0))==Decimal(amount['net'])
        for head in (await s.scalars(select(UnitHead))).all():
            if not head.generation:continue
            gen=await s.get(UnitGeneration,head.generation);assert gen
            assert await s.get(FinancialGeneration,gen.reconciliation)
        for head in (await s.scalars(select(ControlHead))).all():
            if head.generation:assert await s.get(ControlGeneration,head.generation)
    return immutable

@pytest.mark.asyncio
async def test_corrected_generation_reads_restarts_and_four_milestone_heads(system):
    units,worker=await setup(system);assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    async with system.db.engine.begin() as conn:await conn.run_sync(ControlBase.metadata.create_all)
    controls=Controls(system.db.sessions);adapter=Adapter(system.db.sessions);registry=financial_registry();policy=Policy()
    issue=utcnow()+timedelta(days=1)
    async def publish_controls():
        inputs=await adapter.load('development',SCOPE,'2026-01','EUR',issue)
        assert inputs.source_vector
        key,revision=await controls.reserve('development',SCOPE,'2026-01','FORECAST')
        return await controls.publish(key,revision,inputs,registry,policy,'FORECAST',calculate(inputs,registry,policy,'FORECAST'))
    assert await publish_controls()=='PUBLISHED'
    assert (await controls.current('development',SCOPE,'FORECAST'))[0][1].body['result']['items']
    before=await audit(system.db);latencies=[]
    async def read():
        for _ in range(8):
            began=time.monotonic();await audit(system.db);latencies.append(time.monotonic()-began);await asyncio.sleep(0)
    async def corrections():
        for value in ('20','30','40'):
            system.a.rows=(charge(value),)
            assert await system.ingestion.ingest('DETAILED_BILLING',WINDOW)=='PUBLISHED'
            await system.worker.drain();assert await system.worker.run_period(WINDOW,NOW)=='PUBLISHED'
            assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
            assert await publish_controls()=='PUBLISHED'
            after=await audit(system.db)
            for table,history in before.items():
                assert all(after[table].get(identity)==body for identity,body in history.items())
    await asyncio.gather(corrections(),read(),read(),read())
    # Reconstruct repositories after workload; immutable generation replay is exact.
    from backend.observability.finops.reconciliation_repository import Repository
    from backend.observability.finops.unit_economics_repository import Repository as UnitRepository
    assert (await Repository(system.db.sessions).current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][1]
    assert (await UnitRepository(system.db.sessions).current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0][1]
    assert (await Controls(system.db.sessions).current('development',SCOPE,'FORECAST'))[0][1]
    after=await audit(system.db)
    for table,history in before.items():assert all(after[table].get(identity)==body for identity,body in history.items())
    assert all(len(history)==4 for history in after.values())
    assert system.db.engine.pool.checkedout()==0
    envelope('four_milestone_financial_lineage',latencies,correction_generations=3,current_heads='one valid complete per scope',
        preserved_histories=True,financial_pool_idle=0)

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['head_update_uncommitted','published_committed'])
async def test_financial_owned_process_exit_preserves_cross_generation_lineage(system,phase):
    import subprocess,sys
    units,worker=await setup(system)
    assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    async with system.db.engine.begin() as conn:await conn.run_sync(ControlBase.metadata.create_all)
    controls=Controls(system.db.sessions);adapter=Adapter(system.db.sessions)
    issue=utcnow()+timedelta(days=1);registry=financial_registry();policy=Policy()
    inputs=await adapter.load('development',SCOPE,'2026-01','EUR',issue)
    key,revision=await controls.reserve('development',SCOPE,'2026-01','FORECAST')
    assert await controls.publish(key,revision,inputs,registry,policy,'FORECAST',calculate(inputs,registry,policy,'FORECAST'))=='PUBLISHED'
    previous=(await controls.current('development',SCOPE,'FORECAST'))[0][1].identity
    before=await audit(system.db)
    key,revision=await controls.reserve('development',SCOPE,'2026-01','FORECAST')
    code='''
import asyncio,os,sys
from datetime import datetime
from sqlalchemy import event
from backend.observability.database import Database
from backend.observability.finops.control_inputs import Adapter
from backend.observability.finops.control_repository import Repository,calculate
from backend.observability.finops.control_contracts import Policy
from backend.observability.finops.control_models import PublicationRow
from backend.observability.finops.budget_registry import Registry
from backend.tests.test_m16_financial_cross_generation import financial_registry
async def run():
 assert os.environ.get('SLOPANOC_M16_LOCAL_TEST')=='1'
 db=Database(sys.argv[1],isolated_test=True);phase=sys.argv[2]
 inp=await Adapter(db.sessions).load('development',sys.argv[3],'2026-01','EUR',datetime.fromisoformat(sys.argv[4]))
 reg=financial_registry();policy=Policy()
 def after(conn,cursor,statement,parameters,context,many):
  sql=statement.lower()
  if phase=='head_update_uncommitted' and sql.startswith('update '+PublicationRow.__tablename__) and 'generation=' in sql.replace(' ',''):os._exit(73)
 event.listen(db.engine.sync_engine,'after_cursor_execute',after)
 status=await Repository(db.sessions).publish(sys.argv[5],int(sys.argv[6]),inp,reg,policy,'FORECAST',calculate(inp,reg,policy,'FORECAST'))
 assert status=='PUBLISHED',status
 os._exit(73)
asyncio.run(run())
'''
    assert system.db.engine.url.get_backend_name()=='sqlite'
    await system.db.engine.dispose()
    result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',code,str(system.db.engine.url),phase,SCOPE,
        (issue+timedelta(days=1)).isoformat(),key,str(revision)],capture_output=True,timeout=15)
    assert result.returncode==73,result.stderr.decode()
    after=await audit(system.db)
    for table,history in before.items():assert all(after[table].get(identity)==body for identity,body in history.items())
    current=(await Controls(system.db.sessions).current('development',SCOPE,'FORECAST'))[0][1].identity
    committed=phase=='published_committed'
    assert (current!=previous)==committed
    assert len(after[ControlGeneration.__tablename__])==(2 if committed else 1)
    assert system.db.engine.pool.checkedout()==0
    print('M16_FINANCIAL_PROCESS_EXIT phase='+phase+' committed='+str(committed)+' immutable_history_preserved=True all_four_lineages_valid=True business_dispatches=0')
