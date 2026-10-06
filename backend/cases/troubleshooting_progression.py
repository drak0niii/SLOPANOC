"""Troubleshooting Progression -- the authoritative, server-owned record of an investigation.

    Case
    +-- Fault Threads            (FaultProgression, one per fault/component)
    +-- Troubleshooting Progression
    |   +-- current (active) fault
    |   +-- hypotheses           (deterministic states, evidence-referenced transitions)
    |   +-- steps                (ordered TroubleshootingStep history, all faults)
    |   +-- observations/results (StepResult on each step, with provenance)
    |   +-- current phase / current step / resolution state (per fault)
    |   +-- open questions
    +-- Audit / provenance       (ProgressionEvent)

The investigation lives here, independent of chat history or model memory. It is persisted by
the Case (`slopanoc_case_troubleshooting_progressions`, versioned compare-and-swap; see
backend/cases/progression_store.py and progression_repository.py) or, for a session not linked to
any Case, in that session's state. `TroubleshootingState` (backend/cases/troubleshooting_state.py)
is ONLY a bounded read projection of one fault thread (`project_troubleshooting_state`), re-derived
on every save for existing readers; it is never read back.

This module is pure domain + storage helpers: no model call, no I/O beyond the given state mapping,
no governance decisions. Older sessions are adapted deterministically, one way, from their legacy
fault-thread state (`progression_from_legacy_state`), without modifying that state.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, MutableMapping, Optional

from pydantic import BaseModel, Field, field_validator

from backend.cases.evidence_identity import EvidenceIdentity, identity_from_fields, stored_identity
from backend.cases.evidence_model import (
    AcquisitionGap,
    AcquisitionHint,
    CandidateValidation,
    DiscoveryAttempt,
    EvidenceRequirement,
    EvidenceSourceRef,
    GapReason,
    GapStatus,
    RequirementStatus,
)
from backend.cases.troubleshooting_state import (
    CheckLifecycleStatus,
    DiagnosticCheckRecord,
    TroubleshootingState,
    TroubleshootingStatus,
)

PROGRESSION_STATE_KEY = "troubleshooting_progression"
SCHEMA_VERSION = "troubleshooting-progression/1"
_LEGACY_ACTIVE_KEY = "troubleshooting_state"
_LEGACY_THREADS_KEY = "troubleshooting_threads"
_MAX_RESULT_CHARS = 8000


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ProgressionPhase(str, Enum):
    NEW = "new"
    CONTEXT_BUILDING = "context_building"
    READY_FOR_DIAGNOSIS = "ready_for_diagnosis"
    EVIDENCE_SELECTED = "evidence_selected"
    STEP_PROPOSED = "step_proposed"
    STEP_VALIDATED = "step_validated"
    AWAITING_OBSERVATION = "awaiting_observation"
    RESULT_RECEIVED = "result_received"
    RESULT_VALIDATED = "result_validated"
    """The candidate observation was validated against the pending step (trusted result)."""
    RESULT_VERIFIED = "result_verified"
    REASSESS = "reassess"
    REMEDIATION_CANDIDATE = "remediation_candidate"
    REMEDIATION = "remediation"
    ACTION_EXECUTED = "action_executed"
    POST_ACTION_VERIFICATION_REQUIRED = "post_action_verification_required"
    RESOLVED = "resolved"
    ESCALATION_REQUIRED = "escalation_required"
    BLOCKED_MISSING_INFORMATION = "blocked_missing_information"


class StepStatus(str, Enum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    PRESENTED = "presented"
    EXECUTED = "executed"
    OBSERVED = "observed"
    VERIFIED = "verified"
    COMPLETED = "completed"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    FAILED = "failed"
    CANCELLED = "cancelled"
    """The operator explicitly cancelled the pending step (no result, no new objective stated)."""
    SUPERSEDED = "superseded"
    """The operator explicitly replaced the pending step's objective with a new one."""
    BLOCKED_BY_CLARIFICATION = "blocked_by_clarification"
    """A governed diagnostic action identified on server structure whose command cannot be
    authorized ONLY because applicability is unresolved (an open applicability clarification).
    Pending for sequencing; zero execution authority (no command, no command source)."""
    BLOCKED_GOVERNED_ACQUISITION_GAP = "blocked_governed_acquisition_gap"
    """The step needs evidence for which discovery completed and NO legitimate acquisition method
    exists. Never presented as an operator task; not pending (the investigation may choose another
    path or escalate). Its EvidenceRequirement and AcquisitionGap record why."""


_AWAITING_RESULT_STATUSES = frozenset({
    StepStatus.PROPOSED, StepStatus.VALIDATED, StepStatus.PRESENTED, StepStatus.BLOCKED_BY_CLARIFICATION,
})
"""Statuses in which a step awaits its result (a step holding a recorded result never re-enters them)."""


class HypothesisState(str, Enum):
    ACTIVE = "active"
    SUPPORTED = "supported"
    WEAKENED = "weakened"
    REJECTED = "rejected"
    CONFIRMED = "confirmed"


class ResultSource(str, Enum):
    OPERATOR_MESSAGE = "operator_message"
    """The operator's own verbatim words."""
    EXECUTION_ADAPTER = "execution_adapter"
    """Output of a controlled (server-authorized) execution."""
    CALLER_SUMMARY = "caller_summary"
    """A model/caller-written summary: informative only, never trusted evidence."""
    CONTEXT_SOURCE = "context_source"
    """Returned by an approved live operational / context source (observed evidence; never authority)."""


