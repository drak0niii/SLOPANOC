"""Phase 6A.8: deterministic Experience admission (§20-22).

`evaluate_admission` is a PURE function -- no I/O, no LLM call, no
model of any kind. It is a plain, closed-set membership check against
`ExperienceCandidate.source_origin` (`enums.ExperienceSourceOrigin`):
ALLOWED origins may ACCEPT, DENIED origins always REJECT, and anything
else (currently only `UNSPECIFIED`) is always INDETERMINATE -- never
silently promoted to ACCEPT (§22).

This function does NOT decide whether to PERSIST a candidate -- that is
`sqlalchemy/service.py::record_experience`'s job, which calls this
function first and only persists on `ACCEPT`.
"""
from __future__ import annotations

from pydantic import BaseModel

from backend.experience_memory.domain.enums import AdmissionOutcome, ExperienceSourceOrigin
from backend.experience_memory.domain.models import ExperienceCandidate

__all__ = ["AdmissionResult", "evaluate_admission"]

_ALLOWED_ORIGINS: frozenset[ExperienceSourceOrigin] = frozenset(
    {
        ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
        ExperienceSourceOrigin.EXPLICIT_CASE_RESOLUTION,
        ExperienceSourceOrigin.EXECUTED_ACTION_RESULT,
        ExperienceSourceOrigin.VALIDATED_OPERATOR_FEEDBACK,
        ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE,
    }
)

_DENIED_ORIGINS: frozenset[ExperienceSourceOrigin] = frozenset(
    {
        ExperienceSourceOrigin.LLM_SPECULATION,
        ExperienceSourceOrigin.GENERATED_RECOMMENDATION,
        ExperienceSourceOrigin.UNEXECUTED_ACTION,
        ExperienceSourceOrigin.UNVERIFIED_RCA,
        ExperienceSourceOrigin.ASSISTANT_ANSWER,
        ExperienceSourceOrigin.USER_FREE_FORM_STATEMENT,
        ExperienceSourceOrigin.RETRIEVAL_RANKING,
        ExperienceSourceOrigin.SEMANTIC_SIMILARITY,
        ExperienceSourceOrigin.MODEL_CONFIDENCE,
    }
)

# Structural invariant, checked once at import time rather than hoped
# for: every enum member is classified into EXACTLY one of the two sets,
# or is the single reserved INDETERMINATE-only value (`UNSPECIFIED`) --
# there is no origin this module silently forgets to classify.
_UNCLASSIFIED = set(ExperienceSourceOrigin) - _ALLOWED_ORIGINS - _DENIED_ORIGINS
assert _UNCLASSIFIED == {ExperienceSourceOrigin.UNSPECIFIED}, f"unclassified ExperienceSourceOrigin members: {_UNCLASSIFIED}"
assert not (_ALLOWED_ORIGINS & _DENIED_ORIGINS), "ALLOWED and DENIED origin sets must be disjoint"


class AdmissionResult(BaseModel):
    outcome: AdmissionOutcome
    reason: str


def evaluate_admission(candidate: ExperienceCandidate) -> AdmissionResult:
    """§20: deterministic ACCEPT/REJECT/INDETERMINATE. Structural
    validity (non-blank fields, schema version, Skill-version-required-
    with-Skill-id, evidence-reference shape) is already enforced by
    `ExperienceCandidate`'s own pydantic validators at construction time
    -- by the time a candidate reaches this function, it is always
    already well-formed; this function's ONLY job is the trust-origin
    decision."""
    if candidate.source_origin in _DENIED_ORIGINS:
        return AdmissionResult(
            outcome=AdmissionOutcome.REJECT,
            reason=f"source origin {candidate.source_origin.value!r} is never automatically admissible as durable Experience",
        )
    if candidate.source_origin in _ALLOWED_ORIGINS:
        return AdmissionResult(
            outcome=AdmissionOutcome.ACCEPT,
            reason=f"source origin {candidate.source_origin.value!r} is a sufficiently trustworthy observed/executed/validated source",
        )
    return AdmissionResult(
        outcome=AdmissionOutcome.INDETERMINATE,
        reason=f"source origin {candidate.source_origin.value!r} has insufficient trust classification to admit automatically",
    )
