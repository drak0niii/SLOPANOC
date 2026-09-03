"""End-to-end state-continuity tests for the interactive Teams
chat-selection flow -- a hardening pass triggered by a live-testing
report: after a user resolved an ambiguous chat name via SelectionCard
and successfully sent a message, a later follow-up ("also summarize that
chatroom") incorrectly re-triggered a clarification request referencing
the ORIGINAL unresolved name.

Root cause, established by direct inspection before writing any fix
(see backend/agents/team_manager/prompts.py's hardening-pass comment):
the state-persistence and instruction-templating mechanism itself was
already correct and already covered by test_team_manager_state_sync.py
(real ADK `inject_session_state` tests) and
test_api_selection_endpoints.py (`selection_service.choose` persistence
tests) -- `selected_teams_chat_id`/`selected_teams_chat_topic` were
already being written and were already readable on the next turn. The
actual gap was PROMPT-LEVEL: team_manager's instruction never told the
model that this state value outranks its own earlier "not found"/
"ambiguous" replies in the same conversation. That gap was closed with
additive prompt wording (see test_selection_prompt_contract.py for the
pinned wording); this file exists to prove the DETERMINISTIC, testable
half of the full chain end-to-end -- the parts no prompt wording can
paper over if they were actually broken -- using only synthetic fixture
names, never the real private Teams example referenced in the live
report.
"""
from __future__ import annotations

import pytest

from backend.api import execution_service, selection_service
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY, approve_proposal
from backend.gateway import power_automate_client as pac_module
from backend.selection.schemas import SelectionKind
from backend.selection.service import PENDING_SELECTION_STATE_KEY, create_pending_selection
from backend.tests._api_fakes import FakeRunner
from backend.tests._fakes import FakeResponse
from backend.tools.teams.schemas import ChatSummary


def _candidates() -> list[ChatSummary]:
    return [
        ChatSummary(chat_id="chat-real-1", title="Project Falcon Room Test"),
        ChatSummary(chat_id="chat-real-2", title="Project Falcon Test"),
    ]


async def _seed_selection(
    service: ApiSessionService, session_id: str, requested_value: str = "Project Falcon Room", pending_write_message=None
):
    session = await service.get_session(session_id)
    selection = create_pending_selection(
        SelectionKind.TEAMS_CHAT, requested_value, _candidates(), session.state, pending_write_message=pending_write_message
    )
    await service.persist_state_delta(session, {PENDING_SELECTION_STATE_KEY: session.state[PENDING_SELECTION_STATE_KEY]})
    return selection


# --- Item 16/17: the exact live scenario, end to end -------------------------