class VerificationStatus(str, Enum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    INCONCLUSIVE = "inconclusive"


class ResolutionState(str, Enum):
    UNRESOLVED = "unresolved"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class StepPurpose(str, Enum):
    DIAGNOSTIC = "diagnostic"
    REMEDIATION = "remediation"
    VERIFICATION = "verification"
    """A check performed after a remediation action, evaluated against its verification criteria."""


class CriterionKind(str, Enum):
    ORIGINAL_CONDITION = "original_condition"
    OPERATIONAL_STATE = "operational_state"
    DEPENDENT_CONDITION = "dependent_condition"
    EXPECTED_EVIDENCE = "expected_evidence"


class CriterionStatus(str, Enum):
    PENDING = "pending"
    MET = "met"
    NOT_MET = "not_met"


class VerificationCriterion(BaseModel):
    """An explicit, server-grounded success criterion for a remediation. Evaluated deterministically
    against trusted post-action observations only (never the remediation command's own output)."""

    criterion_id: str = Field(default_factory=lambda: f"crit-{uuid.uuid4().hex[:10]}")
    kind: CriterionKind
    description: str
    check_action_key: Optional[str] = Field(default=None, description="Identity action_key of the governed read that must be re-observed.")
    scope: Optional[str] = Field(default=None, description="Literal token selecting the observation lines to evaluate.")
    must_include: list[str] = Field(default_factory=list, description="Stated by the SELECTED governed procedure.")
    must_exclude: list[str] = Field(default_factory=list, description="Observed in a trusted pre-action observation.")
    grounding: list[str] = Field(default_factory=list, description="governed:<source_id> / baseline:<result_id>")
    status: CriterionStatus = CriterionStatus.PENDING
    evidence_ids: list[str] = Field(default_factory=list)
    detail: Optional[str] = None


class RemediationState(str, Enum):
    CANDIDATE = "candidate"
    """Passed the remediation gate; presented; still subject to authority / confirmation / HITL."""
    VERIFICATION_REQUIRED = "verification_required"
    """The action was executed: COMMAND SUCCEEDED != INCIDENT RESOLVED."""
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"
    ACTION_FAILED = "action_failed"
    NOT_PERFORMED = "not_performed"


class RemediationRecord(BaseModel):
    remediation_id: str = Field(default_factory=lambda: f"rem-{uuid.uuid4().hex[:10]}")
    fault_id: str
    step_id: str
    procedure_action_id: Optional[str] = None
    source_id: Optional[str] = None
    target: str = ""
    hypothesis_id: Optional[str] = None
    gate: dict[str, Any] = Field(default_factory=dict, description="Server evaluation of each diagnosis->remediation gate condition.")
    state: RemediationState = RemediationState.CANDIDATE
    criteria: list[VerificationCriterion] = Field(default_factory=list)
    rejected_criteria: list[dict[str, str]] = Field(default_factory=list)
    criteria_frozen: bool = Field(default=False, description="Criteria are fixed when the action is executed.")
    executed_result_id: Optional[str] = None
    outcome_reason: Optional[str] = None
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class StepResult(BaseModel):
    """A TRUSTED result: a candidate observation that passed server-side result validation."""

    result_id: str = Field(default_factory=lambda: f"obs-{uuid.uuid4().hex[:10]}")
    text: str
    source: ResultSource
    recorded_at: datetime = Field(default_factory=_now)
    execution_id: Optional[str] = None
    validation: dict[str, Any] = Field(default_factory=dict, description="Server result-validation decision (status, reasons, signals).")


class CandidateObservation(BaseModel):
    """What the operator (or an adapter) supplied for a pending step that did NOT validate as its
    result. Kept for audit; never evidence, never a parameter source."""

    candidate_id: str = Field(default_factory=lambda: f"cand-{uuid.uuid4().hex[:10]}")
    text: str
    source: ResultSource
    received_at: datetime = Field(default_factory=_now)
    execution_id: Optional[str] = None
    validation: dict[str, Any] = Field(default_factory=dict)


class StatusChange(BaseModel):
    at: datetime = Field(default_factory=_now)
    from_status: Optional[StepStatus] = None
    to_status: StepStatus
    reason: Optional[str] = None


class StepIdentity(BaseModel):
    """Server-owned canonical identity of a diagnostic step (structured fields only; never a model
    similarity judgment). Two steps are the SAME step when `key` is equal; `result_key` identifies
    the observation the step produces; the context fields detect a material change."""

    key: str = Field(description="Digest of (fault_id, action_key, target).")
    fault_id: str
    action_key: str = Field(description="pa:<knowledge_id>:<template> | cmd:<command> | obs:<objective tokens>|<expected tokens>")
    target: str = Field(default="", description="Canonical sorted name=value parameter bindings; empty when none.")
    result_key: Optional[str] = Field(default=None, description="Normalized rendered command whose output this step observes.")
    source_id: Optional[str] = None
    version_label: Optional[str] = None
    applicability: Optional[str] = None
    template: Optional[str] = None
    operation_type: Optional[str] = None
    sequence: Optional[int] = Field(default=None, description="Document order of the governed action within its section.")


class BlockedGovernedAction(BaseModel):
    """Continuity metadata of an applicability-blocked governed action: WHICH server-extracted
    ProcedureAction of WHICH selected governed section the step is, so the same logical step is
    recognised once applicability reaches MATCH. Server-owned (derived from selected evidence and
    the deterministic action extractor, never from model wording). It carries ZERO execution
    authority: it never populates a command, command source, approved catalog or operational
    control -- the command must be freshly resolved and authorized by the normal chain."""

    procedure_action_id: str = Field(description="Deterministic ProcedureAction identity (section + template).")
    normalized_command: str = Field(description="Normalized governed command template (identity only).")
    source_id: str = Field(description="Canonical governed source id (display / diagnostics only; never parsed).")
    knowledge_id: str
    version_label: str
    section_id: Optional[str] = Field(default=None, description="None only on records persisted before structured identity.")
    blocking_reason: str = Field(description="applicability_unknown / applicability_partial_match")
    unresolved_dimensions: list[str] = Field(default_factory=list)
    clarification_id: Optional[str] = Field(default=None, description="The open applicability clarification blocking it.")
    recorded_at: datetime = Field(default_factory=_now)
    released_at: Optional[datetime] = None
    released_by_action_id: Optional[str] = Field(
        default=None, description="ProcedureAction resolved and authorized on MATCH evidence that continued this step."
    )

    @property
    def identity(self) -> Optional[EvidenceIdentity]:
        """Structured source identity; None for a legacy record without `section_id` (never parsed
        from `source_id`)."""
        return identity_from_fields(self.knowledge_id, self.version_label, self.section_id)


class TroubleshootingStep(BaseModel):
    step_id: str = Field(default_factory=lambda: f"step-{uuid.uuid4().hex[:10]}")
    fault_id: str
    sequence: int = 0
    phase: ProgressionPhase = ProgressionPhase.STEP_PROPOSED
    objective: str
    hypothesis_being_tested: Optional[str] = Field(default=None, description="hypothesis_id under test, if any")
    procedure_action_id: Optional[str] = None
    command: Optional[str] = None
    command_source_id: Optional[str] = Field(default=None, description="Display / diagnostics only; never parsed.")
    command_source_identity: Optional[EvidenceIdentity] = Field(
        default=None, description="Structured identity of the command's governed source (exact tuple)."
    )
    target: Optional[dict[str, str]] = None
    selected_evidence_ids: list[str] = Field(default_factory=list, description="Display / diagnostics only; never parsed.")
    selected_evidence: list[EvidenceIdentity] = Field(
        default_factory=list, description="Structured identities of the governed evidence selected for this step."
    )
    applicability_snapshot: dict[str, Any] = Field(default_factory=dict)
    prerequisites: list[str] = Field(default_factory=list)
    expected_evidence: str = ""
    completion_criteria: list[str] = Field(default_factory=list)
    safety_constraints: list[str] = Field(default_factory=list)
    result: Optional[StepResult] = None
    result_source: Optional[ResultSource] = None
    candidate_observations: list[CandidateObservation] = Field(default_factory=list)
    observation_summary: Optional[str] = Field(
        default=None, description="Caller/model-written summary of the result: informative only, never evidence."
    )
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED
    status: StepStatus = StepStatus.PROPOSED
    status_history: list[StatusChange] = Field(default_factory=list)
    check_id: Optional[str] = Field(default=None, description="Legacy DiagnosticCheckRecord id this step projects to.")
    identity: Optional[StepIdentity] = None
    purpose: StepPurpose = StepPurpose.DIAGNOSTIC
    recheck_of: Optional[str] = Field(default=None, description="step_id this step deliberately repeats.")
    recheck_reason: Optional[str] = Field(default=None, description="Explicit rule that permitted the repeat.")
    control_id: Optional[str] = None
    control_stage: Optional[str] = None
    blocked_candidate: Optional[BlockedGovernedAction] = Field(
        default=None, description="Structural identity of an applicability-blocked governed action (no authority)."
    )
    evidence_requirement_id: Optional[str] = Field(default=None, description="The EvidenceRequirement this step acquires.")
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("command_source_identity", mode="before")
    @classmethod
    def _stored_identity(cls, value: Any) -> Optional[EvidenceIdentity]:
        return stored_identity(value)

    @field_validator("selected_evidence", mode="before")
    @classmethod
    def _stored_identities(cls, value: Any) -> list[EvidenceIdentity]:
        identities = [stored_identity(v) for v in value or []] if isinstance(value, (list, tuple)) else []
        return [i for i in identities if i is not None]


class HypothesisTransition(BaseModel):
    at: datetime = Field(default_factory=_now)
    from_state: Optional[HypothesisState] = None
    to_state: HypothesisState
    evidence_ids: list[str] = Field(default_factory=list, description="Observation/evidence ids supporting this transition.")
    step_id: Optional[str] = None
    reason: Optional[str] = None


class Hypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: f"hyp-{uuid.uuid4().hex[:10]}")
    fault_id: str
    statement: str
    state: HypothesisState = HypothesisState.ACTIVE
    transitions: list[HypothesisTransition] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


