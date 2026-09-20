"""Tests for backend/agents/team_manager/case_context.py -- the
`InstructionProvider` mechanism that injects Case context (instruction
sections 21-23), exercised against REAL ADK objects
(`InvocationContext`/`ReadonlyContext`/`InMemorySessionService`), the
same rigor Phase 4C/earlier milestones already established for
`{var?}` state templating.
"""
from __future__ import annotations

import pytest
from google.adk.agents.invocation_context import InvocationContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.sessions import InMemorySessionService

import backend.agents.team_manager.case_context as case_context_module
from backend.agents.team_manager.agent import team_manager
from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY
from backend.cases.service import CaseService

ALICE = "alice"
BOB = "bob"


@pytest.fixture()
def patched_case_service(monkeypatch: pytest.MonkeyPatch) -> CaseService:
    service = CaseService()
    monkeypatch.setattr(case_context_module, "get_case_service", lambda: service)
    return service


async def _readonly_context(user_id: str, state: dict) -> ReadonlyContext:
    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="t", user_id=user_id, state=state)
    invocation_context = InvocationContext(
        session_service=session_service, invocation_id="test-inv", agent=team_manager, session=session
    )
    return ReadonlyContext(invocation_context)


@pytest.mark.asyncio
async def test_instruction_is_unchanged_when_no_case_is_linked(patched_case_service: CaseService) -> None:
    from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION

    ctx = await _readonly_context(ALICE, state={})
    rendered = await team_manager.canonical_instruction(ctx)

    assert "ACTIVE CASE CONTEXT:" not in rendered[0]
    # The static part is rendered identically to before this milestone --
    # same `inject_session_state` templating, just via the provider now.
    assert rendered[0].startswith(TEAM_MANAGER_INSTRUCTION.split("{selected_teams_chat_topic?}")[0])


@pytest.mark.asyncio
async def test_instruction_includes_case_context_when_linked(patched_case_service: CaseService) -> None:
    case = await patched_case_service.create_case(ALICE, "Packet loss", "Loss on core router.")
    await patched_case_service.add_user_context_item(ALICE, case.case_id, "observation", "Loss started 14:00 UTC.")

    ctx = await _readonly_context(ALICE, state={ACTIVE_CASE_ID_STATE_KEY: case.case_id})
    rendered = await team_manager.canonical_instruction(ctx)

    assert "ACTIVE CASE CONTEXT:" in rendered[0]
    assert "Packet loss" in rendered[0]
    assert "Loss started 14:00 UTC." in rendered[0]


@pytest.mark.asyncio
async def test_instruction_provider_uses_bypass_state_injection(patched_case_service: CaseService) -> None:
    """Confirms `LlmAgent.canonical_instruction` recognizes team_manager's
    instruction as an `InstructionProvider` (not a plain string) -- the
    documented ADK signal for that is `bypass_state_injection=True`.
    """
    ctx = await _readonly_context(ALICE, state={})
    rendered = await team_manager.canonical_instruction(ctx)
    assert rendered[1] is True


@pytest.mark.asyncio
async def test_fresh_context_every_call_reflects_a_change_between_calls(patched_case_service: CaseService) -> None:
    """No caching: calling the provider twice, with a Case update in
    between, must reflect the update on the second call -- this is what
    makes "fresh context every turn" (instruction section 21) true.
    """
    case = await patched_case_service.create_case(ALICE, "Title", "Problem")
    ctx = await _readonly_context(ALICE, state={ACTIVE_CASE_ID_STATE_KEY: case.case_id})

    first = await team_manager.canonical_instruction(ctx)
    assert "NEW_ITEM_MARKER" not in first[0]

    await patched_case_service.add_user_context_item(ALICE, case.case_id, "observation", "NEW_ITEM_MARKER")

    second = await team_manager.canonical_instruction(ctx)
    assert "NEW_ITEM_MARKER" in second[0]


@pytest.mark.asyncio
async def test_stale_active_case_hint_never_grants_access(patched_case_service: CaseService) -> None:
    """The `active_case_id` state hint is not authorization -- if the
    session's user is not (or is no longer) a member of that case, the
    provider silently omits Case context rather than leaking it or
    erroring the turn.
    """
    case = await patched_case_service.create_case(ALICE, "Alice's case", "Problem")
    # BOB's session has a hint pointing at a case bob is not a member of.
    ctx = await _readonly_context(BOB, state={ACTIVE_CASE_ID_STATE_KEY: case.case_id})

    rendered = await team_manager.canonical_instruction(ctx)

    assert "ACTIVE CASE CONTEXT:" not in rendered[0]
    assert "Alice's case" not in rendered[0]


@pytest.mark.asyncio
async def test_stale_hint_for_a_case_that_no_longer_exists_is_handled_safely(
    patched_case_service: CaseService,
) -> None:
    ctx = await _readonly_context(ALICE, state={ACTIVE_CASE_ID_STATE_KEY: "case-that-was-deleted"})
    rendered = await team_manager.canonical_instruction(ctx)
    assert "ACTIVE CASE CONTEXT:" not in rendered[0]


