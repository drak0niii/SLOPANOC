"""Phase 6A.7 §67: the controlled synthetic test Skill example, and
directory-level loading (deterministic order, explicit per-file failure
naming its own path) -- real files on disk, not merely in-memory
strings.
"""
from __future__ import annotations

import pytest

from backend.skills.contracts import SkillLifecycle
from backend.skills.loader import SkillLoadError, load_skill_definitions_from_directory

_CONTROLLED_EXAMPLE_YAML = """
skill_schema_version: "1.0"
skill_id: "telco.incident_evidence_review"
version: "1.0.0"
lifecycle: active

name: "Incident Evidence Review"
description: "Systematically review trusted operational context and selected evidence."
objective: "Establish known/unknown context, review selected evidence, identify unresolved information gaps."

requires_case_context: true
evidence_requirement:
  minimum_selected_items: 1

methodology:
  - step_id: establish_context
    sequence: 0
    title: "Establish known/unknown context"
  - step_id: review_evidence
    sequence: 1
    title: "Review selected evidence"
  - step_id: identify_gaps
    sequence: 2
    title: "Identify unresolved information gaps"
"""
"""§67's own worked example -- a TEST FIXTURE, not automatically a
production Skill (§67's own explicit "It is a test fixture, not
automatically a production Skill" statement, and §72's own "production
Skill count = 0 is completely valid" decision -- see the closure
report's own §X for the explicit, documented rationale)."""


def test_controlled_example_loads_and_validates() -> None:
    from backend.skills.loader import load_skill_definition_from_yaml_text

    skill = load_skill_definition_from_yaml_text(_CONTROLLED_EXAMPLE_YAML, source="§67 fixture")
    assert skill.skill_id == "telco.incident_evidence_review"
    assert skill.lifecycle == SkillLifecycle.ACTIVE
    assert skill.requires_case_context is True
    assert skill.evidence_requirement.minimum_selected_items == 1
    assert [s.step_id for s in sorted(skill.methodology, key=lambda s: s.sequence)] == ["establish_context", "review_evidence", "identify_gaps"]


def test_directory_loading_deterministic_order(tmp_path) -> None:
    (tmp_path / "b_skill.yaml").write_text("skill_id: b.skill\nversion: 1.0.0\nlifecycle: active\nname: n\ndescription: d\nobjective: o\n", encoding="utf-8")
    (tmp_path / "a_skill.yaml").write_text("skill_id: a.skill\nversion: 1.0.0\nlifecycle: active\nname: n\ndescription: d\nobjective: o\n", encoding="utf-8")

    definitions = load_skill_definitions_from_directory(tmp_path)
    assert [d.skill_id for d in definitions] == ["a.skill", "b.skill"], "directory loading must be sorted by filename, never filesystem/glob iteration order"


def test_directory_loading_supports_yml_extension_too(tmp_path) -> None:
    (tmp_path / "c_skill.yml").write_text("skill_id: c.skill\nversion: 1.0.0\nlifecycle: active\nname: n\ndescription: d\nobjective: o\n", encoding="utf-8")
    definitions = load_skill_definitions_from_directory(tmp_path)
    assert [d.skill_id for d in definitions] == ["c.skill"]


def test_one_malformed_file_fails_closed_naming_its_own_path(tmp_path) -> None:
    (tmp_path / "good_skill.yaml").write_text("skill_id: good.skill\nversion: 1.0.0\nlifecycle: active\nname: n\ndescription: d\nobjective: o\n", encoding="utf-8")
    bad_path = tmp_path / "malformed_skill.yaml"
    bad_path.write_text("skill_id: bad.skill\nversion: not-a-real-version\nlifecycle: active\nname: n\ndescription: d\nobjective: o\n", encoding="utf-8")

    with pytest.raises(SkillLoadError) as exc_info:
        load_skill_definitions_from_directory(tmp_path)
    assert str(bad_path) in str(exc_info.value), "the failure must name the exact malformed file's own path"


def test_directory_loading_never_partially_coerces_unsafe_data(tmp_path) -> None:
    (tmp_path / "unsafe.yaml").write_text("!!python/object/apply:os.system ['echo unsafe']", encoding="utf-8")
    with pytest.raises(SkillLoadError):
        load_skill_definitions_from_directory(tmp_path)
