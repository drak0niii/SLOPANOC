"""Phase 6A.7: deterministic, typed-only Skill applicability evaluation
(§23-§26).

APPLICABILITY != READINESS (§25): this answers "could this methodology
apply to this explicitly known operational context?" -- never "are the
inputs available right now" (`readiness.py`'s own question).
APPLICABILITY != SELECTION (§25/§51): this never ranks, recommends, or
picks a Skill -- it evaluates exactly one, explicitly-referenced Skill.
APPLICABILITY != KNOWLEDGE APPLICABILITY (§24): this is NOT a second
copy of 6A.4's narrowing engine/applicability model -- it is a much
smaller, Skill-scoped, typed dimension-value match, with no code path
into `backend.knowledge.narrowing` (enforced by the dependency-boundary
test).

NO FREE-TEXT INFERENCE (§26): the only inputs ever consulted are the
already-typed `ContextPackage.telco_context` values -- never raw user
text, never a keyword/semantic/LLM router.
"""
from __future__ import annotations

from backend.context.domain.enums import ContextState
from backend.context_engineering.contracts import ContextPackage
from backend.skills.contracts import (
    ApplicabilityConditionResult,
    ApplicabilityOutcome,
    SkillApplicabilityResult,
    SkillDefinition,
)

__all__ = ["evaluate_skill_applicability"]


def _evaluate_condition(dimension, allowed_values: list[str], context_package: ContextPackage) -> ApplicabilityConditionResult:
    view = next((v for v in context_package.telco_context if v.dimension == dimension), None)
    if view is None or view.state in (ContextState.UNKNOWN, ContextState.CONFLICTING):
        return ApplicabilityConditionResult(dimension=dimension, outcome=ApplicabilityOutcome.INDETERMINATE)
    if view.state == ContextState.NOT_APPLICABLE:
        return ApplicabilityConditionResult(dimension=dimension, outcome=ApplicabilityOutcome.NOT_APPLICABLE)
    # KNOWN: matches if ANY accepted canonical value is among the allowed set.
    accepted_values = {a.canonical_value for a in view.accepted if a.canonical_value is not None}
    if accepted_values & set(allowed_values):
        return ApplicabilityConditionResult(dimension=dimension, outcome=ApplicabilityOutcome.APPLICABLE)
    return ApplicabilityConditionResult(dimension=dimension, outcome=ApplicabilityOutcome.NOT_APPLICABLE)


def evaluate_skill_applicability(skill: SkillDefinition, context_package: ContextPackage) -> SkillApplicabilityResult:
    """A Skill declaring NO conditions is trivially APPLICABLE (§23: "an
    empty condition list means the Skill declares no scope restriction",
    never a guess). Overall outcome: an explicit mismatch on ANY
    condition wins over an indeterminate one (a known incompatibility is
    more informative than an unknown), and INDETERMINATE wins over
    APPLICABLE (never claim applicability while a required condition is
    unresolved)."""
    condition_results = [_evaluate_condition(condition.dimension, condition.allowed_values, context_package) for condition in skill.applicability.conditions]

    if any(result.outcome == ApplicabilityOutcome.NOT_APPLICABLE for result in condition_results):
        outcome = ApplicabilityOutcome.NOT_APPLICABLE
    elif any(result.outcome == ApplicabilityOutcome.INDETERMINATE for result in condition_results):
        outcome = ApplicabilityOutcome.INDETERMINATE
    else:
        outcome = ApplicabilityOutcome.APPLICABLE

    return SkillApplicabilityResult(skill_id=skill.skill_id, version=skill.version, outcome=outcome, condition_results=condition_results)
