"""SQLAlchemy ORM models for the Case/Fault context domain.

All table names use the `slopanoc_` prefix (instruction section 3: "Keep
Case SQLAlchemy models/table names clearly namespaced... Do not collide
with ADK-managed tables.") -- ADK's own `DatabaseSessionService` tables
(`sessions`, `events`, `app_states`, `user_states`, confirmed in Phase 4C)
share no name with anything here, and this module never imports or
touches ADK's schema classes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class CaseRecord(Base):
    __tablename__ = "slopanoc_cases"

    case_id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    problem_statement: Mapped[str] = mapped_column(Text)
    external_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column()
    created_by_user_id: Mapped[str] = mapped_column()
    # POST-5.1 A4: `DateTime(timezone=True)` explicitly -- every value this
    # codebase ever writes here is `datetime.now(timezone.utc)` (tz-aware).
    # SQLAlchemy's default `Mapped[datetime]` maps to a naive
    # TIMESTAMP WITHOUT TIME ZONE column; SQLite tolerates a tz-aware value
    # written into that silently, but PostgreSQL's asyncpg driver
    # correctly rejects it (confirmed live against Cloud SQL: "can't
    # subtract offset-naive and offset-aware datetimes"). Explicit
    # `timezone=True` makes the column TIMESTAMPTZ, matching what the
    # application actually writes, on every dialect.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseMembershipRecord(Base):
    __tablename__ = "slopanoc_case_memberships"

    case_id: Mapped[str] = mapped_column(ForeignKey("slopanoc_cases.case_id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CaseSessionLinkRecord(Base):
    """`session_id` is the primary key -- a hard, DB-enforced guarantee
    that a Session is linked to at most one Case at a time (instruction
    section 8's "one Session should be linked to at most one active Case
    in v1"). Unlinking deletes the row; there is deliberately no history
    of past links kept in v1 (see service.py's docstring for the
    reasoning) -- linking is still fully audited going forward via
    `linked_at`/`linked_by_user_id` on whatever the CURRENT link is.
    """

    __tablename__ = "slopanoc_case_session_links"

    session_id: Mapped[str] = mapped_column(primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("slopanoc_cases.case_id"))
    session_user_id: Mapped[str] = mapped_column()
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    linked_by_user_id: Mapped[str] = mapped_column()


class CaseContextItemRecord(Base):
    __tablename__ = "slopanoc_case_context_items"

    item_id: Mapped[str] = mapped_column(primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("slopanoc_cases.case_id"))
    kind: Mapped[str] = mapped_column()
    content: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column()
    source_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    source_author: Mapped[Optional[str]] = mapped_column(nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_by_user_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    created_by_agent: Mapped[Optional[str]] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # JSON list[str] of other item_ids this analysis cites as support --
    # every id is deterministically validated (exists, same Case, visible
    # to the current user) before this row is ever written; see
    # service.py's `record_case_analysis` (instruction section 17).
    supporting_item_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
