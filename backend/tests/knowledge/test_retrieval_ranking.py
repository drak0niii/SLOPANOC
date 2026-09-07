"""Phase 5.1G: `KnowledgeRetrievalService` -- deterministic ranking,
limit application, repository-order independence, section-level (never
document-level) candidates, and content fidelity.
"""
from __future__ import annotations

import random
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


def _obj(
    knowledge_id: str,
    sections: list[KnowledgeSection],
    title: str = "guide",
    dimensions: dict[str, list[str]] | None = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.SOP,
        title=title,
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id=knowledge_id),
        applicability=Applicability(dimensions=dimensions or {}),
        sections=sections,
    )


def _section(knowledge_id: str, section_id: str, sequence: int, content: str) -> KnowledgeSection:
    return KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=sequence, content=content)


async def _retrieve(objects: list[KnowledgeObject], query_text: str = "router outage", limit: int = 50, context: ApplicabilityContext | None = None):
    service = KnowledgeRetrievalService(_FakeRepository(objects))
    query = KnowledgeRetrievalQuery(query_text=query_text, applicability_context=context or ApplicabilityContext(), as_of=_AS_OF, limit=limit)
    return await service.retrieve(query)


# --- higher relevance ranks first ---------------------------------------------


@pytest.mark.asyncio
async def test_higher_relevance_ranks_before_lower_relevance() -> None:
    high = _obj("k-high", [_section("k-high", "k-high:s0", 0, "router outage")], title="router outage")
    low = _obj("k-low", [_section("k-low", "k-low:s0", 0, "router")], title="router")
    result = await _retrieve([low, high])
    assert [item.knowledge_id for item in result.items] == ["k-high", "k-low"]


# --- applicability-certainty tie-break for equal relevance ---------------------


@pytest.mark.asyncio
async def test_equal_relevance_breaks_tie_by_applicability_certainty() -> None:
    match_obj = _obj("k-match", [_section("k-match", "k-match:s0", 0, "router outage")], title="router outage")
    partial_obj = _obj(
        "k-partial",
        [_section("k-partial", "k-partial:s0", 0, "router outage")],
        title="router outage",
        dimensions={"region": ["apac"], "vendor": ["acme"]},
    )
    unknown_obj = _obj(
        "k-unknown",
        [_section("k-unknown", "k-unknown:s0", 0, "router outage")],
        title="router outage",
        dimensions={"vendor": ["acme"]},
    )
    context = ApplicabilityContext(dimensions={"region": ["apac"]})
    result = await _retrieve([unknown_obj, partial_obj, match_obj], context=context)

    outcomes = [item.applicability_outcome for item in result.items]
    assert outcomes == [ApplicabilityOutcome.MATCH, ApplicabilityOutcome.PARTIAL_MATCH, ApplicabilityOutcome.UNKNOWN]


# --- deterministic identity tie-breakers when relevance and certainty tie -----


@pytest.mark.asyncio
async def test_final_tiebreak_is_deterministic_identity_ordering() -> None:
    obj_b = _obj("k-b", [_section("k-b", "k-b:s0", 0, "router outage")], title="router outage")
    obj_a = _obj("k-a", [_section("k-a", "k-a:s0", 0, "router outage")], title="router outage")
    result = await _retrieve([obj_b, obj_a])
    assert [item.knowledge_id for item in result.items] == ["k-a", "k-b"]


@pytest.mark.asyncio
async def test_multiple_sections_within_one_object_ordered_by_sequence_on_tie() -> None:
    obj = _obj(
        "k1",
        [
            _section("k1", "k1:s1", 1, "router outage"),
            _section("k1", "k1:s0", 0, "router outage"),
        ],
        title="router outage",
    )
    result = await _retrieve([obj])
    assert [item.section.section_id for item in result.items] == ["k1:s0", "k1:s1"]


# --- limit applied only after full ranking -------------------------------------


@pytest.mark.asyncio
async def test_limit_truncates_only_after_full_ranking() -> None:
    high = _obj("k-high", [_section("k-high", "k-high:s0", 0, "router outage")], title="router outage")
    low = _obj("k-low", [_section("k-low", "k-low:s0", 0, "router")], title="router")
    result = await _retrieve([low, high], limit=1)
    assert len(result.items) == 1
    assert result.items[0].knowledge_id == "k-high"


