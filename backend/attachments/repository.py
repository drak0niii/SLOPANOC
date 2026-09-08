"""The async-SQLAlchemy repository for the Chat Attachment domain
(POST-5.1 B1).

Owns its own engine, session factory, and `DeclarativeBase`/table set --
mirrors `backend/knowledge/repository/sqlalchemy.py`'s self-contained
pattern (engine + CRUD in one file) rather than `backend/cases/db.py`'s
split (a separate `db.py` for the engine, business logic inline in
`service.py`) -- this package's `service.py` (B1) needs a genuine
data-access seam beneath it for later passes (B2's HTTP layer, B7's
retention sweep), so a distinct `repository.py` is worth the extra file
here even though Cases didn't need one.

TRANSITIONS ARE ATOMIC, NEVER CHECK-THEN-WRITE: `link_to_message`/
`mark_deleted` are single conditional `UPDATE ... WHERE status = ...`
statements -- the WHERE clause IS the concurrency-safe state-machine
guard (instruction section 5: "invalid transitions must fail
deterministically"). A caller never needs to `get()` first, inspect
`status`, then decide whether to write -- that would be racy across two
round trips. Returns `False` (never raises) when no row matched the
expected prior status/id, so `service.py` can translate that into a
clear domain error without this layer needing to know why the caller
thought the transition should apply.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.attachments.models import Base, ChatAttachmentRecord, ChatAttachmentStatus


def _engine_kwargs(database_url: str) -> dict[str, Any]:
    """Mirrors `backend/cases/db.py`/`backend/knowledge/repository/
    sqlalchemy.py`'s own `_engine_kwargs` exactly (deliberately
    duplicated, not imported -- same domain-isolation discipline both
    already establish). SQLite-in-memory-only special case; never fires
    for any other dialect, including `postgresql+asyncpg://...`.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database in (None, ":memory:"):
        return {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    return {}


class AttachmentRepository:
    """`database_url` is always explicit -- never defaults to or
    hardcodes any environment-specific path; the process-wide singleton
    (`backend.attachments.service.get_attachment_repository`, wired in
    B2 when a live consumer exists) is what binds this to
    `Settings.resolve_database_url()`.
    """

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, **_engine_kwargs(database_url))
        self._session_factory = async_sessionmaker(bind=self._engine, expire_on_commit=False)
        self._schema_ready = False

    async def ensure_schema(self) -> None:
        """Idempotent, lazy `create_all` -- mirrors the established
        pattern; every public method below calls this itself.
        """
        if self._schema_ready:
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._schema_ready = True

    async def close(self) -> None:
        await self._engine.dispose()

    async def add(self, attachment: ChatAttachmentRecord) -> None:
        await self.ensure_schema()
        async with self._session_factory() as session:
            session.add(attachment)
            await session.commit()

    async def get(self, attachment_id: str) -> Optional[ChatAttachmentRecord]:
        await self.ensure_schema()
        async with self._session_factory() as session:
            return await session.get(ChatAttachmentRecord, attachment_id)

    async def get_for_owner_session(self, owner_user_id: str, session_id: str) -> list[ChatAttachmentRecord]:
        """Ownership-scoped retrieval (instruction section 7's index)."""
        await self.ensure_schema()
        async with self._session_factory() as session:
            result = await session.execute(
                select(ChatAttachmentRecord)
                .where(
                    ChatAttachmentRecord.owner_user_id == owner_user_id,
                    ChatAttachmentRecord.session_id == session_id,
                )
                .order_by(ChatAttachmentRecord.created_at)
            )
            return list(result.scalars().all())

    async def list_for_message(self, session_id: str, message_id: str) -> list[ChatAttachmentRecord]:
        """POST-5.1 B4B: `message_id` here is the owning turn's ADK
        `invocation_id` (see `models.py`'s `ChatAttachmentRecord.message_id`
        docstring) -- never the frontend-visible history message id.
        """
        await self.ensure_schema()
        async with self._session_factory() as session:
            result = await session.execute(
                select(ChatAttachmentRecord)
                .where(
                    ChatAttachmentRecord.session_id == session_id,
                    ChatAttachmentRecord.message_id == message_id,
                )
                .order_by(ChatAttachmentRecord.created_at)
            )
            return list(result.scalars().all())

    async def link_to_message(self, attachment_id: str, message_id: str, linked_at: datetime) -> bool:
        """Atomic `READY -> LINKED`. Returns `True` iff exactly one row
        transitioned; `False` if the attachment doesn't exist or wasn't
        `READY` (already linked/deleted, or an unrecognized id) -- never
        raises for that case, `service.py` decides how to surface it.
        """
        await self.ensure_schema()
        async with self._session_factory() as session:
            result = await session.execute(
                update(ChatAttachmentRecord)
                .where(
                    ChatAttachmentRecord.attachment_id == attachment_id,
                    ChatAttachmentRecord.status == ChatAttachmentStatus.READY.value,
                )
                .values(status=ChatAttachmentStatus.LINKED.value, message_id=message_id, linked_at=linked_at)
            )
            await session.commit()
            return result.rowcount == 1

    async def link_many_to_message(
        self,
        attachment_ids: list[str],
        owner_user_id: str,
        session_id: str,
        message_id: str,
        linked_at: datetime,
    ) -> bool:
        """POST-5.1 B5 -- atomic bulk `READY -> LINKED` for a real
        multimodal turn's attachments, ALL transitioning to the SAME
        `message_id` (the owning turn's ADK invocation_id). ONE UPDATE
        statement, never a loop of individual transitions -- the
        conditional WHERE (id IN (...), owner, session, status=READY) is
        itself the atomicity/ownership/state guard, identical in spirit
        to `link_to_message`'s own single-row version. Returns `True` iff
        EVERY requested id matched and transitioned (`rowcount ==
        len(attachment_ids)`); otherwise the transaction is never
        committed (equivalent to a rollback -- nothing this call touched
        becomes durable) and `False` is returned, so the caller can never
        observe a partially-linked set (e.g. 3 of 4 images LINKED, one
        still READY) even under a concurrent transition or an invalid id
        slipped in since request-time validation. Assumes `attachment_ids`
        is already de-duplicated (the caller's job -- see
        `backend/api/attachment_service.py`'s `prepare_attachments_for_turn`).
        A vacuous `True` for an empty list -- nothing to link is trivially
        atomic.
        """
        if not attachment_ids:
            return True
        await self.ensure_schema()
        async with self._session_factory() as session:
            result = await session.execute(
                update(ChatAttachmentRecord)
                .where(
                    ChatAttachmentRecord.attachment_id.in_(attachment_ids),
                    ChatAttachmentRecord.owner_user_id == owner_user_id,
                    ChatAttachmentRecord.session_id == session_id,
                    ChatAttachmentRecord.status == ChatAttachmentStatus.READY.value,
                )
                .values(status=ChatAttachmentStatus.LINKED.value, message_id=message_id, linked_at=linked_at)
            )
            if result.rowcount != len(attachment_ids):
                await session.rollback()
                return False
            await session.commit()
            return True

    async def mark_deleted(self, attachment_id: str, deleted_at: datetime) -> bool:
        """Atomic `READY|LINKED -> DELETED`. Returns `True` iff exactly
        one row transitioned; `False` if the attachment doesn't exist or
        is already `DELETED`.
        """
        await self.ensure_schema()
        async with self._session_factory() as session:
            result = await session.execute(
                update(ChatAttachmentRecord)
                .where(
                    ChatAttachmentRecord.attachment_id == attachment_id,
                    ChatAttachmentRecord.status.in_(
                        (ChatAttachmentStatus.READY.value, ChatAttachmentStatus.LINKED.value)
                    ),
                )
                .values(status=ChatAttachmentStatus.DELETED.value, deleted_at=deleted_at)
            )
            await session.commit()
            return result.rowcount == 1
