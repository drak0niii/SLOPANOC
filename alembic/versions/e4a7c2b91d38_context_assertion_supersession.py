"""POST-6A: context assertion correction/supersession.

Adds the two columns that let the append-only TELCO Context assertion
store express a CORRECTION ("actually it is RRU-10") and a RETRACTION
("ignore the vendor I gave you") without ever editing or deleting a prior
row -- see `backend/context/domain/models.py`'s `effective_assertions`
for how they are resolved into the effective context.

Both are nullable/defaulted, so every existing row keeps its exact
current meaning: `supersedes_assertion_id IS NULL` and
`retracted = false` is precisely "this assertion still stands", which is
what every pre-existing assertion already meant.

Revision ID: e4a7c2b91d38
Revises: d3f8b1c6a942
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "e4a7c2b91d38"
down_revision: str | None = "d3f8b1c6a942"
branch_labels: str | None = None
depends_on: str | None = None

_TABLE = "slopanoc_telco_context_assertions"


def upgrade() -> None:
    op.add_column(_TABLE, sa.Column("supersedes_assertion_id", sa.Text(), nullable=True))
    op.add_column(
        _TABLE,
        sa.Column("retracted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    op.drop_column(_TABLE, "retracted")
    op.drop_column(_TABLE, "supersedes_assertion_id")
