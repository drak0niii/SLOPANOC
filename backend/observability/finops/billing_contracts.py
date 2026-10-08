"""M11 versioned financial source contracts. No runtime usage or reconciliation."""
from dataclasses import dataclass
from datetime import datetime, date, timezone, timedelta
from decimal import Decimal, localcontext
from hashlib import sha256
import json
import re

SOURCES = ('DETAILED_BILLING', 'PRICING_EXPORT', 'FOCUS')
CATEGORIES = ('MODEL_AI', 'CLOUD_RUN', 'CLOUD_SQL', 'BIGQUERY', 'GCS', 'MONITORING_LOGGING', 'NETWORK', 'OTHER', 'UNMAPPED')
COST_TYPES = ('regular', 'adjustment', 'tax', 'rounding_error')
CREDIT_TYPES = ('COMMITTED_USAGE_DISCOUNT', 'COMMITTED_USAGE_DISCOUNT_DOLLAR_BASE', 'DISCOUNT', 'FREE_TIER', 'PROMOTION', 'RESELLER_MARGIN', 'SUBSCRIPTION_BENEFIT', 'SUSTAINED_USAGE_DISCOUNT', 'OTHER')

class SourceError(ValueError):
    """Safe stable code only; never retain a provider payload or exception text."""
    def __init__(self, code='SCHEMA_INCOMPATIBLE'):
        self.code = code
        super().__init__(code)

def decimal(value):
    if isinstance(value, (float, bool)) or not isinstance(value, (str, int, Decimal)):
        raise SourceError('INVALID_DECIMAL')
    try:
        result = Decimal(value)
        if not result.is_finite() or result.copy_abs() >= Decimal('1e38') or result.as_tuple().exponent < -38:
            raise ValueError()
        return result
    except Exception:
        raise SourceError('INVALID_DECIMAL') from None

def money(value):
    return format(decimal(value), 'f')

def instant(value):
    try:
        value = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError()
        return value.astimezone(timezone.utc)
    except Exception:
        raise SourceError() from None

def code(value, pattern=r'[A-Za-z0-9_.:/ -]{1,128}'):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise SourceError()
    return value

def currency(value):
    return code(value, r'[A-Z]{3}')

def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    def __post_init__(self):
        object.__setattr__(self, 'start', instant(self.start))
        object.__setattr__(self, 'end', instant(self.end))
        if not timedelta(0) < self.end - self.start <= timedelta(days=1):
            raise SourceError('INVALID_WINDOW')
        if self.start.time() != datetime.min.time() or self.end != self.start + timedelta(days=1):
            raise SourceError('INVALID_WINDOW')

    @property
    def partition(self): return self.start.date().isoformat()

@dataclass(frozen=True)
class Extraction:
    rows: tuple[dict, ...]
    window: Window
    extracted_at: datetime
    complete: bool = True
    schema_version: int = 1
    mode: str = 'TEST_FIXTURE'
    bytes_processed: int = 0

@dataclass(frozen=True)
class Policy:
    """Provisional, deployment-owned policy; not a financial SLA."""
    billing_delayed_seconds: int = 86400
    billing_stale_seconds: int = 259200
    pricing_delayed_seconds: int = 172800
    pricing_stale_seconds: int = 604800
    billing_cadence_seconds: int = 3600
    pricing_cadence_seconds: int = 86400
    max_rows: int = 100000
    max_groups: int = 1000
    lookback_days: int = 7
    retained_days: int = 731
    query_seconds: int = 30

    def __post_init__(self):
        if not 1 <= self.max_rows <= 100000 or not 1 <= self.max_groups <= 1000:
            raise SourceError('INVALID_POLICY')
        if not 1 <= self.lookback_days <= self.retained_days <= 731:
            raise SourceError('INVALID_POLICY')
        if not 0 < self.billing_delayed_seconds < self.billing_stale_seconds or not 0 < self.pricing_delayed_seconds < self.pricing_stale_seconds:
            raise SourceError('INVALID_POLICY')
        if not 1 <= self.query_seconds <= 30 or min(self.billing_cadence_seconds, self.pricing_cadence_seconds) < 1:
            raise SourceError('INVALID_POLICY')

@dataclass(frozen=True)
class Projection:
    fingerprint: str
    rows: int
    groups: tuple[dict, ...]
    controls: tuple[dict, ...]
    export_time: datetime | None
    price_from: datetime | None = None
    price_to: datetime | None = None
    prices: tuple[dict, ...] = ()
    late_rows: int = 0
    correction_rows: int = 0
    unmapped_rows: int = 0
    evidence_rows: int = 0
    mapping_version: str = 'v1'
