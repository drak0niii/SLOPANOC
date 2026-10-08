"""M2 canonical ChatService tests. Only local storage/fake runtime boundaries."""
import asyncio
import json
import logging
from contextlib import suppress
import pytest
from opentelemetry import trace
from opentelemetry.trace import StatusCode
import backend.api.chat_service as chat
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.observability.active_runs import active_runs
from backend.observability.logging import safe_record, SafeJsonFormatter
from backend.observability.runtime import Runtime
from backend.observability.turn_trace import current_turn, TurnTrace
from backend.observability.tracing import current_trace_ids
from backend.tests._api_fakes import FakeRunner, FakeEvent, FakeFunctionResponse, RaisingRunner
from backend.tests.test_observability_runtime import config


@pytest.fixture
def telemetry(monkeypatch):
    runtime = Runtime(config())
    runs = []
    original = chat.begin_turn
    def begin(*args):
        turn = original(*args)
        if turn is not None:
            runs.append(turn)
        return turn
    monkeypatch.setattr(chat, 'begin_turn', begin)
    yield runtime, runs
    runtime.close()


async def collect(service, session_id, text='hello'):
    return [event async for event in service.execute_turn_events(session_id, text)]


def root(runtime):
    assert runtime.flush(1)
    roots = [s for s in runtime.exporters['trace'].snapshot() if s.name == 'slopanoc.turn']
    assert len(roots) == 1
    return roots[0]


def assert_terminal(runtime, runs, status):
    sample = root(runtime)
    assert sample.attributes['slopanoc.status'] == status
    terminals = [e for e in sample.events if e.name.startswith('turn.')]
    assert len(terminals) == 1
    assert terminals[0].attributes['slopanoc.status'] == status
    assert runs[0].snapshot().status.value == status
    assert active_runs.get(runs[0].run_id) is None
    assert sample.end_time >= sample.start_time
    assert current_turn() is None and current_trace_ids() == (None, None)
    return sample


@pytest.mark.asyncio
@pytest.mark.parametrize('sync', [False, True])
async def test_canonical_root_spans_actual_execution_and_persistence(telemetry, sync):
    runtime, runs = telemetry
    sessions = ApiSessionService()
    sid = await sessions.create_session()
    service = ChatService(sessions, runner=FakeRunner(sessions), observability_runtime=runtime)
    if sync:
        response = await service.run_turn(sid, 'hello')
        assert response.message.content == 'echo: hello'
    else:
        events = await collect(service, sid)
        assert events[0].type == StreamEventType.RUN_STARTED
        assert events[-1].type == StreamEventType.RUN_COMPLETED
        assert len([e for e in events if e.type == StreamEventType.RUN_COMPLETED]) == 1
    sample = assert_terminal(runtime, runs, 'COMPLETED')
    assert sample.parent is None
    assert sample.status.status_code == StatusCode.OK
    assert sample.attributes['slopanoc.run_id'] == runs[0].run_id
    assert sample.attributes['slopanoc.session_id'] == sid
    assert sample.attributes['slopanoc.turn_id'] == 'test-invocation'
    assert sample.attributes['slopanoc.turn_id_origin'] == 'adk'
    names = {e.name for e in sample.events}
    assert {'request.received','request.validated','session.load.started','session.load.completed',
            'persistence.started','persistence.completed','sse.started','sse.completed','turn.completed'} <= names
    children = [s for s in runtime.exporters['trace'].snapshot() if s.name != 'slopanoc.turn']
    assert {'request','session.load','orchestration','finalization','persistence.final_answer','sse.complete'} <= {s.name for s in children}
    assert all(s.context.trace_id == sample.context.trace_id for s in children)
    assert max(s.end_time for s in children) <= sample.end_time
    assert not any(s.name.startswith(('gen_ai', 'model.', 'tool.', 'agent.', 'http.', 'database.')) for s in children)


