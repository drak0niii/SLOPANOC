"""POST-5.1 B2: `backend.api.attachment_service` orchestration --
session authorization, write ordering (GCS before Cloud SQL), rollback
on DB failure, size boundary enforcement, hash correctness, and the
READY-not-LINKED post-upload state. Uses a real in-memory
`AttachmentService`/`AttachmentRepository` (SQLite) and a real
`ApiSessionService` (in-memory ADK sessions, this backend's own standing
test convention), but a FAKE storage double -- no real GCS anywhere in
this file.
"""
from __future__ import annotations

import hashlib
import io

import pytest
import pytest_asyncio
from PIL import Image

from backend.api.attachment_service import (
    get_attachment_content,
    get_attachment_metadata,
    upload_attachment,
)
from backend.api.session_service import ApiSessionService
from backend.attachments.models import ChatAttachmentStatus
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentNotFoundError, AttachmentService
from backend.attachments.storage import AttachmentStorageUnavailableError
from backend.config.settings import Settings
from backend.gateway.safe_error import SafeErrorException


def _png_bytes(size: tuple[int, int] = (4, 4)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color=(1, 2, 3)).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeUploadFile:
    def __init__(self, data: bytes, filename: str = "screenshot.png", content_type: str = "image/png") -> None:
        self._data = data
        self._pos = 0
        self.filename = filename
        self.content_type = content_type

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk


class _FakeAttachmentStorage:
    def __init__(self, configured: bool = True, fail_put: bool = False, fail_get: bool = False) -> None:
        self._configured = configured
        self._fail_put = fail_put
        self._fail_get = fail_get
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.put_calls: list[str] = []
        self.delete_calls: list[str] = []

    @property
    def is_configured(self) -> bool:
        return self._configured

    def put_bytes(self, object_name: str, data: bytes, content_type: str) -> None:
        self.put_calls.append(object_name)
        if self._fail_put:
            raise RuntimeError("simulated GCS put failure")
        self.objects[object_name] = (data, content_type)

    def get_bytes(self, object_name: str) -> bytes:
        if self._fail_get or object_name not in self.objects:
            raise AttachmentStorageUnavailableError("simulated GCS get failure")
        return self.objects[object_name][0]

    def delete(self, object_name: str) -> None:
        self.delete_calls.append(object_name)
        self.objects.pop(object_name, None)

    def exists(self, object_name: str) -> bool:
        return object_name in self.objects


@pytest.fixture
def settings() -> Settings:
    return Settings(env={})  # default 8 MiB limit


@pytest_asyncio.fixture
async def session_service():
    service = ApiSessionService()
    yield service


@pytest_asyncio.fixture
async def attachment_service():
    repository = AttachmentRepository("sqlite+aiosqlite:///:memory:")
    svc = AttachmentService(repository)
    yield svc
    await repository.close()


async def _real_session(session_service: ApiSessionService, user_id: str = "alice") -> str:
    return await session_service.create_session(user_id=user_id)


# --- happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_successful_upload_creates_ready_unlinked_attachment(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    data = _png_bytes()

    record = await upload_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=storage,
        settings=settings,
        user_id="alice",
        session_id=session_id,
        upload_file=_FakeUploadFile(data),
    )

    assert record.status == ChatAttachmentStatus.READY.value
    assert record.message_id is None
    assert record.linked_at is None
    assert record.owner_user_id == "alice"
    assert record.session_id == session_id
    assert record.size_bytes == len(data)
    assert record.mime_type == "image/png"
    assert record.sha256 == hashlib.sha256(data).hexdigest()


@pytest.mark.asyncio
async def test_gcs_object_written_with_correct_bytes(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    data = _png_bytes()

    record = await upload_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=storage,
        settings=settings,
        user_id="alice",
        session_id=session_id,
        upload_file=_FakeUploadFile(data),
    )

    from backend.attachments.storage import build_object_name

    object_name = build_object_name(session_id, record.attachment_id)
    assert object_name == record.storage_object_name
    stored_bytes, stored_content_type = storage.objects[object_name]
    assert stored_bytes == data
    assert stored_content_type == "image/png"


# --- session authorization ------------------------------------------------


