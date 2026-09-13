"""Phase 6A.9 §54-56: deterministic grounding validation for a
Troubleshooting Manager structured response against the Intelligence
Package it was actually given.

FAIL CLOSED: a reference to an evidence id, experience id, or Skill
identity that is not present in the package is a hard validation
failure -- the caller (`backend/agents/troubleshooting_manager/
runtime.py`) must never silently strip the bad reference while still
presenting the rest of the answer as valid; it must replace the whole
response with a safe, deterministic failure.

Deliberately takes plain `list[str]`/`str` arguments rather than the
`TroubleshootingManagerResponse` type itself, so this module never needs
to import `backend.agents.troubleshooting_manager` (which would create a
package/agents circular dependency and would also pull `google.adk`
transitively into this otherwise pure package).
"""
from __future__ import annotations

from typing import Optional

from backend.troubleshooting_intelligence.contracts import TroubleshootingIntelligencePackage

__all__ = [
    "TroubleshootingGroundingError",
    "validate_evidence_references",
    "validate_experience_references",
    "validate_skill_reference",
]


class TroubleshootingGroundingError(ValueError):
    """Raised when a Troubleshooting Manager response references an
    evidence id, experience id, or Skill identity that is not present in
    the Intelligence Package it was given -- a hallucinated reference,
    never silently accepted or stripped."""


def validate_evidence_references(package: TroubleshootingIntelligencePackage, evidence_ids: list[str]) -> None:
    known = {item.evidence_id for item in package.context_package.evidence.items}
    unknown = sorted({eid for eid in evidence_ids if eid not in known})
    if unknown:
        raise TroubleshootingGroundingError(f"response referenced unknown evidence id(s): {unknown}")


def validate_experience_references(package: TroubleshootingIntelligencePackage, experience_ids: list[str]) -> None:
    known = {item.experience_id for item in package.experience}
    unknown = sorted({eid for eid in experience_ids if eid not in known})
    if unknown:
        raise TroubleshootingGroundingError(f"response referenced unknown experience id(s): {unknown}")


def validate_skill_reference(package: TroubleshootingIntelligencePackage, skill_id: Optional[str], skill_version: Optional[str]) -> None:
    """A response may legitimately omit a Skill reference entirely
    (`skill_id is None and skill_version is None`) even when a Skill WAS
    selected -- omission is never itself an error. Anything else must
    match the package's own selected Skill EXACTLY (never 'latest', never
    a Skill outside the closed candidate this package actually carries)."""
    if skill_id is None and skill_version is None:
        return
    if package.skill is None:
        raise TroubleshootingGroundingError("response referenced a Skill, but no Skill was selected for this Intelligence Package")
    if skill_id != package.skill.skill_id or skill_version != package.skill.version:
        raise TroubleshootingGroundingError(
            f"response referenced skill_id={skill_id!r} version={skill_version!r}, but the package's selected "
            f"skill is skill_id={package.skill.skill_id!r} version={package.skill.version!r}"
        )
