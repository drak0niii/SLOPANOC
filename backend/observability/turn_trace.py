"""Canonical turn observation; no business decisions, persistence or SDK startup."""
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from threading import RLock
import asyncio
import time
from opentelemetry import context, trace
from opentelemetry.trace import Status, StatusCode
from .active_runs import active_runs, Progress
from .errors import ErrorCode
from .reliability_contract import Category
from .stages import RunStatus, Stage, TERMINAL_STATUSES
from .tracing import Operation

_current = ContextVar('slopanoc_telemetry_turn', default=None)


def current_turn(*, include_terminal=False):
    value = _current.get()
    return value if value is not None and (include_terminal or value.status not in TERMINAL_STATUSES) else None


def degraded(runtime):
    if runtime is not None:
        try:
            runtime.health.add('trace', 'failed')
        except Exception:
            pass  # Health's own rate-limited fallback is the last telemetry boundary.


def notify(turn, method, *args, **kwargs):
    """Even a replaced/broken lifecycle method cannot alter application behavior."""
    if turn is not None:
        try:
            return getattr(turn, method)(*args, **kwargs)
        except Exception:
            degraded(getattr(turn, "runtime", None))
    return None


def signal(method, *args, **kwargs):
    return notify(current_turn(), method, *args, **kwargs)


def begin_turn(run_id, session_id, runtime=None):
    try:
        if runtime is None:
            from .runtime import current_runtime
            runtime = current_runtime()
        return TurnTrace(run_id, session_id, runtime)
    except Exception:
        degraded(runtime)
        return None


