"""Application audit references/delivery state and derived read models only."""
from sqlalchemy import String, BigInteger, Float, JSON, CheckConstraint, Index, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ..models import UTCDateTime

class HybridBase(DeclarativeBase): pass

class EventColumns:
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_schema_version: Mapped[int] = mapped_column(BigInteger)
    event_class: Mapped[str] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(16))
    aggregate_type: Mapped[str] = mapped_column(String(32))
    aggregate_id: Mapped[str] = mapped_column(String(128))
    aggregate_version: Mapped[int] = mapped_column(BigInteger)
    run_id: Mapped[str | None] = mapped_column(String(36))
    turn_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    occurred_at: Mapped[object] = mapped_column(UTCDateTime)
    producer: Mapped[str] = mapped_column(String(32))
    safe_payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[object] = mapped_column(UTCDateTime)

class AuditRow(EventColumns, HybridBase):
    __tablename__='observability_critical_audit'
    __table_args__=(CheckConstraint("event_schema_version = 1 AND event_class = 'AUDIT' AND aggregate_version >= 1"),
        Index('ix_hybrid_audit_aggregate','environment','aggregate_type','aggregate_id','aggregate_version'))

class OutboxRow(EventColumns, HybridBase):
    __tablename__='observability_audit_outbox'
    event_id: Mapped[str] = mapped_column(String(36), ForeignKey('observability_critical_audit.event_id'),primary_key=True)
    publication_state: Mapped[str] = mapped_column(String(16))
    attempt_count: Mapped[int] = mapped_column(BigInteger)
    available_at: Mapped[object] = mapped_column(UTCDateTime)
    lease_owner: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[object | None] = mapped_column(UTCDateTime)
    published_at: Mapped[object | None] = mapped_column(UTCDateTime)
    last_failure_code: Mapped[str | None] = mapped_column(String(32))
    __table_args__=(CheckConstraint("event_schema_version = 1 AND event_class = 'AUDIT' AND aggregate_version >= 1 AND attempt_count >= 0"),
        CheckConstraint("publication_state IN ('PENDING','LEASED','RETRYING','DEAD','PUBLISHED')"),
        CheckConstraint("(publication_state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR (publication_state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)"),
        CheckConstraint("(publication_state = 'PUBLISHED' AND published_at IS NOT NULL) OR (publication_state <> 'PUBLISHED' AND published_at IS NULL)"),
        Index('ix_hybrid_outbox_oldest','environment','created_at','publication_state'),
        Index('ix_hybrid_outbox_available','environment','publication_state','available_at','event_id'))

class ReceiptRow(HybridBase):
    __tablename__='observability_projection_receipt'
    environment: Mapped[str] = mapped_column(String(16),primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36),primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[object] = mapped_column(UTCDateTime)
    processed_at: Mapped[object] = mapped_column(UTCDateTime)
    duplicate_count: Mapped[int] = mapped_column(BigInteger)
    __table_args__=(CheckConstraint('duplicate_count >= 0'),
        Index('ix_hybrid_receipt_recent','environment','processed_at'))

class FailureRow(HybridBase):
    __tablename__='observability_projection_failure'
    environment: Mapped[str] = mapped_column(String(16),primary_key=True)
    failure_key: Mapped[str] = mapped_column(String(64),primary_key=True)
    failure_code: Mapped[str] = mapped_column(String(32))
    processing_state: Mapped[str] = mapped_column(String(16))
    attempts: Mapped[int] = mapped_column(BigInteger)
    last_at: Mapped[object] = mapped_column(UTCDateTime)
    __table_args__=(CheckConstraint("failure_code IN ('INVALID','CONFLICT','PROCESSING_FAILED') AND processing_state IN ('RETRY','DEAD','RESOLVED') AND attempts >= 1"),)

class RunProjectionRow(HybridBase):
    __tablename__='observability_run_projection'
    environment: Mapped[str] = mapped_column(String(16),primary_key=True)
    run_id: Mapped[str] = mapped_column(String(36),primary_key=True)
    aggregate_version: Mapped[int] = mapped_column(BigInteger)
    turn_id: Mapped[str | None] = mapped_column(String(128))
    trace_id: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    latest_event_occurred_at: Mapped[object] = mapped_column(UTCDateTime)
    latest_event_processed_at: Mapped[object] = mapped_column(UTCDateTime)
    projection_lag: Mapped[float] = mapped_column(Float)
    __table_args__=(CheckConstraint('aggregate_version >= 1 AND projection_lag >= 0'),
        CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED','TIMEOUT','CANCELLED')"),
        Index('ix_hybrid_run_recent','environment','latest_event_occurred_at','run_id'))

class AgentProjectionRow(HybridBase):
    __tablename__='observability_agent_projection'
    environment: Mapped[str] = mapped_column(String(16),primary_key=True)
    agent: Mapped[str] = mapped_column(String(64),primary_key=True)
    model_attempts_started: Mapped[int] = mapped_column(BigInteger)
    model_attempts_completed: Mapped[int] = mapped_column(BigInteger)
    usage_records_captured: Mapped[int] = mapped_column(BigInteger)
    latest_event_occurred_at: Mapped[object] = mapped_column(UTCDateTime)
    latest_event_processed_at: Mapped[object] = mapped_column(UTCDateTime)
    projection_lag: Mapped[float] = mapped_column(Float)
    __table_args__=(CheckConstraint('model_attempts_started >= 0 AND model_attempts_completed >= 0 AND usage_records_captured >= 0 AND projection_lag >= 0'),)

# Register additive application-owned execution models on the same metadata.
from backend.execution_audit import models as _execution_models
