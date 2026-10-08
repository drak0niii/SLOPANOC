"""Application operational source state/manifests/cache, never raw billing lines."""
from sqlalchemy import String, BigInteger, JSON, Index, CheckConstraint, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ..models import UTCDateTime

class BillingBase(DeclarativeBase): pass

class StateRow(BillingBase):
    __tablename__='finops_billing_ingest_state'
    key: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    source: Mapped[str]=mapped_column(String(32))
    scope: Mapped[str]=mapped_column(String(64))
    partition: Mapped[str]=mapped_column(String(10))
    mode: Mapped[str]=mapped_column(String(16))
    revision: Mapped[int]=mapped_column(BigInteger,default=0)
    generation: Mapped[str|None]=mapped_column(String(64),ForeignKey('finops_billing_publication.generation'))
    last_success: Mapped[object|None]=mapped_column(UTCDateTime)
    last_attempt: Mapped[object|None]=mapped_column(UTCDateTime)
    error: Mapped[str|None]=mapped_column(String(32))
    __table_args__=(Index('ix_billing_state_scope','environment','source','scope','partition',unique=True),
        CheckConstraint("environment IN ('local','development','staging','production')"),
        CheckConstraint("source IN ('DETAILED_BILLING','PRICING_EXPORT','FOCUS')"),
        CheckConstraint("mode IN ('TEST_FIXTURE','BIGQUERY')"),CheckConstraint('revision >= 0'))

class PublicationRow(BillingBase):
    __tablename__='finops_billing_publication'
    generation: Mapped[str]=mapped_column(String(64),primary_key=True)
    state_key: Mapped[str]=mapped_column(String(64))
    fingerprint: Mapped[str]=mapped_column(String(64))
    schema_version: Mapped[int]=mapped_column(BigInteger)
    mapping_version: Mapped[str]=mapped_column(String(32))
    window_start: Mapped[object]=mapped_column(UTCDateTime)
    window_end: Mapped[object]=mapped_column(UTCDateTime)
    extracted_at: Mapped[object]=mapped_column(UTCDateTime)
    export_time: Mapped[object|None]=mapped_column(UTCDateTime)
    price_from: Mapped[object|None]=mapped_column(UTCDateTime)
    price_to: Mapped[object|None]=mapped_column(UTCDateTime)
    row_count: Mapped[int]=mapped_column(BigInteger)
    late_rows: Mapped[int]=mapped_column(BigInteger)
    correction_rows: Mapped[int]=mapped_column(BigInteger)
    unmapped_rows: Mapped[int]=mapped_column(BigInteger)
    evidence_rows: Mapped[int]=mapped_column(BigInteger)
    controls: Mapped[list]=mapped_column(JSON)
    published_at: Mapped[object]=mapped_column(UTCDateTime)
    status: Mapped[str]=mapped_column(String(16),default='PUBLISHED')
    __table_args__=(Index('ix_billing_publication_history','state_key','published_at'),
        CheckConstraint('row_count >= 0 AND late_rows >= 0 AND correction_rows >= 0 AND unmapped_rows >= 0'),
        CheckConstraint("status = 'PUBLISHED'"),CheckConstraint('schema_version = 1'))

class CacheRow(BillingBase):
    __tablename__='finops_billing_projection_cache'
    generation: Mapped[str]=mapped_column(String(64),ForeignKey('finops_billing_publication.generation'),primary_key=True)
    # Summaries and bounded tariff lookup values are exact decimal strings, avoiding
    # SQLite's binary-float NUMERIC affinity and JSON float encoders.
    summaries: Mapped[list]=mapped_column(JSON)
    prices: Mapped[list]=mapped_column(JSON)