@pytest.mark.asyncio
async def test_context_never_persisted_into_session_state(patched_case_service: CaseService) -> None:
    """Rendering the instruction must never write anything back into the
    session -- confirmed by checking the session's state dict is
    unchanged after rendering (instruction section 22).
    """
    case = await patched_case_service.create_case(ALICE, "Title", "Problem")
    await patched_case_service.add_user_context_item(ALICE, case.case_id, "observation", "Some content")

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="t", user_id=ALICE, state={ACTIVE_CASE_ID_STATE_KEY: case.case_id}
    )
    invocation_context = InvocationContext(
        session_service=session_service, invocation_id="test-inv", agent=team_manager, session=session
    )
    state_before = dict(session.state)

    await team_manager.canonical_instruction(ReadonlyContext(invocation_context))

    refreshed = await session_service.get_session(app_name="t", user_id=ALICE, session_id=session.id)
    assert dict(refreshed.state) == state_before


@pytest.mark.asyncio
async def test_instruction_includes_troubleshooting_state_when_present(patched_case_service: CaseService) -> None:
    case = await patched_case_service.create_case(ALICE, "Cell Outage", "Loss on cell 42.")
    ts_state = {
        "fault_id": "FAULT-CELL-42",
        "status": "investigating",
        "symptom_summary": "Loss on cell 42",
        "working_hypothesis": "Loose RF jumper",
        "competing_hypotheses": ["Transceiver fault"],
    }
    ctx = await _readonly_context(
        ALICE,
        state={
            ACTIVE_CASE_ID_STATE_KEY: case.case_id,
            "troubleshooting_state": ts_state,
        },
    )
    rendered = await team_manager.canonical_instruction(ctx)

    assert "ACTIVE CASE CONTEXT:" in rendered[0]
    assert "Active Fault: FAULT-CELL-42 (Status: investigating)" in rendered[0]
    assert "Working Hypothesis: Loose RF jumper" in rendered[0]
    assert "Competing Hypotheses: Transceiver fault" in rendered[0]


def test_case_context_module_never_calls_append_event() -> None:
    """Structural guarantee alongside the functional one above -- this
    module has no code path that could persist anything. Checks for an
    actual call site (`name(`), not a docstring mention explaining that
    the mechanism is deliberately absent (mirrors the `_mentions_as_code`
    pattern used elsewhere in this suite).
    """
    import inspect

    source = inspect.getsource(case_context_module)
    assert "append_event(" not in source
    assert "persist_state_delta(" not in source


@pytest.mark.asyncio
async def test_roster_reflects_registered_tools_strictly(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock
    from backend.agents.team_manager.case_context import make_team_manager_instruction_provider

    # Even if feature flag is enabled in env, if tool is not registered, it must not appear!
    monkeypatch.setenv("SLOPANOC_TECHNICAL_AUTHORITY_ENABLED", "true")
    monkeypatch.setenv("SLOPANOC_PROBLEM_MANAGER_ENABLED", "true")
    monkeypatch.setenv("SLOPANOC_AUTOMATED_OPERATIONS_ENABLED", "true")

    im_tool = MagicMock()
    im_tool.name = "incident_manager"
    baseline_tools = [im_tool]

    provider = make_team_manager_instruction_provider(baseline_tools)
    ctx = await _readonly_context(ALICE, state={})
    instruction = await provider(ctx)

    assert "ACTIVE AGENT ROSTER & SPECIALISTS:" in instruction
    assert "`incident_manager`" in instruction
    assert "`technical_authority_engineer`" not in instruction
    assert "`problem_manager`" not in instruction
    assert "`automated_operations_engineer`" not in instruction
    assert "TECHNICAL AUTHORITY ENGINEER DELEGATION" not in instruction
    assert "PROBLEM MANAGER DELEGATION" not in instruction
    assert "AUTOMATED OPERATIONS ENGINEER DELEGATION" not in instruction

    # Now add TAE tool only
    tae_tool = MagicMock()
    tae_tool.name = "technical_authority_engineer"
    tae_provider = make_team_manager_instruction_provider([im_tool, tae_tool])
    tae_instruction = await tae_provider(ctx)

    assert "`technical_authority_engineer`" in tae_instruction
    assert "TECHNICAL AUTHORITY ENGINEER DELEGATION" in tae_instruction
    assert "`problem_manager`" not in tae_instruction
    assert "`automated_operations_engineer`" not in tae_instruction
    assert "PROBLEM MANAGER DELEGATION" not in tae_instruction
    assert "AUTOMATED OPERATIONS ENGINEER DELEGATION" not in tae_instruction

    # Now add PM tool only
    pm_tool = MagicMock()
    pm_tool.name = "problem_manager"
    pm_provider = make_team_manager_instruction_provider([im_tool, pm_tool])
    pm_instruction = await pm_provider(ctx)

    assert "`problem_manager`" in pm_instruction
    assert "PROBLEM MANAGER DELEGATION" in pm_instruction
    assert "`technical_authority_engineer`" not in pm_instruction
    assert "`automated_operations_engineer`" not in pm_instruction
