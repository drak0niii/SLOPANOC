import asyncio
import json
import time
from types import SimpleNamespace
from threading import Event
from uuid import uuid4
import pytest
from sqlalchemy import select
from backend.tests._m7_storage import storage,state,change,config,close_chat_storage
from backend.observability.persistence import Coordinator
from backend.observability.turn_trace import TurnTrace,current_turn
from backend.observability.stages import RunStatus,Stage
from backend.observability.models import RunRow,SummaryRow,EventRow
from backend.observability.watchdog import Watchdog


@pytest.mark.asyncio
async def test_publisher_bounds_events_coalesces_and_protects_terminal(tmp_path):
    db,repo=await storage(tmp_path/'db.sqlite');cfg=config();c=Coordinator(repo,cfg)
    runtime=SimpleNamespace(enabled=False,projection=c)
    c.start()
    try:
        turn=TurnTrace(str(uuid4()),'session-1',runtime)
        owner=Watchdog(turn,cfg);turn.bind_reliability(owner)
        for _ in range(160):
            turn.event(Stage.PLANNING_STARTED);turn.event(Stage.PLANNING_COMPLETED)
        owner.last_progress-=40;owner.poll();turn.project('stalled')
        assert turn.status==RunStatus.STALLED
        turn.progress();assert turn.status==RunStatus.RUNNING
        turn.wire_result('ok');assert turn.finish()
        assert not turn.finish(TimeoutError())
        assert len(c.slots)==1 and c.slots[turn.run_id].state.status==RunStatus.COMPLETED
        assert c.health.snapshot()[0]['coalesced']>300
        assert await c.flush(1)
        result=await repo.get(turn.run_id,('development',))
        assert result.status==RunStatus.COMPLETED and result.ever_stalled
        events=await repo.timeline(turn.run_id,0,129)
        assert len(events)==128 and result.timeline_truncated_count>0
        assert {'started','stalled','resumed','terminal'}<={e.event_type for e in events}
        assert [e.event_seq for e in events]==sorted(e.event_seq for e in events)
        turn.relay_closed(False);assert await c.flush(1)
        assert (await repo.get(turn.run_id,('development',))).delivery_status=='disconnected'
        # Canonical finished turn ignores all late lifecycle changes.
        saved=await repo.timeline(turn.run_id,0,129)
        turn.event(Stage.TOOL_STARTED);turn.progress();turn.project('agent')
        assert await c.flush(1)
        assert await repo.timeline(turn.run_id,0,129)==saved
    finally:await c.close();await db.close()


@pytest.mark.asyncio
async def test_saturation_reserves_capacity_after_normal_write_and_terminal_priority():
    written=[]
    class Repo:
        async def persist(self,s):written.append(s);return 'persisted'
    c=Coordinator(Repo(),config(projection_capacity=2,projection_write_spacing_seconds=10))
    s1,s2,s3=state(),state(),state()
    c.start()
    try:
        assert c.submit(s1);assert c.submit(s2);assert await c.flush(1)
        assert len(c.admitted)==2 and not c.slots
        assert not c.submit(s3)  # Already-written active runs still own terminal capacity.
        c.submit(change(s1,state_version=2))
        terminal=change(s2,state_version=2,status=RunStatus.COMPLETED,current_stage=Stage.TURN_COMPLETED,terminal_at=s2.observed_at)
        assert c.submit(terminal)
        for _ in range(100):
            if any(s.status==RunStatus.COMPLETED for s in written):break
            await asyncio.sleep(.002)
        assert written[-1].status==RunStatus.COMPLETED
        assert len(c.slots)<=2 and c.health.snapshot()[0]['rejected']==1
    finally:await c.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal',[False,True])
async def test_db_outage_bounded_retry_and_shutdown(terminal,capsys):
    calls=[]
    class Repo:
        async def persist(self,s):
            calls.append(s)
            raise RuntimeError('SENTINEL_DSN_TOKEN_SQL')
    c=Coordinator(Repo(),config());c.start()
    s=state(status=RunStatus.COMPLETED if terminal else RunStatus.RUNNING)
    c.submit(s)
    await c.flush(1)
    assert len(calls)<=(3 if terminal else 1)
    counts,_=c.health.snapshot();assert counts['failed']>=1
    assert counts['terminal_unpersisted']==int(terminal)
    before=time.monotonic();await c.close();assert time.monotonic()-before<.3
    assert 'SENTINEL' not in capsys.readouterr().err


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal',[False,True])
async def test_shutdown_flush_hung_db_is_bounded_and_visible(terminal):
    class Repo:
        async def persist(self,s):await asyncio.Event().wait()
    cfg=config(projection_attempt_seconds=.2,projection_shutdown_seconds=.08)
    c=Coordinator(Repo(),cfg);c.start();c.submit(state(status=RunStatus.COMPLETED if terminal else RunStatus.RUNNING))
    before=time.monotonic();ok=await c.close()
    assert not ok and time.monotonic()-before<.15
    assert c.health.snapshot()[0]['shutdown_incomplete']==1
    assert not c.slots and c.task.done()


