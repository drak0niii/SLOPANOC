"""Phase 6A.5: deterministic reranking (`backend/knowledge/hybrid_retrieval/reranking.py`)."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.knowledge.hybrid_retrieval.contracts import ChannelHit, EvidenceIndexRecord, HybridRetrievalCandidate, RetrievalChannel
from backend.knowledge.hybrid_retrieval.reranking import rerank

_NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _record(evidence_id: str, is_derived: bool = False) -> EvidenceIndexRecord:
    return EvidenceIndexRecord(
        evidence_id=evidence_id, knowledge_id="K", version_label="1.0", section_id=evidence_id,
        is_derived=is_derived, indexable_text="text", content_hash="h", created_at=_NOW, updated_at=_NOW,
    )


def _candidate(evidence_id: str, fusion_score: float, channels: list[RetrievalChannel], is_derived: bool = False) -> HybridRetrievalCandidate:
    return HybridRetrievalCandidate(
        record=_record(evidence_id, is_derived),
        channel_hits=[ChannelHit(evidence_id=evidence_id, channel=c, raw_score=1.0) for c in channels],
        fusion_score=fusion_score,
        rerank_score=0.0,
    )


def test_exact_match_outranks_vague_semantic_of_similar_fusion_score() -> None:
    """§35: an exact hit must not lose to a vaguely semantically similar
    document, even when their RRF fusion scores are close."""
    exact = _candidate("exact-doc", fusion_score=0.05, channels=[RetrievalChannel.EXACT])
    semantic = _candidate("semantic-doc", fusion_score=0.06, channels=[RetrievalChannel.SEMANTIC])
    ranked = rerank([exact, semantic])
    assert ranked[0].record.evidence_id == "exact-doc"


def test_source_preferred_over_derived_at_equal_signals() -> None:
    """§29: source evidence preferred over derived when otherwise equal."""
    source = _candidate("source-doc", fusion_score=0.1, channels=[RetrievalChannel.LEXICAL], is_derived=False)
    derived = _candidate("derived-doc", fusion_score=0.1, channels=[RetrievalChannel.LEXICAL], is_derived=True)
    ranked = rerank([derived, source])
    assert ranked[0].record.evidence_id == "source-doc"


def test_derived_still_surfaces_when_it_materially_improves_relevance() -> None:
    """§29: derived evidence is never blanket-suppressed -- a materially
    higher fusion score for derived content still outranks a barely
    relevant source candidate."""
    strong_derived = _candidate("strong-derived", fusion_score=0.5, channels=[RetrievalChannel.SEMANTIC, RetrievalChannel.LEXICAL], is_derived=True)
    weak_source = _candidate("weak-source", fusion_score=0.01, channels=[RetrievalChannel.LEXICAL], is_derived=False)
    ranked = rerank([weak_source, strong_derived])
    assert ranked[0].record.evidence_id == "strong-derived"


def test_multi_channel_agreement_ranks_higher_than_single_channel_of_equal_fusion() -> None:
    multi = _candidate("multi", fusion_score=0.1, channels=[RetrievalChannel.LEXICAL, RetrievalChannel.SEMANTIC])
    single = _candidate("single", fusion_score=0.1, channels=[RetrievalChannel.LEXICAL])
    ranked = rerank([single, multi])
    assert ranked[0].record.evidence_id == "multi"


def test_deterministic_same_inputs_produce_same_order_repeatedly() -> None:
    candidates = [
        _candidate("a", 0.2, [RetrievalChannel.LEXICAL]),
        _candidate("b", 0.2, [RetrievalChannel.SEMANTIC]),
        _candidate("c", 0.5, [RetrievalChannel.EXACT]),
    ]
    order1 = [c.record.evidence_id for c in rerank(candidates)]
    order2 = [c.record.evidence_id for c in rerank(list(reversed(candidates)))]
    order3 = [c.record.evidence_id for c in rerank(candidates)]
    assert order1 == order2 == order3


def test_stable_tie_breaker_on_fully_equal_signals() -> None:
    a = _candidate("aaa", 0.1, [RetrievalChannel.LEXICAL])
    b = _candidate("bbb", 0.1, [RetrievalChannel.LEXICAL])
    ranked = rerank([b, a])
    assert [c.record.evidence_id for c in ranked] == ["aaa", "bbb"]


def test_no_model_call_source_proof() -> None:
    import ast
    import inspect

    from backend.knowledge.hybrid_retrieval import reranking as module

    tree = ast.parse(inspect.getsource(module))
    forbidden = {"google.adk", "google.genai"}
    imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    imported |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not (imported & forbidden)
