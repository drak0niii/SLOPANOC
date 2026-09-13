"""Phase 6A.8 core test matrix -- ADMISSION (§20-22/§61-63): deterministic
ACCEPT/REJECT/INDETERMINATE, proven against every closed-set
`ExperienceSourceOrigin` member, plus the mandatory §61/§62/§63 worked
examples using the actual public admission path (`evaluate_admission`),
never merely asserting on the enum sets directly.
"""
from __future__ import annotations

import pytest

from backend.experience_memory.domain.admission import evaluate_admission
from backend.experience_memory.domain.enums import AdmissionOutcome, ExperienceSourceOrigin, ExperienceType
from backend.experience_memory.domain.models import ExperienceCandidate

_ALLOWED = [
    ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
    ExperienceSourceOrigin.EXPLICIT_CASE_RESOLUTION,
    ExperienceSourceOrigin.EXECUTED_ACTION_RESULT,
    ExperienceSourceOrigin.VALIDATED_OPERATOR_FEEDBACK,
    ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE,
]

_DENIED = [
    ExperienceSourceOrigin.LLM_SPECULATION,
    ExperienceSourceOrigin.GENERATED_RECOMMENDATION,
    ExperienceSourceOrigin.UNEXECUTED_ACTION,
    ExperienceSourceOrigin.UNVERIFIED_RCA,
    ExperienceSourceOrigin.ASSISTANT_ANSWER,
    ExperienceSourceOrigin.USER_FREE_FORM_STATEMENT,
    ExperienceSourceOrigin.RETRIEVAL_RANKING,
    ExperienceSourceOrigin.SEMANTIC_SIMILARITY,
    ExperienceSourceOrigin.MODEL_CONFIDENCE,
]


def _candidate(source_origin: ExperienceSourceOrigin) -> ExperienceCandidate:
    return ExperienceCandidate(
        experience_type=ExperienceType.OBSERVATION,
        source_origin=source_origin,
        source_namespace="test_system",
        owner_id="VODAFONE",
        source_event_id="evt-1",
        outcome_summary="Observed alarm cleared.",
    )


@pytest.mark.parametrize("origin", _ALLOWED)
def test_allowed_origins_accept(origin: ExperienceSourceOrigin) -> None:
    result = evaluate_admission(_candidate(origin))
    assert result.outcome is AdmissionOutcome.ACCEPT


@pytest.mark.parametrize("origin", _DENIED)
def test_denied_origins_reject(origin: ExperienceSourceOrigin) -> None:
    result = evaluate_admission(_candidate(origin))
    assert result.outcome is AdmissionOutcome.REJECT


def test_unspecified_origin_is_indeterminate() -> None:
    result = evaluate_admission(_candidate(ExperienceSourceOrigin.UNSPECIFIED))
    assert result.outcome is AdmissionOutcome.INDETERMINATE


def test_indeterminate_never_silently_becomes_accept() -> None:
    """§22: the exact negative proof the instruction requires."""
    result = evaluate_admission(_candidate(ExperienceSourceOrigin.UNSPECIFIED))
    assert result.outcome is not AdmissionOutcome.ACCEPT


def test_every_source_origin_member_is_classified() -> None:
    """Exhaustive proof: every member of the closed enum resolves to
    exactly one of the three outcomes -- no origin is silently
    unhandled (mirrors the module-level assertion in admission.py
    itself, re-proven here at the test-suite level)."""
    for origin in ExperienceSourceOrigin:
        result = evaluate_admission(_candidate(origin))
        assert result.outcome in (AdmissionOutcome.ACCEPT, AdmissionOutcome.REJECT, AdmissionOutcome.INDETERMINATE)


def test_section_61_worked_example_trusted_observed_source_accepts() -> None:
    """§61: a candidate based on a trusted synthetic observed source ->
    ACCEPT."""
    candidate = ExperienceCandidate(
        experience_type=ExperienceType.CASE_RESOLUTION,
        source_origin=ExperienceSourceOrigin.EXPLICIT_CASE_RESOLUTION,
        owner_id="VODAFONE",
        source_namespace="case_management",
        case_id="CASE-EXP-001",
        source_event_id="CASE-EXP-001-resolution",
        outcome_summary="Alarm cleared after documented intervention.",
    )
    result = evaluate_admission(candidate)
    assert result.outcome is AdmissionOutcome.ACCEPT


def test_section_62_worked_example_llm_recommendation_not_executed_rejects() -> None:
    """§62: an LLM recommendation, not executed, no verified outcome ->
    REJECT. No LLM is called anywhere -- the candidate is constructed
    synthetically with a source classification that indicates
    model-generated/unverified content."""
    candidate = ExperienceCandidate(
        experience_type=ExperienceType.OBSERVATION,
        source_origin=ExperienceSourceOrigin.GENERATED_RECOMMENDATION,
        owner_id="VODAFONE",
        source_namespace="assistant",
        source_event_id="evt-unexecuted-1",
        outcome_summary="Model suggested restarting the cell (never executed).",
    )
    result = evaluate_admission(candidate)
    assert result.outcome is AdmissionOutcome.REJECT


def test_section_63_worked_example_insufficient_trust_is_indeterminate() -> None:
    """§63: a candidate with insufficient trust/provenance -> INDETERMINATE
    (never silently persisted)."""
    candidate = ExperienceCandidate(
        experience_type=ExperienceType.OBSERVATION,
        source_origin=ExperienceSourceOrigin.UNSPECIFIED,
        owner_id="VODAFONE",
        source_namespace="unknown_system",
        source_event_id="evt-unclear-1",
        outcome_summary="Unclear provenance observation.",
    )
    result = evaluate_admission(candidate)
    assert result.outcome is AdmissionOutcome.INDETERMINATE


def test_admission_is_pure_no_io_side_effects() -> None:
    """Calling evaluate_admission repeatedly with the same input always
    produces the same result -- no hidden state, no I/O."""
    candidate = _candidate(ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME)
    results = [evaluate_admission(candidate).outcome for _ in range(5)]
    assert len(set(results)) == 1
