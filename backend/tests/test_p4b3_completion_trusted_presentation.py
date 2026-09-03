"""P4B.3 COMPLETION PASS + CORRECTION PASS -- direct-unique reads converge
into the SAME trusted-result + `presentation_team_manager` pipeline post-
selection already uses, and the trust boundary fails closed.

CORRECTION PASS CONTEXT: the original completion-pass mechanism used a
`ContextVar` (`_pending_trusted_result`) to bridge `incident_manager`'s
nested AgentTool call back to team_manager's own next turn. This NEVER
actually worked -- ADK's own `handle_function_call_list_async` runs every
tool call (including the `incident_manager` AgentTool call) inside a
child `asyncio.Task`, and a `ContextVar.set(...)` performed inside a child
task does not propagate back to the parent's context (verified with a
minimal, isolated repro before writing this fix -- see direct_read_fast_
path.py's own module docstring, "CORRECTION PASS"). Every direct-unique
read therefore silently fell through to team_manager's own NORMAL,
full-instruction presentation turn -- `presentation_team_manager` was
never actually reached in production, and the previous version of this
test file could not detect that because its shared fake `BaseLlm` was
scripted by call COUNT alone, unable to distinguish "presentation_team_
manager's turn" from "team_manager's own normal fallback turn".

This file replaces that test with:
  - Tier 1: domain-logic tests for the corrected run-id-keyed registry
    (`_pending_trusted_result_by_run`) and its fail-closed behavior.
  - Tier 2 (TEST A/B): a full real-ADK end-to-end test using FOUR
    independent fake `BaseLlm` instances -- one per real model call in the
    call graph (team_manager routing, incident_manager discovery,
    incident_manager synthesis-only, presentation_team_manager) -- each
    appending to a SHARED, ordered call log, so the test can assert the
    EXACT ordered graph, not merely a count that could hide a wrong-agent
    substitution the way the previous version's single shared fake did.
  - Tier 3 (TEST C/D/E/F): fail-closed behavior for run-mismatch/source-
    mismatch/malformed envelopes, and confirmation that a VALID envelope
    whose own `outcome` is "error" still goes through normal trusted
    presentation (never conflated with an invalid/untrusted envelope).
  - Tier 4 (TEST G/H): ContextVar/registry cleanup and final durable-event
    behavior for both success and fail-closed paths.
  - Tier 5 (TEST I/J): ambiguity/post-selection regression spot-checks.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import AgentTool
from google.genai import types

from backend.agents.team_manager import direct_read_fast_path as fast_path_module
from backend.agents.team_manager.direct_read_fast_path import (
    _pending_trusted_result_by_run,
    _present_fast_path_result_via_trusted_pipeline,
    _TRUST_VALIDATION_FAILURE_TEXT,
    discard_pending_trusted_result,
    get_fast_path_team_manager,
)
from backend.agents.team_manager.read_continuation_presentation import (
    build_and_validate_trusted_envelope,
    build_trusted_specialist_result_envelope,
)


# --- Tier 1: domain logic ---------------------------------------------------


class _FakeState(dict):
    def to_dict(self) -> dict:
        return dict(self)


class _FakeInvocationContext:
    def __init__(self, invocation_id: str = "inv-1") -> None:
        self.invocation_id = invocation_id
        self.user_content = types.Content(role="user", parts=[types.Part.from_text(text="summarize Network Ops")])


class _FakeCallbackContext:
    def __init__(self, state: Optional[dict] = None) -> None:
        self.state = _FakeState(state or {})
        self._invocation_context = _FakeInvocationContext()
        self.invocation_id = "inv-1"
        self.user_content = self._invocation_context.user_content


def test_build_and_validate_trusted_envelope_round_trips() -> None:
    result = {"outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "Network Operations Daily", "summary": "Done."}
    validated = build_and_validate_trusted_envelope("run-A", result)
    assert validated is not None
    assert validated["outcome"] == "ok"
    assert validated["chat_id"] == "chat-b2-id"


def test_build_and_validate_trusted_envelope_rejects_malformed_result() -> None:
    validated = build_and_validate_trusted_envelope("run-A", {"summary": "no outcome field"})
    assert validated is None


@pytest.fixture(autouse=True)
def _clear_pending_registry() -> Any:
    _pending_trusted_result_by_run.clear()
    yield
    _pending_trusted_result_by_run.clear()


@pytest.mark.asyncio
async def test_before_model_callback_noop_when_no_pending_trusted_result() -> None:
    ctx = _FakeCallbackContext()
    result = await _present_fast_path_result_via_trusted_pipeline(ctx, llm_request=None)
    assert result is None


# --- TEST C: run mismatch fails closed --------------------------------------


@pytest.mark.asyncio
async def test_run_id_mismatch_fails_closed_never_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A result stashed under a DIFFERENT run_id than the one this callback
    is currently executing under must never be consumed, and the pending
    entry for run B (which never had anything staged) must not somehow
    succeed either -- covers section 13.
    """
    called_presentation = False

    async def fail_if_called(**kwargs: Any) -> Any:
        nonlocal called_presentation
        called_presentation = True
        raise AssertionError("presentation must never run for a run_id mismatch")

    monkeypatch.setattr(fast_path_module, "_run_trusted_presentation", fail_if_called)

    # Stash under run A only.
    _pending_trusted_result_by_run["run-A"] = {
        "outcome": "ok",
        "chat_id": "chat-b2-id",
        "chat_title": "Network Operations Daily",
        "summary": "Done.",
    }

    # Callback executes under run B -- pops its OWN key (nothing there),
    # never touches run A's entry.
    ctx = _FakeCallbackContext()
    ctx._invocation_context.invocation_id = "run-B"
    ctx.invocation_id = "run-B"

    monkeypatch.setattr(fast_path_module, "current_run_id", lambda: "run-B")

    result = await _present_fast_path_result_via_trusted_pipeline(ctx, llm_request=None)

    assert result is None  # run B legitimately has nothing pending -- normal turn proceeds
    assert not called_presentation
    assert "run-A" in _pending_trusted_result_by_run  # untouched, not stolen by run B

    # Cleanup no longer needed for the assertion, but discard it explicitly
    # (mirrors chat_service.py's own backstop) so this test does not leak
    # into others despite the autouse fixture already covering it.
    discard_pending_trusted_result("run-A")


