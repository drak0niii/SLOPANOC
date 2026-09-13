"""Phase 6A.7: deterministic Skill content fingerprinting (§21/§63).

Unlike 6A.6's `ContextPackage` (which carries a real, volatile `created
_at` wall-clock timestamp that must be excluded from its own
fingerprint), `SkillDefinition` is a purely declarative, static artifact
with NO timestamp/random-identity field of any kind -- so the ENTIRE
canonical model is fingerprinted, with no exclusion set needed.

Given identical logical content (field VALUES, not the load order the
caller happened to construct them in), `compute_skill_fingerprint`
always returns the identical SHA-256 hex digest. A material methodology
change, or a `version` change, always changes the fingerprint. This is
for AUDITABILITY/change-detection only -- never authorization (§21's own
explicit statement).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.skills.contracts import SkillDefinition

__all__ = ["compute_skill_fingerprint"]


def _canonical_json(skill: SkillDefinition) -> str:
    """`sort_keys=True` makes dict KEY order irrelevant. List order is a
    SEPARATE concern `json.dumps` never normalizes on its own -- several
    of `SkillDefinition`'s own list fields are semantically UNORDERED
    sets (declaring the same two `context_requirements` in a different
    order is the identical requirement, per §63's own "semantically
    unordered field reordering -> same fingerprint" requirement), so
    they are explicitly re-sorted here before serialization.
    `methodology` is the one list whose order carries real meaning --
    but that meaning is fully captured by each step's own `sequence`
    field (duplicates already forbidden by `SkillDefinition`'s own
    validator), so re-sorting by `sequence` here also makes fingerprint
    computation independent of the SOURCE list's own declaration order,
    without discarding any ordering information.
    """
    data: dict[str, Any] = skill.model_dump(mode="json")
    data["context_requirements"] = sorted(data["context_requirements"], key=lambda item: item["dimension"])
    data["capability_requirements"] = sorted(data["capability_requirements"])
    data["guardrails"] = sorted(data["guardrails"])
    data["methodology"] = sorted(data["methodology"], key=lambda item: item["sequence"])
    data["applicability"] = dict(data["applicability"])
    data["applicability"]["conditions"] = sorted(data["applicability"]["conditions"], key=lambda item: item["dimension"])
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_skill_fingerprint(skill: SkillDefinition) -> str:
    canonical = _canonical_json(skill)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
