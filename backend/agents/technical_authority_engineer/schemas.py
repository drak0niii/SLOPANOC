"""Structured input and output schemas for the Technical Authority Engineer.

Historical alias: Troubleshooting Manager (Phase 6A).

This specialist:
- Interprets verified technical problems.
- Distinguishes observed facts from hypotheses.
- Identifies missing diagnostic information.
- Evaluates applicable approved knowledge.
- Recommends at most ONE useful next diagnostic check.
- Explains why the check matters and what specific evidence is needed.
- Enforces strict command trust: operational commands must be grounded in an
  approved, verified source catalog.
- Operates in an advisory role only: no direct execution, no configuration
  changes, no approval authority, no Teams write capabilities.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class TechnicalAuthorityOutcome(str, Enum):
    """The high-level disposition of the technical evaluation."""

    RECOMMENDED = "recommended"
    """A single evidence-supported diagnostic step is recommended."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    """Current evidence is insufficient to recommend a reliable next check.
    Missing diagnostic information must be gathered first."""

    ESCALATION_REQUIRED = "escalation_required"
    """The issue cannot be resolved or diagnosed safely with available
    procedures, or policy requires escalation to the system/platform owner."""

    ERROR = "error"
    """A technical evaluation failure occurred (e.g. malformed inputs or
    conflicting unresolvable state)."""


class EvidenceReference(BaseModel):
    """A server-validated evidence item available to the specialist.

    Populated from trusted application state (governed knowledge, Teams
    retrievals, Case context). The model cannot forge these items.
    """

    source_id: str = Field(
        ...,
        description="Unique identifier for the evidence item (e.g. 'doc123:v1:sec2', 'teams:msg987').",
    )
    source_type: str = Field(
        ...,
        description="Type of source: 'governed_knowledge', 'teams_conversation', 'case_context', etc.",
    )
    title: Optional[str] = Field(
        default=None,
        description="Human-readable title or heading of the evidence source.",
    )
    content_snippet: Optional[str] = Field(
        default=None,
        description="Relevant text or diagnostic extract from the verified evidence.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured metadata (e.g. section_id, author, timestamp).",
    )


class CommandOperationType(str, Enum):
    """Classification of command operational impact."""

    READ_ONLY_DIAGNOSTIC = "read_only_diagnostic"
    """Read-only inspection or diagnostic check that does not alter node state."""

    MUTATING_OPERATIONAL = "mutating_operational"
    """State-changing operational action (e.g., restart, reset, reload, unlock/lock)."""

    CONFIGURATION_CHANGE = "configuration_change"
    """Persistent configuration or parameter modification."""

    UNKNOWN = "unknown"
    """Unclassified or ambiguous command."""


class GroundedCommand(BaseModel):
    """A command candidate whose textual or template presence is verified against trusted evidence.

    Textual grounding verifies that the command or template appears in approved,
    applicability-matching governed evidence. Grounding alone does NOT confer operational authorization.
    """

    command: str = Field(
        ...,
        description="The exact command string or instantiated template found in the evidence.",
    )
    source_id: str = Field(
        ...,
        description="Canonical source ID of the approved governed evidence item grounding this command.",
    )
    knowledge_id: Optional[str] = Field(
        default=None,
        description="Identifier of the knowledge document.",
    )
    version_label: Optional[str] = Field(
        default=None,
        description="Version label of the governing document.",
    )
    section_id: Optional[str] = Field(
        default=None,
        description="Section identifier containing the command.",
    )
    procedure_section: Optional[str] = Field(
        default=None,
        description="Human-readable title or procedure section name.",
    )
    grounding_method: str = Field(
        default="exact_match",
        description="Grounding method: 'exact_match', 'backtick_template', or 'template_pattern'.",
    )
    raw_snippet: Optional[str] = Field(
        default=None,
        description="Snippet extract where the command was matched.",
    )


class AuthorizedCommand(BaseModel):
    """An approved diagnostic command authorized for the current incident context.

    Evaluated and authorized strictly by server-side policy from a GroundedCommand.
    Validates operational classification, target constraints, parameter bindings,
    prerequisites, and restrictions.
    """

    command: str = Field(
        ...,
        description="The exact command string authorized by an approved procedure.",
    )
    source_id: str = Field(
        ...,
        description="The source_id of the approved knowledge document/section containing this command.",
    )
    procedure_section: Optional[str] = Field(
        default=None,
        description="Specific section or procedure step where the command is authorized.",
    )
    operation_type: CommandOperationType = Field(
        default=CommandOperationType.UNKNOWN,
        description="Classification of operational impact (read-only diagnostic vs mutating operational).",
    )
    target_confirmed: bool = Field(
        default=False,
        description="Whether target node/entity identity is confirmed in trusted server state.",
    )
    required_parameters: list[str] = Field(
        default_factory=list,
        description="Parameters required by the command template.",
    )
    validated_parameters: dict[str, str] = Field(
        default_factory=dict,
        description="Parameters verified and bound from trusted context.",
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description="Prerequisites required prior to execution.",
    )
    restrictions: list[str] = Field(
        default_factory=list,
        description="Safety restrictions or precautions associated with this command.",
    )
    authorization_decision: str = Field(
        default="unauthorized",
        description="Server authorization decision rationale.",
    )


class ApprovedCommand(AuthorizedCommand):
    """Backward-compatible alias and subtype for AuthorizedCommand.

    Existing callers and schemas referencing ApprovedCommand remain fully compatible.
    """
    pass


class ProcedureParameterValue(BaseModel):
    """A model-proposed association of an operator-stated value with a procedure placeholder.

    The server accepts the value only if the operator literally typed it (or it was confirmed
    earlier in the session); the model can point at operator text, never supply a value.
    """

    name: str = Field(..., description="Exact parameter name from the chosen action's required_parameters.")
    value: str = Field(..., description="The value exactly as the operator typed it. Never inferred or reformatted.")


class VerificationCriterionProposal(BaseModel):
    """A PROPOSED success criterion for a remediation. The server accepts it only when every
    expectation is grounded: `must_exclude` values were observed in a trusted pre-action
    observation, `must_include` values are stated by the SELECTED governed procedure, and the check
    (if any) is a governed read action. Acceptance and evaluation are server-side."""

    kind: Literal["original_condition", "operational_state", "dependent_condition", "expected_evidence"] = Field(
        ..., description="What the criterion verifies."
    )
    description: str = Field(..., description="Plain-language statement of the criterion.")
    check_procedure_action_id: Optional[str] = Field(
        default=None, description="action_id of the governed read whose post-action output is evaluated."
    )
    scope: Optional[str] = Field(default=None, description="Literal token (e.g. the target identifier) selecting the output lines to evaluate.")
    must_include: list[str] = Field(default_factory=list, description="Literal values the governed procedure states must be observed.")
    must_exclude: list[str] = Field(default_factory=list, description="Literal values observed before the action that must no longer be observed.")


class HypothesisUpdateProposal(BaseModel):
    """A PROPOSED hypothesis state. The server applies it only with trusted observations recorded
    this turn; CONFIRMED is never applied from a proposal (it requires met verification criteria)."""

    statement: str = Field(..., description="The hypothesis, stated once and reused verbatim across turns.")
    proposed_state: Literal["active", "supported", "weakened", "rejected", "confirmed"] = Field(...)
    rationale: str = Field(default="", description="Why the latest observation supports this state (informative only).")


class EvidenceRequirementProposal(BaseModel):
    """WHAT evidence is needed (never HOW it is acquired). A command, procedure, tool or approval is
    never evidence: a missing approved acquisition method is the system's gap, not an operator fact."""

    kind: str = Field(
        description=(
            "'operator_fact' (a fact the operator states: vendor, technology, node id, board slot); "
            "'diagnostic_result' (output of a diagnostic read / system query: alarm list, sync status, counters); "
            "'observation' (what the operator can see or measure directly WITHOUT any command: LED colour, cabling); "
            "'live_operational_context' (ticket history, alarm history, topology)."
        )
    )
    description: str = Field(description="The evidence itself, e.g. 'Node synchronization source and status'. Never a command.")
    capability: Optional[str] = Field(
        default=None, description="Short normalized key of the evidence, e.g. 'active_alarms', 'node_sync_status'."
    )
    requirement_ref: Optional[str] = Field(
        default=None,
        description=(
            "requirement_id of an OPEN requirement in investigation_state.open_evidence_requirements when this is the same "
            "evidence need (the server validates it); omit for a genuinely new need."
        ),
    )


