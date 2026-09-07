"""Phase 5.1G: structural validation of `KnowledgeRetrievalQuery`,
`KnowledgeRetrievalItem`, `KnowledgeRetrievalDiagnostic`, and
`KnowledgeRetrievalResult` -- never relevance/ranking/orchestration
behavior itself (see test_retrieval_scoring.py / test_retrieval_ranking.py
/ test_retrieval_currentness.py for that).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.applicability import ApplicabilityContext, ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.retrieval.contracts import (
    KnowledgeRetrievalDiagnostic,
    KnowledgeRetrievalDiagnosticReason,
    KnowledgeRetrievalItem,
    KnowledgeRetrievalQuery,
    KnowledgeRetrievalResult,
)

_AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _section() -> KnowledgeSection:
    return KnowledgeSection(section_id="sec-1", knowledge_id="k1", heading="Overview", sequence=0, content="content")


def _source() -> KnowledgeSource:
    return KnowledgeSource(source_system="test", source_id="doc-1")


# --- KnowledgeRetrievalQuery -------------------------------------------------


def test_query_accepts_minimal_required_fields() -> None:
    query = KnowledgeRetrievalQuery(query_text="router outage", as_of=_AS_OF, limit=5)
    assert query.query_text == "router outage"
    assert query.limit == 5
    assert query.applicability_context == ApplicabilityContext()


def test_query_rejects_blank_query_text() -> None:
    with pytest.raises(ValidationError):
        KnowledgeRetrievalQuery(query_text="   ", as_of=_AS_OF, limit=5)


def test_query_rejects_empty_query_text() -> None:
    with pytest.raises(ValidationError):
        KnowledgeRetrievalQuery(query_text="", as_of=_AS_OF, limit=5)


@pytest.mark.parametrize("limit", [0, -1, -100])
def test_query_rejects_non_positive_limit(limit: int) -> None:
    with pytest.raises(ValidationError):
        KnowledgeRetrievalQuery(query_text="router outage", as_of=_AS_OF, limit=limit)


def test_query_accepts_explicit_applicability_context() -> None:
    ctx = ApplicabilityContext(dimensions={"region": ["apac"]})
    query = KnowledgeRetrievalQuery(query_text="router outage", applicability_context=ctx, as_of=_AS_OF, limit=5)
    assert query.applicability_context.dimensions == {"region": ["apac"]}


def test_query_as_of_is_required() -> None:
    with pytest.raises(ValidationError):
        KnowledgeRetrievalQuery(query_text="router outage", limit=5)  # type: ignore[call-arg]


# --- KnowledgeRetrievalItem ---------------------------------------------------


def test_item_accepts_valid_fields() -> None:
    item = KnowledgeRetrievalItem(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title="Router Outage Runbook",
        version_label="v1",
        lifecycle_status=LifecycleStatus.APPROVED,
        section=_section(),
        source=_source(),
        applicability_outcome=ApplicabilityOutcome.MATCH,
        relevance_score=0.5,
    )
    assert item.relevance_score == 0.5
    assert item.section.section_id == "sec-1"


@pytest.mark.parametrize("score", [-0.001, 1.001, -1.0, 2.0])
def test_item_rejects_relevance_score_out_of_range(score: float) -> None:
    with pytest.raises(ValidationError):
        KnowledgeRetrievalItem(
            knowledge_id="k1",
            document_type=KnowledgeDocumentType.SOP,
            title="t",
            version_label="v1",
            lifecycle_status=LifecycleStatus.APPROVED,
            section=_section(),
            source=_source(),
            applicability_outcome=ApplicabilityOutcome.MATCH,
            relevance_score=score,
        )


@pytest.mark.parametrize("score", [0.0, 1.0, 0.5])
def test_item_accepts_boundary_relevance_scores(score: float) -> None:
    item = KnowledgeRetrievalItem(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title="t",
        version_label="v1",
        lifecycle_status=LifecycleStatus.APPROVED,
        section=_section(),
        source=_source(),
        applicability_outcome=ApplicabilityOutcome.MATCH,
        relevance_score=score,
    )
    assert item.relevance_score == score


@pytest.mark.parametrize(
    "outcome", [ApplicabilityOutcome.MATCH, ApplicabilityOutcome.PARTIAL_MATCH, ApplicabilityOutcome.UNKNOWN]
)
def test_item_accepts_every_eligible_applicability_outcome(outcome: ApplicabilityOutcome) -> None:
    item = KnowledgeRetrievalItem(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title="t",
        version_label="v1",
        lifecycle_status=LifecycleStatus.APPROVED,
        section=_section(),
        source=_source(),
        applicability_outcome=outcome,
        relevance_score=0.5,
    )
    assert item.applicability_outcome is outcome


def test_item_reuses_knowledge_section_and_knowledge_source_directly() -> None:
    """Precedent: KnowledgeContextItem (5.1A) reuses domain types
    directly rather than flattening their fields -- this contract
    follows the same discipline.
    """
    section = _section()
    source = _source()
    item = KnowledgeRetrievalItem(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title="t",
        version_label="v1",
        lifecycle_status=LifecycleStatus.APPROVED,
        section=section,
        source=source,
        applicability_outcome=ApplicabilityOutcome.MATCH,
        relevance_score=0.5,
    )
    assert item.section == section
    assert item.source == source


# --- KnowledgeRetrievalDiagnostic --------------------------------------------


def test_diagnostic_accepts_minimal_fields_without_detail() -> None:
    diagnostic = KnowledgeRetrievalDiagnostic(
        knowledge_id="k1", reason=KnowledgeRetrievalDiagnosticReason.CURRENT_VERSION_AMBIGUOUS
    )
    assert diagnostic.detail is None


def test_diagnostic_reason_is_a_closed_enum() -> None:
    assert {member.value for member in KnowledgeRetrievalDiagnosticReason} == {
        "current_version_ambiguous",
        "invalid_version_family",
    }


# --- KnowledgeRetrievalResult -------------------------------------------------


def test_result_defaults_to_empty_items_and_no_diagnostics() -> None:
    result = KnowledgeRetrievalResult()
    assert result.items == []
    assert result.excluded_families == []


def test_empty_result_is_not_inherently_an_error_shape() -> None:
    """A `KnowledgeRetrievalResult()` with nothing set is a normal,
    successful "nothing matched" shape -- it has no error/status field at
    all, so there is nothing to mistake for a failure.
    """
    result = KnowledgeRetrievalResult()
    assert not hasattr(result, "error")
    assert not hasattr(result, "success")
