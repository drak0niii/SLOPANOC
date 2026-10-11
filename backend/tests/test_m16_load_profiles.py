"""Finite opt-in local soak: immutable archive retries, outage and worker recovery.

Warm resources before registering baseline; sample current RSS, never maxrss.
This certifies this synthetic workload only, not production or PostgreSQL.
"""
import concurrent.futures
import gc
import os
import threading
import time
import pytest
from backend.observability.archive import FilesystemArchive, ArchiveError
from backend.tests.test_m15_archive import request, principal, TID
from backend.tests._m16_harness import resources, envelope

@pytest.mark.parametrize('extended',[False,True])
def test_three_fault_drain_cycles(tmp_path,monkeypatch,extended):
    if extended and os.environ.get('SLOPANOC_M16_EXTENDED') != '1':
        pytest.skip('explicit extended local profile required')
    if not extended and os.environ.get('SLOPANOC_M16_EXTENDED') == '1':
        pytest.skip('CI soak executed separately')
    duration=600 if extended else 30
    archive=FilesystemArchive(tmp_path,'test');raw=request(children=19).SerializeToString()
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=4)
    def healthy():
        result,=archive.submit(raw)
        assert result.state=='COMPLETE'
        archive.reconstruct(principal(),TID,result.digest)
    for _ in range(4): list(pool.map(lambda _:healthy(),range(4)))
    gc.collect();baseline=resources();samples=[];settled=[];timings=[]
    original=archive._read;began=time.monotonic();cpu=time.process_time();operations=0
    try:
        for cycle in range(3):
            until=began+(cycle+1)*duration/3
            # Failed durable write cannot turn retry into new business execution.
            monkeypatch.setattr(archive,'_read',lambda *args: (_ for _ in ()).throw(OSError('synthetic outage')))
            with pytest.raises(ArchiveError): archive.submit(raw)
            monkeypatch.setattr(archive,'_read',original)
            while time.monotonic()<until:
                tick=time.monotonic()
                list(pool.map(lambda _:healthy(),range(4)))
                timings.append(time.monotonic()-tick);operations+=4
                samples.append(resources())
                assert len(samples)<=duration+10
                if len(samples)%30==0: print(f'M16_SOAK_HEARTBEAT seconds={int(time.monotonic()-began)} cycle={cycle+1} operations={operations}',flush=True)
                time.sleep(max(0,1-(time.monotonic()-tick)))
            gc.collect();settled.append(resources())
        allowance=max(16*1024*1024,baseline['rss']*.1)
        assert settled[-1]['rss']-baseline['rss']<=allowance
        assert all(s['threads']<=baseline['threads'] and s['fds']<=baseline['fds'] for s in settled)
        assert not list(tmp_path.rglob('.commit-*'))
        assert len(list(tmp_path.rglob('*.manifest.json')))==1
        assert len(list(tmp_path.rglob('*.otlp')))==1
        envelope('archive_extended' if extended else 'archive_ci_soak',timings,
                 duration_seconds=time.monotonic()-began,operations=operations,
                 cpu_seconds=time.process_time()-cpu,baseline=baseline,settled=settled,
                 peak_rss=max(s['rss'] for s in samples),rss_allowance=allowance,
                 business_dispatches=0,fault_drain_cycles=3,workers=4)
    finally:pool.shutdown(wait=True,cancel_futures=True)

import asyncio
import httpx
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk._logs.export import LogRecordExportResult
from opentelemetry.sdk.metrics.export import MetricExporter,MetricExportResult
from sqlalchemy import select,func
from backend.tests.test_finops_runtime_ledger import accounting
from backend.tests.test_observability_model_provider import fake_model,payload,request as model_request
from backend.observability.finops.usage_ledger import install
from backend.observability.finops.models import LedgerRow
from backend.observability.model_adapter import instrument_model
from backend.observability.runtime import Runtime
from backend.observability.config import ObservabilityConfig
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.tests._api_fakes import FakeRunner

