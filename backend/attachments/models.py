"""SQLAlchemy ORM model for the durable Chat Attachment domain
(POST-5.1 B1).

Table name uses the `slopanoc_` prefix, mirroring `backend/cases/models.py`
and `backend/knowledge/repository/sqlalchemy.py` exactly -- ADK's own
`DatabaseSessionService` tables (`sessions`, `events`, `app_states`,
`user_states`, `adk_internal_metadata`) share no name with anything here,
and this module never imports or touches ADK's schema classes.

METADATA/REFERENCE ONLY (B0's locked architecture): this table stores
identity, ownership, and a `storage_object_name` REFERENCE into private
GCS -- never image bytes, base64, or a data URL. There is no
`LargeBinary`/`BYTEA`/`BLOB` column, and none should ever be added here;
see `backend/tests/test_attachments_no_binary_persistence.py` for the
automated invariant check.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ChatAttachmentStatus(str, Enum):
    """The smallest useful persisted lifecycle (B1 instruction section 5).

    Deliberately does NOT include `UPLOADING` (the HTTP upload endpoint
    itself, built in B2, represents that transient state -- a row is only
    ever created once the object is durably in GCS) or `FAILED` (a failed
    upload simply means no `READY` resource was ever created -- never
    permanent failed-row clutter for speculative observability).
    """

    READY = "ready"
    LINKED = "linked"
    DELETED = "deleted"


class ChatAttachmentRecord(Base):
    """One durable chat attachment -- metadata/reference only.

    Identity is `attachment_id`, a server-generated opaque id (never a
    user-entered value, never derived from `original_filename`). No
    user-entered value may become an authoritative storage object path
    (instruction section 4) -- `storage_object_name` is always generated
    by `backend.attachments.storage`, never accepted from a caller.
    """

    __tablename__ = "slopanoc_chat_attachments"

    attachment_id: Mapped[str] = mapped_column(primary_key=True)
    owner_user_id: Mapped[str] = mapped_column(Text)
    session_id: Mapped[str] = mapped_column(Text)
    # Nullable until linked to the user message it belongs to (instruction
    # section 4/9: upload may happen before a message_id exists).
    #
    # POST-5.1 B4A/B4B SEMANTIC (locked, no schema change): despite the
    # column name, this stores the owning USER TURN's ADK `invocation_id`
    # -- i.e. `SessionHistoryMessageDTO.turn_id`, NEVER the frontend-
    # visible `SessionHistoryMessageDTO.message_id` (which is the
    # deterministic `f"{invocation_id}:user"`/`f"{invocation_id}:assistant"`
    # string; B4A's correction pass explains why: ADK's own internal user-
    # content `Event.id` is never observable by this backend without an
    # awkward extra round trip, whereas `invocation_id` is directly
    # observable on every event yielded during a turn). B4B's history
    # projection groups attachments by this same turn identity via
    # `AttachmentService.list_for_message(session_id, invocation_id)` --
    # see `backend/api/session_history_service.py`. Do not confuse the
    # two identities when touching this column.
    message_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    original_filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(Text)
    storage_object_name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    # Timezone-aware throughout (POST-5.1 A4 lesson, see cases/models.py's
    # own comment) -- every value this codebase writes is
    # `datetime.now(timezone.utc)`; PostgreSQL's asyncpg driver rejects a
    # tz-aware value against a naive TIMESTAMP column, so `DateTime(
    # timezone=True)` is mandatory here from the start, not a later fix.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    linked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("size_bytes > 0", name="ck_slopanoc_chat_attachments_size_bytes_positive"),
        CheckConstraint("length(original_filename) > 0", name="ck_slopanoc_chat_attachments_filename_nonempty"),
        CheckConstraint("length(mime_type) > 0", name="ck_slopanoc_chat_attachments_mime_type_nonempty"),
        CheckConstraint("length(sha256) > 0", name="ck_slopanoc_chat_attachments_sha256_nonempty"),
        CheckConstraint(
            "length(storage_object_name) > 0", name="ck_slopanoc_chat_attachments_storage_object_name_nonempty"
        ),
        # Ownership-scoped retrieval (instruction section 7).
        Index("ix_slopanoc_chat_attachments_owner_session", "owner_user_id", "session_id"),
        # list_for_message / linking queries.
        Index("ix_slopanoc_chat_attachments_session_message", "session_id", "message_id"),
        # Future orphan/retention cleanup sweep (not built in B1).
        Index("ix_slopanoc_chat_attachments_status_created", "status", "created_at"),
    )
