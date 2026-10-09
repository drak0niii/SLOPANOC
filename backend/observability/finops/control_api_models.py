"""Closed content-free M14 API contracts; private bindings and source rows never exported."""
from typing import Literal
from pydantic import Field
from ..schemas import Contract,Count
from .control_contracts import Money,Ratio
from .unit_economics_contracts import Hash,Version,Environment

State=Literal['AVAILABLE','NOT_CONFIGURED','UNEVALUATED','INSUFFICIENT_DATA','CURRENCY_UNAVAILABLE','SCOPE_UNAVAILABLE','SOURCE_STALE',
 'RECONCILIATION_INCOMPLETE','ZERO_BUDGET','HEALTHY','INFORMATIONAL','WATCH','AT_RISK','AT_LIMIT','EXCEEDED','POLICY_UNAVAILABLE',
 'SOURCE_QUALITY_DEGRADATION','UNSUPPORTED_NET_CREDIT_REGIME','EVALUATED','FAILED','OBSERVED','ACTIVE','RESOLVED','NORMAL']
Reasons=Literal['SOURCE_QUALITY_DEGRADATION','BILLING_CORRECTION','WORKLOAD_MIX_SHIFT','PRICE_CHANGE','USAGE_VOLUME_INCREASE','SHARED_INFRASTRUCTURE_CHANGE','UNALLOCATED_MOVEMENT','UNALLOCATED_INCREASE','UNEXPLAINED']
class Driver(Contract):
    driver: Literal['USAGE_VOLUME','UNIT_PRICE','WORKLOAD_MIX','SHARED_INFRASTRUCTURE','UNALLOCATED']
    amount: Money

class Item(Contract):
    generation: Hash
    kind: Literal['BUDGET','FORECAST','ANOMALY']
    issue: str=Field(max_length=40)
    month: str=Field(pattern=r'^\d{4}-\d{2}$')
    currency: str=Field(pattern=r'^[A-Z]{3}$')
    scope: Version='TOTAL'
    state: State
    freshness: Literal['FRESH','STALE','UNAVAILABLE']='FRESH'
    budget_id: Version|None=None
    budget_version: Version|None=None
    amount: Money|None=None
    actual_observed: Money|None=None
    actual_eligible: Money|None=None
    utilization: Ratio|None=None
    utilization_pct: Ratio|None=None
    variance: Money|None=None
    coverage: Money|None=None
    positive_zero_budget_breach: bool=False
    actual_to_cutoff: Money|None=None
    remaining: Money|None=None
    forecast: Money|None=None
    lower: Money|None=None
    upper: Money|None=None
    range_type: Literal['NONE','SCENARIO_RANGE']='NONE'
    tier: Count=Field(default=0,le=2)
    algorithm: Literal['unavailable-v1','eligible-run-rate-v1','weekday-median-v1','robust-daily-v1']='unavailable-v1'
    confidence: Literal['UNAVAILABLE','LOW','QUALIFIED','HIGH','QUALITY']='UNAVAILABLE'
    risk: Literal['ON_TRACK','WATCH','PROJECTED_OVER','ALREADY_EXCEEDED','UNCERTAIN','INSUFFICIENT_DATA','SOURCE_STALE']='INSUFFICIENT_DATA'
    eligible_days: Count=0
    period_days: Count=0
    prior_eligible_days: Count=0
    day_states: dict[Literal['ELIGIBLE_COMPLETE','OPEN','TOO_RECENT','STALE','MISSING','PARTIAL','EXCLUDED'],Count]=Field(default_factory=dict)
    backtest_quality: dict[Literal['mae','wape','bias'],Ratio|None]|None=None
    anomaly_id: Hash|None=None
    type: Literal['TOTAL_SPEND','PROVIDER_MODEL_SPEND','SHARED_INFRASTRUCTURE','OBSERVABILITY_SPEND','UNIT_COST','USAGE_VOLUME','BILLING_CORRECTION','UNALLOCATED_EXPOSURE','RECONCILIATION_QUALITY']|None=None
    day: str|None=Field(default=None,pattern=r'^\d{4}-\d{2}-\d{2}$')
    observed: Money|None=None
    baseline: Money|None=None
    deviation: Money|None=None
    absolute_deviation: Money|None=None
    relative_deviation: Ratio|None=None
    severity: Literal['NONE','WATCH','HIGH','CRITICAL']='NONE'
    reasons: tuple[Reasons,...]=()
    drivers: tuple[Driver,...]=Field(default=(),max_length=100)
    residual: Money|None=None
    robust_score: Ratio|None=None
    impact: Ratio|None=None
    impact_type: Literal['FIXED_POPULATION_UNIT_EXPOSURE','ACTUAL_DEVIATION']|None=None
    unit_population: Count=0
    baseline_method: Literal['SAME_WEEKDAY','MEDIAN_MAD']|None=None
    persistence: Count=0
    baseline_days: Count=0
    label: Literal['MONETARY','USAGE','QUALITY']|None=None
    unit_id: Version|None=None
    model: str|None=Field(default=None,pattern=r'^(other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$')
    provider: Literal['gcp.vertex_ai','gcp.gemini','other']|None=None
    quantity_unit: Literal['tokens','characters','calls']|None=None
    provisional: Literal[True]=True
    read_only: Literal[True]=True

class Page(Contract):
    schema_version: Literal[1]=1
    environment: Environment
    state: State
    items: tuple[Item,...]=Field(default=(),max_length=100)
    next_cursor: str|None=Field(default=None,max_length=512)
    evaluated: Count=0
