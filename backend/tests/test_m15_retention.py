from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select,func,insert
from backend.tests._m7_storage import storage,state
from backend.observability.stages import RunStatus
from backend.observability.projection import utcnow
from backend.observability.models import SummaryRow,RunRow
from backend.observability.policy_models import RetentionCheckpoint
from backend.observability.retention_worker import RetentionWorker,DependencyChecker,financial_tables
from backend.observability.finops.control_models import InputRow,BacktestRow

@pytest.mark.asyncio
async def test_retention_dry_run_scope_bounds_checkpoint_and_idempotency(tmp_path):
    db,repo=await storage(tmp_path/'retention.sqlite');now=utcnow()+timedelta(days=100)
    try:
        for env in ('development','production'):
            for i in range(23):
                await repo.persist(state(status=RunStatus.FAILED,environment=env))
        worker=RetentionWorker(db.sessions,'development',batch=7)
        first=await worker.run('exceptional_summary',now)
        assert (first.scanned,first.candidate,first.eligible,first.deleted,first.scan_complete)==(7,7,7,0,False)
        async with db.sessions() as s:
            assert (await s.execute(select(func.count()).select_from(SummaryRow))).scalar_one()==46
            assert (await s.execute(select(func.count()).select_from(RetentionCheckpoint))).scalar_one()==0
        deleted=0
        for i in range(4):
            report=await worker.run('exceptional_summary',now,dry_run=False);deleted+=report.deleted
            assert report.scanned<=7
        assert deleted==23 and report.scan_complete
        assert (await worker.run('exceptional_summary',now,dry_run=False)).deleted==0
        async with db.sessions() as s:
            assert (await s.execute(select(func.count()).select_from(SummaryRow).where(SummaryRow.environment=='production'))).scalar_one()==23
    finally:await db.close()

@pytest.mark.asyncio
async def test_financial_lineage_blocks_source_and_unknown_coverage_retains(tmp_path):
    db,repo=await storage(tmp_path/'financial.sqlite');now=utcnow();old=now-timedelta(days=1000)
    try:
        tables=financial_tables()
        async with db.engine.begin() as c:
            for metadata in {t.metadata for t in tables.values()}:await c.run_sync(metadata.create_all)
        async with db.sessions() as s,s.begin():
            s.add(InputRow(identity='a'*64,environment='development',scope='scope',month='2023-01',cutoff=old,body={}))
            s.add(InputRow(identity='b'*64,environment='development',scope='scope',month='2023-01',cutoff=old,body={}))
            s.add(BacktestRow(identity='c'*64,environment='development',scope='scope',forecast='d'*64,truth='a'*64,
                scoring_version='v1',known_at=now,body={'source_vector':['a'*64]}))
        worker=RetentionWorker(db.sessions,'development',batch=10)
        dry=await worker.run('finops_control_input',now)
        assert (dry.eligible,dry.dependency_blocked,dry.deleted)==(1,1,0)
        done=await worker.run('finops_control_input',now,dry_run=False)
        assert done.deleted==1 and done.dependency_blocked==1
        async with db.sessions() as s,s.begin():
            s.add(BacktestRow(identity='e'*64,environment='development',scope='scope',forecast='d'*64,truth='f'*64,
                scoring_version='v1',known_at=now,body={'unknown_source_vector':['f'*64]}))
            await s.flush()
            assert await DependencyChecker(limit=1).check(s,'finops_control_input','0'*64,'development')=='UNKNOWN'
    finally:await db.close()

@pytest.mark.asyncio
async def test_failure_rolls_back_deletes_and_checkpoint_then_resumes(tmp_path):
    db,repo=await storage(tmp_path/'rollback.sqlite');now=utcnow()
    try:
        tables=financial_tables()
        async with db.engine.begin() as c:
            for metadata in {t.metadata for t in tables.values()}:await c.run_sync(metadata.create_all)
        async with db.sessions() as s,s.begin():
            for i in range(3):s.add(InputRow(identity=str(i)*64,environment='development',scope='scope',month='2023-01',cutoff=now-timedelta(days=1000),body={}))
        class Broken:
            calls=0
            async def check(self,*args):
                self.calls+=1
                if self.calls==2: raise RuntimeError('synthetic batch failure')
                return 'CLEAR'
        worker=RetentionWorker(db.sessions,'development',dependencies=Broken())
        with pytest.raises(RuntimeError):await worker.run('finops_control_input',now,dry_run=False)
        async with db.sessions() as s:
            assert (await s.execute(select(func.count()).select_from(InputRow))).scalar_one()==3
            assert (await s.execute(select(func.count()).select_from(RetentionCheckpoint))).scalar_one()==0
        assert (await RetentionWorker(db.sessions,'development').run('finops_control_input',now,dry_run=False)).deleted==3
    finally:await db.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('category',['corrections','source_generation','pricing','reconciliation','denominator','forecast','backtest','anomaly'])
