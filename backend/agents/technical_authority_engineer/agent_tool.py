"""`TechnicalAuthorityAgentTool` -- in-process ADK AgentTool delegation for the
Technical Authority Engineer specialist.

Enforces a server-validated context envelope:
- The model (Team Manager) cannot fabricate `verified_evidence` references.
  Evidence is assembled from server-held trusted state: governed knowledge
  selected in this run, verified Teams messages, and active Case context.
- Operational commands in `approved_commands_catalog` must be grounded in
  authoritative governed procedures. Fabricated commands supplied by the caller
  are filtered out.
- Forwards current-turn image evidence (if any) using trusted `file_data` Parts,
  mirroring the `MultimodalAgentTool` discipline.
"""
from __future__ import annotations

import json
import logging
import re
import uuid as _uuid_mod
from typing import Any, Optional

from google.adk.memory import InMemoryMemoryService
from google.adk.tools import AgentTool
from google.adk.tools._forwarding_artifact_service import ForwardingArtifactService
from google.adk.tools.agent_tool import _get_input_schema, _get_output_schema
from google.adk.tools.tool_context import ToolContext
from google.adk.utils._schema_utils import validate_schema
from google.adk.utils.context_utils import Aclosing
from google.genai import types
from typing_extensions import override

from backend.agents.technical_authority_engineer.execution_context import (
    record_technical_authority_execution,
)
from backend.agents.technical_authority_engineer.schemas import (
    ApprovedCommand,
    AuthorizedCommand,
    CommandOperationType,
    EvidenceReference,
    GroundedCommand,
    TechnicalAuthorityOutcome,
    TechnicalAuthorityRequest,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.validation import (
    ACTION_RESOLUTION_DEFERRED,
    INTEGRITY_UNRESOLVED_CITATION_TEXT,
    _is_command_matching_snippet,
    _template_to_regex,
    integrity_decisions,
    record_integrity_decision,
    rollback_integrity_decisions,
    validate_technical_authority_payload,
)
from backend.agents.technical_authority_engineer.applicability_context import (
    CONFIRMED_APPLICABILITY_FACTS_STATE_KEY,
    build_applicability_clarification,
    current_user_text,
    load_governed_vocabulary,
    merge_applicability_facts,
    read_confirmed_applicability_facts,
    reconcile_applicability_facts,
)
from backend.agents.technical_authority_engineer.procedure_actions import (
    applicability_blocked_action,
    authorized_command_action,
    build_procedure_action_catalog,
    resolved_candidate_grounded_in_section,
    verified_resolver_candidate,
    CONFIRMED_PROCEDURE_PARAMETERS_STATE_KEY,
    NO_APPROVED_COMMAND_FOR_ACTION_TEXT,
    PROCEDURE_ACTION_RESOLUTION_KEY,
    ProcedureActionResolution,
    ProcedureActionResolutionStatus,
    ProcedureActionType,
    build_parameter_clarification,
    build_condition_clarification,
    build_target_clarification,
    instruction_view,
    issued_action_ids,
    issued_actions,
    names_governed_action,
    read_confirmed_parameters,
    record_issued_actions,
    resolution_summary,
    resolve_procedure_action,
)
from backend.agents.technical_authority_engineer.procedure_semantics import GOVERNED_SCOPE_ANNOTATION, ensure_governed_scope
from backend.agents.technical_authority_engineer.clarification_continuity import (
    CLARIFICATION_CONTINUITY_KEY,
    NO_PENDING_CLARIFICATION_TEXT,
    ClarificationTurnKind,
    bind_applicability_answer,
    clarification_meta_result,
    is_clarification_follow_up,
    render_clarification_request,
)
from backend.agents.technical_authority_engineer.command_syntax import single_invocation_violation
from backend.agents.technical_authority_engineer.acquisition_continuity import (
    ACQUISITION_CONTINUITY_KEY,
    AcquisitionBinding,
    ContinuationKind,
    ContinuationRule,
    KnownGovernedAcquisition,
    bind_acquisition_request,
    continuation_kind,
    is_acquisition_request,
    known_governed_acquisition,
    effective_rule,
    is_definitive_rejection,
    model_chose,
    pre_run_rule,
    reconciled_proposal,
    reconstruct_known_action,
)
from backend.agents.technical_authority_engineer.evidence_acquisition import (
    EVIDENCE_ACQUISITION_KEY,
    SUPPORTING_EVIDENCE_IDENTITIES_KEY,
    SUPPORTING_EVIDENCE_KEY,
    AcquisitionDecision,
    AcquisitionOutcome,
    DiscoveryState,
    GovernedActionState,
    authority_decision,
    decide_acquisition,
    detect_acquisition_hint,
    evidence_from_mechanism_item,
    hint_target,
    make_hint,
    match_open_requirement,
    refine_requirement,
    requirement_from_proposal,
    requirement_from_step,
    split_missing_information,
)
from backend.agents.technical_authority_engineer.evidence_sources import governed_document_type, is_action_source, live_sources_for
from backend.agents.technical_authority_engineer.gap_recovery import (
    GAP_CONTINUATION_KEY,
    GAP_RECOVERY_KEY,
    NON_TERMINAL_GAP_NOTE,
    GovernedCommand,
    action_choice_instruction,
    attempted_ungrounded_command,
    gap_continuation_instruction,
    proposed_branch_requirement,
    proposes_method,
    recovery_instruction,
    resume_choice_instruction,
    viable_governed_alternatives,
)
from backend.agents.technical_authority_engineer.progression_repository import ProgressionConflict, ProgressionRepository
from backend.agents.technical_authority_engineer.action_id_recovery import (
    MAX_ACTION_ID_RESELECTIONS,
    proposed_action_id,
    reselection_instruction,
    trace_reselection_request,
    trace_reselection_result,
    unknown_action_id,
)
from backend.agents.technical_authority_engineer.structured_output import (
    MAX_STRUCTURED_OUTPUT_ATTEMPTS,
    STRUCTURED_OUTPUT_RECORD_KEY,
    StructuredOutputFailure,
    StructuredOutputRecovery,
    classify_structured_output,
    regeneration_instruction,
)
from backend.agents.technical_authority_engineer.progression_controller import (
    withdraw_rejected_command,
    ACCEPTED_DECISIONS,
    PENDING_STATUSES,
    PROGRESSION_CONTROL_KEY,
    ProgressionController,
    ProposalDecision,
    TurnKind,
    is_multi_action_command,
)
from backend.agents.technical_authority_engineer.troubleshooting_threads import (
    ThreadDecision,
    load_active_thread,
    resolve_active_thread,
    save_projections,
)
from backend.agents.technical_authority_engineer.turn_request import (
    active_investigation_objective,
    build_turn_request_contract,
)
from backend.api.turn_context import current_run_id
from backend.cases.evidence_identity import EvidenceIdentity, identities_of, identity_of
from backend.cases.evidence_model import CandidateAvailability, EvidenceKind, GapReason, GapStatus, RequirementStatus
from backend.cases.target_facts import case_target_facts, trusted_case_results
from backend.cases.troubleshooting_progression import (
    ClarificationReason,
    ClarificationStatus,
    OpenQuestion,
    ProgressionPhase,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
    applicability_clarification_trace_view,
    stale_applicability_blockers,
)
from backend.cases.troubleshooting_state import (
    CheckLifecycleStatus,
    TroubleshootingState,
    TroubleshootingStatus,
)
from backend.knowledge.domain.applicability import ApplicabilityContext
from backend.knowledge.domain.models import normalize_dimension_key
from backend.tools.knowledge.runtime import (
    run_search_log,
    get_available_knowledge_evidence,
    get_evidence_applicability_outcome,
    get_available_unresolved_applicability_dimensions,
    get_evidence_unresolved_applicability_dimensions,
    get_evidence_annotation,
    has_explicit_empty_knowledge_selection,
    has_knowledge_run_state,
    refresh_run_applicability_context,
    snapshot_selected_knowledge_evidence,
)
from backend.tools.knowledge.diagnostic_trace import (
    record_action_resolution,
    record_command_authority,
    record_turn_request,
)
from backend.tools.teams.get_messages import read_known_message_ids

logger = logging.getLogger(__name__)


def _trusted_image_parts(tool_context: ToolContext) -> list[types.Part]:
    """Extracts trusted `file_data` parts from calling invocation's `user_content`."""
    user_content = getattr(tool_context, "user_content", None)
    parts = getattr(user_content, "parts", None) if user_content is not None else None
    if not parts:
        return []
    return [part for part in parts if getattr(part, "file_data", None) is not None]


def _governed_reference(run_id: str, item: Any) -> EvidenceReference:
    """Server-built reference of one governed KM evidence item of this run (structured identity in
    `metadata`; `source_id` is the display id)."""
    ident = (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)
    lifecycle_val = (
        item.lifecycle_status.value.lower()
        if hasattr(item.lifecycle_status, "value")
        else str(item.lifecycle_status).lower()
    )
    return EvidenceReference(
        source_id=f"{ident[0]}:{ident[1]}:{ident[2]}",
        source_type="governed_knowledge",
        title=item.title,
        content_snippet=item.section.content,
        metadata={
            "knowledge_id": item.reference.knowledge_id,
            "version_label": item.reference.version_label,
            "section_id": item.reference.section_id,
            "heading": item.section.heading,
            "section_type": item.section.section_type,
            "source_locator": item.section.source_locator,
            "source_id": item.source.source_id,
            "title": item.title,
            "document_type": getattr(item.document_type, "value", item.document_type),
            "lifecycle_status": lifecycle_val,
            "applicability_outcome": get_evidence_applicability_outcome(run_id, ident) or "unknown",
            "unresolved_applicability_dimensions": get_evidence_unresolved_applicability_dimensions(run_id, ident),
            # Server-derived document structure of the same governed version
            # (None until derived -> state changes from it fail closed).
            "governed_scope": get_evidence_annotation(run_id, ident, GOVERNED_SCOPE_ANNOTATION),
            "artifact_derived": bool(item.artifact is not None and item.artifact.derived),
        },
    )


def run_governed_evidence(run_id: Optional[str]) -> list[EvidenceReference]:
    """THIS run's governed evidence, AVAILABLE and SELECTED, as server-built references -- for the
    gap-recovery CANDIDATE SET only (identity, zero authority). Never handed to Command Authority,
    the resolver or the catalog tool: authority still requires explicit selection. Items whose
    display id is shared by another identity are skipped."""
    if not run_id:
        return []
    items: dict[tuple[Any, ...], Any] = {}
    for item in list(snapshot_selected_knowledge_evidence(run_id)) + list(get_available_knowledge_evidence(run_id).items):
        ident = (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)
        if all(isinstance(v, str) and v for v in ident):
            items.setdefault(ident, item)
    displays: dict[str, int] = {}
    for ident in items:
        displays[f"{ident[0]}:{ident[1]}:{ident[2]}"] = displays.get(f"{ident[0]}:{ident[1]}:{ident[2]}", 0) + 1
    return [_governed_reference(run_id, item) for ident, item in items.items() if displays[f"{ident[0]}:{ident[1]}:{ident[2]}"] == 1]


def build_server_validated_evidence(
    run_id: Optional[str],
    tool_context: ToolContext,
    caller_evidence: list[dict[str, Any]],
) -> list[EvidenceReference]:
    """Assembles trusted evidence references from server state and validates caller claims."""
    evidence_items: list[EvidenceReference] = []
    seen_source_ids: set[str] = set()

    # 1. Governed Knowledge evidence from current run (SELECTED only; SEARCH != SELECTION).
    # Identity is the exact (knowledge_id, version_label, section_id) tuple; `source_id` is its
    # canonical DISPLAY string. Components may contain ':', so two different tuples can render the
    # same string: such sections are withheld (fail closed) so that within this run every display
    # string denotes exactly one tuple wherever later stages key or compare by it.
    if run_id:
        km_selected = snapshot_selected_knowledge_evidence(run_id)
        tuples_by_display: dict[str, set[tuple[Any, ...]]] = {}
        for item in km_selected:
            ident = (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)
            tuples_by_display.setdefault(f"{ident[0]}:{ident[1]}:{ident[2]}", set()).add(ident)
        ambiguous = {display for display, tuples in tuples_by_display.items() if len(tuples) > 1}
        for display in sorted(ambiguous):
            logger.warning("Withholding selected governed evidence: display id %r is shared by %d distinct identities", display, len(tuples_by_display[display]))
            try:
                from backend.tools.knowledge.diagnostic_trace import record_operational_event

                record_operational_event({
                    "stage": "evidence_identity_conflict", "display_id": display,
                    "identities": [list(t) for t in sorted(tuples_by_display[display], key=str)],
                })
            except Exception:
                pass
        for item in km_selected:
            source_id = f"{item.reference.knowledge_id}:{item.reference.version_label}:{item.reference.section_id}"
            if source_id not in seen_source_ids and source_id not in ambiguous:
                seen_source_ids.add(source_id)
                evidence_items.append(_governed_reference(run_id, item))

    # 2. Known Teams messages from session state
    if tool_context and hasattr(tool_context, "state") and tool_context.state:
        known_msg_ids = read_known_message_ids(tool_context.state)
        for msg_id in known_msg_ids:
            source_id = f"teams:{msg_id}"
            if source_id not in seen_source_ids:
                seen_source_ids.add(source_id)
                evidence_items.append(
                    EvidenceReference(
                        source_id=source_id,
                        source_type="teams_conversation",
                        title=f"Teams Message {msg_id}",
                        content_snippet=None,
                        metadata={"message_id": msg_id},
                    )
                )

    # 3. Caller-supplied evidence validation (caller cannot fabricate arbitrary IDs)
    for caller_item in caller_evidence:
        if not isinstance(caller_item, dict):
            continue
        c_src = caller_item.get("source_id", "").strip()
        if not c_src:
            continue
        # If caller referenced an evidence item that is confirmed in server state, enrich/preserve it
        if c_src in seen_source_ids:
            continue

        # If caller provides case context or user-provided verified observation with non-forgeable type:
        c_type = caller_item.get("source_type")
        if c_type in ("case_context", "user_evidence", "observed_metric"):
            seen_source_ids.add(c_src)
            evidence_items.append(
                EvidenceReference(
                    source_id=c_src,
                    source_type=c_type,
                    title=caller_item.get("title"),
                    content_snippet=caller_item.get("content_snippet"),
                    metadata=caller_item.get("metadata", {}),
                )
            )
        else:
            logger.warning("Rejecting ungrounded caller evidence item: %r", c_src)

    return evidence_items


_PLACEHOLDER_REGEX = re.compile(r"<[^>]+>|\$[A-Za-z_][A-Za-z0-9_]*|\{[^}]+\}")

_MUTATING_PATTERNS = [
    re.compile(r"\b(manualrestart|cvrestart|restart|reboot|reset|reload|poweroff|power_off|shutdown|format|kill|halt|bounce)\b", re.IGNORECASE),
    # Action invocation on a target. The action may carry an object suffix (e.g. a restart of a
    # specific unit type): any action word that BEGINS with a state-changing verb is state-changing.
    # This only moves such commands from UNKNOWN (rejected) to MUTATING_OPERATIONAL, which still
    # requires grounding, trusted target confirmation, policy and human approval -- never less.
    re.compile(r"\bacc\s+\S+\s+(manualrestart|restart|cvrestart|reset|lock|unlock|start|stop)[a-z0-9_]*\b", re.IGNORECASE),
    # The same action-invocation STRUCTURE for any command verb (not one command name): a lowercase
    # verb, one or more managed-object arguments (`Key=Value` paths or placeholders), then a
    # lowercase action word that BEGINS with a state-changing verb, then only plain arguments.
    # Attribute names (camelCase, e.g. a counter) never qualify. Moving a command from UNKNOWN
    # (rejected) to MUTATING_OPERATIONAL only adds gates: target validation, confirmation, approval.
    re.compile(
        r"^[a-z][a-z0-9_\-]*(?:\s+(?:[A-Za-z][A-Za-z0-9_\-]*=\S+|<[^>\s]+>|\{[^}\s]+\}))+"
        r"\s+(?:manualrestart|cvrestart|restart|reboot|reset|reload|lock|unlock|shutdown|poweroff)[a-z0-9_]*"
        r"(?:\s+[^\s=;|&`$<>(){}]+)*$"
    ),
    re.compile(r"\b(bl|deb)\b", re.IGNORECASE),
    re.compile(r"\b(rm|format|drop|isolate|drain)\b", re.IGNORECASE),
]

_CONFIG_PATTERNS = [
    re.compile(r"^(set|cr|del|config|configure|modify|alter|patch|update|provision)\b", re.IGNORECASE),
    re.compile(r"\bacc\s+\S+\s+(set|modify|configure|update)\b", re.IGNORECASE),
]

_READ_ONLY_PATTERNS = [
    re.compile(r"^(alt|alt\s+.*)$", re.IGNORECASE),
    re.compile(r"^(st|st\s+.*)$", re.IGNORECASE),
    re.compile(r"^(show|display|get|read|inspect|check|traceroute|ping|lh|pr|inv|cab|cvls|lg|status)\b", re.IGNORECASE),
    # The `get` read family with a one-letter modifier (e.g. a layout/scope variant such as a
    # tabular or recursive get). Structural rule only -- no specific command, vendor or object is
    # listed. Deliberately stricter than the rules above: the whole command must be a single
    # invocation (no `;`, `|`, `&`, backtick, `$`, redirection, parentheses/braces or newline), so a
    # chained or substituted state-changing command can never be classified read-only through it.
    # Mutating/config patterns are still evaluated first. Classification never authorizes anything:
    # SELECTED evidence, grounding, applicability MATCH, ProcedureAction resolution, Command
    # Authority and policy all still apply.
    re.compile(r"^[a-z]get(?:[ \t]+[^;|&`$<>(){}\n\r]+)?$", re.IGNORECASE),
]


def classify_command_operation(command: str) -> CommandOperationType:
    """Classifies command operational impact.

    Read-only diagnostics (alt, st, show, etc.) inspection.
    Mutating operational commands (restart, reboot, acc manualrestart, etc.) alter node state.
    Configuration commands (set, cr, del, etc.) change persistent state.
    Unclassified or empty commands return UNKNOWN (fail closed).
    """
    clean_cmd = re.sub(r"\*\*|`", "", command).strip()
    if not clean_cmd:
        return CommandOperationType.UNKNOWN
    for p in _MUTATING_PATTERNS:
        if p.search(clean_cmd):
            return CommandOperationType.MUTATING_OPERATIONAL
    for p in _CONFIG_PATTERNS:
        if p.search(clean_cmd):
            return CommandOperationType.CONFIGURATION_CHANGE
    # A composed candidate (several invocations / substitution / redirection) is never read-only;
    # mutating/config detection above stays conservative. Authorization rejects it regardless.
    if single_invocation_violation(command):
        return CommandOperationType.UNKNOWN
    for p in _READ_ONLY_PATTERNS:
        if p.search(clean_cmd):
            return CommandOperationType.READ_ONLY_DIAGNOSTIC
    return CommandOperationType.UNKNOWN


_STATE_CHANGE_WORD = re.compile(
    r"\b(manualrestart|cvrestart|restart|reboot|reset|reload|poweroff|power_off|shutdown|unlock|lock|start|stop|format|kill|halt|bounce|"
    r"isolate|drain|bl|deb)[a-z0-9_]*\b",
    re.IGNORECASE,
)


def state_change_class(command: str) -> Optional[str]:
    """The state-changing operation word of a classified command ("restart" for an action word
    beginning with it, "configuration" for a configuration change); None for reads/unknown.
    Descriptive only -- authority never depends on it."""
    op_type = classify_command_operation(command)
    if op_type is CommandOperationType.CONFIGURATION_CHANGE:
        return "configuration"
    if op_type is not CommandOperationType.MUTATING_OPERATIONAL:
        return None
    for token in re.sub(r"\*\*|`", "", command).split()[1:]:
        if "=" in token:
            continue
        match = _STATE_CHANGE_WORD.match(token)
        if match:
            return match.group(1).lower()
    return "state_change"


def ground_command_candidate(
    candidate: dict[str, Any],
    evidence_references: list[EvidenceReference],
    authorized_sources: dict[str, EvidenceReference],
) -> Optional[GroundedCommand]:
    """Evaluates candidate command for textual/template presence in approved governed evidence.

    Textual grounding verifies that the command or template appears in approved,
    applicability-matching governed evidence. Grounding alone does NOT confer operational authorization.
    """
    from backend.agents.technical_authority_engineer.validation import (
        _is_command_matching_snippet,
        is_command_prohibited_in_snippet,
        resolve_command_source,
    )

    if not isinstance(candidate, dict):
        return None
    raw_cmd = str(candidate.get("command") or "").strip()
    raw_src = str(candidate.get("source_id") or "").strip()
    if not raw_cmd or not raw_src:
        return None

    # A document-level alias naming several SELECTED sections resolves only to the ONE section whose
    # instruction-only text grounds this exact command (none / several -> unresolved -> rejected).
    canonical_src = resolve_command_source(raw_cmd, raw_src, evidence_references) or raw_src
    if not canonical_src or canonical_src not in authorized_sources:
        return None

    ev = authorized_sources[canonical_src]
    full_snippet = ev.content_snippet or ""
    cmd_stripped = re.sub(r"\*\*|`", "", raw_cmd).strip()

    if is_command_prohibited_in_snippet(raw_cmd, full_snippet):
        return None
    # Grounding sees only the section's INSTRUCTION text: a command that appears only in sample
    # output, a CLI transcript, an explicit example or an image transcription is never authority.
    ev_meta = ev.metadata if isinstance(ev.metadata, dict) else {}
    snippet = instruction_view(full_snippet, artifact_derived=bool(ev_meta.get("artifact_derived")))

    grounding_method = "template_pattern"
    if not _is_command_matching_snippet(raw_cmd, cmd_stripped, snippet):
        # Plain-line parameterized template grounding is available ONLY to a candidate minted by the
        # ProcedureAction resolver (process-scoped HMAC attestation over command, source, action,
        # template, parameters and operation type) -- never to a model-written or caller-supplied
        # command, which keeps exactly the legacy grounding above. The attested template must be
        # re-derived verbatim by the deterministic extractor from this exact authorized section and
        # the command must be its strict rendering with the attested parameters. Every later
        # authorization stage (classification, placeholders, target confirmation, policy, approval)
        # still applies.
        attested = verified_resolver_candidate(candidate)
        if attested is None or canonical_src != raw_src:
            return None
        template, parameters = attested
        if not resolved_candidate_grounded_in_section(raw_cmd, template, parameters, snippet):
            return None
        grounding_method = "procedure_action_template"
    elif raw_cmd in snippet or cmd_stripped in snippet:
        grounding_method = "exact_match"
    else:
        for match in re.finditer(r"`([^`\n]+)`", snippet):
            token = match.group(1).strip()
            if raw_cmd == token or cmd_stripped == token:
                grounding_method = "exact_match"
                break
            template_regex = _template_to_regex(token)
            if template_regex and (template_regex.match(raw_cmd) or template_regex.match(cmd_stripped)):
                grounding_method = "backtick_template"
                break

    # Structured identity from the server-built evidence metadata only (never parsed from the id).
    meta = ev.metadata if isinstance(ev.metadata, dict) else {}
    k_id = meta.get("knowledge_id")
    v_label = meta.get("version_label")
    s_id = meta.get("section_id")

    return GroundedCommand(
        command=raw_cmd,
        source_id=canonical_src,
        knowledge_id=k_id,
        version_label=v_label,
        section_id=s_id,
        procedure_section=candidate.get("procedure_section") or ev.title,
        grounding_method=grounding_method,
        raw_snippet=full_snippet,
    )


def state_change_target_problem(command: str, target_validation: Any) -> Optional[str]:
    """None when `target_validation` (the server target gate's decision, supplied only by trusted
    server code) passed, names at least one target, and every validated `key=value` is exactly what
    `command` acts on; otherwise why not."""
    if not isinstance(target_validation, dict) or target_validation.get("passed") is not True:
        return "no passed current-case target validation"
    targets = [t for t in target_validation.get("targets") or [] if isinstance(t, dict)]
    if not targets:
        return "no validated target"
    for target in targets:
        key, value = str(target.get("key") or ""), str(target.get("value") or "")
        if not value or not target.get("fact_ids"):
            return "a target has no current-case fact"
        written = f"{re.escape(key)}={re.escape(value)}" if key else re.escape(value)
        if not re.search(rf"(?<![A-Za-z0-9_\-]){written}(?![A-Za-z0-9_\-\.:/])", command):
            return f"validated target {(key + '=') if key else ''}{value} is not what the command acts on"
    return None


def authorize_grounded_command(
    grounded: GroundedCommand,
    candidate_meta: dict[str, Any],
    trusted_context: Optional[dict[str, Any]] = None,
) -> Optional[AuthorizedCommand]:
    """Server-evaluated operational safety contract.

    Validates:
    - Operation classification: read-only diagnostics vs mutating operations vs config changes.
    - Placeholder check: unresolved template variables (<mo>, <target>, $TARGET) must fail closed.
    - Authoritative target confirmation: Mutating and config-change commands require
      server-verified target confirmation (`trusted_context={"target_confirmed": True}`).
      Concrete command text arguments (e.g. `cell=1`, `SLOT-1`) do NOT constitute target confirmation.
    - Current-case target validation (checked FIRST for state changes): the trusted caller must pass
      the server target gate's decision (`trusted_context["target_validation"]`); it must have
      passed, name at least one target, and every validated `key=value` must be exactly what the
      command acts on. Confirmation / approval never substitute for it.
    - Unknown operation types fail closed.
    """
    if not isinstance(grounded, GroundedCommand):
        return None

    # Single-invocation boundary FIRST (before classification): a composed candidate is never
    # eligible, even when that exact composed string is present in approved, selected evidence.
    violation = single_invocation_violation(grounded.command)
    if violation:
        logger.warning("Rejecting command authorization: %r is not a single command invocation (%s)", grounded.command, violation)
        record_command_authority(
            command=grounded.command, source_id=grounded.source_id, decision="rejected",
            reason=f"not a single command invocation: {violation}", stage="authorization",
        )
        return None

    op_type = classify_command_operation(grounded.command)
    if op_type == CommandOperationType.UNKNOWN:
        logger.warning("Rejecting command authorization: unknown operation type for %r", grounded.command)
        record_command_authority(
            command=grounded.command, source_id=grounded.source_id, decision="rejected",
            reason="unknown operation type", stage="authorization",
        )
        return None

    clean_cmd = re.sub(r"\*\*|`", "", grounded.command).strip()
    if _PLACEHOLDER_REGEX.findall(clean_cmd):
        logger.warning("Rejecting command authorization: unresolved placeholders in %r", grounded.command)
        record_command_authority(
            command=grounded.command, source_id=grounded.source_id, decision="rejected",
            reason="unresolved placeholders", stage="authorization",
        )
        return None

    is_target_confirmed = bool(trusted_context.get("target_confirmed", False)) if isinstance(trusted_context, dict) else False

    if op_type in (CommandOperationType.MUTATING_OPERATIONAL, CommandOperationType.CONFIGURATION_CHANGE):
        target_problem = state_change_target_problem(
            clean_cmd, trusted_context.get("target_validation") if isinstance(trusted_context, dict) else None
        )
        if target_problem:
            logger.warning("Rejecting command authorization: state change %r has no validated current-case target (%s)", grounded.command, target_problem)
            record_command_authority(
                command=grounded.command, source_id=grounded.source_id, decision="rejected",
                reason=f"{getattr(op_type, 'value', op_type)} requires a validated current-case target: {target_problem}", stage="authorization",
            )
            return None
        if not is_target_confirmed:
            logger.warning(
                "Rejecting command authorization: mutating/config command %r requires trusted target confirmation",
                grounded.command,
            )
            record_command_authority(
                command=grounded.command, source_id=grounded.source_id, decision="rejected",
                reason=f"{getattr(op_type, 'value', op_type)} requires trusted target confirmation", stage="authorization",
            )
            return None

    restrictions = list(candidate_meta.get("restrictions") or [])
    prerequisites = list(candidate_meta.get("prerequisites") or [])
    if isinstance(trusted_context, dict):
        if trusted_context.get("restrictions"):
            restrictions.extend(trusted_context["restrictions"])
        if trusted_context.get("prerequisites"):
            prerequisites.extend(trusted_context["prerequisites"])

    return AuthorizedCommand(
        command=grounded.command,
        source_id=grounded.source_id,
        procedure_section=grounded.procedure_section,
        operation_type=op_type,
        target_confirmed=is_target_confirmed,
        required_parameters=[],
        validated_parameters={},
        prerequisites=prerequisites,
        restrictions=restrictions,
        authorization_decision="authorized",
    )


def build_server_validated_commands(
    evidence_references: list[EvidenceReference],
    caller_commands: list[dict[str, Any]],
    trusted_context: Optional[dict[str, Any]] = None,
) -> list[ApprovedCommand]:
    """Assembles and authorizes commands through the 3-stage trust lifecycle.

    Lifecycle:
    Stage 1: Raw candidate from caller/model
    Stage 2: GroundedCommand (textual/template support in approved, matching governed evidence)
    Stage 3: AuthorizedCommand (operational safety, target confirmation, placeholder evaluation)

    Only fully authorized commands are returned in the catalog.
    """
    from backend.agents.technical_authority_engineer.validation import resolve_canonical_source_id

    approved_list: list[ApprovedCommand] = []
    seen_commands: set[tuple[str, str]] = set()

    _AUTHORIZED_SOURCE_TYPES = {"governed_knowledge", "approved_procedure", "governed_command_registry"}
    authorized_sources: dict[str, EvidenceReference] = {}
    for ev in evidence_references:
        src_type = ev.source_type or ""
        src_id = (ev.source_id or "").strip()
        # Non-governed sources (Teams chats, raw case notes, user observations) CANNOT authorize operational commands
        if src_type in ("teams_conversation", "case_context", "user_evidence", "observed_metric") or src_id.startswith("teams:"):
            continue
        if src_type in _AUTHORIZED_SOURCE_TYPES or (not src_type and not src_id.startswith("teams:")):
            # For governed knowledge, require lifecycle_status == approved and applicability_outcome == match
            if src_type == "governed_knowledge":
                meta = ev.metadata if isinstance(ev.metadata, dict) else {}
                l_status = str(meta.get("lifecycle_status", "")).lower()
                app_outcome = str(meta.get("applicability_outcome", "")).lower()
                if l_status != "approved":
                    logger.warning("Rejecting command source %r: lifecycle_status=%r != approved", src_id, l_status)
                    record_command_authority(
                        command="", source_id=src_id, decision="rejected",
                        reason=f"source lifecycle_status={l_status!r} != approved", stage="source",
                    )
                    continue
                if app_outcome != "match":
                    logger.warning("Rejecting command source %r: applicability_outcome=%r != match", src_id, app_outcome)
                    record_command_authority(
                        command="", source_id=src_id, decision="rejected",
                        reason=f"source applicability_outcome={app_outcome!r} != match", stage="source",
                    )
                    continue
                if not is_action_source(meta):
                    # Diagnostic knowledge (RCA / KB / ...) informs reasoning; it is never an action source.
                    record_command_authority(
                        command="", source_id=src_id, decision="rejected",
                        reason=f"source document_type={governed_document_type(meta)!r} is diagnostic knowledge, not an approved action source",
                        stage="source",
                    )
                    continue
            authorized_sources[src_id] = ev

    # Validate caller or candidate commands against governed evidence
    for cmd_item in caller_commands:
        if not isinstance(cmd_item, dict):
            continue
        raw_cmd = str(cmd_item.get("command") or "").strip()
        raw_src = str(cmd_item.get("source_id") or "").strip()
        if not raw_cmd:
            continue

        grounded = ground_command_candidate(
            candidate=cmd_item,
            evidence_references=evidence_references,
            authorized_sources=authorized_sources,
        )
        if not grounded:
            logger.warning(
                "Rejecting ungrounded candidate command: %r (not found in matching evidence %r)",
                raw_cmd,
                raw_src,
            )
            canonical = resolve_canonical_source_id(raw_src, evidence_references) or raw_src
            record_command_authority(
                command=raw_cmd,
                source_id=raw_src,
                decision="rejected",
                reason=(
                    "command text/template not found (or prohibited) in the cited selected section"
                    if canonical in authorized_sources
                    else "cited source is not selected, approved, applicability-MATCH governed evidence"
                ),
                stage="grounding",
            )
            continue

        authorized = authorize_grounded_command(
            grounded=grounded,
            candidate_meta=cmd_item,
            trusted_context=trusted_context,
        )
        if not authorized:
            logger.warning(
                "Rejecting unauthorized grounded command: %r (failed operational safety contract)",
                raw_cmd,
            )
            continue

        record_command_authority(
            command=authorized.command, source_id=authorized.source_id, decision="authorized",
            reason=f"{getattr(authorized.operation_type, 'value', authorized.operation_type)} grounded ({grounded.grounding_method})", stage="authorization",
        )
        key = (authorized.command, authorized.source_id)
        if key not in seen_commands:
            seen_commands.add(key)
            approved_list.append(
                ApprovedCommand(
                    command=authorized.command,
                    source_id=authorized.source_id,
                    procedure_section=authorized.procedure_section,
                    operation_type=authorized.operation_type,
                    target_confirmed=authorized.target_confirmed,
                    required_parameters=authorized.required_parameters,
                    validated_parameters=authorized.validated_parameters,
                    prerequisites=authorized.prerequisites,
                    restrictions=authorized.restrictions,
                    authorization_decision=authorized.authorization_decision,
                )
            )

    return approved_list


SELECTION_REMEDIATION_INSTRUCTION = (
    "SERVER EVIDENCE SELECTION CHECK: your previous response recommended a governed operational "
    "step (diagnostic_step.command / command_source), but no governed knowledge evidence has been "
    "explicitly selected in this run. Search results are AVAILABLE only and never authorize a step. "
    "Call `knowledge_select_evidence` now with the exact selection keys (knowledge_id, version_label, "
    "section_id) of the evidence you relied upon, then return your complete TechnicalAuthorityResponse "
    "again. If none of the available evidence supports the step, return outcome "
    "'insufficient_evidence' with no diagnostic_step.command."
)

def _selection_discovery_message(record: Optional[dict[str, Any]]) -> str:
    """Selection remediation after the server's own governed search (nothing was AVAILABLE before)."""
    results = [
        {"selection_key": i.get("selection_key"), "title": i.get("title"), "applicability_outcome": i.get("applicability_outcome")}
        for i in (record or {}).get("results") or []
    ]
    return (
        "SERVER EVIDENCE SELECTION CHECK: your previous response recommended a governed operational step, but no "
        "governed knowledge was AVAILABLE or selected in this run. The server has now run one governed knowledge_search "
        f"(status {(record or {}).get('status')}, query {(record or {}).get('query_text')!r}); AVAILABLE results: "
        f"{json.dumps(results)}. Search results are AVAILABLE only and never authorize a step. Call "
        "`knowledge_select_evidence` with the exact selection keys (knowledge_id, version_label, section_id) of the "
        "evidence you rely on, or with an empty list if none supports the step, then return your complete "
        "TechnicalAuthorityResponse again. Without selected evidence, return outcome 'insufficient_evidence' with no "
        "diagnostic_step.command."
    )


UNSELECTED_EVIDENCE_MISSING_INFORMATION = (
    "Governed recommendation withheld: the governed knowledge it relied upon was not explicitly "
    "selected via knowledge_select_evidence in this run."
)


def _parse_response_payload(content: Optional[types.Content]) -> Optional[dict[str, Any]]:
    """Best-effort JSON parse of a specialist response event (no authority is derived from it)."""
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return None
    text = "\n".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def is_governed_recommendation(payload: Optional[dict[str, Any]]) -> bool:
    """True when the specialist recommends a step that claims governed procedural backing:
    a `recommended` outcome whose diagnostic step carries a command or a command source."""
    if not isinstance(payload, dict):
        return False
    if payload.get("outcome") != TechnicalAuthorityOutcome.RECOMMENDED.value:
        return False
    step = payload.get("diagnostic_step")
    if not isinstance(step, dict):
        return False
    return bool(
        str(step.get("command") or "").strip()
        or str(step.get("command_source") or "").strip()
        or str(step.get("procedure_action_id") or "").strip()
    )


def requires_selection_remediation(run_id: Optional[str], payloads: list[dict[str, Any]]) -> bool:
    """A governed recommendation with an empty SELECTED set needs explicit selection.

    AVAILABLE evidence is never consulted here and is never promoted to SELECTED.
    """
    if not run_id:
        return False
    if not any(is_governed_recommendation(p) for p in payloads):
        return False
    return not snapshot_selected_knowledge_evidence(run_id)


def unselected_governed_recommendation_response() -> dict[str, Any]:
    """Fail-closed result when the one bounded selection remediation did not produce SELECTED evidence."""
    return TechnicalAuthorityResponse(
        outcome=TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE,
        technical_interpretation=(
            "A governed diagnostic step cannot be recommended because the supporting governed "
            "knowledge was not explicitly selected in this run."
        ),
        missing_information=[UNSELECTED_EVIDENCE_MISSING_INFORMATION],
        detail="Fail-closed: governed recommendation without explicitly selected evidence after one selection remediation attempt.",
    ).model_dump(mode="json")


SELECTION_CONTRACT_RECORD_KEY = "server_selection_contract"
"""Execution-record key holding server-observed evidence-selection facts for this TAE run."""

NON_OPERATIONAL_OUTCOMES = frozenset(
    {TechnicalAuthorityOutcome.ESCALATION_REQUIRED.value, TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value}
)
"""Outcomes that never carry a diagnostic step, command, or command source."""

SELECTION_CONTRACT_REMEDIATION_INSTRUCTION = (
    "SERVER EVIDENCE SELECTION CHECK: you searched governed knowledge in this run but did not complete "
    "the evidence-selection contract. If your response relies on retrieved governed evidence, call "
    "`knowledge_select_evidence` with the exact selection keys of that evidence. If none of the retrieved "
    "evidence is suitable, call `knowledge_select_evidence` with an empty list (`selections=[]`). Never "
    "select evidence merely to satisfy this check. Then return your complete TechnicalAuthorityResponse again."
)


def proposes_operational_step(payload: Optional[dict[str, Any]]) -> bool:
    """True when a specialist payload carries any operational step content: a command or command
    source under any outcome, or any diagnostic step under a non-operational outcome (which must
    never carry one). Evaluated on raw specialist payloads, before validation strips fields."""
    if not isinstance(payload, dict):
        return False
    step = payload.get("diagnostic_step")
    if not isinstance(step, dict):
        return False
    if (
        str(step.get("command") or "").strip()
        or str(step.get("command_source") or "").strip()
        or str(step.get("procedure_action_id") or "").strip()
    ):
        return True
    return payload.get("outcome") in NON_OPERATIONAL_OUTCOMES and any(
        str(v or "").strip() for v in step.values() if not isinstance(v, (list, dict))
    )


def requires_selection_contract_remediation(run_id: Optional[str], payloads: list[dict[str, Any]]) -> bool:
    """A non-operational outcome after a governed search, with neither a positive nor an explicit
    empty selection, has not completed the selection contract. One bounded remediation is allowed;
    it never selects anything on the specialist's behalf (AVAILABLE is never promoted)."""
    if not run_id or not payloads:
        return False
    if payloads[-1].get("outcome") not in NON_OPERATIONAL_OUTCOMES:
        return False
    if any(proposes_operational_step(p) for p in payloads):
        return False
    if not has_knowledge_run_state(run_id):
        return False
    if snapshot_selected_knowledge_evidence(run_id) or has_explicit_empty_knowledge_selection(run_id):
        return False
    # Unresolved applicability is answered by the existing targeted clarification path instead.
    return not get_available_unresolved_applicability_dimensions(run_id)


_MAX_OPERATOR_OBSERVATION_CHARS = 8000

OPERATIONAL_CONTROL_KEY = "operational_control"
"""Server-owned tool-result key: the Tranche 3 control-plane summary (policy decision, target
confirmation / approval requirement, execution availability). Never model-supplied."""


def _progression_conflict_response(conflict: ProgressionConflict) -> dict[str, Any]:
    """Fail closed on a stale Case progression write: nothing is overwritten; the operator retries
    against the newer progression (reloaded on the next turn)."""
    logger.warning("Troubleshooting progression conflict: %s", conflict)
    return TechnicalAuthorityResponse(
        outcome=TechnicalAuthorityOutcome.ERROR,
        technical_interpretation=(
            "The case investigation was updated from another session while this request was being evaluated. "
            "Nothing was overwritten; please repeat the request so it is evaluated against the latest investigation state."
        ),
        detail=f"progression_conflict: case progression is at version {conflict.current_version}, this turn read {conflict.expected_version}",
    ).model_dump(mode="json")


def _clarification_turn_response(
    run_id: Optional[str],
    kind: ClarificationTurnKind,
    *,
    fault_id: Optional[str],
    question: Any,
    text: str,
    confirmed_facts: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """Clarification follow-up / partial answer, rendered from the fault's server-recorded
    clarification: no specialist call, no governed retrieval, no step, no command (CLARIFICATION
    PROGRESSION, never operational progression)."""
    result = clarification_meta_result(kind, fault_id=fault_id, question=question, text=text, confirmed_facts=confirmed_facts)
    if run_id:
        from backend.api.troubleshooting_guidance_context import discard_troubleshooting_guidance

        record = dict(result)
        record["approved_commands_catalog"] = []
        record["verified_evidence"] = []
        record[SELECTION_CONTRACT_RECORD_KEY] = {
            "governed_search_performed": False,
            "explicit_negative_selection": False,
            "operational_step_proposed": False,
        }
        record_technical_authority_execution(run_id, record)
        discard_troubleshooting_guidance(run_id)
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event(
            {"stage": "clarification_continuity", "kind": kind.value, "fault_id": fault_id,
             "clarification": result[CLARIFICATION_CONTINUITY_KEY].get("clarification")}
        )
    except Exception:
        pass
    return result


def _run_evaluated_applicable_evidence(run_id: Optional[str], verified_evidence: list[Any]) -> bool:
    """True when this run's server evaluation found governed evidence APPLICABLE (MATCH): selected,
    or available. Such a run settles the applicability question itself (see
    `_record_fault_clarifications`), so the earlier request is not restated."""
    if any(
        isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge"
        and (ev.get("metadata") or {}).get("applicability_outcome") == "match"
        for ev in verified_evidence or []
    ):
        return True
    if not run_id:
        return False
    return any(
        get_evidence_applicability_outcome(
            run_id, (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)
        ) == "match"
        for item in get_available_knowledge_evidence(run_id).items
    )


def _attach_pending_clarification(
    tool_result: dict[str, Any],
    progression: TroubleshootingProgression,
    fault_id: str,
    confirmed_facts: dict[str, list[str]],
    *,
    run_id: Optional[str] = None,
    verified_evidence: Optional[list[Any]] = None,
) -> dict[str, Any]:
    """A non-operational result whose own run found no applicable governed evidence (e.g. nothing
    was retrieved) still states the fault's OPEN applicability clarification, from the server
    record -- the request is never lost because one turn found no evidence."""
    step = tool_result.get("diagnostic_step")
    if (
        tool_result.get("applicability_clarification")
        or tool_result.get("outcome") == TechnicalAuthorityOutcome.ESCALATION_REQUIRED.value
        or (isinstance(step, dict) and step.get("command"))
        or _run_evaluated_applicable_evidence(run_id, verified_evidence or [])
    ):
        return tool_result
    question = progression.pending_clarification(fault_id, ClarificationReason.APPLICABILITY)
    if question is None or not question.unresolved_fields:
        return tool_result
    out = dict(tool_result)
    out["applicability_clarification"] = {
        "confirmed_facts": {k: list(v) for k, v in confirmed_facts.items()},
        "missing_dimensions": question.unresolved_fields,
        "unconfirmed_facts": [],
        "text": render_clarification_request(question),
    }
    return out


_PRE_STEP_PHASES = frozenset({
    ProgressionPhase.NEW, ProgressionPhase.CONTEXT_BUILDING, ProgressionPhase.READY_FOR_DIAGNOSIS, ProgressionPhase.EVIDENCE_SELECTED,
})
"""Phases of a fault that has no step yet: an applicability clarification is then what it waits on."""


def _record_fault_clarifications(
    progression: TroubleshootingProgression,
    fault_id: str,
    tool_result: dict[str, Any],
    verified_evidence: list[Any],
    confirmed_facts: dict[str, list[str]],
    step_id: Optional[str],
    *,
    run_id: Optional[str] = None,
    requirement_id: Optional[str] = None,
) -> None:
    """Persist what THIS run's server evaluation still needs as the fault's pending clarification
    (structured fields, never prose), and close what it no longer needs:
      applicability   <- unresolved applicability dimensions of the evaluated governed evidence;
                         resolved when the evaluated governed evidence needs nothing more
      parameters      <- unresolved ProcedureAction parameters; resolved when the governed command
                         was rendered and authorized
    A run that evaluated nothing leaves the clarification as it was.

    The applicability clarification is the fault's resumable dependency on its own: it is recorded
    with the requirement and governed sources it must settle whether or not a step is pending and
    whatever outcome label the specialist chose. A fault with no step yet is BLOCKED on it."""
    applicability = tool_result.get("applicability_clarification")
    governed = [
        ev for ev in verified_evidence or [] if isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge"
    ]
    if isinstance(applicability, dict) and applicability.get("missing_dimensions"):
        question = progression.record_clarification(
            fault_id, ClarificationReason.APPLICABILITY, applicability["missing_dimensions"],
            text=str(applicability.get("text") or ""), originating_step_id=step_id, known_values=confirmed_facts,
            requirement_id=requirement_id, source_identities=_clarification_source_identities(verified_evidence, run_id), run_id=run_id,
        )
        fault = progression.faults.get(fault_id)
        if question is not None and step_id is None and fault is not None and fault.phase in _PRE_STEP_PHASES:
            progression.set_phase(fault_id, ProgressionPhase.BLOCKED_MISSING_INFORMATION, reason="applicability clarification required")
        _trace_applicability_clarification("recorded", question)
    elif governed and all((ev.get("metadata") or {}).get("applicability_outcome") == "match" for ev in governed):
        question = progression.pending_clarification(fault_id, ClarificationReason.APPLICABILITY)
        if question is not None:
            known = {normalize_dimension_key(k): v for k, v in confirmed_facts.items()}
            values = {f: known[normalize_dimension_key(f)] for f in question.unresolved_fields if known.get(normalize_dimension_key(f))}
            progression.resolve_clarification_fields(question.question_id, values, actor="system")
            if question.status is ClarificationStatus.OPEN:
                progression.close_clarification(
                    question.question_id, ClarificationStatus.SUPERSEDED, reason="the evaluated governed procedure applies without them"
                )
            _settle_applicability_clarification(progression, fault_id, question, verified_evidence, run_id)

    parameters = tool_result.get("parameter_clarification")
    step = tool_result.get("diagnostic_step")
    if isinstance(parameters, dict):
        fields = [*parameters.get("missing_parameters", []), *parameters.get("ambiguous_parameters", []), *parameters.get("conflicting_parameters", [])]
        progression.record_clarification(
            fault_id, ClarificationReason.MISSING_PARAMETER, fields, text=str(parameters.get("text") or ""), originating_step_id=step_id
        )
    elif isinstance(step, dict) and step.get("command"):
        question = progression.pending_clarification(fault_id, ClarificationReason.MISSING_PARAMETER)
        if question is not None:
            resolution = tool_result.get(PROCEDURE_ACTION_RESOLUTION_KEY) or {}
            bound = {
                p["name"]: [str(p["value"])]
                for p in resolution.get("parameters") or []
                if isinstance(p, dict) and p.get("name") in question.unresolved_fields and p.get("value") not in (None, "")
            }
            same_step = question.originating_step_id in (None, step_id)
            if same_step:
                progression.resolve_clarification_fields(question.question_id, bound, actor="system")
            if question.status is ClarificationStatus.OPEN:
                progression.close_clarification(
                    question.question_id,
                    ClarificationStatus.RESOLVED if same_step else ClarificationStatus.SUPERSEDED,
                    reason="governed command rendered and authorized" if same_step else "a later step's governed command was authorized",
                )


_RESUMPTION_QUERY_MAX_CHARS = 300
RESUMED_RETRIEVAL_RULE = (
    "The operator just supplied the applicability context the governed procedure required. The server ran a "
    "fresh governed knowledge_search in THIS run under that context (results above, AVAILABLE only, with their "
    "recomputed applicability). Nothing is selected: call knowledge_select_evidence with the exact selection_key "
    "of the evidence you rely on (or an empty list if none applies); you may search again. Then continue the "
    "pending step, using its governed procedure action when one applies."
)


ACQUISITION_RETRIEVAL_RULE = (
    "The operator asked how to obtain the evidence the pending step needs. The server ran a fresh governed "
    "knowledge_search in THIS run (results above, AVAILABLE only, applicability evaluated now). Nothing is selected: "
    "call knowledge_select_evidence with the exact selection_key of the evidence you rely on (or an empty list if none "
    "applies). Then resolve the pending step with its governed procedure action (pending_step.governed_acquisition) "
    "when its source is selected and applicable; the server re-resolves and re-authorizes it in this run."
)


CONTINUATION_RETRIEVAL_RULE = (
    "The operator asked to go on with the pending step without supplying its result. The server ran a fresh governed "
    "knowledge_search in THIS run (results above, AVAILABLE only, applicability evaluated now); evidence selected in an "
    "earlier turn does not carry over. Nothing is selected: call knowledge_select_evidence with the exact selection_key "
    "of the evidence you rely on (or an empty list if none applies). Then resolve the pending step with its governed "
    "procedure action (pending_step.governed_acquisition) when its source is selected and applicable; the server "
    "re-resolves and re-authorizes it in this run. Without its result the pending step remains the current step."
)


RESUMED_INVESTIGATION_RULE = (
    "The operator just supplied the applicability context this investigation was waiting on (`resumes`: the answered "
    "clarification); no executable diagnostic step is pending. The server ran a fresh governed knowledge_search in THIS run under "
    "that context (results above, AVAILABLE only, applicability recomputed); evidence selected or actions issued in an "
    "earlier turn do not carry over. Nothing is selected: call knowledge_select_evidence with the exact selection_key of "
    "the evidence you rely on (or an empty list if none applies). Then resume the investigation with exactly ONE next "
    "diagnostic step, using its governed procedure action (procedure_action_catalog) when one applies. Do not ask again "
    "for the context just supplied."
)


def _resumed_clarification_view(question: OpenQuestion) -> dict[str, Any]:
    """What the specialist is told about the clarification that resumed the investigation: identity
    only (which sources were unresolved), never a selection, an action or authority."""
    return {
        "clarification_id": question.question_id,
        "answered": {k: list(v) for k, v in question.resolved_values.items()},
        "previously_unresolved_sources": [i.selection_key() for i in question.source_identities],
        "authority": (
            "none -- identity only. Select a source in THIS run only if it applies; nothing from an earlier turn is "
            "selected, issued or authorized."
        ),
    }


def _trace_applicability_clarification(event: str, question: Optional[OpenQuestion], **extra: Any) -> None:
    if question is None:
        return
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({"stage": "applicability_clarification", "event": event, **applicability_clarification_trace_view(question), **extra})
    except Exception:
        pass


def _clarification_source_identities(verified_evidence: list[Any], run_id: Optional[str]) -> list[EvidenceIdentity]:
    """Governed sources whose applicability THIS run left unresolved: the SELECTED ones (unknown /
    partial match), else the AVAILABLE ones (identity only)."""
    unresolved = [
        ev for ev in verified_evidence or []
        if isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge"
        and (ev.get("metadata") or {}).get("applicability_outcome") in ("unknown", "partial_match")
    ]
    if unresolved:
        return identities_of(unresolved)
    return [i for i, outcome in _run_governed_view(run_id)[1].items() if str(outcome or "").lower() in ("unknown", "partial_match")]


def _evaluated_applicability(question: OpenQuestion, verified_evidence: list[Any], run_id: Optional[str]) -> Optional[str]:
    """Applicability THIS run evaluated for the sources the clarification concerned (their SELECTED
    evaluation, else their AVAILABLE one); with no recorded or re-found source, the run's selected
    governed evidence. `match` when any of them applies, `mismatch` when all were excluded."""
    selected: dict[EvidenceIdentity, str] = {}
    for ev in verified_evidence or []:
        if isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge":
            identity = identity_of(ev)
            outcome = str((ev.get("metadata") or {}).get("applicability_outcome") or "").lower()
            if identity is not None and outcome:
                selected[identity] = outcome
    available = _run_governed_view(run_id)[1]
    outcomes = [
        o for o in (selected.get(i) or str(available.get(i) or "").lower() for i in question.source_identities) if o
    ] or list(selected.values())
    if not outcomes:
        return None
    if "match" in outcomes:
        return "match"
    if all(o == "mismatch" for o in outcomes):
        return "mismatch"
    return outcomes[0]


def _settle_applicability_clarification(
    progression: TroubleshootingProgression, fault_id: str, question: OpenQuestion, verified_evidence: list[Any], run_id: Optional[str]
) -> Optional[str]:
    """Record what THIS run evaluated after the clarification was answered (audit only) and, on MATCH
    with no applicability clarification left open, clear the now-stale `applicability_unresolved`
    blockers of the fault's requirements."""
    outcome = _evaluated_applicability(question, verified_evidence, run_id)
    progression.record_clarification_outcome(question.question_id, outcome, run_id)
    released: list[str] = []
    if outcome == "match" and progression.pending_clarification(fault_id, ClarificationReason.APPLICABILITY) is None:
        released = progression.release_applicability_blockers(
            fault_id, f"applicability resolved to MATCH (clarification {question.question_id})"
        )
    _trace_applicability_clarification(
        "settled", question, released_requirements=released, stale_blockers=stale_applicability_blockers(progression, fault_id)
    )
    return outcome


def _known_source_available(run_id: Optional[str], known: Optional[KnownGovernedAcquisition]) -> bool:
    """The known acquisition's source is AVAILABLE in THIS run (exact identity; never an earlier
    run's selection). A legacy record without structured identity is never assumed available."""
    if known is None or known.identity is None:
        return False
    return known.identity in _run_governed_view(run_id)[1]


async def _resume_governed_retrieval(
    run_id: Optional[str],
    progression: TroubleshootingProgression,
    fault_id: str,
    clarification: Any,
    confirmed_facts: dict[str, list[str]],
) -> dict[str, Any]:
    """An APPLICABILITY clarification of this fault became RESOLVED in this turn: run ONE fresh governed
    `knowledge_search` in the current run (same tool, same run-scoped evidence store, applicability
    evaluated under the confirmed context) before the specialist runs. The query is built from
    server-owned investigation state (the fault's symptom summary and the step the clarification
    blocked). Results are AVAILABLE evidence only: nothing is selected, no earlier run's evidence is
    reused and no authority is granted. Recorded in the run's diagnostic trace."""
    step = progression.step(clarification.originating_step_id) if clarification.originating_step_id else None
    return await _server_governed_search(
        run_id, progression, fault_id, step, confirmed_facts,
        stage="retrieval_resumption", reason="applicability_clarification_resolved",
        rule=RESUMED_RETRIEVAL_RULE, clarification_id=clarification.question_id,
    )


async def _server_governed_search(
    run_id: Optional[str],
    progression: TroubleshootingProgression,
    fault_id: str,
    step: Any,
    confirmed_facts: dict[str, list[str]],
    *,
    stage: str,
    reason: str,
    rule: str,
    clarification_id: Optional[str] = None,
    subject: Optional[str] = None,
) -> dict[str, Any]:
    """ONE fresh governed `knowledge_search` performed by the server in the current run (same tool,
    same run-scoped evidence store, applicability evaluated under the confirmed context). The query
    comes from server-owned state only: the fault's symptom summary and the pending step's objective
    (or the open requirement's description). Results are AVAILABLE only: nothing is selected, no
    earlier run's evidence is reused, no authority is granted."""
    fault = progression.faults.get(fault_id)
    parts = [fault.symptom_summary if fault else "", step.objective if step else (subject or "")]
    query = " ".join(dict.fromkeys(p.strip() for p in parts if p and p.strip()))[:_RESUMPTION_QUERY_MAX_CHARS]
    record: dict[str, Any] = {
        "stage": stage,
        "reason": reason,
        "clarification_id": clarification_id,
        "fault_id": fault_id,
        "applicability_context": {k: list(v) for k, v in confirmed_facts.items()},
        "query_text": query,
        "previous_selected_sources": list(step.selected_evidence_ids) if step else [],
    }
    if not run_id or not query:
        record["status"] = "skipped"
    else:
        from backend.tools.knowledge.tools import knowledge_search

        result = await knowledge_search(query_text=query)
        if not isinstance(result, dict) or "error" in result:
            record["status"] = "search_failed"
            record["results"] = []
        else:
            items = [i for i in result.get("items") or [] if isinstance(i, dict)]
            record["status"] = "searched"
            record["results"] = items
    record["rule"] = rule
    _trace_retrieval_resumption(record)
    return record


def _resumption_trace_view(record: dict[str, Any]) -> dict[str, Any]:
    view = {k: v for k, v in record.items() if k not in ("results", "rule")}
    view["available"] = [
        {
            **{k: (i.get("selection_key") or {}).get(k) for k in ("knowledge_id", "version_label", "section_id")},
            "applicability_outcome": i.get("applicability_outcome"),
        }
        for i in record.get("results") or []
    ]
    return view


def _trace_retrieval_resumption(record: dict[str, Any]) -> None:
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event(_resumption_trace_view(record))
    except Exception:
        pass


def _record_resumed_retrieval_outcome(run_id: Optional[str], record: dict[str, Any], verified_evidence: list[Any]) -> None:
    """After the specialist ran: what it explicitly selected from the resumed retrieval, with the
    freshly evaluated applicability (diagnostics only)."""
    governed = [ev for ev in verified_evidence if isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge"]
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({
            "stage": f"{record.get('stage') or 'retrieval_resumption'}_outcome",
            "clarification_id": record.get("clarification_id"),
            "governed_search_performed": bool(run_id) and has_knowledge_run_state(run_id),
            "selected": [{"source_id": ev.get("source_id"), "applicability_outcome": (ev.get("metadata") or {}).get("applicability_outcome")} for ev in governed],
        })
    except Exception:
        pass


def _run_governed_view(run_id: Optional[str]) -> tuple[list[EvidenceIdentity], dict[EvidenceIdentity, Optional[str]]]:
    """(SELECTED identities, AVAILABLE identity -> applicability) of THIS run (runtime facts), keyed
    by the exact structured tuple (display strings: `.canonical`)."""
    if not run_id:
        return [], {}
    selected = identities_of(snapshot_selected_knowledge_evidence(run_id))
    available: dict[EvidenceIdentity, Optional[str]] = {}
    for item in get_available_knowledge_evidence(run_id).items:
        identity = identity_of(item)
        if identity is not None:
            available[identity] = get_evidence_applicability_outcome(run_id, identity.key)
    return selected, available


def _acquisition_remediation_need(
    run_id: Optional[str],
    known: Optional[KnownGovernedAcquisition],
    rule: Optional[ContinuationRule],
    binding: Optional[AcquisitionBinding],
    payloads: list[dict[str, Any]],
    progression: TroubleshootingProgression,
    fault_id: str,
    *,
    acquisition_wording: bool = False,
) -> Optional[str]:
    """'discovery' when a governed acquisition is being considered and THIS run performed no
    governed search; 'selection' when the known acquisition's source is AVAILABLE with
    applicability MATCH in this run but the specialist made no decision on it; else None. An
    acquisition still awaiting an OPEN applicability clarification is answered by that
    clarification, never by forced discovery."""
    if not run_id:
        return None
    payload = payloads[-1] if payloads else None
    if known is not None:
        if known.clarification_open:
            return None
        effective = effective_rule(rule, payload, progression, fault_id, known, payloads)
        if effective is None or (model_chose(payload, known) and (payload or {}).get("outcome") == TechnicalAuthorityOutcome.RECOMMENDED.value):
            return None
        if not has_knowledge_run_state(run_id):
            return "discovery"
        selected, available = _run_governed_view(run_id)
        if known.identity is None or known.identity in selected:
            return None  # a legacy record with no structured source identity cannot be pointed at
        return "selection" if str(available.get(known.identity) or "").lower() == "match" else None
    if (
        binding is not None
        and acquisition_wording
        and not has_knowledge_run_state(run_id)
        and progression.pending_clarification(fault_id, ClarificationReason.APPLICABILITY) is None
    ):
        return "discovery"
    return None


def _acquisition_remediation_message(
    kind: str, known: Optional[KnownGovernedAcquisition], record: Optional[dict[str, Any]], requirement: Any
) -> str:
    """One bounded instruction. It names WHAT must be decided; it never selects for the specialist."""
    need = f" (open evidence requirement {requirement.requirement_id}: {requirement.description})" if requirement is not None else ""
    lines = []
    if kind == "discovery":
        results = [
            {"selection_key": i.get("selection_key"), "title": i.get("title"), "applicability_outcome": i.get("applicability_outcome")}
            for i in (record or {}).get("results") or []
        ]
        lines.append(
            "A governed acquisition is being considered for the pending evidence" + need + ", but no governed knowledge_search "
            "was performed in this run. The server has now run one (status "
            f"{(record or {}).get('status')}, query {(record or {}).get('query_text')!r}); AVAILABLE results: {json.dumps(results)}."
        )
    else:
        lines.append(
            "The pending step's known governed acquisition source"
            + need + f" is AVAILABLE in this run with applicability MATCH (selection_key {json.dumps(known.selection_key if known else None)}), "
            "but you made no selection decision on it."
        )
    if known is not None:
        lines.append(
            f"Its known procedure action is {known.procedure_action_id} (identity only, no authority): if you rely on that source, "
            "select it with knowledge_select_evidence and set diagnostic_step.procedure_action_id to that id; the server re-resolves "
            "it from this run's selected evidence and Command Authority re-authorizes it."
        )
    lines.append(
        "Decide explicitly: call knowledge_select_evidence with the exact selection_key values you rely on, or with an empty "
        "list if none applies. Then return your complete final JSON response. Never present a command from an earlier turn."
    )
    return " ".join(lines)


def _issue_chosen_known_action(
    raw_result: dict[str, Any], known: KnownGovernedAcquisition, *, run_id: Optional[str], evidence: list[EvidenceReference]
) -> Optional[dict[str, Any]]:
    """The specialist chose the server-known action id (told to it as identity only) without
    listing this run's catalog. The server re-derives that exact action from THIS run's SELECTED
    evidence (`reconstruct_known_action`: same id, canonical source and template; approved,
    applicability MATCH, diagnostic read) and issues it -- the same entry `procedure_action_catalog`
    would issue -- so the normal resolver and Command Authority decide. Only the server-derived
    action is issued; evidence is never selected on the specialist's behalf. None when the
    specialist did not choose the known id."""
    if not model_chose(raw_result, known) or raw_result.get("outcome") != TechnicalAuthorityOutcome.RECOMMENDED.value:
        return None
    action, reason = reconstruct_known_action(known, evidence)
    issued = action is not None and bool(run_id)
    if issued:
        record_issued_actions(run_id, [action])
    return {"issued": issued, "reconstruction": reason}


def _fault_closed(progression: ProgressionController) -> bool:
    fault = progression.progression.faults.get(progression.fault_id)
    return fault is not None and fault.resolution.value in ("resolved", "escalated")


def _ungrounded_command(command: Optional[str], source: Optional[str], selected_evidence: list[Any]) -> bool:
    """A model-written single diagnostic command that names no governed action of THIS run's SELECTED
    evidence (`names_governed_action`): no selected procedure instructs it, so it is no acquisition
    method. Not judged here: a composed command (the single-invocation boundary and the Progression
    Controller reject several actions in one step) and a state-changing command (a remediation is
    governed by the remediation gate and Command Authority, not by evidence acquisition)."""
    text = str(command or "").strip()
    return (
        bool(text) and not is_multi_action_command(text) and state_change_class(text) is None
        and not names_governed_action(text, source, selected_evidence)
    )


def _governed_command_check(run_id: Optional[str]) -> GovernedCommand:
    """(command, cited source) -> whether a model-written command may count as an acquisition method:
    anything but an ungrounded command (`_ungrounded_command`) over THIS run's SELECTED evidence,
    re-read at each call (a remediation message may change the selection)."""
    def _check(command: str, source: Optional[str]) -> bool:
        return not _ungrounded_command(command, source, build_server_validated_evidence(run_id, None, []) if run_id else [])

    return _check


async def _forecast_gap(
    payloads: list[dict[str, Any]],
    progression: ProgressionController,
    fault_id: str,
    run_id: Optional[str],
    governed_command: Optional[GovernedCommand] = None,
    *,
    actions_declined: bool = False,
) -> Optional[tuple[Optional[dict[str, Any]], Any, AcquisitionDecision]]:
    """(payload, requirement, decision) when the specialist's latest proposal names acquirable evidence
    WITHOUT a governed method and the server's own acquisition decision for it -- forecast on a SHADOW
    copy of the progression, nothing recorded -- is a governed acquisition GAP; None otherwise. A
    method is judged on the RAW payloads too (an integrity callback may already have stripped a refused
    command -- a refused governed method is not a gap); with `governed_command`, a command that names
    no governed action of this run is no method at all."""
    if not run_id or proposes_method(payloads, governed_command):
        return None
    payload = payloads[-1] if payloads else None
    candidate, from_mechanism = proposed_branch_requirement(
        payload, fault_id, governed_command, attempted_command=attempted_ungrounded_command(payloads, governed_command)
    )
    if candidate is None or live_sources_for(candidate.capability):
        return None
    shadow = progression.progression.model_copy(deep=True)
    requirement, _ = _continue_requirement(shadow, candidate, run_id, followup=from_mechanism)
    discovery = _discovery_state(run_id, progression.progression, fault_id)
    discovery.actions_declined = actions_declined
    decision = await decide_acquisition(requirement, progression=shadow, action=GovernedActionState(), discovery=discovery, run_id=run_id)
    if decision.outcome is not AcquisitionOutcome.GAP:
        return None
    return payload, requirement, decision


async def _plan_gap_continuation(
    payloads: list[dict[str, Any]],
    progression: ProgressionController,
    fault_id: str,
    run_id: Optional[str],
    governed_command: GovernedCommand,
    *,
    actions_declined: bool,
) -> Optional[dict[str, Any]]:
    """The specialist's proposal still ends in a governed acquisition GAP after the bounded governed
    recovery path (current catalog choice, governed alternatives, one server search): the exhausted
    branch to record and to continue past -- ONE continuation request, never a loop. Nothing is chosen,
    selected or authorized here."""
    forecast = await _forecast_gap(payloads, progression, fault_id, run_id, governed_command, actions_declined=actions_declined)
    if forecast is None:
        return None
    payload, requirement, decision = forecast
    return {
        "payload": payload, "requirement": requirement, "requirement_id": requirement.requirement_id,
        "repeated": decision.repeated, "gap_reason": requirement.blocking_reason.value if requirement.blocking_reason else None,
    }


async def _plan_gap_recovery(
    payloads: list[dict[str, Any]],
    progression: ProgressionController,
    fault_id: str,
    run_id: Optional[str],
    confirmed_facts: dict[str, list[str]],
    governed_command: Optional[GovernedCommand] = None,
) -> Optional[dict[str, Any]]:
    """Forecast whether the specialist's proposal ends in a governed acquisition GAP (`_forecast_gap`)
    and, if so, the valid governed alternatives of this fault. Alternatives come only from THIS run's
    governed evidence (gap_recovery.viable_governed_alternatives); when there are none, the server
    runs ONE fresh governed search (AVAILABLE only, server-owned query) and re-evaluates. None when
    the proposal names a method, is not acquirable evidence, or would not be a gap."""
    forecast = await _forecast_gap(payloads, progression, fault_id, run_id, governed_command)
    if forecast is None:
        return None
    payload, requirement, decision = forecast
    alternatives, excluded = viable_governed_alternatives(run_governed_evidence(run_id), progression.progression, fault_id)
    discovery: Optional[str] = None
    if not alternatives:
        record = await _server_governed_search(
            run_id, progression.progression, fault_id, None, confirmed_facts,
            stage="gap_recovery_discovery", reason="governed_acquisition_gap", rule="",
        )
        discovery = str(record.get("status"))
        alternatives, excluded = viable_governed_alternatives(run_governed_evidence(run_id), progression.progression, fault_id)
    return {
        "payload": payload, "requirement": requirement, "requirement_id": requirement.requirement_id, "repeated": decision.repeated,
        "gap_reason": requirement.blocking_reason.value if requirement.blocking_reason else None,
        "alternatives": alternatives, "excluded": excluded, "discovery": discovery, "offered": False,
    }


_PROVENANCE_NOT_PROVEN = frozenset({
    ProcedureActionResolutionStatus.UNKNOWN_ACTION,
    ProcedureActionResolutionStatus.SOURCE_NOT_SELECTED,
    ProcedureActionResolutionStatus.SOURCE_NOT_AUTHORITATIVE,
    ProcedureActionResolutionStatus.PROHIBITED,
})
"""Resolution outcomes that do NOT prove the action was issued in this run and re-derives from this
run's SELECTED, approved, MATCH evidence (every other status passed those gates)."""


def _enforce_deferred_citation_check(raw_result: dict[str, Any], action_resolution: Any, run_id: Optional[str]) -> dict[str, Any]:
    """Complete a citation check the integrity callback deferred to ProcedureAction resolution.
    Applies only when THIS run's last integrity-callback decision deferred it."""
    callbacks = [d for d in integrity_decisions(run_id) if d.get("phase") == "integrity_callback"]
    if not callbacks or callbacks[-1].get("action") != ACTION_RESOLUTION_DEFERRED:
        return raw_result
    action_id = callbacks[-1].get("procedure_action_id")
    status = getattr(action_resolution, "status", None)
    action = getattr(action_resolution, "action", None)
    if action_resolution is None or status in _PROVENANCE_NOT_PROVEN or action is None:
        out = dict(raw_result)
        out["outcome"] = TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value
        out["diagnostic_step"] = None
        missing = [m for m in out.get("missing_information") or [] if isinstance(m, str)]
        if INTEGRITY_UNRESOLVED_CITATION_TEXT not in missing:
            missing.append(INTEGRITY_UNRESOLVED_CITATION_TEXT)
        out["missing_information"] = missing
        record_integrity_decision({
            "phase": "post_resolution", "original_outcome": "recommended", "final_outcome": out["outcome"],
            "procedure_action_id": action_id, "check_triggered": "action_provenance_not_proven",
            "action": "fail_closed_insufficient_evidence",
            "reason": f"citations unresolved and ProcedureAction resolution {getattr(status, 'value', status) or 'not performed'}",
        })
        return out
    record_integrity_decision({
        "phase": "post_resolution", "original_outcome": "recommended",
        "final_outcome": getattr(raw_result.get("outcome"), "value", raw_result.get("outcome")),
        "procedure_action_id": action.action_id, "check_triggered": "citation_replaced_by_action_provenance",
        "action": "proceed", "reason": f"ProcedureAction re-derived from SELECTED evidence ({status.value}); source {action.source.canonical_source_id}",
    })
    return {**raw_result, "verified_evidence_citations": [action.source.canonical_source_id]}


ACTION_CHOICE_KEY = "action_choice"
"""Server-owned tool-result / trace key (never model-supplied)."""


def _operational_step_disposition(
    *, proposed: bool, tool_result: Any, action_resolution: Any, refused_command: Optional[str],
    progression_decision: ProposalDecision, run_id: Optional[str],
) -> str:
    """Audit: what became of an operational step the specialist proposed in this run."""
    if not proposed:
        return "none"
    step = tool_result.get("diagnostic_step") if isinstance(tool_result, dict) else None
    if isinstance(step, dict) and str(step.get("command") or "").strip():
        return "accepted"
    if any(d.get("original_outcome") == TechnicalAuthorityOutcome.RECOMMENDED.value
           and d.get("final_outcome") == TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value for d in integrity_decisions(run_id)):
        return "integrity_rejected"
    if action_resolution is not None and action_resolution.status is not ProcedureActionResolutionStatus.RESOLVED:
        return "action_resolution_rejected"
    if (action_resolution is not None and action_resolution.candidate is not None) or refused_command:
        return "command_authority_rejected"
    if progression_decision not in ACCEPTED_DECISIONS and progression_decision is not ProposalDecision.NONE:
        return "progression_rejected"
    return "accepted_without_command"


def _plan_action_choice(
    payloads: list[dict[str, Any]],
    progression: ProgressionController,
    fault_id: str,
    run_id: Optional[str],
    governed_command: Optional[GovernedCommand] = None,
) -> Optional[dict[str, Any]]:
    """The specialist named an acquirable evidence need WITHOUT any method (judged on raw payloads
    too; with `governed_command`, a command naming no governed action of this run is no method) while
    THIS run's SELECTED, approved, MATCH evidence offers valid diagnostic-read actions the fault has
    not performed: the candidate set for one explicit choice. None otherwise."""
    if not run_id or proposes_method(payloads, governed_command):
        return None
    requirement, _ = proposed_branch_requirement(
        payloads[-1] if payloads else None, fault_id, governed_command,
        attempted_command=attempted_ungrounded_command(payloads, governed_command),
    )
    if requirement is None:
        return None
    actions = _selected_action_alternatives(run_id, progression.progression, fault_id)
    if not actions:
        return None
    return {"requirement": requirement, "actions": actions, "offered": True, "chosen": None, "declined": None}


def _issue_offered_alternative(
    raw_result: dict[str, Any], gap_recovery: Optional[dict[str, Any]], run_id: Optional[str], evidence: list[EvidenceReference]
) -> Optional[str]:
    """The specialist chose one of the governed alternatives the server offered: that exact action is
    re-derived from THIS run's SELECTED evidence (the catalog's own rule) and issued, so the normal
    resolver, target gate and Command Authority decide. Never issued from AVAILABLE evidence; never
    another id. Returns the issued id, or None."""
    if not gap_recovery or not gap_recovery.get("offered") or not run_id:
        return None
    step = raw_result.get("diagnostic_step") if isinstance(raw_result.get("diagnostic_step"), dict) else {}
    chosen = str(step.get("procedure_action_id") or "").strip()
    if not chosen or chosen not in {a.action.action_id for a in gap_recovery["alternatives"]}:
        return None
    gap_recovery["chosen"] = chosen
    action = next((a for a in build_procedure_action_catalog(evidence)[0] if a.action_id == chosen), None)
    if action is None or action.action_type is not ProcedureActionType.DIAGNOSTIC_READ:
        gap_recovery["chosen_issued"] = False
        return None
    record_issued_actions(run_id, [action])
    gap_recovery["chosen_issued"] = True
    return chosen


async def _record_branch_gap(
    gap_recovery: dict[str, Any], progression: ProgressionController, fault_id: str, run_id: Optional[str], evidence: list[EvidenceReference]
) -> Optional[AcquisitionDecision]:
    """Persist the exhausted branch: the gapped requirement and its gap stay in the progression (and
    the audit) whatever the specialist does next. The real acquisition decision is taken here on the
    authoritative progression (the forecast only ran on a copy), from the SAME proposal and with the
    same requirement derivation as the server's normal path (a proposed step becomes its evidence need)."""
    payload = gap_recovery.get("payload") or {}
    if payload.get("outcome") != TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value:
        requirement = gap_recovery["requirement"]
        payload = {
            "outcome": TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value,
            "required_evidence": [{"kind": requirement.kind.value, "description": requirement.description, "capability": requirement.capability}],
        }
    # The forecast already proved this need had no governed method in this run's SELECTED evidence;
    # evidence selected afterwards for ANOTHER branch (an offered alternative) is not a method for it.
    decision, _, _ = await _decide_evidence_acquisition(
        payload, ProposalDecision.NONE, action_resolution=None, blocked_action=None, refused_command=None,
        refreshed_evidence=evidence, progression=progression.progression, fault_id=fault_id, run_id=run_id,
        actions_declined=True,
    )
    if decision is not None and decision.outcome is AcquisitionOutcome.GAP:
        _persist_acquisition(progression, decision, None, ProposalDecision.NONE, None, None)
        gap_recovery["requirement_id"] = decision.requirement.requirement_id
        gap_recovery["gap_id"] = decision.gap.gap_id if decision.gap else None
        return decision
    return None


def _gap_recovery_view(gap_recovery: dict[str, Any]) -> dict[str, Any]:
    """Diagnostics / server-owned tool-result view (identifiers only, never a command)."""
    return {
        "requirement_id": gap_recovery.get("requirement_id"),
        "gap_id": gap_recovery.get("gap_id"),
        "gap_reason": gap_recovery.get("gap_reason"),
        "repeated": gap_recovery.get("repeated"),
        "alternatives": [a.view() for a in gap_recovery.get("alternatives") or []],
        "excluded": len(gap_recovery.get("excluded") or []),
        "discovery": gap_recovery.get("discovery"),
        "offered": gap_recovery.get("offered"),
        "chosen": gap_recovery.get("chosen"),
        "chosen_issued": gap_recovery.get("chosen_issued"),
        "terminal": gap_recovery.get("terminal"),
    }


def _gap_continuation_view(
    branch_gaps: list[AcquisitionDecision], acquisition: Optional[AcquisitionDecision], gap_continuation: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Server-owned record of the governed acquisition gaps recorded in this run that the final answer
    continues past: every recorded branch gap whose requirement the final acquisition decision does not
    itself address (a final gap, or a method found, for the SAME requirement is rendered by its own
    path). Requirement descriptions are server-derived evidence descriptions, never commands."""
    final_requirement_id = acquisition.requirement.requirement_id if acquisition is not None else None
    gaps: list[dict[str, Any]] = []
    for decision in branch_gaps:
        requirement_id = decision.requirement.requirement_id
        if requirement_id == final_requirement_id or any(g["requirement_id"] == requirement_id for g in gaps):
            continue
        gaps.append({
            "requirement_id": requirement_id,
            "gap_id": decision.gap.gap_id if decision.gap is not None else None,
            "reason": decision.gap.gap_reason.value if decision.gap is not None else None,
            "description": decision.requirement.description,
        })
    return {"gaps": gaps, "continued": gap_continuation is not None}


def _record_gap_continuation(view: dict[str, Any], tool_result: dict[str, Any], acquisition: Optional[AcquisitionDecision]) -> None:
    """Diagnostics (identifiers only): the gaps continued past, and what the final answer did next."""
    step = tool_result.get("diagnostic_step") if isinstance(tool_result.get("diagnostic_step"), dict) else {}
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({
            "stage": "gap_continuation",
            "continued": view.get("continued"),
            "gaps": [{k: g.get(k) for k in ("requirement_id", "gap_id", "reason")} for g in view.get("gaps") or []],
            "final_outcome": getattr(tool_result.get("outcome"), "value", tool_result.get("outcome")),
            "final_requirement_id": acquisition.requirement.requirement_id if acquisition is not None else None,
            "final_acquisition": acquisition.outcome if acquisition is not None else None,
            "governed_command_presented": bool(str(step.get("command") or "").strip()),
            "procedure_action_id": step.get("procedure_action_id"),
        })
    except Exception:
        pass


def _reconcile_known_acquisition(
    raw_result: dict[str, Any],
    known: KnownGovernedAcquisition,
    rule: Optional[ContinuationRule],
    *,
    run_id: Optional[str],
    progression: TroubleshootingProgression,
    fault_id: str,
    pending: Optional[TroubleshootingStep],
    evidence: list[EvidenceReference],
    remediation: Optional[str],
    raw_payloads: Optional[list[dict[str, Any]]] = None,
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    """Model proposal + server-owned acquisition state -> reconciled proposal. When the continuation
    applies and the specialist did not propose the known governed action, the pending step is
    proposed with that action -- ONLY if it is reconstructed from THIS run's SELECTED evidence (same
    deterministic id, canonical source and template, approved + MATCH, diagnostic read). The normal
    resolver and Command Authority then decide; nothing earlier is reused as authority. When the
    specialist itself chose the known id, that same reconstruction is issued for it, whether or not
    a continuation rule applies (`_issue_chosen_known_action`)."""
    issuance = _issue_chosen_known_action(raw_result, known, run_id=run_id, evidence=evidence)
    effective = effective_rule(rule, raw_result, progression, fault_id, known, raw_payloads or [])
    if effective is None or pending is None:
        if issuance is not None:
            try:
                from backend.tools.knowledge.diagnostic_trace import record_operational_event

                record_operational_event({"stage": "known_action_issuance", "procedure_action_id": known.procedure_action_id, **issuance})
            except Exception:
                pass
        return raw_result, None
    selected, available = _run_governed_view(run_id)
    step = raw_result.get("diagnostic_step") if isinstance(raw_result.get("diagnostic_step"), dict) else {}
    trace: dict[str, Any] = {
        "stage": "acquisition_continuity",
        "rule": effective.value,
        **known.view(),
        "governed_search_performed": bool(run_id) and has_knowledge_run_state(run_id),
        "selected_sources": [i.canonical for i in selected],
        "known_source_applicability": available.get(known.identity) if known.identity is not None else None,
        "remediation": remediation,
        "model_proposal": {
            "outcome": getattr(raw_result.get("outcome"), "value", raw_result.get("outcome")),
            "procedure_action_id": step.get("procedure_action_id"),
            "had_command": bool(str(step.get("command") or "").strip()),
        },
    }
    if issuance is not None:
        trace["outcome"] = "model_continued"
        trace.update(issuance)
        return raw_result, trace
    action, reason = reconstruct_known_action(known, evidence)
    trace["reconstruction"] = reason
    if action is None:
        trace["outcome"] = "not_reconstructable"
        return raw_result, trace
    if run_id:
        # The same deterministic catalog entry the catalog tool issues, rebuilt from this run's
        # SELECTED evidence (the resolver's "issued this turn" gate); no other id is issued.
        record_issued_actions(run_id, [action])
    trace["outcome"] = "server_reconciled"
    return reconciled_proposal(known, pending, progression.requirement(known.requirement_id), raw_result), trace


def _record_continuation(
    kind: ContinuationKind, rule: Optional[ContinuationRule], known: Optional[KnownGovernedAcquisition], known_available: bool,
    *, fault_id: Optional[str] = None, pending: Any = None, discovery: Optional[str] = None,
    clarification: Optional[OpenQuestion] = None,
) -> None:
    """Diagnostics of this turn's continuation decision (identifiers only). `source`: what server
    state the continuation rests on -- the pending step's known acquisition, or the fault's
    applicability clarification on its own."""
    source = "pending_step" if known is not None else "open_applicability_clarification" if rule is not None else "none"
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({
            "stage": "continuation", "kind": kind.value, "rule": rule.value if rule else None, "fault_id": fault_id,
            "source": source,
            "open_applicability_clarification": applicability_clarification_trace_view(clarification) if clarification is not None else None,
            "pending_step": {"step_id": pending.step_id, "status": pending.status.value} if pending is not None else None,
            "known_procedure_action_id": known.procedure_action_id if known else None,
            "known_clarification_open": known.clarification_open if known else None,
            "known_source_available_in_run": known_available,
            "discovery": discovery,
        })
    except Exception:
        pass


RESPONSE_COMPLETENESS_KEY = "response_completeness"
"""Server-owned tool-result / execution-record key (never model-supplied)."""


def _record_result_binding(progression: ProgressionController, fault_id: str, pending_before: Optional[TroubleshootingStep]) -> None:
    """Diagnostics of this turn's operator-result binding (identifiers and statuses only)."""
    binding = progression.result_binding
    if binding is None and pending_before is None:
        return
    consumption = progression.result_consumption()
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({
            "stage": "result_binding", "fault_id": fault_id, "turn_kind": progression.turn_kind.value,
            "pending_before": pending_before.step_id if pending_before is not None else None,
            **(binding or {"binding": "none", "step_id": pending_before.step_id if pending_before is not None else None}),
            "pending_after_binding": consumption["pending_after_binding"],
            "consumed_step_ids": consumption["consumed_step_ids"],
        })
    except Exception:
        pass


def _record_continuation_state(progression: ProgressionController, fault_id: str, recorded_step: Optional[TroubleshootingStep]) -> None:
    """Diagnostics: the post-binding progression this turn continued from, and where it ended."""
    consumption = progression.result_consumption()
    if not consumption["consumed_step_ids"] and not consumption["result_provided"]:
        return
    fault = progression.progression.faults.get(fault_id)
    pending = progression.pending_step()
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({
            "stage": "continuation_state", "fault_id": fault_id,
            "consumed_step_ids": consumption["consumed_step_ids"],
            "pending_after_binding": consumption["pending_after_binding"],
            "next_step_id": recorded_step.step_id if recorded_step is not None and recorded_step.step_id not in consumption["consumed_step_ids"] else None,
            "pending_now": pending.step_id if pending is not None else None,
            "progression_phase": fault.phase.value if fault is not None else None,
        })
    except Exception:
        pass


def _record_specialist_invocation(specialist: str, run_id: Optional[str]) -> None:
    """Diagnostics: this specialist ran, and whether the server's operational routing forced it."""
    try:
        from backend.agents.team_manager.operational_routing import record_specialist_invocation

        record_specialist_invocation(specialist, run_id)
    except Exception:
        pass


def _server_forced_route(run_id: Optional[str]) -> bool:
    """The server routed THIS run through the governed operational pipeline (operational_routing.py)."""
    try:
        from backend.agents.team_manager.operational_routing import operational_route_forced

        return operational_route_forced(run_id)
    except Exception:
        return False


def _response_completeness(
    tool_result: dict[str, Any],
    *,
    rule: Optional[ContinuationRule],
    run_id: Optional[str],
    progression: ProgressionController,
    fault_id: str,
    remediation: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Completeness of a turn the SERVER continued (a continuation rule applied, or the server routed
    the turn through the governed operational pipeline). Actionable investigation state = the fault
    is open and THIS run's SELECTED, approved, MATCH evidence offers valid governed diagnostic actions
    the fault has not performed -- or, on a server-routed turn, simply an open fault. Such a turn must
    end with a next governed step (or the pending step's result request), a required clarification, a
    governed gap, an escalation or a resolved conclusion -- never with nothing. The pending step (if
    any) is recorded so the final-answer boundary can request its result. Deterministic; confers no
    authority (consumed by the final-answer boundary)."""
    forced = _server_forced_route(run_id)
    if rule is None and not forced:
        return None
    step = tool_result.get("diagnostic_step") if isinstance(tool_result.get("diagnostic_step"), dict) else None
    outcome = tool_result.get("outcome")
    elements: list[str] = []
    if outcome == TechnicalAuthorityOutcome.RECOMMENDED.value and step is not None and str(step.get("action") or "").strip():
        elements.append("next_step")
    if tool_result.get("applicability_clarification") or tool_result.get("parameter_clarification"):
        elements.append("clarification")
    acquisition_view = tool_result.get(EVIDENCE_ACQUISITION_KEY)
    continued_gaps = (tool_result.get(GAP_CONTINUATION_KEY) or {}).get("gaps") if isinstance(tool_result.get(GAP_CONTINUATION_KEY), dict) else None
    if (isinstance(acquisition_view, dict) and acquisition_view.get("response_text")) or continued_gaps:
        elements.append("governed_gap")
    if outcome == TechnicalAuthorityOutcome.ESCALATION_REQUIRED.value:
        elements.append("escalation")
    if (
        step is None and bool(run_id) and has_explicit_empty_knowledge_selection(run_id)
        and not snapshot_selected_knowledge_evidence(run_id)
    ):
        # Governed discovery ran and the specialist explicitly found no applicable procedure: a
        # meaningful governed outcome (rendered from the validated record), not an empty answer.
        elements.append("no_applicable_governed_procedure")
    closed = _fault_closed(progression)
    if closed:
        elements.append("resolved")
    actions = [] if closed else _selected_action_alternatives(run_id, progression.progression, fault_id)
    required = bool(actions) or (forced and not closed)
    # The pending-result candidate is re-read from the AUTHORITATIVE post-binding progression (never a
    # snapshot taken before this turn's result binding). A step whose result this turn consumed is
    # excluded structurally -- it can never be requested again in the same turn.
    consumption = progression.result_consumption()
    consumed = set(consumption["consumed_step_ids"])
    pending = progression.pending_step()
    if pending is not None and pending.status is not StepStatus.PRESENTED:
        pending = None  # only a step actually presented to the operator awaits its result
    if pending is not None and (pending.step_id in consumed or pending.result is not None):
        pending = None
    candidate_rejected = (
        pending is not None and progression.turn_kind is TurnKind.UNVALIDATED_RESULT
        and (progression.result_binding or {}).get("step_id") == pending.step_id
    )
    known_result = progression.progression.step(progression.known_result_step_id) if progression.known_result_step_id else None
    record = {
        "rule": rule.value if rule is not None else None,
        "forced_route": forced,
        "required": required,
        "valid_actions": [a.action.action_id for a in actions],
        "elements": elements,
        "satisfied": not required or bool(elements),
        "remediation": remediation,
        # Server-recorded pending step (objective and expected evidence only -- never a command: its
        # command was authorized in an earlier run, which is not current authority).
        "pending_step": {
            "step_id": pending.step_id,
            "objective": _without_step_commands(pending.objective, pending),
            "expected_evidence": _without_step_commands(pending.expected_evidence, pending),
            # This message carried output for it that could not be attributed to it (not bound).
            "candidate_rejected": candidate_rejected,
            "known_result_objective": _without_step_commands(known_result.objective, known_result) if known_result is not None else None,
        } if pending is not None and pending.fault_id == fault_id else None,
        # PendingResultConsumption (server-owned): the steps whose result THIS turn bound. The
        # final-answer boundary never renders any of them as awaiting its result.
        "consumed_step_ids": sorted(consumed),
        "result_consumption": consumption,
        # The pending step the final validated proposal resolves / re-presents (server decision), if any.
        "presented_pending_step_id": progression.presented_pending_step_id
        if progression.presented_pending_step_id not in consumed else None,
        # The final proposal re-presents a step this turn consumed (stale state): never presentable.
        "presents_consumed_step": progression.presented_pending_step_id in consumed,
        # The fault's open governed acquisition gaps (requirement descriptions only).
        "open_gaps": [
            g.requirement_description for g in progression.progression.acquisition_gaps
            if g.fault_id == fault_id and g.status is GapStatus.OPEN
        ][:5],
    }
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({"stage": "response_completeness", **record})
    except Exception:
        pass
    return record


def _resume_on_procedure_action(raw_result: dict[str, Any], *, run_id: Optional[str], evidence: list[EvidenceReference]) -> dict[str, Any]:
    """Clarification-resumed turn: a step the specialist expressed as legacy command text that is
    exactly ONE diagnostic-read ProcedureAction of THIS run's SELECTED, approved, MATCH evidence (its
    cited source, template rendered exactly: `authorized_command_action`) continues on the
    ProcedureAction path -- the server path a known blocked action takes. The id is issued from this
    run's selection, the model's command text is dropped, the resolver renders the command and Command
    Authority re-authorizes it. Anything else stays on the legacy grounding path unchanged. No
    selection, no choice and no authority is made here."""
    step = raw_result.get("diagnostic_step")
    if raw_result.get("outcome") != TechnicalAuthorityOutcome.RECOMMENDED.value or not isinstance(step, dict):
        return raw_result
    if str(step.get("procedure_action_id") or "").strip():
        return raw_result
    command, source = str(step.get("command") or "").strip(), str(step.get("command_source") or "").strip()
    action = authorized_command_action(command, source, evidence) if command and source else None
    if action is None:
        return raw_result
    if run_id:
        record_issued_actions(run_id, [action])
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({"stage": "resume_procedure_action", "procedure_action_id": action.action_id, "source": action.source.canonical_source_id})
    except Exception:
        pass
    return {**raw_result, "diagnostic_step": {**step, "procedure_action_id": action.action_id, "command": None, "command_source": None}}


def _without_step_commands(text: Optional[str], step: TroubleshootingStep) -> str:
    """The step's own prose with its command / governed template removed (and the prose repaired):
    a request for a pending step's result never repeats the command an earlier run authorized."""
    from backend.agents.technical_authority_engineer.validation import repair_removed_command_references

    out = str(text or "")
    commands = [step.command, step.identity.template if step.identity else None,
                step.blocked_candidate.normalized_command if step.blocked_candidate else None]
    for command in {" ".join(str(c).split()) for c in commands if c and str(c).strip()}:
        pattern = re.compile(r"(?<![\w-])" + r"\s+".join(re.escape(part) for part in command.split()) + r"(?![\w-])", re.IGNORECASE)
        out = pattern.sub("", out)
    return repair_removed_command_references(out, command_removed=out != str(text or ""))


def _actionable_proposal(payloads: list[dict[str, Any]]) -> bool:
    """The specialist's final message proposes something the operator can act on: a governed method
    (command / procedure action, judged on raw payloads too) or an escalation."""
    if proposes_method(payloads):
        return True
    payload = payloads[-1] if payloads else None
    return isinstance(payload, dict) and payload.get("outcome") == TechnicalAuthorityOutcome.ESCALATION_REQUIRED.value


def _record_resume_completeness(record: Optional[dict[str, Any]]) -> None:
    if not record:
        return
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({"stage": "resume_completeness", **record})
    except Exception:
        pass


def _record_acquisition_continuity(trace: Optional[dict[str, Any]]) -> None:
    if not trace:
        return
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event(dict(trace))
    except Exception:
        pass


def _latest_proposed_command(
    payloads: list[dict[str, Any]], fallback_command: Optional[str], fallback_source: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """The command the specialist itself proposed in its final message (latest command-bearing
    payload), before any sanitization; falls back to the parsed result's own fields."""
    for payload in reversed(payloads or []):
        step = payload.get("diagnostic_step") if isinstance(payload, dict) else None
        if isinstance(step, dict) and str(step.get("command") or "").strip():
            return str(step["command"]).strip(), (str(step.get("command_source") or "").strip() or None)
    return fallback_command, fallback_source


def _selected_action_alternatives(run_id: Optional[str], progression: Optional[TroubleshootingProgression], fault_id: Optional[str]) -> list[Any]:
    """Valid diagnostic-read ProcedureActions of THIS run's SELECTED, approved, MATCH evidence that the
    fault has not performed (never AVAILABLE-only evidence)."""
    if not run_id or progression is None or not fault_id:
        return []
    return viable_governed_alternatives(build_server_validated_evidence(run_id, None, []), progression, fault_id)[0]


def _discovery_state(
    run_id: Optional[str], progression: Optional[TroubleshootingProgression] = None, fault_id: Optional[str] = None
) -> DiscoveryState:
    """What governed discovery THIS run actually performed (runtime facts, not model claims), and the
    valid governed diagnostic actions its SELECTED evidence offers for the fault."""
    if not run_id:
        return DiscoveryState()
    available_identities = [
        (identity, get_evidence_applicability_outcome(run_id, identity.key)) for identity in identities_of(get_available_knowledge_evidence(run_id).items)
    ]
    selected = [identity.canonical for identity in identities_of(snapshot_selected_knowledge_evidence(run_id))]
    searches = run_search_log(run_id)
    return DiscoveryState(
        governed_search_performed=has_knowledge_run_state(run_id) and bool(searches), searches=searches,
        available=[(identity.canonical, outcome) for identity, outcome in available_identities],
        available_identities=available_identities, selected=selected,
        selected_actions=[a.action.action_id for a in _selected_action_alternatives(run_id, progression, fault_id)],
    )


def _norm_command(command: Any) -> str:
    return " ".join(str(command or "").split()).casefold()


def _authorized_legacy_action(
    step: Any, authorized_catalog: list[ApprovedCommand], evidence: list[EvidenceReference]
) -> Optional[tuple[str, str, Any]]:
    """Legacy free-text path: (normalized command, canonical source, ProcedureAction) when the step's
    command was AUTHORIZED by Command Authority in this run (approved catalog, one source) and is
    exactly one diagnostic-read ProcedureAction re-derived from THIS run's SELECTED evidence
    (`authorized_command_action`). Continuity metadata only: it never authorizes or presents."""
    command = _norm_command(step.get("command")) if isinstance(step, dict) else ""
    if not command:
        return None
    cited = str(step.get("command_source") or "").strip() or None
    sources = {c.source_id for c in authorized_catalog if _norm_command(c.command) == command and (cited is None or c.source_id == cited)}
    if len(sources) != 1:
        return None
    source = next(iter(sources))
    action = authorized_command_action(step["command"], source, evidence)
    return (command, source, action) if action is not None else None


def _legacy_action_for(step: Any, legacy: Optional[tuple[str, str, Any]]) -> Any:
    """The legacy continuity action, only for a step that still presents exactly that authorized command."""
    if legacy is None or not isinstance(step, dict):
        return None
    command, source, action = legacy
    if _norm_command(step.get("command")) != command or (step.get("command_source") or source) != source:
        return None
    return action


def _governed_action_state(
    step: Optional[dict[str, Any]],
    tool_result: dict[str, Any],
    action_resolution: Any,
    blocked_action: Any,
    refused_command: Optional[str],
    evidence: list[EvidenceReference],
    legacy_action: Any = None,
    refused_ungrounded: bool = False,
) -> GovernedActionState:
    """What THIS run established about the step's governed action (its CURRENT authority).
    `legacy_action`: the continuity identity of an authorized legacy command (`_legacy_action_for`).
    `refused_ungrounded`: the refused command names no governed action of this run
    (`names_governed_action`) -- it is no acquisition method, so it blocks nothing: the step's
    evidence need goes through the normal decision (governed alternatives / gap), never presented
    as an operator task."""
    applicability = {e.source_id: (e.metadata or {}).get("applicability_outcome") for e in evidence if e.source_type == "governed_knowledge"}
    command = str((step or {}).get("command") or "").strip() or None
    state = GovernedActionState()
    action = getattr(action_resolution, "action", None)
    if action is None and blocked_action is None and command:
        action = legacy_action
    if action is not None:
        state.procedure_action_id = action.action_id
        state.command_template = action.command_template
        state.canonical_source_id = action.source.canonical_source_id
        state.source_identity = identity_of(action.source)
        state.parameters = [p.name for p in action.parameters]
    elif blocked_action is not None:
        state.procedure_action_id = blocked_action.procedure_action_id
        state.command_template = blocked_action.normalized_command
        state.canonical_source_id = blocked_action.source_id
        state.source_identity = blocked_action.identity
    elif command:
        state.canonical_source_id = (step or {}).get("command_source")
    state.applicability = applicability.get(state.canonical_source_id or "")
    if command:
        state.authorized_command = command
    elif blocked_action is not None:
        state.blocking_reason = GapReason.APPLICABILITY_UNRESOLVED
    elif tool_result.get("parameter_clarification"):
        state.blocking_reason = GapReason.REQUIRED_PARAMETER_MISSING
    elif action is not None or (refused_command and not refused_ungrounded):
        state.blocking_reason = GapReason.ACTION_NOT_AUTHORIZED
    return state


async def _decide_evidence_acquisition(
    tool_result: dict[str, Any],
    decision: ProposalDecision,
    *,
    action_resolution: Any,
    blocked_action: Any,
    refused_command: Optional[str],
    refreshed_evidence: list[EvidenceReference],
    progression: TroubleshootingProgression,
    fault_id: str,
    run_id: Optional[str],
    pending_step: Optional[TroubleshootingStep] = None,
    continuity_trace: Optional[dict[str, Any]] = None,
    legacy_action: Optional[tuple[str, str, Any]] = None,
    actions_declined: bool = False,
    refused_ungrounded: bool = False,
) -> tuple[Optional[AcquisitionDecision], Optional[dict[str, Any]], dict[str, Any]]:
    """Returns (decision, the proposed step it applies to, updated result). Only an ACCEPTED step
    proposal or a non-operational outcome is evaluated (a rejected proposal changes nothing).
    `pending_step`: the step a PENDING_STEP_RESOLVED decision resolves -- its own open requirement is
    the structural continuity (wording never splits it into a new requirement). `legacy_action`: the
    continuity identity of an authorized legacy command (`_authorized_legacy_action`).
    `refused_ungrounded`: the refused command names no governed action of this run (no method)."""
    step = tool_result.get("diagnostic_step") if isinstance(tool_result.get("diagnostic_step"), dict) else None
    outcome = tool_result.get("outcome")
    out = dict(tool_result)
    # Commands / procedures / tools are never operator-facing missing information.
    evidence_items, system_items = split_missing_information(list(out.get("missing_information") or []))
    if system_items:
        out["missing_information"] = evidence_items
    discovery = _discovery_state(run_id, progression, fault_id)
    discovery.actions_declined = actions_declined
    if continuity_trace and continuity_trace.get("outcome") == "not_reconstructable" and is_definitive_rejection(continuity_trace.get("reconstruction")):
        # This run's selected governed evidence positively no longer supports the known action.
        discovery.rejected_actions[str(continuity_trace.get("procedure_action_id"))] = str(continuity_trace.get("reconstruction"))
    if step is not None and outcome == TechnicalAuthorityOutcome.RECOMMENDED.value:
        if decision not in ACCEPTED_DECISIONS:
            return None, None, out
        requirement = requirement_from_step(step, fault_id, attempted_command=bool(refused_command))
        own = (
            progression.requirement(pending_step.evidence_requirement_id)
            if decision is ProposalDecision.PENDING_STEP_RESOLVED and pending_step is not None
            else None
        )
        if own is not None and own.status is RequirementStatus.UNSATISFIED and own.fault_id == fault_id:
            requirement, continuity = refine_requirement(own, requirement, run_id), "pending_step_requirement"
        else:
            requirement, continuity = _continue_requirement(progression, requirement, run_id, followup=False)
        state = _governed_action_state(
            step, out, action_resolution, blocked_action, refused_command, refreshed_evidence, _legacy_action_for(step, legacy_action),
            refused_ungrounded=refused_ungrounded,
        )
        result = await decide_acquisition(requirement, progression=progression, action=state, discovery=discovery, run_id=run_id)
        result.continuity = continuity
    elif outcome == TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value:
        proposals = [p for p in out.get("required_evidence") or [] if isinstance(p, dict)]
        requirements = [r for r in (requirement_from_proposal(p, fault_id) for p in proposals) if r is not None]
        acquirable = [r for r in requirements if r.kind in (EvidenceKind.DIAGNOSTIC_RESULT, EvidenceKind.LIVE_OPERATIONAL_CONTEXT)]
        derived_from_mechanism = False
        if not acquirable and system_items:
            # The specialist said it lacks a METHOD to obtain some evidence: that evidence is the
            # requirement (never "the operator must provide a command").
            description = next((d for d in (evidence_from_mechanism_item(i) for i in system_items) if d), None) or "; ".join(evidence_items)[:300]
            if description:
                derived = requirement_from_proposal({"kind": "diagnostic_result", "description": description}, fault_id)
                acquirable = [derived] if derived is not None else []
                derived_from_mechanism = bool(acquirable)
        if not acquirable:
            if system_items:
                out[EVIDENCE_ACQUISITION_KEY] = {"outcome": "none", "system_items_withheld": system_items}
            return None, None, out
        # Only a requirement derived from "I lack a method for X" (no typed content of its own) may
        # fall back to the single outstanding need; typed content must match on its own.
        requirement, continuity = _continue_requirement(progression, acquirable[0], run_id, followup=derived_from_mechanism)
        result = await decide_acquisition(requirement, progression=progression, action=GovernedActionState(), discovery=discovery, run_id=run_id)
        result.continuity = continuity
        if result.outcome is AcquisitionOutcome.FAILED and result.requirement.blocking_reason is GapReason.DISCOVERY_INCOMPLETE:
            # Nothing searched in this turn: no conclusion is drawn (the request stays as stated).
            out[EVIDENCE_ACQUISITION_KEY] = {**result.view(), "response_text": None, "system_items_withheld": system_items}
            return result, None, out
    else:
        return None, None, out
    view = result.view()
    if system_items:
        view["system_items_withheld"] = system_items
    out[EVIDENCE_ACQUISITION_KEY] = view
    if result.outcome in (AcquisitionOutcome.GAP, AcquisitionOutcome.FAILED, AcquisitionOutcome.SATISFIED) and result.response_text:
        # No operator task, no command: the response is rendered from the server decision.
        out = withdraw_rejected_command(out)
        out["outcome"] = TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value
        out["diagnostic_step"] = None
        out["missing_information"] = [result.requirement.description] if result.outcome is not AcquisitionOutcome.SATISFIED else []
    return result, step, out


def _continue_requirement(
    progression: TroubleshootingProgression, candidate: Any, run_id: Optional[str], *, followup: bool
) -> tuple[Any, Optional[str]]:
    """Server-owned continuity: continue the matching OPEN requirement of this fault (refining its
    wording, never its identity) instead of creating a new one."""
    existing, rule = match_open_requirement(progression, candidate, mechanism_followup=followup)
    if existing is None:
        return candidate, None
    return refine_requirement(existing, candidate, run_id), rule


_GOVERNED_METHOD_PENDING = frozenset({GapReason.APPLICABILITY_UNRESOLVED, GapReason.REQUIRED_PARAMETER_MISSING})


def _settle_open_gap(progression: TroubleshootingProgression, acquisition: AcquisitionDecision) -> None:
    """A requirement that now has a usable method (or was obtained) RESOLVES its open gap; an
    identified governed method that only awaits applicability / a parameter SUPERSEDES it. A refused
    or unauthorized command is not a method: the gap stays open. History and attempts are kept."""
    requirement = acquisition.requirement
    if progression.open_gap(requirement.requirement_id) is None:
        return
    selected = requirement.selected_acquisition
    if acquisition.outcome is AcquisitionOutcome.SATISFIED or (
        acquisition.outcome is AcquisitionOutcome.PRESENT and selected is not None and selected.availability is CandidateAvailability.AVAILABLE
    ):
        method = selected.acquisition_type.value if selected is not None else "evidence obtained"
        progression.close_gaps(requirement.requirement_id, GapStatus.RESOLVED, f"acquisition method available: {method}")
    elif (
        selected is not None
        and (selected.procedure_action_id or selected.canonical_source_id)
        and selected.blocking_reason in _GOVERNED_METHOD_PENDING
    ):
        progression.close_gaps(
            requirement.requirement_id, GapStatus.SUPERSEDED, f"governed method identified, awaiting {selected.blocking_reason.value}"
        )


def _persist_acquisition(
    progression: ProgressionController,
    acquisition: AcquisitionDecision,
    step_data: Optional[dict[str, Any]],
    decision: ProposalDecision,
    check_id: Optional[str],
    recorded_step: Any,
) -> None:
    """The progression records the requirement with the step it belongs to (presented, blocked by
    a gap, acquired by the server) -- the step state machine is the existing one."""
    requirement = acquisition.requirement
    if acquisition.outcome is AcquisitionOutcome.GAP and acquisition.gap is not None:
        if not acquisition.gap_is_new and acquisition.attempt is not None:
            progression.progression.record_gap_attempt(acquisition.gap, acquisition.attempt, requirement.blocking_reason or acquisition.gap.gap_reason)
        if step_data is not None:
            progression.record_gap_step(decision, step_data, requirement, acquisition.gap, check_id)
        else:
            progression.progression.add_requirement(requirement)
            progression.progression.record_acquisition_gap(acquisition.gap)
        return
    _settle_open_gap(progression.progression, acquisition)
    if acquisition.outcome is AcquisitionOutcome.SATISFIED and step_data is not None and acquisition.acquired is not None:
        source, content = acquisition.acquired
        progression.record_acquired_step(decision, step_data, requirement, source, content, check_id)
        return
    progression.link_requirement(recorded_step, requirement)


def _supporting_evidence(tool_result: dict[str, Any], verified_evidence: list[Any], blocked_action: Any) -> list[str]:
    """Canonical ids of the SELECTED evidence that materially grounds what is presented:
    the presented command's source, an applicability-blocked governed action's source, citations
    that resolve to selected evidence, and -- for a presented step with no command -- the selected
    procedures the recommendation relied on. A non-operational outcome is grounded only by what it
    cites."""
    from backend.agents.technical_authority_engineer.validation import resolve_canonical_source_id

    governed = [ev for ev in verified_evidence if isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge"]
    selected = {ev.get("source_id") for ev in governed}
    supporting: list[str] = []

    def _add(source_id: Optional[str]) -> None:
        if source_id and source_id in selected and source_id not in supporting:
            supporting.append(source_id)

    step = tool_result.get("diagnostic_step") if isinstance(tool_result.get("diagnostic_step"), dict) else None
    if step is not None:
        _add(step.get("command_source"))
        if blocked_action is not None:
            _add(blocked_action.source_id)
    for citation in tool_result.get("verified_evidence_citations") or []:
        if isinstance(citation, str):
            _add(citation if citation in selected else resolve_canonical_source_id(citation, governed))
    if step is not None and tool_result.get("outcome") == TechnicalAuthorityOutcome.RECOMMENDED.value and not supporting:
        # A presented step without its own command grounding relied on the selected procedures.
        for ev in governed:
            _add(ev.get("source_id"))
    return supporting


def _evidence_identities(source_ids: list[str], verified_evidence: list[Any]) -> list[EvidenceIdentity]:
    """Structured identities of THIS run's governed evidence items with these display ids (a display
    id denotes exactly one item within a run: build_server_validated_evidence withholds collisions).
    Read from each item's server-built metadata; never parsed from the id."""
    governed = [ev for ev in verified_evidence if isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge"]
    return identities_of(ev for source_id in source_ids for ev in governed if ev.get("source_id") == source_id)


def _link_blocked_step_clarification(progression: TroubleshootingProgression, fault_id: str, step: Any) -> None:
    """Bind a blocked governed step to the open applicability clarification that blocks it."""
    blocked = getattr(step, "blocked_candidate", None)
    if blocked is None or blocked.clarification_id or getattr(step, "status", None) is not StepStatus.BLOCKED_BY_CLARIFICATION:
        return
    question = progression.pending_clarification(fault_id, ClarificationReason.APPLICABILITY)
    if question is not None:
        blocked.clarification_id = question.question_id
        question.originating_step_id = question.originating_step_id or step.step_id


def _execution_observation_evidence(troubleshooting_state: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for rec in getattr(troubleshooting_state, "diagnostic_history", None) or []:
        if not rec.execution_ids or not rec.observed_result:
            continue
        evidence.append(
            {
                "source_id": f"execution:{rec.execution_ids[-1]}",
                "source_type": "observed_metric",
                "title": f"Observed output of controlled diagnostic read: {rec.grounded_command or rec.action}",
                "content_snippet": rec.observed_result[:4000],
                "metadata": {
                    "evidence_kind": "controlled_read_execution",
                    "check_id": rec.check_id,
                    "procedure_action_id": rec.procedure_action_id,
                    "execution_id": rec.execution_ids[-1],
                },
            }
        )
    return evidence


GOVERNED_RECOMMENDATION_INVARIANT_KEY = "governed_recommendation_invariant"
"""Server-owned tool-result key recording how the SELECTED-evidence invariant treated a
`recommended` outcome that had no SELECTED governed evidence and no ProcedureAction catalog."""

MANUAL_OBSERVATION_KEY = "manual_observation_only"
"""Server-owned flag: the surviving `recommended` step is a manual/observational check that claims
no governed operational authority (no command, source, action id, or operational instruction)."""

NO_SELECTED_GOVERNED_EVIDENCE_NOTE = (
    "No approved, applicable governed procedure was selected for this request, so no governed "
    "operational step can be recommended."
)
MANUAL_OBSERVATION_RESTRICTION = (
    "[Manual observation only: no approved governed procedure was selected; no command is provided.]"
)


def _governed_operational_claims(step: Any) -> list[str]:
    """Why a recommended step needs governed evidence (empty list = genuinely manual/observational)."""
    if not isinstance(step, dict) or not str(step.get("action") or "").strip():
        return ["no_valid_diagnostic_step"]
    claims: list[str] = []
    for field in ("command", "command_source", "procedure_action_id"):
        if str(step.get(field) or "").strip():
            claims.append(field)
    if step.get("parameter_values"):
        claims.append("parameter_values")
    if any(
        isinstance(r, str) and (r.startswith("[Command stripped") or NO_APPROVED_COMMAND_FOR_ACTION_TEXT in r)
        for r in step.get("restrictions") or []
    ):
        claims.append("stripped_governed_command")
    from backend.agents.technical_authority_engineer.synthesis_boundary import _has_unauthorized_instruction

    for field in ("action", "expected_evidence"):
        text = str(step.get(field) or "")
        if text and _has_unauthorized_instruction(text, set(), [], []):
            claims.append(f"operational_instruction_in_{field}")
    return claims


def enforce_selected_evidence_invariant(
    result: Any, selected_evidence: list[EvidenceReference], run_id: Optional[str]
) -> Any:
    """A governed operational recommendation cannot survive when SELECTED governed evidence is
    empty AND the ProcedureAction catalog is empty. Such a `recommended` result is normalized to
    `escalation_required` (when the specialist gave an escalation reason) or
    `insufficient_evidence` (the applicability clarification path still applies downstream).
    A genuinely manual/observational step (no command, source, action id, parameters or
    operational instruction) may remain `recommended`, explicitly marked manual-only.
    Never selects evidence and never consults AVAILABLE evidence."""
    if not isinstance(result, dict) or result.get("outcome") != TechnicalAuthorityOutcome.RECOMMENDED.value:
        return result
    if any(ev.source_type == "governed_knowledge" for ev in selected_evidence):
        return result
    catalog, _ = build_procedure_action_catalog(selected_evidence)
    if catalog or issued_action_ids(run_id):
        return result

    step = result.get("diagnostic_step")
    claims = _governed_operational_claims(step)
    out = dict(result)
    if not claims:
        manual = dict(step)
        manual["command"] = None
        manual["command_source"] = None
        manual["restrictions"] = list(manual.get("restrictions") or []) + [MANUAL_OBSERVATION_RESTRICTION]
        out["diagnostic_step"] = manual
        out[MANUAL_OBSERVATION_KEY] = True
        out[GOVERNED_RECOMMENDATION_INVARIANT_KEY] = {"normalized": False, "kind": "manual_observation"}
    else:
        target = (
            TechnicalAuthorityOutcome.ESCALATION_REQUIRED
            if str(result.get("escalation_reason") or "").strip()
            else TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE
        )
        out["outcome"] = target.value
        out["diagnostic_step"] = None
        missing = [m for m in (result.get("missing_information") or []) if isinstance(m, str)]
        if NO_SELECTED_GOVERNED_EVIDENCE_NOTE not in missing:
            missing.append(NO_SELECTED_GOVERNED_EVIDENCE_NOTE)
        out["missing_information"] = missing
        out[MANUAL_OBSERVATION_KEY] = False
        out[GOVERNED_RECOMMENDATION_INVARIANT_KEY] = {
            "normalized": True,
            "from": TechnicalAuthorityOutcome.RECOMMENDED.value,
            "to": target.value,
            "claims": claims,
        }
        logger.warning(
            "Technical Authority Engineer recommendation normalized to %s: no SELECTED governed evidence and empty ProcedureAction catalog (claims=%s)",
            target.value,
            claims,
        )
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({"stage": "invariant", **out[GOVERNED_RECOMMENDATION_INVARIANT_KEY]})
    except Exception:
        pass
    return out


def _record_target_gate(resolution: ProcedureActionResolution, facts: list[Any], fault_id: Optional[str]) -> None:
    """Diagnostics of the state-change target gate: the case facts, each target decision and its
    provenance (identifiers only)."""
    validation = resolution.target_validation
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({
            "stage": "target_gate",
            "procedure_action_id": resolution.action_id,
            "operation": resolution.action.operation_type.value if resolution.action else None,
            "source": resolution.action.source.canonical_source_id if resolution.action else None,
            "fault_id": fault_id,
            "declared_targets": [t.view() for t in (resolution.action.targets if resolution.action else [])],
            "case_target_facts": [f.view() for f in facts][:50],
            "passed": bool(validation and validation.passed),
            "targets": [d.view() for d in (validation.targets if validation else [])],
            "reason": validation.reason if validation else None,
            "resolution": resolution.status.value,
            "instance_semantics": resolution.action.instance_semantics.value if resolution.action else None,
            "condition_check": dict(resolution.condition_check) if resolution.condition_check else None,
            "governed_conditions": len(resolution.action.conditions) if resolution.action else 0,
        })
    except Exception:
        pass


def _apply_procedure_action(
    raw_result: dict[str, Any],
    *,
    run_id: Optional[str],
    tool_context: ToolContext,
    evidence: list[EvidenceReference],
    observed_texts: Optional[list[str]] = None,
    target_facts: Optional[list[Any]] = None,
    fault_id: Optional[str] = None,
    case_results: Optional[list[dict[str, Any]]] = None,
    operator_intent_text: Optional[str] = None,
) -> tuple[dict[str, Any], ProcedureActionResolution, Optional[str]]:
    """Resolve the model-chosen procedure_action_id deterministically. The model's own
    command/command_source are discarded; parameter values are accepted only from operator text
    or earlier session confirmation -- and, for a STATE CHANGE, every target only from this fault's
    trusted case target facts (`target_facts`). Returns (result, resolution, discarded model command).
    `operator_intent_text`: the operator's OWN words this turn (ProgressionController.intent_text).
    Identifiers inside pasted / bound output are observations (`observed_texts`, `target_facts`),
    never operator requests; the model's structured proposal is validated against them."""
    step = dict(raw_result.get("diagnostic_step") or {})
    action_id = str(step.get("procedure_action_id") or "").strip()
    model_command = str(step.get("command") or "").strip() or None
    step["command"] = None
    step["command_source"] = None
    state = getattr(tool_context, "state", None)
    confirmed = read_confirmed_parameters(state)
    resolution, updated = resolve_procedure_action(
        action_id,
        issued_ids=issued_action_ids(run_id),
        selected_evidence=evidence,
        proposals=step.get("parameter_values") or [],
        operator_text=current_user_text(tool_context) if operator_intent_text is None else operator_intent_text,
        confirmed_parameters=confirmed,
        observed_texts=observed_texts or [],
        target_facts=target_facts or [],
        fault_id=fault_id,
        case_results=case_results or [],
    )
    if updated != confirmed and state is not None:
        state[CONFIRMED_PROCEDURE_PARAMETERS_STATE_KEY] = updated
    if resolution.action is not None and resolution.action.action_type is ProcedureActionType.STATE_CHANGE:
        _record_target_gate(resolution, target_facts or [], fault_id)
    if resolution.status is ProcedureActionResolutionStatus.UNKNOWN_ACTION:
        step["procedure_action_id"] = None  # a fabricated / stale id is never carried forward
    result = dict(raw_result)
    result["diagnostic_step"] = step
    return result, resolution, model_command


def _finalize_procedure_action(
    raw_result: dict[str, Any],
    resolution: ProcedureActionResolution,
    authorized_catalog: list[ApprovedCommand],
    model_command: Optional[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply the EXISTING Command Authority result to the resolved candidate. Only a candidate
    that `build_server_validated_commands` authorized becomes `diagnostic_step.command`."""
    step = dict(raw_result.get("diagnostic_step") or {})
    candidate = resolution.candidate
    authority: Optional[str] = None
    reason = resolution.reason
    if candidate is not None:
        authorized = any(c.command == candidate.command and c.source_id == candidate.source_id for c in authorized_catalog)
        authority = "authorized" if authorized else "rejected"
        if authorized:
            step["command"] = candidate.command
            step["command_source"] = candidate.source_id
        else:
            reason = "rendered candidate rejected by command authority"
    result = dict(raw_result)
    if not step.get("command"):
        restrictions = list(step.get("restrictions") or [])
        restrictions.append(f"[{NO_APPROVED_COMMAND_FOR_ACTION_TEXT} Reason: {reason}]")
        step["restrictions"] = restrictions
        target_clarification = build_condition_clarification(resolution.condition_check) or build_target_clarification(
            resolution.target_validation
        )
        clarification = None if target_clarification else build_parameter_clarification(resolution.bindings)
        if target_clarification:
            # A state-change target is never supplied by the operator's word: say what the trusted
            # evidence does / does not establish (no value is guessed or suggested).
            result["target_clarification"] = target_clarification
            missing = list(result.get("missing_information") or [])
            if target_clarification["text"] and target_clarification["text"] not in missing:
                missing.append(target_clarification["text"])
            result["missing_information"] = missing
        if clarification:
            result["parameter_clarification"] = clarification
            missing = list(result.get("missing_information") or [])
            if clarification["text"] not in missing:
                missing.append(clarification["text"])
            result["missing_information"] = missing
    result["diagnostic_step"] = step
    summary = resolution_summary(resolution, authority)
    summary["model_command_ignored"] = model_command if model_command and model_command != step.get("command") else None
    record_action_resolution(summary)
    return result, summary


async def _run_specialist_message(
    runner: Any,
    session: Any,
    message: types.Content,
    tool_context: ToolContext,
) -> tuple[Optional[types.Content], Any, list[dict[str, Any]]]:
    """Runs one specialist message; returns (last content, grounding metadata, parsed response payloads)."""
    last_content = None
    last_grounding_metadata = None
    payloads: list[dict[str, Any]] = []
    async with Aclosing(
        runner.run_async(user_id=session.user_id, session_id=session.id, new_message=message)
    ) as agen:
        async for event in agen:
            if event.actions.state_delta:
                tool_context.state.update(event.actions.state_delta)
            if event.content:
                last_content = event.content
                last_grounding_metadata = event.grounding_metadata
                payload = _parse_response_payload(event.content)
                if payload is not None:
                    payloads.append(payload)
    return last_content, last_grounding_metadata, payloads


def _finalization_agent(agent: Any) -> Any:
    """The specialist used for a structured-output regeneration: the same configuration, schemas and
    callbacks with NO tools declared (nothing can be searched, selected, issued or called)."""
    return agent.clone(update={"tools": []})


async def _run_tool_free_message(
    runner: Any,
    session: Any,
    tool_context: ToolContext,
    text: str,
) -> tuple[Optional[types.Content], Any, list[dict[str, Any]]]:
    """Runs one server message in the SAME specialist session with the specialist cloned with NO tools
    declared (`_finalization_agent`): it cannot search, select, issue actions or call anything; its
    callbacks are kept, so the integrity callback validates the answer like any other."""
    from google.adk.runners import Runner

    finalizer_runner = Runner(
        app_name=runner.app_name,
        agent=_finalization_agent(runner.agent),
        artifact_service=runner.artifact_service,
        session_service=runner.session_service,
        memory_service=runner.memory_service,
        credential_service=runner.credential_service,
        plugins=list(runner.plugin_manager.plugins) if runner.plugin_manager else None,
    )
    # Not closed here: it owns no toolset, and the shared plugins are closed with the specialist runner.
    message = types.Content(role="user", parts=[types.Part.from_text(text=text)])
    return await _run_specialist_message(finalizer_runner, session, message, tool_context)


async def _regenerate_structured_output(
    runner: Any,
    session: Any,
    tool_context: ToolContext,
    failure: StructuredOutputFailure,
    run_id: Optional[str],
) -> tuple[Optional[types.Content], Any, list[dict[str, Any]]]:
    """ONE regeneration of the final structured answer in the SAME specialist session (same run,
    request contract, tool results, selection and issued catalog), tool-free. A runner error is a
    failed regeneration (the caller fails closed), never a further attempt."""
    try:
        return await _run_tool_free_message(runner, session, tool_context, regeneration_instruction(failure, run_id))
    except Exception as exc:
        logger.error("Technical Authority Engineer structured-output regeneration failed: %s", exc)
        return None, None, []


async def _reselect_procedure_action(
    runner: Any,
    session: Any,
    tool_context: ToolContext,
    output_schema: Any,
    run_id: Optional[str],
    content: Optional[types.Content],
    grounding: Any,
    payloads: list[dict[str, Any]],
    offered_ids: set[str],
) -> tuple[Optional[types.Content], Any, list[dict[str, Any]], bool]:
    """ProcedureAction id recovery (action_id_recovery.py). When the final answer recommends a step
    whose procedure_action_id this run neither issued nor offered, and this run HAS an issued catalog,
    the specialist is asked ONCE, tool-free, in the same session, to select one identifier from that
    catalog. Only an answer whose id EXACTLY matches the issued catalog is adopted; otherwise the
    original answer is kept and fails closed through the existing UNKNOWN_ACTION path. The server
    never corrects, completes or substitutes an id. Returns (content, grounding, payloads, adopted)."""
    catalog = issued_actions(run_id)
    invalid = unknown_action_id(_parse_response_payload(content), issued_action_ids(run_id), offered_ids)
    if invalid is None or not catalog:
        return content, grounding, payloads, False  # valid / no proposal / no current-run catalog: no call
    for _ in range(MAX_ACTION_ID_RESELECTIONS):
        trace_reselection_request(runner.agent.name, run_id, invalid, len(catalog))
        integrity_mark = len(integrity_decisions(run_id))
        try:
            r_content, r_grounding, r_payloads = await _run_tool_free_message(
                runner, session, tool_context, reselection_instruction(invalid, catalog)
            )
        except Exception as exc:
            logger.error("Technical Authority Engineer action id re-selection failed: %s", exc)
            r_content, r_grounding, r_payloads = None, None, []
        structurally_valid = classify_structured_output(r_content, output_schema) is None
        returned = proposed_action_id(_parse_response_payload(r_content)) if structurally_valid else None
        exact_match = returned is not None and returned in issued_action_ids(run_id)
        trace_reselection_result(runner.agent.name, run_id, returned, exact_match)
        if exact_match:
            return r_content, r_grounding, list(r_payloads), True
        # Discarded: the integrity callback's decisions on this answer must not stand for the kept one.
        rollback_integrity_decisions(run_id, integrity_mark)
    return content, grounding, payloads, False


async def _recover_structured_output(
    recovery: StructuredOutputRecovery,
    runner: Any,
    session: Any,
    tool_context: ToolContext,
    output_schema: Any,
    content: Optional[types.Content],
    grounding: Any,
    payloads: list[dict[str, Any]],
    *,
    phase: str,
) -> tuple[Optional[types.Content], Any, list[dict[str, Any]], bool]:
    """Structural check of a final specialist answer and, when it is unusable, the bounded
    regeneration (budget shared by this invocation). A valid answer -- whatever its outcome -- is
    returned untouched with no extra model call. Returns (content, grounding, payloads, regenerated)."""
    failure = classify_structured_output(content, output_schema)
    if failure is None and phase == "final":
        return content, grounding, payloads, False  # already-valid remediation answer: nothing to record
    recovery.observe(failure, phase=phase)
    regenerated = False
    for _ in range(MAX_STRUCTURED_OUTPUT_ATTEMPTS - 1):
        if failure is None or not recovery.can_regenerate:
            break
        recovery.begin_regeneration(failure)
        r_content, r_grounding, r_payloads = await _regenerate_structured_output(
            runner, session, tool_context, failure, recovery.run_id
        )
        regenerated = True
        failure = classify_structured_output(r_content, output_schema)
        recovery.observe(failure, phase="regeneration")
        if r_content is not None:
            content, grounding, payloads = r_content, r_grounding, list(r_payloads)
    if failure is not None:
        recovery.fail_closed(failure)
    return content, grounding, payloads, regenerated


class TechnicalAuthorityAgentTool(AgentTool):
    """Specialized in-process AgentTool for the Technical Authority Engineer.

    Enforces server-validated evidence and command catalogs before invoking the
    specialist runner.
    """

    @override
    async def run_async(
        self,
        *,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        from google.adk.runners import Runner
        from google.adk.sessions.in_memory_session_service import InMemorySessionService

        if self.skip_summarization:
            tool_context.actions.skip_summarization = True

        run_id = current_run_id()
        _record_specialist_invocation(self.agent.name, run_id)

        # 1. Server-reconciled applicability context, accumulated across turns of this session.
        # Model-proposed facts are accepted only when literally stated by the user (or already
        # confirmed) and consistent with governed applicability metadata; confirmed facts from
        # earlier turns are never discarded.
        confirmed_facts = read_confirmed_applicability_facts(tool_context.state)
        caller_facts = args.get("known_applicability_facts")
        unconfirmed_facts = []
        accepted_facts: dict[str, list[str]] = {}
        vocabulary = None
        if caller_facts:
            vocabulary = await load_governed_vocabulary()
            accepted_facts, unconfirmed_facts = reconcile_applicability_facts(
                caller_facts, current_user_text(tool_context), confirmed_facts, vocabulary
            )
            confirmed_facts = merge_applicability_facts(confirmed_facts, accepted_facts)
        if confirmed_facts:
            tool_context.state[CONFIRMED_APPLICABILITY_FACTS_STATE_KEY] = confirmed_facts
        if run_id:
            refresh_run_applicability_context(run_id, ApplicabilityContext(dimensions=confirmed_facts))

        # ADK exposes the session on the invocation context (`session.id`); there is no `session_id`.
        session_id = getattr(getattr(getattr(tool_context, "_invocation_context", None), "session", None), "id", None)
        case_id = tool_context.state.get("active_case_id")

        # 2. Load the AUTHORITATIVE progression (the linked Case's versioned record, or this
        #    session's own when no Case is linked) and re-derive the fault-thread projections from it
        #    BEFORE thread resolution, so a new session on an existing Case continues its faults.
        progression_repository = ProgressionRepository(tool_context.state, session_id=session_id)
        authoritative_progression = await progression_repository.load()
        if progression_repository.case_scoped:
            case_id = progression_repository.case_id
        if authoritative_progression.faults:
            save_projections(tool_context.state, authoritative_progression)

        # Retrieve the ACTIVE fault thread (a session may hold several fault threads).
        troubleshooting_state: Optional[TroubleshootingState] = load_active_thread(tool_context.state)

        # Clarification continuity (clarification_continuity.py): classify the operator's exact
        # latest message against the ACTIVE fault's pending clarification BEFORE building the
        # request: a question about the requested information, or an answer to it, is not a new
        # troubleshooting objective. Answer values bind only from the operator's literal words that
        # governed applicability metadata knows for a requested dimension.
        operator_text_raw = current_user_text(tool_context)
        pending_clarification = (
            authoritative_progression.pending_clarification(troubleshooting_state.fault_id) if troubleshooting_state is not None else None
        )
        clarification_values: dict[str, list[str]] = {}
        request_kind = ClarificationTurnKind.OPERATIONAL
        if is_clarification_follow_up(operator_text_raw):
            request_kind = ClarificationTurnKind.FOLLOW_UP
        elif pending_clarification is not None and pending_clarification.reason is ClarificationReason.APPLICABILITY:
            if vocabulary is None:
                vocabulary = await load_governed_vocabulary()
            clarification_values = bind_applicability_answer(pending_clarification.unresolved_fields, operator_text_raw, vocabulary)
            wanted = {normalize_dimension_key(f): f for f in pending_clarification.unresolved_fields}
            for dim, values in accepted_facts.items():  # literally stated + governed-valid this turn
                field = wanted.get(normalize_dimension_key(dim))
                if field is not None:
                    known = {v.casefold() for v in clarification_values.get(field, [])}
                    clarification_values.setdefault(field, []).extend(v for v in values if v.casefold() not in known)
            if clarification_values:
                request_kind = ClarificationTurnKind.ANSWER

        # Current-turn request contract, built from the operator's exact latest message BEFORE any
        # history is consulted: the explicit current-turn request takes precedence over the
        # historical troubleshooting objective, which may only enrich it (turn_request.py).
        turn_request_contract = build_turn_request_contract(
            args.get("current_request") or {},
            operator_text_raw,
            troubleshooting_state,
            confirmed_facts,
            request_kind=request_kind.value,
            answer_values=[v for values in clarification_values.values() for v in values],
        )
        # Resolve which fault thread this turn belongs to: continue the active thread, switch to an
        # existing thread the operator returned to, or open a new thread for a new subject. Other
        # threads are kept intact; only the resolved thread's history is used below.
        troubleshooting_state, _threads, thread_decision, previous_fault_id = resolve_active_thread(
            tool_context.state,
            turn_request_contract,
            problem_statement=str(args.get("problem_statement") or ""),
            session_id=session_id,
            case_id=case_id,
        )
        if thread_decision is not ThreadDecision.CONTINUED:
            # Background must describe the thread now in focus, never the one the operator left.
            turn_request_contract = turn_request_contract.model_copy(
                update={
                    "active_investigation_objective": active_investigation_objective(troubleshooting_state)
                    if thread_decision is ThreadDecision.SWITCHED_TO_EXISTING
                    else None
                }
            )
        record_turn_request(
            turn_request_contract.model_dump(mode="json")
            | {
                "troubleshooting_thread": {
                    "decision": thread_decision.value,
                    "active_fault_id": troubleshooting_state.fault_id,
                    "previous_fault_id": previous_fault_id,
                    "subject_component": troubleshooting_state.subject_component,
                }
            }
        )
        if session_id and not troubleshooting_state.session_id:
            troubleshooting_state.session_id = session_id
        if case_id and not troubleshooting_state.case_id:
            troubleshooting_state.case_id = case_id

        # The pending clarification belongs to ONE fault: it applies only when this turn stays on it.
        clarification_applies = (
            pending_clarification is not None
            and thread_decision is ThreadDecision.CONTINUED
            and troubleshooting_state.fault_id == pending_clarification.fault_id
        )
        if request_kind is ClarificationTurnKind.FOLLOW_UP:
            if clarification_applies:
                return _clarification_turn_response(
                    run_id, request_kind, fault_id=troubleshooting_state.fault_id, question=pending_clarification,
                    text=render_clarification_request(pending_clarification), confirmed_facts=confirmed_facts,
                )
            if not any(s.status in PENDING_STATUSES for s in authoritative_progression.steps_for(troubleshooting_state.fault_id)):
                # Nothing is outstanding on this fault: say so; never invent requested fields.
                return _clarification_turn_response(
                    run_id, request_kind, fault_id=troubleshooting_state.fault_id, question=None,
                    text=NO_PENDING_CLARIFICATION_TEXT,
                )
            # A pending STEP (no clarification) is resolved by the normal flow below.
        clarification_answered = request_kind is ClarificationTurnKind.ANSWER and clarification_applies
        retrieval_resumption: Optional[dict[str, Any]] = None
        # The fault's applicability clarification answered IN FULL this turn: the server-owned
        # dependency that resumes the investigation (with or without a pending step).
        resolved_applicability_clarification: Optional[OpenQuestion] = None
        if clarification_answered:
            confirmed_facts = merge_applicability_facts(confirmed_facts, clarification_values)
            tool_context.state[CONFIRMED_APPLICABILITY_FACTS_STATE_KEY] = confirmed_facts
            if run_id:
                refresh_run_applicability_context(run_id, ApplicabilityContext(dimensions=confirmed_facts))
            authoritative_progression.resolve_clarification_fields(pending_clarification.question_id, clarification_values)
            try:
                await progression_repository.save(authoritative_progression, troubleshooting_state.fault_id)
            except ProgressionConflict as conflict:
                return _progression_conflict_response(conflict)
            if pending_clarification.unresolved_fields:
                # Partial answer: ask ONLY for what is still unresolved; nothing restarts.
                _trace_applicability_clarification("answered_partially", pending_clarification)
                return _clarification_turn_response(
                    run_id, request_kind, fault_id=troubleshooting_state.fault_id, question=pending_clarification,
                    text=render_clarification_request(
                        pending_clarification, newly_resolved=clarification_values, unconfirmed=unconfirmed_facts
                    ),
                    confirmed_facts=confirmed_facts,
                )
            # Every requested field resolved: governed applicability must be recomputed from a FRESH
            # current-run retrieval under the confirmed context before any operational guidance. The
            # server performs that search itself (never relying on the specialist to remember it);
            # results are AVAILABLE only -- selection stays the specialist's explicit decision.
            if (
                pending_clarification.status is ClarificationStatus.RESOLVED
                and pending_clarification.reason is ClarificationReason.APPLICABILITY
            ):
                resolved_applicability_clarification = pending_clarification
                _trace_applicability_clarification("answered", pending_clarification)
                fault = authoritative_progression.faults.get(troubleshooting_state.fault_id)
                if (
                    fault is not None and fault.phase is ProgressionPhase.BLOCKED_MISSING_INFORMATION
                    and not any(s.status in PENDING_STATUSES for s in authoritative_progression.steps_for(fault.fault_id))
                ):
                    authoritative_progression.set_phase(
                        fault.fault_id, ProgressionPhase.READY_FOR_DIAGNOSIS, reason="applicability clarification resolved"
                    )
                retrieval_resumption = await _resume_governed_retrieval(
                    run_id, authoritative_progression, troubleshooting_state.fault_id, pending_clarification, confirmed_facts
                )

        # 3. Progression Controller (server-owned sequencing). An operator RESULT is bound to the exact
        #    pending step, a step the operator cannot perform is recorded SKIPPED; any other message
        #    leaves the pending step waiting: NO RESULT = NO PROGRESSION. A command follow-up
        #    ("what's next cmd?") resolves the SAME pending step instead of a fresh investigation.
        progression = ProgressionController(
            tool_context.state, troubleshooting_state, session_id=session_id, case_id=case_id, progression=authoritative_progression
        )
        operator_text = operator_text_raw.strip()
        # A clarification answer is requested context, never an observation for the pending step.
        progression.classify_turn(operator_text, clarification_answer=clarification_answered)
        pending_before = progression.pending_step()
        # The observation summary comes from the operator's OWN words, never the caller's paraphrase.
        legacy_observation = None
        if pending_before is not None and operator_text:
            cmd = (pending_before.command or "").strip()
            legacy_observation = f"Output observed for `{cmd}`: {operator_text[:200]}" if cmd else operator_text[:200]
        progression.apply_operator_turn(
            operator_text[:_MAX_OPERATOR_OBSERVATION_CHARS],
            legacy_observation=legacy_observation,
            # An explicit "forget that check ..." refers to the pending step of the fault that was
            # active BEFORE this turn's thread resolution.
            previous_fault_id=previous_fault_id if thread_decision is not ThreadDecision.CONTINUED else None,
        )
        # From here on the pending step is re-read from the post-binding progression only:
        # `pending_before` is the pre-turn snapshot (observation wording above) and never decides
        # anything after the result was bound.
        _record_result_binding(progression, troubleshooting_state.fault_id, pending_before)
        # Operator acquisition hint ("I normally use X"): attached to this fault's OPEN requirement as
        # an UNTRUSTED suggestion. It may steer discovery (the specialist sees it as a search term);
        # it never authorizes, never enters the approved catalog, never becomes a command.
        acquisition_hint = None
        detected_hint = detect_acquisition_hint(operator_text) if not clarification_answered else None
        if detected_hint is not None:
            hint_requirement = hint_target(progression.progression, troubleshooting_state.fault_id)
            if hint_requirement is not None:
                acquisition_hint = progression.progression.add_acquisition_hint(
                    hint_requirement.requirement_id, make_hint(hint_requirement, detected_hint[0], detected_hint[1], run_id)
                )
            progression.events.append({
                "event": "acquisition_hint_recorded" if acquisition_hint is not None else "acquisition_hint_ignored",
                "requirement_id": acquisition_hint.requirement_id if acquisition_hint is not None else None,
                "hint_id": acquisition_hint.hint_id if acquisition_hint is not None else None,
                "authority": "none",
            })
        # Persist the result binding now (versioned): it must survive a specialist failure later in
        # this turn. A stale write fails closed; the newer progression is never overwritten.
        try:
            await progression_repository.save(progression.progression, troubleshooting_state.fault_id)
        except ProgressionConflict as conflict:
            return _progression_conflict_response(conflict)
        progression_decision = ProposalDecision.NONE
        progression_check_id: Optional[str] = None
        blocked_action: Any = None
        legacy_action: Optional[tuple[str, str, Any]] = None
        step_disposition: Optional[str] = None
        acquisition: Optional[AcquisitionDecision] = None
        acquisition_step: Optional[dict[str, Any]] = None
        operational_summary: Optional[dict[str, Any]] = None
        # Acquisition continuity (acquisition_continuity.py): the pending step's server-known governed
        # acquisition (structural identity, zero authority) and, for an acquisition request ("what
        # command do I use?"), the OPEN requirement it is about. The identity is only a lookup key:
        # it is re-resolved from THIS run's selected evidence by the normal resolver and Command
        # Authority -- never reused as authority.
        pending_now = progression.pending_step()
        known_acquisition = known_governed_acquisition(progression.progression, troubleshooting_state.fault_id, pending_now)
        command_follow_up = progression.turn_kind is TurnKind.COMMAND_FOLLOW_UP
        acquisition_binding = (
            bind_acquisition_request(progression.progression, troubleshooting_state.fault_id, operator_text, pending_now)
            if not clarification_answered and (command_follow_up or (pending_now is None and is_acquisition_request(operator_text)))
            else None
        )
        # Deterministic continuation state of this turn (never phrase-driven on its own): what the
        # operator's message is relative to the server-owned progression.
        continuation = continuation_kind(
            progression.turn_kind.value,
            clarification_answered=clarification_answered,
            new_objective=thread_decision is ThreadDecision.CREATED,
            mechanism_requested=progression.mechanism_requested,
        )
        continuation_rule = pre_run_rule(
            known_acquisition, clarification_answered=clarification_answered, command_follow_up=command_follow_up, binding=acquisition_binding,
            continuation=continuation, applicability_clarification_resolved=resolved_applicability_clarification is not None,
        )
        # Clarification continuity without a pending operational step: the answered clarification
        # itself is what resumes. The specialist is told which clarification was answered and which
        # sources it concerned (identity only); selection, actions and authority are this run's own.
        resumed_from_clarification = (
            continuation_rule is ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED and known_acquisition is None
        )
        # A pending step recorded while applicability was unresolved, whose applicability clarification is
        # now RESOLVED (this turn or earlier), continues by server structure (see `_against_pending`).
        settled_clarification = resolved_applicability_clarification or progression.progression.latest_clarification(
            troubleshooting_state.fault_id, ClarificationReason.APPLICABILITY
        )
        if settled_clarification is not None and settled_clarification.status is ClarificationStatus.RESOLVED:
            progression.resume_from_clarification(settled_clarification.originating_step_id)
        if resumed_from_clarification and retrieval_resumption is not None:
            retrieval_resumption["rule"] = RESUMED_INVESTIGATION_RULE
            retrieval_resumption["resumes"] = _resumed_clarification_view(resolved_applicability_clarification)
        known_available = _known_source_available(run_id, known_acquisition)
        resumed_before = retrieval_resumption is not None
        acquisition_trace: Optional[dict[str, Any]] = None
        acquisition_remediation: Optional[str] = None
        if continuation_rule is ContinuationRule.ACQUISITION_REQUEST and retrieval_resumption is None:
            # HOW to satisfy the pending requirement: discovery runs in THIS run before the specialist
            # (fresh search -> AVAILABLE only; selection stays the specialist's explicit decision).
            retrieval_resumption = await _server_governed_search(
                run_id, progression.progression, troubleshooting_state.fault_id, pending_now, confirmed_facts,
                stage="acquisition_discovery", reason=ContinuationRule.ACQUISITION_REQUEST.value, rule=ACQUISITION_RETRIEVAL_RULE,
            )
        elif continuation_rule is ContinuationRule.GENERIC_CONTINUATION and retrieval_resumption is None and not known_available:
            # "what next?" on a pending step whose governed method is known: the server never relies on
            # the specialist remembering to search. It forces DISCOVERY in this run (AVAILABLE only);
            # it never forces selection -- that stays the specialist's explicit decision.
            retrieval_resumption = await _server_governed_search(
                run_id, progression.progression, troubleshooting_state.fault_id, pending_now, confirmed_facts,
                stage="acquisition_discovery", reason=ContinuationRule.GENERIC_CONTINUATION.value, rule=CONTINUATION_RETRIEVAL_RULE,
            )
        _record_continuation(
            continuation, continuation_rule, known_acquisition, known_available,
            fault_id=troubleshooting_state.fault_id, pending=pending_now,
            clarification=resolved_applicability_clarification
            or progression.progression.pending_clarification(troubleshooting_state.fault_id, ClarificationReason.APPLICABILITY),
            discovery=(
                "clarification_resumption" if resumed_before
                else f"forced:{retrieval_resumption.get('reason')}" if retrieval_resumption is not None
                else "not_required" if known_available
                else "none"
            ),
        )
        continuity_objective = progression.continuity_objective()
        bound_requirement = progression.progression.requirement(acquisition_binding.requirement_id) if acquisition_binding else None
        if bound_requirement is not None and not acquisition_binding.pending_requirement:
            # The request is about another open requirement than the pending step's: never redirect it.
            continuity_objective = f"Determine how to obtain the evidence still required: {bound_requirement.description}"
        if continuity_objective and not turn_request_contract.explicit_in_current_message:
            turn_request_contract = turn_request_contract.model_copy(
                update={"diagnostic_objective": continuity_objective, "focus": "continue"}
            )

        # 4. Populate prior_steps_taken into sanitized_args to prevent diagnostic repetition loops
        prior_steps = troubleshooting_state.get_prior_steps_summary()
        caller_prior = list(args.get("prior_steps_taken") or [])
        merged_prior = list(prior_steps)
        for cp in caller_prior:
            if cp not in merged_prior:
                merged_prior.append(cp)

        # Build server-validated envelope
        # Controlled read-execution output recorded on earlier checks is OBSERVED EVIDENCE for this
        # evaluation (server-built, source_type=observed_metric): it can inform interpretation but
        # can never ground or authorize a command, nor become governed knowledge.
        caller_evidence = _execution_observation_evidence(troubleshooting_state) + list(args.get("verified_evidence") or [])
        caller_commands = list(args.get("approved_commands_catalog") or [])

        await ensure_governed_scope(run_id)
        server_evidence = build_server_validated_evidence(run_id, tool_context, caller_evidence)
        server_commands = build_server_validated_commands(server_evidence, caller_commands)

        sanitized_args = dict(args)
        sanitized_args["verified_evidence"] = [e.model_dump(mode="json") for e in server_evidence]
        sanitized_args["approved_commands_catalog"] = [c.model_dump(mode="json") for c in server_commands]
        sanitized_args["prior_steps_taken"] = merged_prior
        sanitized_args["known_applicability_facts"] = confirmed_facts or None
        # The active investigation is supplied as separate BACKGROUND, never merged into the
        # problem statement: it may enrich the current request but must not redefine it.
        # The specialist sees only the validated contract: the caller's raw reading and any values
        # discarded as not stated in the latest message (recorded in the trace) are withheld.
        sanitized_args["turn_request_contract"] = turn_request_contract.model_dump(mode="json", exclude={"discarded_fields"})
        sanitized_args["active_investigation_context"] = turn_request_contract.active_investigation_objective
        sanitized_args["current_request"] = None
        # Server-built pending-step context (any caller value is replaced).
        sanitized_args["pending_step"] = progression.pending_context()
        sanitized_args["investigation_state"] = progression.investigation_context()
        # Server-performed resumed retrieval of this turn (any caller value is replaced).
        sanitized_args["resumed_governed_retrieval"] = (
            {k: v for k, v in retrieval_resumption.items() if k in ("status", "reason", "query_text", "applicability_context", "results", "rule", "resumes")}
            if retrieval_resumption
            else None
        )

        input_schema = _get_input_schema(self.agent)
        if input_schema:
            input_value = input_schema.model_validate(sanitized_args)
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=input_value.model_dump_json(exclude_none=True))],
            )
        else:
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=sanitized_args.get("request", ""))],
            )

        # Forward current-turn image evidence if present
        content.parts.extend(_trusted_image_parts(tool_context))

        invocation_context = tool_context._invocation_context
        parent_app_name = invocation_context.app_name if invocation_context else None
        child_app_name = parent_app_name or self.agent.name
        plugins = (
            tool_context._invocation_context.plugin_manager.plugins
            if self.include_plugins
            else None
        )

        runner = Runner(
            app_name=child_app_name,
            agent=self.agent,
            artifact_service=ForwardingArtifactService(tool_context),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            credential_service=tool_context._invocation_context.credential_service,
            plugins=plugins,
        )

        state_dict = {
            k: v for k, v in tool_context.state.to_dict().items() if not k.startswith("_adk")
        }
        session = await runner.session_service.create_session(
            app_name=child_app_name,
            user_id=tool_context._invocation_context.user_id,
            state=state_dict,
        )

        last_content = None
        last_grounding_metadata = None
        selection_fail_closed = False
        # An explicit empty selection recorded before this specialist ran (e.g. by another agent in
        # the same run) is not this specialist's negative selection and is never credited to it.
        negative_selection_preexisting = bool(run_id) and has_explicit_empty_knowledge_selection(run_id)
        all_payloads: list[dict[str, Any]] = []
        # Payloads of the specialist message that produced `last_content`: the raw model output, then
        # the copy the specialist's own integrity callback sanitized.
        final_payloads: list[dict[str, Any]] = []
        gap_recovery: Optional[dict[str, Any]] = None
        gap_continuation: Optional[dict[str, Any]] = None
        action_choice: Optional[dict[str, Any]] = None
        # A model-written command that names no governed action of this run's SELECTED evidence is no
        # acquisition method: its evidence need takes the governed recovery path (never an operator task).
        governed_command = _governed_command_check(run_id)
        resume_completeness: Optional[dict[str, Any]] = None
        output_schema = _get_output_schema(self.agent)
        # Structured-output recovery (structured_output.py): an unusable FINAL answer (empty, invalid /
        # truncated JSON, schema-invalid) is regenerated ONCE per invocation, tool-free, in this same
        # session; the reasoning / tool phase is never rerun. A valid answer costs no extra model call.
        structured_output = StructuredOutputRecovery(self.agent.name, run_id)
        try:
            last_content, last_grounding_metadata, payloads = await _run_specialist_message(
                runner, session, content, tool_context
            )
            if output_schema:
                last_content, last_grounding_metadata, payloads, _ = await _recover_structured_output(
                    structured_output, runner, session, tool_context, output_schema,
                    last_content, last_grounding_metadata, payloads, phase="initial",
                )
            checked_content = last_content
            all_payloads.extend(payloads)
            final_payloads = list(payloads)
            # FIX 1: a governed recommendation requires explicitly SELECTED evidence. Exactly one
            # bounded remediation turn; AVAILABLE evidence is never promoted to SELECTED.
            if requires_selection_remediation(run_id, payloads):
                logger.warning("Technical Authority Engineer governed recommendation without selected evidence; requesting explicit selection once")
                instruction = SELECTION_REMEDIATION_INSTRUCTION
                if not get_available_knowledge_evidence(run_id).items:
                    # Nothing is AVAILABLE in this run, so a selection request alone could never succeed:
                    # the server runs ONE governed search first (AVAILABLE only, server-owned query).
                    # Selection is still the specialist's explicit decision afterwards.
                    discovery_record = await _server_governed_search(
                        run_id, progression.progression, troubleshooting_state.fault_id, pending_now, confirmed_facts,
                        stage="selection_discovery", reason="selection_remediation_without_available_evidence",
                        rule=SELECTION_REMEDIATION_INSTRUCTION,
                    )
                    instruction = _selection_discovery_message(discovery_record)
                remediation = types.Content(role="user", parts=[types.Part.from_text(text=instruction)])
                r_content, r_grounding, r_payloads = await _run_specialist_message(
                    runner, session, remediation, tool_context
                )
                all_payloads.extend(r_payloads)
                if r_content is not None:
                    last_content, last_grounding_metadata = r_content, r_grounding
                    final_payloads = list(r_payloads)
                if not snapshot_selected_knowledge_evidence(run_id) and (
                    r_content is None or any(is_governed_recommendation(p) for p in r_payloads)
                ):
                    selection_fail_closed = True
            elif not negative_selection_preexisting and requires_selection_contract_remediation(run_id, payloads):
                # Non-operational outcome after a governed search without any selection decision:
                # exactly one bounded request to complete the contract (positive or explicit empty).
                # Nothing is selected on the specialist's behalf; if it still does not decide, the
                # completion boundary in chat_service fails closed.
                logger.warning("Technical Authority Engineer non-operational outcome without a selection decision; requesting it once")
                remediation = types.Content(
                    role="user", parts=[types.Part.from_text(text=SELECTION_CONTRACT_REMEDIATION_INSTRUCTION)]
                )
                r_content, r_grounding, r_payloads = await _run_specialist_message(
                    runner, session, remediation, tool_context
                )
                all_payloads.extend(r_payloads)
                if r_content is not None and r_payloads:
                    last_content, last_grounding_metadata = r_content, r_grounding
                    final_payloads = list(r_payloads)
                if not snapshot_selected_knowledge_evidence(run_id) and any(
                    is_governed_recommendation(p) for p in r_payloads
                ):
                    selection_fail_closed = True
            # Acquisition continuity: a governed acquisition under consideration cannot proceed without
            # governed discovery and an explicit selection in THIS run. Exactly one bounded request: the
            # server runs the search itself when none was performed, or points at the AVAILABLE,
            # applicability-MATCH source of the known acquisition the specialist did not decide on.
            # Nothing is selected on its behalf; AVAILABLE is never promoted.
            acquisition_remediation = _acquisition_remediation_need(
                run_id, known_acquisition, continuation_rule, acquisition_binding, final_payloads,
                progression.progression, troubleshooting_state.fault_id, acquisition_wording=is_acquisition_request(operator_text),
            )
            if acquisition_remediation is not None and not selection_fail_closed:
                discovery_record = None
                if acquisition_remediation == "discovery":
                    discovery_record = await _server_governed_search(
                        run_id, progression.progression, troubleshooting_state.fault_id, pending_now, confirmed_facts,
                        stage="acquisition_discovery", reason="governed_discovery_required", rule=ACQUISITION_RETRIEVAL_RULE,
                        subject=bound_requirement.description if bound_requirement is not None else None,
                    )
                logger.warning(
                    "Technical Authority Engineer governed acquisition needs %s in this run; requesting it once", acquisition_remediation
                )
                remediation = types.Content(role="user", parts=[types.Part.from_text(text=_acquisition_remediation_message(
                    acquisition_remediation, known_acquisition, discovery_record,
                    progression.progression.requirement(known_acquisition.requirement_id) if known_acquisition else bound_requirement,
                ))])
                r_content, r_grounding, r_payloads = await _run_specialist_message(runner, session, remediation, tool_context)
                all_payloads.extend(r_payloads)
                if r_content is not None and r_payloads:
                    last_content, last_grounding_metadata = r_content, r_grounding
                    final_payloads = list(r_payloads)
            # Gap recovery (gap_recovery.py): an evidence requirement without a governed acquisition
            # method ends one BRANCH, not the investigation. With no step pending, the server forecasts
            # the acquisition decision of the specialist's proposal; if it is a gap, the server -- not
            # the model -- determines the remaining valid governed branches of this fault and asks the
            # specialist ONCE to choose among them (or escalate). Nothing is selected or authorized here.
            # Action choice: the specialist named the evidence it needs without a method, while THIS run's
            # SELECTED, approved, MATCH evidence offers valid governed diagnostic actions. A method exists,
            # so this is never a gap: the specialist is asked ONCE to choose one explicitly (or say none
            # applies). The offered ids are issued from SELECTED evidence (the catalog tool's own rule);
            # the resolver and Command Authority still decide. Never auto-chosen, never auto-authorized.
            if not selection_fail_closed and known_acquisition is None:
                action_choice = _plan_action_choice(final_payloads, progression, troubleshooting_state.fault_id, run_id, governed_command)
                if action_choice is not None:
                    record_issued_actions(run_id, [a.action for a in action_choice["actions"]])
                    logger.warning("Technical Authority Engineer named evidence without a method; offering %d selected governed action(s) once",
                                   len(action_choice["actions"]))
                    remediation = types.Content(role="user", parts=[types.Part.from_text(text=action_choice_instruction(
                        action_choice["requirement"], action_choice["actions"],
                    ))])
                    r_content, r_grounding, r_payloads = await _run_specialist_message(runner, session, remediation, tool_context)
                    all_payloads.extend(r_payloads)
                    if r_content is not None and r_payloads:
                        last_content, last_grounding_metadata = r_content, r_grounding
                        final_payloads = list(r_payloads)
            if not selection_fail_closed and pending_now is None and not _fault_closed(progression) and action_choice is None:
                gap_recovery = await _plan_gap_recovery(
                    final_payloads, progression, troubleshooting_state.fault_id, run_id, confirmed_facts, governed_command
                )
                if gap_recovery is not None and gap_recovery["alternatives"]:
                    gap_recovery["offered"] = True
                    logger.warning("Technical Authority Engineer proposal ends in a governed acquisition gap; offering %d governed alternative(s) once",
                                   len(gap_recovery["alternatives"]))
                    remediation = types.Content(role="user", parts=[types.Part.from_text(text=recovery_instruction(
                        gap_recovery["requirement"], gap_recovery["alternatives"], repeated=gap_recovery["repeated"],
                    ))])
                    r_content, r_grounding, r_payloads = await _run_specialist_message(runner, session, remediation, tool_context)
                    all_payloads.extend(r_payloads)
                    if r_content is not None and r_payloads:
                        last_content, last_grounding_metadata = r_content, r_grounding
                        final_payloads = list(r_payloads)
            # Acquisition-gap continuation (gap_recovery.py): the bounded governed recovery path above
            # (current catalog choice, governed alternatives, one server search) established no grounded
            # method for the evidence the specialist still needs. The gap ends that BRANCH, not the
            # investigation: the server records it and asks the specialist ONCE to continue reasoning from
            # the evidence already collected (next hypothesis / evidence need). Tools stay enabled, so its
            # next need is resolved through the normal governed path; nothing is chosen, selected or
            # authorized on its behalf, and no command of its own ever becomes a method.
            if not selection_fail_closed and pending_now is None and not _fault_closed(progression):
                gap_continuation = await _plan_gap_continuation(
                    final_payloads, progression, troubleshooting_state.fault_id, run_id, governed_command,
                    actions_declined=action_choice is not None,
                )
                if gap_continuation is not None:
                    logger.warning(
                        "Technical Authority Engineer evidence need has no governed acquisition method after governed recovery; "
                        "recording the gap and requesting continued reasoning once"
                    )
                    remediation = types.Content(role="user", parts=[types.Part.from_text(text=gap_continuation_instruction(
                        gap_continuation["requirement"], gap_continuation["gap_reason"], repeated=gap_continuation["repeated"],
                    ))])
                    r_content, r_grounding, r_payloads = await _run_specialist_message(runner, session, remediation, tool_context)
                    all_payloads.extend(r_payloads)
                    if r_content is not None and r_payloads:
                        last_content, last_grounding_metadata = r_content, r_grounding
                        final_payloads = list(r_payloads)
            # Clarification continuity completeness: the investigation resumed from an answered
            # applicability clarification (no pending step), THIS run's SELECTED evidence applies and
            # offers valid governed diagnostic actions, yet the specialist proposed nothing actionable
            # (no method, no escalation) and no other server check already asked it. Exactly one bounded
            # request to resume with ONE of those actions or to say why none applies. Nothing is chosen,
            # selected or authorized on its behalf; the resolver and Command Authority still decide.
            if (
                resumed_from_clarification
                and not selection_fail_closed
                and action_choice is None
                and not (gap_recovery is not None and gap_recovery.get("offered"))
                and gap_continuation is None
                and not _fault_closed(progression)
                and not _actionable_proposal(final_payloads)
            ):
                resume_actions = _selected_action_alternatives(run_id, progression.progression, troubleshooting_state.fault_id)
                resume_completeness = {"offered": [a.action.action_id for a in resume_actions], "chosen": None}
                if resume_actions:
                    record_issued_actions(run_id, [a.action for a in resume_actions])
                    logger.warning(
                        "Technical Authority Engineer resumed from an answered applicability clarification without a next step; "
                        "offering %d selected governed action(s) once", len(resume_actions),
                    )
                    remediation = types.Content(role="user", parts=[types.Part.from_text(text=resume_choice_instruction(
                        resolved_applicability_clarification.resolved_values, resume_actions,
                    ))])
                    r_content, r_grounding, r_payloads = await _run_specialist_message(runner, session, remediation, tool_context)
                    all_payloads.extend(r_payloads)
                    if r_content is not None and r_payloads:
                        last_content, last_grounding_metadata = r_content, r_grounding
                        final_payloads = list(r_payloads)
                    chosen = next(
                        (str((p.get("diagnostic_step") or {}).get("procedure_action_id") or "").strip()
                         for p in reversed(final_payloads) if isinstance(p, dict) and isinstance(p.get("diagnostic_step"), dict)),
                        "",
                    )
                    resume_completeness["chosen"] = chosen if chosen in resume_completeness["offered"] else None
                _record_resume_completeness(resume_completeness)
            # A remediation message's answer adopted above is the final answer: the same structural
            # check, within the SAME budget (no regeneration remains if one was already used).
            if output_schema and not selection_fail_closed and last_content is not checked_content:
                last_content, last_grounding_metadata, r_payloads, regenerated = await _recover_structured_output(
                    structured_output, runner, session, tool_context, output_schema,
                    last_content, last_grounding_metadata, final_payloads, phase="final",
                )
                if regenerated:
                    all_payloads.extend(r_payloads)
                    final_payloads = list(r_payloads)
            # ProcedureAction id recovery: an opaque id this run never issued (e.g. mistyped) gets ONE
            # tool-free re-selection from THIS run's issued catalog; only an exact match is adopted.
            # Ids the server itself offered for exact issuance after the run are never re-selected.
            if output_schema and not selection_fail_closed:
                offered_ids = {known_acquisition.procedure_action_id} if known_acquisition is not None else set()
                if gap_recovery is not None and gap_recovery.get("offered"):
                    offered_ids |= {a.action.action_id for a in gap_recovery["alternatives"]}
                last_content, last_grounding_metadata, r_payloads, reselected = await _reselect_procedure_action(
                    runner, session, tool_context, output_schema, run_id,
                    last_content, last_grounding_metadata, final_payloads, offered_ids,
                )
                if reselected:
                    all_payloads.extend(r_payloads)
                    final_payloads = list(r_payloads)
        except Exception as e:
            logger.error("Technical Authority Engineer runner failed: %s", e, exc_info=True)
            if run_id:
                from backend.api.troubleshooting_guidance_context import discard_troubleshooting_guidance

                discard_troubleshooting_guidance(run_id)
            safe_error_resp = TechnicalAuthorityResponse(
                outcome=TechnicalAuthorityOutcome.ERROR,
                technical_interpretation="Technical Authority Engineer encountered an unhandled exception during evaluation.",
                detail=f"Safe error boundary intercepted specialist runner exception: {e}",
            )
            return safe_error_resp.model_dump(mode="json")
        finally:
            await runner.close()

        if selection_fail_closed:
            logger.warning("Technical Authority Engineer governed recommendation failed closed: selected evidence still empty after remediation")
            last_content = types.Content(
                role="model",
                parts=[types.Part.from_text(text=json.dumps(unselected_governed_recommendation_response()))],
            )

        if last_content is None or last_content.parts is None:
            if run_id:
                from backend.api.troubleshooting_guidance_context import discard_troubleshooting_guidance

                discard_troubleshooting_guidance(run_id)
            safe_error_resp = TechnicalAuthorityResponse(
                outcome=TechnicalAuthorityOutcome.ERROR,
                technical_interpretation="Technical Authority Engineer produced no content.",
                detail="Safe error boundary intercepted empty specialist response.",
            )
            return safe_error_resp.model_dump(mode="json")

        merged_text = "\n".join(p.text for p in last_content.parts if p.text and not p.thought)
        if output_schema:
            try:
                raw_result = validate_schema(output_schema, merged_text)
                if isinstance(raw_result, dict):
                    # Dynamic evidence refresh: specialist may have called knowledge_search
                    # and knowledge_select_evidence during execution. Refresh server-validated
                    # evidence and approved commands catalog so legitimately selected evidence
                    # is available to post-runner validation.
                    await ensure_governed_scope(run_id)
                    refreshed_evidence = build_server_validated_evidence(run_id, tool_context, caller_evidence)
                    candidate_commands = list(caller_commands)
                    if known_acquisition is not None and not known_acquisition.clarification_open:
                        # Model proposal + server-owned acquisition state -> reconciled proposal (a
                        # model omission never removes the known governed acquisition).
                        raw_result, acquisition_trace = _reconcile_known_acquisition(
                            raw_result, known_acquisition, continuation_rule, run_id=run_id,
                            progression=progression.progression, fault_id=troubleshooting_state.fault_id,
                            pending=pending_now, evidence=refreshed_evidence, remediation=acquisition_remediation,
                            raw_payloads=final_payloads,
                        )
                    # A governed alternative the server offered for an exhausted branch: re-derived from
                    # THIS run's SELECTED evidence and issued (never from AVAILABLE), then the normal chain.
                    _issue_offered_alternative(raw_result, gap_recovery, run_id, refreshed_evidence)
                    if resumed_from_clarification:
                        # Same server path as a known blocked action: the ProcedureAction resolver.
                        raw_result = _resume_on_procedure_action(raw_result, run_id=run_id, evidence=refreshed_evidence)
                    step = raw_result.get("diagnostic_step")
                    legacy_model_command = (str(step.get("command") or "").strip() or None) if isinstance(step, dict) else None
                    legacy_model_source = (str(step.get("command_source") or "").strip() or None) if isinstance(step, dict) else None
                    action_resolution: Optional[ProcedureActionResolution] = None
                    model_command: Optional[str] = None
                    if (
                        raw_result.get("outcome") == TechnicalAuthorityOutcome.RECOMMENDED.value
                        and isinstance(step, dict)
                        and str(step.get("procedure_action_id") or "").strip()
                    ):
                        # PREFERRED PATH: the model chose WHICH governed action; the server resolves
                        # HOW (template + trusted parameters). The model's own command text is
                        # never a candidate on this path.
                        raw_result, action_resolution, model_command = _apply_procedure_action(
                            raw_result,
                            run_id=run_id,
                            tool_context=tool_context,
                            evidence=refreshed_evidence,
                            # Operator-provided observed output of THIS thread only (parameter
                            # evidence, never command authority).
                            observed_texts=troubleshooting_state.trusted_observation_texts(),
                            # State changes: targets only from THIS fault's trusted case target facts.
                            target_facts=case_target_facts(progression.progression, troubleshooting_state.fault_id),
                            fault_id=troubleshooting_state.fault_id,
                            # Condition-scoped state changes: the named condition only from THIS
                            # fault's trusted, validated results.
                            case_results=trusted_case_results(progression.progression, troubleshooting_state.fault_id),
                            # Operator REQUESTS come from the operator's own words only; pasted /
                            # bound output is observation (target facts above), never a request.
                            operator_intent_text=progression.intent_text,
                        )
                        if action_resolution.candidate is not None:
                            candidate_commands.append(
                                action_resolution.candidate.as_authority_candidate(
                                    (raw_result.get("diagnostic_step") or {}).get("action")
                                )
                            )
                    elif isinstance(step, dict) and step.get("command"):
                        # LEGACY (deprecated) compatibility path: model-written command text is
                        # grounded against SELECTED evidence exactly as before -- no new authority.
                        candidate_commands.append({
                            "command": step["command"],
                            "source_id": step.get("command_source") or "",
                            "procedure_section": step.get("action"),
                            "restrictions": step.get("restrictions") or [],
                        })
                    refreshed_commands = build_server_validated_commands(refreshed_evidence, candidate_commands)
                    sanitized_args["verified_evidence"] = [e.model_dump(mode="json") for e in refreshed_evidence]
                    sanitized_args["approved_commands_catalog"] = [c.model_dump(mode="json") for c in refreshed_commands]

                    action_summary: Optional[dict[str, Any]] = None
                    if action_resolution is not None:
                        raw_result, action_summary = _finalize_procedure_action(
                            raw_result, action_resolution, refreshed_commands, model_command
                        )
                        if acquisition_trace is not None:
                            acquisition_trace["resolution"] = action_summary.get("status")
                            acquisition_trace["authority_decision"] = action_summary.get("command_authority")
                    # Deferred citation check (validation.py): the integrity callback let a ProcedureAction
                    # step through although its citation strings resolved to no SELECTED evidence. The
                    # server's resolution now decides: provenance proven -> its own source replaces the
                    # citations; not proven -> fail closed exactly as the citation check would have.
                    raw_result = _enforce_deferred_citation_check(raw_result, action_resolution, run_id)

                    tool_result, _ = validate_technical_authority_payload(
                        response_payload=raw_result,
                        request_payload=sanitized_args,
                        phase="post_run",
                    )
                    if action_summary is not None:
                        tool_result = dict(tool_result)
                        tool_result[PROCEDURE_ACTION_RESOLUTION_KEY] = action_summary
                    # Applicability-blocked governed action (identity only, ZERO authority): the
                    # command Command Authority just refused is matched against the deterministic
                    # actions of the SELECTED section it cites; kept only if the refusal was solely
                    # unresolved applicability. It never becomes a command, source or catalog entry.
                    # The specialist's own integrity callback may already have stripped the command from
                    # its final output, so the refused proposal is read from that message's raw model
                    # payload (the command text is only an input to the structural matcher).
                    validated_step = tool_result.get("diagnostic_step") if isinstance(tool_result, dict) else None
                    refused_command, refused_source = _latest_proposed_command(final_payloads, legacy_model_command, legacy_model_source)
                    if action_resolution is None and refused_command and isinstance(validated_step, dict) and not validated_step.get("command"):
                        blocked_action = applicability_blocked_action(refused_command, refused_source, refreshed_evidence)
                    # A refused model-written command that names no governed action of THIS run's SELECTED
                    # evidence (no selected procedure instructs it) is no acquisition method: its evidence
                    # need is decided like any method-less need (governed alternative / gap), never kept as
                    # a "blocked" method behind a command-less operator task.
                    refused_ungrounded = bool(
                        action_resolution is None and isinstance(validated_step, dict)
                        and not validated_step.get("command") and blocked_action is None
                        and _ungrounded_command(refused_command, refused_source, refreshed_evidence)
                    )
                    # Authorized legacy command (identity only, ZERO authority): the ProcedureAction it
                    # exactly corresponds to in THIS run's selected evidence, kept on the step and its
                    # acquisition candidate so the next turn knows the governed acquisition method.
                    legacy_action = (
                        _authorized_legacy_action(validated_step, refreshed_commands, refreshed_evidence)
                        if action_resolution is None
                        else None
                    )
                    # Progression Controller: sequencing only, BEFORE any operational control. The
                    # candidate's canonical identity is compared with the pending / completed /
                    # attempted steps: accepted as ONE new step, an explicitly justified re-check, or
                    # the pending step's own resolution; otherwise rejected (pending step awaits its
                    # result / out of order / several actions / duplicate). Authority, policy and
                    # HITL are unchanged.
                    progression_decision, tool_result = progression.evaluate_proposal(
                        tool_result, action_resolution, refreshed_evidence,
                        # Legacy path only: the model's own command text (authority may already
                        # have removed it). On the ProcedureAction path it is never a candidate.
                        proposed_command=None if action_resolution is not None else legacy_model_command,
                        blocked_action=blocked_action,
                    )
                    progression_check_id = progression.check_id_for(progression_decision, f"chk-{_uuid_mod.uuid4().hex[:8]}")
                    # Evidence acquisition (server-owned): WHAT the proposal needs and HOW it can be
                    # acquired -- existing evidence, an approved live source, an operator fact, a
                    # governed action with its CURRENT authority, a genuine manual observation -- or,
                    # after completed discovery, a governed acquisition gap (never a fake operator task).
                    if isinstance(tool_result, dict):
                        tool_result = {
                            k: v for k, v in tool_result.items()
                            if k not in (
                                EVIDENCE_ACQUISITION_KEY, SUPPORTING_EVIDENCE_KEY, SUPPORTING_EVIDENCE_IDENTITIES_KEY, GAP_RECOVERY_KEY,
                                ACTION_CHOICE_KEY, GAP_CONTINUATION_KEY,
                            )
                        }
                        branch_gaps: list[AcquisitionDecision] = []
                        if gap_recovery is not None and gap_recovery.get("offered"):
                            # The exhausted branch stays recorded whatever the specialist chose instead.
                            recorded_gap = await _record_branch_gap(gap_recovery, progression, troubleshooting_state.fault_id, run_id, refreshed_evidence)
                            branch_gaps += [recorded_gap] if recorded_gap is not None else []
                        if gap_continuation is not None:
                            # The branch the specialist was asked to continue past stays recorded too.
                            recorded_gap = await _record_branch_gap(gap_continuation, progression, troubleshooting_state.fault_id, run_id, refreshed_evidence)
                            branch_gaps += [recorded_gap] if recorded_gap is not None else []
                        if action_choice is not None:
                            # The specialist was offered this run's valid SELECTED actions: it chose one, or
                            # explicitly kept a method-less need (declined) -- only then may a gap be recorded.
                            final_step = raw_result.get("diagnostic_step") if isinstance(raw_result.get("diagnostic_step"), dict) else {}
                            chosen = str(final_step.get("procedure_action_id") or "").strip()
                            action_choice["chosen"] = chosen if chosen in {a.action.action_id for a in action_choice["actions"]} else None
                            action_choice["declined"] = not proposes_method(final_payloads, governed_command) and (
                                proposed_branch_requirement(
                                    final_payloads[-1] if final_payloads else None, troubleshooting_state.fault_id, governed_command,
                                    attempted_command=attempted_ungrounded_command(final_payloads, governed_command),
                                )[0] is not None
                            )
                        acquisition, acquisition_step, tool_result = await _decide_evidence_acquisition(
                            tool_result,
                            progression_decision,
                            pending_step=pending_now,
                            continuity_trace=acquisition_trace,
                            legacy_action=legacy_action,
                            action_resolution=action_resolution,
                            blocked_action=blocked_action,
                            refused_command=refused_command,
                            refreshed_evidence=refreshed_evidence,
                            progression=progression.progression,
                            fault_id=troubleshooting_state.fault_id,
                            run_id=run_id,
                            actions_declined=bool(action_choice and action_choice.get("declined")),
                            refused_ungrounded=refused_ungrounded,
                        )
                        if action_choice is not None:
                            final_gap = acquisition is not None and acquisition.outcome is AcquisitionOutcome.GAP
                            view = tool_result.get(EVIDENCE_ACQUISITION_KEY)
                            if final_gap and isinstance(view, dict):
                                # Declined for THIS need, but valid governed actions remain: not terminal.
                                view = {**view, "terminal": False}
                                if view.get("response_text"):
                                    view["response_text"] = f"{view['response_text']}\n\n{NON_TERMINAL_GAP_NOTE}"
                                tool_result[EVIDENCE_ACQUISITION_KEY] = view
                            tool_result[ACTION_CHOICE_KEY] = {
                                "requirement": action_choice["requirement"].description,
                                "offered": [a.action.action_id for a in action_choice["actions"]],
                                "chosen": action_choice["chosen"], "declined": action_choice["declined"],
                                "acquisition_outcome": acquisition.outcome if acquisition is not None else None,
                                "blocking_reason": acquisition.requirement.blocking_reason.value
                                if acquisition is not None and acquisition.requirement.blocking_reason else None,
                            }
                            try:
                                from backend.tools.knowledge.diagnostic_trace import record_operational_event

                                record_operational_event({"stage": "action_choice", **tool_result[ACTION_CHOICE_KEY]})
                            except Exception:
                                pass
                        if gap_recovery is not None:
                            # Terminal only when the fault has no other valid governed branch; otherwise the
                            # gap is reported as a branch limitation (and stays recorded either way).
                            final_gap = acquisition is not None and acquisition.outcome is AcquisitionOutcome.GAP
                            gap_recovery["terminal"] = final_gap and not gap_recovery["alternatives"]
                            view = tool_result.get(EVIDENCE_ACQUISITION_KEY)
                            if final_gap and isinstance(view, dict):
                                view = {**view, "terminal": gap_recovery["terminal"]}
                                if not gap_recovery["terminal"] and view.get("response_text"):
                                    view["response_text"] = f"{view['response_text']}\n\n{NON_TERMINAL_GAP_NOTE}"
                                tool_result[EVIDENCE_ACQUISITION_KEY] = view
                            tool_result[GAP_RECOVERY_KEY] = _gap_recovery_view(gap_recovery)
                            try:
                                from backend.tools.knowledge.diagnostic_trace import record_operational_event

                                record_operational_event({"stage": "gap_recovery", **tool_result[GAP_RECOVERY_KEY]})
                            except Exception:
                                pass
                        if branch_gaps or gap_continuation is not None:
                            continuation_view = _gap_continuation_view(branch_gaps, acquisition, gap_continuation)
                            if continuation_view["gaps"]:
                                tool_result[GAP_CONTINUATION_KEY] = continuation_view
                            _record_gap_continuation(continuation_view, tool_result, acquisition)
                    step_after = tool_result.get("diagnostic_step") if isinstance(tool_result, dict) else None
                    step_disposition = _operational_step_disposition(
                        proposed=selection_fail_closed or any(proposes_operational_step(p) for p in all_payloads),
                        tool_result=tool_result, action_resolution=action_resolution, refused_command=refused_command,
                        progression_decision=progression_decision, run_id=run_id,
                    )
                    if (
                        action_resolution is not None
                        and action_resolution.candidate is not None
                        and progression_check_id is not None
                        and tool_result.get("outcome") == TechnicalAuthorityOutcome.RECOMMENDED.value
                        and isinstance(step_after, dict)
                    ):
                        # Tranche 3: operational control plane AFTER Command Authority (policy,
                        # target confirmation request, read-execution eligibility). Never grants
                        # command authority; never executes. Imported here, not at module top:
                        # backend.operations.control_plane imports this package's schemas, so a
                        # top-level import would be circular when control_plane loads first
                        # (the app.py -> approval_service startup path).
                        from backend.operations.control_plane import plan_operational_control

                        pending_check_id = progression_check_id
                        operational_summary = plan_operational_control(
                            action_resolution,
                            refreshed_commands,
                            refreshed_evidence,
                            state=tool_context.state,
                            check_id=pending_check_id,
                            run_id=run_id,
                            session_id=session_id,
                            case_id=case_id,
                            reason=str(step_after.get("reason") or ""),
                            fault_id=troubleshooting_state.fault_id,
                        )
                        if operational_summary is not None:
                            tool_result = dict(tool_result)
                            tool_result[OPERATIONAL_CONTROL_KEY] = operational_summary
                            if operational_summary.get("target_confirmation_required"):
                                step_after = dict(step_after)
                                step_after["restrictions"] = list(step_after.get("restrictions") or []) + [
                                    f"[{operational_summary['blocker']}]"
                                ]
                                tool_result["diagnostic_step"] = step_after
                    if acquisition is not None:
                        # AUDIT of this run's authority decision (Command Authority + policy of THIS run);
                        # never read back as authority by any later run.
                        decision_record = authority_decision(acquisition.requirement, operational_summary, run_id)
                        if decision_record is not None:
                            acquisition.requirement.record_authority(decision_record)
                    # Deterministic invariant: no governed operational recommendation survives with
                    # no SELECTED governed evidence and an empty ProcedureAction catalog.
                    tool_result = enforce_selected_evidence_invariant(tool_result, refreshed_evidence, run_id)
                    # Escalation is a PROPOSAL: the deterministic escalation gate (server state +
                    # server-owned policy) decides; only an accepted proposal transitions the fault.
                    knowledge_gap = bool(run_id) and (
                        has_knowledge_run_state(run_id)
                        and not negative_selection_preexisting
                        and has_explicit_empty_knowledge_selection(run_id)
                        and not snapshot_selected_knowledge_evidence(run_id)
                    )
                    _, tool_result = progression.evaluate_escalation(tool_result, refreshed_evidence, knowledge_gap=knowledge_gap)
                else:
                    tool_result = raw_result
            except Exception as e:
                logger.error("Technical Authority Engineer output schema validation failed: %s", e)
                if run_id:
                    from backend.api.troubleshooting_guidance_context import discard_troubleshooting_guidance

                    discard_troubleshooting_guidance(run_id)
                safe_error_resp = TechnicalAuthorityResponse(
                    outcome=TechnicalAuthorityOutcome.ERROR,
                    technical_interpretation="Technical Authority Engineer produced a malformed result schema.",
                    detail=f"Safe error boundary intercepted schema validation error: {e}",
                )
                return safe_error_resp.model_dump(mode="json")
        else:
            tool_result = merged_text

        # 5. Persist recommended checks and record specialist execution
        if isinstance(tool_result, dict):
            step_out = tool_result.get("diagnostic_step")
            if run_id and not (isinstance(step_out, dict) and step_out.get("command")):
                missing_dims: list[str] = []
                for ev in sanitized_args.get("verified_evidence") or []:
                    meta = ev.get("metadata") if isinstance(ev, dict) and isinstance(ev.get("metadata"), dict) else {}
                    if ev.get("source_type") == "governed_knowledge" and meta.get("applicability_outcome") in ("unknown", "partial_match"):
                        missing_dims.extend(meta.get("unresolved_applicability_dimensions") or [])
                if not missing_dims:
                    missing_dims = get_available_unresolved_applicability_dimensions(run_id)
                clarification = build_applicability_clarification(confirmed_facts, missing_dims, unconfirmed_facts)
                if clarification:
                    tool_result = dict(tool_result)
                    tool_result["applicability_clarification"] = clarification
            # Only the clarification short-circuit above ever sets the meta-response key.
            tool_result = {k: v for k, v in tool_result.items() if k != CLARIFICATION_CONTINUITY_KEY}
            tool_result = _attach_pending_clarification(
                tool_result, progression.progression, troubleshooting_state.fault_id, confirmed_facts,
                run_id=run_id, verified_evidence=sanitized_args.get("verified_evidence") or [],
            )
            # Which SELECTED evidence materially grounds what is presented (the only evidence shown
            # as an authoritative Source); everything else selected is consulted evidence.
            tool_result[SUPPORTING_EVIDENCE_KEY] = _supporting_evidence(
                tool_result, sanitized_args.get("verified_evidence") or [], blocked_action
            )
            tool_result[SUPPORTING_EVIDENCE_IDENTITIES_KEY] = [
                identity.selection_key()
                for identity in _evidence_identities(tool_result[SUPPORTING_EVIDENCE_KEY], sanitized_args.get("verified_evidence") or [])
            ]
            # Final-response completeness (server-owned; no authority): a turn the server continued
            # must not end with nothing while actionable investigation state exists.
            completeness = _response_completeness(
                tool_result, rule=continuation_rule, run_id=run_id, progression=progression, fault_id=troubleshooting_state.fault_id,
                remediation=resume_completeness,
            )
            tool_result.pop(RESPONSE_COMPLETENESS_KEY, None)
            if completeness is not None:
                tool_result[RESPONSE_COMPLETENESS_KEY] = completeness
            if run_id:
                execution_record = dict(tool_result)
                if isinstance(sanitized_args, dict) and "approved_commands_catalog" in sanitized_args:
                    execution_record["approved_commands_catalog"] = sanitized_args["approved_commands_catalog"]
                    execution_record["verified_evidence"] = sanitized_args.get("verified_evidence", [])
                # Server-owned identity of this authorization record (command egress boundary):
                # the run and the fault it was produced for. Never model-supplied.
                execution_record["run_id"] = run_id
                execution_record["fault_id"] = troubleshooting_state.fault_id
                # Server-owned selection-contract facts (always overwritten; never model-supplied).
                # Consumed only by the completion boundary in chat_service; confers no authority.
                execution_record[SELECTION_CONTRACT_RECORD_KEY] = {
                    "governed_search_performed": has_knowledge_run_state(run_id),
                    "explicit_negative_selection": (
                        not negative_selection_preexisting
                        and has_explicit_empty_knowledge_selection(run_id)
                        and not snapshot_selected_knowledge_evidence(run_id)
                    ),
                    "operational_step_proposed": selection_fail_closed
                    or any(proposes_operational_step(p) for p in all_payloads),
                    # What became of it: accepted / integrity_rejected / action_resolution_rejected /
                    # command_authority_rejected / progression_rejected / accepted_without_command / none.
                    "operational_step_disposition": step_disposition,
                }
                # Server-owned: how this run's final structured answer was obtained (no authority).
                execution_record[STRUCTURED_OUTPUT_RECORD_KEY] = structured_output.summary()
                record_technical_authority_execution(run_id, execution_record)

            step_data = tool_result.get("diagnostic_step")
            recorded_step = None
            if (
                tool_result.get("outcome") == TechnicalAuthorityOutcome.RECOMMENDED.value
                and isinstance(step_data, dict)
                and step_data.get("action")
                and progression_decision in ACCEPTED_DECISIONS
            ):
                resolved_action = tool_result.get(PROCEDURE_ACTION_RESOLUTION_KEY)
                operational = tool_result.get(OPERATIONAL_CONTROL_KEY)
                action_id = (
                    resolved_action.get("action_id")
                    if isinstance(resolved_action, dict)
                    and resolved_action.get("status") != ProcedureActionResolutionStatus.UNKNOWN_ACTION.value
                    else None
                )
                if action_id is None:
                    continuity_action = _legacy_action_for(step_data, legacy_action)
                    action_id = continuity_action.action_id if continuity_action is not None else None
                check_id = (operational.get("check_id") if isinstance(operational, dict) else None) or progression_check_id
                # The progression is the ONLY writer: the step (and the check record projected from
                # it) is created or updated here; the fault-thread state is re-derived on save.
                governed = [ev for ev in (sanitized_args.get("verified_evidence") or []) if isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge"]
                recorded_step = progression.record(
                    progression_decision,
                    {**tool_result, "diagnostic_step": {**step_data, "procedure_action_id": action_id}},
                    check_id,
                    selected_evidence_ids=[ev["source_id"] for ev in governed],
                    applicability={ev["source_id"]: (ev.get("metadata") or {}).get("applicability_outcome") for ev in governed},
                    target=(operational or {}).get("target") if isinstance(operational, dict) else None,
                    control_id=operational.get("control_id") if isinstance(operational, dict) else None,
                    control_stage=operational.get("control_stage") if isinstance(operational, dict) else None,
                    # Kept only while this turn leaves an applicability clarification open.
                    blocked_candidate=blocked_action
                    if (tool_result.get("applicability_clarification") or {}).get("missing_dimensions")
                    else None,
                    selected_evidence=identities_of(governed),
                    command_source_identity=next(
                        iter(_evidence_identities([step_data["command_source"]], governed)), None
                    ) if step_data.get("command_source") else None,
                )
            if acquisition is not None:
                _persist_acquisition(progression, acquisition, acquisition_step, progression_decision, progression_check_id, recorded_step)
            tool_result = dict(tool_result)
            if acquisition_trace is None and acquisition_binding is not None:
                acquisition_trace = {
                    "stage": "acquisition_continuity",
                    "rule": ContinuationRule.ACQUISITION_REQUEST.value,
                    "requirement_id": acquisition_binding.requirement_id,
                    "governed_search_performed": bool(run_id) and has_knowledge_run_state(run_id),
                    "selected_sources": [i.canonical for i in _run_governed_view(run_id)[0]],
                    "remediation": acquisition_remediation,
                    "outcome": (
                        "no_known_governed_acquisition" if known_acquisition is None
                        else "awaiting_applicability_clarification" if known_acquisition.clarification_open
                        else "not_continued"
                    ),
                }
            if acquisition_trace is not None:
                if acquisition_binding is not None:
                    acquisition_trace["binding"] = {"requirement_id": acquisition_binding.requirement_id, "rule": acquisition_binding.rule}
                if acquisition is not None:
                    acquisition_trace["requirement_id"] = acquisition.requirement.requirement_id
                    acquisition_trace["acquisition_candidate_id"] = acquisition.requirement.selected_acquisition_id
                    acquisition_trace["acquisition_outcome"] = acquisition.outcome
                    acquisition_trace["requirement_continuity"] = acquisition.continuity
                presented = tool_result.get("diagnostic_step") if isinstance(tool_result.get("diagnostic_step"), dict) else {}
                acquisition_trace["progression_decision"] = progression_decision.value
                acquisition_trace["command_presented"] = bool(str(presented.get("command") or "").strip())
                tool_result[ACQUISITION_CONTINUITY_KEY] = {k: v for k, v in acquisition_trace.items() if k != "stage"}
                _record_acquisition_continuity(acquisition_trace)
            pending_after = progression.pending_step()
            _record_continuation_state(progression, troubleshooting_state.fault_id, recorded_step)
            _record_fault_clarifications(
                progression.progression,
                troubleshooting_state.fault_id,
                tool_result,
                sanitized_args.get("verified_evidence") or [],
                confirmed_facts,
                pending_after.step_id if pending_after is not None else None,
                run_id=run_id,
                requirement_id=(
                    acquisition.requirement.requirement_id
                    if acquisition is not None and acquisition.requirement.blocking_reason is GapReason.APPLICABILITY_UNRESOLVED
                    else pending_after.evidence_requirement_id if pending_after is not None else None
                ),
            )
            if resolved_applicability_clarification is not None:
                # What THIS run evaluated after the answer (audit); a MATCH clears stale blockers.
                _settle_applicability_clarification(
                    progression.progression, troubleshooting_state.fault_id, resolved_applicability_clarification,
                    sanitized_args.get("verified_evidence") or [], run_id,
                )
            _link_blocked_step_clarification(progression.progression, troubleshooting_state.fault_id, pending_after)
            if retrieval_resumption is not None:
                _record_resumed_retrieval_outcome(run_id, retrieval_resumption, sanitized_args.get("verified_evidence") or [])
            progression.refresh_projection()
            if tool_result.get("hypothesis_updates"):
                # Proposals only: replaced by what the server applied (evidence-bound; never self-confirmed).
                tool_result["hypothesis_updates"] = progression.apply_hypothesis_updates(tool_result["hypothesis_updates"])
            tool_result[PROGRESSION_CONTROL_KEY] = progression.summary(progression_decision)
            try:
                await progression_repository.save(progression.progression, troubleshooting_state.fault_id)
            except ProgressionConflict as conflict:
                return _progression_conflict_response(conflict)
            try:
                from backend.tools.knowledge.diagnostic_trace import record_operational_event

                record_operational_event({"stage": "progression", **tool_result[PROGRESSION_CONTROL_KEY]})
            except Exception:
                pass

        # Deterministic result arbitration:
        # When TAE executes and produces a result, TAE is the authoritative technical specialist.
        # Discard any legacy troubleshooting guidance registered by prior tool calls (e.g. incident_manager)
        # in this turn so ungrounded/legacy advice never overrides TAE's authoritative evaluation.
        if run_id:
            from backend.api.troubleshooting_guidance_context import discard_troubleshooting_guidance

            discard_troubleshooting_guidance(run_id)

        if self.propagate_grounding_metadata and last_grounding_metadata:
            tool_context.state["temp:_adk_grounding_metadata"] = last_grounding_metadata

        if isinstance(tool_result, dict):
            # The gap notice is rendered by the server from the execution record (never re-worded by the
            # Team Manager): the record keeps it, the Team Manager's input does not.
            tool_result = {k: v for k, v in tool_result.items() if k != GAP_CONTINUATION_KEY}
        return tool_result
