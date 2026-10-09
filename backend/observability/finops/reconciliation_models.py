"""M12 application-owned derived state. Never source ledger tables."""
from sqlalchemy import String,BigInteger,JSON,ForeignKey,CheckConstraint,Index
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column
from ..models import UTCDateTime

class ReconciliationBase(DeclarativeBase):pass

class RegistryRow(ReconciliationBase):
    __tablename__='finops_reconciliation_registry'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    version: Mapped[str]=mapped_column(String(32))
    ownership_version: Mapped[str]=mapped_column(String(32))
    body: Mapped[dict]=mapped_column(JSON)
    __table_args__=(CheckConstraint("environment IN ('local','development','staging','production')"),Index('ux_reconciliation_registry_version','environment','version','ownership_version',unique=True),)

class InputRow(ReconciliationBase):
    __tablename__='finops_reconciliation_input'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    scope: Mapped[str]=mapped_column(String(64))
    period: Mapped[str]=mapped_column(String(10))
    kind: Mapped[str]=mapped_column(String(16))
    source_generation: Mapped[str|None]=mapped_column(String(64))
    body: Mapped[dict]=mapped_column(JSON)
    __table_args__=(CheckConstraint("environment IN ('local','development','staging','production')"),CheckConstraint("kind IN ('BILLING','RUNTIME','PRICING')"),
        Index('ix_reconciliation_inputs','environment','scope','period','kind','source_generation'))

class GenerationRow(ReconciliationBase):
    __tablename__='finops_reconciliation_generation'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    registry: Mapped[str]=mapped_column(String(64),ForeignKey('finops_reconciliation_registry.identity'))
    runtime_input: Mapped[str]=mapped_column(String(64),ForeignKey('finops_reconciliation_input.identity'))
    billing_input: Mapped[str]=mapped_column(String(64),ForeignKey('finops_reconciliation_input.identity'))
    pricing_input: Mapped[str]=mapped_column(String(64),ForeignKey('finops_reconciliation_input.identity'))
    body: Mapped[dict]=mapped_column(JSON)
    published_at: Mapped[object]=mapped_column(UTCDateTime)

class StateRow(ReconciliationBase):
    __tablename__='finops_reconciliation_state'
    key: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    scope: Mapped[str]=mapped_column(String(64))
    period: Mapped[str]=mapped_column(String(10))
    revision: Mapped[int]=mapped_column(BigInteger,default=0)
    generation: Mapped[str|None]=mapped_column(String(64),ForeignKey('finops_reconciliation_generation.identity'))
    last_attempt: Mapped[object|None]=mapped_column(UTCDateTime)
    last_success: Mapped[object|None]=mapped_column(UTCDateTime)
    error: Mapped[str|None]=mapped_column(String(32))
    __table_args__=(CheckConstraint("environment IN ('local','development','staging','production')"),CheckConstraint('revision >= 0'),Index('ux_reconciliation_period','environment','scope','period',unique=True),)
