import asyncio
import pytest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import FunctionTool, AgentTool
from google.genai import types
from backend.observability.adk_adapter import ObservedAgent
from backend.observability.agent_instrumentation import active_execution, Execution
from backend.observability.tool_instrumentation import TOOLS, SIDE_EFFECTING, tool_scope, observe_result
from backend.observability.model_adapter import instrument_model
from backend.observability.model_context import observation_sink
from backend.observability.runtime import Runtime
from backend.observability.turn_trace import TurnTrace
from backend.tests.test_observability_agent_instrumentation import drive, synthetic
from backend.tests.test_observability_runtime import config
from backend.tests.test_observability_model_provider import fake_model, spans

async def invoke(base,name,body,*,before=None,after=None,error_callback=None):
    async def function(value:str):
        return await body(value)
    function.__name__=name
    tool=FunctionTool(function)
    round=[0]
    def model(callback_context,llm_request):
        round[0]+=1
        if round[0]==1:
            return LlmResponse(content=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(name=name,args={'value':'M4_PRIVATE_ARGS'}))]))
        return synthetic(callback_context,llm_request)
    agent=ObservedAgent(name='incident_manager',model=instrument_model(base,'incident_manager'),
        tools=[tool],before_model_callback=model,before_tool_callback=before,
        after_tool_callback=after,on_tool_error_callback=error_callback)
    return await drive(agent)

@pytest.mark.asyncio
@pytest.mark.parametrize('name',sorted(TOOLS))
async def test_each_registered_name_real_adk_dispatch_one_span(name):
    r=Runtime(config());root=TurnTrace('r','s',r);calls=[]
    async def body(value):
        assert active_execution().tool==name
        calls.append(value)
        return {'status':'completed','content':'M4_PRIVATE_RESULT'}
    try:
        async with fake_model(lambda req:pytest.fail('synthetic')) as (base,_):
            with root.attached(),observation_sink(None,runtime=r):
                await invoke(base,name,body);root.finish()
            tools=[s for s in spans(r) if s.name=='slopanoc.tool']
            agents=[s for s in spans(r) if s.name=='slopanoc.agent']
            assert len(tools)==len(agents)==len(calls)==1
            assert tools[0].parent.span_id==agents[0].context.span_id
            assert tools[0].attributes['slopanoc.execution_disposition']=='executed'
            assert tools[0].attributes['slopanoc.tool']==name
            assert 'M4_PRIVATE' not in repr(tools)
    finally:r.close()

@pytest.mark.asyncio
async def test_actual_adk_injected_structured_response_tool():
    from pydantic import BaseModel
    class Response(BaseModel):
        answer:str
    def get_current_time_context():
        pytest.fail('ADK injected response should finish without another tool')
    def model(callback_context,llm_request):
        return LlmResponse(content=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(
            name='set_model_response',args={'answer':'M4_PRIVATE_STRUCTURED_RESPONSE'}))]))
    r=Runtime(config());root=TurnTrace('schema','session',r)
    try:
        async with fake_model(lambda req:pytest.fail('synthetic')) as (base,_):
            agent=ObservedAgent(name='incident_manager',model=instrument_model(base,'incident_manager'),
                tools=[get_current_time_context],output_schema=Response,before_model_callback=model)
            with root.attached(),observation_sink(None,runtime=r):
                events=await drive(agent);root.finish()
            assert any(p.text and 'M4_PRIVATE_STRUCTURED_RESPONSE' in p.text for e in events for p in e.content.parts or ())
            tools=[s for s in spans(r) if s.name=='slopanoc.tool']
            assert len(tools)==1 and tools[0].attributes['slopanoc.tool']=='set_model_response'
            assert tools[0].attributes['slopanoc.status']=='COMPLETED'
            assert 'M4_PRIVATE_STRUCTURED_RESPONSE' not in repr(spans(r))
    finally:r.close()

@pytest.mark.asyncio
async def test_policy_callback_order_and_blocked_body():
    r=Runtime(config());root=TurnTrace('r','s',r);order=[]
    def before1(tool,args,tool_context):order.append('before1')
    def before2(tool,args,tool_context):
        order.append('before2');return {'status':'policy_blocked'}
    def before3(tool,args,tool_context):pytest.fail('short circuit preserved')
    def after(tool,args,tool_context,tool_response):order.append('after');return tool_response
    async def body(value):pytest.fail('blocked body')
    try:
        async with fake_model(lambda req:pytest.fail('synthetic')) as (base,_):
            with root.attached(),observation_sink(None,runtime=r):
                await invoke(base,'teams_send_message',body,before=[before1,before2,before3],after=after);root.finish()
            tool=next(s for s in spans(r) if s.name=='slopanoc.tool')
            assert order==['before1','before2','after']
            assert tool.attributes['slopanoc.execution_disposition']=='not_executed'
            assert tool.attributes['slopanoc.status']=='COMPLETED' and tool.status.status_code.name=='UNSET'
    finally:r.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('result,category,error',[({'error':{'errorCode':'authorization_error','userMessage':'M4_PRIVATE_APPROVAL'}},'approval_rejected','APPROVAL_REJECTED'),
    ({'outcome':'source_gap'},'source_gap','SOURCE_GAP'),({'outcome':'command_rejected'},'command_rejected','COMMAND_AUTHORITY_REJECTED'),
    ({'status':'accepted'},'accepted',None),({'actions':[]},'empty',None)])
