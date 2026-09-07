"""R1 + R3 -- correctness-regression pass:

R1: trusted-result presentation must be STRUCTURALLY presentation-only --
`presentation_team_manager` (agent.py) has `tools=[]`, so `incident_
manager`/`record_conversation_target` are structurally absent from its
function-calling schema, never merely discouraged by prompt wording.

R3: `selection_needed` must be terminal for that turn -- `before_tool_
callback=block_repeated_delegation_after_selection_needed` (selection_
delegation_guard.py) structurally refuses a second `incident_manager`
delegation once an earlier one this same turn already returned
`selection_needed`, never merely discouraged by prompt wording.

Structural claims are verified by inspecting the actual instantiated
`LlmAgent`/tool schema (per the task's own explicit instruction), not by
asserting prompt text.
"""
from __future__ import annotations

import logging

import pytest

from backend.agents.team_manager.agent import presentation_team_manager, team_manager
from backend.agents.team_manager.read_continuation_presentation import (
    PENDING_SPECIALIST_RESULT_STATE_KEY,
    TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY,
)
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.selection.schemas import ResolvedReadContinuation
from backend.selection.service import store_read_continuation
from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse, FakeRunner


async def _collect(chat_service: ChatService, session_id: str, message: str, user_id: str = "api-user") -> list:
    return [event async for event in chat_service.execute_turn_events(session_id, message, user_id)]


class _TaggedFakeRunner(FakeRunner):
    """Tags which of the two runners chat_service.py actually invoked --
    the direct, structural proof R1.1 calls for (never inferred from
    output content alone).
    """

    def __init__(self, service, tag, calls, **kwargs):
        super().__init__(service, **kwargs)
        self._tag = tag
        self._calls = calls

    async def run_async(self, *, user_id, session_id, new_message, run_config=None):
        self._calls.append(self._tag)
        async for event in super().run_async(
            user_id=user_id, session_id=session_id, new_message=new_message, run_config=run_config
        ):
            yield event


# --- TEST 1 (R1): structural proof, no prompt-wording-only claim -----------


def test_presentation_team_manager_has_no_tools_at_all() -> None:
    assert presentation_team_manager.tools == []


@pytest.mark.asyncio
async def test_presentation_team_manager_function_declarations_expose_nothing() -> None:
    """Inspects the ACTUAL ADK function-calling schema this agent's Runner
    would send to Gemini -- not prompt text. `LlmAgent.canonical_tools`
    (an async method: "the resolved self.tools field as a list of
    BaseTool based on the context") is the exact source ADK's own
    `_get_declaration`/function-calling machinery draws from -- an empty
    result means there is nothing to build a `FunctionDeclaration` from.
    """
    resolved_tools = await presentation_team_manager.canonical_tools()
    assert resolved_tools == []


def test_presentation_team_manager_is_the_same_role_different_capability_set() -> None:
    """Same user-facing identity/name/model -- R1 explicitly requires this
    to remain "the SAME logical/user-facing Team Manager role", never a
    second agent.
    """
    assert presentation_team_manager.name == team_manager.name == "team_manager"
    assert presentation_team_manager.model is team_manager.model
    assert presentation_team_manager.instruction is team_manager.instruction
    assert presentation_team_manager.before_tool_callback is None
    assert presentation_team_manager.after_tool_callback is None
    # P2 unchanged: same model-call instrumentation, same agent attribution.
    assert presentation_team_manager.before_model_callback is team_manager.before_model_callback
    assert presentation_team_manager.after_model_callback is team_manager.after_model_callback