class DiagnosticStep(BaseModel):
    """A single recommended diagnostic action.

    Strictly at most one step may be provided in a response.
    Operational commands must be grounded in an ApprovedCommand or verified evidence.
    """

    action: str = Field(
        ...,
        description="The single diagnostic action to perform, described clearly in plain language.",
    )
    reason: str = Field(
        ...,
        description="Technical justification for why this check is necessary and what it will isolate or confirm.",
    )
    command: Optional[str] = Field(
        default=None,
        description=(
            "Exact operational command to execute, if applicable. Must be drawn verbatim from an "
            "approved procedure or verified evidence. Must be None if no approved command exists."
        ),
    )
    command_source: Optional[str] = Field(
        default=None,
        description="Citation indicating the exact approved document/section authorizing the command.",
    )
    expected_evidence: str = Field(
        ...,
        description="The exact observation, diagnostic output, or symptom the engineer should report back.",
    )
    restrictions: list[str] = Field(
        default_factory=list,
        description="Operational precautions, warnings, or prohibitions (e.g. 'Do not reboot', 'Read-only check').",
    )
    diagnostic_intent: Optional[str] = Field(
        default=None,
        description="WHAT this check establishes, in plain words (e.g. which component state it confirms).",
    )
    procedure_action_id: Optional[str] = Field(
        default=None,
        description=(
            "Exact action_id from this turn's `procedure_action_catalog` result when an approved procedure "
            "action performs this check; otherwise null. The server resolves the command from it."
        ),
    )
    parameter_values: list[ProcedureParameterValue] = Field(
        default_factory=list,
        description="Operator-stated values for the chosen action's required_parameters (verbatim only).",
    )
    tests_hypothesis: Optional[str] = Field(
        default=None, description="Statement of the hypothesis this step tests (verbatim from hypothesis_updates), if any."
    )
    verification_criteria: list[VerificationCriterionProposal] = Field(
        default_factory=list,
        description="For a remediation (state-changing) step only: how success must be verified after the action.",
    )
    evidence_requirement: Optional[EvidenceRequirementProposal] = Field(
        default=None, description="WHAT evidence this step obtains (kind + description). Required for every step."
    )
    acquisition: Optional[str] = Field(
        default=None,
        description=(
            "HOW you propose to obtain it: 'governed_action' (procedure_action_id / governed command), "
            "'manual_observation' (ONLY for evidence of kind 'observation'), 'operator_fact' (ONLY for kind "
            "'operator_fact'), or 'none' when no approved method is known. Never ask the operator for a command."
        ),
    )


