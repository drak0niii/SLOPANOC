"""Explicit safe public projection; no raw source identifiers or nested payloads."""
from typing import Literal,Annotated
from pydantic import Field
from ..schemas import Contract,Count
from .reconciliation_contracts import Status,Confidence,Reason

D=Annotated[str,Field(pattern=r'^-?\d+(?:\.\d+)?$',max_length=100)]
class Coverage(Contract):
    quantity_groups:Count
    runtime_known_groups:Count
    billing_known_groups:Count
    compatible_groups:Count
    price_eligible_groups:Count
    priced_groups:Count
    runtime_quantity_known:D|None
    billing_quantity_known:D|None
    compatible_quantity:D|None
    price_reference:D|None
    eligible_attempts:Count
    represented_attempts:Count
    mapped_rows:Count
    source_rows:Count
    source_components:Count
    classified_components:Count
    runtime_representation:D|None
    ledger_completeness:D|None
    mapping_rows:D|None
    mapping_amount:D|None
    billing_classification:D|None
    monetary_accounting:D|None
    allocation:D|None
class Quantity(Contract):
    model:str=Field(pattern=r'^(other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$')
    component:Literal['input_tokens','output_tokens','candidate_tokens','cached_tokens','total_tokens','thought_tokens','tool_input_tokens','billable_characters']
    unit:Literal['tokens','characters','requests','seconds','bytes','byte-seconds']
    currency:str|None=Field(pattern=r'^[A-Z]{3}$')
    runtime_quantity:D|None
    runtime_unknown:Count
    billing_quantity:D|None
    billing_unknown:Count
    difference:D|None
    variance_pct:D|None
    confidence:Confidence
    reason:Reason|None
class Ratios(Contract):
    direct:D|None
    shared:D|None
    unallocated:D|None
class Amount(Contract):
    currency:str=Field(pattern=r'^[A-Z]{3}$')
    gross:D
    credit:D
    adjustment:D
    tax:D
    rounding:D
    net:D
    absolute:D
    direct:D
    shared:D
    unallocated:D
    excluded:Literal['0']
    ratios:Ratios
    percentages:Ratios
    reference_rounding:Literal['ROUND_HALF_EVEN_38']
    catalog_reference:D|None
    monetary_variance:D|None
    monetary_variance_pct:D|None
    eur_net:D|None
    eur_state:Literal['NATIVE_EUR','EUR_UNAVAILABLE']
    count:Count
    controls:Literal['PASS']
class Components(Contract):
    gross:D='0'
    credit:D='0'
    adjustment:D='0'
    tax:D='0'
    rounding:D='0'
class Allocation(Contract):
    currency:str=Field(pattern=r'^[A-Z]{3}$')
    classification:Literal['DIRECT','SHARED','UNALLOCATED']
    confidence:Literal['HIGH','UNMATCHED']
    method:Literal['OWNERSHIP_POOL','VERIFIED_OWNERSHIP','NO_ATTRIBUTION']
    driver:Literal['VERIFIED_POOL_MEMBERSHIP','NONE']
    net:D
    absolute:D
    count:Count
    components:Components
class Controls(Contract):
    signed_net:Literal['PASS']
    components:Literal['PASS']
    absolute:Literal['PASS']
    count:Literal['PASS']
class Versions(Contract):
    mapping:str=Field(pattern=r'^[A-Za-z0-9_.-]{1,32}$')
    ownership:str=Field(pattern=r'^[A-Za-z0-9_.-]{1,32}$')
    registry:str=Field(pattern=r'^[a-f0-9]{64}$')
    policy:Literal['policy-v1']
    algorithm:Literal['reconciliation-v1']
    schema_version:Literal[1]
class RuntimeQuality(Contract):
    eligible:Count
    captured:Count
    debt:Count
    global_debt:Count
    pending:Count
    completeness:D|None
    available:bool
    coverage_complete:bool
    freshness:Literal['FRESH','STALE','UNAVAILABLE']='FRESH'
    coverage_start:str|None=None
    last_persistence:str|None=None
class View(Contract):
    period:str=Field(pattern=r'^\d{4}-\d{2}-\d{2}$')
    invoice_months:tuple[Annotated[str,Field(pattern=r'^\d{4}(0[1-9]|1[0-2])$')],...]=Field(default=(),max_length=100)
    generation:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    source_generations:tuple[str,...]=Field(default=(),max_length=32)
    status:Status=Status.UNRECONCILED
    display_state:Literal['Reconciled','Partial','Unresolved','Stale source','Source unavailable']='Source unavailable'
    billing_freshness:Literal['FRESH','DELAYED','STALE','UNAVAILABLE','NOT_CONFIGURED']='NOT_CONFIGURED'
    pricing_freshness:Literal['FRESH','DELAYED','STALE','UNAVAILABLE','NOT_CONFIGURED']='NOT_CONFIGURED'
    source_mode:Literal['TEST_FIXTURE','BIGQUERY','NOT_CONFIGURED']='NOT_CONFIGURED'
    runtime_freshness:Literal['FRESH','STALE','UNAVAILABLE','SNAPSHOT_ONLY']='SNAPSHOT_ONLY'
    current_accounting_debt:Count|None=None
    current_accounting_pending:Count|None=None
    job_error:Reason|None=None
    reasons:tuple[Reason,...]=()
    quantity_count:Count=0
    quantity_detail_state:Literal['COMPLETE','BOUNDED_SUMMARY']='COMPLETE'
    quantities:tuple[Quantity,...]=Field(default=(),max_length=100)
    amounts:tuple[Amount,...]=Field(default=(),max_length=100)
    allocation:tuple[Allocation,...]=Field(default=(),max_length=100)
    coverage:Coverage|None=None
    controls:Controls|None=None
    runtime_quality:RuntimeQuality|None=None
    versions:Versions|None=None
    period_basis:Literal['usage']='usage'
    configuration:Literal['READ_ONLY']='READ_ONLY'
    estimate_state:Literal['M10_ESTIMATE_UNAVAILABLE']='M10_ESTIMATE_UNAVAILABLE'
    published_at:str|None=None
class Page(Contract):
    schema_version:Literal[1]=1
    environment:Literal['local','development','staging','production']
    items:tuple[View,...]=Field(default=(),max_length=31)
    next_cursor:str|None=None
