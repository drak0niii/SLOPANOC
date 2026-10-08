"""M6 local fault injection: deadlines, leases, bounded work and delivery."""
import asyncio
from contextlib import suppress
from threading import Event
from types import SimpleNamespace
import time
import pytest
from backend.observability.config import ObservabilityConfig
from backend.observability.deadlines import (Deadline, DeadlineExceeded, bind, boundary,
    child_budget, cleanup_scope, enforce, retry_allowed)
from backend.observability.reliability_contract import Category, project
from backend.observability.watchdog import Watchdog
from backend.observability.blocking_work import BlockingExecutor, AdmissionRejected, defer_business
from backend.observability.delivery import DeliveryBuffer, Backpressure, HEARTBEAT, ReliableStreamingResponse
from backend.observability.stages import RunStatus


def config(**values):
    return ObservabilityConfig(turn_timeout_seconds=.3,cleanup_timeout_seconds=.1,
        watchdog_stall_seconds=.03,heartbeat_seconds=.01,watchdog_check_seconds=.01,
        queue_wait_timeout_seconds=.02,sse_delivery_timeout_seconds=.02,**values)


def controller(**values):
    return Watchdog(None,config(**values))

@pytest.mark.parametrize('category',list(Category))
def test_child_caps_never_extend_parent(category):
    clock=[100.]
    parent=Deadline(105.,Category.TURN,lambda:clock[0])
    child=parent.child(category,20)
    assert child.absolute==105.
    clock[0]=104.
    assert child.remaining==1
    grandchild=child.child(Category.MODEL,20)
    assert grandchild.absolute==105
    clock[0]=105.
    with pytest.raises(DeadlineExceeded) as error:
        grandchild.check()
    assert error.value.category==Category.TURN

@pytest.mark.asyncio
async def test_nested_timeout_attributes_expired_ancestor():
    parent=Deadline(time.monotonic()+.02,Category.TURN)
    with bind(parent):
        with pytest.raises(DeadlineExceeded) as error:
            async with boundary(Category.MODEL) as child:
                assert child.absolute==parent.absolute
                async with enforce(child):
                    await asyncio.Event().wait()
        assert error.value.category==Category.TURN

@pytest.mark.asyncio
async def test_cleanup_uses_one_reserve_inside_total():
    owner=controller()
    with bind(owner.work,owner):
        owner.seal()
        first=owner.start_cleanup()
        assert first.absolute <= owner.total.absolute
        async with cleanup_scope():
            await asyncio.sleep(.005)
            async with cleanup_scope():
                assert child_budget(Category.PERSISTENCE).absolute <= first.absolute
        assert owner.start_cleanup() is first
        with pytest.raises(asyncio.CancelledError):
            child_budget(Category.TOOL)

@pytest.mark.asyncio
async def test_cleanup_timeout_is_bounded_and_truthful():
    owner=controller()
    with bind(owner.work,owner):
        owner.seal()
        started=time.monotonic()
        with pytest.raises(DeadlineExceeded) as error:
            async with cleanup_scope():
                await asyncio.Event().wait()
        assert error.value.category==Category.CLEANUP
        assert owner.cleanup_outcome=='timeout'
        assert time.monotonic()-started < .2


def test_watchdog_stall_resume_does_not_become_terminal():
    clock=[0.]
    turn=SimpleNamespace(status=RunStatus.RUNNING,_root=SimpleNamespace(add_event=lambda *a:None))
    owner=Watchdog(turn,config(),clock=lambda:clock[0])
    clock[0]=.04;owner.poll();owner.poll()
    assert turn.status==RunStatus.STALLED
    assert sum(n=='run.stalled' for n,_,_ in owner.events)==1
    clock[0]=.06;owner.material()
    assert turn.status==RunStatus.RUNNING and owner.stalled_at is None
    assert sum(n=='reliability.progress_resumed' for n,_,_ in owner.events)==1
    clock[0]=.1;owner.poll()
    assert turn.status==RunStatus.STALLED
    clock[0]=.21;owner.poll();owner.poll()
    assert owner.cause.category==Category.TURN
    assert sum(n=='reliability.deadline_exceeded' for n,_,_ in owner.events)==1