class ClarificationReason(str, Enum):
    APPLICABILITY = "applicability"
    TARGET_IDENTITY = "target_identity"
    MISSING_PARAMETER = "missing_parameter"
    MISSING_CONTEXT = "missing_context"
    POLICY_CLARIFICATION = "policy_clarification"
    OTHER = "other"


class ClarificationStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    """No longer relevant: a later server evaluation replaced it."""


class OpenQuestion(BaseModel):
    """An open question on one fault. With `requested_fields` it is the fault's PENDING
    CLARIFICATION: the server-recorded information the operator was asked for (field names come
    from server evaluation -- applicability dimensions, parameter names -- never from prose).
    It survives across turns until answered, cancelled or superseded."""

    question_id: str = Field(default_factory=lambda: f"q-{uuid.uuid4().hex[:10]}")
    fault_id: str
    text: str
    answered: bool = False
    reason: ClarificationReason = ClarificationReason.OTHER
    requested_fields: list[str] = Field(default_factory=list)
    resolved_values: dict[str, list[str]] = Field(default_factory=dict)
    status: ClarificationStatus = ClarificationStatus.OPEN
    originating_step_id: Optional[str] = None
    # Applicability clarification dependency (server-owned, independent of any pending step and of
    # how the specialist labelled its outcome). Identity / audit only: it never selects evidence,
    # issues a ProcedureAction or carries command authority into a later run.
    requirement_id: Optional[str] = Field(default=None, description="The open evidence requirement this clarification blocks, if any.")
    source_identities: list[EvidenceIdentity] = Field(
        default_factory=list,
        description="Structured identities of the governed sources whose applicability the answer must settle (identity only).",
    )
    known_facts: dict[str, list[str]] = Field(default_factory=dict, description="Confirmed applicability facts when it was recorded.")
    created_run_id: Optional[str] = None
    resolved_run_id: Optional[str] = None
    resulting_applicability: Optional[str] = Field(
        default=None, description="Applicability of the governed evidence evaluated after the answer (match / mismatch / ...)."
    )
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)
    resolved_at: Optional[datetime] = None

    @field_validator("source_identities", mode="before")
    @classmethod
    def _stored_identities(cls, value: Any) -> list[EvidenceIdentity]:
        identities = [stored_identity(v) for v in value or []] if isinstance(value, (list, tuple)) else []
        return [i for i in identities if i is not None]

    @property
    def is_clarification(self) -> bool:
        return bool(self.requested_fields)

    @property
    def unresolved_fields(self) -> list[str]:
        return [f for f in self.requested_fields if f not in self.resolved_values]


class EscalationRecord(BaseModel):
    """A SERVER escalation transition: which gate rule and policy caused it, on what evidence."""

    escalation_id: str = Field(default_factory=lambda: f"esc-{uuid.uuid4().hex[:10]}")
    fault_id: str
    rule: str
    reason: str
    evidence_step_ids: list[str] = Field(default_factory=list)
    evidence_result_ids: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict, description="{policy_id, version, rule}")
    proposer: str = Field(description="technical_authority_engineer (proposal accepted by the gate) or server (policy threshold).")
    proposed_reason: Optional[str] = Field(default=None, description="The proposer's own words (informative only).")
    at: datetime = Field(default_factory=_now)


class FaultProgression(BaseModel):
    """One fault/component thread: its own phase, current step and resolution state."""

    fault_id: str
    symptom_summary: str
    subject_component: Optional[str] = None
    node_id: Optional[str] = None
    phase: ProgressionPhase = ProgressionPhase.NEW
    current_step_id: Optional[str] = None
    resolution: ResolutionState = ResolutionState.UNRESOLVED
    remediations: list[RemediationRecord] = Field(default_factory=list)
    reopened_at_sequence: Optional[int] = Field(
        default=None, description="Last step sequence when the operator reopened a resolved/escalated fault; earlier results are stale."
    )
    escalations: list[EscalationRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @property
    def active_remediation(self) -> Optional[RemediationRecord]:
        return self.remediations[-1] if self.remediations else None


_STAGE_BY_PHASE = {
    "remediation_candidate": "remediation_candidate",
    "remediation": "remediation",
    "action_executed": "post_action_verification",
    "post_action_verification_required": "post_action_verification",
    "resolved": "resolved",
    "escalation_required": "escalation_required",
}


def fault_stage(fault: "FaultProgression") -> str:
    """Coarse lifecycle: diagnosis -> remediation_candidate -> remediation -> post_action_verification
    -> resolved / (reassess = back to diagnosis) / escalation_required."""
    return _STAGE_BY_PHASE.get(fault.phase.value, "diagnosis")


class ProgressionEvent(BaseModel):
    at: datetime = Field(default_factory=_now)
    event: str
    actor: str = "system"
    fault_id: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)


