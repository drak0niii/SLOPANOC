"""POST-6A: durable ownership generations for dispatchable operations.

Creates `slopanoc_operation_claims` -- one row per durable operation
identity (`derive_operation_id`: proposal + operation + destination +
payload hash), carrying the FENCING GENERATION that makes multi-worker
dispatch safe.

WHY A TABLE AND NOT JUST THE ADVISORY LOCK. The advisory lock gives
liveness (PostgreSQL releases it the moment the holder's connection
dies) but cannot stop a worker that has already passed its own ownership
check from writing afterwards. The `generation` column is the fencing
token: it increases every time ownership changes hands, and every
subsequent write is conditional on presenting the generation the writer
was given. A superseded worker's write matches no row and is rejected by
the database, not by the worker's own good behaviour. See
`backend/api/operation_claims.py` for the full protocol.

`status` deliberately reuses the `ExecutionStatus` vocabulary, including
UNKNOWN_OUTCOME -- an ambiguous dispatch must stay distinguishable from a
failure, or it invites an automatic resend.

Revision ID: b7c4e1a95d60
Revises: f1b6c3d05a27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b7c4e1a95d60"
down_revision: str | None = "f1b6c3d05a27"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "slopanoc_operation_claims"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _TABLE in inspector.get_table_names():
        # Idempotent against a table created by a test/local
        # `manage_schema=True` store: leave it and its rows completely
        # untouched. These rows record whether real operations were
        # dispatched; recreating the table would erase exactly the
        # history that prevents a resend.
        return
    op.create_table(
        _TABLE,
        sa.Column("operation_id", sa.String(length=128), primary_key=True),
        sa.Column("owner_worker_id", sa.String(length=255), nullable=False),
        # The fencing token. Monotonically increasing per operation --
        # never reset, never reused.
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table(_TABLE)
