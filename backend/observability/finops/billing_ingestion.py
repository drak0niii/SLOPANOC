"""Isolated asynchronous source worker and restart-safe retained partition rescans."""
import asyncio
from datetime import datetime,timedelta,timezone
from .billing_contracts import Policy,Window,SourceError
from .billing_normalization import normalize
from .pricing import normalize as normalize_prices
from .billing_sources import normalize_focus
from .sku_mapping import Registry
from .billing_metrics import Metrics

class Ingestion:
    def __init__(self,repository,environment,adapters=(),policy=None,mapping=None,runtime=None):
        if environment not in ('local','development','staging','production'):raise SourceError('INVALID_POLICY')
        self.repository=repository;self.environment=environment
        self.adapters={a.source:a for a in adapters}; self.policy=policy or Policy();self.mapping=mapping or Registry()
        self.metrics=Metrics(runtime,environment);self.task=None
        self.lock=asyncio.Lock()

    async def ingest(self,source,window):
        import time
        adapter=self.adapters[source];began=time.monotonic();key=revision=None
        self.metrics.add('extractions',source)
        try:
            async with asyncio.timeout(self.policy.query_seconds+2):
                key,revision=await self.repository.reserve(self.environment,adapter,window)
                extraction=await adapter.extract(window)
                if extraction.window!=window or extraction.mode!=adapter.mode:raise SourceError('INCOMPLETE_GENERATION')
                fn={'DETAILED_BILLING':normalize,'PRICING_EXPORT':normalize_prices,'FOCUS':normalize_focus}[source]
                projection=await asyncio.to_thread(fn,extraction,self.mapping,self.policy)
                self.metrics.add('bytes_processed',source,extraction.bytes_processed)
                result=await self.repository.publish(key,revision,extraction,projection)
                self.metrics.add('identical_generations' if result=='IDENTICAL' else 'publications',source)
                for name,value in [('rows',projection.rows),('late_rows',projection.late_rows),('correction_rows',projection.correction_rows),('unmapped_rows',projection.unmapped_rows)]:
                    self.metrics.add(name,source,value)
                return result
        except asyncio.CancelledError:
            if key is not None:
                try:
                    async with asyncio.timeout(1):await asyncio.shield(self.repository.fail(key,revision,'SOURCE_UNAVAILABLE'))
                except Exception:pass
            raise
        except Exception as exc:
            error=exc.code if isinstance(exc,SourceError) else 'SOURCE_UNAVAILABLE'
            self.metrics.add('failures',source)
            if error=='SCHEMA_INCOMPATIBLE':self.metrics.add('schema_incompatible',source)
            if key is not None:
                try:
                    async with asyncio.timeout(1):await self.repository.fail(key,revision,error)
                except Exception:pass
            # Emit only constant vocabulary through the existing JSON logging pipeline.
            import logging
            logging.getLogger('backend.observability.finops').warning('financial_source_unavailable')
            return 'FAILED'
        finally:self.metrics.duration(source,time.monotonic()-began)

    async def cycle(self,now=None,max_partitions=2):
        """Recent overlap plus least-recently-checked retained day, durable backlog.

        Retained history is checked in bounded passes. No settled-period assumption;
        restarts reconstruct remaining work from per-partition state, not RAM cursors.
        """
        if not 1<=max_partitions<=32:raise SourceError('INVALID_POLICY')
        now=now or datetime.now(timezone.utc);today=now.replace(hour=0,minute=0,second=0,microsecond=0)
        async with self.lock:
            for source,adapter in self.adapters.items():
                rows=await self.repository.current(self.environment,source,adapter.scope,include_cache=False)
                checked={r.partition:r.last_success for r,_,_ in rows}
                days=[today-timedelta(days=i) for i in range(self.policy.retained_days)]
                cadence=self.policy.pricing_cadence_seconds if source=='PRICING_EXPORT' else self.policy.billing_cadence_seconds
                due=[d for d in days if checked.get(d.date().isoformat()) is None or (now-checked[d.date().isoformat()]).total_seconds()>=cadence]
                # Split finite pass between recent overlap and old coverage. Cycling
                # least-checked days ensures arbitrarily late retained rows are revisited.
                recent=[d for d in due if (today-d).days<max(self.policy.lookback_days,62)]
                old=[d for d in due if d not in recent]
                rank=lambda d:checked.get(d.date().isoformat()) or datetime.min.replace(tzinfo=timezone.utc)
                recent.sort(key=lambda d:(rank(d),-d.timestamp()))
                old.sort(key=lambda d:(rank(d),-d.timestamp()))
                recent_slots=max(1,max_partitions//2)
                chosen=recent[:recent_slots]+old[:max_partitions//2]
                remaining=[d for d in recent+old if d not in chosen]
                chosen+=remaining[:max_partitions-len(chosen)]
                for d in chosen:await self.ingest(source,Window(d,d+timedelta(days=1)))

    def start(self):
        if self.task is not None:raise RuntimeError('Billing worker already started')
        async def run():
            while True:
                try:await self.cycle()
                except asyncio.CancelledError:raise
                except Exception:pass # Durable source state reports failures; no business coupling.
                await asyncio.sleep(min(self.policy.billing_cadence_seconds,60))
        self.task=asyncio.create_task(run(),name='finops-source-ingestion')

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                async with asyncio.timeout(3):await self.task
            except (asyncio.CancelledError,TimeoutError):pass
            self.task=None
