"""Teams Image Vision corrective milestone, extended by the Multiple
Teams Hosted Images milestone -- backend/api/hosted_content_vision_
context.py.

Two tiers, mirroring this codebase's own established pattern (see
test_multimodal_agent_tool.py's own docstring):

  1. Plain unit tests of the module's own stash/pop/inject/discard/order
     mechanics, no ADK involved.
  2. A REAL, ADK-native end-to-end proof (real `Agent`/`Runner`, only the
     underlying model scripted via a real `BaseLlm` subclass) that TWO
     tool calls stashing bytes mid-turn cause the agent's OWN NEXT model
     call to genuinely receive TWO `Part.from_bytes` inline-data parts,
     in true Teams order, with exact mime_type/bytes -- never converted
     into prompt text, never leaked as Base64 anywhere in that call's
     text parts.
"""
from __future__ import annotations

from typing import Any, AsyncGenerator

import pytest
from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import PrivateAttr

from backend.api import hosted_content_vision_context as hcv
from backend.api.turn_context import bind_run_id, reset_run_id

APP_NAME = "test-hosted-content-vision"

_PNG_BYTES = b"\x89PNG\r\n\x1a\nFAKE-BUT-DETERMINISTIC-BYTES-FOR-A-TEST"
_JPEG_BYTES = b"\xff\xd8\xffFAKE-BUT-DETERMINISTIC-JPEG-BYTES"


class _FakeLlmRequest:
    def __init__(self) -> None:
        self.contents: list[Any] = []


def _inline_data_parts(content: Any) -> list[Any]:
    return [p for p in content.parts if getattr(p, "inline_data", None) is not None]


@pytest.fixture(autouse=True)
def _clean_store() -> Any:
    yield
    with hcv._lock:  # noqa: SLF001 -- test-only direct access to guarantee isolation.
        hcv._pending.clear()
        hcv._message_order.clear()
        hcv._message_metadata.clear()
        hcv._delivered.clear()


# --- Tier 1: plain unit mechanics -------------------------------------------


def test_stash_and_inject_round_trip_single_image() -> None:
    token = bind_run_id("run-1")
    try:
        assert hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", _PNG_BYTES) is True
        request = _FakeLlmRequest()
        result = hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    assert result is None  # never short-circuits the real model call
    assert len(request.contents) == 1
    content = request.contents[0]
    assert content.role == "user"
    file_parts = _inline_data_parts(content)
    assert len(file_parts) == 1
    assert file_parts[0].inline_data.mime_type == "image/png"
    assert file_parts[0].inline_data.data == _PNG_BYTES


def test_inject_is_a_no_op_when_nothing_pending() -> None:
    token = bind_run_id("run-2")
    try:
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    assert request.contents == []


def test_inject_consumes_all_pending_images_exactly_once() -> None:
    """A SECOND model call in the same run must NOT re-attach the same
    images -- they were already delivered once."""
    token = bind_run_id("run-3")
    try:
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", _PNG_BYTES)
        first = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=first)
        second = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=second)
    finally:
        reset_run_id(token)

    assert len(first.contents) == 1
    assert second.contents == []


def test_stash_outside_a_bound_turn_is_a_safe_no_op_and_reports_not_delivered() -> None:
    # No bind_run_id -- current_run_id() is None.
    delivered = hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", _PNG_BYTES)
    assert delivered is False
    with hcv._lock:  # noqa: SLF001
        assert hcv._pending == {}


def test_inject_outside_a_bound_turn_is_a_safe_no_op() -> None:
    request = _FakeLlmRequest()
    result = hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)

    assert result is None
    assert request.contents == []


def test_pending_images_are_isolated_per_run() -> None:
    """Section 8: images from run A must never enter run B."""
    token_a = bind_run_id("run-a")
    try:
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", b"AAA")
    finally:
        reset_run_id(token_a)

    token_b = bind_run_id("run-b")
    try:
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-b", "image/jpeg", b"BBB")
        request_b = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request_b)
    finally:
        reset_run_id(token_b)

    file_parts = _inline_data_parts(request_b.contents[0])
    assert len(file_parts) == 1
    assert file_parts[0].inline_data.data == b"BBB"

    # run-a's own stash is untouched by run-b's activity -- discard it
    # explicitly (would otherwise leak past this test).
    with hcv._lock:  # noqa: SLF001
        assert "run-a" in hcv._pending
        assert len(hcv._pending["run-a"]) == 1
        assert hcv._pending["run-a"][0].data == b"AAA"
    hcv.discard_pending_hosted_content_image("run-a")


