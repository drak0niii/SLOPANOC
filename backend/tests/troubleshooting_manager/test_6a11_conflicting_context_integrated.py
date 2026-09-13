"""Phase 6A.11 Pass 1 -- Integrated TELCO Validation: proves a
CONFLICTING TELCO Context dimension survives into the assembled
`ContextPackage`, is NEVER silently resolved to one side, and correctly
blocks Skill readiness when (and only when) a Skill actually requires
that dimension -- through the REAL, unmodified `run_troubleshooting_
assessment`/`evaluate_skill_readiness` orchestration (6A.7/6A.9), never
a redesign of either.

DEF-0022 (see docs/DEFECT_REGISTER.md): the original shared fixture,
`context_package_fault_conflicting`, conflicted on `FAULT` -- a MULTI-
cardinality dimension, so two different fault values were legitimately
BOTH accepted (KNOWN), never CONFLICTING; the fixture never actually
proved what its name promised, because it was never exercised in an
assertion until this pass. Fixed to conflict on `VENDOR` (SINGULAR)
instead, while still asserting one real, unconflicted `FAULT` value so
the current production Skill (which requires only `fault`) can still
legitimately reach `ADVISORY_READY` -- deliberately proving BOTH
properties in one scenario: a conflict the current Skill does not need
never blocks it, AND that same conflict is never silently discarded or
resolved -- it remains truthfully CONFLICTING in the assembled package
throughout.
"""
from __future__ import annotations

import pytest

import backend.agents.troubleshooting_manager.runtime as runtime_module
from backend.agents.troubleshooting_manager.runtime import run_troubleshooting_assessment
from backend.agents.troubleshooting_manager.schemas import TroubleshootingManagerRequest, TroubleshootingManagerResponse, TroubleshootingResponseStatus
from backend.context.domain.enums import ContextDimension, ContextState
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService
from backend.skills.contracts import ContextRequirement, EvidenceRequirement, RequirementStatus, SkillDefinition, SkillLifecycle, SkillReadinessStatus
from backend.skills.readiness import evaluate_skill_readiness

from ._fixtures import context_package_fault_conflicting, evidence_candidate


def _vendor_requiring_skill() -> SkillDefinition:
    """A LOCALLY-CONSTRUCTED test Skill, never registered anywhere, never
    touching the production registry/Team Manager -- exists solely to
    prove `evaluate_skill_readiness`'s own generic, dimension-agnostic
    CONFLICTING-blocks-readiness behavior using a dimension (`vendor`)
    the real production Skill does not itself require."""
    return SkillDefinition(
        skill_id="test.6a11-vendor-requiring-probe",
        version="1.0.0",
        lifecycle=SkillLifecycle.ACTIVE,
        name="6A.11 Pass 1 Vendor-Requiring Probe",
        description="Test-only Skill requiring VENDOR, used solely to prove CONFLICTING blocks readiness generically.",
        objective="probe readiness behavior for a conflicting SINGULAR dimension",
        context_requirements=[ContextRequirement(dimension=ContextDimension.VENDOR)],
        evidence_requirement=EvidenceRequirement(minimum_selected_items=0),
    )


def test_conflicting_vendor_survives_unresolved_into_the_assembled_package() -> None:
    package = context_package_fault_conflicting(owner_id="OWNER-CONFLICT-0", evidence_items=[evidence_candidate("ev-1")])
    by_dimension = {view.dimension: view for view in package.telco_context}

    assert by_dimension[ContextDimension.VENDOR].state == ContextState.CONFLICTING
    assert by_dimension[ContextDimension.VENDOR].accepted == []
    assert {a.canonical_value for a in by_dimension[ContextDimension.VENDOR].conflicting} == {"ERICSSON", "NOKIA"}
    # The unrelated, non-conflicting FAULT dimension is completely unaffected by VENDOR's conflict.
    assert by_dimension[ContextDimension.FAULT].state == ContextState.KNOWN


@pytest.mark.asyncio
async def test_conflicting_vendor_does_not_block_a_skill_that_does_not_require_it(monkeypatch) -> None:
    """The current production Skill (`telco.troubleshooting_assessment`)
    requires only `fault` -- an unrelated, genuinely CONFLICTING `vendor`
    dimension must not block it, and must not be silently resolved
    merely because it happens to sit alongside a KNOWN dimension the
    Skill DOES need. The model invocation itself is monkeypatched
    (mirrors `test_runtime.py`'s own established convention) so this
    test remains deterministic and needs no real Vertex/Gemini
    reachability -- it proves the SELECTION/readiness/package-integrity
    behavior, not model output quality (covered separately by the real-
    stack suite)."""
    good_response = TroubleshootingManagerResponse(status=TroubleshootingResponseStatus.ADVISORY_READY, assessment="ok", findings=[], stop_or_escalation_condition=None)

    async def _fake_invoke(_package):
        return good_response

    monkeypatch.setattr(runtime_module, "_invoke_troubleshooting_manager_model", _fake_invoke)

    package = context_package_fault_conflicting(owner_id="OWNER-CONFLICT-1", evidence_items=[evidence_candidate("ev-1")])
    request = TroubleshootingManagerRequest(owner_id="OWNER-CONFLICT-1", context_package=package, objective="what should be checked next?")

    result = await run_troubleshooting_assessment(request, experience_service=ExperienceMemoryService())

    by_dimension = {view.dimension: view for view in result.intelligence_package.context_package.telco_context}
    assert by_dimension[ContextDimension.VENDOR].state == ContextState.CONFLICTING  # never silently resolved
    assert result.intelligence_package.skill_selection_outcome.value == "selected"
    assert result.model_invoked is True
    assert result.response.status == TroubleshootingResponseStatus.ADVISORY_READY


def test_conflicting_vendor_blocks_readiness_when_a_skill_actually_requires_it() -> None:
    """Direct, generic proof of the readiness mechanism itself (6A.7,
    unmodified): a Skill that DOES require the conflicting dimension is
    correctly NOT_READY, with an explicit CONFLICTING reason -- never
    resolved by picking the earlier/later/USER/CASE-origin assertion."""
    package = context_package_fault_conflicting(owner_id="OWNER-CONFLICT-2", evidence_items=[evidence_candidate("ev-1")])
    readiness = evaluate_skill_readiness(_vendor_requiring_skill(), package)

    assert readiness.status == SkillReadinessStatus.NOT_READY
    vendor_result = next(r for r in readiness.context_results if r.dimension == ContextDimension.VENDOR)
    assert vendor_result.status == RequirementStatus.MISSING
    assert "conflicting" in vendor_result.reason.lower()


@pytest.mark.asyncio
async def test_conflicting_vendor_never_arbitrarily_picks_a_winner_across_repeated_calls() -> None:
    """Determinism/no-arbitrary-winner proof: repeated evaluation of the
    SAME conflicting package always yields the identical readiness
    outcome for a Skill that requires the conflicting dimension -- never
    a coin-flip between the two disputed values."""
    package = context_package_fault_conflicting(owner_id="OWNER-CONFLICT-3", evidence_items=[evidence_candidate("ev-1")])
    skill = _vendor_requiring_skill()
    statuses = {evaluate_skill_readiness(skill, package).status for _ in range(5)}
    assert statuses == {SkillReadinessStatus.NOT_READY}
