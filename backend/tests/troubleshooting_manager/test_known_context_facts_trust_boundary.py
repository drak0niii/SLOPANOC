"""Phase 6A.10.2 CORRECTIVE PASS -- the dedicated `known_context_facts`
trust-boundary regression suite.

CORE RULE THIS FILE PROVES: a `FunctionTool` argument (`known_context_
facts`) is MODEL OUTPUT, not automatically a verified user-stated fact,
a canonical Case fact, or verified operational state, merely because its
own Pydantic/schema validation passed. Team Manager must not be able to
hallucinate `vendor`/`technology`/`fault`/etc. and have the result
silently promoted to canonical KNOWN TELCO Context.

FIX PROVEN HERE: `context_support.build_context_state_from_known_facts`
now REQUIRES the real, verbatim current-turn user text (`extract_
current_user_text(tool_context)`, reading `tool_context.user_content` --
never `troubleshooting_question`, which is itself model-authored) and
DROPS any fact whose value is not a literal, case-insensitive substring
of that real text. Only a fact that survives this deterministic,
application-enforced check is ever labeled `ContextOrigin.USER`.

This file also proves, independently, that neither Knowledge retrieval
nor historical Experience can ever bootstrap/create a current TELCO
Context value -- both are one-way consumers of `telco_context_state`,
never a producer of it.
"""
from __future__ import annotations

import pytest

import backend.agents.team_manager.troubleshooting_tool as tool_module
import backend.agents.troubleshooting_manager.runtime as runtime_module
from backend.agents.troubleshooting_manager.context_support import (
    build_context_state_from_known_facts,
    extract_current_user_text,
    query_selected_evidence,
)
from backend.agents.troubleshooting_manager.schemas import TroubleshootingManagerRequest, TroubleshootingManagerResponse, TroubleshootingResponseStatus
from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextState
from backend.context.domain.models import ContextAssertion, compute_context_state
from backend.experience_memory.domain.enums import ExperienceSourceOrigin, ExperienceType
from backend.experience_memory.domain.models import ExperienceCandidate
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService

from ._fixtures import context_package_with_fault_known, evidence_candidate

ALICE = "alice"


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeSession:
    def __init__(self, session_id: str = "sess-trust-1") -> None:
        self.id = session_id


class _FakeToolContext:
    def __init__(self, user_id: str = ALICE, user_text: str = "") -> None:
        self.user_id = user_id
        self.session = _FakeSession()
        self.state: dict = {}
        self.user_content = _FakeContent(user_text) if user_text else None


# --- extract_current_user_text: pure extraction correctness -------------


def test_extract_current_user_text_joins_all_text_parts() -> None:
    ctx = _FakeToolContext(user_text="hello world")
    assert extract_current_user_text(ctx) == "hello world"


def test_extract_current_user_text_empty_for_missing_user_content() -> None:
    ctx = _FakeToolContext()  # user_text="" -> user_content is None
    assert extract_current_user_text(ctx) == ""


def test_extract_current_user_text_empty_for_missing_parts() -> None:
    class _Ctx:
        pass

    ctx = _Ctx()
    ctx.user_content = _FakeContent("")
    ctx.user_content.parts = []
    assert extract_current_user_text(ctx) == ""


def test_extract_current_user_text_defensive_for_no_user_content_attr_at_all() -> None:
    class _Ctx:
        pass

    assert extract_current_user_text(_Ctx()) == ""


# --- Section 17: unsupported model-claimed facts never become KNOWN -----


def test_unsupported_vendor_claim_never_becomes_known_when_user_said_nothing() -> None:
    """The mandatory test: the user's real message says nothing about
    vendor -- the tool path must NOT result in vendor becoming canonical
    KNOWN Context merely because the model's own tool-call argument
    claimed one."""
    real_text = "What should I check next for this alarm?"
    state = build_context_state_from_known_facts({"vendor": "Ericsson"}, real_text)
    assert ContextDimension.VENDOR not in state


def test_unsupported_technology_claim_never_becomes_known_when_user_said_nothing() -> None:
    real_text = "What should I check next for this alarm?"
    state = build_context_state_from_known_facts({"technology": "5G"}, real_text)
    assert ContextDimension.TECHNOLOGY not in state


def test_unsupported_fault_claim_never_becomes_known_when_user_said_nothing() -> None:
    real_text = "hello, can you help me?"
    state = build_context_state_from_known_facts({"fault": "VSWR Over Threshold"}, real_text)
    assert ContextDimension.FAULT not in state