class TroubleshootingProgression(BaseModel):
    schema_version: str = SCHEMA_VERSION
    progression_id: str = Field(default_factory=lambda: f"prog-{uuid.uuid4().hex[:12]}")
    case_id: Optional[str] = None
    session_ids: list[str] = Field(default_factory=list)
    active_fault_id: Optional[str] = None
    faults: dict[str, FaultProgression] = Field(default_factory=dict)
    steps: list[TroubleshootingStep] = Field(default_factory=list)
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)
    evidence_requirements: list[EvidenceRequirement] = Field(default_factory=list)
    acquisition_gaps: list[AcquisitionGap] = Field(default_factory=list)
    events: list[ProgressionEvent] = Field(default_factory=list)
    version: int = 0
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    # ---- read views -------------------------------------------------------------------------
    @property
    def active_fault(self) -> Optional[FaultProgression]:
        return self.faults.get(self.active_fault_id or "")

    @property
    def current_phase(self) -> Optional[ProgressionPhase]:
        fault = self.active_fault
        return fault.phase if fault else None

    @property
    def current_step(self) -> Optional[TroubleshootingStep]:
        fault = self.active_fault
        return self.step(fault.current_step_id) if fault and fault.current_step_id else None

    def step(self, step_id: str) -> Optional[TroubleshootingStep]:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def steps_for(self, fault_id: str) -> list[TroubleshootingStep]:
        return sorted((s for s in self.steps if s.fault_id == fault_id), key=lambda s: s.sequence)

    def hypotheses_for(self, fault_id: str) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.fault_id == fault_id]

    # ---- mutations (record facts only; no decision logic) ----------------------------------------
    def _touch(self, event: str, fault_id: Optional[str] = None, actor: str = "system", **details: Any) -> None:
        self.version += 1
        self.updated_at = _now()
        self.events.append(ProgressionEvent(event=event, actor=actor, fault_id=fault_id, details=details))

    def add_fault(self, fault: FaultProgression, activate: bool = True) -> FaultProgression:
        if fault.fault_id in self.faults:
            raise ValueError(f"fault {fault.fault_id} already exists")
        self.faults[fault.fault_id] = fault
        if activate or self.active_fault_id is None:
            self.active_fault_id = fault.fault_id
        self._touch("fault_added", fault.fault_id, subject=fault.subject_component)
        return fault

    def activate_fault(self, fault_id: str) -> None:
        if fault_id not in self.faults:
            raise KeyError(fault_id)
        self.active_fault_id = fault_id
        self._touch("fault_activated", fault_id)

    def set_phase(self, fault_id: str, phase: ProgressionPhase, reason: Optional[str] = None) -> None:
        fault = self.faults[fault_id]
        fault.phase = phase
        fault.updated_at = _now()
        self._touch("phase_changed", fault_id, phase=phase.value, reason=reason)

    def set_resolution(self, fault_id: str, resolution: ResolutionState, reason: Optional[str] = None) -> None:
        fault = self.faults[fault_id]
        fault.resolution = resolution
        fault.updated_at = _now()
        self._touch("resolution_changed", fault_id, resolution=resolution.value, reason=reason)

    def add_remediation(self, record: RemediationRecord) -> RemediationRecord:
        self.faults[record.fault_id].remediations.append(record)
        self._touch("remediation_added", record.fault_id, remediation_id=record.remediation_id, step_id=record.step_id)
        return record

    def remediation_for_step(self, step_id: str) -> Optional[RemediationRecord]:
        step = self.step(step_id)
        fault = self.faults.get(step.fault_id) if step else None
        return next((r for r in (fault.remediations if fault else []) if r.step_id == step_id), None)

    def append_step(self, step: TroubleshootingStep) -> TroubleshootingStep:
        if step.fault_id not in self.faults:
            raise KeyError(f"unknown fault {step.fault_id}")
        step.sequence = max((s.sequence for s in self.steps), default=0) + 1
        step.status_history = step.status_history or [StatusChange(to_status=step.status, reason="created")]
        self.steps.append(step)
        fault = self.faults[step.fault_id]
        fault.current_step_id = step.step_id
        fault.updated_at = _now()
        self._touch("step_added", step.fault_id, step_id=step.step_id, status=step.status.value)
        return step

    def set_step_status(self, step_id: str, status: StepStatus, reason: Optional[str] = None) -> TroubleshootingStep:
        step = self.step(step_id)
        if step is None:
            raise KeyError(step_id)
        if step.result is not None and status in _AWAITING_RESULT_STATUSES:
            # A recorded result consumes the step for good: it can never await its result again. A new
            # execution of the same check is a NEW step (recheck_of / recheck_reason).
            raise ValueError(f"step {step_id} has a recorded result; it cannot return to {status.value}")
        step.status_history.append(StatusChange(from_status=step.status, to_status=status, reason=reason))
        step.status = status
        step.updated_at = _now()
        self._touch("step_status_changed", step.fault_id, step_id=step_id, status=status.value)
        return step

    def record_candidate_observation(
        self, step_id: str, text: str, source: ResultSource, validation: Mapping[str, Any], execution_id: Optional[str] = None
    ) -> CandidateObservation:
        step = self.step(step_id)
        if step is None:
            raise KeyError(step_id)
        candidate = CandidateObservation(text=text[:_MAX_RESULT_CHARS], source=source, validation=dict(validation), execution_id=execution_id)
        step.candidate_observations = [*step.candidate_observations[-9:], candidate]
        step.updated_at = _now()
        self._touch("candidate_observation_rejected", step.fault_id, step_id=step_id, status=validation.get("status"))
        return candidate

    def record_step_result(
        self,
        step_id: str,
        text: str,
        source: ResultSource,
        execution_id: Optional[str] = None,
        status: StepStatus = StepStatus.OBSERVED,
        validation: Optional[Mapping[str, Any]] = None,
    ) -> StepResult:
        step = self.step(step_id)
        if step is None:
            raise KeyError(step_id)
        step.result = StepResult(text=text[:_MAX_RESULT_CHARS], source=source, execution_id=execution_id, validation=dict(validation or {}))
        step.result_source = source
        self.set_step_status(step_id, status, reason=f"result recorded ({source.value})")
        return step.result

    def add_hypothesis(self, hypothesis: Hypothesis) -> Hypothesis:
        if hypothesis.fault_id not in self.faults:
            raise KeyError(f"unknown fault {hypothesis.fault_id}")
        hypothesis.transitions = hypothesis.transitions or [HypothesisTransition(to_state=hypothesis.state, reason="proposed")]
        self.hypotheses.append(hypothesis)
        self._touch("hypothesis_added", hypothesis.fault_id, hypothesis_id=hypothesis.hypothesis_id)
        return hypothesis

    def transition_hypothesis(
        self,
        hypothesis_id: str,
        to_state: HypothesisState,
        evidence_ids: Iterable[str],
        step_id: Optional[str] = None,
        reason: Optional[str] = None,
    ) -> Hypothesis:
        """Any move away from ACTIVE must cite the observation/evidence ids that justify it."""
        hypothesis = next((h for h in self.hypotheses if h.hypothesis_id == hypothesis_id), None)
        if hypothesis is None:
            raise KeyError(hypothesis_id)
        evidence = [e for e in evidence_ids if isinstance(e, str) and e.strip()]
        if to_state is not HypothesisState.ACTIVE and not evidence:
            raise ValueError("a hypothesis transition must reference supporting observation/evidence ids")
        hypothesis.transitions.append(
            HypothesisTransition(from_state=hypothesis.state, to_state=to_state, evidence_ids=evidence, step_id=step_id, reason=reason)
        )
        hypothesis.state = to_state
        hypothesis.updated_at = _now()
        self._touch("hypothesis_transition", hypothesis.fault_id, hypothesis_id=hypothesis_id, state=to_state.value, evidence_ids=evidence)
        return hypothesis

    def add_open_question(self, fault_id: str, text: str) -> OpenQuestion:
        if fault_id not in self.faults:
            raise KeyError(f"unknown fault {fault_id}")
        question = OpenQuestion(fault_id=fault_id, text=text)
        self.open_questions.append(question)
        self._touch("open_question_added", fault_id, question_id=question.question_id)
        return question

    # ---- evidence requirements / acquisition gaps -----------------------------------------------
    def requirement(self, requirement_id: Optional[str]) -> Optional[EvidenceRequirement]:
        return next((r for r in self.evidence_requirements if r.requirement_id == requirement_id), None) if requirement_id else None

    def requirements_for(self, fault_id: str) -> list[EvidenceRequirement]:
        return [r for r in self.evidence_requirements if r.fault_id == fault_id]

    def open_requirements(self, fault_id: str) -> list[EvidenceRequirement]:
        """UNSATISFIED requirements of ONE fault (never another fault's), most recent first."""
        return [r for r in reversed(self.evidence_requirements) if r.fault_id == fault_id and r.status is RequirementStatus.UNSATISFIED]

    def add_requirement(self, requirement: EvidenceRequirement) -> EvidenceRequirement:
        if requirement.fault_id not in self.faults:
            raise KeyError(f"unknown fault {requirement.fault_id}")
        if any(r.requirement_id == requirement.requirement_id for r in self.evidence_requirements):
            # Continuity: the same requirement object was reused (and refined) this turn.
            self._touch("evidence_requirement_updated", requirement.fault_id, requirement_id=requirement.requirement_id,
                        status=requirement.status.value,
                        blocking_reason=requirement.blocking_reason.value if requirement.blocking_reason else None)
            return requirement
        self.evidence_requirements.append(requirement)
        self._touch(
            "evidence_requirement_recorded", requirement.fault_id, requirement_id=requirement.requirement_id,
            kind=requirement.kind.value, status=requirement.status.value,
            blocking_reason=requirement.blocking_reason.value if requirement.blocking_reason else None,
        )
        return requirement

    def satisfy_requirement(self, requirement_id: str, satisfied_by: list[EvidenceSourceRef]) -> EvidenceRequirement:
        requirement = self.requirement(requirement_id)
        if requirement is None:
            raise KeyError(requirement_id)
        requirement.status = RequirementStatus.SATISFIED
        requirement.satisfied_by = [*requirement.satisfied_by, *satisfied_by]
        requirement.blocking_reason = None
        requirement.satisfied_at = requirement.updated_at = _now()
        self._touch("evidence_requirement_satisfied", requirement.fault_id, requirement_id=requirement_id,
                    sources=[s.source_id for s in satisfied_by])
        self.close_gaps(requirement_id, GapStatus.RESOLVED, "evidence obtained")
        return requirement

    def open_gap(self, requirement_id: str) -> Optional[AcquisitionGap]:
        """The ONE open acquisition gap of a requirement (the most recent, if legacy duplicates exist)."""
        return next((g for g in reversed(self.acquisition_gaps) if g.requirement_id == requirement_id and g.status is GapStatus.OPEN), None)

    def close_gaps(self, requirement_id: str, status: GapStatus, resolution: str) -> list[AcquisitionGap]:
        """Close every open gap of the requirement (history and discovery attempts are kept)."""
        closed = []
        for gap in self.acquisition_gaps:
            if gap.requirement_id == requirement_id and gap.status is GapStatus.OPEN:
                gap.status = status
                gap.resolution = resolution
                gap.resolved_at = gap.updated_at = _now()
                closed.append(gap)
                self._touch(f"acquisition_gap_{status.value}", gap.fault_id, gap_id=gap.gap_id, requirement_id=requirement_id, resolution=resolution)
        return closed

    def record_gap_attempt(self, gap: AcquisitionGap, attempt: DiscoveryAttempt, reason: GapReason) -> AcquisitionGap:
        gap.add_attempt(attempt, reason)
        self._touch("acquisition_gap_attempt_recorded", gap.fault_id, gap_id=gap.gap_id, requirement_id=gap.requirement_id,
                    attempts=len(gap.discovery_attempts), reason=reason.value)
        return gap

    def add_acquisition_hint(self, requirement_id: str, hint: AcquisitionHint) -> Optional[AcquisitionHint]:
        """Attach an operator acquisition hint (untrusted; never authority) to an open requirement.
        A hint with the same value is recorded once."""
        requirement = self.requirement(requirement_id)
        if requirement is None or requirement.status is not RequirementStatus.UNSATISFIED:
            return None
        existing = next((h for h in requirement.acquisition_hints if h.value.casefold() == hint.value.casefold()), None)
        if existing is not None:
            return existing
        requirement.acquisition_hints = [*requirement.acquisition_hints, hint][-10:]
        requirement.updated_at = _now()
        self._touch("acquisition_hint_recorded", requirement.fault_id, requirement_id=requirement_id, hint_id=hint.hint_id,
                    hint_type=hint.hint_type.value)
        return hint

    def record_acquisition_gap(self, gap: AcquisitionGap) -> AcquisitionGap:
        if gap.fault_id not in self.faults:
            raise KeyError(f"unknown fault {gap.fault_id}")
        if any(g.gap_id == gap.gap_id for g in self.acquisition_gaps):
            return gap  # the existing open gap was updated with a new discovery attempt
        self.acquisition_gaps.append(gap)
        self._touch("acquisition_gap_recorded", gap.fault_id, gap_id=gap.gap_id, requirement_id=gap.requirement_id, reason=gap.gap_reason.value)
        return gap

    # ---- pending clarification (one server-owned record per fault and reason) -------------------
    def pending_clarification(self, fault_id: Optional[str], reason: Optional[ClarificationReason] = None) -> Optional[OpenQuestion]:
        """The fault's most recent OPEN clarification (optionally of one reason). Never another fault's."""
        for question in reversed(self.open_questions):
            if (
                question.fault_id == fault_id
                and question.is_clarification
                and question.status is ClarificationStatus.OPEN
                and (reason is None or question.reason is reason)
            ):
                return question
        return None

    def record_clarification(
        self,
        fault_id: str,
        reason: ClarificationReason,
        requested_fields: Iterable[str],
        text: str,
        originating_step_id: Optional[str] = None,
        known_values: Optional[Mapping[str, list[str]]] = None,
        *,
        requirement_id: Optional[str] = None,
        source_identities: Iterable[EvidenceIdentity] = (),
        run_id: Optional[str] = None,
    ) -> Optional[OpenQuestion]:
        """Open or refresh the fault's clarification of `reason` from the LATEST server evaluation:
        `requested_fields` is what that evaluation still needs. A field asked for earlier that the
        evaluation no longer needs is resolved from `known_values` (confirmed context) or, when no
        value is known, dropped as no longer relevant. Resolved values are never discarded.
        `requirement_id` / `source_identities`: the requirement and governed sources the answer must
        settle (identity only, merged on refresh)."""
        if fault_id not in self.faults:
            raise KeyError(f"unknown fault {fault_id}")
        fields = [f for f in dict.fromkeys(str(f).strip() for f in requested_fields) if f]
        question = self.pending_clarification(fault_id, reason)
        if not fields:
            return question
        identities = [i for i in source_identities if i is not None]
        if question is None:
            question = OpenQuestion(
                fault_id=fault_id, text=text, reason=reason, requested_fields=fields, originating_step_id=originating_step_id,
                requirement_id=requirement_id, source_identities=list(dict.fromkeys(identities)),
                known_facts={k: list(v) for k, v in (known_values or {}).items()}, created_run_id=run_id,
            )
            self.open_questions.append(question)
            self._touch(
                "clarification_requested", fault_id, question_id=question.question_id, reason=reason.value, fields=fields,
                requirement_id=requirement_id, run_id=run_id,
            )
            return question
        question.requirement_id = question.requirement_id or requirement_id
        question.source_identities = list(dict.fromkeys([*question.source_identities, *identities]))
        before = (list(question.requested_fields), dict(question.resolved_values))
        for name in question.unresolved_fields:
            if name in fields:
                continue
            values = [v for v in (known_values or {}).get(name, []) if isinstance(v, str) and v.strip()]
            if values:
                question.resolved_values[name] = values
            else:
                question.requested_fields.remove(name)
        question.requested_fields.extend(f for f in fields if f not in question.requested_fields)
        question.text = text
        question.originating_step_id = originating_step_id or question.originating_step_id
        question.updated_at = _now()
        if (list(question.requested_fields), dict(question.resolved_values)) != before:
            self._touch("clarification_updated", fault_id, question_id=question.question_id, fields=list(question.requested_fields))
        return question

    def resolve_clarification_fields(self, question_id: str, values: Mapping[str, list[str]], actor: str = "operator") -> OpenQuestion:
        """Record values for requested fields only (anything else is ignored). The clarification is
        RESOLVED once no requested field remains unresolved."""
        question = next((q for q in self.open_questions if q.question_id == question_id), None)
        if question is None:
            raise KeyError(question_id)
        if question.status is not ClarificationStatus.OPEN:
            return question
        bound = {
            name: [v for v in vals if isinstance(v, str) and v.strip()]
            for name, vals in values.items()
            if name in question.requested_fields and name not in question.resolved_values
        }
        bound = {name: vals for name, vals in bound.items() if vals}
        if not bound:
            return question
        question.resolved_values.update(bound)
        question.updated_at = _now()
        self._touch("clarification_answered", question.fault_id, actor=actor, question_id=question_id, fields=sorted(bound))
        if not question.unresolved_fields:
            self.close_clarification(question_id, ClarificationStatus.RESOLVED, reason="every requested field resolved", actor=actor)
        return question

    def close_clarification(self, question_id: str, status: ClarificationStatus, reason: Optional[str] = None, actor: str = "system") -> OpenQuestion:
        question = next((q for q in self.open_questions if q.question_id == question_id), None)
        if question is None:
            raise KeyError(question_id)
        if status is ClarificationStatus.OPEN or question.status is not ClarificationStatus.OPEN:
            return question
        question.status = status
        question.answered = status is ClarificationStatus.RESOLVED
        question.resolved_at = question.updated_at = _now()
        self._touch(f"clarification_{status.value}", question.fault_id, actor=actor, question_id=question_id, reason=reason)
        return question

    def latest_clarification(self, fault_id: Optional[str], reason: ClarificationReason) -> Optional[OpenQuestion]:
        """The fault's most recent clarification of `reason`, whatever its status. Never another fault's."""
        return next(
            (q for q in reversed(self.open_questions) if q.fault_id == fault_id and q.is_clarification and q.reason is reason),
            None,
        )

    def record_clarification_outcome(self, question_id: str, resulting_applicability: Optional[str], run_id: Optional[str]) -> Optional[OpenQuestion]:
        """Audit: the applicability of the governed evidence THIS run evaluated after the answer. It
        is a record of that run only; it never selects evidence or grants authority."""
        question = next((q for q in self.open_questions if q.question_id == question_id), None)
        if question is None or not resulting_applicability:
            return question
        if (question.resulting_applicability, question.resolved_run_id) != (resulting_applicability, run_id):
            question.resulting_applicability = resulting_applicability
            question.resolved_run_id = run_id
            question.updated_at = _now()
            self._touch(
                "clarification_outcome", question.fault_id, question_id=question_id, resulting_applicability=resulting_applicability, run_id=run_id
            )
        return question

    def release_applicability_blockers(self, fault_id: str, reason: str) -> list[str]:
        """Once applicability is RESOLVED to MATCH, an `applicability_unresolved` blocker on this
        fault's open requirements (and their governed candidates) is stale: it is cleared. The
        requirements stay UNSATISFIED; the next acquisition decision re-derives any blocker from its
        own run. An applicability-blocked candidate reverts to structural identity (zero authority)."""
        released: list[str] = []
        for requirement in self.open_requirements(fault_id):
            changed = False
            if requirement.blocking_reason is GapReason.APPLICABILITY_UNRESOLVED:
                requirement.blocking_reason = None
                changed = True
            for candidate in requirement.acquisition_candidates:
                if candidate.blocking_reason is GapReason.APPLICABILITY_UNRESOLVED:
                    candidate.blocking_reason = None
                    changed = True
                if candidate.validation is CandidateValidation.BLOCKED_APPLICABILITY:
                    # Any other validation describes its own run's decision and is kept as recorded.
                    candidate.validation = CandidateValidation.KNOWN_STRUCTURAL
                    candidate.validation_reason = reason
                    changed = True
            if changed:
                requirement.updated_at = _now()
                released.append(requirement.requirement_id)
        if released:
            self._touch("applicability_blockers_released", fault_id, requirement_ids=released, reason=reason)
        return released


