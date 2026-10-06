"""case troubleshooting progression (authoritative, versioned)

Revision ID: 7c4e9a2d1f05
Revises: 3e59584b1012
Create Date: 2026-09-30 12:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7c4e9a2d1f05'
down_revision: Union[str, None] = '3e59584b1012'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('slopanoc_case_troubleshooting_progressions',
    sa.Column('case_id', sa.String(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('schema_version', sa.Text(), nullable=False),
    sa.Column('progression', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_by_session_id', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['case_id'], ['slopanoc_cases.case_id'], ),
    sa.PrimaryKeyConstraint('case_id'),
    sa.CheckConstraint('version >= 1', name='ck_slopanoc_case_troubleshooting_progressions_version_positive'),
    )


def downgrade() -> None:
    op.drop_table('slopanoc_case_troubleshooting_progressions')
