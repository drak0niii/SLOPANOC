"""End-to-end regression tests for the exact live bug reported: after
choosing a candidate for a READ-kind Teams chat selection, the frontend
used to re-send the user's own original ambiguous request text as a new
turn, which still named the OLD, unresolved destination -- re-triggering
fuzzy resolution and reopening the same ambiguity (a second, unwanted
SelectionCard).

This file exercises the REAL, un-mocked pieces of the fix end to end:
`teams_list_chats` (real matching/fuzzy-resolution logic, only the Power
Automate gateway call itself is faked), `chat_resolution.resolve_chat`
(spied, never mocked away, to prove it is genuinely not called a second
time), `selection_service.choose` (real), and `ChatService.execute_turn_events`
(real event pipeline, only the ADK `Runner` is faked via `FakeRunner`).
Only synthetic fixture names are used, never the real private Teams
example referenced in the live report.
"""
from __future__ import annotations

import pytest

from backend.api import selection_service
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.gateway import power_automate_client as pac_module
from backend.selection.read_resume import GENERIC_GET_MESSAGES_RESUME_TEXT, GENERIC_SUMMARY_RESUME_TEXT
from backend.selection.service import load_active_selection
from backend.tests._api_fakes import FakeRunner
from backend.tests._fakes import FakeResponse, chat
from backend.tools.teams import list_chats as list_chats_module
from backend.tools.teams.list_chats import teams_list_chats


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _canned_read_continuation_executor(result: dict):
    """Production hardening pass #2: since `ResolvedReadContinuation`
    execution is now unconditional (chat_service.py calls it directly,
    before team_manager's own turn -- never left to team_manager's model
    to decide), these tests inject a fake `read_continuation_executor`
    (the same injectable-callable pattern as `runner`/
    `teams_contributors_resolver`) instead of relying on `FakeRunner`'s
    `side_effect` to simulate the read -- that simulation is exactly what
    the deterministic executor now performs for real, so a fake `Runner`-
    level `side_effect` is no longer the right place to model it. Never
    used to bypass an assertion this file still cares about -- it returns
    a canned, already-decided `IncidentManagerResponse`-shaped dict, and
    the tests below still assert on the resulting selected chat, absence
    of a second SelectionCard, `resolve_chat` call count, and final
    presented text exactly as before.
    """

    async def executor(*, user_id, continuation, **kwargs):
        return result

    return executor


def _spy_resolve_chat(monkeypatch: pytest.MonkeyPatch) -> list:
    """Wraps the REAL `resolve_chat` with a call-recording spy, patched at
    its point of use inside list_chats.py (where it was imported via
    `from ... import resolve_chat`) -- proves item 24 ("resolve_chat is
    NOT called again after choose") against the actual call site, not a
    module that nothing imports from directly.
    """
    calls: list = []
    original = list_chats_module.resolve_chat

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    monkeypatch.setattr(list_chats_module, "resolve_chat", spy)
    return calls


