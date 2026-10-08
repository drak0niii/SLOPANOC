import asyncio
import logging
import json
import httpx
import pytest
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from backend.observability.model_adapter import instrument_model
from backend.observability.model_context import observation_sink, model_context
from backend.observability.model_instrumentation import _active, ModelAttempt, observe_invalid_output
from backend.observability.turn_trace import TurnTrace, current_turn
from backend.observability.runtime import Runtime
from backend.observability.config import ObservabilityConfig
from backend.observability.logging import SafeJsonFormatter
from backend.observability.tracing import current_trace_ids
from backend.tests.test_observability_runtime import config
from backend.tests.test_observability_model_provider import fake_model, payload, request, spans, Bytes, SENTINELS


@pytest.mark.asyncio
async def test_parallel_roots_nested_ownership_and_native_parent_graph():
    r=Runtime(config());observations=[]
    async def handler(req):
        await asyncio.sleep(.001)
        return httpx.Response(200,json=payload())
    try:
        async with fake_model(handler) as (base,_):
            async def turn(index):
                root=TurnTrace(f'run-{index}',f'session-{index}',r)
                with root.attached():
                    root.bind_turn_id(f'turn-{index}')
                    for agent in ('team_manager','incident_manager','technical_authority_engineer','problem_manager','automated_operations_engineer'):
                        model=instrument_model(base,agent,'orchestration' if agent=='team_manager' else 'specialist_reasoning')
                        _=[x async for x in model.generate_content_async(request())]
                        assert current_turn() is root and _active.get() is None
                    root.finish()
            with observation_sink(observations.append,runtime=r):
                await asyncio.gather(turn(1),turn(2))
            samples=spans(r);roots={s.attributes['slopanoc.run_id']:s for s in samples if s.name=='slopanoc.turn'}
            ids={s.context.span_id:s for s in samples}
            assert len(observations)==10 and len({o.observation_id for o in observations})==10
            for o in observations:
                assert o.session_id=='session-'+o.run_id[-1] and o.turn_id=='turn-'+o.run_id[-1]
                assert o.trace_id==format(roots[o.run_id].context.trace_id,'032x')
            for s in samples:
                if s.name in ('gen_ai.request','slopanoc.model.operation'):
                    assert s.parent.span_id in ids
                    assert ids[s.parent.span_id].context.trace_id==s.context.trace_id
            assert current_turn() is None and current_trace_ids()==(None,None)
    finally:r.close()


@pytest.mark.asyncio
async def test_real_adk_callback_short_circuit_has_zero_provider_requests():
    calls=[];observations=[]
    async with fake_model(lambda req:calls.append(req)) as (base,_):
        def synthetic(callback_context,llm_request):
            return LlmResponse(content=types.Content(role='model',parts=[types.Part(text='synthetic')]))
        agent=Agent(name='incident_manager',model=instrument_model(base,'incident_manager'),before_model_callback=synthetic)
        sessions=InMemorySessionService()
        runner=Runner(app_name='model-test',agent=agent,session_service=sessions)
        session=await sessions.create_session(app_name='model-test',user_id='u')
        try:
            with observation_sink(observations.append):
                events=[e async for e in runner.run_async(user_id='u',session_id=session.id,new_message=types.Content(role='user',parts=[types.Part(text='hello')]))]
            assert events and calls==observations==[]
        finally:await runner.close()


@pytest.mark.asyncio
async def test_warmup_ingestion_and_post_terminal_context_detach():
    r=Runtime(config());seen=[]
    try:
        async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,_):
            turn=TurnTrace('user-run','user-session',r)
            with turn.attached(),observation_sink(seen.append,runtime=r):
                for agent,purpose,workload in [('system','warmup','warmup'),('km_image_interpreter','image_interpretation','ingestion')]:
                    _=[x async for x in instrument_model(base,agent,purpose,workload=workload).generate_content_async(request())]
                copied=asyncio.get_running_loop().create_future()
                async def late():
                    await copied
                    _=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
                task=asyncio.create_task(late())
                turn.finish();copied.set_result(None);await task
            assert len(seen)==3
            assert [o.workload for o in seen]==['warmup','ingestion','background']
            assert all(o.run_id is None and o.session_id is None and o.turn_id is None for o in seen)
            assert all(o.trace_id!=turn.trace_id for o in seen)
            logs=r.exporters['log'].snapshot();r.flush(1);logs=r.exporters['log'].snapshot()
            assert all(log.log_record.body['run_id'] is None for log in logs if log.log_record.body['event_name'].startswith('model.'))
    finally:r.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('action',['close','cancel','stream_error'])
