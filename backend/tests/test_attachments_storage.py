"""POST-5.1 B1: opaque object-key naming, no-PII-in-path invariants, and
`SLOPANOC_CHAT_ATTACHMENTS_BUCKET` settings behavior. Never touches a
real GCS bucket -- `ChatAttachmentStorage`'s underlying client is only
ever constructed lazily on first real operation, which these tests never
trigger for the "unconfigured" cases.
"""
from __future__ import annotations

import pytest

from backend.attachments.storage import (
    AttachmentStorageUnavailableError,
    ChatAttachmentStorage,
    build_object_name,
    generate_attachment_id,
)
from backend.config.settings import Settings


def test_object_name_is_opaque_and_deterministic() -> None:
    name = build_object_name("session-123", "attachment-456")
    assert name == "chat-attachments/session-123/attachment-456"


def test_object_name_never_contains_raw_filename() -> None:
    # build_object_name doesn't even accept a filename parameter -- this
    # is a structural guarantee, verified here by signature/behavior.
    name = build_object_name("session-123", "attachment-456")
    assert "screenshot" not in name
    assert ".png" not in name
    assert ".jpg" not in name


def test_object_name_never_contains_email_like_or_owner_value() -> None:
    """Object keys are built ONLY from session_id/attachment_id -- an
    owner identity (even one that looked like an email) could never leak
    into the path, because build_object_name has no owner parameter at
    all.
    """
    name = build_object_name("session-123", "attachment-456")
    assert "@" not in name


def test_generate_attachment_id_is_opaque_and_unique() -> None:
    a = generate_attachment_id()
    b = generate_attachment_id()
    assert a != b
    # Opaque UUID4 shape -- not a sequential/predictable id, not derived
    # from any user input.
    assert len(a) == 36
    assert a.count("-") == 4


def test_storage_reports_unconfigured_when_bucket_unset() -> None:
    storage = ChatAttachmentStorage(bucket_name=None)
    assert storage.is_configured is False


def test_storage_reports_configured_when_bucket_set() -> None:
    storage = ChatAttachmentStorage(bucket_name="slopanoc-chat-attachments-sandbox01")
    assert storage.is_configured is True


def test_put_bytes_raises_clearly_when_unconfigured() -> None:
    storage = ChatAttachmentStorage(bucket_name=None)
    with pytest.raises(AttachmentStorageUnavailableError):
        storage.put_bytes("chat-attachments/s1/a1", b"data", "image/png")


def test_get_bytes_raises_clearly_when_unconfigured() -> None:
    storage = ChatAttachmentStorage(bucket_name=None)
    with pytest.raises(AttachmentStorageUnavailableError):
        storage.get_bytes("chat-attachments/s1/a1")


def test_delete_raises_clearly_when_unconfigured() -> None:
    storage = ChatAttachmentStorage(bucket_name=None)
    with pytest.raises(AttachmentStorageUnavailableError):
        storage.delete("chat-attachments/s1/a1")


# --- Settings ----------------------------------------------------------


def test_chat_attachments_bucket_defaults_to_none_when_unset() -> None:
    settings = Settings(env={})
    assert settings.chat_attachments_bucket is None


def test_chat_attachments_bucket_returns_configured_value() -> None:
    settings = Settings(env={"SLOPANOC_CHAT_ATTACHMENTS_BUCKET": "slopanoc-chat-attachments-sandbox01"})
    assert settings.chat_attachments_bucket == "slopanoc-chat-attachments-sandbox01"


def test_chat_attachments_bucket_treats_blank_as_unset() -> None:
    settings = Settings(env={"SLOPANOC_CHAT_ATTACHMENTS_BUCKET": "   "})
    assert settings.chat_attachments_bucket is None


def test_ordinary_settings_still_work_when_bucket_unset() -> None:
    """Application startup / ordinary text chat must never depend on
    this setting being present (instruction section 9).
    """
    settings = Settings(env={})
    assert settings.chat_attachments_bucket is None
    # Unrelated settings resolve completely normally alongside it.
    assert settings.resolve_database_url().startswith("sqlite+aiosqlite:///")
    assert settings.session_backend == "database"