@pytest.mark.asyncio
async def test_monitor_failure_keeps_independent_deadline(monkeypatch):
    owner=controller()
    owner.poll=lambda: (_ for _ in ()).throw(RuntimeError('PRIVATE'))
    async def driver():
        with bind(owner.work,owner):
            owner.start(asyncio.current_task())
            try:
                await asyncio.Event().wait()
            finally:
                async with cleanup_scope():
                    await owner.stop()
    task=asyncio.create_task(driver())
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task,.5)
    assert isinstance(owner.cause,DeadlineExceeded)
    assert any(n=='reliability.controller_failed' for n,_,_ in owner.events)

@pytest.mark.asyncio
async def test_worker_timeout_retains_capacity_and_discards_deferred_write():
    executor=BlockingExecutor(1,0)
    entered,release=Event(),Event(); business=[]
    @defer_business
    def write(value):business.append(value)
    def blocking():
        entered.set();release.wait(1);write('PRIVATE_RESULT');return 'PRIVATE_RESULT'
    try:
        with pytest.raises(DeadlineExceeded):
            await executor.run(blocking,budget=Deadline(time.monotonic()+.02,Category.TOOL))
        assert entered.is_set() and executor.occupied==1
        with pytest.raises(AdmissionRejected):executor.submit(lambda:None,Category.TOOL)
        assert business==[]
    finally:release.set()
    for _ in range(100):
        if executor.occupied==0:break
        await asyncio.sleep(.002)
    assert executor.occupied==0 and business==[]
    assert executor.history[-1].caller=='timeout'
    assert executor.history[-1].underlying=='terminated' and executor.history[-1].late

@pytest.mark.asyncio
async def test_pending_cancel_never_egresses_and_capacity_releases_on_dequeue():
    executor=BlockingExecutor(1,1);release=Event();started=Event();calls=[]
    first,_=executor.submit(lambda:(started.set(),release.wait(1)),Category.TOOL)
    try:
        with pytest.raises(DeadlineExceeded):
            await executor.run(lambda:calls.append('remote'),budget=Deadline(time.monotonic()+.02,Category.TOOL))
        assert executor.occupied==2 and calls==[]
    finally:release.set()
    for _ in range(100):
        if not executor.occupied:break
        await asyncio.sleep(.002)
    assert calls==[] and executor.occupied==0

@pytest.mark.asyncio
async def test_worker_success_commits_deferred_writes_once():
    executor=BlockingExecutor(1,0);calls=[]
    @defer_business
    def write():calls.append('commit')
    def fn():write();return 42
    assert await executor.run(fn)==42
    assert calls==['commit']

@pytest.mark.asyncio
async def test_telemetry_failure_does_not_disable_timeout(monkeypatch):
    import backend.observability.reliability_metrics as metrics
    monkeypatch.setattr(metrics,'record',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('PRIVATE')))
    with pytest.raises(DeadlineExceeded):
        async with enforce(Deadline(time.monotonic()+.01,Category.MODEL)):
            await asyncio.Event().wait()

@pytest.mark.asyncio
async def test_count_byte_queue_full_has_independent_failure_control():
    owner=controller()
    with bind(owner.work,owner):
        buffer=DeliveryBuffer(capacity=1,byte_limit=20)
        await buffer.put({'x':1})
        with pytest.raises(Backpressure):await buffer.put({'x':2})
        assert buffer.done.is_set() and buffer.queue.qsize()==1 and buffer.bytes<=20
        with pytest.raises(Backpressure):await buffer.get()
        buffer.detach()
        assert buffer.bytes==0 and await buffer.put({'big':'x'*1000}) is False

@pytest.mark.asyncio
async def test_queue_oversize_explicit_and_finish_drains_in_order():
    buffer=DeliveryBuffer(capacity=2,byte_limit=10)
    with pytest.raises(Backpressure):await buffer.put({'x':'SECRET'*100})
    assert buffer.queue.empty() and buffer.done.is_set()
    buffer=DeliveryBuffer(capacity=2,byte_limit=100)
    await buffer.put(1);await buffer.put(2);buffer.finish()
    assert [await buffer.get(),await buffer.get(),await buffer.get()]==[1,2,None]

@pytest.mark.asyncio
async def test_heartbeat_does_not_reset_watchdog_progress():
    owner=controller()
    with bind(owner.work,owner):
        buffer=DeliveryBuffer();before=owner.last_progress
        assert await buffer.get(heartbeat=.001) is HEARTBEAT
        assert owner.last_progress==before