@pytest.mark.asyncio
@pytest.mark.parametrize('exc,status', [(RuntimeError('FAKE_SECRET'), 'FAILED'),(TimeoutError('FAKE_TOKEN'), 'TIMEOUT')])
async def test_swallowed_runner_failure_retains_classification(telemetry, exc, status):
    runtime, runs = telemetry
    sessions = ApiSessionService(); sid = await sessions.create_session()
    service = ChatService(sessions, runner=RaisingRunner(exc), observability_runtime=runtime)
    events = await collect(service, sid)
    assert events[-1].data == {'outcome': 'error'}
    sample = assert_terminal(runtime, runs, status)
    assert sample.attributes['slopanoc.turn_id_origin'] == 'execution'
    assert sample.attributes['slopanoc.turn_id'] == 'pre-adk:' + runs[0].run_id
    assert 'FAKE' not in str(sample.attributes) + str(sample.events)


@pytest.mark.asyncio
@pytest.mark.parametrize('where', ['session','finalization','persistence'])
async def test_failures_outside_runner_finally_close_root(telemetry, monkeypatch, where):
    runtime, runs = telemetry
    sessions = ApiSessionService(); sid = await sessions.create_session()
    service = ChatService(sessions, runner=FakeRunner(sessions), observability_runtime=runtime)
    if where == 'session':
        async def fail(*args, **kwargs): raise RuntimeError('FAKE_AUTH')
        monkeypatch.setattr(sessions,'get_session',fail)
    elif where == 'finalization':
        def fail(*args, **kwargs): raise RuntimeError('FAKE_AUTH')
        monkeypatch.setattr(chat,'build_knowledge_source_references',fail)
    else:
        original = sessions.persist_state_delta
        async def fail(session, delta):
            if 'turn_final_answers' in delta:
                raise RuntimeError('FAKE_AUTH')
            return await original(session,delta)
        monkeypatch.setattr(sessions,'persist_state_delta',fail)
    events = await collect(service,sid)
    sample = assert_terminal(runtime,runs,'FAILED')
    if where == 'persistence':
        assert not any(e.type == StreamEventType.MESSAGE_COMPLETED for e in events)
        assert sample.attributes['slopanoc.error_code'] == 'DATABASE_PERSISTENCE_ERROR'
    await asyncio.sleep(0)  # existing done-callback removal runs on the next loop tick
    assert not service._background_turns


@pytest.mark.asyncio
async def test_no_root_for_generator_never_entered(telemetry):
    runtime, runs = telemetry
    sessions = ApiSessionService(); sid = await sessions.create_session()
    service = ChatService(sessions, runner=FakeRunner(sessions), observability_runtime=runtime)
    generator = service.execute_turn_events(sid,'hello')
    await generator.aclose()
    assert not runs and not service._background_turns
    assert runtime.flush(1) and not runtime.exporters['trace'].snapshot()


@pytest.mark.asyncio
async def test_close_at_first_yield_keeps_execution_owned_and_finishes(telemetry):
    runtime, runs = telemetry
    sessions = ApiSessionService(); sid = await sessions.create_session()
    service = ChatService(sessions, runner=FakeRunner(sessions), observability_runtime=runtime)
    generator = service.execute_turn_events(sid,'hello')
    first = await anext(generator)
    task = service._run_tasks[(sid,first.run_id)]
    await generator.aclose()
    await task
    assert_terminal(runtime,runs,'COMPLETED')
    assert runs[0].delivery == 'disconnected'


@pytest.mark.asyncio
async def test_pre_start_cancellation_backstop_unblocks_relay(telemetry):
    runtime, runs = telemetry
    sessions = ApiSessionService(); sid = await sessions.create_session()
    service = ChatService(sessions, runner=FakeRunner(sessions), observability_runtime=runtime)
    generator = service.execute_turn_events(sid,'hello')
    first = await anext(generator)
    task = service._run_tasks[(sid,first.run_id)]
    task.cancel()  # no intervening await: _drive has not executed its first line
    with suppress(asyncio.CancelledError): await task
    assert await asyncio.wait_for(collect_generator(generator),1) == []
    assert_terminal(runtime,runs,'CANCELLED')


