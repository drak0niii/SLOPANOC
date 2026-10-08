"""Content-free financial read projections, quantities only."""
from typing import Literal
from pydantic import AwareDatetime, Field
from ..schemas import Contract, Count
from .contracts import ENVIRONMENTS, QUANTITIES

class QuantityView(Contract):
    observed: int | None = None
    known: Count
    unknown: Count

class UsageGroup(Contract):
    dimension: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,128}$')
    attempts: Count
    captured_attempts: Count
    quantity_coverage: float | None = Field(default=None,ge=0,le=1)
    quantity_known: Count
    quantity_partial: Count
    quantities: dict[str, QuantityView]

class UsagePage(Contract):
    schema_version: Literal[1] = 1
    environment: Literal['local','development','staging','production']
    start: AwareDatetime
    end: AwareDatetime
    group: Literal['model','provider','agent','workload','operation','operation_type']
    items: tuple[UsageGroup,...] = Field(max_length=100)
    next_cursor: str | None = Field(default=None,max_length=512)

class LedgerHealth(Contract):
    schema_version: Literal[1] = 1
    environment: Literal['local','development','staging','production']
    state: Literal['HEALTHY','DEGRADED','STALE_DATA','DATA_SOURCE_UNAVAILABLE']
    pending: Count
    debt: Count
    conflicts: Count
    exhausted: Count
    admissions_failed: Count
    oldest_pending_at: AwareDatetime | None
    oldest_debt_at: AwareDatetime | None
    updated_at: AwareDatetime | None
    coverage_start: AwareDatetime | None
    last_observation: AwareDatetime | None
    last_persistence: AwareDatetime | None
    retention_months: int = Field(ge=24,le=120)
    retention_execution: Literal['NOT_ACTIVATED'] = 'NOT_ACTIVATED'
