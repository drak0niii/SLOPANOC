"""Phase 6A.9 core test matrix -- deterministic Skill resolution: zero
registered, zero applicable, zero ready (UNKNOWN/CONFLICTING/missing-
evidence), exactly one ready selected deterministically without a model
call, and a documented multi-ready tie-break."""
from __future__ import annotations

from backend.agents.troubleshooting_manager.skill_resolution import (
    get_troubleshooting_skill_registry,
    resolve_skill_for_troubleshooting,
)
from backend.context.domain.enums import ContextDimension
from backend.skills.contracts import ApplicabilityCondition, ContextRequirement, EvidenceRequirement, SkillApplicability, SkillLifecycle
from backend.skills.registry import SkillRegistry
from backend.troubleshooting_intelligence.contracts import SkillSelectionOutcome

from ._fixtures import context_package_fault_conflicting, context_package_fault_unknown, context_package_with_fault_known, evidence_candidate

_TROUBLESHOOTING_ASSESSMENT_SKILL_ID = "telco.troubleshooting_assessment"


def test_production_skill_is_registered_and_active() -> None:
    registry = get_troubleshooting_skill_registry()
    skill = registry.resolve_latest_active(_TROUBLESHOOTING_ASSESSMENT_SKILL_ID)
    assert skill.lifecycle == SkillLifecycle.ACTIVE
    assert skill.context_requirements == [ContextRequirement(dimension=ContextDimension.FAULT)]
    assert skill.evidence_requirement.minimum_selected_items == 1


def test_zero_registered_skills() -> None:
    empty_registry = SkillRegistry([])
    package = context_package_with_fault_known(evidence_items=[evidence_candidate()])
    resolution = resolve_skill_for_troubleshooting(package, registry=empty_registry)
    assert resolution.outcome == SkillSelectionOutcome.NONE_REGISTERED
    assert resolution.skill is None
    assert resolution.readiness is None


def test_exactly_one_ready_skill_selected_deterministically() -> None:
    package = context_package_with_fault_known(evidence_items=[evidence_candidate()])
    resolution = resolve_skill_for_troubleshooting(package)
    assert resolution.outcome == SkillSelectionOutcome.SELECTED
    assert resolution.skill is not None
    assert resolution.skill.skill_id == _TROUBLESHOOTING_ASSESSMENT_SKILL_ID
    assert resolution.fingerprint is not None


def test_unknown_required_context_yields_none_ready_never_a_guess() -> None:
    """FAULT has zero assertions -- 6A.2's own `compute_context_state`
    never fabricates an entry for an unasserted dimension (see
    `context_engineering/test_telco_integration.py`'s own precedent), so
    readiness reports it as MISSING/absent -- functionally identical to
    UNKNOWN (no known context exists for this dimension) and, critically,
    never silently guessed."""
    package = context_package_fault_unknown(evidence_items=[evidence_candidate()])
    resolution = resolve_skill_for_troubleshooting(package)
    assert resolution.outcome == SkillSelectionOutcome.NONE_READY
    assert resolution.skill is None
    assert resolution.readiness is not None
    reasons = {r.dimension: r.reason for r in resolution.readiness.context_results}
    assert "absent" in reasons[ContextDimension.FAULT]


