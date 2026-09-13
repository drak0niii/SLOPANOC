"""Phase 6A.9: `assemble_troubleshooting_intelligence` -- the one
deterministic Troubleshooting Intelligence Package composition entry
point.

NO LLM CALL, NO DATABASE ACCESS, NO SKILL REGISTRY LOOKUP, NO EXPERIENCE
MEMORY QUERY ANYWHERE IN THIS MODULE (enforced by `backend/tests/
troubleshooting_intelligence/test_dependency_boundary.py`'s AST +
standalone-import checks, mirroring 6A.6's own `test_no_knowledge_
bypass.py` precedent): every input is already-computed data the caller
supplies via `TroubleshootingIntelligenceInput` -- Skill resolution
(registry lookup + readiness/applicability evaluation,
`backend/agents/troubleshooting_manager/skill_resolution.py`) and
Experience Memory retrieval (`backend/agents/troubleshooting_manager/
experience_support.py`) both already happened before this function is
ever called. This module only resolves, validates, combines, labels,
bounds, serializes, and fingerprints.

Given the SAME logical `TroubleshootingIntelligenceInput`, calling this
function twice always produces two packages whose `content_fingerprint`
is identical -- only `created_at` (excluded from the fingerprint) may
legitimately differ.

DOES NOT MUTATE ANY UPSTREAM OBJECT: every list on the resulting package
is built from NEW view objects or from the caller's own already-immutable
inputs (`ContextPackage`/`SkillDefinition` are both themselves immutable-
once-built per their own milestones' discipline), never a mutable
upstream object reused in place.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.troubleshooting_intelligence.contracts import (
    ExperienceSupportView,
    TroubleshootingIntelligenceInput,
    TroubleshootingIntelligencePackage,
)
from backend.troubleshooting_intelligence.fingerprint import compute_intelligence_fingerprint

__all__ = ["TroubleshootingOwnerMismatchError", "assemble_troubleshooting_intelligence"]


class TroubleshootingOwnerMismatchError(ValueError):
    """Raised when the caller-supplied `owner_id` disagrees with the
    `ContextPackage`'s own `owner_id` -- fail closed (§44), never
    silently reconciled, never a code path that queries Experience Memory
    or reasons for one owner while the trusted context belongs to
    another."""


def _build_experience_views(input_: TroubleshootingIntelligenceInput) -> list[ExperienceSupportView]:
    """Preserves the caller-supplied order verbatim (the coordinator's
    own `ExperienceMemoryService.query` already returns a deterministic
    `recorded_at DESC, experience_id ASC` order) -- never re-sorted,
    re-ranked, or filtered here."""
    return [ExperienceSupportView.from_record(record) for record in input_.experience_records]


def assemble_troubleshooting_intelligence(
    input_: TroubleshootingIntelligenceInput,
) -> TroubleshootingIntelligencePackage:
    if input_.context_package.owner_id != input_.owner_id:
        raise TroubleshootingOwnerMismatchError(
            f"owner mismatch: TroubleshootingIntelligenceInput.owner_id={input_.owner_id!r} != "
            f"context_package.owner_id={input_.context_package.owner_id!r}"
        )

    experience_views = _build_experience_views(input_)

    package = TroubleshootingIntelligencePackage(
        owner_id=input_.owner_id,
        objective=input_.objective,
        context_package=input_.context_package,
        skill=input_.skill,
        skill_fingerprint=input_.skill_fingerprint,
        skill_selection_outcome=input_.skill_selection_outcome,
        skill_readiness=input_.skill_readiness,
        skill_applicability=input_.skill_applicability,
        experience=experience_views,
        experience_query=input_.experience_query_metadata,
        content_fingerprint="",
        created_at=datetime.now(timezone.utc),
    )
    fingerprint = compute_intelligence_fingerprint(package)
    return package.model_copy(update={"content_fingerprint": fingerprint})