async def collect_generator(generator):
    return [event async for event in generator]


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel', [False,True])
async def test_disconnect_and_explicit_cancellation_are_independent(telemetry,cancel):
    runtime, runs = telemetry
    sessions = ApiSessionService(); sid = await sessions.create_session()
    started, resume = asyncio.Event(), asyncio.Event()
    async def wait(*args):
        started.set(); await resume.wait()
    service = ChatService(sessions,runner=FakeRunner(sessions,side_effect=wait),observability_runtime=runtime)
    generator=service.execute_turn_events(sid,'hello')
    first=await anext(generator); task=service._run_tasks[(sid,first.run_id)]
    await started.wait()
    progress=active_runs.get(first.run_id)
    assert progress.current_stage.value == 'agent.team_manager'
    assert progress.status.value == 'RUNNING' and progress.elapsed_ms >= 0
    await generator.aclose()
    if cancel:
        assert await service.cancel_run(sid,first.run_id)
        with suppress(asyncio.CancelledError): await task
    else:
        resume.set(); await task
    assert_terminal(runtime,runs,'CANCELLED' if cancel else 'COMPLETED')
    assert runs[0].delivery == 'disconnected'


@pytest.mark.asyncio
async def test_late_identity_and_logs_do_not_borrow_previous_turn(telemetry,monkeypatch):
    runtime,runs=telemetry
    sessions=ApiSessionService();sid=await sessions.create_session()
    observed=[]
    async def inspect_early(*args):
        observed.append(safe_record(runtime.config))
    runner=FakeRunner(sessions,side_effect=inspect_early)
    service=ChatService(sessions,runner=runner,observability_runtime=runtime)
    # Existing previous history must not provide a telemetry turn ID.
    session=await sessions.get_session(sid)
    await sessions.persist_state_delta(session,{'turn_final_answers': {'old-invocation':'old answer'}})
    original=sessions.persist_state_delta
    async def inspect_final(session,delta):
        if 'turn_final_answers' in delta:
            observed.append(safe_record(runtime.config))
        return await original(session,delta)
    monkeypatch.setattr(sessions,'persist_state_delta',inspect_final)
    await collect(service,sid)
    sample=assert_terminal(runtime,runs,'COMPLETED')
    assert observed[0].turn_id is None
    assert observed[-1].turn_id == 'test-invocation'
    assert {v.run_id for v in observed} == {runs[0].run_id}
    assert {v.trace_id for v in observed} == {format(sample.context.trace_id,'032x')}
    assert safe_record(runtime.config).run_id is None
    assert all('old-invocation' not in str(e.attributes) for e in sample.events)


@pytest.mark.asyncio
async def test_concurrent_nested_tasks_have_isolated_log_and_span_context(telemetry,monkeypatch):
    runtime,runs=telemetry
    sessions=ApiSessionService(); sids=[await sessions.create_session() for _ in range(2)]
    barrier=asyncio.Event(); observed={}; arrivals=0
    async def inspect(session_service, session, text):
        nonlocal arrivals
        async def nested():
            record=safe_record(runtime.config)
            observed[session.id]=(record, trace.get_current_span().get_span_context())
        await asyncio.create_task(nested())
        arrivals+=1
        if arrivals == 2: barrier.set()
        await barrier.wait()
    late={}
    original=sessions.persist_state_delta
    async def record_late(session,delta):
        if 'turn_final_answers' in delta:
            late[session.id]=safe_record(runtime.config)
        return await original(session,delta)
    monkeypatch.setattr(sessions,'persist_state_delta',record_late)
    class UniqueRunner(FakeRunner):
        async def run_async(self,**kwargs):
            async for event in super().run_async(**kwargs):
                event.invocation_id='turn-'+kwargs['session_id']
                yield event
    service=ChatService(sessions,runner=UniqueRunner(sessions,side_effect=inspect),observability_runtime=runtime)
    await asyncio.gather(*(collect(service,sid) for sid in sids))
    assert len({r.run_id for r in runs}) == 2
    assert len({r.trace_id for r in runs}) == 2
    for run in runs:
        record,native=observed[run.session_id]
        assert record.run_id == run.run_id and record.trace_id == run.trace_id
        assert record.span_id == format(native.span_id,'016x') and record.turn_id is None
        assert late[run.session_id].turn_id == 'turn-'+run.session_id
        assert late[run.session_id].run_id == run.run_id
        assert late[run.session_id].trace_id == run.trace_id
    assert runtime.flush(1)
    roots=[s for s in runtime.exporters['trace'].snapshot() if s.name=='slopanoc.turn']
    assert len(roots)==2 and all(s.attributes['slopanoc.status']=='COMPLETED' for s in roots)
    assert current_turn() is None and current_trace_ids() == (None,None)


