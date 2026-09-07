"""API-layer orchestration for Chat Attachment upload/retrieval
(POST-5.1 B2) -- combines ADK session ownership
(`backend.api.session_service`) with the Attachment domain layer
(`backend.attachments.service`/`storage`), exactly the same role
`backend/api/case_service.py` plays for Cases (see that module's own
docstring). `backend/attachments/` itself knows nothing about ADK
sessions or FastAPI -- this is the one place those meet.

WRITE ORDER (instruction section 17): session authorization -> bounded
read -> validate actual image -> hash -> generate id/object key -> GCS
put -> Cloud SQL `READY` row -> return DTO. A `READY` row is never
created before the GCS object it references definitely exists. If the
Cloud SQL insert fails after a successful GCS write, the object is
best-effort deleted immediately -- never left as an avoidable orphan.

EVENT LOOP SAFETY: every `ChatAttachmentStorage` call here goes through
`starlette.concurrency.run_in_threadpool` -- the exact escape hatch this
codebase already uses for the other synchronous SDK call in this API
(`backend/api/execution_service.py`'s `PowerAutomateClient` calls, which
use the blocking `requests` library the same way).
"""
from __future__ import annotations

import hashlib
import logging
from typing import Optional

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from backend.api.session_service import ApiSessionService
from backend.attachments.models import ChatAttachmentRecord, ChatAttachmentStatus
from backend.attachments.service import AttachmentNotFoundError, AttachmentService
from backend.attachments.storage import ChatAttachmentStorage, build_object_name, generate_attachment_id
from backend.attachments.validation import InvalidImageError, normalize_filename, validate_image_bytes
from backend.config.settings import Settings
from backend.gateway.safe_error import (
    connector_unavailable,
    internal_error,
    not_found,
    payload_too_large,
    unsupported_media_type,
    validation_error,
)

logger = logging.getLogger(__name__)

_READ_CHUNK_SIZE = 65536  # 64 KiB per chunk -- bounded read granularity.

# Multipart parts commonly arrive with no real type claim at all -- both
# render as "no declared type" here (validation.py's own docstring
# explains why octet-stream is treated the same as absent rather than as
# a real, rejectable claim).
_UNCLAIMED_MIME_TYPES = frozenset({"", "application/octet-stream"})


async def _bounded_read(upload_file: UploadFile, max_bytes: int) -> bytes:
    """Reads at most `max_bytes` (+ one final chunk's worth) from
    `upload_file`, regardless of what `Content-Length` the client
    declared -- a client cannot bypass the application limit merely by
    lying about size. Raises `payload_too_large` (413) the moment the
    bound is exceeded, without buffering unboundedly first.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload_file.read(_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise payload_too_large(f"The uploaded file exceeds the {max_bytes} byte limit.")
        chunks.append(chunk)
    return b"".join(chunks)


def _attachment_response_status(record: ChatAttachmentRecord) -> ChatAttachmentRecord:
    """A `DELETED` attachment is treated as not-found by every read path
    in this module -- never distinguished from "never existed" (same
    anti-enumeration discipline the domain layer already applies to
    ownership).
    """
    if record.status == ChatAttachmentStatus.DELETED.value:
        raise AttachmentNotFoundError("No such attachment was found.")
    return record


async def upload_attachment(
    *,
    session_service: ApiSessionService,
    attachment_service: AttachmentService,
    storage: ChatAttachmentStorage,
    settings: Settings,
    user_id: str,
    session_id: str,
    upload_file: UploadFile,
) -> ChatAttachmentRecord:
    # (1) Session ownership -- instruction section 7: session must exist
    # AND belong to the resolved principal. Mirrors case_service.py's
    # `link_session` exactly; raises the same anti-enumeration `not_found`.
    await session_service.get_session(session_id, user_id)

    if not storage.is_configured:
        raise connector_unavailable("Attachment storage is not currently available.")

    # (2) Bounded read -- size limit enforced BEFORE any decoding.
    data = await _bounded_read(upload_file, settings.chat_attachment_max_bytes)
    if not data:
        raise validation_error("The uploaded file was empty.")

    # (3) Declared vs. actual MIME (backend.attachments.validation's
    # own documented decision).
    declared_mime_type: Optional[str] = upload_file.content_type
    if declared_mime_type in _UNCLAIMED_MIME_TYPES:
        declared_mime_type = None
    try:
        authoritative_mime_type = validate_image_bytes(data, declared_mime_type)
    except InvalidImageError as exc:
        raise unsupported_media_type(str(exc)) from exc

    # (4) Hash -- integrity/diagnostics/future evidence identity, never
    # authorization, never dedupe (instruction section 16).
    sha256_hex = hashlib.sha256(data).hexdigest()

    # (5) Identity + opaque object key, generated BEFORE the GCS write
    # so the object actually written and the row later persisted always
    # agree on both.
    attachment_id = generate_attachment_id()
    object_name = build_object_name(session_id, attachment_id)

    # (6) GCS put -- off the event loop.
    try:
        await run_in_threadpool(storage.put_bytes, object_name, data, authoritative_mime_type)
    except Exception:
        logger.exception("attachment GCS upload failed: attachment_id=%s", attachment_id)
        raise connector_unavailable("Attachment storage is not currently available.") from None

    # (7) Cloud SQL READY row -- only now that the object definitely
    # exists. On failure, best-effort clean up the now-orphaned object
    # immediately rather than leaving it behind.
    safe_filename = normalize_filename(upload_file.filename or "")
    try:
        record = await attachment_service.create(
            owner_user_id=user_id,
            session_id=session_id,
            original_filename=safe_filename,
            mime_type=authoritative_mime_type,
            size_bytes=len(data),
            sha256=sha256_hex,
            attachment_id=attachment_id,
        )
    except Exception:
        logger.exception("attachment metadata write failed after GCS upload: attachment_id=%s", attachment_id)
        try:
            await run_in_threadpool(storage.delete, object_name)
        except Exception:
            logger.exception("best-effort GCS cleanup also failed: attachment_id=%s", attachment_id)
        raise internal_error("The attachment could not be saved.") from None

    return record


async def get_attachment_metadata(
    attachment_service: AttachmentService,
    user_id: str,
    attachment_id: str,
) -> ChatAttachmentRecord:
    """Instruction section 8: authorize using current principal + stored
    attachment ownership -- never solely because the caller knows
    `attachment_id`.
    """
    try:
        record = await attachment_service.get_owned(attachment_id, user_id)
        return _attachment_response_status(record)
    except AttachmentNotFoundError as exc:
        raise not_found("No such attachment was found.") from exc


async def get_attachment_content(
    attachment_service: AttachmentService,
    storage: ChatAttachmentStorage,
    user_id: str,
    attachment_id: str,
) -> tuple[bytes, ChatAttachmentRecord]:
    record = await get_attachment_metadata(attachment_service, user_id, attachment_id)

    if not storage.is_configured:
        raise connector_unavailable("Attachment storage is not currently available.")

    try:
        data = await run_in_threadpool(storage.get_bytes, record.storage_object_name)
    except Exception:
        # Cloud SQL says this attachment is READY/LINKED, but GCS doesn't
        # have the object -- fail deterministically (instruction section
        # 24), never return empty/fabricated content, never silently
        # recreate anything. Safe diagnostic context only (attachment_id,
        # never bucket/object internals) reaches the log.
        logger.error("attachment content missing from storage: attachment_id=%s", attachment_id)
        raise internal_error("The attachment content could not be retrieved.") from None

    return data, record
