"""POST-5.1 B1: `ChatAttachmentRecord` schema-level invariants -- CHECK
constraints, the no-binary-persistence guarantee, and timezone-aware
datetime round-tripping. Uses a disposable in-memory SQLite database,
mirroring `backend/tests/test_case_persistence.py`'s own pattern.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError

from backend.attachments.models import ChatAttachmentRecord, ChatAttachmentStatus
from backend.attachments.repository import AttachmentRepository


def _record(**overrides) -> ChatAttachmentRecord:
    defaults = dict(
        attachment_id="att-1",
        owner_user_id="user-1",
        session_id="session-1",
        message_id=None,
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=1024,
        sha256="a" * 64,
        storage_object_name="chat-attachments/session-1/att-1",
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
async def test_valid_attachment_can_be_added(repo: AttachmentRepository) -> None:
    await repo.add(_record())
    stored = await repo.get("att-1")
    assert stored is not None
    assert stored.status == ChatAttachmentStatus.READY.value


@pytest.mark.asyncio
async def test_zero_size_bytes_is_rejected(repo: AttachmentRepository) -> None:
    with pytest.raises(IntegrityError):
        await repo.add(_record(size_bytes=0))


@pytest.mark.asyncio
async def test_negative_size_bytes_is_rejected(repo: AttachmentRepository) -> None:
    with pytest.raises(IntegrityError):
        await repo.add(_record(attachment_id="att-2", size_bytes=-5))


@pytest.mark.asyncio
async def test_empty_original_filename_is_rejected(repo: AttachmentRepository) -> None:
    with pytest.raises(IntegrityError):
        await repo.add(_record(attachment_id="att-3", original_filename=""))


@pytest.mark.asyncio
async def test_empty_mime_type_is_rejected(repo: AttachmentRepository) -> None:
    with pytest.raises(IntegrityError):
        await repo.add(_record(attachment_id="att-4", mime_type=""))


@pytest.mark.asyncio
async def test_empty_sha256_is_rejected(repo: AttachmentRepository) -> None:
    with pytest.raises(IntegrityError):
        await repo.add(_record(attachment_id="att-5", sha256=""))


@pytest.mark.asyncio
async def test_empty_storage_object_name_is_rejected(repo: AttachmentRepository) -> None:
    with pytest.raises(IntegrityError):
        await repo.add(_record(attachment_id="att-6", storage_object_name=""))


@pytest.mark.asyncio
async def test_timezone_aware_datetime_round_trips(repo: AttachmentRepository) -> None:
    """`DateTime(timezone=True)` is what makes this column TIMESTAMPTZ on
    PostgreSQL (proven correct against real Cloud SQL in POST-5.1 A4) --
    the underlying moment in time round-trips correctly here too. SQLite
    itself (used for this offline test, like every other repository test
    in this backend) stores the value as text and does not reattach
    tzinfo on read -- a SQLAlchemy+SQLite dialect characteristic, not a
    bug in this schema -- so the comparison normalizes to the naive UTC
    instant rather than asserting `tzinfo is not None` here.
    """
    created_at = datetime(2026, 9, 8, 10, 30, 0, tzinfo=timezone.utc)
    await repo.add(_record(attachment_id="att-7", created_at=created_at))
    stored = await repo.get("att-7")
    assert stored is not None
    assert stored.created_at.replace(tzinfo=timezone.utc) == created_at


def test_no_binary_persistence_columns_exist() -> None:
    """The schema must contain no LargeBinary/BYTEA/BLOB column, and no
    column plausibly named/typed for inline image content -- this table
    stores metadata/reference ONLY (POST-5.1 B0's locked architecture).
    """
    from sqlalchemy import LargeBinary

    forbidden_name_fragments = ("data", "bytes_content", "base64", "blob", "binary", "content")
    # "size_bytes" legitimately contains "bytes" as a metadata field (an
    # integer, not a binary column) -- explicitly allowed.
    allowed_exceptions = {"size_bytes"}

    for column in ChatAttachmentRecord.__table__.columns:
        assert not isinstance(column.type, LargeBinary), f"{column.name} must never be a LargeBinary column"
        if column.name in allowed_exceptions:
            continue
        lowered = column.name.lower()
        for fragment in forbidden_name_fragments:
            assert fragment not in lowered, (
                f"column {column.name!r} name suggests it might hold binary/inline content "
                f"(matched fragment {fragment!r}) -- attachment rows must be metadata/reference only"
            )