@pytest.mark.asyncio
@pytest.mark.parametrize('breakage',['start','update','finish','attach'])
async def test_telemetry_failure_keeps_canonical_business_completion(telemetry,monkeypatch,breakage):
    runtime,runs=telemetry
    sessions=ApiSessionService();sid=await sessions.create_session()
    def fail(*args,**kwargs): raise RuntimeError('FAKE_SECRET')
    if breakage=='start': monkeypatch.setattr(runtime.tracer,'start_span',fail)
    elif breakage=='update': monkeypatch.setattr(TurnTrace,'event',fail)
    elif breakage=='finish': monkeypatch.setattr(TurnTrace,'finish',fail)
    else: monkeypatch.setattr(TurnTrace,'attached',fail)
    service=ChatService(sessions,runner=FakeRunner(sessions),observability_runtime=runtime)
    events=await collect(service,sid)
    await asyncio.sleep(0)  # task callback backstop
    assert events[-1].data=={'outcome':'ok'}
    assert runtime.health.snapshot()['trace']['failed']>0
    assert current_turn() is None and current_trace_ids()==(None,None)
    if breakage in {'update','finish'}: assert_terminal(runtime,runs,'COMPLETED')


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['clarification','source_gap'])
async def test_valid_server_outcomes_are_completed(telemetry,monkeypatch,outcome):
    runtime,runs=telemetry
    sessions=ApiSessionService();sid=await sessions.create_session()
    if outcome=='clarification':
        async def clarification(*args):return 'Please clarify the unit.'
        monkeypatch.setattr(chat,'pending_clarification_follow_up_text',clarification)
        runner=FakeRunner(sessions)
    else:
        runner=FakeRunner(sessions,events=[
            FakeEvent(final=False,function_responses=[FakeFunctionResponse('record_source_requirements',
                {'requires_teams':False,'requires_governed_knowledge':True})]),
            FakeEvent(text='No governed source was found.')])
        # Trusted negative-selection gate is tested by the existing domain suite;
        # expose that safe server outcome without executing a live remediation.
        async def completion(**kwargs): return ("No approved source is available.", [])
        monkeypatch.setattr(chat,'enforce_governed_knowledge_at_completion',completion)
    service=ChatService(sessions,runner=runner,observability_runtime=runtime)
    events=await collect(service,sid)
    assert events[-1].data=={'outcome':'ok'}
    assert_terminal(runtime,runs,'COMPLETED')


@pytest.mark.asyncio
async def test_root_and_structured_logs_do_not_contain_business_content(telemetry):
    runtime,runs=telemetry
    sessions=ApiSessionService();sid=await sessions.create_session()
    sentinels=['SENTINEL_PROMPT','Bearer SENTINEL_TOKEN','SENTINEL_TEAMS_BODY','SENTINEL_ATTACHMENT']
    logs=[]
    async def inspect(*args):
        record=logging.LogRecord('backend.test',20,__file__,1,' '.join(sentinels),(),None)
        record.authorization=sentinels[1]
        logs.append(SafeJsonFormatter(runtime.config).format(record))
    service=ChatService(sessions,runner=FakeRunner(sessions,side_effect=inspect),observability_runtime=runtime)
    await collect(service,sid,' '.join(sentinels))
    sample=assert_terminal(runtime,runs,'COMPLETED')
    encoded=str(sample.attributes)+str([(e.name,dict(e.attributes)) for e in sample.events])+''.join(logs)
    assert all(value not in encoded for value in sentinels)


