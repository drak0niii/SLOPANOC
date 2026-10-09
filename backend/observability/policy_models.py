"""Bounded policy operational metadata; no full traces or business payloads."""
from sqlalchemy import String, BigInteger, Integer, Boolean, Index, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, UTCDateTime

class RetentionCheckpoint(Base):
    __tablename__='observability_retention_checkpoint'
    environment: Mapped[str]=mapped_column(String(16),primary_key=True)
    dataset: Mapped[str]=mapped_column(String(64),primary_key=True)
    policy_version: Mapped[str]=mapped_column(String(32))
    cutoff: Mapped[object]=mapped_column(UTCDateTime)
    cursor: Mapped[str | None]=mapped_column(String(1024))
    generation: Mapped[int]=mapped_column(BigInteger,default=0)
    complete: Mapped[bool]=mapped_column(Boolean,default=False)
    __table_args__=(CheckConstraint('generation >= 0'),)

class ArchiveReceipt(Base):
    __tablename__='observability_archive_receipt'
    environment: Mapped[str]=mapped_column(String(16),primary_key=True)
    trace_id: Mapped[str]=mapped_column(String(32),primary_key=True)
    trace_class: Mapped[str]=mapped_column(String(16))
    expected_spans: Mapped[int]=mapped_column(Integer)
    population_digest: Mapped[str | None]=mapped_column(String(64))
    terminal_at: Mapped[object]=mapped_column(UTCDateTime)
    population_closed: Mapped[bool]=mapped_column(Boolean)
    policy_version: Mapped[str]=mapped_column(String(32))
    confirmed_digest: Mapped[str | None]=mapped_column(String(64))
    confirmed_at: Mapped[object | None]=mapped_column(UTCDateTime)
    __table_args__=(CheckConstraint('expected_spans >= 0 AND expected_spans <= 10000'),
        Index('ix_archive_receipt_environment_terminal','environment','terminal_at'))
