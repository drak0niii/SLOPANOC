"""Dedicated accounting metadata; immutable ledger and durable mutable delivery state."""
from sqlalchemy import String, BigInteger, Boolean, JSON, Index, CheckConstraint, ForeignKeyConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ..models import UTCDateTime
from .contracts import AttemptState, QUANTITIES, ENVIRONMENTS

class FinOpsBase(DeclarativeBase): pass

def enum_check(field, values):
    return CheckConstraint(field+' IN ('+','.join(repr(v) for v in values)+')')

class AttemptRow(FinOpsBase):
    __tablename__ = 'finops_runtime_attempt'
    environment: Mapped[str] = mapped_column(String(16), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(32))
    admitted_at: Mapped[object] = mapped_column(UTCDateTime)
    started_at: Mapped[object | None] = mapped_column(UTCDateTime)
    provider_finished_at: Mapped[object | None] = mapped_column(UTCDateTime)
    deadline_at: Mapped[object] = mapped_column(UTCDateTime)
    captured_at: Mapped[object | None] = mapped_column(UTCDateTime)
    gap_reason: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (enum_check('environment', ENVIRONMENTS), enum_check('state',[s.value for s in AttemptState]),
        Index('ix_finops_attempt_debt','environment','state','deadline_at'))

class InboxRow(FinOpsBase):
    __tablename__ = 'finops_usage_inbox'
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    environment: Mapped[str] = mapped_column(String(16))
    attempt_id: Mapped[str] = mapped_column(String(36))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(BigInteger, default=0)
    next_attempt_at: Mapped[object] = mapped_column(UTCDateTime)
    created_at: Mapped[object] = mapped_column(UTCDateTime)
    delivered_at: Mapped[object | None] = mapped_column(UTCDateTime)
    exhausted: Mapped[bool] = mapped_column(Boolean, default=False)
    __table_args__ = (ForeignKeyConstraint(['environment','attempt_id'],['finops_runtime_attempt.environment','finops_runtime_attempt.attempt_id']),
        CheckConstraint('attempts >= 0'),Index('ix_finops_inbox_pending','delivered','exhausted','next_attempt_at'))

class LedgerRow(FinOpsBase):
    __tablename__ = 'ai_usage_ledger'
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    environment: Mapped[str] = mapped_column(String(16))
    attempt_id: Mapped[str] = mapped_column(String(36))
    payload_hash: Mapped[str] = mapped_column(String(64))
    record_kind: Mapped[str] = mapped_column(String(16))
    original_event_id: Mapped[str | None] = mapped_column(String(36))
    correction_reason: Mapped[str | None] = mapped_column(String(32))
    correction_process: Mapped[str | None] = mapped_column(String(32))
    metadata_safe: Mapped[dict] = mapped_column(JSON)
    observed_at: Mapped[object] = mapped_column(UTCDateTime)
    created_at: Mapped[object] = mapped_column(UTCDateTime)
    for _q in QUANTITIES:
        locals()[_q] = mapped_column(BigInteger, nullable=True)
    __table_args__ = (enum_check('environment', ENVIRONMENTS),enum_check('record_kind',('BASE','ADJUSTMENT')),
        ForeignKeyConstraint(['environment','attempt_id'],['finops_runtime_attempt.environment','finops_runtime_attempt.attempt_id']),
        ForeignKeyConstraint(['original_event_id'],['ai_usage_ledger.event_id']),
        CheckConstraint("(record_kind = 'BASE' AND original_event_id IS NULL) OR (record_kind = 'ADJUSTMENT' AND original_event_id IS NOT NULL)"),
        *[CheckConstraint(f"record_kind = 'ADJUSTMENT' OR {q} IS NULL OR {q} >= 0") for q in QUANTITIES],
        Index('ux_finops_base_attempt','environment','attempt_id',unique=True,
            postgresql_where=__import__('sqlalchemy').text("record_kind = 'BASE'"),
            sqlite_where=__import__('sqlalchemy').text("record_kind = 'BASE'")),
        Index('ix_finops_ledger_time','environment','observed_at','event_id'))

class BucketRow(FinOpsBase):
    __tablename__ = 'finops_usage_bucket'
    environment: Mapped[str] = mapped_column(String(16), primary_key=True)
    at: Mapped[object] = mapped_column(UTCDateTime, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(128), primary_key=True)
    agent: Mapped[str] = mapped_column(String(64), primary_key=True)
    workload: Mapped[str] = mapped_column(String(32), primary_key=True)
    operation: Mapped[str] = mapped_column(String(64), primary_key=True)
    operation_type: Mapped[str] = mapped_column(String(16), primary_key=True)
    good: Mapped[int] = mapped_column(BigInteger, default=0)
    bad: Mapped[int] = mapped_column(BigInteger, default=0)
    quantity_known: Mapped[int] = mapped_column(BigInteger, default=0)
    quantity_partial: Mapped[int] = mapped_column(BigInteger, default=0)
    for _q in QUANTITIES:
        locals()[_q] = mapped_column(BigInteger, nullable=False, default=0)
        locals()[_q+'_known'] = mapped_column(BigInteger, nullable=False, default=0)
    __table_args__ = (Index('ix_finops_bucket_time','environment','at'),
        CheckConstraint('good >= 0 AND bad >= 0 AND quantity_known >= 0 AND quantity_partial >= 0'))

class SourceRow(FinOpsBase):
    __tablename__ = 'finops_accounting_source'
    environment: Mapped[str] = mapped_column(String(16), primary_key=True)
    coverage_start: Mapped[object] = mapped_column(UTCDateTime)
    updated_at: Mapped[object] = mapped_column(UTCDateTime)
    last_observation: Mapped[object | None] = mapped_column(UTCDateTime)
    last_persistence: Mapped[object | None] = mapped_column(UTCDateTime)
    admission_failures: Mapped[int] = mapped_column(BigInteger, default=0)
