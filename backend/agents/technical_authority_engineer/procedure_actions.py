"""Governed ProcedureAction -> deterministic command resolution (Troubleshooting Tranche 2).

TRUST CHAIN (each arrow is a separate, deterministic server gate):

    SELECTED governed evidence (approved, current, applicability MATCH)
      -> ProcedureAction          (derived here from the exact section text; NOT authorization)
      -> server-issued catalog    (bounded, per run; the model may only choose an action_id from it)
      -> trusted parameter binding (operator-stated literal values / session-confirmed values)
      -> ResolvedCommandCandidate (strict template render; NOT authorization)
      -> EXISTING Command Authority (`build_server_validated_commands`): grounding, operation
         classification, placeholder check, target confirmation -- the only authorizing step.

MATERIALIZATION: a ProcedureAction is a pure, deterministic function of one governed section's
identity (knowledge_id / version_label / section_id) and its exact text. It is re-derived from the
trusted SELECTED evidence every time it is used and is never persisted as an independent record,
so it cannot outlive or diverge from the governed source: a new version yields new action ids, an
unselected / non-approved / non-MATCH source yields no usable action. Lifecycle and applicability
are never copied into the action; they are read from the current run's server-validated evidence.

EXTRACTION NEVER INVENTS: only text that is explicitly command-formatted in the source becomes a
command template -- inline code spans, fenced code blocks, or the lines of an explicit
"...command(s):" block. Prose ("Inspect optical levels.") never becomes a command. A command that
the source mentions in a prohibition/warning context is never materialized. No model is involved.

SEMANTICS (procedure_semantics.py): every candidate is classified from explicit evidence as a
PARAMETERIZED_TEMPLATE, FIXED_INSTANCE, EXPLICIT_EXAMPLE, SAMPLE_OUTPUT, SCREENSHOT_TRANSCRIPTION
or UNKNOWN; only the first two become actions. A literal is never turned into a slot.
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from google.adk.tools import ToolContext
from pydantic import BaseModel, Field

from backend.agents.technical_authority_engineer.schemas import CommandOperationType, EvidenceReference
from backend.agents.technical_authority_engineer.command_syntax import single_invocation_violation
from backend.agents.technical_authority_engineer import procedure_semantics as ps
from backend.agents.technical_authority_engineer.procedure_semantics import BlockKind, InstanceSemantics
from backend.agents.technical_authority_engineer.validation import is_command_prohibited_in_snippet
from backend.cases.evidence_identity import identity_of
from backend.operations.signing import sign, verify

CONFIRMED_PROCEDURE_PARAMETERS_STATE_KEY = "confirmed_procedure_parameters"
"""Session-state key: {parameter_name: value} values previously VERIFIED from operator text."""

PROCEDURE_ACTION_RESOLUTION_KEY = "procedure_action_resolution"
"""Server-owned tool-result / execution-record key (always overwritten, never model-supplied)."""

NO_APPROVED_COMMAND_FOR_ACTION_TEXT = (
    "Relevant governed troubleshooting guidance was found, but no approved operational command is "
    "available for this diagnostic action."
)

_SUPPORTED_PLACEHOLDER = re.compile(r"<([A-Za-z0-9_\-]+)>|\{([A-Za-z0-9_\-]+)\}")
_KEYED_DOCUMENT_PLACEHOLDER = re.compile(r"(?<![A-Za-z0-9_\-])([A-Za-z][A-Za-z0-9_\-]*)=(x{3,})(?![A-Za-z0-9_\-])", re.IGNORECASE)
"""A document's own placeholder notation for a keyed argument, e.g. `SomeKey=xxxx`: the run of
`x` is the value to supply and the parameter is named by its key. Only a keyed run qualifies; a
bare `xxxx` has no deterministic name and is never treated as a parameter."""
_ARGUMENT_TOKEN = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*=\S+$")
_COMMAND_VERB = re.compile(r"^[a-z][a-z0-9_\-]*$")
_ANY_PLACEHOLDER = re.compile(r"<[^>]+>|\$[A-Za-z_][A-Za-z0-9_]*|\{[^}]+\}")
"""Mirrors agent_tool._PLACEHOLDER_REGEX (the authority's unresolved-placeholder check)."""

_PARAMETER_VALUE = re.compile(r"^[A-Za-z0-9_\-\.:/]+$")
"""Mirrors the value charset validation._template_to_regex accepts for a placeholder, so a bound
value can never smuggle separators, whitespace or shell syntax into a rendered command."""

_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_FENCED_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_COMMAND_BLOCK_LABEL = re.compile(r"^\s*([^:\n]{0,80}\bcommands?\b[^:\n]{0,40}):\s*$", re.IGNORECASE)
_MAX_TEMPLATE_CHARS = 160
_MAX_TEMPLATE_TOKENS = 12


class ProcedureActionType(str, Enum):
    DIAGNOSTIC_READ = "diagnostic_read"
    STATE_CHANGE = "state_change"
    MANUAL_CHECK = "manual_check"
    """Reserved: a check with no command. The deterministic extractor never produces one."""


class ProcedureActionParameter(BaseModel):
    name: str
    placeholder: str
    key: Optional[str] = Field(default=None, description="The identifier key written before the placeholder (`Key=xxxx`); None if untyped.")


class ActionTargetKind(str, Enum):
    PARAMETERIZED = "parameterized"
    """An explicit placeholder slot (`key=xxxx`, `<name>`, `{name}`): bound only from a trusted
    current-case target fact of the same key."""
    FIXED = "fixed"
    """A literal `key=value` instance written in the governed command: valid only if that exact
    instance is a trusted current-case target fact (the document text alone proves nothing)."""


class ActionTarget(BaseModel):
    """One target requirement of a governed action, as the procedure text explicitly writes it."""

    key: Optional[str] = Field(
        default=None,
        description="The identifier key the procedure writes before `=`; None for an untyped slot (`<name>` / `{name}` "
        "with no key): any identifier observed in this fault, matched exactly, may fill it.",
    )
    kind: ActionTargetKind
    parameter: Optional[str] = Field(default=None, description="Placeholder parameter name (parameterized slots).")
    fixed_value: Optional[str] = Field(default=None, description="Literal instance value (fixed targets).")

    def view(self) -> dict[str, Any]:
        return {"key": self.key, "kind": self.kind.value, "parameter": self.parameter, "fixed_value": self.fixed_value}


class ProcedureActionSource(BaseModel):
    knowledge_id: str
    version_label: str
    section_id: str
    source_locator: Optional[str] = None
    canonical_source_id: str
    title: Optional[str] = None
    heading: Optional[str] = None


class RelationshipType(str, Enum):
    MANDATORY_PREREQUISITE = "mandatory_prerequisite"
    """The governed text EXPLICITLY obliges the related action first ("must", "required",
    "prerequisite", "only after", ...). The ONLY relationship the server enforces."""
    RECOMMENDED_BEFORE = "recommended_before"
    """Temporal / advisory ordering without obligation ("before X, check Y", "should", "may").
    Guidance for the agent; never enforced."""
    POST_ACTION_VERIFICATION = "post_action_verification"
    """After the owning (state-changing) action, the procedure re-runs the related read."""
    UNKNOWN = "unknown"
    """The text suggests a relationship but not unambiguously: recorded, never enforced."""


class ActionRelationship(BaseModel):
    """A structured relationship `owner --relationship_type--> related_action_id` between two
    ProcedureActions of the SAME governed section, derived deterministically at extraction time,
    with provenance back to the exact governed line. For prerequisites the owner is the dependent
    action (B MANDATORY_PREREQUISITE A = "B requires A")."""

    relationship_type: RelationshipType
    related_action_id: str
    rule: str
    source_locator: Optional[str] = None
    line: int = Field(..., description="1-based line in the governed section.")
    text: str = Field(..., description="The governed line the relationship was derived from.")


class ProcedureAction(BaseModel):
    """Server-owned description of HOW a governed procedure performs one check. Not authorization."""

    action_id: str
    intent: str
    action_type: ProcedureActionType
    operation_type: CommandOperationType
    command_template: str
    execution_context: Optional[str] = Field(
        default=None, description="Never inferred; only set when the source states it explicitly (not yet extracted)."
    )
    parameters: list[ProcedureActionParameter] = Field(default_factory=list)
    source: ProcedureActionSource
    restrictions: list[str] = Field(default_factory=list)
    description: str = ""
    extraction_method: str
    sequence: int = Field(default=0, description="Document order of the action within its section (0-based).")
    relationships: list[ActionRelationship] = Field(default_factory=list)
    targets: list[ActionTarget] = Field(
        default_factory=list, description="Target requirements the governed command explicitly writes (cardinality = len)."
    )
    instance_semantics: InstanceSemantics = Field(
        default=InstanceSemantics.FIXED_INSTANCE,
        description="parameterized_template (explicit placeholder) or fixed_instance (literal, valid only as written).",
    )
    semantics_reason: str = ""
    source_line: Optional[int] = Field(default=None, description="1-based line of the command in its governed section.")
    state_change_class: Optional[str] = Field(default=None, description="The state-changing operation word parsed from the command.")
    conditions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="State changes: governed precondition / post-action / block-condition text (verbatim, with provenance) "
        "that confirmation and approval are bound to. Only from explicit document structure.",
    )
    condition_scope: Optional[dict[str, Any]] = Field(
        default=None,
        description="State changes inside a condition-specific block: {dimension, value, label, source}; the action is "
        "applicable only when this investigation's trusted evidence states that condition.",
    )

    @property
    def risk_class(self) -> str:
        return self.operation_type.value

    def related(self, *types: RelationshipType) -> list[ActionRelationship]:
        return [r for r in self.relationships if r.relationship_type in types]

    @property
    def mandatory_prerequisite_ids(self) -> list[str]:
        return [r.related_action_id for r in self.related(RelationshipType.MANDATORY_PREREQUISITE)]

    @property
    def recommended_before_ids(self) -> list[str]:
        return [r.related_action_id for r in self.related(RelationshipType.RECOMMENDED_BEFORE)]

    @property
    def verification_action_ids(self) -> list[str]:
        return [r.related_action_id for r in self.related(RelationshipType.POST_ACTION_VERIFICATION)]

    def catalog_entry(self) -> dict[str, Any]:
        """The only view the model sees: no template, source identity or risk field to alter."""
        return {
            "action_id": self.action_id,
            "intent": self.intent,
            "action_type": self.action_type.value,
            "description": self.description,
            "required_parameters": [p.name for p in self.parameters],
            "restrictions": list(self.restrictions),
            "procedure": self.source.title,
            "section": self.source.heading,
            # Guidance for the agent's own planning; only mandatory prerequisites are server-enforced.
            "mandatory_prerequisite_action_ids": self.mandatory_prerequisite_ids,
            "recommended_before_action_ids": self.recommended_before_ids,
            # A state change is offered only for a target observed in this investigation's trusted
            # evidence (key and value exactly as observed); never an invented or operator-only value.
            "target_requirements": [{"key": t.key, "kind": t.kind.value} for t in self.targets]
            if self.action_type is ProcedureActionType.STATE_CHANGE
            else [],
            "instance_semantics": self.instance_semantics.value,
            "applies_only_to_condition": (
                {"dimension": self.condition_scope.get("dimension"), "value": self.condition_scope.get("value")}
                if self.condition_scope else None
            ),
            "governed_conditions": [c["text"] for c in self.conditions],
        }

    def governance_view(self) -> dict[str, Any]:
        """Semantics + governed conditions/scope (identities and verbatim governed text only)."""
        return {
            "instance_semantics": self.instance_semantics.value,
            "semantics_reason": self.semantics_reason,
            "source_line": self.source_line,
            "state_change_class": self.state_change_class,
            "targets": [t.view() for t in self.targets],
            "parameter_schema": [{"name": p.name, "key": p.key, "placeholder": p.placeholder} for p in self.parameters],
            "conditions": [dict(c) for c in self.conditions],
            "condition_scope": dict(self.condition_scope) if self.condition_scope else None,
        }


