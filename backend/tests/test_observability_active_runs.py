"""M2 cache tests: bounded local observations, no durable/support claims."""
from datetime import timezone
from backend.observability.active_runs import ActiveRuns
from backend.observability.turn_trace import TurnTrace
from backend.observability.stages import Stage


def test_snapshot_identity_progress_and_terminal_cleanup():
    clock=[10.0];cache=ActiveRuns()
    turn=TurnTrace('run-1','session-1',clock=lambda:clock[0],registry=cache)
    first=cache.get('run-1')
    assert first.turn_id is None and first.trace_id is None
    assert first.started_at.tzinfo==timezone.utc and first.elapsed_ms==0
    clock[0]+=2
    turn.event(Stage.SESSION_LOAD_STARTED)
    current=cache.get('run-1')
    assert current.current_stage==Stage.SESSION_LOAD_STARTED and current.elapsed_ms==2000
    assert current.last_progress_at>=first.last_progress_at
    turn.bind_turn_id('new-invocation');turn.bind_turn_id('old-invocation')
    assert cache.get('run-1').turn_id=='new-invocation'
    turn.wire_result('ok');turn.finish()
    assert cache.get('run-1') is None and turn.snapshot().terminal_at is not None
    turn.event(Stage.SESSION_LOAD_STARTED)
    assert turn.current_stage==Stage.TURN_COMPLETED


def test_cache_capacity_and_timeline_bounds_are_explicit():
    cache=ActiveRuns(capacity=1)
    first=TurnTrace('run-1','session-1',registry=cache)
    second=TurnTrace('run-2','session-2',registry=cache)
    assert len(cache)==1 and cache.get('run-2') is None
    for _ in range(200):first.event(Stage.SESSION_LOAD_COMPLETED)
    assert len(first.snapshot().timeline)==128 and first.snapshot().dropped_events>0
    first.finish();second.finish()
    assert len(cache)==0