@pytest.mark.asyncio
async def test_full_wrapper_never_promotes_a_vendor_the_user_never_stated() -> None:
    """End-to-end proof through the real `_build_context_package`, not
    just the pure function -- a model that calls the tool with a vendor
    claim absent from the real user text must not see it reflected in the
    assembled `ContextPackage`."""
    ctx = _FakeToolContext(user_text="what should I check next for this alarm?")
    package = await tool_module._build_context_package(ctx, "what should I check next?", known_context_facts={"vendor": "Nokia"})
    assert package.telco_context == []


# --- Section 18: explicit user fact retains truthful USER provenance ----


@pytest.mark.asyncio
async def test_explicit_user_fact_produces_known_context_with_truthful_user_provenance() -> None:
    ctx = _FakeToolContext(user_text="This is an Ericsson LTE alarm, what should I check next?")
    package = await tool_module._build_context_package(
        ctx, "what should I check next?", known_context_facts={"vendor": "Ericsson", "technology": "LTE"}
    )
    by_dimension = {entry.dimension: entry for entry in package.telco_context}
    assert ContextDimension.VENDOR in by_dimension
    assert ContextDimension.TECHNOLOGY in by_dimension
    for entry in by_dimension.values():
        assert entry.state == ContextState.KNOWN
        assert len(entry.accepted) == 1
        assert entry.accepted[0].origin == ContextOrigin.USER.value
        assert entry.accepted[0].raw_value in ("Ericsson", "LTE")
        assert entry.accepted[0].asserted_at is not None


# --- Section 13/19: provenance audit + conflict semantics preserved -----


def test_context_assertion_carries_full_provenance_fields() -> None:
    """Direct audit of what `build_context_state_from_known_facts`
    actually stores -- value, state, origin, source_reference,
    asserted_at (§13)."""
    real_text = "vendor is Ericsson"
    state = build_context_state_from_known_facts({"vendor": "Ericsson"}, real_text)
    value = state[ContextDimension.VENDOR]
    assert value.state == ContextState.KNOWN
    assertion = value.accepted[0]
    assert assertion.raw_value == "Ericsson"
    assert assertion.canonical_value == "ERICSSON"
    assert assertion.origin == ContextOrigin.USER
    assert assertion.source_reference is None  # honest: no deeper source than "this turn's own message" exists yet
    assert assertion.asserted_at is not None


