"""M10 dedicated runtime usage accounting; no shared database application."""
from alembic import op
import sqlalchemy as sa
revision = 'c10a8f6e2d41'
down_revision = 'b37e90a14c62'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('finops_accounting_source',
        sa.Column('environment', sa.String(length=16), nullable=False, primary_key=True),
        sa.Column('coverage_start', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('last_observation', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('last_persistence', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('admission_failures', sa.BigInteger(), nullable=False, primary_key=False),
    )
    op.create_table('finops_runtime_attempt',
        sa.Column('environment', sa.String(length=16), nullable=False, primary_key=True),
        sa.Column('attempt_id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('identity', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('state', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('admitted_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('provider_finished_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('deadline_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('gap_reason', sa.String(length=32), nullable=True, primary_key=False),
        sa.CheckConstraint("environment IN ('local','development','staging','production')"),
        sa.CheckConstraint("state IN ('ADMITTED','STARTED','PROVIDER_FINISHED','USAGE_CAPTURED','NOT_STARTED','FINAL_CAPTURE_PENDING','FINAL_CAPTURE_FAILED','OUTCOME_UNKNOWN','CONFLICT')"),
    )
    op.create_index('ix_finops_attempt_debt', 'finops_runtime_attempt', ['environment', 'state', 'deadline_at'], unique=False)
    op.create_table('finops_usage_bucket',
        sa.Column('environment', sa.String(length=16), nullable=False, primary_key=True),
        sa.Column('at', sa.DateTime(timezone=True), nullable=False, primary_key=True),
        sa.Column('provider', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('model', sa.String(length=128), nullable=False, primary_key=True),
        sa.Column('agent', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('workload', sa.String(length=32), nullable=False, primary_key=True),
        sa.Column('operation', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('operation_type', sa.String(length=16), nullable=False, primary_key=True),
        sa.Column('good', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('bad', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('quantity_known', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('quantity_partial', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('input_tokens', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('input_tokens_known', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('output_tokens', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('output_tokens_known', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('candidate_tokens', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('candidate_tokens_known', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('cached_tokens', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('cached_tokens_known', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('total_tokens', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('total_tokens_known', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('thought_tokens', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('thought_tokens_known', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('tool_input_tokens', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('tool_input_tokens_known', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('billable_characters', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('billable_characters_known', sa.BigInteger(), nullable=False, primary_key=False),
        sa.CheckConstraint('good >= 0 AND bad >= 0 AND quantity_known >= 0 AND quantity_partial >= 0'),
    )
    op.create_index('ix_finops_bucket_time', 'finops_usage_bucket', ['environment', 'at'], unique=False)
    op.create_table('ai_usage_ledger',
        sa.Column('event_id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('environment', sa.String(length=16), nullable=False, primary_key=False),
        sa.Column('attempt_id', sa.String(length=36), nullable=False, primary_key=False),
        sa.Column('payload_hash', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('record_kind', sa.String(length=16), nullable=False, primary_key=False),
        sa.Column('original_event_id', sa.String(length=36), nullable=True, primary_key=False),
        sa.Column('correction_reason', sa.String(length=32), nullable=True, primary_key=False),
        sa.Column('correction_process', sa.String(length=32), nullable=True, primary_key=False),
        sa.Column('metadata_safe', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('observed_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('input_tokens', sa.BigInteger(), nullable=True, primary_key=False),
        sa.Column('output_tokens', sa.BigInteger(), nullable=True, primary_key=False),
        sa.Column('candidate_tokens', sa.BigInteger(), nullable=True, primary_key=False),
        sa.Column('cached_tokens', sa.BigInteger(), nullable=True, primary_key=False),
        sa.Column('total_tokens', sa.BigInteger(), nullable=True, primary_key=False),
        sa.Column('thought_tokens', sa.BigInteger(), nullable=True, primary_key=False),
        sa.Column('tool_input_tokens', sa.BigInteger(), nullable=True, primary_key=False),
        sa.Column('billable_characters', sa.BigInteger(), nullable=True, primary_key=False),
        sa.CheckConstraint("record_kind IN ('BASE','ADJUSTMENT')"),
        sa.CheckConstraint("environment IN ('local','development','staging','production')"),
        sa.CheckConstraint("record_kind = 'ADJUSTMENT' OR input_tokens IS NULL OR input_tokens >= 0"),
        sa.CheckConstraint("(record_kind = 'BASE' AND original_event_id IS NULL) OR (record_kind = 'ADJUSTMENT' AND original_event_id IS NOT NULL)"),
        sa.CheckConstraint("record_kind = 'ADJUSTMENT' OR output_tokens IS NULL OR output_tokens >= 0"),
        sa.CheckConstraint("record_kind = 'ADJUSTMENT' OR candidate_tokens IS NULL OR candidate_tokens >= 0"),
        sa.CheckConstraint("record_kind = 'ADJUSTMENT' OR cached_tokens IS NULL OR cached_tokens >= 0"),
        sa.CheckConstraint("record_kind = 'ADJUSTMENT' OR total_tokens IS NULL OR total_tokens >= 0"),
        sa.CheckConstraint("record_kind = 'ADJUSTMENT' OR tool_input_tokens IS NULL OR tool_input_tokens >= 0"),
        sa.CheckConstraint("record_kind = 'ADJUSTMENT' OR thought_tokens IS NULL OR thought_tokens >= 0"),
        sa.CheckConstraint("record_kind = 'ADJUSTMENT' OR billable_characters IS NULL OR billable_characters >= 0"),
        sa.ForeignKeyConstraint(['environment', 'attempt_id'], ['finops_runtime_attempt.environment', 'finops_runtime_attempt.attempt_id']),
        sa.ForeignKeyConstraint(['original_event_id'], ['ai_usage_ledger.event_id']),
    )
    op.create_index('ix_finops_ledger_time', 'ai_usage_ledger', ['environment', 'observed_at', 'event_id'], unique=False)
    op.create_index('ux_finops_base_attempt', 'ai_usage_ledger', ['environment', 'attempt_id'], unique=True, postgresql_where=sa.text("record_kind = 'BASE'"), sqlite_where=sa.text("record_kind = 'BASE'"))
    op.create_table('finops_usage_inbox',
        sa.Column('event_id', sa.String(length=36), nullable=False, primary_key=True),
        sa.Column('environment', sa.String(length=16), nullable=False, primary_key=False),
        sa.Column('attempt_id', sa.String(length=36), nullable=False, primary_key=False),
        sa.Column('payload', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('payload_hash', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('delivered', sa.Boolean(), nullable=False, primary_key=False),
        sa.Column('attempts', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('next_attempt_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('exhausted', sa.Boolean(), nullable=False, primary_key=False),
        sa.CheckConstraint('attempts >= 0'),
        sa.ForeignKeyConstraint(['environment', 'attempt_id'], ['finops_runtime_attempt.environment', 'finops_runtime_attempt.attempt_id']),
    )
    op.create_index('ix_finops_inbox_pending', 'finops_usage_inbox', ['delivered', 'exhausted', 'next_attempt_at'], unique=False)

def downgrade():
    op.drop_table('finops_usage_inbox')
    op.drop_table('ai_usage_ledger')
    op.drop_table('finops_usage_bucket')
    op.drop_table('finops_runtime_attempt')
    op.drop_table('finops_accounting_source')