# ---------------------------------------------------------------------------------------------
# Runtime projection: progression -> bounded TroubleshootingState (existing contract)
# ---------------------------------------------------------------------------------------------

_STEP_TO_CHECK: dict[StepStatus, CheckLifecycleStatus] = {
    StepStatus.PROPOSED: CheckLifecycleStatus.RECOMMENDED,
    StepStatus.VALIDATED: CheckLifecycleStatus.RECOMMENDED,
    StepStatus.PRESENTED: CheckLifecycleStatus.RECOMMENDED,
    StepStatus.BLOCKED_BY_CLARIFICATION: CheckLifecycleStatus.RECOMMENDED,
    StepStatus.BLOCKED_GOVERNED_ACQUISITION_GAP: CheckLifecycleStatus.SKIPPED,
    StepStatus.EXECUTED: CheckLifecycleStatus.EXECUTED,
    StepStatus.OBSERVED: CheckLifecycleStatus.EXECUTED,
    StepStatus.FAILED: CheckLifecycleStatus.EXECUTED,
    StepStatus.VERIFIED: CheckLifecycleStatus.COMPLETED,
    StepStatus.COMPLETED: CheckLifecycleStatus.COMPLETED,
    StepStatus.REJECTED: CheckLifecycleStatus.SKIPPED,
    StepStatus.SKIPPED: CheckLifecycleStatus.SKIPPED,
    StepStatus.CANCELLED: CheckLifecycleStatus.SKIPPED,
    StepStatus.SUPERSEDED: CheckLifecycleStatus.SKIPPED,
}


