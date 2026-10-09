"""M13 frozen additive application-owned analytical persistence."""
from alembic import op
import sqlalchemy as sa
revision = 'f13a7c9d2e61'
down_revision = 'e12c6d8a4b50'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('finops_unit_definition_version',
        sa.Column('identity', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('version', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('body', sa.JSON(), nullable=False, primary_key=False),
        sa.UniqueConstraint(*['version']),
    )
    op.create_table('finops_unit_denominator_snapshot',
        sa.Column('identity', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('environment', sa.String(length=16), nullable=False, primary_key=False),
        sa.Column('period', sa.String(length=10), nullable=False, primary_key=False),
        sa.Column('source', sa.String(length=8), nullable=False, primary_key=False),
        sa.Column('body', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.CheckConstraint("source IN ('M9','M10')"),
    )
    op.create_index('ix_unit_snapshot_period', 'finops_unit_denominator_snapshot', ['environment', 'period', 'source'], unique=False)
    op.create_table('finops_unit_driver_version',
        sa.Column('identity', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('environment', sa.String(length=16), nullable=False, primary_key=False),
        sa.Column('scope', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('version', sa.String(length=32), nullable=False, primary_key=False),
        sa.Column('body', sa.JSON(), nullable=False, primary_key=False),
    )
    op.create_index('ux_unit_driver_version', 'finops_unit_driver_version', ['environment', 'scope', 'version'], unique=True)
    op.create_table('finops_unit_economics_generation',
        sa.Column('identity', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('definitions', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('drivers', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('reconciliation', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('turn_snapshot', sa.String(length=64), nullable=True, primary_key=False),
        sa.Column('model_snapshot', sa.String(length=64), nullable=True, primary_key=False),
        sa.Column('body', sa.JSON(), nullable=False, primary_key=False),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=False, primary_key=False),
        sa.ForeignKeyConstraint(['definitions'], ['finops_unit_definition_version.identity']),
        sa.ForeignKeyConstraint(['drivers'], ['finops_unit_driver_version.identity']),
        sa.ForeignKeyConstraint(['model_snapshot'], ['finops_unit_denominator_snapshot.identity']),
        sa.ForeignKeyConstraint(['turn_snapshot'], ['finops_unit_denominator_snapshot.identity']),
    )
    op.create_table('finops_unit_economics_publication',
        sa.Column('key', sa.String(length=64), nullable=False, primary_key=True),
        sa.Column('environment', sa.String(length=16), nullable=False, primary_key=False),
        sa.Column('scope', sa.String(length=64), nullable=False, primary_key=False),
        sa.Column('period', sa.String(length=10), nullable=False, primary_key=False),
        sa.Column('kind', sa.String(length=16), nullable=False, primary_key=False),
        sa.Column('revision', sa.BigInteger(), nullable=False, primary_key=False),
        sa.Column('snapshot', sa.String(length=64), nullable=True, primary_key=False),
        sa.Column('generation', sa.String(length=64), nullable=True, primary_key=False),
        sa.Column('last_attempt', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('last_success', sa.DateTime(timezone=True), nullable=True, primary_key=False),
        sa.Column('error', sa.String(length=32), nullable=True, primary_key=False),
        sa.CheckConstraint("kind IN ('M9','M10','ECONOMICS')"),
        sa.CheckConstraint('revision >= 0'),
        sa.ForeignKeyConstraint(['generation'], ['finops_unit_economics_generation.identity']),
        sa.ForeignKeyConstraint(['snapshot'], ['finops_unit_denominator_snapshot.identity']),
    )
    op.create_index('ux_unit_publication', 'finops_unit_economics_publication', ['environment', 'scope', 'period', 'kind'], unique=True)

def downgrade():
    op.drop_table('finops_unit_economics_publication')
    op.drop_table('finops_unit_economics_generation')
    op.drop_table('finops_unit_driver_version')
    op.drop_table('finops_unit_denominator_snapshot')
    op.drop_table('finops_unit_definition_version')