@pytest.mark.asyncio
@pytest.mark.parametrize('where',['lock','session','attachments','persistence'])
async def test_cancellation_at_real_boundaries_closes_root(telemetry,monkeypatch,where):
    from contextlib import asynccontextmanager
    runtime,runs=telemetry
    sessions=ApiSessionService();sid=await sessions.create_session()
    started=asyncio.Event();gate=asyncio.Event()
    async def blocked(*args,**kwargs):started.set();await gate.wait()
    if where=='lock':
        @asynccontextmanager
        async def lock(*args):
            await blocked();yield
        monkeypatch.setattr(sessions,'lock_for',lock)
    elif where=='session':monkeypatch.setattr(sessions,'get_session',blocked)
    elif where=='attachments':monkeypatch.setattr(chat,'prepare_attachments_for_turn',blocked)
    else:
        original=sessions.persist_state_delta
        async def persist(session,delta):
            if 'turn_final_answers' in delta:await blocked()
            return await original(session,delta)
        monkeypatch.setattr(sessions,'persist_state_delta',persist)
    service=ChatService(sessions,runner=FakeRunner(sessions),observability_runtime=runtime)
    generator=service.execute_turn_events(sid,'hello',attachment_ids=('attachment-1',) if where=='attachments' else ())
    first=await anext(generator);task=service._run_tasks[(sid,first.run_id)]
    await started.wait();task.cancel()
    with suppress(asyncio.CancelledError):await task
    await asyncio.wait_for(collect_generator(generator),1)
    assert_terminal(runtime,runs,'CANCELLED')


@pytest.mark.asyncio
async def test_attachment_phase_is_conditional_and_does_not_export_identifiers(telemetry,monkeypatch):
    runtime,runs=telemetry
    sessions=ApiSessionService();sid=await sessions.create_session()
    async def prepare(**kwargs):return []
    monkeypatch.setattr(chat,'prepare_attachments_for_turn',prepare)
    service=ChatService(sessions,runner=FakeRunner(sessions),observability_runtime=runtime)
    events=[e async for e in service.execute_turn_events(sid,'hello',attachment_ids=('SENTINEL_ATTACHMENT',))]
    sample=assert_terminal(runtime,runs,'COMPLETED')
    assert {'attachments.started','attachments.completed'} <= {e.name for e in sample.events}
    assert 'SENTINEL_ATTACHMENT' not in str(sample.events)+str(sample.attributes)


@pytest.mark.asyncio
async def test_cleanup_error_after_wire_success_does_not_hide_exception_or_reopen_terminal(telemetry,monkeypatch):
    from contextlib import asynccontextmanager
    runtime,runs=telemetry
    sessions=ApiSessionService();sid=await sessions.create_session()
    original=sessions.lock_for
    @asynccontextmanager
    async def lock(*args):
        async with original(*args):yield
        raise RuntimeError('SENTINEL_CLEANUP')
    monkeypatch.setattr(sessions,'lock_for',lock)
    service=ChatService(sessions,runner=FakeRunner(sessions),observability_runtime=runtime)
    events=await collect(service,sid)
    sample=assert_terminal(runtime,runs,'FAILED')
    assert len([e for e in events if e.type==StreamEventType.RUN_COMPLETED])==1
    mismatch=[e for e in sample.events if e.attributes.get('slopanoc.error_code')=='SSE_COMPLETION_MISMATCH']
    assert len(mismatch)==1
    assert 'SENTINEL_CLEANUP' not in str(sample.events)


