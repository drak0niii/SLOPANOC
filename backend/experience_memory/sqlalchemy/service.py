"""Phase 6A.8: the ONE Experience Memory write/read boundary (§23/§44).

`ExperienceMemoryService` is the sole way a candidate becomes durable,
and the sole way durable Experience is queried. No agent/tool writes
directly to `ExperienceRecordTable` -- there is no other code path
(enforced by `test_no_execution_no_bypass.py`'s import-boundary proof
elsewhere in this package, and simply by the fact that nothing outside
this module imports `ExperienceRecordTable`).

NO MODEL CALL ANYWHERE IN THIS MODULE (§20/§76): admission is
`domain.admission.evaluate_admission`, a pure function; nothing here
calls Gemini/ADK/any LLM client.

QUERY-TIME OWNER ISOLATION (§33/§34, the single most safety-critical
property in this module): `owner_id` is filtered INSIDE the SQL
`WHERE` clause of every query this module issues -- `get_by_id` and
`query` both add `ExperienceRecordTable.owner_id == owner_id` to the
`select()` statement itself, before the database ever executes it.
There is no method anywhere in this class that fetches rows for one
owner and then filters in Python, and there is no `list_all()` method
(§44/§80) that could be misused to obtain an unscoped result set.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import select

from backend.experience_memory.domain.admission import evaluate_admission
from backend.experience_memory.domain.enums import AdmissionOutcome, ExperienceLifecycle, ExperienceType
from backend.experience_memory.domain.fingerprint import compute_experience_content_fingerprint, compute_experience_id
from backend.experience_memory.domain.models import (
    ExperienceCandidate,
    ExperienceEvidenceReference,
    ExperienceMemoryResult,
    ExperienceQuery,
    ExperienceRecord,
)
from backend.experience_memory.sqlalchemy.db import ExperienceMemoryDatabase
from backend.experience_memory.sqlalchemy.models import ExperienceRecordTable

_IN_MEMORY_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

__all__ = ["RecordExperienceResult", "ExperienceMemoryService", "get_experience_memory_service"]


class RecordExperienceResult(BaseModel):
    """The result of `record_experience` -- always states the admission
    outcome; `record`/`deduplicated` are populated only on `ACCEPT`."""

    outcome: AdmissionOutcome
    reason: str
    record: Optional[ExperienceRecord] = None
    deduplicated: bool = False


def _row_to_record(row: ExperienceRecordTable) -> ExperienceRecord:
    return ExperienceRecord(
        experience_id=row.experience_id,
        experience_schema_version=row.experience_schema_version,
        experience_type=ExperienceType(row.experience_type),
        lifecycle=ExperienceLifecycle(row.lifecycle),
        owner_id=row.owner_id,
        source_origin=row.source_origin,
        source_namespace=row.source_namespace,
        source_reference=row.source_reference,
        source_event_id=row.source_event_id,
        source_event_at=row.source_event_at,
        case_id=row.case_id,
        session_id=row.session_id,
        context_fingerprint=row.context_fingerprint,
        skill_id=row.skill_id,
        skill_version=row.skill_version,
        skill_fingerprint=row.skill_fingerprint,
        evidence_references=[ExperienceEvidenceReference(**item) for item in json.loads(row.evidence_references_json)],
        observed_facts=json.loads(row.observed_facts_json),
        outcome_summary=row.outcome_summary,
        metadata=json.loads(row.metadata_json),
        content_fingerprint=row.content_fingerprint,
        recorded_at=row.recorded_at,
        invalidated_at=row.invalidated_at,
        invalidation_reason=row.invalidation_reason,
    )


class ExperienceMemoryService:
    """Mirrors `backend.context.sqlalchemy.service.TelcoContextService`'s
    own construction/session pattern exactly (default in-memory SQLite
    for ad hoc/test use; the real runtime default is wired only by
    `get_experience_memory_service()`)."""

    def __init__(self, database: Optional[ExperienceMemoryDatabase] = None) -> None:
        self._db = database if database is not None else ExperienceMemoryDatabase(database_url=_IN_MEMORY_TEST_DATABASE_URL)

    async def record_experience(self, candidate: ExperienceCandidate) -> RecordExperienceResult:
        """§23: validate (already done by `ExperienceCandidate`'s own
        pydantic construction) -> evaluate admission -> enforce owner
        scope (structural: `owner_id` is a required candidate field) ->
        calculate identity/fingerprint -> persist ONLY on ACCEPT ->
        return a deterministic result. REJECT/INDETERMINATE candidates
        are never written anywhere (§63: "only ACCEPTED becomes durable
        Experience... do not create a candidate table unless
        necessary" -- none was created)."""
        admission = evaluate_admission(candidate)
        if admission.outcome is not AdmissionOutcome.ACCEPT:
            return RecordExperienceResult(outcome=admission.outcome, reason=admission.reason)

        experience_id = compute_experience_id(candidate.owner_id, candidate.experience_type.value, candidate.source_namespace, candidate.source_event_id)
        content_fingerprint = compute_experience_content_fingerprint(candidate)

        await self._db.ensure_schema()
        async with self._db.session() as session:
            # §17 idempotency: the SAME source identity always computes
            # the SAME experience_id -- a repeated write is detected
            # here and short-circuited, never re-inserted, never
            # altering the existing row in any way (§41 immutability).
            existing = await session.get(ExperienceRecordTable, experience_id)
            if existing is not None:
                return RecordExperienceResult(
                    outcome=AdmissionOutcome.ACCEPT,
                    reason="idempotent: an Experience record with this identity already exists",
                    record=_row_to_record(existing),
                    deduplicated=True,
                )

            now = datetime.now(timezone.utc)
            row = ExperienceRecordTable(
                experience_id=experience_id,
                experience_schema_version=candidate.experience_schema_version,
                experience_type=candidate.experience_type.value,
                lifecycle=ExperienceLifecycle.ACTIVE.value,
                owner_id=candidate.owner_id,
                source_origin=candidate.source_origin.value,
                source_namespace=candidate.source_namespace,
                source_reference=candidate.source_reference,
                source_event_id=candidate.source_event_id,
                source_event_at=candidate.source_event_at,
                case_id=candidate.case_id,
                session_id=candidate.session_id,
                context_fingerprint=candidate.context_fingerprint,
                skill_id=candidate.skill_id,
                skill_version=candidate.skill_version,
                skill_fingerprint=candidate.skill_fingerprint,
                evidence_references_json=json.dumps([ref.model_dump(mode="json") for ref in candidate.evidence_references]),
                observed_facts_json=json.dumps(candidate.observed_facts),
                outcome_summary=candidate.outcome_summary,
                metadata_json=json.dumps(candidate.metadata),
                content_fingerprint=content_fingerprint,
                recorded_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except Exception:
                # A concurrent writer inserted the same identity between
                # our `get` and our `commit` -- re-fetch and return the
                # now-existing row idempotently rather than raising.
                await session.rollback()
                refetched = await session.get(ExperienceRecordTable, experience_id)
                if refetched is not None:
                    return RecordExperienceResult(
                        outcome=AdmissionOutcome.ACCEPT,
                        reason="idempotent: a concurrent write already created this Experience record",
                        record=_row_to_record(refetched),
                        deduplicated=True,
                    )
                raise
            await session.refresh(row)
            return RecordExperienceResult(outcome=AdmissionOutcome.ACCEPT, reason=admission.reason, record=_row_to_record(row), deduplicated=False)

    async def get_by_id(self, owner_id: str, experience_id: str) -> Optional[ExperienceRecord]:
        """Owner-scoped lookup -- `owner_id` is part of the `WHERE`
        clause itself (§33/§34), never applied after the fact. A record
        that exists but belongs to a different owner is indistinguishable
        from "does not exist" (`None`) -- anti-enumeration, matching this
        codebase's existing attachment/session lookup convention."""
        await self._db.ensure_schema()
        async with self._db.session() as session:
            result = await session.execute(
                select(ExperienceRecordTable).where(
                    ExperienceRecordTable.experience_id == experience_id,
                    ExperienceRecordTable.owner_id == owner_id,
                )
            )
            row = result.scalar_one_or_none()
            return _row_to_record(row) if row is not None else None

    async def query(self, experience_query: ExperienceQuery) -> ExperienceMemoryResult:
        """§36/§45/§46: structured, bounded, deterministic retrieval.
        `owner_id` is REQUIRED on `ExperienceQuery` itself (structural,
        §45) and is the first predicate added to the `WHERE` clause --
        every other filter is additive on top of it, never a substitute
        for it. Default ordering is `recorded_at DESC, experience_id ASC`
        (§46: `recorded_at` is never NULL, unlike `source_event_at`,
        making it the only field that can give a fully deterministic,
        always-available primary sort; `experience_id` breaks ties
        deterministically for equal timestamps)."""
        await self._db.ensure_schema()

        stmt = select(ExperienceRecordTable).where(ExperienceRecordTable.owner_id == experience_query.owner_id)
        applied_filters: dict[str, str] = {}

        if not experience_query.include_invalidated:
            stmt = stmt.where(ExperienceRecordTable.lifecycle == ExperienceLifecycle.ACTIVE.value)
        if experience_query.case_id is not None:
            stmt = stmt.where(ExperienceRecordTable.case_id == experience_query.case_id)
            applied_filters["case_id"] = experience_query.case_id
        if experience_query.experience_type is not None:
            stmt = stmt.where(ExperienceRecordTable.experience_type == experience_query.experience_type.value)
            applied_filters["experience_type"] = experience_query.experience_type.value
        if experience_query.skill_id is not None:
            stmt = stmt.where(ExperienceRecordTable.skill_id == experience_query.skill_id)
            applied_filters["skill_id"] = experience_query.skill_id
        if experience_query.skill_version is not None:
            stmt = stmt.where(ExperienceRecordTable.skill_version == experience_query.skill_version)
            applied_filters["skill_version"] = experience_query.skill_version
        if experience_query.source_event_from is not None:
            stmt = stmt.where(ExperienceRecordTable.source_event_at >= experience_query.source_event_from)
            applied_filters["source_event_from"] = experience_query.source_event_from.isoformat()
        if experience_query.source_event_to is not None:
            stmt = stmt.where(ExperienceRecordTable.source_event_at <= experience_query.source_event_to)
            applied_filters["source_event_to"] = experience_query.source_event_to.isoformat()

        stmt = stmt.order_by(ExperienceRecordTable.recorded_at.desc(), ExperienceRecordTable.experience_id.asc())
        stmt = stmt.limit(experience_query.limit)

        async with self._db.session() as session:
            result = await session.execute(stmt)
            rows = result.scalars().all()

        records = [_row_to_record(row) for row in rows]
        return ExperienceMemoryResult(
            owner_id=experience_query.owner_id,
            records=records,
            count=len(records),
            applied_filters=applied_filters,
            limit=experience_query.limit,
        )

    async def invalidate(self, owner_id: str, experience_id: str, reason: str) -> Optional[ExperienceRecord]:
        """§40/§41: administrative, auditable withdrawal from default
        retrieval -- never a physical delete, never a content rewrite.
        Owner-scoped exactly like `get_by_id`. Returns `None` (never
        raises) if no such record exists for this owner -- the same
        anti-enumeration shape as `get_by_id`."""
        if not reason or not reason.strip():
            raise ValueError("invalidation_reason must not be blank")

        await self._db.ensure_schema()
        async with self._db.session() as session:
            result = await session.execute(
                select(ExperienceRecordTable).where(
                    ExperienceRecordTable.experience_id == experience_id,
                    ExperienceRecordTable.owner_id == owner_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return None
            row.lifecycle = ExperienceLifecycle.INVALIDATED.value
            row.invalidated_at = datetime.now(timezone.utc)
            row.invalidation_reason = reason
            await session.commit()
            await session.refresh(row)
            return _row_to_record(row)


def get_experience_memory_service() -> ExperienceMemoryService:
    """The real runtime default -- backed by the process-wide
    `ExperienceMemoryDatabase` singleton. Deferred import to avoid a
    module-level singleton constructed at import time (mirrors
    `get_telco_context_service()`'s own pattern)."""
    from backend.experience_memory.sqlalchemy.db import get_experience_memory_database

    return ExperienceMemoryService(get_experience_memory_database())