def test_validate_trusted_envelope_rejects_a_stale_run_id() -> None:
    """The lower-level building block section 13 depends on: an envelope
    built for run A, explicitly validated against run B, must fail.
    """
    from backend.agents.team_manager.read_continuation_presentation import validate_trusted_envelope_for_run

    result = {"outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "Network Operations Daily", "summary": "Done."}
    envelope = build_trusted_specialist_result_envelope("run-A", result)
    assert envelope is not None

    assert validate_trusted_envelope_for_run(envelope, current_run_id="run-B") is None
    assert validate_trusted_envelope_for_run(envelope, current_run_id="run-A") is not None


# --- TEST D: source mismatch fails closed -----------------------------------


def test_source_mismatch_fails_closed() -> None:
    """`TrustedSpecialistResult.source` is a closed field -- only
    "incident_manager" validates. Constructing one with a wrong source
    must raise at construction time (never silently coerced), and
    `build_and_validate_trusted_envelope`'s own dict-based path must
    likewise reject a raw dict claiming a different source.
    """
    from backend.agents.team_manager.read_continuation_presentation import (
        TrustedSpecialistResult,
        validate_trusted_envelope_for_run,
    )
    from backend.agents.incident_manager.schemas import IncidentManagerResponse

    with pytest.raises(Exception):
        TrustedSpecialistResult(
            run_id="run-A",
            source="some_other_agent",
            result=IncidentManagerResponse(outcome="ok", chat_id="chat-b2-id", chat_title="X", summary="s"),
        )

    raw = {
        "run_id": "run-A",
        "source": "some_other_agent",
        "result": {"outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "X", "summary": "s"},
    }
    assert validate_trusted_envelope_for_run(raw, current_run_id="run-A") is None


# --- TEST E: malformed envelope fails closed --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "malformed_result",
    [
        {"summary": "missing required outcome field"},
        {"outcome": "not_a_real_outcome_value"},
        "not-even-a-dict",
        None,
    ],
)
async def test_malformed_pending_result_fails_closed(malformed_result: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    presentation_called = False

    async def fail_if_called(**kwargs: Any) -> Any:
        nonlocal presentation_called
        presentation_called = True
        raise AssertionError("presentation must never run for a malformed envelope")

    monkeypatch.setattr(fast_path_module, "_run_trusted_presentation", fail_if_called)
    monkeypatch.setattr(fast_path_module, "current_run_id", lambda: "run-malformed")

    if malformed_result is not None:
        _pending_trusted_result_by_run["run-malformed"] = malformed_result
    else:
        # `None` is indistinguishable from "nothing pending" by design --
        # covered separately by the no-op test above; skip constructing an
        # entry here since `.pop(..., None)` cannot tell the difference.
        pytest.skip("None is not a distinguishable 'pending' entry by design")

    ctx = _FakeCallbackContext()
    ctx.invocation_id = "run-malformed"
    result = await _present_fast_path_result_via_trusted_pipeline(ctx, llm_request=None)

    assert result is not None  # NEVER None -- that would let normal team_manager continue
    assert not presentation_called
    text = "".join(p.text for p in result.content.parts if p.text)
    assert text == _TRUST_VALIDATION_FAILURE_TEXT
    assert "run-malformed" not in _pending_trusted_result_by_run  # consumed, not left dangling


# --- TEST F: a VALID envelope with outcome="error" is NOT conflated --------
# --- with an invalid/untrusted one ------------------------------------------


@pytest.mark.asyncio
async def test_valid_error_outcome_still_goes_through_normal_trusted_presentation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 17: a specialist result that is itself well-formed but
    reports `outcome="error"` (e.g. a genuine Teams retrieval failure) is
    a VALID `IncidentManagerResponse` -- it must still be promoted through
    the normal trusted-presentation path (not treated as an invalid
    envelope) so `presentation_team_manager` can phrase it using its own
    existing "error" outcome guidance.
    """
    presented_with: dict[str, Any] = {}

    async def fake_run_trusted_presentation(**kwargs: Any) -> types.Content:
        presented_with.update(kwargs)
        return types.Content(role="model", parts=[types.Part.from_text(text="Sorry, that request could not be completed.")])

    monkeypatch.setattr(fast_path_module, "_run_trusted_presentation", fake_run_trusted_presentation)
    monkeypatch.setattr(fast_path_module, "current_run_id", lambda: "run-valid-error")

    _pending_trusted_result_by_run["run-valid-error"] = {
        "outcome": "error",
        "detail": "The Teams connector is temporarily unavailable.",
    }

    ctx = _FakeCallbackContext()
    ctx.invocation_id = "run-valid-error"
    result = await _present_fast_path_result_via_trusted_pipeline(ctx, llm_request=None)

    assert result is not None
    text = "".join(p.text for p in result.content.parts if p.text)
    assert text == "Sorry, that request could not be completed."
    assert presented_with["validated_result"]["outcome"] == "error"


# --- TEST G: registry cleanup (success / failure / cancellation) -----------


@pytest.mark.asyncio
async def test_registry_cleared_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_run_trusted_presentation(**kwargs: Any) -> types.Content:
        return types.Content(role="model", parts=[types.Part.from_text(text="presented.")])

    monkeypatch.setattr(fast_path_module, "_run_trusted_presentation", fake_run_trusted_presentation)
    monkeypatch.setattr(fast_path_module, "current_run_id", lambda: "run-success")

    _pending_trusted_result_by_run["run-success"] = {
        "outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "X", "summary": "s"
    }
    ctx = _FakeCallbackContext()
    ctx.invocation_id = "run-success"
    await _present_fast_path_result_via_trusted_pipeline(ctx, llm_request=None)

    assert "run-success" not in _pending_trusted_result_by_run


@pytest.mark.asyncio
async def test_registry_cleared_on_validation_failure() -> None:
    _pending_trusted_result_by_run["run-fail"] = {"outcome": "not_a_real_value"}
    ctx = _FakeCallbackContext()
    ctx.invocation_id = "run-fail"

    import backend.agents.team_manager.direct_read_fast_path as module

    orig = module.current_run_id
    module.current_run_id = lambda: "run-fail"
    try:
        await _present_fast_path_result_via_trusted_pipeline(ctx, llm_request=None)
    finally:
        module.current_run_id = orig

    assert "run-fail" not in _pending_trusted_result_by_run


@pytest.mark.asyncio
async def test_registry_cleared_when_presentation_raises_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def cancelling(**kwargs: Any) -> Any:
        raise asyncio.CancelledError()

    monkeypatch.setattr(fast_path_module, "_run_trusted_presentation", cancelling)
    monkeypatch.setattr(fast_path_module, "current_run_id", lambda: "run-cancel")

    _pending_trusted_result_by_run["run-cancel"] = {
        "outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "X", "summary": "s"
    }
    ctx = _FakeCallbackContext()
    ctx.invocation_id = "run-cancel"

    with pytest.raises(asyncio.CancelledError):
        await _present_fast_path_result_via_trusted_pipeline(ctx, llm_request=None)

    # Popped BEFORE the presentation attempt -- cancellation during
    # presentation must not leave a stale entry behind either.
    assert "run-cancel" not in _pending_trusted_result_by_run


def test_discard_pending_trusted_result_is_a_safe_noop_when_nothing_pending() -> None:
    discard_pending_trusted_result("never-existed")  # must not raise


def test_discard_pending_trusted_result_clears_an_orphaned_entry() -> None:
    _pending_trusted_result_by_run["orphaned-run"] = {"outcome": "ok"}
    discard_pending_trusted_result("orphaned-run")
    assert "orphaned-run" not in _pending_trusted_result_by_run


# --- Tier 2 (TEST A/B): full real-ADK end-to-end, exact ordered call graph -


class _RecordingFakeLlm(BaseLlm):
    """One instance per logical stage in the call graph -- NEVER shared
    across stages (the previous version of this test used ONE shared fake
    indexed by call count alone, which could not tell "presentation_team_
    manager's turn" apart from "team_manager's own normal fallback turn";
    that is exactly the gap that let the broken ContextVar bridge go
    undetected). Appends `stage_name` to a SHARED, ordered `call_log` on
    every real invocation, and raises if invoked more than `max_calls`
    times -- proving both the exact ordered graph AND that no stage runs
    twice.
    """

    stage_name: str
    response_parts: Any
    # `Any`, not `list` -- pydantic v2 COPIES a `list`-typed field's value
    # during validation (verified empirically: `instance.call_log is log`
    # is `False` when typed `list`), silently breaking the shared-log
    # assumption every assertion in this file depends on. `Any` bypasses
    # that coercion, preserving the exact object identity passed in.
    call_log: Any
    max_calls: int = 1
    calls: int = 0

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        self.call_log.append(self.stage_name)
        if self.calls > self.max_calls:
            raise AssertionError(f"stage {self.stage_name!r} invoked a real model more than {self.max_calls} time(s)")
        yield LlmResponse(content=types.Content(role="model", parts=self.response_parts()))


def _function_call_parts(name: str, args: dict[str, Any], call_id: str) -> Any:
    def _build() -> list:
        part = types.Part.from_function_call(name=name, args=args)
        part.function_call.id = call_id
        return [part]

    return _build


def _text_parts(text: str) -> Any:
    return lambda: [types.Part.from_text(text=text)]


@pytest.fixture
def direct_unique_graph(monkeypatch: pytest.MonkeyPatch):
    """Wires up all four independent fakes and the gateway mocks, returns
    (probe_agent, call_log, session_service_factory) for TEST A/B to drive.
    """
    from backend.gateway import power_automate_client as pac_module
    from backend.tests._fakes import FakeResponse, chat, message

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("chat-b2-id", "Network Operations Daily")])
        if operation == "teams.getMessages":
            return FakeResponse(200, [message("m1", "Alex", "We shipped the fix.", "2026-08-20T09:00:00Z")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    call_log: list = []

    # --- Stage 1: team_manager routing --------------------------------
    team_manager_fake = _RecordingFakeLlm(
        model="fake-team-manager-routing",
        stage_name="team_manager_routing",
        response_parts=_function_call_parts("incident_manager", {"chat_topic": "Network Operations Daily"}, "call-1"),
        call_log=call_log,
    )

    # --- Stage 2: incident_manager discovery --------------------------
    discovery_fake = _RecordingFakeLlm(
        model="fake-incident-manager-discovery",
        stage_name="incident_manager_discovery",
        response_parts=_function_call_parts("teams_list_chats", {"topic": "Network Operations Daily"}, "call-2"),
        call_log=call_log,
    )
    from backend.agents.team_manager.direct_read_fast_path import _fast_path_incident_manager

    fake_discovery_agent = _fast_path_incident_manager.model_copy(update={"model": discovery_fake})
    fake_discovery_tool = AgentTool(agent=fake_discovery_agent)

    # --- Stage 3: incident_manager synthesis-only ---------------------
    synthesis_fake = _RecordingFakeLlm(
        model="fake-synthesis-only",
        stage_name="incident_manager_synthesis",
        response_parts=_text_parts(
            json.dumps(
                {
                    "outcome": "ok",
                    "chat_id": "chat-b2-id",
                    "chat_title": "Network Operations Daily",
                    "summary": "The fix shipped.",
                    "evidence": [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-20T09:00:00Z"}],
                }
            )
        ),
        call_log=call_log,
    )
    import backend.agents.team_manager.read_continuation_execution as rce_module

    fake_synthesis_agent = rce_module._SYNTHESIS_ONLY_INCIDENT_MANAGER.model_copy(update={"model": synthesis_fake})
    monkeypatch.setattr(rce_module, "_SYNTHESIS_ONLY_INCIDENT_MANAGER", fake_synthesis_agent)

    # --- Stage 4: presentation_team_manager ---------------------------
    presentation_fake = _RecordingFakeLlm(
        model="fake-presentation",
        stage_name="presentation_team_manager",
        response_parts=_text_parts("Network Operations Daily: the fix shipped."),
        call_log=call_log,
    )
    import backend.agents.team_manager.agent as agent_module

    fake_presentation_agent = agent_module.presentation_team_manager.model_copy(update={"model": presentation_fake})
    monkeypatch.setattr(agent_module, "presentation_team_manager", fake_presentation_agent)

    base = get_fast_path_team_manager()
    new_tools = [fake_discovery_tool if getattr(t, "name", None) == "incident_manager" else t for t in base.tools]
    probe_agent = base.model_copy(update={"model": team_manager_fake, "tools": new_tools})

    return probe_agent, call_log


@pytest.mark.asyncio
async def test_direct_unique_exact_ordered_model_call_graph(direct_unique_graph: Any) -> None:
    """TEST A -- exact full model call count, across every real agent/
    Runner in the direct-unique call graph, ordered.
    """
    probe_agent, call_log = direct_unique_graph

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )

    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-graph-test")
    last_text: Optional[str] = None
    try:
        async for event in runner.run_async(
            user_id="u1",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="summarize Network Operations Daily")]),
        ):
            if event.content and event.content.parts:
                texts = [p.text for p in event.content.parts if p.text]
                if texts:
                    last_text = texts[-1]
    finally:
        reset_run_id(token)
        await runner.close()

    assert call_log == [
        "team_manager_routing",
        "incident_manager_discovery",
        "incident_manager_synthesis",
        "presentation_team_manager",
    ]
    assert last_text == "Network Operations Daily: the fix shipped."


@pytest.mark.asyncio
async def test_direct_unique_teams_gateway_call_counts(direct_unique_graph: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sections 20/26 -- exactly one `teams.listChats`, exactly one
    `teams.getMessages`, no repeats.
    """
    from backend.gateway import power_automate_client as pac_module
    from backend.tests._fakes import FakeResponse, chat, message

    gateway_calls: dict[str, int] = {}

    def counting_post(url, json, timeout):
        operation = json.get("operation")
        gateway_calls[operation] = gateway_calls.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("chat-b2-id", "Network Operations Daily")])
        if operation == "teams.getMessages":
            return FakeResponse(200, [message("m1", "Alex", "We shipped the fix.", "2026-08-20T09:00:00Z")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", counting_post)

    probe_agent, _call_log = direct_unique_graph
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-gateway-test")
    try:
        async for _event in runner.run_async(
            user_id="u1", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part.from_text(text="summarize")])
        ):
            pass
    finally:
        reset_run_id(token)
        await runner.close()

    assert gateway_calls.get("teams.listChats", 0) == 1
    assert gateway_calls.get("teams.getMessages", 0) == 1


