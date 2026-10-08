"""Pure M0 data/interfaces: no persistence, RBAC enforcement or runtime registry."""
from decimal import Decimal
from enum import Enum, unique
from types import MappingProxyType
from typing import Annotated, Literal, Protocol
from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator
from .attributes import Attribute, RESOURCE_ATTRIBUTES
from .errors import ErrorCode
from .redaction import validate_metadata
from .stages import RunStatus, Stage, TERMINAL_STATUSES

TELEMETRY_SCHEMA_VERSION = 1
COST_SCHEMA_VERSION = 1
PRICING_SCHEMA_VERSION = 1
REPORTING_CURRENCY = 'EUR'
LEDGER_SAMPLED = False
BILLING_PRIMARY_SOURCES = ('gcp_detailed_billing_export', 'gcp_pricing_export')
BILLING_SUPPLEMENTARY_SOURCE = 'focus'

Identifier = Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$')]
Code = Annotated[str, Field(strict=True, min_length=1, max_length=64, pattern=r'^[A-Za-z0-9_.-]+$')]
Count = Annotated[int, Field(strict=True, ge=0, le=2**63-1)]
Duration = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Money = Annotated[Decimal, Field(allow_inf_nan=False)]
def _nonzero_identifier(value: str) -> str:
    if not value.strip('0'):
        raise ValueError('Telemetry identifier must be nonzero')
    return value

TraceId = Annotated[str, Field(strict=True, pattern=r'^[0-9a-f]{32}$'), AfterValidator(_nonzero_identifier)]
SpanId = Annotated[str, Field(strict=True, pattern=r'^[0-9a-f]{16}$'), AfterValidator(_nonzero_identifier)]
SafeValue = Count

class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, validate_default=True,
                              allow_inf_nan=False, hide_input_in_errors=True)

class Correlation(Contract):
    trace_id: TraceId
    span_id: SpanId
    run_id: Identifier
    turn_id: Identifier
    session_id: Identifier

class TraceEvent(Correlation):
    telemetry_schema_version: Literal[1] = TELEMETRY_SCHEMA_VERSION
    event_id: Identifier
    parent_span_id: SpanId | None = None
    timestamp: AwareDatetime
    stage: Stage
    status: RunStatus
    agent: Code | None = None
    tool: Code | None = None
    dependency: Code | None = None
    thread_id: Identifier | None = None
    case_id: Identifier | None = None
    fault_id: Identifier | None = None
    approval_id: Identifier | None = None
    execution_id: Identifier | None = None
    knowledge_id: Identifier | None = None
    source_id: Identifier | None = None
    duration_ms: Duration | None = None
    metadata: dict[str, SafeValue] = Field(default_factory=dict)
    error_code: ErrorCode | None = None

    @model_validator(mode='after')
    def event_lifecycle(self):
        required = {
            Stage.TURN_COMPLETED: RunStatus.COMPLETED,
            Stage.TURN_FAILED: RunStatus.FAILED,
            Stage.TURN_TIMEOUT: RunStatus.TIMEOUT,
            Stage.TURN_CANCELLED: RunStatus.CANCELLED,
            Stage.RUN_STALLED: RunStatus.STALLED,
        }.get(self.stage)
        if required is not None and self.status != required:
            raise ValueError('Event stage and status disagree')
        return self

    @field_validator('metadata', mode='before')
    @classmethod
    def safe_metadata(cls, value):
        return validate_metadata(value)

class SpanRecord(TraceEvent):
    """Span description, not a span implementation; completed_at absent while active."""
    operation: Code
    started_at: AwareDatetime
    completed_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def chronological(self):
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError('Span end precedes start')
        return self

class ActiveRun(Contract):
    telemetry_schema_version: Literal[1] = TELEMETRY_SCHEMA_VERSION
    run_id: Identifier
    trace_id: TraceId
    turn_id: Identifier
    session_id: Identifier
    version: Count = 0
    status: RunStatus
    current_stage: Stage
    current_agent: Code | None = None
    current_tool: Code | None = None
    current_dependency: Code | None = None
    started_at: AwareDatetime
    stage_started_at: AwareDatetime
    last_progress_at: AwareDatetime
    heartbeat_at: AwareDatetime | None = None
    terminal_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def lifecycle(self):
        if (self.status in TERMINAL_STATUSES) != (self.terminal_at is not None):
            raise ValueError('Terminal status and timestamp must agree')
        for value in (self.stage_started_at, self.last_progress_at, self.heartbeat_at, self.terminal_at):
            if value is not None and value < self.started_at:
                raise ValueError('Run timestamp precedes start')
        return self

class ActiveRunRepository(Protocol):
    """Future Cloud SQL truth; local cache is never the support lookup authority.

    Implementations must compare-and-set version and reject terminal reopening.
    No implementation or database schema is installed in M0.
    """
    async def get(self, run_id: str) -> ActiveRun | None: ...
    async def compare_and_set(self, run: ActiveRun, expected_version: int) -> bool: ...

