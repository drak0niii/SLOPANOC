"""SQLAlchemy ORM models for the TELCO Context domain.

Own `Base`/table set, exactly like `backend/cases/models.py` and
`backend/knowledge/repository/sqlalchemy.py` before it -- never shared
with ADK's own session tables, Case's tables, or Knowledge's table. All
table names use the `slopanoc_` prefix, matching this codebase's existing
namespacing convention (`backend/cases/models.py`'s own docstring: "Keep
... table names clearly namespaced... Do not collide with ADK-managed
tables.").

`slopanoc_telco_context_assertions` is APPEND-ONLY, exactly like
`slopanoc_case_context_items` (`backend/cases/models.py`) -- an assertion
is never mutated or deleted once written; current state is always
recomputed from the full history (see
`backend.context.domain.models.reduce_dimension`), never stored as a
separate, potentially-stale column.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TelcoContextProfileRecord(Base):
    __tablename__ = "slopanoc_telco_context_profiles"
    __table_args__ = (UniqueConstraint("owner_kind", "owner_id", name="uq_telco_context_profile_owner"),)

    profile_id: Mapped[str] = mapped_column(primary_key=True)
    owner_kind: Mapped[str] = mapped_column()
    owner_id: Mapped[str] = mapped_column()
    created_by_user_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    # POST-5.1 A4 precedent (backend/cases/models.py): DateTime(timezone=True)
    # explicitly -- every value this codebase writes here is
    # datetime.now(timezone.utc); PostgreSQL/asyncpg rejects a tz-aware
    # value written into a naive TIMESTAMP column, SQLite silently
    # tolerates it, so the column must be TIMESTAMPTZ on every dialect.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class TelcoContextAssertionRecord(Base):
    __tablename__ = "slopanoc_telco_context_assertions"

    assertion_id: Mapped[str] = mapped_column(primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("slopanoc_telco_context_profiles.profile_id"))
    dimension: Mapped[str] = mapped_column()
    kind: Mapped[str] = mapped_column()
    raw_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    canonical_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column()
    source_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    asserted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