@pytest.mark.asyncio
async def test_periodic_checkpoint_not_watchdog_tick_or_material_progress(tmp_path):
    db,repo=await storage(tmp_path/'db.sqlite');cfg=config(projection_checkpoint_seconds=.02)
    c=Coordinator(repo,cfg);c.start();turn=TurnTrace(str(uuid4()),'session',SimpleNamespace(enabled=False,projection=c))
    owner=Watchdog(turn,cfg);turn.bind_reliability(owner)
    initial=turn.last_progress_at
    try:
        for _ in range(100):owner.poll()
        await asyncio.sleep(.065)
        assert turn.last_progress_at==initial
        read=await repo.get(turn.run_id,('development',))
        assert read.heartbeat_at>initial and read.last_progress_at==initial
        assert c.health.snapshot()[0]['attempts']<=5
        events=await repo.timeline(turn.run_id,0,129)
        assert all(e.event_type!='heartbeat' for e in events)
        turn.wire_result('ok');turn.finish()
    finally:await c.close();await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('outage',[False,True])
async def test_real_business_turn_projection_failure_isolated(tmp_path,outage):
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.tests._api_fakes import FakeRunner
    from backend.observability.runtime import Runtime
    db,repo=await storage(tmp_path/'db.sqlite');cfg=config()
    if outage:
        async def fail(*a):raise RuntimeError('SENTINEL_SQL_CREDENTIAL')
        repo.persist=fail
    c=Coordinator(repo,cfg);c.start();runtime=Runtime(cfg);runtime.projection=c
    sessions=ApiSessionService();sid=await sessions.create_session();service=ChatService(sessions,runner=FakeRunner(sessions),observability_runtime=runtime)
    try:
        response=await service.run_turn(sid,'SENTINEL_PROMPT_CONTENT')
        assert response.message.content=='echo: SENTINEL_PROMPT_CONTENT'
        await asyncio.gather(*tuple(service._background_turns),return_exceptions=True)
        assert not sessions.lock_for(sid).locked()
        assert await c.flush(1)
        if outage:assert c.health.snapshot()[0]['terminal_unpersisted']>=1
        else:
            rows=await repo.list_runs(('development',),limit=10)
            assert len(rows)==1 and rows[0].status==RunStatus.COMPLETED
            async with db.sessions() as s:
                for model in (RunRow,SummaryRow,EventRow):
                    all_rows=(await s.execute(select(model))).scalars().all()
                    data=[{col.name:getattr(r,col.name) for col in model.__table__.columns} for r in all_rows]
                    assert 'SENTINEL' not in json.dumps(data,default=str)
    finally:await c.close();runtime.close();await close_chat_storage(service);await db.close()


@pytest.mark.asyncio
async def test_actual_m6_timed_out_worker_cannot_change_durable_final_state(tmp_path,monkeypatch):
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.streaming_events import StreamEventType
    from backend.observability.runtime import Runtime
    from backend.observability.blocking_work import BlockingExecutor
    from backend.observability.reliability_contract import Category
    from backend.observability.agent_instrumentation import Execution
    db,repo=await storage(tmp_path/'db.sqlite')
    cfg=config(turn_timeout_seconds=.18,cleanup_timeout_seconds=.04,watchdog_stall_seconds=.04,heartbeat_seconds=.01,watchdog_check_seconds=.01)
    c=Coordinator(repo,cfg);c.start();runtime=Runtime(cfg);runtime.projection=c
    sessions=ApiSessionService();sid=await sessions.create_session();service=ChatService(sessions,observability_runtime=runtime)
    executor=BlockingExecutor(1,0);entered,release=Event(),Event();turns=[]
    def worker():
        entered.set();release.wait(2)
        turns[0].event(Stage.TOOL_STARTED)
        return 'SENTINEL_LATE_TOOL_RESULT'
    async def events(seq,*args):
        turns.append(current_turn())
        scope=Execution('agent','incident_manager')
        try:await executor.run(worker,category=Category.TOOL)
        finally:scope.finish()
        yield seq.build(StreamEventType.RUN_COMPLETED,{'outcome':'ok'})
    monkeypatch.setattr(service,'_run_turn_events',events)
    try:
        output=[e async for e in service.execute_turn_events(sid,'SENTINEL_PROMPT') if e is not None]
        await asyncio.gather(*tuple(service._background_turns),return_exceptions=True)
        assert output[-1].data=={'outcome':'error'} and entered.is_set() and executor.occupied==1
        assert await c.flush(1)
        before=await repo.get(turns[0].run_id,('development',))
        timeline=await repo.timeline(before.run_id,0,129)
        assert before.status==RunStatus.TIMEOUT and not before.dependencies and before.current_agent is None
        release.set()
        for _ in range(100):
            if executor.occupied==0:break
            await asyncio.sleep(.002)
        assert executor.occupied==0 and await c.flush(1)
        assert await repo.get(before.run_id,('development',))==before
        assert await repo.timeline(before.run_id,0,129)==timeline
        assert not sessions.lock_for(sid).locked()
    finally:release.set();await c.close();runtime.close();await close_chat_storage(service);await db.close()


