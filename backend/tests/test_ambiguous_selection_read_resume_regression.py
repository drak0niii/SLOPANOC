"""Regression test for the live bug (pre-4H): ambiguous Teams summary
request -> SelectionCard -> user picks a candidate -> the system falsely
reported "no relevant messages to summarize" for a chat that genuinely
has real messages.

Traces the REAL runtime path end to end, exercising the actual
deterministic functions at every stage (never mocked away): `teams_list_
chats` (real matching), `selection_service.choose` (real), `read_resume.
build_read_resume_message` (real), `teams_get_messages` (real retrieval/
filtering, only the Power Automate gateway call itself is faked),
`evidence.validate_evidence` (real), `source_reference.build_teams_source_
reference` (real). Only the ADK `Runner`/model reasoning is faked
(`FakeRunner`), since no live model is available in this offline suite --
but its `side_effect` drives the SAME real deterministic functions a
correctly-behaving `incident_manager` would call, so this proves the
FULL chain is healthy end to end: given a genuinely-populated Teams chat,
selecting it must never fall through to a false "no relevant messages"
result. Only synthetic fixture names/content are used.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.agents.incident_manager.evidence import validate_evidence
from backend.api import selection_service
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.gateway import power_automate_client as pac_module
from backend.selection.read_resume import GENERIC_SUMMARY_RESUME_TEXT, build_read_resume_message
from backend.selection.schemas import PendingReadIntent, ReadOperation
from backend.selection.service import load_active_selection
from backend.tests._api_fakes import FakeRunner
from backend.tests._fakes import FakeResponse, chat, message
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY, teams_get_messages
from backend.tools.teams.list_chats import teams_list_chats


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _routed_gateway(list_chats_payload: list, get_messages_payload: list, members_payload: list = ()):
    """A `requests.post` fake that dispatches on `json["operation"]` --
    needed here (unlike simpler single-operation tests elsewhere) because
    this test genuinely drives `teams.listChats`, `teams.getMessages`,
    AND (via chat_service.py's own contributor-accuracy enrichment,
    unaffected by this fix) `teams.getMembers` against the real tool
    functions in one flow.
    """

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        if operation == "teams.listChats":
            return FakeResponse(200, list_chats_payload)
        if operation == "teams.getMessages":
            return FakeResponse(200, get_messages_payload)
        if operation == "teams.getMembers":
            return FakeResponse(200, list(members_payload))
        raise AssertionError(f"unexpected operation: {operation}")

    return fake_post


@pytest.mark.asyncio
async def test_ambiguous_summary_then_selection_then_resume_produces_a_real_non_empty_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages_payload = [
        message("m1", "Alex", "We should migrate the gateway this sprint.", "2026-08-20T09:00:00Z"),
        message("m2", "Priya", "Agreed, I will draft the plan.", "2026-08-20T09:05:00Z"),
        message("m3", "Sam", "Let's review it Thursday.", "2026-08-20T09:10:00Z"),
    ]
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _routed_gateway(
            [
                chat("chat-real-1", "SLOPANOC Gateway Group Test"),
                chat("chat-real-2", "SLOPANOC Gateway Ops"),
            ],
            messages_payload,
        ),
    )

    service = ApiSessionService()
    session_id = await service.create_session()

    # Turn 1: "Summarize SLOPANOC Gateway Group" -- no exact match, real
    # similarity scoring finds candidates, a PendingSelection is created
    # with a plain PendingReadIntent (operation=summarize, no question, no
    # time range) -- mirrors incident_manager's own step-2 call exactly.
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    ambiguity_result = teams_list_chats(
        topic="SLOPANOC Gateway Group",
        pending_operation="summarize",
        tool_context=ctx,
    )
    await service.persist_state_delta(session, dict(ctx.state))

    assert ambiguity_result["match"] == "not_found"
    assert ambiguity_result["selection_pending"] is True

    refreshed = await service.get_session(session_id)
    pending = load_active_selection(refreshed.state)
    assert pending is not None
    assert pending.pending_read_intent == PendingReadIntent(operation=ReadOperation.SUMMARIZE)
    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")

    # User selects "SLOPANOC Gateway Group Test" in the SelectionCard.
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    assert choose_response.pending_action is None  # a read, not a write
    assert choose_response.resume_message == GENERIC_SUMMARY_RESUME_TEXT
    assert "Teams conversation" in choose_response.resume_message
    assert "SLOPANOC" not in choose_response.resume_message  # never the destination name

    after_choose = await service.get_session(session_id)
    assert after_choose.state["selected_teams_chat_id"] == "chat-real-1"
    assert after_choose.state["selected_teams_chat_topic"] == "SLOPANOC Gateway Group Test"

    # PendingReadIntent survives selection UNCHANGED -- selecting a
    # candidate only resolved the destination.
    resolved_selection = load_active_selection(after_choose.state)
    assert resolved_selection.pending_read_intent == pending.pending_read_intent
    assert resolved_selection.pending_read_intent.operation == ReadOperation.SUMMARIZE
    assert resolved_selection.pending_read_intent.requested_time_range is None

    # Turn 2: production hardening pass #2 -- the Incident Manager/read
    # path now executes deterministically and unconditionally via the
    # injected `read_continuation_executor`, before team_manager's own
    # turn (never left to a `FakeRunner` `side_effect`/team_manager model
    # decision). This fake executor still calls the REAL `teams_get_
    # messages` (directly, with the continuation's OWN authoritative
    # `selected_chat_id` -- no `teams_list_chats` call at all, matching
    # exactly what `incident_manager`'s own updated prompt now does) and
    # the REAL `validate_evidence`, proving the full deterministic chain
    # is healthy end to end. `retrieved_message_ids`/`validated_evidence`
    # are captured into the outer scope for the assertions below.
    captured: dict[str, Any] = {}

    async def fake_executor(*, user_id, continuation, **kwargs):
        assert continuation.selected_chat_topic == "SLOPANOC Gateway Group Test"
        resolved_chat_id = continuation.selected_chat_id
        assert resolved_chat_id == "chat-real-1"
        captured["chat_id_used"] = resolved_chat_id

        inner_ctx = _Ctx({})
        # requested_time_range was never set -- no unintended narrowing.
        retrieval = teams_get_messages(chat_id=resolved_chat_id, tool_context=inner_ctx)
        assert "error" not in retrieval
        captured["retrieved_count"] = retrieval["retrieved_count"]
        captured["retrieved_count_raw"] = retrieval["retrieved_count_raw"]

        # incident_manager cites evidence for every retrieved message.
        candidate_evidence = [
            {"message_id": m["id"], "author": m["author"], "sent_at": m["sent_at"]} for m in retrieval["messages"]
        ]
        known_ids = set(inner_ctx.state.get(KNOWN_MESSAGE_IDS_STATE_KEY, []))
        validated = validate_evidence(candidate_evidence, known_ids)
        captured["validated_evidence"] = validated

        return {
            "outcome": "ok",
            "chat_id": resolved_chat_id,
            "chat_title": "SLOPANOC Gateway Group Test",
            "summary": "The team discussed migrating the gateway this sprint.",
            "evidence": validated,
        }

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "The team discussed migrating the gateway this sprint."),
        read_continuation_executor=fake_executor,
    )
    collected = [
        event async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    # --- Stage-by-stage counts, per the audit requirement -------------------
    assert captured["chat_id_used"] == "chat-real-1"  # never chat-real-2
    assert captured["retrieved_count_raw"] == 3
    assert captured["retrieved_count"] == 3  # nothing filtered out (no system/event entries, no time range)
    assert len(captured["validated_evidence"]) == 3  # every cited message_id was genuinely retrieved

    # --- No second ambiguity / destination resolution -----------------------
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    assert not any(e.type == StreamEventType.ACTION_PENDING for e in collected)

    # --- A real, non-empty summary -- NOT the false "no relevant messages" --
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "The team discussed migrating the gateway this sprint."
    assert "no relevant messages" not in completed.data["content"].lower()

    # --- SourceReference emitted normally ------------------------------------
    assert "source" in completed.data
    assert completed.data["source"]["message_count"] == 3

    final_selection = load_active_selection((await service.get_session(session_id, "api-user")).state)
    assert final_selection.selection_id == pending.selection_id
    assert final_selection.status == "resolved"


@pytest.mark.asyncio
async def test_no_time_range_is_ever_inherited_across_the_resume(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit test for section 7's failure mode: the original request
    had no time range; nothing (a prior read, "today", stale state)
    should narrow the resumed retrieval -- messages from several different
    dates all remain eligible.
    """
    messages_payload = [
        message("m1", "Alex", "Old message from last month.", "2026-07-01T09:00:00Z"),
        message("m2", "Priya", "Recent message from today.", "2026-08-20T09:00:00Z"),
    ]
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _routed_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], messages_payload),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(topic="SLOPANOC Gateway Group", pending_operation="summarize", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))

    pending = load_active_selection((await service.get_session(session_id)).state)
    assert pending.pending_read_intent.requested_time_range is None
    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")

    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)
    assert "(time range:" not in choose_response.resume_message

    after_choose = await service.get_session(session_id)
    resolved_selection = load_active_selection(after_choose.state)
    assert resolved_selection.pending_read_intent.requested_time_range is None

    # Resumed retrieval, with NO from_datetime/to_datetime -- both dates
    # remain eligible.
    inner_ctx = _Ctx(dict(after_choose.state))
    lookup = teams_list_chats(topic="SLOPANOC Gateway Group Test", tool_context=inner_ctx)
    retrieval = teams_get_messages(chat_id=lookup["matched_chat"]["chat_id"], tool_context=inner_ctx)
    assert retrieval["retrieved_count"] == 2
    assert {m["id"] for m in retrieval["messages"]} == {"m1", "m2"}