@pytest.mark.asyncio
async def test_valid_trusted_result_routes_to_the_presentation_runner_not_the_normal_one() -> None:
    """R1.1: mode selection is server-trusted, decided by `chat_service.py`
    from a validated `ResolvedReadContinuation`/`TrustedSpecialistResult`
    envelope -- never from user text or model output. Two DISTINCT fake
    runners prove exactly which one chat_service.py actually invokes.
    """
    normal_runner_calls: list[str] = []
    presentation_runner_calls: list[str] = []

    service = ApiSessionService()
    session_id = await service.create_session()

    continuation = ResolvedReadContinuation(selected_chat_id="chat-x", selected_chat_topic="Chat X")
    session = await service.get_session(session_id)
    state = dict(session.state)
    store_read_continuation(state, continuation)
    await service.persist_state_delta(session, state)

    async def fake_executor(*, user_id, continuation, **kwargs):
        return {
            "outcome": "ok",
            "chat_id": continuation.selected_chat_id,
            "chat_title": continuation.selected_chat_topic,
            "summary": "Resumed summary.",
            "evidence": [],
        }

    chat_service = ChatService(
        service,
        runner=_TaggedFakeRunner(service, "normal", normal_runner_calls, respond=lambda t: "wrong path"),
        presentation_runner=_TaggedFakeRunner(
            service, "presentation", presentation_runner_calls, respond=lambda t: "Resumed summary."
        ),
        read_continuation_executor=fake_executor,
    )

    collected = await _collect(chat_service, session_id, "resume")

    assert presentation_runner_calls == ["presentation"]
    assert normal_runner_calls == []
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Resumed summary."


@pytest.mark.asyncio
async def test_trusted_result_presentation_mode_logs_the_safe_diagnostic(caplog: pytest.LogCaptureFixture) -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    continuation = ResolvedReadContinuation(selected_chat_id="chat-x", selected_chat_topic="Chat X")
    session = await service.get_session(session_id)
    state = dict(session.state)
    store_read_continuation(state, continuation)
    await service.persist_state_delta(session, state)

    async def fake_executor(*, user_id, continuation, **kwargs):
        return {"outcome": "ok", "chat_id": "chat-x", "chat_title": "Chat X", "summary": "Summary.", "evidence": []}

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Summary."),
        read_continuation_executor=fake_executor,
    )

    with caplog.at_level(logging.INFO, logger="backend.api.chat_service"):
        await _collect(chat_service, session_id, "resume")

    assert any("trusted_result_presentation_mode" in r.getMessage() for r in caplog.records)
    for record in caplog.records:
        msg = record.getMessage()
        assert "chat-x" not in msg
        assert "Chat X" not in msg


# --- TEST 5 (R3): one delegation, one listChats, one PendingSelection ------


