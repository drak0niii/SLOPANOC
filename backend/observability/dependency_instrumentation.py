"""Observational dependency scopes; business calls never enter telemetry guards."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
import time
from opentelemetry import context, trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from .dependency_contract import DEPENDENCIES, OPERATIONS, SPAN_NAMES, project
from .model_instrumentation import guarded
from .agent_instrumentation import active_execution, runtime_for_execution
from .turn_trace import current_turn
from .stages import RunStatus
from .errors import ErrorCode

_active = ContextVar('slopanoc_dependency', default=None)

# Concrete import/call sites and owning milestone; coverage tests reject new sinks.
CLIENT_OWNERS = {
    'backend/observability/database.py': 'M7 detached projection health (no business dependency recursion)',
    'backend/gateway/power_automate_client.py': 'M5',
    'backend/api/session_service.py': 'M5',
    'backend/cases/db.py': 'M5',
    'backend/attachments/repository.py': 'M5',
    'backend/knowledge/repository/sqlalchemy.py': 'M5',
    'backend/attachments/storage.py': 'M5',
    'backend/knowledge_ingestion/artifact_storage.py': 'M5',
    'backend/config/settings.py': 'M5/M3',
    'backend/tools/knowledge/dense_similarity.py': 'M3',
    'backend/knowledge/embeddings/service.py': 'M3',
    'backend/observability/model_provider.py': 'M3',
    'backend/observability/exporters/otlp.py': 'M1',
}


def active_dependency(turn=None):
    scope = _active.get()
    return scope if scope and not scope.closed and scope.turn is turn and (turn is None or current_turn() is turn) else None


def dependency_parent(turn):
    scope = active_dependency(turn)
    return scope.span if scope else None


@dataclass(frozen=True)
class LiveDependency:
    dependency: str
    operation: str
    span_id: str | None
    parent_span_id: str | None
    agent: str | None
    tool: str | None
    started_at: datetime
    elapsed_ms: float


class Dependency:
    def __init__(self, dependency, operation, name, *, kind='external', attributes=None):
        self.dependency = dependency if dependency in DEPENDENCIES else 'other'
        self.operation = operation if operation in OPERATIONS else 'other'
        self.name = name if name in SPAN_NAMES else 'http.client'
        self.kind = kind if kind in {'external', 'local', 'acquisition', 'transaction'} else 'external'
        self.runtime = guarded(None, runtime_for_execution)
        execution = active_execution()
        from .model_context import current_attribution
        attribution = current_attribution()
        self.turn = current_turn()
        if execution is not None and execution.workload in ('ingestion', 'warmup', 'background'):
            self.turn = execution.turn
        if attribution and attribution.workload in ('ingestion', 'warmup', 'background'):
            self.turn = None
            if execution and execution.turn is not None:
                execution = None
        self.owner = execution
        self.workload = execution.workload if execution else attribution.workload if attribution and attribution.workload else 'user_turn' if self.turn else 'background'
        parent = dependency_parent(self.turn)
        if parent is None:
            parent = execution.span if execution else self.turn.model_parent() if self.turn else trace.INVALID_SPAN
        self.parent = parent
        self.span = trace.INVALID_SPAN
        self.closed = False
        self.started = time.monotonic()
        self.started_at = datetime.now(timezone.utc)
        self.status, self.error = RunStatus.RUNNING, None
        self.attrs = project(attributes or {})
        self.caller_disposition = "completed"
        self.worker_disposition = "running"
        self.outcome_certainty = None
        self.attrs.update({'slopanoc.dependency': self.dependency,
            'slopanoc.dependency_operation': self.operation, 'slopanoc.dependency_kind': self.kind,
            'slopanoc.workload': self.workload})
        if self.turn:
            self.attrs.update(self.turn.attributes())
        if execution:
            self.attrs['slopanoc.agent'] = execution.agent.value
            if execution.tool:
                self.attrs['slopanoc.tool'] = execution.tool
        self.token = self.native = None

    def begin(self):
        from .slo_sources import observe
        observe(self.turn, "require", "dependency")
        from .redaction import safe_span_attributes
        if self.runtime and self.runtime.enabled and not self.runtime.closed:
            self.span = guarded(self.runtime, self.runtime.tracer.start_span, self.name,
                kind=SpanKind.CLIENT if self.kind == 'external' else SpanKind.INTERNAL,
                context=trace.set_span_in_context(self.parent, context.Context()),
                attributes=guarded(self.runtime, safe_span_attributes, self.attrs) or {}) or trace.INVALID_SPAN
        self.token = _active.set(self)
        self.native = guarded(self.runtime, context.attach, trace.set_span_in_context(self.span, context.Context()))
        if self.turn and self.kind != 'local':
            guarded(self.runtime, self.turn.add_dependency, self)

    def update(self, **attributes):
        self.attrs.update(project(attributes))

    def fail(self, code, kind='unknown', origin='unknown', *, timeout=False):
        self.status = RunStatus.TIMEOUT if timeout else RunStatus.FAILED
        self.error = ErrorCode(code)
        self.update(**{'slopanoc.failure_kind': kind, 'slopanoc.error_origin': origin})
        if self.kind == 'transaction':
            self.update(**{'slopanoc.transaction_outcome': 'failure'})

    def finish(self, exc=None):
        if exc is not None:
            from .deadlines import current_budget
            budget = current_budget()
            if isinstance(exc, asyncio.CancelledError) and budget is not None and budget.expired:
                self.caller_disposition = 'timeout'
                self.outcome_certainty = 'OUTCOME_UNKNOWN'
                self.worker_disposition = 'unknown'
                exc = TimeoutError()
            if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
                self.status, self.error = RunStatus.CANCELLED, ErrorCode.TURN_CANCELLED
            elif self.status == RunStatus.RUNNING:
                classify_exception(self, exc)
        if self.status == RunStatus.RUNNING:
            self.status = RunStatus.COMPLETED
        from .reliability_contract import project as reliability_project
        attrs = self.attrs | reliability_project({'slopanoc.caller_disposition':self.caller_disposition,
            'slopanoc.worker_disposition':self.worker_disposition, 'slopanoc.outcome_certainty':self.outcome_certainty}) | {'slopanoc.status': self.status.value,
            'slopanoc.duration_ms': max(0, (time.monotonic() - self.started) * 1000)}
        if self.error:
            attrs['slopanoc.error_code'] = self.error.value
        from .redaction import safe_span_attributes
        guarded(self.runtime, self.span.set_attributes, guarded(self.runtime, safe_span_attributes, attrs) or {})
        if self.status in (RunStatus.FAILED, RunStatus.TIMEOUT):
            guarded(self.runtime, self.span.set_status, Status(StatusCode.ERROR))
        from .slo_sources import dependency_observation
        guarded(self.runtime, dependency_observation, self)
        from .dependency_metrics import record
        guarded(self.runtime, record, self, attrs['slopanoc.duration_ms'])
        if self.error and self.status in (RunStatus.FAILED, RunStatus.TIMEOUT):
            from .logging import emit_dependency_failure
            if self.runtime and self.runtime.enabled:
                guarded(self.runtime, emit_dependency_failure, self.runtime, self)

    def close(self, exc=None):
        if self.closed:
            return
        self.closed = True
        self.worker_disposition = 'terminated'
        from .blocking_work import _work
        work = _work.get()
        if work is not None:
            self.caller_disposition = work.caller
            if work.caller in ('timeout','cancelled'):
                self.outcome_certainty = 'OUTCOME_UNKNOWN'
        # Independent cleanup guards: a broken finalizer cannot retain live state.
        guarded(self.runtime, self.finish, exc)
        guarded(self.runtime, self.span.end)
        if self.turn:
            guarded(self.runtime, self.turn.remove_dependency, self)
            guarded(self.runtime, self.turn.progress)
        if self.native is not None:
            guarded(self.runtime, context.detach, self.native)
        if self.token is not None:
            guarded(self.runtime, _active.reset, self.token)

    def snapshot(self):
        native, parent = self.span.get_span_context(), self.parent.get_span_context()
        return LiveDependency(self.dependency, self.operation,
            format(native.span_id, '016x') if native.is_valid else None,
            format(parent.span_id, '016x') if parent.is_valid else None,
            self.owner.agent.value if self.owner else None, self.owner.tool if self.owner else None,
            self.started_at, max(0, (time.monotonic() - self.started) * 1000))


def classify_exception(scope, exc):
    """Only typed exceptions/scalar status; never parse/stringify exception data."""
    from sqlalchemy.exc import TimeoutError as PoolTimeout, DBAPIError, InterfaceError, OperationalError
    from requests.exceptions import Timeout, ConnectTimeout, ReadTimeout, ConnectionError
    from google.api_core.exceptions import DeadlineExceeded, TooManyRequests, Unauthorized, Forbidden, NotFound
    timeout = isinstance(exc, (TimeoutError, Timeout, PoolTimeout, DeadlineExceeded))
    kind = 'connect_timeout' if isinstance(exc, ConnectTimeout) else 'read_timeout' if isinstance(exc, ReadTimeout) else 'timeout' if timeout else 'unknown'
    if scope.dependency in {'session_db', 'case_db', 'attachment_db', 'knowledge_db'}:
        if isinstance(exc, PoolTimeout):
            kind = 'pool_timeout'
        connection = isinstance(exc, InterfaceError) or isinstance(exc, DBAPIError) and exc.connection_invalidated
        connection = connection or scope.operation in {'CONNECT', 'CHECKOUT'}
        code = ErrorCode.DATABASE_TIMEOUT if timeout else ErrorCode.DATABASE_CONNECTION_ERROR if connection else ErrorCode.DATABASE_PERSISTENCE_ERROR if scope.operation == 'COMMIT' else ErrorCode.DATABASE_QUERY_ERROR
        if not timeout:
            kind = 'connection' if connection else 'persistence' if scope.operation == 'COMMIT' else 'query'
        origin = 'database'
    elif scope.dependency in {'chat_attachments', 'knowledge_artifacts'}:
        code, origin = (ErrorCode.STORAGE_TIMEOUT if timeout else ErrorCode.STORAGE_ERROR), 'storage'
    elif scope.dependency == 'knowledge':
        code, origin = (ErrorCode.KNOWLEDGE_TIMEOUT if timeout else ErrorCode.KNOWLEDGE_PROVIDER_ERROR), 'knowledge'
    else:
        code = ErrorCode.TOOL_TIMEOUT if timeout else ErrorCode.TOOL_ERROR
        origin = 'gateway_transport' if scope.dependency == 'power_automate_gateway' else 'secret_manager' if scope.dependency == 'secret_manager' else 'unknown'
    if isinstance(exc, (TooManyRequests, Unauthorized, Forbidden, NotFound)):
        kind = 'rate_limit' if isinstance(exc, TooManyRequests) else 'not_found' if isinstance(exc, NotFound) else 'auth'
    if isinstance(exc, ConnectionError) and not timeout:
        kind = 'connection'
    scope.fail(code, kind, origin, timeout=timeout)
    if kind == 'rate_limit':
        scope.update(**{'slopanoc.rate_limited': True})


@contextmanager
def dependency_scope(dependency, operation, name, *, kind='external', attributes=None):
    scope = guarded(None, Dependency, dependency, operation, name, kind=kind, attributes=attributes)
    error = None
    try:
        if scope:
            guarded(scope.runtime, scope.begin)
        yield scope
    except BaseException as exc:
        error = exc
        raise
    finally:
        if scope:
            guarded(scope.runtime, scope.close, error)


def update(scope, **attributes):
    if scope:
        guarded(scope.runtime, scope.update, **attributes)


def observe_call(dependency, operation, span_name, fn, /, *args, byte_count=None, **kwargs):
    with dependency_scope(dependency, operation, span_name, attributes={'slopanoc.retry_visibility': 'unknown'}) as scope:
        result = fn(*args, **kwargs)  # exactly one business invocation
        if byte_count is not None:
            def record_bytes():
                count = len(result) if byte_count == 'result' else byte_count
                update(scope, **{'slopanoc.bytes': count})
            guarded(scope.runtime if scope else None, record_bytes)
        return result
