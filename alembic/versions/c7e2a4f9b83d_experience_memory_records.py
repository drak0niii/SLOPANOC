"""experience memory records (6A.8 / P11-M08)

Revision ID: c7e2a4f9b83d
Revises: a1f3c9e07b21
Create Date: 2026-09-12 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7e2a4f9b83d'
down_revision: Union[str, None] = 'a1f3c9e07b21'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'slopanoc_experience_records',
        sa.Column('experience_id', sa.String(), nullable=False),
        sa.Column('experience_schema_version', sa.String(), nullable=False),
        sa.Column('experience_type', sa.String(), nullable=False),
        sa.Column('lifecycle', sa.String(), nullable=False),
        sa.Column('owner_id', sa.String(), nullable=False),
        sa.Column('source_origin', sa.String(), nullable=False),
        sa.Column('source_reference', sa.Text(), nullable=True),
        sa.Column('source_event_id', sa.String(), nullable=False),
        sa.Column('source_event_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('case_id', sa.String(), nullable=True),
        sa.Column('session_id', sa.String(), nullable=True),
        sa.Column('context_fingerprint', sa.Text(), nullable=True),
        sa.Column('skill_id', sa.String(), nullable=True),
        sa.Column('skill_version', sa.String(), nullable=True),
        sa.Column('skill_fingerprint', sa.Text(), nullable=True),
        sa.Column('evidence_references_json', sa.Text(), nullable=False),
        sa.Column('observed_facts_json', sa.Text(), nullable=False),
        sa.Column('outcome_summary', sa.Text(), nullable=False),
        sa.Column('metadata_json', sa.Text(), nullable=False),
        sa.Column('content_fingerprint', sa.String(), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('invalidated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('invalidation_reason', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('experience_id'),
    )
    op.create_index('ix_experience_owner', 'slopanoc_experience_records', ['owner_id'])
    op.create_index('ix_experience_owner_case', 'slopanoc_experience_records', ['owner_id', 'case_id'])
    op.create_index('ix_experience_owner_type', 'slopanoc_experience_records', ['owner_id', 'experience_type'])
    op.create_index('ix_experience_owner_skill', 'slopanoc_experience_records', ['owner_id', 'skill_id'])
    op.create_index('ix_experience_owner_recorded_at', 'slopanoc_experience_records', ['owner_id', 'recorded_at'])


def downgrade() -> None:
    op.drop_index('ix_experience_owner_recorded_at', table_name='slopanoc_experience_records')
    op.drop_index('ix_experience_owner_skill', table_name='slopanoc_experience_records')
    op.drop_index('ix_experience_owner_type', table_name='slopanoc_experience_records')
    op.drop_index('ix_experience_owner_case', table_name='slopanoc_experience_records')
    op.drop_index('ix_experience_owner', table_name='slopanoc_experience_records')
    op.drop_table('slopanoc_experience_records')