def test_conflicting_vendor_assertions_preserve_conflicting_state_6a2_semantics() -> None:
    """§19: existing canonical/Case context says vendor=NOKIA while the
    current user states vendor=ERICSSON -- 6A.2's own unmodified
    `compute_context_state` must produce CONFLICTING, never silently pick
    one side. This is a DOMAIN-LEVEL proof (via `ContextAssertion`
    directly) rather than a wrapper-level one, because the current
    `troubleshooting_tool.py` wrapper has only ONE live TELCO Context
    source (`known_context_facts`) -- a second, Case/canonical-sourced
    assertion stream is not wired into the wrapper yet (honestly, that
    remains future work); this test proves the UNDERLYING capability the
    wrapper will rely on the moment a second source exists is genuinely
    intact and unweakened by this corrective pass."""
    assertions = [
        ContextAssertion(assertion_id="a-case", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Nokia", canonical_value="NOKIA", origin=ContextOrigin.CASE, asserted_at=None),
        ContextAssertion(assertion_id="a-user", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER, asserted_at=None),
    ]
    state = compute_context_state(assertions)
    assert state[ContextDimension.VENDOR].state == ContextState.CONFLICTING
    assert state[ContextDimension.VENDOR].accepted == []  # never silently pick one side
    assert len(state[ContextDimension.VENDOR].conflicting) == 2


# --- Section 20: missing facts stay UNKNOWN/absent -----------------------


def test_missing_dimension_stays_absent_never_inferred() -> None:
    state = build_context_state_from_known_facts({"fault": "VSWR Over Threshold"}, "VSWR Over Threshold reported")
    assert ContextDimension.VENDOR not in state
    assert ContextDimension.TECHNOLOGY not in state


@pytest.mark.asyncio
async def test_wrapper_produces_empty_telco_context_with_no_known_facts_at_all() -> None:
    ctx = _FakeToolContext(user_text="hello")
    package = await tool_module._build_context_package(ctx, "hello", known_context_facts=None)
    assert package.telco_context == []


# --- Section 21: Knowledge cannot bootstrap its own applicability Context


@pytest.mark.asyncio
async def test_query_selected_evidence_never_mutates_the_context_state_it_is_given() -> None:
    """Structural proof of the one-way data flow: `telco_context_state`
    feeds Knowledge narrowing/retrieval, never the reverse. A retrieval
    call -- regardless of what it returns, or whether it errors -- must
    never add/remove/alter any entry in the caller's own `context_state`
    dict."""
    real_text = "This is a VSWR Over Threshold alarm."
    state = build_context_state_from_known_facts({"fault": "VSWR Over Threshold"}, real_text)
    snapshot = dict(state)

    result = await query_selected_evidence("some ericsson-flavored query text nobody asserted as context", state)

    assert state == snapshot  # unchanged, regardless of result content
    assert ContextDimension.VENDOR not in state  # Knowledge never bootstraps a vendor value into Context


# --- Section 22: Experience cannot overwrite/create current Context -----


@pytest.mark.asyncio
async def test_experience_content_never_leaks_into_assembled_context_package(monkeypatch) -> None:
    """An ACCEPTED historical Experience record whose own text explicitly
    mentions a vendor must have ZERO effect on the resulting
    `TroubleshootingIntelligencePackage.context_package.telco_context` --
    Experience is consumed as a separate, clearly-labeled field, never
    merged into current Context (Current Context > Experience, §22 of
    the corrective-pass instruction)."""
    package_in = context_package_with_fault_known(owner_id="OWNER-TRUST-1", evidence_items=[evidence_candidate("ev-1")])
    assert all(entry.dimension != ContextDimension.VENDOR for entry in package_in.telco_context)

    service = ExperienceMemoryService()
    await service.record_experience(
        ExperienceCandidate(
            experience_type=ExperienceType.OBSERVATION,
            source_origin=ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
            owner_id="OWNER-TRUST-1",
            source_namespace="bmc",
            source_event_id="evt-vendor-leak-probe-1",
            skill_id="telco.troubleshooting_assessment",
            skill_version="1.0.0",
            outcome_summary="Vendor Ericsson equipment was involved in a prior, unrelated case.",
        )
    )

    good_response = TroubleshootingManagerResponse(status=TroubleshootingResponseStatus.ADVISORY_READY, assessment="ok", findings=[], stop_or_escalation_condition=None)

    async def _fake_invoke(_package):
        return good_response

    monkeypatch.setattr(runtime_module, "_invoke_troubleshooting_manager_model", _fake_invoke)

    request = TroubleshootingManagerRequest(owner_id="OWNER-TRUST-1", context_package=package_in, objective="what should be checked next?")
    result = await runtime_module.run_troubleshooting_assessment(request, experience_service=service)

    assert len(result.intelligence_package.experience) == 1  # the Experience record WAS queried/attached
    assert result.intelligence_package.context_package.telco_context == package_in.telco_context  # but Context is byte-identical to what was passed in
    assert all(entry.dimension != ContextDimension.VENDOR for entry in result.intelligence_package.context_package.telco_context)


# --- Section 23: no hardcoding proof (source-level) ----------------------


def test_no_hardcoded_vendor_technology_or_fault_value_in_production_context_support() -> None:
    """AST-free, deliberately simple source-text scan mirroring this
    codebase's own established "no keyword/hardcoded value in executable
    logic" convention -- illustrative docstring examples are permitted
    (they are prose, not branching), but the module's own real code must
    never special-case a specific vendor/technology/fault string."""
    import inspect

    import backend.agents.troubleshooting_manager.context_support as context_support_module

    source = inspect.getsource(context_support_module)
    forbidden_literals_in_code = ["ericsson", "nokia", "huawei", "vswr"]
    # Strip the module's own module-level docstring block(s) so illustrative
    # examples inside prose never produce a false positive here.
    import ast

    tree = ast.parse(source)
    code_only_lines = set(range(1, len(source.splitlines()) + 1))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
                doc_node = body[0]
                for line in range(doc_node.lineno, doc_node.end_lineno + 1):
                    code_only_lines.discard(line)
    lines = source.splitlines()
    code_only_text = "\n".join(lines[i - 1] for i in sorted(code_only_lines)).lower()
    for literal in forbidden_literals_in_code:
        assert literal not in code_only_text, f"found hardcoded literal {literal!r} in context_support.py's own executable code"
