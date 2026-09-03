"""P4A: tests for the trusted-result presentation instruction-mode switch
in case_context.py's `team_manager_instruction_provider` -- exercised
against REAL ADK objects (`InvocationContext`/`ReadonlyContext`/
`InMemorySessionService`), the same rigor
test_team_manager_case_context_provider.py already established for the
Case-context conditional mechanism this mirrors.

Mode selection is keyed on `PENDING_SPECIALIST_RESULT_STATE_KEY` alone --
the SAME state key `chat_service.py` writes strictly before calling
team_manager's `Runner.run_async` whenever a `ResolvedReadContinuation`
was just deterministically executed (see read_continuation_presentation.py
and prompts.py's own "P4A" docstring section). Nothing here re-implements
or duplicates that trust boundary -- these tests only prove the
INSTRUCTION rendering responds to it correctly.
"""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.sessions import InMemorySessionService

from backend.agents.team_manager.agent import team_manager
from backend.agents.team_manager.prompts import (
    TEAM_MANAGER_INSTRUCTION,
    TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION,
)
from backend.agents.team_manager.read_continuation_presentation import PENDING_SPECIALIST_RESULT_STATE_KEY

ALICE = "alice"

_SAMPLE_RESULT = {
    "outcome": "ok",
    "chat_title": "Production Bridge",
    "summary": "The team discussed a database migration and decided to proceed next sprint.",
}


async def _readonly_context(state: dict) -> ReadonlyContext:
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="t", user_id=ALICE, state=state)
    invocation_context = InvocationContext(
        session_service=session_service, invocation_id="test-inv", agent=team_manager, session=session
    )
    return ReadonlyContext(invocation_context)


@pytest.mark.asyncio
async def test_normal_turn_uses_the_full_orchestration_instruction() -> None:
    ctx = await _readonly_context(state={})
    rendered = await team_manager.canonical_instruction(ctx)

    assert rendered[0].startswith(TEAM_MANAGER_INSTRUCTION.split("{selected_teams_chat_topic?}")[0])
    assert "CONVERSATION TARGET" in rendered[0]
    assert "TRUSTED RESULT PRESENTATION ONLY" not in rendered[0]


@pytest.mark.asyncio
async def test_pending_specialist_result_switches_to_the_trusted_result_instruction() -> None:
    ctx = await _readonly_context(state={PENDING_SPECIALIST_RESULT_STATE_KEY: _SAMPLE_RESULT})
    rendered = await team_manager.canonical_instruction(ctx)

    assert rendered[0].startswith(TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION.split("{pending_specialist_result?}")[0])
    assert "TRUSTED RESULT PRESENTATION ONLY" in rendered[0]


@pytest.mark.asyncio
async def test_trusted_result_instruction_excludes_conversation_target_and_write_actions() -> None:
    """The whole point of the narrower mode -- a resumed continuation is
    always a READ, already fully resolved, so none of this reasoning
    surface applies (section 15's "trusted result presentation mode").
    """
    ctx = await _readonly_context(state={PENDING_SPECIALIST_RESULT_STATE_KEY: _SAMPLE_RESULT})
    rendered = await team_manager.canonical_instruction(ctx)

    for absent in (
        "CONVERSATION TARGET",
        "TEAMS WRITE ACTIONS",
        "COLLECTING WRITE-ACTION DETAILS",
        "PRESENTING A PROPOSAL",
        "record_conversation_target` with exactly",
    ):
        assert absent not in rendered[0]


@pytest.mark.asyncio
async def test_trusted_result_instruction_still_forbids_redelegation() -> None:
    ctx = await _readonly_context(state={PENDING_SPECIALIST_RESULT_STATE_KEY: _SAMPLE_RESULT})
    rendered = await team_manager.canonical_instruction(ctx)

    assert "Do not call `incident_manager`" in rendered[0] or "do not call `incident_manager`" in rendered[0]
    assert "record_conversation_target" in rendered[0]


@pytest.mark.asyncio
async def test_trusted_result_instruction_renders_the_result_via_state_templating() -> None:
    ctx = await _readonly_context(state={PENDING_SPECIALIST_RESULT_STATE_KEY: _SAMPLE_RESULT})
    rendered = await team_manager.canonical_instruction(ctx)

    # ADK's own `{var?}` state templating substituted the placeholder with
    # the actual state value -- never left as a literal, unsubstituted tag.
    assert "{pending_specialist_result?}" not in rendered[0]
    assert "Production Bridge" in rendered[0]


