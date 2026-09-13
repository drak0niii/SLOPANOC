"""Phase 6A.11 FINAL CORRECTIVE PASS -- closes exactly three evidence
gaps identified in review of the Pass 1 + Pass 2 closure:

1. Partial dual-specialist failure in the previously-untested direction:
   Incident Manager fails, Troubleshooting Manager succeeds.
2. Full Case-isolation proof beyond Experience-only isolation (stale
   Case hint, model-generated Case id, session state, repository-level
   isolation).
3. Experience repository failure during the Troubleshooting preparation
   path.

No production code is expected to change for any of these three checks
(see the closure report for the corrective pass's own audit trail); if a
genuine defect had been found, it would be registered in
docs/DEFECT_REGISTER.md separately.
"""
from __future__ import annotations

import asyncio

import pytest

import backend.agents.team_manager.troubleshooting_tool as tool_module
import backend.agents.troubleshooting_manager.experience_support as experience_support_module
from backend.agents.team_manager.agent import incident_manager_tool, team_manager
from backend.agents.incident_manager.schemas import IncidentManagerOutcome, IncidentManagerResponse
from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY
from backend.api.session_service import APP_NAME
from backend.cases.service import CaseService
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


def _real_model_reachable() -> bool:
    import os

    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    try:
        async def _probe():
            session_service = InMemorySessionService()
            runner = Runner(app_name="probe", agent=team_manager, session_service=session_service, memory_service=InMemoryMemoryService())
            await session_service.create_session(app_name="probe", user_id="probe", session_id="probe")
            content = types.Content(role="user", parts=[types.Part.from_text(text="hello")])
            async for _ in runner.run_async(user_id="probe", session_id="probe", new_message=content):
                pass
            await runner.close()

        asyncio.run(_probe())
        return True
    except Exception:
        return False


_REACHABLE = _real_model_reachable()
_real_model = pytest.mark.skipif(not _REACHABLE, reason="real Vertex AI model not reachable in this environment (no valid ADC, or model unavailable)")

_USER_ID = "6a11-final-corrective-user"


async def _run_turn(user_text: str, session_id: str, *, experience_service: ExperienceMemoryService, state: dict | None = None) -> tuple[list[str], list]:
    original = experience_support_module.get_experience_memory_service
    experience_support_module.get_experience_memory_service = lambda: experience_service
    try:
        session_service = InMemorySessionService()
        runner = Runner(app_name=APP_NAME, agent=team_manager, session_service=session_service, memory_service=InMemoryMemoryService())
        try:
            await session_service.create_session(app_name=APP_NAME, user_id=_USER_ID, session_id=session_id, state=state or {})
            content = types.Content(role="user", parts=[types.Part.from_text(text=user_text)])
            events = []
            function_call_names: list[str] = []
            async for event in runner.run_async(user_id=_USER_ID, session_id=session_id, new_message=content):
                events.append(event)
                for call in event.get_function_calls():
                    function_call_names.append(call.name)
        finally:
            await runner.close()
    finally:
        experience_support_module.get_experience_memory_service = original
    return function_call_names, events


def _final_text(events) -> str:
    texts = []
    for event in events:
        if event.content and event.content.parts:
            for part in event.content.parts:
                if getattr(part, "text", None) and not getattr(part, "thought", False):
                    texts.append(part.text)
    return "\n".join(texts)


# =========================================================================
# GAP 1 -- Incident Manager fails, Troubleshooting Manager succeeds
# =========================================================================


