"""M15 retention checkpoints and archive receipts; no full trace database blobs."""
from alembic import op
import sqlalchemy as sa
revision='b15e3f7a9c81'
down_revision='a14d2e6f8c70'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('observability_retention_checkpoint',
        sa.Column('environment',sa.String(16),primary_key=True),
        sa.Column('dataset',sa.String(64),primary_key=True),
        sa.Column('policy_version',sa.String(32),nullable=False),
        sa.Column('cutoff',sa.DateTime(timezone=True),nullable=False),
        sa.Column('cursor',sa.String(1024),nullable=True),
        sa.Column('generation',sa.BigInteger(),nullable=False),
        sa.Column('complete',sa.Boolean(),nullable=False),
        sa.CheckConstraint('generation >= 0'))
    op.create_table('observability_archive_receipt',
        sa.Column('environment',sa.String(16),primary_key=True),
        sa.Column('trace_id',sa.String(32),primary_key=True),
        sa.Column('trace_class',sa.String(16),nullable=False),
        sa.Column('expected_spans',sa.Integer(),nullable=False),
        sa.Column('population_digest',sa.String(64),nullable=True),
        sa.Column('terminal_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('population_closed',sa.Boolean(),nullable=False),
        sa.Column('policy_version',sa.String(32),nullable=False),
        sa.Column('confirmed_digest',sa.String(64),nullable=True),
        sa.Column('confirmed_at',sa.DateTime(timezone=True),nullable=True),
        sa.CheckConstraint('expected_spans >= 0 AND expected_spans <= 10000'))
    op.create_index('ix_archive_receipt_environment_terminal','observability_archive_receipt',['environment','terminal_at'])
    op.create_index('ix_m15_run_retention','observability_run_status',['environment','expiry_at','run_id'])
    op.create_index('ix_m15_summary_retention','observability_turn',['environment','expiry_at','run_id'])

def downgrade():
    op.drop_index('ix_m15_summary_retention',table_name='observability_turn')
    op.drop_index('ix_m15_run_retention',table_name='observability_run_status')
    op.drop_table('observability_archive_receipt')
    op.drop_table('observability_retention_checkpoint')
