from datetime import timedelta
import pytest
from sqlalchemy import select,func
from backend.tests._m7_storage import storage,state,config
from backend.observability.projection import utcnow
from backend.observability.retention import classification
from backend.observability.models import RunRow,EventRow,SummaryRow
from backend.observability.stages import RunStatus


@pytest.mark.parametrize('heartbeat_age,deadline_age,status,result',[
    (0,None,RunStatus.RUNNING,'ACTIVE'),(0,None,RunStatus.STALLED,'STALLED'),
    (66,None,RunStatus.RUNNING,'STALE'),(65,None,RunStatus.RUNNING,'ACTIVE'),
    (0,6,RunStatus.RUNNING,'STALE'),(0,5,RunStatus.RUNNING,'ACTIVE')])
def test_stale_classification_never_invents_business_outcome(heartbeat_age,deadline_age,status,result):
    now=utcnow();start=now-timedelta(seconds=100)
    s=state(started_at=start,stage_started_at=start,last_progress_at=start,
        heartbeat_at=now-timedelta(seconds=heartbeat_age),status=status,
        total_deadline_at=now-timedelta(seconds=deadline_age) if deadline_age is not None else None)
    assert classification(s,now,config())==result
    assert s.status==status and s.terminal_at is None


@pytest.mark.asyncio
async def test_retention_tiers_active_preserved_terminal_cache_and_timeline(tmp_path):
    db,repo=await storage(tmp_path/'db.sqlite');now=utcnow()
    specs=[(0,'exceptional',RunStatus.RUNNING),(2,'normal',RunStatus.COMPLETED),
        (31,'normal',RunStatus.COMPLETED),(45,'exceptional',RunStatus.COMPLETED),
        (91,'exceptional',RunStatus.TIMEOUT)]
    rows=[]
    try:
        for days,tier,status in specs:
            at=now-timedelta(days=days)
            s=state(started_at=at,stage_started_at=at,last_progress_at=at,heartbeat_at=at,observed_at=at,
                terminal_at=at if status!=RunStatus.RUNNING else None,status=status,retention_class=tier)
            rows.append(s);await repo.persist(s)
        await repo.cleanup(now)
        assert await repo.get(rows[0].run_id,('development',)) is not None
        assert await repo.get(rows[1].run_id,('development',)) is not None
        assert len(await repo.timeline(rows[1].run_id,0,100))==1
        assert await repo.get(rows[2].run_id,('development',)) is None
        assert await repo.get(rows[3].run_id,('development',)) is not None
        assert await repo.get(rows[4].run_id,('development',)) is None
        async with db.sessions() as s:
            assert (await s.execute(select(func.count()).select_from(RunRow))).scalar_one()==1
            assert (await s.execute(select(func.count()).select_from(SummaryRow))).scalar_one()==2
            assert (await s.execute(select(func.count()).select_from(EventRow))).scalar_one()==3
        await repo.detach_session('session-1')
        assert (await repo.get(rows[1].run_id,('development',))).session_id is None
        with pytest.raises(ValueError):await repo.cleanup(now,1001)
    finally:await db.close()


@pytest.mark.asyncio
async def test_expired_stale_rows_cleanup_bounded_and_restart_read(tmp_path):
    db,repo=await storage(tmp_path/'db.sqlite');now=utcnow()
    try:
        for _ in range(3):
            old=now-timedelta(days=91)
            await repo.persist(state(started_at=old,stage_started_at=old,last_progress_at=old,heartbeat_at=old,observed_at=old))
        await repo.cleanup(now,1)
        assert len(await repo.list_runs(('development',),limit=10,as_of=now))==2
        from backend.observability.repository import Repository
        restarted=Repository(db.sessions,config())
        rows=await restarted.list_runs(('development',),limit=10,as_of=now)
        assert all(classification(r,now,config())=='STALE' and r.status==RunStatus.RUNNING for r in rows)
    finally:await db.close()
