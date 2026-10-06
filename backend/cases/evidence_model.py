"""Evidence requirements, acquisition and authority -- kept as SEPARATE concepts.

    WHAT evidence is required           EvidenceRequirement (kind, description, status)
    WHERE evidence/knowledge comes from EvidenceSourceRef   (source type, authority class, provenance)
    HOW it can be acquired              AcquisitionCandidate[] (+ selected_acquisition_id)
    WHETHER acquisition is authorized   AuthorityDecisionRecord -- an AUDIT of the decision taken in
                                        one run; never consulted to authorize anything later
    WHETHER it has been obtained        EvidenceRequirement.status / satisfied_by
    troubleshooting sequencing          TroubleshootingStep (progression), unchanged

A command, procedure, tool or approval is never evidence: they are acquisition mechanisms or
authority controls. Only an approved PROCEDURAL source can ground an operational action (through the
ProcedureAction resolver and Command Authority, evaluated fresh in every run); diagnostic knowledge
(RCA / KB), live operational context (ITSM, alarm management, monitoring, topology), conversational
and operator-provided evidence inform reasoning only.

When every legitimate discovery path completed and no acquisition method exists, the system records
an `AcquisitionGap` (governance audit: knowledge onboarding, SME escalation, tool/automation
backlog) instead of turning its own gap into an operator task. Retrieval / data-source / tool
failures, unresolved applicability, missing parameters and refused commands are NOT gaps.

Pure domain models: no I/O, no model call.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, PrivateAttr, field_validator

from backend.cases.evidence_identity import EvidenceIdentity, stored_identity


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceKind(str, Enum):
    OPERATOR_FACT = "operator_fact"
    """A fact the operator can state directly (vendor, technology, node identifier, board slot)."""
    DIAGNOSTIC_RESULT = "diagnostic_result"
    """The output of a diagnostic read / system query (alarm list, synchronization status)."""
    OBSERVATION = "observation"
    """Something the operator can see or measure directly without a command (cabinet LED colour)."""
    LIVE_OPERATIONAL_CONTEXT = "live_operational_context"
    """Context held by an operational system (ticket history, alarm history, topology)."""


class RequirementStatus(str, Enum):
    UNSATISFIED = "unsatisfied"
    SATISFIED = "satisfied"
    WITHDRAWN = "withdrawn"


class EvidenceSourceType(str, Enum):
    GOVERNED_KNOWLEDGE = "governed_knowledge"
    ITSM = "itsm"
    ALARM_MANAGEMENT = "alarm_management"
    MONITORING = "monitoring"
    TOPOLOGY = "topology"
    CONTROLLED_EXECUTION = "controlled_execution"
    OPERATOR = "operator"
    CASE_CONTEXT = "case_context"
    TEAMS = "teams"


class SourceAuthority(str, Enum):
    """What a source may be used for. Only PROCEDURAL_AUTHORITY may ground an operational action."""

    PROCEDURAL_AUTHORITY = "procedural_authority"
    """Approved governed procedure (MOP / SOP / runbook / operational procedure / technical
    instruction). May ground a governed action -- through ProcedureAction + Command Authority."""
    DIAGNOSTIC_KNOWLEDGE = "diagnostic_knowledge"
    """Governed RCA / KB / known error: hypothesis and prioritization only."""
    LIVE_OPERATIONAL_CONTEXT = "live_operational_context"
    """ITSM / alarm management / monitoring / topology: context and evidence only."""
    OBSERVED_EVIDENCE = "observed_evidence"
    """Operator-reported output or controlled read output: evidence of what was observed."""
    CONVERSATIONAL = "conversational"
    CASE_CONTEXT = "case_context"


class EvidenceSourceRef(BaseModel):
    """Provenance of one piece of evidence or knowledge (never flattened into text)."""

    source_type: EvidenceSourceType
    source_id: str
    authority: SourceAuthority
    title: Optional[str] = None
    document_type: Optional[str] = None
    applicability: Optional[str] = None
    observed_at: Optional[datetime] = Field(default=None, description="Freshness: when the source observed/produced it.")
    provenance: dict[str, Any] = Field(default_factory=dict)


class AcquisitionType(str, Enum):
    EXISTING_EVIDENCE = "existing_evidence"
    OPERATOR_FACT = "operator_fact"
    MANUAL_OBSERVATION = "manual_observation"
    GOVERNED_ACTION = "governed_action"
    TOOL_QUERY = "tool_query"
    ITSM_QUERY = "itsm_query"
    MONITORING_QUERY = "monitoring_query"


class ExecutionActor(str, Enum):
    OPERATOR = "operator"
    ANOC = "anoc"


class GapReason(str, Enum):
    """Why a requirement has no usable acquisition. Only the DISCOVERY_COMPLETE_GAPS are governed
    acquisition gaps; every other reason is a blocker of a different nature."""

    NO_APPLICABLE_PROCEDURE = "no_applicable_procedure"
    NO_APPROVED_ACQUISITION_ACTION = "no_approved_acquisition_action"
    NO_RELEVANT_EVIDENCE_FOUND = "no_relevant_evidence_found"
    ACTION_NOT_AUTHORIZED = "action_not_authorized"
    APPLICABILITY_UNRESOLVED = "applicability_unresolved"
    REQUIRED_PARAMETER_MISSING = "required_parameter_missing"
    TOOL_UNAVAILABLE = "tool_unavailable"
    DATA_SOURCE_UNAVAILABLE = "data_source_unavailable"
    RETRIEVAL_FAILED = "retrieval_failed"
    DISCOVERY_INCOMPLETE = "discovery_incomplete"
    """No governed discovery was performed in the run: nothing can be concluded yet."""
    ACTION_NOT_CHOSEN = "governed_action_not_chosen"
    """Valid governed diagnostic actions exist in the run's SELECTED evidence but none was chosen for
    this need: a method exists, so this is never a governed acquisition gap."""


DISCOVERY_COMPLETE_GAPS = frozenset(
    {GapReason.NO_APPROVED_ACQUISITION_ACTION, GapReason.NO_APPLICABLE_PROCEDURE, GapReason.NO_RELEVANT_EVIDENCE_FOUND}
)
"""Discovery completed successfully and no legitimate acquisition method exists."""


class CandidateAvailability(str, Enum):
    AVAILABLE = "available"
    BLOCKED = "blocked"
    """Exists, but cannot be used now (applicability unresolved, parameter missing, not authorized)."""
    UNAVAILABLE = "unavailable"
    """The tool / data source cannot be reached."""


class CandidateValidation(str, Enum):
    """Where a governed candidate stands. The *_CURRENT_RUN / BLOCKED_APPLICABILITY values describe
    ONLY the run recorded in `validated_run_id`: read in any other run, the candidate is
    KNOWN_STRUCTURAL (identity only, zero authority) and must be rediscovered, re-selected and
    re-authorized from that run's own evidence (`effective_validation`)."""

    KNOWN_STRUCTURAL = "known_structural"
    BLOCKED_APPLICABILITY = "blocked_applicability"
    AVAILABLE_CURRENT_RUN = "available_current_run"
    AUTHORIZED_CURRENT_RUN = "authorized_current_run"
    REJECTED_CURRENT_RUN = "rejected_current_run"


