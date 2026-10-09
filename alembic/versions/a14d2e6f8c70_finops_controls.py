"""M14 financial controls; additive, source schemas untouched."""
from alembic import op
import sqlalchemy as sa
revision='a14d2e6f8c70'
down_revision='f13a7c9d2e61'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('finops_budget_definition_version',
        sa.Column('identity',sa.String(64),primary_key=True,nullable=False),
        sa.Column('version',sa.String(32),primary_key=False,nullable=False,unique=True),
        sa.Column('body',sa.JSON(),primary_key=False,nullable=False),
        sa.Column('recorded_at',sa.DateTime(timezone=True),primary_key=False,nullable=False),
    )
    op.create_table('finops_control_input',
        sa.Column('identity',sa.String(64),primary_key=True,nullable=False),
        sa.Column('environment',sa.String(16),primary_key=False,nullable=False),
        sa.Column('scope',sa.String(64),primary_key=False,nullable=False),
        sa.Column('month',sa.String(7),primary_key=False,nullable=False),
        sa.Column('cutoff',sa.DateTime(timezone=True),primary_key=False,nullable=False),
        sa.Column('body',sa.JSON(),primary_key=False,nullable=False),
    )
    op.create_index('ix_control_input_asof','finops_control_input',['environment', 'scope', 'month', 'cutoff'],unique=False)
    op.create_table('finops_forecast_backtest_generation',
        sa.Column('identity',sa.String(64),primary_key=True,nullable=False),
        sa.Column('environment',sa.String(16),primary_key=False,nullable=False),
        sa.Column('scope',sa.String(64),primary_key=False,nullable=False),
        sa.Column('forecast',sa.String(64),primary_key=False,nullable=False),
        sa.Column('truth',sa.String(64),primary_key=False,nullable=False),
        sa.Column('scoring_version',sa.String(32),primary_key=False,nullable=False),
        sa.Column('known_at',sa.DateTime(timezone=True),primary_key=False,nullable=False),
        sa.Column('body',sa.JSON(),primary_key=False,nullable=False),
    )
    op.create_index('ix_control_backtest_asof','finops_forecast_backtest_generation',['environment', 'scope', 'known_at'],unique=False)
    op.create_table('finops_control_generation',
        sa.Column('identity',sa.String(64),primary_key=True,nullable=False),
        sa.Column('definitions',sa.String(64),sa.ForeignKey('finops_budget_definition_version.identity'),primary_key=False,nullable=False),
        sa.Column('inputs',sa.String(64),sa.ForeignKey('finops_control_input.identity'),primary_key=False,nullable=False),
        sa.Column('environment',sa.String(16),primary_key=False,nullable=False),
        sa.Column('scope',sa.String(64),primary_key=False,nullable=False),
        sa.Column('month',sa.String(7),primary_key=False,nullable=False),
        sa.Column('kind',sa.String(16),primary_key=False,nullable=False),
        sa.Column('issue',sa.DateTime(timezone=True),primary_key=False,nullable=False),
        sa.Column('body',sa.JSON(),primary_key=False,nullable=False),
        sa.Column('published_at',sa.DateTime(timezone=True),primary_key=False,nullable=False),
        sa.CheckConstraint("kind IN ('BUDGET','FORECAST','ANOMALY')"),
    )
    op.create_index('ix_control_history','finops_control_generation',['environment', 'scope', 'month', 'kind', 'issue'],unique=False)
    op.create_table('finops_anomaly_transition',
        sa.Column('identity',sa.String(64),primary_key=True,nullable=False),
        sa.Column('anomaly',sa.String(64),primary_key=False,nullable=False),
        sa.Column('generation',sa.String(64),sa.ForeignKey('finops_control_generation.identity'),primary_key=False,nullable=False),
        sa.Column('previous',sa.String(64),primary_key=False,nullable=True),
        sa.Column('state',sa.String(16),primary_key=False,nullable=False),
        sa.Column('at',sa.DateTime(timezone=True),primary_key=False,nullable=False),
        sa.CheckConstraint("state IN ('OBSERVED','ACTIVE','RESOLVED')"),
    )
    op.create_index('ix_control_anomaly_history','finops_anomaly_transition',['anomaly', 'at'],unique=False)
    op.create_table('finops_control_publication',
        sa.Column('key',sa.String(64),primary_key=True,nullable=False),
        sa.Column('environment',sa.String(16),primary_key=False,nullable=False),
        sa.Column('scope',sa.String(64),primary_key=False,nullable=False),
        sa.Column('month',sa.String(7),primary_key=False,nullable=False),
        sa.Column('kind',sa.String(16),primary_key=False,nullable=False),
        sa.Column('selection',sa.String(128),primary_key=False,nullable=False),
        sa.Column('revision',sa.BigInteger(),primary_key=False,nullable=False),
        sa.Column('generation',sa.String(64),sa.ForeignKey('finops_control_generation.identity'),primary_key=False,nullable=True),
        sa.Column('last_attempt',sa.DateTime(timezone=True),primary_key=False,nullable=True),
        sa.Column('last_success',sa.DateTime(timezone=True),primary_key=False,nullable=True),
        sa.Column('error',sa.String(32),primary_key=False,nullable=True),
        sa.CheckConstraint("environment IN ('local','development','staging','production')"),
        sa.CheckConstraint("kind IN ('BUDGET','FORECAST','ANOMALY')"),
        sa.CheckConstraint('revision >= 0'),
    )
    op.create_index('ux_control_publication','finops_control_publication',['environment', 'scope', 'month', 'kind', 'selection'],unique=True)

def downgrade():
    op.drop_table('finops_control_publication')
    op.drop_table('finops_anomaly_transition')
    op.drop_table('finops_control_generation')
    op.drop_table('finops_forecast_backtest_generation')
    op.drop_table('finops_control_input')
    op.drop_table('finops_budget_definition_version')
