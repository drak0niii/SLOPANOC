"""M12 bounded derived contracts; source ledgers retain independent authority."""
from enum import Enum
from typing import Literal
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
from pydantic import Field, StrictInt, field_validator
from ..schemas import Contract
from .billing_contracts import decimal, money, digest, SourceError

class Status(str, Enum):
    UNRECONCILED='UNRECONCILED'
    PARTIALLY_RECONCILED='PARTIALLY_RECONCILED'
    RECONCILED='RECONCILED'
    VARIANCE_REVIEW='VARIANCE_REVIEW'
    CORRECTED='CORRECTED'

class Confidence(str, Enum):
    EXACT='EXACT'
    HIGH='HIGH'
    AGGREGATED='AGGREGATED'
    PARTIAL='PARTIAL'
    UNMATCHED='UNMATCHED'
    AMBIGUOUS='AMBIGUOUS'
    SOURCE_UNAVAILABLE='SOURCE_UNAVAILABLE'

class Reason(str, Enum):
    LATE_BILLING='LATE_BILLING'
    SKU_UNMAPPED='SKU_UNMAPPED'
    UNIT_MISMATCH='UNIT_MISMATCH'
    RUNTIME_USAGE_UNKNOWN='RUNTIME_USAGE_UNKNOWN'
    BILLING_QUANTITY_UNKNOWN='BILLING_QUANTITY_UNKNOWN'
    CREDIT_ADJUSTMENT='CREDIT_ADJUSTMENT'
    SHARED_INFRASTRUCTURE='SHARED_INFRASTRUCTURE'
    SOURCE_STALE='SOURCE_STALE'
    AMBIGUOUS_MATCH='AMBIGUOUS_MATCH'
    ACCOUNTING_DEBT='ACCOUNTING_DEBT'
    PRICING_UNAVAILABLE='PRICING_UNAVAILABLE'
    INPUT_DETAIL_UNAVAILABLE='INPUT_DETAIL_UNAVAILABLE'
    SCOPE_UNPROVEN='SCOPE_UNPROVEN'
    FINANCIAL_IMBALANCE='FINANCIAL_IMBALANCE'
    NO_DATA='NO_DATA'
    SOURCE_UNAVAILABLE='SOURCE_UNAVAILABLE'
    TEMPORAL_GRAIN_MISMATCH='TEMPORAL_GRAIN_MISMATCH'
    QUANTITY_VARIANCE='QUANTITY_VARIANCE'
    MONETARY_VARIANCE='MONETARY_VARIANCE'
    MAPPING_INVALID='MAPPING_INVALID'
    BATCH_LIMIT='BATCH_LIMIT'
    PUBLICATION_FAILED='PUBLICATION_FAILED'

KINDS=('gross','credit','adjustment','tax','rounding')
CLASSES=('DIRECT','SHARED','UNALLOCATED','EXCLUDED')
ALGORITHM='reconciliation-v1'

class Policy(Contract):
    version: Literal['policy-v1']='policy-v1'
    max_rows: StrictInt=Field(default=100000,ge=1,le=100000)
    max_buckets: StrictInt=Field(default=10000,ge=1,le=10000)
    max_bytes: StrictInt=Field(default=134217728,ge=1024,le=134217728)
    job_seconds: StrictInt=Field(default=60,ge=1,le=60)
    cycle_partitions: StrictInt=Field(default=2,ge=1,le=32)
    retention_days: StrictInt=Field(default=731,ge=1,le=731)
    overlap_days: StrictInt=Field(default=7,ge=1,le=731)
    cadence_seconds: StrictInt=Field(default=60,ge=1,le=86400)
    @field_validator('overlap_days')
    @classmethod
    def overlap(cls,value,info):
        if value>info.data['retention_days']:raise ValueError('Invalid policy')
        return value


def ratio(numerator,denominator):
    n,d=decimal(numerator),decimal(denominator)
    if d==0:return None
    with localcontext() as ctx:
        ctx.prec=100
        return money((n/d).quantize(Decimal('0.000000000001'),rounding=ROUND_HALF_EVEN))


def percentage(numerator,denominator):
    n,d=decimal(numerator),decimal(denominator)
    if d==0:return None
    with localcontext() as ctx:
        ctx.prec=100
        value=n/d*100
        if abs(value)>Decimal('1e12'):return None
        return money(value.quantize(Decimal('0.000000000001'),rounding=ROUND_HALF_EVEN))


def bounded(body,policy):
    import json
    if len(json.dumps(body,separators=(',',':')).encode())>policy.max_bytes:
        raise SourceError('BATCH_LIMIT')
    return body


def controls(components):
    """Signed, absolute and count controls independently for EACH kind/currency."""
    result={}
    with localcontext() as ctx:
        ctx.prec=100
        for c in components:
            if c['kind'] not in KINDS or type(c['count']) is not int or c['count']<1:
                raise SourceError('FINANCIAL_IMBALANCE')
            from .billing_contracts import currency
            key=(currency(c['currency']),c['kind'])
            amount,absolute=decimal(c['amount']),decimal(c['absolute'])
            if absolute!=abs(amount):
                # Inputs split by sign. Cancellation is never compacted into net.
                raise SourceError('FINANCIAL_IMBALANCE')
            v=result.setdefault(key,{'amount':Decimal(0),'absolute':Decimal(0),'count':0})
            v['amount']+=amount;v['absolute']+=absolute;v['count']+=c['count']
        return [dict(currency=k[0],kind=k[1],amount=money(v['amount']),absolute=money(v['absolute']),count=v['count'])
                for k,v in sorted(result.items())]