async def test_stream_abandonment_cancel_and_errors_close_attempts(action):
    raw=Bytes([payload(2),payload(3),httpx.ReadTimeout('private transport error')])
    seen=[];r=Runtime(config())
    try:
        async with fake_model(lambda req:httpx.Response(200,stream=raw)) as (base,_):
            with observation_sink(seen.append,runtime=r):
                iterator=instrument_model(base,'team_manager','orchestration').generate_content_async(request(),stream=True)
                if action=='close':
                    await iterator.__anext__();assert _active.get() is None
                    await iterator.aclose()
                elif action=='stream_error':
                    with pytest.raises(httpx.ReadTimeout):
                        _=[x async for x in iterator]
                else:
                    async def handler(req):
                        await asyncio.Future()
                    async with fake_model(handler) as (waiting,_):
                        iterator=instrument_model(waiting,'incident_manager').generate_content_async(request(),stream=True)
                        task=asyncio.create_task(iterator.__anext__())
                        await asyncio.sleep(.01);task.cancel()
                        with pytest.raises(asyncio.CancelledError):await task
            assert len(seen)==1
            assert seen[0].status.value in ('CANCELLED','TIMEOUT')
            assert seen[0].usage.availability in ('PARTIAL','UNKNOWN')
            samples=spans(r)
            assert len([s for s in samples if s.name=='gen_ai.request'])==1
            assert all(s.end_time is not None for s in samples)
            if action!='cancel':assert raw.closed
    finally:r.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['usage','metrics','sink','span','span_end','logging'])
async def test_telemetry_faults_do_not_corrupt_provider_response(monkeypatch,failure):
    import backend.observability.model_instrumentation as instrumentation
    import backend.observability.model_metrics as metrics
    import backend.observability.logging as logs
    r=Runtime(config());seen=[]
    def fail(*args,**kwargs):raise RuntimeError(' '.join(SENTINELS))
    if failure=='usage':monkeypatch.setattr(instrumentation,'extract_usage',fail)
    if failure=='metrics':monkeypatch.setattr(metrics,'record',fail)
    if failure=='span':monkeypatch.setattr(r.tracer,'start_span',fail)
    if failure=='span_end':
        from opentelemetry.sdk.trace import Span
        monkeypatch.setattr(Span,'end',fail)
    if failure=='logging':monkeypatch.setattr(logs,'emit_model_observation',fail)
    try:
        async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,_):
            with observation_sink(fail if failure=='sink' else seen.append,runtime=r):
                output=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
            assert output[0].content.parts[0].text==SENTINELS[3]
            if failure!='sink':assert len(seen)==1
            if failure=='usage':assert seen[0].usage.availability=='UNKNOWN'
            assert r.health.snapshot()['trace']['failed']>0
    finally:r.close()


@pytest.mark.asyncio
async def test_successful_provider_and_invalid_structured_output_are_separate():
    r=Runtime(config());seen=[]
    try:
        async with fake_model(lambda req:httpx.Response(200,json=payload(text='invalid JSON'))) as (base,_):
            turn=TurnTrace('run-validation','session-validation',r)
            with turn.attached(),observation_sink(seen.append,runtime=r):
                _=[x async for x in instrument_model(base,'technical_authority_engineer').generate_content_async(request())]
                observe_invalid_output('technical_authority_engineer')
                turn.finish()
            assert len(seen)==1 and seen[0].status.value=='COMPLETED' and seen[0].usage.input_tokens==10
            invalid=next(s for s in spans(r) if s.name=='slopanoc.model.validation')
            assert invalid.attributes['slopanoc.error_code']=='MODEL_INVALID_RESPONSE'
            assert invalid.attributes['slopanoc.logical_call_id']==seen[0].logical_call_id
    finally:r.close()


@pytest.mark.asyncio
async def test_sampling_and_disabled_tracing_do_not_sample_handoff():
    from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
    r=Runtime(config());r.tracer.sampler=ALWAYS_OFF
    seen=[]
    try:
        async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,_):
            with observation_sink(seen.append,runtime=r):
                _=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
            assert len(seen)==1 and seen[0].usage.input_tokens==10 and spans(r)==()
        disabled=Runtime(ObservabilityConfig())
        try:
            async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,_):
                with observation_sink(seen.append,runtime=disabled):
                    _=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
                assert len(seen)==2 and seen[1].trace_id is None
        finally:disabled.close()
    finally:r.close()


