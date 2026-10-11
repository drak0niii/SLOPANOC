"""experience records source_namespace (6A.8 final source-namespace corrective pass)

Revision ID: d3f8b1c6a942
Revises: c7e2a4f9b83d
Create Date: 2026-09-13 00:00:00.000000

Adds `source_namespace` -- the stable PRODUCER/SYSTEM namespace that
owns `source_event_id` (a genuinely different concern from
`source_origin`, the trust/admission classification field that already
existed). `NOT NULL` with no server default: the verified live table
contained zero rows both immediately before this migration was authored
and immediately before it was applied, so no backfill/default value is
needed. Adds no index on this column -- identity computation reads it
directly (application-side hashing), not via a database query, and no
retrieval requirement currently justifies one (§27 of that pass's own
instruction).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd3f8b1c6a942'
down_revision: Union[str, None] = 'c7e2a4f9b83d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('slopanoc_experience_records', sa.Column('source_namespace', sa.String(), nullable=False))


def downgrade() -> None:
    op.drop_column('slopanoc_experience_records', 'source_namespace')
