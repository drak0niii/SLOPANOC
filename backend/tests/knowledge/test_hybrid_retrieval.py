"""Tests for dense vector relevance scoring, hybrid fusion (RRF), and neural reranking.
"""
import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import (
    KnowledgeMetadata,
    KnowledgeObject,
    KnowledgeSection,
    KnowledgeSource,
    KnowledgeVersion,
)
from backend.knowledge.domain.applicability import ApplicabilityOutcome
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalItem
from backend.knowledge.retrieval.reranking import (
    HeuristicReranker,
    compute_reciprocal_rank_fusion,
)
from backend.knowledge.retrieval.scoring import (
    DenseVectorRelevanceScorer,
    HybridRelevanceScorer,
    TokenOverlapRelevanceScorer,
)


def _make_sample_item(
    section_id: str,
    content: str,
    relevance_score: float,
    is_derived: bool = False,
) -> KnowledgeRetrievalItem:
    sec = KnowledgeSection(
        section_id=section_id,
        knowledge_id="DOC-TELCO-01",
        sequence=1,
        content=content,
    )
    return KnowledgeRetrievalItem(
        knowledge_id="DOC-TELCO-01",
        document_type=KnowledgeDocumentType.SOP,
        title="Antenna and Transceiver Diagnostics",
        version_label="v1.0",
        lifecycle_status=LifecycleStatus.APPROVED,
        section=sec,
        source=KnowledgeSource(source_system="generic_km", source_id="DOC-TELCO-01"),
        relevance_score=relevance_score,
        applicability_outcome=ApplicabilityOutcome.MATCH,
        is_derived=is_derived,
    )


def test_dense_vector_relevance_scorer() -> None:
    scorer = DenseVectorRelevanceScorer()

    obj = KnowledgeObject(
        knowledge_id="DOC-RAN-42",
        document_type=KnowledgeDocumentType.MOP,
        title="High VSWR Troubleshooting Procedure",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="km", source_id="DOC-RAN-42"),
        metadata=KnowledgeMetadata(tags=["vswr", "feeder", "rf"]),
        sections=[],
    )
    sec = KnowledgeSection(
        section_id="sec-01",
        knowledge_id="DOC-RAN-42",
        sequence=1,
        heading="Alarm Check",
        content="Inspect connector torque and check return loss telemetry.",
    )

    # Empty query returns 0.0
    assert scorer.score("", obj, sec) == 0.0
    assert scorer.score("   ", obj, sec) == 0.0

    # Relevant query produces high similarity
    score_rel = scorer.score("Inspect connector torque telemetry", obj, sec)
    assert 0.0 < score_rel <= 1.0

    # Irrelevant query produces lower similarity
    score_irrel = scorer.score("Fiber optic spectral density modulation", obj, sec)
    assert score_rel > score_irrel


def test_hybrid_relevance_scorer() -> None:
    hybrid = HybridRelevanceScorer(
        sparse_scorer=TokenOverlapRelevanceScorer(),
        dense_scorer=DenseVectorRelevanceScorer(),
        sparse_weight=0.6,
        dense_weight=0.4,
    )

    obj = KnowledgeObject(
        knowledge_id="DOC-RAN-42",
        document_type=KnowledgeDocumentType.MOP,
        title="High VSWR Troubleshooting Procedure",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="km", source_id="DOC-RAN-42"),
        sections=[],
    )
    sec = KnowledgeSection(
        section_id="sec-01",
        knowledge_id="DOC-RAN-42",
        sequence=1,
        content="Inspect connector torque and check return loss telemetry.",
    )

    score = hybrid.score("connector torque", obj, sec)
    assert 0.0 < score <= 1.0


def test_reciprocal_rank_fusion() -> None:
    item1 = _make_sample_item("sec-01", "Check VSWR", 0.9, is_derived=False)
    item2 = _make_sample_item("sec-02", "Check jumper cables", 0.7, is_derived=False)
    item3 = _make_sample_item("sec-03", "Inspect tilt", 0.5, is_derived=True)

    sparse_ranked = [item1, item2]
    dense_ranked = [item2, item3]

    fused = compute_reciprocal_rank_fusion(sparse_ranked, dense_ranked, k=60)
    assert len(fused) == 3

    # item2 appeared in both rankings, so its RRF score should be highest or competitive
    top_keys = [f.section.section_id for f in fused]
    assert "sec-02" in top_keys


@pytest.mark.asyncio
async def test_heuristic_reranker_native_precedence() -> None:
    reranker = HeuristicReranker()

    # Two items with identical score, one native and one derived
    item_native = _make_sample_item("sec-native", "Native text", 0.85, is_derived=False)
    item_derived = _make_sample_item("sec-derived", "Derived image description", 0.85, is_derived=True)

    results = await reranker.rerank(
        query="diagnostic query",
        candidates=[item_derived, item_native],
        top_k=2,
    )

    assert len(results) == 2
    # Native should strictly precede derived in equivalent bucket
    assert results[0].section.section_id == "sec-native"
    assert results[1].section.section_id == "sec-derived"
