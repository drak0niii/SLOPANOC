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
from dataclasses import dataclass
from typing import Optional

from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from backend.api.session_service import ApiSessionService
from backend.attachments.models import ChatAttachmentRecord, ChatAttachmentStatus
from backend.attachments.service import AttachmentNotFoundError, AttachmentService, InvalidAttachmentTransitionError
from backend.attachments.storage import (
    AttachmentStorageUnavailableError,
    ChatAttachmentStorage,
    build_object_name,
    generate_attachment_id,
)
from backend.attachments.validation import SUPPORTED_MIME_TYPES, InvalidImageError, normalize_filename, validate_image_bytes
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


async def delete_ready_attachment(
    *,
    session_service: ApiSessionService,
    attachment_service: AttachmentService,
    storage: ChatAttachmentStorage,
    user_id: str,
    session_id: str,
    attachment_id: str,
) -> None:
    """POST-5.1 B7 -- closes the B3-documented orphan gap: an uploaded
    image REMOVED from the draft before send (`READY`, `message_id IS
    NULL`) previously had no way to ever be deleted. This is the one,
    narrow entry point for that case -- session ownership is verified
    first (instruction: "lifecycle decisions must be ownership/session
    scoped"), mirroring `upload_attachment`'s own first step.

    DELIBERATELY NARROWER than the domain layer's own `AttachmentService
    .mark_deleted` (which already supports `READY|LINKED -> DELETED` --
    see that method's own docstring): this function only ever permits
    `READY -> DELETED`. A `LINKED` attachment belongs to a durable, sent
    conversation turn and must NEVER be deleted merely because it
    disappeared from a draft -- rejecting it here, before `mark_deleted`
    is ever called, is what makes "never delete a LINKED attachment"
    a structural guarantee of THIS call site, not a hope that `mark_
    deleted`'s own broader capability is never misused by a future
    caller.

    IDEMPOTENT for an already-`DELETED` id (a caller who already owns
    this attachment learns nothing new from a second delete call
    succeeding harmlessly -- instruction: "repeated cleanup/delete should
    be safe/idempotent").

    ORDER: Cloud SQL transitions to `DELETED` FIRST, GCS deletion is
    best-effort SECOND -- the inverse of `upload_attachment`'s own order,
    for the same underlying reason: the Cloud SQL row's status is the
    authoritative claim about whether the binary exists, so that claim
    must never be retracted a moment before the deletion actually starts,
    nor left standing after the binary is gone. A GCS-side failure after
    the DB transition already succeeded is logged, never raised -- the
    attachment is correctly gone from every path that matters
    (`get_attachment_content`/`prepare_attachments_for_turn` already treat
    `DELETED` as not-found unconditionally); a rare orphaned GCS object
    left behind is a harmless, unreachable byte range, not a correctness
    or security issue.
    """
    await session_service.get_session(session_id, user_id)  # 404 before ever taking any other action

    try:
        record = await attachment_service.get_owned(attachment_id, user_id)
    except AttachmentNotFoundError as exc:
        raise not_found("No such attachment was found.") from exc
    if record.session_id != session_id:
        # Same anti-enumeration discipline as prepare_attachments_for_turn:
        # a real attachment owned by this user but uploaded against a
        # DIFFERENT session must look identical to "no such attachment".
        raise not_found("No such attachment was found.")

    if record.status == ChatAttachmentStatus.DELETED.value:
        return  # idempotent no-op -- already exactly the state the caller wants

    if record.status == ChatAttachmentStatus.LINKED.value:
        raise validation_error(
            "This attachment is part of a sent message and cannot be removed this way."
        )

    # status is READY here -- the only state this endpoint may ever delete.
    try:
        await attachment_service.mark_deleted(attachment_id, user_id, session_id)
    except InvalidAttachmentTransitionError:
        # Lost a race with a concurrent transition (e.g. a send that
        # linked this same attachment between the read above and this
        # call). Fail safely rather than deleting something that may now
        # be part of a durable message -- the caller can simply retry,
        # which will correctly see LINKED (rejected) or DELETED
        # (idempotent no-op) on the next attempt.
        raise validation_error(
            "This attachment could not be removed -- it may have just been used in a message."
        ) from None

    if storage.is_configured:
        try:
            await run_in_threadpool(storage.delete, record.storage_object_name)
        except Exception:
            logger.exception(
                "best-effort GCS cleanup after attachment deletion failed: attachment_id=%s", attachment_id
            )