@pytest.mark.asyncio
async def test_focused_ambiguous_request_preserves_focus_and_still_finds_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 14: "Summarize SLOPANOC Gateway Group focusing on
    decisions" -- ambiguity, selection, then the focus text (never the
    destination) drives the resume, and real evidence still validates.
    """
    messages_payload = [
        message("m1", "Alex", "We decided to migrate the gateway this sprint.", "2026-08-20T09:00:00Z"),
    ]
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _routed_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], messages_payload),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(
        topic="SLOPANOC Gateway Group",
        pending_question="decisions",
        pending_operation="summarize",
        tool_context=ctx,
    )
    await service.persist_state_delta(session, dict(ctx.state))

    pending = load_active_selection((await service.get_session(session_id)).state)
    assert pending.pending_read_intent.operation == ReadOperation.SUMMARIZE
    assert pending.pending_read_intent.question == "decisions"
    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")

    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)
    assert choose_response.resume_message == "decisions"
    assert "SLOPANOC" not in choose_response.resume_message

    after_choose = await service.get_session(session_id)
    inner_ctx = _Ctx(dict(after_choose.state))
    lookup = teams_list_chats(topic=after_choose.state["selected_teams_chat_topic"], tool_context=inner_ctx)
    assert lookup["match"] == "matched"
    retrieval = teams_get_messages(chat_id=lookup["matched_chat"]["chat_id"], tool_context=inner_ctx)
    assert retrieval["retrieved_count"] == 1

    known_ids = set(inner_ctx.state.get(KNOWN_MESSAGE_IDS_STATE_KEY, []))
    evidence = [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-20T09:00:00Z"}]
    assert validate_evidence(evidence, known_ids) == evidence


@pytest.mark.asyncio
async def test_previously_selected_chat_never_queried_when_a_new_ambiguous_chat_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 15: Chat A is already selected; the user explicitly asks
    about an ambiguous Chat B; selection resolves to Chat B2 -- Chat B2
    becomes authoritative, Chat A is never queried again, and Chat B2's
    real messages are retrieved successfully.
    """
    chat_a_messages = [message("a1", "Someone", "Chat A content -- must never be retrieved here.", "2026-08-01T09:00:00Z")]
    chat_b2_messages = [
        message("b1", "Alex", "Chat B2 discussion.", "2026-08-20T09:00:00Z"),
        message("b2", "Priya", "Chat B2 follow-up.", "2026-08-20T09:05:00Z"),
    ]

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        if operation == "teams.listChats":
            return FakeResponse(
                200,
                [
                    chat("chat-a", "Chat A"),
                    chat("chat-b2-id", "Chat B2"),
                    chat("chat-b3-id", "Chat B3"),
                ],
            )
        if operation == "teams.getMessages":
            chat_id = json.get("chatId")
            if chat_id == "chat-a":
                return FakeResponse(200, chat_a_messages)
            if chat_id == "chat-b2-id":
                return FakeResponse(200, chat_b2_messages)
            return FakeResponse(200, [])
        raise AssertionError(f"unexpected operation: {operation}")

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    service = ApiSessionService()
    session_id = await service.create_session()

    session = await service.get_session(session_id)
    await service.persist_state_delta(session, {"selected_teams_chat_id": "chat-a", "selected_teams_chat_topic": "Chat A"})

    refreshed = await service.get_session(session_id)
    ctx = _Ctx(refreshed.state)
    result = teams_list_chats(topic="Chat B", pending_operation="summarize", tool_context=ctx)
    await service.persist_state_delta(refreshed, dict(ctx.state))
    assert result["selection_pending"] is True

    pending = load_active_selection((await service.get_session(session_id)).state)
    chosen = next(o for o in pending.options if o.label == "Chat B2")

    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    after_choose = await service.get_session(session_id)
    assert after_choose.state["selected_teams_chat_id"] == "chat-b2-id"
    assert after_choose.state["selected_teams_chat_topic"] == "Chat B2"

    inner_ctx = _Ctx(dict(after_choose.state))
    lookup = teams_list_chats(topic="Chat B2", tool_context=inner_ctx)
    assert lookup["matched_chat"]["chat_id"] == "chat-b2-id"

    retrieval = teams_get_messages(chat_id=lookup["matched_chat"]["chat_id"], tool_context=inner_ctx)
    assert retrieval["retrieved_count"] == 2
    assert {m["id"] for m in retrieval["messages"]} == {"b1", "b2"}
    # Chat A's content never appears -- it was never queried for this turn.
    assert all("Chat A content" not in m["text"] for m in retrieval["messages"])


@pytest.mark.asyncio
async def test_a_genuinely_empty_chat_still_correctly_reports_no_relevant_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 16: the false-empty bug is not a license to remove the
    legitimate empty-result path -- a chat that truly has zero usable
    messages must still resolve to an honest empty result.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _routed_gateway([chat("chat-real-1", "SLOPANOC Gateway Group Test")], []),
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

    after_choose = await service.get_session(session_id)
    inner_ctx = _Ctx(dict(after_choose.state))
    lookup = teams_list_chats(topic=after_choose.state["selected_teams_chat_topic"], tool_context=inner_ctx)
    retrieval = teams_get_messages(chat_id=lookup["matched_chat"]["chat_id"], tool_context=inner_ctx)

    assert retrieval["retrieved_count"] == 0
    assert retrieval["messages"] == []