@pytest.mark.asyncio
async def test_choosing_a_candidate_persists_state_a_later_turns_instruction_reflects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deterministic half of the fix: `selection_service.choose()`
    persists the resolved chat, and a REAL ADK instruction render for a
    later turn (not a fake/mock) reflects it -- proving the mechanism
    itself was never the broken part.
    """
    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.agents.readonly_context import ReadonlyContext
    from google.adk.utils.instructions_utils import inject_session_state

    from backend.agents.team_manager.agent import team_manager
    from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION

    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, requested_value="Project Falcon Room")
    chosen = selection.options[0]  # -> chat-real-1 / "Project Falcon Room Test"

    await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    refreshed = await service.get_session(session_id)
    assert refreshed.state["selected_teams_chat_id"] == "chat-real-1"
    assert refreshed.state["selected_teams_chat_topic"] == "Project Falcon Room Test"

    invocation_context = InvocationContext(
        session_service=service.adk_session_service,
        invocation_id="test-continuity-1",
        agent=team_manager,
        session=refreshed,
    )
    rendered = await inject_session_state(TEAM_MANAGER_INSTRUCTION, ReadonlyContext(invocation_context))

    assert 'topic "Project Falcon Room Test".' in rendered
    assert "authoritative even over your OWN earlier replies" in rendered


@pytest.mark.asyncio
async def test_old_requested_value_is_never_the_persisted_selected_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 12/17: the ambiguous original text the user typed
    ("Project Falcon Room") must never end up as the authoritative
    selected chat -- only the real, chosen candidate's own title does.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, requested_value="Project Falcon Room")
    chosen = selection.options[1]  # -> chat-real-2 / "Project Falcon Test"

    await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    refreshed = await service.get_session(session_id)
    assert refreshed.state["selected_teams_chat_topic"] == "Project Falcon Test"
    assert refreshed.state["selected_teams_chat_topic"] != "Project Falcon Room"
    # The resolved PendingSelection record itself still carries the
    # original requested_value -- that is legitimate history, not active
    # routing input (item 12's distinction).
    resolved_selection = refreshed.state[PENDING_SELECTION_STATE_KEY]
    assert resolved_selection["requested_value"] == "Project Falcon Room"
    assert resolved_selection["status"] == "resolved"


@pytest.mark.asyncio
async def test_successful_write_execution_does_not_clear_the_selected_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Item 8: after choosing a candidate for a pending write, approving
    and executing it successfully must leave `selected_teams_chat_*`
    exactly as the selection set them -- the write's own success/consume
    path has no reason to, and must not, touch this state.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"id": "chat-real-1", "webUrl": "https://teams/x"}),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, pending_write_message="hi")
    chosen = selection.options[0]  # -> chat-real-1 / "Project Falcon Room Test"

    choose_response = await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)
    proposal_id = choose_response.pending_action.proposal_id

    refreshed = await service.get_session(session_id)
    approve_proposal(proposal_id, refreshed.state)
    await service.persist_state_delta(
        refreshed, {PENDING_ACTION_PROPOSAL_STATE_KEY: refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
    )

    await execution_service.execute(service, session_id, proposal_id)

    final = await service.get_session(session_id)
    assert final.state["selected_teams_chat_id"] == "chat-real-1"
    assert final.state["selected_teams_chat_topic"] == "Project Falcon Room Test"


def test_execute_write_module_never_touches_selected_chat_state_keys() -> None:
    """Structural guarantee mirroring this suite's established
    inspect.getsource() pattern: the write-execution tool has no code
    path that could clear/overwrite the selected-chat keys as a side
    effect of consuming a proposal.
    """
    import inspect

    import backend.tools.teams.execute_write as execute_write_module

    source = inspect.getsource(execute_write_module)
    assert "selected_teams_chat" not in source


# --- Item 6/14/18: explicit new destination overrides the selection --------


@pytest.mark.asyncio
async def test_explicit_new_destination_overrides_a_prior_interactive_selection() -> None:
    """Combines the two distinct write paths for `selected_teams_chat_*`
    -- the interactive-selection path (`selection_service.choose`) and
    the normal incident_manager-resolution path
    (`sync_incident_manager_result_to_state`) -- proving the second
    genuinely overrides the first, exactly as an explicit "now summarize
    Chat B" follow-up would in a real turn.
    """
    from backend.agents.team_manager.state_sync import sync_incident_manager_result_to_state

    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, requested_value="Project Falcon Room")
    chosen = selection.options[0]  # -> chat-real-1 / "Project Falcon Room Test"
    await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    after_selection = await service.get_session(session_id)
    assert after_selection.state["selected_teams_chat_id"] == "chat-real-1"

    class _Tool:
        name = "incident_manager"

    class _Ctx:
        def __init__(self, state):
            self.state = state

    ctx = _Ctx(after_selection.state)
    sync_incident_manager_result_to_state(
        tool=_Tool(),
        args={},
        tool_context=ctx,
        tool_response={
            "outcome": "ok",
            "chat_id": "chat-real-9",
            "chat_title": "Finance Operations",
            "summary": "...",
            "evidence": [],
        },
    )
    await service.persist_state_delta(
        after_selection,
        {"selected_teams_chat_id": ctx.state["selected_teams_chat_id"], "selected_teams_chat_topic": ctx.state["selected_teams_chat_topic"]},
    )

    final = await service.get_session(session_id)
    assert final.state["selected_teams_chat_id"] == "chat-real-9"
    assert final.state["selected_teams_chat_topic"] == "Finance Operations"
    # The earlier selection is not resurrected or forced back.
    assert final.state["selected_teams_chat_id"] != "chat-real-1"


# --- Item 9/16: a read follow-up after resolution needs no new selection ---


@pytest.mark.asyncio
async def test_read_follow_up_after_a_resolved_selection_needs_no_new_selection_event() -> None:
    """Once `selected_teams_chat_*` is set (exactly as `choose()` sets it),
    a normal follow-up turn -- here simulated the same way every other
    streaming test in this suite simulates an incident_manager result,
    via `FakeRunner`'s `side_effect` -- completes as an ordinary read: no
    `selection.pending`, no `action.pending` (a read never produces a
    write proposal), and the resolved chat_id from that turn's own
    (synthetic) incident_manager result matches the already-selected
    chat -- proving nothing forces a fresh disambiguation merely because
    a `PendingSelection` exists in history.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    selection = await _seed_selection(service, session_id, requested_value="Project Falcon Room")
    chosen = selection.options[0]  # -> chat-real-1 / "Project Falcon Room Test"
    await selection_service.choose(service, session_id, selection.selection_id, chosen.option_id)

    # Production hardening pass #2: choosing a selection now always
    # creates a `ResolvedReadContinuation`, so this follow-up turn's
    # Incident Manager/read execution runs deterministically and
    # unconditionally, before team_manager's own turn -- inject a fake
    # executor (mirrors `runner`/`teams_contributors_resolver`'s own
    # injectable-callable pattern) rather than relying on a `FakeRunner`
    # `side_effect` to simulate it.
    async def fake_executor(*, user_id, continuation, **kwargs):
        return {
            "outcome": "ok",
            "chat_id": "chat-real-1",
            "chat_title": "Project Falcon Room Test",
            "summary": "Here is the summary.",
            "evidence": [],
        }

    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "Here is the summary."),
        read_continuation_executor=fake_executor,
    )
    collected = [event async for event in chat_service.execute_turn_events(session_id, "also summarize that chatroom for me", "api-user")]

    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    assert not any(e.type == StreamEventType.ACTION_PENDING for e in collected)
    assert any(e.type == StreamEventType.MESSAGE_COMPLETED for e in collected)

    final = await service.get_session(session_id, "api-user")
    assert final.state["selected_teams_chat_id"] == "chat-real-1"
    assert final.state["selected_teams_chat_topic"] == "Project Falcon Room Test"


