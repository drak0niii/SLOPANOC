"""Safe aggregate-only projection. Analytical views must never be summed."""
from typing import Literal
from pydantic import Field
from ..schemas import Contract
from .unit_economics_contracts import Environment, Hash, Result

class Day(Contract):
    period: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    generation: Hash | None = None
    reconciliation_generation: Hash | None = None
    denominator_generations: tuple[Hash, ...] = Field(default=(),max_length=2)
    unit_version: Literal['units-v1'] = 'units-v1'
    driver_version: str = Field(default='drivers-v1',pattern=r'^[A-Za-z0-9_.-]{1,32}$')
    algorithm_version: Literal['unit-economics-v1'] = 'unit-economics-v1'
    published_at: str | None = None
    job_state: Literal['AVAILABLE','UNAVAILABLE','FAILED'] = 'UNAVAILABLE'
    results: tuple[Result, ...] = Field(default=(),max_length=100)
    result_count: int = Field(default=0,ge=0,le=10000)
    bounded_summary: bool = False

class Page(Contract):
    schema_version: Literal[1] = 1
    environment: Environment
    period_basis: Literal['usage'] = 'usage'
    configuration: Literal['READ_ONLY'] = 'READ_ONLY'
    analytical_views: Literal['ALTERNATIVE_NON_ADDITIVE'] = 'ALTERNATIVE_NON_ADDITIVE'
    items: tuple[Day, ...] = Field(default=(),max_length=31)
    next_cursor: str | None = None
