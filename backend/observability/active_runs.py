"""M2 execution-local cache. Never a cross-instance support registry."""
from dataclasses import dataclass
from datetime import datetime
from threading import RLock
from weakref import WeakValueDictionary
from .stages import RunStatus, Stage


@dataclass(frozen=True)
class Progress:
    run_id: str
    session_id: str
    trace_id: str | None
    span_id: str | None
    current_span_id: str | None
    turn_id: str | None
    turn_id_origin: str | None
    status: RunStatus
    current_stage: Stage
    started_at: datetime
    stage_started_at: datetime
    last_progress_at: datetime
    terminal_at: datetime | None
    elapsed_ms: float
    timeline: tuple
    dropped_events: int
    delivery: str
    dependencies: tuple = ()
    current_dependency: str | None = None
    current_agent: str | None = None
    current_tool: str | None = None
    remaining_seconds: float | None = None
    progress_age_seconds: float | None = None
    stalled: bool = False
    expired_category: str | None = None
    absolute_deadline: float | None = None
    workers: tuple = ()
    cleanup_outcome: str = 'pending' 


class ActiveRuns:
    """Weak execution references; finished turns removed, no terminal history."""
    def __init__(self, capacity=2048):
        self.capacity = capacity
        self._lock = RLock()
        self._runs = WeakValueDictionary()

    def register(self, run):
        with self._lock:
            if len(self._runs) >= self.capacity:
                return False
            self._runs[run.run_id] = run
            return True

    def discard(self, run_id):
        with self._lock:
            self._runs.pop(run_id, None)

    def get(self, run_id):
        with self._lock:
            run = self._runs.get(run_id)
        return run.snapshot() if run is not None else None

    def __len__(self):
        with self._lock:
            return len(self._runs)


active_runs = ActiveRuns()