# --- Multimodal turn preparation (POST-5.1 B5) ------------------------------


@dataclass(frozen=True)
class PreparedAttachment:
    """TRUSTED, backend-internal input to model construction ONLY -- never
    an API response shape (see `schemas.AttachmentHistoryDTO`/
    `AttachmentResponse` for the frontend-safe views; those two never
    carry `gcs_uri`, and this type is never serialized into either).

    No `filename` -- the model doesn't need it, and keeping it out avoids
    ever being tempted to build a URI or log line from it. No
    `owner_user_id` -- ownership was already verified to construct this;
    it has no further use once validation has passed.
    """

    attachment_id: str
    mime_type: str
    size_bytes: int
    gcs_uri: str


async def prepare_attachments_for_turn(
    *,
    attachment_service: AttachmentService,
    storage: ChatAttachmentStorage,
    settings: Settings,
    user_id: str,
    session_id: str,
    attachment_ids: list[str],
) -> list[PreparedAttachment]:
    """POST-5.1 B5 -- validates every `attachment_id` a real multimodal
    send supplied, BEFORE the Runner/Gemini ever sees any of them
    (instruction section 12). Never trusts frontend upload state --
    re-derives everything from the authoritative Cloud SQL record.

    Validates, in order:
      1. no duplicate ids (instruction section 8) -- structural, a plain
         set-size comparison, never string matching.
      2. count <= `settings.chat_attachment_max_images_per_turn`.
      3. for each id: exists AND belongs to (`user_id`, `session_id`) --
         unknown id and foreign-owner/foreign-session id are
         DELIBERATELY indistinguishable (`not_found`, same anti-
         enumeration discipline as every other attachment read path in
         this codebase; never "that belongs to someone else").
      4. `DELETED` -> treated identically to not-found (matches
         `_attachment_response_status`'s existing convention elsewhere in
         this module).
      5. `LINKED` -> a distinct, safe-to-reveal `validation_error` (the
         caller genuinely owns this attachment, so confirming it was
         already used leaks nothing) -- instruction section 15: no
         attachment replay/rebinding onto a different turn.
      6. MIME must still be one of `SUPPORTED_MIME_TYPES` -- re-checked
         here, never assumed from upload-time validation alone.
      7. combined `size_bytes` <= `settings.chat_attachment_max_total_bytes_per_turn`.

    Returns `PreparedAttachment`s in the EXACT order `attachment_ids` was
    given (client/draft order) -- never database/dict iteration order
    (instruction section 18/34) -- each carrying the internal `gs://` URI
    Gemini/ADK will receive. An empty `attachment_ids` list returns `[]`
    immediately, no lookups.
    """
    if not attachment_ids:
        return []

    if len(set(attachment_ids)) != len(attachment_ids):
        raise validation_error("A message cannot reference the same attachment twice.")

    if len(attachment_ids) > settings.chat_attachment_max_images_per_turn:
        raise validation_error(
            f"A message may include at most {settings.chat_attachment_max_images_per_turn} images."
        )

    records_by_id: dict[str, ChatAttachmentRecord] = {}
    for attachment_id in attachment_ids:
        try:
            record = await attachment_service.get_owned(attachment_id, user_id)
        except AttachmentNotFoundError as exc:
            raise not_found("No such attachment was found.") from exc
        if record.session_id != session_id:
            # Same anti-enumeration discipline: a real attachment owned by
            # this user but uploaded against a DIFFERENT session must look
            # identical to "no such attachment" -- never confirm its
            # existence under a session the caller didn't upload it to.
            raise not_found("No such attachment was found.")
        if record.status == ChatAttachmentStatus.DELETED.value:
            raise not_found("No such attachment was found.")
        if record.status == ChatAttachmentStatus.LINKED.value:
            raise validation_error("This attachment has already been used in another message.")
        # status is READY here -- the only state a NEW send may reference
        # (instruction section 15).
        if record.mime_type not in SUPPORTED_MIME_TYPES:
            raise unsupported_media_type(f"Unsupported attachment type: {record.mime_type!r}.")
        records_by_id[attachment_id] = record

    total_bytes = sum(record.size_bytes for record in records_by_id.values())
    if total_bytes > settings.chat_attachment_max_total_bytes_per_turn:
        raise payload_too_large(
            "The combined size of the attached images exceeds the "
            f"{settings.chat_attachment_max_total_bytes_per_turn} byte limit."
        )

    try:
        return [
            PreparedAttachment(
                attachment_id=attachment_id,
                mime_type=records_by_id[attachment_id].mime_type,
                size_bytes=records_by_id[attachment_id].size_bytes,
                gcs_uri=storage.uri_for(records_by_id[attachment_id].storage_object_name),
            )
            for attachment_id in attachment_ids  # preserves CLIENT order, not dict/DB order
        ]
    except AttachmentStorageUnavailableError:
        # Every referenced attachment is a genuinely READY row (a READY
        # row can only exist if the bucket was configured at upload
        # time -- see B2's own `upload_attachment` gate), so this should
        # never fire in practice; defensive only, against configuration
        # changing between upload and send.
        raise connector_unavailable("Attachment storage is not currently available.") from None


