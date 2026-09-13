"""Phase 6A.6: the Context Package / Evidence Package contracts.

DEPENDENCY BOUNDARY (enforced by `backend/tests/context_engineering/
test_dependency_boundary.py`, mirroring 6A.2/6A.4/6A.5's own established
pattern): this module imports ONLY type/contract modules from peer
domains -- `backend.context.domain.enums`/`.models` (TELCO Context),
`backend.cases.schemas` (Case), `backend.knowledge.hybrid_retrieval
.contracts` (6A.5 evidence) -- never a service/repository/database
module of any domain, never `google.adk`/`google.genai`, never
`backend.agents`/`backend.tools`. This package has NO code path to fetch
its own inputs -- every input is supplied by the caller, already
computed by the real, authoritative source (docs/KNOWLEDGE_CONTRACT.md
§28.1).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from backend.cases.schemas import CaseContextSnapshot
from backend.context.domain.enums import ContextDimension, ContextOrigin, ContextProfileOwnerKind, ContextState
from backend.context.domain.models import ContextValue
from backend.knowledge.hybrid_retrieval.contracts import EvidenceSelectionResult

__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "OperationalObservationKind",
    "TelcoAssertionView",
    "TelcoDimensionView",
    "RequestContext",
    "OperationalObservation",
    "EvidenceItemView",
    "EvidencePackage",
    "ProvenanceManifestEntry",
    "AssemblyTrace",
    "ContextPackage",
    "ContextPackageInput",
]

CONTEXT_SCHEMA_VERSION = "1.0"
"""§8: the Context Package's own schema version -- distinct from any
application/deployment version. Bumped only on a genuine, intentional
contract change; a new OPTIONAL field may be added without bumping this
(existing consumers must keep working), a field REMOVAL or MEANING
change would require a bump. Never duplicates existing app version
metadata."""


class OperationalObservationKind(str, Enum):
    """§17: OBSERVED (a real, tool/system-sourced fact, e.g. an alarm
    reading) vs. ASSERTED (a stated fact from a case/session/user, not
    independently instrument-verified). A closed, deliberately small
    vocabulary -- never conflated with `AssertionKind` (TELCO Context,
    §11) or `is_derived` (Knowledge, §22), which answer different
    questions about different kinds of content."""

    OBSERVED = "observed"
    ASSERTED = "asserted"


class TelcoAssertionView(BaseModel):
    """A package-facing projection of one `backend.context.domain.models
    .ContextAssertion` -- same fields, never fewer, never mutating the
    original (§23). Deliberately excludes only `assertion_id`/
    `dimension`/`kind`, which are redundant with the enclosing
    `TelcoDimensionView`'s own `dimension` field and this list's own
    accepted/conflicting placement (kind is implied by which list an
    assertion appears in)."""

    raw_value: Optional[str] = None
    canonical_value: Optional[str] = None
    origin: ContextOrigin
    source_reference: Optional[str] = None
    asserted_at: Optional[datetime] = None


class TelcoDimensionView(BaseModel):
    """One dimension's current, computed TELCO Context state, packaged
    verbatim from 6A.2's own `ContextValue` (§11/§12/§13) -- `state` is
    NEVER collapsed/resolved here: UNKNOWN stays UNKNOWN, CONFLICTING
    stays CONFLICTING with every disputed assertion preserved, exactly
    as 6A.2's own `reduce_dimension` computed it."""

    dimension: ContextDimension
    state: ContextState
    accepted: list[TelcoAssertionView] = Field(default_factory=list)
    conflicting: list[TelcoAssertionView] = Field(default_factory=list)


class RequestContext(BaseModel):
    """§15: the bounded, already-existing request/query context this
    codebase's own `IncidentManagerRequest` already carries -- never the
    full conversation, never a new memory/summarization mechanism."""

    chat_topic: Optional[str] = None
    question: Optional[str] = None
    requested_time_range: Optional[str] = None


class OperationalObservation(BaseModel):
    """§16: a generic, forward-compatible SLOT for a trusted operational
    fact -- NO producer exists in this codebase yet (confirmed by audit:
    no ITSM/Alarm/Topology/KPI/Change/Handover connector is wired to
    anything today; `backend.context.domain.enums.ContextOrigin` already
    reserves these source names for exactly this future purpose). This
    contract exists so 6A.6 has somewhere deterministic to place such a
    fact once a real source exists, WITHOUT 6A.6 itself ever collecting
    one (§16's own explicit "must NOT become responsible for performing
    arbitrary network/tool collection")."""

    kind: OperationalObservationKind
    content: str
    source_reference: Optional[str] = None
    observed_at: Optional[datetime] = None


