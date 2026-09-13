"""Phase 6A.7 core test matrix -- APPLICABILITY (§66). Typed-only, never
free-text, never ranking.
"""
from __future__ import annotations

from backend.context.domain.enums import ContextDimension
from backend.skills.applicability import evaluate_skill_applicability
from backend.skills.contracts import ApplicabilityCondition, ApplicabilityOutcome, SkillApplicability, SkillDefinition, SkillLifecycle
from backend.tests.skills._fixtures import build_context_package


def _skill(**overrides) -> SkillDefinition:
    defaults = dict(skill_id="telco.vswr_review", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="n", description="d", objective="o")
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def test_matching_explicit_typed_context_is_applicable() -> None:
    skill = _skill(applicability=SkillApplicability(conditions=[ApplicabilityCondition(dimension=ContextDimension.TECHNOLOGY, allowed_values=["LTE", "5G"])]))
    package = build_context_package(known={ContextDimension.TECHNOLOGY: "LTE"})
    result = evaluate_skill_applicability(skill, package)
    assert result.outcome == ApplicabilityOutcome.APPLICABLE


def test_explicit_typed_mismatch_is_not_applicable() -> None:
    skill = _skill(applicability=SkillApplicability(conditions=[ApplicabilityCondition(dimension=ContextDimension.TECHNOLOGY, allowed_values=["5G"])]))
    package = build_context_package(known={ContextDimension.TECHNOLOGY: "LTE"})
    result = evaluate_skill_applicability(skill, package)
    assert result.outcome == ApplicabilityOutcome.NOT_APPLICABLE


def test_required_applicability_field_unknown_is_indeterminate() -> None:
    skill = _skill(applicability=SkillApplicability(conditions=[ApplicabilityCondition(dimension=ContextDimension.TECHNOLOGY, allowed_values=["LTE"])]))
    package = build_context_package()  # TECHNOLOGY never asserted
    result = evaluate_skill_applicability(skill, package)
    assert result.outcome == ApplicabilityOutcome.INDETERMINATE


def test_required_applicability_field_conflicting_is_indeterminate() -> None:
    """Uses VENDOR (SINGULAR cardinality) -- TECHNOLOGY is a MULTI-
    cardinality dimension (per 6A.2's own domain model), where two
    distinct concurrent values are never a conflict at all."""
    skill = _skill(applicability=SkillApplicability(conditions=[ApplicabilityCondition(dimension=ContextDimension.VENDOR, allowed_values=["ERICSSON"])]))
    package = build_context_package(conflicting=[ContextDimension.VENDOR])
    result = evaluate_skill_applicability(skill, package)
    assert result.outcome == ApplicabilityOutcome.INDETERMINATE


def test_no_conditions_declared_is_trivially_applicable() -> None:
    skill = _skill()
    package = build_context_package()
    result = evaluate_skill_applicability(skill, package)
    assert result.outcome == ApplicabilityOutcome.APPLICABLE


def test_explicit_mismatch_wins_over_indeterminate() -> None:
    skill = _skill(applicability=SkillApplicability(conditions=[
        ApplicabilityCondition(dimension=ContextDimension.TECHNOLOGY, allowed_values=["5G"]),  # mismatch (LTE known)
        ApplicabilityCondition(dimension=ContextDimension.VENDOR, allowed_values=["ERICSSON"]),  # unknown
    ]))
    package = build_context_package(known={ContextDimension.TECHNOLOGY: "LTE"})
    result = evaluate_skill_applicability(skill, package)
    assert result.outcome == ApplicabilityOutcome.NOT_APPLICABLE


def test_multiple_conditions_all_match_is_applicable() -> None:
    skill = _skill(applicability=SkillApplicability(conditions=[
        ApplicabilityCondition(dimension=ContextDimension.TECHNOLOGY, allowed_values=["LTE"]),
        ApplicabilityCondition(dimension=ContextDimension.VENDOR, allowed_values=["ERICSSON"]),
    ]))
    package = build_context_package(known={ContextDimension.TECHNOLOGY: "LTE", ContextDimension.VENDOR: "Ericsson"})
    result = evaluate_skill_applicability(skill, package)
    assert result.outcome == ApplicabilityOutcome.APPLICABLE


def test_no_free_text_inference_ever_used() -> None:
    """Structural proof: `applicability.py` never accepts/consults any
    free-text field (e.g. a raw user question) -- its ONLY inputs are
    the Skill's own typed conditions and the ContextPackage's own typed
    `telco_context`."""
    import inspect

    from backend.skills import applicability as module

    sig = inspect.signature(module.evaluate_skill_applicability)
    param_names = list(sig.parameters.keys())
    assert param_names == ["skill", "context_package"], f"unexpected extra parameter (possible free-text input channel): {param_names}"


def test_no_ranking_or_selection_exists() -> None:
    """§25/§51: applicability evaluates exactly ONE explicitly-referenced
    Skill -- there is no function anywhere in this module that accepts a
    LIST of Skills to rank/select among."""
    import inspect

    from backend.skills import applicability as module

    functions = [name for name, _ in inspect.getmembers(module, predicate=inspect.isfunction)]
    forbidden_fragments = ("rank", "select", "recommend", "best", "route")
    for name in functions:
        for fragment in forbidden_fragments:
            assert fragment not in name.lower(), f"unexpected ranking/selection-shaped function: {name}"
