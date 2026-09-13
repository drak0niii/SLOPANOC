"""Phase 6A.7 core test matrix -- REGISTRY (§64), including duplicate
protection (§47) with explicit, deterministic, never-last-write-wins
failure.
"""
from __future__ import annotations

import pytest

from backend.skills.contracts import SkillDefinition, SkillLifecycle
from backend.skills.registry import SkillRegistry, SkillRegistryError


def _skill(skill_id: str, version: str, lifecycle: SkillLifecycle = SkillLifecycle.ACTIVE, description: str = "d") -> SkillDefinition:
    return SkillDefinition(skill_id=skill_id, version=version, lifecycle=lifecycle, name="n", description=description, objective="o")


def test_deterministic_loading_order() -> None:
    reg = SkillRegistry([_skill("b.skill", "1.0.0"), _skill("a.skill", "2.0.0"), _skill("a.skill", "1.0.0")])
    ordering = [(d.skill_id, d.version) for d in reg.list_skills()]
    assert ordering == [("a.skill", "1.0.0"), ("a.skill", "2.0.0"), ("b.skill", "1.0.0")]


def test_stable_ordering_across_repeated_calls() -> None:
    reg = SkillRegistry([_skill("b.skill", "1.0.0"), _skill("a.skill", "1.0.0")])
    first = reg.list_skills()
    second = reg.list_skills()
    assert [d.skill_id for d in first] == [d.skill_id for d in second]


def test_duplicate_identity_rejected_at_construction() -> None:
    with pytest.raises(SkillRegistryError):
        SkillRegistry([_skill("a.skill", "1.0.0"), _skill("a.skill", "1.0.0")])


def test_duplicate_identity_rejected_even_with_different_content() -> None:
    """§47: 'same skill_id + same version + DIFFERENT or duplicate
    definition source' -- rejected regardless of whether the two
    definitions happen to be content-identical."""
    with pytest.raises(SkillRegistryError):
        SkillRegistry([_skill("a.skill", "1.0.0", description="first version of the text"), _skill("a.skill", "1.0.0", description="a materially different definition")])


def test_no_last_write_wins_behavior() -> None:
    """Confirms the rejection happens at CONSTRUCTION time -- there is no
    code path where the registry would silently end up holding only the
    last-loaded of two duplicate definitions."""
    with pytest.raises(SkillRegistryError):
        SkillRegistry([_skill("a.skill", "1.0.0", description="one"), _skill("a.skill", "1.0.0", description="two")])
    # If construction failed as expected, no registry object exists to
    # query at all -- there is nothing further to assert; the exception
    # itself IS the proof no partial/duplicate-tolerant registry formed.


def test_exact_lookup() -> None:
    reg = SkillRegistry([_skill("a.skill", "1.0.0")])
    assert reg.resolve_exact("a.skill", "1.0.0").skill_id == "a.skill"


def test_version_lookup_distinguishes_coexisting_versions() -> None:
    reg = SkillRegistry([_skill("a.skill", "1.0.0"), _skill("a.skill", "1.1.0")])
    assert reg.resolve_exact("a.skill", "1.0.0").version == "1.0.0"
    assert reg.resolve_exact("a.skill", "1.1.0").version == "1.1.0"


def test_latest_active_lookup() -> None:
    reg = SkillRegistry([_skill("a.skill", "1.0.0"), _skill("a.skill", "1.1.0")])
    assert reg.resolve_latest_active("a.skill").version == "1.1.0"


def test_registry_never_reasons_or_executes() -> None:
    """Structural proof: `SkillRegistry` has no method whose name
    suggests reasoning/search/execution."""
    import inspect

    members = [name for name, _ in inspect.getmembers(SkillRegistry, predicate=inspect.isfunction) if not name.startswith("_")]
    forbidden_fragments = ("search", "rank", "select", "execute", "invoke", "reason", "embed")
    for name in members:
        for fragment in forbidden_fragments:
            assert fragment not in name.lower(), f"unexpected reasoning/execution-shaped method: {name}"
