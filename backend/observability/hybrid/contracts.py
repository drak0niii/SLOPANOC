"""Versioned safe event contracts; references never grant business authority."""
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID
from pydantic import AwareDatetime, ConfigDict, Field, TypeAdapter, field_validator, model_validator, model_serializer
from ..schemas import Contract, Count, Duration, Identifier, TraceId
from ..stages import Stage, RunStatus
from ..errors import ErrorCode
from ..model_context import ModelAgent

from backend.execution_audit.contracts import Fact, JOURNAL_EVENTS

Environment = Literal['local', 'development', 'test', 'staging', 'production']
AuditType = Literal['governance.progression.recorded','turn.bound', 'run.accepted', 'model.attempt.started', 'model.attempt.completed',
    'model.usage.captured', 'accounting.debt.created', 'accounting.debt.resolved', 'run.registered', 'run.terminal', 'provider.admitted',
    'provider.attempt', 'usage.captured', 'accounting.debt', 'approval.recorded',
    'authorization.recorded', 'action.receipt', 'governance.recorded', 'financial.published', *JOURNAL_EVENTS]
TelemetryType = Literal['execution.stage', 'execution.timing', 'dependency.timing',
    'retry.observed', 'queue.observed', 'collector.health', 'archive.health']

class AuditPayload(Contract):
    execution: Fact | None = None
    session_id: Identifier | None = None
    model_attempt_id: UUID | None = None
    agent_execution_id: UUID | None = None
    tool_execution_id: Identifier | None = None
    approval_id: Identifier | None = None
    action_id: Identifier | None = None
    # New optional references omitted when unavailable to preserve existing v1 hashes.
    @model_serializer(mode='wrap')
    def references(self, handler):
        result=handler(self)
        for name in ('session_id','model_attempt_id','tool_execution_id','approval_id','action_id','execution','agent_execution_id'):
            if result.get(name) is None:result.pop(name,None)
        return result

    # Reference the existing authoritative record; never duplicate ledger quantities.
    source_record_id: Identifier
    source_record_version: Annotated[int, Field(strict=True, ge=1, le=2**63-1)]
    capture: Literal['CAPTURED', 'RECONSTRUCTED'] = 'CAPTURED'
    status: RunStatus | None = None
    agent: ModelAgent | None = None

class TelemetryPayload(Contract):
    stage: Stage | None = None
    status: RunStatus | None = None
    agent: ModelAgent | None = None
    duration_ms: Duration | None = Field(default=None, le=86400000)
    retry_count: Count | None = None
    queue_depth: Count | None = None
    error_code: ErrorCode | None = None
    availability: Literal['CAPTURED', 'RECONSTRUCTED', 'UNKNOWN_NOT_CAPTURED'] = 'CAPTURED'

class Envelope(Contract):
    event_id: UUID
    schema_version: Literal[1] = 1
    environment: Environment
    timestamp: AwareDatetime
    run_id: UUID | None = None
    turn_id: Identifier | None = None
    trace_id: TraceId | None = None

    @field_validator('schema_version',mode='before')
    @classmethod
    def schema_integer(cls,value):
        if type(value) is not int:raise ValueError('Integer schema required')
        return value

    @field_validator("timestamp")
    @classmethod
    def utc(cls, value):
        from datetime import timezone
        return value.astimezone(timezone.utc)