class ParameterBindingState(str, Enum):
    VERIFIED = "verified"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    CONFLICTING = "conflicting"


class ParameterBinding(BaseModel):
    name: str
    state: ParameterBindingState
    value: Optional[str] = None
    detail: Optional[str] = None


class ProcedureActionResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    UNKNOWN_ACTION = "unknown_action"
    SOURCE_NOT_SELECTED = "source_not_selected"
    SOURCE_NOT_AUTHORITATIVE = "source_not_authoritative"
    PROHIBITED = "prohibited"
    PARAMETERS_UNRESOLVED = "parameters_unresolved"
    NOT_SINGLE_INVOCATION = "not_single_invocation"
    TARGET_NOT_VALIDATED = "target_not_validated"
    """A state change whose target is not a trusted current-case target (missing, ambiguous,
    unobserved, conflicting, fixed literal not in case, or no declared target)."""
    GOVERNED_SCOPE_UNAVAILABLE = "governed_scope_unavailable"
    """A state change whose governed version's document-level structure (general preconditions,
    condition scope) could not be established: never offered on the section text alone."""
    CONDITION_NOT_ESTABLISHED = "condition_not_established"
    """A state change scoped to a named condition the investigation's trusted evidence has not shown."""


class TargetDecisionStatus(str, Enum):
    VALIDATED = "validated"
    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    NOT_OBSERVED = "not_observed"
    CONFLICTING = "conflicting"
    FIXED_NOT_IN_CASE = "fixed_target_not_in_case"


class TargetDecision(BaseModel):
    key: Optional[str]
    kind: ActionTargetKind
    parameter: Optional[str] = None
    requested: list[str] = Field(default_factory=list, description="Untrusted requested values (model / operator / earlier confirmation).")
    status: TargetDecisionStatus
    value: Optional[str] = None
    fact_ids: list[str] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)
    detail: str = ""

    def view(self) -> dict[str, Any]:
        return {
            "key": self.key, "kind": self.kind.value, "parameter": self.parameter, "requested": list(self.requested),
            "status": self.status.value, "value": self.value, "fact_ids": list(self.fact_ids),
            "provenance": list(self.provenance), "detail": self.detail,
        }


class TargetValidation(BaseModel):
    """The target gate decision for one state-changing action resolution (server-owned)."""

    passed: bool
    fault_id: Optional[str] = None
    targets: list[TargetDecision] = Field(default_factory=list)
    reason: str = ""
    observed_keys: list[str] = Field(default_factory=list)

    def view(self) -> dict[str, Any]:
        return {
            "passed": self.passed, "fault_id": self.fault_id, "reason": self.reason, "observed_keys": list(self.observed_keys),
            "targets": [t.view() for t in self.targets],
        }

    def authority_view(self) -> dict[str, Any]:
        """What Command Authority checks: the validated `key=value` identities and their facts."""
        return {
            "passed": self.passed, "fault_id": self.fault_id,
            "targets": [
                {"key": t.key, "parameter": t.parameter, "value": t.value, "kind": t.kind.value, "fact_ids": list(t.fact_ids)}
                for t in self.targets
            ],
        }


_RESOLVER_ATTESTATION_KIND = "resolved_command_candidate.v1"


class ResolvedCommandCandidate(BaseModel):
    """A fully rendered command candidate. NOT authorized until Command Authority accepts it.

    `attestation` is a process-scoped HMAC (backend.operations.signing) over the candidate's
    command, source, action, template, bound parameters and operation type, minted ONLY by this
    module's resolver/re-derivation. It is the server-side proof that a command dict handed to
    Command Authority came from the ProcedureAction resolver -- never a model-supplied flag.
    """

    command: str
    source_id: str
    action_id: str
    command_template: str
    bound_parameters: dict[str, str] = Field(default_factory=dict)
    operation_type: CommandOperationType
    attestation: str = ""

    def _attested_fields(self) -> dict[str, Any]:
        return _resolver_fields(
            self.command, self.source_id, self.action_id, self.command_template, self.bound_parameters, self.operation_type.value
        )

    def as_authority_candidate(self, procedure_section: Optional[str]) -> dict[str, Any]:
        return {
            "command": self.command,
            "source_id": self.source_id,
            "procedure_section": procedure_section,
            "restrictions": [],
            "procedure_action_id": self.action_id,
            "command_template": self.command_template,
            "bound_parameters": dict(self.bound_parameters),
            "operation_type": self.operation_type.value,
            "resolver_attestation": self.attestation,
        }


def _resolver_fields(
    command: str, source_id: str, action_id: str, template: str, parameters: Mapping[str, str], operation_type: str
) -> dict[str, Any]:
    return {
        "command": command,
        "source_id": source_id,
        "action_id": action_id,
        "command_template": template,
        "bound_parameters": dict(sorted(parameters.items())),
        "operation_type": operation_type,
    }


def _signed_candidate(action: "ProcedureAction", command: str, parameters: Mapping[str, str]) -> ResolvedCommandCandidate:
    candidate = ResolvedCommandCandidate(
        command=command,
        source_id=action.source.canonical_source_id,
        action_id=action.action_id,
        command_template=action.command_template,
        bound_parameters=dict(parameters),
        operation_type=action.operation_type,
    )
    candidate.attestation = sign(_RESOLVER_ATTESTATION_KIND, candidate._attested_fields())
    return candidate


def verified_resolver_candidate(candidate: Any) -> Optional[tuple[str, dict[str, str]]]:
    """(template, bound parameters) when `candidate` (an authority candidate dict) carries a genuine
    resolver attestation over exactly its command, source, action, template, parameters and
    operation type; otherwise None. Model-written/caller-supplied dicts can never pass: the HMAC key
    is process-scoped and never leaves the server."""
    if not isinstance(candidate, Mapping):
        return None
    try:
        parameters = {str(k): str(v) for k, v in dict(candidate.get("bound_parameters") or {}).items()}
        fields = _resolver_fields(
            str(candidate.get("command") or ""),
            str(candidate.get("source_id") or ""),
            str(candidate.get("procedure_action_id") or ""),
            str(candidate.get("command_template") or ""),
            parameters,
            str(candidate.get("operation_type") or ""),
        )
    except Exception:
        return None
    if not verify(_RESOLVER_ATTESTATION_KIND, fields, str(candidate.get("resolver_attestation") or "")):
        return None
    return fields["command_template"], parameters


class ProcedureActionResolution(BaseModel):
    status: ProcedureActionResolutionStatus
    action_id: str
    action: Optional[ProcedureAction] = None
    bindings: list[ParameterBinding] = Field(default_factory=list)
    candidate: Optional[ResolvedCommandCandidate] = None
    reason: str = ""
    target_validation: Optional[TargetValidation] = None
    condition_check: Optional[dict[str, Any]] = None


# ---------------------------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------------------------


def _action_id(knowledge_id: str, version_label: str, section_id: str, template: str) -> str:
    digest = hashlib.sha256("\x1f".join((knowledge_id, version_label, section_id, template)).encode("utf-8"))
    return f"pa-{digest.hexdigest()[:20]}"


def template_placeholders(template: str) -> list[tuple[str, int, int]]:
    """(parameter name, start, end) of every placeholder span in source order: `<name>`,
    `{name}`, or the value run of a keyed document placeholder (`key=xxxx` -> name `key`)."""
    spans = [(m.group(1) or m.group(2), m.start(), m.end()) for m in _SUPPORTED_PLACEHOLDER.finditer(template)]
    spans += [(m.group(1), m.start(2), m.end(2)) for m in _KEYED_DOCUMENT_PLACEHOLDER.finditer(template)]
    return sorted(spans, key=lambda span: span[1])


def _substitute(template: str, values: Mapping[str, str]) -> str:
    out, cursor = [], 0
    for name, start, end in template_placeholders(template):
        out.append(template[cursor:start])
        out.append(values[name])
        cursor = end
    out.append(template[cursor:])
    return "".join(out)


def _neutralize_placeholders(template: str) -> str:
    return _substitute(template, {name: "X" for name, _, _ in template_placeholders(template)})


def template_regex_body(template: str) -> str:
    """Regex body for a governed template: literal text escaped, each placeholder span replaced by
    the command-parameter charset (the same charset validation._template_to_regex accepts)."""
    parts, cursor = [], 0
    for _, start, end in template_placeholders(template):
        parts.append(re.escape(template[cursor:start]))
        parts.append(r"[A-Za-z0-9_\-\.:/]+")
        cursor = end
    parts.append(re.escape(template[cursor:]))
    return "".join(parts)


def command_matches_template(command: str, template: str) -> bool:
    """True when `command` is exactly `template` with every placeholder filled by a charset-valid
    value (case-insensitive literals, mirroring existing template grounding)."""
    clean = re.sub(r"\*\*|`", "", command or "").strip()
    return bool(clean) and re.fullmatch(template_regex_body(template.strip()), clean, re.IGNORECASE) is not None


