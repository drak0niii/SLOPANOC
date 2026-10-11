"""Actual canonical ChatService children; restart repairs accounting, never business."""
import asyncio
from collections import Counter
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
import pytest
from sqlalchemy import select
from google.adk.sessions import DatabaseSessionService
from backend.api.session_service import ApiSessionService
from backend.observability.database import Database
from backend.observability.config import ObservabilityConfig
from backend.observability.finops.repository import Repository as AccountingRepository
from backend.observability.finops.usage_ledger import Ledger
from backend.observability.finops.models import AttemptRow,LedgerRow
from backend.observability.models import RunRow,SummaryRow
from backend.observability.repository import Repository
from backend.observability.projection import RunState,utcnow
from backend.observability.retention import classification

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['accepted','admission','started','provider_completed','tool_completed','terminal_persisted','pre_delivery','cleanup'])
async def test_canonical_backend_hard_exit_truth_and_no_replay(tmp_path,phase):
    child=Path(__file__).with_name('_m16_backend_child.py')
    result=await asyncio.to_thread(subprocess.run,[sys.executable,str(child),str(tmp_path),phase],capture_output=True,timeout=20)
    assert result.returncode==73,result.stderr.decode()[-3000:]
    dispatch=tmp_path/'dispatch';before=dispatch.read_bytes() if dispatch.exists() else b''
    counts=Counter(before.decode().splitlines())
    assert counts['provider']==int(phase not in ('accepted','admission','started'))
    assert counts['tool']==int(phase in ('tool_completed','terminal_persisted','pre_delivery','cleanup'))
    assert counts['agent']==int(phase!='accepted')
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'state.db'),isolated_test=True)
    cfg=ObservabilityConfig(projection_enabled=True)
    try:
        repo=AccountingRepository(db.sessions);ledger=Ledger(repo,cfg)
        recovered=await ledger.recover();assert await ledger.recover()==0
        await repo.checkpoint('development',utcnow()+timedelta(days=1))
        async with db.sessions() as session:
            attempts=(await session.scalars(select(AttemptRow))).all()
            rows=(await session.scalars(select(LedgerRow))).all()
            runs=(await session.scalars(select(RunRow))).all()
            summaries=(await session.scalars(select(SummaryRow))).all()
        assert len(attempts)==int(phase!='accepted')
        if phase=='admission':assert attempts[0].state=='NOT_STARTED'
        if phase=='started':assert attempts[0].state=='OUTCOME_UNKNOWN' and not rows
        if counts['provider']:assert len(rows)==1 and rows[0].input_tokens==10
        if phase=='terminal_persisted':assert len(summaries)==1 and summaries[0].status=='COMPLETED'
        states=[]
        operational=Repository(db.sessions,cfg)
        for row in runs:
            value=await operational.get(row.run_id,('development',));states.append(classification(value,utcnow()+timedelta(days=1),cfg))
            assert await operational.persist(value.model_copy(update={'producer_instance_id':str(__import__('uuid').uuid4())}))=='owner_conflict'
            current=await operational.get(row.run_id,('development',));assert current==value
            assert await operational.persist(value.model_copy(update={'state_version':0})) in ('stale','duplicate')
        # A fresh process-local lock cannot retain the dead owner's acquisition.
        sessions=ApiSessionService(DatabaseSessionService('sqlite+aiosqlite:///'+str(tmp_path/'session.db')))
        sid=(tmp_path/'session_id').read_text()
        async with asyncio.timeout(1),sessions.lock_for(sid,'default'):assert await sessions.get_session(sid)
        await sessions.adk_session_service.db_engine.dispose()
        assert (dispatch.read_bytes() if dispatch.exists() else b'')==before
        print('M16_BACKEND_EXIT phase='+phase+' dispatches='+str(dict(counts))+' classifications='+str(states)+' terminal_summaries='+str(len(summaries))+' recovered_inbox='+str(recovered))
    finally:await db.close()
