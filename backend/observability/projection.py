"""One content-free projection boundary. Diagnostic only, never execution authority."""
from datetime import datetime, timezone, timedelta
from typing import Literal
from uuid import UUID
from weakref import ref
from pydantic import AwareDatetime, Field, field_validator, model_validator
from .schemas import Contract, Identifier, Code, TraceId, SpanId, Count, Duration
from .stages import RunStatus, Stage, TERMINAL_STATUSES
from .errors import ErrorCode
from .model_context import ModelAgent
from .dependency_contract import DEPENDENCIES, OPERATIONS

EventType = Literal['started', 'stage', 'agent', 'tool', 'dependency', 'stalled',
    'resumed', 'terminal', 'cleanup', 'delivery', 'identity', 'deadline']
CRITICAL = {'started', 'stalled', 'resumed', 'terminal', 'cleanup', 'delivery'}


def utcnow():
    return datetime.now(timezone.utc)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class DependencyState(Contract):
    dependency: str
    operation: str
    agent: ModelAgent | None = None
    tool: str | None = None
    span_id: SpanId | None = None
    parent_span_id: SpanId | None = None
    started_at: AwareDatetime

    @field_validator('dependency')
    @classmethod
    def known_dependency(cls, value):
        if value not in DEPENDENCIES:
            raise ValueError('Invalid dependency')
        return value

    @field_validator('operation')
    @classmethod
    def known_operation(cls, value):
        if value not in OPERATIONS:
            raise ValueError('Invalid operation')
        return value

    @field_validator('tool')
    @classmethod
    def known_tool(cls, value):
        from .tool_instrumentation import TOOLS
        if value is not None and value not in TOOLS and value != 'other':
            raise ValueError('Invalid tool')
        return value


class DiagnosticEvent(Contract):
    event_seq: Count
    state_version: Count
    event_type: EventType
    timestamp: AwareDatetime
    elapsed_ms: Duration
    stage: Stage
    status: RunStatus
    agent: ModelAgent | None = None
    tool: str | None = None
    dependency: str | None = None
    error_code: ErrorCode | None = None

    _tool = field_validator('tool')(DependencyState.known_tool.__func__)

    @field_validator('dependency')
    @classmethod
    def dependency_code(cls, value):
        if value is not None and value not in DEPENDENCIES:
            raise ValueError('Invalid dependency')
        return value


class RunState(Contract):
    run_id: str
    producer_instance_id: str
    state_version: Count
    session_id: Identifier | None = None
    turn_id: Identifier | None = None
    turn_id_origin: Literal['adk', 'execution'] | None = None
    trace_id: TraceId | None = None
    root_span_id: SpanId | None = None
    current_span_id: SpanId | None = None
    environment: Code = 'development'
    status: RunStatus
    current_stage: Stage
    current_agent: ModelAgent | None = None
    current_tool: str | None = None
    dependencies: tuple[DependencyState, ...] = Field(default=(), max_length=128)
    started_at: AwareDatetime
    stage_started_at: AwareDatetime
    last_progress_at: AwareDatetime
    heartbeat_at: AwareDatetime
    observed_at: AwareDatetime
    elapsed_ms: Duration
    work_deadline_at: AwareDatetime | None = None
    total_deadline_at: AwareDatetime | None = None
    stalled: bool = False
    ever_stalled: bool = False
    stalled_at: AwareDatetime | None = None
    terminal_at: AwareDatetime | None = None
    error_code: ErrorCode | None = None
    error_stage: Stage | None = None
    cleanup_status: Literal['pending', 'running', 'completed', 'failed', 'timeout'] = 'pending'
    delivery_status: Literal['pending', 'relayed', 'disconnected'] = 'pending'
    service_version: Code | None = None
    git_sha: str | None = Field(default=None, pattern=r'^[a-fA-F0-9]{7,64}$')
    release_id: Code | None = None
    revision: Code | None = None
    region: Code | None = None
    config_version: str | None = Field(default=None, pattern=r'^[a-f0-9]{16}$')
    telemetry_schema_version: Literal[1] = 1
    projection_schema_version: Literal[1] = 1
    retention_class: Literal['normal', 'exceptional'] = 'exceptional'
    timeline_truncated_count: Count = 0
    events: tuple[DiagnosticEvent, ...] = Field(default=(), max_length=128)

    _tool = field_validator('current_tool')(DependencyState.known_tool.__func__)

    @field_validator('run_id', 'producer_instance_id')
    @classmethod
    def uuid_id(cls, value):
        if len(value) != 36 or str(UUID(value)) != value:
            raise ValueError('Invalid identity')
        return value

    @model_validator(mode='after')
    def invariants(self):
        terminal = self.status in TERMINAL_STATUSES
        if terminal != (self.terminal_at is not None):
            raise ValueError('Invalid terminal state')
        if terminal and (self.current_agent or self.current_tool or self.dependencies):
            raise ValueError('Terminal state has active work')
        if (self.turn_id is None) != (self.turn_id_origin is None):
            raise ValueError('Invalid turn identity')
        if any(t < self.started_at for t in (self.stage_started_at, self.last_progress_at, self.observed_at, self.heartbeat_at)):
            raise ValueError('Invalid chronology')
        seqs = [e.event_seq for e in self.events]
        if seqs != sorted(set(seqs)) or any(e.state_version > self.state_version for e in self.events):
            raise ValueError('Invalid event sequence')
        return self


