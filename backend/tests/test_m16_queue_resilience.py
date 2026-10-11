"""Actual default queues, explicit drop/source-gap and bounded recovery."""
import asyncio
from threading import Event
import time
import pytest
from opentelemetry.sdk.trace.export import SpanExportResult
from backend.observability.config import ObservabilityConfig
from backend.observability.metrics import Health
from backend.observability.exporters.queue import ExportQueue
from backend.observability.slo_sources import Writer,Counts,Snapshot,now_utc

@pytest.mark.parametrize('signal',['trace','log'])
def test_default_sdk_capacity_outage_and_recovery(signal):
    start=Event();release=Event();health=Health();config=ObservabilityConfig()
    class Exporter:
        def export(self,records):
            start.set();assert release.wait(2)
            return SpanExportResult.SUCCESS
        def shutdown(self):pass
    queue=ExportQueue(Exporter(),signal,config,health)
    began=time.monotonic()
    try:
        for _ in range(256):assert queue.put('safe fixture record')
        assert start.wait(1)
        for _ in range(2048):assert queue.put('safe fixture record')
        for _ in range(100):assert not queue.put('safe fixture record')
        assert health.snapshot()[signal]['queue_size']==2048
        assert not queue.flush(10)
        release.set();assert queue.flush(2000)
        snapshot=health.snapshot()[signal]
        assert snapshot['accepted']==snapshot['exported']==2304 and snapshot['dropped']==100
        assert snapshot['queue_size']==0
        print(f'M16_QUEUE signal={signal} capacity=2048 accepted=2304 rejected=100 recovery_seconds={time.monotonic()-began:.3f} business_dispatches=0')
    finally:release.set();queue.shutdown(2000)
    assert not queue._worker.is_alive()

@pytest.mark.asyncio
async def test_actual_slo_writer_1024_gap_never_becomes_healthy():
    release=asyncio.Event();heartbeats=[];saved=0
    class Rollups:
        async def record(self,*args):
            nonlocal saved
            await release.wait();saved+=1
        async def settle(self,*args):pass
        async def heartbeat(self,env,at,coverage):heartbeats.append(coverage)
        async def snapshot(self,*args):return Snapshot('development',available=False,source='TEST_GAP')
        async def prune(self,*args):pass
    writer=Writer(Rollups(),ObservabilityConfig());event=('availability',now_utc(),Counts(good=1))
    try:
        assert all(writer.publish(event) for _ in range(1024))
        assert all(not writer.publish(event) for _ in range(100))
        assert writer.inflight==1024 and writer.lost
        release.set()
        await asyncio.wait_for(writer.drain(),5)
        assert writer.inflight==0 and saved==1024 and heartbeats and not any(heartbeats)
        print('M16_QUEUE signal=slo capacity=1024 accepted=1024 rejected=100 source_coverage=UNKNOWN business_dispatches=0')
    finally:release.set();await writer.close()
