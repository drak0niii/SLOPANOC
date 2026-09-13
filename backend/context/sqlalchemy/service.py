"""Deterministic TELCO Context persistence operations.

Internal service/repository API only -- no HTTP endpoint, no new agent
tool, no UI (6A.2 instruction section 25: "do not create public
endpoints merely because the model exists... if an internal service/
repository API is sufficient, prefer it"). A future milestone that
actually wires TELCO Context into a live request path adds its own
authorization/HTTP layer on top of this; this module raises plain
`ValueError`/`LookupError` for programmer-facing misuse, never a
`SafeError` (an API-facing concern this module has no dependency on).

NO MODEL CALL ANYWHERE IN THIS MODULE (instruction section 26): every
method here is a deterministic database read/write. The only way a
`ContextAssertion` is created is `record_assertion`, called explicitly by
a caller that already decided the dimension/kind/value/origin -- there is
no code path from Gemini/ADK output to a persisted assertion in this
milestone.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextProfileOwnerKind
from backend.context.domain.models import (
    ContextAssertion,
    ContextValue,
    TelcoContextProfile,
    compute_context_state,
)
from backend.context.sqlalchemy.db import ContextDatabase
from backend.context.sqlalchemy.models import TelcoContextAssertionRecord, TelcoContextProfileRecord

_IN_MEMORY_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


def _profile_to_domain(record: TelcoContextProfileRecord) -> TelcoContextProfile:
    return TelcoContextProfile(
        profile_id=record.profile_id,
        owner_kind=ContextProfileOwnerKind(record.owner_kind),
        owner_id=record.owner_id,
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _assertion_to_domain(record: TelcoContextAssertionRecord) -> ContextAssertion:
    return ContextAssertion(
        assertion_id=record.assertion_id,
        dimension=ContextDimension(record.dimension),
        kind=AssertionKind(record.kind),
        raw_value=record.raw_value,
        canonical_value=record.canonical_value,
        origin=ContextOrigin(record.origin),
        source_reference=record.source_reference,
        asserted_at=record.asserted_at,
        created_at=record.created_at,
    )


class TelcoContextService:
    """Owns profile identity + the append-only assertion ledger. Mirrors
    `backend.cases.service.CaseService`'s own construction/session
    pattern exactly (default in-memory SQLite for ad hoc/test use; the
    real runtime default is wired only by `get_telco_context_service()`).
    """

    def __init__(self, database: Optional[ContextDatabase] = None) -> None:
        self._db = database if database is not None else ContextDatabase(database_url=_IN_MEMORY_TEST_DATABASE_URL)

    async def get_or_create_profile(
        self,
        owner_kind: ContextProfileOwnerKind,
        owner_id: str,
        created_by_user_id: Optional[str] = None,
    ) -> TelcoContextProfile:
        """Idempotent: returns the existing profile for `(owner_kind,
        owner_id)` if one already exists (the DB-enforced unique
        constraint is what actually guarantees "at most one profile per
        owner" -- see `TelcoContextProfileRecord.__table_args__`),
        otherwise creates a new one.
        """
        if not owner_id or not owner_id.strip():
            raise ValueError("owner_id must not be blank")

        await self._db.ensure_schema()
        async with self._db.session() as session:
            existing = await session.execute(
                select(TelcoContextProfileRecord).where(
                    TelcoContextProfileRecord.owner_kind == owner_kind.value,
                    TelcoContextProfileRecord.owner_id == owner_id,
                )
            )
            record = existing.scalar_one_or_none()
            if record is not None:
                return _profile_to_domain(record)

            now = datetime.now(timezone.utc)
            record = TelcoContextProfileRecord(
                profile_id=str(uuid.uuid4()),
                owner_kind=owner_kind.value,
                owner_id=owner_id,
                created_by_user_id=created_by_user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return _profile_to_domain(record)

    async def get_profile(self, owner_kind: ContextProfileOwnerKind, owner_id: str) -> Optional[TelcoContextProfile]:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            result = await session.execute(
                select(TelcoContextProfileRecord).where(
                    TelcoContextProfileRecord.owner_kind == owner_kind.value,
                    TelcoContextProfileRecord.owner_id == owner_id,
                )
            )
            record = result.scalar_one_or_none()
            return _profile_to_domain(record) if record is not None else None

    async def get_profile_by_id(self, profile_id: str) -> Optional[TelcoContextProfile]:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            record = await session.get(TelcoContextProfileRecord, profile_id)
            return _profile_to_domain(record) if record is not None else None

    async def record_assertion(
        self,
        profile_id: str,
        dimension: ContextDimension,
        kind: AssertionKind,
        origin: ContextOrigin,
        *,
        raw_value: Optional[str] = None,
        canonical_value: Optional[str] = None,
        source_reference: Optional[str] = None,
        asserted_at: Optional[datetime] = None,
    ) -> ContextAssertion:
        """Append one new, immutable assertion. Validates via the domain
        model's own `ContextAssertion` constructor (kind/value
        consistency) before ever touching the database -- an invalid
        assertion is rejected structurally, never partially written.
        Does NOT recompute or return the resulting `ContextValue` --
        callers needing the current state call `get_context_state`
        separately, keeping "record a fact" and "compute current state"
        two distinct, independently-testable operations.
        """
        await self._db.ensure_schema()
        async with self._db.session() as session:
            profile_record = await session.get(TelcoContextProfileRecord, profile_id)
            if profile_record is None:
                raise LookupError(f"no TelcoContextProfile found with profile_id {profile_id!r}")

            now = datetime.now(timezone.utc)
            assertion = ContextAssertion(
                assertion_id=str(uuid.uuid4()),
                dimension=dimension,
                kind=kind,
                raw_value=raw_value,
                canonical_value=canonical_value,
                origin=origin,
                source_reference=source_reference,
                asserted_at=asserted_at,
                created_at=now,
            )
            record = TelcoContextAssertionRecord(
                assertion_id=assertion.assertion_id,
                profile_id=profile_id,
                dimension=assertion.dimension.value,
                kind=assertion.kind.value,
                raw_value=assertion.raw_value,
                canonical_value=assertion.canonical_value,
                origin=assertion.origin.value,
                source_reference=assertion.source_reference,
                asserted_at=assertion.asserted_at,
                created_at=assertion.created_at,
            )
            session.add(record)
            profile_record.updated_at = now
            await session.commit()
            return assertion

    async def get_assertions(self, profile_id: str, dimension: Optional[ContextDimension] = None) -> list[ContextAssertion]:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            query = select(TelcoContextAssertionRecord).where(TelcoContextAssertionRecord.profile_id == profile_id)
            if dimension is not None:
                query = query.where(TelcoContextAssertionRecord.dimension == dimension.value)
            query = query.order_by(TelcoContextAssertionRecord.created_at)
            result = await session.execute(query)
            return [_assertion_to_domain(record) for record in result.scalars().all()]

    async def get_context_state(self, profile_id: str) -> dict[ContextDimension, ContextValue]:
        """The current, deterministically-recomputed state of every
        dimension `profile_id` has at least one assertion for -- see
        `backend.context.domain.models.compute_context_state`.
        """
        assertions = await self.get_assertions(profile_id)
        return compute_context_state(assertions)


def get_telco_context_service() -> TelcoContextService:
    """The real runtime default -- backed by the process-wide
    `ContextDatabase` singleton (`get_context_database()`), mirroring
    `backend.cases.service`'s own `get_case_service()` pattern. Deferred
    import to avoid a module-level singleton constructed at import time.
    """
    from backend.context.sqlalchemy.db import get_context_database

    return TelcoContextService(get_context_database())
