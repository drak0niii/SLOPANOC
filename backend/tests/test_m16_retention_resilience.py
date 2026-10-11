"""Retained lineage remains conservative during concurrent analytical reads."""
import asyncio
from datetime import timedelta
import pytest
from sqlalchemy import select,func
from backend.tests._m7_storage import storage,state
from backend.observability.retention_worker import RetentionWorker
from backend.observability.stages import RunStatus
from backend.observability.projection import utcnow
from backend.observability.models import SummaryRow
from backend.observability.policy_models import RetentionCheckpoint

@pytest.mark.asyncio
async def test_concurrent_retention_read_write_and_checkpoint_resume(tmp_path):
    db,repo=await storage(tmp_path/'retention.sqlite');now=utcnow()+timedelta(days=100)
    try:
        identities=[]
        for _ in range(30):
            s=state(status=RunStatus.FAILED);identities.append(s.run_id);await repo.persist(s)
        worker=RetentionWorker(db.sessions,'development',batch=7)
        async def reads():
            for _ in range(30):
                await repo.get(identities[-1],('development',))
                await asyncio.sleep(0)
        async def retention():
            total=0
            for _ in range(5):
                report=await worker.run('exceptional_summary',now,dry_run=False)
                total+=report.deleted
                assert report.scanned<=7
            return total
        deleted,_=await asyncio.gather(retention(),reads())
        assert deleted==30
        assert (await RetentionWorker(db.sessions,'development',batch=7).run('exceptional_summary',now,dry_run=False)).deleted==0
        async with db.sessions() as s:
            checkpoint=await s.get(RetentionCheckpoint,('development','exceptional_summary'))
            assert checkpoint.complete and checkpoint.generation==6
    finally:await db.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['before_transaction','candidate_scan','bounded_delete','delete_before_checkpoint','after_checkpoint'])
async def test_retention_owned_process_exit_atomicity(tmp_path,phase):
    import subprocess,sys
    from backend.observability.database import Database
    from backend.observability.repository import Repository
    from backend.tests._m7_storage import config
    db,repo=await storage(tmp_path/'kill.sqlite'); now=utcnow()+timedelta(days=100)
    original=[]
    for _ in range(8):
        value=state(status=RunStatus.FAILED);original.append(value.run_id);await repo.persist(value)
    url=str(db.engine.url);await db.close()
    code='''
import asyncio,os,sys
from datetime import datetime
from sqlalchemy import event
from backend.observability.database import Database
from backend.observability.retention_worker import RetentionWorker
async def run():
 db=Database(sys.argv[1],isolated_test=True);phase=sys.argv[2]
 if phase=='before_transaction':os._exit(73)
 def before(conn,cursor,statement,parameters,context,many):
  sql=statement.lower()
  if phase=='candidate_scan' and sql.startswith('select') and 'from observability_turn' in sql:os._exit(73)
  if phase=='bounded_delete' and sql.startswith('delete'):os._exit(73)
 def after(conn,cursor,statement,parameters,context,many):
  if phase=='delete_before_checkpoint' and statement.lower().startswith('delete'):os._exit(73)
 event.listen(db.engine.sync_engine,'before_cursor_execute',before)
 event.listen(db.engine.sync_engine,'after_cursor_execute',after)
 await RetentionWorker(db.sessions,'development',batch=7).run('exceptional_summary',datetime.fromisoformat(sys.argv[3]),dry_run=False)
 os._exit(73)
asyncio.run(run())
'''
    result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',code,url,phase,now.isoformat()],capture_output=True,timeout=10)
    assert result.returncode==73,result.stderr.decode()
    replacement=Database(url,isolated_test=True)
    try:
        async with replacement.sessions() as session:
            remaining=(await session.scalars(select(SummaryRow.run_id))).all()
            checkpoint=await session.get(RetentionCheckpoint,('development','exceptional_summary'))
        committed=phase=='after_checkpoint'
        assert len(remaining)==(1 if committed else 8)
        assert (checkpoint.generation if checkpoint else 0)==(1 if committed else 0)
        worker=RetentionWorker(replacement.sessions,'development',batch=7)
        deleted=0
        for _ in range(3):deleted+=(await worker.run('exceptional_summary',now,dry_run=False)).deleted
        assert deleted==(1 if committed else 8)
        async with replacement.sessions() as session:
            assert await session.scalar(select(func.count()).select_from(SummaryRow))==0
            final=await session.get(RetentionCheckpoint,('development','exceptional_summary'))
            assert final.complete
        assert (await worker.run('exceptional_summary',now,dry_run=False)).deleted==0
        print('M16_RETENTION_KILL phase='+phase+' committed='+str(committed)+' total_unique_deleted=8 resumed_deleted='+str(deleted))
    finally:await replacement.close()