@pytest.mark.asyncio
async def test_asgi_send_timeout_closes_relay():
    owner=controller();closed=[]
    async def body():
        try:yield 'data: test\n\n'
        finally:closed.append(True)
    response=ReliableStreamingResponse(body())
    sends=0
    async def send(message):
        nonlocal sends
        sends+=1
        if sends>1:await asyncio.Event().wait()
    with bind(owner.work,owner):
        with pytest.raises(OSError,match='sse_delivery_timeout'):
            await response.stream_response(send)
    assert closed==[True]


def test_retry_denial_preserves_absolute_budget_and_no_new_attempt():
    owner=controller(retry_minimum_seconds=.1)
    with bind(Deadline(time.monotonic()+.03,Category.MODEL),owner):
        with pytest.raises(DeadlineExceeded):retry_allowed(.01)
    with bind(Deadline(time.monotonic()+.15,Category.MODEL),owner):retry_allowed(.01)


def test_safe_reliability_projection_never_accepts_content_or_unbounded_values():
    assert project({'slopanoc.outcome_certainty':'OUTCOME_UNKNOWN','slopanoc.deadline_category':'tool',
        'slopanoc.remaining_seconds':float('nan'),'slopanoc.progress_age_seconds':-1,
        'slopanoc.worker_disposition':'SECRET','prompt':'PRIVATE','run_id':'PRIVATE'})=={
        'slopanoc.outcome_certainty':'OUTCOME_UNKNOWN','slopanoc.deadline_category':'tool'}


def test_effective_caps_are_read_only_versioned_without_endpoints():
    first=ObservabilityConfig();second=ObservabilityConfig(tool_timeout_seconds=31)
    projected={v.setting:v for v in first.effective_configuration()}
    assert projected['tool_timeout_seconds'].value==30 and projected['tool_timeout_seconds'].read_only
    assert first.effective_configuration()[0].config_version!=second.effective_configuration()[0].config_version
    assert not any('endpoint' in k for k in projected if k!='collector_endpoint_configured')

@pytest.mark.asyncio
@pytest.mark.parametrize('enabled',[False,True])
async def test_root_timeout_late_worker_lock_release_and_next_turn(monkeypatch,enabled):
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.observability.runtime import Runtime
    from backend.observability.turn_trace import current_turn
    from backend.api.streaming_events import StreamEventType
    from backend.observability.agent_instrumentation import active_execution
    cfg=config()
    if enabled:cfg=cfg.model_copy(update={'observability_enabled':True,'otel_enabled':True,'otel_exporter_mode':'local'})
    runtime=Runtime(cfg);sessions=ApiSessionService();sid=await sessions.create_session()
    service=ChatService(sessions,observability_runtime=runtime)
    executor=BlockingExecutor(1,0);entered,release=Event(),Event();late_context=[];turns=[]
    def worker():
        entered.set();release.wait(1)
        late_context.append((current_turn(),active_execution()))
        return 'SECRET_RESULT'
    async def events(sequencer,*args):
        turns.append(current_turn())
        await executor.run(worker,category=Category.TOOL)
        yield sequencer.build(StreamEventType.RUN_COMPLETED,{'outcome':'ok'})
    monkeypatch.setattr(service,'_run_turn_events',events)
    try:
        output=[e async for e in service.execute_turn_events(sid,'hello') if e is not None]
        await asyncio.gather(*tuple(service._background_turns),return_exceptions=True)
        turn=turns[0]
        assert turn.status==RunStatus.TIMEOUT and executor.occupied==1 and entered.is_set()
        assert output[-1].data=={'outcome':'error'}
        assert not sessions.lock_for(sid).locked()
        assert turn.snapshot().workers[-1][1:]==('cancelled','running','OUTCOME_UNKNOWN')
        terminal=turn.terminal_at
        release.set()
        for _ in range(100):
            if executor.occupied==0:break
            await asyncio.sleep(.002)
        assert executor.occupied==0 and late_context==[(None,None)]
        assert turn.status==RunStatus.TIMEOUT and turn.terminal_at==terminal
        assert any(n=='reliability.late_completion' for n,_,_ in turn.reliability.events)
        async def next_events(sequencer,*args):
            yield sequencer.build(StreamEventType.RUN_COMPLETED,{'outcome':'ok'})
        monkeypatch.setattr(service,'_run_turn_events',next_events)
        second=[e async for e in service.execute_turn_events(sid,'next') if e is not None]
        assert second[-1].data=={'outcome':'ok'} and not sessions.lock_for(sid).locked()
        if enabled:
            runtime.flush(1)
            roots=[s for s in runtime.exporters['trace'].snapshot() if s.name=='slopanoc.turn']
            assert len(roots)==2
            first=next(s for s in roots if s.attributes['slopanoc.status']=='TIMEOUT')
            assert sum(e.name=='turn.timeout' for e in first.events)==1
            assert first.end_time>=first.start_time
    finally:release.set();runtime.close()

