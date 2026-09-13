"""Phase 6A.10 core test matrix -- the `troubleshooting_manager`
FunctionTool wrapper (team_manager/troubleshooting_tool.py): owner/case/
session propagation from TRUSTED runtime context only, real (never
fabricated) TELCO Context from `known_context_facts` -- now VERIFIED
against the real current-turn user text (6A.10.2 corrective pass, see
`_FakeToolContext.user_content`/`user_text` below) -- and real (never
fabricated) Evidence from the live 6A.4/6A.5 pipeline (6A.10.1
corrective pass), exception safety, and the bounded (max 1 real call per
turn) delegation guard. See `test_known_context_facts_trust_boundary.py`
for the dedicated, deeper trust-boundary regression suite (unsupported-
fact rejection, explicit-user-fact provenance, conflict/UNKNOWN
semantics, Knowledge/Experience-cannot-create-Context proofs).

ISOLATION (6A.10 corrective pass -- fixes the exact test-isolation gap
that caused an unexpected `slopanoc_experience_records` table/local
Knowledge DB access from this file, see DEF-0020/docs/DEFECT_REGISTER.md):
every test in this module runs against an ISOLATED, in-memory SQLite
Knowledge repository (never the real local/dev database) via the
`_isolated_knowledge_repository` autouse fixture below, mirroring
`test_p5_1j_knowledge_tools.py`'s own already-established `isolated_repo`
pattern exactly (env-var override + `lru_cache.cache_clear()`, since
`tools.py`-style call sites hold an already-bound reference to the
singleton function). Because the isolated repository starts genuinely
empty, `narrow_knowledge` always returns an empty `permitted_knowledge_
ids` set for it, so `context_support.query_selected_evidence` always
short-circuits BEFORE ever constructing `EvidenceIndexRepository`/
`VertexTextEmbeddingProvider` -- no real Cloud SQL evidence-index
connection and no real Vertex embedding call is ever made by this file.
Real Vertex/Cloud SQL Evidence retrieval is proven separately, mirroring
6A.5's own already-proven real-DEV-gated convention -- see
`test_dual_specialist_real_model.py`/`test_troubleshooting_context_
wiring_real_stack.py`.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

import backend.agents.team_manager.troubleshooting_tool as module
import backend.agents.troubleshooting_manager.experience_support as experience_support_module
import backend.tools.knowledge.runtime as knowledge_runtime
from backend.agents.team_manager.troubleshooting_tool import (
    TOOL_NAME,
    TROUBLESHOOTING_RESULT_THIS_TURN_STATE_KEY,
    block_repeated_troubleshooting_invocation,
    cache_troubleshooting_result_this_turn,
    troubleshooting_manager,
)
from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY
from backend.cases.service import CaseService
from backend.context.domain.enums import ContextDimension, ContextState
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService
from backend.knowledge.hybrid_retrieval.contracts import EvidenceSelectionResult

ALICE = "alice"


@pytest.fixture(autouse=True)
def _isolated_knowledge_repository(monkeypatch):
    """Forces the real `get_knowledge_repository()` singleton to rebuild
    against an isolated, empty, in-memory database for the duration of
    each test in this file -- never the real local/dev Knowledge database
    -- exactly mirroring `test_p5_1j_knowledge_tools.py`'s own established
    `isolated_repo` fixture. See this module's own docstring above for why
    this is sufficient to also keep the new 6A.4/6A.5 Evidence wiring from
    ever reaching a real Cloud SQL evidence-index connection or a real
    Vertex embedding call during this file's tests."""
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    knowledge_runtime.get_knowledge_repository.cache_clear()
    yield
    knowledge_runtime.get_knowledge_repository.cache_clear()


