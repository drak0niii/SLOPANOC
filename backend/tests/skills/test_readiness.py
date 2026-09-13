"""Phase 6A.7 core test matrix -- READINESS (§65), against a REAL 6A.6
`ContextPackage`.
"""
from __future__ import annotations

from backend.cases.schemas import CaseContextSnapshot, CaseStatus
from backend.context.domain.enums import ContextDimension
from backend.skills.contracts import ContextRequirement, EvidenceRequirement, RequirementStatus, SkillDefinition, SkillLifecycle, SkillReadinessStatus
from backend.skills.readiness import evaluate_skill_readiness
from backend.tests.skills._fixtures import build_context_package


def _skill(**overrides) -> SkillDefinition:
    defaults = dict(skill_id="telco.fault_diagnosis", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="n", description="d", objective="o")
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def test_all_requirements_satisfied_is_ready() -> None:
    skill = _skill(context_requirements=[ContextRequirement(dimension=ContextDimension.VENDOR)], evidence_requirement=EvidenceRequirement(minimum_selected_items=1))
    package = build_context_package(known={ContextDimension.VENDOR: "Ericsson"}, selected_evidence_count=1)
    result = evaluate_skill_readiness(skill, package)
    assert result.status == SkillReadinessStatus.READY


def test_required_known_context_unknown_is_not_ready() -> None:
    skill = _skill(context_requirements=[ContextRequirement(dimension=ContextDimension.VENDOR)])
    package = build_context_package(known={}, selected_evidence_count=0)
    result = evaluate_skill_readiness(skill, package)
    assert result.status == SkillReadinessStatus.NOT_READY
    assert result.context_results[0].status == RequirementStatus.MISSING
    assert "UNKNOWN" in result.context_results[0].reason or "absent" in result.context_results[0].reason


def test_required_context_conflicting_is_not_ready() -> None:
    skill = _skill(context_requirements=[ContextRequirement(dimension=ContextDimension.VENDOR)])
    package = build_context_package(conflicting=[ContextDimension.VENDOR])
    result = evaluate_skill_readiness(skill, package)
    assert result.status == SkillReadinessStatus.NOT_READY
    assert "CONFLICTING" in result.context_results[0].reason


def test_required_evidence_absent_is_not_ready() -> None:
    skill = _skill(evidence_requirement=EvidenceRequirement(minimum_selected_items=1))
    package = build_context_package(selected_evidence_count=0)
    result = evaluate_skill_readiness(skill, package)
    assert result.status == SkillReadinessStatus.NOT_READY
    assert result.evidence_result.status == RequirementStatus.MISSING


def test_required_evidence_present_satisfies_requirement() -> None:
    skill = _skill(evidence_requirement=EvidenceRequirement(minimum_selected_items=1))
    package = build_context_package(selected_evidence_count=2)
    result = evaluate_skill_readiness(skill, package)
    assert result.evidence_result.status == RequirementStatus.SATISFIED


def test_requires_source_evidence_rejects_all_derived() -> None:
    skill = _skill(evidence_requirement=EvidenceRequirement(minimum_selected_items=1, requires_source_evidence=True))
    package = build_context_package(selected_evidence_count=1, derived_evidence_count=1)
    result = evaluate_skill_readiness(skill, package)
    assert result.status == SkillReadinessStatus.NOT_READY
    assert result.evidence_result.status == RequirementStatus.MISSING


def test_optional_context_absent_remains_ready() -> None:
    """A Skill with NO context requirements declared must stay READY
    even though the ContextPackage has no known dimensions at all."""
    skill = _skill()
    package = build_context_package()
    result = evaluate_skill_readiness(skill, package)
    assert result.status == SkillReadinessStatus.READY


def test_case_context_required_and_present() -> None:
    skill = _skill(requires_case_context=True)
    snapshot = CaseContextSnapshot(case_id="C1", title="t", status=CaseStatus.OPEN, problem_statement="p")
    package = build_context_package(case_context=snapshot)
    result = evaluate_skill_readiness(skill, package)
    assert result.case_context_result == RequirementStatus.SATISFIED
    assert result.status == SkillReadinessStatus.READY


def test_case_context_required_but_absent() -> None:
    skill = _skill(requires_case_context=True)
    package = build_context_package()
    result = evaluate_skill_readiness(skill, package)
    assert result.case_context_result == RequirementStatus.MISSING
    assert result.status == SkillReadinessStatus.NOT_READY


def test_multiple_context_requirements_all_must_be_known() -> None:
    skill = _skill(context_requirements=[ContextRequirement(dimension=ContextDimension.VENDOR), ContextRequirement(dimension=ContextDimension.TECHNOLOGY)])
    package = build_context_package(known={ContextDimension.VENDOR: "Ericsson"})  # TECHNOLOGY left unknown
    result = evaluate_skill_readiness(skill, package)
    assert result.status == SkillReadinessStatus.NOT_READY
    statuses = {r.dimension: r.status for r in result.context_results}
    assert statuses[ContextDimension.VENDOR] == RequirementStatus.SATISFIED
    assert statuses[ContextDimension.TECHNOLOGY] == RequirementStatus.MISSING


def test_owner_isolation_readiness_never_looks_elsewhere() -> None:
    """§53: readiness consumes exactly the ONE ContextPackage passed in
    -- there is no code path that could retrieve another case/session."""
    import inspect

    from backend.skills import readiness as module

    source = inspect.getsource(module)
    for forbidden in ("get_context_state", "get_link_for_session", "get_context_items", "TelcoContextService", "CaseService"):
        assert forbidden not in source, f"readiness.py must never look up another owner's data -- found reference to {forbidden!r}"
