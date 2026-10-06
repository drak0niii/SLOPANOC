"""Deterministic Action Policy -- runs strictly AFTER the existing Command Authority.

Policy only RESTRICTS. Its sole input about command authority is an `AuthorityAttestation`,
which is minted by `attest_authority` exclusively from an `ApprovedCommand` that
`build_server_validated_commands` actually returned, and is HMAC-signed with a process-scoped
key. A hand-built `AuthorizedCommand`, a raw command string, or an edited/forged attestation
fails verification and evaluates to PROHIBITED / COMMAND_NOT_AUTHORIZED -- policy has no path to
grant command authority.

Policies are keyed by the existing `CommandOperationType` classification (no string matching
here) and are operational governance, kept separate from knowledge-derived ProcedureActions.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable, Optional

from pydantic import BaseModel, Field

from backend.agents.technical_authority_engineer.schemas import ApprovedCommand, CommandOperationType
from backend.operations.context import OperationalActionContext
from backend.operations.signing import sign, verify

_ATTESTATION_KIND = "authority_attestation.v1"


class ExecutionMode(str, Enum):
    ADVISORY = "advisory"
    READ_EXECUTION = "read_execution"
    APPROVAL_REQUIRED = "approval_required"
    PROHIBITED = "prohibited"


class PolicyDecisionKind(str, Enum):
    ALLOWED = "allowed"
    APPROVAL_REQUIRED = "approval_required"
    CLARIFICATION_REQUIRED = "clarification_required"
    PROHIBITED = "prohibited"


class PolicyReasonCode(str, Enum):
    COMMAND_NOT_AUTHORIZED = "COMMAND_NOT_AUTHORIZED"
    SOURCE_NOT_SELECTED = "SOURCE_NOT_SELECTED"
    SOURCE_NOT_APPROVED = "SOURCE_NOT_APPROVED"
    APPLICABILITY_NOT_MATCH = "APPLICABILITY_NOT_MATCH"
    UNCLASSIFIED_OPERATION = "UNCLASSIFIED_OPERATION"
    POLICY_PROHIBITED = "POLICY_PROHIBITED"
    MISSING_PREREQUISITE = "MISSING_PREREQUISITE"
    READ_ONLY_DIAGNOSTIC = "READ_ONLY_DIAGNOSTIC"
    READ_EXECUTION_PERMITTED = "READ_EXECUTION_PERMITTED"
    EXECUTION_ADAPTER_UNAVAILABLE = "EXECUTION_ADAPTER_UNAVAILABLE"
    STATE_CHANGE = "STATE_CHANGE"
    TARGET_NOT_CONFIRMED = "TARGET_NOT_CONFIRMED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


class AuthorityStatus(str, Enum):
    AUTHORIZED = "authorized"
    PENDING_TARGET_CONFIRMATION = "pending_target_confirmation"
    """Grounded, classified and placeholder-free; the ONLY remaining Command Authority gate is
    trusted target confirmation. Never executable, never approvable."""


class AuthorityAttestation(BaseModel):
    command: str
    source_id: str
    operation_type: str
    procedure_action_id: str
    binding_hash: str
    authority_status: AuthorityStatus
    source_selected: bool
    source_lifecycle: str
    source_applicability: str
    target_confirmation_id: Optional[str] = None
    signature: str

    def _fields(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude={"signature"})

    def is_genuine(self) -> bool:
        return verify(_ATTESTATION_KIND, self._fields(), self.signature)


def attest_authority(
    authorized: ApprovedCommand,
    context: OperationalActionContext,
    *,
    authority_status: AuthorityStatus,
    source_selected: bool,
    source_lifecycle: str,
    source_applicability: str,
    target_confirmation_id: Optional[str] = None,
) -> AuthorityAttestation:
    """Mint an attestation for an ApprovedCommand that the existing Command Authority returned
    (or, for PENDING_TARGET_CONFIRMATION, returned only under a hypothetical confirmed target).
    The command/source must equal the action context's resolved candidate."""
    if not isinstance(authorized, ApprovedCommand):
        raise TypeError("only a Command Authority result can be attested")
    if authorized.command != context.command or authorized.source_id != context.source.canonical_source_id:
        raise ValueError("authority result does not match the resolved action context")
    fields = {
        "command": authorized.command,
        "source_id": authorized.source_id,
        "operation_type": getattr(authorized.operation_type, "value", str(authorized.operation_type)),
        "procedure_action_id": context.procedure_action_id,
        "binding_hash": context.binding_hash(),
        "authority_status": authority_status.value,
        "source_selected": bool(source_selected),
        "source_lifecycle": source_lifecycle,
        "source_applicability": source_applicability,
        "target_confirmation_id": target_confirmation_id,
    }
    return AuthorityAttestation(**fields, signature=sign(_ATTESTATION_KIND, fields))


class ActionPolicy(BaseModel):
    policy_id: str
    operation_types: list[str]
    action_type: str
    execution_mode: ExecutionMode
    requires_selected_governed_evidence: bool = True
    requires_applicability_match: bool = True
    requires_target_confirmation: bool = False
    requires_human_approval: bool = False
    read_only: bool = False
    prohibited: bool = False
    prerequisite_facts: list[str] = Field(default_factory=list)
    time_window: Optional[str] = Field(default=None, description="Reserved for maintenance-window policy; not evaluated yet.")


