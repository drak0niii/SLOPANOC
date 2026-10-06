"""Deterministic operational control plane around Command-Authority-authorized ProcedureActions.

STATE-CHANGE PATH
    TAE turn: resolved candidate -> Command Authority rejects ONLY for missing trusted target
      confirmation (checked by an authority *preview* under a hypothetical confirmed target; the
      preview result is never used as authority) -> Policy: CLARIFICATION_REQUIRED /
      TARGET_NOT_CONFIRMED -> server creates an `operational.confirmTarget` proposal card.
    Operator clicks "Confirm target" (trusted /approve endpoint, user identity from UserContext)
      -> source re-validated against the repository -> TargetConfirmation recorded (confirmed_by)
      -> REAL Command Authority re-run with trusted_context.target_confirmed derived from that
      confirmation -> attestation -> Policy: APPROVAL_REQUIRED -> `operational.procedureAction`
      approval card.
    Operator clicks "Approve" -> re-validation (binding hash, confirmation, source, authority,
      policy) -> APPROVED -> READY_FOR_EXECUTION. Nothing executes: `/execute` refuses operational
      proposals and no state-changing adapter exists.

DIAGNOSTIC-READ PATH
    TAE turn: Command Authority authorizes -> attestation -> Policy: ALLOWED (read_execution only
      when an adapter is registered for the explicitly configured execution context, otherwise
      advisory). Execution happens only when an operator explicitly requests it for that check
      (`execute_read_check`), which re-runs every gate, mints a signed AuthorizedReadAction and
      calls the registered adapter. Output is recorded as observed evidence on the same check.

Nothing here is a model tool; nothing here calls an LLM. There is no loop: every action passes
the full pipeline independently.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, MutableMapping, Optional

from pydantic import BaseModel, Field

from backend.agents.technical_authority_engineer.schemas import ApprovedCommand, CommandOperationType, EvidenceReference
from backend.approval.canonical import compute_payload_hash
from backend.approval.schemas import ActionProposal, OperationalOperation, ProposalStatus
from backend.approval.service import (
    PENDING_ACTION_PROPOSAL_STATE_KEY,
    approve_proposal,
    consume_proposal,
    effective_status,
    load_active_proposal,
    reject_proposal,
)
from backend.config.settings import get_settings
from backend.operations.context import ActionSourceIdentity, OperationalActionContext, target_identity_for, validated_target_identity
from backend.operations.execution import (
    ExecutionAdapterRegistry,
    ExecutionContext,
    ExecutionResult,
    ExecutionStatus,
    get_execution_adapter_registry,
    issue_authorized_read_action,
)
from backend.operations.policy import (
    AuthorityAttestation,
    AuthorityStatus,
    PolicyDecision,
    PolicyDecisionKind,
    attest_authority,
    evaluate_action_policy,
)
from backend.operations.signing import text_hash
from backend.operations.targets import (
    TARGET_CONFIRMATIONS_STATE_KEY,
    confirmation_mismatch,
    create_target_confirmation,
    invalidate_target_confirmation,
    load_target_confirmation,
)

OPERATIONAL_CONTROLS_STATE_KEY = "operational_action_controls"
PERSISTED_STATE_KEYS = (
    PENDING_ACTION_PROPOSAL_STATE_KEY,
    OPERATIONAL_CONTROLS_STATE_KEY,
    TARGET_CONFIRMATIONS_STATE_KEY,
    "troubleshooting_state",
    "troubleshooting_threads",
    "troubleshooting_progression",
    "troubleshooting_progression_ref",
)

_audit_logger = logging.getLogger("backend.operations.audit")
_STATE_CHANGING = {CommandOperationType.MUTATING_OPERATIONAL.value, CommandOperationType.CONFIGURATION_CHANGE.value}


class ControlStage(str, Enum):
    AUTHORIZED = "authorized"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_APPROVAL = "awaiting_approval"
    READY_FOR_EXECUTION = "ready_for_execution"
    EXECUTING = "executing"
    EXECUTED = "executed"
    COMPLETED = "completed"
    FAILED = "failed"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"


class OperationalApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVALIDATED = "invalidated"


class AuditEvent(BaseModel):
    at: datetime
    event: str
    actor: str
    stage: str
    details: dict[str, Any] = Field(default_factory=dict)


class OperationalControlRecord(BaseModel):
    context: OperationalActionContext
    stage: ControlStage
    execution_mode: str
    policy: Optional[dict[str, Any]] = None
    execution_context: Optional[str] = None
    confirmation_request_id: Optional[str] = None
    confirmation_id: Optional[str] = None
    approval_id: Optional[str] = None
    approval_status: Optional[OperationalApprovalStatus] = None
    approved_by: Optional[str] = None
    approved_at: Optional[datetime] = None
    rejected_by: Optional[str] = None
    invalidation_reason: Optional[str] = None
    executions: list[dict[str, Any]] = Field(default_factory=list)
    audit: list[AuditEvent] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class OperationalDenial(Exception):
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


class EligibilityResult(BaseModel):
    eligible: bool
    stage: str
    reason_codes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# persistence helpers
# ---------------------------------------------------------------------------------------------


def _now(now: Optional[datetime]) -> datetime:
    return now if now is not None else datetime.now(timezone.utc)


def load_controls(state: MutableMapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = state.get(OPERATIONAL_CONTROLS_STATE_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def load_control(state: MutableMapping[str, Any], control_id: Optional[str]) -> Optional[OperationalControlRecord]:
    raw = load_controls(state).get(control_id or "")
    if not isinstance(raw, dict):
        return None
    try:
        return OperationalControlRecord.model_validate(raw)
    except Exception:
        return None


def find_control_by_check(state: MutableMapping[str, Any], check_id: str) -> Optional[OperationalControlRecord]:
    for control_id, raw in load_controls(state).items():
        if isinstance(raw, dict) and (raw.get("context") or {}).get("check_id") == check_id:
            return load_control(state, control_id)
    return None


def _save(state: MutableMapping[str, Any], record: OperationalControlRecord) -> None:
    controls = load_controls(state)
    controls[record.context.control_id] = record.model_dump(mode="json")
    state[OPERATIONAL_CONTROLS_STATE_KEY] = controls


def _audit(record: OperationalControlRecord, event: str, actor: str, now: Optional[datetime] = None, **details: Any) -> None:
    at = _now(now)
    record.audit.append(AuditEvent(at=at, event=event, actor=actor, stage=record.stage.value, details=details))
    record.updated_at = at
    _audit_logger.info(
        "operational_audit control_id=%s check_id=%s action=%s event=%s actor=%s stage=%s command_hash=%s source=%s target=%s details=%s",
        record.context.control_id,
        record.context.check_id,
        record.context.procedure_action_id,
        event,
        actor,
        record.stage.value,
        record.context.command_hash()[:16],
        record.context.source.canonical_source_id,
        record.context.target.canonical_identifier,
        details,
    )


def _sync_check(state: MutableMapping[str, Any], record: OperationalControlRecord) -> None:
    """Mirror the control stage onto the correlated DiagnosticCheckRecord (identity only), in
    whichever fault thread owns the check -- not necessarily the currently active thread."""
    from backend.agents.technical_authority_engineer.troubleshooting_threads import find_thread_for_check, save_thread

    if not record.context.check_id:
        return
    ts = find_thread_for_check(state, record.context.check_id)
    if ts is not None and ts.set_control_state(record.context.check_id, record.context.control_id, record.stage.value):
        save_thread(state, ts)


async def _bind_execution_to_progression(
    state: MutableMapping[str, Any], check_id: str, result: Any, context: OperationalActionContext, stage: str
) -> None:
    from backend.agents.technical_authority_engineer.progression_controller import ProgressionController
    from backend.agents.technical_authority_engineer.progression_repository import ProgressionConflict, ProgressionRepository
    from backend.agents.technical_authority_engineer.troubleshooting_threads import find_thread_for_check
    from backend.cases.troubleshooting_state import TroubleshootingState

    succeeded = result.status is ExecutionStatus.SUCCEEDED
    for _ in range(3):  # the binding is idempotent per (check, execution): reload + re-apply on conflict
        repository = ProgressionRepository(state, session_id=context.session_id)
        progression = await repository.load()
        fault_id = next((s.fault_id for s in progression.steps if s.check_id == check_id), None)
        if fault_id is None:
            projected = find_thread_for_check(state, check_id)
            if projected is None:
                return
            fault_id = projected.fault_id
        thread = TroubleshootingState(fault_id=fault_id, symptom_summary="")
        controller = ProgressionController(state, thread, session_id=context.session_id, activate=False, progression=progression)
        controller.record_controlled_execution(
            check_id, result.execution_id, result.observed_evidence if succeeded else None, succeeded, context.control_id, stage
        )
        try:
            await repository.save(progression)
            return
        except ProgressionConflict:
            continue
    raise OperationalDenial("progression_conflict", "The case investigation kept changing; the observation is recorded but not yet bound to its step.")


def _trace(event: dict[str, Any]) -> None:
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event(event)
    except Exception:  # observability never changes a decision
        pass


# ---------------------------------------------------------------------------------------------
# authority / source helpers
# ---------------------------------------------------------------------------------------------


def _candidate(context: OperationalActionContext, evidence: list[EvidenceReference]) -> dict[str, Any]:
    """The authority candidate for a stored action context. Re-derived server-side from the given
    (re-validated) governed evidence so it carries a fresh ProcedureAction resolver attestation;
    if it cannot be re-derived to exactly the stored command, a plain (legacy-grounding-only)
    candidate is returned, which can never use plain-line template grounding."""
    from backend.agents.technical_authority_engineer.procedure_actions import rederive_resolved_candidate

    resolved = rederive_resolved_candidate(context.procedure_action_id, context.parameters, evidence)
    if resolved is not None and resolved.command == context.command and resolved.source_id == context.source.canonical_source_id:
        return resolved.as_authority_candidate(context.description)
    return {"command": context.command, "source_id": context.source.canonical_source_id, "procedure_section": context.description, "restrictions": []}


def _authority(
    evidence: list[EvidenceReference], context: OperationalActionContext, target_confirmation_id: Optional[str] = None
) -> Optional[ApprovedCommand]:
    """The EXISTING Command Authority, unchanged. trusted_context.target_confirmed is True only
    when a server-owned TargetConfirmation id is supplied by the caller; the target gate's decision
    (server-owned, re-validated by the caller) is passed for state changes."""
    from backend.agents.technical_authority_engineer.agent_tool import build_server_validated_commands

    trusted_context = (
        {"target_confirmed": True, "target_confirmation_id": target_confirmation_id, "target_validation": context.target_validation}
        if target_confirmation_id
        else None
    )
    for approved in build_server_validated_commands(evidence, [_candidate(context, evidence)], trusted_context=trusted_context):
        if approved.command == context.command and approved.source_id == context.source.canonical_source_id:
            return approved
    return None


def _authority_preview_if_target_confirmed(evidence: list[EvidenceReference], context: OperationalActionContext) -> Optional[ApprovedCommand]:
    """Would the EXISTING Command Authority authorize this if the target were confirmed? Used only
    to decide whether to REQUEST a confirmation; its result never becomes authority."""
    from backend.agents.technical_authority_engineer.agent_tool import build_server_validated_commands
    from backend.tools.knowledge.diagnostic_trace import authority_preview

    with authority_preview():
        trusted_context = {"target_confirmed": True, "target_validation": context.target_validation}
        for approved in build_server_validated_commands(evidence, [_candidate(context, evidence)], trusted_context=trusted_context):
            if approved.command == context.command and approved.source_id == context.source.canonical_source_id:
                return approved
    return None


def _meta(ev: EvidenceReference) -> dict[str, Any]:
    return ev.metadata if isinstance(ev.metadata, dict) else {}


async def revalidate_target(context: OperationalActionContext, state: MutableMapping[str, Any]) -> Optional[str]:
    """For a state change: every validated target must STILL be a trusted target fact of the
    action's own fault in the authoritative progression (a reopen makes earlier results stale; a
    fact never transfers between faults). None when it holds, else a reason code. Reads pass."""
    if context.operation_type not in _STATE_CHANGING:
        return None
    validation = context.target_validation or {}
    targets = [t for t in validation.get("targets") or [] if isinstance(t, dict)]
    if not validation.get("passed") or not targets or not context.fault_id:
        return "TARGET_NOT_VALIDATED"
    from backend.agents.technical_authority_engineer.progression_repository import ProgressionRepository
    from backend.cases.target_facts import case_target_facts

    try:
        progression = await ProgressionRepository(state, session_id=context.session_id).load()
    except Exception:
        return "TARGET_FACTS_UNAVAILABLE"
    facts = case_target_facts(progression, context.fault_id)
    for target in targets:
        key, value = target.get("key"), target.get("value")
        matching = {
            f.fact_id for f in facts
            if (f.raw_identifier == value and (not key or f.target_key == key)) or (not key and value in (f.identity, f.mo_path))
        }
        if not matching or not matching & set(target.get("fact_ids") or []):
            return "TARGET_NO_LONGER_IN_CASE"
    if context.condition_scope:
        from backend.agents.technical_authority_engineer.procedure_actions import check_condition_scope
        from backend.cases.target_facts import trusted_case_results

        if not check_condition_scope(context.condition_scope, trusted_case_results(progression, context.fault_id))["passed"]:
            return "CONDITION_NO_LONGER_ESTABLISHED"
    return None


async def revalidate_source(
    context: OperationalActionContext, state: MutableMapping[str, Any], now: Optional[datetime] = None
) -> tuple[Optional[EvidenceReference], Optional[str]]:
    """Re-establish, from the governed repository at `now`, that the action's exact source is still
    the current APPROVED version, with byte-identical section text, and applicability MATCH for the
    session's confirmed facts. Returns (fresh evidence reference, None) or (None, reason code)."""
    from backend.agents.technical_authority_engineer.applicability_context import read_confirmed_applicability_facts
    from backend.knowledge.domain.applicability import ApplicabilityContext, ApplicabilityOutcome, evaluate_applicability
    from backend.knowledge.domain.enums import LifecycleStatus
    from backend.knowledge.governance.contracts import CurrentVersionResolutionStatus
    from backend.knowledge.governance.versioning import resolve_current_version
    from backend.tools.knowledge.runtime import get_knowledge_repository

    src = context.source
    try:
        versions = await get_knowledge_repository().list_versions(src.knowledge_id)
        resolution = resolve_current_version(versions, _now(now))
    except Exception:
        return None, "SOURCE_UNAVAILABLE"
    if resolution.status is not CurrentVersionResolutionStatus.RESOLVED or resolution.current is None:
        return None, "SOURCE_NOT_CURRENT"
    current = resolution.current
    if current.version.label != src.version_label:
        return None, "SOURCE_VERSION_CHANGED"
    if current.lifecycle_status is not LifecycleStatus.APPROVED:
        return None, "SOURCE_NOT_APPROVED"
    section = next((s for s in current.sections if s.section_id == src.section_id), None)
    if section is None:
        return None, "SOURCE_SECTION_MISSING"
    if text_hash(section.content) != src.content_hash:
        return None, "SOURCE_CONTENT_CHANGED"
    from backend.agents.technical_authority_engineer.procedure_actions import extract_procedure_actions
    from backend.agents.technical_authority_engineer.procedure_semantics import governed_document_scope

    scope = governed_document_scope(current, src.section_id)
    if context.operation_type in _STATE_CHANGING:
        # The governed conditions / condition scope the approval covers must be exactly what the
        # current approved version (all of its sections) still states.
        actions, _ = extract_procedure_actions(
            knowledge_id=src.knowledge_id, version_label=src.version_label, section_id=src.section_id,
            content=section.content, governed_scope=scope,
        )
        action = next((a for a in actions if a.action_id == context.procedure_action_id), None)
        if action is None or action.conditions != context.governed_conditions or action.condition_scope != context.condition_scope:
            return None, "SOURCE_CONDITIONS_CHANGED"
    facts = read_confirmed_applicability_facts(state)
    evaluation = evaluate_applicability(current.applicability, ApplicabilityContext(dimensions=facts))
    if evaluation.outcome is not ApplicabilityOutcome.MATCH:
        return None, "APPLICABILITY_NOT_MATCH"
    return (
        EvidenceReference(
            source_id=src.canonical_source_id,
            source_type="governed_knowledge",
            title=current.title,
            content_snippet=section.content,
            metadata={
                "knowledge_id": src.knowledge_id,
                "version_label": src.version_label,
                "section_id": src.section_id,
                "source_locator": section.source_locator,
                "heading": section.heading,
                "title": current.title,
                "source_id": current.source.source_id,
                "lifecycle_status": "approved",
                "applicability_outcome": "match",
                "unresolved_applicability_dimensions": [],
                "governed_scope": scope,
                "artifact_derived": bool(scope.get("artifact_derived")),
            },
        ),
        None,
    )


