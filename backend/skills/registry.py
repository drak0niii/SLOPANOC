"""Phase 6A.7: the deterministic Skill registry/catalog (§45-§49).

The registry LOADS, VALIDATES, and RESOLVES -- it never reasons, never
performs semantic search, never calls a model or an embedding provider,
and never executes a Skill or invokes a Tool (§45's own explicit "it
must NOT" list, proven by the dependency-boundary test).
"""
from __future__ import annotations

from backend.skills.contracts import SkillDefinition, SkillLifecycle
from backend.skills.versioning import parse_skill_version

__all__ = ["SkillRegistryError", "SkillNotFoundError", "SkillRegistry"]


class SkillRegistryError(ValueError):
    """Raised on ambiguous/duplicate Skill identity at registry
    construction time (§47) -- never resolved by "last-loaded wins" or
    "first-loaded wins"."""


class SkillNotFoundError(LookupError):
    """Raised by `resolve_exact`/`resolve_latest_active` on a clean,
    unambiguous lookup failure (§64) -- never returns `None` silently."""


class SkillRegistry:
    """Deterministic in-memory catalog over an already-loaded list of
    `SkillDefinition`s (from `loader.py`, or constructed directly by a
    test). Immutable once constructed -- there is no `add`/`remove`
    method, so "same skill_id + version, loaded twice" can only ever
    happen at construction time, where it is rejected (§47).
    """

    def __init__(self, definitions: list[SkillDefinition]) -> None:
        seen: dict[tuple[str, str], SkillDefinition] = {}
        for definition in definitions:
            key = (definition.skill_id, definition.version)
            if key in seen:
                raise SkillRegistryError(f"duplicate Skill identity: skill_id={definition.skill_id!r}, version={definition.version!r} appears more than once")
            seen[key] = definition
        self._by_key = seen

    def list_skills(self) -> list[SkillDefinition]:
        """§46: deterministic order -- sorted by `skill_id`, then by
        PARSED semantic version (never lexical string order, never
        insertion/filesystem order)."""
        return sorted(self._by_key.values(), key=lambda d: (d.skill_id, parse_skill_version(d.version)))

    def resolve_exact(self, skill_id: str, version: str) -> SkillDefinition:
        """§19: an explicit, historical `skill_id`+`version` reference
        always resolves to THAT exact definition if present -- never
        silently substituted with a newer or older one."""
        definition = self._by_key.get((skill_id, version))
        if definition is None:
            raise SkillNotFoundError(f"no Skill found for skill_id={skill_id!r}, version={version!r}")
        return definition

    def resolve_latest_active(self, skill_id: str) -> SkillDefinition:
        """§20/§49: the highest valid semantic version, among ACTIVE
        lifecycle only. DEPRECATED versions are excluded here (though
        still exact-resolvable via `resolve_exact`) -- and there is NO
        automatic fallback to a DEPRECATED version merely because no
        ACTIVE one exists (§49's own explicit "do not invent such
        fallback by default")."""
        active_versions = [d for d in self._by_key.values() if d.skill_id == skill_id and d.lifecycle == SkillLifecycle.ACTIVE]
        if not active_versions:
            raise SkillNotFoundError(f"no ACTIVE Skill found for skill_id={skill_id!r}")
        return max(active_versions, key=lambda d: parse_skill_version(d.version))
