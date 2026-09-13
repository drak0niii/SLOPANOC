"""Phase 6A.9 §54-56: deterministic grounding validation -- a reference
not present in the package is always rejected, an omitted reference is
always fine, and an exact match is always accepted."""
from __future__ import annotations

import pytest

from backend.troubleshooting_intelligence.assembly import assemble_troubleshooting_intelligence
from backend.troubleshooting_intelligence.contracts import SkillSelectionOutcome, TroubleshootingIntelligenceInput
from backend.troubleshooting_intelligence.grounding import (
    TroubleshootingGroundingError,
    validate_evidence_references,
    validate_experience_references,
    validate_skill_reference,
)

from ._fixtures import make_context_package, make_evidence_item, make_evidence_selection, make_experience_record, make_skill


def _package(skill=None, skill_fingerprint=None, outcome=SkillSelectionOutcome.NONE_REGISTERED, evidence_items=None, experience_records=None):
    context_package = make_context_package(evidence_selection=make_evidence_selection(evidence_items or []))
    input_ = TroubleshootingIntelligenceInput(
        owner_id="OWNER-1",
        context_package=context_package,
        skill=skill,
        skill_fingerprint=skill_fingerprint,
        skill_selection_outcome=outcome,
        experience_records=experience_records or [],
    )
    return assemble_troubleshooting_intelligence(input_)


def test_evidence_reference_within_package_accepted() -> None:
    pkg = _package(evidence_items=[make_evidence_item("ev-1")])
    validate_evidence_references(pkg, ["ev-1"])  # must not raise


def test_evidence_reference_outside_package_rejected() -> None:
    pkg = _package(evidence_items=[make_evidence_item("ev-1")])
    with pytest.raises(TroubleshootingGroundingError):
        validate_evidence_references(pkg, ["ev-1", "ev-HALLUCINATED"])


def test_empty_evidence_reference_list_always_accepted() -> None:
    pkg = _package(evidence_items=[])
    validate_evidence_references(pkg, [])


def test_experience_reference_within_package_accepted() -> None:
    pkg = _package(experience_records=[make_experience_record("exp-1")])
    validate_experience_references(pkg, ["exp-1"])


def test_experience_reference_outside_package_rejected() -> None:
    pkg = _package(experience_records=[make_experience_record("exp-1")])
    with pytest.raises(TroubleshootingGroundingError):
        validate_experience_references(pkg, ["exp-HALLUCINATED"])


def test_skill_reference_omitted_always_accepted() -> None:
    pkg = _package(skill=make_skill(), skill_fingerprint="fp", outcome=SkillSelectionOutcome.SELECTED)
    validate_skill_reference(pkg, None, None)


def test_skill_reference_exact_match_accepted() -> None:
    skill = make_skill(skill_id="telco.x", version="1.2.3")
    pkg = _package(skill=skill, skill_fingerprint="fp", outcome=SkillSelectionOutcome.SELECTED)
    validate_skill_reference(pkg, "telco.x", "1.2.3")


def test_skill_reference_wrong_version_rejected() -> None:
    skill = make_skill(skill_id="telco.x", version="1.2.3")
    pkg = _package(skill=skill, skill_fingerprint="fp", outcome=SkillSelectionOutcome.SELECTED)
    with pytest.raises(TroubleshootingGroundingError):
        validate_skill_reference(pkg, "telco.x", "9.9.9")


def test_skill_reference_when_none_selected_rejected() -> None:
    pkg = _package(skill=None, outcome=SkillSelectionOutcome.NONE_READY)
    with pytest.raises(TroubleshootingGroundingError):
        validate_skill_reference(pkg, "telco.x", "1.0.0")
