"""Deterministic Chat Attachment domain operations (POST-5.1 B1).

AUTHORIZATION LIVES HERE (instruction section 16): every read/write
method that acts on an EXISTING attachment takes the requesting
principal's `owner_user_id` and independently re-verifies it against the
stored row before doing anything -- never trusting a caller's prior
check. An attachment that does not exist and one that exists but belongs
to a different principal are DELIBERATELY indistinguishable
(`AttachmentNotFoundError` either way) -- the same anti-enumeration
discipline `backend/cases/service.py` already established for Cases and
`backend/api/session_service.py` established for ADK sessions.

NOT BUILT HERE (explicitly out of scope for B1, per instruction): HTTP
identity resolution (a `UserContext`/FastAPI dependency is a B2 concern),
and upload itself (`create()` takes already-known metadata -- filename,
mime type, size, sha256, storage object name -- it never touches raw
bytes; B2's upload endpoint computes those from real bytes via
`backend.attachments.storage` and this module's `build_object_name`/
`generate_attachment_id`, then calls `create()`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from backend.attachments.models import ChatAttachmentRecord, ChatAttachmentStatus
from backend.attachments.repository import AttachmentRepository
from backend.attachments.storage import build_object_name, generate_attachment_id
from backend.config.settings import get_settings


class AttachmentError(Exception):
    """Base class for every error this module raises."""


class AttachmentNotFoundError(AttachmentError):
    """No such attachment, OR it exists but does not belong to the
    requesting principal/session -- see module docstring's anti-
    enumeration rationale. Never distinguishes the two cases in its
    message.
    """


class InvalidAttachmentTransitionError(AttachmentError):
    """The attachment was found and is owned by the requesting
    principal, but is not in a state the requested transition allows
    (e.g. already `LINKED`/`DELETED`). Raised only after ownership is
    already confirmed, so it never leaks existence to the wrong
    principal.
    """


def _now(now: Optional[datetime] = None) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


class AttachmentService:
    def __init__(self, repository: AttachmentRepository) -> None:
        self._repository = repository

    async def create(
        self,
        owner_user_id: str,
        session_id: str,
        original_filename: str,
        mime_type: str,
        size_bytes: int,
        sha256: str,
        now: Optional[datetime] = None,
        *,
        attachment_id: Optional[str] = None,
    ) -> ChatAttachmentRecord:
        """Registers a new, already-durably-stored attachment as `READY`.
        Takes metadata only -- the caller (B2's upload endpoint) is
        responsible for having already written the bytes to
        `build_object_name(session_id, attachment_id)` in GCS before
        calling this; `create()` never writes binary data itself.

        `attachment_id` (POST-5.1 B2 addition, optional, keyword-only):
        when omitted (B1's original behavior, still exactly what every
        B1 test exercises), one is generated here. B2's real upload flow
        passes one explicitly -- it must generate the id *before* the
        GCS write (so the object key and the DB row agree), meaning the
        id has to exist before this method is ever called; this
        parameter lets the id be supplied rather than silently
        regenerated (which would desynchronize `storage_object_name`
        from the object B2 actually already wrote).
        """
        if not original_filename.strip():
            raise ValueError("original_filename must not be empty.")
        if not mime_type.strip():
            raise ValueError("mime_type must not be empty.")
        if size_bytes <= 0:
            raise ValueError("size_bytes must be positive.")
        if not sha256.strip():
            raise ValueError("sha256 must not be empty.")

        attachment_id = attachment_id or generate_attachment_id()
        created_at = _now(now)
        record = ChatAttachmentRecord(
            attachment_id=attachment_id,
            owner_user_id=owner_user_id,
            session_id=session_id,
            message_id=None,
            original_filename=original_filename.strip(),
            mime_type=mime_type.strip(),
            size_bytes=size_bytes,
            sha256=sha256.strip(),
            storage_object_name=build_object_name(session_id, attachment_id),
            status=ChatAttachmentStatus.READY.value,
            created_at=created_at,
            linked_at=None,
            deleted_at=None,
        )
        await self._repository.add(record)
        return record

    async def get_for_owner_session(self, owner_user_id: str, session_id: str) -> list[ChatAttachmentRecord]:
        return await self._repository.get_for_owner_session(owner_user_id, session_id)

    async def list_for_message(self, session_id: str, message_id: str) -> list[ChatAttachmentRecord]:
        return await self._repository.list_for_message(session_id, message_id)

    async def _require_owned(self, attachment_id: str, owner_user_id: str, session_id: str) -> ChatAttachmentRecord:
        record = await self._repository.get(attachment_id)
        if record is None or record.owner_user_id != owner_user_id or record.session_id != session_id:
            raise AttachmentNotFoundError("No such attachment was found.")
        return record

    async def get_owned(self, attachment_id: str, owner_user_id: str) -> ChatAttachmentRecord:
        """POST-5.1 B2 addition: authorize by (`attachment_id`,
        `owner_user_id`) alone -- for the two by-attachment-id-only API
        routes (`GET /api/attachments/{id}` and `.../content`), which
        have no `session_id` in their URL to also check. Same anti-
        enumeration discipline as `_require_owned`: unknown id and
        wrong-owner id raise the identical `AttachmentNotFoundError`.
        """
        record = await self._repository.get(attachment_id)
        if record is None or record.owner_user_id != owner_user_id:
            raise AttachmentNotFoundError("No such attachment was found.")
        return record

    async def link_to_message(
        self,
        attachment_id: str,
        owner_user_id: str,
        session_id: str,
        message_id: str,
        now: Optional[datetime] = None,
    ) -> ChatAttachmentRecord:
        """`READY -> LINKED`. Validates, in order: the attachment exists
        and belongs to (`owner_user_id`, `session_id`) (instruction
        section 16: "owner, session"), then that it is still `READY`
        (instruction: "READY, not deleted" -- `LINKED`/`DELETED` are both
        rejected here as "not READY", so a caller can never re-link an
        already-linked or link a deleted attachment).

        POST-5.1 B4A/B4B: `message_id` must be the owning user turn's ADK
        `invocation_id` -- see `models.py`'s `ChatAttachmentRecord
        .message_id` docstring for the full semantic and why it is NOT
        the frontend-visible history message id. Not called anywhere in
        production yet (that's B5's job, wiring `attachment_ids` into a
        real message send) -- B4B only ever READS via `list_for_message`.
        """
        record = await self._require_owned(attachment_id, owner_user_id, session_id)
        if record.status != ChatAttachmentStatus.READY.value:
            raise InvalidAttachmentTransitionError(
                f"attachment {attachment_id!r} is {record.status!r}, not READY -- cannot link."
            )
        linked_at = _now(now)
        applied = await self._repository.link_to_message(attachment_id, message_id, linked_at)
        if not applied:
            # Lost a race with a concurrent transition since the check above.
            raise InvalidAttachmentTransitionError(
                f"attachment {attachment_id!r} was no longer READY when the link was attempted."
            )
        return await self._repository.get(attachment_id)  # type: ignore[return-value]

    async def link_many_to_message(
        self,
        attachment_ids: list[str],
        owner_user_id: str,
        session_id: str,
        message_id: str,
        now: Optional[datetime] = None,
    ) -> None:
        """POST-5.1 B5 -- atomic bulk `READY -> LINKED`, all attachments
        to the SAME real ADK turn `message_id`. Deliberately does NOT
        pre-validate each attachment individually (unlike
        `link_to_message`'s single-attachment `_require_owned` +
        status check) -- by the time this is called, every id has
        already been fully validated by
        `backend/api/attachment_service.py`'s `prepare_attachments_for_turn`
        (existence, ownership, session, READY status, MIME, limits); this
        method's own atomic conditional UPDATE is the re-check that
        matters (defense against a narrow concurrent-transition race,
        never the primary authorization boundary). Raises
        `InvalidAttachmentTransitionError` (generic, safe message -- never
        which specific id/why) if the atomic bulk transition did not
        apply to every requested id; a no-op (never raises) for an empty
        list.
        """
        if not attachment_ids:
            return
        linked_at = _now(now)
        applied = await self._repository.link_many_to_message(
            attachment_ids, owner_user_id, session_id, message_id, linked_at
        )
        if not applied:
            raise InvalidAttachmentTransitionError(
                "one or more attachments could not be linked to this turn -- linkage aborted, none were changed."
            )

    async def mark_deleted(
        self,
        attachment_id: str,
        owner_user_id: str,
        session_id: str,
        now: Optional[datetime] = None,
    ) -> ChatAttachmentRecord:
        """`READY|LINKED -> DELETED`."""
        record = await self._require_owned(attachment_id, owner_user_id, session_id)
        if record.status == ChatAttachmentStatus.DELETED.value:
            raise InvalidAttachmentTransitionError(f"attachment {attachment_id!r} is already DELETED.")
        deleted_at = _now(now)
        applied = await self._repository.mark_deleted(attachment_id, deleted_at)
        if not applied:
            raise InvalidAttachmentTransitionError(
                f"attachment {attachment_id!r} was already DELETED when deletion was attempted."
            )
        return await self._repository.get(attachment_id)  # type: ignore[return-value]


@lru_cache(maxsize=1)
def get_attachment_repository() -> AttachmentRepository:
    """Process-wide singleton, mirroring `get_case_database()`/
    `get_knowledge_repository()`'s pattern. No live caller exists yet in
    B1 -- this exists so B2 has a ready-made wiring point, matching how
    `backend/tools/knowledge/runtime.py` composes 5.1F's repository.
    """
    return AttachmentRepository(get_settings().resolve_database_url())


@lru_cache(maxsize=1)
def get_attachment_service() -> AttachmentService:
    return AttachmentService(get_attachment_repository())
