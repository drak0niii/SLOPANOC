"""SQLAlchemy ORM model for the Experience Memory domain (§10/§14).

Own `Base`/table set, exactly like `backend/context/sqlalchemy/models
.py` before it -- never shared with ADK's own session tables, Case's
tables, Knowledge's table, or TELCO Context's tables. `slopanoc_`
prefix, matching this codebase's existing namespacing convention.

ONE PRIMARY TABLE (§10: "One primary table plus narrowly justified
supporting structures is preferred" -- the audit found no supporting
structure actually justified: candidates that are REJECTed/
INDETERMINATE are never persisted at all, §63, so no separate
"candidate" table exists; evidence references/observed facts/metadata
are small, bounded, per-record structures stored as JSON text columns
on the SAME row rather than normalized child tables -- consistent with
this codebase's own existing convention of a JSON payload column for
bounded, per-record structured data, e.g. Knowledge's own `payload`
column).

APPEND-ONLY CONTENT (§41): every column below except `lifecycle`/
`invalidated_at`/`invalidation_reason` is written exactly once, at
INSERT, and never UPDATEd afterward by any code in this package --
enforced structurally by `service.py` never issuing an UPDATE against
any other column.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ExperienceRecordTable(Base):
    __tablename__ = "slopanoc_experience_records"
    __table_args__ = (
        # §39 -- each index justified against an actual retrieval
        # pattern this milestone implements, never speculative:
        Index("ix_experience_owner", "owner_id"),  # every query requires owner_id (§45) -- the base scope filter.
        Index("ix_experience_owner_case", "owner_id", "case_id"),  # §66's case-lookup test.
        Index("ix_experience_owner_type", "owner_id", "experience_type"),  # §36's experience_type filter.
        Index("ix_experience_owner_skill", "owner_id", "skill_id"),  # §36's Skill ID/version filter.
        Index("ix_experience_owner_recorded_at", "owner_id", "recorded_at"),  # §46's deterministic ordering.
    )

    # Deterministic SHA-256 hex digest of (owner_id, experience_type,
    # source_namespace, source_event_id) -- see domain/fingerprint.py.
    # THIS is the sole idempotency enforcement mechanism (§17); no
    # separate UNIQUE constraint is added because it would be redundant
    # with this PK's own uniqueness (both would collide on exactly the
    # same rows). `source_origin` (trust/admission classification) is
    # deliberately NOT part of this basis -- see the final source-
    # namespace corrective pass's own closure report §E.
    experience_id: Mapped[str] = mapped_column(primary_key=True)
    experience_schema_version: Mapped[str] = mapped_column()
    experience_type: Mapped[str] = mapped_column()
    lifecycle: Mapped[str] = mapped_column()
    owner_id: Mapped[str] = mapped_column()
    source_origin: Mapped[str] = mapped_column()
    # The stable PRODUCER/SYSTEM namespace that owns source_event_id --
    # a genuinely different concern from source_origin (trust/admission
    # class). Persisted as plain provenance (§12), not only inside the
    # experience_id hash where it could never later be inspected.
    source_namespace: Mapped[str] = mapped_column()
    source_reference: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_event_id: Mapped[str] = mapped_column()
    # POST-5.1 A4 / 6A.2 precedent: DateTime(timezone=True) explicitly --
    # every value this codebase writes here is datetime.now(timezone.utc).
    source_event_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    case_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    context_fingerprint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    skill_id: Mapped[Optional[str]] = mapped_column(nullable=True)
    skill_version: Mapped[Optional[str]] = mapped_column(nullable=True)
    skill_fingerprint: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    evidence_references_json: Mapped[str] = mapped_column(Text)
    observed_facts_json: Mapped[str] = mapped_column(Text)
    outcome_summary: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text)
    content_fingerprint: Mapped[str] = mapped_column()
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    invalidated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