class AuditEvent(Envelope):
    event_class: Literal['AUDIT'] = 'AUDIT'
    event_type: AuditType
    producer: Literal['anoc', 'accounting', 'finops']
    payload_safe: AuditPayload
    aggregate_type: Literal['run', 'provider_attempt', 'usage', 'approval', 'authorization', 'action', 'governance', 'financial_generation'] = 'usage'
    aggregate_id: Identifier | None = None
    aggregate_version: Annotated[int, Field(strict=True, ge=1, le=2**63-1)] = 1

    @model_validator(mode='after')
    def aggregate_identity(self):
        if self.aggregate_id is None:
            object.__setattr__(self, 'aggregate_id', self.payload_safe.source_record_id)
        if self.event_type in JOURNAL_EVENTS:
            fact=self.payload_safe.execution
            if fact is None or fact.execution_domain!=self.event_type.split('.')[0] or self.producer!='anoc' or self.aggregate_type!='governance':
                raise ValueError('Execution journal owner required')
            if self.aggregate_id!=self.payload_safe.source_record_id:
                raise ValueError('Execution source required')
            identity={'agent':fact.agent_execution_id,'retrieval':fact.retrieval_id,'tool':self.payload_safe.tool_execution_id,
                'authorization':fact.authorization_id,'action':fact.attempt_id}.get(fact.execution_domain)
            if fact.execution_domain in ('agent','retrieval','tool','authorization','action') and (identity is None or str(identity)!=self.aggregate_id):
                raise ValueError('Actual execution identity required')
            allowed={'agent':{'C'},'retrieval':{'C'},'tool':{'C'},'session':{'C'},'evidence':{'A','B'},'approval':{'B'},'authorization':{'D'},'action':{'D'}}
            if fact.consistency_class not in allowed[fact.execution_domain]:raise ValueError('False consistency class')
            expected={'session.created':{'CREATED'},'session.accessed':{'ACCESSED'},'evidence.selection.recorded':{'RECORDED'},
                'approval.requested':{'PENDING'},'approval.approved':{'APPROVED'},'approval.rejected':{'REJECTED'},'approval.expired':{'EXPIRED'},'approval.consumed':{'CONSUMED'},
                'authorization.decided':{'AUTHORIZED','DENIED'},'action.authorized':{'AUTHORIZED'},'action.dispatch_attempted':{'DISPATCHING'},
                'action.acknowledged':{'ACKNOWLEDGED'},'action.completed':{'COMPLETED'},'action.failed':{'FAILED'},'action.outcome_unknown':{'UNKNOWN'}}
            if self.event_type.endswith('started'):outcomes={'STARTED'}
            elif self.event_type.endswith('failed') and fact.execution_domain!='action':outcomes={'FAILED','TIMEOUT','CANCELLED'}
            elif self.event_type.endswith('completed'):outcomes={'COMPLETED'}
            else:outcomes=expected[self.event_type]
            if fact.outcome not in outcomes:raise ValueError('Event outcome mismatch')
            if fact.execution_domain=='authorization' and ((fact.decision=='ALLOW')!=(fact.outcome=='AUTHORIZED') or fact.decision is None):
                raise ValueError('Decision outcome mismatch')
        elif self.payload_safe.execution is not None:
            raise ValueError('Unexpected execution fact')
        runtime_types={'governance.progression.recorded','turn.bound','run.accepted','run.terminal','model.attempt.started','model.attempt.completed',
            'model.usage.captured','accounting.debt.created','accounting.debt.resolved'}
        if self.event_type in runtime_types:
            # Actual owners use UUID references. Generic conceptual v1 types remain
            # decodable for compatibility but are never admitted by Phase-1 workers.
            if str(UUID(self.aggregate_id))!=self.aggregate_id or self.payload_safe.source_record_id!=self.aggregate_id:
                raise ValueError('Canonical source identity required')
            expected='governance' if self.event_type=='governance.progression.recorded' else 'run' if self.event_type.startswith('run.') or self.event_type=='turn.bound' else ('usage' if self.event_type=='model.usage.captured' else 'provider_attempt')
            if self.aggregate_type!=expected or self.producer!=('anoc' if expected in ('run','governance') else 'accounting'):
                raise ValueError('Canonical owner required')
            if self.event_type.startswith('model.') and self.payload_safe.agent is None:
                raise ValueError('Canonical model agent required')
        if self.event_type=='run.accepted' and self.aggregate_version!=1 or self.event_type=='run.terminal' and self.aggregate_version!=2:
            raise ValueError('Run lifecycle sequence rejected')
        if self.event_type=='turn.bound' and (self.turn_id is None or self.aggregate_type!='run' or str(self.run_id)!=self.aggregate_id):
            raise ValueError('Durable turn binding required')
        if self.event_type in ('run.accepted','run.terminal'):
            if self.aggregate_type!='run' or self.run_id is None or self.aggregate_id!=str(self.run_id):
                raise ValueError('Run identity required')
            if self.event_type=='run.accepted' and self.payload_safe.status!=RunStatus.RUNNING:
                raise ValueError('Acceptance state required')
            if self.event_type=='run.terminal' and self.payload_safe.status not in {RunStatus.COMPLETED,RunStatus.FAILED,RunStatus.TIMEOUT,RunStatus.CANCELLED}:
                raise ValueError('Terminal state required')
        return self

