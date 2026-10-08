import asyncio
import hashlib
import inspect
import pytest
from google.adk.agents import Agent
from google.adk.flows.llm_flows import functions
from google.adk.flows.llm_flows.base_llm_flow import BaseLlmFlow
from backend.observability.adk_adapter import ObservedAgent, compatible, dispatch
from backend.observability.model_adapter import instrument_model
from backend.observability.model_context import observation_sink
from backend.observability.runtime import Runtime
from backend.observability.turn_trace import TurnTrace
from backend.tests.test_observability_runtime import config
from backend.tests.test_observability_model_provider import fake_model, spans
from backend.tests.test_observability_agent_instrumentation import drive, synthetic
from backend.tests.test_observability_tool_instrumentation import invoke


def test_exact_pin_and_no_global_mutation_with_clones():
    original=functions._execute_single_function_call_async
    flow_method=BaseLlmFlow._postprocess_handle_function_calls_async
    assert compatible()
    for _ in range(3):assert dispatch() is dispatch()
    agent=ObservedAgent(name='team_manager',model='gemini-2.5-flash')
    for copy in (agent,agent.model_copy(),agent.clone()):
        assert isinstance(copy,ObservedAgent)
        assert copy._llm_flow._postprocess_handle_function_calls_async.__func__ is dispatch()
    ordinary=Agent(name='team_manager',model='gemini-2.5-flash')
    assert ordinary._llm_flow._postprocess_handle_function_calls_async.__func__ is flow_method
    assert functions._execute_single_function_call_async is original
    assert BaseLlmFlow._postprocess_handle_function_calls_async is flow_method

@pytest.mark.asyncio
async def test_incompatible_shape_fails_closed_before_tool_egress(monkeypatch):
    import backend.observability.adk_adapter as adapter
    adapter.dispatch.cache_clear()
    monkeypatch.setattr(adapter,'compatible',lambda:False)
    r=Runtime(config());root=TurnTrace('r','s',r);calls=[]
    async def body(value):calls.append(value);return {'status':'executed'}
    try:
        async with fake_model(lambda req:pytest.fail('synthetic')) as (base,_):
            with root.attached(),observation_sink(None,runtime=r):
                with pytest.raises(RuntimeError, match='Unsupported ADK reliability interface'):
                    await invoke(base,'teams_send_message',body)
                root.finish(RuntimeError('unsupported'))
            assert calls==[]
            assert r.health.snapshot()['trace']['failed']>0
            assert not any(s.name=='slopanoc.tool' for s in spans(r))
    finally:r.close();adapter.dispatch.cache_clear()
