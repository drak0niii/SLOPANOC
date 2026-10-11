"""Read dependencies reuse existing truth without constructing producer singletons."""
import asyncio
from dataclasses import dataclass
from .service import Service
from .api_models import HealthView
from .projection import utcnow
from .authorization import denied
from .schemas import Permission


class ReaderService(Service):
    def __init__(self, repository, config, settings, *, policy=None):
        super().__init__(repository,config)
        self.settings=settings;self.producer_policy=policy
        self.reads=asyncio.Semaphore(settings.read_concurrency)
        self.archive_reads=asyncio.Semaphore(1)
        self.read_only=True

    async def reconstruct_archive(self, archive, principal, trace_id, digest):
        # Cancellation cannot stop a filesystem thread. Keep its slot until it
        # actually finishes, so expired requests cannot multiply archive work.
        await self.archive_reads.acquire()
        task=asyncio.create_task(asyncio.to_thread(archive.reconstruct,principal,trace_id,digest))
        def finished(value):
            self.archive_reads.release()
            if not value.cancelled(): value.exception()  # observe detached failures
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    async def query(self, method, *args, **kwargs):
        async with asyncio.timeout(self.settings.read_deadline):
            return await super().query(method,*args,**kwargs)

    async def health(self, principal):
        self.environment_scope(principal,self.config.otel_environment)
        stale=None;state='unavailable'
        if self.repository:
            try:
                stale=await self.query('stale_count',self.environment_scope(principal),utcnow())
                state='healthy'  # Only DB/read-model connectivity, not ANOC writer health.
            except Exception: pass
        return HealthView(persistence=state,writer_scope='process_local_anoc_unavailable',
            as_of=utcnow(),pending=None,admitted=None,terminal_pending=None,stale_records=stale,
            exporter_state='unknown',collector_configured=None,
            accepted=None,coalesced=None,attempts=None,persisted=None,failed=None,retries=None,
            rejected=None,terminal_unpersisted=None,shutdown_incomplete=None,invalid=None,
            read_plane_state='healthy',anoc_state='unknown',collector_state='unknown')

    def effective_policy(self):
        if self.producer_policy is None:
            raise denied('connector_unavailable','Producer policy is not published to this reader.')
        return self.producer_policy


@dataclass(frozen=True)
class AccountingRead:
    repository: object
    config: object
    # None means ANOC admission counters are unavailable, not zero failures.
    health_counts: None = None


@dataclass(frozen=True)
class PublishedSource:
    source: str
    scope: str
    mode: str


def bind_repositories(service, sessions):
    from .slo_rollups import Rollups
    from .slo_sources import RuntimeProvider
    from .finops.repository import Repository as AccountingRepository
    from .finops.slo_source import AccountingSource, CompositeSource
    from .finops.billing_repository import Repository as BillingRepository
    from .finops.billing_service import FinancialSources
    from .finops.reconciliation_repository import Repository as ReconciliationRepository
    from .finops.unit_economics_repository import Repository as UnitRepository
    from .finops.control_repository import Repository as ControlRepository
    from .finops.billing_contracts import Policy
    from .hybrid.read import ReadRepository as HybridReadRepository
    service.hybrid_reads=HybridReadRepository(sessions)
    from backend.execution_audit.read import AuditReadRepository
    service.execution_audit_reads=AuditReadRepository(sessions)
    accounting=AccountingRepository(sessions)
    service.accounting=AccountingRead(accounting,service.config)
    service.slo_provider=CompositeSource(RuntimeProvider(Rollups(sessions),service.config),AccountingSource(accounting))
    adapters=tuple(PublishedSource(name,scope,service.settings.source_mode) for name,scope in (
        ('DETAILED_BILLING',service.settings.billing_scope),('PRICING_EXPORT',service.settings.pricing_scope),
        ('FOCUS',service.settings.focus_scope)) if scope)
    service.financial_sources=FinancialSources(BillingRepository(sessions),adapters,Policy())
    service.reconciliation_repository=ReconciliationRepository(sessions)
    service.unit_economics_repository=UnitRepository(sessions)
    service.control_repository=ControlRepository(sessions)