class TurnTrace:
    def __init__(self, run_id, session_id, runtime=None, *, clock=time.monotonic, registry=active_runs):
        self.run_id, self.session_id, self.runtime = run_id, session_id, runtime
        self.registry, self.clock = registry, clock
        self._lock = RLock()
        self.started_at = self.stage_started_at = self.last_progress_at = datetime.now(timezone.utc)
        self._start = clock()
        self._end_elapsed_ms = None
        self.reliability = None
        self.status = RunStatus.RUNNING
        self.current_stage = Stage.REQUEST_RECEIVED
        self.turn_id = self.turn_id_origin = self.terminal_at = None
        self.error_code = None
        self.error_stage = None
        self._session_verified = False
        self._candidate = None
        self._wire_completed = False
        self._wire_outcome = None
        self.delivery = 'pending'
        self.timeline = deque(maxlen=128)
        self.dropped_events = 0
        self._phases = []
        from weakref import WeakValueDictionary
        self._dependencies = WeakValueDictionary()
        self._executions = WeakValueDictionary()
        self._root = trace.INVALID_SPAN
        self.trace_id = self.span_id = None
        self.config_version = None
        self.publisher = None
        self._sre_root_closed = False
        from .slo_sources import Tracker
        writer = getattr(runtime, 'sre', None)
        self.sre = Tracker(self, writer) if writer is not None else None
        if runtime is not None and runtime.enabled:
            try:
                self.config_version = runtime.config.effective_configuration()[0].config_version
                self._root = runtime.tracer.start_span(Operation.TURN.value, context=context.Context(),
                    attributes=self.attributes())
                native = self._root.get_span_context()
                if native.is_valid:
                    self.trace_id, self.span_id = format(native.trace_id, '032x'), format(native.span_id, '016x')
            except Exception:
                degraded(runtime)
        try:
            if not registry.register(self):
                degraded(runtime)
        except Exception:
            degraded(runtime)
        try:
            from .persistence import current_coordinator
            coordinator = getattr(runtime, 'projection', None) or current_coordinator()
            if coordinator is not None:
                self.publisher = coordinator.attach(self)
                self.project('started')
        except Exception:
            pass  # Projection must never gate canonical execution.
        request = notify(self, "start_phase", Operation.REQUEST)
        notify(self, 'event', Stage.REQUEST_RECEIVED)
        notify(self, 'event', Stage.REQUEST_VALIDATED)
        notify(self, 'end_phase', request)

    def project(self, event_type=None):
        if self.publisher is not None:
            try:
                self.publisher.publish(event_type)
            except Exception:
                self.publisher.coordinator.health.add('invalid')

    def confirm_session(self, value):
        with self._lock:
            if self.status not in TERMINAL_STATUSES and value == self.session_id:
                self._session_verified = True
                self.project('identity')

    def bind_reliability(self, owner):
        with self._lock:
            self.reliability = owner
            self.project('deadline')

    def add_execution(self, scope):
        with self._lock:
            if self.status not in TERMINAL_STATUSES:
                self._executions[id(scope)] = scope
                self.progress()
                self.project('tool' if scope.tool else 'agent')

    def remove_execution(self, scope):
        with self._lock:
            if self.status not in TERMINAL_STATUSES:
                self._executions.pop(id(scope), None)
                self.progress()
                self.project('tool' if scope.tool else 'agent')

    def model_parent(self):
        """Nearest visible live phase/root, never a filtered foreign ADK span."""
        from .agent_instrumentation import execution_parent
        from .dependency_instrumentation import dependency_parent
        dependency = dependency_parent(self)
        if dependency is not None:
            return dependency
        parent = execution_parent(self)
        if parent is not None:
            return parent
        with self._lock:
            native = trace.get_current_span()
            return native if any(native is item[0] for item in self._phases) else self._root

    def attributes(self):
        values = {'slopanoc.run_id': self.run_id, 'slopanoc.session_id': self.session_id,
                  'slopanoc.status': self.status.value}
        if self.turn_id is not None:
            values.update({'slopanoc.turn_id': self.turn_id, 'slopanoc.turn_id_origin': self.turn_id_origin})
        if self.config_version is not None:
            values['slopanoc.config_version'] = self.config_version
        if self.error_code is not None:
            values['slopanoc.error_code'] = self.error_code.value
        return values

    def event(self, stage, metadata=None):
        with self._lock:
            if self.status in TERMINAL_STATUSES:
                return
            stage = Stage(stage)
            if self.reliability is not None and stage != self.current_stage:
                self.reliability.material()
            now = datetime.now(timezone.utc)
            if stage != self.current_stage:
                self.stage_started_at = now
            self.current_stage, self.last_progress_at = stage, now
            if len(self.timeline) == self.timeline.maxlen:
                self.dropped_events += 1
            self.timeline.append((stage.value, now, (self.clock()-self._start)*1000))
            self.project('stage')
            from .redaction import safe_span_attributes
            attrs = safe_span_attributes(self.attributes() | {'slopanoc.stage': stage.value} | (metadata or {}))
            try:
                self._root.add_event(stage.value, attrs)
                self._root.set_attribute('slopanoc.stage', stage.value)
            except Exception:
                degraded(self.runtime)

    def bind_turn_id(self, value):
        from .schemas import Identifier
        from pydantic import TypeAdapter
        value = TypeAdapter(Identifier).validate_python(value)
        with self._lock:
            if self.status in TERMINAL_STATUSES or self.turn_id is not None:
                return
            self.turn_id, self.turn_id_origin = value, 'adk'
            self.project('identity')
            self._set_root_attributes()

    def _set_root_attributes(self):
        from .redaction import safe_span_attributes
        try:
            self._root.set_attributes(safe_span_attributes(self.attributes()))
        except Exception:
            degraded(self.runtime)

    def failure(self, exc=None, error_code=None):
        with self._lock:
            if self.status in TERMINAL_STATUSES:
                return
            if self._candidate == RunStatus.TIMEOUT:
                return
            if self.error_stage is None:
                self.error_stage = self.current_stage
            if isinstance(exc, asyncio.CancelledError):
                self._candidate, self.error_code = RunStatus.CANCELLED, ErrorCode.TURN_CANCELLED
            elif isinstance(exc, TimeoutError):
                self._candidate, self.error_code = RunStatus.TIMEOUT, ErrorCode.TURN_TIMEOUT
            elif self._candidate not in {RunStatus.CANCELLED, RunStatus.TIMEOUT}:
                self._candidate = RunStatus.FAILED
                if error_code is not None:
                    self.error_code = ErrorCode(error_code)

    def progress(self):
        with self._lock:
            if self.status not in TERMINAL_STATUSES:
                self.last_progress_at = datetime.now(timezone.utc)
                if self.reliability is not None:
                    self.reliability.material()

    def wire_result(self, outcome):
        self._wire_completed = True
        self._wire_outcome = outcome
        if outcome == 'error':
            self.failure()

    def start_phase(self, operation, started=None):
        with self._lock:
            if self.status in TERMINAL_STATUSES:
                return None
            operation = Operation(operation)
            native = trace.INVALID_SPAN
            try:
                if self.runtime is not None and self.runtime.enabled:
                    parent = trace.get_current_span()
                    # ADK scopes are intentionally not exportable; keep actual M2
                    # children under the visible root if a foreign span is current.
                    if not any(parent is handle[0] for handle in self._phases):
                        parent = self._root
                    native = self.runtime.tracer.start_span(operation.value,
                        context=trace.set_span_in_context(parent, context.Context()), attributes=self.attributes())
            except Exception:
                degraded(self.runtime)
            token = None
            try:
                token = context.attach(trace.set_span_in_context(native))
            except Exception:
                degraded(self.runtime)
            previous = (self.current_stage, self.stage_started_at)
            handle = (native, token, previous, Stage(started) if started is not None else self.current_stage, datetime.now(timezone.utc))
            self._phases.append(handle)
            if self.sre is not None:
                self.sre.phase_names[id(handle)] = operation.value
            from .slo_sources import observe
            observe(self, 'phase', operation.value)
            if started is not None:
                notify(self, "event", started)
            return handle

    def end_phase(self, handle, completed=None):
        if handle is None:
            return
        with self._lock:
            if handle not in self._phases:
                return
            self._phases.remove(handle)
            if completed is not None:
                notify(self, "event", completed)
            try:
                handle[0].end()
                from .slo_sources import observe
                observe(self, 'phase', self.sre.phase_names.pop(id(handle), 'unknown') if self.sre is not None else 'unknown', True)
            except Exception:
                degraded(self.runtime)
            finally:
                if handle[1] is not None:
                    try:
                        context.detach(handle[1])
                    except Exception:
                        degraded(self.runtime)
            if self._phases:
                parent = self._phases[-1]
                self.current_stage, self.stage_started_at = parent[3], parent[4]

    @contextmanager
    def attached(self):
        identity_token = _current.set(self)
        otel_token = None
        try:
            try:
                otel_token = context.attach(trace.set_span_in_context(self._root, context.Context()))
            except Exception:
                degraded(self.runtime)
            yield
        finally:
            if otel_token is not None:
                try:
                    context.detach(otel_token)
                except Exception:
                    degraded(self.runtime)
            _current.reset(identity_token)

    def finish(self, exc=None):
        return self._finish(exc)

    def _finish(self, exc=None):
        with self._lock:
            if self.status in TERMINAL_STATUSES:
                return False
            if exc is not None:
                self.failure(exc)
                if self._wire_outcome == 'ok':
                    notify(self, 'event', Stage.SSE_COMPLETED, {'slopanoc.error_code': ErrorCode.SSE_COMPLETION_MISMATCH.value})
            result = self._candidate or (RunStatus.COMPLETED if self._wire_outcome == "ok" else RunStatus.FAILED)
            if self.turn_id is None:
                self.turn_id, self.turn_id_origin = 'pre-adk:' + self.run_id, 'execution'
            # Close all outstanding phases in the owning task, including errors
            # in setup/finalization or cancellation while the generator is yielding.
            for handle in tuple(reversed(self._phases)):
                self.end_phase(handle)
            terminal = {RunStatus.COMPLETED: Stage.TURN_COMPLETED, RunStatus.FAILED: Stage.TURN_FAILED,
                        RunStatus.TIMEOUT: Stage.TURN_TIMEOUT, RunStatus.CANCELLED: Stage.TURN_CANCELLED}[result]
            self._dependencies.clear()
            self._executions.clear()
            if self.reliability is not None:
                from .deadlines import observed
                if result == RunStatus.TIMEOUT:
                    observed("turn_timeout", Category.TURN, status="TIMEOUT")
                elif result == RunStatus.CANCELLED:
                    observed("cancellation", Category.TURN, status="CANCELLED")
            self.status = result
            self.terminal_at = self.last_progress_at = datetime.now(timezone.utc)
            self._end_elapsed_ms = (self.clock()-self._start)*1000
            self.current_stage, self.stage_started_at = terminal, self.terminal_at
            if len(self.timeline) == self.timeline.maxlen:
                self.dropped_events += 1
            self.timeline.append((terminal.value, self.terminal_at, self._end_elapsed_ms))
            self._set_root_attributes()
            # Independent guards: event/status failure must still attempt span end.
            for action in (
                lambda: self._root.set_attribute('slopanoc.stage', terminal.value),
                lambda: self._root.add_event(terminal.value, self.attributes() | {'slopanoc.stage': terminal.value}),
                lambda: self._root.set_status(Status(StatusCode.OK if result == RunStatus.COMPLETED
                    else StatusCode.UNSET if result == RunStatus.CANCELLED else StatusCode.ERROR)),
                self._root.end,
            ):
                try:
                    action()
                except Exception:
                    degraded(self.runtime)
            try:
                self._sre_root_closed = not self._root.is_recording() and self.trace_id is not None
            except Exception:
                self._sre_root_closed = False
            from .slo_sources import observe
            observe(self, 'publish')
            self.project('terminal')
            try:
                self.registry.discard(self.run_id)
            except Exception:
                degraded(self.runtime)
            return True

    def backstop(self, task):
        if task.cancelled():
            return self._finish(asyncio.CancelledError())
        return self._finish(task.exception())

    def relay_closed(self, completed=False):
        with self._lock:
            self.delivery = 'relayed' if completed else 'disconnected'
            self.project('delivery')
        if self.runtime is not None:
            try:
                from .logging import emit
                with self.attached():
                    emit(self.runtime, Stage.SSE_COMPLETED, run_id=self.run_id,
                         error_code=None if completed else ErrorCode.SSE_DISCONNECTED)
            except Exception:
                degraded(self.runtime)

    def as_active_run(self):
        """M0 strict projection when genuine/native identity is available."""
        from .schemas import ActiveRun
        if self.trace_id is None or self.turn_id is None:
            return None
        with self._lock:
            return ActiveRun(run_id=self.run_id, trace_id=self.trace_id, turn_id=self.turn_id,
                session_id=self.session_id, status=self.status, current_stage=self.current_stage,
                started_at=self.started_at, stage_started_at=self.stage_started_at,
                last_progress_at=self.last_progress_at, terminal_at=self.terminal_at)

    def add_dependency(self, scope):
        with self._lock:
            if self.terminal_at is None and len(self._dependencies) < 128:
                self._dependencies[id(scope)] = scope
                self.project('dependency')

    def remove_dependency(self, scope):
        with self._lock:
            self._dependencies.pop(id(scope), None)
            if self.status not in TERMINAL_STATUSES:
                self.project('dependency')

    def reliability_snapshot(self):
        owner = self.reliability
        active = tuple(v for v in self._executions.values() if not v.closed)
        return dict(current_agent=active[-1].agent.value if active else None,
            current_tool=next((v.tool for v in reversed(active) if v.tool), None),
            remaining_seconds=owner.work.remaining if owner else None,
            progress_age_seconds=max(0,owner.clock()-owner.last_progress) if owner else None,
            stalled=owner.stalled_at is not None if owner else False,
            expired_category=owner.cause.category.value if owner and owner.cause else owner.expired_category if owner else None,
            absolute_deadline=owner.total.absolute if owner else None,
            workers=tuple((w.category.value,w.caller,w.underlying,w.outcome) for w in owner.workers) if owner else (),
            cleanup_outcome=owner.cleanup_outcome if owner else 'pending')

    def snapshot(self):
        with self._lock:
            current_span_id = self.span_id
            if self._phases:
                native = self._phases[-1][0].get_span_context()
                if native.is_valid:
                    current_span_id = format(native.span_id, '016x')
            live = tuple(s.snapshot() for s in self._dependencies.values() if not s.closed and self.terminal_at is None)
            if live and live[-1].span_id is not None:
                current_span_id = live[-1].span_id
            return Progress(self.run_id, self.session_id, self.trace_id, self.span_id, current_span_id,
                self.turn_id, self.turn_id_origin, self.status, self.current_stage,
                self.started_at, self.stage_started_at, self.last_progress_at,
                self.terminal_at, self._end_elapsed_ms if self._end_elapsed_ms is not None else (self.clock()-self._start)*1000,
                tuple(self.timeline), self.dropped_events, self.delivery, live, live[-1].dependency if live else None,
                **self.reliability_snapshot())