def test_discard_is_idempotent_and_safe_for_an_unknown_run() -> None:
    hcv.discard_pending_hosted_content_image("never-registered")  # must not raise


def test_discard_clears_both_pending_images_and_order_map() -> None:
    token = bind_run_id("run-discard")
    try:
        hcv.record_message_hosted_content_order("msg-1", ["content-a", "content-b"])
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", _PNG_BYTES)
    finally:
        reset_run_id(token)

    hcv.discard_pending_hosted_content_image("run-discard")

    with hcv._lock:  # noqa: SLF001
        assert "run-discard" not in hcv._pending
        assert "run-discard" not in hcv._message_order


def test_a_second_stash_for_the_same_id_is_idempotent_never_duplicated() -> None:
    token = bind_run_id("run-idempotent")
    try:
        first = hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", _PNG_BYTES)
        second = hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", _PNG_BYTES)
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    assert first is True
    assert second is True
    assert len(_inline_data_parts(request.contents[0])) == 1


def test_injected_content_never_contains_the_raw_bytes_as_text() -> None:
    token = bind_run_id("run-no-text-leak")
    try:
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", _PNG_BYTES)
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    text_parts = [p.text for p in request.contents[0].parts if getattr(p, "text", None)]
    joined = "\n".join(text_parts)
    assert _PNG_BYTES not in joined.encode("utf-8", errors="ignore")
    import base64

    assert base64.b64encode(_PNG_BYTES).decode("ascii") not in joined


# --- Ordering (section 3/7/8) -----------------------------------------------


def test_multiple_images_preserve_recorded_teams_order_regardless_of_stash_arrival_order() -> None:
    """The core anti-reordering guarantee: even if images are STASHED in
    the WRONG order (simulating nondeterministic concurrent tool-call
    completion -- see this module's own "MULTIPLE IMAGES" docstring), the
    injected `Part`s must still follow the recorded canonical Teams order.
    """
    token = bind_run_id("run-order")
    try:
        hcv.record_message_hosted_content_order("msg-1", ["content-a", "content-b", "content-c"])
        # Stash out of order: C, then A, then B.
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-c", "image/png", b"C")
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", b"A")
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-b", "image/png", b"B")
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    file_parts = _inline_data_parts(request.contents[0])
    assert [p.inline_data.data for p in file_parts] == [b"A", b"B", b"C"]


def test_ordinal_is_globally_correct_across_multiple_separate_injection_calls_in_one_run() -> None:
    """LIVE-VALIDATION BUGFIX regression: a real live run proved the model
    frequently calls `teams_get_hosted_content` across SEPARATE model
    turns rather than one batched function-calling response -- each
    triggers its OWN `inject_pending_hosted_content_image` call. Both
    delivered images must still get their TRUE, distinct ordinal (1 and
    2), never both `ordinal=1` (the exact live defect found and fixed).
    """
    token = bind_run_id("run-split-delivery")
    try:
        hcv.record_message_hosted_content_order("msg-1", ["content-a", "content-b"])

        # First model turn: only content-a is retrieved/stashed, then
        # injected on its own -- simulates the model calling the tool
        # once, receiving a response, THEN calling it again in a LATER,
        # separate turn (not a batched multi-function-call response).
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", b"A")
        first_request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=first_request)

        # Second, separate model turn: content-b is retrieved/stashed and
        # injected in its OWN, later call.
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-b", "image/png", b"B")
        second_request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=second_request)

        delivered = hcv.pop_delivered_visual_evidence("run-split-delivery")
    finally:
        reset_run_id(token)

    ordinals_by_content_id = {d.hosted_content_id: d.ordinal for d in delivered}
    assert ordinals_by_content_id == {"content-a": 1, "content-b": 2}
    # The core live-observed defect: two delivered images must NEVER
    # share the same ordinal.
    assert len({d.ordinal for d in delivered}) == len(delivered)


def test_an_image_with_no_recorded_order_sorts_after_known_ordered_images_stably() -> None:
    """An id missing from the canonical order map (should not normally
    happen) must never crash or drop the image -- it sorts after every
    known-ordered image, using arrival order as a stable tiebreak.
    """
    token = bind_run_id("run-unknown-order")
    try:
        hcv.record_message_hosted_content_order("msg-1", ["content-a"])
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-unrecorded", "image/png", b"UNRECORDED")
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", b"A")
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    file_parts = _inline_data_parts(request.contents[0])
    assert [p.inline_data.data for p in file_parts] == [b"A", b"UNRECORDED"]