@pytest.mark.asyncio
async def test_safe_schema_sink_rejects_all_content_categories(tmp_path,capsys):
    from pydantic import ValidationError
    db,repo=await storage(tmp_path/'db.sqlite')
    sentinels=['PROMPT','COMPLETION','TEAMS','COMMAND','TOOL_ARGUMENTS','TOOL_RESULT','SQL','URL','CREDENTIAL','ATTACHMENT','KNOWLEDGE','REASONING']
    try:
        good=state()
        for category in sentinels:
            bad=good.model_dump(mode='json');bad[category.lower()]='SENTINEL_'+category
            with pytest.raises(ValidationError):
                from backend.observability.projection import RunState
                RunState.model_validate(bad)
        bad=good.model_copy(update={'current_tool':'SENTINEL_COMMAND'})
        with pytest.raises(ValidationError):await repo.persist(bad)
        assert await repo.get(good.run_id,('development',)) is None
        assert 'SENTINEL' not in capsys.readouterr().err
    finally:await db.close()


@pytest.mark.asyncio
async def test_single_app_lifespan_no_ddl_default_deny_and_disabled_export(tmp_path,monkeypatch):
    import httpx
    from sqlalchemy import event
    from backend.api.app import create_app
    import backend.api.app as app_module
    from backend.config.settings import Settings
    from backend.api.session_service import ApiSessionService
    from backend.api.chat_service import ChatService
    from backend.tests._api_fakes import FakeRunner
    from backend.observability.persistence import current_coordinator
    from backend.observability.authorization import Principal,verified_principal_provider
    from backend.observability.schemas import Role,ROLE_PERMISSIONS
    db,repo=await storage(tmp_path/'lifespan.sqlite')
    statements=[]
    event.listen(db.engine.sync_engine,'before_cursor_execute',lambda conn,cursor,statement,*a:statements.append(statement))
    settings=Settings({'SLOPANOC_DATABASE_URL':'postgresql+asyncpg://unused:unused@127.0.0.1/unused',
        'SLOPANOC_PROJECTION_ENABLED':'true','SLOPANOC_OTEL_ENVIRONMENT':'production',
        'SLOPANOC_MODEL_WARMUP_ENABLED':'false'})
    monkeypatch.setattr(app_module,'get_settings',lambda:settings)
    def isolated(url):
        assert url==settings.resolve_database_url()
        return db
    monkeypatch.setattr('backend.observability.database.Database',isolated)
    app=create_app()
    async with app.router.lifespan_context(app):
        c=current_coordinator();assert c is app.state.observability_service.coordinator
        sessions=ApiSessionService();sid=await sessions.create_session()
        service=ChatService(sessions,runner=FakeRunner(sessions))
        response=await service.run_turn(sid,'SENTINEL_PROMPT')
        assert response.message.content=='echo: SENTINEL_PROMPT'
        assert await c.flush(1)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            denied=await client.get('/api/observability/runs',headers={'X-SLOPANOC-DEV-USER':'admin'})
            assert denied.status_code==401
            app.dependency_overrides[verified_principal_provider]=lambda:Principal('test',Role.OPERATOR,ROLE_PERMISSIONS[Role.OPERATOR],frozenset({'production'}),True)
            runs=await client.get('/api/observability/runs')
            assert runs.status_code==200 and runs.json()['items'][0]['status']=='COMPLETED'
            assert 'SENTINEL' not in runs.text
    await close_chat_storage(service)
    assert current_coordinator() is None
    assert not any(statement.lstrip().upper().startswith(('CREATE','ALTER','DROP')) for statement in statements)


