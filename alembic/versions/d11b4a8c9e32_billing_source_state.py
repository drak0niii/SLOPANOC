"""M11 operational financial state only; no billing warehouse or shared application."""
from alembic import op
import sqlalchemy as sa
revision = 'd11b4a8c9e32'
down_revision = 'c10a8f6e2d41'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('finops_billing_publication',
        sa.Column('generation', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('state_key', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('schema_version', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('mapping_version', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('window_start', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('window_end', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('extracted_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('export_time', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('price_from', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('price_to', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('row_count', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('late_rows', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('correction_rows', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('unmapped_rows', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('evidence_rows', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('controls', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.Column('status', sa.String(length=16), nullable=False, primary_key=False),
        sa.CheckConstraint("status = 'PUBLISHED'"),
        sa.CheckConstraint('row_count >= 0 AND late_rows >= 0 AND correction_rows >= 0 AND unmapped_rows >= 0'),
        sa.CheckConstraint('schema_version = 1'),
    )
    op.create_index('ix_billing_publication_history', 'finops_billing_publication', ['state_key', 'published_at'], unique=False)
    op.create_table('finops_billing_ingest_state',
        sa.Column('key', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('environment', sa.String(length=16), nullable=False, primary_key=False),
        sa.Column('source', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('scope', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('partition', sa.String(length=10), nullable=False, primary_key=False),
        sa.Column('mode', sa.String(length=16), nullable=False, primary_key=False),
        sa.Column('revision', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('generation', sa.String(length=64), nullable=True, primary_key=False),
        sa.Column('last_success', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('last_attempt', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('error', sa.String(length=32), nullable=True, primary_key=False),
        sa.CheckConstraint("environment IN ('local','development','staging','production')"),
        sa.CheckConstraint('revision >= 0'),
        sa.CheckConstraint("mode IN ('TEST_FIXTURE','BIGQUERY')"),
        sa.CheckConstraint("source IN ('DETAILED_BILLING','PRICING_EXPORT','FOCUS')"),
        sa.ForeignKeyConstraint(['generation'], ['finops_billing_publication.generation']),
    )
    op.create_index('ix_billing_state_scope', 'finops_billing_ingest_state', ['environment', 'source', 'scope', 'partition'], unique=True)
    op.create_table('finops_billing_projection_cache',
        sa.Column('generation', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('summaries', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('prices', sa.JSON(), nullable=False, primary_key=False),
        sa.ForeignKeyConstraint(['generation'], ['finops_billing_publication.generation']),
    )

def downgrade():
    op.drop_table('finops_billing_projection_cache')
    op.drop_table('finops_billing_ingest_state')
    op.drop_table('finops_billing_publication')
