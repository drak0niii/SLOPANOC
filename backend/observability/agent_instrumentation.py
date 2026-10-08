"""Semantic execution scopes. No execution, evidence or authority decisions."""
import asyncio
from contextlib import contextmanager
import time
from opentelemetry import context, trace
from opentelemetry.trace import Status, StatusCode
from .model_context import ModelAgent, ModelPurpose, Attribution, _override, _execution, _runtime
from .model_instrumentation import guarded
from .turn_trace import current_turn, degraded
from .stages import RunStatus, Stage
from .errors import ErrorCode


def active_execution():
    scope = _execution.get()
    if scope is None or scope.closed or (scope.turn is not None and current_turn() is not scope.turn):
        return None
    return scope


def execution_parent(turn):
    scope = active_execution()
    return scope.span if scope is not None and scope.turn is turn else None


def runtime_for_execution():
    turn = current_turn()
    if turn is not None:
        return turn.runtime
    from .runtime import current_runtime
    return _runtime.get() or current_runtime()


class Execution:
    """Start/end once; attach only while driving work, never across yielded events."""
    def __init__(self, kind, agent, purpose=ModelPurpose.SPECIALIST, *, tool=None, category=None,
                 workload=None, role='unknown', runtime=None):
        self.kind, self.agent, self.purpose = kind, ModelAgent(agent), ModelPurpose(purpose)
        self.tool, self.category, self.role = tool, category, role
        self.runtime = runtime or runtime_for_execution()
        self.turn = current_turn() if workload not in ('ingestion', 'warmup', 'background') else None
        self.workload = workload or ('user_turn' if self.turn else 'background')
        candidate = active_execution()
        self.parent_scope = candidate if candidate is not None and candidate.turn is self.turn and candidate.workload == self.workload else None
        self.started, self.closed = time.monotonic(), False
        self.status, self.error = RunStatus.RUNNING, None
        self.result_category, self.result_count = 'unknown', None
        self.executed = kind == 'agent'
        if self.turn is not None:
            guarded(self.runtime, self.turn.add_execution, self)
        from .slo_sources import observe
        observe(self.turn, "require", kind)
        self.span = trace.INVALID_SPAN
        if self.runtime and self.runtime.enabled and not self.runtime.closed:
            parent = self.parent_scope.span if self.parent_scope else self.turn.model_parent() if self.turn else trace.INVALID_SPAN
            self.span = guarded(self.runtime, self.runtime.tracer.start_span, 'slopanoc.'+kind,
                context=trace.set_span_in_context(parent, context.Context()), attributes=self.attributes()) or trace.INVALID_SPAN
            guarded(self.runtime, self.span.add_event, kind+'.started')

    def attributes(self):
        attrs = self.turn.attributes() if self.turn else {}
        attrs.update({'slopanoc.agent':self.agent.value, 'slopanoc.execution_role':self.role,
            'slopanoc.execution_purpose':self.purpose.value, 'slopanoc.status':self.status.value,
            'slopanoc.workload':self.workload, 'slopanoc.result_category':self.result_category})
        if self.tool is not None:
            attrs.update({'slopanoc.tool':self.tool, 'slopanoc.tool_category':self.category,
                'slopanoc.attempt':1, 'slopanoc.execution_disposition':'executed' if self.executed else 'not_executed'})
        if self.result_count is not None:
            attrs['slopanoc.result_count'] = self.result_count
        if self.error:
            attrs['slopanoc.error_code'] = self.error.value
        return attrs

    @contextmanager
    def attached(self):
        token = _execution.set(self)
        attribution = _override.set(Attribution(self.agent, self.purpose, self.workload, self))
        native = guarded(self.runtime, context.attach, trace.set_span_in_context(self.span, context.Context()))
        try:
            yield self
        finally:
            if native is not None:
                guarded(self.runtime, context.detach, native)
            _override.reset(attribution)
            _execution.reset(token)

    def finish(self, error=None):
        from .deadlines import cancellation_cause
        error = cancellation_cause(error)
        if self.closed:
            return
        self.closed = True
        if self.turn is not None:
            guarded(self.runtime, self.turn.remove_execution, self)
        if isinstance(error, (asyncio.CancelledError, GeneratorExit)):
            self.status, self.error = RunStatus.CANCELLED, ErrorCode.TURN_CANCELLED
        elif isinstance(error, TimeoutError):
            self.status = RunStatus.TIMEOUT
            self.error = ErrorCode.TOOL_TIMEOUT if self.kind == 'tool' else ErrorCode.TURN_TIMEOUT
        elif error is not None:
            self.status = RunStatus.FAILED
            self.error = ErrorCode.TOOL_ERROR if self.kind == 'tool' else ErrorCode.SPECIALIST_FAILED
        elif self.status == RunStatus.RUNNING:
            self.status = RunStatus.COMPLETED
        duration = max(0, (time.monotonic()-self.started)*1000)
        from .redaction import safe_span_attributes
        attrs = (guarded(self.runtime, self.attributes) or {}) | {'slopanoc.duration_ms':duration}
        if self.kind == 'tool' and self.status == RunStatus.TIMEOUT:
            attrs['slopanoc.timeout_observed'] = True
        guarded(self.runtime, self.span.set_attributes, guarded(self.runtime, safe_span_attributes, attrs) or {})
        if self.status in (RunStatus.FAILED, RunStatus.TIMEOUT):
            guarded(self.runtime, self.span.set_status, Status(StatusCode.ERROR))
        event = self.kind+('.timeout' if self.kind == 'tool' and self.status == RunStatus.TIMEOUT else
            '.failed' if self.status in (RunStatus.FAILED, RunStatus.TIMEOUT) else '.completed')
        guarded(self.runtime, self.span.add_event, event, attributes={'slopanoc.status':self.status.value})
        guarded(self.runtime, self.span.end)
        from .slo_sources import observe
        guarded(self.runtime, lambda: observe(self.turn, 'component', self.kind, self.span.get_span_context().is_valid, agent=self.agent.value, tool=self.tool))
        from .execution_metrics import record
        guarded(self.runtime, record, self, duration)


