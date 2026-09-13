"""Phase 6A.7: deterministic Skill readiness evaluation against an
already-assembled 6A.6 `ContextPackage` (§50).

READINESS != SELECTION (§51): this module answers ONLY "does this
EXPLICITLY REFERENCED Skill have sufficient required inputs right now?"
-- never "which Skill should be used" (no ranking, no recommendation, no
intent detection). READINESS != APPLICABILITY (§25): "could this
methodology apply" is `applicability.py`'s own, separate question.

NO KNOWLEDGE/RETRIEVAL ACCESS (§31, enforced by `backend/tests/skills/
test_dependency_boundary.py` and a dedicated call-level AST scan): this
module consumes `ContextPackage`/`EvidencePackage` exactly as already
assembled by 6A.6 -- it never calls `hybrid_retrieve`/`narrow_corpus`/
`KnowledgeRepository`/an embedding provider.

NO RUNTIME CAPABILITY DISCOVERY (§34/§35/§36): `available_capabilities`
is an OPTIONAL, caller-supplied `set[str]` (or `None`) -- this module
never imports Tools, never queries Team Manager/Incident Manager/MCP/
Graph/BMC/Power Automate, and never inspects a live network to decide
whether a capability exists.
"""
from __future__ import annotations

from typing import Optional

from backend.context.domain.enums import ContextState
from backend.context_engineering.contracts import ContextPackage
from backend.skills.contracts import (
    CapabilityRequirementResult,
    ContextRequirementResult,
    EvidenceRequirementResult,
    RequirementStatus,
    SkillDefinition,
    SkillReadinessResult,
    SkillReadinessStatus,
)

__all__ = ["evaluate_skill_readiness"]

_UNKNOWN_REASON = "required context is UNKNOWN"
_CONFLICTING_REASON = "required context is CONFLICTING"
_NOT_APPLICABLE_REASON = "required context is NOT_APPLICABLE"
_ABSENT_REASON = "required context is absent"


def _evaluate_context_requirements(skill: SkillDefinition, context_package: ContextPackage) -> list[ContextRequirementResult]:
    """§30: KNOWN -> SATISFIED. UNKNOWN/CONFLICTING/NOT_APPLICABLE/absent
    -> MISSING, with an explicit, distinct reason for each -- never
    guessed, never resolved by picking one assertion or the latest one."""
    by_dimension = {view.dimension: view for view in context_package.telco_context}
    results: list[ContextRequirementResult] = []
    for requirement in skill.context_requirements:
        view = by_dimension.get(requirement.dimension)
        if view is None:
            results.append(ContextRequirementResult(dimension=requirement.dimension, status=RequirementStatus.MISSING, reason=_ABSENT_REASON))
        elif view.state == ContextState.KNOWN:
            results.append(ContextRequirementResult(dimension=requirement.dimension, status=RequirementStatus.SATISFIED))
        elif view.state == ContextState.UNKNOWN:
            results.append(ContextRequirementResult(dimension=requirement.dimension, status=RequirementStatus.MISSING, reason=_UNKNOWN_REASON))
        elif view.state == ContextState.CONFLICTING:
            results.append(ContextRequirementResult(dimension=requirement.dimension, status=RequirementStatus.MISSING, reason=_CONFLICTING_REASON))
        else:  # ContextState.NOT_APPLICABLE
            results.append(ContextRequirementResult(dimension=requirement.dimension, status=RequirementStatus.MISSING, reason=_NOT_APPLICABLE_REASON))
    return results


def _evaluate_evidence_requirement(skill: SkillDefinition, context_package: ContextPackage) -> EvidenceRequirementResult:
    evidence = context_package.evidence
    if evidence.selected_evidence_count < skill.evidence_requirement.minimum_selected_items:
        return EvidenceRequirementResult(
            status=RequirementStatus.MISSING,
            reason=f"requires at least {skill.evidence_requirement.minimum_selected_items} selected evidence item(s), found {evidence.selected_evidence_count}",
        )
    if skill.evidence_requirement.requires_source_evidence and evidence.source_evidence_count < 1:
        return EvidenceRequirementResult(status=RequirementStatus.MISSING, reason="requires at least one SOURCE (non-derived) selected evidence item, found none")
    return EvidenceRequirementResult(status=RequirementStatus.SATISFIED)


def _evaluate_capability_requirements(skill: SkillDefinition, available_capabilities: Optional[set[str]]) -> list[CapabilityRequirementResult]:
    """§35: if `available_capabilities` is `None`, every requirement is
    `NOT_EVALUATED` -- never silently SATISFIED, never silently MISSING."""
    results: list[CapabilityRequirementResult] = []
    for capability in skill.capability_requirements:
        if available_capabilities is None:
            results.append(CapabilityRequirementResult(capability=capability, status=RequirementStatus.NOT_EVALUATED))
        elif capability in available_capabilities:
            results.append(CapabilityRequirementResult(capability=capability, status=RequirementStatus.SATISFIED))
        else:
            results.append(CapabilityRequirementResult(capability=capability, status=RequirementStatus.MISSING))
    return results


def _evaluate_case_context_requirement(skill: SkillDefinition, context_package: ContextPackage) -> Optional[RequirementStatus]:
    if not skill.requires_case_context:
        return None
    return RequirementStatus.SATISFIED if context_package.case_context is not None else RequirementStatus.MISSING


def _overall_status(
    context_results: list[ContextRequirementResult],
    case_context_result: Optional[RequirementStatus],
    evidence_result: EvidenceRequirementResult,
    capability_results: list[CapabilityRequirementResult],
) -> SkillReadinessStatus:
    if any(result.status == RequirementStatus.MISSING for result in context_results):
        return SkillReadinessStatus.NOT_READY
    if case_context_result == RequirementStatus.MISSING:
        return SkillReadinessStatus.NOT_READY
    if evidence_result.status == RequirementStatus.MISSING:
        return SkillReadinessStatus.NOT_READY
    if any(result.status == RequirementStatus.MISSING for result in capability_results):
        return SkillReadinessStatus.NOT_READY
    if any(result.status == RequirementStatus.NOT_EVALUATED for result in capability_results):
        return SkillReadinessStatus.INDETERMINATE
    return SkillReadinessStatus.READY


def evaluate_skill_readiness(
    skill: SkillDefinition,
    context_package: ContextPackage,
    available_capabilities: Optional[set[str]] = None,
) -> SkillReadinessResult:
    """Pure, deterministic, single-`ContextPackage`-scoped evaluation
    (§53: never retrieves another case/session/owner to fill in missing
    context -- there is no code path here that could)."""
    context_results = _evaluate_context_requirements(skill, context_package)
    case_context_result = _evaluate_case_context_requirement(skill, context_package)
    evidence_result = _evaluate_evidence_requirement(skill, context_package)
    capability_results = _evaluate_capability_requirements(skill, available_capabilities)
    status = _overall_status(context_results, case_context_result, evidence_result, capability_results)
    return SkillReadinessResult(
        skill_id=skill.skill_id,
        version=skill.version,
        status=status,
        context_results=context_results,
        case_context_result=case_context_result,
        evidence_result=evidence_result,
        capability_results=capability_results,
    )
