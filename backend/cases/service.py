"""Deterministic Case/Fault domain operations.

ACCESS CONTROL (instruction section 7): every read/write method takes the
REQUESTING user's id and independently re-verifies membership against the
database before doing anything -- never trusting a caller's prior check.
A case that does not exist and a case that exists but this user is not a
member of are DELIBERATELY indistinguishable (`_require_membership`
raises the identical `not_found` SafeError either way) -- the same
anti-enumeration behavior Phase 4C already established for foreign ADK
sessions.

SESSION LINKING lives partly here (the Case-side bookkeeping:
"is this session already linked, to which case, is the requester a Case
member") and partly in `backend/api/case_service.py` (the ADK-side check:
"does the requester actually own this ADK session"). This module knows
nothing about ADK sessions/`ApiSessionService` -- see this package's
`__init__.py` docstring for why that separation is deliberate.

NO LINK HISTORY IN v1: `CaseSessionLinkRecord.session_id` is a primary
key, so unlinking simply deletes the row rather than marking it inactive.
This is the simplest safe v1 implementation of "one Session linked to at
most one active Case" (instruction section 8) -- a past link is not
queryable after unlinking, which this milestone's requirements do not
call for; a future phase could add an `is_active`/history table without
changing this class's public shape if that becomes necessary.

AGENT-WRITABLE CONTEXT (instruction section 15): `record_case_analysis`
is the ONLY method that writes a `source_type="agent"` item, and it is
the only method that validates against `AGENT_ANALYSIS_KINDS` -- every
other write path (`add_user_context_item`) always writes
`source_type="user"` and accepts any `ContextItemKind`, since a human
Case member may legitimately record evidence, a decision, a resolution,
etc. Nothing in this module ever lets a caller choose an arbitrary
`source_type` -- it is always determined by WHICH method was called, never
passed in as a parameter (instruction section 14: "client cannot claim
that arbitrary data was retrieved from Teams/tickets/alarms").
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.cases.db import CaseDatabase, get_case_database
from backend.cases.models import (
    CaseContextItemRecord,
    CaseMembershipRecord,
    CaseRecord,
    CaseSessionLinkRecord,
)
from backend.cases.schemas import (
    AGENT_ANALYSIS_KINDS,
    CaseContextItemDTO,
    CaseDTO,
    CaseListItemDTO,
    CaseMemberRole,
    CaseMembershipDTO,
    CaseSessionLinkDTO,
    CaseStatus,
    ContextItemKind,
    SourceType,
)
from backend.gateway.safe_error import SafeError, SafeErrorException, not_found, validation_error

_IN_MEMORY_TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


def _authorization_error(message: str) -> SafeErrorException:
    return SafeErrorException(SafeError(error_code="authorization_error", user_message=message))


def _conflict_error(message: str) -> SafeErrorException:
    return SafeErrorException(SafeError(error_code="action_failure", user_message=message))


def _case_to_dto(record: CaseRecord) -> CaseDTO:
    return CaseDTO(
        case_id=record.case_id,
        title=record.title,
        problem_statement=record.problem_statement,
        external_reference=record.external_reference,
        status=CaseStatus(record.status),
        created_by_user_id=record.created_by_user_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _item_to_dto(record: CaseContextItemRecord) -> CaseContextItemDTO:
    return CaseContextItemDTO(
        item_id=record.item_id,
        case_id=record.case_id,
        kind=ContextItemKind(record.kind),
        content=record.content,
        source_type=SourceType(record.source_type),
        source_ref=record.source_ref,
        source_timestamp=record.source_timestamp,
        source_author=record.source_author,
        confidence=record.confidence,
        created_by_user_id=record.created_by_user_id,
        created_by_agent=record.created_by_agent,
        created_at=record.created_at,
        supporting_item_ids=list(record.supporting_item_ids or []),
    )


def _link_to_dto(record: CaseSessionLinkRecord) -> CaseSessionLinkDTO:
    return CaseSessionLinkDTO(
        case_id=record.case_id,
        session_id=record.session_id,
        session_user_id=record.session_user_id,
        linked_at=record.linked_at,
        linked_by_user_id=record.linked_by_user_id,
    )


class CaseService:
    def __init__(self, database: Optional[CaseDatabase] = None) -> None:
        # Defaults to an isolated in-memory SQLite database -- fast, safe
        # for ad hoc/test construction, never the configured production
        # URL by accident. The real runtime default is wired only by
        # `get_case_service()` below, via `get_case_database()`.
        self._db = database if database is not None else CaseDatabase(database_url=_IN_MEMORY_TEST_DATABASE_URL)

    async def _load_case_and_membership(
        self, session: AsyncSession, case_id: str, user_id: str
    ) -> tuple[Optional[CaseRecord], Optional[CaseMembershipRecord]]:
        case_record = await session.get(CaseRecord, case_id)
        if case_record is None:
            return None, None
        membership = await session.get(CaseMembershipRecord, (case_id, user_id))
        return case_record, membership

    async def _require_membership(self, session: AsyncSession, case_id: str, user_id: str) -> CaseRecord:
        case_record, membership = await self._load_case_and_membership(session, case_id, user_id)
        if case_record is None or membership is None:
            raise not_found("No case was found with that id.")
        return case_record

    async def _require_owner(self, session: AsyncSession, case_id: str, user_id: str) -> CaseRecord:
        case_record, membership = await self._load_case_and_membership(session, case_id, user_id)
        if case_record is None or membership is None:
            raise not_found("No case was found with that id.")
        if membership.role != CaseMemberRole.OWNER.value:
            raise _authorization_error("Only the case owner can do this.")
        return case_record

    # --- Case CRUD -----------------------------------------------------

    async def create_case(
        self,
        user_id: str,
        title: str,
        problem_statement: str,
        external_reference: Optional[str] = None,
    ) -> CaseDTO:
        if not title or not title.strip():
            raise validation_error("A case title is required.")
        if not problem_statement or not problem_statement.strip():
            raise validation_error("A problem statement is required.")

        await self._db.ensure_schema()
        now = datetime.now(timezone.utc)
        case_id = str(uuid.uuid4())
        async with self._db.session() as session:
            record = CaseRecord(
                case_id=case_id,
                title=title.strip(),
                problem_statement=problem_statement.strip(),
                external_reference=(external_reference.strip() if external_reference else None) or None,
                status=CaseStatus.OPEN.value,
                created_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            # POST-5.1 A4: explicit flush before adding the dependent
            # membership row -- confirmed live against Cloud SQL that
            # without it, PostgreSQL can execute the
            # slopanoc_case_memberships INSERT (referencing this case_id)
            # before the slopanoc_cases INSERT that satisfies its FK
            # constraint, raising ForeignKeyViolationError. SQLite never
            # surfaced this: it does not enforce FOREIGN KEY constraints
            # unless PRAGMA foreign_keys=ON is set, which this codebase
            # never sets. Still one atomic transaction -- flush is not
            # commit; a failure after this point still rolls back both
            # rows together.
            await session.flush()
            session.add(
                CaseMembershipRecord(
                    case_id=case_id, user_id=user_id, role=CaseMemberRole.OWNER.value, created_at=now
                )
            )
            await session.commit()
        return _case_to_dto(record)

    async def get_case(self, user_id: str, case_id: str) -> CaseDTO:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            record = await self._require_membership(session, case_id, user_id)
            return _case_to_dto(record)

    async def list_cases(self, user_id: str) -> list[CaseListItemDTO]:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            stmt = (
                select(CaseRecord)
                .join(CaseMembershipRecord, CaseMembershipRecord.case_id == CaseRecord.case_id)
                .where(CaseMembershipRecord.user_id == user_id)
                .order_by(CaseRecord.updated_at.desc())
            )
            result = await session.execute(stmt)
            return [
                CaseListItemDTO(case_id=r.case_id, title=r.title, status=CaseStatus(r.status), updated_at=r.updated_at)
                for r in result.scalars().all()
            ]

    async def update_case(
        self,
        user_id: str,
        case_id: str,
        title: Optional[str] = None,
        problem_statement: Optional[str] = None,
        status: Optional[str] = None,
        external_reference: Optional[str] = None,
    ) -> CaseDTO:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            record = await self._require_membership(session, case_id, user_id)
            if title is not None:
                if not title.strip():
                    raise validation_error("Title cannot be empty.")
                record.title = title.strip()
            if problem_statement is not None:
                if not problem_statement.strip():
                    raise validation_error("Problem statement cannot be empty.")
                record.problem_statement = problem_statement.strip()
            if external_reference is not None:
                record.external_reference = external_reference.strip() or None
            if status is not None:
                record.status = CaseStatus(status).value
            record.updated_at = datetime.now(timezone.utc)
            await session.commit()
            return _case_to_dto(record)

    # --- Membership ------------------------------------------------------

    async def add_member(
        self, requesting_user_id: str, case_id: str, member_user_id: str, role: str = "member"
    ) -> CaseMembershipDTO:
        if not member_user_id or not member_user_id.strip():
            raise validation_error("A member user id is required.")
        role_enum = CaseMemberRole(role)

        await self._db.ensure_schema()
        async with self._db.session() as session:
            await self._require_owner(session, case_id, requesting_user_id)
            existing = await session.get(CaseMembershipRecord, (case_id, member_user_id))
            if existing is not None:
                existing.role = role_enum.value
                await session.commit()
                return CaseMembershipDTO(
                    case_id=case_id, user_id=member_user_id, role=role_enum, created_at=existing.created_at
                )
            now = datetime.now(timezone.utc)
            record = CaseMembershipRecord(case_id=case_id, user_id=member_user_id, role=role_enum.value, created_at=now)
            session.add(record)
            await session.commit()
            return CaseMembershipDTO(case_id=case_id, user_id=member_user_id, role=role_enum, created_at=now)

    async def is_member(self, user_id: str, case_id: str) -> bool:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            case_record, membership = await self._load_case_and_membership(session, case_id, user_id)
            return case_record is not None and membership is not None

    # --- Session linking (Case-side bookkeeping only) --------------------

    async def link_session(
        self, user_id: str, case_id: str, session_id: str, session_owner_user_id: str
    ) -> CaseSessionLinkDTO:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            await self._require_membership(session, case_id, user_id)
            existing_link = await session.get(CaseSessionLinkRecord, session_id)
            if existing_link is not None:
                if existing_link.case_id == case_id:
                    return _link_to_dto(existing_link)  # idempotent re-link to the same case
                raise _conflict_error(
                    "This session is already linked to a different case. Unlink it before linking a new one."
                )
            now = datetime.now(timezone.utc)
            record = CaseSessionLinkRecord(
                session_id=session_id,
                case_id=case_id,
                session_user_id=session_owner_user_id,
                linked_at=now,
                linked_by_user_id=user_id,
            )
            session.add(record)
            await session.commit()
            return _link_to_dto(record)

    async def unlink_session(self, user_id: str, case_id: str, session_id: str) -> None:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            await self._require_membership(session, case_id, user_id)
            existing_link = await session.get(CaseSessionLinkRecord, session_id)
            if existing_link is None or existing_link.case_id != case_id:
                raise not_found("No such session link was found for this case.")
            await session.delete(existing_link)
            await session.commit()

    async def get_link_for_session(self, session_id: str) -> Optional[CaseSessionLinkDTO]:
        """No membership check -- the caller (backend/api/case_service.py)
        already independently verified ADK session ownership; resolving
        "which case (if any) is this session linked to" is not itself a
        Case-membership-gated question.
        """
        await self._db.ensure_schema()
        async with self._db.session() as session:
            record = await session.get(CaseSessionLinkRecord, session_id)
            return _link_to_dto(record) if record is not None else None

    # --- Context ledger ----------------------------------------------------

    async def add_user_context_item(self, user_id: str, case_id: str, kind: str, content: str) -> CaseContextItemDTO:
        """The user-facing write path -- always `source_type=USER`,
        `source_author`/`created_by_user_id` always the resolved
        `UserContext`, never client-supplied (instruction section 14).
        """
        kind_enum = ContextItemKind(kind)
        if not content or not content.strip():
            raise validation_error("Context item content is required.")

        await self._db.ensure_schema()
        async with self._db.session() as session:
            case_record = await self._require_membership(session, case_id, user_id)
            now = datetime.now(timezone.utc)
            record = CaseContextItemRecord(
                item_id=str(uuid.uuid4()),
                case_id=case_id,
                kind=kind_enum.value,
                content=content.strip(),
                source_type=SourceType.USER.value,
                source_author=user_id,
                created_by_user_id=user_id,
                created_at=now,
                supporting_item_ids=[],
            )
            session.add(record)
            case_record.updated_at = now
            await session.commit()
            return _item_to_dto(record)

    async def record_case_analysis(
        self,
        user_id: str,
        agent_name: str,
        case_id: str,
        kind: str,
        content: str,
        confidence: Optional[float] = None,
        supporting_item_ids: Optional[list[str]] = None,
    ) -> CaseContextItemDTO:
        """The ONE agent-writable path (instruction section 15). `user_id`
        is the resolved owner of the session the agent is acting within --
        membership is re-verified here independently, even though the
        calling tool layer should already have checked it, exactly the
        same defense-in-depth posture the rest of this codebase uses.

        `kind` is restricted to `AGENT_ANALYSIS_KINDS`
        (hypothesis/recommendation/open_question) -- anything else raises
        a `validation_error` before anything is written. Every id in
        `supporting_item_ids` is independently verified to exist, belong
        to THIS case, before the write proceeds (instruction section 17)
        -- a single unverifiable id fails the whole call, nothing partial
        is written.
        """
        kind_enum = ContextItemKind(kind)
        if kind_enum not in AGENT_ANALYSIS_KINDS:
            allowed = sorted(k.value for k in AGENT_ANALYSIS_KINDS)
            raise validation_error(f"'{kind_enum.value}' cannot be recorded by the agent -- only {allowed} are allowed.")
        if not content or not content.strip():
            raise validation_error("Case analysis content is required.")

        requested_supporting_ids = list(supporting_item_ids or [])

        await self._db.ensure_schema()
        async with self._db.session() as session:
            case_record = await self._require_membership(session, case_id, user_id)

            validated_supporting_ids: list[str] = []
            for supporting_id in requested_supporting_ids:
                supporting_item = await session.get(CaseContextItemRecord, supporting_id)
                if supporting_item is None or supporting_item.case_id != case_id:
                    raise validation_error("One or more supporting item ids could not be verified for this case.")
                validated_supporting_ids.append(supporting_id)

            now = datetime.now(timezone.utc)
            record = CaseContextItemRecord(
                item_id=str(uuid.uuid4()),
                case_id=case_id,
                kind=kind_enum.value,
                content=content.strip(),
                source_type=SourceType.AGENT.value,
                source_author=agent_name,
                created_by_agent=agent_name,
                created_at=now,
                confidence=confidence,
                supporting_item_ids=validated_supporting_ids,
            )
            session.add(record)
            case_record.updated_at = now
            await session.commit()
            return _item_to_dto(record)

    async def get_context_items(self, user_id: str, case_id: str) -> list[CaseContextItemDTO]:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            await self._require_membership(session, case_id, user_id)
            stmt = (
                select(CaseContextItemRecord)
                .where(CaseContextItemRecord.case_id == case_id)
                .order_by(CaseContextItemRecord.created_at.asc())
            )
            result = await session.execute(stmt)
            return [_item_to_dto(r) for r in result.scalars().all()]


@lru_cache(maxsize=1)
def get_case_service() -> CaseService:
    return CaseService(get_case_database())