class AcquisitionCandidate(BaseModel):
    """One legitimate way the requirement could be acquired. Never authority by itself."""

    acquisition_id: str = Field(default_factory=lambda: f"acq-{uuid.uuid4().hex[:10]}")
    requirement_id: str
    acquisition_type: AcquisitionType
    source: Optional[EvidenceSourceRef] = None
    procedure_action_id: Optional[str] = None
    command_template: Optional[str] = Field(default=None, description="Identity only: never a presentable or executable command.")
    canonical_source_id: Optional[str] = Field(default=None, description="Display / diagnostics only; never parsed or compared as identity.")
    source_identity: Optional[EvidenceIdentity] = Field(
        default=None, description="Structured identity of the governed source (exact tuple); None when unknown or malformed."
    )
    applicability: Optional[str] = None
    parameters: list[str] = Field(default_factory=list)
    availability: CandidateAvailability = CandidateAvailability.AVAILABLE
    blocking_reason: Optional[GapReason] = None
    execution_actor: Optional[ExecutionActor] = None
    validation: CandidateValidation = CandidateValidation.KNOWN_STRUCTURAL
    validated_run_id: Optional[str] = Field(default=None, description="The run `validation` describes (audit only).")
    validation_reason: Optional[str] = None

    @field_validator("source_identity", mode="before")
    @classmethod
    def _stored_identity(cls, value: Any) -> Optional[EvidenceIdentity]:
        return stored_identity(value)

    def effective_validation(self, run_id: Optional[str]) -> CandidateValidation:
        """This candidate's standing for `run_id`: anything recorded by another run is structural only."""
        if run_id and self.validated_run_id == run_id:
            return self.validation
        return CandidateValidation.KNOWN_STRUCTURAL