@contextmanager
def phase(operation, started=None, completed=None):
    turn = current_turn()
    handle = notify(turn, 'start_phase', operation, started)
    try:
        yield
    except BaseException:
        notify(turn, 'end_phase', handle)
        raise
    else:
        notify(turn, 'end_phase', handle, completed)


def observe_phase(operation, started=None, completed=None):
    """Pure observation of real sync/async boundaries; return/error unchanged."""
    def decorate(fn):
        if asyncio.iscoroutinefunction(fn):
            @wraps(fn)
            async def async_wrapper(*args, **kwargs):
                if operation == Operation.PLANNING:
                    from .deadlines import boundary
                    async with boundary(Category.PLANNING):
                        with phase(operation, started, completed):
                            return await fn(*args, **kwargs)
                with phase(operation, started, completed):
                    return await fn(*args, **kwargs)
            return async_wrapper
        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            with phase(operation, started, completed):
                return fn(*args, **kwargs)
        return sync_wrapper
    return decorate


@contextmanager
def attach_turn(turn):
    """Guard entry/exit separately; never catches exceptions from business work."""
    manager = None
    if turn is not None:
        try:
            manager = turn.attached()
        except Exception:
            degraded(turn.runtime)
    entered = False
    try:
        if manager is not None:
            try:
                manager.__enter__()
                entered = True
            except Exception:
                degraded(turn.runtime)
        yield
    finally:
        if entered:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                degraded(turn.runtime)