@pytest.mark.asyncio
async def test_side_effect_unknown_no_retry_no_late_authority_mutation(monkeypatch):
    import backend.observability.blocking_work as work
    import backend.observability.reliability_metrics as metrics
    monkeypatch.setattr(work,'_executor',BlockingExecutor(1,0))
    monkeypatch.setattr(metrics,'record',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('PRIVATE')))
    release,entered=Event(),Event();calls=[]
    canonical=SimpleNamespace(state={'approved':True,'proposal':'original'})
    def side_effect(tool_context):
        calls.append('egress');entered.set();release.wait(1)
        tool_context.state['approved']=False
        tool_context.state['proposal']='consumed'
        return {'status':'executed'}
    try:
        with bind(Deadline(time.monotonic()+.02,Category.TOOL)):
            with pytest.raises(DeadlineExceeded):await work.isolated_call(side_effect,tool_context=canonical)
        assert calls==['egress'] and entered.is_set()
        assert canonical.state=={'approved':True,'proposal':'original'}
        # Timeout leaves outcome uncertain while the actual worker remains active.
        assert work._executor.occupied==1
    finally:release.set()
    for _ in range(100):
        if not work._executor.occupied:break
        await asyncio.sleep(.002)
    assert canonical.state=={'approved':True,'proposal':'original'} and calls==['egress']

@pytest.mark.asyncio
async def test_waiting_lock_timeout_never_acquires_later():
    lock=asyncio.Lock();await lock.acquire()
    async def waiter():
        async with enforce(Deadline(time.monotonic()+.02,Category.LOCK)):
            async with lock:pytest.fail('timed-out waiter acquired lock')
    with pytest.raises(DeadlineExceeded):await waiter()
    assert lock.locked();lock.release()
    async with lock:assert lock.locked()
    assert not lock.locked()

@pytest.mark.asyncio
async def test_parallel_deadlines_and_progress_are_isolated():
    first,second=controller(),controller();seen=[]
    async def run(owner,cap):
        with bind(owner.work,owner):
            owner.material()
            own=child_budget(Category.MODEL)
            seen.append((owner,own.absolute))
            with pytest.raises(DeadlineExceeded):
                async with enforce(Deadline(time.monotonic()+cap,Category.MODEL,parent=own)):
                    await asyncio.Event().wait()
    await asyncio.gather(run(first,.01),run(second,.03))
    assert seen[0][1]==first.work.absolute and seen[1][1]==second.work.absolute
    assert first.changed is not second.changed and first.work is not second.work
    assert first.cause is second.cause is None

@pytest.mark.asyncio
async def test_generator_cleanup_hang_still_releases_owned_lock(monkeypatch):
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.observability.runtime import Runtime
    from backend.observability.turn_trace import current_turn
    from backend.api.streaming_events import StreamEventType
    runtime=Runtime(config());sessions=ApiSessionService();sid=await sessions.create_session()
    service=ChatService(sessions,observability_runtime=runtime);turns=[]
    async def events(sequencer,*args):
        turns.append(current_turn())
        try:await asyncio.Event().wait()
        finally:await asyncio.Event().wait()
        yield sequencer.build(StreamEventType.RUN_COMPLETED,{'outcome':'ok'})
    monkeypatch.setattr(service,'_run_turn_events',events)
    try:
        await asyncio.wait_for(asyncio.create_task(_consume(service,sid)),.6)
        assert turns[0].status==RunStatus.TIMEOUT
        assert turns[0].reliability.cleanup_outcome=='timeout'
        assert not sessions.lock_for(sid).locked()
    finally:runtime.close()

async def _consume(service,sid):
    return [e async for e in service.execute_turn_events(sid,'hello')]

