"""Real engine restart and bounded accounting-only recovery."""
import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import select
from backend.observability.database import Database
from backend.observability.config import ObservabilityConfig
from backend.observability.finops.repository import Repository
from backend.observability.finops.usage_ledger import Ledger
from backend.observability.finops.models import InboxRow,LedgerRow,AttemptRow
from backend.tests.test_finops_runtime_ledger import accounting,identity,payload,started,NOW

@pytest.mark.asyncio
async def test_dispose_reopen_retains_debt_and_inbox_once(accounting):
    db,repo,ledger=accounting;i=identity();j=identity(deadline_at=NOW-timedelta(minutes=2))
    await started(repo,i);await repo.capture(payload(i));await started(repo,j)
    url=str(db.engine.url);await db.close()
    replacement=Database(url,isolated_test=True)
    try:
        recovered=Repository(replacement.sessions);worker=Ledger(recovered,ObservabilityConfig())
        assert await worker.recover()==1
        await recovered.checkpoint('development',NOW)
        assert (await recovered.health('development'))['debt']==1
        async with replacement.sessions() as s:
            assert (await s.get(AttemptRow,('development',str(j.attempt_id)))).state=='OUTCOME_UNKNOWN'
            assert (await s.get(LedgerRow,i.event_id)).input_tokens==100
        assert await worker.recover()==0
    finally:await replacement.close()

@pytest.mark.asyncio
async def test_recovery_limit_failure_exhaustion_keeps_pending_payload(accounting,monkeypatch):
    db,repo,ledger=accounting
    ledger.config=ObservabilityConfig(finops_recovery_batch=2,finops_recovery_attempts=1)
    for _ in range(3):
        i=identity();await started(repo,i);await repo.capture(payload(i))
    assert await ledger.recover()==2
    assert (await repo.health('development'))['pending']==1
    monkeypatch.setattr(repo,'materialize',AsyncMock(side_effect=OSError('SECRET_SQL')))
    assert await ledger.recover()==1
    health=await repo.health('development');assert health['pending']==1 and health['exhausted']==1
    assert await ledger.recover()==0
    async with db.sessions() as s:
        pending=(await s.execute(select(InboxRow).where(InboxRow.delivered.is_(False)))).scalar_one()
        assert pending.payload['usage']['input_tokens']==100

@pytest.mark.asyncio
async def test_abrupt_process_exit_after_inbox_commit_survives_reopen(accounting,tmp_path):
    import os,sys,subprocess,json
    db,repo,ledger=accounting;url=str(db.engine.url);await db.close()
    # Child touches only fixture's disposable SQLite path; inherited test guard
    # blocks nonloopback network and all provider calls are absent by construction.
    script='''
import asyncio,json,os,sys
from backend.observability.database import Database
from backend.observability.finops.repository import Repository
from backend.tests.test_finops_runtime_ledger import identity,payload,started
async def run():
 db=Database(sys.argv[1],isolated_test=True);repo=Repository(db.sessions);i=identity()
 await started(repo,i);await repo.capture(payload(i))
 os._exit(73)
asyncio.run(run())
'''
    result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',script,url],capture_output=True,timeout=10)
    assert result.returncode==73,result.stderr.decode()
    reopened=Database(url,isolated_test=True)
    try:
        worker=Ledger(Repository(reopened.sessions),ObservabilityConfig())
        assert await worker.recover()==1
        assert await worker.recover()==0
        async with reopened.sessions() as s:
            assert len((await s.execute(select(LedgerRow))).scalars().all())==1
    finally:await reopened.close()