@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get('SLOPANOC_M16_EXTENDED')!='1',reason='explicit extended local application profile')
async def test_application_soak_with_accounting_and_exporter_faults(accounting):
    from contextlib import AsyncExitStack
    import tracemalloc
    diagnostic=os.environ.get('SLOPANOC_M16_HEAP_DIAGNOSTIC')=='1'
    if diagnostic:tracemalloc.start()
    db,repo,ledger=accounting;install(ledger);physical=0;turns=0;samples=[];times=[];settled=[]
    class CounterSink:
        def __init__(self,signal):self.signal=signal;self.count=0
        def export(self,records):
            self.count+=len(records)
            if self.signal=='trace':
                from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
                safe=encode_spans(records).SerializeToString()
            else:
                from opentelemetry.exporter.otlp.proto.common._log_encoder import encode_logs
                safe=encode_logs(records).SerializeToString()
            assert b'M3_' not in safe
            return SpanExportResult.SUCCESS if self.signal=='trace' else LogRecordExportResult.SUCCESS
        def shutdown(self):pass
    class CounterMetrics(MetricExporter):
        def export(self,*args,**kwargs):return MetricExportResult.SUCCESS
        def force_flush(self,*args,**kwargs):return True
        def shutdown(self,*args,**kwargs):pass
    # Production transport does not retain exported snapshots. The heap
    # diagnostic separately demonstrates development capture's bounded caches.
    runtime=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,
                    otel_exporter_mode='local',otel_batch_interval_seconds=.01),
                    exporters={'trace':CounterSink('trace'),'log':CounterSink('log'),'metric':CounterMetrics()})
    exporter=runtime.exporters['trace'];original=exporter.export
    def handler(req):
        nonlocal physical
        physical+=1;return httpx.Response(200,json=payload())
    async with fake_model(handler) as (base,_):
        model=instrument_model(base,'team_manager','orchestration')
        async def execution(*args):
            _=[value async for value in model.generate_content_async(model_request())]
        sessions=ApiSessionService();service=ChatService(sessions,runner=FakeRunner(sessions,side_effect=execution),observability_runtime=runtime)
        ids=[await sessions.create_session() for _ in range(4)]
        async def batch():
            async def one(sid):
                nonlocal turns
                turns+=1;began=time.monotonic()
                _=[event async for event in service.execute_turn_events(sid,'synthetic local ordinary request')]
                times.append(time.monotonic()-began)
            await asyncio.gather(*(one(sid) for sid in ids))
        try:
            await batch();assert runtime.flush(2)
            # SQLite retains its five idle connections/threads. Warm the actual
            # pool, not just four concurrent turns, before measuring drift.
            async with AsyncExitStack() as stack:
                for _ in range(db.engine.pool.size()):
                    await stack.enter_async_context(db.engine.connect())
            from backend.observability.blocking_work import executor
            from backend.observability.reliability_contract import Category
            workers=executor();release=threading.Event()
            warm=[workers.submit(lambda:release.wait(1),Category.TOOL)[0] for _ in range(workers.workers)]
            release.set();await asyncio.gather(*(asyncio.wrap_future(f) for f in warm))
            gc.collect();baseline=resources();begin=time.monotonic();cpu=time.process_time()
            heap_before=tracemalloc.take_snapshot() if diagnostic else None
            for cycle in range(3):
                exporter.export=lambda _:SpanExportResult.FAILURE
                await batch();assert runtime.flush(2)
                exporter.export=original
                until=begin+(cycle+1)*20
                while time.monotonic()<until:
                    tick=time.monotonic();await batch();assert runtime.flush(2)
                    samples.append(resources());await asyncio.sleep(max(0,1-(time.monotonic()-tick)))
                gc.collect();settled.append(resources())
                assert not service._background_turns and not service._run_tasks
                assert (await repo.health('development'))['debt']==0
            async with db.sessions() as s:assert await s.scalar(select(func.count()).select_from(LedgerRow))==physical==turns
            allowance=max(16*1024*1024,baseline['rss']*.1)
            print('M16_APPLICATION_RESOURCES='+__import__('json').dumps(dict(baseline=baseline,settled=settled,pool_size=db.engine.pool.size(),checked_out=db.engine.pool.checkedout())))
            if diagnostic:
                growth=tracemalloc.take_snapshot().compare_to(heap_before,'lineno')
                print('M16_HEAP='+__import__('json').dumps([dict(file=stat.traceback[0].filename.split('/SLOPANOC-main/')[-1],line=stat.traceback[0].lineno,bytes=stat.size_diff,objects=stat.count_diff) for stat in growth[:20]]))
                print('M16_CAPTURE_COUNTS='+__import__('json').dumps(dict(trace_retained=0,log_retained=0,metric_retained=0,turns=turns)))
            assert settled[-1]['rss']-baseline['rss']<=allowance
            assert all(s['threads']<=baseline['threads'] and s['fds']<=baseline['fds'] and s['tasks']<=baseline['tasks'] for s in settled), (baseline,settled)
            assert db.engine.pool.checkedout()==0
            assert runtime.health.snapshot()['trace']['queue_size']==0
            assert runtime.health.snapshot()['trace']['dropped']>0
            envelope('application_accounting_soak',times,duration_seconds=time.monotonic()-begin,turns=turns,
                     physical_provider_calls=physical,ledger_rows=physical,cpu_seconds=time.process_time()-cpu,
                     baseline=baseline,settled=settled,peak_rss=max(s['rss'] for s in samples),
                     rss_allowance=allowance,exporter_fault_cycles=3,all_other_business_dispatches=0,
                     exporter_fixture='otlp_encoding_no_capture')
        finally:
            exporter.export=original;runtime.close()
            if diagnostic:tracemalloc.stop()

def test_capture_memory_is_development_only_and_finite():
    from backend.observability.exporters.local import LocalExporter,LocalMetricExporter
    from pydantic import ValidationError
    # Configuration enforces this separation, not the benchmark's name.
    with pytest.raises(ValidationError,match='Production requires OTLP'):
        ObservabilityConfig(observability_enabled=True,otel_enabled=True,
                            otel_environment='production',otel_exporter_mode='local')
    exporter=LocalExporter('trace',capacity=8)
    exporter.export(tuple(range(20)))
    assert exporter.snapshot()==tuple(range(12,20))
    metrics=LocalMetricExporter()
    for index in range(100):metrics.export(index)
    assert tuple(metrics.records)==tuple(range(36,100))
    print('M16_CAPTURE_CLASSIFICATION=TEST_HARNESS_ONLY production_local_mode_rejected=True trace_fixture_capacity=8 metric_default_capacity=64')