@pytest.mark.asyncio
async def test_real_nested_adk_agent_tool_keeps_outer_root_and_child_owner():
    from google.adk.tools.agent_tool import AgentTool
    r=Runtime(config());seen=[];calls=[]
    def handler(req):
        body=json.loads(req.content);calls.append(body)
        owner=str(body.get('systemInstruction'))
        if 'CHILD_OWNER' in owner:
            return httpx.Response(200,json=payload(text='specialist result'))
        if len(calls)==1:
            out=payload()
            out['candidates'][0]['content']['parts']=[{'functionCall':{'name':'incident_manager','args':{'request':'analyze'}}}]
            return httpx.Response(200,json=out)
        return httpx.Response(200,json=payload(text='final result'))
    try:
        async with fake_model(handler) as (base,_):
            child=Agent(name='incident_manager',instruction='CHILD_OWNER',model=instrument_model(base,'incident_manager'))
            parent=Agent(name='team_manager',instruction='ROOT_OWNER',model=instrument_model(base,'team_manager','orchestration'),tools=[AgentTool(agent=child)])
            sessions=InMemorySessionService();runner=Runner(app_name='nested-model',agent=parent,session_service=sessions)
            session=await sessions.create_session(app_name='nested-model',user_id='u')
            root=TurnTrace('outer-run','outer-session',r)
            try:
                with root.attached(),observation_sink(seen.append,runtime=r):
                    root.bind_turn_id('outer-turn')
                    events=[e async for e in runner.run_async(user_id='u',session_id=session.id,new_message=types.Content(role='user',parts=[types.Part(text='hello')]))]
                    root.finish()
                assert events and len(calls)==len(seen)==3
                assert [o.agent.value for o in seen]==['team_manager','incident_manager','team_manager']
                assert all((o.run_id,o.session_id,o.turn_id)==('outer-run','outer-session','outer-turn') for o in seen)
                native={s.context.span_id:s for s in spans(r)}
                assert all(s.parent.span_id in native for s in native.values() if s.name in ('gen_ai.request','slopanoc.model.operation'))
                assert current_turn() is None and _active.get() is None
            finally:await runner.close()
    finally:r.close()


@pytest.mark.asyncio
async def test_exporter_failure_preserves_unsampled_handoff(monkeypatch):
    from opentelemetry.sdk.trace.export import SpanExportResult
    r=Runtime(config());seen=[]
    monkeypatch.setattr(r.exporters['trace'],'export',lambda batch:SpanExportResult.FAILURE)
    try:
        async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,_):
            with observation_sink(seen.append,runtime=r):
                output=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
            r.flush(1)
            assert output and len(seen)==1 and seen[0].usage.input_tokens==10
            assert r.health.snapshot()['trace']['failed']>0
    finally:r.close()


@pytest.mark.parametrize('code,error',[(408,'MODEL_TIMEOUT'),(504,'MODEL_TIMEOUT'),(401,'MODEL_AUTH_ERROR'),(429,'MODEL_RATE_LIMIT')])
def test_sdk_exception_code_mapping_also_applies_to_logical_operation(code,error):
    from google.genai.errors import APIError
    from backend.observability.model_instrumentation import error_category
    assert error_category(APIError(code,{'message':'private'}))[1].value==error


def test_ttft_uses_first_raw_output_boundary_and_not_usage_or_thought_packets():
    from backend.observability.model_instrumentation import ModelOperation
    from backend.observability.model_context import Attribution, ModelAgent, ModelPurpose
    clock=[10.0];seen=[]
    with observation_sink(seen.append):
        op=ModelOperation('gemini-2.5-flash',Attribution(ModelAgent.TEAM_MANAGER,ModelPurpose.ORCHESTRATION),streaming=True,clock=lambda:clock[0])
        attempt=op.attempt()
        clock[0]=11.0;attempt.observe({'usageMetadata':{'promptTokenCount':2}})
        attempt.observe({'candidates':[{'content':{'parts':[{'text':'private','thought':True}]}}]})
        attempt.observe({'candidates':[{'content':{'parts':[{'functionCall':None},{'functionCall':{}},{'inlineData':{}}]}}]})
        assert attempt.ttft is None
        clock[0]=12.0;attempt.observe({'candidates':[{'content':{'parts':[{'functionCall':{'name':'private','args':{'secret':'private'}}}]}}]})
        clock[0]=15.0;attempt.observe(payload());attempt.finish();op.finish()
    assert seen[0].ttft_ms==2000 and seen[0].duration_ms==5000
    assert seen[0].ttft_boundary=='first_provider_output'
    assert 'private' not in seen[0].model_dump_json()


