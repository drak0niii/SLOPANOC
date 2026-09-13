"""Phase 6A.5: evidence selection distinct from retrieval
(`backend/knowledge/hybrid_retrieval/evidence_selection.py`)."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.knowledge.hybrid_retrieval.contracts import EvidenceIndexRecord, HybridRetrievalCandidate
from backend.knowledge.hybrid_retrieval.evidence_selection import select_evidence

_NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _candidate(evidence_id: str) -> HybridRetrievalCandidate:
    record = EvidenceIndexRecord(
        evidence_id=evidence_id, knowledge_id="K", version_label="1.0", section_id=evidence_id,
        is_derived=False, indexable_text="text", content_hash="h", created_at=_NOW, updated_at=_NOW,
    )
    return HybridRetrievalCandidate(record=record, channel_hits=[], fusion_score=0.1, rerank_score=0.1)


def test_retrieved_candidates_are_not_automatically_selected() -> None:
    """§31: retrieved result != evidence used -- selection is its own,
    explicit step, never implicit."""
    candidates = [_candidate("a"), _candidate("b"), _candidate("c")]
    result = select_evidence("query", candidates, max_evidence_units=2)
    assert len(result.selected) == 2
    assert result.selected == candidates[:2]


def test_budget_respected() -> None:
    candidates = [_candidate(str(i)) for i in range(10)]
    result = select_evidence("query", candidates, max_evidence_units=3)
    assert len(result.selected) == 3


def test_empty_candidates_selects_nothing_without_error() -> None:
    result = select_evidence("query", [], max_evidence_units=5)
    assert result.selected == []
    assert result.selection_reason == "no_candidates"


def test_selection_reason_recorded() -> None:
    result = select_evidence("query", [_candidate("a")], max_evidence_units=5)
    assert result.selection_reason == "top_k_within_budget"


def test_selection_never_reorders_input() -> None:
    """Selection only truncates an already-reranked list -- it never
    re-sorts."""
    candidates = [_candidate("z"), _candidate("a"), _candidate("m")]
    result = select_evidence("query", candidates, max_evidence_units=3)
    assert [c.record.evidence_id for c in result.selected] == ["z", "a", "m"]