async def test_policy_results_not_infrastructure_failure(result,category,error):
    r=Runtime(config());root=TurnTrace('r','s',r)
    try:
        with root.attached():
            with tool_scope('procedure_action_catalog' if 'actions' in result else 'teams_send_message') as scope:observe_result(scope,result)
            root.finish()
        tool=next(s for s in spans(r) if s.name=='slopanoc.tool')
        assert tool.attributes['slopanoc.result_category']==category
        assert tool.attributes.get('slopanoc.error_code')==error
        assert tool.attributes['slopanoc.status']=='COMPLETED' and tool.status.status_code.name=='UNSET'
    finally:r.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('name',sorted(SIDE_EFFECTING))
@pytest.mark.parametrize('failure',['start','classify','metric','finish','attach'])
async def test_telemetry_failure_never_replays_side_effects(name,failure,monkeypatch):
    import backend.observability.tool_instrumentation as tools
    import backend.observability.execution_metrics as metrics
    r=Runtime(config());root=TurnTrace('r','s',r);calls=[]
    def broken(*args,**kwargs):raise ValueError('M4_PRIVATE_TELEMETRY_FAILURE')
    if failure=='start':monkeypatch.setattr(tools,'start_tool',broken)
    if failure=='classify':monkeypatch.setattr(tools,'classify',broken)
    if failure=='metric':monkeypatch.setattr(metrics,'record',broken)
    if failure=='finish':monkeypatch.setattr(Execution,'finish',broken)
    if failure=='attach':monkeypatch.setattr(Execution,'attached',broken)
    async def body(value):calls.append(value);return {'status':'executed'}
    try:
        async with fake_model(lambda req:pytest.fail('synthetic')) as (base,_):
            # Adapter resolves patched factory explicitly, rather than patching ADK.
            if failure=='start':
                import backend.observability.adk_adapter as adapter
                monkeypatch.setattr(adapter,'start_tool',broken)
            with root.attached(),observation_sink(None,runtime=r):
                events=await invoke(base,name,body);root.finish()
            assert events and len(calls)==1
    finally:r.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('error',[ValueError('private'),TimeoutError('private'),asyncio.CancelledError()])
async def test_direct_error_timeout_cancel_closure(error):
    r=Runtime(config());root=TurnTrace('r','s',r)
    try:
        with root.attached():
            with pytest.raises(type(error)):
                with tool_scope('knowledge_search'):raise error
            root.finish()
        tool=next(s for s in spans(r) if s.name=='slopanoc.tool')
        expected='TIMEOUT' if isinstance(error,TimeoutError) else 'CANCELLED' if isinstance(error,asyncio.CancelledError) else 'FAILED'
        assert tool.attributes['slopanoc.status']==expected
        assert active_execution() is None
    finally:r.close()

@pytest.mark.asyncio
async def test_adk_error_callback_conversion_preserves_failure_and_once():
    r=Runtime(config());root=TurnTrace('r','s',r);calls=[]
    async def body(value):calls.append(1);raise ValueError('M4_PRIVATE_ERROR')
    def recover(tool,args,tool_context,error):return {'status':'completed'}
    try:
        async with fake_model(lambda req:pytest.fail('synthetic')) as (base,_):
            with root.attached(),observation_sink(None,runtime=r):
                await invoke(base,'knowledge_search',body,error_callback=recover);root.finish()
            tool=next(s for s in spans(r) if s.name=='slopanoc.tool')
            assert tool.attributes['slopanoc.status']=='FAILED' and calls==[1]
    finally:r.close()

@pytest.mark.asyncio
async def test_direct_thread_and_parallel_scopes_are_isolated():
    r=Runtime(config());root=TurnTrace('r','s',r)
    async def call(name):
        with tool_scope(name) as scope:
            assert await asyncio.to_thread(lambda:active_execution().tool)==name
            await asyncio.sleep(.001)
            observe_result(scope,{'status':'completed'})
    try:
        with root.attached():
            await asyncio.gather(call('teams_get_members'),call('knowledge_search'))
            assert active_execution() is None;root.finish()
        tools=[s for s in spans(r) if s.name=='slopanoc.tool']
        assert len(tools)==2 and all(s.parent.span_id==root._root.get_span_context().span_id for s in tools)
    finally:r.close()