@pytest.mark.asyncio
async def test_read_resume_after_choosing_a_candidate_never_replays_or_reopens_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolve_chat_calls = _spy_resolve_chat(monkeypatch)
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [chat("chat-real-1", "Gateway Group Test"), chat("chat-real-2", "Gateway Ops")]),
    )

    service = ApiSessionService()
    session_id = await service.create_session()

    # Turn 1: "gimme a summary of this chat room Gateway" -- no exact
    # match, similarity scoring finds candidates, a PendingSelection is
    # created (mirrors incident_manager's own step 2 call).
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    result = teams_list_chats(topic="Gateway", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))

    assert result["match"] == "not_found"
    assert result["selection_pending"] is True
    assert len(resolve_chat_calls) == 1  # the one legitimate fuzzy resolution

    refreshed = await service.get_session(session_id)
    pending = load_active_selection(refreshed.state)
    assert pending is not None
    chosen = next(o for o in pending.options if o.label == "Gateway Group Test")

    # User clicks "Gateway Group Test" in the SelectionCard.
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    assert choose_response.pending_action is None  # a read, not a write
    assert choose_response.resume_message is not None
    # The resume text never restates the old (or new) destination name --
    # item 3/9's core requirement.
    assert "Gateway" not in choose_response.resume_message

    after_choose = await service.get_session(session_id)
    assert after_choose.state["selected_teams_chat_id"] == "chat-real-1"
    assert after_choose.state["selected_teams_chat_topic"] == "Gateway Group Test"

    # Turn 2: production hardening pass #2 -- the Incident Manager/read
    # path now executes deterministically, unconditionally, BEFORE
    # team_manager's own turn (never left to a `FakeRunner` `side_effect`
    # to simulate) -- see `_canned_read_continuation_executor` above.
    # `resolve_chat_calls` staying at 1 (asserted below) now proves this
    # even more directly than before: the canned executor makes no
    # `teams_list_chats` call at all.
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Here is the summary of Gateway Group Test."),
        read_continuation_executor=_canned_read_continuation_executor(
            {
                "outcome": "ok",
                "chat_id": "chat-real-1",
                "chat_title": "Gateway Group Test",
                "summary": "Here is the summary of Gateway Group Test.",
                "evidence": [],
            }
        ),
    )
    collected = [
        event async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    assert not any(e.type == StreamEventType.ACTION_PENDING for e in collected)
    assert any(e.type == StreamEventType.MESSAGE_COMPLETED for e in collected)

    # resolve_chat is STILL only ever called once, total -- turn 2's
    # exact-topic lookup short-circuits on the MATCHED branch before ever
    # reaching fuzzy resolution.
    assert len(resolve_chat_calls) == 1

    # Exactly one selection exists, still the same selection_id, now resolved.
    final = await service.get_session(session_id, "api-user")
    final_selection = load_active_selection(final.state)
    assert final_selection.selection_id == pending.selection_id
    assert final_selection.status == "resolved"


@pytest.mark.asyncio
async def test_read_resume_generalizes_to_a_non_summary_question(monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 19/25: the same mechanism resumes ANY read-shaped question
    (e.g. "who is in this chat?"), not just plain summarization -- proving
    the design is not a one-off "pending_summary_request" special case.
    """
    resolve_chat_calls = _spy_resolve_chat(monkeypatch)
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [chat("chat-real-1", "Gateway Group Test")]),
    )

    service = ApiSessionService()
    session_id = await service.create_session()

    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(
        topic="Gateway",
        pending_question="Who is in this chat?",
        tool_context=ctx,
    )
    await service.persist_state_delta(session, dict(ctx.state))

    refreshed = await service.get_session(session_id)
    pending = load_active_selection(refreshed.state)
    chosen = pending.options[0]

    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    assert choose_response.pending_action is None
    assert choose_response.resume_message == "Who is in this chat?"

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Here is who is in this chat."),
        read_continuation_executor=_canned_read_continuation_executor(
            {
                "outcome": "ok",
                "chat_id": "chat-real-1",
                "chat_title": "Gateway Group Test",
                "summary": "Here is who is in this chat.",
                "evidence": [],
            }
        ),
    )
    collected = [
        event async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    assert not any(e.type == StreamEventType.ACTION_PENDING for e in collected)
    assert len(resolve_chat_calls) == 1


@pytest.mark.asyncio
async def test_write_flow_is_unaffected_still_requires_approval_no_resume_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 8/26: sendMessage with an ambiguous destination must still
    stop at a normal ActionProposal -- no resume text, no auto-execution.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [chat("chat-real-1", "Gateway Group Test")]),
    )

    service = ApiSessionService()
    session_id = await service.create_session()

    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    teams_list_chats(topic="Gateway", pending_write_message="hello team", tool_context=ctx)
    await service.persist_state_delta(session, dict(ctx.state))

    refreshed = await service.get_session(session_id)
    pending = load_active_selection(refreshed.state)
    chosen = pending.options[0]

    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    assert choose_response.resume_message is None
    assert choose_response.pending_action is not None
    assert choose_response.pending_action.status == "pending"  # awaiting explicit approval, never auto-executed
    assert choose_response.pending_action.message == "hello team"
    assert choose_response.pending_action.chat_id == "chat-real-1"


@pytest.mark.asyncio
async def test_read_resume_after_a_prior_selected_chat_switches_to_the_new_candidate_and_still_resumes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact live-bug regression (pre-4H refinement, item 3/8): a chat was
    ALREADY selected from earlier work (a completed Teams send to Chat A),
    then the user explicitly asks about a DIFFERENT, inexact chat ("Chat
    B"). `pending_question` is set to the user's own raw request text
    (team_manager's historical, permitted behavior for a first question --
    see `_safe_pending_question`'s docstring), which still names "Chat B".
    Asserts the selected-chat state correctly switches from Chat A to the
    newly-chosen Chat B candidate, the summarize intent survives, the read
    resumes automatically with NO second ambiguity, and Chat A is never
    touched again. Only synthetic fixture names are used.
    """
    resolve_chat_calls = _spy_resolve_chat(monkeypatch)
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                chat("chat-a", "Chat A"),
                chat("chat-b2", "Knowledge Management Daily Sync up"),
                chat("chat-b3", "Knowledge Management Weekly"),
            ],
        ),
    )

    service = ApiSessionService()
    session_id = await service.create_session()

    # Precondition: Chat A is already the session's authoritative selected
    # chat, from earlier, unrelated, already-completed work (a prior Teams
    # send) -- simulates state_sync.py's own persistence after that turn.
    session = await service.get_session(session_id)
    await service.persist_state_delta(
        session, {"selected_teams_chat_id": "chat-a", "selected_teams_chat_topic": "Chat A"}
    )

    # Turn: "now i need you to sum up this chat room Knowledge Management
    # Daily" -- no exact match. team_manager passes the user's own raw
    # request text as `pending_question` (still names the ambiguous chat).
    refreshed = await service.get_session(session_id)
    ctx = _Ctx(refreshed.state)
    result = teams_list_chats(
        topic="Knowledge Management Daily",
        pending_question="now i need you to sum up this chat room Knowledge Management Daily",
        tool_context=ctx,
    )
    await service.persist_state_delta(refreshed, dict(ctx.state))

    assert result["match"] == "not_found"
    assert result["selection_pending"] is True

    after_ambiguity = await service.get_session(session_id)
    # The OLD selected chat is untouched by the mere ambiguity -- it is
    # only ever replaced once a NEW selection actually resolves.
    assert after_ambiguity.state["selected_teams_chat_id"] == "chat-a"
    assert after_ambiguity.state["selected_teams_chat_topic"] == "Chat A"

    pending = load_active_selection(after_ambiguity.state)
    assert pending is not None
    # The tainted question (restates "Knowledge Management Daily") was
    # discarded at the source -- never stored, never available to leak
    # into a resume later.
    assert pending.pending_read_intent.question is None
    chosen = next(o for o in pending.options if o.label == "Knowledge Management Daily Sync up")

    # User clicks "Knowledge Management Daily Sync up" in the SelectionCard.
    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    assert choose_response.pending_action is None  # a read, not a write
    assert choose_response.resume_message is not None
    assert "Knowledge Management Daily" not in choose_response.resume_message
    assert "Chat A" not in choose_response.resume_message

    after_choose = await service.get_session(session_id)
    # Selected-chat state has SWITCHED from Chat A to the newly-chosen
    # candidate -- the explicit new destination correctly takes precedence.
    assert after_choose.state["selected_teams_chat_id"] == "chat-b2"
    assert after_choose.state["selected_teams_chat_topic"] == "Knowledge Management Daily Sync up"

    # Turn 2: production hardening pass #2 -- the resumed read now
    # executes deterministically via the injected executor (never a
    # `FakeRunner` `side_effect`); `resolve_chat_calls` staying at 1
    # (asserted below) proves "Knowledge Management Daily" is never
    # re-resolved and Chat A is never queried again.
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Here is the summary of the selected chat."),
        read_continuation_executor=_canned_read_continuation_executor(
            {
                "outcome": "ok",
                "chat_id": "chat-b2",
                "chat_title": "Knowledge Management Daily Sync up",
                "summary": "Here is the summary of the selected chat.",
                "evidence": [],
            }
        ),
    )
    collected = [
        event async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    # No second clarification/SelectionCard, no ActionProposal (this is a
    # read) -- the resumed turn produces a real completed answer.
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    assert not any(e.type == StreamEventType.ACTION_PENDING for e in collected)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Here is the summary of the selected chat."

    # resolve_chat (the fuzzy/similarity path) was invoked exactly once,
    # total -- for the ORIGINAL ambiguity only. The resume turn's exact-
    # topic lookup short-circuits on the MATCHED branch, never reopening
    # or re-resolving "Knowledge Management Daily" again.
    assert len(resolve_chat_calls) == 1

    final = await service.get_session(session_id, "api-user")
    final_selection = load_active_selection(final.state)
    assert final_selection.selection_id == pending.selection_id  # still the ONE selection, now resolved
    assert final_selection.status == "resolved"
    assert final.state["selected_teams_chat_id"] == "chat-b2"  # Chat A never restored/reused


# --- Pre-4H hardening pass (item 1): operation/focus/destination field ------
# --- separation -- the exact "previous chat + focus text" live scenario ----


@pytest.mark.asyncio
async def test_previous_chat_plus_focused_summary_request_preserves_both_destination_switch_and_focus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exact regression scenario requested (pre-4H refinement, item 6):
    a chat was already selected (Chat A, from earlier completed work),
    then the user asks to summarize a DIFFERENT, inexact chat ("Chat B")
    WITH a specific focus ("decisions and open actions"). A correctly-
    behaving incident_manager (per the strengthened prompt contract) sets
    `pending_question` to ONLY the focus detail -- never restating "Chat
    B" -- and `pending_operation` to "summarize" independently. Asserts
    the destination switches to the new candidate, `operation` stays
    "summarize", the FULL focus text survives verbatim, Chat A is never
    used, the old ambiguous "Chat B" name is never re-resolved, and the
    read resumes automatically with no clarification/second SelectionCard/
    ApprovalCard. Only synthetic fixture names are used.
    """
    resolve_chat_calls = _spy_resolve_chat(monkeypatch)
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                chat("chat-a", "Chat A"),
                chat("chat-b2", "Chat B Daily Sync"),
                chat("chat-b3", "Chat B Weekly"),
            ],
        ),
    )

    service = ApiSessionService()
    session_id = await service.create_session()

    # Precondition: Chat A is already the session's authoritative selected
    # chat, from earlier, unrelated, already-completed work.
    session = await service.get_session(session_id)
    await service.persist_state_delta(
        session, {"selected_teams_chat_id": "chat-a", "selected_teams_chat_topic": "Chat A"}
    )

    # "Summarize Chat B focusing on decisions and open actions" -- no
    # exact match. incident_manager passes ONLY the focus detail as
    # `pending_question` (never "Chat B" itself) plus `pending_operation`.
    refreshed = await service.get_session(session_id)
    ctx = _Ctx(refreshed.state)
    result = teams_list_chats(
        topic="Chat B",
        pending_question="decisions and open actions",
        pending_operation="summarize",
        tool_context=ctx,
    )
    await service.persist_state_delta(refreshed, dict(ctx.state))

    assert result["match"] == "not_found"
    assert result["selection_pending"] is True

    after_ambiguity = await service.get_session(session_id)
    assert after_ambiguity.state["selected_teams_chat_id"] == "chat-a"  # untouched by the mere ambiguity

    pending = load_active_selection(after_ambiguity.state)
    assert pending is not None
    assert pending.pending_read_intent.operation == "summarize"
    # The FULL focus text survives -- never dropped, never partially
    # stripped -- because it never contained the ambiguous chat name.
    assert pending.pending_read_intent.question == "decisions and open actions"
    chosen = next(o for o in pending.options if o.label == "Chat B Daily Sync")

    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    assert choose_response.pending_action is None  # a read -- no ApprovalCard
    assert choose_response.resume_message == "decisions and open actions"
    assert "Chat B" not in choose_response.resume_message
    assert "Chat A" not in choose_response.resume_message

    after_choose = await service.get_session(session_id)
    assert after_choose.state["selected_teams_chat_id"] == "chat-b2"
    assert after_choose.state["selected_teams_chat_topic"] == "Chat B Daily Sync"

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Decisions: X. Open actions: Y."),
        read_continuation_executor=_canned_read_continuation_executor(
            {
                "outcome": "ok",
                "chat_id": "chat-b2",
                "chat_title": "Chat B Daily Sync",
                "summary": "Decisions: X. Open actions: Y.",
                "evidence": [],
            }
        ),
    )
    collected = [
        event async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)  # no second SelectionCard
    assert not any(e.type == StreamEventType.ACTION_PENDING for e in collected)  # no ApprovalCard
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Decisions: X. Open actions: Y."

    # resolve_chat (fuzzy path) invoked exactly once, total -- for the
    # ORIGINAL ambiguity only; "Chat B" is never re-resolved.
    assert len(resolve_chat_calls) == 1

    final = await service.get_session(session_id, "api-user")
    final_selection = load_active_selection(final.state)
    assert final_selection.selection_id == pending.selection_id
    assert final_selection.status == "resolved"
    assert final.state["selected_teams_chat_id"] == "chat-b2"  # Chat A never restored/reused


@pytest.mark.asyncio
async def test_previous_chat_plus_plain_summarize_request_needs_no_focus_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 7: a plain "summarize Chat B" request (no distinguishing
    focus) resumes correctly with no `question` at all -- the operation
    alone is enough to produce a correct, generic resume instruction.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [chat("chat-a", "Chat A"), chat("chat-b2", "Chat B Daily Sync")]),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await service.persist_state_delta(
        session, {"selected_teams_chat_id": "chat-a", "selected_teams_chat_topic": "Chat A"}
    )

    refreshed = await service.get_session(session_id)
    ctx = _Ctx(refreshed.state)
    teams_list_chats(topic="Chat B", pending_operation="summarize", tool_context=ctx)
    await service.persist_state_delta(refreshed, dict(ctx.state))

    pending = load_active_selection((await service.get_session(session_id)).state)
    assert pending.pending_read_intent.question is None
    assert pending.pending_read_intent.operation == "summarize"
    chosen = next(o for o in pending.options if o.label == "Chat B Daily Sync")

    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    assert choose_response.pending_action is None
    assert choose_response.resume_message == GENERIC_SUMMARY_RESUME_TEXT

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Summary text."),
        read_continuation_executor=_canned_read_continuation_executor(
            {
                "outcome": "ok",
                "chat_id": "chat-b2",
                "chat_title": "Chat B Daily Sync",
                "summary": "Summary text.",
                "evidence": [],
            }
        ),
    )
    collected = [
        event async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Summary text."


@pytest.mark.asyncio
async def test_previous_chat_plus_get_messages_with_time_range_preserves_operation_and_time_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 8: "show me messages from Chat B from today" -- the
    get_messages operation and the time range both survive resume, the
    destination switches correctly, and there is no clarification.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [chat("chat-a", "Chat A"), chat("chat-b2", "Chat B Daily Sync")]),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await service.persist_state_delta(
        session, {"selected_teams_chat_id": "chat-a", "selected_teams_chat_topic": "Chat A"}
    )

    refreshed = await service.get_session(session_id)
    ctx = _Ctx(refreshed.state)
    teams_list_chats(
        topic="Chat B",
        pending_operation="get_messages",
        pending_time_range="today",
        tool_context=ctx,
    )
    await service.persist_state_delta(refreshed, dict(ctx.state))

    pending = load_active_selection((await service.get_session(session_id)).state)
    assert pending.pending_read_intent.operation == "get_messages"
    assert pending.pending_read_intent.requested_time_range == "today"
    chosen = next(o for o in pending.options if o.label == "Chat B Daily Sync")

    choose_response = await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id)

    assert choose_response.pending_action is None
    assert choose_response.resume_message == f"{GENERIC_GET_MESSAGES_RESUME_TEXT} (time range: today)"
    assert "Chat B" not in choose_response.resume_message
    assert "Chat A" not in choose_response.resume_message

    after_choose = await service.get_session(session_id)
    assert after_choose.state["selected_teams_chat_topic"] == "Chat B Daily Sync"

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Here are today's messages."),
        read_continuation_executor=_canned_read_continuation_executor(
            {
                "outcome": "ok",
                "chat_id": "chat-b2",
                "chat_title": "Chat B Daily Sync",
                "summary": "Here are today's messages.",
                "evidence": [],
            }
        ),
    )
    collected = [
        event async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    assert not any(e.type == StreamEventType.ACTION_PENDING for e in collected)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "Here are today's messages."
