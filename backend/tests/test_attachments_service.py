"""POST-5.1 B1: `AttachmentService` domain invariants -- ownership
re-verification (anti-enumeration), and the legal lifecycle transitions.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import (
    AttachmentNotFoundError,
    AttachmentService,
    InvalidAttachmentTransitionError,
)


@pytest_asyncio.fixture
async def service():
    repository = AttachmentRepository("sqlite+aiosqlite:///:memory:")
    svc = AttachmentService(repository)
    yield svc
    await repository.close()


@pytest.mark.asyncio
async def test_create_registers_a_ready_attachment(service: AttachmentService) -> None:
    from backend.attachments.models import ChatAttachmentStatus

    record = await service.create(
        owner_user_id="alice",
        session_id="s1",
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=1024,
        sha256="c" * 64,
    )

    assert record.status == ChatAttachmentStatus.READY.value
    assert record.storage_object_name == f"chat-attachments/s1/{record.attachment_id}"
    assert record.owner_user_id == "alice"
    assert record.session_id == "s1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [
        dict(original_filename=""),
        dict(mime_type=""),
        dict(size_bytes=0),
        dict(size_bytes=-1),
        dict(sha256=""),
    ],
)
async def test_create_rejects_invalid_fields(service: AttachmentService, kwargs: dict) -> None:
    base = dict(
        owner_user_id="alice",
        session_id="s1",
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=1024,
        sha256="d" * 64,
    )
    base.update(kwargs)
    with pytest.raises(ValueError):
        await service.create(**base)


@pytest.mark.asyncio
async def test_link_to_message_succeeds_for_owner(service: AttachmentService) -> None:
    from backend.attachments.models import ChatAttachmentStatus

    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="e" * 64,
    )

    linked = await service.link_to_message(record.attachment_id, "alice", "s1", "msg-1")

    assert linked.status == ChatAttachmentStatus.LINKED.value
    assert linked.message_id == "msg-1"


@pytest.mark.asyncio
async def test_link_to_message_rejects_wrong_owner(service: AttachmentService) -> None:
    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="f" * 64,
    )

    with pytest.raises(AttachmentNotFoundError):
        await service.link_to_message(record.attachment_id, "mallory", "s1", "msg-1")


@pytest.mark.asyncio
async def test_link_to_message_rejects_wrong_session(service: AttachmentService) -> None:
    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="1" * 64,
    )

    with pytest.raises(AttachmentNotFoundError):
        await service.link_to_message(record.attachment_id, "alice", "s2", "msg-1")


@pytest.mark.asyncio
async def test_link_to_message_rejects_unknown_attachment(service: AttachmentService) -> None:
    with pytest.raises(AttachmentNotFoundError):
        await service.link_to_message("does-not-exist", "alice", "s1", "msg-1")


@pytest.mark.asyncio
async def test_unknown_attachment_and_wrong_owner_raise_the_same_error_type(service: AttachmentService) -> None:
    """Anti-enumeration: a caller cannot distinguish "doesn't exist" from
    "exists but isn't yours" (module docstring).
    """
    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="2" * 64,
    )

    with pytest.raises(AttachmentNotFoundError) as exc_unknown:
        await service.link_to_message("totally-unknown-id", "alice", "s1", "msg-1")
    with pytest.raises(AttachmentNotFoundError) as exc_wrong_owner:
        await service.link_to_message(record.attachment_id, "mallory", "s1", "msg-1")

    assert str(exc_unknown.value) == str(exc_wrong_owner.value)


@pytest.mark.asyncio
async def test_link_to_message_rejects_already_linked(service: AttachmentService) -> None:
    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="3" * 64,
    )
    await service.link_to_message(record.attachment_id, "alice", "s1", "msg-1")

    with pytest.raises(InvalidAttachmentTransitionError):
        await service.link_to_message(record.attachment_id, "alice", "s1", "msg-2")


@pytest.mark.asyncio
async def test_link_to_message_rejects_deleted(service: AttachmentService) -> None:
    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="4" * 64,
    )
    await service.mark_deleted(record.attachment_id, "alice", "s1")

    with pytest.raises(InvalidAttachmentTransitionError):
        await service.link_to_message(record.attachment_id, "alice", "s1", "msg-1")


@pytest.mark.asyncio
async def test_mark_deleted_succeeds_for_owner_from_ready(service: AttachmentService) -> None:
    from backend.attachments.models import ChatAttachmentStatus

    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="5" * 64,
    )

    deleted = await service.mark_deleted(record.attachment_id, "alice", "s1")

    assert deleted.status == ChatAttachmentStatus.DELETED.value
    assert deleted.deleted_at is not None


@pytest.mark.asyncio
async def test_mark_deleted_succeeds_from_linked(service: AttachmentService) -> None:
    from backend.attachments.models import ChatAttachmentStatus

    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="6" * 64,
    )
    await service.link_to_message(record.attachment_id, "alice", "s1", "msg-1")

    deleted = await service.mark_deleted(record.attachment_id, "alice", "s1")

    assert deleted.status == ChatAttachmentStatus.DELETED.value


@pytest.mark.asyncio
async def test_mark_deleted_rejects_wrong_owner(service: AttachmentService) -> None:
    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="7" * 64,
    )

    with pytest.raises(AttachmentNotFoundError):
        await service.mark_deleted(record.attachment_id, "mallory", "s1")


@pytest.mark.asyncio
async def test_mark_deleted_rejects_already_deleted(service: AttachmentService) -> None:
    record = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="8" * 64,
    )
    await service.mark_deleted(record.attachment_id, "alice", "s1")

    with pytest.raises(InvalidAttachmentTransitionError):
        await service.mark_deleted(record.attachment_id, "alice", "s1")


@pytest.mark.asyncio
async def test_get_for_owner_session_and_list_for_message(service: AttachmentService) -> None:
    r1 = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="a.png", mime_type="image/png",
        size_bytes=10, sha256="9" * 64,
    )
    r2 = await service.create(
        owner_user_id="alice", session_id="s1", original_filename="b.png", mime_type="image/png",
        size_bytes=20, sha256="0" * 64,
    )
    await service.link_to_message(r1.attachment_id, "alice", "s1", "msg-1")
    await service.link_to_message(r2.attachment_id, "alice", "s1", "msg-1")

    owned = await service.get_for_owner_session("alice", "s1")
    assert {r.attachment_id for r in owned} == {r1.attachment_id, r2.attachment_id}

    for_message = await service.list_for_message("s1", "msg-1")
    assert {r.attachment_id for r in for_message} == {r1.attachment_id, r2.attachment_id}
