import asyncio
import importlib
import json
import httpx
import pytest
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from backend.observability.adk_adapter import ObservedAgent
from backend.observability.agent_instrumentation import Execution, active_execution
from backend.observability.model_adapter import instrument_model
from backend.observability.model_context import observation_sink, model_context, current_attribution
from backend.observability.runtime import Runtime
from backend.observability.turn_trace import TurnTrace
from backend.tests.test_observability_runtime import config
from backend.tests.test_observability_model_provider import fake_model, payload, spans

OWNERS = ['team_manager','incident_manager','technical_authority_engineer','problem_manager','automated_operations_engineer']

async def drive(agent):
    sessions=InMemorySessionService()
    runner=Runner(app_name='m4-test',agent=agent,session_service=sessions)
    session=await sessions.create_session(app_name='m4-test',user_id='u')
    try:
        return [e async for e in runner.run_async(user_id='u',session_id=session.id,
            new_message=types.Content(role='user',parts=[types.Part(text='M4_PRIVATE_PROMPT')]))]
    finally:await runner.close()


def synthetic(callback_context,llm_request):
    return LlmResponse(content=types.Content(role='model',parts=[types.Part(text='M4_PRIVATE_ANSWER')]))

@pytest.mark.asyncio
@pytest.mark.parametrize('owner',OWNERS)
async def test_real_configured_agent_and_model_graph(owner,monkeypatch):
    actual=getattr(importlib.import_module('backend.agents.'+owner+'.agent'),owner)
    assert isinstance(actual,ObservedAgent)
    r=Runtime(config());root=TurnTrace('m4-run','m4-session',r);seen=[]
    try:
        async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,_):
            # Same configured subclass, real ADK execution; strip business input/output
            # validators for this pure parentage fixture, retained in coverage/regressions.
            agent=actual.model_copy(update={'model':instrument_model(base,owner), 'input_schema':None,
                'output_schema':None,'tools':[], 'instruction':'', 'before_agent_callback':None,
                'after_agent_callback':None,'before_model_callback':None,'after_model_callback':None})
            with root.attached(),observation_sink(seen.append,runtime=r):
                events=await drive(agent)
                assert events and active_execution() is None
                root.finish()
        exported=spans(r);nodes={s.context.span_id:s for s in exported}
        execution=next(s for s in exported if s.name=='slopanoc.agent')
        model=next(s for s in exported if s.name=='slopanoc.model.operation')
        assert model.parent.span_id==execution.context.span_id
        assert execution.parent.span_id==root._root.get_span_context().span_id
        assert execution.attributes['slopanoc.agent']==owner
        assert execution.attributes['slopanoc.status']=='COMPLETED'
        assert len(seen)==1 and seen[0].agent.value==owner and seen[0].run_id=='m4-run'
        assert all(s.parent is None or s.parent.span_id in nodes for s in exported)
        assert 'M4_PRIVATE_PROMPT' not in repr(exported)
    finally:r.close()

@pytest.mark.asyncio
async def test_real_callback_short_circuit_reentry_nested_and_purpose():
    r=Runtime(config());root=TurnTrace('r','s',r)
    try:
        async with fake_model(lambda req:pytest.fail('No model request')) as (base,_):
            child=ObservedAgent(name='incident_manager',model=instrument_model(base,'incident_manager'),before_model_callback=synthetic)
            async def nested(callback_context):
                with model_context('incident_manager','remediation'):
                    await drive(child)
            outer=child.model_copy(update={'after_agent_callback':nested})
            with root.attached(),observation_sink(None,runtime=r):
                await drive(outer)
                await drive(child)
                root.finish()
            agents=[s for s in spans(r) if s.name=='slopanoc.agent']
            assert len(agents)==3
            remediation=next(s for s in agents if s.attributes['slopanoc.execution_purpose']=='remediation')
            assert remediation.parent.span_id in {s.context.span_id for s in agents}
            assert active_execution() is None and current_attribution() is None
    finally:r.close()