@pytest.mark.asyncio
async def test_upload_fails_for_unknown_session(session_service, attachment_service, settings) -> None:
    storage = _FakeAttachmentStorage()
    with pytest.raises(SafeErrorException):
        await upload_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id="alice",
            session_id="does-not-exist",
            upload_file=_FakeUploadFile(_png_bytes()),
        )
    assert storage.put_calls == []


@pytest.mark.asyncio
async def test_upload_fails_for_wrong_user_session(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service, user_id="alice")
    storage = _FakeAttachmentStorage()

    with pytest.raises(SafeErrorException):
        await upload_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id="mallory",
            session_id=session_id,
            upload_file=_FakeUploadFile(_png_bytes()),
        )
    assert storage.put_calls == []


# --- ordering / validation-before-storage ---------------------------------


@pytest.mark.asyncio
async def test_gcs_not_called_when_image_invalid(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()

    with pytest.raises(SafeErrorException):
        await upload_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id="alice",
            session_id=session_id,
            upload_file=_FakeUploadFile(b"not an image", filename="fake.png", content_type="image/png"),
        )

    assert storage.put_calls == []


@pytest.mark.asyncio
async def test_db_row_not_created_when_gcs_put_fails(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage(fail_put=True)

    with pytest.raises(SafeErrorException):
        await upload_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id="alice",
            session_id=session_id,
            upload_file=_FakeUploadFile(_png_bytes()),
        )

    remaining = await attachment_service.get_for_owner_session("alice", session_id)
    assert remaining == []


@pytest.mark.asyncio
async def test_gcs_cleanup_when_db_insert_fails(session_service, attachment_service, settings, monkeypatch) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()

    async def _broken_add(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(attachment_service._repository, "add", _broken_add)

    with pytest.raises(SafeErrorException):
        await upload_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id="alice",
            session_id=session_id,
            upload_file=_FakeUploadFile(_png_bytes()),
        )

    assert len(storage.put_calls) == 1
    assert len(storage.delete_calls) == 1
    assert storage.put_calls[0] == storage.delete_calls[0]
    assert storage.objects == {}


# --- size boundary --------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_within_size_limit_succeeds(session_service, attachment_service) -> None:
    settings = Settings(env={"SLOPANOC_CHAT_ATTACHMENT_MAX_BYTES": "1000000"})
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    data = _png_bytes()
    assert len(data) < 1000000

    record = await upload_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=storage,
        settings=settings,
        user_id="alice",
        session_id=session_id,
        upload_file=_FakeUploadFile(data),
    )
    assert record.status == ChatAttachmentStatus.READY.value


@pytest.mark.asyncio
async def test_upload_exceeding_size_limit_is_rejected(session_service, attachment_service) -> None:
    data = _png_bytes((64, 64))
    settings = Settings(env={"SLOPANOC_CHAT_ATTACHMENT_MAX_BYTES": str(len(data) - 1)})
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()

    with pytest.raises(SafeErrorException) as exc_info:
        await upload_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id="alice",
            session_id=session_id,
            upload_file=_FakeUploadFile(data),
        )
    assert exc_info.value.safe_error.error_code == "payload_too_large"
    assert storage.put_calls == []


# --- storage unavailable ---------------------------------------------------


@pytest.mark.asyncio
async def test_upload_fails_clearly_when_storage_unconfigured(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage(configured=False)

    with pytest.raises(SafeErrorException) as exc_info:
        await upload_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id="alice",
            session_id=session_id,
            upload_file=_FakeUploadFile(_png_bytes()),
        )
    assert exc_info.value.safe_error.error_code == "connector_unavailable"


# --- retrieval / authorization ---------------------------------------------


