"""A5 final corrective pass, Correction D: proves the FULL path --
`known_applicability_facts` -> captured `ApplicabilityContext` ->
`get_or_init_run_state` -> real `KnowledgeRetrievalService.retrieve()`
-- produces the correct deterministic MATCH/NOT_APPLICABLE/UNKNOWN
outcome, using the existing, UNMODIFIED `evaluate_applicability`
engine. Never asserts on live Gemini output -- this is the deterministic
Python-only portion of Correction D, structurally independent of any
model call.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.api.applicability_context_capture import register_known_applicability_context
from backend.knowledge.domain.applicability import ApplicabilityContext, ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.tools.knowledge.runtime import get_or_init_run_state

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


def _mop(knowledge_id: str, technology: list[str]) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title=f"{knowledge_id} procedure",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id=knowledge_id),
        applicability=Applicability(dimensions={"vendor": ["ericsson"], "technology": technology}),
        sections=[KnowledgeSection(section_id=f"{knowledge_id}-s0", knowledge_id=knowledge_id, sequence=0, content="Resource Allocation Failure procedure")],
    )


@pytest.mark.asyncio
async def test_case_a_known_4g_context_matches_4g_only_document() -> None:
    doc_4g = _mop("4g-only", technology=["4g"])
    register_known_applicability_context("case-a-run", ApplicabilityContext(dimensions={"vendor": ["ericsson"], "technology": ["4g"]}))
    state = get_or_init_run_state("case-a-run")

    service = KnowledgeRetrievalService(_FakeRepository([doc_4g]))
    result = await service.retrieve(
        KnowledgeRetrievalQuery(query_text="Resource Allocation Failure procedure", as_of=_AS_OF, applicability_context=state.execution_context.applicability_context, limit=10)
    )
    assert len(result.items) == 1
    assert result.items[0].applicability_outcome == ApplicabilityOutcome.MATCH


@pytest.mark.asyncio
async def test_case_a_known_4g_context_excludes_conflicting_5g_only_document() -> None:
    doc_5g_only = _mop("5g-only", technology=["5g"])
    register_known_applicability_context("case-a2-run", ApplicabilityContext(dimensions={"vendor": ["ericsson"], "technology": ["4g"]}))
    state = get_or_init_run_state("case-a2-run")

    service = KnowledgeRetrievalService(_FakeRepository([doc_5g_only]))
    result = await service.retrieve(
        KnowledgeRetrievalQuery(query_text="Resource Allocation Failure procedure", as_of=_AS_OF, applicability_context=state.execution_context.applicability_context, limit=10)
    )
    # NOT_APPLICABLE families never contribute an item at all -- excluded
    # deterministically, before ranking, never merely ranked lower.
    assert result.items == []


@pytest.mark.asyncio
async def test_case_b_known_4g5g_context_matches_combined_document() -> None:
    doc_4g5g = _mop("4g-5g-combined", technology=["4g", "5g"])
    register_known_applicability_context("case-b-run", ApplicabilityContext(dimensions={"vendor": ["ericsson"], "technology": ["5g"]}))
    state = get_or_init_run_state("case-b-run")

    service = KnowledgeRetrievalService(_FakeRepository([doc_4g5g]))
    result = await service.retrieve(
        KnowledgeRetrievalQuery(query_text="Resource Allocation Failure procedure", as_of=_AS_OF, applicability_context=state.execution_context.applicability_context, limit=10)
    )
    assert len(result.items) == 1
    assert result.items[0].applicability_outcome == ApplicabilityOutcome.MATCH


@pytest.mark.asyncio
async def test_case_c_missing_discriminator_yields_unknown_not_a_guess() -> None:
    doc_4g = _mop("4g-only-c", technology=["4g"])
    doc_4g5g = _mop("4g5g-c", technology=["4g", "5g"])
    # No known_applicability_facts registered at all -- run_state defaults
    # to an empty ApplicabilityContext, exactly the pre-A5-corrective-pass
    # behavior for a query with no explicit technology statement.
    state = get_or_init_run_state("case-c-run")

    service = KnowledgeRetrievalService(_FakeRepository([doc_4g, doc_4g5g]))
    result = await service.retrieve(
        KnowledgeRetrievalQuery(query_text="Resource Allocation Failure procedure", as_of=_AS_OF, applicability_context=state.execution_context.applicability_context, limit=10)
    )
    # Both remain eligible candidates (UNKNOWN, never NOT_APPLICABLE, and
    # never silently upgraded to MATCH) -- ambiguity is preserved for the
    # model/conflict-isolation layer to handle, never resolved by a guess.
    assert len(result.items) == 2
    assert all(item.applicability_outcome == ApplicabilityOutcome.UNKNOWN for item in result.items)


@pytest.mark.asyncio
async def test_not_applicable_never_overridden_by_high_lexical_relevance() -> None:
    # Deterministic exclusion order (lifecycle -> applicability ->
    # relevance/ranking) -- a document that is NOT_APPLICABLE must never
    # reappear merely because its text is a near-perfect lexical match.
    doc_5g_only = _mop("5g-only-strict", technology=["5g"])
    register_known_applicability_context("strict-run", ApplicabilityContext(dimensions={"vendor": ["ericsson"], "technology": ["4g"]}))
    state = get_or_init_run_state("strict-run")

    service = KnowledgeRetrievalService(_FakeRepository([doc_5g_only]))
    result = await service.retrieve(
        KnowledgeRetrievalQuery(
            query_text="5g-only-strict procedure Resource Allocation Failure procedure procedure procedure",
            as_of=_AS_OF,
            applicability_context=state.execution_context.applicability_context,
            limit=10,
        )
    )
    assert result.items == []