def resolved_candidate_grounded_in_section(command: str, template: str, parameters: Mapping[str, str], content: str) -> bool:
    """Plain-line template grounding for a RESOLVER-ATTESTED candidate only: `template` must be a
    template the deterministic extractor re-derives verbatim from this exact section (prohibited or
    unclassified text never qualifies), the parameters must be exactly its placeholders with
    command-safe values, and `command` must equal the strict rendering -- nothing else."""
    names = [name for name, _, _ in template_placeholders(template)]
    if set(parameters) != set(names) or not all(_PARAMETER_VALUE.fullmatch(v) for v in parameters.values()):
        return False
    if _substitute(template, parameters) != command:
        return False
    actions, _ = extract_procedure_actions(knowledge_id="-", version_label="-", section_id="-", content=content or "")
    return any(action.command_template == template for action in actions)


def rederive_resolved_candidate(
    action_id: str, parameters: Mapping[str, str], selected_evidence: Iterable[EvidenceReference | dict[str, Any]]
) -> Optional[ResolvedCommandCandidate]:
    """Server-side re-derivation for later authority re-runs (control plane): re-extract the action
    from the given (freshly re-validated) governed evidence and render it strictly with the
    previously bound parameters. Returns a newly attested candidate, or None."""
    for ev, meta in _governed_selected(selected_evidence):
        for action in actions_for_evidence(ev, meta)[0]:
            if action.action_id != action_id:
                continue
            bindings = [ParameterBinding(name=k, state=ParameterBindingState.VERIFIED, value=v) for k, v in parameters.items()]
            rendered = render_command_template(action.command_template, bindings)
            if rendered is None or {p.name for p in action.parameters} != set(parameters):
                return None
            return _signed_candidate(action, rendered, parameters)
    return None


def _has_argument_syntax(line: str) -> bool:
    """A command-invocation line: starts with a lowercase command verb and carries at least one
    command argument (`key=value`, `<name>`/`{name}` or a keyed document placeholder). Prose
    sentences ("Restart the affected unit") never qualify."""
    tokens = line.split()
    if len(tokens) < 2 or not _COMMAND_VERB.match(tokens[0]):
        return False
    return any(_ARGUMENT_TOKEN.match(t) for t in tokens[1:]) or bool(template_placeholders(line))


def _step_title(previous_lines: list[str]) -> str:
    """Nearest preceding title-like line (several words, every word capitalised, no argument or
    table syntax) -- used only as a human-readable description, never for authority."""
    for prev in reversed(previous_lines[-8:]):
        text = prev.strip().rstrip(":")
        words = text.split()
        if len(words) >= 2 and all(w[:1].isupper() or w[:1].isdigit() for w in words) and "=" not in text and "|" not in text:
            return text[:160]
    return ""


def _intent(operation_type: CommandOperationType, template: str) -> str:
    head = " ".join(tok for tok in template.split() if not _ANY_PLACEHOLDER.fullmatch(tok))
    return f"{operation_type.value}:{head.strip().lower()}"


def _is_command_shaped(candidate: str) -> bool:
    text = candidate.strip()
    return (
        bool(text)
        and len(text) <= _MAX_TEMPLATE_CHARS
        and len(text.split()) <= _MAX_TEMPLATE_TOKENS
        and "|" not in text
        and not text.endswith((".", "?", "!"))
    )


@dataclass
class _Candidate:
    template: str
    method: str
    description: str
    line: int
    kind: BlockKind
    reason: str = ""


@dataclass
class _SectionScan:
    candidates: list[_Candidate] = dataclass_field(default_factory=list)
    non_instruction: dict[int, list[tuple[int, int]]] = dataclass_field(default_factory=dict)
    """line -> character spans that are example / sample-output / screenshot text, not instruction."""


def _fences(lines: list[str]) -> dict[int, tuple[int, int, str]]:
    out: dict[int, tuple[int, int, str]] = {}
    opened: Optional[tuple[int, str]] = None
    for index, line in enumerate(lines):
        if line.strip().startswith("```"):
            if opened is None:
                opened = (index, line.strip()[3:])
            else:
                out[opened[0]] = (opened[0], index, opened[1])
                opened = None
    return out


def _token_in(literal: str, template: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_\-]){re.escape(literal)}(?![A-Za-z0-9_\-])", template) is not None


def _scan_section(content: str) -> _SectionScan:
    """Command-formatted candidates in source order, each with the structural block it sits in.
    Structural rules only: fenced blocks, inline code, labelled blocks, argument-syntax lines."""
    lines = (content or "").splitlines()
    fences = _fences(lines)
    scan = _SectionScan()

    def mark(index: int, span: Optional[tuple[int, int]] = None) -> None:
        scan.non_instruction.setdefault(index, []).append(span or (0, len(lines[index])))

    block: Optional[tuple[BlockKind, str]] = None
    index = 0
    while index < len(lines):
        if index in fences:
            opened, closed, info = fences[index]
            kind, reason = ps.fence_kind(info, lines[opened + 1: closed], block[0] if block else None)
            for inner in range(opened + 1, closed):
                if kind is not BlockKind.COMMAND:
                    mark(inner)
                if lines[inner].strip():
                    scan.candidates.append(_Candidate(lines[inner].strip(), "code_block", "", inner, kind, reason))
            block, index = None, closed + 1
            continue
        line = lines[index]
        stripped = line.strip()
        label = ps.LABEL_LINE.match(line) if stripped.endswith(":") else None
        non_instruction = block is not None and block[0] in ps.NON_INSTRUCTION_SEMANTICS
        if non_instruction and label is None and ps.BULLET.match(line):
            block, non_instruction = None, False  # a new list item ends an example / output block
        cursor, inline = 0, False
        for span in _INLINE_CODE.finditer(line):
            inline = True
            if non_instruction:
                kind, reason = block[0], f"{block[0].value} block {block[1]!r}"
            elif ps.inline_example(line[cursor: span.start()], line[span.end():]):
                kind, reason = BlockKind.EXAMPLE, "explicit inline example marker"
            else:
                kind, reason = BlockKind.INSTRUCTION, ""
            cursor = span.end()
            if kind in ps.NON_INSTRUCTION_SEMANTICS:
                mark(index, (span.start(), span.end()))
            description = _INLINE_CODE.sub("", line).strip(" \t:-–—.")
            scan.candidates.append(_Candidate(span.group(1).strip(), "inline_code", description[:160], index, kind, reason))
        if not stripped:
            block = None
        elif stripped.endswith(":"):
            kind = ps.label_kind(label.group(1)) if label else None
            block = (kind, label.group(1).strip()) if kind is not None else None
        elif non_instruction:
            mark(index)
            if not inline and _has_argument_syntax(stripped):
                scan.candidates.append(
                    _Candidate(stripped, "argument_syntax_line", "", index, block[0], f"{block[0].value} block {block[1]!r}")
                )
        elif ps.transcript_line(line):
            mark(index)  # a recorded `host> command` session line is output, never an instruction
        elif block is None and not inline and _has_argument_syntax(stripped):
            # Procedure-step command lines outside a labelled block (e.g. a step title followed by
            # its command). Structural: verb + argument syntax; classification is checked below.
            scan.candidates.append(_Candidate(stripped, "argument_syntax_line", _step_title(lines[:index]), index, BlockKind.INSTRUCTION))
        elif block is not None and block[0] is BlockKind.COMMAND and not inline:
            scan.candidates.append(_Candidate(stripped, "command_block", block[1][:160], index, BlockKind.COMMAND))
        index += 1

    # "replace <literal> with ...": the named literal instance is explicitly illustrative.
    replaced = [lit for lit in ps.replaced_literals(content) if re.search(r"[0-9=]", lit)]
    for candidate in scan.candidates:
        if candidate.kind not in (BlockKind.INSTRUCTION, BlockKind.COMMAND):
            continue
        slots = [candidate.template[a:b] for _, a, b in template_placeholders(candidate.template)]
        named = next((lit for lit in replaced if lit not in slots and _token_in(lit, candidate.template)), None)
        if named:
            candidate.kind, candidate.reason = BlockKind.EXAMPLE, f"the source instructs replacing {named!r}"
            start = lines[candidate.line].find(candidate.template)
            mark(candidate.line, (start, start + len(candidate.template)) if start >= 0 else None)
    return scan


def _candidates(content: str) -> list[tuple[str, str, str]]:
    """(template, extraction_method, description) of EVERY command-formatted candidate, whatever its
    semantics (examples and sample output included): the egress boundary's governed-command corpus,
    where blocking more text is the safe direction."""
    return [(c.template, c.method, c.description) for c in _scan_section(content).candidates]


def instruction_view(content: str, *, artifact_derived: bool = False) -> str:
    """The section text with every example / sample-output / screenshot-transcription region
    blanked (same line structure). Grounding a command against governed text uses only this view,
    so text that merely LOOKS like a command in an output, example or transcription is never
    authority. A section transcribed from an artifact image has no instruction text at all."""
    if artifact_derived:
        return ""
    lines = (content or "").splitlines()
    scan = _scan_section(content or "")
    out = []
    for index, line in enumerate(lines):
        chars = list(line)
        for start, end in scan.non_instruction.get(index, []):
            chars[start:end] = " " * (end - start)
        out.append("".join(chars))
    return "\n".join(out)


def _instance_semantics(candidate: _Candidate, artifact_derived: bool) -> tuple[InstanceSemantics, str]:
    if artifact_derived:
        return InstanceSemantics.SCREENSHOT_TRANSCRIPTION, "section text is a model interpretation of an artifact (e.g. an image)"
    if candidate.kind in ps.NON_INSTRUCTION_SEMANTICS:
        return ps.NON_INSTRUCTION_SEMANTICS[candidate.kind], candidate.reason
    names = [name for name, _, _ in template_placeholders(candidate.template)]
    if names:
        return InstanceSemantics.PARAMETERIZED_TEMPLATE, f"explicit placeholder(s) {names}"
    return InstanceSemantics.FIXED_INSTANCE, "literal instance: the source declares no placeholder and does not mark it as an example"


def _condition_alternative(occurrence: _Candidate, outline: "ps.Outline", governed_scope: Mapping[str, Any], canonical: str) -> tuple[Optional[dict[str, Any]], list[int]]:
    """(condition-scope alternative or None, the occurrence's own block lines) from structure only."""
    block, child = outline.child_at(occurrence.line)
    if block is not None:
        alternative = {
            "dimension": block.dimension, "value": ps.heading_value(child.heading) if child else None,
            "label": block.label, "source": canonical, "line": (child.line if child else block.line) + 1,
        }
        return alternative, (child.body if child else [])
    scope = governed_scope.get("condition_scope") if governed_scope else None
    if isinstance(scope, Mapping):
        return dict(scope), []
    return None, []