class CurrentTurnRequest(BaseModel):
    """What the operator asks in THEIR LATEST MESSAGE ONLY (filled by the caller). The server
    re-checks every field against the operator's exact words; values that only come from earlier
    turns are discarded, never used to redefine the current request."""

    intent: Optional[str] = Field(
        default=None,
        description="What the latest message asks for, in a few words (e.g. 'procedure to reset a unit', 'interpret this output').",
    )
    subject_component: Optional[str] = Field(
        default=None, description="Component/object the latest message is about, as the operator wrote it."
    )
    requested_operation: Optional[str] = Field(
        default=None, description="Operation the latest message asks about (e.g. reset, restart, status check), as written."
    )
    explicit_target: Optional[str] = Field(
        default=None, description="Specific target identifier written in the latest message (unit/node/MO id), verbatim."
    )
    vendor: Optional[str] = Field(default=None, description="Vendor, only if written in the latest message.")
    technology: Optional[str] = Field(default=None, description="Technology, only if written in the latest message.")
    continues_active_objective: Optional[bool] = Field(
        default=None,
        description="True only if the latest message continues the current diagnostic objective; False if it switches focus.",
    )


class RequestField(BaseModel):
    value: str
    source: str = Field(description="'current_message' (operator's latest words) or 'session_confirmed' (enrichment only).")


class TurnRequestContract(BaseModel):
    """Server-built structured request for this turn (never model-authored). The explicit
    current-turn request takes precedence over the historical troubleshooting objective;
    history may only enrich it."""

    user_request_text: str
    intent: Optional[str] = None
    subject_component: Optional[RequestField] = None
    requested_operation: Optional[RequestField] = None
    explicit_target: Optional[RequestField] = None
    vendor: Optional[RequestField] = None
    technology: Optional[RequestField] = None
    focus: str = Field(description="'new' (no active investigation), 'continue' or 'switch' (explicit change of focus).")
    request_kind: str = Field(
        default="operational",
        description="'operational', 'clarification_follow_up' (asks what information is still required) or "
        "'clarification_answer' (supplies requested information). Server-classified against the fault's pending clarification.",
    )
    explicit_in_current_message: bool = False
    diagnostic_objective: str
    active_investigation_objective: Optional[str] = None
    discarded_fields: list[dict[str, str]] = Field(default_factory=list)
    precedence_rule: str = (
        "The explicit current-turn request takes precedence over the historical troubleshooting objective; "
        "history may only enrich it, never redefine it."
    )