def _projected_status(fault: FaultProgression) -> TroubleshootingStatus:
    if fault.resolution is ResolutionState.RESOLVED or fault.phase is ProgressionPhase.RESOLVED:
        return TroubleshootingStatus.RESOLVED
    if fault.resolution is ResolutionState.ESCALATED or fault.phase is ProgressionPhase.ESCALATION_REQUIRED:
        return TroubleshootingStatus.ESCALATION_REQUIRED
    if fault.phase in (ProgressionPhase.REMEDIATION_CANDIDATE, ProgressionPhase.REMEDIATION):
        return TroubleshootingStatus.MITIGATION_RECOMMENDED
    if fault.phase in (ProgressionPhase.ACTION_EXECUTED, ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED):
        return TroubleshootingStatus.TESTING_NEXT_STEP
    if fault.phase in (ProgressionPhase.STEP_PROPOSED, ProgressionPhase.STEP_VALIDATED, ProgressionPhase.AWAITING_OBSERVATION):
        return TroubleshootingStatus.TESTING_NEXT_STEP
    return TroubleshootingStatus.INVESTIGATING


def _projected_check_status(step: TroubleshootingStep) -> CheckLifecycleStatus:
    status = _STEP_TO_CHECK[step.status]
    # Check-record semantics: an operator-reported result leaves the record EXECUTED; only a
    # controlled execution completes it. The progression step holds the authoritative status.
    if status is CheckLifecycleStatus.COMPLETED and step.result is not None and step.result.source is not ResultSource.EXECUTION_ADAPTER:
        return CheckLifecycleStatus.EXECUTED
    return status


