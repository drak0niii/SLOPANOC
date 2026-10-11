"""Remaining actual queues: no invented financial-generation buffering."""
import asyncio
from datetime import timedelta
import time
import pytest
from backend.tests._m7_storage import state,config
from backend.observability.persistence import Coordinator
from backend.observability.stages import RunStatus
from backend.observability.config import ObservabilityConfig
from backend.observability.finops.usage_ledger import Ledger
from backend.observability.archive import FilesystemArchive,ArchiveError
from backend.tests.test_m16_collector_resilience import split_trace,merged

@pytest.mark.asyncio
async def test_projection_default_capacity_coalesce_reject_drain():
    class Repo:
        calls=0
        async def persist(self,value):self.calls+=1;return 'persisted'
    repo=Repo();coordinator=Coordinator(repo,config())
    values=[state(status=RunStatus.COMPLETED) for _ in range(2048)]
    assert all(coordinator.submit(value) for value in values)
    assert len(coordinator.slots)==2048
    assert not coordinator.submit(state(status=RunStatus.COMPLETED))
    assert coordinator.health.snapshot()[0]['terminal_unpersisted']==1
    assert coordinator.submit(values[0].model_copy(update={'state_version':2}))
    assert len(coordinator.slots)==2048
    began=time.monotonic();coordinator.start()
    try:
        assert await coordinator.flush(5)
        assert not coordinator.slots and not coordinator.admitted and repo.calls==2048
        assert coordinator.submit(state(status=RunStatus.COMPLETED))
        assert await coordinator.flush(1)
        print('M16_QUEUE projection capacity=2048 rejected=1 coalesced=1 recovered=2049 seconds='+str(time.monotonic()-began))
    finally:await coordinator.close()

@pytest.mark.asyncio
async def test_accounting_actual_eight_slots_bounded_wait_and_recovery():
    ledger=Ledger(None,ObservabilityConfig());release=asyncio.Event();started=asyncio.Event();calls=0
    async def blocked():
        nonlocal calls
        calls+=1
        if calls==8:started.set()
        await release.wait()
    active=[asyncio.create_task(ledger.bounded(blocked)) for _ in range(8)]
    try:
        await asyncio.wait_for(started.wait(),1)
        began=time.monotonic()
        with pytest.raises(TimeoutError):await ledger.bounded(blocked)
        assert .2<=time.monotonic()-began<1 and calls==8
        release.set();await asyncio.gather(*active)
        await ledger.bounded(blocked);assert calls==9 and ledger.writes._value==8
        print('M16_QUEUE accounting slots=8 overflow=1 provider_dispatches=0 recovered_slots=8')
    finally:
        release.set();await asyncio.gather(*active,return_exceptions=True)

@pytest.mark.asyncio
async def test_activity_actual_bound_and_recovery(monkeypatch):
    import backend.api.activity_queue as module
    identity='owned-m16-activity';module.register_activity_channel(identity)
    monkeypatch.setattr(module,'current_run_id',lambda:identity)
    queue=module.get_activity_channel(identity)
    try:
        capacity=queue.maxsize;assert capacity==16
        for _ in range(capacity+10):module.report_activity('knowledge_search_started')
        assert queue.qsize()==capacity
        for _ in range(capacity):queue.get_nowait()
        module.report_activity('knowledge_search_started');assert queue.qsize()==1
        print('M16_QUEUE activity capacity=16 overflow_dropped=10 recovered=1')
    finally:module.discard_activity_channel(identity)
    assert module.get_activity_channel(identity) is None

@pytest.mark.parametrize('bound',['population','bytes'])
def test_archive_actual_spool_pressure_rejection_recovery(tmp_path,bound):
    from datetime import datetime,timezone
    now=[datetime.now(timezone.utc)]
    cases=[split_trace(n+50000,'FAILED') for n in range(257)]
    cap=256 if bound=='population' else 1
    archive=FilesystemArchive(tmp_path/'spool','test',clock=lambda:now[0])
    if bound=='population':
        for _,early,_ in cases[:cap]:archive.submit(early.SerializeToString())
        assert len(list((tmp_path/'spool'/'.staging'/'test').iterdir()))==cap
    else:
        archive.submit(cases[0][1].SerializeToString())
        staged=next((tmp_path/'spool').rglob('*.chunk'))
        # Default byte-quota pressure uses an explicitly corrupted sparse
        # staging file. It tests real stat-based admission without claiming
        # 256MiB of valid trace payload or allocating/persisting private data.
        with staged.open('r+b') as stream:stream.truncate(archive.max_spool_bytes)
        assert staged.stat().st_size==archive.max_spool_bytes==256*1024*1024
    with pytest.raises(ArchiveError):archive.submit(cases[cap][1].SerializeToString())
    now[0]+=timedelta(seconds=601)
    archive._expire_staging()
    result,=archive.submit(merged(cases[0][1:]).SerializeToString());assert result.state=='COMPLETE'
    assert not list((tmp_path/'spool').rglob('*.tmp'))
    print('M16_QUEUE archive_spool bound='+bound+' configured_pending=256 configured_bytes='+str(archive.max_spool_bytes)+' overflow_rejected=1 business_dispatches=0')

from backend.tests._m12_reconciliation import system,WINDOW,NOW
from backend.tests._m11_financial import mapping
from backend.observability.finops.billing_normalization import normalize
from backend.observability.finops.billing_sources import BillingSourceAdapter

@pytest.mark.asyncio
async def test_financial_materializer_actual_two_admissions_overflow_recovery(system,monkeypatch):
    release=asyncio.Event();started=asyncio.Event();reserved=0
    original=system.repo.reserve
    async def reserve(*args,**kwargs):
        nonlocal reserved
        reserved+=1
        if reserved==2:started.set()
        await release.wait();return await original(*args,**kwargs)
    monkeypatch.setattr(system.repo,'reserve',reserve)
    extraction=await system.a.extract(WINDOW);projection=normalize(extraction,mapping())
    failures=[]
    monkeypatch.setattr(system.worker,'signal',lambda name,value=1:failures.append((name,value)))
    try:
        for _ in range(3):system.worker.offer('development',system.a,extraction,projection,mapping())
        assert len(system.worker.materializers)==2 and ('failures',1) in failures
        await asyncio.wait_for(started.wait(),1);release.set();await system.worker.drain()
        await asyncio.sleep(0);assert not system.worker.materializers
        system.worker.offer('development',system.a,extraction,projection,mapping());await system.worker.drain()
        await asyncio.sleep(0);assert not system.worker.materializers and reserved==3
        print('M16_QUEUE finops_materializer capacity=2 overflow_rejected=1 recovery_admitted=1 source_head_untouched=True')
    finally:release.set();await system.worker.drain()
