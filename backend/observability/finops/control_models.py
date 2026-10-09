"""M14-owned append-oriented analytical storage, six independent table families."""
from sqlalchemy import String,BigInteger,JSON,ForeignKey,CheckConstraint,Index
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column
from ..models import UTCDateTime

class ControlBase(DeclarativeBase):pass

class DefinitionRow(ControlBase):
    __tablename__='finops_budget_definition_version'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    version: Mapped[str]=mapped_column(String(32),unique=True)
    body: Mapped[dict]=mapped_column(JSON)
    recorded_at: Mapped[object]=mapped_column(UTCDateTime)

class InputRow(ControlBase):
    __tablename__='finops_control_input'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    scope: Mapped[str]=mapped_column(String(64))
    month: Mapped[str]=mapped_column(String(7))
    cutoff: Mapped[object]=mapped_column(UTCDateTime)
    body: Mapped[dict]=mapped_column(JSON)
    __table_args__=(Index('ix_control_input_asof','environment','scope','month','cutoff'),)

class GenerationRow(ControlBase):
    __tablename__='finops_control_generation'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    definitions: Mapped[str]=mapped_column(ForeignKey('finops_budget_definition_version.identity'))
    inputs: Mapped[str]=mapped_column(ForeignKey('finops_control_input.identity'))
    environment: Mapped[str]=mapped_column(String(16))
    scope: Mapped[str]=mapped_column(String(64))
    month: Mapped[str]=mapped_column(String(7))
    kind: Mapped[str]=mapped_column(String(16))
    issue: Mapped[object]=mapped_column(UTCDateTime)
    body: Mapped[dict]=mapped_column(JSON)
    published_at: Mapped[object]=mapped_column(UTCDateTime)
    __table_args__=(CheckConstraint("kind IN ('BUDGET','FORECAST','ANOMALY')"),Index('ix_control_history','environment','scope','month','kind','issue'))

class BacktestRow(ControlBase):
    __tablename__='finops_forecast_backtest_generation'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    scope: Mapped[str]=mapped_column(String(64))
    forecast: Mapped[str]=mapped_column(String(64))
    truth: Mapped[str]=mapped_column(String(64))
    scoring_version: Mapped[str]=mapped_column(String(32))
    known_at: Mapped[object]=mapped_column(UTCDateTime)
    body: Mapped[dict]=mapped_column(JSON)
    __table_args__=(Index('ix_control_backtest_asof','environment','scope','known_at'),)

class PublicationRow(ControlBase):
    __tablename__='finops_control_publication'
    key: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    scope: Mapped[str]=mapped_column(String(64))
    month: Mapped[str]=mapped_column(String(7))
    kind: Mapped[str]=mapped_column(String(16))
    selection: Mapped[str]=mapped_column(String(128),default='TOTAL:EUR')
    revision: Mapped[int]=mapped_column(BigInteger,default=0)
    generation: Mapped[str|None]=mapped_column(ForeignKey('finops_control_generation.identity'))
    last_attempt: Mapped[object|None]=mapped_column(UTCDateTime)
    last_success: Mapped[object|None]=mapped_column(UTCDateTime)
    error: Mapped[str|None]=mapped_column(String(32))
    __table_args__=(CheckConstraint('revision >= 0'),CheckConstraint("kind IN ('BUDGET','FORECAST','ANOMALY')"),
        CheckConstraint("environment IN ('local','development','staging','production')"),Index('ux_control_publication','environment','scope','month','kind','selection',unique=True))

class TransitionRow(ControlBase):
    __tablename__='finops_anomaly_transition'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    anomaly: Mapped[str]=mapped_column(String(64))
    generation: Mapped[str]=mapped_column(ForeignKey('finops_control_generation.identity'))
    previous: Mapped[str|None]=mapped_column(String(64))
    state: Mapped[str]=mapped_column(String(16))
    at: Mapped[object]=mapped_column(UTCDateTime)
    __table_args__=(CheckConstraint("state IN ('OBSERVED','ACTIVE','RESOLVED')"),Index('ix_control_anomaly_history','anomaly','at'))