def _trusted_operator_text(result: Optional[StepResult]) -> Optional[str]:
    """Operator text that VALIDATED as the step's result (a command error is never evidence)."""
    if result is None or result.source is not ResultSource.OPERATOR_MESSAGE:
        return None
    return result.text if result.validation.get("status", "validated") == "validated" else None


def _projected_check(step: TroubleshootingStep) -> DiagnosticCheckRecord:
    result = step.result
    status = _projected_check_status(step)
    return DiagnosticCheckRecord(
        check_id=step.check_id or step.step_id,
        action=step.objective,
        rationale=step.hypothesis_being_tested or "",
        grounded_command=step.command,
        command_source_id=step.command_source_id,
        procedure_action_id=step.procedure_action_id,
        control_id=step.control_id,
        control_stage=step.control_stage,
        expected_observation=step.expected_evidence,
        observed_result=(result.text if result and result.source is ResultSource.EXECUTION_ADAPTER else None)
        or step.observation_summary
        or (result.text if result else None),
        observed_result_source="execution_adapter" if result and result.source is ResultSource.EXECUTION_ADAPTER else None,
        operator_observation=_trusted_operator_text(result),
        execution_ids=[result.execution_id] if result and result.execution_id else [],
        status=status,
        recommended_at=step.created_at,
        executed_at=result.recorded_at if result else None,
        completed_at=step.updated_at if status is CheckLifecycleStatus.COMPLETED else None,
    )


PROJECTION_MAX_STEPS = 50


def requirement_view(requirement: EvidenceRequirement) -> dict[str, Any]:
    """Identity/state view of an evidence requirement (projections, tool results, traces)."""
    selected = requirement.selected_acquisition
    return {
        "requirement_id": requirement.requirement_id,
        "kind": requirement.kind.value,
        "description": requirement.description,
        "status": requirement.status.value,
        "blocking_reason": requirement.blocking_reason.value if requirement.blocking_reason else None,
        "selected_acquisition": selected.acquisition_type.value if selected else None,
        "candidates": len(requirement.acquisition_candidates),
        "originating_step_id": requirement.originating_step_id,
    }


def clarification_view(question: OpenQuestion) -> dict[str, Any]:
    """Identity/field view of a clarification (projections, tool results, traces)."""
    return {
        "question_id": question.question_id,
        "fault_id": question.fault_id,
        "reason": question.reason.value,
        "status": question.status.value,
        "requested_fields": list(question.requested_fields),
        "unresolved_fields": question.unresolved_fields,
        "resolved_values": {k: list(v) for k, v in question.resolved_values.items()},
        "originating_step_id": question.originating_step_id,
    }


def applicability_clarification_trace_view(question: OpenQuestion) -> dict[str, Any]:
    """Diagnostics of an applicability clarification dependency (identifiers and field names only)."""
    return {
        "clarification_id": question.question_id,
        "fault_id": question.fault_id,
        "requirement_id": question.requirement_id,
        "missing_dimensions": list(question.requested_fields),
        "unresolved_dimensions": question.unresolved_fields,
        "answered_dimensions": sorted(question.resolved_values),
        "source_identities": [i.canonical for i in question.source_identities],
        "status": question.status.value,
        "originating_step_id": question.originating_step_id,
        "created_run_id": question.created_run_id,
        "resolved_run_id": question.resolved_run_id,
        "resulting_applicability": question.resulting_applicability,
    }


def stale_applicability_blockers(progression: TroubleshootingProgression, fault_id: str) -> list[str]:
    """INVARIANT: open requirements of the fault still blocked by `applicability_unresolved` although
    its applicability clarification is settled -- no OPEN one remains and the latest was answered with
    a MATCH evaluation. Must always be empty (contradictory state otherwise)."""
    if progression.pending_clarification(fault_id, ClarificationReason.APPLICABILITY) is not None:
        return []
    latest = progression.latest_clarification(fault_id, ClarificationReason.APPLICABILITY)
    if latest is None or latest.resulting_applicability != "match":
        return []
    return [
        r.requirement_id
        for r in progression.open_requirements(fault_id)
        if r.blocking_reason is GapReason.APPLICABILITY_UNRESOLVED
        or any(c.blocking_reason is GapReason.APPLICABILITY_UNRESOLVED for c in r.acquisition_candidates)
    ]


def project_threads(progression: TroubleshootingProgression, max_steps: int = PROJECTION_MAX_STEPS) -> dict[str, TroubleshootingState]:
    """Read projection of EVERY fault thread (the session-state `troubleshooting_threads` view)."""
    return {fid: project_troubleshooting_state(progression, fid, max_steps) for fid in progression.faults}


def project_troubleshooting_state(
    progression: TroubleshootingProgression, fault_id: Optional[str] = None, max_steps: int = 20
) -> Optional[TroubleshootingState]:
    """Bounded runtime view of ONE fault thread (default: the active one). Only that fault's steps
    and hypotheses are included; at most the latest `max_steps` steps."""
    fault = progression.faults.get(fault_id or progression.active_fault_id or "")
    if fault is None:
        return None
    steps = progression.steps_for(fault.fault_id)[-max_steps:]
    live = [h for h in progression.hypotheses_for(fault.fault_id) if h.state in (HypothesisState.CONFIRMED, HypothesisState.SUPPORTED, HypothesisState.ACTIVE)]
    rank = {HypothesisState.CONFIRMED: 0, HypothesisState.SUPPORTED: 1, HypothesisState.ACTIVE: 2}
    live.sort(key=lambda h: (rank[h.state], -h.updated_at.timestamp()))
    sources: list[str] = []
    for step in steps:
        # Structured identity only: a legacy step with just the canonical display string contributes nothing.
        if step.command_source_identity is not None and step.command_source_identity.knowledge_id not in sources:
            sources.append(step.command_source_identity.knowledge_id)
    clarification = progression.pending_clarification(fault.fault_id)
    return TroubleshootingState(
        fault_id=fault.fault_id,
        status=_projected_status(fault),
        symptom_summary=fault.symptom_summary,
        subject_component=fault.subject_component,
        session_id=progression.session_ids[-1] if progression.session_ids else None,
        case_id=progression.case_id,
        node_id=fault.node_id,
        working_hypothesis=live[0].statement if live else None,
        competing_hypotheses=[h.statement for h in live[1:]],
        verified_evidence_ids=[s.result.result_id for s in steps if s.result and s.result.source is not ResultSource.CALLER_SUMMARY],
        diagnostic_history=[_projected_check(s) for s in steps],
        applicable_procedure_ids=sources,
        pending_clarification=clarification_view(clarification) if clarification is not None else None,
        evidence_requirements=[requirement_view(r) for r in progression.requirements_for(fault.fault_id)][-10:],
        created_at=fault.created_at,
        updated_at=fault.updated_at,
    )


# ---------------------------------------------------------------------------------------------
# Backward-compatible adaptation from existing fault-thread state (read-only on that state)
# ---------------------------------------------------------------------------------------------

