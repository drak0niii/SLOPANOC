"""case timestamps use timezone-aware columns

Revision ID: b85242503972
Revises: d2081e4fd455
Create Date: 2026-09-07 15:15:54.814899

POST-5.1 A4: corrects `backend/cases/models.py`'s datetime columns to
`DateTime(timezone=True)` -- every value this codebase writes is
`datetime.now(timezone.utc)` (tz-aware). SQLite silently tolerated a
tz-aware value in a naive TIMESTAMP column; PostgreSQL's asyncpg driver
correctly rejects it (confirmed live against Cloud SQL:
"can't subtract offset-naive and offset-aware datetimes"). This migration
only ALTERs the 6 affected columns' type (TIMESTAMP -> TIMESTAMPTZ) --
existing (empty, at time of writing) data is unaffected.

NOTE ON THIS FILE'S ORIGIN: autogenerate also proposed dropping ADK's own
session tables (app_states/events/sessions/user_states/
adk_internal_metadata) -- expected and harmless to detect (they are not
part of this repo's `target_metadata`), but never intended to be applied;
those statements were removed by hand before this migration was ever run.
See alembic/env.py's `include_object` filter (added alongside this
migration) for why future autogenerate passes will no longer propose this.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b85242503972'
down_revision: Union[str, None] = 'd2081e4fd455'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column('slopanoc_case_context_items', 'source_timestamp',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=True)
    op.alter_column('slopanoc_case_context_items', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False)
    op.alter_column('slopanoc_case_memberships', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False)
    op.alter_column('slopanoc_case_session_links', 'linked_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False)
    op.alter_column('slopanoc_cases', 'created_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False)
    op.alter_column('slopanoc_cases', 'updated_at',
               existing_type=postgresql.TIMESTAMP(),
               type_=sa.DateTime(timezone=True),
               existing_nullable=False)


def downgrade() -> None:
    op.alter_column('slopanoc_cases', 'updated_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False)
    op.alter_column('slopanoc_cases', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False)
    op.alter_column('slopanoc_case_session_links', 'linked_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False)
    op.alter_column('slopanoc_case_memberships', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False)
    op.alter_column('slopanoc_case_context_items', 'created_at',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=False)
    op.alter_column('slopanoc_case_context_items', 'source_timestamp',
               existing_type=sa.DateTime(timezone=True),
               type_=postgresql.TIMESTAMP(),
               existing_nullable=True)