class AuthorityStatus(str, Enum):
    AUTHORIZED = "authorized"
    """Command Authority authorized the governed action in THIS run (advisory: operator executes)."""
    AUTHORIZED_BY_CURRENT_POLICY = "authorized_by_current_policy"
    """Authorized AND the current policy lets ANOC execute it now (closed loop, current action only)."""
    APPROVAL_REQUIRED = "approval_required"
    """Authorized; human approval required before ANOC executes it (HITL)."""
    BLOCKED_APPLICABILITY = "blocked_applicability"
    BLOCKED_PARAMETERS = "blocked_parameters"
    NOT_AUTHORIZED = "not_authorized"
    NOT_REQUIRED = "not_required"
    """The acquisition is not an operational action (operator fact, observation, context query)."""


class AuthorityDecisionRecord(BaseModel):
    """AUDIT of an authority decision taken in one run. It is never read back as authority: every
    run re-derives authority from its own selected evidence, applicability, target, parameters,
    policy and execution mode."""

    decision_id: str = Field(default_factory=lambda: f"auth-{uuid.uuid4().hex[:10]}")
    acquisition_id: str
    status: AuthorityStatus
    reason: Optional[str] = None
    execution_actor: Optional[ExecutionActor] = None
    execution_mode: Optional[str] = None
    run_id: Optional[str] = None
    decided_at: datetime = Field(default_factory=_now)


class HintType(str, Enum):
    COMMAND_OR_METHOD = "command_or_method"
    TOOL_OR_SOURCE = "tool_or_source"


class AcquisitionHint(BaseModel):
    """An operator-proposed way to acquire an open requirement ("I normally use X"). UNTRUSTED input:
    it may steer discovery (search terms); it never authorizes, never populates a command or the
    approved catalog, never becomes ProcedureAction authority and never bypasses applicability or
    Command Authority. Only an approved governed action found independently can be used."""

    hint_id: str = Field(default_factory=lambda: f"hint-{uuid.uuid4().hex[:10]}")
    requirement_id: str
    source: str = "operator"
    value: str
    hint_type: HintType = HintType.COMMAND_OR_METHOD
    authority: str = Field(default="none", description="Always 'none': a hint is never authority.")
    run_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)


class DescriptionRevision(BaseModel):
    description: str
    run_id: Optional[str] = None
    at: datetime = Field(default_factory=_now)


