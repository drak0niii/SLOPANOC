"""The generic agent-facing tool contracts: model-controlled request,
backend-trusted execution context, model-safe evidence view, and the
internal execution result that keeps trusted state separate from what a
model ever sees.

Only STRUCTURAL validation belongs here -- non-blank query text, a
bounded limit, closed (`extra="forbid"`) request/context contracts --
never the actual retrieval/provenance orchestration itself (service.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.knowledge.domain.applicability import ApplicabilityContext, ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType
from backend.knowledge.domain.models import require_non_blank
from backend.knowledge.provenance.contracts import KnowledgeEvidenceSelectionKey, KnowledgeEvidenceSet
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalDiagnosticReason

DEFAULT_SEARCH_LIMIT = 5
MAXIMUM_SEARCH_LIMIT = 10
"""A small TECHNICAL cap on how many sections one `knowledge_search` call
may request -- not business/domain hardcoding. It exists solely to stop
a model from requesting an unbounded corpus; 5.1G's own `limit` already
enforces this once it reaches retrieval, but the tool-facing request must
reject an excessive value outright rather than silently clamping it.
"""


class KnowledgeToolError(Exception):
    """Base class for every error this package raises. Never constructed
    from raw document/payload content -- messages carry identity
    information only.
    """


class KnowledgeToolConsistencyError(KnowledgeToolError):
    """Raised when a 5.1G `KnowledgeRetrievalResult` and a 5.1H
    `KnowledgeEvidenceSet` do not correlate one-to-one by
    `(knowledge_id, version_label, section_id)` identity -- a retrieval
    item with no corresponding validated evidence, an evidence item with
    no corresponding retrieval item, or a duplicate identity on either
    side. This is never expected in normal operation (provenance is
    built directly from the retrieval result that produced it); it exists
    as a fail-closed guard against ever assuming two lists "line up"
    merely because their current implementations happen to preserve
    order.
    """


class KnowledgeSearchToolRequest(BaseModel):
    """The ONLY model-controlled input to `knowledge_search`. A CLOSED
    contract (`extra="forbid"`): a model cannot supply `source_system`,
    `document_type`, `version_label`, `lifecycle_status`, `content`,
    `evidence`, `provenance`, `as_of`, `applicability_context`, or any
    other field -- only `query_text` and `limit` exist at all. `as_of`
    and applicability facts are trusted, backend-supplied execution
    context (`KnowledgeToolExecutionContext`), never model input -- see
    this module's own `KnowledgeToolExecutionContext` docstring.
    """

    model_config = ConfigDict(extra="forbid")

    query_text: str
    limit: int = DEFAULT_SEARCH_LIMIT

    @field_validator("query_text")
    @classmethod
    def _query_text_non_blank(cls, value: str) -> str:
        return require_non_blank(value, "query_text")

    @field_validator("limit")
    @classmethod
    def _limit_within_bounds(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("limit must be greater than zero")
        if value > MAXIMUM_SEARCH_LIMIT:
            raise ValueError(f"limit must not exceed {MAXIMUM_SEARCH_LIMIT} -- got {value}")
        return value


class KnowledgeToolExecutionContext(BaseModel):
    """The TRUSTED, backend/orchestrator-supplied execution context --
    never model input. `as_of` is always explicit (this contract never
    calls the wall clock); `applicability_context` may validly default to
    empty (e.g. 5.1J's first consumer, before any validated applicability
    facts exist) but is never invented -- the caller supplies only
    already-known operational facts. A CLOSED contract
    (`extra="forbid"`), exactly like `KnowledgeSearchToolRequest`: this is
    where `as_of`/`applicability_context` belong, and nowhere else.
    """

    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    applicability_context: ApplicabilityContext = Field(default_factory=ApplicabilityContext)


class KnowledgeToolEvidenceItem(BaseModel):
    """One model-safe, provenance-backed evidence item. Authoritative
    fields (`title`, `document_type`, `section_heading`, `content`,
    `source_system`, `source_id`, `source_display_name`,
    `source_locator`) are constructed EXCLUSIVELY from a validated 5.1H
    `KnowledgeEvidenceItem` -- never copied from the pre-provenance 5.1G
    `KnowledgeRetrievalItem`. `applicability_outcome` and
    `relevance_score` come from the 5.1G retrieval decision (provenance
    does not carry either). `selection_key` is the exact
    `KnowledgeEvidenceSelectionKey` (5.1H) a future model may later use
    to cite this item -- never a new/random citation id.

    Deliberately excludes `KnowledgeSource.source_uri`: a model does not
    need raw navigation URLs to reason over evidence, and a URI may carry
    environment-specific/internal detail. This is a presentation/security
    boundary only -- the complete `KnowledgeSource` (including
    `source_uri`) remains fully retained in the trusted
    `KnowledgeEvidenceSet`.

    RELEVANCE SCORE != CONFIDENCE: `relevance_score` is only the lexical
    relevance score the configured 5.1G scorer produced. It is NOT a
    probability, NOT confidence that a procedure or diagnosis is correct,
    and NOT a source-authority score -- never renamed `confidence`,
    `certainty`, or `probability`.
    """

    selection_key: KnowledgeEvidenceSelectionKey
    title: str
    document_type: KnowledgeDocumentType
    section_heading: Optional[str] = None
    content: str
    source_system: str
    source_id: str
    source_display_name: Optional[str] = None
    source_locator: Optional[str] = None
    applicability_outcome: ApplicabilityOutcome = Field(
        description="MATCH, PARTIAL_MATCH, or UNKNOWN, preserved exactly from 5.1G -- never normalized/upgraded to MATCH. NOT_APPLICABLE never appears here (5.1G already excludes it)."
    )
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="The 5.1G lexical relevance score in [0.0, 1.0]. RELEVANCE SCORE != CONFIDENCE -- see this class's own docstring.",
    )
    section_sequence: Optional[int] = None


class KnowledgeSearchDiagnostic(BaseModel):
    """A safe, generic, family-level exclusion diagnostic -- a direct,
    unmodified carry-through of 5.1G's own already-safe
    `KnowledgeRetrievalDiagnostic` (`knowledge_id`/`reason`/`detail`,
    where `detail` is already documented as a short, safe string -- an
    exception class name or competing version labels, never raw document
    content or a stack trace). This package does not invent a new
    diagnostic vocabulary or add any additional detail.
    """

    knowledge_id: str
    reason: KnowledgeRetrievalDiagnosticReason
    detail: Optional[str] = None


class KnowledgeSearchAgentPayload(BaseModel):
    """The ONLY object this package intends to be serialized and shown to
    a model. Must never contain a `KnowledgeEvidenceSet`, a
    `KnowledgeRepository`, a `KnowledgeRetrievalResult`, a service
    object, or any database/internal state -- only `KnowledgeToolEvidenceItem`
    and `KnowledgeSearchDiagnostic` instances, both plain, JSON-
    serializable Pydantic models.
    """

    items: list[KnowledgeToolEvidenceItem] = Field(default_factory=list)
    diagnostics: list[KnowledgeSearchDiagnostic] = Field(default_factory=list)


@dataclass(frozen=True)
class KnowledgeSearchExecutionResult:
    """The internal, Python-only bridge between one `knowledge_search`
    call and whatever consumes it next (5.1J+). A plain frozen dataclass
    -- not a Pydantic model -- specifically to keep the distinction
    between the serializable `agent_payload` and internal trusted state
    visually and structurally obvious; this type itself is never intended
    to be serialized as a whole.

    `evidence_set` is TRUSTED BACKEND STATE: NOT MODEL INPUT, NOT MODEL
    AUTHORITY. A model never sees, resubmits, or reconstructs it -- it is
    retained here explicitly so a later selection
    (`KnowledgeToolService.validate_selection`) can validate a model's
    minimal identity-only selection against exactly the evidence this
    execution actually produced, and nothing else (not the wider
    repository, not a different execution's evidence). Access is via the
    obvious `result.agent_payload` / `result.evidence_set` attributes --
    there is no `to_dict()`/serialization helper that would encourage
    sending the whole result anywhere.
    """

    agent_payload: KnowledgeSearchAgentPayload
    evidence_set: KnowledgeEvidenceSet