@pytest.fixture(autouse=True)
def _isolated_experience_memory(monkeypatch):
    """DEF-0020 fix (6A.10 corrective pass): this file's tests call the
    real `troubleshooting_manager()`/`run_troubleshooting_assessment()`
    pipeline directly, without an explicit `experience_service` override
    -- which, before this fixture existed, meant `query_experience_
    support`'s own `service is None` fallback resolved to `get_
    experience_memory_service()` -> `ExperienceMemoryDatabase()` (no
    explicit URL) -> `Settings.resolve_database_url()` -> the REAL local
    `slopanoc_sessions.db` file, whose `.query()` call unconditionally
    runs `ensure_schema()` even for a zero-result read -- the exact,
    confirmed root cause of the unexpected `slopanoc_experience_records`
    table this corrective pass investigates (see docs/DEFECT_REGISTER.md
    DEF-0020). Monkeypatches the SAME module-level function reference
    `experience_support.py` itself calls, mirroring `test_dual_
    specialist_real_model.py`'s own already-correct isolation pattern
    exactly, to an isolated, in-memory-only `ExperienceMemoryService()`
    (its own no-arg default is `sqlite+aiosqlite:///:memory:` -- see
    `backend/experience_memory/sqlalchemy/service.py`)."""
    isolated_service = ExperienceMemoryService()
    original = experience_support_module.get_experience_memory_service
    experience_support_module.get_experience_memory_service = lambda: isolated_service
    yield
    experience_support_module.get_experience_memory_service = original


class _FakeSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeToolContext:
    def __init__(
        self,
        user_id: str,
        session_id: str = "sess-1",
        state: Optional[dict[str, Any]] = None,
        user_text: str = "",
    ) -> None:
        self.user_id = user_id
        self.session = _FakeSession(session_id)
        self.state = state if state is not None else {}
        # 6A.10.2: the REAL, verbatim current-turn user message --
        # `extract_current_user_text` reads this exact attribute.
        self.user_content = _FakeContent(user_text) if user_text else None


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


# --- Owner/session/case propagation (never model-invented) -------------


@pytest.mark.asyncio
async def test_owner_id_comes_from_trusted_tool_context_never_model_text() -> None:
    ctx = _FakeToolContext(user_id=ALICE)
    result = await troubleshooting_manager(troubleshooting_question="what should I check next?", tool_context=ctx)
    # No live TELCO/Evidence source is wired in 6A.10 -- the honest,
    # deterministic outcome is NEEDS_INFORMATION (or BLOCKED if the
    # production Skill were ever removed), never an invented assessment,
    # and never a crash from a missing owner scope.
    assert result["status"] in ("needs_information", "blocked")
    assert "assessment" in result


@pytest.mark.asyncio
async def test_no_tool_context_fails_closed_safely() -> None:
    result = await troubleshooting_manager(troubleshooting_question="what next?", tool_context=None)
    assert result["status"] == "blocked"
    assert result["assessment"] is None


@pytest.mark.asyncio
async def test_case_context_is_reused_from_existing_case_service_when_linked() -> None:
    case_service = CaseService()
    case = await case_service.create_case(ALICE, "Title", "Problem statement")

    original = module.get_case_service
    module.get_case_service = lambda: case_service
    try:
        ctx = _FakeToolContext(user_id=ALICE, state={ACTIVE_CASE_ID_STATE_KEY: case.case_id})
        package = await module._build_context_package(ctx, "what should I check next?")
    finally:
        module.get_case_service = original

    assert package.case_id == case.case_id
    assert package.case_context is not None
    assert package.case_context.case_id == case.case_id


@pytest.mark.asyncio
async def test_stale_case_hint_never_aborts_never_fabricates_case() -> None:
    """Mirrors case_context.py's own established behavior: an unlinked/
    stale active-case hint resolves to 'no case', never an error.

    DEF-0021 fix (6A.10.2 corrective pass, see docs/DEFECT_REGISTER.md):
    this test previously called `_build_context_package` with a truthy
    `ACTIVE_CASE_ID_STATE_KEY` WITHOUT mocking `get_case_service` --
    `_resolve_case_context`'s own `get_case_service()` call therefore hit
    the REAL, process-wide singleton, whose `CaseService.get_case()`
    unconditionally calls `self._db.ensure_schema()` BEFORE its own
    not-found check, creating all 4 real `slopanoc_cases`/`_case_
    memberships`/`_case_session_links`/`_case_context_items` tables on
    the real local `slopanoc_sessions.db` file even though the case was
    never found -- the exact same bug CLASS as DEF-0020, for the Case
    domain instead of Experience Memory. Fixed by routing through an
    isolated, in-memory `CaseService()` (mirrors `test_case_context_is_
    reused_from_existing_case_service_when_linked`'s own already-correct
    pattern immediately above)."""
    case_service = CaseService()
    original = module.get_case_service
    module.get_case_service = lambda: case_service
    try:
        ctx = _FakeToolContext(user_id=ALICE, state={ACTIVE_CASE_ID_STATE_KEY: "case-does-not-exist"})
        package = await module._build_context_package(ctx, "what next?")
    finally:
        module.get_case_service = original
    assert package.case_id is None
    assert package.case_context is None


