"""Explicit safe diagnostic response DTOs, independent of SQLAlchemy objects."""
from typing import Literal
from pydantic import AwareDatetime, Field
from .schemas import Contract, Count, Duration, Code, TraceId, SpanId, Identifier
from .stages import RunStatus, Stage
from .errors import ErrorCode
from .model_context import ModelAgent
from .projection import DependencyState, DiagnosticEvent


class DependencyView(Contract):
    dependency: str
    operation: str
    agent: ModelAgent | None = None
    tool: str | None = None
    started_at: AwareDatetime


class TechnicalView(Contract):
    trace_id: TraceId | None = None
    root_span_id: SpanId | None = None
    current_span_id: SpanId | None = None
    turn_id: Identifier | None = None
    turn_id_origin: Literal['adk','execution'] | None = None
    producer_instance_id: str
    state_version: Count
    service_version: Code | None = None
    git_sha: str | None = None
    release_id: Code | None = None
    revision: Code | None = None
    region: Code | None = None
    config_version: str | None = None
    telemetry_schema_version: Literal[1] = 1


class RunView(Contract):
    run_id: str
    environment: Code
    status: RunStatus
    classification: Literal['ACTIVE','STALLED','STALE','TERMINAL']
    outcome_unknown: bool
    source: Literal['durable'] = 'durable'
    classification_as_of: AwareDatetime
    started_at: AwareDatetime
    terminal_at: AwareDatetime | None = None
    current_stage: Stage
    current_agent: ModelAgent | None = None
    current_tool: str | None = None
    dependencies: tuple[DependencyView,...] = Field(default=(),max_length=128)
    elapsed_ms: Duration
    last_progress_at: AwareDatetime
    heartbeat_at: AwareDatetime
    progress_age_ms: Duration
    work_deadline_at: AwareDatetime | None = None
    total_deadline_at: AwareDatetime | None = None
    error_code: ErrorCode | None = None
    cleanup_status: Literal['pending','running','completed','failed','timeout']
    delivery_status: Literal['pending','relayed','disconnected']
    timeline_truncated_count: Count
    technical: TechnicalView | None = None


class RunPage(Contract):
    items: tuple[RunView,...] = Field(max_length=100)
    next_cursor: str | None = None
    as_of: AwareDatetime


class TimelinePage(Contract):
    items: tuple[DiagnosticEvent,...] = Field(max_length=100)
    next_cursor: str | None = None
    truncated_count: Count


class HealthView(Contract):
    persistence: Literal['disabled','healthy','degraded','unavailable']
    writer_scope: Literal['process_local'] = 'process_local'
    durable_scope: Literal['authorized_environments'] = 'authorized_environments'
    as_of: AwareDatetime
    pending: Count
    admitted: Count
    terminal_pending: Count
    stale_records: Count | None = None
    exporter_state: Literal['disabled','healthy','degraded','unknown']
    collector_configured: bool
    last_success: AwareDatetime | None = None
    accepted: Count = 0
    coalesced: Count = 0
    attempts: Count = 0
    persisted: Count = 0
    failed: Count = 0
    retries: Count = 0
    rejected: Count = 0
    terminal_unpersisted: Count = 0
    shutdown_incomplete: Count = 0
    invalid: Count = 0
