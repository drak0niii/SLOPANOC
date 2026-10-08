"""Strict server-owned accounting vocabulary and physical event identities."""
from datetime import datetime, timezone
from enum import Enum
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid5
from pydantic import AwareDatetime, Field, StrictInt, field_validator
from ..schemas import Contract, Count, Identifier, TraceId, SpanId
from ..model_context import ModelAgent, ModelPurpose
from ..model_usage import ModelUsage, ModelUsageObservation

ENVIRONMENTS = ('local', 'development', 'staging', 'production')
NAMESPACE = UUID('7e91f5c5-7431-5353-ae1e-15d8746a3ecd')
QUANTITIES = ('input_tokens', 'output_tokens', 'candidate_tokens', 'cached_tokens',
              'total_tokens', 'thought_tokens', 'tool_input_tokens', 'billable_characters')

class AttemptState(str, Enum):
    ADMITTED = 'ADMITTED'
    STARTED = 'STARTED'
    PROVIDER_FINISHED = 'PROVIDER_FINISHED'
    USAGE_CAPTURED = 'USAGE_CAPTURED'
    NOT_STARTED = 'NOT_STARTED'
    FINAL_CAPTURE_PENDING = 'FINAL_CAPTURE_PENDING'
    FINAL_CAPTURE_FAILED = 'FINAL_CAPTURE_FAILED'
    OUTCOME_UNKNOWN = 'OUTCOME_UNKNOWN'
    CONFLICT = 'CONFLICT'

class Identity(Contract):
    environment: Literal['local', 'development', 'staging', 'production']
    attempt_id: UUID
    logical_call_id: UUID
    provider_request_id: UUID
    attempt: Count = Field(ge=1)
    provider: Literal['gcp.vertex_ai', 'gcp.gemini', 'other']
    model: str = Field(pattern=r'^(other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$')
    agent: ModelAgent
    operation: ModelPurpose
    operation_type: Literal['GENERATION', 'EMBEDDING']
    workload: Literal['USER_TURN', 'SYSTEM_WARMUP', 'INGESTION', 'BACKGROUND', 'UNKNOWN']
    started_at: AwareDatetime
    deadline_at: AwareDatetime

    @field_validator('started_at', 'deadline_at')
    @classmethod
    def utc(cls, value): return value.astimezone(timezone.utc)

    @property
    def event_id(self):
        return str(uuid5(NAMESPACE, f'v1:{self.environment}:{self.attempt_id}:{self.operation_type}'))

class Payload(Contract):
    ledger_schema_version: Literal[1] = 1
    quantity_semantics_version: Literal[1] = 1
    idempotency_key_version: Literal[1] = 1
    identity: Identity
    observed_at: AwareDatetime
    provider_finished_at: AwareDatetime
    usage: ModelUsage
    status: Literal['COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED']
    run_id: UUID | None = None
    turn_id: Identifier | None = None
    trace_id: TraceId | None = None
    span_id: SpanId | None = None
    release_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.-]{1,64}$')
    revision: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.-]{1,64}$')
    git_sha: str | None = Field(default=None, pattern=r'^[a-fA-F0-9]{7,64}$')
    region: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_.-]{1,64}$')
    config_version: str = Field(pattern=r'^[a-f0-9]{16}$')

    @field_validator('observed_at', 'provider_finished_at')
    @classmethod
    def utc(cls, value): return value.astimezone(timezone.utc)

    def digest(self):
        # Delivery time/native span correlation cannot change metered identity.
        import json
        body = self.model_dump(mode='json')
        body.pop('observed_at')
        return sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

class Adjustment(Contract):
    """Trusted internal correction only; no HTTP mutation endpoint or estimation."""
    correction_id: UUID
    original_event_id: UUID
    environment: Literal['local', 'development', 'staging', 'production']
    reason: Literal['PROVIDER_LATE_USAGE', 'QUANTITY_CORRECTION', 'REVERSAL']
    process: Literal['accounting_recovery', 'provider_usage_adapter']
    at: AwareDatetime
    # Explicit values are adjustments; null means no adjustment, NOT zero.
    quantities: dict[str, StrictInt | None]

    @field_validator('quantities')
    @classmethod
    def bounded(cls, values):
        if set(values) - set(QUANTITIES) or any(type(v) is not int or abs(v) > 2**63-1 for v in values.values() if v is not None):
            raise ValueError('Invalid correction quantities')
        return values


def identity_for(attempt, config):
    op = attempt.operation
    from datetime import timedelta
    return Identity(environment=config.otel_environment, attempt_id=attempt.id,
        logical_call_id=op.id, provider_request_id=attempt.request_id, attempt=attempt.number,
        provider=op.provider, model=op.model, agent=op.attribution.agent,
        operation=op.attribution.operation,
        operation_type='EMBEDDING' if op.attribution.operation.value.startswith('embedding') else 'GENERATION',
        workload={'user_turn':'USER_TURN','warmup':'SYSTEM_WARMUP','ingestion':'INGESTION','background':'BACKGROUND'}.get(op.workload, 'UNKNOWN'),
        started_at=attempt.started_at,
        deadline_at=attempt.started_at+timedelta(seconds=config.model_timeout_seconds+config.cleanup_timeout_seconds))


def payload_for(identity, observation: ModelUsageObservation, config):
    # Revalidate even frozen M3 data; never serialize raw provider response.
    observation = ModelUsageObservation.model_validate(observation.model_dump())
    if str(identity.attempt_id) != observation.attempt_id or str(identity.logical_call_id) != observation.logical_call_id:
        raise ValueError('Accounting observation identity mismatch')
    return Payload(identity=identity, observed_at=datetime.now(timezone.utc),
        provider_finished_at=observation.completed_at, usage=observation.usage,
        status=observation.status.value, run_id=observation.run_id,
        turn_id=observation.turn_id, trace_id=observation.trace_id, span_id=observation.span_id,
        release_id=config.otel_release_id, revision=config.otel_cloud_run_revision,
        git_sha=config.otel_git_sha, region=config.otel_region,
        config_version=config.effective_configuration()[0].config_version)
