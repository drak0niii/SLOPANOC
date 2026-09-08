"""POST-5.1 B6 -- instruction section 17: the direct/exact-match Teams
fast path (direct_read_fast_path.py) must be structurally bypassed
whenever this turn carries trusted current-turn image evidence, because
the shortcut's whole mechanism is to let `incident_manager`'s own REAL
model call be skipped entirely -- which would mean the image is silently
never reasoned over by anything (instruction section 17's own example:
"image + 'compare this with ABC bridge' must not become: Teams fast path
-> Teams-only answer -> image silently ignored").

Mirrors test_p4b3_fast_path.py's own two-tier discipline: a unit-level
test of `_capture_unique_match_for_fast_path`'s new gate, plus an
end-to-end test through the REAL `_fast_path_incident_manager` agent via a
real `Runner`, proving the model's own SECOND turn both happens AND
genuinely receives the image (never merely "the shortcut didn't fire").
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.agents.team_manager import direct_read_fast_path as fast_path_module
from backend.agents.team_manager.direct_read_fast_path import (
    _capture_unique_match_for_fast_path,
    _fast_path_incident_manager,
    _FAST_PATH_PENDING_STATE_KEY,
)


def _matched_response(chat_id: str = "chat-b2-id", title: str = "Network Operations Daily") -> dict[str, Any]:
    return {"chats": [], "match": "matched", "matched_chat": {"chat_id": chat_id, "title": title}}


def _teams_list_chats_tool() -> Any:
    return type("Tool", (), {"name": "teams_list_chats"})()


class _ImageBearingCtx:
    """Mirrors test_p4b3_fast_path.py's own `_Ctx`, but with a trusted
    image `Part` also present on `user_content` -- exactly the shape
    `MultimodalAgentTool` produces for a real image-bearing delegation.
    """

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.user_content = types.Content(
            role="user",
            parts=[
                types.Part.from_text(text=json.dumps({"requires_governed_knowledge": False})),
                types.Part.from_uri(file_uri="gs://bucket/screenshot.png", mime_type="image/png"),
            ],
        )


def test_capture_never_marks_state_when_image_evidence_is_present() -> None:
    ctx = _ImageBearingCtx()
    _capture_unique_match_for_fast_path(
        _teams_list_chats_tool(),
        {"topic": "Network Operations Daily", "pending_question": None, "pending_time_range": None},
        ctx,
        _matched_response(),
    )
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


def test_capture_still_marks_state_for_a_text_only_unique_match() -> None:
    """Regression: the new gate must not affect the existing, proven
    text-only fast path at all (instruction section 18)."""
    from backend.tests.test_p4b3_fast_path import _Ctx

    ctx = _Ctx()
    _capture_unique_match_for_fast_path(
        _teams_list_chats_tool(),
        {"topic": "Network Operations Daily", "pending_question": None, "pending_time_range": None},
        ctx,
        _matched_response(),
    )
    assert _FAST_PATH_PENDING_STATE_KEY in ctx.state


@pytest.mark.asyncio
async def test_end_to_end_image_bearing_unique_match_reaches_a_real_second_model_call_that_sees_the_image(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strongest possible proof: a request that would otherwise take
    the fast path (an exact, unique Teams match) must, when image evidence
    is present, still reach incident_manager's own SECOND real model turn
    -- AND that second turn's own `llm_request.contents` must still
    contain the image `Part` (never dropped along the way).
    """
    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        from backend.tests._fakes import FakeResponse, chat

        if json.get("operation") == "teams.listChats":
            return FakeResponse(200, [chat("chat-b2-id", "Network Operations Daily")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    execute_calls = 0

    async def fake_execute_read_continuation(**kwargs: Any) -> dict[str, Any]:
        nonlocal execute_calls
        execute_calls += 1
        return {"outcome": "ok"}

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", fake_execute_read_continuation)

    class _TwoTurnImageAwareLlm(BaseLlm):
        calls: int = 0
        saw_image_on_second_turn: bool = False

        async def generate_content_async(
            self, llm_request: Any, stream: bool = False
        ) -> AsyncGenerator[LlmResponse, None]:
            self.calls += 1
            if self.calls == 1:
                part = types.Part.from_function_call(
                    name="teams_list_chats", args={"topic": "Network Operations Daily"}
                )
                part.function_call.id = "call-1"
                yield LlmResponse(content=types.Content(role="model", parts=[part]))
                return
            # The real, un-shortcut-ed second turn -- prove it genuinely
            # still has the image in its own contents.
            for content in llm_request.contents:
                for content_part in content.parts:
                    if getattr(content_part, "file_data", None) is not None:
                        self.saw_image_on_second_turn = True
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part.from_text(
                            text=json.dumps(
                                {
                                    "outcome": "ok",
                                    "chat_id": "chat-b2-id",
                                    "chat_title": "Network Operations Daily",
                                    "summary": "Combined image and Teams synthesis.",
                                }
                            )
                        )
                    ],
                )
            )

    fake_llm = _TwoTurnImageAwareLlm(model="fake")
    probe_agent = _fast_path_incident_manager.model_copy(update={"model": fake_llm})

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )

    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-image-gate-test")
    last_text: Optional[str] = None
    request_text = json.dumps({"chat_topic": "Network Operations Daily", "requires_governed_knowledge": False})
    image_part = types.Part.from_uri(file_uri="gs://bucket/screenshot.png", mime_type="image/png")
    try:
        async for event in runner.run_async(
            user_id="u1",
            session_id=session.id,
            new_message=types.Content(
                role="user", parts=[types.Part.from_text(text=request_text), image_part]
            ),
        ):
            if event.content and event.content.parts:
                texts = [p.text for p in event.content.parts if p.text]
                if texts:
                    last_text = texts[-1]
    finally:
        reset_run_id(token)
        await runner.close()

    assert fake_llm.calls == 2  # the shortcut did NOT engage -- a real second turn happened
    assert execute_calls == 0  # the fast path's own deterministic executor was never invoked
    assert fake_llm.saw_image_on_second_turn is True  # and that second turn genuinely still had the image

    payload = json.loads(last_text)
    assert payload["outcome"] == "ok"
    assert payload["summary"] == "Combined image and Teams synthesis."