@pytest.mark.asyncio
async def test_no_hidden_fifth_call_after_presentation(direct_unique_graph: Any) -> None:
    """TEST B -- after `presentation_team_manager` returns, team_manager's
    own normal model must NOT execute again. Enforced structurally by
    `_RecordingFakeLlm`'s own `max_calls=1` (any stage's real model being
    invoked a 2nd time raises `AssertionError` from inside the fake) --
    this test asserts the run completes successfully with exactly 4 total
    real invocations across all stages, confirming no 5th ran.
    """
    probe_agent, call_log = direct_unique_graph
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-fifth-call-test")
    try:
        async for _event in runner.run_async(
            user_id="u1", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part.from_text(text="summarize")])
        ):
            pass
    finally:
        reset_run_id(token)
        await runner.close()

    assert len(call_log) == 4


@pytest.mark.asyncio
async def test_final_durable_event_exactly_once_on_success(direct_unique_graph: Any) -> None:
    """TEST H (success half) -- the real session ends up with exactly one
    user-facing, team_manager-authored final event; `presentation_team_
    manager`'s own nested ephemeral run never pollutes it.
    """
    probe_agent, _call_log = direct_unique_graph
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-durable-event-test")
    try:
        async for _event in runner.run_async(
            user_id="u1", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part.from_text(text="summarize")])
        ):
            pass
    finally:
        reset_run_id(token)
        await runner.close()

    refreshed = await session_service.get_session(app_name="probe-app", user_id="u1", session_id=session.id)
    final_texts = [
        p.text
        for e in refreshed.events
        if e.author == "team_manager" and e.content and e.content.parts
        for p in e.content.parts
        if p.text
    ]
    assert final_texts.count("Network Operations Daily: the fix shipped.") == 1