def test_conflicting_required_context_yields_none_ready_never_picks_a_side() -> None:
    """FAULT is a MULTI-cardinality dimension (6A.2) -- two different
    FAULT values are both legitimately accepted, never a conflict. To
    exercise the genuinely CONFLICTING branch, use a synthetic Skill
    requiring a SINGULAR-cardinality dimension (VENDOR) instead."""
    from backend.context.domain.enums import AssertionKind, ContextOrigin
    from backend.context.domain.models import ContextAssertion, compute_context_state
    from backend.context_engineering.assembly import assemble_context_package
    from backend.context_engineering.contracts import ContextPackageInput
    from backend.context.domain.enums import ContextProfileOwnerKind
    from backend.skills.contracts import SkillDefinition

    vendor_skill = SkillDefinition(
        skill_id="test.vendor_required",
        version="1.0.0",
        lifecycle=SkillLifecycle.ACTIVE,
        name="n",
        description="d",
        objective="o",
        context_requirements=[ContextRequirement(dimension=ContextDimension.VENDOR)],
    )
    registry = SkillRegistry([vendor_skill])

    a1 = ContextAssertion(assertion_id="a1", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER)
    a2 = ContextAssertion(assertion_id="a2", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Nokia", canonical_value="NOKIA", origin=ContextOrigin.CASE)
    state = compute_context_state([a1, a2])
    from backend.knowledge.hybrid_retrieval.contracts import EvidenceSelectionResult

    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="OWNER-1", telco_context_state=state, evidence_selection=EvidenceSelectionResult(query_text="q", selected=[], selection_reason="none"))
    package = assemble_context_package(input_)

    resolution = resolve_skill_for_troubleshooting(package, registry=registry)
    assert resolution.outcome == SkillSelectionOutcome.NONE_READY
    reasons = {r.dimension: r.reason for r in resolution.readiness.context_results}
    assert "CONFLICTING" in reasons[ContextDimension.VENDOR]


def test_missing_evidence_yields_none_ready() -> None:
    package = context_package_with_fault_known(evidence_items=[])
    resolution = resolve_skill_for_troubleshooting(package)
    assert resolution.outcome == SkillSelectionOutcome.NONE_READY
    assert resolution.readiness.evidence_result.status.value == "missing"


def test_zero_applicable_skills() -> None:
    """A Skill whose applicability condition genuinely mismatches the
    current context (a known VENDOR outside its own allow-list) resolves
    NONE_APPLICABLE -- never silently treated as ready."""
    from backend.context.domain.enums import AssertionKind, ContextOrigin
    from backend.context.domain.models import ContextAssertion
    from backend.skills.contracts import SkillDefinition

    restricted = SkillDefinition(
        skill_id="test.vendor_restricted",
        version="1.0.0",
        lifecycle=SkillLifecycle.ACTIVE,
        name="n",
        description="d",
        objective="o",
        applicability=SkillApplicability(conditions=[ApplicabilityCondition(dimension=ContextDimension.VENDOR, allowed_values=["ERICSSON"])]),
    )
    registry = SkillRegistry([restricted])
    vendor_nokia = ContextAssertion(assertion_id="a-vendor", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Nokia", canonical_value="NOKIA", origin=ContextOrigin.USER)
    package = context_package_with_fault_known(evidence_items=[evidence_candidate()], extra_assertions=[vendor_nokia])
    resolution = resolve_skill_for_troubleshooting(package, registry=registry)
    assert resolution.outcome == SkillSelectionOutcome.NONE_APPLICABLE
    assert resolution.skill is None


def test_multiple_ready_skills_deterministic_tie_break_documented_deferral() -> None:
    """§63: with more than one applicable+READY Skill, resolution falls
    back to a deterministic (skill_id, version) ordering rather than a
    model call -- a documented deferral, exercised here only because
    production currently ships exactly one Skill."""
    from backend.skills.contracts import SkillDefinition

    skill_a = SkillDefinition(skill_id="test.aaa", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="A", description="d", objective="o")
    skill_b = SkillDefinition(skill_id="test.bbb", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="B", description="d", objective="o")
    registry = SkillRegistry([skill_b, skill_a])
    package = context_package_with_fault_known(evidence_items=[evidence_candidate()])
    resolution = resolve_skill_for_troubleshooting(package, registry=registry)
    assert resolution.outcome == SkillSelectionOutcome.SELECTED
    assert resolution.skill.skill_id == "test.aaa", "deterministic tie-break must always pick the lowest skill_id, never depend on registry construction order"


def test_capability_not_evaluated_when_available_capabilities_none() -> None:
    """A Skill with NO capability_requirements is unaffected either way
    -- this proves the production Skill's own overall readiness never
    silently depends on capability evaluation it was never given."""
    package = context_package_with_fault_known(evidence_items=[evidence_candidate()])
    resolution = resolve_skill_for_troubleshooting(package, available_capabilities=None)
    assert resolution.outcome == SkillSelectionOutcome.SELECTED
    assert resolution.readiness.capability_results == []
