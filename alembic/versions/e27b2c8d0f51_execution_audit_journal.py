"""Execution audit read models and controlled egress intent; local-only approval required."""
from alembic import op
import sqlalchemy as sa
revision='e27b2c8d0f51'
down_revision='d26a1b7c9e40'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('execution_action_record',
        sa.Column('execution_id',sa.String(36),nullable=False,primary_key=True),
        sa.Column('environment',sa.String(16),nullable=False,primary_key=False),
        sa.Column('action_id',sa.String(128),nullable=True,primary_key=False),
        sa.Column('authorization_id',sa.String(36),nullable=False,primary_key=False),
        sa.Column('approval_id',sa.String(128),nullable=True,primary_key=False),
        sa.Column('session_id',sa.String(128),nullable=True,primary_key=False),
        sa.Column('run_id',sa.String(36),nullable=True,primary_key=False),
        sa.Column('turn_id',sa.String(128),nullable=True,primary_key=False),
        sa.Column('action_type',sa.String(64),nullable=False,primary_key=False),
        sa.Column('safe_target_reference',sa.String(128),nullable=True,primary_key=False),
        sa.Column('state',sa.String(16),nullable=False,primary_key=False),
        sa.Column('attempt',sa.Integer(),nullable=False,primary_key=False),
        sa.Column('started_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('downstream_request_id',sa.String(36),nullable=True,primary_key=False),
        sa.CheckConstraint("action_type IN ('teams.createChat','teams.sendMessage')"),
        sa.CheckConstraint("state IN ('AUTHORIZED','DISPATCHING','ACKNOWLEDGED','COMPLETED','FAILED','UNKNOWN') AND attempt >= 1"),
    )
    op.create_index('ix_execution_action_run','execution_action_record',['environment', 'run_id', 'started_at', 'execution_id'])
    op.create_table('execution_audit_timeline',
        sa.Column('environment',sa.String(16),nullable=False,primary_key=True),
        sa.Column('event_id',sa.String(36),nullable=False,primary_key=True),
        sa.Column('run_id',sa.String(36),nullable=True,primary_key=False),
        sa.Column('occurred_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('processed_at',sa.DateTime(timezone=True),nullable=False,primary_key=False),
        sa.Column('payload_hash',sa.String(64),nullable=False,primary_key=False),
        sa.Column('event',sa.JSON(),nullable=False,primary_key=False),
    )
    op.create_index('ix_execution_timeline_run','execution_audit_timeline',['environment', 'run_id', 'occurred_at', 'event_id'])
    op.create_table('execution_activity_record',
        sa.Column('environment',sa.String(16),nullable=False,primary_key=True),
        sa.Column('execution_id',sa.String(128),nullable=False,primary_key=True),
        sa.Column('domain',sa.String(16),nullable=False,primary_key=False),
        sa.Column('run_id',sa.String(36),nullable=True,primary_key=False),
        sa.Column('session_id',sa.String(128),nullable=True,primary_key=False),
        sa.Column('turn_id',sa.String(128),nullable=True,primary_key=False),
        sa.Column('started_at',sa.DateTime(timezone=True),nullable=True,primary_key=False),
        sa.Column('terminal_at',sa.DateTime(timezone=True),nullable=True,primary_key=False),
        sa.Column('outcome',sa.String(16),nullable=False,primary_key=False),
        sa.Column('terminal_event_id',sa.String(36),nullable=True,primary_key=False),
        sa.CheckConstraint("domain IN ('agent','retrieval','tool')"),
    )
    op.create_index('ix_execution_activity_run','execution_activity_record',['environment', 'run_id', 'execution_id'])
    # Runtime append-only is also enforced against accidental owner UPDATE/DELETE.
    if op.get_bind().dialect.name=='postgresql':
        op.execute("""CREATE FUNCTION slopanoc_reject_audit_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Execution audit is append-only'; END $$""")
        op.execute("CREATE TRIGGER execution_audit_immutable BEFORE UPDATE OR DELETE ON observability_critical_audit FOR EACH ROW EXECUTE FUNCTION slopanoc_reject_audit_mutation()")

def downgrade():
    if op.get_bind().dialect.name=="postgresql":
        op.execute("DROP TRIGGER execution_audit_immutable ON observability_critical_audit")
        op.execute("DROP FUNCTION slopanoc_reject_audit_mutation()")
    op.drop_table('execution_activity_record')
    op.drop_table('execution_audit_timeline')
    op.drop_table('execution_action_record')
