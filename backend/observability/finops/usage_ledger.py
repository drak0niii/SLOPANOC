"""Awaited capture/admission and process-owned accounting lifecycle, independent of OTel."""
import asyncio
import time
from contextvars import Context
from threading import Lock
from collections import Counter
from backend.gateway.safe_error import SafeError, SafeErrorException
from .contracts import identity_for, payload_for
from .repository import utcnow

_current=None
_lock=Lock()

class AdmissionFailed(SafeErrorException):
    def __init__(self):
        super().__init__(SafeError(error_code='connector_unavailable',user_message='Runtime accounting is unavailable. Please try again later.'))

def install(ledger):
    global _current
    with _lock:
        if _current is not None and _current is not ledger: raise RuntimeError('Accounting ownership conflict')
        _current=ledger

def release(ledger):
    global _current
    with _lock:
        if _current is ledger:_current=None

def current():
    with _lock:return _current

class Ledger:
    def __init__(self, repository, config, runtime=None):
        self.repository,self.config,self.runtime=repository,config,runtime
        self.writes=asyncio.Semaphore(config.finops_write_concurrency)
        self.health_counts=Counter();self.task=None;self.stopping=False

    def signal(self, name, value=1):
        self.health_counts[name]+=value
        from .metrics import record
        record(self.runtime,name,value)
        if name in ('admissions_failed','persistence_failures','conflicts'):
            from ..logging import accounting_failure
            accounting_failure(self.runtime)

    async def bounded(self, fn, *args):
        from ..deadlines import current_budget
        budget=current_budget()
        seconds=min(self.config.finops_write_seconds,budget.remaining) if budget else self.config.finops_write_seconds
        async with asyncio.timeout(max(.001,seconds)):
            async with asyncio.timeout(self.config.finops_admission_wait_seconds):
                await self.writes.acquire()
            try:
                for attempt in range(self.config.finops_write_attempts):
                    try:return await fn(*args)
                    except (ValueError,TypeError):raise
                    except Exception:
                        if attempt+1==self.config.finops_write_attempts:raise
                        await asyncio.sleep(.01*(attempt+1))
            finally:self.writes.release()

    async def admission(self, attempt):
        self.signal('admissions_attempted')
        began=time.monotonic()
        try:
            identity=identity_for(attempt,self.config)
            await self.bounded(self.repository.admit,identity)
            attempt.accounting_identity=identity
            return identity
        except Exception:
            self.signal('admissions_failed')
            raise AdmissionFailed() from None
        finally:
            from .metrics import record
            record(self.runtime,'admission_duration',time.monotonic()-began)

    async def started(self, attempt):
        try:
            await self.bounded(self.repository.start,attempt.accounting_identity)
            attempt.accounting_started=True
            self.signal('provider_started')
        except Exception:
            self.signal('admissions_failed')
            raise AdmissionFailed() from None

    async def abort_admission(self, attempt):
        if getattr(attempt,'accounting_identity',None) is not None and not getattr(attempt,'accounting_started',False):
            try:
                async with asyncio.timeout(.25):
                    await self.bounded(self.repository.not_started,attempt.accounting_identity)
            except Exception:self.signal('persistence_failures')

    async def prepare(self, attempt):
        """One total admission+start budget; no provider dispatch after failure."""
        from ..deadlines import check
        try:
            async with asyncio.timeout(self.config.finops_write_seconds):
                await self.admission(attempt)
                check()
                await self.started(attempt)
        except asyncio.CancelledError:
            await self.abort_admission(attempt)
            raise
        except Exception:
            await self.abort_admission(attempt)
            raise AdmissionFailed() from None

    async def capture(self, attempt):
        if getattr(attempt,'accounting_done',False) or not getattr(attempt,'accounting_started',False):return
        attempt.accounting_done=True
        self.signal('observations')
        began=time.monotonic()
        try:
            # Total final accounting budget, including finished/inbox/materialization.
            async with asyncio.timeout(self.config.finops_write_seconds):
                obs=getattr(attempt,'observation',None)
                if obs is None:raise ValueError('Missing finalized usage')
                identity=attempt.accounting_identity
                await self.bounded(self.repository.finished,identity,obs.completed_at)
                payload=payload_for(identity,obs,self.config)
                if payload.usage.availability!='KNOWN':self.signal('metadata_missing')
                result=await self.bounded(self.repository.capture,payload)
                self.signal({'ACCEPTED':'inbox_accepted','DUPLICATE':'duplicates','CONFLICT':'conflicts'}[result])
                if result!='CONFLICT':
                    result=await self.bounded(self.repository.materialize,identity.event_id)
                    self.signal({'PERSISTED':'ledger_persisted','DUPLICATE':'duplicates','BUSY':'pending','CONFLICT':'conflicts'}[result])
        except asyncio.CancelledError:
            # Durable STARTED receipt already survives cancellation; preserve cancel.
            self.signal('persistence_failures')
            raise
        except Exception:
            self.signal('persistence_failures')
            try:
                async with asyncio.timeout(self.config.finops_admission_wait_seconds):
                    await self.repository.failed_capture(attempt.accounting_identity)
            except Exception:pass  # durable STARTED/FINISHED receipt is the obligation
        finally:
            from .metrics import record
            record(self.runtime,'capture_duration',time.monotonic()-began)

    async def recover(self):
        from .recovery import recover
        return await recover(self)

    def start(self):
        self.task=asyncio.get_running_loop().create_task(self.drive(),context=Context())

    async def drive(self):
        while not self.stopping:
            try:
                async with asyncio.timeout(self.config.finops_recovery_seconds):
                    await self.recover()
                    await self.repository.checkpoint(self.config.otel_environment,limit=self.config.finops_recovery_batch)
                    from .slo_source import AccountingSource
                    from ..slo_evaluator import evaluate
                    from ..slo_metrics import publish_result
                    source=AccountingSource(self.repository)
                    publish_result(self.runtime,evaluate('cost_ledger_completeness',await source.snapshot(self.config.otel_environment,'cost_ledger_completeness',utcnow()),now=utcnow()))
                    from ..slo_contract import COST_COVERAGE_WINDOW_SECONDS
                    from ..slo_metrics import publish_cost_coverage
                    snap=await source.snapshot(self.config.otel_environment,'cost_ledger_completeness',utcnow())
                    publish_cost_coverage(self.runtime,evaluate('cost_ledger_completeness',snap,now=utcnow(),window_seconds=COST_COVERAGE_WINDOW_SECONDS))
                    health=await self.repository.health(self.config.otel_environment)
                    for name,key in (('pending','pending'),('debt','debt')):
                        from .metrics import record
                        record(self.runtime,name,health[key])
            except asyncio.CancelledError:raise
            except Exception:self.signal('persistence_failures')
            await asyncio.sleep(self.config.finops_checkpoint_seconds)

    async def close(self):
        self.stopping=True
        try:
            async with asyncio.timeout(self.config.finops_shutdown_seconds):
                if self.task:
                    self.task.cancel()
                    await asyncio.gather(self.task,return_exceptions=True)
                await self.recover()
        except Exception:self.signal('persistence_failures')