def retain_events(events):
    """128 total; latest critical types get eight reserved slots plus run start."""
    if len(events) <= 128:
        return tuple(events), 0
    protected = {0}
    for kind in CRITICAL:
        candidates = [i for i, e in enumerate(events) if e.event_type == kind]
        if candidates:
            protected.add(candidates[-1])
    ordinary = [i for i in range(len(events)) if i not in protected]
    keep = protected | set(ordinary[-(128-len(protected)):])
    return tuple(e for i, e in enumerate(events) if i in keep), len(events)-len(keep)


class Publisher:
    def __init__(self, turn, coordinator):
        self.turn = ref(turn)
        self.coordinator = coordinator
        self.version = self.seq = self.truncated = 0
        self.events = ()
        self.ever_stalled = False
        self.operational_seen = False
        self.last_signature = None
        self.deadlines = None

    def publish(self, event_type=None, *, checkpoint=False):
        turn = self.turn()
        if turn is None:
            return
        with turn._lock:
            p = turn.snapshot()
            terminal = p.status in TERMINAL_STATUSES
            if terminal and event_type not in ('terminal', 'cleanup', 'delivery'):
                return
            if self.events and any(e.event_type == 'terminal' for e in self.events) and event_type == 'terminal':
                return
            self.ever_stalled |= p.stalled
            # Safe server-owned producers classify operational work; never inspect content.
            from .tool_instrumentation import TOOLS
            category = TOOLS.get(p.current_tool, 'other') if p.current_tool else None
            self.operational_seen |= bool(category not in (None, 'utility', 'internal_coordination') or
                p.current_agent not in (None, 'team_manager', 'system') or
                any(d.dependency in ('knowledge','power_automate_gateway') for d in p.dependencies) or
                p.current_stage in (Stage.KNOWLEDGE_SELECTION_COMPLETED,Stage.PROCEDURE_ACTION_RESOLVED,
                    Stage.COMMAND_AUTHORITY_COMPLETED,Stage.APPROVAL_REQUESTED,Stage.APPROVAL_COMPLETED))
            now = utcnow()
            owner = turn.reliability
            work = total = stalled_at = None
            if owner:
                if self.deadlines is None:
                    reference = owner.clock()
                    self.deadlines = (now + timedelta(seconds=owner.work.absolute-reference),
                        now + timedelta(seconds=owner.total.absolute-reference))
                work, total = self.deadlines
                if owner.stalled_at is not None:
                    stalled_at = now - timedelta(seconds=max(0, owner.clock()-owner.stalled_at))
            deps = tuple(DependencyState(dependency=d.dependency, operation=d.operation,
                agent=d.agent, tool=d.tool, span_id=d.span_id, parent_span_id=d.parent_span_id,
                started_at=d.started_at) for d in p.dependencies)
            sig = (p.status, p.current_stage, p.current_agent, p.current_tool,
                   tuple((d.dependency,d.operation,d.span_id,d.started_at) for d in deps),
                   p.cleanup_outcome, p.delivery, p.turn_id, turn._session_verified)
            if event_type and sig == self.last_signature:
                return
            self.last_signature = sig
            self.version += 1
            config = self.coordinator.config
            if event_type:
                self.seq += 1
                event = DiagnosticEvent(event_seq=self.seq, state_version=self.version,
                    event_type=event_type, timestamp=now, elapsed_ms=p.elapsed_ms,
                    stage=p.current_stage, status=p.status, agent=p.current_agent,
                    tool=p.current_tool, dependency=p.current_dependency, error_code=turn.error_code)
                self.events, dropped = retain_events((*self.events, event))
                self.truncated += dropped
            self.coordinator.submit(RunState(run_id=p.run_id, producer_instance_id=self.coordinator.instance_id,
                state_version=self.version, session_id=p.session_id if turn._session_verified else None, turn_id=p.turn_id,
                turn_id_origin=p.turn_id_origin, trace_id=p.trace_id, root_span_id=p.span_id,
                current_span_id=p.current_span_id, environment=config.otel_environment,
                status=p.status, current_stage=p.current_stage, current_agent=p.current_agent,
                current_tool=p.current_tool, dependencies=deps, started_at=p.started_at,
                stage_started_at=p.stage_started_at, last_progress_at=p.last_progress_at,
                heartbeat_at=now, observed_at=now, elapsed_ms=p.elapsed_ms,
                work_deadline_at=work, total_deadline_at=total, stalled=p.stalled,
                ever_stalled=self.ever_stalled, stalled_at=stalled_at, terminal_at=p.terminal_at,
                error_code=turn.error_code, error_stage=turn.error_stage, cleanup_status=p.cleanup_outcome, delivery_status=p.delivery,
                service_version=config.otel_service_version, git_sha=config.otel_git_sha,
                release_id=config.otel_release_id, revision=config.otel_cloud_run_revision,
                region=config.otel_region, config_version=self.coordinator.config_version,
                retention_class='normal' if terminal and p.status == RunStatus.COMPLETED and turn._session_verified and not self.ever_stalled and not self.operational_seen else 'exceptional', timeline_truncated_count=self.truncated, events=self.events))
