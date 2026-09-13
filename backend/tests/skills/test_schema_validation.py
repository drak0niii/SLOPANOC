"""Phase 6A.7 core test matrix -- SKILL SCHEMA VALIDATION (§61). Malformed
or ambiguous production Skill definitions must fail closed -- proven
against the actual final `SkillDefinition` contract and the strict
`load_skill_definition_from_yaml_text` loader.
"""
from __future__ import annotations

import pytest

from backend.skills.contracts import ContextRequirement, EvidenceRequirement, MethodologyStep, SkillDefinition, SkillLifecycle
from backend.skills.loader import SkillLoadError, load_skill_definition_from_yaml_text
from backend.context.domain.enums import ContextDimension

_VALID_YAML = """
skill_id: telco.fault_diagnosis
version: 1.0.0
lifecycle: active
name: Fault Diagnosis
description: A structured fault diagnosis methodology.
objective: Establish trusted context, inspect selected evidence.
"""


def test_valid_skill_loads_cleanly() -> None:
    sd = load_skill_definition_from_yaml_text(_VALID_YAML, source="fixture")
    assert sd.skill_id == "telco.fault_diagnosis"
    assert sd.lifecycle == SkillLifecycle.ACTIVE


def test_unknown_skill_schema_version_fails_closed() -> None:
    text = _VALID_YAML.replace("skill_id:", "skill_schema_version: \"9.9\"\nskill_id:")
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text(text, source="fixture")


def test_missing_skill_id_fails_closed() -> None:
    text = _VALID_YAML.replace("skill_id: telco.fault_diagnosis\n", "")
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text(text, source="fixture")


def test_missing_version_fails_closed() -> None:
    text = _VALID_YAML.replace("version: 1.0.0\n", "")
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text(text, source="fixture")


def test_invalid_version_string_fails_closed() -> None:
    text = _VALID_YAML.replace("version: 1.0.0", "version: not-a-version")
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text(text, source="fixture")


def test_empty_objective_fails_closed() -> None:
    text = _VALID_YAML.replace("objective: Establish trusted context, inspect selected evidence.", "objective: \"\"")
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text(text, source="fixture")


def test_invalid_lifecycle_fails_closed() -> None:
    text = _VALID_YAML.replace("lifecycle: active", "lifecycle: pending_review")
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text(text, source="fixture")


def test_unknown_fields_fail_closed() -> None:
    text = _VALID_YAML + "\nunexpected_field: some_value\n"
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text(text, source="fixture")


def test_invalid_context_dimension_fails_closed() -> None:
    with pytest.raises(Exception):
        ContextRequirement(dimension="VENDORR")


def test_duplicate_methodology_step_id_fails_closed() -> None:
    with pytest.raises(Exception):
        SkillDefinition(
            skill_id="x", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="n", description="d", objective="o",
            methodology=[
                MethodologyStep(step_id="s1", sequence=0, title="a"),
                MethodologyStep(step_id="s1", sequence=1, title="b"),
            ],
        )


def test_duplicate_methodology_sequence_fails_closed() -> None:
    with pytest.raises(Exception):
        SkillDefinition(
            skill_id="x", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="n", description="d", objective="o",
            methodology=[
                MethodologyStep(step_id="s1", sequence=0, title="a"),
                MethodologyStep(step_id="s2", sequence=0, title="b"),
            ],
        )


def test_invalid_capability_identifier_fails_closed() -> None:
    with pytest.raises(Exception):
        SkillDefinition(
            skill_id="x", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="n", description="d", objective="o",
            capability_requirements=["not a valid identifier!"],
        )


def test_unsafe_yaml_content_never_executes() -> None:
    """A YAML payload attempting a Python-object-instantiation tag must
    fail closed (rejected by safe_load itself), never execute anything."""
    unsafe = "!!python/object/apply:os.system ['echo unsafe']"
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text(unsafe, source="fixture")


def test_non_mapping_yaml_fails_closed() -> None:
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text("- just\n- a\n- list\n", source="fixture")


def test_malformed_yaml_syntax_fails_closed() -> None:
    with pytest.raises(SkillLoadError):
        load_skill_definition_from_yaml_text("skill_id: [unterminated", source="fixture")


def test_load_error_names_its_source() -> None:
    try:
        load_skill_definition_from_yaml_text("not: valid: yaml: at: all:", source="/some/fixture/path.yaml")
        pytest.fail("expected SkillLoadError")
    except SkillLoadError as exc:
        assert "/some/fixture/path.yaml" in str(exc)
