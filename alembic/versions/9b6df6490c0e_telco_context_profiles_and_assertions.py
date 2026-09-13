"""telco context profiles and assertions (6A.2 / P11-M02)

Revision ID: 9b6df6490c0e
Revises: 3e59584b1012
Create Date: 2026-09-11 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9b6df6490c0e'
down_revision: Union[str, None] = '3e59584b1012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('slopanoc_telco_context_profiles',
    sa.Column('profile_id', sa.String(), nullable=False),
    sa.Column('owner_kind', sa.String(), nullable=False),
    sa.Column('owner_id', sa.String(), nullable=False),
    sa.Column('created_by_user_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('profile_id'),
    sa.UniqueConstraint('owner_kind', 'owner_id', name='uq_telco_context_profile_owner')
    )
    op.create_table('slopanoc_telco_context_assertions',
    sa.Column('assertion_id', sa.String(), nullable=False),
    sa.Column('profile_id', sa.String(), nullable=False),
    sa.Column('dimension', sa.String(), nullable=False),
    sa.Column('kind', sa.String(), nullable=False),
    sa.Column('raw_value', sa.Text(), nullable=True),
    sa.Column('canonical_value', sa.Text(), nullable=True),
    sa.Column('origin', sa.String(), nullable=False),
    sa.Column('source_reference', sa.Text(), nullable=True),
    sa.Column('asserted_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['profile_id'], ['slopanoc_telco_context_profiles.profile_id'], ),
    sa.PrimaryKeyConstraint('assertion_id')
    )


def downgrade() -> None:
    op.drop_table('slopanoc_telco_context_assertions')
    op.drop_table('slopanoc_telco_context_profiles')
