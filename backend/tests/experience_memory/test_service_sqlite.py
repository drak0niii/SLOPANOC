"""Phase 6A.8 core test matrix -- SERVICE (fast, isolated in-memory
SQLite): admission-to-persistence wiring, anti-enumeration lookup,
lifecycle/invalidation, and deterministic ordering. Complements
`test_experience_memory_postgres.py`'s real-PostgreSQL proofs (owner
isolation at the SQL level, provenance round-trip, bounded retrieval,
idempotency) with fast, hermetic coverage of behavior that does not
require a real database dialect.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.experience_memory.domain.enums import AdmissionOutcome, ExperienceLifecycle, ExperienceSourceOrigin, ExperienceType
from backend.experience_memory.domain.models import ExperienceCandidate, ExperienceQuery
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService


def _candidate(owner_id: str, source_event_id: str, **overrides) -> ExperienceCandidate:
    defaults = dict(
        experience_type=ExperienceType.OBSERVATION,
        source_origin=ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME,
        owner_id=owner_id,
        source_namespace="bmc",
        source_event_id=source_event_id,
        outcome_summary="Observed alarm cleared.",
    )
    defaults.update(overrides)
    return ExperienceCandidate(**defaults)


@pytest.mark.asyncio
async def test_reject_candidate_never_persisted() -> None:
    service = ExperienceMemoryService()
    candidate = _candidate("OWNER-1", "evt-1", source_origin=ExperienceSourceOrigin.LLM_SPECULATION)
    result = await service.record_experience(candidate)
    assert result.outcome is AdmissionOutcome.REJECT
    assert result.record is None

    query_result = await service.query(ExperienceQuery(owner_id="OWNER-1"))
    assert query_result.count == 0


@pytest.mark.asyncio
async def test_indeterminate_candidate_never_persisted() -> None:
    service = ExperienceMemoryService()
    candidate = _candidate("OWNER-1", "evt-1", source_origin=ExperienceSourceOrigin.UNSPECIFIED)
    result = await service.record_experience(candidate)
    assert result.outcome is AdmissionOutcome.INDETERMINATE
    assert result.record is None

    query_result = await service.query(ExperienceQuery(owner_id="OWNER-1"))
    assert query_result.count == 0


@pytest.mark.asyncio
async def test_get_by_id_foreign_owner_returns_none_anti_enumeration() -> None:
    service = ExperienceMemoryService()
    result = await service.record_experience(_candidate("OWNER-A", "evt-1"))
    real_id = result.record.experience_id

    foreign = await service.get_by_id("OWNER-B", real_id)
    assert foreign is None

    real = await service.get_by_id("OWNER-A", real_id)
    assert real is not None


@pytest.mark.asyncio
async def test_invalidate_then_default_retrieval_excludes_it() -> None:
    service = ExperienceMemoryService()
    result = await service.record_experience(_candidate("OWNER-1", "evt-1"))
    eid = result.record.experience_id

    default_result = await service.query(ExperienceQuery(owner_id="OWNER-1"))
    assert default_result.count == 1

    invalidated = await service.invalidate("OWNER-1", eid, "found to be erroneous")
    assert invalidated.lifecycle is ExperienceLifecycle.INVALIDATED
    assert invalidated.invalidation_reason == "found to be erroneous"
    assert invalidated.invalidated_at is not None

    default_after = await service.query(ExperienceQuery(owner_id="OWNER-1"))
    assert default_after.count == 0

    with_invalidated = await service.query(ExperienceQuery(owner_id="OWNER-1", include_invalidated=True))
    assert with_invalidated.count == 1
    assert with_invalidated.records[0].lifecycle is ExperienceLifecycle.INVALIDATED


@pytest.mark.asyncio
async def test_invalidate_does_not_erase_content() -> None:
    """§41: avoid making history rewrite itself -- invalidation changes
    only lifecycle/invalidated_at/invalidation_reason, never the
    record's own logical content."""
    service = ExperienceMemoryService()
    result = await service.record_experience(_candidate("OWNER-1", "evt-1", outcome_summary="Original observed outcome."))
    eid = result.record.experience_id

    await service.invalidate("OWNER-1", eid, "administrative correction")

    fetched = await service.get_by_id("OWNER-1", eid)
    assert fetched.outcome_summary == "Original observed outcome."


@pytest.mark.asyncio
async def test_invalidate_foreign_owner_returns_none_never_mutates() -> None:
    service = ExperienceMemoryService()
    result = await service.record_experience(_candidate("OWNER-A", "evt-1"))
    eid = result.record.experience_id

    outcome = await service.invalidate("OWNER-B", eid, "attempted cross-owner invalidation")
    assert outcome is None

    still_active = await service.get_by_id("OWNER-A", eid)
    assert still_active.lifecycle is ExperienceLifecycle.ACTIVE


@pytest.mark.asyncio
async def test_invalidate_blank_reason_rejected() -> None:
    service = ExperienceMemoryService()
    result = await service.record_experience(_candidate("OWNER-1", "evt-1"))
    with pytest.raises(ValueError):
        await service.invalidate("OWNER-1", result.record.experience_id, "   ")


@pytest.mark.asyncio
async def test_deterministic_ordering_independent_of_insertion_order() -> None:
    service = ExperienceMemoryService()
    for i in (3, 1, 4, 2, 0):
        await service.record_experience(_candidate("OWNER-1", f"evt-{i}"))

    result_a = await service.query(ExperienceQuery(owner_id="OWNER-1"))
    result_b = await service.query(ExperienceQuery(owner_id="OWNER-1"))
    assert [r.experience_id for r in result_a.records] == [r.experience_id for r in result_b.records]