class TurnSummary(ActiveRun):
    duration_ms: Duration | None = None
    domain: Code | None = None
    primary_agent: Code | None = None
    authority_mode: Code | None = None
    error_code: ErrorCode | None = None
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    model_calls: Count = 0
    tool_calls: Count = 0
    estimated_cost: Money | None = None
    billed_cost: Money | None = None
    currency: Literal['EUR'] = REPORTING_CURRENCY

class UsageEvent(Correlation):
    """Append-oriented usage/correction contract, not a ledger implementation."""
    cost_schema_version: Literal[1] = COST_SCHEMA_VERSION
    pricing_schema_version: Literal[1] = PRICING_SCHEMA_VERSION
    usage_event_id: Identifier
    call_id: Identifier
    attempt: Count
    timestamp: AwareDatetime
    provider: Code
    service: Code
    model: Code
    operation: Code
    agent: Code
    domain: Code | None = None
    quantity: Annotated[Decimal, Field(ge=0, allow_inf_nan=False)] | None = None
    unit: Code
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    cached_tokens: Count | None = None
    request_count: Count = 1
    retry_count: Count = 0
    estimated_cost: Money | None = None
    billed_cost: Money | None = None
    currency: Literal['EUR'] = REPORTING_CURRENCY
    native_currency: Annotated[str, Field(pattern=r'^[A-Z]{3}$')]
    native_cost: Money | None = None
    price_version: Code | None = None
    pricing_source: Code | None = None
    usage_estimated: bool
    environment: Code
    use_case_key: Code | None = None
    event_kind: Literal['usage', 'correction', 'reversal'] = 'usage'
    original_usage_event_id: Identifier | None = None
    correction_reason: Code | None = None
    correction_actor: Identifier | None = None

    @model_validator(mode='after')
    def accounting(self):
        correction = self.event_kind != 'usage'
        if correction and not all((self.original_usage_event_id, self.correction_reason, self.correction_actor)):
            raise ValueError('Adjustment requires original event, reason and actor')
        if not correction and any((self.original_usage_event_id, self.correction_reason, self.correction_actor)):
            raise ValueError('Usage cannot masquerade as adjustment')
        if self.original_usage_event_id == self.usage_event_id:
            raise ValueError('Adjustment cannot reference itself')
        if self.estimated_cost is not None and not self.price_version:
            raise ValueError('Estimate requires reproducible price version')
        if not correction and any(v is not None and v < 0 for v in (self.estimated_cost, self.billed_cost, self.native_cost)):
            raise ValueError('Negative financial values require an adjustment')
        return self

@unique
class SettingsSection(str, Enum):
    OVERVIEW = 'overview'
    TRACING = 'tracing'
    RELIABILITY = 'reliability'
    SLOS = 'slos'
    FINOPS = 'finops'
    DATA_RETENTION = 'data_retention'
    INTEGRATIONS = 'integrations'
    ACCESS = 'access'
    DIAGNOSTICS = 'diagnostics'

SETTINGS_CATEGORY_ID = 'observability_finops'
SETTINGS_CATEGORY_NAME = 'Observability & FinOps'
SETTINGS_SECTION_NAMES = MappingProxyType(dict(zip(SettingsSection, (
    'Overview', 'Tracing', 'Reliability', 'SLOs', 'FinOps', 'Data & Retention',
    'Integrations', 'Access', 'Diagnostics',
))))

@unique
class Role(str, Enum):
    USER = 'User'
    OPERATOR = 'Operator'
    DEVELOPER_SRE = 'Developer / SRE'
    FINOPS = 'FinOps'
    ADMIN = 'Admin'
    AUDITOR = 'Auditor'

@unique
class Permission(str, Enum):
    OWN_PROGRESS = 'own_progress'
    OPERATIONAL_METADATA = 'operational_metadata'
    TECHNICAL_DIAGNOSTICS = 'technical_diagnostics'
    FINANCIAL_DATA = 'financial_data'
    EFFECTIVE_CONFIGURATION = 'effective_configuration'
    GOVERNED_SUMMARIES = 'governed_summaries'

# Capability ceiling for later server enforcement, not authentication/authorization.
ROLE_PERMISSIONS = MappingProxyType({
    Role.USER: frozenset({Permission.OWN_PROGRESS}),
    Role.OPERATOR: frozenset({Permission.OWN_PROGRESS, Permission.OPERATIONAL_METADATA}),
    Role.DEVELOPER_SRE: frozenset({Permission.OPERATIONAL_METADATA, Permission.TECHNICAL_DIAGNOSTICS}),
    Role.FINOPS: frozenset({Permission.FINANCIAL_DATA}),
    Role.ADMIN: frozenset({Permission.EFFECTIVE_CONFIGURATION, Permission.OPERATIONAL_METADATA}),
    Role.AUDITOR: frozenset({Permission.GOVERNED_SUMMARIES}),
})