def extract_procedure_actions(
    *,
    knowledge_id: str,
    version_label: str,
    section_id: str,
    content: str,
    source_locator: Optional[str] = None,
    title: Optional[str] = None,
    heading: Optional[str] = None,
    governed_scope: Optional[Mapping[str, Any]] = None,
    artifact_derived: bool = False,
) -> tuple[list[ProcedureAction], list[dict[str, str]]]:
    """Deterministically derive ProcedureActions from one governed section's exact text.

    Returns (actions, skipped) where `skipped` records why an explicit command-formatted
    candidate was not materialized (non-executable semantics, prohibited, unsupported placeholder,
    unclassified). `governed_scope` is the server-derived document-level structure of the same
    governed version (procedure_semantics.governed_document_scope); never model input.
    """
    from backend.agents.technical_authority_engineer.agent_tool import classify_command_operation, state_change_class

    canonical = f"{knowledge_id}:{version_label}:{section_id}"
    source = ProcedureActionSource(
        knowledge_id=knowledge_id,
        version_label=version_label,
        section_id=section_id,
        source_locator=source_locator,
        canonical_source_id=canonical,
        title=title,
        heading=heading,
    )
    content = content or ""
    lines = content.splitlines()
    scan = _scan_section(content)
    outline = ps.parse_outline(content)
    artifact_derived = artifact_derived or bool((governed_scope or {}).get("artifact_derived"))
    command_lines = {c.line for c in scan.candidates} | set(scan.non_instruction)
    document_conditions = ps.outline_document_conditions(outline, lines, canonical) + [
        dict(c) for c in (governed_scope or {}).get("document_conditions") or [] if isinstance(c, Mapping)
    ]

    occurrences: dict[str, list[_Candidate]] = {}
    for candidate in scan.candidates:
        occurrences.setdefault(candidate.template, []).append(candidate)

    actions: list[ProcedureAction] = []
    skipped: list[dict[str, str]] = []
    for template, found in occurrences.items():
        classified = [(c, *_instance_semantics(c, artifact_derived)) for c in found]
        executable = [(c, sem, why) for c, sem, why in classified if sem in ps.EXECUTABLE_SEMANTICS]
        if not executable:
            _, semantics, why = classified[0]
            skipped.append({"template": template[:_MAX_TEMPLATE_CHARS], "reason": f"{semantics.value}: {why}",
                            "semantics": semantics.value, "line": str(found[0].line + 1)})
            continue
        first, semantics, why = executable[0]
        if not _is_command_shaped(template):
            skipped.append({"template": template[:_MAX_TEMPLATE_CHARS], "reason": "not command-shaped", "semantics": InstanceSemantics.UNKNOWN.value})
            continue
        if is_command_prohibited_in_snippet(template, content):
            skipped.append({"template": template, "reason": "prohibited in source", "semantics": semantics.value})
            continue
        composition = single_invocation_violation(template)
        if composition:
            skipped.append({"template": template, "reason": f"not a single command invocation ({composition})", "semantics": semantics.value})
            continue
        unsupported = [m.group(0) for m in _ANY_PLACEHOLDER.finditer(template) if not _SUPPORTED_PLACEHOLDER.fullmatch(m.group(0))]
        if unsupported:
            skipped.append({"template": template, "reason": "unsupported placeholder syntax", "semantics": semantics.value})
            continue
        operation_type = classify_command_operation(_neutralize_placeholders(template))
        if operation_type is CommandOperationType.UNKNOWN:
            skipped.append({"template": template, "reason": "unclassified operation type", "semantics": InstanceSemantics.UNKNOWN.value})
            continue
        action_type = (
            ProcedureActionType.DIAGNOSTIC_READ
            if operation_type is CommandOperationType.READ_ONLY_DIAGNOSTIC
            else ProcedureActionType.STATE_CHANGE
        )
        parameters: list[ProcedureActionParameter] = []
        for name, start, end in template_placeholders(template):
            if name not in [p.name for p in parameters]:
                keyed = re.search(r"([A-Za-z][A-Za-z0-9_]*)=$", template[:start])
                parameters.append(ProcedureActionParameter(name=name, placeholder=template[start:end], key=keyed.group(1) if keyed else None))
        targets = action_targets(template)
        restrictions = (
            ["State-changing operation: requires trusted target confirmation and existing approval policy."]
            if action_type is ProcedureActionType.STATE_CHANGE
            else ["Read-only diagnostic."]
        )
        conditions: list[dict[str, Any]] = []
        condition_scope: Optional[dict[str, Any]] = None
        if action_type is ProcedureActionType.STATE_CHANGE:
            alternatives: list[dict[str, Any]] = []
            unscoped = False
            block_lines: list[int] = []
            for occurrence, _, _ in executable:
                alternative, own_block = _condition_alternative(occurrence, outline, governed_scope or {}, canonical)
                if alternative is None:
                    unscoped = True  # an unscoped governed occurrence applies without a named condition
                    continue
                alternatives.append(alternative)
                block_lines += own_block or ([] if outline.child_at(occurrence.line)[0] else list(range(len(lines))))
            if alternatives and not unscoped:
                condition_scope = {"alternatives": alternatives}
            block = ps.block_conditions(lines, sorted(set(block_lines)), command_lines, canonical)
            seen_conditions: set[tuple[str, int]] = set()
            for condition in document_conditions + block:
                key = (str(condition.get("source")), int(condition.get("line") or 0))
                if key not in seen_conditions:
                    seen_conditions.add(key)
                    conditions.append(condition)
            if condition_scope:
                named = ", ".join(f"{a.get('dimension')} {a.get('value')!r}" for a in condition_scope["alternatives"])
                restrictions.append(f"Applies only when this investigation's trusted evidence shows the governed condition: {named}.")
            labels = {"precondition": "Governed precondition", "post_action": "Governed post-action requirement",
                      "block_condition": "Governed procedure condition"}
            restrictions += [f"{labels.get(c['kind'], 'Governed condition')}: {c['text']}" for c in conditions]
        actions.append(
            ProcedureAction(
                action_id=_action_id(knowledge_id, version_label, section_id, template),
                intent=_intent(operation_type, template),
                action_type=action_type,
                operation_type=operation_type,
                command_template=template,
                parameters=parameters,
                source=source,
                restrictions=restrictions,
                description=first.description or (heading or ""),
                extraction_method=first.method,
                targets=targets,
                instance_semantics=semantics,
                semantics_reason=why,
                source_line=first.line + 1,
                state_change_class=state_change_class(_neutralize_placeholders(template)) if action_type is ProcedureActionType.STATE_CHANGE else None,
                conditions=conditions,
                condition_scope=condition_scope,
            )
        )
    derive_action_relationships(actions, content, source_locator)
    return actions, skipped


_LITERAL_PAIR = re.compile(r"(?<![A-Za-z0-9_\-])([A-Za-z][A-Za-z0-9_]*)=([A-Za-z0-9][A-Za-z0-9_\-\.:/]*)")


def action_targets(template: str) -> list[ActionTarget]:
    """Target requirements exactly as the governed command writes them: every placeholder is a
    PARAMETERIZED slot (typed by the `key=` written before it; untyped otherwise) and every literal
    `key=value` is a FIXED instance. Nothing is inferred from wording or examples."""
    spans = template_placeholders(template)
    targets: list[ActionTarget] = []
    for name, start, end in spans:
        keyed = re.search(r"([A-Za-z][A-Za-z0-9_]*)=$", template[:start])
        key = keyed.group(1) if keyed else None
        if not any(t.kind is ActionTargetKind.PARAMETERIZED and t.parameter == name for t in targets):
            targets.append(ActionTarget(key=key, kind=ActionTargetKind.PARAMETERIZED, parameter=name))
    for match in _LITERAL_PAIR.finditer(template):
        if any(start <= match.start(2) < end for _, start, end in spans):
            continue
        targets.append(ActionTarget(key=match.group(1), kind=ActionTargetKind.FIXED, fixed_value=match.group(2)))
    return targets


# ---- structured action relationships (extraction time; never re-derived from prose at runtime) --------
_DEPENDENCY_MARKER = re.compile(r"\b(?:before|prior to|pre-?requisites?|pre-?checks?|pre-?conditions?|only after|until)\b", re.IGNORECASE)
_OBLIGATION = re.compile(
    r"\b(?:must|shall|mandatory|required|requires?|pre-?requisites?|pre-?conditions?|only after|(?:do not|don't|never)\b[^.]*\b(?:before|until))\b",
    re.IGNORECASE,
)
_ADVISORY = re.compile(r"\b(?:may|might|optional(?:ly)?|if (?:needed|required|necessary|applicable)|recommended|consider|can|could|should)\b", re.IGNORECASE)
_UNIVERSAL_SCOPE = re.compile(r"\b(?:before|prior to)\s+(?:any|all|every|each)\b", re.IGNORECASE)
_VERIFICATION_MARKER = re.compile(r"\b(?:after|verify|verification|confirm|re-?check|validate)\b", re.IGNORECASE)
_BEFORE = re.compile(r"\b(?:before|prior to)\b", re.IGNORECASE)
_AFTER = re.compile(r"\b(?:after|only after|requires?|following)\b", re.IGNORECASE)


def _template_pattern(template: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w]){re.escape(template)}(?![\w])")


def _template_lines(template: str, lines: list[str]) -> list[int]:
    pattern = _template_pattern(template)
    return [index for index, line in enumerate(lines) if pattern.search(line)]


def _prerequisite_type(line: str) -> tuple[RelationshipType, str]:
    """Obligation strength of a dependency phrase. Advisory wording wins over obligation words in
    the same line (e.g. "if required"), so only an unhedged obligation is ever mandatory."""
    if _ADVISORY.search(line):
        return RelationshipType.RECOMMENDED_BEFORE, "advisory dependency language"
    if _OBLIGATION.search(line):
        return RelationshipType.MANDATORY_PREREQUISITE, "explicit mandatory dependency language"
    return RelationshipType.RECOMMENDED_BEFORE, "temporal order without obligation"