@pytest.mark.asyncio
async def test_real_model_timeout_keeps_one_logical_operation():
    from backend.tests.test_observability_model_provider import fake_model,request,spans
    from backend.observability.model_adapter import instrument_model
    from backend.observability.runtime import Runtime
    from backend.observability.turn_trace import TurnTrace
    runtime=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='local',model_timeout_seconds=.02))
    turn=TurnTrace('m6-model','session',runtime);owner=Watchdog(turn,runtime.config);turn.reliability=owner;calls=[]
    async def reply(req):calls.append(1);await asyncio.Event().wait()
    try:
        async with fake_model(reply) as (base,_):
            with turn.attached(),bind(owner.work,owner):
                with pytest.raises(DeadlineExceeded) as error:
                    _=[e async for e in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
                assert error.value.category==Category.MODEL
                turn.finish(error.value)
        samples=spans(runtime)
        assert calls==[1]
        logical=[s for s in samples if s.name=='slopanoc.model.operation']
        assert len(logical)==1 and logical[0].attributes['slopanoc.status']=='TIMEOUT'
        physical=[s for s in samples if s.name=='gen_ai.request']
        assert len(physical)==1 and physical[0].attributes['slopanoc.status']=='TIMEOUT'
        assert all(s.end_time is not None for s in samples)
        assert turn.status==RunStatus.TIMEOUT
    finally:runtime.close()

@pytest.mark.asyncio
async def test_real_gateway_worker_timeout_unknown_and_no_retry(monkeypatch):
    from backend.gateway.power_automate_client import PowerAutomateClient
    from backend.config.settings import Settings
    from backend.observability.runtime import Runtime
    from backend.observability.turn_trace import TurnTrace
    runtime=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='local'))
    turn=TurnTrace('m6-write','session',runtime);owner=Watchdog(turn,runtime.config);turn.reliability=owner
    release,entered=Event(),Event();calls=[];executor=BlockingExecutor(1,0)
    def post(url,**kwargs):
        calls.append(kwargs['timeout']);entered.set();release.wait(1)
        return SimpleNamespace(status_code=200,headers={},json=lambda:{'sent':True})
    monkeypatch.setattr('backend.gateway.power_automate_client.requests.post',post)
    client=PowerAutomateClient(Settings(env={'SLOPANOC_POWER_AUTOMATE_GATEWAY_URL':'https://fake.invalid'}))
    try:
        with turn.attached(),bind(Deadline(time.monotonic()+.02,Category.TOOL),owner):
            with pytest.raises(DeadlineExceeded) as error:
                await executor.run(client.send_message,'chat','PRIVATE_MESSAGE')
            turn.finish(error.value)
        assert calls and calls[0]<=.02 and entered.is_set() and executor.occupied==1
        release.set()
        for _ in range(100):
            if not executor.occupied:break
            await asyncio.sleep(.002)
        runtime.flush(1)
        deps=[s for s in runtime.exporters['trace'].snapshot() if s.name=='http.client']
        assert len(deps)==len(calls)==1
        assert deps[0].attributes['slopanoc.dependency']=='power_automate_gateway'
        assert deps[0].attributes['slopanoc.outcome_certainty']=='OUTCOME_UNKNOWN'
        assert deps[0].attributes['slopanoc.caller_disposition']=='timeout'
        assert turn.status==RunStatus.TIMEOUT
    finally:release.set();runtime.close()


def test_metric_gate_rejects_dynamic_labels_and_invalid_histogram():
    from backend.tests._m5_dependencies import environment,points,capture
    from backend.observability.reliability_metrics import record,safe_point
    from dataclasses import replace
    with environment() as (runtime,turn):
        record('stall',Category.TURN,status='STALLED',runtime=runtime)
        record('stall_duration',Category.TURN,.1,runtime=runtime)
        assert points(runtime,'slopanoc.turn.stalls')[0].value==1
        meter=runtime.meter_provider.get_meter('slopanoc.observability')
        bad=meter.create_counter('slopanoc.stage.timeouts')
        bad.add(1,{'environment':runtime.config.otel_environment,'operation':'SECRET','status':'TIMEOUT','run_id':'SECRET'})
        assert 'SECRET' not in capture(runtime)
        batch=runtime.exporters['metric'].records[-1]
        metric=next(m for rm in batch.resource_metrics for sm in rm.scope_metrics for m in sm.metrics if m.name=='slopanoc.turn.stall_duration')
        point=metric.data.data_points[0]
        assert not safe_point(metric,point,runtime.config).exemplars
        with pytest.raises(ValueError):safe_point(metric,replace(point,sum=float('nan')),runtime.config)

