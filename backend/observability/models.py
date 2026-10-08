"""Application-owned operational tables. No business content or accounting fields."""
from datetime import timezone
from sqlalchemy import String, Integer, BigInteger, Float, Boolean, DateTime, JSON, CheckConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator
from .stages import RunStatus, Stage


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


class Base(DeclarativeBase):
    pass


def choices(field, values):
    return CheckConstraint(field + ' IN (' + ','.join("'"+v+"'" for v in values) + ')')


class IdentityColumns:
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    producer_instance_id: Mapped[str] = mapped_column(String(36))
    state_version: Mapped[int] = mapped_column(BigInteger)
    session_detached: Mapped[bool] = mapped_column(Boolean, default=False)
    session_id: Mapped[str | None] = mapped_column(String(128))
    turn_id: Mapped[str | None] = mapped_column(String(128))
    turn_id_origin: Mapped[str | None] = mapped_column(String(16))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    root_span_id: Mapped[str | None] = mapped_column(String(16))
    environment: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    current_stage: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[object] = mapped_column(UTCDateTime)
    terminal_at: Mapped[object | None] = mapped_column(UTCDateTime)
    elapsed_ms: Mapped[float] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_stage: Mapped[str | None] = mapped_column(String(64))
    cleanup_status: Mapped[str] = mapped_column(String(16))
    delivery_status: Mapped[str] = mapped_column(String(16))
    service_version: Mapped[str | None] = mapped_column(String(64))
    git_sha: Mapped[str | None] = mapped_column(String(64))
    release_id: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[str | None] = mapped_column(String(64))
    region: Mapped[str | None] = mapped_column(String(64))
    config_version: Mapped[str | None] = mapped_column(String(16))
    telemetry_schema_version: Mapped[int] = mapped_column(Integer)
    projection_schema_version: Mapped[int] = mapped_column(Integer)
    retention_class: Mapped[str] = mapped_column(String(16))
    ever_stalled: Mapped[bool] = mapped_column(Boolean)
    timeline_truncated_count: Mapped[int] = mapped_column(BigInteger)
    expiry_at: Mapped[object] = mapped_column(UTCDateTime)


def state_constraints():
    return (choices('status', [s.value for s in RunStatus]),
        choices('current_stage', [s.value for s in Stage]),
        choices('cleanup_status', ['pending','running','completed','failed','timeout']),
        choices('delivery_status', ['pending','relayed','disconnected']),
        choices('retention_class', ['normal','exceptional']),
        CheckConstraint('state_version >= 0 AND elapsed_ms >= 0 AND timeline_truncated_count >= 0'),
        CheckConstraint('telemetry_schema_version = 1 AND projection_schema_version = 1'),
        CheckConstraint("(status IN ('COMPLETED','FAILED','TIMEOUT','CANCELLED') AND terminal_at IS NOT NULL) OR (status IN ('PENDING','RUNNING','STALLED') AND terminal_at IS NULL)"))


class RunRow(IdentityColumns, Base):
    __tablename__ = 'observability_run_status'
    current_span_id: Mapped[str | None] = mapped_column(String(16))
    current_agent: Mapped[str | None] = mapped_column(String(64))
    current_tool: Mapped[str | None] = mapped_column(String(64))
    # Only strict DependencyState objects cross the repository boundary.
    dependencies: Mapped[list] = mapped_column(JSON)
    stage_started_at: Mapped[object] = mapped_column(UTCDateTime)
    last_progress_at: Mapped[object] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[object] = mapped_column(UTCDateTime)
    observed_at: Mapped[object] = mapped_column(UTCDateTime)
    work_deadline_at: Mapped[object | None] = mapped_column(UTCDateTime)
    total_deadline_at: Mapped[object | None] = mapped_column(UTCDateTime)
    stalled: Mapped[bool] = mapped_column(Boolean)
    stalled_at: Mapped[object | None] = mapped_column(UTCDateTime)
    __table_args__ = (*state_constraints(),
        CheckConstraint("status NOT IN ('COMPLETED','FAILED','TIMEOUT','CANCELLED') OR (current_agent IS NULL AND current_tool IS NULL)"),
        Index('ix_observability_active', 'environment','status','heartbeat_at','run_id'),
        Index('ix_observability_session', 'session_id','started_at','run_id'),
        Index('ix_observability_status_expiry', 'expiry_at','run_id'))


class SummaryRow(IdentityColumns, Base):
    __tablename__ = 'observability_turn'
    __table_args__ = (*state_constraints(),
        CheckConstraint("status IN ('COMPLETED','FAILED','TIMEOUT','CANCELLED')"),
        Index('ix_observability_recent', 'environment','started_at','run_id'),
        Index('ix_observability_terminal', 'environment','terminal_at','run_id'),
        Index('ix_observability_summary_session', 'session_id','started_at','run_id'),
        Index('ix_observability_summary_expiry', 'expiry_at','run_id'))


class EventRow(Base):
    __tablename__ = 'observability_run_event'
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_seq: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    state_version: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(16))
    timestamp: Mapped[object] = mapped_column(UTCDateTime)
    elapsed_ms: Mapped[float] = mapped_column(Float)
    stage: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    agent: Mapped[str | None] = mapped_column(String(64))
    tool: Mapped[str | None] = mapped_column(String(64))
    dependency: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (CheckConstraint('event_seq >= 0 AND state_version >= 0 AND elapsed_ms >= 0'),
        choices('stage', [s.value for s in Stage]), choices('status', [s.value for s in RunStatus]),
        choices('event_type', ['started','stage','agent','tool','dependency','stalled','resumed','terminal','cleanup','delivery','identity','deadline']))