@pytest.mark.asyncio
async def test_parallel_roots_and_closed_copied_context():
    r=Runtime(config());gate=asyncio.Event();late=[]
    async def run(index):
        root=TurnTrace(f'r{index}',f's{index}',r)
        with root.attached():
            scope=Execution('agent','incident_manager',runtime=r)
            with scope.attached():
                async def worker():
                    await gate.wait()
                    assert active_execution() is None and current_attribution() is None
                    late.append(index)
                task=asyncio.create_task(worker())
                await asyncio.sleep(0)
                assert active_execution() is scope
            scope.finish();root.finish()
            return task
    try:
        tasks=await asyncio.gather(run(1),run(2));gate.set();await asyncio.gather(*tasks)
        agents=[s for s in spans(r) if s.name=='slopanoc.agent']
        assert len({s.context.trace_id for s in agents})==2 and sorted(late)==[1,2]
    finally:r.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('action',['error','cancel','close'])
async def test_agent_exception_cancel_and_stream_abandon(action):
    r=Runtime(config());root=TurnTrace('r','s',r);ready=asyncio.Event()
    async def before(callback_context):
        ready.set()
        if action=='error':raise ValueError('M4_PRIVATE_ERROR')
        if action=='cancel':await asyncio.Future()
    try:
        async with fake_model(lambda req:pytest.fail('synthetic')) as (base,_):
            agent=ObservedAgent(name='incident_manager',model=instrument_model(base,'incident_manager'),before_agent_callback=before,before_model_callback=synthetic)
            with root.attached(),observation_sink(None,runtime=r):
                if action=='error':
                    with pytest.raises(ValueError,match='M4_PRIVATE_ERROR'):await drive(agent)
                elif action=='cancel':
                    task=asyncio.create_task(drive(agent));await ready.wait();task.cancel()
                    with pytest.raises(asyncio.CancelledError):await task
                else:
                    # Direct installed invocation context permits deterministic generator abandonment.
                    from google.adk.agents.invocation_context import InvocationContext
                    from google.adk.plugins.plugin_manager import PluginManager
                    from google.adk.agents.run_config import RunConfig
                    sessions=InMemorySessionService();session=await sessions.create_session(app_name='a',user_id='u')
                    ctx=InvocationContext(invocation_id='i',agent=agent,session=session,session_service=sessions,
                        plugin_manager=PluginManager([]),run_config=RunConfig(),user_content=types.Content(role='user',parts=[types.Part(text='private')]))
                    iterator=agent.run_async(ctx);await iterator.__anext__()
                    assert active_execution() is None
                    await iterator.aclose()
                root.finish()
            executions=[s for s in spans(r) if s.name=='slopanoc.agent']
            assert len(executions)==1
            assert executions[0].attributes['slopanoc.status']==('FAILED' if action=='error' else 'CANCELLED')
            assert active_execution() is None
    finally:r.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('owner',['incident_manager','technical_authority_engineer','problem_manager','automated_operations_engineer'])
async def test_real_nested_agent_tool_model_children(owner):
    from google.adk.tools import AgentTool
    calls=[];seen=[];r=Runtime(config());root=TurnTrace('nested','session',r)
    def handler(req):
        body=json.loads(req.content);calls.append(body)
        if 'CHILD_OWNER' in str(body.get('systemInstruction')):
            return httpx.Response(200,json=payload(text='M4_PRIVATE_CHILD_RESPONSE'))
        if len(calls)==1:
            data=payload();data['candidates'][0]['content']['parts']=[{'functionCall':{'name':owner,'args':{'request':'M4_PRIVATE_DELEGATION'}}}]
            return httpx.Response(200,json=data)
        return httpx.Response(200,json=payload(text='M4_PRIVATE_FINAL'))
    try:
        async with fake_model(handler) as (base,_):
            child=ObservedAgent(name=owner,instruction='CHILD_OWNER',model=instrument_model(base,owner))
            parent=ObservedAgent(name='team_manager',instruction='ROOT_OWNER',model=instrument_model(base,'team_manager','orchestration'),tools=[AgentTool(agent=child)])
            with root.attached(),observation_sink(seen.append,runtime=r):
                await drive(parent);root.finish()
            samples=spans(r);nodes={s.context.span_id:s for s in samples}
            assert len([s for s in samples if s.name=='slopanoc.agent'])==2
            tool=next(s for s in samples if s.name=='slopanoc.tool')
            child_span=next(s for s in samples if s.name=='slopanoc.agent' and s.attributes['slopanoc.agent']==owner)
            assert child_span.parent.span_id==tool.context.span_id
            for model in [s for s in samples if s.name=='slopanoc.model.operation']:
                assert nodes[model.parent.span_id].name=='slopanoc.agent'
                assert nodes[model.parent.span_id].attributes['slopanoc.agent']==model.attributes['slopanoc.agent']
            assert [o.agent.value for o in seen]==['team_manager',owner,'team_manager']
            assert all(o.run_id=='nested' for o in seen)
            assert 'M4_PRIVATE' not in repr(samples)
    finally:r.close()