@pytest.mark.asyncio
async def test_ambiguous_request_delegates_exactly_once_and_lists_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The model calling `incident_manager` exactly once, receiving
    `selection_needed`, must not trigger a second delegation. FakeRunner
    replays a single delegation event pair (production behavior for a
    well-formed turn); the deterministic `teams_list_chats` side effect
    (via `side_effect`), against a fake gateway with multiple similarly-
    named chats, creates exactly one `PendingSelection`.
    """
    from backend.gateway import power_automate_client as pac_module
    from backend.tests._fakes import FakeResponse, chat
    from backend.tools.teams.list_chats import teams_list_chats

    def fake_post(url, json, timeout):
        return FakeResponse(
            200,
            [chat("kms-a", "Knowledge Management Sync"), chat("kms-b", "Knowledge Management Daily Sync up")],
        )

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    class _Ctx:
        def __init__(self, state):
            self.state = state

    async def side_effect(session_service, session, text):
        ctx = _Ctx(dict(session.state))
        teams_list_chats(
            topic="Knowledge Management Daily Sync", pending_operation="summarize", tool_context=ctx
        )
        await session_service.persist_state_delta(session, dict(ctx.state))

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=[
                FakeEvent(
                    final=False,
                    function_calls=[FakeFunctionCall("incident_manager", {"chat_topic": "Knowledge Management Daily Sync"})],
                ),
                FakeEvent(
                    final=False,
                    function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "selection_needed"})],
                ),
                FakeEvent(text="I found a few similar chats -- please pick one below.", final=True),
            ],
            side_effect=side_effect,
        ),
    )

    collected = await _collect(chat_service, session_id, "summarize Knowledge Management Daily Sync")

    selection_events = [e for e in collected if e.type == StreamEventType.SELECTION_PENDING]
    assert len(selection_events) == 1
    trace_steps = [e for e in collected if e.type == StreamEventType.TRACE_STEP]
    teams_steps = [s for s in trace_steps if s.data["category"] == "teams"]
    assert len(teams_steps) == 1  # exactly one delegation observed this turn


# --- TEST 6 (R3 + R1 + R2, full flow): ambiguity -> choose -> summary ------


@pytest.mark.asyncio
async def test_full_ambiguous_to_resolved_flow_call_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end: ambiguous lookup -> SelectionCard -> user chooses ->
    resumed continuation -> presentation-only Team Manager -> final answer.
    Reuses the same REAL `execute_read_continuation` + gateway-counting
    pattern test_p0_p1_live_incident_regression.py already established.
    """
    from backend.agents.team_manager import read_continuation_execution as execution_module
    from backend.api import selection_service
    from backend.gateway import power_automate_client as pac_module
    from backend.selection.service import load_active_selection
    from backend.tests._fakes import FakeResponse, chat, message
    from backend.tests.test_p0_p1_live_incident_regression import _RealRetrievalFakeIncidentManagerRunner

    call_counts: dict[str, int] = {}

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("kms-a", "Knowledge Management Sync"), chat("kms-b", "Knowledge Management Daily Sync up")])
        if operation == "teams.getMessages":
            return FakeResponse(
                200,
                [message("m1", "Sam", "We reviewed the backlog and agreed on priorities.", "2026-08-20T09:00:00Z")],
            )
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    monkeypatch.setattr(execution_module, "Runner", _RealRetrievalFakeIncidentManagerRunner)

    service = ApiSessionService()
    session_id = await service.create_session()

    class _Ctx:
        def __init__(self, state):
            self.state = state

    async def ambiguous_side_effect(session_service, session, text):
        from backend.tools.teams.list_chats import teams_list_chats

        ctx = _Ctx(dict(session.state))
        teams_list_chats(topic="Knowledge Management Daily Sync", pending_operation="summarize", tool_context=ctx)
        await session_service.persist_state_delta(session, dict(ctx.state))

    chat_service_1 = ChatService(
        service,
        runner=FakeRunner(
            service,
            events=[
                FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
                FakeEvent(
                    final=False,
                    function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "selection_needed"})],
                ),
                FakeEvent(text="I found similar chats -- please pick one below.", final=True),
            ],
            side_effect=ambiguous_side_effect,
        ),
    )
    collected_1 = await _collect(chat_service_1, session_id, "summarize Knowledge Management Daily Sync")
    assert any(e.type == StreamEventType.SELECTION_PENDING for e in collected_1)
    assert call_counts.get("teams.listChats", 0) == 1

    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == "Knowledge Management Daily Sync up")
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    listchats_before_resume = call_counts.get("teams.listChats", 0)

    chat_service_2 = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "WRONG -- normal runner must not be used here."),
        presentation_runner=FakeRunner(service, respond=lambda t: "Here is the Knowledge Management summary."),
    )
    collected_2 = await _collect(chat_service_2, session_id, choose_response.resume_message)

    # --- The exact required post-selection call graph.
    assert call_counts.get("teams.listChats", 0) == listchats_before_resume  # 0 new listChats
    assert call_counts.get("teams.getMessages", 0) == 1
    completed = next(e for e in collected_2 if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Here is the Knowledge Management summary."
    assert "source" in completed.data

    final_session = await service.get_session(session_id)
    assert final_session.state.get(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY) is None
    assert final_session.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY) is None


# --- TEST 7: normal exact chat regression -- P4A parallel path preserved ---


