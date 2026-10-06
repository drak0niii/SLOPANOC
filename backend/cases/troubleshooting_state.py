"""Persistent Troubleshooting State domain model for SLOPANOC.

Persists the continuous technical diagnostic lifecycle across turns and sessions,
tracking:
- Active fault identity and observed symptoms
- Current working hypothesis and competing hypotheses
- Verified evidence references and chronological check history
- Applicable governed operational procedures
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class TroubleshootingStatus(str, Enum):
    INVESTIGATING = "investigating"
    HYPOTHESIS_FORMULATED = "hypothesis_formulated"
    TESTING_NEXT_STEP = "testing_next_step"
    MITIGATION_RECOMMENDED = "mitigation_recommended"
    ESCALATION_REQUIRED = "escalation_required"
    RESOLVED = "resolved"


class CheckLifecycleStatus(str, Enum):
    RECOMMENDED = "recommended"
    EXECUTED = "executed"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class DiagnosticCheckRecord(BaseModel):
    check_id: str
    action: str
    rationale: str
    grounded_command: Optional[str] = None
    command_source_id: Optional[str] = None
    procedure_action_id: Optional[str] = None
    """Server-issued governed ProcedureAction this check was resolved from (identity only; the
    action is re-derived from governed evidence whenever it is used -- this is not authority)."""
    control_id: Optional[str] = None
    """Operational control record (backend/operations) correlated with this check, if any."""
    control_stage: Optional[str] = None
    """Mirror of the operational control stage: authorized / awaiting_confirmation /
    awaiting_approval / ready_for_execution / executing / executed / completed / failed /
    rejected / invalidated. Distinct from `status` (the operator-facing check lifecycle)."""
    execution_ids: list[str] = Field(default_factory=list)
    """Controlled read executions whose observed output was recorded on this check."""
    expected_observation: str
    observed_result: Optional[str] = None
    observed_result_source: Optional[str] = None
    """Provenance of `observed_result`: 'execution_adapter' (controlled read output) or None
    (a caller/model-written summary -- never used as parameter evidence)."""
    operator_observation: Optional[str] = None
    """The operator's own verbatim message reporting this check's result (bounded). Operator-provided
    observed evidence: may supply parameter VALUES; never governed command authority."""
    status: CheckLifecycleStatus = Field(default=CheckLifecycleStatus.RECOMMENDED)
    node_id: Optional[str] = None
    fault_id: Optional[str] = None
    session_id: Optional[str] = None
    case_id: Optional[str] = None
    recommended_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    executed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class TroubleshootingState(BaseModel):
    fault_id: str = Field(description="Unique identifier for the active fault/incident under investigation")
    status: TroubleshootingStatus = Field(default=TroubleshootingStatus.INVESTIGATING)
    symptom_summary: str = Field(description="Summary of initial observed symptoms or alarm triggers")
    subject_component: Optional[str] = Field(
        default=None,
        description="Component/subject this fault thread is about, as the operator stated it (a session may hold several threads).",
    )
    session_id: Optional[str] = Field(default=None, description="Active session ID associated with this troubleshooting lifecycle")
    case_id: Optional[str] = Field(default=None, description="Active case ID if associated with a formal case")
    node_id: Optional[str] = Field(default=None, description="Identifier of the node/site under investigation")
    working_hypothesis: Optional[str] = Field(default=None, description="Primary isolated hypothesis being evaluated")
    competing_hypotheses: list[str] = Field(default_factory=list, description="Alternative hypotheses not yet eliminated")
    verified_evidence_ids: list[str] = Field(default_factory=list, description="IDs of confirmed evidence items")
    diagnostic_history: list[DiagnosticCheckRecord] = Field(default_factory=list, description="History of checks performed")
    applicable_procedure_ids: list[str] = Field(default_factory=list, description="Applicable governed MOP/SOP IDs")
    evidence_requirements: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Read projection of this fault's evidence requirements (kind, description, status, blocking "
        "reason, selected acquisition type). The progression's EvidenceRequirement is the only authority.",
    )
    pending_clarification: Optional[dict[str, Any]] = Field(
        default=None,
        description="Read projection of this fault's OPEN clarification (question_id, reason, requested/unresolved "
        "fields, resolved values). The progression's OpenQuestion is the only authority.",
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def record_recommended_check(
        self,
        action: str,
        rationale: str,
        expected_observation: str,
        grounded_command: Optional[str] = None,
        command_source_id: Optional[str] = None,
        check_id: Optional[str] = None,
        node_id: Optional[str] = None,
        fault_id: Optional[str] = None,
        session_id: Optional[str] = None,
        case_id: Optional[str] = None,
        procedure_action_id: Optional[str] = None,
        control_id: Optional[str] = None,
        control_stage: Optional[str] = None,
    ) -> DiagnosticCheckRecord:
        """Records a newly proposed/recommended diagnostic check.
        Never marks it as completed -- always starts as RECOMMENDED.
        """
        import uuid
        now = datetime.now(timezone.utc)
        record = DiagnosticCheckRecord(
            check_id=check_id or f"chk-{uuid.uuid4().hex[:8]}",
            action=action,
            rationale=rationale,
            expected_observation=expected_observation,
            grounded_command=grounded_command,
            command_source_id=command_source_id,
            procedure_action_id=procedure_action_id,
            control_id=control_id,
            control_stage=control_stage,
            status=CheckLifecycleStatus.RECOMMENDED,
            node_id=node_id or self.node_id,
            fault_id=fault_id or self.fault_id,
            session_id=session_id or self.session_id,
            case_id=case_id or self.case_id,
            recommended_at=now,
            executed_at=None,
            completed_at=None,
        )
        self.diagnostic_history.append(record)
        self.status = TroubleshootingStatus.TESTING_NEXT_STEP
        self.updated_at = now
        return record

    def record_user_execution(
        self,
        check_id: Optional[str] = None,
        command: Optional[str] = None,
        observed_result: Optional[str] = None,
        operator_observation: Optional[str] = None,
    ) -> Optional[DiagnosticCheckRecord]:
        """Transitions a recommended check to EXECUTED upon user reporting execution.
        Observed result is recorded, but the check is NEVER automatically marked COMPLETED;
        Safeguard 5 mandates preserving RECOMMENDED, EXECUTED, and COMPLETED as distinct states.
        """
        now = datetime.now(timezone.utc)
        matched: Optional[DiagnosticCheckRecord] = None

        if check_id:
            for rec in reversed(self.diagnostic_history):
                if rec.check_id == check_id:
                    matched = rec
                    break

        if not matched and command:
            cmd_clean = command.strip().lower()
            for rec in reversed(self.diagnostic_history):
                if rec.grounded_command and rec.grounded_command.strip().lower() == cmd_clean:
                    matched = rec
                    break

        if not matched:
            # Fall back to latest RECOMMENDED check
            for rec in reversed(self.diagnostic_history):
                if rec.status == CheckLifecycleStatus.RECOMMENDED:
                    matched = rec
                    break

        if matched:
            if not matched.executed_at:
                matched.executed_at = now
            matched.status = CheckLifecycleStatus.EXECUTED
            if observed_result:
                matched.observed_result = observed_result
            if operator_observation:
                matched.operator_observation = operator_observation
            self.updated_at = now

        return matched

    def set_control_state(self, check_id: str, control_id: str, control_stage: str) -> bool:
        for rec in self.diagnostic_history:
            if rec.check_id == check_id:
                rec.control_id = control_id
                rec.control_stage = control_stage
                self.updated_at = datetime.now(timezone.utc)
                return True
        return False

    def record_adapter_execution(
        self,
        check_id: str,
        execution_id: str,
        observed_result: Optional[str],
        succeeded: bool,
        control_id: Optional[str] = None,
        control_stage: Optional[str] = None,
    ) -> Optional[DiagnosticCheckRecord]:
        """Correlates a controlled read execution with its check. A successful execution whose
        output was captured is EXECUTED then COMPLETED (the observation exists); a failed one is
        recorded but leaves the check awaiting a result. The output is observed evidence only."""
        now = datetime.now(timezone.utc)
        for rec in self.diagnostic_history:
            if rec.check_id != check_id:
                continue
            if execution_id not in rec.execution_ids:
                rec.execution_ids.append(execution_id)
            if control_id:
                rec.control_id = control_id
            if control_stage:
                rec.control_stage = control_stage
            if succeeded:
                rec.status = CheckLifecycleStatus.EXECUTED
                rec.executed_at = rec.executed_at or now
                rec.observed_result = observed_result
                rec.observed_result_source = "execution_adapter"
                rec.status = CheckLifecycleStatus.COMPLETED
                rec.completed_at = now
            self.updated_at = now
            return rec
        return None

    def mark_check_skipped(self, check_id: str, reason: Optional[str] = None) -> Optional[DiagnosticCheckRecord]:
        """The operator reported that this check cannot be performed (recorded, not executed)."""
        for rec in self.diagnostic_history:
            if rec.check_id == check_id:
                rec.status = CheckLifecycleStatus.SKIPPED
                if reason:
                    rec.observed_result = f"Not performed: {reason}"
                self.updated_at = datetime.now(timezone.utc)
                return rec
        return None

    def update_check(self, check_id: str, **fields: Any) -> Optional[DiagnosticCheckRecord]:
        """Refresh identity/command fields of an existing (still pending) check; never its lifecycle."""
        allowed = {"grounded_command", "command_source_id", "procedure_action_id", "control_id", "control_stage", "action", "expected_observation"}
        for rec in self.diagnostic_history:
            if rec.check_id == check_id:
                for name, value in fields.items():
                    if name in allowed and value is not None:
                        setattr(rec, name, value)
                self.updated_at = datetime.now(timezone.utc)
                return rec
        return None

    def trusted_observation_texts(self) -> list[str]:
        """Operator-provided or controlled-execution observed output recorded in THIS thread, newest
        first. Parameter evidence only -- never a source of governed command authority."""
        texts: list[str] = []
        for rec in reversed(self.diagnostic_history):
            if rec.operator_observation:
                texts.append(rec.operator_observation)
            if rec.observed_result and rec.observed_result_source == "execution_adapter":
                texts.append(rec.observed_result)
        return texts

    def get_prior_steps_summary(self) -> list[str]:
        """Produces a deterministic summary of prior checks for specialist context."""
        summaries: list[str] = []
        for rec in self.diagnostic_history:
            cmd_part = f", command: '{rec.grounded_command}'" if rec.grounded_command else ""
            res_part = f", Observed: {rec.observed_result}" if rec.observed_result else ""
            summaries.append(
                f"Check '{rec.action}' [{rec.status.value}]{cmd_part}{res_part}"
            )
        return summaries
