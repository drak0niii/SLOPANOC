"""M12 additive derived reconciliation state; frozen application-only schema."""
from alembic import op
import sqlalchemy as sa
revision = 'e12c6d8a4b50'
down_revision = 'd11b4a8c9e32'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('finops_reconciliation_input',
        sa.Column('identity', sa.String(64), nullable=False, primary_key=True),
        sa.Column('environment', sa.String(16), nullable=False, primary_key=False),
        sa.CheckConstraint("environment IN ('local','development','staging','production')"),
        sa.Column('scope', sa.String(64), nullable=False, primary_key=False),
        sa.Column('period', sa.String(10), nullable=False, primary_key=False),
        sa.Column('kind', sa.String(16), nullable=False, primary_key=False),
        sa.Column('source_generation', sa.String(64), nullable=True, primary_key=False),
        sa.Column('body', sa.JSON(), nullable=False, primary_key=False),
        sa.CheckConstraint("kind IN ('BILLING','RUNTIME','PRICING')"),
    )
    op.create_index('ix_reconciliation_inputs', 'finops_reconciliation_input', ['environment', 'scope', 'period', 'kind', 'source_generation'], unique=False)
    op.create_table('finops_reconciliation_registry',
        sa.Column('identity', sa.String(64), nullable=False, primary_key=True),
        sa.Column('environment', sa.String(16), nullable=False, primary_key=False),
        sa.CheckConstraint("environment IN ('local','development','staging','production')"),
        sa.Column('version', sa.String(32), nullable=False, primary_key=False),
        sa.Column('ownership_version', sa.String(32), nullable=False, primary_key=False),
        sa.Column('body', sa.JSON(), nullable=False, primary_key=False),
    )
    op.create_index('ux_reconciliation_registry_version', 'finops_reconciliation_registry', ['environment', 'version', 'ownership_version'], unique=True)
    op.create_table('finops_reconciliation_generation',
        sa.Column('identity', sa.String(64), nullable=False, primary_key=True),
        sa.Column('registry', sa.String(64), nullable=False, primary_key=False),
        sa.Column('runtime_input', sa.String(64), nullable=False, primary_key=False),
        sa.Column('billing_input', sa.String(64), nullable=False, primary_key=False),
        sa.Column('pricing_input', sa.String(64), nullable=False, primary_key=False),
        sa.Column('body', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.ForeignKeyConstraint(['registry'], ['finops_reconciliation_registry.identity']),
        sa.ForeignKeyConstraint(['billing_input'], ['finops_reconciliation_input.identity']),
        sa.ForeignKeyConstraint(['pricing_input'], ['finops_reconciliation_input.identity']),
        sa.ForeignKeyConstraint(['runtime_input'], ['finops_reconciliation_input.identity']),
    )
    op.create_table('finops_reconciliation_state',
        sa.Column('key', sa.String(64), nullable=False, primary_key=True),
        sa.Column('environment', sa.String(16), nullable=False, primary_key=False),
        sa.CheckConstraint("environment IN ('local','development','staging','production')"),
        sa.Column('scope', sa.String(64), nullable=False, primary_key=False),
        sa.Column('period', sa.String(10), nullable=False, primary_key=False),
        sa.Column('revision', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('generation', sa.String(64), nullable=True, primary_key=False),
        sa.Column('last_attempt', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('last_success', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('error', sa.String(32), nullable=True, primary_key=False),
        sa.CheckConstraint('revision >= 0'),
        sa.ForeignKeyConstraint(['generation'], ['finops_reconciliation_generation.identity']),
    )
    op.create_index('ux_reconciliation_period', 'finops_reconciliation_state', ['environment', 'scope', 'period'], unique=True)

def downgrade():
    op.drop_table('finops_reconciliation_state')
    op.drop_table('finops_reconciliation_generation')
    op.drop_table('finops_reconciliation_registry')
    op.drop_table('finops_reconciliation_input')
