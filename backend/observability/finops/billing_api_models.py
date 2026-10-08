"""Safe aggregate financial DTOs. Decimal strings only, no restricted source identity."""
from typing import Literal
from pydantic import Field,AwareDatetime
from ..schemas import Contract,Count

DecimalString=str
class SourceHealth(Contract):
    source: Literal['DETAILED_BILLING','PRICING_EXPORT','FOCUS']
    state: Literal['FRESH','DELAYED','STALE','UNAVAILABLE','NOT_CONFIGURED']
    reason: Literal['SOURCE_CURRENT','SOURCE_AGE','NO_DATA','SOURCE_UNAVAILABLE','SCHEMA_INCOMPATIBLE','COVERAGE_PARTIAL','NOT_CONFIGURED']
    mode: Literal['TEST_FIXTURE','BIGQUERY','NOT_CONFIGURED']
    as_of: AwareDatetime|None=None
    last_extraction: AwareDatetime|None=None
    last_export: AwareDatetime|None=None
    coverage_start: AwareDatetime|None=None
    coverage_end: AwareDatetime|None=None
    generation: str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    row_count: Count=0
    unmapped_rows: Count=0
    price_evidence_rows: Count=0
    pending_partitions: Count=0
    policy: Literal['PROVISIONAL']='PROVISIONAL'
    delayed_seconds: int=Field(default=86400,ge=1,le=2592000)
    stale_seconds: int=Field(default=259200,ge=1,le=2592000)
    cadence_seconds: int=Field(default=3600,ge=1,le=604800)

class HealthPage(Contract):
    schema_version: Literal[1]=1
    environment: Literal['local','development','staging','production']
    sources: tuple[SourceHealth,...]=Field(max_length=3)

class Amounts(Contract):
    currency: str=Field(pattern=r'^[A-Z]{3}$')
    category: Literal['MODEL_AI','CLOUD_RUN','CLOUD_SQL','BIGQUERY','GCS','MONITORING_LOGGING','NETWORK','OTHER','UNMAPPED']
    gross: str=Field(pattern=r'^-?\d+(?:\.\d+)?$')
    credits: str=Field(pattern=r'^-?\d+(?:\.\d+)?$')
    adjustments: str=Field(pattern=r'^-?\d+(?:\.\d+)?$')
    tax: str=Field(pattern=r'^-?\d+(?:\.\d+)?$')
    rounding: str=Field(pattern=r'^-?\d+(?:\.\d+)?$')
    pre_credit: str=Field(pattern=r'^-?\d+(?:\.\d+)?$')
    net: str=Field(pattern=r'^-?\d+(?:\.\d+)?$')
    rows: Count
    unmapped_rows: Count
    mapping_coverage: str|None=Field(default=None,pattern=r'^\d+(?:\.\d+)?$')
    eur_net: str|None=Field(default=None,pattern=r'^-?\d+(?:\.\d+)?$')
    eur_state: Literal['NATIVE_EUR','EUR_REPORTING_UNAVAILABLE']

class BillingSummary(Contract):
    schema_version: Literal[1]=1
    environment: Literal['local','development','staging','production']
    period_basis: Literal['usage','invoice']
    start: str
    end: str
    invoice_month: str|None=None
    amount_basis: Literal['ACTUAL_BILLED_COST']='ACTUAL_BILLED_COST'
    tax_basis: Literal['SOURCE_TAX_SEPARATE']='SOURCE_TAX_SEPARATE'
    source: SourceHealth
    coverage_complete: bool
    amounts: tuple[Amounts,...]|None=Field(default=None,max_length=100)
    next_cursor: str|None=Field(default=None,max_length=512)

class PricingStatus(Contract):
    schema_version: Literal[1]=1
    environment: Literal['local','development','staging','production']
    amount_basis: Literal['PRICING_EXPORT_CATALOG']='PRICING_EXPORT_CATALOG'
    source: SourceHealth
    earliest_pricing_as_of: AwareDatetime|None=None
    latest_pricing_as_of: AwareDatetime|None=None
    historical_policy: Literal['NO_EXTRAPOLATION']='NO_EXTRAPOLATION'