def test_order_recording_outside_a_bound_turn_is_a_safe_no_op() -> None:
    hcv.record_message_hosted_content_order("msg-1", ["content-a"])
    with hcv._lock:  # noqa: SLF001
        assert hcv._message_order == {}


def test_order_recording_with_empty_list_is_a_safe_no_op() -> None:
    token = bind_run_id("run-empty-order")
    try:
        hcv.record_message_hosted_content_order("msg-1", [])
    finally:
        reset_run_id(token)
    with hcv._lock:  # noqa: SLF001
        assert hcv._message_order.get("run-empty-order") in (None, {})


# --- Bounds (section 9) ------------------------------------------------------


def test_image_count_limit_rejects_beyond_max_and_reports_not_delivered() -> None:
    token = bind_run_id("run-count-limit")
    try:
        results = [
            hcv.stash_pending_hosted_content_image("chat-1", "msg-1", f"content-{i}", "image/png", f"IMG{i}".encode())
            for i in range(hcv.MAX_HOSTED_IMAGES_PER_MESSAGE + 2)
        ]
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    assert results[: hcv.MAX_HOSTED_IMAGES_PER_MESSAGE] == [True] * hcv.MAX_HOSTED_IMAGES_PER_MESSAGE
    assert results[hcv.MAX_HOSTED_IMAGES_PER_MESSAGE :] == [False, False]
    assert len(_inline_data_parts(request.contents[0])) == hcv.MAX_HOSTED_IMAGES_PER_MESSAGE


def test_total_byte_budget_rejects_an_image_that_would_exceed_it() -> None:
    token = bind_run_id("run-byte-limit")
    try:
        big = b"\x00" * (hcv.MAX_TOTAL_HOSTED_IMAGE_BYTES - 10)
        first = hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", big)
        second = hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-b", "image/png", b"\x00" * 100)
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    assert first is True
    assert second is False
    assert len(_inline_data_parts(request.contents[0])) == 1


def test_bounds_never_silently_process_an_unbounded_number_of_images() -> None:
    """Direct proof that stashing far more than the limit results in
    EXACTLY the bounded count being delivered, never all of them."""
    token = bind_run_id("run-unbounded-attempt")
    try:
        for i in range(50):
            hcv.stash_pending_hosted_content_image("chat-1", "msg-1", f"content-{i}", "image/png", b"x")
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    assert len(_inline_data_parts(request.contents[0])) == hcv.MAX_HOSTED_IMAGES_PER_MESSAGE


# --- Failure semantics (section 10) -----------------------------------------


def test_mixed_success_and_failure_only_successful_images_are_delivered() -> None:
    """One image's stash is simply never attempted for a corrupt/failed
    retrieval (get_hosted_content.py never calls stash for a failed
    validation) -- proves the OTHER, successfully-stashed images are
    completely unaffected."""
    token = bind_run_id("run-mixed")
    try:
        hcv.record_message_hosted_content_order("msg-1", ["content-a", "content-b", "content-c"])
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", b"A")
        # content-b deliberately never stashed (simulates a failed/corrupt image).
        hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-c", "image/jpeg", b"C")
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    file_parts = _inline_data_parts(request.contents[0])
    assert [p.inline_data.data for p in file_parts] == [b"A", b"C"]


# --- Tier 2: real ADK end-to-end proof --------------------------------------


def _stash_tool(
    chat_id: str, message_id: str, hosted_content_id: str, mime_type: str, data_hex: str, tool_context: ToolContext
) -> dict[str, Any]:
    delivered = hcv.stash_pending_hosted_content_image(
        chat_id, message_id, hosted_content_id, mime_type, bytes.fromhex(data_hex)
    )
    return {"content_type": mime_type, "delivered_for_visual_reasoning": delivered}