# ---------------------------------------------------------------------------------------------
# proposals (existing lifecycle; operational payload built only from trusted context)
# ---------------------------------------------------------------------------------------------


def _proposal_payload(context: OperationalActionContext, confirmation_id: Optional[str] = None) -> dict[str, Any]:
    return {
        "control_id": context.control_id,
        "binding_hash": context.binding_hash(),
        "binding": context.binding(),
        "target_confirmation_id": confirmation_id,
    }


def _create_operational_proposal(
    state: MutableMapping[str, Any],
    operation: OperationalOperation,
    context: OperationalActionContext,
    confirmation_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> ActionProposal:
    created = _now(now)
    payload = _proposal_payload(context, confirmation_id)
    verb = "Confirm the target for" if operation is OperationalOperation.CONFIRM_TARGET else "Approve"
    proposal = ActionProposal(
        proposal_id=str(uuid.uuid4()),
        operation=operation,
        payload=payload,
        payload_hash=compute_payload_hash(operation.value, payload),
        created_at=created,
        expires_at=created + timedelta(seconds=get_settings().action_proposal_expiry_seconds),
        status=ProposalStatus.PENDING,
        summary=f"{verb} state-changing action: {context.command}",
        target_display_name=context.target.display_value,
    )
    state[PENDING_ACTION_PROPOSAL_STATE_KEY] = proposal.model_dump(mode="json")
    return proposal


def _proposal_matches(proposal: ActionProposal, context: OperationalActionContext) -> bool:
    return (
        isinstance(proposal.payload, dict)
        and proposal.payload.get("control_id") == context.control_id
        and proposal.payload.get("binding_hash") == context.binding_hash()
        and proposal.payload_hash == compute_payload_hash(proposal.operation.value, proposal.payload)
    )


# ---------------------------------------------------------------------------------------------
# in-turn planning (called by the TAE tool after Command Authority)
# ---------------------------------------------------------------------------------------------


def build_action_context(
    resolution: Any,
    evidence: list[EvidenceReference],
    *,
    check_id: Optional[str],
    run_id: Optional[str],
    session_id: Optional[str],
    case_id: Optional[str],
    reason: str = "",
    fault_id: Optional[str] = None,
) -> Optional[tuple[OperationalActionContext, EvidenceReference]]:
    candidate = getattr(resolution, "candidate", None)
    action = getattr(resolution, "action", None)
    if candidate is None or action is None:
        return None
    source_ev = next((ev for ev in evidence if ev.source_type == "governed_knowledge" and ev.source_id == candidate.source_id), None)
    if source_ev is None:
        return None
    meta = _meta(source_ev)
    validation = getattr(resolution, "target_validation", None)
    target_validation = validation.authority_view() if validation is not None else None
    if action.operation_type.value in _STATE_CHANGING and not (target_validation and target_validation.get("passed")):
        return None  # a state change without a validated current-case target never reaches confirmation
    context = OperationalActionContext(
        control_id=f"ctl-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        session_id=session_id,
        case_id=case_id,
        check_id=check_id,
        procedure_action_id=action.action_id,
        intent=action.intent,
        action_type=action.action_type.value,
        operation_type=action.operation_type.value,
        command_template=action.command_template,
        command=candidate.command,
        parameters=dict(candidate.bound_parameters),
        target=validated_target_identity(target_validation) or target_identity_for(dict(candidate.bound_parameters), candidate.command),
        source=ActionSourceIdentity(
            canonical_source_id=candidate.source_id,
            knowledge_id=str(meta.get("knowledge_id") or ""),
            version_label=str(meta.get("version_label") or ""),
            section_id=str(meta.get("section_id") or ""),
            source_locator=meta.get("source_locator"),
            title=meta.get("title") or source_ev.title,
            heading=meta.get("heading"),
            content_hash=text_hash(source_ev.content_snippet or ""),
        ),
        description=action.description,
        reason=reason,
        restrictions=list(action.restrictions),
        fault_id=fault_id or (target_validation or {}).get("fault_id"),
        target_validation=target_validation,
        governed_conditions=[dict(c) for c in action.conditions],
        condition_scope=dict(action.condition_scope) if action.condition_scope else None,
    )
    return context, source_ev


_BLOCKER_TEXT = {
    ControlStage.AWAITING_CONFIRMATION: (
        "This is a state-changing action. It requires explicit target confirmation and human approval on the "
        "action card before it can be approved for execution; SLOPANOC does not execute state-changing actions."
    ),
    "read_execution": "This diagnostic check can be run through the configured read-only execution adapter.",
    "advisory": "No execution adapter is configured for this diagnostic check; run it yourself and report the output.",
}


def plan_operational_control(
    resolution: Any,
    authorized_catalog: list[ApprovedCommand],
    evidence: list[EvidenceReference],
    *,
    state: MutableMapping[str, Any],
    check_id: Optional[str],
    run_id: Optional[str],
    session_id: Optional[str],
    case_id: Optional[str],
    reason: str = "",
    registry: Optional[ExecutionAdapterRegistry] = None,
    now: Optional[datetime] = None,
    fault_id: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """After Command Authority, in the TAE turn. Returns a presentation summary or None when no
    operational control applies (no resolved candidate, or authority rejected for a reason other
    than missing target confirmation)."""
    built = build_action_context(
        resolution, evidence, check_id=check_id, run_id=run_id, session_id=session_id, case_id=case_id, reason=reason, fault_id=fault_id
    )
    if built is None:
        return None
    context, source_ev = built
    meta = _meta(source_ev)
    lifecycle = str(meta.get("lifecycle_status", "")).lower()
    applicability = str(meta.get("applicability_outcome", "")).lower()
    registry = registry or get_execution_adapter_registry()
    execution_context = get_settings().diagnostic_execution_context
    at = _now(now)

    authorized = next((c for c in authorized_catalog if c.command == context.command and c.source_id == context.source.canonical_source_id), None)
    if authorized is not None:
        attestation = attest_authority(
            authorized, context, authority_status=AuthorityStatus.AUTHORIZED,
            source_selected=True, source_lifecycle=lifecycle, source_applicability=applicability,
        )
        decision = evaluate_action_policy(attestation, adapter_available=registry.is_available(execution_context))
        if decision.decision is not PolicyDecisionKind.ALLOWED:
            return None  # a state change cannot be authorized in-turn (no confirmation exists yet)
        stage = ControlStage.READY_FOR_EXECUTION if decision.permits_read_execution else ControlStage.AUTHORIZED
    elif context.operation_type in _STATE_CHANGING:
        preview = _authority_preview_if_target_confirmed(evidence, context)
        if preview is None:
            return None
        attestation = attest_authority(
            preview, context, authority_status=AuthorityStatus.PENDING_TARGET_CONFIRMATION,
            source_selected=True, source_lifecycle=lifecycle, source_applicability=applicability,
        )
        decision = evaluate_action_policy(attestation, target_confirmed=False)
        if decision.decision is not PolicyDecisionKind.CLARIFICATION_REQUIRED:
            return None
        stage = ControlStage.AWAITING_CONFIRMATION
    else:
        return None

    record = OperationalControlRecord(
        context=context,
        stage=stage,
        execution_mode=decision.execution_mode.value,
        policy=decision.model_dump(mode="json"),
        execution_context=execution_context if decision.permits_read_execution else None,
        created_at=at,
        updated_at=at,
    )
    _audit(record, "policy_evaluated", "system", at, decision=decision.decision.value, reason_codes=[c.value for c in decision.reason_codes])
    if stage is ControlStage.AWAITING_CONFIRMATION:
        proposal = _create_operational_proposal(state, OperationalOperation.CONFIRM_TARGET, context, now=at)
        record.confirmation_request_id = proposal.proposal_id
        _audit(record, "target_confirmation_requested", "system", at, proposal_id=proposal.proposal_id)
    _save(state, record)
    summary = control_summary(record)
    _trace({**summary, "stage": "plan"})
    return summary


def control_summary(record: OperationalControlRecord) -> dict[str, Any]:
    policy = record.policy or {}
    blocker_key: Any = record.stage if record.stage is ControlStage.AWAITING_CONFIRMATION else record.execution_mode
    return {
        "control_id": record.context.control_id,
        "check_id": record.context.check_id,
        "procedure_action_id": record.context.procedure_action_id,
        "command": record.context.command,
        "operation_type": record.context.operation_type,
        "target": record.context.target.model_dump(mode="json"),
        "control_stage": record.stage.value,
        "policy_decision": policy.get("decision"),
        "reason_codes": policy.get("reason_codes", []),
        "execution_mode": record.execution_mode,
        "execution_context": record.execution_context,
        "target_confirmation_required": record.stage is ControlStage.AWAITING_CONFIRMATION,
        "confirmation_id": record.confirmation_id,
        "approval_id": record.approval_id,
        "approval_status": record.approval_status.value if record.approval_status else None,
        "invalidation_reason": record.invalidation_reason,
        "blocker": _BLOCKER_TEXT.get(blocker_key),
    }


def invalidate_control(state: MutableMapping[str, Any], control_id: str, reason: str, actor: str = "system") -> None:
    record = load_control(state, control_id)
    if record is None:
        return
    record.stage = ControlStage.INVALIDATED
    record.invalidation_reason = reason
    if record.approval_status in (OperationalApprovalStatus.PENDING, OperationalApprovalStatus.APPROVED):
        record.approval_status = OperationalApprovalStatus.INVALIDATED
    if record.confirmation_id:
        invalidate_target_confirmation(state, record.confirmation_id, reason)
    proposal = load_active_proposal(state)
    if proposal is not None and isinstance(proposal.payload, dict) and proposal.payload.get("control_id") == control_id:
        reject_proposal(proposal.proposal_id, state)
    _audit(record, "invalidated", actor, reason=reason)
    _save(state, record)
    _sync_check(state, record)


# ---------------------------------------------------------------------------------------------
# trusted endpoint transitions (called only from backend/api/approval_service.py)
# ---------------------------------------------------------------------------------------------


def is_operational_proposal(proposal: Optional[ActionProposal]) -> bool:
    return proposal is not None and isinstance(proposal.operation, OperationalOperation)


def _deny(state: MutableMapping[str, Any], record: Optional[OperationalControlRecord], reason: str, message: str, actor: str) -> OperationalDenial:
    if record is not None:
        invalidate_control(state, record.context.control_id, reason, actor)
    return OperationalDenial(reason, message)


async def approve_operational(
    state: MutableMapping[str, Any], proposal_id: str, *, user_id: str, now: Optional[datetime] = None
) -> OperationalControlRecord:
    """Operator approval of an operational proposal. Re-validates EVERYTHING before any transition."""
    proposal = load_active_proposal(state)
    if proposal is None or proposal.proposal_id != proposal_id or not is_operational_proposal(proposal):
        raise OperationalDenial("proposal_id_mismatch", "This action card is no longer the active one for this session.")
    status = effective_status(proposal, now)
    if status is not ProposalStatus.PENDING:
        raise OperationalDenial(f"proposal_{status.value}", "This action card can no longer be approved.")
    record = load_control(state, proposal.payload.get("control_id") if isinstance(proposal.payload, dict) else None)
    if record is None or record.stage in (ControlStage.INVALIDATED, ControlStage.REJECTED):
        raise _deny(state, record, "action_invalidated", "This action is no longer valid.", user_id)
    context = record.context
    if not _proposal_matches(proposal, context):
        raise _deny(state, record, "binding_changed", "The action changed after this card was created; it is no longer valid.", user_id)

    evidence, source_problem = await revalidate_source(context, state, now)
    if evidence is None:
        raise _deny(state, record, source_problem or "source_invalid", "The governed procedure behind this action changed or is no longer applicable.", user_id)
    target_problem = await revalidate_target(context, state)
    if target_problem is not None:
        raise _deny(state, record, target_problem, "The action's target is no longer established by this investigation's trusted evidence.", user_id)

    at = _now(now)
    if proposal.operation is OperationalOperation.CONFIRM_TARGET:
        if record.stage is not ControlStage.AWAITING_CONFIRMATION:
            raise OperationalDenial("unexpected_stage", "This action is not awaiting target confirmation.")
        if not approve_proposal(proposal_id, state, now).success:
            raise OperationalDenial("approval_failed", "The target confirmation could not be recorded.")
        confirmation = create_target_confirmation(state, context, confirmed_by=user_id, now=at)
        consume_proposal(proposal_id, state, now)
        record.confirmation_id = confirmation.confirmation_id
        _audit(record, "target_confirmed", user_id, at, confirmation_id=confirmation.confirmation_id, target=context.target.canonical_identifier)

        authorized = _authority([evidence], context, target_confirmation_id=confirmation.confirmation_id)
        if authorized is None:
            raise _deny(state, record, "COMMAND_NOT_AUTHORIZED", "Command Authority did not authorize this action for the confirmed target.", "system")
        attestation = attest_authority(
            authorized, context, authority_status=AuthorityStatus.AUTHORIZED, source_selected=True,
            source_lifecycle="approved", source_applicability="match", target_confirmation_id=confirmation.confirmation_id,
        )
        decision = evaluate_action_policy(attestation, target_confirmed=confirmation_mismatch(confirmation, context, at) is None)
        record.policy = decision.model_dump(mode="json")
        record.execution_mode = decision.execution_mode.value
        if decision.decision is not PolicyDecisionKind.APPROVAL_REQUIRED:
            raise _deny(state, record, "policy_" + decision.decision.value, "Policy does not allow this action to be requested for approval.", "system")
        approval = _create_operational_proposal(state, OperationalOperation.PROCEDURE_ACTION, context, confirmation.confirmation_id, now=at)
        record.approval_id = approval.proposal_id
        record.approval_status = OperationalApprovalStatus.PENDING
        record.stage = ControlStage.AWAITING_APPROVAL
        _audit(record, "approval_requested", "system", at, approval_id=approval.proposal_id, reason_codes=[c.value for c in decision.reason_codes])
        _save(state, record)
        _sync_check(state, record)
        return record

    # PROCEDURE_ACTION approval
    if record.stage is not ControlStage.AWAITING_APPROVAL or record.approval_id != proposal_id:
        raise OperationalDenial("unexpected_stage", "This action is not awaiting approval.")
    confirmation = load_target_confirmation(state, record.confirmation_id)
    mismatch = confirmation_mismatch(confirmation, context, now)
    if mismatch is not None or proposal.payload.get("target_confirmation_id") != record.confirmation_id:
        raise _deny(state, record, mismatch or "TARGET_CONFIRMATION_OTHER_ACTION", "The target confirmation no longer matches this action.", user_id)
    authorized = _authority([evidence], context, target_confirmation_id=record.confirmation_id)
    if authorized is None:
        raise _deny(state, record, "COMMAND_NOT_AUTHORIZED", "Command Authority no longer authorizes this action.", "system")
    attestation = attest_authority(
        authorized, context, authority_status=AuthorityStatus.AUTHORIZED, source_selected=True,
        source_lifecycle="approved", source_applicability="match", target_confirmation_id=record.confirmation_id,
    )
    decision = evaluate_action_policy(attestation, target_confirmed=True)
    if decision.decision is not PolicyDecisionKind.APPROVAL_REQUIRED:
        raise _deny(state, record, "policy_" + decision.decision.value, "Policy no longer allows this action.", "system")
    if not approve_proposal(proposal_id, state, now).success:
        raise OperationalDenial("approval_failed", "The approval could not be recorded.")
    record.approval_status = OperationalApprovalStatus.APPROVED
    record.approved_by = user_id
    record.approved_at = at
    record.stage = ControlStage.READY_FOR_EXECUTION
    _audit(record, "approved", user_id, at, approval_id=proposal_id, confirmation_id=record.confirmation_id)
    _audit(record, "execution_not_started", "system", at, reason="STATE_CHANGE_EXECUTION_NOT_ENABLED")
    _save(state, record)
    _sync_check(state, record)
    return record


def reject_operational(state: MutableMapping[str, Any], proposal_id: str, *, user_id: str, now: Optional[datetime] = None) -> OperationalControlRecord:
    proposal = load_active_proposal(state)
    if proposal is None or proposal.proposal_id != proposal_id or not is_operational_proposal(proposal):
        raise OperationalDenial("proposal_id_mismatch", "This action card is no longer the active one for this session.")
    result = reject_proposal(proposal_id, state, now)
    if not result.success:
        raise OperationalDenial(result.reason.value if result.reason else "reject_failed", "This action card can no longer be rejected.")
    record = load_control(state, proposal.payload.get("control_id") if isinstance(proposal.payload, dict) else None)
    if record is None:
        raise OperationalDenial("action_invalidated", "This action is no longer valid.")
    record.stage = ControlStage.REJECTED
    record.rejected_by = user_id
    if proposal.operation is OperationalOperation.PROCEDURE_ACTION:
        record.approval_status = OperationalApprovalStatus.REJECTED
    _audit(record, "rejected", user_id, now, proposal_id=proposal_id, operation=proposal.operation.value)
    _save(state, record)
    _sync_check(state, record)
    return record


async def evaluate_execution_eligibility(
    state: MutableMapping[str, Any], control_id: str, now: Optional[datetime] = None
) -> EligibilityResult:
    """READY_FOR_EXECUTION only if every layer still holds for the EXACT approved context.
    Eligibility never executes anything."""
    record = load_control(state, control_id)
    if record is None:
        return EligibilityResult(eligible=False, stage="unknown", reason_codes=["CONTROL_NOT_FOUND"])
    context = record.context
    codes: list[str] = []
    if record.stage is not ControlStage.READY_FOR_EXECUTION:
        codes.append(f"STAGE_{record.stage.value.upper()}")
    if context.operation_type in _STATE_CHANGING:
        proposal = load_active_proposal(state)
        if proposal is None or proposal.proposal_id != record.approval_id:
            codes.append("APPROVAL_NOT_ACTIVE")
        else:
            status = effective_status(proposal, now)
            if status is not ProposalStatus.APPROVED:
                codes.append(f"APPROVAL_{status.value.upper()}")
            if not _proposal_matches(proposal, context):
                codes.append("APPROVAL_BINDING_CHANGED")
        mismatch = confirmation_mismatch(load_target_confirmation(state, record.confirmation_id), context, now)
        if mismatch:
            codes.append(mismatch)
    evidence, source_problem = await revalidate_source(context, state, now)
    target_problem = await revalidate_target(context, state)
    if target_problem is not None:
        codes.append(target_problem)
    if evidence is None:
        codes.append(source_problem or "SOURCE_INVALID")
    elif _authority([evidence], context, target_confirmation_id=record.confirmation_id if context.operation_type in _STATE_CHANGING else None) is None:
        codes.append("COMMAND_NOT_AUTHORIZED")
    return EligibilityResult(eligible=not codes, stage=record.stage.value, reason_codes=codes)


# ---------------------------------------------------------------------------------------------
# controlled diagnostic-read execution (operator-requested, one check at a time)
# ---------------------------------------------------------------------------------------------


async def execute_read_check(
    state: MutableMapping[str, Any],
    check_id: str,
    *,
    user_id: str,
    registry: Optional[ExecutionAdapterRegistry] = None,
    now: Optional[datetime] = None,
) -> ExecutionResult:
    record = find_control_by_check(state, check_id)
    if record is None:
        raise OperationalDenial("control_not_found", "No authorized diagnostic action exists for this check.")
    context = record.context
    if context.operation_type != CommandOperationType.READ_ONLY_DIAGNOSTIC.value:
        raise OperationalDenial("state_change_execution_not_enabled", "State-changing actions are never executed by SLOPANOC.")
    if record.stage is not ControlStage.READY_FOR_EXECUTION:
        raise OperationalDenial(f"stage_{record.stage.value}", "This diagnostic check is not ready for execution.")
    registry = registry or get_execution_adapter_registry()
    execution_context = get_settings().diagnostic_execution_context
    if not execution_context or execution_context != record.execution_context or not registry.is_available(execution_context):
        raise OperationalDenial("EXECUTION_UNAVAILABLE", "No read-only execution adapter is available for this check.")

    evidence, source_problem = await revalidate_source(context, state, now)
    if evidence is None:
        raise _deny(state, record, source_problem or "source_invalid", "The governed procedure behind this check changed or is no longer applicable.", "system")
    authorized = _authority([evidence], context)
    if authorized is None:
        raise _deny(state, record, "COMMAND_NOT_AUTHORIZED", "Command Authority no longer authorizes this check.", "system")
    attestation = attest_authority(
        authorized, context, authority_status=AuthorityStatus.AUTHORIZED, source_selected=True,
        source_lifecycle="approved", source_applicability="match",
    )
    decision = evaluate_action_policy(attestation, adapter_available=True)
    if not decision.permits_read_execution:
        raise OperationalDenial("policy_" + decision.decision.value, "Policy does not permit executing this check.")
    action = issue_authorized_read_action(context, attestation, decision, execution_context)

    record.stage = ControlStage.EXECUTING
    _audit(record, "execution_started", user_id, now, execution_context=execution_context)
    result = await registry.execute(action, ExecutionContext(execution_context=execution_context, session_id=context.session_id, requested_by=user_id, check_id=check_id))
    succeeded = result.status is ExecutionStatus.SUCCEEDED
    record.stage = ControlStage.EXECUTED if succeeded else ControlStage.FAILED
    record.executions.append(result.model_dump(mode="json", exclude={"stdout", "stderr"}) | {"output_hash": text_hash(result.stdout or "")})
    _audit(record, "execution_completed", "system", None, execution_id=result.execution_id, status=result.status.value, adapter=result.adapter_type)

    if succeeded:
        record.stage = ControlStage.COMPLETED
        _audit(record, "observation_recorded", "system", None, check_id=check_id, execution_id=result.execution_id)
    _save(state, record)
    # Progression-first: the authoritative progression validates the controlled observation against
    # the execution just recorded on the control record and binds it to its exact step; the
    # fault-thread state is re-derived from it (never written directly).
    await _bind_execution_to_progression(state, check_id, result, context, record.stage.value)
    _trace({"stage": "execution", "control_id": context.control_id, "check_id": check_id, "execution_id": result.execution_id,
            "status": result.status.value, "adapter": result.adapter_type, "execution_context": execution_context})
    return result
