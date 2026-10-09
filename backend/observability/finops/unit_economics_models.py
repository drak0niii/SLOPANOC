"""Independent application-owned derived state; no M9–M12 schema alteration."""
from sqlalchemy import String, BigInteger, JSON, ForeignKey, CheckConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from ..models import UTCDateTime

class UnitEconomicsBase(DeclarativeBase):
    pass

class DefinitionRow(UnitEconomicsBase):
    __tablename__='finops_unit_definition_version'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    version: Mapped[str]=mapped_column(String(32),unique=True)
    body: Mapped[dict]=mapped_column(JSON)

class DriverRow(UnitEconomicsBase):
    __tablename__='finops_unit_driver_version'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    scope: Mapped[str]=mapped_column(String(64))
    version: Mapped[str]=mapped_column(String(32))
    body: Mapped[dict]=mapped_column(JSON)
    __table_args__=(Index('ux_unit_driver_version','environment','scope','version',unique=True),)

class SnapshotRow(UnitEconomicsBase):
    __tablename__='finops_unit_denominator_snapshot'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    period: Mapped[str]=mapped_column(String(10))
    source: Mapped[str]=mapped_column(String(8))
    body: Mapped[dict]=mapped_column(JSON)
    captured_at: Mapped[object]=mapped_column(UTCDateTime)
    __table_args__=(CheckConstraint("source IN ('M9','M10')"),Index('ix_unit_snapshot_period','environment','period','source'))

class GenerationRow(UnitEconomicsBase):
    __tablename__='finops_unit_economics_generation'
    identity: Mapped[str]=mapped_column(String(64),primary_key=True)
    definitions: Mapped[str]=mapped_column(ForeignKey('finops_unit_definition_version.identity'))
    drivers: Mapped[str]=mapped_column(ForeignKey('finops_unit_driver_version.identity'))
    # M12 immutable reference stored without owning its schema/metadata.
    reconciliation: Mapped[str]=mapped_column(String(64))
    turn_snapshot: Mapped[str|None]=mapped_column(ForeignKey('finops_unit_denominator_snapshot.identity'))
    model_snapshot: Mapped[str|None]=mapped_column(ForeignKey('finops_unit_denominator_snapshot.identity'))
    body: Mapped[dict]=mapped_column(JSON)
    published_at: Mapped[object]=mapped_column(UTCDateTime)

class PublicationRow(UnitEconomicsBase):
    __tablename__='finops_unit_economics_publication'
    key: Mapped[str]=mapped_column(String(64),primary_key=True)
    environment: Mapped[str]=mapped_column(String(16))
    scope: Mapped[str]=mapped_column(String(64))
    period: Mapped[str]=mapped_column(String(10))
    kind: Mapped[str]=mapped_column(String(16))
    revision: Mapped[int]=mapped_column(BigInteger,default=0)
    snapshot: Mapped[str|None]=mapped_column(ForeignKey('finops_unit_denominator_snapshot.identity'))
    generation: Mapped[str|None]=mapped_column(ForeignKey('finops_unit_economics_generation.identity'))
    last_attempt: Mapped[object|None]=mapped_column(UTCDateTime)
    last_success: Mapped[object|None]=mapped_column(UTCDateTime)
    error: Mapped[str|None]=mapped_column(String(32))
    __table_args__=(CheckConstraint('revision >= 0'),CheckConstraint("kind IN ('M9','M10','ECONOMICS')"),
        Index('ux_unit_publication','environment','scope','period','kind',unique=True))