@pytest.mark.asyncio
async def test_get_attachment_metadata_succeeds_for_owner(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    record = await upload_attachment(
        session_service=session_service, attachment_service=attachment_service, storage=storage, settings=settings,
        user_id="alice", session_id=session_id, upload_file=_FakeUploadFile(_png_bytes()),
    )

    fetched = await get_attachment_metadata(attachment_service, "alice", record.attachment_id)
    assert fetched.attachment_id == record.attachment_id


@pytest.mark.asyncio
async def test_get_attachment_metadata_denied_for_other_user(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    record = await upload_attachment(
        session_service=session_service, attachment_service=attachment_service, storage=storage, settings=settings,
        user_id="alice", session_id=session_id, upload_file=_FakeUploadFile(_png_bytes()),
    )

    with pytest.raises(SafeErrorException) as exc_info:
        await get_attachment_metadata(attachment_service, "mallory", record.attachment_id)
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_get_attachment_content_byte_identical(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    data = _png_bytes()
    record = await upload_attachment(
        session_service=session_service, attachment_service=attachment_service, storage=storage, settings=settings,
        user_id="alice", session_id=session_id, upload_file=_FakeUploadFile(data),
    )

    content, fetched = await get_attachment_content(attachment_service, storage, "alice", record.attachment_id)
    assert content == data
    assert fetched.attachment_id == record.attachment_id


@pytest.mark.asyncio
async def test_get_attachment_content_denied_for_other_user(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    record = await upload_attachment(
        session_service=session_service, attachment_service=attachment_service, storage=storage, settings=settings,
        user_id="alice", session_id=session_id, upload_file=_FakeUploadFile(_png_bytes()),
    )

    with pytest.raises(SafeErrorException) as exc_info:
        await get_attachment_content(attachment_service, storage, "mallory", record.attachment_id)
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_get_content_fails_deterministically_when_gcs_object_missing(
    session_service, attachment_service, settings
) -> None:
    """DB says READY but the GCS object is unexpectedly gone -- must
    fail closed, never return empty/fabricated content.
    """
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    record = await upload_attachment(
        session_service=session_service, attachment_service=attachment_service, storage=storage, settings=settings,
        user_id="alice", session_id=session_id, upload_file=_FakeUploadFile(_png_bytes()),
    )
    storage.objects.clear()  # simulate the object vanishing out from under us

    with pytest.raises(SafeErrorException) as exc_info:
        await get_attachment_content(attachment_service, storage, "alice", record.attachment_id)
    assert exc_info.value.safe_error.error_code == "internal_error"


@pytest.mark.asyncio
async def test_get_deleted_attachment_denied(session_service, attachment_service, settings) -> None:
    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    record = await upload_attachment(
        session_service=session_service, attachment_service=attachment_service, storage=storage, settings=settings,
        user_id="alice", session_id=session_id, upload_file=_FakeUploadFile(_png_bytes()),
    )
    await attachment_service.mark_deleted(record.attachment_id, "alice", session_id)

    with pytest.raises(SafeErrorException) as exc_info:
        await get_attachment_metadata(attachment_service, "alice", record.attachment_id)
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_get_missing_attachment_denied(attachment_service) -> None:
    with pytest.raises(SafeErrorException) as exc_info:
        await get_attachment_metadata(attachment_service, "alice", "totally-unknown-id")
    assert exc_info.value.safe_error.error_code == "not_found"


# --- no binary in SQL, after a REAL upload flow (extends B1's schema-only
# invariant test in test_attachments_model.py) ------------------------------


@pytest.mark.asyncio
async def test_real_upload_flow_writes_no_binary_into_cloud_sql_row(
    session_service, attachment_service, settings
) -> None:
    """B1 proved the SCHEMA has no binary column. This proves the same
    thing about a row actually produced by the real upload orchestration
    (bounded read -> validate -> hash -> GCS put -> Cloud SQL insert),
    inspecting the raw column values directly -- not just the column
    types -- for the image's own byte content, its base64 encoding, or a
    data-URL prefix.
    """
    import base64

    session_id = await _real_session(session_service)
    storage = _FakeAttachmentStorage()
    data = _png_bytes()
    base64_of_image = base64.b64encode(data).decode()

    record = await upload_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=storage,
        settings=settings,
        user_id="alice",
        session_id=session_id,
        upload_file=_FakeUploadFile(data),
    )

    for column_name in ("attachment_id", "owner_user_id", "session_id", "original_filename", "mime_type",
                        "sha256", "storage_object_name", "status"):
        value = str(getattr(record, column_name))
        assert data not in value.encode("utf-8", errors="ignore")
        assert base64_of_image not in value
        assert "data:image" not in value
        assert b"\x89PNG\r\n\x1a\n" not in value.encode("utf-8", errors="ignore")

    # The GCS object -- not the Cloud SQL row -- is where the real bytes live.
    object_name = record.storage_object_name
    assert storage.objects[object_name][0] == data
