"""Safe SRE read DTOs. Numeric truth belongs to the canonical evaluator."""
from typing import Literal
from pydantic import AwareDatetime, Field
from .schemas import Contract, Count
from .slo_contract import State

class BurnWindow(Contract):
    seconds: int = Field(gt=0,le=259200)
    eligible: Count
    bad: Count
    unknown: Count
    burn_rate: float | None = Field(default=None,ge=0)

class SLOView(Contract):
    schema_version: Literal[1]
    slo_id: str = Field(max_length=64)
    name: str = Field(max_length=128)
    description: str = Field(max_length=512)
    objective: float = Field(gt=0,le=1)
    objective_status: Literal['PROVISIONAL','ARCHITECTURAL']
    window_seconds: int = Field(gt=0)
    window_start: AwareDatetime
    window_end: AwareDatetime
    population: str = Field(max_length=256)
    threshold_seconds: float | None = Field(default=None,ge=0)
    eligible: Count
    good: Count
    bad: Count
    unknown: Count
    excluded: Count
    current_value: float | None = Field(default=None,ge=0,le=1)
    allowed_bad: float | None = Field(default=None,ge=0)
    consumed_fraction: float | None = Field(default=None,ge=0)
    remaining_fraction: float | None = None
    burn_tiers: tuple[Literal['fast','sustained','slow'],...] = Field(max_length=3)
    burn_windows: tuple[BurnWindow,...] = Field(max_length=5)
    state: State
    reason: Literal['FRESH','SOURCE_UNAVAILABLE','STALE_SOURCE','INCOMPLETE_COVERAGE','LOW_VOLUME_OR_PARTIAL_WINDOW','DATA_SOURCE_AVAILABLE_IN_M10']
    data_source: Literal['SRE_ROLLUPS','M10_COST_LEDGER','DIRECT_GRAPH_UNAVAILABLE']
    source_status: Literal['AVAILABLE','UNAVAILABLE','STALE','DATA_SOURCE_AVAILABLE_IN_M10']
    source_kind: Literal['ISOLATED_FIXTURE','RUNTIME_ROLLUPS','CLOUD_MONITORING_NOT_ACTIVATED']
    coverage: float | None = Field(default=None,ge=0,le=1)
    evaluated_at: AwareDatetime
    source_last_updated: AwareDatetime | None = None
    freshness_seconds: int = Field(gt=0)
    population_watermark: AwareDatetime
    runbook: str = Field(pattern=r'^docs/Telemetry/runbooks/[a-z_]+\.md$')

class SLOPage(Contract):
    schema_version: Literal[1] = 1
    environment: str = Field(max_length=16)
    evaluated_at: AwareDatetime
    items: tuple[SLOView,...] = Field(max_length=32)

class SSEReceipt(Contract):
    run_id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    event: Literal['message.completed']
