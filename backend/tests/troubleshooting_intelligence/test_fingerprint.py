"""Phase 6A.9: deterministic fingerprint proofs -- same logical input
always yields the same fingerprint; a material input change always
changes it; `created_at` is excluded."""
from __future__ import annotations

from backend.troubleshooting_intelligence.assembly import assemble_troubleshooting_intelligence
from backend.troubleshooting_intelligence.contracts import SkillSelectionOutcome, TroubleshootingIntelligenceInput
from backend.troubleshooting_intelligence.fingerprint import compute_intelligence_fingerprint

from ._fixtures import make_context_package, make_experience_record, make_skill


def _build(objective="q"):
    package = make_context_package()
    input_ = TroubleshootingIntelligenceInput(owner_id="OWNER-1", objective=objective, context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED)
    return assemble_troubleshooting_intelligence(input_)


def test_created_at_excluded_from_fingerprint() -> None:
    pkg1 = _build()
    pkg2 = pkg1.model_copy(update={"created_at": pkg1.created_at.replace(year=2030)})
    assert compute_intelligence_fingerprint(pkg1) == compute_intelligence_fingerprint(pkg2)


def test_objective_change_changes_fingerprint() -> None:
    pkg1 = _build(objective="what next?")
    pkg2 = _build(objective="different question")
    assert pkg1.content_fingerprint != pkg2.content_fingerprint


def test_skill_selection_change_changes_fingerprint() -> None:
    package = make_context_package()
    skill = make_skill()
    a = assemble_troubleshooting_intelligence(
        TroubleshootingIntelligenceInput(owner_id="OWNER-1", context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED)
    )
    b = assemble_troubleshooting_intelligence(
        TroubleshootingIntelligenceInput(owner_id="OWNER-1", context_package=package, skill=skill, skill_fingerprint="fp", skill_selection_outcome=SkillSelectionOutcome.SELECTED)
    )
    assert a.content_fingerprint != b.content_fingerprint


def test_experience_content_change_changes_fingerprint() -> None:
    package = make_context_package()
    a = assemble_troubleshooting_intelligence(
        TroubleshootingIntelligenceInput(owner_id="OWNER-1", context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED, experience_records=[])
    )
    b = assemble_troubleshooting_intelligence(
        TroubleshootingIntelligenceInput(
            owner_id="OWNER-1", context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED, experience_records=[make_experience_record("exp-1")]
        )
    )
    assert a.content_fingerprint != b.content_fingerprint