class TechnicalAuthorityRequest(BaseModel):
    """Input payload sent to the Technical Authority Engineer specialist.

    Constructed with server-validated context so the model cannot fabricate
    trusted evidence.
    """

    problem_statement: str = Field(
        ...,
        description="The technical problem or incident symptom under investigation.",
    )
    verified_symptoms: Optional[list[str]] = Field(
        default=None,
        description="Observed facts and symptoms verified from current evidence.",
    )
    missing_information: Optional[list[str]] = Field(
        default=None,
        description="Gaps in diagnostic evidence identified so far.",
    )
    verified_evidence: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="List of server-verified evidence items available for this evaluation.",
    )
    approved_commands_catalog: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Catalog of verified operational commands approved for this operational domain.",
    )
    known_applicability_facts: Optional[dict[str, list[str]]] = Field(
        default=None,
        description="Operational context facts explicitly stated (e.g. {'vendor': ['ericsson'], 'technology': ['4g']}).",
    )
    prior_steps_taken: Optional[list[str]] = Field(
        default=None,
        description="Diagnostic or operational steps already completed in this session.",
    )
    current_request: Optional[CurrentTurnRequest] = Field(
        default=None,
        description="Structured reading of the operator's LATEST message only (never an earlier objective).",
    )
    turn_request_contract: Optional[TurnRequestContract] = Field(
        default=None,
        description="Server-built; any caller-supplied value is replaced.",
    )
    active_investigation_context: Optional[str] = Field(
        default=None,
        description="Server-built background from the active troubleshooting state; never the current request.",
    )
    pending_step: Optional[dict[str, Any]] = Field(
        default=None,
        description="Server-built: the current diagnostic step still awaiting its result (any caller value is replaced).",
    )
    resumed_governed_retrieval: Optional[dict[str, Any]] = Field(
        default=None,
        description="Server-performed: after the operator resolved an applicability clarification, the fresh governed "
        "knowledge_search of THIS run under the confirmed context (AVAILABLE results only; nothing selected). "
        "Any caller value is replaced.",
    )
    investigation_state: Optional[dict[str, Any]] = Field(
        default=None,
        description="Server-built: stage, resolution, remediation/verification status and hypothesis states (any caller value is replaced).",
    )


class TechnicalAuthorityResponse(BaseModel):
    """Output payload produced by the Technical Authority Engineer specialist.

    Validated deterministically by after_agent_callback to guarantee single-step
    discipline, citation provenance, and command grounding.
    """

    outcome: TechnicalAuthorityOutcome = Field(
        ...,
        description="Outcome of the evaluation: 'recommended', 'insufficient_evidence', 'escalation_required', 'error'.",
    )
    technical_interpretation: str = Field(
        ...,
        description=(
            "Current technical interpretation of the incident. Must distinguish observed facts "
            "from hypotheses and avoid speculative claims."
        ),
    )
    verified_evidence_citations: list[str] = Field(
        default_factory=list,
        description="List of source_ids from verified_evidence that support the interpretation and recommendations.",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Key diagnostic facts or observations that are still missing (never a command, procedure or tool).",
    )
    required_evidence: list[EvidenceRequirementProposal] = Field(
        default_factory=list,
        description="Typed evidence still required when no diagnostic_step is given (operator facts vs diagnostic results).",
    )
    diagnostic_step: Optional[DiagnosticStep] = Field(
        default=None,
        description="The single recommended next diagnostic step. Must be None unless outcome is 'recommended'.",
    )
    escalation_reason: Optional[str] = Field(
        default=None,
        description="Explanation of why escalation is required, populated when outcome is 'escalation_required'.",
    )
    hypothesis_updates: list[HypothesisUpdateProposal] = Field(
        default_factory=list,
        description="Proposed hypothesis states given the latest observation; applied only by the server with evidence.",
    )
    detail: Optional[str] = Field(
        default=None,
        description="Optional technical context, boundary notes, or explanation.",
    )