DEFAULT_ACTION_POLICIES: tuple[ActionPolicy, ...] = (
    ActionPolicy(
        policy_id="default.diagnostic_read",
        operation_types=[CommandOperationType.READ_ONLY_DIAGNOSTIC.value],
        action_type="diagnostic_read",
        execution_mode=ExecutionMode.READ_EXECUTION,
        read_only=True,
    ),
    ActionPolicy(
        policy_id="default.state_change",
        operation_types=[CommandOperationType.MUTATING_OPERATIONAL.value, CommandOperationType.CONFIGURATION_CHANGE.value],
        action_type="state_change",
        execution_mode=ExecutionMode.APPROVAL_REQUIRED,
        requires_target_confirmation=True,
        requires_human_approval=True,
    ),
)


class PolicyDecision(BaseModel):
    decision: PolicyDecisionKind
    execution_mode: ExecutionMode
    reason_codes: list[PolicyReasonCode] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)
    policy_id: Optional[str] = None

    @property
    def permits_read_execution(self) -> bool:
        return self.decision is PolicyDecisionKind.ALLOWED and self.execution_mode is ExecutionMode.READ_EXECUTION


def _deny(*codes: PolicyReasonCode, policy_id: Optional[str] = None) -> PolicyDecision:
    return PolicyDecision(
        decision=PolicyDecisionKind.PROHIBITED, execution_mode=ExecutionMode.PROHIBITED, reason_codes=list(codes), policy_id=policy_id
    )


def evaluate_action_policy(
    attestation: Any,
    *,
    target_confirmed: bool = False,
    adapter_available: bool = False,
    satisfied_prerequisites: Iterable[str] = (),
    policies: Optional[Iterable[ActionPolicy]] = None,
) -> PolicyDecision:
    """Deterministic policy decision for an attested command. Never calls a model."""
    if not isinstance(attestation, AuthorityAttestation) or not attestation.is_genuine():
        return _deny(PolicyReasonCode.COMMAND_NOT_AUTHORIZED)
    if not attestation.source_selected:
        return _deny(PolicyReasonCode.SOURCE_NOT_SELECTED)
    if attestation.source_lifecycle != "approved":
        return _deny(PolicyReasonCode.SOURCE_NOT_APPROVED)
    if attestation.source_applicability != "match":
        return _deny(PolicyReasonCode.APPLICABILITY_NOT_MATCH)

    policy = next((p for p in (policies or DEFAULT_ACTION_POLICIES) if attestation.operation_type in p.operation_types), None)
    if policy is None:
        return _deny(PolicyReasonCode.UNCLASSIFIED_OPERATION)
    if policy.prohibited or policy.execution_mode is ExecutionMode.PROHIBITED:
        return _deny(PolicyReasonCode.POLICY_PROHIBITED, policy_id=policy.policy_id)

    requirements = ["selected_governed_evidence", "applicability_match", "command_authorized"]
    missing = [f for f in policy.prerequisite_facts if f not in set(satisfied_prerequisites)]
    if missing:
        return PolicyDecision(
            decision=PolicyDecisionKind.CLARIFICATION_REQUIRED,
            execution_mode=policy.execution_mode,
            reason_codes=[PolicyReasonCode.MISSING_PREREQUISITE],
            requirements=requirements + [f"prerequisite:{m}" for m in missing],
            policy_id=policy.policy_id,
        )

    if policy.requires_target_confirmation or policy.requires_human_approval:
        requirements += [r for r, needed in (("target_confirmation", policy.requires_target_confirmation), ("human_approval", policy.requires_human_approval)) if needed]
        if policy.requires_target_confirmation and (
            not target_confirmed
            or attestation.authority_status is not AuthorityStatus.AUTHORIZED
            or not attestation.target_confirmation_id
        ):
            return PolicyDecision(
                decision=PolicyDecisionKind.CLARIFICATION_REQUIRED,
                execution_mode=policy.execution_mode,
                reason_codes=[PolicyReasonCode.STATE_CHANGE, PolicyReasonCode.TARGET_NOT_CONFIRMED],
                requirements=requirements,
                policy_id=policy.policy_id,
            )
        return PolicyDecision(
            decision=PolicyDecisionKind.APPROVAL_REQUIRED,
            execution_mode=ExecutionMode.APPROVAL_REQUIRED,
            reason_codes=[PolicyReasonCode.STATE_CHANGE, PolicyReasonCode.APPROVAL_REQUIRED],
            requirements=requirements,
            policy_id=policy.policy_id,
        )

    if attestation.authority_status is not AuthorityStatus.AUTHORIZED:
        return _deny(PolicyReasonCode.COMMAND_NOT_AUTHORIZED, policy_id=policy.policy_id)
    if policy.read_only and policy.execution_mode is ExecutionMode.READ_EXECUTION and adapter_available:
        return PolicyDecision(
            decision=PolicyDecisionKind.ALLOWED,
            execution_mode=ExecutionMode.READ_EXECUTION,
            reason_codes=[PolicyReasonCode.READ_ONLY_DIAGNOSTIC, PolicyReasonCode.READ_EXECUTION_PERMITTED],
            requirements=requirements,
            policy_id=policy.policy_id,
        )
    return PolicyDecision(
        decision=PolicyDecisionKind.ALLOWED,
        execution_mode=ExecutionMode.ADVISORY,
        reason_codes=[PolicyReasonCode.READ_ONLY_DIAGNOSTIC]
        + ([PolicyReasonCode.EXECUTION_ADAPTER_UNAVAILABLE] if policy.execution_mode is ExecutionMode.READ_EXECUTION else []),
        requirements=requirements,
        policy_id=policy.policy_id,
    )