async def test_actual_financial_lineage_categories_are_never_deleted(tmp_path,category):
    from backend.observability.finops.reconciliation_models import InputRow as ReconciliationInput
    from backend.observability.finops.unit_economics_models import SnapshotRow
    from backend.observability.finops.control_models import GenerationRow,DefinitionRow,TransitionRow
    from backend.observability.finops.models import LedgerRow
    db,_=await storage(tmp_path/(category+'.sqlite'));now=utcnow();source='a'*32
    try:
        tables=financial_tables()
        async with db.engine.begin() as c:
            for metadata in {t.metadata for t in tables.values()}:await c.run_sync(metadata.create_all)
        async with db.sessions() as s,s.begin():
            s.add(InputRow(identity=source,environment='development',scope='scope',month='2023-01',cutoff=now-timedelta(days=1000),body={}))
            if category=='corrections':
                s.add(LedgerRow(event_id='b'*32,environment='development',attempt_id='c'*32,payload_hash='d'*64,
                    record_kind='ADJUSTMENT',original_event_id=source,metadata_safe={},observed_at=now,created_at=now))
            elif category in ('source_generation','pricing','reconciliation'):
                s.add(ReconciliationInput(identity='b'*64,environment='development',scope='scope',period='2026-10',
                    kind='PRICING' if category=='pricing' else 'RUNTIME',source_generation=source,body={category:source}))
            elif category=='denominator':
                s.add(SnapshotRow(identity='b'*64,environment='development',period='2026-10',source='M9',body={'source_vector':[source]},captured_at=now))
            elif category=='backtest':
                s.add(BacktestRow(identity='b'*64,environment='development',scope='scope',forecast='c'*64,truth=source,scoring_version='v1',known_at=now,body={}))
            else:
                s.add(DefinitionRow(identity='c'*64,version='v1',body={},recorded_at=now))
                s.add(GenerationRow(identity='b'*64,definitions='c'*64,inputs=source,environment='development',scope='scope',month='2023-01',
                    kind='ANOMALY' if category=='anomaly' else 'FORECAST',issue=now,body={'source_vector':[source]},published_at=now))
                if category=='anomaly':s.add(TransitionRow(identity='d'*64,anomaly='e'*64,generation='b'*64,previous=source,state='ACTIVE',at=now))
        report=await RetentionWorker(db.sessions,'development').run('finops_control_input',now,dry_run=False)
        assert report.dependency_blocked==1 and report.deleted==0
    finally:await db.close()

@pytest.mark.asyncio
async def test_lost_checkpoint_fence_rolls_back_the_entire_batch(tmp_path):
    from sqlalchemy import update
    db,_=await storage(tmp_path/'fence.sqlite');now=utcnow()
    try:
        tables=financial_tables()
        async with db.engine.begin() as c:
            for metadata in {t.metadata for t in tables.values()}:await c.run_sync(metadata.create_all)
        async with db.sessions() as s,s.begin():
            s.add(InputRow(identity='a'*64,environment='development',scope='scope',month='2023-01',cutoff=now-timedelta(days=1000),body={}))
        class LostFence:
            async def check(self,session,*args):
                await session.execute(update(RetentionCheckpoint).values(generation=9))
                return 'CLEAR'
        with pytest.raises(RuntimeError,match='fence lost'):
            await RetentionWorker(db.sessions,'development',dependencies=LostFence()).run('finops_control_input',now,dry_run=False)
        async with db.sessions() as s:
            assert (await s.execute(select(func.count()).select_from(InputRow))).scalar_one()==1
            assert (await s.execute(select(func.count()).select_from(RetentionCheckpoint))).scalar_one()==0
    finally:await db.close()
