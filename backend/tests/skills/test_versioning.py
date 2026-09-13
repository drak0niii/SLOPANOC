"""Phase 6A.7 core test matrix -- VERSIONING (§62), corrected and
extended by the 6A.7 final corrective pass (§1-§5): strict, stable
`MAJOR.MINOR.PATCH` methodology-version validation, malformed/PEP-440-
only-version rejection, semantic-ordering, and the exact-version/
latest-active resolution proofs against a real `SkillRegistry`.

Every accepted/rejected form is proven against BOTH the internal
`parse_skill_version` function AND the actual public Skill-definition
validation path (constructing a real `SkillDefinition`) -- per the
corrective pass's own explicit instruction not to merely test
`packaging.Version` in isolation.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.skills.contracts import SkillDefinition, SkillLifecycle
from backend.skills.registry import SkillNotFoundError, SkillRegistry
from backend.skills.versioning import InvalidSkillVersion, parse_skill_version

# §1's own explicit worked examples: strict, stable MAJOR.MINOR.PATCH
# forms that must be accepted.
ACCEPTED_STABLE_VERSIONS = ["0.1.0", "1.0.0", "1.9.0", "1.10.0", "25.3.7"]

# §1's own explicit worked examples: every unsupported PEP-440-only /
# malformed form that must be rejected.
REJECTED_VERSIONS = [
    "1.0",  # two-component
    "v1.0.0",  # v-prefix
    "01.0.0",  # leading zero (major)
    "1.00.0",  # leading zero (minor)
    "1.0.00",  # leading zero (patch)
    "1.0.0rc1",  # prerelease, no separator
    "1.0.0-alpha",  # prerelease
    "1.0.0-beta.1",  # prerelease with numbered segment
    "1!2.0.0",  # epoch
    "1.0.post1",  # postrelease (also two numeric components)
    "1.0.dev1",  # dev release (also two numeric components)
    "1.0.0+local",  # local version / build metadata
]


def _skill(version: str, lifecycle: SkillLifecycle = SkillLifecycle.ACTIVE, skill_id: str = "telco.fault_diagnosis") -> SkillDefinition:
    return SkillDefinition(skill_id=skill_id, version=version, lifecycle=lifecycle, name="n", description="d", objective="o")


def test_parse_valid_version() -> None:
    assert parse_skill_version("1.0.0") is not None


def test_parse_invalid_version_raises() -> None:
    with pytest.raises(InvalidSkillVersion):
        parse_skill_version("not-a-version")


def test_semantic_ordering_not_lexical() -> None:
    """1.9.0 < 1.10.0 semantically -- naive lexical string comparison
    gets this backwards ('1.10.0' < '1.9.0' lexically, since '1' < '9'
    at the second character), confirming a real bug class this module
    exists to avoid."""
    assert parse_skill_version("1.9.0") < parse_skill_version("1.10.0")
    assert "1.10.0" < "1.9.0", "sanity check: naive lexical string comparison really does get this backwards"


# --- §1/§5: strict stable MAJOR.MINOR.PATCH acceptance/rejection -------
# Tested against BOTH the internal function and the public Skill-
# definition construction path.


@pytest.mark.parametrize("version", ACCEPTED_STABLE_VERSIONS)
def test_strict_stable_version_accepted_by_parse_skill_version(version: str) -> None:
    assert parse_skill_version(version) is not None


@pytest.mark.parametrize("version", REJECTED_VERSIONS)
def test_strict_stable_version_rejected_by_parse_skill_version(version: str) -> None:
    with pytest.raises(InvalidSkillVersion):
        parse_skill_version(version)


@pytest.mark.parametrize("version", ACCEPTED_STABLE_VERSIONS)
def test_strict_stable_version_accepted_by_skill_definition(version: str) -> None:
    """The actual PUBLIC Skill-definition validation path -- not merely
    `parse_skill_version` in isolation -- accepts every strict, stable
    version form."""
    skill = _skill(version)
    assert skill.version == version


@pytest.mark.parametrize("version", REJECTED_VERSIONS)
def test_strict_stable_version_rejected_by_skill_definition(version: str) -> None:
    """The actual PUBLIC Skill-definition validation path rejects every
    PEP-440-only / non-stable / malformed version form -- fails closed
    at construction time, never silently coerced."""
    with pytest.raises(ValidationError):
        _skill(version)


def test_two_component_version_rejected() -> None:
    with pytest.raises(InvalidSkillVersion):
        parse_skill_version("1.0")


def test_leading_zero_components_rejected() -> None:
    for version in ("01.0.0", "1.00.0", "1.0.00"):
        with pytest.raises(InvalidSkillVersion):
            parse_skill_version(version)


def test_prerelease_rejected() -> None:
    for version in ("1.0.0rc1", "1.0.0-alpha", "1.0.0-beta.1"):
        with pytest.raises(InvalidSkillVersion):
            parse_skill_version(version)


def test_postrelease_rejected() -> None:
    with pytest.raises(InvalidSkillVersion):
        parse_skill_version("1.0.post1")


def test_dev_release_rejected() -> None:
    with pytest.raises(InvalidSkillVersion):
        parse_skill_version("1.0.dev1")


def test_epoch_rejected() -> None:
    with pytest.raises(InvalidSkillVersion):
        parse_skill_version("1!2.0.0")


def test_local_version_rejected() -> None:
    with pytest.raises(InvalidSkillVersion):
        parse_skill_version("1.0.0+local")


def test_v_prefix_rejected() -> None:
    with pytest.raises(InvalidSkillVersion):
        parse_skill_version("v1.0.0")


def test_semantic_ordering_remains_correct_after_strict_validation() -> None:
    assert parse_skill_version("1.9.0") < parse_skill_version("1.10.0")
    assert parse_skill_version("0.1.0") < parse_skill_version("1.0.0")
    assert parse_skill_version("25.3.7") > parse_skill_version("2.0.0")


def test_invalid_version_variants_cannot_reach_registry_construction() -> None:
    """§3's own explicit requirement: invalid/prerelease/non-stable
    versions must fail at Skill-definition construction time, so they
    can never silently participate in registry ordering at all."""
    for version in REJECTED_VERSIONS:
        with pytest.raises(ValidationError):
            _skill(version)


def test_registry_exact_version_resolution() -> None:
    reg = SkillRegistry([_skill("1.0.0"), _skill("1.1.0"), _skill("2.0.0", SkillLifecycle.DEPRECATED)])
    assert reg.resolve_exact("telco.fault_diagnosis", "1.0.0").version == "1.0.0"
    assert reg.resolve_exact("telco.fault_diagnosis", "1.1.0").version == "1.1.0"
    assert reg.resolve_exact("telco.fault_diagnosis", "2.0.0").version == "2.0.0"


def test_multiple_versions_coexist() -> None:
    reg = SkillRegistry([_skill("1.0.0"), _skill("1.1.0"), _skill("2.0.0")])
    assert len(reg.list_skills()) == 3


def test_latest_active_resolution() -> None:
    reg = SkillRegistry([_skill("1.0.0"), _skill("1.1.0"), _skill("2.0.0", SkillLifecycle.DEPRECATED)])
    assert reg.resolve_latest_active("telco.fault_diagnosis").version == "1.1.0"


def test_deprecated_excluded_from_latest_active() -> None:
    reg = SkillRegistry([_skill("1.0.0"), _skill("2.0.0", SkillLifecycle.DEPRECATED)])
    assert reg.resolve_latest_active("telco.fault_diagnosis").version == "1.0.0"


def test_deprecated_still_exact_resolvable() -> None:
    reg = SkillRegistry([_skill("1.0.0"), _skill("2.0.0", SkillLifecycle.DEPRECATED)])
    result = reg.resolve_exact("telco.fault_diagnosis", "2.0.0")
    assert result.lifecycle == SkillLifecycle.DEPRECATED


def test_explicit_historical_version_never_auto_upgraded() -> None:
    reg = SkillRegistry([_skill("1.0.0"), _skill("1.1.0"), _skill("2.0.0")])
    result = reg.resolve_exact("telco.fault_diagnosis", "1.0.0")
    assert result.version == "1.0.0", "an explicit historical reference must resolve to exactly that version, never a newer one"


def test_unknown_version_fails_cleanly() -> None:
    reg = SkillRegistry([_skill("1.0.0")])
    with pytest.raises(SkillNotFoundError):
        reg.resolve_exact("telco.fault_diagnosis", "9.9.9")


def test_unknown_skill_id_fails_cleanly() -> None:
    reg = SkillRegistry([_skill("1.0.0")])
    with pytest.raises(SkillNotFoundError):
        reg.resolve_exact("no.such.skill", "1.0.0")


def test_no_active_version_fails_cleanly_never_falls_back_to_deprecated() -> None:
    reg = SkillRegistry([_skill("1.0.0", SkillLifecycle.DEPRECATED)])
    with pytest.raises(SkillNotFoundError):
        reg.resolve_latest_active("telco.fault_diagnosis")


def test_latest_active_correct_with_double_digit_minor_version() -> None:
    """§71's own explicit worked example: 1.10.0 must be recognized as
    newer than 1.1.0/1.9.0, never a lexical-ordering error."""
    reg = SkillRegistry([_skill("1.0.0"), _skill("1.1.0"), _skill("2.0.0", SkillLifecycle.DEPRECATED), _skill("1.10.0")])
    assert reg.resolve_latest_active("telco.fault_diagnosis").version == "1.10.0"
