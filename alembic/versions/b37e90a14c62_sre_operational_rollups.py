"""M9 compact SRE receipts/rollups, no accounting schema; local authoring only."""
from alembic import op
import sqlalchemy as sa
revision='b37e90a14c62'
down_revision='9f71c2a64e08'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('observability_sre_receipt',
        sa.Column('run_id',sa.String(36),primary_key=True),
        sa.Column('environment',sa.String(16),nullable=False),
        sa.Column('due_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('payload',sa.JSON(),nullable=False),sa.Column('contribution',sa.JSON(),nullable=False),
        sa.Column('settled',sa.Boolean(),nullable=False))
    op.create_index('ix_sre_due','observability_sre_receipt',['settled','due_at'])
    op.create_table('observability_sre_bucket',
        sa.Column('environment',sa.String(16),primary_key=True),sa.Column('slo_id',sa.String(64),primary_key=True),
        sa.Column('at',sa.DateTime(timezone=True),primary_key=True),
        *[sa.Column(k,sa.BigInteger(),nullable=False) for k in ('good','bad','unknown','excluded')])
    op.create_table('observability_sre_source',
        sa.Column('environment',sa.String(16),primary_key=True),
        sa.Column('coverage_start',sa.DateTime(timezone=True),nullable=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('capture_complete',sa.Boolean(),nullable=False))

def downgrade():
    op.drop_table('observability_sre_source');op.drop_table('observability_sre_bucket')
    op.drop_index('ix_sre_due',table_name='observability_sre_receipt');op.drop_table('observability_sre_receipt')