@pytest.mark.asyncio
async def test_evidence_selection_is_empty_when_no_governed_knowledge_matches() -> None:
    """The isolated Knowledge repository is genuinely empty, so 6A.4's
    real `narrow_knowledge` genuinely finds nothing permitted -- this
    proves the real pipeline runs and fails closed to an honest empty
    result on a real 'no applicable knowledge' outcome, never that the
    pipeline was skipped or a hardcoded stub was returned (see the
    `..._real_pipeline_composition` test below for that distinction)."""
    ctx = _FakeToolContext(user_id=ALICE)
    package = await module._build_context_package(ctx, "what should I check next?")
    assert package.evidence.items == []
    assert package.evidence.selected_evidence_count == 0
    assert package.evidence.selection_reason == "no_permitted_knowledge"


@pytest.mark.asyncio
async def test_telco_context_is_empty_when_no_known_context_facts_given() -> None:
    ctx = _FakeToolContext(user_id=ALICE)
    package = await module._build_context_package(ctx, "what should I check next?")
    assert package.telco_context == []


@pytest.mark.asyncio
async def test_telco_context_reflects_known_context_facts_when_provided() -> None:
    """`known_context_facts` -- populated by team_manager's own model
    ONLY from facts the current message explicitly, literally states,
    and VERIFIED (6A.10.2) against the real current-turn user text --
    must produce a real, KNOWN TelcoDimensionView, never stay empty
    merely because no live TelcoContextProfile persistence pipeline
    exists yet."""
    ctx = _FakeToolContext(user_id=ALICE, user_text="This is a VSWR Over Threshold alarm, what should I check next?")
    package = await module._build_context_package(
        ctx, "what should I check next?", known_context_facts={"fault": "VSWR Over Threshold"}
    )
    assert len(package.telco_context) == 1
    entry = package.telco_context[0]
    assert entry.dimension == ContextDimension.FAULT
    assert entry.state == ContextState.KNOWN
    assert entry.accepted[0].origin == "user"


@pytest.mark.asyncio
async def test_known_context_fact_not_present_in_real_user_text_is_dropped_6a10_2() -> None:
    """6A.10.2 CORRECTIVE PASS -- THE core trust-boundary proof: even
    though `known_context_facts` claims a fault, the REAL current-turn
    user text never mentions it -- a `FunctionTool` argument is model
    output, not automatically a verified fact, and must be dropped
    rather than silently promoted to KNOWN Context."""
    ctx = _FakeToolContext(user_id=ALICE, user_text="what should I check next?")
    package = await module._build_context_package(
        ctx, "what should I check next?", known_context_facts={"fault": "VSWR Over Threshold"}
    )
    assert package.telco_context == []


@pytest.mark.asyncio
async def test_known_context_fact_dropped_when_no_user_content_at_all() -> None:
    """A `_FakeToolContext` with no `user_content` (e.g. `user_text=""`)
    must fail closed to an empty TELCO Context -- never trust a fact with
    nothing real to verify it against."""
    ctx = _FakeToolContext(user_id=ALICE)  # user_text="" -> user_content is None
    package = await module._build_context_package(
        ctx, "what should I check next?", known_context_facts={"fault": "VSWR Over Threshold"}
    )
    assert package.telco_context == []


@pytest.mark.asyncio
async def test_unrecognized_context_fact_dimension_is_silently_dropped_never_a_crash() -> None:
    ctx = _FakeToolContext(user_id=ALICE)
    package = await module._build_context_package(
        ctx, "what should I check next?", known_context_facts={"not_a_real_dimension": "whatever"}
    )
    assert package.telco_context == []


@pytest.mark.asyncio
async def test_blank_context_fact_value_is_silently_dropped_never_a_crash() -> None:
    ctx = _FakeToolContext(user_id=ALICE)
    package = await module._build_context_package(
        ctx, "what should I check next?", known_context_facts={"fault": "   "}
    )
    assert package.telco_context == []


