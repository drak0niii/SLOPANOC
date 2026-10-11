"""Join immutable historical applied history and M7–M15 sibling history.

Revision ID: c16f4a8b2d90
Revises: d3f8b1c6a942, b15e3f7a9c81

No schema operations. Both parent branches must be satisfied before this
merge is recorded. In particular, upgrading from historical d3 executes the
missing business-progression and observability siblings, never historical DDL.
Database deployment requires separate exact-operation user approval.
"""

revision = 'c16f4a8b2d90'
down_revision = ('d3f8b1c6a942', 'b15e3f7a9c81')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    # Alembic may split version rows; this revision changes no schema.
    pass