@pytest.mark.asyncio
async def test_trusted_result_instruction_still_supports_case_context_addendum() -> None:
    """Mode selection and Case-context conditional inclusion are
    independent axes -- a trusted-result turn on a Case-linked session
    still gets the Case addendum, exactly like a normal turn would.
    """
    from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY
    from backend.cases.service import CaseService
    import backend.agents.team_manager.case_context as case_context_module

    service = CaseService()
    case = await service.create_case(ALICE, "Packet loss", "Loss on core router.")

    ctx = await _readonly_context(
        state={PENDING_SPECIALIST_RESULT_STATE_KEY: _SAMPLE_RESULT, ACTIVE_CASE_ID_STATE_KEY: case.case_id}
    )

    import unittest.mock

    with unittest.mock.patch.object(case_context_module, "get_case_service", lambda: service):
        rendered = await team_manager.canonical_instruction(ctx)

    assert "TRUSTED RESULT PRESENTATION ONLY" in rendered[0]
    assert "ACTIVE CASE CONTEXT:" in rendered[0]
    assert "Packet loss" in rendered[0]


@pytest.mark.asyncio
async def test_pending_specialist_result_absent_falls_back_to_normal_mode() -> None:
    """An empty dict / falsy value must never accidentally trigger the
    trusted-result mode -- only a genuinely present, truthy result does.
    """
    ctx = await _readonly_context(state={PENDING_SPECIALIST_RESULT_STATE_KEY: None})
    rendered = await team_manager.canonical_instruction(ctx)
    assert "CONVERSATION TARGET" in rendered[0]


# USER-FACING MARKDOWN OUTPUT CONTRACT -- these are prompt-contract tests
# only (per the task's own instruction): they prove the correct instruction
# text reaches the correct user-facing agents, never a claim that Gemini
# itself will reliably comply -- that is a live-QA concern, not something a
# deterministic test can guarantee.

@pytest.mark.asyncio
async def test_a_normal_team_manager_instruction_contains_markdown_contract() -> None:
    ctx = await _readonly_context(state={})
    rendered = await team_manager.canonical_instruction(ctx)

    assert "RESPONSE FORMATTING" in rendered[0]
    assert "standard Markdown" in rendered[0]


@pytest.mark.asyncio
async def test_b_presentation_team_manager_receives_the_same_markdown_contract() -> None:
    """presentation_team_manager shares team_manager's own instruction
    provider (never overridden by its `.model_copy` -- see agent.py) --
    mode selection is state-driven, not tied to the agent object, exactly
    like `test_pending_specialist_result_switches_to_the_trusted_result_
    instruction` above already proves for team_manager itself. Also
    reconfirms the R1 structural trust boundary (`tools == []`) that this
    formatting-only pass must never weaken.
    """
    from backend.agents.team_manager.agent import presentation_team_manager

    assert presentation_team_manager.tools == []

    ctx = await _readonly_context(state={PENDING_SPECIALIST_RESULT_STATE_KEY: _SAMPLE_RESULT})
    rendered = await presentation_team_manager.canonical_instruction(ctx)

    assert "RESPONSE FORMATTING" in rendered[0]
    assert "standard Markdown" in rendered[0]
    assert "TRUSTED RESULT PRESENTATION ONLY" in rendered[0]


def test_c_both_instructions_prefer_direct_markdown_over_escaped() -> None:
    """A prompt-contract test: both instructions must explicitly show the
    correct (`**bold**`, `- item`) form as correct and the escaped
    (`\\*\\*bold\\*\\*` / `\\- item`) form as wrong -- not merely mention
    Markdown in the abstract.
    """
    for text in (TEAM_MANAGER_INSTRUCTION, TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION):
        assert "**bold**" in text or "**Decisions**" in text
        assert "always wrong" in text
        assert "\\*\\*" in text  # the escaped form is shown, as the counter-example


def test_d_markdown_contract_does_not_mandate_structure_for_every_response() -> None:
    """Short, ordinary replies must stay simple text -- the contract must
    not read as "always use headings/lists."
    """
    assert "simple conversational text" in TEAM_MANAGER_INSTRUCTION
    assert "not headings or a list just because they are available" in TEAM_MANAGER_INSTRUCTION

    assert "short plain-text reply is enough" in TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION
    assert "do not force headings or a list" in TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION


def test_e_markdown_contract_never_instructs_global_unescaping() -> None:
    """Section 6/9's hard constraint: the model must never be told to
    strip/remove/unescape backslashes wholesale -- that would corrupt
    legitimate technical content (Windows paths, regex, code). Assert the
    absence of that class of instruction, and the explicit presence of the
    opposite guidance (leave genuine backslashes alone).
    """
    forbidden_phrases = (
        "remove backslashes",
        "unescape all",
        "strip escape characters",
        "strip all backslashes",
        "remove all backslash",
    )
    for text in (TEAM_MANAGER_INSTRUCTION, TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION):
        lowered = text.lower()
        for phrase in forbidden_phrases:
            assert phrase not in lowered

    assert "leave those exactly as given" in TEAM_MANAGER_INSTRUCTION
    assert "must stay exactly as given" in TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION
