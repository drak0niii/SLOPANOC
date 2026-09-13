"""Phase 6A.8 core test matrix -- CONTRACTS: schema validation, schema
version fail-closed, Experience identity inputs, evidence-reference
shape, Skill-version-required-with-Skill-id, observed_facts/outcome_
summary bounds.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.experience_memory.domain.enums import ExperienceSourceOrigin, ExperienceType
from backend.experience_memory.domain.models import (
    EXPERIENCE_SCHEMA_VERSION,
    ExperienceCandidate,
    ExperienceEvidenceReference,
    ExperienceMemoryResult,
    ExperienceQuery,
)


def _candidate(**overrides) -> ExperienceCandidate:
    defaults = dict(
        experience_type=ExperienceType.OBSERVATION,
        source_origin=ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
        owner_id="VODAFONE",
        source_namespace="onefm",
        source_event_id="evt-1",
        outcome_summary="Observed alarm cleared.",
    )
    defaults.update(overrides)
    return ExperienceCandidate(**defaults)


def test_minimal_valid_candidate_constructs() -> None:
    candidate = _candidate()
    assert candidate.experience_schema_version == EXPERIENCE_SCHEMA_VERSION


def test_unknown_field_fails_closed() -> None:
    with pytest.raises(ValidationError):
        ExperienceCandidate(
            experience_type=ExperienceType.OBSERVATION,
            source_origin=ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
            owner_id="VODAFONE",
            source_namespace="onefm",
            source_event_id="evt-1",
            outcome_summary="x",
            some_unknown_field="should fail",
        )


def test_unknown_schema_version_fails_closed() -> None:
    with pytest.raises(ValidationError):
        _candidate(experience_schema_version="2.0")


@pytest.mark.parametrize("field", ["owner_id", "source_event_id", "outcome_summary"])
def test_blank_required_fields_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _candidate(**{field: "   "})


def test_owner_id_required_structurally() -> None:
    with pytest.raises(ValidationError):
        ExperienceCandidate(
            experience_type=ExperienceType.OBSERVATION,
            source_origin=ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
            source_namespace="onefm",
            source_event_id="evt-1",
            outcome_summary="x",
        )


def test_outcome_summary_bounded() -> None:
    with pytest.raises(ValidationError):
        _candidate(outcome_summary="x" * 2001)


def test_observed_facts_bounded_count() -> None:
    with pytest.raises(ValidationError):
        _candidate(observed_facts=["fact"] * 21)


def test_observed_facts_bounded_length() -> None:
    with pytest.raises(ValidationError):
        _candidate(observed_facts=["x" * 501])


def test_observed_facts_blank_entry_rejected() -> None:
    with pytest.raises(ValidationError):
        _candidate(observed_facts=["real fact", "   "])


def test_source_namespace_required_structurally() -> None:
    with pytest.raises(ValidationError):
        ExperienceCandidate(
            experience_type=ExperienceType.OBSERVATION,
            source_origin=ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
            owner_id="VODAFONE",
            source_event_id="evt-1",
            outcome_summary="x",
        )


@pytest.mark.parametrize("value", ["", "   ", "BMC", "one fm", "123bmc", "-bmc", "bmc!", "a b"])
def test_source_namespace_invalid_forms_rejected(value: str) -> None:
    """§5: blank/whitespace-only, uppercase, leading digit/hyphen, and
    embedded-whitespace forms are all rejected -- the format is a small,
    generic symbolic identifier contract, not free-form narrative."""
    with pytest.raises(ValidationError):
        _candidate(source_namespace=value)


@pytest.mark.parametrize("value", ["bmc", "teams", "onefm", "enm", "alarm_platform", "system-b", "a.b.c"])
def test_source_namespace_valid_forms_accepted(value: str) -> None:
    candidate = _candidate(source_namespace=value)
    assert candidate.source_namespace == value


def test_source_namespace_never_derived_from_source_origin() -> None:
    """§5's own explicit prohibition: source_namespace must never be
    silently derived from source_origin -- proven by constructing two
    candidates with the SAME source_origin but DIFFERENT explicit
    source_namespace values, and confirming both are preserved exactly
    as supplied, never overwritten/defaulted from source_origin."""
    a = _candidate(source_origin=ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE, source_namespace="bmc")
    b = _candidate(source_origin=ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE, source_namespace="onefm")
    assert a.source_namespace == "bmc"
    assert b.source_namespace == "onefm"
    assert a.source_origin == b.source_origin


def test_skill_version_required_when_skill_id_present() -> None:
    """§28: never store only 'latest Skill' -- an id with no version
    fails closed rather than implying 'any'/'latest'."""
    with pytest.raises(ValidationError):
        _candidate(skill_id="telco.incident_evidence_review")


def test_skill_id_and_version_together_valid() -> None:
    candidate = _candidate(skill_id="telco.incident_evidence_review", skill_version="1.0.0")
    assert candidate.skill_version == "1.0.0"


def test_no_skill_at_all_is_valid() -> None:
    """§28: an experience without a Skill is valid if the source
    legitimately had no Skill."""
    candidate = _candidate()
    assert candidate.skill_id is None
    assert candidate.skill_version is None


def test_evidence_reference_requires_at_least_one_identifier() -> None:
    with pytest.raises(ValidationError):
        ExperienceEvidenceReference()


def test_evidence_reference_single_identifier_valid() -> None:
    ref = ExperienceEvidenceReference(knowledge_id="K1")
    assert ref.knowledge_id == "K1"


def test_query_owner_id_required_structurally() -> None:
    with pytest.raises(ValidationError):
        ExperienceQuery()


def test_query_owner_id_blank_rejected() -> None:
    with pytest.raises(ValidationError):
        ExperienceQuery(owner_id="   ")


def test_query_limit_bounded() -> None:
    with pytest.raises(ValidationError):
        ExperienceQuery(owner_id="X", limit=0)
    with pytest.raises(ValidationError):
        ExperienceQuery(owner_id="X", limit=201)


def test_query_default_limit_and_include_invalidated() -> None:
    query = ExperienceQuery(owner_id="X")
    assert query.limit == 50
    assert query.include_invalidated is False


def test_empty_memory_result_is_a_valid_construction() -> None:
    """§53: `records = []` is a valid, ordinary result -- never an
    error, never requiring any special-case construction."""
    result = ExperienceMemoryResult(owner_id="X", records=[], count=0, limit=50)
    assert result.records == []
    assert result.count == 0