class TelemetryEvent(Envelope):
    event_class: Literal['TELEMETRY'] = 'TELEMETRY'
    event_type: TelemetryType
    producer: Literal['collector_adapter'] = 'collector_adapter'
    payload_safe: TelemetryPayload

Event = Annotated[AuditEvent | TelemetryEvent, Field(discriminator='event_class')]
EVENT_ADAPTER = TypeAdapter(Event, config=ConfigDict(hide_input_in_errors=True))
MAX_EVENT_BYTES = 16384

def decode_event(data: bytes, *, environment: Environment) -> AuditEvent | TelemetryEvent:
    """Internal envelope only. Native Collector OTLP needs a separate safe adapter."""
    if not isinstance(data, bytes) or len(data) > MAX_EVENT_BYTES:
        raise ValueError('Event size/type rejected')
    event = EVENT_ADAPTER.validate_json(data)
    if event.environment != environment:
        raise ValueError('Event environment rejected')
    validate_privacy(event)
    return event

def validate_privacy(event):
    if isinstance(event,AuditEvent):
        from ..privacy_policy import field_policy, Surface
        from ..redaction import DataClassification
        fields=set(type(event).model_fields)-{'payload_safe'} | set(type(event.payload_safe).model_fields)
        for field in fields:
            policy=field_policy('hybrid_audit.'+field)
            if policy.classification==DataClassification.PROHIBITED or Surface.EVENT not in policy.surfaces:
                raise ValueError('Audit privacy policy rejected')
        if event.payload_safe.execution is not None:
            for field in type(event.payload_safe.execution).model_fields:
                policy=field_policy('execution_audit.'+field)
                if policy.classification==DataClassification.PROHIBITED or Surface.EVENT not in policy.surfaces:
                    raise ValueError('Journal privacy policy rejected')

def event_digest(event: AuditEvent | TelemetryEvent) -> str:
    # Revalidate frozen nested objects: unsafe construction/copy is not admission.
    validated = decode_event(event.model_dump_json(warnings='none').encode(), environment=event.environment)
    import json
    return sha256(json.dumps(validated.model_dump(mode='json'), sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()

class OutboxRecord(Contract):
    event: AuditEvent
    aggregate_type: Literal['run', 'provider_attempt', 'usage', 'approval',
        'authorization', 'action', 'governance', 'financial_generation']
    aggregate_id: Identifier
    publication_state: Literal['PENDING', 'CLAIMED', 'PUBLISHED', 'DEBT'] = 'PENDING'
    attempt_count: Count = 0
    published_at: AwareDatetime | None = None
    lease_token: UUID | None = None
    lease_until: AwareDatetime | None = None

    @model_validator(mode='after')
    def lifecycle(self):
        if (self.publication_state == 'PUBLISHED') != (self.published_at is not None):
            raise ValueError('Publication confirmation required')
        if self.published_at is not None and self.published_at < self.event.timestamp:
            raise ValueError('Publication precedes occurrence')
        claimed = self.publication_state == 'CLAIMED'
        if claimed != (self.lease_token is not None and self.lease_until is not None):
            raise ValueError('Claim requires a bounded lease')
        if not claimed and (self.lease_token is not None or self.lease_until is not None):
            raise ValueError('Unexpected publication lease')
        return self
