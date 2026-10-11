"""hybrid retrieval evidence index -- pgvector + FTS (6A.5 / P11-M05)

Revision ID: a1f3c9e07b21
Revises: 9b6df6490c0e
Create Date: 2026-09-12 00:00:00.000000

REQUIRES the PostgreSQL `vector` extension (pgvector >= 0.5.0) to be
installed by a role with sufficient privilege -- Cloud SQL PostgreSQL
requires the `cloudsqlsuperuser` role for `CREATE EXTENSION vector`.
CONFIRMED BLOCKER during this milestone's own live validation: the real
Cloud SQL IAM database user (`costin.ionita@ericsson.com`, member of
`cloudsqliamuser`/`slopanoc_migrator`/`slopanoc_runtime`, none of which
include `cloudsqlsuperuser`) received `InsufficientPrivilegeError:
permission denied to create extension "vector" -- HINT: Must be
superuser to create this extension.` when this exact statement was
probed directly (outside Alembic, in a rolled-back transaction) against
the real DEV database. This migration is written as the correct,
intended schema regardless -- applying it will fail at this first
statement until a `cloudsqlsuperuser`-privileged operation installs the
extension (a real GCP/Cloud SQL administrative action, never self-
granted by this migration or by application code -- see docs/
KNOWLEDGE_CONTRACT.md §27 and the 6A.5 closure report's own Evidence
Pack for the full, verbatim error).
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR


# revision identifiers, used by Alembic.
revision: str = 'a1f3c9e07b21'
down_revision: Union[str, None] = '9b6df6490c0e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_VECTOR_DIMENSIONS = 768


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')
    op.create_table(
        'slopanoc_knowledge_evidence_index',
        sa.Column('evidence_id', sa.Text(), nullable=False),
        sa.Column('knowledge_id', sa.Text(), nullable=False),
        sa.Column('version_label', sa.Text(), nullable=False),
        sa.Column('section_id', sa.Text(), nullable=False),
        sa.Column('artifact_id', sa.Text(), nullable=True),
        sa.Column('is_derived', sa.Boolean(), nullable=False),
        sa.Column('indexable_text', sa.Text(), nullable=False),
        sa.Column('content_hash', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(_VECTOR_DIMENSIONS), nullable=True),
        sa.Column('embedding_model', sa.Text(), nullable=True),
        sa.Column('embedding_model_version', sa.Text(), nullable=True),
        sa.Column('embedding_dimensions', sa.Integer(), nullable=True),
        sa.Column('embedding_generated_at', sa.DateTime(), nullable=True),
        sa.Column('text_search_vector', TSVECTOR(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('evidence_id'),
    )
    op.create_index('ix_evidence_index_knowledge_id', 'slopanoc_knowledge_evidence_index', ['knowledge_id'])
    op.create_index('ix_evidence_index_content_hash', 'slopanoc_knowledge_evidence_index', ['content_hash'])
    op.execute(
        'CREATE INDEX ix_evidence_index_text_search_vector '
        'ON slopanoc_knowledge_evidence_index USING GIN (text_search_vector)'
    )
    # Deliberately NO ANN index (ivfflat/hnsw) on `embedding` -- exact
    # cosine-distance scan is sufficient at this milestone's real,
    # measured corpus scale (§25); adding one prematurely would be
    # complexity with no justified tradeoff.


def downgrade() -> None:
    op.drop_index('ix_evidence_index_text_search_vector', table_name='slopanoc_knowledge_evidence_index')
    op.drop_index('ix_evidence_index_content_hash', table_name='slopanoc_knowledge_evidence_index')
    op.drop_index('ix_evidence_index_knowledge_id', table_name='slopanoc_knowledge_evidence_index')
    op.drop_table('slopanoc_knowledge_evidence_index')
    # Deliberately does NOT `DROP EXTENSION vector` -- another table/
    # migration may come to depend on the same extension later, and this
    # migration did not prove it is the extension's only consumer (per
    # instruction: "dropping the vector extension on downgrade may be
    # inappropriate if shared dependencies exist"). Leaving the extension
    # installed on downgrade is the safe default.
