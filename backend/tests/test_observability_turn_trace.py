"""M2 lifecycle, safe event projection, backstop and conditional phase tests."""
import asyncio
from datetime import timezone
import pytest
from opentelemetry import trace, context
from opentelemetry.trace import StatusCode
from backend.observability.runtime import Runtime
from backend.observability.turn_trace import TurnTrace, current_turn, phase, notify
from backend.observability.tracing import Operation, current_trace_ids
from backend.observability.stages import Stage
from backend.observability.logging import safe_record
from backend.tests.test_observability_runtime import config


@pytest.mark.parametrize('mode',['local','none','disabled'])
def test_lifecycle_noop_modes_and_exactly_once(mode):
    runtime=Runtime(config().model_copy(update=dict(otel_exporter_mode='none' if mode=='none' else 'local',
        observability_enabled=mode!='disabled',otel_enabled=mode!='disabled')))
    try:
        turn=TurnTrace('run-1','session-1',runtime)
        with turn.attached():
            with phase(Operation.SESSION,Stage.SESSION_LOAD_STARTED,Stage.SESSION_LOAD_COMPLETED):
                assert current_turn() is turn
            assert turn.as_active_run() is None
            turn.bind_turn_id('invocation-1')
            if mode != 'disabled': assert turn.as_active_run().turn_id == 'invocation-1'
            turn.wire_result('ok')
            assert turn.finish()
            assert not turn.finish(TimeoutError())
            assert current_turn() is None
        assert current_trace_ids()==(None,None)
        assert turn.status.value=='COMPLETED'
        if mode=='local':
            assert runtime.flush(1)
            roots=[s for s in runtime.exporters['trace'].snapshot() if s.name=='slopanoc.turn']
            assert len(roots)==1 and roots[0].status.status_code==StatusCode.OK
            assert len([e for e in roots[0].events if e.name.startswith('turn.')])==1
        if mode=='disabled': assert turn.trace_id is None
    finally: runtime.close()


def test_root_is_fresh_and_does_not_import_foreign_baggage_or_parent():
    runtime=Runtime(config())
    try:
        with runtime.tracer_provider.get_tracer('google.adk').start_as_current_span('foreign') as foreign:
            turn=TurnTrace('run-1','session-1',runtime)
            with turn.attached():
                assert trace.get_current_span().get_span_context().trace_id != foreign.get_span_context().trace_id
                turn.wire_result('ok');turn.finish()
        assert runtime.flush(1)
        root=next(s for s in runtime.exporters['trace'].snapshot() if s.name=='slopanoc.turn')
        assert root.parent is None and not root.context.trace_state
    finally:runtime.close()


def test_safe_known_events_survive_but_unsafe_content_and_later_events_do_not():
    runtime=Runtime(config())
    try:
        turn=TurnTrace('run-1','session-1',runtime)
        turn._root.add_event('session.load.completed',{'prompt':'FAKE_PROMPT','authorization':'Bearer FAKE_TOKEN',
            'message_count':2,'slopanoc.turn_id_origin':'FAKE_CONTENT','slopanoc.status':'FAKE_CONTENT'})
        turn._root.add_event('model.request.completed',{'prompt':'FAKE_PROMPT'})
        turn._root.add_event('FAKE_PROMPT',{'response':'FAKE_RESPONSE'})
        turn._root.record_exception(RuntimeError('FAKE_SECRET'))
        turn.wire_result('ok');turn.finish()
        assert runtime.flush(1)
        root=next(s for s in runtime.exporters['trace'].snapshot() if s.name=='slopanoc.turn')
        assert 'FAKE' not in str(root.attributes)+str(root.events)
        assert 'model.request.completed' not in {e.name for e in root.events}
        safe=next(e for e in root.events if e.name=='session.load.completed')
        assert safe.attributes['message_count']==2
        assert not root.links
    finally:runtime.close()


def test_actual_conditional_broker_and_fault_thread_boundaries_are_children():
    from backend.context.assembly import ContextEngineeringBroker
    from backend.agents.technical_authority_engineer.troubleshooting_threads import resolve_active_thread
    from types import SimpleNamespace
    runtime=Runtime(config())
    try:
        turn=TurnTrace('run-1','session-1',runtime)
        with turn.attached():
            with phase(Operation.ORCHESTRATION,Stage.AGENT_TEAM_MANAGER):
                ContextEngineeringBroker().assemble(query='FAKE_PROMPT')
                assert turn.current_stage==Stage.AGENT_TEAM_MANAGER
                resolve_active_thread({},SimpleNamespace(user_request_text='FAKE_PROMPT'),problem_statement='FAKE_PROMPT')
                assert turn.current_stage==Stage.AGENT_TEAM_MANAGER
            turn.wire_result('ok');turn.finish()
        assert runtime.flush(1)
        spans=runtime.exporters['trace'].snapshot()
        parent=next(s for s in spans if s.name=='orchestration')
        for name in ('context.select','thread.resolve'):
            child=next(s for s in spans if s.name==name)
            assert child.parent.span_id==parent.context.span_id
        assert all('FAKE' not in str(s.attributes)+str(s.events) for s in spans)
    finally:runtime.close()


@pytest.mark.asyncio
async def test_backstop_preserves_specific_terminal_and_completed_results():
    runtime=Runtime(config())
    try:
        turn=TurnTrace('run-1','session-1',runtime)
        turn.failure(TimeoutError());turn.wire_result('error')
        task=asyncio.create_task(asyncio.sleep(0));await task
        turn.backstop(task)
        assert turn.status.value=='TIMEOUT'
        other=TurnTrace('run-2','session-1',runtime)
        other.wire_result('ok');other.finish()
        cancelled=asyncio.create_task(asyncio.sleep(10));cancelled.cancel()
        try:await cancelled
        except asyncio.CancelledError:pass
        assert not other.backstop(cancelled)
        assert other.status.value=='COMPLETED'
    finally:runtime.close()


def test_span_end_failure_is_counted_and_cache_cleanup_still_runs(monkeypatch):
    from backend.observability.active_runs import ActiveRuns
    runtime=Runtime(config());registry=ActiveRuns()
    try:
        turn=TurnTrace('run-1','session-1',runtime,registry=registry)
        original=turn._root.end
        def broken():
            original()
            raise RuntimeError('FAKE_SECRET')
        monkeypatch.setattr(turn._root,'end',broken)
        turn.wire_result('ok');turn.finish()
        assert len(registry)==0 and runtime.health.snapshot()['trace']['failed']>=1
        assert runtime.flush(1)
        assert len([s for s in runtime.exporters['trace'].snapshot() if s.name=='slopanoc.turn'])==1
    finally:runtime.close()


@pytest.mark.asyncio
async def test_copied_late_worker_context_cannot_reopen_or_log_ended_identity():
    runtime=Runtime(config());gate=asyncio.Event()
    try:
        turn=TurnTrace('run-1','session-1',runtime)
        async def late():
            await gate.wait()
            from backend.observability.turn_trace import signal
            signal('event',Stage.SESSION_LOAD_STARTED)
            return current_turn(),current_trace_ids(),safe_record(runtime.config)
        with turn.attached():
            worker=asyncio.create_task(late())
            turn.wire_result('ok');turn.finish()
        gate.set();identity,ids,log=await worker
        assert identity is None and ids==(None,None)
        assert log.run_id is None and log.turn_id is None
        assert turn.current_stage==Stage.TURN_COMPLETED
    finally:runtime.close()