@pytest.mark.asyncio
async def test_real_adk_parallel_tools_and_sync_function_preserve_context():
    r=Runtime(config());root=TurnTrace('parallel','session',r);entered=[];gate=asyncio.Event();rounds=[0]
    async def knowledge_search(value:str):
        entered.append(active_execution().tool)
        if len(entered)==2:gate.set()
        await gate.wait()
        assert active_execution().tool=='knowledge_search'
        return {'items':[]}
    async def teams_get_messages(value:str):
        entered.append(active_execution().tool)
        if len(entered)==2:gate.set()
        await gate.wait()
        assert active_execution().tool=='teams_get_messages'
        return {'messages':[]}
    def get_current_time_context():
        assert active_execution().tool=='get_current_time_context'
        entered.append(active_execution().tool)
        return {'status':'completed'}
    def model(callback_context,llm_request):
        rounds[0]+=1
        names=['knowledge_search','teams_get_messages'] if rounds[0]==1 else ['get_current_time_context'] if rounds[0]==2 else []
        return LlmResponse(content=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(
            name=name,args={} if name=='get_current_time_context' else {'value':'M4_PRIVATE_PARALLEL'})) for name in names])) if names else synthetic(callback_context,llm_request)
    try:
        async with fake_model(lambda req:pytest.fail('synthetic')) as (base,_):
            agent=ObservedAgent(name='incident_manager',model=instrument_model(base,'incident_manager'),
                tools=[knowledge_search,teams_get_messages,get_current_time_context],before_model_callback=model)
            with root.attached(),observation_sink(None,runtime=r):
                await asyncio.wait_for(drive(agent),5);root.finish()
            samples=spans(r);tools=[s for s in samples if s.name=='slopanoc.tool']
            owner=next(s for s in samples if s.name=='slopanoc.agent')
            assert len(tools)==3 and set(entered)=={'knowledge_search','teams_get_messages','get_current_time_context'}
            assert all(s.parent.span_id==owner.context.span_id for s in tools)
            assert active_execution() is None
    finally:r.close()

@pytest.mark.asyncio
@pytest.mark.parametrize('sampled',[True,False])
async def test_execution_metrics_bounded_unsampled_and_private(sampled):
    from dataclasses import replace
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
    from backend.observability.execution_metrics import UNITS, safe_point
    from backend.observability.logging import emit
    from backend.observability.schemas import OperationalEvent
    r=Runtime(config());root=TurnTrace('r','s',r)
    if not sampled:r.tracer.sampler=ALWAYS_OFF
    try:
        with root.attached():
            agent=Execution('agent','incident_manager',runtime=r)
            with agent.attached():
                with tool_scope('teams_send_message') as scope:
                    observe_result(scope,{'status':'executed','message':'M4_PRIVATE_COMMAND_TOKEN'})
                    emit(r,OperationalEvent.LEGACY,metadata={'prompt':'M4_PRIVATE_PROMPT','message_count':2})
            agent.finish();root.finish()
        assert r.flush(1)
        metrics=[m for data in r.exporters['metric'].records for rs in data.resource_metrics
            for ss in rs.scope_metrics for m in ss.metrics if m.name in UNITS]
        assert {'slopanoc.agent.executions','slopanoc.tool.invocations','slopanoc.agent.duration','slopanoc.tool.duration'} <= {m.name for m in metrics}
        for metric in metrics:
            point=metric.data.data_points[0]
            assert set(point.attributes)==({'agent','environment','status'} if '.agent.' in metric.name else {'agent','environment','status','tool','tool_category'})
            assert metric.unit==UNITS[metric.name] and not point.exemplars
            safe_point(metric,point,r.config)
            with pytest.raises(ValueError):safe_point(metric,replace(point,attributes=dict(point.attributes,run_id='r')),r.config)
        assert 'M4_PRIVATE' not in repr(metrics)+repr(r.exporters['log'].snapshot())+repr(r.exporters['trace'].snapshot())
    finally:r.close()

@pytest.mark.asyncio
async def test_trusted_approval_service_events_and_governance_decisions():
    from datetime import datetime, timezone
    from backend.approval.service import create_action_proposal, approve_proposal, reject_proposal
    from backend.observability.tool_instrumentation import governance_decision
    r=Runtime(config());root=TurnTrace('r','s',r);state={}
    try:
        with root.attached():
            with tool_scope('teams_propose_send_message'):
                proposal=create_action_proposal('teams.sendMessage',{'chatId':'private','message':'M4_PRIVATE_APPROVAL'},state,summary='M4_PRIVATE_SUMMARY')
            with tool_scope('teams_send_message'):
                result=approve_proposal(proposal.proposal_id,state)
                assert result.success
                governance_decision('command_authority.completed',False)
            root.finish()
        exported=spans(r)
        assert {'approval.requested','approval.completed','command_authority.completed'} <= {e.name for s in exported for e in s.events}
        assert 'M4_PRIVATE' not in repr(exported)
    finally:r.close()
