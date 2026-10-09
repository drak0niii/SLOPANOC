"""Read-only policy DTOs, independent of execution/business data."""
from typing import Literal
from pydantic import Field
from .schemas import Contract, Permission, Count, TraceId, SpanId
from .retention_registry import VERSION, REGISTRY
from .privacy_policy import VERSION as PRIVACY_VERSION
from .sampling_policy import VERSION as SAMPLING_VERSION, ARCHIVE_VERSION

class RetentionView(Contract):
    dataset: str
    horizon: int
    unit: Literal['hours','days','months']
    owner: str
    semantics: str
    dependencies: bool

class PolicyView(Contract):
    privacy_version: Literal['privacy-v1']=PRIVACY_VERSION
    retention_version: Literal['retention-v1']=VERSION
    sampling_version: Literal['sampling-v1']=SAMPLING_VERSION
    archive_version: Literal['archive-v1']=ARCHIVE_VERSION
    sdk_capture_percent: Literal[100]=100
    healthy_trace_percent: Literal[100]=100
    healthy_reduction_status: Literal['PROVISIONAL_DISABLED']='PROVISIONAL_DISABLED'
    production_archive: Literal['NOT_DEPLOYED']='NOT_DEPLOYED'
    production_retention_scheduling: Literal['NOT_EXECUTED']='NOT_EXECUTED'
    financial_retention_months: int=Field(24,ge=24,le=120)
    retention: tuple[RetentionView,...]

def policy_view(config):
    return PolicyView(financial_retention_months=config.finops_retention_months,
        retention=tuple(RetentionView(**{k:getattr(value,k) for k in RetentionView.model_fields}) for value in REGISTRY.values()))

class AccessView(Contract):
    verified: Literal[True]=True
    capabilities: tuple[Permission,...]
    environments: tuple[Literal['local','development','test','staging','production'],...]

class ArchiveSpanView(Contract):
    trace_id: TraceId
    span_id: SpanId
    parent_span_id: SpanId | None
    name: str
    kind: int=Field(ge=0,le=5)
    start_ns: Count
    end_ns: Count
    status: int=Field(ge=0,le=2)
    attributes: dict[str,str|int|float|bool|tuple[str,...]]
    events: tuple[dict,...]

class ArchivePage(Contract):
    trace_id: TraceId
    digest: str=Field(pattern='^[0-9a-f]{64}$')
    spans: tuple[ArchiveSpanView,...]=Field(max_length=100)
    next_offset: int | None=Field(None,ge=0,le=10000)
    completeness: Literal['COMPLETE']='COMPLETE'

class RunRetentionView(Contract):
    run_id: str=Field(pattern='^[0-9a-f-]{36}$')
    instrumentation: Literal['COMPLETE','MISSING','UNKNOWN']='UNKNOWN'
    cloud_trace: Literal['NOT_LIVE_CONFIRMED']='NOT_LIVE_CONFIRMED'
    exceptional_archive: Literal['RETAINED','ELIGIBLE_FOR_DELETION','PENDING_OR_DEGRADED','NOT_REQUIRED','UNAVAILABLE']='UNAVAILABLE'
    production_binding: Literal['NOT_DEPLOYED']='NOT_DEPLOYED'
