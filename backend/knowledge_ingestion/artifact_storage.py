"""Private GCS storage abstraction for durable Knowledge-artifact
binaries (A5 Layer B).

This is a DELIBERATE, LINE-FOR-LINE-INSPIRED mirror of
`backend/attachments/storage.py`'s pattern (lazy client construction,
opaque object keys, `put_bytes`/`get_bytes`/`delete`/`exists`/`uri_for`,
a `*_require_bucket()` fail-clearly-only-when-invoked guard, a process-
wide `@lru_cache` singleton resolved from `Settings`) -- but it is a
SEPARATE module, importing nothing from `backend.attachments`, because
Knowledge artifacts and chat attachments are different domains (A5
instruction section 27/51): different bucket setting
(`Settings.knowledge_artifacts_bucket`, never
`chat_attachments_bucket`), different object-key prefix
(`knowledge-artifacts/`, never `chat-attachments/`), and -- critically
-- NO lifecycle state machine at all here. Chat attachments have
READY/LINKED/DELETED because that answers "has this image been sent in
a message yet"; a Knowledge artifact's operational authority is decided
entirely by the existing Generic KM lifecycle/governance boundary
(`backend/knowledge/governance/`, CANDIDATE/APPROVED/ARCHIVE) -- this
module has no opinion on that at all, exactly as `ChatAttachmentStorage`
has no opinion on chat-turn semantics.

Lives OUTSIDE `backend/knowledge/` entirely (not even under
`backend/knowledge/ingestion/`): `backend/knowledge/{ingestion,repository}`
must stay free of any concrete cloud/vendor SDK import, enforced
automatically by `test_dependency_boundary.py` -- this module's
`google.cloud.storage` import means it belongs in this separate,
concrete package instead, exactly like `backend/tools/knowledge/` (the
concrete ADK-facing layer) lives outside `backend/knowledge/tools/`
(the generic one).

OBJECT KEY STRATEGY (deliberately different from chat attachments):
content-hash-addressed, not attachment-id-addressed --

    knowledge-artifacts/<sha256[0:2]>/<sha256>

This makes deduplication (A5 instruction section 34, Case C: "different
parent MOPs, same embedded binary -> deduplicate underlying binary,
preserve both parent relationships") a structural property of the key
itself: two artifacts (even from two entirely different root documents)
with identical content share exactly one GCS object, uploaded at most
once (`put_bytes_if_absent` below is idempotent -- a second call for
the same hash is a cheap no-op `exists()` check, never a duplicate
upload or an error). The two-character shard prefix avoids an
unbounded flat prefix, the same practice large object stores commonly
use, and carries no identifying information of its own.

BINARY NEVER IN POSTGRES: this module is the only place Knowledge-
artifact bytes are read/written; the repository/domain layers only ever
see a `storage_ref` (`gs://...`) string.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from backend.config.settings import get_settings

_OBJECT_KEY_PREFIX = "knowledge-artifacts"


class KnowledgeArtifactStorageUnavailableError(RuntimeError):
    """Raised when a storage operation is attempted but no bucket is
    configured (`SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET` unset) -- never
    raised merely because the setting exists but the bucket is
    unreachable, which surfaces as whatever the underlying GCS client
    itself raises.
    """


def build_artifact_object_name(content_hash: str) -> str:
    """The opaque, content-hash-addressed object key for one artifact's
    raw binary. `content_hash` must already be a lowercase hex SHA-256
    digest (`KnowledgeArtifact.content_hash`'s own validated shape) --
    never a filename, artifact_id, or any other value.
    """
    if len(content_hash) < 2:
        raise ValueError("content_hash must be a full SHA-256 hex digest")
    return f"{_OBJECT_KEY_PREFIX}/{content_hash[:2]}/{content_hash}"


class KnowledgeArtifactStorage:
    """Thin wrapper over the `google-cloud-storage` client, scoped to the
    single configured Knowledge-artifacts bucket. Constructed with an
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
            raise KnowledgeArtifactStorageUnavailableError(
                "SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET is not configured -- "
                "Knowledge artifact storage is unavailable."
            )
        if self._client is None:
            from google.cloud import storage  # noqa: PLC0415 -- lazy, only when actually used.

            self._client = storage.Client()
        return self._client.bucket(self._bucket_name)

    def put_bytes_if_absent(self, object_name: str, data: bytes, content_type: str) -> bool:
        """Uploads `data` to `object_name` only if no object already
        exists there. Returns True if an upload happened, False if the
        object was already present (the expected outcome on every
        duplicate-content re-ingestion, per this module's own object-key
        strategy). Synchronous (the underlying `google-cloud-storage`
        client is sync); a caller on the async request path is expected
        to offload via a thread pool, mirroring this codebase's existing
        `run_in_threadpool` use for chat-attachment GCS calls.
        """
        bucket = self._require_bucket()
        blob = bucket.blob(object_name)
        if blob.exists():
            return False
        blob.upload_from_string(data, content_type=content_type)
        return True

    def get_bytes(self, object_name: str) -> bytes:
        bucket = self._require_bucket()
        blob = bucket.blob(object_name)
        return blob.download_as_bytes()

    def exists(self, object_name: str) -> bool:
        bucket = self._require_bucket()
        return bucket.blob(object_name).exists()

    def uri_for(self, object_name: str) -> str:
        """The internal `gs://` URI for one artifact's binary.
        BACKEND-ONLY: never returned in any API DTO, SSE event, log
        line, or frontend state. Cheap and synchronous (no GCS client
        needed just to format a string) -- raises the same
        `KnowledgeArtifactStorageUnavailableError` as every other
        operation here if no bucket is configured.
        """
        if not self._bucket_name:
            raise KnowledgeArtifactStorageUnavailableError(
                "SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET is not configured -- "
                "Knowledge artifact storage is unavailable."
            )
        return f"gs://{self._bucket_name}/{object_name}"


@lru_cache(maxsize=1)
def get_knowledge_artifact_storage() -> KnowledgeArtifactStorage:
    """Process-wide singleton, mirroring `get_attachment_storage()`'s
    pattern. Safe to construct even when
    `SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET` is unset -- the underlying GCS
    client is never built until a real operation is attempted.
    """
    return KnowledgeArtifactStorage(get_settings().knowledge_artifacts_bucket)
