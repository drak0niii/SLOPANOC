"""POST-5.1 B1: `AttachmentRepository` CRUD, atomic lifecycle
transitions, and ownership/session-scoped queries.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from backend.attachments.models import ChatAttachmentRecord, ChatAttachmentStatus
from backend.attachments.repository import AttachmentRepository


def _record(attachment_id: str, owner_user_id: str = "user-1", session_id: str = "session-1", **overrides) -> ChatAttachmentRecord:
    defaults = dict(
        attachment_id=attachment_id,
        owner_user_id=owner_user_id,
        session_id=session_id,
        message_id=None,
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=2048,
        sha256="b" * 64,
        storage_object_name=f"chat-attachments/{session_id}/{attachment_id}",
        status=ChatAttachmentStatus.READY.value,
        created_at=datetime.now(timezone.utc),
        linked_at=None,
        deleted_at=None,
    )
    defaults.update(overrides)
    return ChatAttachmentRecord(**defaults)


@pytest_asyncio.fixture
async def repo():
    repository = AttachmentRepository("sqlite+aiosqlite:///:memory:")
    yield repository
    await repository.close()


@pytest.mark.asyncio
async def test_get_missing_attachment_returns_none(repo: AttachmentRepository) -> None:
    assert await repo.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_get_for_owner_session_scopes_correctly(repo: AttachmentRepository) -> None:
    await repo.add(_record("att-1", owner_user_id="alice", session_id="s1"))
    await repo.add(_record("att-2", owner_user_id="alice", session_id="s1"))
    await repo.add(_record("att-3", owner_user_id="alice", session_id="s2"))
    await repo.add(_record("att-4", owner_user_id="bob", session_id="s1"))

    results = await repo.get_for_owner_session("alice", "s1")
    ids = {r.attachment_id for r in results}
    assert ids == {"att-1", "att-2"}


@pytest.mark.asyncio
async def test_list_for_message_scopes_correctly(repo: AttachmentRepository) -> None:
    await repo.add(_record("att-1", session_id="s1", message_id="m1"))
    await repo.add(_record("att-2", session_id="s1", message_id="m1"))
    await repo.add(_record("att-3", session_id="s1", message_id="m2"))
    await repo.add(_record("att-4", session_id="s2", message_id="m1"))

    results = await repo.list_for_message("s1", "m1")
    ids = {r.attachment_id for r in results}
    assert ids == {"att-1", "att-2"}


# --- transitions -----------------------------------------------------------


@pytest.mark.asyncio
async def test_link_to_message_ready_to_linked(repo: AttachmentRepository) -> None:
    await repo.add(_record("att-1"))
    linked_at = datetime.now(timezone.utc)

    applied = await repo.link_to_message("att-1", "msg-1", linked_at)

    assert applied is True
    stored = await repo.get("att-1")
    assert stored.status == ChatAttachmentStatus.LINKED.value
    assert stored.message_id == "msg-1"
    # SQLite (this offline test's backend) doesn't reattach tzinfo on
    # read -- see test_attachments_model.py's own comment on this.
    assert stored.linked_at.replace(tzinfo=timezone.utc) == linked_at


@pytest.mark.asyncio
async def test_link_to_message_fails_when_not_ready(repo: AttachmentRepository) -> None:
    await repo.add(_record("att-1", status=ChatAttachmentStatus.LINKED.value))

    applied = await repo.link_to_message("att-1", "msg-1", datetime.now(timezone.utc))

    assert applied is False


@pytest.mark.asyncio
async def test_link_to_message_fails_for_unknown_attachment(repo: AttachmentRepository) -> None:
    applied = await repo.link_to_message("does-not-exist", "msg-1", datetime.now(timezone.utc))
    assert applied is False


@pytest.mark.asyncio
async def test_mark_deleted_from_ready(repo: AttachmentRepository) -> None:
    await repo.add(_record("att-1"))
    deleted_at = datetime.now(timezone.utc)

    applied = await repo.mark_deleted("att-1", deleted_at)

    assert applied is True
    stored = await repo.get("att-1")
    assert stored.status == ChatAttachmentStatus.DELETED.value
    assert stored.deleted_at.replace(tzinfo=timezone.utc) == deleted_at


@pytest.mark.asyncio
async def test_mark_deleted_from_linked(repo: AttachmentRepository) -> None:
    await repo.add(_record("att-1", status=ChatAttachmentStatus.LINKED.value, message_id="msg-1"))

    applied = await repo.mark_deleted("att-1", datetime.now(timezone.utc))

    assert applied is True
    stored = await repo.get("att-1")
    assert stored.status == ChatAttachmentStatus.DELETED.value


@pytest.mark.asyncio
async def test_mark_deleted_fails_when_already_deleted(repo: AttachmentRepository) -> None:
    await repo.add(_record("att-1", status=ChatAttachmentStatus.DELETED.value))

    applied = await repo.mark_deleted("att-1", datetime.now(timezone.utc))

    assert applied is False


@pytest.mark.asyncio
async def test_invalid_transition_leaves_row_unchanged(repo: AttachmentRepository) -> None:
    """Deterministic failure (instruction section 5) -- an invalid
    transition attempt never partially mutates the row.
    """
    await repo.add(_record("att-1", status=ChatAttachmentStatus.DELETED.value))
    before = await repo.get("att-1")

    await repo.link_to_message("att-1", "msg-1", datetime.now(timezone.utc))

    after = await repo.get("att-1")
    assert after.status == before.status == ChatAttachmentStatus.DELETED.value
    assert after.message_id is None
