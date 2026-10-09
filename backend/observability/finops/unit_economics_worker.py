"""Off-path bounded capture and generation; no provider/billing jobs or source writes."""
import asyncio
from datetime import datetime, timezone, timedelta
from .billing_contracts import Window, SourceError
from .unit_economics_contracts import Policy
from .unit_registry import Registry as Units
from .unit_drivers import Registry as Drivers
from .unit_economics_repository import turn_scope
from .unit_economics import calculate

class Worker:
    def __init__(self,repository,turns,models,environment,scope=None,drivers=None,policy=None,metrics=None):
        self.repository,self.turns,self.models=repository,turns,models
        self.environment,self.scope=environment,scope
        self.drivers=Drivers.model_validate((drivers or Drivers(environment=environment,financial_scope=scope)).model_dump())
        self.policy=Policy.model_validate((policy or Policy()).model_dump())
        if self.drivers.environment!=environment or self.drivers.financial_scope not in (None,scope):
            raise SourceError('SCOPE_UNPROVEN')
        self.metrics=metrics;self.units=Units();self.task=None;self.lock=asyncio.Lock()
    def signal(self,name,value=1):
        if self.metrics:
            try:self.metrics.record(name,value)
            except Exception:pass
    async def capture_turns(self,window,now):
        if not self.turns:return None
        key,rev=await self.repository.reserve(self.environment,turn_scope(self.environment),window.partition,'M9')
        try:
            snap=await self.turns.snapshot(window,now)
            status=await self.repository.save_snapshot(key,rev,snap)
            self.signal('snapshot_successes')
            if any(v is None for v in snap.counts.values()):self.signal('source_expiry_gaps')
            return status
        except asyncio.CancelledError:raise
        except Exception as exc:
            await self.repository.fail(key,rev,exc.code if isinstance(exc,SourceError) else 'PUBLICATION_FAILED')
            self.signal('snapshot_failures');return 'FAILED'
    async def run_period(self,window,now=None):
        now=now or datetime.now(timezone.utc)
        if not self.scope:return 'SOURCE_UNAVAILABLE'
        key,rev=await self.repository.reserve(self.environment,self.scope,window.partition)
        try:
            async with asyncio.timeout(self.policy.job_seconds):
                financial=await self.repository.financial(self.environment,self.scope,window.partition)
                if not financial:raise SourceError('SOURCE_UNAVAILABLE')
                if self.models:
                    mk,mr=await self.repository.reserve(self.environment,self.scope,window.partition,'M10')
                    snapshot=await self.models.snapshot(window,financial['runtime'],now)
                    await self.repository.save_snapshot(mk,mr,snapshot)
                snapshots=[];ids={}
                for source,scope in (('M9',turn_scope(self.environment)),('M10',self.scope)):
                    _,snap=await self.repository.snapshot(self.environment,scope,window.partition,source)
                    if snap:
                        snapshots.append(snap);ids[source]=snap.identity
                registries=await self.repository.registries(self.units,self.drivers,self.scope)
                result=await asyncio.to_thread(calculate,financial['body']['result'],financial['billing'],financial['runtime'],
                    financial['registry'],snapshots,self.drivers,self.units,self.policy)
                status=await self.repository.publish(key,rev,financial['generation'],ids,registries,result)
                self.signal('publications' if status=='PUBLISHED' else 'identical_generations' if status=='IDENTICAL' else 'superseded')
                self.signal('driver_unavailable',sum('DRIVER_UNAVAILABLE' in r['reasons'] for r in result['results']))
                return status
        except asyncio.CancelledError:raise
        except Exception as exc:
            error=exc.code if isinstance(exc,SourceError) else 'PUBLICATION_FAILED'
            await self.repository.fail(key,rev,error);self.signal('generation_failures')
            if error=='FINANCIAL_IMBALANCE':self.signal('control_failures')
            return 'FAILED'
    async def cycle(self,now=None):
        now=now or datetime.now(timezone.utc);today=now.replace(hour=0,minute=0,second=0,microsecond=0)
        async with self.lock:
            # Capture yesterday before any financial work. No billing prerequisite.
            # Today's preliminary aggregate is allowed but never a complete price.
            for offset in (1,0):
                day=today-timedelta(days=offset)
                async with asyncio.timeout(self.policy.job_seconds):
                    await self.capture_turns(Window(day,day+timedelta(days=1)),now)
            if self.scope:
                from sqlalchemy import select
                from .reconciliation_models import StateRow
                async with self.repository.sessions() as s:
                    periods=(await s.scalars(select(StateRow.period).where(StateRow.environment==self.environment,
                        StateRow.scope==self.scope,StateRow.generation.is_not(None),
                        StateRow.period>=(today-timedelta(days=self.policy.retention_days-1)).date().isoformat())
                        .order_by(StateRow.period))).all()
                # Round-robin historical revisit, bounded per cycle, restart-safe via last_attempt.
                from .unit_economics_models import PublicationRow
                async with self.repository.sessions() as s:
                    rows=(await s.scalars(select(PublicationRow).where(PublicationRow.environment==self.environment,
                        PublicationRow.scope==self.scope,PublicationRow.kind=='ECONOMICS',
                        PublicationRow.period>=(today-timedelta(days=self.policy.retention_days-1)).date().isoformat(),
                        PublicationRow.period<=today.date().isoformat()).limit(self.policy.retention_days))).all()
                checked={r.period:r.last_attempt for r in rows}
                periods.sort(key=lambda p:checked.get(p) or datetime.min.replace(tzinfo=timezone.utc))
                for p in periods[:self.policy.cycle_partitions]:
                    day=datetime.fromisoformat(p).replace(tzinfo=timezone.utc)
                    await self.run_period(Window(day,day+timedelta(days=1)),now)
    def start(self):
        async def drive():
            while True:
                try:await self.cycle()
                except asyncio.CancelledError:raise
                except Exception:self.signal('generation_failures')
                await asyncio.sleep(self.policy.cadence_seconds)
        self.task=asyncio.create_task(drive(),name='finops-unit-economics')
    async def close(self):
        if self.task:
            self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