def derive_action_relationships(actions: list[ProcedureAction], content: str, source_locator: Optional[str] = None) -> None:
    """Deterministic action graph for ONE governed section (document order is NEVER a dependency):

    sequence                   first-occurrence document order -- ordering only, never enforced
    one action on a line with dependency language ("before", "prior to", "prerequisite",
      "precondition", "only after", "until") ahead of later state-changing actions:
        MANDATORY_PREREQUISITE  unhedged obligation ("must", "required", "mandatory",
                                "prerequisite", "only after", "do not ... before") AND the target is
                                identifiable ("before any/all ...", or exactly one later state change)
        RECOMMENDED_BEFORE      advisory ("should", "may", "if needed") or plain temporal wording
        UNKNOWN                 obligation wording whose target action cannot be identified
    two actions on one line: "<A> ... before <B>" / "<B> ... after|requires <A>" -> B depends on A
                                with the obligation strength of the line; any other shape -> UNKNOWN
    three or more actions on one line -> UNKNOWN
    POST_ACTION_VERIFICATION    a read occurrence AFTER a state change on a line with verification
                                language, owned by the nearest preceding state change
    Provenance: rule, 1-based line, exact line text, section locator."""
    lines = content.splitlines()
    occurrences = {a.action_id: _template_lines(a.command_template, lines) for a in actions}
    first = {aid: (lines_[0] if lines_ else len(lines) + position) for position, (aid, lines_) in enumerate(occurrences.items())}
    by_id = {a.action_id: a for a in actions}

    def position(aid: str) -> tuple[int, int]:
        line = first[aid]
        match = _template_pattern(by_id[aid].command_template).search(lines[line]) if line < len(lines) else None
        return line, match.start() if match else 0

    for rank, action in enumerate(sorted(actions, key=lambda a: position(a.action_id))):
        action.sequence = rank
    state_changes = [a for a in actions if a.action_type is ProcedureActionType.STATE_CHANGE and occurrences[a.action_id]]
    reads = [a for a in actions if a.action_type is ProcedureActionType.DIAGNOSTIC_READ and occurrences[a.action_id]]

    def relate(owner: ProcedureAction, relationship_type: RelationshipType, related: ProcedureAction, rule: str, index: int) -> None:
        if owner is related or any(r.related_action_id == related.action_id and r.relationship_type is relationship_type for r in owner.relationships):
            return
        owner.relationships.append(
            ActionRelationship(relationship_type=relationship_type, related_action_id=related.action_id, rule=rule,
                               source_locator=source_locator, line=index + 1, text=lines[index].strip()[:300])
        )

    for index, line in enumerate(lines):
        on_line = sorted((a for a in actions if _template_pattern(a.command_template).search(line)),
                         key=lambda a: _template_pattern(a.command_template).search(line).start())
        if len(on_line) >= 3 and _DEPENDENCY_MARKER.search(line):
            for later in on_line[1:]:
                relate(later, RelationshipType.UNKNOWN, on_line[0], "several actions on one dependency line", index)
        elif len(on_line) == 2:
            left, right = on_line
            between = line[_template_pattern(left.command_template).search(line).end(): _template_pattern(right.command_template).search(line).start()]
            strength, rule = _prerequisite_type(line)
            if _BEFORE.search(between):
                relate(right, strength, left, f"{rule} ('<A> before <B>')", index)
            elif _AFTER.search(between):
                relate(left, strength, right, f"{rule} ('<B> after/requires <A>')", index)
            elif _DEPENDENCY_MARKER.search(line):
                relate(right, RelationshipType.UNKNOWN, left, "dependency direction not identifiable", index)
        elif len(on_line) == 1 and on_line[0] in reads and _DEPENDENCY_MARKER.search(line):
            read = on_line[0]
            later = [s for s in state_changes if position(s.action_id) > position(read.action_id)]
            if not later:
                continue
            strength, rule = _prerequisite_type(line)
            if strength is RelationshipType.MANDATORY_PREREQUISITE and not (_UNIVERSAL_SCOPE.search(line) or len(later) == 1):
                strength, rule = RelationshipType.UNKNOWN, "mandatory language does not identify which later action"
            for state_change in later:
                relate(state_change, strength, read, rule, index)

    for read in reads:
        for index in occurrences[read.action_id]:
            preceding = [s for s in state_changes if first[s.action_id] < index]
            if not preceding or not _VERIFICATION_MARKER.search(lines[index]):
                continue
            owner = max(preceding, key=lambda s: first[s.action_id])
            relate(owner, RelationshipType.POST_ACTION_VERIFICATION, read, "post-action verification language", index)


def _field(ev: Any, name: str) -> Any:
    return ev.get(name) if isinstance(ev, dict) else getattr(ev, name, None)


def _governed_selected(evidence: Iterable[EvidenceReference | dict[str, Any]]) -> list[tuple[Any, dict[str, Any]]]:
    out = []
    for ev in evidence or []:
        if _field(ev, "source_type") != "governed_knowledge":
            continue
        meta = _field(ev, "metadata")
        out.append((ev, meta if isinstance(meta, dict) else {}))
    return out


def _source_authority(meta: dict[str, Any]) -> Optional[str]:
    """None when authoritative for command resolution; else the reason it is not."""
    lifecycle = str(meta.get("lifecycle_status", "")).lower()
    applicability = str(meta.get("applicability_outcome", "")).lower()
    if lifecycle != "approved":
        return f"source lifecycle_status={lifecycle or 'unknown'} (approved required)"
    if applicability != "match":
        return f"source applicability={applicability or 'unknown'} (MATCH required)"
    from backend.agents.technical_authority_engineer.evidence_sources import governed_document_type, is_action_source

    if not is_action_source(meta):
        return f"document type {governed_document_type(meta)} is diagnostic knowledge (approved procedure required)"
    return None