@unique
class ConfigurationOwner(str, Enum):
    APPLICATION = 'application_settings'
    ENVIRONMENT = 'environment_policy'
    TERRAFORM = 'terraform'
    DATABASE = 'durable_database'
    RUNTIME_UI = 'runtime_ui'

class EffectiveConfiguration(Contract):
    """Safe numeric/boolean effective policy; secret endpoint values excluded."""
    setting: Literal['projection_enabled', 'projection_write_spacing_seconds', 'projection_checkpoint_seconds', 'projection_stale_seconds', 'projection_clock_grace_seconds', 'projection_capacity', 'projection_attempt_seconds', 'projection_terminal_attempts', 'projection_retry_seconds', 'projection_shutdown_seconds', 'projection_terminal_hours', 'projection_success_days', 'projection_exceptional_days', 'trace_sample_rate', 'heartbeat_seconds', 'watchdog_stall_seconds',
                     'observability_enabled', 'otel_enabled', 'finops_enabled',
                     'exporter_mode', 'collector_endpoint_configured', 'service_name',
                     'environment', 'telemetry_schema_version', 'model_timeout_seconds', 'graph_timeout_seconds', 'knowledge_timeout_seconds', 'database_timeout_seconds', 'storage_timeout_seconds', 'turn_timeout_seconds', 'cleanup_timeout_seconds', 'session_load_timeout_seconds', 'session_lock_timeout_seconds', 'planning_timeout_seconds', 'orchestration_timeout_seconds', 'agent_timeout_seconds', 'tool_timeout_seconds', 'gateway_timeout_seconds', 'database_acquire_timeout_seconds', 'database_query_timeout_seconds', 'secret_timeout_seconds', 'persistence_timeout_seconds', 'sse_delivery_timeout_seconds', 'queue_wait_timeout_seconds', 'retry_minimum_seconds', 'watchdog_check_seconds', 'blocking_workers', 'blocking_pending', 'business_queue_capacity', 'business_queue_bytes']
    value: bool | int | Annotated[float, Field(allow_inf_nan=False)] | Code
    owner: ConfigurationOwner
    read_only: Literal[True] = True
    config_version: Code

    @model_validator(mode='before')
    @classmethod
    def effective_types(cls, data):
        if not isinstance(data, dict):
            return data
        key, value = data.get('setting'), data.get('value')
        if key in {'projection_enabled','observability_enabled','otel_enabled','finops_enabled','collector_endpoint_configured'}:
            if type(value) is not bool:
                raise ValueError('Expected effective boolean')
        elif key in {'environment','service_name'}:
            if not isinstance(value, str):
                raise ValueError('Expected effective code')
        elif key == 'exporter_mode':
            if value not in {'disabled','none','local','otlp'}:
                raise ValueError('Invalid effective exporter mode')
        elif key == 'telemetry_schema_version':
            if type(value) is not int or value != TELEMETRY_SCHEMA_VERSION:
                raise ValueError('Invalid effective schema version')
        elif type(value) not in {int,float}:
            raise ValueError('Expected effective numeric value')
        return data

class ResourceMetadata(Contract):
    attributes: dict[Attribute, Code]

    @field_validator('attributes')
    @classmethod
    def resource_scope(cls, value):
        if not set(value).issubset(RESOURCE_ATTRIBUTES):
            raise ValueError('Per-run attributes cannot be resource attributes')
        return value

class OperationalEvent(str, Enum):
    INITIALIZED = 'telemetry.initialized'
    DEGRADED = 'telemetry.degraded'
    SHUTDOWN = 'telemetry.shutdown'
    LEGACY = 'telemetry.legacy_log'
    LATE_COMPLETION = 'reliability.late_completion'

class LogRecord(Contract):
    """Structured logging shape; event_name is the message, never arbitrary text."""
    telemetry_schema_version: Literal[1] = TELEMETRY_SCHEMA_VERSION
    timestamp: AwareDatetime
    severity: Literal['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    service: Code
    environment: Code
    event_name: Stage | OperationalEvent
    message: Stage | OperationalEvent

    @model_validator(mode='after')
    def same_event(self):
        if self.message != self.event_name:
            raise ValueError('Log message must be the canonical event')
        if (self.turn_id is None) != (self.turn_id_origin is None):
            raise ValueError('Turn identity requires its origin')
        return self
    error_code: ErrorCode | None = None
    trace_id: TraceId | None = None
    span_id: SpanId | None = None
    run_id: Identifier | None = None
    turn_id: Identifier | None = None
    turn_id_origin: Literal['adk', 'execution'] | None = None
    metadata: dict[str, SafeValue] = Field(default_factory=dict)

    @field_validator('metadata', mode='before')
    @classmethod
    def safe_metadata(cls, value):
        return validate_metadata(value)


