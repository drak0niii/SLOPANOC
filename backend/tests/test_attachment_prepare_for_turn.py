"""POST-5.1 B5: `backend.api.attachment_service.prepare_attachments_for_turn`
-- the full server-side validation gate every `attachment_ids` list passes
through BEFORE the Runner/Gemini ever sees any of them. Uses a real
in-memory `AttachmentService`/`AttachmentRepository` (SQLite) and a real,
bucket-configured `ChatAttachmentStorage` double (never a real GCS call --
`uri_for` only formats a string, see storage.py's own implementation).
"""
from __future__ import annotations

import pytest

from backend.api.attachment_service import PreparedAttachment, prepare_attachments_for_turn
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.attachments.storage import ChatAttachmentStorage
from backend.config.settings import Settings
from backend.gateway.safe_error import SafeErrorException

OWNER = "alice"
SESSION = "session-1"


@pytest.fixture
def attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


@pytest.fixture
def storage() -> ChatAttachmentStorage:
    return ChatAttachmentStorage("test-bucket")


@pytest.fixture
def settings() -> Settings:
    return Settings(env={})  # defaults: 4 images/turn, 16 MiB total


async def _ready(attachment_service: AttachmentService, attachment_id: str, **overrides) -> str:
    record = await attachment_service.create(
        owner_user_id=overrides.pop("owner_user_id", OWNER),
        session_id=overrides.pop("session_id", SESSION),
        original_filename=overrides.pop("original_filename", "screenshot.png"),
        mime_type=overrides.pop("mime_type", "image/png"),
        size_bytes=overrides.pop("size_bytes", 1000),
        sha256="a" * 64,
        attachment_id=attachment_id,
    )
    return record.attachment_id


async def _error_code(coro) -> str:
    try:
        await coro
    except SafeErrorException as exc:
        return exc.safe_error.error_code
    raise AssertionError("expected a SafeErrorException")


@pytest.mark.asyncio
async def test_empty_list_returns_empty_immediately(attachment_service, storage, settings) -> None:
    result = await prepare_attachments_for_turn(
        attachment_service=attachment_service,
        storage=storage,
        settings=settings,
        user_id=OWNER,
        session_id=SESSION,
        attachment_ids=[],
    )
    assert result == []


@pytest.mark.asyncio
async def test_one_ready_attachment_is_prepared(attachment_service, storage, settings) -> None:
    await _ready(attachment_service, "att-1")

    [prepared] = await prepare_attachments_for_turn(
        attachment_service=attachment_service,
        storage=storage,
        settings=settings,
        user_id=OWNER,
        session_id=SESSION,
        attachment_ids=["att-1"],
    )

    assert prepared == PreparedAttachment(
        attachment_id="att-1",
        mime_type="image/png",
        size_bytes=1000,
        gcs_uri="gs://test-bucket/chat-attachments/session-1/att-1",
    )


@pytest.mark.asyncio
async def test_four_ready_attachments_all_prepared_in_client_order(attachment_service, storage, settings) -> None:
    for i in (4, 1, 3, 2):  # created out of order on purpose
        await _ready(attachment_service, f"att-{i}")

    client_order = ["att-2", "att-4", "att-1", "att-3"]
    prepared = await prepare_attachments_for_turn(
        attachment_service=attachment_service,
        storage=storage,
        settings=settings,
        user_id=OWNER,
        session_id=SESSION,
        attachment_ids=client_order,
    )

    assert [p.attachment_id for p in prepared] == client_order


@pytest.mark.asyncio
async def test_duplicate_attachment_ids_rejected(attachment_service, storage, settings) -> None:
    await _ready(attachment_service, "att-1")

    code = await _error_code(
        prepare_attachments_for_turn(
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=OWNER,
            session_id=SESSION,
            attachment_ids=["att-1", "att-1"],
        )
    )
    assert code == "validation_error"