class EvidenceRequirement(BaseModel):
    requirement_id: str = Field(default_factory=lambda: f"req-{uuid.uuid4().hex[:10]}")
    fault_id: str
    kind: EvidenceKind
    description: str
    capability: Optional[str] = Field(default=None, description="Normalized evidence capability key, used to find live sources.")
    semantic_key: Optional[str] = Field(
        default=None, description="Server-normalized identity of the evidence need (capability or content-derived); never the raw model text."
    )
    semantic_tokens: list[str] = Field(
        default_factory=list, description="Server-derived content tokens of every description seen (continuity matching)."
    )
    description_history: list[DescriptionRevision] = Field(default_factory=list, description="Earlier / refined wordings (audit).")
    acquisition_hints: list[AcquisitionHint] = Field(default_factory=list)
    _proposed_ref: Optional[str] = PrivateAttr(default=None)
    """A specialist's reference to an existing requirement (unvalidated; never persisted)."""
    status: RequirementStatus = RequirementStatus.UNSATISFIED
    required: bool = True
    originating_step_id: Optional[str] = None
    acquisition_candidates: list[AcquisitionCandidate] = Field(default_factory=list)
    selected_acquisition_id: Optional[str] = None
    blocking_reason: Optional[GapReason] = None
    last_authority_decision: Optional[AuthorityDecisionRecord] = Field(default=None, description="Audit only; never authority.")
    authority_history: list[AuthorityDecisionRecord] = Field(default_factory=list)
    supporting_sources: list[EvidenceSourceRef] = Field(default_factory=list, description="Why this evidence is required (provenance).")
    satisfied_by: list[EvidenceSourceRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    satisfied_at: Optional[datetime] = None

    @property
    def selected_acquisition(self) -> Optional[AcquisitionCandidate]:
        return next((c for c in self.acquisition_candidates if c.acquisition_id == self.selected_acquisition_id), None)

    def record_authority(self, decision: AuthorityDecisionRecord, max_history: int = 20) -> None:
        self.last_authority_decision = decision
        self.authority_history = [*self.authority_history[-(max_history - 1):], decision]
        self.updated_at = _now()


class GapStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    """A legitimate acquisition method became available (or the evidence was obtained)."""
    SUPERSEDED = "superseded"
    """The gap condition no longer holds for another reason (e.g. a method exists but is blocked)."""


class ConsultedSource(BaseModel):
    """A governed source a discovery pass consulted, with its applicability in that pass."""

    identity: EvidenceIdentity
    applicability: Optional[str] = None


class DiscoveryAttempt(BaseModel):
    """One discovery pass for the requirement (audit; never authority)."""

    attempt_id: str = Field(default_factory=lambda: f"att-{uuid.uuid4().hex[:10]}")
    run_id: Optional[str] = None
    searches: list[dict[str, Any]] = Field(default_factory=list)
    sources_consulted: list[str] = Field(default_factory=list, description="Display ids (diagnostics only).")
    consulted: list[ConsultedSource] = Field(
        default_factory=list, description="Structured identities + applicability this pass consulted (novelty detection)."
    )
    selected_evidence: list[str] = Field(default_factory=list)
    candidates_considered: list[dict[str, Any]] = Field(default_factory=list)
    hints_considered: list[str] = Field(default_factory=list)
    outcome: str = Field(description="gap reason of this attempt, or 'resolved'")
    at: datetime = Field(default_factory=_now)

    @field_validator("consulted", mode="before")
    @classmethod
    def _stored_consulted(cls, value: Any) -> list[dict[str, Any]]:
        out = []
        for item in value or [] if isinstance(value, (list, tuple)) else []:
            raw = item.model_dump() if isinstance(item, BaseModel) else item
            identity = stored_identity(raw.get("identity")) if isinstance(raw, dict) else None
            if identity is not None:
                out.append({"identity": identity, "applicability": raw.get("applicability")})
        return out


class AcquisitionGap(BaseModel):
    """Governance audit of a governed acquisition gap (feeds knowledge onboarding, SME escalation,
    tool / automation backlog, missing-capability reporting). Not a workflow. ONE open gap per
    unresolved requirement: every further discovery pass is a `discovery_attempts` entry."""

    gap_id: str = Field(default_factory=lambda: f"gap-{uuid.uuid4().hex[:10]}")
    fault_id: str
    step_id: Optional[str] = None
    requirement_id: str
    requirement_description: str
    gap_reason: GapReason
    status: GapStatus = GapStatus.OPEN
    discovery_attempts: list[DiscoveryAttempt] = Field(default_factory=list)
    updated_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None
    resolution: Optional[str] = None
    sources_searched: list[dict[str, Any]] = Field(default_factory=list, description="Governed searches: query, status, context, counts.")
    sources_consulted: list[str] = Field(default_factory=list, description="AVAILABLE evidence identities returned by discovery.")
    supporting_evidence: list[str] = Field(default_factory=list, description="SELECTED evidence identities of the run.")
    candidates_considered: list[dict[str, Any]] = Field(default_factory=list)
    run_id: Optional[str] = None
    recorded_at: datetime = Field(default_factory=_now)

    def all_attempts(self) -> list[DiscoveryAttempt]:
        """Every discovery pass, including the first one of a gap persisted before attempts existed."""
        if self.discovery_attempts:
            return list(self.discovery_attempts)
        return [DiscoveryAttempt(
            run_id=self.run_id, searches=list(self.sources_searched), sources_consulted=list(self.sources_consulted),
            selected_evidence=list(self.supporting_evidence), candidates_considered=list(self.candidates_considered),
            outcome=self.gap_reason.value, at=self.recorded_at,
        )]

    def add_attempt(self, attempt: DiscoveryAttempt, reason: GapReason, max_attempts: int = 20) -> None:
        self.discovery_attempts = [*self.all_attempts(), attempt][-max_attempts:]
        self.gap_reason = reason
        self.updated_at = _now()