@pytest.mark.asyncio
async def test_unvalidated_session_input_is_not_persisted_and_detach_is_monotonic(tmp_path):
    db,repo=await storage(tmp_path/'db.sqlite');c=Coordinator(repo,config());c.start()
    turn=TurnTrace(str(uuid4()),'SENTINEL_CALLER_SESSION_TEXT',SimpleNamespace(enabled=False,projection=c))
    try:
        assert await c.flush(1)
        assert (await repo.get(turn.run_id,('development',))).session_id is None
        turn.wire_result('error');turn.finish()
        assert await c.flush(1)
        async with db.sessions() as s:
            for model in (RunRow,SummaryRow,EventRow):
                rows=(await s.execute(select(model))).scalars().all()
                assert 'SENTINEL' not in json.dumps([{col.name:getattr(r,col.name) for col in model.__table__.columns} for r in rows],default=str)
        s=state();await repo.persist(s);await repo.detach_session(s.session_id)
        assert await repo.persist(change(s,state_version=2))=='persisted'
        assert (await repo.get(s.run_id,('development',))).session_id is None
    finally:await c.close();await db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('operational',[False,True])
async def test_runtime_retention_uses_safe_observed_operational_classification(tmp_path,operational):
    from backend.observability.agent_instrumentation import Execution
    db,repo=await storage(tmp_path/'db.sqlite');c=Coordinator(repo,config());c.start()
    turn=TurnTrace(str(uuid4()),'trusted-session',SimpleNamespace(enabled=False,projection=c))
    turn.confirm_session('trusted-session')
    try:
        if operational:
            with turn.attached():
                scope=Execution('agent','incident_manager');scope.finish()
        turn.wire_result('ok');turn.finish();assert await c.flush(1)
        read=await repo.get(turn.run_id,('development',))
        assert read.retention_class==('exceptional' if operational else 'normal')
    finally:await c.close();await db.close()


@pytest.mark.asyncio
async def test_normal_updates_during_write_still_obey_spacing_and_reserved_terminal():
    times=[];entered=asyncio.Event();release=asyncio.Event()
    class Repo:
        async def persist(self,s):
            times.append(time.monotonic())
            if len(times)==1:entered.set();await release.wait()
            return 'persisted'
    c=Coordinator(Repo(),config(projection_capacity=1,projection_write_spacing_seconds=.06));c.start()
    s=state();c.submit(s)
    try:
        await entered.wait();c.submit(change(s,state_version=2));release.set()
        assert await c.flush(1)
        assert times[1]-times[0]>=.055
        # Persisted active state keeps its terminal reservation.
        assert len(c.admitted)==1 and not c.submit(state())
    finally:release.set();await c.close()


@pytest.mark.asyncio
async def test_terminal_inflight_new_delivery_keeps_capacity_reserved():
    entered=asyncio.Event();release=asyncio.Event();calls=[]
    class Repo:
        async def persist(self,s):
            calls.append(s)
            if len(calls)==1:entered.set();await release.wait()
            return 'persisted'
    c=Coordinator(Repo(),config(projection_capacity=1));c.start();s=state(status=RunStatus.COMPLETED);c.submit(s)
    try:
        await entered.wait();c.submit(change(s,state_version=2,delivery_status='disconnected'))
        release.set();await asyncio.sleep(.001)
        # No write can transiently free reservation while a newer terminal waits.
        if c.slots:assert len(c.admitted)==1 and not c.submit(state())
        assert await c.flush(1) and calls[-1].delivery_status=='disconnected'
        assert not c.admitted
    finally:release.set();await c.close()


def test_pending_terminal_cannot_be_coalesced_to_another_outcome():
    c=Coordinator(None,config());s=state(status=RunStatus.COMPLETED)
    assert c.submit(s)
    conflict=change(s,state_version=2,status=RunStatus.TIMEOUT,current_stage=Stage.TURN_TIMEOUT)
    assert not c.submit(conflict)
    assert c.slots[s.run_id].state.status==RunStatus.COMPLETED
    assert c.health.snapshot()[0]['invalid']==1


@pytest.mark.asyncio
async def test_publication_storm_schedules_one_detached_wakeup(monkeypatch):
    calls=[]
    class Repo:
        async def persist(self,s):return 'persisted'
    c=Coordinator(Repo(),config());c.start()
    original=c.loop.call_soon_threadsafe
    def tracked(callback,*args,**kwargs):
        if callback==c._signal:calls.append(kwargs.get('context'))
        return original(callback,*args,**kwargs)
    monkeypatch.setattr(c.loop,'call_soon_threadsafe',tracked)
    s=state()
    try:
        for version in range(1,2001):c.submit(change(s,state_version=version))
        assert len(calls)==1 and list(calls[0].items())==[]
        assert len(c.slots)==1 and len(c.admitted)==1
        assert await c.flush(1)
    finally:await c.close()