@pytest.mark.asyncio
async def test_real_local_database_pool_wait_respects_parent(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import AsyncAdaptedQueuePool
    from backend.observability.dependency_database import pool_kwargs,observe_engine
    url='sqlite+aiosqlite:///'+str(tmp_path/'m6-disposable.sqlite')
    engine=observe_engine(create_async_engine(url,**pool_kwargs(url,{'poolclass':AsyncAdaptedQueuePool,'pool_size':1,'max_overflow':0},'session_db')),'session_db')
    held=await engine.connect()
    try:
        budget=Deadline(time.monotonic()+.02,Category.TURN)
        with bind(budget):
            with pytest.raises(DeadlineExceeded) as error:await engine.connect()
        assert error.value.category==Category.TURN
        assert engine.pool.checkedout()==1
    finally:await held.close()
    async with engine.connect():assert engine.pool.checkedout()==1
    assert engine.pool.checkedout()==0
    await engine.dispose()

@pytest.mark.asyncio
async def test_query_and_persistence_callers_bounded_without_claiming_remote_abort():
    from backend.observability.dependency_database import observed_session_class
    class Base:
        async def execute(self,*a,**k):await asyncio.Event().wait()
        async def commit(self):await asyncio.Event().wait()
        def get_bind(self):return None
    session=observed_session_class(Base)()
    for operation in (session.execute,session.commit):
        with bind(Deadline(time.monotonic()+.02,Category.TURN)):
            with pytest.raises(DeadlineExceeded):await operation()

@pytest.mark.asyncio
async def test_existing_provider_backoff_cannot_outlive_budget():
    import httpx
    from google.genai import types
    from backend.tests.test_observability_model_provider import fake_model,request
    from backend.observability.model_adapter import instrument_model
    calls=[]
    async def reply(req):calls.append(1);return httpx.Response(503,json={'error':{'code':503,'message':'PRIVATE'}})
    async with fake_model(reply,retry_options=types.HttpRetryOptions(attempts=3,initial_delay=1,max_delay=1,jitter=0)) as (base,_):
        with bind(Deadline(time.monotonic()+.03,Category.MODEL)):
            with pytest.raises(DeadlineExceeded):
                _=[e async for e in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
    assert calls==[1]

@pytest.mark.asyncio
async def test_disconnected_relay_keeps_existing_driver_and_cleanup(monkeypatch):
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.streaming_events import StreamEventType
    from backend.observability.runtime import Runtime
    from backend.observability.turn_trace import current_turn
    runtime=Runtime(config());sessions=ApiSessionService();sid=await sessions.create_session()
    service=ChatService(sessions,observability_runtime=runtime);entered=asyncio.Event();release=asyncio.Event();turns=[]
    async def events(sequencer,*args):
        turns.append(current_turn());entered.set();await release.wait()
        yield sequencer.build(StreamEventType.RUN_COMPLETED,{'outcome':'ok'})
    monkeypatch.setattr(service,'_run_turn_events',events)
    relay=service.execute_turn_events(sid,'hello')
    try:
        assert (await anext(relay)).type==StreamEventType.RUN_STARTED
        await entered.wait();await relay.aclose()
        assert service._background_turns and turns[0].status not in (RunStatus.CANCELLED,RunStatus.FAILED)
        release.set();await asyncio.gather(*tuple(service._background_turns))
        assert turns[0].status==RunStatus.COMPLETED and turns[0].delivery=='disconnected'
        assert not sessions.lock_for(sid).locked()
    finally:release.set();runtime.close()

@pytest.mark.asyncio
async def test_hosted_mailbox_boolean_admission_preserved_and_late_write_rejected():
    from backend.api.turn_context import bind_run_id,reset_run_id
    import backend.api.hosted_content_vision_context as images
    executor=BlockingExecutor(1,1);run_id='m6-image-owner';token=bind_run_id(run_id)
    release=Event()
    try:
        for index in range(images.MAX_HOSTED_IMAGES_PER_MESSAGE):
            assert await executor.run(images.stash_pending_hosted_content_image,'chat','message',str(index),'image/png',b'fake') is True
        assert await executor.run(images.stash_pending_hosted_content_image,'chat','message','overflow','image/png',b'fake') is False
        assert len(images._pending[run_id])==images.MAX_HOSTED_IMAGES_PER_MESSAGE
        images.discard_pending_hosted_content_image(run_id)
        def late():
            release.wait(1)
            return images.stash_pending_hosted_content_image('chat','message','late','image/png',b'fake')
        with pytest.raises(DeadlineExceeded):
            await executor.run(late,budget=Deadline(time.monotonic()+.02,Category.TOOL))
        release.set()
        for _ in range(100):
            if not executor.occupied:break
            await asyncio.sleep(.002)
        assert run_id not in images._pending
    finally:
        release.set();images.discard_pending_hosted_content_image(run_id);reset_run_id(token)

@pytest.mark.asyncio
async def test_parallel_real_turns_keep_agent_tool_dependency_and_terminal_isolated(monkeypatch):
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.streaming_events import StreamEventType
    from backend.observability.runtime import Runtime
    from backend.observability.turn_trace import current_turn
    from backend.observability.agent_instrumentation import Execution
    from backend.observability.tool_instrumentation import tool_scope
    from backend.observability.dependency_instrumentation import dependency_scope
    runtime=Runtime(config().model_copy(update={'observability_enabled':True,'otel_enabled':True,'otel_exporter_mode':'local'}))
    sessions=ApiSessionService();first_sid=await sessions.create_session();second_sid=await sessions.create_session()
    first=ChatService(sessions,observability_runtime=runtime);second=ChatService(sessions,observability_runtime=runtime)
    waiting=asyncio.Event();turns={}
    async def first_events(seq,*args):
        turn=turns['first']=current_turn();execution=Execution('agent','incident_manager',runtime=runtime)
        try:
            with execution.attached(),tool_scope('teams_get_messages'),dependency_scope('power_automate_gateway','teams.getMessages','http.client'):
                waiting.set()
                async with enforce(Deadline(time.monotonic()+.03,Category.TOOL,parent=turn.reliability.work)):
                    await asyncio.Event().wait()
        finally:execution.finish(TimeoutError())
        yield seq.build(StreamEventType.RUN_COMPLETED,{'outcome':'ok'})
    async def second_events(seq,*args):
        turn=turns['second']=current_turn();execution=Execution('agent','technical_authority_engineer',runtime=runtime)
        try:
            with execution.attached(),tool_scope('knowledge_search'),dependency_scope('knowledge','sparse','knowledge.retrieval',kind='local'):
                await waiting.wait()
                assert turns['first'].snapshot().current_tool=='teams_get_messages'
                assert turn.snapshot().current_tool=='knowledge_search'
                assert turns['first'].snapshot().current_dependency=='power_automate_gateway'
                assert turn.reliability is not turns['first'].reliability
        finally:execution.finish()
        yield seq.build(StreamEventType.RUN_COMPLETED,{'outcome':'ok'})
    monkeypatch.setattr(first,'_run_turn_events',first_events);monkeypatch.setattr(second,'_run_turn_events',second_events)
    try:
        await asyncio.gather(_consume(first,first_sid),_consume(second,second_sid))
        assert turns['first'].status==RunStatus.TIMEOUT and turns['second'].status==RunStatus.COMPLETED
        assert turns['first'].snapshot().dependencies==turns['second'].snapshot().dependencies==()
        assert not sessions.lock_for(first_sid).locked() and not sessions.lock_for(second_sid).locked()
        assert current_turn() is None
    finally:runtime.close()

@pytest.mark.asyncio
async def test_provider_owned_retry_exhaustion_is_separate_from_budget_denial():
    import httpx
    from google.genai import types
    from backend.tests.test_observability_model_provider import fake_model,request
    from backend.tests._m5_dependencies import environment,points
    from backend.observability.model_adapter import instrument_model
    calls=[]
    def reply(req):calls.append(1);return httpx.Response(503,json={'error':{'code':503,'message':'PRIVATE'}})
    with environment() as (runtime,turn):
        owner=Watchdog(turn,runtime.config);turn.reliability=owner
        with bind(owner.work,owner):
            async with fake_model(reply,retry_options=types.HttpRetryOptions(attempts=2,initial_delay=.001,max_delay=.001,jitter=0)) as (base,_):
                with pytest.raises(Exception):
                    _=[e async for e in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
        assert calls==[1,1]
        assert points(runtime,'slopanoc.reliability.retry_exhausted')[0].value==1
        assert not points(runtime,'slopanoc.reliability.retry_denied')
