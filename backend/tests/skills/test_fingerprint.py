"""Phase 6A.7 core test matrix -- FINGERPRINT (§63)."""
from __future__ import annotations

from backend.skills.contracts import ApplicabilityCondition, ContextRequirement, MethodologyStep, SkillApplicability, SkillDefinition, SkillLifecycle
from backend.skills.fingerprint import compute_skill_fingerprint
from backend.context.domain.enums import ContextDimension


def _base_skill(**overrides) -> SkillDefinition:
    defaults = dict(skill_id="telco.fault_diagnosis", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="n", description="d", objective="o")
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def test_same_canonical_skill_same_fingerprint() -> None:
    a = _base_skill()
    b = _base_skill()
    assert compute_skill_fingerprint(a) == compute_skill_fingerprint(b)


def test_repeated_computation_stable() -> None:
    skill = _base_skill()
    fingerprints = {compute_skill_fingerprint(skill) for _ in range(10)}
    assert len(fingerprints) == 1


def test_reordered_context_requirements_same_fingerprint() -> None:
    a = _base_skill(context_requirements=[ContextRequirement(dimension=ContextDimension.VENDOR), ContextRequirement(dimension=ContextDimension.CUSTOMER)])
    b = _base_skill(context_requirements=[ContextRequirement(dimension=ContextDimension.CUSTOMER), ContextRequirement(dimension=ContextDimension.VENDOR)])
    assert compute_skill_fingerprint(a) == compute_skill_fingerprint(b)


def test_reordered_capability_requirements_same_fingerprint() -> None:
    a = _base_skill(capability_requirements=["alarms.read", "tickets.read"])
    b = _base_skill(capability_requirements=["tickets.read", "alarms.read"])
    assert compute_skill_fingerprint(a) == compute_skill_fingerprint(b)


def test_reordered_applicability_conditions_same_fingerprint() -> None:
    a = _base_skill(applicability=SkillApplicability(conditions=[
        ApplicabilityCondition(dimension=ContextDimension.VENDOR, allowed_values=["ERICSSON"]),
        ApplicabilityCondition(dimension=ContextDimension.TECHNOLOGY, allowed_values=["LTE"]),
    ]))
    b = _base_skill(applicability=SkillApplicability(conditions=[
        ApplicabilityCondition(dimension=ContextDimension.TECHNOLOGY, allowed_values=["LTE"]),
        ApplicabilityCondition(dimension=ContextDimension.VENDOR, allowed_values=["ERICSSON"]),
    ]))
    assert compute_skill_fingerprint(a) == compute_skill_fingerprint(b)


def test_methodology_reordered_by_declaration_but_same_sequence_same_fingerprint() -> None:
    step_a = MethodologyStep(step_id="s1", sequence=0, title="first")
    step_b = MethodologyStep(step_id="s2", sequence=1, title="second")
    a = _base_skill(methodology=[step_a, step_b])
    b = _base_skill(methodology=[step_b, step_a])
    assert compute_skill_fingerprint(a) == compute_skill_fingerprint(b)


def test_material_methodology_change_different_fingerprint() -> None:
    a = _base_skill(methodology=[MethodologyStep(step_id="s1", sequence=0, title="Establish context")])
    b = _base_skill(methodology=[MethodologyStep(step_id="s1", sequence=0, title="Review evidence instead")])
    assert compute_skill_fingerprint(a) != compute_skill_fingerprint(b)


def test_version_change_different_fingerprint() -> None:
    a = _base_skill(version="1.0.0")
    b = _base_skill(version="1.0.1")
    assert compute_skill_fingerprint(a) != compute_skill_fingerprint(b)


def test_lifecycle_change_different_fingerprint() -> None:
    a = _base_skill(lifecycle=SkillLifecycle.ACTIVE)
    b = _base_skill(lifecycle=SkillLifecycle.DEPRECATED)
    assert compute_skill_fingerprint(a) != compute_skill_fingerprint(b)
