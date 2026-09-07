"""The generic retrieval query/result contracts.

Only STRUCTURAL validation belongs here -- non-blank query text, a
positive limit -- never relevance/ranking behavior itself (scoring.py)
or orchestration (service.py).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from backend.knowledge.domain.applicability import ApplicabilityContext, ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource, require_non_blank


class KnowledgeRetrievalQuery(BaseModel):
    """One retrieval request. `as_of` is always explicit -- this
    contract never calls the wall clock, so the same query always
    resolves currentness identically (see governance/versioning.py's own
    "as_of must be explicit" discipline, reused unchanged). No agent,
    ADK, or Gemini type appears anywhere in this contract.
    """

    query_text: str
    applicability_context: ApplicabilityContext = Field(default_factory=ApplicabilityContext)
    as_of: datetime
    limit: int

    @field_validator("query_text")
    @classmethod
    def _query_text_non_blank(cls, value: str) -> str:
        return require_non_blank(value, "query_text")

    @field_validator("limit")
    @classmethod
    def _limit_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("limit must be greater than zero")
        return value


class KnowledgeRetrievalItem(BaseModel):
    """One bounded, self-describing retrieval result unit -- SECTION
    granularity, never a whole document. `knowledge_id`/`version_label`/
    `section.section_id` together are unambiguous identity; the rest is
    carried through unmodified from the governed `KnowledgeObject`/
    `KnowledgeSection` this was derived from -- never rewritten,
    summarized, or re-chunked.

    Deliberately reuses `KnowledgeSection`/`KnowledgeSource` directly
    (the same domain types `KnowledgeContextItem` already reuses, 5.1A)
    rather than flattening/duplicating their fields here.

    This is NOT a provenance/evidence assertion (5.1H owns that) and NOT
    the future Knowledge Context contract -- it is retrieval's own,
    narrower output shape; 5.1H/5.1I may transform this later into
    whatever contract they need.
    """

    knowledge_id: str
    document_type: KnowledgeDocumentType
    title: str
    version_label: str
    lifecycle_status: LifecycleStatus
    section: KnowledgeSection
    source: KnowledgeSource
    applicability_outcome: ApplicabilityOutcome = Field(
        description="MATCH, PARTIAL_MATCH, or UNKNOWN -- NOT_APPLICABLE sections are excluded before an item is ever constructed. Uncertainty (PARTIAL_MATCH/UNKNOWN) is retained explicitly, never silently upgraded to MATCH."
    )
    relevance_score: float = Field(description="0.0-1.0, from the KnowledgeRelevanceScorer that produced this item. Zero-relevance sections are excluded before an item is ever constructed.")

    @field_validator("relevance_score")
    @classmethod
    def _relevance_score_in_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("relevance_score must be between 0.0 and 1.0")
        return value


class KnowledgeRetrievalDiagnosticReason(str, Enum):
    """A closed, generic set of reasons a whole version family was
    excluded from retrieval -- never a raw exception, never model-
    generated text, never document content.
    """

    CURRENT_VERSION_AMBIGUOUS = "current_version_ambiguous"
    INVALID_VERSION_FAMILY = "invalid_version_family"


class KnowledgeRetrievalDiagnostic(BaseModel):
    """A small, deterministic record that one `knowledge_id` family was
    excluded from a retrieval result, and why -- distinguishing
    "successful retrieval with zero matching sections" from "a family
    existed but its governance state could not be safely resolved".
    `detail` is always a short, safe, deterministic string (e.g. an
    exception class name, or the competing version labels for an
    AMBIGUOUS family) -- never a raw stack trace, never document body
    content.
    """

    knowledge_id: str
    reason: KnowledgeRetrievalDiagnosticReason
    detail: Optional[str] = None


class KnowledgeRetrievalResult(BaseModel):
    """The typed, bounded retrieval output. `items` never exceeds the
    requesting query's `limit`. An empty `items` list with no
    `excluded_families` is a normal, successful "nothing matched"
    result -- never an error.
    """

    items: list[KnowledgeRetrievalItem] = Field(default_factory=list)
    excluded_families: list[KnowledgeRetrievalDiagnostic] = Field(default_factory=list)