@pytest.mark.asyncio
async def test_actual_warmup_and_concurrent_embedding_requests_detach_or_inherit_correctly():
    from backend.config.model_warmup import _run_warmup_request
    from backend.observability.model_provider import embedding_request
    seen=[];r=Runtime(config())
    async def handler(req):
        await asyncio.sleep(0)
        if 'embedContent' in str(req.url):
            return httpx.Response(200,json={'embeddings':[{'values':[.1,.2]}]})
        return httpx.Response(200,json=payload())
    try:
        async with fake_model(handler) as (base,client):
            root=TurnTrace('embedding-root','embedding-session',r)
            with root.attached(),observation_sink(seen.append,runtime=r):
                await _run_warmup_request(base,'gemini-2.5-flash')
                await asyncio.gather(*(embedding_request(client,agent='knowledge_retrieval',operation='embedding_document',model='text-embedding-005',contents=['document']) for _ in range(3)))
                root.finish()
            assert len(seen)==4 and seen[0].workload=='warmup' and seen[0].run_id is None
            assert all(o.run_id=='embedding-root' and o.agent.value=='knowledge_retrieval' for o in seen[1:])
            assert len({o.logical_call_id for o in seen})==4
            assert all(o.attempt==1 for o in seen)
    finally:r.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('agent,purpose',[('team_manager','classification'),('team_manager','presentation'),
    ('team_manager','synthesis'),('incident_manager','remediation'),('incident_manager','synthesis'),
    ('technical_authority_engineer','structured_output_repair'),('technical_authority_engineer','action_reselection'),
    ('technical_authority_engineer','remediation')])
async def test_bounded_business_operation_context_preserves_model_graph(agent,purpose):
    r=Runtime(config());seen=[]
    try:
        async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,_):
            root=TurnTrace('purpose-run','purpose-session',r)
            with root.attached(),observation_sink(seen.append,runtime=r),model_context(agent,purpose):
                model=instrument_model(base,agent,'orchestration' if agent=='team_manager' else 'specialist_reasoning')
                _=[x async for x in model.generate_content_async(request())]
                root.finish()
            assert len(seen)==1 and seen[0].operation.value==purpose and seen[0].agent.value==agent
            native={s.context.span_id:s for s in spans(r)}
            attempt=next(s for s in native.values() if s.name=='gen_ai.request')
            operation=native[attempt.parent.span_id]
            assert operation.name=='slopanoc.model.operation' and native[operation.parent.span_id].name=='slopanoc.turn'
            assert attempt.attributes['slopanoc.model_operation']==purpose
    finally:r.close()


def test_production_formatter_does_not_evaluate_sdk_content():
    r=Runtime(config())
    try:
        record=logging.LogRecord('google.genai',logging.ERROR,'',1,' '.join(SENTINELS),(),None)
        result=SafeJsonFormatter(r.config).format(record)
        assert not any(s in result for s in SENTINELS)
    finally:r.close()


@pytest.mark.asyncio
async def test_model_metric_projection_rejects_dynamic_dimensions_and_invalid_histograms():
    from dataclasses import replace
    from backend.observability.model_metrics import safe_point
    r=Runtime(config())
    try:
        async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,_):
            with observation_sink(lambda _:None,runtime=r):
                _=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
            r.flush(1)
            metrics=[metric for resource in r.exporters['metric'].records[-1].resource_metrics for scope in resource.scope_metrics for metric in scope.metrics]
            metric=next(m for m in metrics if m.name=='gen_ai.client.operation.duration')
            point=metric.data.data_points[0]
            assert set(point.attributes)=={'environment','provider','model','agent','operation','status'}
            for bad in (replace(point,attributes=dict(point.attributes)|{'run_id':'secret'}),
                        replace(point,attributes=dict(point.attributes)|{'model':'gemini-arbitrary-user-name'}),
                        replace(point,sum=float('nan')),replace(point,count=point.count+1)):
                with pytest.raises(ValueError):safe_point(metric,bad,r.config)
            assert safe_point(metric,replace(point,exemplars=['private']),r.config).exemplars==[]
    finally:r.close()
