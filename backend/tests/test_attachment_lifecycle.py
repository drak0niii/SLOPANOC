"""POST-5.1 B7 -- backend.api.attachment_service.delete_ready_attachment.

Closes the B3-documented orphan gap: a real, session-ownership-scoped
delete for a still-READY (never sent) attachment, with a real
`AttachmentService`/`AttachmentRepository` (in-memory SQLite) and a real,
bucket-configured `ChatAttachmentStorage` double whose GCS calls are
monkeypatched at the `storage.delete`/`storage.exists` level (never a real
network call).
"""
from __future__ import annotations

import pytest

from backend.api.attachment_service import delete_ready_attachment
from backend.api.session_service import ApiSessionService
from backend.attachments.models import ChatAttachmentStatus
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.attachments.storage import ChatAttachmentStorage
from backend.gateway.safe_error import SafeErrorException

OWNER = "alice"
OTHER_USER = "mallory"


@pytest.fixture
def attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


@pytest.fixture
def storage() -> ChatAttachmentStorage:
    return ChatAttachmentStorage("test-bucket")


async def _session(owner: str = OWNER) -> tuple[ApiSessionService, str]:
    service = ApiSessionService()
    session_id = await service.create_session(owner)
    return service, session_id


async def _ready(attachment_service: AttachmentService, session_id: str, owner: str = OWNER, **overrides) -> str:
    record = await attachment_service.create(
        owner_user_id=owner,
        session_id=session_id,
        original_filename=overrides.pop("original_filename", "screenshot.png"),
        mime_type=overrides.pop("mime_type", "image/png"),
        size_bytes=overrides.pop("size_bytes", 1000),
        sha256="a" * 64,
        attachment_id=overrides.pop("attachment_id", None),
    )
    return record.attachment_id


async def _error_code(coro) -> str:
    try:
        await coro
    except SafeErrorException as exc:
        return exc.safe_error.error_code
    raise AssertionError("expected a SafeErrorException")


@pytest.mark.asyncio
async def test_deletes_a_ready_attachment_and_cleans_up_storage(
    attachment_service: AttachmentService, storage: ChatAttachmentStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_service, session_id = await _session()
    attachment_id = await _ready(attachment_service, session_id)

    deleted_object_names: list[str] = []
    monkeypatch.setattr(storage, "delete", lambda object_name: deleted_object_names.append(object_name))

    await delete_ready_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=storage,
        user_id=OWNER,
        session_id=session_id,
        attachment_id=attachment_id,
    )

    record = await attachment_service._repository.get(attachment_id)
    assert record.status == ChatAttachmentStatus.DELETED.value
    assert len(deleted_object_names) == 1


@pytest.mark.asyncio
async def test_repeated_delete_is_idempotent(
    attachment_service: AttachmentService, storage: ChatAttachmentStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_service, session_id = await _session()
    attachment_id = await _ready(attachment_service, session_id)
    monkeypatch.setattr(storage, "delete", lambda object_name: None)

    await delete_ready_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=storage,
        user_id=OWNER,
        session_id=session_id,
        attachment_id=attachment_id,
    )
    # Second call must succeed silently -- never raise, never re-delete.
    await delete_ready_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=storage,
        user_id=OWNER,
        session_id=session_id,
        attachment_id=attachment_id,
    )

    record = await attachment_service._repository.get(attachment_id)
    assert record.status == ChatAttachmentStatus.DELETED.value


@pytest.mark.asyncio
async def test_linked_attachment_is_never_deleted(
    attachment_service: AttachmentService, storage: ChatAttachmentStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_service, session_id = await _session()
    attachment_id = await _ready(attachment_service, session_id)
    await attachment_service.link_to_message(attachment_id, OWNER, session_id, "turn-1")

    delete_calls: list[str] = []
    monkeypatch.setattr(storage, "delete", lambda object_name: delete_calls.append(object_name))

    code = await _error_code(
        delete_ready_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            user_id=OWNER,
            session_id=session_id,
            attachment_id=attachment_id,
        )
    )
    assert code == "validation_error"
    assert delete_calls == []

    record = await attachment_service._repository.get(attachment_id)
    assert record.status == ChatAttachmentStatus.LINKED.value  # untouched


