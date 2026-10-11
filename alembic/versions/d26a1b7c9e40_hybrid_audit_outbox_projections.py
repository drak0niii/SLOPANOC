"""Local Phase-1 critical audit/outbox and derived projection tables.

Only additive application-owned tables; never apply to shared SQL by implication.
"""
from alembic import op
import sqlalchemy as sa
revision='d26a1b7c9e40'
down_revision='c16f4a8b2d90'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('observability_agent_projection',
        sa.Column('environment',sa.String(16),nullable=False,primary_key=True),
        sa.Column('agent',sa.String(64),nullable=False,primary_key=True),
        sa.Column('model_attempts_started',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('model_attempts_completed',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('usage_records_captured',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('latest_event_occurred_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('latest_event_processed_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('projection_lag',sa.Float(),nullable=False,primary_key=False),
        sa.CheckConstraint('model_attempts_started >= 0 AND model_attempts_completed >= 0 AND usage_records_captured >= 0 AND projection_lag >= 0'),
    )
    op.create_table('observability_critical_audit',
        sa.Column('event_id',sa.String(36),nullable=False,primary_key=True),
        sa.Column('event_schema_version',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('event_class',sa.String(16),nullable=False,primary_key=False),
        sa.Column('event_type',sa.String(64),nullable=False,primary_key=False),
        sa.Column('environment',sa.String(16),nullable=False,primary_key=False),
        sa.Column('aggregate_type',sa.String(32),nullable=False,primary_key=False),
        sa.Column('aggregate_id',sa.String(128),nullable=False,primary_key=False),
        sa.Column('aggregate_version',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('run_id',sa.String(36),nullable=True,primary_key=False),
        sa.Column('turn_id',sa.String(128),nullable=True,primary_key=False),
        sa.Column('trace_id',sa.String(32),nullable=True,primary_key=False),
        sa.Column('occurred_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('producer',sa.String(32),nullable=False,primary_key=False),
        sa.Column('safe_payload',sa.JSON(),nullable=False,primary_key=False),
        sa.Column('payload_hash',sa.String(64),nullable=False,primary_key=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.CheckConstraint("event_schema_version = 1 AND event_class = 'AUDIT' AND aggregate_version >= 1"),
    )
    op.create_index('ix_hybrid_audit_aggregate','observability_critical_audit',['environment', 'aggregate_type', 'aggregate_id', 'aggregate_version'])
    op.create_table('observability_projection_failure',
        sa.Column('environment',sa.String(16),nullable=False,primary_key=True),
        sa.Column('failure_key',sa.String(64),nullable=False,primary_key=True),
        sa.Column('failure_code',sa.String(32),nullable=False,primary_key=False),
        sa.Column('processing_state',sa.String(16),nullable=False,primary_key=False),
        sa.Column('attempts',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('last_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.CheckConstraint("failure_code IN ('INVALID','CONFLICT','PROCESSING_FAILED') AND processing_state IN ('RETRY','DEAD','RESOLVED') AND attempts >= 1"),
    )
    op.create_table('observability_projection_receipt',
        sa.Column('environment',sa.String(16),nullable=False,primary_key=True),
        sa.Column('event_id',sa.String(36),nullable=False,primary_key=True),
        sa.Column('payload_hash',sa.String(64),nullable=False,primary_key=False),
        sa.Column('occurred_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('processed_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('duplicate_count',sa.BigInteger(),nullable=False,primary_key=False),
        sa.CheckConstraint('duplicate_count >= 0'),
    )
    op.create_index('ix_hybrid_receipt_recent','observability_projection_receipt',['environment', 'processed_at'])
    op.create_table('observability_run_projection',
        sa.Column('environment',sa.String(16),nullable=False,primary_key=True),
        sa.Column('run_id',sa.String(36),nullable=False,primary_key=True),
        sa.Column('aggregate_version',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('turn_id',sa.String(128),nullable=True,primary_key=False),
        sa.Column('trace_id',sa.String(32),nullable=True,primary_key=False),
        sa.Column('status',sa.String(16),nullable=False,primary_key=False),
        sa.Column('latest_event_occurred_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('latest_event_processed_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('projection_lag',sa.Float(),nullable=False,primary_key=False),
        sa.CheckConstraint('aggregate_version >= 1 AND projection_lag >= 0'),
        sa.CheckConstraint("status IN ('RUNNING','COMPLETED','FAILED','TIMEOUT','CANCELLED')"),
    )
    op.create_index('ix_hybrid_run_recent','observability_run_projection',['environment', 'latest_event_occurred_at', 'run_id'])
    op.create_table('observability_audit_outbox',
        sa.Column('event_id',sa.String(36), sa.ForeignKey('observability_critical_audit.event_id'),nullable=False,primary_key=True),
        sa.Column('publication_state',sa.String(16),nullable=False,primary_key=False),
        sa.Column('attempt_count',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('available_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('lease_owner',sa.String(36),nullable=True,primary_key=False),
        sa.Column('lease_expires_at',sa.DateTime(timezone=True),nullable=True,primary_key=False),
        sa.Column('published_at',sa.DateTime(timezone=True),nullable=True,primary_key=False),
        sa.Column('last_failure_code',sa.String(32),nullable=True,primary_key=False),
        sa.Column('event_schema_version',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('event_class',sa.String(16),nullable=False,primary_key=False),
        sa.Column('event_type',sa.String(64),nullable=False,primary_key=False),
        sa.Column('environment',sa.String(16),nullable=False,primary_key=False),
        sa.Column('aggregate_type',sa.String(32),nullable=False,primary_key=False),
        sa.Column('aggregate_id',sa.String(128),nullable=False,primary_key=False),
        sa.Column('aggregate_version',sa.BigInteger(),nullable=False,primary_key=False),
        sa.Column('run_id',sa.String(36),nullable=True,primary_key=False),
        sa.Column('turn_id',sa.String(128),nullable=True,primary_key=False),
        sa.Column('trace_id',sa.String(32),nullable=True,primary_key=False),
        sa.Column('occurred_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('producer',sa.String(32),nullable=False,primary_key=False),
        sa.Column('safe_payload',sa.JSON(),nullable=False,primary_key=False),
        sa.Column('payload_hash',sa.String(64),nullable=False,primary_key=False),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.CheckConstraint("(publication_state = 'PUBLISHED' AND published_at IS NOT NULL) OR (publication_state <> 'PUBLISHED' AND published_at IS NULL)"),
        sa.CheckConstraint("publication_state IN ('PENDING','LEASED','RETRYING','DEAD','PUBLISHED')"),
        sa.CheckConstraint("(publication_state = 'LEASED' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL) OR (publication_state <> 'LEASED' AND lease_owner IS NULL AND lease_expires_at IS NULL)"),
        sa.CheckConstraint("event_schema_version = 1 AND event_class = 'AUDIT' AND aggregate_version >= 1 AND attempt_count >= 0"),
    )
    op.create_index('ix_hybrid_outbox_available','observability_audit_outbox',['environment', 'publication_state', 'available_at', 'event_id'])
    op.create_index('ix_hybrid_outbox_oldest','observability_audit_outbox',['environment', 'created_at', 'publication_state'])

def downgrade():
    op.drop_table('observability_audit_outbox')
    op.drop_table('observability_run_projection')
    op.drop_table('observability_projection_receipt')
    op.drop_table('observability_projection_failure')
    op.drop_table('observability_critical_audit')
    op.drop_table('observability_agent_projection')