@pytest.mark.asyncio
async def test_too_many_attachments_rejected(attachment_service, storage, settings) -> None:
    for i in range(5):
        await _ready(attachment_service, f"att-{i}")

    code = await _error_code(
        prepare_attachments_for_turn(
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=OWNER,
            session_id=SESSION,
            attachment_ids=[f"att-{i}" for i in range(5)],  # default limit is 4
        )
    )
    assert code == "validation_error"


@pytest.mark.asyncio
async def test_combined_bytes_over_limit_rejected(attachment_service, storage) -> None:
    settings = Settings(env={"SLOPANOC_CHAT_ATTACHMENT_MAX_TOTAL_BYTES_PER_TURN": "1500"})
    await _ready(attachment_service, "att-1", size_bytes=1000)
    await _ready(attachment_service, "att-2", size_bytes=1000)

    code = await _error_code(
        prepare_attachments_for_turn(
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=OWNER,
            session_id=SESSION,
            attachment_ids=["att-1", "att-2"],
        )
    )
    assert code == "payload_too_large"


@pytest.mark.asyncio
async def test_unknown_attachment_rejected_safely(attachment_service, storage, settings) -> None:
    code = await _error_code(
        prepare_attachments_for_turn(
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=OWNER,
            session_id=SESSION,
            attachment_ids=["does-not-exist"],
        )
    )
    assert code == "not_found"


@pytest.mark.asyncio
async def test_foreign_owner_attachment_rejected_identically_to_unknown(attachment_service, storage, settings) -> None:
    await _ready(attachment_service, "att-1", owner_user_id="bob")

    code = await _error_code(
        prepare_attachments_for_turn(
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=OWNER,  # alice, not bob
            session_id=SESSION,
            attachment_ids=["att-1"],
        )
    )
    assert code == "not_found"  # SAME code as unknown -- anti-enumeration


@pytest.mark.asyncio
async def test_wrong_session_attachment_rejected_safely(attachment_service, storage, settings) -> None:
    await _ready(attachment_service, "att-1", session_id="a-different-session")

    code = await _error_code(
        prepare_attachments_for_turn(
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=OWNER,
            session_id=SESSION,  # the attachment belongs to a different session
            attachment_ids=["att-1"],
        )
    )
    assert code == "not_found"


@pytest.mark.asyncio
async def test_linked_attachment_rejected_for_a_new_send(attachment_service, storage, settings) -> None:
    """Instruction section 15: no attachment replay/rebinding."""
    attachment_id = await _ready(attachment_service, "att-1")
    await attachment_service.link_to_message(attachment_id, OWNER, SESSION, "some-prior-turn")

    code = await _error_code(
        prepare_attachments_for_turn(
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=OWNER,
            session_id=SESSION,
            attachment_ids=["att-1"],
        )
    )
    assert code == "validation_error"  # distinct from not_found -- the caller genuinely owns it


@pytest.mark.asyncio
async def test_deleted_attachment_rejected_safely(attachment_service, storage, settings) -> None:
    await _ready(attachment_service, "att-1")
    await attachment_service.mark_deleted("att-1", OWNER, SESSION)

    code = await _error_code(
        prepare_attachments_for_turn(
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=OWNER,
            session_id=SESSION,
            attachment_ids=["att-1"],
        )
    )
    assert code == "not_found"  # same anti-enumeration treatment as unknown/foreign


@pytest.mark.asyncio
async def test_unsupported_mime_rejected(attachment_service, storage, settings) -> None:
    # create() itself doesn't validate MIME (that's upload-time, via
    # validate_image_bytes) -- this proves prepare_attachments_for_turn
    # re-checks it independently, never trusting upload-time validation
    # alone.
    await attachment_service.create(
        owner_user_id=OWNER,
        session_id=SESSION,
        original_filename="doc.pdf",
        mime_type="application/pdf",
        size_bytes=1000,
        sha256="a" * 64,
        attachment_id="att-1",
    )

    code = await _error_code(
        prepare_attachments_for_turn(
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=OWNER,
            session_id=SESSION,
            attachment_ids=["att-1"],
        )
    )
    assert code == "unsupported_media_type"