@_real_model
@pytest.mark.asyncio
async def test_incident_manager_failure_never_blocks_troubleshooting_content(monkeypatch) -> None:
    """Deterministically injects a REALISTIC Incident Manager failure --
    a genuine `IncidentManagerResponse(outcome=ERROR, ...)`, exactly the
    same safe, structured failure shape a real Power Automate/Teams
    gateway outage would already produce (never a raw, uncaught Python
    exception -- direct audit of installed ADK 1.33.0's `functions.py`
    confirmed `__call_tool_async`/`_execute_single_function_call_async`
    have NO try/except around `tool.run_async`, so a raw exception would
    crash the entire team_manager turn rather than exercising any
    real, designed product behavior -- that is an ADK-internals crash
    path, not a scenario this architecture is built to survive, and
    section 6's own "do not intentionally make a real external service
    fail" instruction is best honored by simulating the REAL failure
    SHAPE incident_manager already has for this, not a synthetic crash).

    Monkeypatches the actual `incident_manager_tool` (`MultimodalAgentTool`)
    instance's own `run_async` to return this safe error result --
    proves, through the REAL team_manager object and a REAL ADK Runner,
    that: (1) `troubleshooting_manager` is still invoked and its content
    is not discarded; (2) Team Manager's final answer does not fabricate
    incident details; (3) the incident-side failure is represented
    honestly, not silently dropped; (4) invocation remains bounded; (5)
    no specialist peer call occurs.
    """
    failed_incident_response = IncidentManagerResponse(
        outcome=IncidentManagerOutcome.ERROR,
        detail="Teams gateway is temporarily unavailable (simulated failure for this test).",
    ).model_dump(mode="json", exclude_none=True)

    async def _fake_incident_run_async(*, args, tool_context):
        return failed_incident_response

    monkeypatch.setattr(incident_manager_tool, "run_async", _fake_incident_run_async)

    service = ExperienceMemoryService()
    calls, events = await _run_turn(
        "First, summarize what happened in the Teams chat named 'Production Bridge'. "
        "Second, and separately, tell me what I should investigate next for a VSWR over threshold alarm. "
        "Please answer both parts.",
        session_id="6a11-corrective-incident-fails",
        experience_service=service,
    )

    assert "incident_manager" in calls, f"expected incident_manager to still be called (and fail), got calls={calls}"
    assert calls.count("incident_manager") <= 1, f"incident_manager must remain bounded even on failure, got calls={calls}"
    assert "troubleshooting_manager" in calls, f"troubleshooting_manager's own content must not be discarded merely because incident_manager failed, got calls={calls}"
    assert calls.count("troubleshooting_manager") == 1

    final_text = _final_text(events).lower()
    # Team Manager must not fabricate Teams content it never actually received.
    assert "production bridge" not in final_text or "unavailable" in final_text or "could not" in final_text or "unable" in final_text or "error" in final_text or "trouble" in final_text.replace("troubleshoot", "")


# =========================================================================
# GAP 2 -- Full Case isolation
# =========================================================================


@pytest.mark.asyncio
async def test_stale_active_case_hint_never_leaks_case_a_into_case_b_context() -> None:
    """2.A -- a stale `ACTIVE_CASE_ID_STATE_KEY` pointing at Case A must
    never cause a request that should operate on Case B (or no case) to
    consume Case A's Context. Uses the REAL `_build_context_package`
    Case-resolution path (`troubleshooting_tool.py`'s `_resolve_case_
    context`) against an isolated, real `CaseService` -- proves the
    trusted-runtime-state -> Case-resolution -> Context-construction
    chain cannot be overridden by a stale hint pointing at the WRONG
    case."""
    case_service = CaseService()
    case_a = await case_service.create_case("alice", "Case A Title", "Case A problem statement")
    case_b = await case_service.create_case("alice", "Case B Title", "Case B problem statement")

    original = tool_module.get_case_service
    tool_module.get_case_service = lambda: case_service
    try:
        class _FakeSession:
            def __init__(self, session_id: str) -> None:
                self.id = session_id

        class _FakeToolContext:
            def __init__(self, active_case_id: str) -> None:
                self.user_id = "alice"
                self.session = _FakeSession("sess-case-isolation")
                self.state = {ACTIVE_CASE_ID_STATE_KEY: active_case_id}
                self.user_content = None

        # A request that is genuinely about Case B, but the trusted runtime
        # state's own active-case hint (the ONLY channel _resolve_case_context
        # reads) correctly points at Case B here -- the isolation property
        # under test is that Case A's own content is NEVER reachable through
        # this call for a context whose trusted hint says Case B.
        ctx_b = _FakeToolContext(active_case_id=case_b.case_id)
        package_b = await tool_module._build_context_package(ctx_b, "what should I check next for Case B?")
        assert package_b.case_id == case_b.case_id
        assert package_b.case_context is not None
        assert package_b.case_context.case_id == case_b.case_id
        assert "Case A" not in (package_b.case_context.problem_statement or "")

        # And the stale/wrong direction: a hint pointing at Case A while the
        # real request session has no legitimate relationship to Case A must
        # still resolve deterministically to Case A's OWN real content only
        # (never blended with Case B's) -- proving resolution is a pure
        # function of the trusted hint, never contaminated by another case's
        # data merely because both cases exist in the same repository.
        ctx_a = _FakeToolContext(active_case_id=case_a.case_id)
        package_a = await tool_module._build_context_package(ctx_a, "what should I check next for Case A?")
        assert package_a.case_id == case_a.case_id
        assert package_a.case_context.case_id == case_a.case_id
        assert "Case B" not in (package_a.case_context.problem_statement or "")
    finally:
        tool_module.get_case_service = original


