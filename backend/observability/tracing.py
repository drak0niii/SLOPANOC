"""Safe OTel operations/events. Business execution remains in ChatService."""
from opentelemetry import trace
from opentelemetry.semconv.schemas import Schemas
from enum import Enum
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, Event
from opentelemetry.trace import Status, SpanContext, TraceState
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from .redaction import safe_span_attributes
from .schemas import OperationalEvent, TELEMETRY_SCHEMA_VERSION
from .stages import Stage

SCOPE = 'slopanoc.observability'
SCHEMA_URL = Schemas.V1_40_0.value


class Operation(str, Enum):
    HTTP = 'http.client'
    DB = 'db.client'
    CONNECTION = 'db.connection'
    TRANSACTION = 'db.transaction'
    STORAGE = 'storage.client'
    SECRET = 'secretmanager.client'
    KNOWLEDGE = 'knowledge.retrieval'
    AGENT = 'slopanoc.agent'
    TOOL = 'slopanoc.tool'
    MODEL = 'slopanoc.model.operation'
    MODEL_REQUEST = 'gen_ai.request'
    MODEL_VALIDATION = 'slopanoc.model.validation'
    TURN = 'slopanoc.turn'
    REQUEST = 'request'
    SESSION = 'session.load'
    ATTACHMENTS = 'attachments'
    ORCHESTRATION = 'orchestration'
    PLANNING = 'planning'
    THREAD = 'thread.resolve'
    PENDING = 'pending_interaction.resolve'
    CONTEXT = 'context.select'
    FINALIZATION = 'finalization'
    PERSISTENCE = 'persistence.final_answer'
    DELIVERY = 'sse.complete'

# M2 emits only these lifecycle observations. Model/tool/dependency stages are
# registered M0 vocabulary but are not enabled as events by M2.
TURN_EVENTS = frozenset({
    Stage.REQUEST_RECEIVED, Stage.REQUEST_VALIDATED,
    Stage.SESSION_LOAD_STARTED, Stage.SESSION_LOAD_COMPLETED,
    Stage.ATTACHMENTS_STARTED, Stage.ATTACHMENTS_COMPLETED,
    Stage.PLANNING_STARTED, Stage.PLANNING_COMPLETED, Stage.PLANNING_FAILED,
    Stage.THREAD_RESOLVE_STARTED, Stage.THREAD_RESOLVE_COMPLETED,
    Stage.PENDING_INTERACTION_RESOLVE_STARTED, Stage.PENDING_INTERACTION_RESOLVE_COMPLETED,
    Stage.CONTEXT_SELECTION_STARTED, Stage.CONTEXT_SELECTION_COMPLETED,
    Stage.APPROVAL_REQUESTED, Stage.APPROVAL_COMPLETED,
    Stage.AGENT_TEAM_MANAGER, Stage.SOURCE_REQUIREMENTS_COMPLETED, Stage.AUTHORITY_SELECTED,
    Stage.PROVENANCE_COMPLETED, Stage.COMMAND_EGRESS_COMPLETED,
    Stage.SYNTHESIS_STARTED, Stage.SYNTHESIS_COMPLETED,
    Stage.PERSISTENCE_STARTED, Stage.PERSISTENCE_COMPLETED,
    Stage.SSE_STARTED, Stage.SSE_COMPLETED,
    Stage.TURN_COMPLETED, Stage.TURN_FAILED, Stage.TURN_TIMEOUT, Stage.TURN_CANCELLED,

})


MODEL_EVENTS = frozenset({Stage.MODEL_REQUEST_STARTED, Stage.MODEL_FIRST_TOKEN,
    Stage.MODEL_REQUEST_COMPLETED, Stage.MODEL_REQUEST_FAILED, Stage.MODEL_REQUEST_TIMEOUT})


EXECUTION_EVENTS = frozenset({Stage.AGENT_STARTED, Stage.AGENT_COMPLETED, Stage.AGENT_FAILED,
    Stage.TOOL_STARTED, Stage.TOOL_COMPLETED, Stage.TOOL_FAILED, Stage.TOOL_TIMEOUT,
    Stage.APPROVAL_REQUESTED, Stage.APPROVAL_COMPLETED,
    Stage.PROCEDURE_ACTION_RESOLVED, Stage.COMMAND_AUTHORITY_COMPLETED})