# --- Item 11: re-resolving the SELECTED (not the old) value never needs ----
# --- fuzzy matching again ----------------------------------------------------


def test_re_resolving_the_now_selected_topic_matches_exactly_not_via_fuzzy_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """team_manager passes the ALREADY-RESOLVED topic (never the old,
    ambiguous `requested_value`) as `chat_topic` for a follow-up -- since
    that resolved topic is copied verbatim from the real chat's own
    title, resolving it again always lands on the deterministic MATCHED
    branch, never back through similarity scoring. This is what
    structurally satisfies "do not unnecessarily run fuzzy resolution
    again against the old requested value": the old value is simply never
    the input on a follow-up turn.
    """
    from backend.gateway import power_automate_client as pac_module
    from backend.tests._fakes import FakeResponse, chat
    from backend.tools.teams.list_chats import teams_list_chats

    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, [chat("chat-real-1", "Project Falcon Room Test"), chat("chat-real-2", "Project Falcon Test")]
        ),
    )

    # A follow-up resolving the SELECTED topic (not "Project Falcon Room").
    result = teams_list_chats(topic="Project Falcon Room Test")

    assert result["match"] == "matched"
    assert result["matched_chat"]["chat_id"] == "chat-real-1"
    assert result["candidates"] == []
    assert result.get("similar_candidates", []) == []
    assert result.get("selection_pending", False) is False