def _resolved_scope(meta: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
    scope = meta.get("governed_scope")
    return scope if isinstance(scope, Mapping) and scope.get("status") == "resolved" else None


def actions_for_evidence(ev: Any, meta: dict[str, Any]) -> tuple[list[ProcedureAction], list[dict[str, str]]]:
    return extract_procedure_actions(
        governed_scope=_resolved_scope(meta),
        artifact_derived=bool(meta.get("artifact_derived")),
        knowledge_id=str(meta.get("knowledge_id") or ""),
        version_label=str(meta.get("version_label") or ""),
        section_id=str(meta.get("section_id") or ""),
        content=str(_field(ev, "content_snippet") or ""),
        source_locator=meta.get("source_locator"),
        title=meta.get("title") or _field(ev, "title"),
        heading=meta.get("heading"),
    )


def build_procedure_action_catalog(
    selected_evidence: Iterable[EvidenceReference | dict[str, Any]],
) -> tuple[list[ProcedureAction], list[dict[str, Any]]]:
    """Bounded catalog from SELECTED governed evidence only. Sources that are not approved +
    applicability MATCH contribute no action (reported in `unavailable`, without action ids)."""
    actions: list[ProcedureAction] = []
    unavailable: list[dict[str, Any]] = []
    for ev, meta in _governed_selected(selected_evidence):
        if not meta.get("knowledge_id") or not meta.get("version_label") or not meta.get("section_id"):
            continue
        reason = _source_authority(meta)
        if reason is not None:
            unavailable.append(
                {
                    "procedure": meta.get("title") or _field(ev, "title"),
                    "section": meta.get("heading"),
                    "reason": reason,
                    "unresolved_applicability_dimensions": list(meta.get("unresolved_applicability_dimensions") or []),
                }
            )
            continue
        section_actions, _ = actions_for_evidence(ev, meta)
        actions.extend(section_actions)
    return actions, unavailable


_BLOCKING_APPLICABILITY = frozenset({"unknown", "partial_match"})


def _normalized(command: Optional[str]) -> str:
    return re.sub(r"\s+", " ", str(command or "").strip()).casefold()


def applicability_blocked_action(
    command: Optional[str],
    cited_source: Optional[str],
    selected_evidence: Iterable[EvidenceReference | dict[str, Any]],
) -> Optional[Any]:
    """Structural identity of a governed DIAGNOSTIC READ whose command Command Authority refused
    ONLY because the selected section's applicability is unresolved (UNKNOWN / PARTIAL_MATCH).

    Returns a `BlockedGovernedAction` (continuity metadata, ZERO authority) when every condition
    holds, else None:
      - `cited_source` resolves to exactly one SELECTED governed evidence item (never AVAILABLE);
      - that source is approved and its applicability is UNKNOWN or PARTIAL_MATCH (not
        NOT_APPLICABLE, not MATCH -- a MATCH source would have been authorized normally);
      - the deterministic extractor derives exactly one diagnostic-read ProcedureAction from that
        exact section whose template the command renders (a fabricated command, a state change or
        an ambiguous match yields None).
    Its `procedure_action_id` is the same deterministic id the catalog issues for that section and
    template once the source is MATCH. Nothing here issues an action, renders a command for
    presentation or grants authority."""
    from backend.agents.technical_authority_engineer.validation import resolve_command_source
    from backend.cases.troubleshooting_progression import BlockedGovernedAction

    normalized = _normalized(command)
    if not normalized or not cited_source:
        return None
    governed = _governed_selected(selected_evidence)
    canonical = resolve_command_source(command, cited_source, [ev for ev, _ in governed])
    if not canonical:
        return None
    match = next(((ev, meta) for ev, meta in governed if _field(ev, "source_id") == canonical), None)
    if match is None:
        return None
    ev, meta = match
    applicability = str(meta.get("applicability_outcome", "")).lower()
    if str(meta.get("lifecycle_status", "")).lower() != "approved" or applicability not in _BLOCKING_APPLICABILITY:
        return None
    from backend.agents.technical_authority_engineer.evidence_sources import is_action_source

    if not is_action_source(meta):
        return None
    candidates = []
    for action in actions_for_evidence(ev, meta)[0]:
        if action.action_type is not ProcedureActionType.DIAGNOSTIC_READ:
            continue
        template = _normalized(action.command_template)
        if action.parameters:
            parts, cursor = [], 0
            for _, start, end in template_placeholders(template):
                parts += [re.escape(template[cursor:start]), r"[a-z0-9_\-\.:/]+"]
                cursor = end
            if re.fullmatch("".join(parts) + re.escape(template[cursor:]), normalized):
                candidates.append(action)
        elif template == normalized:
            candidates.append(action)
    if len(candidates) != 1:
        return None
    action = candidates[0]
    return BlockedGovernedAction(
        procedure_action_id=action.action_id,
        normalized_command=_normalized(action.command_template),
        source_id=action.source.canonical_source_id,
        knowledge_id=action.source.knowledge_id,
        version_label=action.source.version_label,
        section_id=action.source.section_id,
        blocking_reason=f"applicability_{applicability}",
        unresolved_dimensions=[str(d) for d in meta.get("unresolved_applicability_dimensions") or [] if str(d).strip()],
    )


def authorized_command_action(
    command: Optional[str],
    command_source: Optional[str],
    selected_evidence: Iterable[EvidenceReference | dict[str, Any]],
) -> Optional[ProcedureAction]:
    """Continuity identity (ZERO authority) of a command Command Authority ALREADY authorized on the
    legacy free-text path: the single DIAGNOSTIC READ ProcedureAction that the catalog derives from
    THIS run's SELECTED governed evidence (approved + applicability MATCH, procedural source) whose
    canonical source is the command's cited source and whose template the command renders exactly.

    None when the source does not resolve to exactly one selected item, or when no action / several
    actions correspond. Nothing here authorizes, renders or issues anything; the caller keeps only
    the id as metadata of the step that presents that same authorized command."""
    from backend.agents.technical_authority_engineer.validation import resolve_command_source

    normalized = " ".join(str(command or "").split())
    if not normalized or not command_source:
        return None
    evidence = list(selected_evidence or [])
    governed = [ev for ev, _ in _governed_selected(evidence)]
    canonical = resolve_command_source(normalized, command_source, governed)
    cited = [identity_of(ev) for ev in governed if _field(ev, "source_id") == canonical] if canonical else []
    if len(cited) != 1 or cited[0] is None:
        return None
    actions, _ = build_procedure_action_catalog(evidence)
    matches = [
        a for a in actions
        if a.action_type is ProcedureActionType.DIAGNOSTIC_READ
        and identity_of(a.source) == cited[0]
        and command_matches_template(normalized, " ".join(a.command_template.split()))
    ]
    return matches[0] if len(matches) == 1 else None


# ---------------------------------------------------------------------------------------------
# Run-scoped issued catalog (the only set of ids the model may choose from this turn)
# ---------------------------------------------------------------------------------------------

_issued_lock = threading.Lock()
_issued: dict[str, dict[str, ProcedureAction]] = {}


def record_issued_actions(run_id: str, actions: Iterable[ProcedureAction]) -> None:
    with _issued_lock:
        bucket = _issued.setdefault(run_id, {})
        for action in actions:
            bucket[action.action_id] = action


def issued_action_ids(run_id: Optional[str]) -> frozenset[str]:
    if not run_id:
        return frozenset()
    with _issued_lock:
        return frozenset(_issued.get(run_id, {}))


def issued_actions(run_id: Optional[str]) -> list[ProcedureAction]:
    """This run's issued catalog, read-only (never issues, never re-derives)."""
    if not run_id:
        return []
    with _issued_lock:
        return list(_issued.get(run_id, {}).values())


def discard_issued_actions(run_id: str) -> None:
    with _issued_lock:
        _issued.pop(run_id, None)


# ---------------------------------------------------------------------------------------------
# Trusted parameter binding
# ---------------------------------------------------------------------------------------------


def _literally_stated(value: str, text: str) -> bool:
    """Case-sensitive, token-bounded literal presence in operator-authored text."""
    if not value or not text:
        return False
    return re.search(rf"(?<![A-Za-z0-9_\-]){re.escape(value)}(?![A-Za-z0-9_\-])", text) is not None


def read_confirmed_parameters(state: Any) -> dict[str, str]:
    try:
        raw = state.get(CONFIRMED_PROCEDURE_PARAMETERS_STATE_KEY) if state is not None else None
    except Exception:
        raw = None
    if not isinstance(raw, Mapping):
        return {}
    return {
        str(k): str(v)
        for k, v in raw.items()
        if isinstance(k, str) and isinstance(v, str) and _PARAMETER_VALUE.fullmatch(v)
    }


def observed_keyed_values(name: str, observed_texts: Iterable[str]) -> list[str]:
    """Distinct literal values written as `<name>=<value>` in operator-provided observed output.
    Structural and generic: the key must equal the governed placeholder's parameter name (case-
    insensitive); the value is taken exactly as written and must be a plain command-safe token --
    a value wrapped in or adjacent to markup (e.g. `**4**`) is never extracted or cleaned up."""
    pattern = re.compile(
        rf"(?<![A-Za-z0-9_\-]){re.escape(name)}=([A-Za-z0-9_\-\.:/]+)(?![A-Za-z0-9_\-\.:/*`])", re.IGNORECASE
    )
    values: list[str] = []
    for text in observed_texts or []:
        for match in pattern.finditer(text or ""):
            value = match.group(1).rstrip(".:")
            if value and _PARAMETER_VALUE.fullmatch(value) and not re.fullmatch(r"x{3,}", value, re.IGNORECASE) and value not in values:
                values.append(value)
    return values


def reconcile_parameter_bindings(
    required: Iterable[str],
    proposals: Iterable[Any],
    operator_text: str,
    confirmed: Mapping[str, str],
    observed_texts: Iterable[str] = (),
) -> tuple[list[ParameterBinding], dict[str, str]]:
    """Bind each required parameter from TRUSTED sources only.

    Trusted, in precedence order:
    (a) a value the model points at that appears literally in the operator's current message;
    (b) a value previously VERIFIED in this session;
    (c) operator-provided observed output already recorded in the ACTIVE troubleshooting thread
        (`observed_texts`): a literal `<name>=<value>` occurrence, or a model-pointed value that
        appears literally there.
    Every value must be a plain command-safe token taken exactly as written; no markup cleanup,
    no model-supplied value. Observed output is parameter evidence only, never command authority.

    Several operator-stated values, or several distinct observed values -> AMBIGUOUS
    (clarification). A confirmed value contradicted by the observed output -> CONFLICTING (fail
    closed; the confirmation is cleared). Returns (bindings, updated_confirmed).
    """
    observed_texts = [t for t in (observed_texts or []) if t]
    proposed: dict[str, list[str]] = {}
    observed_pointed: dict[str, list[str]] = {}
    rejected: dict[str, list[str]] = {}
    for proposal in proposals or []:
        name = str(_field(proposal, "name") or "").strip()
        value = str(_field(proposal, "value") or "").strip()
        if not name or not value:
            continue
        if not _PARAMETER_VALUE.fullmatch(value):
            rejected.setdefault(name, []).append("value contains characters not permitted in a command parameter")
        elif _literally_stated(value, operator_text):
            if value not in proposed.setdefault(name, []):
                proposed[name].append(value)
        elif any(_literally_stated(value, text) for text in observed_texts):
            if value not in observed_pointed.setdefault(name, []):
                observed_pointed[name].append(value)
        else:
            rejected.setdefault(name, []).append("value was not stated by the operator")

    updated = dict(confirmed)
    bindings: list[ParameterBinding] = []
    for name in required:
        stated = proposed.get(name, [])
        prior = confirmed.get(name)
        observed = observed_keyed_values(name, observed_texts)
        for value in observed_pointed.get(name, []):
            if value not in observed:
                observed.append(value)
        if len(stated) > 1:
            bindings.append(
                ParameterBinding(name=name, state=ParameterBindingState.AMBIGUOUS, detail=f"operator stated several values: {', '.join(stated)}")
            )
        elif len(stated) == 1:
            if prior is not None and prior != stated[0]:
                updated.pop(name, None)
                bindings.append(
                    ParameterBinding(
                        name=name,
                        state=ParameterBindingState.CONFLICTING,
                        detail=f"previously confirmed {prior}, now {stated[0]}",
                    )
                )
            else:
                updated[name] = stated[0]
                bindings.append(ParameterBinding(name=name, state=ParameterBindingState.VERIFIED, value=stated[0], detail="stated by operator"))
        elif prior is not None:
            if observed and prior not in observed:
                updated.pop(name, None)
                bindings.append(
                    ParameterBinding(
                        name=name,
                        state=ParameterBindingState.CONFLICTING,
                        detail=f"previously confirmed {prior}, operator output shows {', '.join(observed)}",
                    )
                )
            else:
                bindings.append(ParameterBinding(name=name, state=ParameterBindingState.VERIFIED, value=prior, detail="confirmed earlier in session"))
        elif len(observed) == 1:
            updated[name] = observed[0]
            bindings.append(
                ParameterBinding(name=name, state=ParameterBindingState.VERIFIED, value=observed[0], detail="from operator-provided output recorded in this investigation")
            )
        elif len(observed) > 1:
            bindings.append(
                ParameterBinding(name=name, state=ParameterBindingState.AMBIGUOUS, detail=f"operator output shows several values: {', '.join(observed)}")
            )
        else:
            bindings.append(
                ParameterBinding(name=name, state=ParameterBindingState.MISSING, detail="; ".join(rejected.get(name, [])) or None)
            )
    return bindings, updated


def render_command_template(template: str, bindings: Iterable[ParameterBinding]) -> Optional[str]:
    """Strict render: every placeholder must have a VERIFIED, charset-valid value; otherwise None.
    Never partially substitutes; never leaves a placeholder in the output."""
    values = {b.name: b.value for b in bindings if b.state is ParameterBindingState.VERIFIED and b.value}
    for name, _, _ in template_placeholders(template):
        if name not in values or not _PARAMETER_VALUE.fullmatch(values[name]):
            return None
    rendered = _substitute(template, values)
    if _ANY_PLACEHOLDER.search(rendered) or _KEYED_DOCUMENT_PLACEHOLDER.search(rendered):
        return None
    return rendered


# ---------------------------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------------------------


# ---------------------------------------------------------------------------------------------
# Target gate (state changes): THIS operation, for THIS exact target, in THIS fault
# ---------------------------------------------------------------------------------------------


def _requested_values(target: ActionTarget, proposals: Iterable[Any], operator_text: str, confirmed: Mapping[str, str]) -> list[str]:
    """Untrusted REQUESTED values for a parameterized target slot: what the model proposed (its
    structured parameter proposal), what the operator wrote as `key=value` in their OWN words, an
    earlier session confirmation. Never authority by themselves. `operator_text` must be the
    operator's own words (the caller passes ProgressionController.intent_text): identifiers inside
    pasted / bound output are observations -- target facts to validate against -- never requests."""
    requested: list[str] = []

    def _add(value: Any) -> None:
        # A malformed value (outside the command-parameter charset, e.g. markup-decorated) is not a
        # usable request at all; a well-formed one is a request that must still match a fact.
        text = str(value or "").strip()
        if text and _PARAMETER_VALUE.fullmatch(text) and text not in requested:
            requested.append(text)

    for proposal in proposals or []:
        if str(_field(proposal, "name") or "").strip() == target.parameter:
            _add(_field(proposal, "value"))
    if target.key:
        for match in re.finditer(rf"(?<![A-Za-z0-9_\-]){re.escape(target.key)}=([A-Za-z0-9][A-Za-z0-9_\-\.:/]*)", operator_text or ""):
            _add(match.group(1).rstrip(".:/"))
    if target.parameter and target.parameter in confirmed:
        _add(confirmed[target.parameter])
    return requested


def validate_action_targets(
    action: ProcedureAction,
    facts: Iterable[Any],
    *,
    fault_id: Optional[str],
    proposals: Iterable[Any] = (),
    operator_text: str = "",
    confirmed_parameters: Optional[Mapping[str, str]] = None,
) -> TargetValidation:
    """Every target the governed action writes must be a trusted target fact of THIS fault, with the
    SAME key and the SAME value (no alias, no case folding, no inference):

        FIXED literal       -> that exact instance observed, else FIXED_NOT_IN_CASE
        parameterized slot  -> requested value(s) present: must match an observed instance of the key
                               (else CONFLICTING when the key was observed with other values, NOT_OBSERVED
                               otherwise); several requested values -> AMBIGUOUS
                               nothing requested: exactly one observed instance -> bound from it;
                               several -> AMBIGUOUS; none -> MISSING
        no declared target  -> fails (a state change must name what it acts on)
    Operator / model / confirmed values are requests, never proof."""
    facts = [f for f in facts or [] if getattr(f, "fault_id", None) == fault_id]
    by_key: dict[str, list[Any]] = {}
    for fact in facts:
        by_key.setdefault(fact.target_key, []).append(fact)
    observed_keys = sorted(k for k in by_key if k)  # "" = literal identifiers (untyped slots only)
    confirmed = dict(confirmed_parameters or {})
    decisions: list[TargetDecision] = []

    def _provenance(matches: list[Any]) -> list[dict[str, Any]]:
        return [{"fact_id": f.fact_id, "step_id": f.step_id, "result_id": f.result_id, "source": f.result_source, "mo_path": f.mo_path} for f in matches]

    def _same(fact: Any, requested: str) -> bool:
        # Exact identity only: the value, or (untyped slot) the written `key=value` / identifier path.
        if fact.raw_identifier == requested:
            return True
        return target.key is None and requested in (fact.identity, fact.mo_path)

    for target in action.targets:
        candidates = by_key.get(target.key, []) if target.key else list(facts)  # typed: same key only
        values = list(dict.fromkeys(f.raw_identifier for f in candidates))
        label = target.key or f"<{target.parameter}>"
        if target.kind is ActionTargetKind.FIXED:
            matches = [f for f in candidates if f.raw_identifier == target.fixed_value]
            other = [v for v in _requested_values(target, (), operator_text, {}) if v != target.fixed_value]
            if other:
                # The operator explicitly named another instance of this key: never act on a different one.
                decisions.append(TargetDecision(
                    key=target.key, kind=target.kind, requested=other, status=TargetDecisionStatus.CONFLICTING, value=target.fixed_value,
                    detail=f"the procedure's literal {target.key}={target.fixed_value} differs from the requested {target.key}={other[0]}",
                ))
                continue
            decisions.append(TargetDecision(
                key=target.key, kind=target.kind, status=TargetDecisionStatus.VALIDATED if matches else TargetDecisionStatus.FIXED_NOT_IN_CASE,
                value=target.fixed_value, fact_ids=[f.fact_id for f in matches], provenance=_provenance(matches),
                detail="fixed instance observed in this fault's trusted evidence" if matches
                else f"the procedure's literal {target.key}={target.fixed_value} is not a target observed in this fault"
                + (f" (observed {target.key} values: {values})" if values else f" (observed keys: {observed_keys})"),
            ))
            continue
        requested = _requested_values(target, proposals, operator_text, confirmed)
        if len(requested) > 1:
            decisions.append(TargetDecision(key=target.key, kind=target.kind, parameter=target.parameter, requested=requested,
                                            status=TargetDecisionStatus.AMBIGUOUS, detail=f"several {label} values requested: {requested}"))
            continue
        if requested:
            matches = [f for f in candidates if _same(f, requested[0])]
            if matches:
                decisions.append(TargetDecision(key=target.key, kind=target.kind, parameter=target.parameter, requested=requested,
                                                status=TargetDecisionStatus.VALIDATED, value=requested[0], fact_ids=[f.fact_id for f in matches],
                                                provenance=_provenance(matches), detail="requested target observed in this fault's trusted evidence"))
            else:
                status = TargetDecisionStatus.CONFLICTING if (values and target.key) else TargetDecisionStatus.NOT_OBSERVED
                decisions.append(TargetDecision(
                    key=target.key, kind=target.kind, parameter=target.parameter, requested=requested, status=status,
                    detail=f"requested {label} {requested[0]} is not observed in this fault"
                    + (f"; observed {label} values: {values}" if values and target.key else f"; observed keys: {observed_keys}"),
                ))
            continue
        if len(values) == 1:
            matches = [f for f in candidates if f.raw_identifier == values[0]]
            decisions.append(TargetDecision(key=target.key, kind=target.kind, parameter=target.parameter, status=TargetDecisionStatus.VALIDATED,
                                            value=values[0], fact_ids=[f.fact_id for f in matches], provenance=_provenance(matches),
                                            detail="the only observed target of this key in this fault"))
        elif values:
            decisions.append(TargetDecision(key=target.key, kind=target.kind, parameter=target.parameter, status=TargetDecisionStatus.AMBIGUOUS,
                                            detail=f"several {label} targets observed: {values}; say which one"))
        else:
            decisions.append(TargetDecision(key=target.key, kind=target.kind, parameter=target.parameter, status=TargetDecisionStatus.MISSING,
                                            detail=f"no {label} target is established by this fault's trusted evidence (observed keys: {observed_keys})"))
    if not action.targets:
        return TargetValidation(passed=False, fault_id=fault_id, reason="state change declares no target", observed_keys=observed_keys)
    failed = [d for d in decisions if d.status is not TargetDecisionStatus.VALIDATED]
    return TargetValidation(
        passed=not failed, fault_id=fault_id, targets=decisions, observed_keys=observed_keys,
        reason="; ".join(f"{d.key or d.parameter}: {d.status.value} ({d.detail})" for d in failed) if failed else "all targets observed in this fault",
    )


def build_target_clarification(validation: Optional[TargetValidation]) -> Optional[dict[str, Any]]:
    """Operator-facing explanation of a failed target gate (never a guessed or suggested value)."""
    if validation is None or validation.passed:
        return None
    lines = []
    for decision in validation.targets:
        label = decision.key or "action"
        if decision.status is TargetDecisionStatus.AMBIGUOUS:
            lines.append(f"Several {label} targets apply ({decision.detail}); state which observed one this action is for.")
        elif decision.status is TargetDecisionStatus.MISSING:
            lines.append(f"No {label} target has been established from this investigation's verified output.")
        elif decision.status is not TargetDecisionStatus.VALIDATED:
            lines.append(f"The {label} target is not one observed in this investigation ({decision.status.value}).")
    if not validation.targets:
        lines.append("This state-changing action does not name a target, so it cannot be validated for this case.")
    return {"text": " ".join(lines), "targets": [d.view() for d in validation.targets if d.status is not TargetDecisionStatus.VALIDATED]}


def resolve_procedure_action(
    action_id: str,
    *,
    issued_ids: Iterable[str],
    selected_evidence: Iterable[EvidenceReference | dict[str, Any]],
    proposals: Iterable[Any] = (),
    operator_text: str = "",
    confirmed_parameters: Optional[Mapping[str, str]] = None,
    observed_texts: Iterable[str] = (),
    target_facts: Optional[Iterable[Any]] = None,
    fault_id: Optional[str] = None,
    case_results: Optional[Iterable[Mapping[str, Any]]] = None,
) -> tuple[ProcedureActionResolution, dict[str, str]]:
    """Deterministic, LLM-free resolution of a model-chosen action_id into a command CANDIDATE.

    Gates, in order: issued this turn -> re-derived from the CURRENT SELECTED evidence ->
    source approved + applicability MATCH -> not prohibited -> [state change: governed document
    structure established -> named condition shown by this fault's trusted results -> target gate
    against this fault's trusted target facts] -> trusted parameters -> strict render.
    Returns (resolution, updated_confirmed_parameters).
    """
    confirmed = dict(confirmed_parameters or {})
    if action_id not in set(issued_ids):
        return (
            ProcedureActionResolution(
                status=ProcedureActionResolutionStatus.UNKNOWN_ACTION,
                action_id=action_id,
                reason="action_id was not issued in this turn's server catalog",
            ),
            confirmed,
        )

    match: Optional[tuple[ProcedureAction, Any, dict[str, Any]]] = None
    for ev, meta in _governed_selected(selected_evidence):
        for action in actions_for_evidence(ev, meta)[0]:
            if action.action_id == action_id:
                match = (action, ev, meta)
                break
        if match:
            break
    if match is None:
        return (
            ProcedureActionResolution(
                status=ProcedureActionResolutionStatus.SOURCE_NOT_SELECTED,
                action_id=action_id,
                reason="action is not derivable from this run's SELECTED governed evidence",
            ),
            confirmed,
        )
    action, ev, meta = match

    authority_gap = _source_authority(meta)
    if authority_gap is not None:
        return (
            ProcedureActionResolution(
                status=ProcedureActionResolutionStatus.SOURCE_NOT_AUTHORITATIVE,
                action_id=action_id,
                action=action,
                reason=authority_gap,
            ),
            confirmed,
        )
    if is_command_prohibited_in_snippet(action.command_template, str(_field(ev, "content_snippet") or "")):
        return (
            ProcedureActionResolution(
                status=ProcedureActionResolutionStatus.PROHIBITED,
                action_id=action_id,
                action=action,
                reason="command is prohibited by its source section",
            ),
            confirmed,
        )

    target_validation: Optional[TargetValidation] = None
    target_parameters: set[str] = set()
    condition_check: Optional[dict[str, Any]] = None
    if action.action_type is ProcedureActionType.STATE_CHANGE:
        # Server-built evidence always carries the governed version's document-level structure
        # (general preconditions, condition scope). If it could not be established, the section
        # text alone is never enough to offer a state change.
        if "governed_scope" in meta and _resolved_scope(meta) is None:
            reason = str((meta.get("governed_scope") or {}).get("reason") or "not established") if isinstance(meta.get("governed_scope"), Mapping) else "not established"
            return (
                ProcedureActionResolution(
                    status=ProcedureActionResolutionStatus.GOVERNED_SCOPE_UNAVAILABLE,
                    action_id=action_id,
                    action=action,
                    reason=f"governed document structure (preconditions / condition scope) unavailable: {reason}",
                ),
                confirmed,
            )
        if action.condition_scope:
            condition_check = check_condition_scope(action.condition_scope, case_results or ())
            if not condition_check["passed"]:
                return (
                    ProcedureActionResolution(
                        status=ProcedureActionResolutionStatus.CONDITION_NOT_ESTABLISHED,
                        action_id=action_id,
                        action=action,
                        reason="the governed action applies only to a named condition this investigation's trusted evidence has not shown",
                        condition_check=condition_check,
                    ),
                    confirmed,
                )
        # TARGET GATE before anything is rendered: no candidate (so no Command Authority, no
        # confirmation card, no approval) exists for a state change whose target is not proven.
        target_validation = validate_action_targets(
            action, list(target_facts or []), fault_id=fault_id, proposals=proposals,
            operator_text=operator_text, confirmed_parameters=confirmed,
        )
        target_parameters = {t.parameter for t in action.targets if t.kind is ActionTargetKind.PARAMETERIZED and t.parameter}
        if not target_validation.passed:
            return (
                ProcedureActionResolution(
                    status=ProcedureActionResolutionStatus.TARGET_NOT_VALIDATED,
                    action_id=action_id,
                    action=action,
                    bindings=[
                        ParameterBinding(
                            name=d.parameter, value=None, detail=d.detail,
                            state=ParameterBindingState.AMBIGUOUS if d.status is TargetDecisionStatus.AMBIGUOUS
                            else ParameterBindingState.MISSING if d.status is TargetDecisionStatus.MISSING
                            else ParameterBindingState.CONFLICTING,
                        )
                        for d in target_validation.targets if d.parameter and d.status is not TargetDecisionStatus.VALIDATED
                    ],
                    reason=f"state-change target not validated for this fault: {target_validation.reason}",
                    target_validation=target_validation,
                    condition_check=condition_check,
                ),
                confirmed,
            )
    bindings, updated = reconcile_parameter_bindings(
        [p.name for p in action.parameters if p.name not in target_parameters], proposals, operator_text, confirmed, observed_texts
    )
    if target_validation is not None:
        # Target parameters are bound ONLY from the validated current-case target facts.
        bindings += [
            ParameterBinding(name=d.parameter, state=ParameterBindingState.VERIFIED, value=d.value, detail=f"current-case target fact {d.fact_ids}")
            for d in target_validation.targets if d.parameter and d.status is TargetDecisionStatus.VALIDATED
        ]
    rendered = render_command_template(action.command_template, bindings)
    composition = single_invocation_violation(rendered or action.command_template)
    if composition:  # defense in depth: an action object never bypasses the single-invocation boundary
        return (
            ProcedureActionResolution(
                status=ProcedureActionResolutionStatus.NOT_SINGLE_INVOCATION,
                action_id=action_id,
                action=action,
                bindings=bindings,
                reason=f"not a single command invocation ({composition})",
                target_validation=target_validation,
                condition_check=condition_check,
            ),
            updated,
        )
    if rendered is None:
        unresolved = [f"{b.name}={b.state.value}" for b in bindings if b.state is not ParameterBindingState.VERIFIED]
        return (
            ProcedureActionResolution(
                status=ProcedureActionResolutionStatus.PARAMETERS_UNRESOLVED,
                action_id=action_id,
                action=action,
                bindings=bindings,
                reason="unresolved parameters: " + ", ".join(unresolved),
                target_validation=target_validation,
                condition_check=condition_check,
            ),
            updated,
        )
    return (
        ProcedureActionResolution(
            status=ProcedureActionResolutionStatus.RESOLVED,
            action_id=action_id,
            action=action,
            bindings=bindings,
            candidate=_signed_candidate(action, rendered, {b.name: b.value for b in bindings if b.value}),
            reason="rendered from governed template with trusted parameters"
            + (" and a validated current-case target" if target_validation is not None else ""),
            target_validation=target_validation,
            condition_check=condition_check,
        ),
        updated,
    )


def check_condition_scope(condition_scope: Mapping[str, Any], case_results: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Is ANY governed condition alternative of the action literally shown by a trusted result of
    this fault? An alternative without a named value (structure says condition-specific, but the
    condition is not named) can never be established."""
    results = list(case_results or ())
    alternatives = [dict(a) for a in condition_scope.get("alternatives") or [] if isinstance(a, Mapping)]
    established = []
    for alternative in alternatives:
        evidence = ps.condition_evidence(alternative, results) if alternative.get("value") else []
        if evidence:
            established.append({"dimension": alternative.get("dimension"), "value": alternative.get("value"), "evidence": evidence})
    return {
        "passed": bool(established),
        "required_any_of": [{"dimension": a.get("dimension"), "value": a.get("value"), "source": a.get("source")} for a in alternatives],
        "established": established,
    }


def build_condition_clarification(condition_check: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    """Operator-facing explanation when a scoped state change does not apply (no command text)."""
    if not condition_check or condition_check.get("passed"):
        return None
    named = [f"{a.get('dimension')} '{a.get('value')}'" for a in condition_check.get("required_any_of") or [] if a.get("value")]
    text = (
        "The governed procedure scopes this action to " + " or ".join(named)
        + ", and this investigation's validated evidence has not shown that condition. Share the current output that "
        "shows it (for example the active alarm list) before this action can be considered."
        if named
        else "The governed procedure scopes this action to a condition it does not name, so it cannot be applied here."
    )
    return {"required_any_of": list(condition_check.get("required_any_of") or []), "text": text}


def build_parameter_clarification(bindings: Iterable[ParameterBinding]) -> Optional[dict[str, Any]]:
    """Deterministic operator clarification for unresolved parameters (never guesses a value)."""
    missing = [b.name for b in bindings if b.state is ParameterBindingState.MISSING]
    ambiguous = [b for b in bindings if b.state is ParameterBindingState.AMBIGUOUS]
    conflicting = [b for b in bindings if b.state is ParameterBindingState.CONFLICTING]
    if not (missing or ambiguous or conflicting):
        return None
    lines = []
    if missing:
        lines.append("To provide the governed command I need the exact value of: " + ", ".join(missing) + ".")
    for b in ambiguous:
        lines.append(f"Please confirm which value to use for {b.name} ({b.detail}).")
    for b in conflicting:
        lines.append(f"The value for {b.name} conflicts ({b.detail}); please restate the intended value.")
    return {
        "missing_parameters": missing,
        "ambiguous_parameters": [b.name for b in ambiguous],
        "conflicting_parameters": [b.name for b in conflicting],
        "text": " ".join(lines),
    }


def resolution_summary(resolution: ProcedureActionResolution, authority: Optional[str] = None) -> dict[str, Any]:
    """Identity/decision-only view for tool results, execution records and traces."""
    action = resolution.action
    return {
        "action_id": resolution.action_id,
        "status": resolution.status.value,
        "reason": resolution.reason,
        "intent": action.intent if action else None,
        "action_type": action.action_type.value if action else None,
        "source": action.source.canonical_source_id if action else None,
        "command_template": action.command_template if action else None,
        "parameters": [
            {"name": b.name, "state": b.state.value, "value": b.value, "detail": b.detail} for b in resolution.bindings
        ],
        "rendered_command": resolution.candidate.command if resolution.candidate else None,
        "command_authority": authority,
        "target_validation": resolution.target_validation.view() if resolution.target_validation is not None else None,
        "condition_check": dict(resolution.condition_check) if resolution.condition_check else None,
        "instance_semantics": action.instance_semantics.value if action else None,
    }


# ---------------------------------------------------------------------------------------------
# TAE tool: server-issued catalog
# ---------------------------------------------------------------------------------------------


async def procedure_action_catalog(tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """List the governed procedure actions available for the evidence you have SELECTED in this
    turn (call `knowledge_select_evidence` first). Each action states WHAT an approved procedure
    step checks; the server -- not you -- holds its exact command and renders it.

    To use one, set `diagnostic_step.procedure_action_id` to its exact `action_id` (never invent
    or modify an id). If it lists `required_parameters`, add `diagnostic_step.parameter_values`
    entries `{name, value}` only with values the operator literally typed, copied verbatim; omit
    any you do not have. Leave `command` and `command_source` null.

    Returns `actions` (usable this turn) and `unavailable_sources` (selected procedures that cannot
    supply a command, with the reason).
    """
    from backend.agents.technical_authority_engineer.agent_tool import build_server_validated_evidence
    from backend.api.turn_context import current_run_id
    from backend.tools.knowledge.diagnostic_trace import record_action_catalog

    run_id = current_run_id()
    if not run_id:
        return {"actions": [], "unavailable_sources": [], "note": "no active run"}
    await ps.ensure_governed_scope(run_id)
    evidence = build_server_validated_evidence(run_id, None, [])
    actions, unavailable = build_procedure_action_catalog(evidence)
    record_issued_actions(run_id, actions)
    skipped = [
        {**k, "source": str(meta.get("knowledge_id")) + ":" + str(meta.get("version_label")) + ":" + str(meta.get("section_id"))}
        for ev, meta in _governed_selected(evidence) if _source_authority(meta) is None
        for k in actions_for_evidence(ev, meta)[1]
    ]
    record_action_catalog(
        skipped=skipped,
        actions=[
            {
                "action_id": a.action_id,
                "intent": a.intent,
                "action_type": a.action_type.value,
                "source": a.source.canonical_source_id,
                "command_template": a.command_template,
                "parameters": [p.name for p in a.parameters],
                **a.governance_view(),
            }
            for a in actions
        ],
        unavailable=unavailable,
    )
    return {
        "actions": [a.catalog_entry() for a in actions],
        "unavailable_sources": unavailable,
        "note": (
            "No governed procedure action is available for the selected evidence. Do not turn evidence that needs a "
            "command into a manual or operator task: set acquisition 'none' (the server records the gap); a manual "
            "check is only for evidence observable without any command."
            if not actions
            else "Choose at most one action_id for the single next diagnostic step."
        ),
    }