def safe_events(events, *, model=False, execution=False):
    result = []
    for event in events:
        from .reliability_contract import EVENTS, project
        if event.name in EVENTS and not model and not execution:
            result.append(Event(event.name, attributes=project(event.attributes or {}), timestamp=event.timestamp))
            continue
        try:
            stage = Stage(event.name)
        except (ValueError, TypeError):
            continue
        if stage not in (MODEL_EVENTS if model else EXECUTION_EVENTS if execution else TURN_EVENTS):
            continue
        attrs = safe_span_attributes(event.attributes)
        attrs['slopanoc.stage'] = stage.value
        result.append(Event(stage.value, attributes=attrs, timestamp=event.timestamp))
    return tuple(result)



def safe_scope():
    return InstrumentationScope(SCOPE, version=str(TELEMETRY_SCHEMA_VERSION), schema_url=SCHEMA_URL)


def safe_context(value):
    if value is None:
        return None
    return SpanContext(trace_id=value.trace_id, span_id=value.span_id,
        is_remote=value.is_remote, trace_flags=value.trace_flags, trace_state=TraceState())


def current_trace_ids():
    from .turn_trace import current_turn
    from .stages import TERMINAL_STATUSES
    turn = current_turn(include_terminal=True)
    if turn is not None and turn.status in TERMINAL_STATUSES:
        return None, None  # copied late-worker context cannot become fresh correlation
    native = trace.get_current_span()
    from .model_context import _execution
    copied = _execution.get()
    if copied is not None and copied.closed and native is copied.span:
        native = turn.model_parent() if turn is not None else trace.INVALID_SPAN
    context = native.get_span_context()
    return (format(context.trace_id, '032x'), format(context.span_id, '016x')) if context.is_valid else (None, None)


