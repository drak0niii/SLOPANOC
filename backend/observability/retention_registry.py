"""Single policy registry; eligibility is distinct from provider physical deletion."""
from dataclasses import dataclass
from datetime import timedelta

VERSION = 'retention-v1'

@dataclass(frozen=True)
class RetentionPolicy:
    dataset: str
    horizon: int
    unit: str
    owner: str
    dependencies: bool = False
    semantics: str = 'minimum_retention_then_eligible'

REGISTRY = {
    'active_terminal': RetentionPolicy('active_terminal',24,'hours','application'),
    'ordinary_summary': RetentionPolicy('ordinary_summary',30,'days','application'),
    'exceptional_summary': RetentionPolicy('exceptional_summary',90,'days','application'),
    'application_logs': RetentionPolicy('application_logs',30,'days','cloud_logging'),
    'cloud_trace': RetentionPolicy('cloud_trace',30,'days','provider',semantics='provider_controlled'),
    'exceptional_archive': RetentionPolicy('exceptional_archive',90,'days','archive',semantics='primary_horizon_async_lifecycle'),
    'analytical_metrics': RetentionPolicy('analytical_metrics',13,'months','provider',semantics='minimum_analytical_access'),
    'financial_history': RetentionPolicy('financial_history',24,'months','application',True),
    'slo_receipts': RetentionPolicy('slo_receipts',2,'days','application'),
    'slo_working_buckets': RetentionPolicy('slo_working_buckets',35,'days','application'),
}

def duration(dataset):
    policy = REGISTRY[dataset]
    if policy.unit == 'months':
        raise ValueError('Calendar-month policy requires calendar arithmetic')
    return timedelta(**{policy.unit:policy.horizon})

@dataclass(frozen=True)
class TableRetention:
    table: str
    policy: str
    deletion_adapter: str
    limitations: str

def table_registry():
    """Actual M7/M9/M10–M15 durable inventory, including conservative retention.

    Missing environment/time/publication coverage is never guessed. These tables
    remain under the minimum policy and are retained until a safe adapter exists.
    No table is given a destructive default merely because it has a timestamp.
    """
    from .models import Base
    from .policy_models import ArchiveReceipt,RetentionCheckpoint
    from .slo_rollups import SREReceiptRow,SREBucketRow,SRESourceRow
    from .retention_worker import financial_tables
    result={
        'observability_run_status':TableRetention('observability_run_status','active_terminal','active_terminal','Active/stale cleanup remains guarded by M7 owner expiry.'),
        'observability_turn':TableRetention('observability_turn','ordinary_summary/exceptional_summary','summary_class','Separate ordinary/exceptional expiry, never a trace blob warehouse.'),
        'observability_run_event':TableRetention('observability_run_event','ordinary_summary/exceptional_summary','parent_guarded','Delete only after both authorized parent tiers are absent.'),
        SREReceiptRow.__tablename__:TableRetention(SREReceiptRow.__tablename__,'slo_receipts','slo_receipts','Settled receipts only.'),
        SREBucketRow.__tablename__:TableRetention(SREBucketRow.__tablename__,'slo_working_buckets','slo_working_buckets','Working buckets are separate from 13-month provider analytical access.'),
        SRESourceRow.__tablename__:TableRetention(SRESourceRow.__tablename__,'analytical_metrics','retain_conservatively','Fixed environment source coverage metadata; no destructive expiry assumption.'),
        ArchiveReceipt.__tablename__:TableRetention(ArchiveReceipt.__tablename__,'exceptional_archive','retain_conservatively','Unsampled health metadata; no payload; no unqualified deletion adapter.'),
        RetentionCheckpoint.__tablename__:TableRetention(RetentionCheckpoint.__tablename__,'operational_checkpoint','retain_conservatively','Fixed environment/dataset keys; bounded cursor; no content.'),
    }
    for name in financial_tables():
        result[name]=TableRetention(name,'financial_history','dependency_checked',
            '24 calendar-month minimum (configured 24–120); unknown scope/time/publication coverage retains rows. Shared PostgreSQL deletion remains UNKNOWN until publication fencing is qualified.')
    assert set(Base.metadata.tables)<=set(result)
    return result
