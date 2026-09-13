"""Phase 6A.9 core test matrix -- deterministic Intelligence Assembly:
owner consistency, selected-evidence-only propagation (inherited
verbatim from the nested `ContextPackage`), SOURCE/DERIVED preservation,
Skill exact identity retention, Experience trust labeling, empty-
Experience validity, and input immutability.
"""
from __future__ import annotations

import pytest

from backend.troubleshooting_intelligence.assembly import TroubleshootingOwnerMismatchError, assemble_troubleshooting_intelligence
from backend.troubleshooting_intelligence.contracts import SkillSelectionOutcome, TroubleshootingIntelligenceInput

from ._fixtures import make_context_package, make_evidence_item, make_evidence_selection, make_experience_record, make_skill


def test_owner_mismatch_fails_closed() -> None:
    package = make_context_package(owner_id="OWNER-A")
    input_ = TroubleshootingIntelligenceInput(
        owner_id="OWNER-B",
        context_package=package,
        skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED,
    )
    with pytest.raises(TroubleshootingOwnerMismatchError):
        assemble_troubleshooting_intelligence(input_)


def test_selected_skill_and_fingerprint_preserved_verbatim() -> None:
    skill = make_skill()
    package = make_context_package()
    input_ = TroubleshootingIntelligenceInput(
        owner_id="OWNER-1",
        context_package=package,
        skill=skill,
        skill_fingerprint="abc123",
        skill_selection_outcome=SkillSelectionOutcome.SELECTED,
    )
    result = assemble_troubleshooting_intelligence(input_)
    assert result.skill is not None
    assert result.skill.skill_id == skill.skill_id
    assert result.skill.version == skill.version
    assert result.skill_fingerprint == "abc123"
    assert result.skill_selection_outcome == SkillSelectionOutcome.SELECTED


def test_none_selected_skill_stays_none() -> None:
    package = make_context_package()
    input_ = TroubleshootingIntelligenceInput(owner_id="OWNER-1", context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_READY)
    result = assemble_troubleshooting_intelligence(input_)
    assert result.skill is None
    assert result.skill_selection_outcome == SkillSelectionOutcome.NONE_READY


def test_evidence_is_inherited_verbatim_from_context_package() -> None:
    """§19/§20: the Intelligence Package never re-selects or re-filters
    evidence -- it inherits exactly what the nested `ContextPackage`
    already carries (itself already selected-only, per 6A.6)."""
    items = [make_evidence_item("ev-1", is_derived=False), make_evidence_item("ev-2", is_derived=True)]
    package = make_context_package(evidence_selection=make_evidence_selection(items))
    input_ = TroubleshootingIntelligenceInput(owner_id="OWNER-1", context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED)
    result = assemble_troubleshooting_intelligence(input_)
    ids = {i.evidence_id for i in result.context_package.evidence.items}
    assert ids == {"ev-1", "ev-2"}
    derived = {i.evidence_id: i.is_derived for i in result.context_package.evidence.items}
    assert derived == {"ev-1": False, "ev-2": True}, "SOURCE/DERIVED must survive assembly unchanged"


def test_experience_records_projected_and_labeled() -> None:
    record = make_experience_record("exp-1", outcome_summary="Observed X.", observed_facts=["fact A"])
    package = make_context_package()
    input_ = TroubleshootingIntelligenceInput(
        owner_id="OWNER-1",
        context_package=package,
        skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED,
        experience_records=[record],
    )
    result = assemble_troubleshooting_intelligence(input_)
    assert len(result.experience) == 1
    view = result.experience[0]
    assert view.source_class == "EXPERIENCE"
    assert view.experience_id == "exp-1"
    assert view.outcome_summary == "Observed X."
    assert view.observed_facts == ["fact A"]


def test_empty_experience_is_valid() -> None:
    package = make_context_package()
    input_ = TroubleshootingIntelligenceInput(owner_id="OWNER-1", context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED)
    result = assemble_troubleshooting_intelligence(input_)
    assert result.experience == []


def test_assembly_does_not_mutate_input_context_package() -> None:
    package = make_context_package()
    original_fingerprint = package.content_fingerprint
    input_ = TroubleshootingIntelligenceInput(owner_id="OWNER-1", context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED)
    assemble_troubleshooting_intelligence(input_)
    assert package.content_fingerprint == original_fingerprint, "the nested ContextPackage must never be mutated by intelligence assembly"


def test_deterministic_repeated_assembly_same_fingerprint() -> None:
    skill = make_skill()
    package = make_context_package(evidence_selection=make_evidence_selection([make_evidence_item("ev-1")]))
    record = make_experience_record("exp-1")

    def _build():
        input_ = TroubleshootingIntelligenceInput(
            owner_id="OWNER-1",
            objective="what next?",
            context_package=package,
            skill=skill,
            skill_fingerprint="fp",
            skill_selection_outcome=SkillSelectionOutcome.SELECTED,
            experience_records=[record],
        )
        return assemble_troubleshooting_intelligence(input_)

    first = _build()
    second = _build()
    assert first.content_fingerprint == second.content_fingerprint
    assert first.content_fingerprint != ""
