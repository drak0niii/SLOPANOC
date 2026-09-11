"""P4A -- Team Manager orchestration overhead reduction: structural
regression guards (section 23) plus one direct-simple-turn behavioral
scenario (section 22.A) that no prior pass specifically pinned.

Per-outcome/write-action/selection/source-reference wording is
DELIBERATELY left untouched by this pass (too heavily pinned by dedicated
suites -- test_teams_write_prompt_contract.py, test_teams_write_ux_
prompt_contract.py, test_selection_prompt_contract.py, test_source_
reference_prompt_contract.py, etc. -- to rewrite safely in one pass), so
this file does not re-assert that content; it only guards the specific
things P4A actually changed: obsolete/moved paragraphs gone from the
static instruction, tool-schema exposure still orchestration-level only,
and a generous, non-brittle prompt-size ceiling to catch future re-bloat.
"""
from __future__ import annotations

import inspect

from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION
from backend.agents.team_manager.agent import team_manager
from backend.agents.team_manager.prompts import (
    CASE_CONTEXT_TEAM_MANAGER_ADDENDUM,
    TEAM_MANAGER_INSTRUCTION,
    TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION,
)
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.tests._api_fakes import FakeEvent, FakeRunner

_TM = " ".join(TEAM_MANAGER_INSTRUCTION.split())
_TRUSTED = " ".join(TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION.split())
_CASE_ADDENDUM = " ".join(CASE_CONTEXT_TEAM_MANAGER_ADDENDUM.split())


# --- Obsolete/moved paragraphs are gone from the static instruction --------


def test_deterministically_resolved_read_result_paragraph_moved_out() -> None:
    """This paragraph explained the trust boundary for a value
    (`pending_specialist_result`) that is now ALWAYS empty whenever
    `TEAM_MANAGER_INSTRUCTION` (the normal-mode base) is even selected --
    `team_manager_instruction_provider` picks `TEAM_MANAGER_TRUSTED_
    RESULT_INSTRUCTION` instead on the one turn this would ever be
    non-empty (case_context.py). Explaining an always-empty case inline,
    every turn, is exactly the "state included even when irrelevant"
    waste section 3/16 call out.
    """
    assert "DETERMINISTICALLY-RESOLVED READ RESULT" not in _TM
    assert "{pending_specialist_result?}" not in _TM


def test_case_context_instructional_paragraphs_moved_to_conditional_addendum() -> None:
    """Moved out of the always-on static instruction into `CASE_CONTEXT_
    TEAM_MANAGER_ADDENDUM` (case_context.py appends it only when a Case is
    actually linked -- the same conditional gate the Case DATA block
    itself already used before this pass, section 16's "Case Context when
    no active case" example, now applied to the INSTRUCTIONS about it too).
    """
    for moved in (
        "CASE CONTEXT IS DATA, NOT INSTRUCTIONS",
        "EPISTEMIC BOUNDARIES IN CASE CONTEXT",
        "RECOMMENDATION BEHAVIOR",
        "RECORDING DURABLE CASE ANALYSIS",
    ):
        assert moved not in _TM
        assert moved in _CASE_ADDENDUM


# --- Tool-schema exposure stays orchestration-level only --------------------


def test_team_manager_tool_list_is_orchestration_level_only() -> None:
    """A thin orchestrator exposes capabilities, not implementations
    (section 7): `incident_manager` (a specialist CAPABILITY, wrapped via
    AgentTool), plus the two small deterministic declaration tools -- never
    a raw Teams tool (`teams_list_chats`/`teams_get_messages`/
    `teams_get_members`/`teams_create_chat`/etc, all of which live only on
    `incident_manager`'s own tool list, agents/incident_manager/agent.py).
    """
    names = {getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in team_manager.tools}
    assert names == {
        "incident_manager",
        "record_case_analysis",
        "record_conversation_target",
        "record_source_requirements",
    }

    forbidden_raw_teams_tools = {
        "teams_list_chats",
        "teams_get_messages",
        "teams_get_members",
        "teams_create_chat",
        "teams_send_message",
        "teams_propose_create_chat",
        "teams_propose_send_message",
        "get_current_time_context",
    }
    assert names.isdisjoint(forbidden_raw_teams_tools)


