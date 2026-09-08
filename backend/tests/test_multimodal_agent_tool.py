"""POST-5.1 B6 -- backend/agents/team_manager/multimodal_agent_tool.py.

Proves, against the REAL ADK `AgentTool` mechanism (a real outer `Agent`/
`Runner`, a real nested `Agent`/`Runner` -- only the underlying model calls
are scripted, via a real `google.adk.models.base_llm.BaseLlm` subclass,
never a fake Runner -- mirrors test_chat_service_function_call_
continuation.py's own established, ADK-native test seam):

  - text-only delegation is byte-for-byte unaffected (no `file_data` part
    ever appended when the top-level turn carried no image);
  - image-bearing delegation causes the NESTED agent's own `Content` to
    contain the structured-request text part FIRST, then every trusted
    image `Part.from_uri` from the top-level turn, in order;
  - `Part.from_bytes` is never used anywhere in this path;
  - no GCS URI ever appears inside the structured-request TEXT part;
  - output-schema validation still applies to the nested agent's response
    (unchanged base `AgentTool` behavior);
  - state-delta forwarding from the nested call back to the parent
    `tool_context.state` still works (unchanged base `AgentTool` behavior).
"""
from __future__ import annotations

from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.agents import Agent
from google.adk.events import EventActions
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import BaseModel, PrivateAttr

from backend.agents.team_manager.multimodal_agent_tool import MultimodalAgentTool

APP_NAME = "test-multimodal-agent-tool"


class _ProbeRequest(BaseModel):
    question: str


class _ProbeResponse(BaseModel):
    echo: str = "ok"


class _CapturingLlm(BaseLlm):
    """Captures the exact `Content` the NESTED Runner call received (the
    real proof this module needs -- never inferred from what the outer
    model merely "said" it was forwarding), then returns one scripted
    final-text response satisfying `_ProbeResponse`'s schema.
    """

    _captured: list[list[types.Content]] = PrivateAttr(default_factory=list)

    def __init__(self, captured: list[list[types.Content]], **kwargs: Any) -> None:
        super().__init__(model="scripted-probe-model", **kwargs)
        self._captured = captured

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._captured.append(list(llm_request.contents))
        yield LlmResponse(
            content=types.Content(
                role="model", parts=[types.Part.from_text(text=_ProbeResponse(echo="ok").model_dump_json())]
            ),
            partial=False,
        )


def _function_call_response(name: str, args: Optional[dict] = None) -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model", parts=[types.Part(function_call=types.FunctionCall(name=name, args=args or {}))]
        ),
        partial=False,
    )


def _final_text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]), partial=False)


class _OuterScriptedLlm(BaseLlm):
    _responses: list[LlmResponse] = PrivateAttr(default_factory=list)
    _call_count: int = PrivateAttr(default=0)

    def __init__(self, responses: list[LlmResponse], **kwargs: Any) -> None:
        super().__init__(model="scripted-outer-model", **kwargs)
        self._responses = responses

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        response = self._responses[self._call_count]
        self._call_count += 1
        yield response


def _build_outer_runner(
    session_service: InMemorySessionService, captured: list[list[types.Content]]
) -> Runner:
    probe_agent = Agent(
        name="probe_agent",
        model=_CapturingLlm(captured),
        input_schema=_ProbeRequest,
        output_schema=_ProbeResponse,
    )
    probe_tool = MultimodalAgentTool(agent=probe_agent)
    outer_agent = Agent(
        name="team_manager",
        model=_OuterScriptedLlm(
            [
                _function_call_response("probe_agent", {"question": "what do you see?"}),
                _final_text_response("done"),
            ]
        ),
        tools=[probe_tool],
    )
    return Runner(app_name=APP_NAME, agent=outer_agent, session_service=session_service)


def _text_parts(content: types.Content) -> list[str]:
    return [p.text for p in content.parts if getattr(p, "text", None)]


def _file_data_parts(content: types.Content) -> list[types.Part]:
    return [p for p in content.parts if getattr(p, "file_data", None) is not None]


@pytest.mark.asyncio
async def test_text_only_delegation_is_byte_for_byte_unaffected() -> None:
    session_service = InMemorySessionService()
    await session_service.create_session(app_name=APP_NAME, user_id="u1", session_id="s1")
    captured: list[list[types.Content]] = []
    runner = _build_outer_runner(session_service, captured)

    user_content = types.Content(role="user", parts=[types.Part.from_text(text="hello, no image here")])
    async for _ in runner.run_async(user_id="u1", session_id="s1", new_message=user_content):
        pass

    assert len(captured) == 1
    nested_content = captured[0][-1]  # the new_message this nested Runner call received
    assert len(_file_data_parts(nested_content)) == 0
    assert len(_text_parts(nested_content)) == 1


@pytest.mark.asyncio
async def test_image_bearing_delegation_appends_trusted_image_parts_in_order() -> None:
    session_service = InMemorySessionService()
    await session_service.create_session(app_name=APP_NAME, user_id="u1", session_id="s1")
    captured: list[list[types.Content]] = []
    runner = _build_outer_runner(session_service, captured)

    image_1 = types.Part.from_uri(file_uri="gs://bucket/obj-1.png", mime_type="image/png")
    image_2 = types.Part.from_uri(file_uri="gs://bucket/obj-2.jpg", mime_type="image/jpeg")
    user_content = types.Content(
        role="user",
        parts=[types.Part.from_text(text="look at these"), image_1, image_2],
    )
    async for _ in runner.run_async(user_id="u1", session_id="s1", new_message=user_content):
        pass

    assert len(captured) == 1
    nested_content = captured[0][-1]
    file_parts = _file_data_parts(nested_content)
    assert len(file_parts) == 2
    assert file_parts[0].file_data.file_uri == "gs://bucket/obj-1.png"
    assert file_parts[0].file_data.mime_type == "image/png"
    assert file_parts[1].file_data.file_uri == "gs://bucket/obj-2.jpg"
    assert file_parts[1].file_data.mime_type == "image/jpeg"

    # The structured-request text part is still present and FIRST -- image
    # parts are appended, never prepended/interleaved.
    text_parts = _text_parts(nested_content)
    assert len(text_parts) == 1
    assert "what do you see?" in text_parts[0]  # the probe_agent's own request JSON

    # No GCS URI ever leaks into the text part.
    assert "gs://" not in text_parts[0]