@pytest.mark.asyncio
async def test_limit_larger_than_eligible_items_returns_all() -> None:
    obj = _obj("k1", [_section("k1", "k1:s0", 0, "router outage")], title="router outage")
    result = await _retrieve([obj], limit=1000)
    assert len(result.items) == 1


# --- repository-order independence ---------------------------------------------


@pytest.mark.asyncio
async def test_result_is_independent_of_repository_return_order() -> None:
    objects = [
        _obj(f"k{i}", [_section(f"k{i}", f"k{i}:s0", 0, f"router outage variant {i % 3}")], title=f"guide {i}")
        for i in range(12)
    ]
    shuffled = list(objects)
    random.Random(42).shuffle(shuffled)

    ordered_result = await _retrieve(objects, limit=100)
    shuffled_result = await _retrieve(shuffled, limit=100)

    assert [(i.knowledge_id, i.relevance_score) for i in ordered_result.items] == [
        (i.knowledge_id, i.relevance_score) for i in shuffled_result.items
    ]


# --- version label carries no precedence ---------------------------------------


@pytest.mark.asyncio
async def test_version_label_is_never_used_as_a_ranking_signal() -> None:
    """Two independent knowledge_id families with identical relevance --
    an alphabetically "larger" version label must not outrank a "smaller"
    one for any reason other than the documented final identity
    tie-breaker (which uses knowledge_id first, not version_label
    magnitude as a proxy for recency).
    """
    obj_with_high_label = KnowledgeObject(
        knowledge_id="k-a",
        document_type=KnowledgeDocumentType.SOP,
        title="router outage",
        version=KnowledgeVersion(label="99.0"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="k-a"),
        sections=[_section("k-a", "k-a:s0", 0, "router outage")],
    )
    obj_with_low_label = KnowledgeObject(
        knowledge_id="k-b",
        document_type=KnowledgeDocumentType.SOP,
        title="router outage",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="k-b"),
        sections=[_section("k-b", "k-b:s0", 0, "router outage")],
    )
    result = await _retrieve([obj_with_high_label, obj_with_low_label])
    # Tie-break is by knowledge_id ("k-a" < "k-b"), independent of the
    # version label magnitude ("99.0" vs "1.0").
    assert [item.knowledge_id for item in result.items] == ["k-a", "k-b"]


# --- section granularity: never document-level, never re-chunked --------------


@pytest.mark.asyncio
async def test_each_eligible_section_produces_its_own_item_not_one_per_document() -> None:
    obj = _obj(
        "k1",
        [
            _section("k1", "k1:s0", 0, "router outage recovery"),
            _section("k1", "k1:s1", 1, "unrelated maintenance topic"),
        ],
        title="guide",
    )
    result = await _retrieve([obj])
    assert len(result.items) == 1
    assert result.items[0].section.section_id == "k1:s0"


@pytest.mark.asyncio
async def test_section_content_is_never_rewritten_or_truncated() -> None:
    original_content = "Router outage recovery: step one, step two, step three."
    obj = _obj("k1", [_section("k1", "k1:s0", 0, original_content)], title="guide")
    result = await _retrieve([obj])
    assert result.items[0].section.content == original_content


@pytest.mark.asyncio
async def test_source_is_carried_through_unmodified() -> None:
    obj = KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title="router outage",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="sharepoint", source_id="doc-42", source_uri="https://example/doc-42"),
        sections=[_section("k1", "k1:s0", 0, "router outage")],
    )
    result = await _retrieve([obj])
    assert result.items[0].source.source_system == "sharepoint"
    assert result.items[0].source.source_id == "doc-42"
    assert result.items[0].source.source_uri == "https://example/doc-42"


# --- zero-relevance sections never appear --------------------------------------


@pytest.mark.asyncio
async def test_zero_relevance_sections_are_excluded_entirely() -> None:
    obj = _obj(
        "k1",
        [
            _section("k1", "k1:s0", 0, "router outage"),
            _section("k1", "k1:s1", 1, "totally unrelated content"),
        ],
        title="guide",
    )
    result = await _retrieve([obj])
    assert [item.section.section_id for item in result.items] == ["k1:s0"]
