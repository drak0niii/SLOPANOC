"""Phase 6A.9 core test matrix -- bounded, structured, owner-scoped
Experience retrieval: skill-filtered querying, empty-result validity,
and query metadata never overclaiming similarity/relevance beyond the
applied structured filters."""
from __future__ import annotations

import pytest

from backend.agents.troubleshooting_manager.experience_support import DEFAULT_EXPERIENCE_LIMIT, query_experience_support
from backend.experience_memory.domain.enums import ExperienceSourceOrigin, ExperienceType
from backend.experience_memory.domain.models import ExperienceCandidate
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
async def test_empty_experience_is_valid_never_an_error() -> None:
    service = ExperienceMemoryService()
    records, metadata = await query_experience_support(owner_id="OWNER-NONE", skill_id=None, skill_version=None, case_id=None, service=service)
    assert records == []
    assert metadata.result_count == 0
    assert metadata.owner_id == "OWNER-NONE"


@pytest.mark.asyncio
async def test_query_is_owner_scoped() -> None:
    service = ExperienceMemoryService()
    await service.record_experience(_candidate("OWNER-A", "evt-1"))
    await service.record_experience(_candidate("OWNER-B", "evt-1"))

    records, metadata = await query_experience_support(owner_id="OWNER-A", skill_id=None, skill_version=None, case_id=None, service=service)
    assert len(records) == 1
    assert records[0].owner_id == "OWNER-A"


@pytest.mark.asyncio
async def test_query_filters_by_resolved_skill() -> None:
    service = ExperienceMemoryService()
    await service.record_experience(_candidate("OWNER-A", "evt-1", skill_id="telco.troubleshooting_assessment", skill_version="1.0.0"))
    await service.record_experience(_candidate("OWNER-A", "evt-2"))  # no skill

    records, metadata = await query_experience_support(owner_id="OWNER-A", skill_id="telco.troubleshooting_assessment", skill_version="1.0.0", case_id=None, service=service)
    assert len(records) == 1
    assert records[0].skill_id == "telco.troubleshooting_assessment"
    assert metadata.applied_filters.get("skill_id") == "telco.troubleshooting_assessment"


@pytest.mark.asyncio
async def test_query_is_bounded_by_default_limit() -> None:
    service = ExperienceMemoryService()
    for i in range(DEFAULT_EXPERIENCE_LIMIT + 5):
        await service.record_experience(_candidate("OWNER-A", f"evt-{i}"))

    records, metadata = await query_experience_support(owner_id="OWNER-A", skill_id=None, skill_version=None, case_id=None, service=service)
    assert len(records) == DEFAULT_EXPERIENCE_LIMIT
    assert metadata.limit == DEFAULT_EXPERIENCE_LIMIT