@pytest.mark.asyncio
async def test_foreign_owner_is_rejected_anti_enumeration(
    attachment_service: AttachmentService, storage: ChatAttachmentStorage
) -> None:
    session_service, session_id = await _session()
    attachment_id = await _ready(attachment_service, session_id, owner=OWNER)

    # A foreign user has no session of their own referencing this
    # session_id, so the session-ownership check itself already fails
    # first (mirroring every other session-scoped route in this codebase).
    code = await _error_code(
        delete_ready_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            user_id=OTHER_USER,
            session_id=session_id,
            attachment_id=attachment_id,
        )
    )
    assert code == "not_found"

    record = await attachment_service._repository.get(attachment_id)
    assert record.status == ChatAttachmentStatus.READY.value  # untouched


@pytest.mark.asyncio
async def test_wrong_session_is_rejected_anti_enumeration(
    attachment_service: AttachmentService, storage: ChatAttachmentStorage
) -> None:
    session_service, session_id_a = await _session()
    _, session_id_b = await _session()
    attachment_id = await _ready(attachment_service, session_id_a)

    code = await _error_code(
        delete_ready_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            user_id=OWNER,
            session_id=session_id_b,
            attachment_id=attachment_id,
        )
    )
    assert code == "not_found"

    record = await attachment_service._repository.get(attachment_id)
    assert record.status == ChatAttachmentStatus.READY.value  # untouched


@pytest.mark.asyncio
async def test_unknown_attachment_id_is_rejected(
    attachment_service: AttachmentService, storage: ChatAttachmentStorage
) -> None:
    session_service, session_id = await _session()

    code = await _error_code(
        delete_ready_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            user_id=OWNER,
            session_id=session_id,
            attachment_id="does-not-exist",
        )
    )
    assert code == "not_found"


@pytest.mark.asyncio
async def test_missing_gcs_object_does_not_fail_the_delete(
    attachment_service: AttachmentService, storage: ChatAttachmentStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Cloud SQL transition is the authoritative outcome -- a GCS-side
    "already gone"/network failure during best-effort cleanup must never
    surface as a failed delete (instruction: "missing GCS object" /
    "DB/storage failure behavior" both fail safely, never block the DB
    transition that already succeeded).
    """
    session_service, session_id = await _session()
    attachment_id = await _ready(attachment_service, session_id)

    def _raise_not_found(object_name: str) -> None:
        raise RuntimeError("simulated: object does not exist in GCS")

    monkeypatch.setattr(storage, "delete", _raise_not_found)

    await delete_ready_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=storage,
        user_id=OWNER,
        session_id=session_id,
        attachment_id=attachment_id,
    )

    record = await attachment_service._repository.get(attachment_id)
    assert record.status == ChatAttachmentStatus.DELETED.value  # DB side still succeeded


@pytest.mark.asyncio
async def test_deleted_attachment_never_reaches_storage_delete_again(
    attachment_service: AttachmentService, storage: ChatAttachmentStorage, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_service, session_id = await _session()
    attachment_id = await _ready(attachment_service, session_id)
    await attachment_service.mark_deleted(attachment_id, OWNER, session_id)

    delete_calls: list[str] = []
    monkeypatch.setattr(storage, "delete", lambda object_name: delete_calls.append(object_name))

    await delete_ready_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=storage,
        user_id=OWNER,
        session_id=session_id,
        attachment_id=attachment_id,
    )
    assert delete_calls == []  # idempotent no-op path never touches storage again


@pytest.mark.asyncio
async def test_unconfigured_storage_still_completes_the_db_transition(
    attachment_service: AttachmentService,
) -> None:
    session_service, session_id = await _session()
    attachment_id = await _ready(attachment_service, session_id)
    unconfigured_storage = ChatAttachmentStorage(None)

    await delete_ready_attachment(
        session_service=session_service,
        attachment_service=attachment_service,
        storage=unconfigured_storage,
        user_id=OWNER,
        session_id=session_id,
        attachment_id=attachment_id,
    )

    record = await attachment_service._repository.get(attachment_id)
    assert record.status == ChatAttachmentStatus.DELETED.value