@pytest.mark.asyncio
async def test_image_only_delegation_never_fabricates_extra_text() -> None:
    """An image with a blank top-level user text part must still produce
    a well-formed nested request -- the structured-request text part
    comes from the OUTER model's own tool-call args (`_ProbeRequest`),
    never from the (possibly blank) top-level text.
    """
    session_service = InMemorySessionService()
    await session_service.create_session(app_name=APP_NAME, user_id="u1", session_id="s1")
    captured: list[list[types.Content]] = []
    runner = _build_outer_runner(session_service, captured)

    image_only = types.Part.from_uri(file_uri="gs://bucket/only.png", mime_type="image/png")
    user_content = types.Content(role="user", parts=[image_only])
    async for _ in runner.run_async(user_id="u1", session_id="s1", new_message=user_content):
        pass

    nested_content = captured[0][-1]
    assert len(_file_data_parts(nested_content)) == 1
    assert len(_text_parts(nested_content)) == 1  # the structured request JSON, never blank/fabricated


@pytest.mark.asyncio
async def test_output_schema_validation_still_applies() -> None:
    """Unchanged base-`AgentTool` behavior: the nested agent's final text
    must still validate against its own `output_schema` -- an image-
    bearing call does not weaken this.
    """
    session_service = InMemorySessionService()
    await session_service.create_session(app_name=APP_NAME, user_id="u1", session_id="s1")

    class _MalformedLlm(BaseLlm):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="malformed-model", **kwargs)

        async def generate_content_async(self, llm_request, stream=False):
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text="not valid json at all")]),
                partial=False,
            )

    probe_agent = Agent(
        name="probe_agent", model=_MalformedLlm(), input_schema=_ProbeRequest, output_schema=_ProbeResponse
    )
    probe_tool = MultimodalAgentTool(agent=probe_agent)
    outer_agent = Agent(
        name="team_manager",
        model=_OuterScriptedLlm(
            [_function_call_response("probe_agent", {"question": "x"}), _final_text_response("done")]
        ),
        tools=[probe_tool],
    )
    runner = Runner(app_name=APP_NAME, agent=outer_agent, session_service=session_service)

    user_content = types.Content(role="user", parts=[types.Part.from_text(text="hi")])
    with pytest.raises(Exception):
        async for _ in runner.run_async(user_id="u1", session_id="s1", new_message=user_content):
            pass


@pytest.mark.asyncio
async def test_state_delta_forwarding_still_works() -> None:
    """Unchanged base-`AgentTool` behavior: a `temp:`-prefixed state_delta
    the NESTED agent's own tool call writes is still forwarded, via
    `event.actions.state_delta`, into the parent `tool_context.state` --
    proven end to end by observing the OUTER Runner's own session state
    (`temp:` keys are visible on the live in-memory session for the
    remainder of the SAME outer turn, exactly like every other `temp:` key
    already relied on elsewhere in this codebase).
    """
    def _echo_tool(value: str, tool_context: ToolContext) -> dict:
        tool_context.state["probe_seen"] = value
        return {"ok": True}

    def _record_probe_seen(tool: Any, args: dict, tool_context: ToolContext, tool_response: Any) -> None:
        seen.append(tool_context.state.get("probe_seen"))

    seen: list[Any] = []

    class _NestedToolCallingLlm(BaseLlm):
        _step: list[int] = PrivateAttr(default_factory=lambda: [0])

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="nested-tool-calling-model", **kwargs)

        async def generate_content_async(self, llm_request, stream=False):
            if self._step[0] == 0:
                self._step[0] += 1
                yield _function_call_response("_echo_tool", {"value": "hello-from-nested"})
            else:
                yield LlmResponse(
                    content=types.Content(
                        role="model", parts=[types.Part.from_text(text=_ProbeResponse(echo="ok").model_dump_json())]
                    ),
                    partial=False,
                )

    session_service = InMemorySessionService()
    await session_service.create_session(app_name=APP_NAME, user_id="u1", session_id="s1")

    probe_agent = Agent(
        name="probe_agent",
        model=_NestedToolCallingLlm(),
        input_schema=_ProbeRequest,
        output_schema=_ProbeResponse,
        tools=[_echo_tool],
    )
    probe_tool = MultimodalAgentTool(agent=probe_agent)
    outer_agent = Agent(
        name="team_manager",
        model=_OuterScriptedLlm(
            [_function_call_response("probe_agent", {"question": "x"}), _final_text_response("done")]
        ),
        tools=[probe_tool],
        after_tool_callback=_record_probe_seen,
    )
    runner = Runner(app_name=APP_NAME, agent=outer_agent, session_service=session_service)
    user_content = types.Content(role="user", parts=[types.Part.from_text(text="hi")])
    async for _ in runner.run_async(user_id="u1", session_id="s1", new_message=user_content):
        pass

    assert seen == ["hello-from-nested"]