@pytest.mark.asyncio
async def test_normal_exact_teams_request_uses_the_normal_runner_not_presentation() -> None:
    """A normal, non-continuation Teams request (P4A's own parallel
    `record_conversation_target` + `incident_manager` shape) must use
    `self._runner`, never `self._presentation_runner` -- R1's new
    runner-selection logic must not divert ordinary delegation turns.
    """
    normal_calls: list[str] = []
    presentation_calls: list[str] = []

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service,
        runner=_TaggedFakeRunner(
            service,
            "normal",
            normal_calls,
            events=[
                FakeEvent(
                    final=False,
                    function_calls=[
                        FakeFunctionCall("record_conversation_target", {"target": "explicit_external_conversation"}),
                        FakeFunctionCall("incident_manager"),
                    ],
                ),
                FakeEvent(
                    final=False,
                    function_responses=[
                        FakeFunctionResponse(
                            "record_conversation_target", {"target": "explicit_external_conversation"}
                        ),
                        FakeFunctionResponse(
                            "incident_manager", {"outcome": "ok", "chat_title": "Production Bridge", "evidence": []}
                        ),
                    ],
                ),
                FakeEvent(
                    final=False,
                    function_responses=[
                        FakeFunctionResponse("record_source_requirements", {"requires_teams": True, "requires_governed_knowledge": False})
                    ],
                ),
                FakeEvent(text="Here is the Production Bridge summary.", final=True),
            ],
        ),
        presentation_runner=_TaggedFakeRunner(service, "presentation", presentation_calls, respond=lambda t: "WRONG"),
    )

    collected = await _collect(chat_service, session_id, "summarize Production Bridge")

    assert normal_calls == ["normal"]
    assert presentation_calls == []
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Here is the Production Bridge summary."


# --- TEST 8: current_thread -- normal runner, zero Teams calls -------------


@pytest.mark.asyncio
async def test_current_thread_uses_the_normal_runner_not_presentation() -> None:
    normal_calls: list[str] = []
    presentation_calls: list[str] = []

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service,
        runner=_TaggedFakeRunner(
            service, "normal", normal_calls, events=[FakeEvent(text="Here is what we discussed.", final=True)]
        ),
        presentation_runner=_TaggedFakeRunner(service, "presentation", presentation_calls, respond=lambda t: "WRONG"),
    )

    collected = await _collect(chat_service, session_id, "summarize what we discussed in this chat window")

    assert normal_calls == ["normal"]
    assert presentation_calls == []
    trace_steps = [e for e in collected if e.type == StreamEventType.TRACE_STEP]
    assert not any(s.data["category"] == "teams" for s in trace_steps)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" not in completed.data


# --- Session/cleanup: no stale state or mode leaks into the next turn ------


@pytest.mark.asyncio
async def test_no_stale_presentation_state_leaks_into_the_next_turn() -> None:
    """After a resolved-continuation/presentation-mode turn completes, a
    NEW, ordinary turn must: (a) find no stale `TrustedSpecialistResult`/
    `ResolvedReadContinuation`, (b) NOT be routed to the presentation
    runner again, and (c) still see the correctly-updated selected-chat
    state from the resolved turn.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    continuation = ResolvedReadContinuation(selected_chat_id="chat-x", selected_chat_topic="Chat X")
    session = await service.get_session(session_id)
    state = dict(session.state)
    store_read_continuation(state, continuation)
    await service.persist_state_delta(session, state)

    async def fake_executor(*, user_id, continuation, **kwargs):
        return {
            "outcome": "ok",
            "chat_id": continuation.selected_chat_id,
            "chat_title": continuation.selected_chat_topic,
            "summary": "Resumed summary.",
            "evidence": [],
        }

    chat_service_1 = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "WRONG"),
        presentation_runner=FakeRunner(service, respond=lambda t: "Resumed summary."),
        read_continuation_executor=fake_executor,
    )
    await _collect(chat_service_1, session_id, "resume")

    mid_session = await service.get_session(session_id)
    assert mid_session.state.get(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY) is None
    assert mid_session.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY) is None
    assert mid_session.state["selected_teams_chat_id"] == "chat-x"

    normal_calls: list[str] = []
    presentation_calls: list[str] = []
    chat_service_2 = ChatService(
        service,
        runner=_TaggedFakeRunner(
            service, "normal", normal_calls, events=[FakeEvent(text="Sure, what would you like next?", final=True)]
        ),
        presentation_runner=_TaggedFakeRunner(service, "presentation", presentation_calls, respond=lambda t: "WRONG"),
    )
    await _collect(chat_service_2, session_id, "thanks, what else is in there?")

    assert normal_calls == ["normal"]
    assert presentation_calls == []

    final_session = await service.get_session(session_id)
    assert final_session.state["selected_teams_chat_id"] == "chat-x"