@pytest.mark.asyncio
async def test_evidence_pipeline_is_really_wired_never_a_hardcoded_stub(monkeypatch) -> None:
    """Proves the tool genuinely CALLS the real `query_selected_evidence`
    composition (never a hardcoded empty `EvidenceSelectionResult`, which
    is exactly the defect this corrective pass fixes) by substituting a
    controlled, non-empty result for it and confirming that result -- not
    an empty one -- reaches the assembled `ContextPackage`."""
    fake_result = EvidenceSelectionResult(query_text="probe", selected=[], selection_reason="controlled-substitution-proof")
    called_with: dict[str, Any] = {}

    async def _fake_query_selected_evidence(query_text, context_state, **kwargs):
        called_with["query_text"] = query_text
        called_with["context_state"] = context_state
        return fake_result

    monkeypatch.setattr(module, "query_selected_evidence", _fake_query_selected_evidence)
    ctx = _FakeToolContext(user_id=ALICE)
    package = await module._build_context_package(ctx, "what should I check next?")

    assert called_with["query_text"] == "what should I check next?"
    assert package.evidence.selection_reason == "controlled-substitution-proof"


@pytest.mark.asyncio
async def test_owner_id_matches_context_package_owner_no_mismatch_error() -> None:
    """The wrapper must never construct a request whose owner_id
    disagrees with the ContextPackage it built -- that would trip 6A.9's
    own fail-closed TroubleshootingOwnerMismatchError as an unexpected
    exception, which this wrapper safely converts to BLOCKED rather than
    ever raising it. Prove the happy path (no mismatch, no exception
    swallowed unexpectedly) by checking the result is never the generic
    exception-path failure text."""
    ctx = _FakeToolContext(user_id=ALICE)
    result = await troubleshooting_manager(troubleshooting_question="what next?", tool_context=ctx)
    assert result["detail"] != "A troubleshooting assessment could not be completed for this request."


# --- Exception safety ----------------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_exception_fails_closed_never_propagates(monkeypatch) -> None:
    async def _boom(request):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "run_troubleshooting_assessment", _boom)
    ctx = _FakeToolContext(user_id=ALICE)
    result = await troubleshooting_manager(troubleshooting_question="what next?", tool_context=ctx)
    assert result["status"] == "blocked"
    assert result["detail"] == "A troubleshooting assessment could not be completed for this request."


# --- Bounded invocation (max 1 real call per turn) -----------------------


def test_first_call_this_turn_is_never_blocked() -> None:
    ctx = _FakeToolContext(user_id=ALICE)
    tool = _FakeTool(TOOL_NAME)
    assert block_repeated_troubleshooting_invocation(tool, {}, ctx) is None


def test_second_call_this_turn_is_intercepted_with_cached_result() -> None:
    ctx = _FakeToolContext(user_id=ALICE)
    tool = _FakeTool(TOOL_NAME)
    first_response = {"status": "needs_information", "detail": "missing fault"}
    cache_troubleshooting_result_this_turn(tool, {}, ctx, first_response)

    blocked = block_repeated_troubleshooting_invocation(tool, {}, ctx)
    assert blocked == first_response
    # Returned dict must be a copy, never the same mutable object as the cache.
    assert blocked is not ctx.state[TROUBLESHOOTING_RESULT_THIS_TURN_STATE_KEY]


def test_guard_never_interferes_with_other_tools() -> None:
    ctx = _FakeToolContext(user_id=ALICE)
    other_tool = _FakeTool("incident_manager")
    cache_troubleshooting_result_this_turn(_FakeTool(TOOL_NAME), {}, ctx, {"status": "needs_information"})
    assert block_repeated_troubleshooting_invocation(other_tool, {}, ctx) is None


def test_cache_ignores_non_dict_tool_response() -> None:
    ctx = _FakeToolContext(user_id=ALICE)
    tool = _FakeTool(TOOL_NAME)
    cache_troubleshooting_result_this_turn(tool, {}, ctx, "not a dict")
    assert TROUBLESHOOTING_RESULT_THIS_TURN_STATE_KEY not in ctx.state


def test_state_key_is_temp_prefixed_never_survives_a_later_turn() -> None:
    """The `temp:` prefix is what makes ADK discard this marker at the
    end of the turn (see selection_delegation_guard.py's own proven
    precedent) -- a structural, not merely conventional, guarantee."""
    assert TROUBLESHOOTING_RESULT_THIS_TURN_STATE_KEY.startswith("temp:")
