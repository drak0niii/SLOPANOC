"""Phase 5.1G: `KnowledgeRetrievalService` -- applicability reuse
(`domain.applicability.evaluate_applicability`, 5.1B), never duplicated.
NOT_APPLICABLE excludes a family entirely (no diagnostic -- it is a
normal, expected outcome, not an invalid-family error); MATCH,
PARTIAL_MATCH, and UNKNOWN are all eligible, with the outcome retained
verbatim on each resulting item -- never silently upgraded to MATCH.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.knowledge.domain.applicability import ApplicabilityContext, ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery
from backend.knowledge.retrieval.service import KnowledgeRetrievalService

_AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)


class _FakeRepository:
    def __init__(self, objects: list[KnowledgeObject]) -> None:
        self._objects = objects

    async def add(self, knowledge_object: KnowledgeObject) -> None:
        raise AssertionError

    async def get(self, knowledge_id: str, version_label: str) -> KnowledgeObject | None:
        raise AssertionError

    async def replace(self, knowledge_object: KnowledgeObject) -> None:
        raise AssertionError

    async def list_versions(self, knowledge_id: str) -> list[KnowledgeObject]:
        raise AssertionError

    async def list_all(self) -> list[KnowledgeObject]:
        return list(self._objects)


def _obj(knowledge_id: str, dimensions: dict[str, list[str]] | None = None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.SOP,
        title=f"{knowledge_id} router outage guide",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id=knowledge_id),
        applicability=Applicability(dimensions=dimensions or {}),
        sections=[
            KnowledgeSection(section_id=f"{knowledge_id}:v1:s0", knowledge_id=knowledge_id, sequence=0, content="router outage recovery")
        ],
    )


async def _retrieve(objects: list[KnowledgeObject], context: ApplicabilityContext | None = None):
    service = KnowledgeRetrievalService(_FakeRepository(objects))
    query = KnowledgeRetrievalQuery(
        query_text="router outage",
        applicability_context=context or ApplicabilityContext(),
        as_of=_AS_OF,
        limit=50,
    )
    return await service.retrieve(query)


@pytest.mark.asyncio
async def test_unconstrained_object_is_match_and_included() -> None:
    result = await _retrieve([_obj("k1")])
    assert len(result.items) == 1
    assert result.items[0].applicability_outcome is ApplicabilityOutcome.MATCH


@pytest.mark.asyncio
async def test_matching_dimension_is_match_and_included() -> None:
    obj = _obj("k1", {"region": ["apac"]})
    result = await _retrieve([obj], ApplicabilityContext(dimensions={"region": ["apac"]}))
    assert len(result.items) == 1
    assert result.items[0].applicability_outcome is ApplicabilityOutcome.MATCH


@pytest.mark.asyncio
async def test_conflicting_dimension_is_not_applicable_and_excluded_silently() -> None:
    obj = _obj("k1", {"region": ["emea"]})
    result = await _retrieve([obj], ApplicabilityContext(dimensions={"region": ["apac"]}))
    assert result.items == []
    assert result.excluded_families == []  # NOT_APPLICABLE is not a diagnostic -- a normal, expected exclusion.


@pytest.mark.asyncio
async def test_unknown_dimension_context_is_unknown_and_still_included() -> None:
    obj = _obj("k1", {"region": ["apac"]})
    result = await _retrieve([obj], ApplicabilityContext())
    assert len(result.items) == 1
    assert result.items[0].applicability_outcome is ApplicabilityOutcome.UNKNOWN


@pytest.mark.asyncio
async def test_partial_match_is_retained_and_never_upgraded_to_match() -> None:
    obj = _obj("k1", {"region": ["apac"], "vendor": ["acme"]})
    result = await _retrieve([obj], ApplicabilityContext(dimensions={"region": ["apac"]}))
    assert len(result.items) == 1
    assert result.items[0].applicability_outcome is ApplicabilityOutcome.PARTIAL_MATCH


@pytest.mark.asyncio
async def test_arbitrary_dimension_names_behave_identically_to_named_examples() -> None:
    obj = _obj("k1", {"some_future_dimension_2030": ["x"]})
    result = await _retrieve([obj], ApplicabilityContext(dimensions={"some_future_dimension_2030": ["x"]}))
    assert len(result.items) == 1
    assert result.items[0].applicability_outcome is ApplicabilityOutcome.MATCH


@pytest.mark.asyncio
async def test_not_applicable_object_never_reaches_relevance_scoring() -> None:
    """A NOT_APPLICABLE object is excluded before any section is ever
    scored -- even one whose content would otherwise score highly against
    the query.
    """
    obj = _obj("k1", {"region": ["emea"]})
    result = await _retrieve([obj], ApplicabilityContext(dimensions={"region": ["apac"]}))
    assert result.items == []


@pytest.mark.asyncio
async def test_match_object_with_zero_lexical_overlap_is_still_excluded() -> None:
    """APPLICABILITY != RELEVANCE: a MATCH outcome only means "not
    excluded by applicability constraints" -- it never implies the
    section is relevant to this specific query.
    """
    obj = KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title="completely unrelated topic",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="k1"),
        applicability=Applicability(dimensions={"region": ["apac"]}),
        sections=[KnowledgeSection(section_id="k1:v1:s0", knowledge_id="k1", sequence=0, content="also unrelated")],
    )
    result = await _retrieve([obj], ApplicabilityContext(dimensions={"region": ["apac"]}))
    assert result.items == []