@pytest.mark.asyncio
async def test_same_session_runs_serialize_with_distinct_root_context(telemetry):
    runtime,runs=telemetry
    sessions=ApiSessionService();sid=await sessions.create_session()
    active=0;peak=0
    async def observe(*args):
        nonlocal active,peak
        active+=1;peak=max(peak,active)
        await asyncio.sleep(0)
        active-=1
    service=ChatService(sessions,runner=FakeRunner(sessions,side_effect=observe),observability_runtime=runtime)
    await asyncio.gather(collect(service,sid),collect(service,sid))
    assert peak==1 and len({run.trace_id for run in runs})==2
    assert all(run.status.value=='COMPLETED' for run in runs)


@pytest.mark.asyncio
async def test_root_reuses_existing_process_runtime_and_release_metadata(monkeypatch):
    from backend.observability.config import ObservabilityConfig
    import backend.observability.runtime as runtime_module
    runtime=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='local',
        otel_git_sha='123456789abcdef',otel_release_id='release-2',otel_cloud_run_revision='revision-2'))
    monkeypatch.setattr(runtime_module,'current_runtime',lambda:runtime)
    def forbidden(*args):raise AssertionError('No per-turn initialization')
    monkeypatch.setattr(runtime_module,'acquire',forbidden)
    try:
        sessions=ApiSessionService();sid=await sessions.create_session()
        service=ChatService(sessions,runner=FakeRunner(sessions))
        await collect(service,sid)
        sample=root(runtime)
        assert sample.resource.attributes['slopanoc.git_sha']=='123456789abcdef'
        assert sample.resource.attributes['slopanoc.release_id']=='release-2'
        assert sample.resource.attributes['slopanoc.cloud_run_revision']=='revision-2'
        assert len(sample.attributes['slopanoc.config_version'])==16
        assert 'slopanoc.git_sha' not in sample.attributes
    finally:runtime.close()


@pytest.mark.asyncio
async def test_missing_required_specialist_is_failure_even_with_safe_response(telemetry,monkeypatch):
    from types import SimpleNamespace
    runtime,runs=telemetry
    sessions=ApiSessionService();sid=await sessions.create_session()
    from backend.agents.team_manager.operational_routing import OperationalRouteDecision, OperationalRoute, OperationalTurnKind
    async def required(*args,**kwargs):
        return OperationalRouteDecision(None,True,(),OperationalTurnKind.OPERATION_REQUEST,
            True,OperationalRoute.GOVERNED_OPERATIONAL,'required')
    monkeypatch.setattr(chat,'evaluate_operational_route',required)
    service=ChatService(sessions,runner=FakeRunner(sessions),observability_runtime=runtime)
    events=await collect(service,sid)
    # Existing safe response/wire contract stays intact. Technical failure is
    # classified from the server-owned missing record, never response prose.
    assert any(e.type==StreamEventType.MESSAGE_COMPLETED for e in events)
    sample=assert_terminal(runtime,runs,'FAILED')
    assert sample.attributes['slopanoc.error_code']=='SPECIALIST_FAILED'


@pytest.mark.asyncio
async def test_root_export_outage_is_observable_and_does_not_change_response(monkeypatch):
    from opentelemetry.sdk.trace.export import SpanExportResult
    class Broken:
        def export(self,batch):return SpanExportResult.FAILURE
        def shutdown(self):pass
    runtime=Runtime(config(),{'trace':Broken()})
    runs=[];original=chat.begin_turn
    def begin(*args):
        turn=original(*args);runs.append(turn);return turn
    monkeypatch.setattr(chat,'begin_turn',begin)
    try:
        sessions=ApiSessionService();sid=await sessions.create_session()
        service=ChatService(sessions,runner=FakeRunner(sessions),observability_runtime=runtime)
        events=await collect(service,sid)
        assert events[-1].data=={'outcome':'ok'}
        assert next(e.data['content'] for e in events if e.type==StreamEventType.MESSAGE_COMPLETED)=='echo: hello'
        runtime.flush(1)
        assert runtime.health.snapshot()['trace']['failed']>0
        assert runs[0].status.value=='COMPLETED' and active_runs.get(runs[0].run_id) is None
    finally:runtime.close()
