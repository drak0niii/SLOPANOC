"""Pinned PER-FLOW ADK adapter; no module/class/process-wide monkeypatching.

Bind the original dispatch bytecode to an isolated globals dictionary with only
its telemetry facade changed. All scheduling, callbacks, response construction,
argument handling and exception behavior remain ADK-owned. Installed source stays
untouched. Agent async iterator attaches context only during next/close.
"""
from contextlib import asynccontextmanager
from functools import lru_cache
from importlib.metadata import version
import hashlib
import inspect
from types import FunctionType, MethodType, SimpleNamespace
from opentelemetry import context
from google.adk.agents import Agent
from google.adk.flows.llm_flows import functions
from google.adk.flows.llm_flows.base_llm_flow import BaseLlmFlow
from google.adk.telemetry import _instrumentation
from .agent_instrumentation import start_agent, active_execution, runtime_for_execution, attached_scope, finish_scope
from .tool_instrumentation import start_tool, observe_result
from .model_instrumentation import guarded, protect_content
from .turn_trace import degraded
from .deadlines import boundary, child_budget, enforce, close_iterator
from .reliability_contract import Category

# Source fingerprints deliberately reviewed against the exact installed pin.
FINGERPRINTS = {'handle_function_calls_async': '6aa32d277db4c2e39af6d28f6254917ba13bbe8208bcfe57302e88ac89a0ee58', 'handle_function_call_list_async': '9e6e2544027f16f9db64799ff658ea31590185386c80de3c37b59a612b6b912d', '_execute_single_function_call_async': 'f4eda798d7885e1d2a133e52c8321fc39d707a4363c45831b11a608d76ac6cec', '_postprocess_handle_function_calls_async': '0973b9a99a7551ef2c3a9720bec0e12ff0694ed70cb15b77635a94a38c2534a1'}


def compatible():
    if version('google-adk') != '1.33.0':
        return False
    targets = {'handle_function_calls_async':functions.handle_function_calls_async,
        'handle_function_call_list_async':functions.handle_function_call_list_async,
        '_execute_single_function_call_async':functions._execute_single_function_call_async,
        '_postprocess_handle_function_calls_async':BaseLlmFlow._postprocess_handle_function_calls_async}
    return all(hashlib.sha256(inspect.getsource(fn).encode()).hexdigest() == FINGERPRINTS.get(name)
        for name, fn in targets.items()) and 'function_response_event' in _instrumentation.TelemetryContext.__dataclass_fields__


@asynccontextmanager
async def record_tool_execution(tool, agent, function_args):
    scope = guarded(runtime_for_execution(), start_tool, tool.name, agent.name)
    tel = _instrumentation.TelemetryContext(otel_context=context.get_current())
    error = None
    try:
        with attached_scope(scope):
            async with boundary(Category.TOOL):
                try:
                    yield tel
                finally:
                    if scope and tel.function_response_event is not None:
                        # ADK response construction holds the final post-callback result.
                        guarded(scope.runtime, observe_event_result, scope, tel.function_response_event)
    except BaseException as exc:
        error = exc
        raise
    finally:
        if scope:
            finish_scope(scope, error)


def observe_event_result(scope, event):
    for part in event.content.parts or ():
        response = part.function_response
        if response is not None:
            observe_result(scope, response.response)


