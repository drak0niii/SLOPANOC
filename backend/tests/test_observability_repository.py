import asyncio
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select,func,delete
from backend.tests._m7_storage import storage,state,change,config
from backend.observability.projection import DiagnosticEvent,DependencyState,utcnow
from backend.observability.stages import RunStatus,Stage
from backend.observability.models import RunRow,SummaryRow,EventRow


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', [RunStatus.COMPLETED,RunStatus.FAILED,RunStatus.TIMEOUT,RunStatus.CANCELLED])
async def test_lifecycle_terminal_is_idempotent_and_never_regresses(tmp_path,outcome):
    db,repo=await storage(tmp_path/'db.sqlite')
    try:
        running=state();assert await repo.persist(running)=='persisted'
        completed=state(run_id=running.run_id,producer_instance_id=running.producer_instance_id,
            started_at=running.started_at,status=outcome,state_version=11)
        assert await repo.persist(completed)=='persisted'
        assert await repo.persist(completed)=='duplicate'
        for version in (1,10,12,100):
            assert await repo.persist(change(running,state_version=version))=='terminal_conflict'
        other=change(completed,status=RunStatus.FAILED if outcome!=RunStatus.FAILED else RunStatus.COMPLETED,state_version=100)
        assert await repo.persist(other)=='terminal_conflict'
        result=await repo.get(running.run_id,('development',))
        assert result.status==outcome and result.state_version==11 and result.terminal_at==completed.terminal_at
        async with db.sessions() as s:
            assert (await s.execute(select(func.count()).select_from(SummaryRow))).scalar_one()==1
            assert (await s.execute(select(func.count()).select_from(EventRow))).scalar_one()==1
    finally:await db.close()


@pytest.mark.asyncio
async def test_stages_parallel_dependencies_stall_resume_and_versions(tmp_path):
    db,repo=await storage(tmp_path/'db.sqlite')
    try:
        s=state(state_version=10)
        assert await repo.persist(s)=='persisted'
        assert await repo.persist(change(s,state_version=9,current_stage=Stage.PLANNING_STARTED,events=()))=='stale'
        deps=tuple(DependencyState(dependency='knowledge',operation=op,started_at=s.started_at,agent='incident_manager',tool='knowledge_search') for op in ('dense','sparse'))
        s=change(s,state_version=11,current_stage=Stage.TOOL_STARTED,current_agent='incident_manager',current_tool='knowledge_search',dependencies=deps,status=RunStatus.STALLED,stalled=True,ever_stalled=True)
        assert await repo.persist(s)=='persisted'
        read=await repo.get(s.run_id,('development',));assert len(read.dependencies)==2 and read.status==RunStatus.STALLED
        s=change(s,state_version=12,status=RunStatus.RUNNING,stalled=False)
        assert await repo.persist(s)=='persisted'
        assert (await repo.get(s.run_id,('development',))).ever_stalled
        assert await repo.persist(change(s,producer_instance_id=str(uuid4()),state_version=13))=='owner_conflict'
        assert await repo.persist(change(s,turn_id='adk-1',turn_id_origin='adk',state_version=13))=='persisted'
        assert await repo.persist(change(s,turn_id='adk-2',turn_id_origin='adk',state_version=14))=='identity_conflict'
    finally:await db.close()


@pytest.mark.asyncio
async def test_terminal_delivery_observation_and_status_ttl_guard(tmp_path):
    db,repo=await storage(tmp_path/'db.sqlite')
    try:
        s=state(status=RunStatus.COMPLETED)
        assert await repo.persist(s)=='persisted'
        delivery=DiagnosticEvent(event_seq=2,state_version=2,event_type='delivery',timestamp=utcnow(),elapsed_ms=0,stage=s.current_stage,status=s.status)
        after=change(s,state_version=2,delivery_status='disconnected',events=(*s.events,delivery))
        assert await repo.persist(after)=='persisted'
        assert (await repo.get(s.run_id,('development',))).delivery_status=='disconnected'
        async with db.sessions() as session,session.begin():
            await session.execute(delete(RunRow))
        assert (await repo.get(s.run_id,('development',))).status==RunStatus.COMPLETED
        assert await repo.persist(change(s,status=RunStatus.RUNNING,terminal_at=None,state_version=99))=='terminal_conflict'
        assert await repo.persist(after)=='duplicate'
        async with db.sessions() as session:
            assert (await session.execute(select(func.count()).select_from(RunRow))).scalar_one()==0
        # Attempted running event after terminal is denied even with identical terminal facts.
        malicious=delivery.model_copy(update={'event_seq':3,'state_version':3,'event_type':'stage','status':RunStatus.RUNNING})
        assert await repo.persist(change(s,state_version=3,events=(*s.events,malicious)))=='terminal_conflict'
    finally:await db.close()


@pytest.mark.asyncio
async def test_concurrent_runs_and_same_run_cas(tmp_path):
    db,repo=await storage(tmp_path/'db.sqlite')
    try:
        runs=[state() for _ in range(12)]
        await asyncio.gather(*(repo.persist(s) for s in runs))
        s=runs[0]
        await asyncio.gather(*(repo.persist(change(s,state_version=v)) for v in (10,2,8,4,9)))
        assert (await repo.get(s.run_id,('development',))).state_version==10
        terminal=change(s,status=RunStatus.TIMEOUT,current_stage=Stage.TURN_TIMEOUT,terminal_at=utcnow(),state_version=11)
        await asyncio.gather(repo.persist(terminal),repo.persist(change(s,state_version=10)))
        assert (await repo.get(s.run_id,('development',))).status==RunStatus.TIMEOUT
        for other in runs[1:]: assert (await repo.get(other.run_id,('development',))).state_version==1
    finally:await db.close()


@pytest.mark.asyncio
async def test_transaction_rollback_does_not_leave_partial_terminal(tmp_path,monkeypatch):
    db,repo=await storage(tmp_path/'db.sqlite')
    original=repo.insert
    def fail(session,model):
        if model is SummaryRow:raise RuntimeError('SENTINEL_SQL_PASSWORD')
        return original(session,model)
    s=state(status=RunStatus.COMPLETED)
    try:
        monkeypatch.setattr(repo,'insert',fail)
        with pytest.raises(RuntimeError):await repo.persist(s)
        assert await repo.get(s.run_id,('development',)) is None
        monkeypatch.setattr(repo,'insert',original)
        assert await repo.persist(s)=='persisted'
    finally:await db.close()
def test_business_modules_cannot_consume_diagnostic_storage():
    """Keep diagnostic persistence out of routing, evidence and command authority."""
    import ast
    from pathlib import Path
    prohibited = tuple('backend.observability.' + name for name in
        ('repository', 'models', 'database', 'service', 'persistence'))
    roots = ('agents', 'tools', 'approval', 'cases', 'context', 'knowledge', 'operations')
    files = [Path('backend/api/chat_service.py')]
    for root in roots:
        files.extend((Path('backend') / root).rglob('*.py'))
    for path in files:
        for node in ast.walk(ast.parse(path.read_text())):
            imports = ([node.module or ''] if isinstance(node, ast.ImportFrom)
                       else [alias.name for alias in node.names] if isinstance(node, ast.Import)
                       else [])
            assert not any(name == prefix or name.startswith(prefix + '.')
                for name in imports for prefix in prohibited), str(path)