@pytest.mark.asyncio
async def test_nested_agent_tool_cancellation_closes_parent_tool_and_child():
    from google.adk.tools import AgentTool
    from backend.observability.agent_instrumentation import observe_primary
    r=Runtime(config());root=TurnTrace('cancel','session',r);ready=asyncio.Event()
    async def wait(callback_context):
        assert active_execution().role=='primary'
        ready.set();await asyncio.Future()
    def delegate(callback_context,llm_request):
        return LlmResponse(content=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(
            name='incident_manager',args={'request':'M4_PRIVATE_REQUEST'}))]))
    def route(tool,args,tool_context):
        observe_primary('incident_manager')
    try:
        async with fake_model(lambda req:pytest.fail('synthetic')) as (base,_):
            child=ObservedAgent(name='incident_manager',model=instrument_model(base,'incident_manager'),before_agent_callback=wait)
            parent=ObservedAgent(name='team_manager',model=instrument_model(base,'team_manager','orchestration'),
                tools=[AgentTool(agent=child)],before_model_callback=delegate,before_tool_callback=route)
            with root.attached(),observation_sink(None,runtime=r):
                task=asyncio.create_task(drive(parent));await asyncio.wait_for(ready.wait(),5);task.cancel()
                with pytest.raises(asyncio.CancelledError):await task
                root.finish()
            samples=[s for s in spans(r) if s.name in ('slopanoc.agent','slopanoc.tool')]
            assert len(samples)==3 and all(s.attributes['slopanoc.status']=='CANCELLED' for s in samples)
            assert active_execution() is None and current_attribution() is None
    finally:r.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('owner,workload',[('incident_manager',None),('km_image_interpreter','ingestion')])
async def test_standalone_and_ingestion_tool_model_parentage(owner,workload):
    from contextlib import nullcontext
    calls=[];seen=[];r=Runtime(config());root=TurnTrace('unrelated-user','session',r)
    def handler(req):
        calls.append(1)
        if len(calls)==1:
            data=payload();data['candidates'][0]['content']['parts']=[{'functionCall':{'name':'knowledge_search','args':{}}}]
            return httpx.Response(200,json=data)
        return httpx.Response(200,json=payload())
    async def knowledge_search():
        assert active_execution().workload==('ingestion' if workload else 'background')
        return {'items':[]}
    try:
        async with fake_model(handler) as (base,_):
            agent=ObservedAgent(name=owner,model=instrument_model(base,owner,
                'image_interpretation' if workload else 'specialist_reasoning',workload=workload),tools=[knowledge_search])
            # Ingestion invoked within a copied user context must remain independent.
            with root.attached() if workload else nullcontext():
                with observation_sink(seen.append,runtime=r):await drive(agent)
            root.finish()
        samples=spans(r);agent_span=next(s for s in samples if s.name=='slopanoc.agent')
        children=[s for s in samples if s.name in ('slopanoc.model.operation','slopanoc.tool')]
        assert len(children)==3 and agent_span.parent is None
        assert all(s.parent.span_id==agent_span.context.span_id for s in children)
        assert all(s.context.trace_id==agent_span.context.trace_id for s in children)
        assert all('slopanoc.run_id' not in s.attributes for s in [agent_span,*children])
        assert all(o.run_id is None for o in seen) and len(seen)==2
    finally:r.close()