async def call_tool(tool, *, args, tool_context):
    scope = active_execution()
    if scope:
        scope.executed = True
    try:
        if tool.name in {'teams_list_chats','teams_get_messages','get_resolved_chat_messages',
                'teams_get_hosted_content','teams_get_all_hosted_content','teams_get_members',
                'teams_create_chat','teams_send_message'} and hasattr(tool,'func') and not inspect.iscoroutinefunction(tool.func):
            from copy import copy
            from functools import wraps
            from .blocking_work import isolated_call
            original = tool.func
            @wraps(original)
            async def invoke(*positional, **keyword):
                return await isolated_call(original,*positional,**keyword)
            isolated = copy(tool)
            isolated.func = invoke
            result = await functions.__call_tool_async(isolated,args=args,tool_context=tool_context)
        else:
            result = await functions.__call_tool_async(tool, args=args, tool_context=tool_context)
    except Exception as exc:
        if scope:
            # An ADK error callback may convert the exception to a safe result.
            scope.result_category = 'error'
            from .stages import RunStatus
            from .errors import ErrorCode
            scope.status, scope.error = (RunStatus.TIMEOUT, ErrorCode.TOOL_TIMEOUT) if isinstance(exc, TimeoutError) else (RunStatus.FAILED, ErrorCode.TOOL_ERROR)
        raise
    return observe_result(scope, result)


def clone(fn, globals_dict):
    copied = FunctionType(fn.__code__, globals_dict, fn.__name__, fn.__defaults__, fn.__closure__)
    copied.__kwdefaults__ = fn.__kwdefaults__
    return copied


@lru_cache(maxsize=1)
def dispatch():
    if not compatible():
        raise RuntimeError('Unsupported ADK telemetry interface')
    local = dict(vars(functions))
    facade = SimpleNamespace(**vars(_instrumentation))
    facade.record_tool_execution = record_tool_execution
    local['_instrumentation'] = facade
    local['__call_tool_async'] = call_tool
    # Keep async tools on their owning loop; sync Teams calls use bounded isolation.
    async def bounded_dispatch(tool, args, tool_context, max_workers=4):
        return await call_tool(tool, args=args, tool_context=tool_context)
    local['_call_tool_in_thread_pool'] = bounded_dispatch
    for name in ('handle_function_calls_async','handle_function_call_list_async','_execute_single_function_call_async'):
        local[name] = clone(getattr(functions,name), local)
    functions_facade = SimpleNamespace(**local)
    flow_globals = dict(BaseLlmFlow._postprocess_handle_function_calls_async.__globals__)
    flow_globals['functions'] = functions_facade
    return clone(BaseLlmFlow._postprocess_handle_function_calls_async, flow_globals)


def observe_agent_result(scope, event):
    # Structured public output only, never instructions/reasoning or partial text.
    import json
    if event.partial or event.content is None:
        return
    parts = event.content.parts or ()
    if any(p.function_call or p.function_response for p in parts):
        return
    text = ''.join(p.text for p in parts if p.text and not p.thought)
    if not text or len(text) > 1_000_000:
        return
    try:
        result = json.loads(text)
    except (ValueError, TypeError):
        return
    from .tool_instrumentation import classify
    classify(scope, result)
    if scope.error is not None and scope.error.value == 'TOOL_ERROR':
        from .errors import ErrorCode
        scope.error = ErrorCode.SPECIALIST_FAILED


class ObservedAgent(Agent):
    """Drop-in agent definition; copies/clones preserve this class and tool roster."""
    @property
    def _llm_flow(self):
        flow = super()._llm_flow
        try:
            flow._postprocess_handle_function_calls_async = MethodType(dispatch(), flow)
        except Exception:
            degraded(runtime_for_execution())
            raise RuntimeError('Unsupported ADK reliability interface') from None
        return flow

    async def run_async(self, parent_context):
        protect_content()
        scope = guarded(runtime_for_execution(), start_agent, self)
        iterator = super().run_async(parent_context)
        error = None
        budget = child_budget(Category.AGENT)
        try:
            while True:
                try:
                    with attached_scope(scope):
                        async with enforce(budget):
                            event = await iterator.__anext__()
                except StopAsyncIteration:
                    break
                if scope and self.output_schema is not None:
                    guarded(scope.runtime, observe_agent_result, scope, event)
                yield event
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                with attached_scope(scope):
                    await close_iterator(iterator)
            finally:
                if scope:
                    finish_scope(scope, error)