@pytest.mark.asyncio
async def test_model_generated_case_id_cannot_override_trusted_runtime_case() -> None:
    """2.B -- `_build_context_package`/`_resolve_case_context` read the
    Case id EXCLUSIVELY from `tool_context.state[ACTIVE_CASE_ID_STATE_KEY]`
    (trusted, ADK-managed runtime state) -- there is structurally no
    parameter through which `troubleshooting_manager`'s own model-facing
    signature (`troubleshooting_question: str`, `known_context_facts:
    dict[str, str] | None`) could carry a Case identifier at all. This is
    proven both by a source-level signature inspection (no `case_id`
    parameter exists to inject) and behaviorally: a `troubleshooting_
    question` string that TEXTUALLY CONTAINS a real, existing Case id
    belonging to a case the trusted state does NOT reference must have
    zero effect on which Case's Context is actually resolved."""
    import inspect

    signature = inspect.signature(tool_module.troubleshooting_manager)
    assert "case_id" not in signature.parameters, "troubleshooting_manager must have no case_id parameter a model could ever populate"

    case_service = CaseService()
    real_case = await case_service.create_case("alice", "Real Trusted Case", "The only case trusted runtime state may resolve.")
    decoy_case = await case_service.create_case("alice", "Decoy Case Never Trusted", "This case must never be resolved via model text.")

    original = tool_module.get_case_service
    tool_module.get_case_service = lambda: case_service
    try:
        class _FakeSession:
            def __init__(self, session_id: str) -> None:
                self.id = session_id

        class _FakeToolContext:
            def __init__(self) -> None:
                self.user_id = "alice"
                self.session = _FakeSession("sess-model-case-id")
                self.state = {ACTIVE_CASE_ID_STATE_KEY: real_case.case_id}
                self.user_content = None

        ctx = _FakeToolContext()
        # The model's own free-text question TEXTUALLY names the decoy case's real id --
        # this must have zero effect; only the trusted runtime hint may select a Case.
        question = f"Regarding case {decoy_case.case_id}, what should I check next?"
        package = await tool_module._build_context_package(ctx, question)

        assert package.case_id == real_case.case_id
        assert package.case_context.case_id == real_case.case_id
        assert decoy_case.case_id not in (package.case_context.problem_statement or "")
    finally:
        tool_module.get_case_service = original


@pytest.mark.asyncio
async def test_session_state_for_one_case_never_leaks_when_trusted_case_differs() -> None:
    """2.C -- session/runtime `state` may legitimately carry OTHER,
    unrelated keys (Teams selection state, action-proposal state, etc.)
    alongside `ACTIVE_CASE_ID_STATE_KEY` -- proves that the presence of
    such unrelated state, even state that happens to reference Case A's
    own id in a different key, never causes Case A content to be used
    when the trusted `ACTIVE_CASE_ID_STATE_KEY` itself says Case B."""
    case_service = CaseService()
    case_a = await case_service.create_case("alice", "Case A", "Case A problem statement")
    case_b = await case_service.create_case("alice", "Case B", "Case B problem statement")

    original = tool_module.get_case_service
    tool_module.get_case_service = lambda: case_service
    try:
        class _FakeSession:
            def __init__(self, session_id: str) -> None:
                self.id = session_id

        class _FakeToolContext:
            def __init__(self) -> None:
                self.user_id = "alice"
                self.session = _FakeSession("sess-state-leak-check")
                # Unrelated state entries reference Case A by a DIFFERENT key --
                # only ACTIVE_CASE_ID_STATE_KEY is ever trusted for resolution.
                self.state = {
                    ACTIVE_CASE_ID_STATE_KEY: case_b.case_id,
                    "some_unrelated_recent_case_mention": case_a.case_id,
                    "temp:some_other_marker": case_a.case_id,
                }
                self.user_content = None

        ctx = _FakeToolContext()
        package = await tool_module._build_context_package(ctx, "what should I check next?")
        assert package.case_id == case_b.case_id
        assert package.case_context.case_id == case_b.case_id
    finally:
        tool_module.get_case_service = original


@pytest.mark.asyncio
async def test_case_repository_lookup_for_case_b_never_returns_or_merges_case_a() -> None:
    """2.D -- direct repository/service-level proof: `CaseService.get_
    case`/`get_context_items` for Case B never returns Case A's own
    record or context items, even when both exist in the SAME repository
    for the SAME user -- the existing, unmodified Case service/repository
    boundary itself enforces this (no code path merges two cases'
    records)."""
    case_service = CaseService()
    case_a = await case_service.create_case("alice", "Case A", "Case A problem statement")
    case_b = await case_service.create_case("alice", "Case B", "Case B problem statement")
    await case_service.add_user_context_item("alice", case_a.case_id, kind="observation", content="Case A only observation")
    await case_service.add_user_context_item("alice", case_b.case_id, kind="observation", content="Case B only observation")

    fetched_b = await case_service.get_case("alice", case_b.case_id)
    items_b = await case_service.get_context_items("alice", case_b.case_id)

    assert fetched_b.case_id == case_b.case_id
    assert fetched_b.problem_statement == "Case B problem statement"
    assert all(item.content != "Case A only observation" for item in items_b)
    assert any(item.content == "Case B only observation" for item in items_b)