def _two_function_calls_response() -> LlmResponse:
    return LlmResponse(
        content=types.Content(
            role="model",
            parts=[
                types.Part(
                    function_call=types.FunctionCall(
                        name="_stash_tool",
                        args={
                            "chat_id": "chat-1",
                            "message_id": "msg-1",
                            "hosted_content_id": "content-b",
                            "mime_type": "image/jpeg",
                            "data_hex": _JPEG_BYTES.hex(),
                        },
                    )
                ),
                types.Part(
                    function_call=types.FunctionCall(
                        name="_stash_tool",
                        args={
                            "chat_id": "chat-1",
                            "message_id": "msg-1",
                            "hosted_content_id": "content-a",
                            "mime_type": "image/png",
                            "data_hex": _PNG_BYTES.hex(),
                        },
                    )
                ),
            ],
        ),
        partial=False,
    )


def _final_text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]), partial=False)


class _ScriptedLlm(BaseLlm):
    _responses: list[LlmResponse] = PrivateAttr(default_factory=list)
    _call_count: int = PrivateAttr(default=0)
    _captured_contents: list[list[types.Content]] = PrivateAttr(default_factory=list)

    def __init__(self, responses: list[LlmResponse], captured_contents: list[list[types.Content]], **kwargs: Any) -> None:
        super().__init__(model="scripted-vision-model", **kwargs)
        self._responses = responses
        self._captured_contents = captured_contents

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self._captured_contents.append(list(llm_request.contents))
        response = self._responses[self._call_count]
        self._call_count += 1
        yield response


@pytest.mark.asyncio
async def test_real_adk_turn_with_two_concurrent_tool_calls_delivers_two_ordered_multimodal_parts() -> None:
    """The mandatory multiple-image proof (instruction sections 6/7/12.G/
    12.H): the model issues TWO function calls for two different hosted-
    content ids in ONE response -- ADK executes both concurrently via
    `asyncio.gather` (verified against installed ADK source, see this
    module's own "MULTIPLE IMAGES" docstring) -- and the SAME agent's own
    NEXT real model call must still receive both images as real `Part`s,
    in the TRUE recorded Teams order (content-a before content-b), never
    the (here, deliberately reversed) function-call/completion order.
    """
    captured: list[list[types.Content]] = []
    scripted = _ScriptedLlm(
        responses=[_two_function_calls_response(), _final_text_response("described both images")],
        captured_contents=captured,
    )
    agent = Agent(
        name="vision_probe_agent",
        model=scripted,
        tools=[_stash_tool],
        before_model_callback=[hcv.inject_pending_hosted_content_image],
    )
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=session_service)
    session = await session_service.create_session(app_name=APP_NAME, user_id="u1")

    token = bind_run_id("real-adk-multi-run")
    try:
        hcv.record_message_hosted_content_order("msg-1", ["content-a", "content-b"])
        events = [
            event
            async for event in runner.run_async(
                user_id="u1",
                session_id=session.id,
                new_message=types.Content(
                    role="user", parts=[types.Part.from_text(text="what do the images show?")]
                ),
            )
        ]
    finally:
        reset_run_id(token)
        hcv.discard_pending_hosted_content_image("real-adk-multi-run")

    assert len(captured) == 2, "expected exactly two real model calls: the tool-call turn, then the post-tool turn"

    first_call_file_parts = [
        p for content in captured[0] for p in (content.parts or []) if getattr(p, "inline_data", None) is not None
    ]
    assert first_call_file_parts == []

    second_call_file_parts = [
        p for content in captured[1] for p in (content.parts or []) if getattr(p, "inline_data", None) is not None
    ]
    assert len(second_call_file_parts) == 2
    # TRUE Teams order (content-a, content-b) -- NOT the reversed function-
    # call order the scripted response issued them in (content-b, content-a).
    assert second_call_file_parts[0].inline_data.mime_type == "image/png"
    assert second_call_file_parts[0].inline_data.data == _PNG_BYTES
    assert second_call_file_parts[1].inline_data.mime_type == "image/jpeg"
    assert second_call_file_parts[1].inline_data.data == _JPEG_BYTES

    all_text = "\n".join(
        p.text for content in captured[1] for p in (content.parts or []) if getattr(p, "text", None)
    )
    import base64

    assert base64.b64encode(_PNG_BYTES).decode("ascii") not in all_text
    assert base64.b64encode(_JPEG_BYTES).decode("ascii") not in all_text
    assert _PNG_BYTES.hex() not in all_text
    assert _JPEG_BYTES.hex() not in all_text

    final_text_events = [e for e in events if e.content and any(getattr(p, "text", None) for p in e.content.parts or [])]
    assert final_text_events, "the turn must still complete normally with a final text response"