@pytest.mark.asyncio
async def test_experience_type_filter_scoped_correctly() -> None:
    service = ExperienceMemoryService()
    await service.record_experience(_candidate("OWNER-1", "evt-obs", experience_type=ExperienceType.OBSERVATION))
    await service.record_experience(
        _candidate("OWNER-1", "evt-res", experience_type=ExperienceType.CASE_RESOLUTION, source_origin=ExperienceSourceOrigin.EXPLICIT_CASE_RESOLUTION)
    )

    result = await service.query(ExperienceQuery(owner_id="OWNER-1", experience_type=ExperienceType.CASE_RESOLUTION))
    assert result.count == 1
    assert result.records[0].experience_type is ExperienceType.CASE_RESOLUTION
    assert result.applied_filters.get("experience_type") == "case_resolution"


@pytest.mark.asyncio
async def test_skill_filter_scoped_correctly() -> None:
    service = ExperienceMemoryService()
    await service.record_experience(_candidate("OWNER-1", "evt-1", skill_id="telco.incident_evidence_review", skill_version="1.0.0"))
    await service.record_experience(_candidate("OWNER-1", "evt-2", skill_id="telco.other_skill", skill_version="2.0.0"))

    result = await service.query(ExperienceQuery(owner_id="OWNER-1", skill_id="telco.incident_evidence_review"))
    assert result.count == 1
    assert result.records[0].skill_id == "telco.incident_evidence_review"


@pytest.mark.asyncio
async def test_corrective_pass_cross_namespace_same_event_id_persists_two_records() -> None:
    """6A.8 final source-namespace corrective pass §9: same owner, same
    experience_type, same source_event_id, same source_origin,
    DIFFERENT source_namespace -> two distinct, durable Experience
    records -- both legitimate experiences persist independently,
    proven end to end through the real service/persistence path (not
    merely the pure identity function)."""
    service = ExperienceMemoryService()
    candidate_a = _candidate("OWNER-1", "evt-shared-id", source_namespace="bmc")
    candidate_b = _candidate("OWNER-1", "evt-shared-id", source_namespace="onefm", outcome_summary="A different observation was recorded by a different producer.")

    result_a = await service.record_experience(candidate_a)
    result_b = await service.record_experience(candidate_b)

    assert result_a.outcome is AdmissionOutcome.ACCEPT
    assert result_b.outcome is AdmissionOutcome.ACCEPT
    assert result_a.record.experience_id != result_b.record.experience_id

    query_result = await service.query(ExperienceQuery(owner_id="OWNER-1"))
    assert query_result.count == 2
    assert {r.experience_id for r in query_result.records} == {result_a.record.experience_id, result_b.record.experience_id}
    assert {r.source_namespace for r in query_result.records} == {"bmc", "onefm"}


@pytest.mark.asyncio
async def test_corrective_pass_same_full_source_identity_written_twice_is_idempotent() -> None:
    """6A.8 final source-namespace corrective pass §8: same owner, same
    experience_type, same source_namespace, same source_event_id,
    written twice -> exactly one durable Experience record."""
    service = ExperienceMemoryService()
    candidate = _candidate("OWNER-1", "evt-idem-corrective", source_namespace="enm")

    first = await service.record_experience(candidate)
    second = await service.record_experience(candidate)

    assert first.record.experience_id == second.record.experience_id
    assert second.deduplicated is True

    query_result = await service.query(ExperienceQuery(owner_id="OWNER-1"))
    assert query_result.count == 1


@pytest.mark.asyncio
async def test_corrective_pass_source_origin_independence_via_service() -> None:
    """§10, service-level proof: same source_origin, different
    source_namespace, same source_event_id -> still two distinct
    records."""
    service = ExperienceMemoryService()
    candidate_a = _candidate("OWNER-1", "evt-origin-independence", source_origin=ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE, source_namespace="enm")
    candidate_b = _candidate(
        "OWNER-1",
        "evt-origin-independence",
        source_origin=ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE,
        source_namespace="alarm_platform",
        outcome_summary="A distinct observation, same trust class, different producer.",
    )

    result_a = await service.record_experience(candidate_a)
    result_b = await service.record_experience(candidate_b)

    assert result_a.record.experience_id != result_b.record.experience_id

    query_result = await service.query(ExperienceQuery(owner_id="OWNER-1"))
    assert query_result.count == 2


@pytest.mark.asyncio
async def test_corrective_pass_denied_origin_remains_denied_regardless_of_namespace() -> None:
    """§21's own explicit example: source_namespace = 'some-system',
    source_origin = LLM_SPECULATION -> must still REJECT. Namespace is
    identity/provenance, never authority."""
    service = ExperienceMemoryService()
    candidate = _candidate("OWNER-1", "evt-1", source_origin=ExperienceSourceOrigin.LLM_SPECULATION, source_namespace="some-system")

    result = await service.record_experience(candidate)

    assert result.outcome is AdmissionOutcome.REJECT
    assert result.record is None


@pytest.mark.asyncio
async def test_record_experience_result_carries_source_class() -> None:
    """§19: even out of context, every persisted record is tagged
    `source_class = "EXPERIENCE"` -- proven end to end through the
    service, not just the bare model."""
    service = ExperienceMemoryService()
    result = await service.record_experience(_candidate("OWNER-1", "evt-1"))
    assert result.record.source_class == "EXPERIENCE"

    fetched = await service.get_by_id("OWNER-1", result.record.experience_id)
    assert fetched.source_class == "EXPERIENCE"
