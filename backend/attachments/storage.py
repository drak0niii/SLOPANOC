"""Private GCS storage abstraction for durable chat attachment binaries
(POST-5.1 B1).

SCOPE: only what B1 genuinely needs -- put/get/delete, plus the opaque
object-key naming contract. This deliberately does NOT wrap the entire
`google-cloud-storage` SDK behind a generic interface; B2 (the real
upload/retrieve HTTP API) is the next consumer and can extend this file
directly rather than needing a speculative abstraction layer built now.

OBJECT KEY STRATEGY (B0 instruction section 8, evaluated): the key
contains ONLY opaque, server-generated identifiers --

    chat-attachments/<session_id>/<attachment_id>

`session_id` is already an opaque, server-generated UUID4 (see
`backend/api/session_service.py`'s `ApiSessionService.create_session` --
`session_id = str(uuid.uuid4())`, never client-supplied), and
`attachment_id` is generated the same way by this package. Deliberately
OMITS `owner_user_id` from the physical path -- today's dev-only identity
(`backend/api/identity.py`) is already just an opaque string, but a future
real identity provider could plausibly resolve to an email address; since
ownership is already fully enforced in SQL/at the service boundary (never
by the object path itself), there is no reason to ever risk leaking a
principal identifier into a GCS object key. `original_filename` never
appears in the key either -- it is metadata only (`ChatAttachmentRecord
.original_filename`), never authoritative for storage addressing.

BINARY NEVER IN POSTGRES: this module is the ONLY place chat-attachment
bytes are read/written. `backend/attachments/repository.py`/`service.py`
never see raw bytes, only `storage_object_name` references.
"""
from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Optional

from backend.config.settings import get_settings

_OBJECT_KEY_PREFIX = "chat-attachments"


class AttachmentStorageUnavailableError(RuntimeError):
    """Raised when a storage operation is attempted but no bucket is
    configured (`SLOPANOC_CHAT_ATTACHMENTS_BUCKET` unset) -- B2+
    functionality, not yet wired into any live request path in B1. Never
    raised merely because the setting exists but the bucket is
    unreachable -- that surfaces as whatever the underlying GCS client
    itself raises.
    """


def build_object_name(session_id: str, attachment_id: str) -> str:
    """The opaque, server-generated object key for one attachment.

    Never accepts or embeds `original_filename`, `owner_user_id`, or any
    other user-entered/potentially-identifying value -- see this module's
    own docstring for the full rationale.
    """
    return f"{_OBJECT_KEY_PREFIX}/{session_id}/{attachment_id}"


def generate_attachment_id() -> str:
    """Server-generated opaque attachment identity -- never accepted from
    a caller, mirroring `ApiSessionService.create_session`'s own
    `str(uuid.uuid4())` pattern.
    """
    return str(uuid.uuid4())


class ChatAttachmentStorage:
    """Thin wrapper over the `google-cloud-storage` client, scoped to the
    single configured chat-attachments bucket. Constructed with an
    explicit bucket name (tests always do this); the process-wide
    singleton below resolves it from `Settings`.
    """

    def __init__(self, bucket_name: Optional[str]) -> None:
        self._bucket_name = bucket_name
        self._client = None  # lazy -- never constructed if never used.

    @property
    def is_configured(self) -> bool:
        return bool(self._bucket_name)

    def _require_bucket(self):
        if not self._bucket_name:
            raise AttachmentStorageUnavailableError(
                "SLOPANOC_CHAT_ATTACHMENTS_BUCKET is not configured -- chat "
                "attachment storage is unavailable."
            )
        if self._client is None:
            from google.cloud import storage  # noqa: PLC0415 -- see module docstring: lazy, only when actually used.

            self._client = storage.Client()
        return self._client.bucket(self._bucket_name)

    def put_bytes(self, object_name: str, data: bytes, content_type: str) -> None:
        """Uploads `data` to `object_name`. Synchronous (the underlying
        `google-cloud-storage` client is sync) -- B2's async HTTP handler
        is expected to run this via a thread-pool offload, exactly like
        this codebase's existing `run_in_threadpool` use in
        `backend/api/execution_service.py`. Not addressed further here --
        B1 has no live request path calling this yet.
        """
        bucket = self._require_bucket()
        blob = bucket.blob(object_name)
        blob.upload_from_string(data, content_type=content_type)

    def get_bytes(self, object_name: str) -> bytes:
        bucket = self._require_bucket()
        blob = bucket.blob(object_name)
        return blob.download_as_bytes()

    def delete(self, object_name: str) -> None:
        bucket = self._require_bucket()
        blob = bucket.blob(object_name)
        blob.delete()

    def exists(self, object_name: str) -> bool:
        bucket = self._require_bucket()
        return bucket.blob(object_name).exists()


@lru_cache(maxsize=1)
def get_attachment_storage() -> ChatAttachmentStorage:
    """Process-wide singleton, mirroring `get_case_database()`/
    `get_knowledge_repository()`'s pattern. Safe to construct even when
    `SLOPANOC_CHAT_ATTACHMENTS_BUCKET` is unset -- the underlying GCS
    client is never built until a real operation is attempted
    (`is_configured` lets a caller check first without triggering
    `AttachmentStorageUnavailableError`).
    """
    return ChatAttachmentStorage(get_settings().chat_attachments_bucket)
