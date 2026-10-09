"""M13 analytical contracts. No source accounting or governance authority."""
from enum import Enum
from typing import Annotated, Literal
from datetime import datetime, timezone
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from pydantic import Field, AfterValidator, AwareDatetime, model_validator
from ..schemas import Contract, Count
from .billing_contracts import decimal, money, digest, SourceError

Hash = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Version = Annotated[str, Field(pattern=r'^[A-Za-z0-9_.-]{1,32}$')]
Environment = Literal['local', 'development', 'staging', 'production']
Money = Annotated[str, AfterValidator(lambda v: money(v))]
ALGORITHM = 'unit-economics-v1'
BEGIN = datetime(2020, 1, 1, tzinfo=timezone.utc)
END = datetime(2100, 1, 1, tzinfo=timezone.utc)

class State(str, Enum):
    AVAILABLE = 'AVAILABLE'
    PARTIAL = 'PARTIAL'
    INSUFFICIENT_DATA = 'INSUFFICIENT_DATA'
    FINANCIAL_SCOPE_UNAVAILABLE = 'FINANCIAL_SCOPE_UNAVAILABLE'
    DENOMINATOR_UNAVAILABLE = 'DENOMINATOR_UNAVAILABLE'
    RECONCILIATION_INCOMPLETE = 'RECONCILIATION_INCOMPLETE'
    SOURCE_STALE = 'SOURCE_STALE'
    DRIVER_UNAVAILABLE = 'DRIVER_UNAVAILABLE'
    CURRENCY_UNAVAILABLE = 'CURRENCY_UNAVAILABLE'
    DEFINED_NOT_AVAILABLE = 'DEFINED_NOT_AVAILABLE'

class Policy(Contract):
    version: Literal['unit-policy-v1'] = 'unit-policy-v1'
    cadence_seconds: Count = Field(default=60, ge=1, le=300)
    job_seconds: Count = Field(default=60, ge=1, le=60)
    max_rows: Count = Field(default=100000, ge=1, le=100000)
    max_buckets: Count = Field(default=10000, ge=1, le=10000)
    max_bytes: Count = Field(default=134217728, ge=1024, le=134217728)
    retention_days: Count = Field(default=731, ge=1, le=731)
    cycle_partitions: Count = Field(default=2, ge=1, le=32)
    # Deployment attestation only; never a browser/header/model claim.
    business_population_proven: bool = False

class Coverage(Contract):
    reconciliation: Money | None = None
    numerator_scope: Money | None = None
    denominator: Money | None = None
    shared_driver: Money | None = None
    currency: Money | None = None
    mapping: Money | None = None
    unallocated_share: Money | None = None

class OperationalContext(Contract):
    source: Literal['M9_ACCEPTANCE_COHORT'] = 'M9_ACCEPTANCE_COHORT'
    availability_ratio: Money | None = None
    timely_terminal_ratio: Money | None = None
    mean_latency_ms: Money | None = None
    latency_observed: Count = 0
    eligible_turns: Count = 0

class Result(Contract):
    unit_id: Version
    name: str = Field(max_length=80)
    definition: str = Field(max_length=400)
    dimension: str = Field(default='ALL', max_length=128, pattern=r'^(ALL|other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$')
    currency: str = Field(pattern=r'^[A-Z]{3}$')
    state: State
    direct_state: State
    fully_loaded_state: State
    unit_count: Count | None = None
    direct_amount: Money = '0'
    direct_without_unit_scope: Money = '0'
    direct_cost_per_unit: Money | None = None
    distributed_shared: Money = '0'
    undistributed_shared: Money = '0'
    fully_loaded_amount: Money = '0'
    fully_loaded_cost_per_unit: Money | None = None
    unallocated_amount: Money = '0'
    actual_net: Money = '0'
    coverage: Coverage = Coverage()
    denominator_freshness: Literal['FRESH', 'STALE', 'UNAVAILABLE', 'RETAINED'] = 'UNAVAILABLE'
    financial_freshness: Literal['FRESH', 'STALE', 'UNAVAILABLE'] = 'UNAVAILABLE'
    analytical_view: Version
    additive: Literal[False] = False
    price_semantics: Literal['AGGREGATE_AVERAGE', 'YIELD_ADJUSTED_AVERAGE'] = 'AGGREGATE_AVERAGE'
    outcome_source: Literal['M9', 'M10', 'M19', 'ITSM', 'UNAVAILABLE']
    quality_context: Literal['M19_OUTCOMES_UNAVAILABLE'] = 'M19_OUTCOMES_UNAVAILABLE'
    reasons: tuple[State, ...] = ()
    operational_context: OperationalContext | None = None

class Snapshot(Contract):
    schema_version: Literal[1] = 1
    definition_version: Literal[1] = 1
    source: Literal['M9', 'M10']
    environment: Environment
    period: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    source_digest: Hash
    watermark: AwareDatetime
    captured_at: AwareDatetime
    complete: bool
    freshness: Literal['FRESH', 'STALE', 'UNAVAILABLE']
    counts: dict[str, Count | None]
    # Safe joint M10 populations; only bounded taxonomy, never receipt copies.
    buckets: tuple['Population', ...] = Field(default=(), max_length=10000)
    runtime_digest: Hash | None = None
    window_aligned: bool = True
    business_population_proven: bool = False
    coverage_start: AwareDatetime | None = None
    operational_contexts: dict[str,OperationalContext] = Field(default={},max_length=8)
    @model_validator(mode='after')
    def bounded_counts(self):
        from datetime import date
        date.fromisoformat(self.period)
        allowed={'PROVIDER_ATTEMPT','MODEL_OPERATION'} if self.source=='M10' else {
            'ACCEPTED_TURN','AVAILABLE_TURN','TIMELY_TERMINAL_TURN','GENERAL_TURN',
            'TEAMS_LOOKUP_TURN','GOVERNED_TROUBLESHOOTING_TURN','COMPLEX_MULTI_AGENT_TURN','UNKNOWN_TURN'}
        if not set(self.counts)<=allowed or not set(self.operational_contexts)<=allowed or self.source=='M9' and self.buckets or self.source=='M10' and self.operational_contexts:
            raise ValueError('Invalid denominator population')
        return self
    @property
    def identity(self):
        body = self.model_dump(mode='json')
        body.pop('captured_at')
        return digest(body)

class Population(Contract):
    provider: Literal['gcp.vertex_ai','gcp.gemini','other']
    model: str = Field(pattern=r'^(other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$')
    region: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')
    operation_type: Literal['GENERATION','EMBEDDING']
    workload: Literal['USER_TURN','SYSTEM_WARMUP','INGESTION','BACKGROUND','UNKNOWN']
    attempts: Count
    operations: Count
    complete: bool

Snapshot.model_rebuild()

def divide(amount, count):
    if count is None or count == 0:
        return None
    with localcontext() as ctx:
        ctx.prec = 100
        return money((decimal(amount) / Decimal(count)).quantize(Decimal('1e-38'), rounding=ROUND_HALF_EVEN))

def bounded(body, policy):
    import json
    if len(json.dumps(body, separators=(',', ':')).encode()) > policy.max_bytes:
        raise SourceError('BATCH_LIMIT')
    return body
