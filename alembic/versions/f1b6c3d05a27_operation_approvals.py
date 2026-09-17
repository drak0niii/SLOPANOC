"""POST-6A: governed operation descriptor approvals.

Creates `slopanoc_operation_approvals` -- one durable row per human
approval decision about one descriptor, on one section, of one version:
reviewer identity, timestamp, the exact source version, the descriptor
fingerprint at approval time, and revocation state.

IDEMPOTENT AGAINST AN ALREADY-CREATED TABLE: earlier builds created this
table lazily at runtime (`OperationApprovalStore.ensure_schema`). If it
already exists, this revision leaves it and its data completely
untouched -- it never drops, never recreates, and never rewrites rows.
It only adds any column that is genuinely missing, so a table created by
the older lazy path converges to the migrated shape without data loss.

Revision ID: f1b6c3d05a27
Revises: e4a7c2b91d38
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f1b6c3d05a27"
down_revision: str | None = "e4a7c2b91d38"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "slopanoc_operation_approvals"

_COLUMNS = (
    sa.Column("approval_id", sa.Text(), primary_key=True),
    sa.Column("knowledge_id", sa.Text(), nullable=False),
    sa.Column("version_label", sa.Text(), nullable=False),
    sa.Column("section_id", sa.Text(), nullable=False),
    sa.Column("descriptor_fingerprint", sa.Text(), nullable=False),
    sa.Column("approved_by", sa.Text(), nullable=False),
    sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("note", sa.Text(), nullable=True),
    sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    sa.Column("revoked_by", sa.Text(), nullable=True),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
)

# Columns that may legitimately be missing from a table created by the
# older lazy path and can be added safely. The identity/approval columns
# are deliberately NOT in this list: a table missing one of those is not
# a compatible earlier version of this table, and silently patching it
# would be worse than failing loudly.
_ADDABLE = {"note", "revoked", "revoked_by", "revoked_at"}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE not in inspector.get_table_names():
        op.create_table(_TABLE, *_COLUMNS)
        op.create_index(f"ix_{_TABLE}_knowledge_id", _TABLE, ["knowledge_id"])
        return

    existing = {column["name"] for column in inspector.get_columns(_TABLE)}
    for column in _COLUMNS:
        if column.name in existing or column.name not in _ADDABLE:
            continue
        op.add_column(_TABLE, column.copy())


def downgrade() -> None:
    op.drop_table(_TABLE)
