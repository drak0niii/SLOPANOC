"""Phase 6A.6 core test matrix -- EVIDENCE (§51/§55). The central,
mandatory invariant: SEARCH RESULT != EVIDENCE USED, extended from
Generic KM (5.1H/5.1J) through 6A.5 into 6A.6 -- if 5 candidates were
retrieved but only 2 were selected, the Context Package must contain
EXACTLY 2 evidence items, never 5, and never any candidate that was not
already selected.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.context.domain.enums import ContextProfileOwnerKind
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackageInput
from backend.knowledge.hybrid_retrieval.contracts import (
    ChannelHit,
    EvidenceIndexRecord,
    EvidenceSelectionResult,
    HybridRetrievalCandidate,
    RetrievalChannel,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _record(evidence_id: str, is_derived: bool = False) -> EvidenceIndexRecord:
    return EvidenceIndexRecord(
        evidence_id=evidence_id, knowledge_id="K1", version_label="1.0", section_id=evidence_id,
        artifact_id=None, is_derived=is_derived, indexable_text=f"text for {evidence_id}",
        content_hash="h", created_at=_NOW, updated_at=_NOW,
    )


def _candidate(evidence_id: str, *, is_derived: bool = False, fusion=0.5, rerank=1.5) -> HybridRetrievalCandidate:
    return HybridRetrievalCandidate(
        record=_record(evidence_id, is_derived=is_derived),
        channel_hits=[ChannelHit(evidence_id=evidence_id, channel=RetrievalChannel.EXACT, raw_score=1.0)],
        fusion_score=fusion, rerank_score=rerank,
    )


def test_search_result_never_equals_evidence_used() -> None:
    """5 candidates were retrieved (per the caller-supplied count); only
    2 were selected. The package must contain exactly 2 evidence items."""
    selected = [_candidate("ev1"), _candidate("ev2")]
    sel = EvidenceSelectionResult(query_text="VSWR", selected=selected, selection_reason="top_k_within_budget")
    input_ = ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1",
        evidence_selection=sel, retrieved_candidate_count=5,
    )
    pkg = assemble_context_package(input_)
    assert pkg.evidence.selected_evidence_count == 2
    assert len(pkg.evidence.items) == 2
    assert {i.evidence_id for i in pkg.evidence.items} == {"ev1", "ev2"}
    assert pkg.assembly_trace.retrieved_candidate_count == 5
    assert pkg.assembly_trace.selected_evidence_count == 2


def test_selected_order_preserved_never_resorted() -> None:
    selected = [_candidate("ev-third", rerank=1.0), _candidate("ev-first", rerank=3.0), _candidate("ev-second", rerank=2.0)]
    sel = EvidenceSelectionResult(query_text="q", selected=selected, selection_reason="top_k_within_budget")
    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1", evidence_selection=sel)
    pkg = assemble_context_package(input_)
    assert [i.evidence_id for i in pkg.evidence.items] == ["ev-third", "ev-first", "ev-second"], "package must preserve 6A.5's OWN order verbatim, never re-sort by rerank_score itself"
    assert [i.selected_rank for i in pkg.evidence.items] == [1, 2, 3]


def test_source_and_derived_counts_and_flags_preserved() -> None:
    selected = [_candidate("ev-source", is_derived=False), _candidate("ev-derived", is_derived=True)]
    sel = EvidenceSelectionResult(query_text="q", selected=selected, selection_reason="top_k_within_budget")
    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1", evidence_selection=sel)
    pkg = assemble_context_package(input_)
    assert pkg.evidence.source_evidence_count == 1
    assert pkg.evidence.derived_evidence_count == 1
    by_id = {i.evidence_id: i for i in pkg.evidence.items}
    assert by_id["ev-source"].is_derived is False
    assert by_id["ev-derived"].is_derived is True


def test_full_identity_chain_and_channel_scores_preserved() -> None:
    record = EvidenceIndexRecord(
        evidence_id="ev1", knowledge_id="K-XLSX", version_label="2.0", section_id="S-TABLE1",
        artifact_id="ART-42", is_derived=False, indexable_text="table=Q3;range=B2:D10\nrevenue figures",
        content_hash="h", created_at=_NOW, updated_at=_NOW,
    )
    candidate = HybridRetrievalCandidate(
        record=record,
        channel_hits=[
            ChannelHit(evidence_id="ev1", channel=RetrievalChannel.LEXICAL, raw_score=0.42),
            ChannelHit(evidence_id="ev1", channel=RetrievalChannel.EXACT, raw_score=1.0),
        ],
        fusion_score=0.9, rerank_score=1.9,
    )
    sel = EvidenceSelectionResult(query_text="q", selected=[candidate], selection_reason="top_k_within_budget")
    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1", evidence_selection=sel)
    pkg = assemble_context_package(input_)
    item = pkg.evidence.items[0]
    assert item.knowledge_id == "K-XLSX"
    assert item.version_label == "2.0"
    assert item.section_id == "S-TABLE1"
    assert item.artifact_id == "ART-42"
    assert "table=Q3;range=B2:D10" in item.indexable_text
    assert item.channel_scores == {"exact": 1.0, "lexical": 0.42}
    assert item.fusion_score == 0.9
    assert item.rerank_score == 1.9


def test_provenance_manifest_traces_every_evidence_item() -> None:
    selected = [_candidate("ev1"), _candidate("ev2")]
    sel = EvidenceSelectionResult(query_text="q", selected=selected, selection_reason="top_k_within_budget")
    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1", evidence_selection=sel)
    pkg = assemble_context_package(input_)
    knowledge_entries = [e for e in pkg.provenance_manifest if e.category == "knowledge_evidence"]
    assert {e.identity for e in knowledge_entries} == {"ev1", "ev2"}
    for entry in knowledge_entries:
        assert entry.source_reference is not None and "K1:1.0:" in entry.source_reference


def test_no_embedding_vector_ever_exposed() -> None:
    """§20's own explicit prohibition -- proven structurally: `EvidenceItemView`
    has no field capable of carrying a raw embedding vector at all."""
    from backend.context_engineering.contracts import EvidenceItemView

    field_names = set(EvidenceItemView.model_fields.keys())
    assert "embedding" not in field_names
    assert not any("vector" in name.lower() for name in field_names)