class EvidenceItemView(BaseModel):
    """One package-facing projection of an already-SELECTED (never
    merely retrieved) `HybridRetrievalCandidate.record` (§18/§19/§20) --
    every field here already exists on the real 6A.5
    `EvidenceIndexRecord`/`HybridRetrievalCandidate`; nothing is
    invented, enriched, or independently re-fetched. `channel_scores` is
    a deterministically-sorted (by channel name) mapping so it never
    depends on the original `channel_hits` list's own iteration order.
    Embedding vectors are never included (§20's own explicit
    prohibition)."""

    evidence_id: str
    knowledge_id: str
    version_label: str
    section_id: str
    artifact_id: Optional[str] = None
    is_derived: bool
    indexable_text: str
    selected_rank: int = Field(description="1-based position within the SELECTED list, in the exact order 6A.5's own deterministic reranking produced -- never re-sorted here.")
    channel_scores: dict[str, float] = Field(default_factory=dict, description="{channel_name: raw_score}, e.g. {'exact': 1.0, 'lexical': 0.42} -- retrieval/ranking SIGNALS only, never a correctness/truth probability (§31/§32).")
    fusion_score: float
    rerank_score: float


class EvidencePackage(BaseModel):
    """§19/§20: the Evidence Package. `retrieved_candidate_count` is
    OPTIONAL and caller-supplied (6A.6 never re-derives it -- it has no
    access to the pre-selection candidate set, only to what was already
    selected) -- `None` means the caller did not report it, never
    "zero". The invariant this package structurally enforces: `len(
    items) == selected_evidence_count`, and `items` can NEVER contain
    more than what `EvidenceSelectionResult.selected` already
    contained -- there is no code path in `assembly.py` that adds a
    candidate that was not already selected."""

    query_text: str
    selection_reason: str
    retrieved_candidate_count: Optional[int] = None
    selected_evidence_count: int
    source_evidence_count: int
    derived_evidence_count: int
    items: list[EvidenceItemView] = Field(default_factory=list)


class ProvenanceManifestEntry(BaseModel):
    """§38: one compact, auditable "where did this come from" row --
    never a duplicate of the full source body, just enough identity to
    let an auditor trace back to the real, authoritative record."""

    category: str = Field(description="One of 'telco_context', 'case_context', 'operational_observation', 'knowledge_evidence' -- an open string, not a closed enum, so a future category never requires a contract migration.")
    identity: str = Field(description="A stable identifier within that category, e.g. a TELCO assertion_id, a Case item_id, an evidence_id.")
    source_reference: Optional[str] = None


class AssemblyTrace(BaseModel):
    """§39: lightweight, deterministic, safe-to-log counters -- never
    Knowledge text, never secrets, never full conversations (§62)."""

    telco_dimensions_included: int
    telco_known_count: int
    telco_unknown_count: int
    telco_conflicting_count: int
    telco_not_applicable_count: int
    operational_observation_count: int
    retrieved_candidate_count: Optional[int] = None
    selected_evidence_count: int
    source_evidence_count: int
    derived_evidence_count: int


class ContextPackage(BaseModel):
    """The canonical, versioned, deterministic Context Package (§7/§8).

    IDENTITY (§9): expressed through the owner/session/case/request
    fields that ALREADY exist and are legitimate -- never a fabricated
    `package_id`. `content_fingerprint` (§27) is the practical,
    deterministic identity for auditability -- two packages built from
    identical logical inputs always produce the identical fingerprint;
    `created_at` is deliberately OUTSIDE that fingerprint (a real
    wall-clock value, useful for logging, never part of the semantic
    identity).

    IMMUTABLE ONCE ASSEMBLED (§23): nothing in `assembly.py` mutates this
    object after construction; a differently-scoped package is always a
    NEW `ContextPackage`, never a patched one.
    """

    context_schema_version: str = CONTEXT_SCHEMA_VERSION
    owner_kind: ContextProfileOwnerKind
    owner_id: str
    session_id: Optional[str] = None
    case_id: Optional[str] = None
    request: RequestContext = Field(default_factory=RequestContext)
    telco_context: list[TelcoDimensionView] = Field(default_factory=list)
    case_context: Optional[CaseContextSnapshot] = None
    operational_observations: list[OperationalObservation] = Field(default_factory=list)
    evidence: EvidencePackage
    provenance_manifest: list[ProvenanceManifestEntry] = Field(default_factory=list)
    assembly_trace: AssemblyTrace
    content_fingerprint: str = Field(description="SHA-256 hex digest of this package's own canonical, deterministic content -- see fingerprint.py. Empty string only transiently, during assembly, before the fingerprint is computed.")
    created_at: datetime = Field(description="Real wall-clock assembly time -- deliberately EXCLUDED from content_fingerprint (§26/§27).")


class ContextPackageInput(BaseModel):
    """The one input contract `assembly.assemble_context_package`
    accepts (§25). Every field is ALREADY-COMPUTED data the caller
    supplies -- this contract itself performs no lookup, no database
    access, no Knowledge search (§18/§24/§56/§57): `telco_context_state`
    is exactly 6A.2's own `TelcoContextService.get_context_state(...)`
    return value; `evidence_selection` is exactly 6A.5's own
    `evidence_selection.select_evidence(...)` return value.
    """

    owner_kind: ContextProfileOwnerKind
    owner_id: str
    session_id: Optional[str] = None
    case_id: Optional[str] = None
    request: RequestContext = Field(default_factory=RequestContext)
    telco_context_state: dict[ContextDimension, ContextValue] = Field(default_factory=dict)
    case_context: Optional[CaseContextSnapshot] = None
    operational_observations: list[OperationalObservation] = Field(default_factory=list)
    evidence_selection: EvidenceSelectionResult
    retrieved_candidate_count: Optional[int] = None