def start_agent(agent):
    """Use model-owned canonical identity, matching explicit M3 remediation purpose."""
    model = getattr(agent, 'model', None)
    attribution = getattr(model, '_attribution', None)
    owner = attribution.agent if attribution else ModelAgent(agent.name)
    purpose = attribution.operation if attribution else ModelPurpose.SPECIALIST
    workload = attribution.workload if attribution else None
    from .model_context import current_attribution
    override = current_attribution()
    if override is not None and override.agent == owner:
        purpose, workload = override.operation, override.workload or workload
    role = 'coordination' if owner == ModelAgent.TEAM_MANAGER else 'system' if workload == 'ingestion' else 'unknown'
    parent = active_execution()
    if parent is not None and getattr(parent, 'child_role', None) == (owner.value, 'primary'):
        role = 'primary'
    return Execution('agent', owner, purpose, workload=workload, role=role)


@contextmanager
def attached_scope(scope):
    manager = None
    entered = False
    if scope is not None:
        manager = guarded(scope.runtime, scope.attached)
        if manager is not None:
            try:
                manager.__enter__()
                entered = True
            except Exception:
                degraded(scope.runtime)
    try:
        yield
    finally:
        if entered:
            guarded(scope.runtime, manager.__exit__, None, None, None)


def observe_primary(agent):
    scope = active_execution()
    if scope is not None and agent in {v.value for v in ModelAgent}:
        scope.child_role = (agent, 'primary')


def finish_scope(scope, error=None):
    try:
        scope.finish(error)
    except Exception:
        degraded(scope.runtime)
        scope.closed = True
        # An injected/broken finalizer cannot leave a copied live scope behind.
        if guarded(scope.runtime, scope.span.is_recording):
            guarded(scope.runtime, scope.span.end)
