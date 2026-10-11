"""Concurrent canonical turns and real bounded-worker/SSE contracts."""
import asyncio
import time
from threading import Event
import pytest
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.tests._api_fakes import FakeRunner
from backend.observability.runtime import Runtime
from backend.observability.config import ObservabilityConfig
from backend.observability.blocking_work import BlockingExecutor, AdmissionRejected
from backend.observability.reliability_contract import Category
from backend.observability.delivery import DeliveryBuffer, Backpressure
from backend.observability.deadlines import Deadline
from backend.tests._m16_harness import Dispatches, envelope, resources

@pytest.mark.asyncio
@pytest.mark.parametrize('concurrency',[4,8,12])
@pytest.mark.parametrize('fault',['healthy','exception','disconnect'])
async def test_actual_turns_no_replay_terminal_and_lock_cleanup(concurrency,fault):
    runtime=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='local',
                    otel_batch_interval_seconds=.01))
    sessions=ApiSessionService();calls=Dispatches();timings=[]
    async def execution(*args):
        calls.hit('agent')
        await asyncio.sleep(.005)
        if fault=='exception':raise OSError('PRIVATE_SENTINEL_SECRET')
    service=ChatService(sessions,runner=FakeRunner(sessions,side_effect=execution),observability_runtime=runtime)
    try:
        async def one():
            sid=await sessions.create_session();began=time.monotonic();calls.hit('turn')
            iterator=service.execute_turn_events(sid,'synthetic local ordinary request')
            await iterator.__anext__()
            if fault=='disconnect':await iterator.aclose()
            else:
                try:_=[item async for item in iterator]
                except OSError:pass
            for _ in range(100):
                if not service._background_turns:break
                await asyncio.sleep(.005)
            async with sessions.lock_for(sid,'default'):pass
            timings.append(time.monotonic()-began)
        await asyncio.gather(*(one() for _ in range(concurrency)))
        await asyncio.gather(*tuple(service._background_turns),return_exceptions=True)
        calls.expect(turn=concurrency,agent=concurrency)
        assert runtime.flush(2)
        roots=[s for s in runtime.exporters['trace'].snapshot() if s.name=='slopanoc.turn']
        assert len(roots)==concurrency and len({s.context.trace_id for s in roots})==concurrency
        assert all(len([e for e in s.events if e.name.startswith('turn.')])==1 for s in roots)
        assert not service._background_turns and not service._run_tasks
        envelope('interactive_GENERAL',timings,concurrency=concurrency,fault=fault,dispatches=dict(calls.counts),resources=resources())
    finally:runtime.close()

@pytest.mark.asyncio
async def test_configured_worker_saturation_and_recovery():
    executor=BlockingExecutor(8,16);release=Event();started=Event();calls=Dispatches()
    def blocked():
        calls.hit('tool');started.set();release.wait(2)
    futures=[]
    try:
        for _ in range(24):futures.append(executor.submit(blocked,Category.TOOL)[0])
        assert executor.occupied==24
        for _ in range(24):
            with pytest.raises(AdmissionRejected):executor.submit(blocked,Category.TOOL)
        assert len(executor._threads)==8
    finally:release.set()
    await asyncio.gather(*(asyncio.wrap_future(f) for f in futures))
    for _ in range(100):
        if executor.occupied==0:break
        await asyncio.sleep(.001)
    assert executor.occupied==0
    calls.expect(tool=24)

@pytest.mark.asyncio
@pytest.mark.parametrize('bound',['count','bytes','oversized'])
async def test_sse_saturation_detach_then_completion(bound):
    queue=DeliveryBuffer(capacity=2,byte_limit=100)
    item={'text':'x'*60} if bound!='count' else {'text':'x'}
    if bound=='oversized':item={'text':'x'*101}
    if bound!='oversized':
        await queue.put(item)
        if bound=='count':await queue.put(item)
    with pytest.raises(Backpressure):await queue.put(item)
    assert queue.queue.qsize()<=2 and queue.bytes<=100
    queue.detach();queue.finish()
    assert queue.bytes==0 and await queue.put(item) is False
    assert queue.done.is_set()
