"""Phase 6A.9: deterministic Skill resolution for a troubleshooting
turn -- the IMPURE coordinator half of Skill consumption (loads the
production Skill registry from disk, then calls the PURE 6A.7
`evaluate_skill_applicability`/`evaluate_skill_readiness` functions).

SKILL SELECTION IS ENTIRELY DETERMINISTIC, NEVER MODEL-DRIVEN (§59-§63):
- Zero ACTIVE Skills registered -> `NONE_REGISTERED`.
- One or more ACTIVE Skills exist, but none is APPLICABLE to the current
  TELCO Context -> `NONE_APPLICABLE`.
- One or more applicable Skills exist, but none is READY (missing
  context/evidence/capability) -> `NONE_READY`; the (deterministically
  chosen, lowest skill_id/version) candidate's own readiness/
  applicability results are still returned, for diagnostic/explanation
  purposes only -- never presented as selected.
- Exactly one applicable+READY Skill -> `SELECTED`, deterministically,
  with NO model call spent choosing it (§62's own explicit "do not waste
  a model call merely to choose the only valid Skill").
- More than one applicable+READY Skill -> deterministically select the
  lowest `(skill_id, parsed version)` candidate. This is a DELIBERATE,
  DOCUMENTED DEFERRAL of full model-driven multi-Skill selection (§63):
  at the time of this milestone, production Skill count is 0 or 1 (see
  this milestone's own closure report §M/§N), so this branch is never
  exercised by real production data -- it exists only so a future
  multi-Skill registry does not silently misbehave, and remains a
  bounded, auditable, non-LLM tie-break rather than an ad hoc choice.
  Building genuine model-driven multi-Skill selection (closed candidate
  list, structured selection, post-validation) is EXPLICITLY DEFERRED to
  a future milestone once more than one production Skill actually
  exists.
"""
from __future__ import annotations

from pathlib import Path
from typing import NamedTuple, Optional

from backend.context_engineering.contracts import ContextPackage
from backend.skills.applicability import evaluate_skill_applicability
from backend.skills.contracts import ApplicabilityOutcome, SkillApplicabilityResult, SkillDefinition, SkillLifecycle, SkillReadinessResult, SkillReadinessStatus
from backend.skills.fingerprint import compute_skill_fingerprint
from backend.skills.loader import load_skill_definitions_from_directory
from backend.skills.readiness import evaluate_skill_readiness
from backend.skills.registry import SkillRegistry
from backend.skills.versioning import parse_skill_version
from backend.troubleshooting_intelligence.contracts import SkillSelectionOutcome

__all__ = ["SkillResolution", "get_troubleshooting_skill_registry", "resolve_skill_for_troubleshooting"]

_PRODUCTION_SKILL_DEFINITIONS_DIR = Path(__file__).resolve().parent.parent.parent / "skills" / "definitions"

_registry_cache: list[SkillRegistry] = []


def get_troubleshooting_skill_registry() -> SkillRegistry:
    """Lazily loads and memoizes the production `SkillRegistry` from
    `backend/skills/definitions/` -- the ONLY directory production Skill
    YAML files live in. Loading is a pure file-read + strict, fail-closed
    `yaml.safe_load`/pydantic validation (`backend.skills.loader`); an
    empty/missing directory yields a valid, empty registry (§25: a
    zero-production-Skill outcome is completely valid, never an error)."""
    if not _registry_cache:
        if _PRODUCTION_SKILL_DEFINITIONS_DIR.is_dir():
            definitions = load_skill_definitions_from_directory(_PRODUCTION_SKILL_DEFINITIONS_DIR)
        else:
            definitions = []
        _registry_cache.append(SkillRegistry(definitions))
    return _registry_cache[0]


class SkillResolution(NamedTuple):
    skill: Optional[SkillDefinition]
    outcome: SkillSelectionOutcome
    readiness: Optional[SkillReadinessResult]
    applicability: Optional[SkillApplicabilityResult]
    fingerprint: Optional[str]


def resolve_skill_for_troubleshooting(
    context_package: ContextPackage,
    *,
    registry: Optional[SkillRegistry] = None,
    available_capabilities: Optional[set[str]] = None,
) -> SkillResolution:
    """Pure with respect to `registry`/`context_package`/
    `available_capabilities` (no I/O of its own once `registry` is
    supplied) -- the real runtime default (`registry=None`) resolves the
    on-disk production registry via `get_troubleshooting_skill_registry`.
    """
    active_registry = registry if registry is not None else get_troubleshooting_skill_registry()
    active_skills = [d for d in active_registry.list_skills() if d.lifecycle == SkillLifecycle.ACTIVE]
    if not active_skills:
        return SkillResolution(skill=None, outcome=SkillSelectionOutcome.NONE_REGISTERED, readiness=None, applicability=None, fingerprint=None)

    evaluated: list[tuple[SkillDefinition, SkillApplicabilityResult, SkillReadinessResult]] = []
    for skill in active_skills:
        applicability = evaluate_skill_applicability(skill, context_package)
        if applicability.outcome == ApplicabilityOutcome.NOT_APPLICABLE:
            continue
        readiness = evaluate_skill_readiness(skill, context_package, available_capabilities)
        evaluated.append((skill, applicability, readiness))

    if not evaluated:
        return SkillResolution(skill=None, outcome=SkillSelectionOutcome.NONE_APPLICABLE, readiness=None, applicability=None, fingerprint=None)

    def _sort_key(entry: tuple[SkillDefinition, SkillApplicabilityResult, SkillReadinessResult]):
        return (entry[0].skill_id, parse_skill_version(entry[0].version))

    ready = sorted((e for e in evaluated if e[2].status == SkillReadinessStatus.READY), key=_sort_key)
    if ready:
        skill, applicability, readiness = ready[0]
        return SkillResolution(
            skill=skill,
            outcome=SkillSelectionOutcome.SELECTED,
            readiness=readiness,
            applicability=applicability,
            fingerprint=compute_skill_fingerprint(skill),
        )

    # None ready -- report the deterministically-first evaluated candidate's own
    # readiness/applicability for diagnostics only; never select it.
    evaluated.sort(key=_sort_key)
    _, applicability, readiness = evaluated[0]
    return SkillResolution(skill=None, outcome=SkillSelectionOutcome.NONE_READY, readiness=readiness, applicability=applicability, fingerprint=None)