@pytest.mark.asyncio
async def test_final_durable_event_exactly_once_on_trust_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEST H (failure half) -- a trust-validation failure still produces
    exactly one team_manager-authored final event, carrying the safe
    fail-closed text, never a duplicate/partial event.
    """
    from backend.gateway import power_automate_client as pac_module
    from backend.tests._fakes import FakeResponse, chat

    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.listChats":
            return FakeResponse(200, [chat("chat-b2-id", "Network Operations Daily")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    call_log: list = []
    team_manager_fake = _RecordingFakeLlm(
        model="fake-team-manager-routing",
        stage_name="team_manager_routing",
        response_parts=_function_call_parts("incident_manager", {"chat_topic": "Network Operations Daily"}, "call-1"),
        call_log=call_log,
    )
    discovery_fake = _RecordingFakeLlm(
        model="fake-incident-manager-discovery",
        stage_name="incident_manager_discovery",
        response_parts=_function_call_parts("teams_list_chats", {"topic": "Network Operations Daily"}, "call-2"),
        call_log=call_log,
    )
    from backend.agents.team_manager.direct_read_fast_path import _fast_path_incident_manager

    fake_discovery_agent = _fast_path_incident_manager.model_copy(update={"model": discovery_fake})
    fake_discovery_tool = AgentTool(agent=fake_discovery_agent)

    # `execute_read_continuation` (frozen) returns a genuinely VALID
    # `IncidentManagerResponse` here -- it must clear the AgentTool
    # boundary's own `output_schema` validation normally (an actually
    # malformed `outcome` would fail THERE instead, a different and much
    # less interesting failure mode already covered by TEST E's domain-
    # logic tests above). What is monkeypatched is `build_and_validate_
    # trusted_envelope` itself, forcing THIS callback's own trust check
    # to fail -- simulating a genuine same-run/source/schema trust
    # failure discovered AFTER the result already passed once.
    async def valid_execute_read_continuation(**kwargs: Any) -> dict[str, Any]:
        return {
            "outcome": "ok",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "summary": "The fix shipped.",
        }

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", valid_execute_read_continuation)
    monkeypatch.setattr(fast_path_module, "build_and_validate_trusted_envelope", lambda run_id, result: None)

    base = get_fast_path_team_manager()
    new_tools = [fake_discovery_tool if getattr(t, "name", None) == "incident_manager" else t for t in base.tools]
    probe_agent = base.model_copy(update={"model": team_manager_fake, "tools": new_tools})

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="u1")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-trust-failure-test")
    try:
        async for _event in runner.run_async(
            user_id="u1", session_id=session.id, new_message=types.Content(role="user", parts=[types.Part.from_text(text="summarize")])
        ):
            pass
    finally:
        reset_run_id(token)
        await runner.close()

    # Only 2 real model calls -- routing + discovery -- the shortcut fired
    # and failed closed WITHOUT a 3rd (normal team_manager) or 4th
    # (presentation) real model call.
    assert call_log == ["team_manager_routing", "incident_manager_discovery"]

    refreshed = await session_service.get_session(app_name="probe-app", user_id="u1", session_id=session.id)
    final_texts = [
        p.text
        for e in refreshed.events
        if e.author == "team_manager" and e.content and e.content.parts
        for p in e.content.parts
        if p.text
    ]
    assert final_texts.count(_TRUST_VALIDATION_FAILURE_TEXT) == 1


# --- Tier 5 (TEST I/J): ambiguity / post-selection regression spot-checks --


def test_wiring_confirmation_team_manager_tool_still_uses_fast_path_variant() -> None:
    from backend.agents.team_manager.agent import incident_manager_tool
    from backend.agents.team_manager.direct_read_fast_path import _fast_path_incident_manager

    assert incident_manager_tool.agent is _fast_path_incident_manager


def test_wiring_confirmation_chat_service_uses_fast_path_team_manager() -> None:
    from backend.api.chat_service import _build_runner
    from backend.api.session_service import ApiSessionService

    runner = _build_runner(ApiSessionService())
    assert runner.agent is get_fast_path_team_manager()
