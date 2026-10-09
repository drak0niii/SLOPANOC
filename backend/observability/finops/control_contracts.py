"""M14 analytical contracts. Financial truth and operational authority remain upstream."""
from datetime import date, datetime, timezone
from typing import Literal, Annotated
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from pydantic import StrictInt, Field, AwareDatetime, AfterValidator, model_validator,field_validator
from ..schemas import Contract, Count
from .billing_contracts import decimal, money, digest, SourceError
from .unit_economics_contracts import Hash, Version, Environment

Money = Annotated[str, Field(strict=True), AfterValidator(money)]

def ratio_value(value):
    d=Decimal(value)
    if not d.is_finite() or abs(d)>=Decimal('1e100') or d.as_tuple().exponent < -38:raise ValueError('Invalid ratio')
    return format(d,'f')
Ratio=Annotated[str,Field(strict=True,max_length=140),AfterValidator(ratio_value)]
DayState = Literal['ELIGIBLE_COMPLETE','OPEN','TOO_RECENT','STALE','MISSING','PARTIAL','EXCLUDED']
KINDS = ('BUDGET','FORECAST','ANOMALY')

def quotient(a,b):
    if decimal(b)==0:return None
    with localcontext() as c:
        c.prec=200
        return ratio_value(format((decimal(a)/decimal(b)).quantize(Decimal('1e-38'),rounding=ROUND_HALF_EVEN),'f'))

def rounded(value):
    with localcontext() as c:
        c.prec=100
        return money(decimal(value).quantize(Decimal('1e-38'),rounding=ROUND_HALF_EVEN))

def total(values):
    with localcontext() as c:
        c.prec=100
        return money(sum(map(decimal,values),Decimal(0)))

def month_bounds(month):
    start=date.fromisoformat(month+'-01')
    return start,date(start.year+1,1,1) if start.month==12 else date(start.year,start.month+1,1)

class Policy(Contract):
    version: Version='control-policy-v1'
    recorded_at: AwareDatetime=datetime(2026,1,1,tzinfo=timezone.utc)
    status: Literal['PROVISIONAL']='PROVISIONAL'
    maturity_hours: StrictInt=Field(default=48,ge=48,le=168)
    min_runrate_days: StrictInt=Field(default=7,ge=7,le=31)
    seasonal_days: StrictInt=Field(default=56,ge=56,le=365)
    baseline_days: StrictInt=Field(default=28,ge=28,le=90)
    observability_binding: Hash|None=None
    observability_ownership: tuple[Hash,...]=Field(default=(),max_length=1000)
    observability_population_complete: bool=False
    thresholds: tuple[Money,Money,Money,Money]=('0.5','0.8','0.9','1')
    scenario_fraction: Money='0.25'
    max_wape: Money='0.25'
    max_bias: Money='0.15'
    seasonal_improvement: Money='0.10'
    min_folds: StrictInt=Field(default=6,ge=6,le=64)
    min_months: StrictInt=Field(default=2,ge=2,le=24)
    noise_absolute_eur: Money='1'
    noise_relative: Money='0.01'
    watch_absolute_eur: Money='25'
    watch_relative: Money='0.25'
    watch_score: Money='3.5'
    high_absolute_eur: Money='50'
    high_relative: Money='0.5'
    high_score: Money='6'
    critical_absolute_eur: Money='500'
    critical_relative: Money='1'
    critical_score: Money='8'
    unallocated_ratio_floor: Money='0.10'
    current_source_stale_seconds: StrictInt=Field(default=259200,ge=3600,le=604800)
    min_unit_population: StrictInt=Field(default=100,ge=100,le=2**63-1)
    usage_floor: Money='1000'
    call_floor: Money='10'
    max_days: StrictInt=Field(default=731,ge=56,le=731)
    max_features: StrictInt=Field(default=100000,ge=1,le=100000)
    max_outputs: StrictInt=Field(default=10000,ge=1,le=10000)
    max_bytes: StrictInt=Field(default=134217728,ge=1024,le=134217728)
    job_seconds: StrictInt=Field(default=60,ge=1,le=60)
    cycle_partitions: StrictInt=Field(default=2,ge=1,le=2)
    cadence_seconds: StrictInt=Field(default=3600,ge=3600,le=86400)
    forecast_hour_utc: StrictInt=Field(default=6,ge=0,le=23)
    max_backtest_origins: StrictInt=Field(default=64,ge=1,le=64)
    @model_validator(mode='after')
    def valid(self):
        if self.observability_population_complete and (not self.observability_binding or not self.observability_ownership):raise ValueError('Missing observability ownership proof')
        t=list(map(decimal,self.thresholds))
        if t!=sorted(set(t)) or t[0]<=0 or t[-1]!=1:raise ValueError('Invalid thresholds')
        if decimal(self.scenario_fraction)>1:raise ValueError('Invalid scenario policy')
        for name in ('noise_absolute_eur','watch_absolute_eur','high_absolute_eur','critical_absolute_eur','watch_score','high_score','critical_score','usage_floor','call_floor'):
            if decimal(getattr(self,name))<=0:raise ValueError('Invalid significance floor')
        for name in ('scenario_fraction','max_wape','max_bias','seasonal_improvement','noise_relative','watch_relative','high_relative','critical_relative'):
            if decimal(getattr(self,name))<0:raise ValueError('Invalid policy')
        return self
    @property
    def identity(self):return digest(self.model_dump(mode='json'))