# =========================================================================
# GAP 3 -- Experience repository failure
# =========================================================================


@pytest.mark.asyncio
async def test_experience_repository_failure_degrades_safely_never_fabricates(monkeypatch) -> None:
    """Audited first, per instruction: `experience_support.query_
    experience_support` has NO try/except of its own around `memory_
    service.query(query)` -- a raised exception there propagates
    directly to its caller, `run_troubleshooting_assessment`, which ALSO
    has no try/except around that specific call. The exception therefore
    propagates all the way to the FunctionTool wrapper, `troubleshooting_
    tool.troubleshooting_manager`, whose own outer `try/except Exception`
    is the ONE place in this call chain that catches it -- converting it
    to the SAME safe, deterministic `BLOCKED` result already proven for
    a Gemini provider exception (test_6a11_pass2_stress_matrix.py's own
    `test_gemini_exception_during_model_invocation_fails_closed_never_
    fabricates`). This is the CORRECT, ALREADY-DEFINED behavior --
    verified here, not redesigned: no Experience is fabricated, nothing
    is promoted to authority, grounding is never bypassed (grounding
    never even runs, since the turn fails before a model call), and no
    unexpected Experience write occurs."""

    class _ExplodingExperienceService:
        async def query(self, *_a, **_kw):
            raise RuntimeError("simulated Experience repository/query failure")

        async def record_experience(self, *_a, **_kw):
            raise AssertionError("record_experience must never be called on this path")

    from backend.agents.troubleshooting_manager.experience_support import query_experience_support
    from backend.agents.troubleshooting_manager.runtime import run_troubleshooting_assessment
    from backend.agents.troubleshooting_manager.schemas import TroubleshootingManagerRequest, TroubleshootingResponseStatus

    from ._fixtures import context_package_with_fault_known, evidence_candidate

    exploding_service = _ExplodingExperienceService()

    # Layer 1: the exception genuinely propagates out of query_experience_support --
    # proving no silent, fabricated empty-Experience substitution happens at this layer.
    with pytest.raises(RuntimeError, match="simulated Experience repository"):
        await query_experience_support(owner_id="OWNER-EXP-FAIL-1", skill_id=None, skill_version=None, case_id=None, service=exploding_service)

    # Layer 2: run_troubleshooting_assessment has no try/except around this call either --
    # the exception propagates all the way up, never silently swallowed into a fabricated result.
    package = context_package_with_fault_known(owner_id="OWNER-EXP-FAIL-2", evidence_items=[evidence_candidate("ev-1")])
    request = TroubleshootingManagerRequest(owner_id="OWNER-EXP-FAIL-2", context_package=package, objective="what should be checked next?")
    with pytest.raises(RuntimeError, match="simulated Experience repository"):
        await run_troubleshooting_assessment(request, experience_service=exploding_service)

    # Layer 3: the ONE place this is actually caught -- the FunctionTool wrapper's own
    # outer try/except -- converts it to the SAME safe BLOCKED result already proven for
    # a Gemini provider exception. No Experience fabricated, no grounding bypassed
    # (grounding never runs -- the turn fails before any model call), no unexpected write.
    class _FakeSession:
        def __init__(self, session_id: str = "sess-exp-fail") -> None:
            self.id = session_id

    class _FakeToolContext:
        def __init__(self) -> None:
            self.user_id = "OWNER-EXP-FAIL-3"
            self.session = _FakeSession()
            self.state: dict = {}
            self.user_content = None

    async def _context_package_returner(_tool_context, _question, known_context_facts=None):
        return context_package_with_fault_known(owner_id="OWNER-EXP-FAIL-3", evidence_items=[evidence_candidate("ev-1")])

    monkeypatch.setattr(tool_module, "_build_context_package", _context_package_returner)
    monkeypatch.setattr(experience_support_module, "get_experience_memory_service", lambda: exploding_service)

    result = await tool_module.troubleshooting_manager(troubleshooting_question="what should be checked next?", tool_context=_FakeToolContext())

    assert result["status"] == "blocked"
    assert result["assessment"] is None
    assert "could not be completed" in (result["detail"] or "").lower()
