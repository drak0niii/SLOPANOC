"""Evidence / knowledge source classification and the live operational source boundary.

Every piece of evidence the Technical Authority Engineer reasons over keeps its provenance as an
`EvidenceSourceRef` (source type, id, authority class, document type, applicability, freshness).
Authority classes decide what a source may be USED for:

    PROCEDURAL_AUTHORITY      approved governed procedure (MOP, SOP, runbook / troubleshooting guide,
                              operational procedure, technical instruction): the ONLY class that may
                              ground an operational action -- and only through ProcedureAction
                              resolution + Command Authority, evaluated fresh in every run
    DIAGNOSTIC_KNOWLEDGE      governed RCA / KB / known error / other: hypothesis + prioritization
    LIVE_OPERATIONAL_CONTEXT  ITSM, alarm management, monitoring, topology: context + evidence
    OBSERVED_EVIDENCE         operator-reported output, controlled read output
    CONVERSATIONAL / CASE_CONTEXT

Live operational sources (ITSM, OneFM / alarm management, monitoring, topology) plug in through
`LiveEvidenceSource` and an explicit registry keyed by evidence capability. No integration is
registered by default: none is implemented in this repository yet, and nothing is ever inferred.
A live source answers read-only context queries; its output is observed evidence, never governed
knowledge and never command authority.
"""
from __future__ import annotations

import abc
import threading
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from pydantic import BaseModel, Field

from backend.cases.evidence_model import AcquisitionType, EvidenceSourceRef, EvidenceSourceType, SourceAuthority

PROCEDURAL_DOCUMENT_TYPES = frozenset({"mop", "sop", "operational_procedure", "technical_instruction", "troubleshooting_guide"})
"""Governed document types that may carry approved operational actions."""
DIAGNOSTIC_DOCUMENT_TYPES = frozenset({"rca", "kb_article", "other"})
"""Governed document types used for diagnosis only (never an action source)."""


def _field(ev: Any, name: str) -> Any:
    return ev.get(name) if isinstance(ev, Mapping) else getattr(ev, name, None)


def governed_document_type(metadata: Mapping[str, Any]) -> Optional[str]:
    value = metadata.get("document_type") if isinstance(metadata, Mapping) else None
    return str(value).strip().lower() if value else None


def is_action_source(metadata: Mapping[str, Any]) -> bool:
    """May this governed evidence ground an operational action? Diagnostic-only document types
    (RCA, KB, other) never can. Evidence built before document types were recorded keeps its
    previous treatment (no type -> procedural), so existing behaviour is unchanged."""
    document_type = governed_document_type(metadata)
    return document_type is None or document_type in PROCEDURAL_DOCUMENT_TYPES


_TYPE_BY_EVIDENCE = {
    "teams_conversation": (EvidenceSourceType.TEAMS, SourceAuthority.CONVERSATIONAL),
    "case_context": (EvidenceSourceType.CASE_CONTEXT, SourceAuthority.CASE_CONTEXT),
    "user_evidence": (EvidenceSourceType.OPERATOR, SourceAuthority.OBSERVED_EVIDENCE),
    "observed_metric": (EvidenceSourceType.CONTROLLED_EXECUTION, SourceAuthority.OBSERVED_EVIDENCE),
}


def classify_evidence(ev: Any) -> EvidenceSourceRef:
    """Provenance + authority class of one validated evidence reference."""
    source_type = _field(ev, "source_type") or ""
    metadata = _field(ev, "metadata") or {}
    source_id = str(_field(ev, "source_id") or "")
    title = _field(ev, "title")
    if source_type == "governed_knowledge":
        document_type = governed_document_type(metadata)
        return EvidenceSourceRef(
            source_type=EvidenceSourceType.GOVERNED_KNOWLEDGE,
            source_id=source_id,
            authority=SourceAuthority.PROCEDURAL_AUTHORITY if is_action_source(metadata) else SourceAuthority.DIAGNOSTIC_KNOWLEDGE,
            title=title,
            document_type=document_type,
            applicability=metadata.get("applicability_outcome"),
            provenance={k: metadata.get(k) for k in ("knowledge_id", "version_label", "section_id", "lifecycle_status") if metadata.get(k)},
        )
    if source_type in _TYPE_BY_EVIDENCE:
        kind, authority = _TYPE_BY_EVIDENCE[source_type]
        return EvidenceSourceRef(source_type=kind, source_id=source_id, authority=authority, title=title)
    return EvidenceSourceRef(source_type=EvidenceSourceType.CASE_CONTEXT, source_id=source_id, authority=SourceAuthority.CASE_CONTEXT, title=title)


# ---------------------------------------------------------------------------------------------
# Live operational sources (pluggable; none registered by default)
# ---------------------------------------------------------------------------------------------

_QUERY_TYPE = {
    EvidenceSourceType.ITSM: AcquisitionType.ITSM_QUERY,
    EvidenceSourceType.ALARM_MANAGEMENT: AcquisitionType.MONITORING_QUERY,
    EvidenceSourceType.MONITORING: AcquisitionType.MONITORING_QUERY,
}


class LiveEvidenceResult(BaseModel):
    status: str = Field(description="'ok' | 'not_found' | 'unavailable' | 'failed'")
    content: str = ""
    observed_at: Optional[datetime] = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    detail: Optional[str] = None


class LiveEvidenceSource(abc.ABC):
    """A read-only operational context source (ITSM, alarm management, monitoring, topology).
    It returns evidence about the current situation; it never executes network actions and its
    output never becomes governed knowledge or command authority."""

    source_type: EvidenceSourceType
    source_id: str
    capabilities: frozenset[str] = frozenset()

    @property
    def acquisition_type(self) -> AcquisitionType:
        return _QUERY_TYPE.get(self.source_type, AcquisitionType.TOOL_QUERY)

    def source_ref(self, observed_at: Optional[datetime] = None, provenance: Optional[dict[str, Any]] = None) -> EvidenceSourceRef:
        return EvidenceSourceRef(
            source_type=self.source_type, source_id=self.source_id, authority=SourceAuthority.LIVE_OPERATIONAL_CONTEXT,
            observed_at=observed_at, provenance=dict(provenance or {}),
        )

    @abc.abstractmethod
    async def query(self, capability: str, context: Mapping[str, Any]) -> LiveEvidenceResult:
        ...


_registry_lock = threading.Lock()
_registry: dict[str, LiveEvidenceSource] = {}


def register_live_evidence_source(source: LiveEvidenceSource) -> None:
    with _registry_lock:
        _registry[source.source_id] = source


def unregister_live_evidence_source(source_id: str) -> None:
    with _registry_lock:
        _registry.pop(source_id, None)


def live_sources_for(capability: Optional[str]) -> list[LiveEvidenceSource]:
    if not capability:
        return []
    key = normalize_capability(capability)
    with _registry_lock:
        return [s for s in _registry.values() if key in {normalize_capability(c) for c in s.capabilities}]


def normalize_capability(value: Optional[str]) -> str:
    return "_".join(str(value or "").strip().casefold().replace("-", " ").split())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