class ModelDimension(Contract):
    provider: Literal['gcp.vertex_ai','gcp.gemini','other']
    model: str=Field(pattern=r'^(other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$')

class Day(Contract):
    day: date
    generation: Hash|None=None
    unit_generation: Hash|None=None
    published_at: AwareDatetime|None=None
    state: DayState='MISSING'
    values: dict[str,Money]=Field(default_factory=dict,max_length=1000)
    coverage: Money|None=None
    unallocated: dict[str,Money]=Field(default_factory=dict,max_length=100)
    unallocated_ratio: dict[str,Money]=Field(default_factory=dict,max_length=100)
    units: dict[str,Money]=Field(default_factory=dict,max_length=100)
    unit_counts: dict[str,Count]=Field(default_factory=dict,max_length=100)
    usage: dict[str,Money]=Field(default_factory=dict,max_length=100)
    models: dict[Version,ModelDimension]=Field(default_factory=dict,max_length=1000)
    mix: dict[str,tuple[Money,Money]]=Field(default_factory=dict,max_length=4)
    mix_proven: bool=False
    corrections: Count=0
    mapping_version: Version|None=None
    # Safe numeric evidence only, exact compatible quantity/gross rate groups.
    factors: dict[str,tuple[Money,Money]]=Field(default_factory=dict,max_length=100)
    @property
    def eligible(self):return self.state=='ELIGIBLE_COMPLETE'

class Inputs(Contract):
    environment: Environment
    binding: Hash
    issue: AwareDatetime
    cutoff: AwareDatetime
    month: str=Field(pattern=r'^\d{4}-(0[1-9]|1[0-2])$')
    currency: str=Field(pattern=r'^[A-Z]{3}$')
    scope: Version='TOTAL'
    days: tuple[Day,...]=Field(max_length=731)
    source_vector: dict[Hash,Hash]=Field(default_factory=dict,max_length=100000)
    fences: dict[Hash,Hash]=Field(default_factory=dict,max_length=100000)
    unit_fences: dict[Hash,Hash]=Field(default_factory=dict,max_length=10000)
    current_health: Literal['FRESH','STALE','UNAVAILABLE']='FRESH'
    @field_validator('issue','cutoff')
    @classmethod
    def utc(cls,value):return value.astimezone(timezone.utc)
    @model_validator(mode='after')
    def valid(self):
        if self.cutoff>self.issue or len({d.day for d in self.days})!=len(self.days):raise ValueError('Invalid financial inputs')
        if any(d.published_at and d.published_at>self.cutoff for d in self.days):raise ValueError('Future information')
        return self
    @property
    def identity(self):return digest(self.model_dump(mode='json'))

def bounded(body,policy):
    import json
    if len(json.dumps(body).encode())>policy.max_bytes:raise SourceError('BATCH_LIMIT')
    return body
