"""Production-hardening pass regression tests: the deterministic
`ResolvedReadContinuation` mechanism that replaces natural-language resume
text as the control path for a resumed SelectionCard read.

PASS #2 (this file, updated): execution of a `ResolvedReadContinuation`'s
Incident Manager/read path is now UNCONDITIONAL -- `chat_service.py` runs
it directly, via the injectable `read_continuation_executor`, BEFORE
team_manager's own turn, regardless of whether team_manager's model would
have chosen to delegate. `read_continuation_executor` is injected here the
same way `runner`/`teams_contributors_resolver` already are -- the fake
below still performs the REAL `teams_get_messages` (directly, with the
continuation's own authoritative `selected_chat_id` -- no `teams_
list_chats` call at all, exactly matching `read_continuation_execution.py`
-- Incident Manager/read execution and `evidence.validate_evidence`, so
these tests still exercise the real deterministic chain end to end, only
the specialist's OWN model reasoning is faked (there is no live model in
this offline suite -- same limitation this codebase's every other
prompt-dependent test already documents).

Pass #1's `enforce_read_continuation` (team_manager's `before_tool_
callback`) is unit-tested independently in
test_read_continuation_enforcement.py -- it remains a defense-in-depth
backstop for the rare case team_manager's own model redundantly attempts
to delegate anyway, not exercised again here.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.agents.incident_manager.evidence import validate_evidence
from backend.agents.team_manager.read_continuation_presentation import PENDING_SPECIALIST_RESULT_STATE_KEY
from backend.api import selection_service
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.gateway import power_automate_client as pac_module
from backend.selection.schemas import PendingReadIntent, ReadOperation
from backend.selection.service import load_active_selection, pop_read_continuation
from backend.tests._api_fakes import FakeRunner
from backend.tests._fakes import FakeResponse, chat, message
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY, teams_get_messages
from backend.tools.teams.list_chats import teams_list_chats


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _counting_gateway(list_chats_payload: list, get_messages_by_chat_id: dict, call_counts: dict):
    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, list_chats_payload)
        if operation == "teams.getMessages":
            return FakeResponse(200, get_messages_by_chat_id.get(json.get("chatId"), []))
        if operation == "teams.getMembers":
            return FakeResponse(200, [])
        raise AssertionError(f"unexpected operation: {operation}")

    return fake_post


def _real_read_continuation_executor(captured: dict[str, Any], summary: str = "resumed summary"):
    """Fake `read_continuation_executor` (production hardening pass #2's
    injectable-callable, matching `execute_read_continuation`'s own `(*,
    user_id, continuation)` signature) that still performs the REAL
    `teams_get_messages`/`validate_evidence` work, using `continuation`'s
    OWN authoritative `selected_chat_id` DIRECTLY -- never a `teams_list_
    chats` lookup of any kind, exactly matching what the real deterministic
    executor now does. Only `incident_manager`'s own model reasoning
    (classification/summarization) is replaced by the fixed `summary`
    text, since no live model is available in this offline suite.
    """

    async def executor(*, user_id: str, continuation: Any, **kwargs: Any) -> dict[str, Any]:
        captured["chat_topic_used"] = continuation.selected_chat_topic
        captured["question_used"] = continuation.question
        captured["time_range_used"] = continuation.requested_time_range
        captured["resolved_chat_id_seen"] = continuation.selected_chat_id

        inner_ctx = _Ctx({})
        retrieval = teams_get_messages(chat_id=continuation.selected_chat_id, tool_context=inner_ctx)
        captured["retrieval"] = retrieval
        captured["matched_chat_id"] = continuation.selected_chat_id

        candidate_evidence = [
            {"message_id": m["id"], "author": m["author"], "sent_at": m["sent_at"]} for m in retrieval["messages"]
        ]
        known_ids = set(inner_ctx.state.get(KNOWN_MESSAGE_IDS_STATE_KEY, []))
        validated = validate_evidence(candidate_evidence, known_ids)
        captured["validated_evidence"] = validated

        return {
            "outcome": "ok",
            "chat_id": continuation.selected_chat_id,
            "chat_title": continuation.selected_chat_topic,
            "summary": summary,
            "evidence": validated,
        }

    return executor


@pytest.mark.asyncio
async def test_exact_live_scenario_zero_listchats_after_choose_and_correct_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 15's exact live regression, hardened: ambiguous "Summarize
    SLOPANOC Gateway Group" -> SelectionCard -> select "SLOPANOC Gateway
    Group Test" -> the resumed execution makes ZERO `teams.listChats`
    gateway calls, uses the authoritative chat_id directly, and the
    continuation is consumed exactly once.
    """
    messages_payload = [
        message("m1", "Alex", "We should migrate the gateway this sprint.", "2026-08-20T09:00:00Z"),
        message("m2", "Priya", "Agreed, I will draft the plan.", "2026-08-20T09:05:00Z"),
    ]
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway(
            [chat("chat-real-1", "SLOPANOC Gateway Group Test"), chat("chat-real-2", "SLOPANOC Gateway Ops")],
            {"chat-real-1": messages_payload},
            call_counts,
        ),
    )

    service = ApiSessionService()
    session_id = await service.create_session()

    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    ambiguity_result = teams_list_chats(topic="SLOPANOC Gateway Group", pending_operation="summarize", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))
    assert ambiguity_result["selection_pending"] is True
    assert call_counts.get("teams.listChats") == 1  # the one legitimate discovery call

    pending = load_active_selection((await service.get_session(session_id)).state)
    original_read_intent = pending.pending_read_intent
    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")

    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)
    assert choose_response.pending_action is None

    after_choose = await service.get_session(session_id)
    assert after_choose.state["selected_teams_chat_id"] == "chat-real-1"
    assert after_choose.state["selected_teams_chat_topic"] == "SLOPANOC Gateway Group Test"

    # HARD acceptance criterion (item 18): the continuation exists,
    # server-side, keyed off the AUTHORITATIVE option_targets mapping --
    # never from a re-resolution against the option's label text.
    stored_continuation = pop_read_continuation(dict(after_choose.state))
    assert stored_continuation is not None
    assert stored_continuation.conversation_target == "selected_external_conversation"
    assert stored_continuation.operation == ReadOperation.SUMMARIZE
    assert stored_continuation.selected_chat_id == "chat-real-1"
    assert stored_continuation.selected_chat_topic == "SLOPANOC Gateway Group Test"
    assert stored_continuation.question is None
    assert stored_continuation.requested_time_range is None

    # PendingReadIntent preserved EXACTLY -- selection only ever resolved
    # the destination.
    resolved_selection = load_active_selection(after_choose.state)
    assert resolved_selection.pending_read_intent == original_read_intent

    captured: dict[str, Any] = {}
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "The team discussed migrating the gateway this sprint."),
        read_continuation_executor=_real_read_continuation_executor(
            captured, summary="The team discussed migrating the gateway this sprint."
        ),
    )
    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    # --- The hard acceptance criterion itself: NO further listChats calls.
    assert call_counts.get("teams.listChats") == 1  # unchanged since before the resumed turn

    # --- The authoritative chat_id was used directly, args deterministically overridden.
    assert captured["resolved_chat_id_seen"] == "chat-real-1"
    assert captured["chat_topic_used"] == "SLOPANOC Gateway Group Test"
    assert captured["matched_chat_id"] == "chat-real-1"
    assert captured["retrieval"]["retrieved_count"] == 2
    assert len(captured["validated_evidence"]) == 2

    # --- No second ambiguity/destination decision surfaced to the user.
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)

    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "The team discussed migrating the gateway this sprint."
    assert "source" in completed.data

    # --- Single-use: the continuation is gone after this turn.
    final_session = await service.get_session(session_id, "api-user")
    assert pop_read_continuation(dict(final_session.state)) is None


@pytest.mark.asyncio
async def test_previously_selected_chat_never_queried_and_continuation_targets_the_new_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 16: Chat A already selected; a new ambiguous "Chat B"
    resolves to candidate B2 -- the continuation targets B2, Chat A is
    never queried, and no `teams.listChats` call happens in the resumed
    execution.
    """
    chat_a_messages = [message("a1", "Someone", "Chat A content -- must never be retrieved here.", "2026-08-01T09:00:00Z")]
    chat_b2_messages = [
        message("b1", "Alex", "Chat B2 discussion.", "2026-08-20T09:00:00Z"),
        message("b2", "Priya", "Chat B2 follow-up.", "2026-08-20T09:05:00Z"),
    ]
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway(
            [chat("chat-a", "Chat A"), chat("chat-b2-id", "Chat B2"), chat("chat-b3-id", "Chat B3")],
            {"chat-a": chat_a_messages, "chat-b2-id": chat_b2_messages},
            call_counts,
        ),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await service.persist_state_delta(
        session, {"selected_teams_chat_id": "chat-a", "selected_teams_chat_topic": "Chat A"}
    )

    refreshed = await service.get_session(session_id)
    ctx = _Ctx(refreshed.state)
    result = teams_list_chats(topic="Chat B", pending_operation="get_messages", tool_context=ctx)
    await service.persist_state_delta(refreshed, dict(ctx.state))
    assert result["selection_pending"] is True

    listchats_calls_before_choose = call_counts.get("teams.listChats", 0)
    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == "Chat B2")
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    after_choose = await service.get_session(session_id)
    stored_continuation = pop_read_continuation(dict(after_choose.state))
    assert stored_continuation.selected_chat_id == "chat-b2-id"
    assert stored_continuation.selected_chat_topic == "Chat B2"
    assert stored_continuation.operation == ReadOperation.GET_MESSAGES

    captured: dict[str, Any] = {}
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Here are the latest messages from Chat B2."),
        read_continuation_executor=_real_read_continuation_executor(
            captured, summary="Here are the latest messages from Chat B2."
        ),
    )
    async for _ in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user"):
        pass

    assert call_counts.get("teams.listChats", 0) == listchats_calls_before_choose  # no new listChats call
    assert captured["matched_chat_id"] == "chat-b2-id"
    assert all("Chat A content" not in m["text"] for m in captured["retrieval"]["messages"])
    assert {m["id"] for m in captured["retrieval"]["messages"]} == {"b1", "b2"}


@pytest.mark.asyncio
async def test_focus_and_time_range_flow_through_the_continuation_unreconstructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 17: "Summarize Chat B focusing on decisions from the last 7
    days" -> ambiguity -> choose B2 -> the continuation carries the exact
    operation/question/time-range captured before the ambiguity existed,
    and `enforce_read_continuation` forwards them verbatim -- nothing is
    reconstructed from the resume text.
    """
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway(
            [chat("chat-b2-id", "Chat B2")],
            {"chat-b2-id": [message("b1", "Alex", "We decided X.", "2026-08-20T09:00:00Z")]},
            call_counts,
        ),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(
        topic="Chat B",
        pending_question="decisions from the last 7 days",
        pending_time_range="the last 7 days",
        pending_operation="summarize",
        tool_context=ctx,
    )
    await service.persist_state_delta(session, dict(ctx.state))

    pending = load_active_selection((await service.get_session(session_id)).state)
    assert pending.pending_read_intent == PendingReadIntent(
        operation=ReadOperation.SUMMARIZE,
        question="decisions from the last 7 days",
        requested_time_range="the last 7 days",
    )
    chosen = next(o for o in pending.options if o.label == "Chat B2")
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    after_choose = await service.get_session(session_id)
    stored_continuation = pop_read_continuation(dict(after_choose.state))
    assert stored_continuation.selected_chat_id == "chat-b2-id"
    assert stored_continuation.operation == ReadOperation.SUMMARIZE
    assert stored_continuation.question == "decisions from the last 7 days"
    assert stored_continuation.requested_time_range == "the last 7 days"

    captured: dict[str, Any] = {}
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "We decided X."),
        read_continuation_executor=_real_read_continuation_executor(captured, summary="We decided X."),
    )
    async for _ in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user"):
        pass

    assert captured["question_used"] == "decisions from the last 7 days"
    assert captured["time_range_used"] == "the last 7 days"
    assert captured["matched_chat_id"] == "chat-b2-id"


@pytest.mark.asyncio
async def test_failed_or_cancelled_continuation_execution_never_leaks_into_a_later_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement (sections 10/11/18): if deterministic specialist
    execution fails (the same externally-observable shape a genuine
    mid-execution cancellation has -- the run does not complete) while a
    `ResolvedReadContinuation` exists, the turn must fail safely (never a
    hallucinated summary), and the continuation must never remain
    available to run again on a later, unrelated turn.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], {}, {}),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(topic="SLOPANOC Gateway Group", pending_operation="summarize", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))

    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    # Turn A: the continuation is popped/consumed (persisted) by chat_
    # service.py, but its deterministic execution then fails -- simulates
    # a genuine specialist/gateway failure, or a cancellation that
    # interrupts execution before it can complete.
    async def failing_executor(*, user_id, continuation, **kwargs):
        raise RuntimeError("simulated specialist failure")

    chat_service_a = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "a hallucinated summary that must never appear"),
        read_continuation_executor=failing_executor,
    )
    collected_a = [
        event
        async for event in chat_service_a.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    # A safe, honest failure -- never team_manager's own turn running with
    # stale/no data, and never the hallucinated summary above.
    assert any(e.type == StreamEventType.ERROR for e in collected_a)
    assert not any(e.type == StreamEventType.MESSAGE_COMPLETED for e in collected_a)

    # The continuation is gone from persisted state either way (single-use,
    # consumed at turn start regardless of what execution then did).
    after_turn_a = await service.get_session(session_id, "api-user")
    assert pop_read_continuation(dict(after_turn_a.state)) is None

    # Turn B: a completely unrelated later request -- the injected
    # executor must NEVER be invoked again; nothing was left pending.
    executor_calls: list = []

    async def must_not_be_called(*, user_id, continuation, **kwargs):
        executor_calls.append(continuation)
        return None

    chat_service_b = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "an ordinary unrelated reply"),
        read_continuation_executor=must_not_be_called,
    )
    collected_b = [
        event
        async for event in chat_service_b.execute_turn_events(session_id, "tell me about Some Other Chat", "api-user")
    ]

    assert executor_calls == []  # never invoked -- no continuation was pending for this turn
    completed_b = next(e for e in collected_b if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed_b.data["content"] == "an ordinary unrelated reply"


@pytest.mark.asyncio
async def test_team_manager_refusing_to_delegate_does_not_prevent_specialist_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE most important acceptance test (section 14): once a
    `ResolvedReadContinuation` exists, execution must be unconditional --
    simulates team_manager's own model choosing NOT to call
    `incident_manager` at all this turn (a plain text reply, no
    `incident_manager` function call/response anywhere in its stream) and
    proves the specialist/read path still runs to completion regardless:
    `teams_get_messages` is called exactly once with the authoritative
    `selected_chat_id`, `teams_list_chats` is never called, evidence is
    validated, a SourceReference is produced, team_manager still receives
    the specialist's result through trusted, structured session state
    (`PENDING_SPECIALIST_RESULT_STATE_KEY` -- never a text marker in the
    message itself, and the message text team_manager receives is
    unchanged from the plain resume text), and the continuation is
    consumed exactly once.
    """
    get_messages_calls: list[str] = []
    call_counts: dict[str, int] = {}

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("chat-real-1", "SLOPANOC Gateway Group Test")])
        if operation == "teams.getMessages":
            get_messages_calls.append(json.get("chatId"))
            return FakeResponse(
                200, [message("m1", "Alex", "We should migrate the gateway this sprint.", "2026-08-20T09:00:00Z")]
            )
        if operation == "teams.getMembers":
            return FakeResponse(200, [])
        raise AssertionError(f"unexpected operation: {operation}")

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(topic="SLOPANOC Gateway Group", pending_operation="summarize", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))
    assert call_counts.get("teams.listChats") == 1  # the one legitimate discovery call

    pending = load_active_selection((await service.get_session(session_id)).state)
    original_read_intent = pending.pending_read_intent
    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    captured: dict[str, Any] = {}
    team_manager_messages: list[str] = []
    state_seen_by_team_manager: list[Any] = []

    async def refusing_side_effect(session_service, session, text):
        # team_manager's own model, this simulates, WOULD NOT voluntarily
        # call `incident_manager` -- it never does anything with `text`/
        # `session` beyond what a normal plain-text reply would; this just
        # records what team_manager's turn actually received (the message
        # text, AND -- via `session`, freshly re-fetched exactly the way a
        # real Runner would -- the trusted structured state it was handed)
        # to prove neither ever carries a text marker.
        team_manager_messages.append(text)
        state_seen_by_team_manager.append(session.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY))

    chat_service = ChatService(
        service,
        runner=FakeRunner(
            service, side_effect=refusing_side_effect, respond=lambda t: "The team discussed migrating the gateway this sprint."
        ),
        read_continuation_executor=_real_read_continuation_executor(
            captured, summary="The team discussed migrating the gateway this sprint."
        ),
    )
    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    # --- The specialist/read path executed despite team_manager's model
    # --- never calling `incident_manager` itself.
    assert get_messages_calls == ["chat-real-1"]  # getMessages called exactly once, authoritative chat_id
    assert call_counts.get("teams.listChats") == 1  # unchanged -- no listChats call in the resumed execution
    assert len(captured["validated_evidence"]) == 1  # evidence validated

    # --- team_manager's own new_message text is UNCHANGED (never a
    # --- marker) -- the trusted result instead arrived via session state.
    assert team_manager_messages == [choose_response.resume_message]
    # Production hardening pass #4: what team_manager's turn actually
    # sees now goes through `TrustedSpecialistResult`'s own schema
    # round-trip (run-id validated first) -- the closed set of
    # `IncidentManagerResponse` fields is always fully present (e.g.
    # `decisions`/`actions`/... default to `[]`, never simply absent),
    # even though the fake executor's own canned dict didn't set them.
    assert len(state_seen_by_team_manager) == 1
    seen = state_seen_by_team_manager[0]
    assert seen["outcome"] == "ok"
    assert seen["chat_id"] == "chat-real-1"
    assert seen["chat_title"] == "SLOPANOC Gateway Group Test"
    assert seen["summary"] == "The team discussed migrating the gateway this sprint."
    assert seen["evidence"] == captured["validated_evidence"]

    # --- Final user-facing response produced, with a real SourceReference.
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "The team discussed migrating the gateway this sprint."
    assert "source" in completed.data
    assert completed.data["source"]["message_count"] == 1

    # --- PendingReadIntent preserved exactly; continuation consumed once.
    resolved_selection = load_active_selection((await service.get_session(session_id)).state)
    assert resolved_selection.pending_read_intent == original_read_intent
    final_session = await service.get_session(session_id, "api-user")
    assert pop_read_continuation(dict(final_session.state)) is None


@pytest.mark.asyncio
async def test_exact_match_normal_flow_never_creates_a_continuation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 20: an exact-match Teams read (no ambiguity, no
    SelectionCard) must never involve `choose()` and must never produce a
    `ResolvedReadContinuation` -- the continuation is only ever created
    inside `selection_service.choose()`.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway([chat("chat-kmds", "Knowledge Management Daily Sync up")], {}, {}),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    result = teams_list_chats(topic="Knowledge Management Daily Sync up", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))

    assert result["match"] == "matched"
    after = await service.get_session(session_id)
    assert pop_read_continuation(dict(after.state)) is None


@pytest.mark.asyncio
async def test_duplicate_choose_after_first_choose_is_denied_and_does_not_disturb_the_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Security/robustness: a second `choose()` call against an
    already-resolved selection is denied by the existing `resolve_selection`
    security check (unchanged) -- it must never reach the
    continuation-creation code a second time, and the original
    continuation must remain exactly as the first, successful `choose()`
    left it.
    """
    from backend.gateway.safe_error import SafeErrorException

    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _counting_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], {}, {}),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(topic="SLOPANOC Gateway Group", pending_operation="summarize", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))

    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")
    await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    with pytest.raises(SafeErrorException):
        await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    after = await service.get_session(session_id)
    stored_continuation = pop_read_continuation(dict(after.state))
    assert stored_continuation is not None
    assert stored_continuation.selected_chat_id == "chat-real-1"


@pytest.mark.asyncio
async def test_normal_turn_with_no_continuation_never_touches_the_read_continuation_executor() -> None:
    """Section 15: a normal user turn (no `ResolvedReadContinuation`
    pending at all) must be entirely unaffected by this hardening pass --
    the injected `read_continuation_executor` must never be called, and
    team_manager's own turn receives the ORIGINAL message text verbatim
    (never a `[INCIDENT_MANAGER_RESULT]` marker), exactly as before this
    pass existed. ConversationTarget/current_thread/exact-match Teams
    read semantics for a normal turn are unaffected by construction (this
    pass adds code only inside the `if pending_read_continuation is not
    None:` branch) and remain covered by their own, unchanged existing
    test suites (test_conversation_target_*.py, test_read_resume_flow.py's
    exact-match cases).
    """
    service = ApiSessionService()
    session_id = await service.create_session()

    executor_calls: list = []

    async def must_not_be_called(*, user_id, continuation, **kwargs):
        executor_calls.append(continuation)
        return None

    received_messages: list[str] = []

    def respond(text: str) -> str:
        received_messages.append(text)
        return "Hello! How can I help?"

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=respond),
        read_continuation_executor=must_not_be_called,
    )
    collected = [
        event async for event in chat_service.execute_turn_events(session_id, "hello there", "api-user")
    ]

    assert executor_calls == []
    assert received_messages == ["hello there"]  # the model's own new_message, unchanged
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Hello! How can I help?"
    assert "source" not in completed.data


def test_no_natural_language_intent_routing_in_the_new_execution_modules() -> None:
    """Section 19: the deterministic-execution modules this pass adds
    must contain no regex classifier, keyword routing, `message contains`
    check, or phrase dictionary -- correctness comes entirely from
    `ResolvedReadContinuation`'s own structured fields.
    """
    import inspect

    import backend.agents.team_manager.read_continuation_execution as execution_module
    import backend.agents.team_manager.read_continuation_presentation as presentation_module

    # `.startswith(` is deliberately NOT in this list -- both modules use
    # it only for a state-KEY prefix filter (`_adk`/`temp:`, mirroring
    # `AgentTool.run_async`'s own established forwarding filter), never to
    # inspect user-authored message text.
    forbidden = [".lower(", "re.search(", "re.match(", "re.compile(", "if 'summar", "message.contains", "in message"]
    for module in (execution_module, presentation_module):
        source = inspect.getsource(module)
        for token in forbidden:
            assert token not in source, f"unexpected NL-routing token in {module.__name__}: {token!r}"
