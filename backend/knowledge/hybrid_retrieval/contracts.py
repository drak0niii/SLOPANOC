"""Phase 6A.5: the hybrid retrieval / evidence-selection contracts.

Only STRUCTURAL validation belongs here -- mirrors the discipline
`backend/knowledge/retrieval/contracts.py` (5.1G) already established.
No Gemini/ADK/agent type appears anywhere in this module.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

__all__ = [
    "RetrievalChannel",
    "EvidenceIndexRecord",
    "ChannelHit",
    "HybridRetrievalQuery",
    "HybridRetrievalCandidate",
    "HybridRetrievalResult",
    "EvidenceSelectionResult",
    "RetrievalTelemetry",
]


class RetrievalChannel(str, Enum):
    """The three hybrid-retrieval channels (§ instruction diagram) --
    a closed, small vocabulary; never conflated with `NarrowingReasonCode`
    (6A.4, a completely separate concern)."""

    EXACT = "exact"
    LEXICAL = "lexical"
    SEMANTIC = "semantic"


class EvidenceIndexRecord(BaseModel):
    """One indexed evidence unit -- SECTION granularity (mirrors 5.1G's
    own `KnowledgeRetrievalItem`, never document-level, per instruction
    section 7). Carries a durable route back to its governed source
    (`knowledge_id`/`version_label`/`section_id`/`artifact_id`) -- a
    vector/index result without source reconstruction is invalid, per
    instruction section 8. This is a DERIVED SEARCH INDEX (instruction
    section 23), never a second Knowledge authority -- rebuildable at any
    time from the governed `KnowledgeObject`/`KnowledgeSection` this
    record was derived from.
    """

    evidence_id: str = Field(description="Deterministic, derived from (knowledge_id, version_label, section_id) -- never a random UUID, so re-indexing the same section always produces the same identity.")
    knowledge_id: str
    version_label: str
    section_id: str
    artifact_id: Optional[str] = None
    is_derived: bool = Field(description="Mirrors 5.1G's own `KnowledgeRetrievalItem.is_derived` resolution -- SOURCE vs DERIVED origin, never conflated.")
    indexable_text: str = Field(description="The deterministic text representation actually embedded/indexed -- see indexable_text.py.")
    content_hash: str = Field(description="SHA-256 of indexable_text -- the basis for skip-unchanged/re-embed-on-change idempotent indexing (instruction section 24).")
    embedding_model: Optional[str] = None
    embedding_model_version: Optional[str] = None
    embedding_dimensions: Optional[int] = None
    embedding_generated_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ChannelHit(BaseModel):
    """One channel's own raw hit for one evidence unit -- kept separate
    per-channel (never pre-merged) so fusion (fusion.py) can inspect
    exactly which channel(s) contributed and by how much."""

    evidence_id: str
    channel: RetrievalChannel
    raw_score: float = Field(description="The channel's own native score -- NOT yet normalized/comparable across channels (see fusion.py).")


class HybridRetrievalQuery(BaseModel):
    """One 6A.5 retrieval request. `permitted_knowledge_ids` is REQUIRED
    and must come from 6A.4's own `KnowledgeNarrowingResult.permitted_
    knowledge_ids` -- this contract never computes or re-derives it
    (instruction section 4: 6A.5 must not rediscover applicability).
    """

    query_text: str
    permitted_knowledge_ids: list[str] = Field(description="6A.4's own permitted set -- an empty list means the caller has nothing to search, never 'search everything' (instruction section 41).")
    permitted_version_keys: list[tuple[str, str]] = Field(
        default_factory=list,
        description=(
            "POST-6A -- the (knowledge_id, version_label) pairs narrowing actually authorized. Narrowing "
            "resolves a CURRENT VERSION per family, so authorization is per-VERSION; collapsing it to "
            "knowledge_id alone silently widens it to every version sharing that id, including ARCHIVE and "
            "superseded ones whose sections are still in the index. When non-empty this is the authoritative "
            "constraint and is applied INSIDE each channel's own SQL. Empty preserves the pre-existing "
            "knowledge_id-only behavior for callers that have not been migrated."
        ),
    )
    limit: int = 10


class HybridRetrievalCandidate(BaseModel):
    """One fully-scored candidate after fusion + deterministic reranking
    -- still RETRIEVAL, not yet EVIDENCE (instruction section 31: search
    result != evidence used). `record` carries full source provenance.
    """

    record: EvidenceIndexRecord
    channel_hits: list[ChannelHit] = Field(default_factory=list)
    fusion_score: float
    rerank_score: float


class HybridRetrievalResult(BaseModel):
    """The full 6A.5 retrieval output -- `candidates` never exceeds
    `HybridRetrievalQuery.limit`, ordered by `rerank_score` (deterministic
    tie-breakers applied, see reranking.py)."""

    candidates: list[HybridRetrievalCandidate] = Field(default_factory=list)
    telemetry: "RetrievalTelemetry"


class EvidenceSelectionResult(BaseModel):
    """Distinct from `HybridRetrievalResult` (instruction section 31/32)
    -- a top-N search result is not automatically evidence. Records
    EXACTLY which retrieval candidates were selected, and why, without
    generating any troubleshooting answer (6A.9's own future scope,
    never this milestone's)."""

    query_text: str
    selected: list[HybridRetrievalCandidate] = Field(default_factory=list)
    selection_reason: str = Field(description="A short, deterministic, non-model-generated label, e.g. 'top_k_within_budget'.")


class RetrievalTelemetry(BaseModel):
    """Lightweight, deterministic observability (instruction section 44)
    -- counts/latencies/mode only, never document text."""

    candidate_count_from_6a4: int
    exact_hit_count: int = 0
    lexical_hit_count: int = 0
    semantic_hit_count: int = 0
    fusion_candidate_count: int = 0
    selected_evidence_count: int = 0
    semantic_channel_mode: str = Field(default="unavailable", description="'executed', 'degraded_no_vector_extension', or 'unavailable' -- never silently claims 'executed' when it was not (instruction section 42/43).")


HybridRetrievalResult.model_rebuild()
