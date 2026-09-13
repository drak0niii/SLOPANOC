"""Phase 6A.7 core test matrix -- CAPABILITY READINESS (§69). No runtime
discovery ever occurs -- `available_capabilities` is a plain, caller-
supplied `set[str] | None`.
"""
from __future__ import annotations

from backend.skills.contracts import RequirementStatus, SkillDefinition, SkillLifecycle, SkillReadinessStatus
from backend.skills.readiness import evaluate_skill_readiness
from backend.tests.skills._fixtures import build_context_package


def _skill(**overrides) -> SkillDefinition:
    defaults = dict(skill_id="telco.fault_diagnosis", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="n", description="d", objective="o", capability_requirements=["alarms.read", "tickets.read"])
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def test_capabilities_explicitly_available_are_satisfied() -> None:
    skill = _skill()
    package = build_context_package()
    result = evaluate_skill_readiness(skill, package, available_capabilities={"alarms.read", "tickets.read"})
    assert all(r.status == RequirementStatus.SATISFIED for r in result.capability_results)
    assert result.status == SkillReadinessStatus.READY


def test_capability_explicitly_missing() -> None:
    skill = _skill()
    package = build_context_package()
    result = evaluate_skill_readiness(skill, package, available_capabilities={"alarms.read"})
    by_capability = {r.capability: r.status for r in result.capability_results}
    assert by_capability["alarms.read"] == RequirementStatus.SATISFIED
    assert by_capability["tickets.read"] == RequirementStatus.MISSING
    assert result.status == SkillReadinessStatus.NOT_READY


def test_capability_availability_not_supplied_is_not_evaluated() -> None:
    skill = _skill()
    package = build_context_package()
    result = evaluate_skill_readiness(skill, package, available_capabilities=None)
    assert all(r.status == RequirementStatus.NOT_EVALUATED for r in result.capability_results)
    # Never silently SATISFIED, never silently MISSING (§35) -- the
    # overall result reflects this honestly as INDETERMINATE, neither
    # falsely READY nor falsely NOT_READY.
    assert result.status == SkillReadinessStatus.INDETERMINATE


def test_no_capabilities_declared_is_ready_regardless_of_supplied_set() -> None:
    skill = _skill(capability_requirements=[])
    package = build_context_package()
    assert evaluate_skill_readiness(skill, package, available_capabilities=None).status == SkillReadinessStatus.READY
    assert evaluate_skill_readiness(skill, package, available_capabilities=set()).status == SkillReadinessStatus.READY


def test_no_runtime_capability_discovery_occurs() -> None:
    """Structural proof (§34/§36): `readiness.py` never imports/calls
    anything that could perform runtime tool/capability discovery."""
    import inspect

    from backend.skills import readiness as module

    source = inspect.getsource(module)
    for forbidden in ("import backend.tools", "import backend.agents", "PowerAutomate", "TeamsClient", "requests.", "httpx."):
        assert forbidden not in source, f"readiness.py must never perform runtime capability discovery -- found reference to {forbidden!r}"