async def resolve_continuation_images(
    *,
    attachment_service: AttachmentService,
    storage: ChatAttachmentStorage,
    user_id: str,
    session_id: str,
    attachment_ids: list[str],
) -> list[PreparedAttachment]:
    """POST-5.1 B6 -- re-resolves the `attachment_id`s a `ResolvedRead
    Continuation` carries (instruction section 30) for a resumed Incident
    Manager read, AFTER a `SelectionCard` ambiguity is chosen. Deliberately
    a SEPARATE function from `prepare_attachments_for_turn` above, never a
    parameterized variant of it -- these are two structurally different
    operations with opposite status requirements:

      NEW SEND (`prepare_attachments_for_turn`): the ONLY acceptable
      status is READY -- a brand-new message may reference an
      attachment exactly once, before it has ever been used.

      CONTINUATION READ (this function): the ONLY acceptable status is
      LINKED -- these ids came from a PRIOR turn's own already-completed,
      already-B5-validated send (the turn that produced `selection_
      needed`); by the time a continuation resumes, they are `LINKED` to
      that earlier turn's own message, never still `READY` (which would
      mean they were never actually sent) and never `DELETED`.

    Every failure mode raises (never silently drops the image and falls
    back to Teams/KM-only reasoning -- instruction section 32): unknown id,
    foreign owner, foreign/different session, wrong status (`READY` or
    `DELETED`), and unsupported MIME are all treated exactly like `prepare_
    attachments_for_turn`'s own anti-enumeration discipline -- unknown/
    foreign-owner/foreign-session/wrong-status collapse to the SAME safe
    `not_found`, since a caller who does not already, legitimately own this
    id learns nothing new from the distinction. The caller (`read_
    continuation_execution.py`) MUST treat any exception here as "this
    continuation cannot be resumed at all" -- a full turn failure, never a
    partial one that silently proceeds without the image (instruction:
    "Do not silently continue with Teams-only reasoning if the original
    operational request required the image").

    An empty `attachment_ids` returns `[]` immediately, no lookups --
    identical shape to `prepare_attachments_for_turn`'s own early return,
    for the common case of a continuation that never carried any images.
    """
    if not attachment_ids:
        return []

    records_by_id: dict[str, ChatAttachmentRecord] = {}
    for attachment_id in attachment_ids:
        try:
            record = await attachment_service.get_owned(attachment_id, user_id)
        except AttachmentNotFoundError as exc:
            raise not_found("No such attachment was found.") from exc
        if record.session_id != session_id:
            raise not_found("No such attachment was found.")
        if record.status != ChatAttachmentStatus.LINKED.value:
            # Covers both a never-sent READY row (should be structurally
            # unreachable -- a PendingReadIntent's own attachment_ids are
            # only ever populated from a turn that just passed B5's own
            # READY-only new-send validation, and that same turn's normal
            # linkage runs before this continuation could ever be chosen
            # -- but never assumed) and a since-DELETED row. Neither may
            # ever silently resolve here.
            raise not_found("No such attachment was found.")
        if record.mime_type not in SUPPORTED_MIME_TYPES:
            raise unsupported_media_type(f"Unsupported attachment type: {record.mime_type!r}.")
        records_by_id[attachment_id] = record

    try:
        return [
            PreparedAttachment(
                attachment_id=attachment_id,
                mime_type=records_by_id[attachment_id].mime_type,
                size_bytes=records_by_id[attachment_id].size_bytes,
                gcs_uri=storage.uri_for(records_by_id[attachment_id].storage_object_name),
            )
            for attachment_id in attachment_ids  # preserves the continuation's own order
        ]
    except AttachmentStorageUnavailableError:
        raise connector_unavailable("Attachment storage is not currently available.") from None