_CHECK_TO_STEP: dict[CheckLifecycleStatus, StepStatus] = {
    CheckLifecycleStatus.RECOMMENDED: StepStatus.PRESENTED,
    CheckLifecycleStatus.EXECUTED: StepStatus.EXECUTED,
    CheckLifecycleStatus.COMPLETED: StepStatus.COMPLETED,
    CheckLifecycleStatus.SKIPPED: StepStatus.SKIPPED,
}


def _legacy_threads(state: Mapping[str, Any]) -> tuple[dict[str, TroubleshootingState], Optional[str]]:
    threads: dict[str, TroubleshootingState] = {}
    raw = state.get(_LEGACY_THREADS_KEY)
    if isinstance(raw, dict):
        for fid, value in raw.items():
            try:
                threads[str(fid)] = TroubleshootingState.model_validate(value)
            except Exception:
                continue
    active_id: Optional[str] = None
    active_raw = state.get(_LEGACY_ACTIVE_KEY)
    if isinstance(active_raw, dict):
        try:
            active = TroubleshootingState.model_validate(active_raw)
            threads[active.fault_id] = active  # the active copy is authoritative for its own fault
            active_id = active.fault_id
        except Exception:
            pass
    return threads, active_id


def _legacy_summary(check: DiagnosticCheckRecord) -> Optional[str]:
    if check.observed_result and check.observed_result_source != "execution_adapter" and check.operator_observation:
        return check.observed_result
    return None


def _legacy_result(check: DiagnosticCheckRecord) -> Optional[StepResult]:
    if check.operator_observation:
        return StepResult(text=check.operator_observation, source=ResultSource.OPERATOR_MESSAGE, recorded_at=check.executed_at or check.recommended_at)
    if check.observed_result:
        source = ResultSource.EXECUTION_ADAPTER if check.observed_result_source == "execution_adapter" else ResultSource.CALLER_SUMMARY
        execution_id = check.execution_ids[-1] if check.execution_ids else None
        return StepResult(text=check.observed_result, source=source, recorded_at=check.executed_at or check.recommended_at, execution_id=execution_id)
    return None


def _legacy_phase(ts: TroubleshootingState, steps: list[TroubleshootingStep]) -> ProgressionPhase:
    if ts.status is TroubleshootingStatus.RESOLVED:
        return ProgressionPhase.RESOLVED
    if ts.status is TroubleshootingStatus.ESCALATION_REQUIRED:
        return ProgressionPhase.ESCALATION_REQUIRED
    if ts.status is TroubleshootingStatus.MITIGATION_RECOMMENDED:
        return ProgressionPhase.REMEDIATION_CANDIDATE
    if not steps:
        return ProgressionPhase.CONTEXT_BUILDING
    last = steps[-1].status
    if last in (StepStatus.PROPOSED, StepStatus.VALIDATED, StepStatus.PRESENTED):
        return ProgressionPhase.AWAITING_OBSERVATION
    if last in (StepStatus.EXECUTED, StepStatus.OBSERVED):
        return ProgressionPhase.RESULT_RECEIVED
    return ProgressionPhase.REASSESS


def progression_from_legacy_state(
    state: Mapping[str, Any], session_id: Optional[str] = None, case_id: Optional[str] = None
) -> Optional[TroubleshootingProgression]:
    """Deterministically adapt an existing session's fault-thread state into a progression. Never
    writes to `state`. Returns None when the session has no troubleshooting state at all."""
    threads, active_id = _legacy_threads(state)
    if not threads:
        return None
    progression = TroubleshootingProgression(case_id=case_id or next((t.case_id for t in threads.values() if t.case_id), None))
    sessions = [s for s in [session_id, *(t.session_id for t in threads.values())] if s]
    progression.session_ids = list(dict.fromkeys(sessions))
    progression.created_at = min(t.created_at for t in threads.values())

    ordered_checks: list[tuple[datetime, int, str, DiagnosticCheckRecord]] = []
    for fid, ts in threads.items():
        progression.faults[fid] = FaultProgression(
            fault_id=fid, symptom_summary=ts.symptom_summary, subject_component=ts.subject_component, node_id=ts.node_id,
            created_at=ts.created_at, updated_at=ts.updated_at,
            resolution=ResolutionState.RESOLVED if ts.status is TroubleshootingStatus.RESOLVED
            else ResolutionState.ESCALATED if ts.status is TroubleshootingStatus.ESCALATION_REQUIRED else ResolutionState.UNRESOLVED,
        )
        for statement in [ts.working_hypothesis, *ts.competing_hypotheses]:
            if statement:
                progression.hypotheses.append(
                    Hypothesis(fault_id=fid, statement=statement, transitions=[HypothesisTransition(to_state=HypothesisState.ACTIVE, reason="adapted from legacy state")])
                )
        for index, check in enumerate(ts.diagnostic_history):
            ordered_checks.append((check.recommended_at, index, fid, check))

    ordered_checks.sort(key=lambda item: (item[0], item[1]))
    for sequence, (_, _, fid, check) in enumerate(ordered_checks, start=1):
        result = _legacy_result(check)
        status = _CHECK_TO_STEP[check.status]
        if status is StepStatus.EXECUTED and result is not None:
            status = StepStatus.OBSERVED
        step = TroubleshootingStep(
            step_id=f"step-{check.check_id}", fault_id=fid, sequence=sequence, objective=check.action,
            procedure_action_id=check.procedure_action_id, command=check.grounded_command, command_source_id=check.command_source_id,
            expected_evidence=check.expected_observation, result=result, result_source=result.source if result else None,
            observation_summary=_legacy_summary(check),
            status=status, status_history=[StatusChange(to_status=status, reason="adapted from legacy state")],
            check_id=check.check_id, control_id=check.control_id, control_stage=check.control_stage,
            created_at=check.recommended_at, updated_at=check.completed_at or check.executed_at or check.recommended_at,
        )
        progression.steps.append(step)
        progression.faults[fid].current_step_id = step.step_id

    for fid, ts in threads.items():
        progression.faults[fid].phase = _legacy_phase(ts, progression.steps_for(fid))
    progression.active_fault_id = active_id or max(threads.values(), key=lambda t: t.updated_at).fault_id
    progression.events.append(ProgressionEvent(event="adapted_from_legacy_state", details={"faults": sorted(threads)}))
    progression.updated_at = _now()
    return progression


# ---------------------------------------------------------------------------------------------
# Session-scope storage (sessions not linked to a Case; see progression_repository.py)
# ---------------------------------------------------------------------------------------------


def load_progression(state: Mapping[str, Any]) -> Optional[TroubleshootingProgression]:
    raw = state.get(PROGRESSION_STATE_KEY) if state is not None else None
    if not isinstance(raw, dict):
        return None
    try:
        return TroubleshootingProgression.model_validate(raw)
    except Exception:
        return None


def load_or_adapt_progression(
    state: Mapping[str, Any], session_id: Optional[str] = None, case_id: Optional[str] = None
) -> Optional[TroubleshootingProgression]:
    """The stored progression if present, otherwise one adapted from legacy fault-thread state.
    Never writes; callers persist explicitly with `save_progression`."""
    return load_progression(state) or progression_from_legacy_state(state, session_id=session_id, case_id=case_id)


def save_progression(state: MutableMapping[str, Any], progression: TroubleshootingProgression) -> None:
    state[PROGRESSION_STATE_KEY] = progression.model_dump(mode="json")