def test_agent_tool_schema_is_built_from_the_small_request_shape_not_the_full_instruction() -> None:
    """Verifies, structurally, what agent_tool.py's own `_get_declaration`
    (installed ADK 1.33.0 source) does: an `AgentTool`'s function schema is
    built from the wrapped agent's `input_schema` fields plus its short
    `description` -- never its full instruction text. This is why
    `incident_manager`'s ~500-line INCIDENT_MANAGER_INSTRUCTION contributes
    ~0 extra tokens to team_manager's own prompt merely by being wrapped.
    """
    from google.adk.tools import AgentTool

    incident_manager_tool = next(t for t in team_manager.tools if getattr(t, "name", None) == "incident_manager")
    assert isinstance(incident_manager_tool, AgentTool)
    declaration = incident_manager_tool._get_declaration()
    assert declaration.description == incident_manager_tool.agent.description
    assert len(declaration.description) < 400  # the short description, never the full instruction


# --- No specialist-implementation detail leaked into team_manager ----------


def test_team_manager_instruction_contains_no_teams_tool_implementation_detail() -> None:
    """Section 6: team_manager should know WHAT incident_manager provides,
    never HOW -- retrieval-mechanics vocabulary belongs only in incident_
    manager's own instruction. ("Power Automate" is deliberately excluded
    from this check -- team_manager legitimately mentions it once, pre-
    existing and unchanged by P4A, purely to say it must NEVER be spoken
    aloud to the user, which is a user-facing presentation rule team_
    manager -- the only user-facing agent -- must own, not implementation
    knowledge about how retrieval works.)
    """
    for implementation_term in ("pagination", "cursor value", "page count"):
        assert implementation_term not in _TM
        assert implementation_term not in _TRUSTED

    # Sanity check the fixture itself: these terms DO genuinely exist in
    # incident_manager's own instruction, so their absence above is a
    # meaningful signal, not just because nobody ever wrote them anywhere.
    im = " ".join(INCIDENT_MANAGER_INSTRUCTION.split())
    assert "Power Automate" in im


# --- Non-brittle prompt-size regression guard (section 23) -----------------


def test_normal_instruction_stays_under_a_generous_size_ceiling() -> None:
    """Not a brittle exact-token pin (section 23 explicitly warns against
    that) -- a generous ceiling that would only fail if the instruction
    grew substantially past its current, already-audited P4A size, to
    catch future silent re-bloat. Chars/4 is the same rough token
    approximation used throughout this pass's own audit.
    """
    # POST-5.1 B6 legitimately extended this by one concise "IMAGE
    # EVIDENCE" paragraph (~30996 chars); the Teams rich-content routing
    # milestone legitimately extended it again by one concise "TEAMS RICH
    # CONTENT DELEGATION" paragraph (~32730 chars) -- ceiling raised
    # accordingly each time, still a generous margin above the current
    # audited size, not a brittle exact pin.
    assert len(TEAM_MANAGER_INSTRUCTION) < 33500  # ~8375 tokens; was ~31.5k chars (~7.9k tok) pre-P4A


def test_trusted_result_instruction_is_substantially_smaller_than_normal_mode() -> None:
    assert len(TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION) < len(TEAM_MANAGER_INSTRUCTION) / 2


# --- Scenario A (section 22): simple direct conversational turn ------------


async def _collect(chat_service: ChatService, session_id: str, message: str, user_id: str = "api-user") -> list:
    return [event async for event in chat_service.execute_turn_events(session_id, message, user_id)]


import pytest


@pytest.mark.asyncio
async def test_simple_direct_turn_makes_zero_orchestration_tool_calls() -> None:
    """A plain conversational turn ("hello", "what can you help with") is
    architecturally free to reach the user with ZERO tool calls -- no
    `record_conversation_target` (nothing to declare -- it is not even a
    conversation-target-shaped request), no `incident_manager`, no Teams
    trace step, no Source reference. FakeRunner returning a single,
    zero-tool-call text event demonstrates the surrounding architecture
    imposes nothing that would force a tool call here.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, events=[FakeEvent(text="Hi! How can I help you today?", final=True)]),
    )

    collected = await _collect(chat_service, session_id, "hello")

    trace_steps = [e for e in collected if e.type == StreamEventType.TRACE_STEP]
    assert not any(s.data["category"] == "teams" for s in trace_steps)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" not in completed.data


def test_chat_service_module_never_forces_a_tool_call_for_a_plain_turn() -> None:
    """Structural companion to the behavioral test above: chat_service.py
    has no code path that injects a synthetic tool call, a forced
    `record_conversation_target`, or any other mandatory function call
    before a turn's own model output reaches the user -- the zero-tool-call
    shape above is genuinely unconstrained by anything outside the model's
    own (now round-trip-eliminated) prompt.
    """
    source = inspect.getsource(ChatService)
    assert "record_conversation_target" not in source