class SafeSpanProcessor(SpanProcessor):
    def __init__(self, queue, resource, runtime=None):
        self.queue, self.resource = queue, resource
        self.runtime=runtime
        from threading import RLock
        self._lock = RLock()
        self._populations = {}

    def on_start(self, span, parent_context=None):
        if not span.instrumentation_scope or span.instrumentation_scope.name != SCOPE:
            return
        if span.name not in {v.value for v in Stage}|{v.value for v in Operation}|{v.value for v in OperationalEvent}:
            with self._lock:
                state=self._populations.get(span.context.trace_id)
                if state is not None:state['overflow']=True
            return
        with self._lock:
            tid = span.context.trace_id
            if tid not in self._populations:
                if len(self._populations) >= 256:
                    self.queue.health.add('trace', 'filtered')
                    return
                self._populations[tid] = {'ids':set(), 'open':set(), 'governed':False, 'stalled':False, 'safety':False}
            state = self._populations[tid]
            if len(state['ids']) >= 10000:
                state['overflow'] = True
                return
            state['ids'].add(format(span.context.span_id, '016x'))
            state['open'].add(span.context.span_id)

    def on_end(self, span):
        # Allowlisted operation names, not arbitrary user/model names. Strip raw
        # events/links/status descriptions; only typed M2 lifecycle events survive.
        if not span.instrumentation_scope or span.instrumentation_scope.name != SCOPE:
            self.queue.health.add('trace', 'filtered')
            return
        if span.name not in {x.value for x in Stage} | {x.value for x in OperationalEvent} | {x.value for x in Operation}:
            self.queue.health.add('trace', 'filtered')
            return
        from .privacy_policy import attributes as project_attributes, Surface
        producer = 'model' if span.name in {Operation.MODEL_REQUEST.value,Operation.MODEL_VALIDATION.value,Operation.MODEL.value} else (
            'agent' if span.name == Operation.AGENT.value else 'tool' if span.name == Operation.TOOL.value else
            'dependency' if span.name in {Operation.HTTP.value,Operation.DB.value,Operation.STORAGE.value,Operation.SECRET.value,Operation.CONNECTION.value,Operation.TRANSACTION.value} else 'turn')
        attributes = project_attributes(span.attributes,producer=producer,surface=Surface.TRACE,
            environment=self.resource.attributes.get('deployment.environment.name'))
        events = safe_events(span.events, model=span.name in {Operation.MODEL_REQUEST.value, Operation.MODEL_VALIDATION.value}, execution=span.name in {Operation.AGENT.value, Operation.TOOL.value})
        with self._lock:
            state = self._populations.get(span.context.trace_id)
            if state is None and (span.parent is None or span.parent.is_remote or span.name==Operation.TURN.value):
                # Capacity/registration loss is explicitly incomplete; still retain
                # known exceptional roots rather than losing them to probability.
                state={'ids':{format(span.context.span_id,'016x')},'open':set(),'overflow':True,
                    'governed':False,'stalled':False,'safety':False}
            if state is not None:
                state['open'].discard(span.context.span_id)
                names = {e.name for e in events}
                state['stalled'] |= 'run.stalled' in names
                state['governed'] |= bool(names & {'approval.requested','approval.completed','procedure_action.resolved','command_authority.completed'})
                state['safety'] |= attributes.get('slopanoc.error_code') in {'COMMAND_AUTHORITY_REJECTED','APPROVAL_REJECTED'}
                if span.parent is None or span.parent.is_remote or span.name == Operation.TURN.value:
                    from .sampling_policy import classify, VERSION, ARCHIVE_VERSION
                    from .privacy_policy import VERSION as privacy_version
                    attributes.update({'slopanoc.trace_class':classify(status=attributes.get('slopanoc.status') or ('FAILED' if span.status.status_code.name=='ERROR' else None),
                        safety=state['safety'], governed=state['governed'], ever_stalled=state['stalled']).value,
                        'slopanoc.archive.population_closed':not state['open'] and not state.get('overflow',False),
                        'slopanoc.archive.expected_span_ids':','.join(sorted(state['ids'])),
                        'slopanoc.redaction_policy':privacy_version,'slopanoc.sampling_policy':VERSION,
                        'slopanoc.archive_policy':ARCHIVE_VERSION})
                    from .sampling_policy import EXCEPTIONAL,TraceClass
                    if TraceClass(attributes['slopanoc.trace_class']) in EXCEPTIONAL:
                        from .policy_metrics import record
                        try:record(self.runtime,'slopanoc.archive.eligible')
                        except Exception:self.queue.health.add('metric','failed')
                    from .turn_trace import current_turn
                    turn = current_turn(include_terminal=True)
                    if turn is not None and turn.trace_id == format(span.context.trace_id,'032x'):
                        from hashlib import sha256
                        turn._archive_expectation = {'archive_trace_class':attributes['slopanoc.trace_class'],
                            'archive_expected_spans':len(state['ids']),
                            'archive_population_digest':sha256(','.join(sorted(state['ids'])).encode()).hexdigest(),
                            'archive_population_closed':attributes['slopanoc.archive.population_closed']}
                    self._populations.pop(span.context.trace_id, None)
        safe = ReadableSpan(name=span.name, context=safe_context(span.context), parent=safe_context(span.parent),
            resource=self.resource, attributes=attributes,
            events=safe_events(span.events, model=span.name in {Operation.MODEL_REQUEST.value, Operation.MODEL_VALIDATION.value}, execution=span.name in {Operation.AGENT.value, Operation.TOOL.value}), links=(), kind=span.kind,
            status=Status(span.status.status_code), start_time=span.start_time,
            end_time=span.end_time, instrumentation_scope=safe_scope())
        self.queue.put(safe)

    def force_flush(self, timeout_millis=30000):
        return self.queue.flush(timeout_millis)

    def shutdown(self):
        with self._lock:
            self._populations.clear()
        self.queue.shutdown()


def span(runtime, event, metadata=None):
    """Explicit metadata-only span; raw exceptions are never automatically recorded."""
    name = event.value if isinstance(event, (Stage, OperationalEvent)) else Stage(event).value
    return runtime.tracer.start_as_current_span(name,
        attributes=safe_span_attributes(metadata or {}), record_exception=False,
        set_status_on_exception=False)
