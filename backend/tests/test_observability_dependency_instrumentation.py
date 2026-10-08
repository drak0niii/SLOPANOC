import asyncio
import pytest
from backend.observability.dependency_instrumentation import dependency_scope, active_dependency
from backend.observability.agent_instrumentation import Execution, attached_scope
from backend.observability.tool_instrumentation import tool_scope
from backend.observability.turn_trace import phase
from backend.observability.tracing import Operation
from backend.tests._m5_dependencies import environment, spans

@pytest.mark.asyncio
async def test_two_turns_concurrent_children_restore_and_phase_parent():
    ready=[asyncio.Event() for _ in range(3)];release=[asyncio.Event() for _ in range(3)]
    with environment() as (r,t):
        async def child(i,dep):
            with dependency_scope(dep,'exists' if dep=='chat_attachments' else 'teams.getMessages','storage.client' if dep=='chat_attachments' else 'http.client'):
                ready[i].set();await release[i].wait()
        with tool_scope('teams_get_messages'):
            tasks=[asyncio.create_task(child(0,'power_automate_gateway')),asyncio.create_task(child(1,'chat_attachments'))]
            await ready[0].wait();await ready[1].wait()
            live=t.snapshot();assert len(live.dependencies)==2
            assert {s.dependency for s in live.dependencies}=={'power_automate_gateway','chat_attachments'}
            assert all(s.tool=='teams_get_messages' and s.elapsed_ms>=0 for s in live.dependencies)
            # Independent root has no copied dependency parent or live children.
            with environment() as (r2,t2):
                task=asyncio.create_task(child(2,'power_automate_gateway'))
                await ready[2].wait();assert len(t2.snapshot().dependencies)==1
                assert len(t.snapshot().dependencies)==2
                release[2].set();await task
                assert spans(r2,'http.client')[0].parent.span_id==int(t2.span_id,16)
            release[0].set();await tasks[0]
            assert t.snapshot().current_dependency=='chat_attachments'
            release[1].set();await tasks[1]
        assert t.snapshot().dependencies==() and active_dependency(t) is None
        tool=spans(r,'slopanoc.tool')[0]
        assert all(s.parent.span_id==tool.context.span_id for s in spans(r) if s.name in {'http.client','storage.client'})
        with phase(Operation.PERSISTENCE):
            with dependency_scope('case_db','SELECT','db.client'):pass
        assert spans(r,'db.client')[0].parent.span_id==spans(r,'persistence.final_answer')[0].context.span_id

@pytest.mark.asyncio
@pytest.mark.parametrize('dep,name,op',[('power_automate_gateway','http.client','teams.getMessages'),('case_db','db.client','SELECT'),('chat_attachments','storage.client','download'),('knowledge','knowledge.retrieval','dense')])
async def test_cancel_closes_span_and_blocked_child(dep,name,op):
    event=asyncio.Event()
    with environment() as (r,t):
        async def child():
            with dependency_scope(dep,op,name):event.set();await asyncio.Event().wait()
        task=asyncio.create_task(child());await event.wait()
        assert len(t.snapshot().dependencies)==1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert t.snapshot().dependencies==()
        span=spans(r,name)[0]
        assert span.attributes['slopanoc.status']=='CANCELLED'
        assert span.status.status_code.name=='UNSET'

@pytest.mark.asyncio
async def test_thread_context_and_late_worker_never_contaminate_turn():
    import threading
    began=threading.Event();release=threading.Event()
    with environment() as (r,t):
        def worker():
            with dependency_scope('chat_attachments','upload','storage.client'):
                began.set();release.wait(3)
        with tool_scope('teams_get_hosted_content'):
            task=asyncio.create_task(asyncio.to_thread(worker))
            while not began.is_set():await asyncio.sleep(.001)
            assert t.snapshot().dependencies[0].tool=='teams_get_hosted_content'
            t.finish();assert t.snapshot().dependencies==()
            release.set();await task
        assert active_dependency(t) is None

@pytest.mark.asyncio
async def test_ingestion_detaches_from_unrelated_user_turn():
    from backend.observability.model_context import model_context
    with environment() as (r,t):
        with model_context('km_image_interpreter','image_interpretation',workload='ingestion'):
            with dependency_scope('knowledge_artifacts','upload','storage.client') as scope:
                assert scope.turn is None and t.snapshot().dependencies==()
        child=spans(r,'storage.client')[0]
        assert child.parent is None and 'slopanoc.run_id' not in child.attributes
        assert child.attributes['slopanoc.workload']=='ingestion'

def test_system_failure_logs_never_borrow_user_identity():
    from backend.observability.model_context import model_context
    from backend.observability.dependency_storage import storage_call
    with environment() as (r,t):
        def fail():raise ValueError('M5_SYSTEM_SECRET')
        with model_context('km_image_interpreter','image_interpretation',workload='ingestion'):
            with pytest.raises(ValueError):storage_call('knowledge_artifacts','upload',fail)
        r.flush()
        logs=r.exporters['log'].snapshot()
        assert len(logs)==1
        body=logs[0].log_record.body
        assert body['run_id'] is None and body['turn_id'] is None
        assert 'M5_SYSTEM_SECRET' not in repr(body)
