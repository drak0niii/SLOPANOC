"""Deterministic gap recovery: a gapped evidence branch is not an exhausted investigation.

    EvidenceRequirement A -> acquisition resolution -> no governed method -> GAP(A)
        -> A is an exhausted BRANCH for the current context (kept in state and audit, never hidden)
        -> the server evaluates the remaining VALID governed diagnostic paths of the active fault
              alternatives exist -> the specialist chooses among them (it may rank and explain);
                                    the chosen one goes through the normal governed path
              none exist         -> the gap is the current limitation (terminal) / escalation
        -> still a gap after that bounded path -> ONE continuation request: the specialist keeps reasoning
           from the evidence already collected (next hypothesis / evidence need); the server states the gap
           and labels model reasoning in the final answer (render_acquisition_gap_notice)

The server determines the valid CANDIDATE SET; it never chooses the troubleshooting step and never
invents one. A candidate is a ProcedureAction the deterministic extractor derives from governed
evidence of THIS run (AVAILABLE or SELECTED -- identity only, zero authority) that is:
    approved + applicability MATCH + procedural source (the catalog's own admission rule),
    a DIAGNOSTIC READ (a fallback branch never offers a state change),
    not already performed / attempted / known for this fault (action id, governed action key, or
    the observation an identical fixed command already produced),
    not blocked by an incomplete governed MANDATORY prerequisite.
Offering a candidate grants nothing: the specialist must still select its source explicitly in this
run, the server re-derives the action from SELECTED evidence, and the resolver, target gate, Command
Authority and universal egress decide exactly as for any other step.

Deterministic; no model call; no vendor, technology, fault, alarm or command vocabulary.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Mapping, Optional

from backend.agents.technical_authority_engineer.evidence_acquisition import (
    evidence_from_mechanism_item,
    requirement_from_proposal,
    requirement_from_step,
    split_missing_information,
)
from backend.agents.technical_authority_engineer.step_identity import candidate_identity, normalize_command, step_identity
from backend.cases.evidence_identity import EvidenceIdentity, identity_of
from backend.cases.evidence_model import CandidateValidation, EvidenceKind, EvidenceRequirement, GapStatus
from backend.cases.troubleshooting_progression import StepStatus, TroubleshootingProgression

GAP_RECOVERY_KEY = "gap_recovery"
"""Server-owned tool-result / trace key (never model-supplied)."""

GAP_CONTINUATION_KEY = "acquisition_gap_continuation"
"""Server-owned execution-record key: the governed acquisition gaps recorded in this run that the final
answer continues past (never model-supplied; never shown to the Team Manager as model input)."""

GovernedCommand = Callable[[str, Optional[str]], bool]
"""(command, cited source) -> whether a model-written command may count as an acquisition method: False
for a single diagnostic command that names no governed action of THIS run's SELECTED evidence
(agent_tool._ungrounded_command / procedure_actions.names_governed_action)."""

MAX_ALTERNATIVES = 10

_ACQUIRABLE = frozenset({EvidenceKind.DIAGNOSTIC_RESULT, EvidenceKind.LIVE_OPERATIONAL_CONTEXT})
_NOT_ATTEMPTED = frozenset({StepStatus.CANCELLED, StepStatus.SUPERSEDED, StepStatus.REJECTED})
"""Steps that never ran or were withdrawn: their action stays available as a branch."""


@dataclass(frozen=True)
class GovernedAlternative:
    """One valid governed diagnostic branch (structural identity only, zero authority)."""

    action: Any
    identity: EvidenceIdentity
    applicability: Optional[str]

    def model_view(self) -> dict[str, Any]:
        """What the specialist is told: identity and purpose (the same fields the catalog tool already
        exposes, incl. its distinguishing `intent`); no authority."""
        return {
            "procedure_action_id": self.action.action_id,
            "intent": self.action.intent,
            "description": self.action.description,
            "procedure": self.action.source.title,
            "section": self.action.source.heading,
            "selection_key": self.identity.selection_key(),
            "required_parameters": [p.name for p in self.action.parameters],
        }

    def view(self) -> dict[str, Any]:
        """Diagnostics (identifiers only)."""
        return {"procedure_action_id": self.action.action_id, "source": self.identity.canonical, "applicability": self.applicability}


def _names_method(step: Mapping[str, Any], governed_command: Optional[GovernedCommand]) -> bool:
    """The step names an acquisition method: a procedure action, or a command -- when
    `governed_command` is given, only a command that names a governed action of THIS run (a command
    with no governed source is not a method, whatever the model wrote)."""
    if str(step.get("procedure_action_id") or "").strip():
        return True
    command = str(step.get("command") or "").strip()
    if not command:
        return False
    return governed_command is None or governed_command(command, str(step.get("command_source") or "").strip() or None)


def attempted_ungrounded_command(payloads: Iterable[Any], governed_command: Optional[GovernedCommand]) -> bool:
    """Any payload's step carried a command that names no governed action of THIS run (judged on RAW
    payloads too: an integrity callback may already have stripped it). Its evidence then needs a
    command or system output: a diagnostic result, never a manual observation."""
    if governed_command is None:
        return False
    for raw in payloads or ():
        step = raw.get("diagnostic_step") if isinstance(raw, Mapping) else None
        if isinstance(step, Mapping) and str(step.get("command") or "").strip() and not _names_method(step, governed_command):
            return True
    return False


def proposed_branch_requirement(
    payload: Optional[Mapping[str, Any]],
    fault_id: str,
    governed_command: Optional[GovernedCommand] = None,
    *,
    attempted_command: bool = False,
) -> tuple[Optional[EvidenceRequirement], bool]:
    """(the acquirable evidence requirement a specialist payload proposes WITHOUT any acquisition
    method -- the shape that ends in a governed acquisition gap --, whether it was derived from a
    "no method for X" item). An `insufficient_evidence` outcome naming the evidence, or a
    `recommended` step with no procedure action and no command (with `governed_command`: no command
    naming a governed action of this run). `attempted_command`: a command with no governed source was
    proposed for it (`attempted_ungrounded_command`). Mirrors the server's own requirement derivation
    (evidence_acquisition / agent_tool); (None, False) when it proposes a method or nothing."""
    if not isinstance(payload, Mapping):
        return None, False
    outcome = payload.get("outcome")
    requirement: Optional[EvidenceRequirement] = None
    from_mechanism = False
    if outcome == "recommended":
        step = payload.get("diagnostic_step")
        if not isinstance(step, Mapping) or _names_method(step, governed_command):
            return None, False
        requirement = requirement_from_step(
            step, fault_id, attempted_command=attempted_command or bool(str(step.get("command") or "").strip())
        )
    elif outcome == "insufficient_evidence":
        proposals = [p for p in payload.get("required_evidence") or [] if isinstance(p, Mapping)]
        requirements = [r for r in (requirement_from_proposal(p, fault_id) for p in proposals) if r is not None]
        requirements = [r for r in requirements if r.kind in _ACQUIRABLE]
        if requirements:
            requirement = requirements[0]
        else:
            evidence_items, system_items = split_missing_information(list(payload.get("missing_information") or []))
            description = next((d for d in (evidence_from_mechanism_item(i) for i in system_items) if d), None)
            if description:
                requirement = requirement_from_proposal({"kind": "diagnostic_result", "description": description}, fault_id)
                from_mechanism = requirement is not None
    if requirement is None or requirement.kind not in _ACQUIRABLE:
        return None, False
    return requirement, from_mechanism


def _performed(progression: TroubleshootingProgression, fault_id: str) -> tuple[set[str], set[str], set[str]]:
    """(action ids, governed action keys, observation keys) of this fault's attempted steps."""
    ids: set[str] = set()
    keys: set[str] = set()
    observations: set[str] = set()
    for step in progression.steps_for(fault_id):
        if step.status in _NOT_ATTEMPTED:
            continue
        identity = step_identity(step)
        if step.procedure_action_id:
            ids.add(step.procedure_action_id)
        if identity.action_key:
            keys.add(identity.action_key)
        for value in (identity.result_key, normalize_command(step.command)):
            if value:
                observations.add(value)
    for requirement in progression.requirements_for(fault_id):
        for candidate in requirement.acquisition_candidates:
            if candidate.procedure_action_id and candidate.validation is CandidateValidation.REJECTED_CURRENT_RUN:
                ids.add(candidate.procedure_action_id)
    return ids, keys, observations


def viable_governed_alternatives(
    evidence: Iterable[Any], progression: TroubleshootingProgression, fault_id: str
) -> tuple[list[GovernedAlternative], list[dict[str, Any]]]:
    """(valid governed diagnostic branches, excluded actions with the reason) for the active fault,
    from THIS run's governed evidence. See the module docstring for the admission rules."""
    from backend.agents.technical_authority_engineer import resolution_gates as gates
    from backend.agents.technical_authority_engineer.procedure_actions import ProcedureActionType, build_procedure_action_catalog

    items = list(evidence or [])
    actions, _ = build_procedure_action_catalog(items)
    by_id = {a.action_id: a for a in actions}
    applicability = {identity_of(ev): str(((ev.get("metadata") if isinstance(ev, Mapping) else getattr(ev, "metadata", None)) or {})
                                         .get("applicability_outcome") or "").lower() or None for ev in items}
    ids, keys, observations = _performed(progression, fault_id)
    steps = progression.steps_for(fault_id)
    alternatives: list[GovernedAlternative] = []
    excluded: list[dict[str, Any]] = []
    for action in actions:
        reason = None
        template = normalize_command(action.command_template)
        if action.action_type is not ProcedureActionType.DIAGNOSTIC_READ:
            reason = "not_a_diagnostic_read"
        elif action.action_id in ids or gates.action_key(action) in keys or (not action.parameters and template in observations):
            reason = "already_performed_or_known"
        else:
            resolution = SimpleNamespace(action=action, candidate=None, bindings=[])
            prerequisites = gates.structured_prerequisites(action, candidate_identity(fault_id, {}, resolution), steps, by_id)
            if not prerequisites["met"]:
                reason = "mandatory_prerequisite_incomplete"
        identity = identity_of(action.source)
        if reason is None and identity is None:
            reason = "no_structured_source_identity"
        if reason is not None:
            excluded.append({"procedure_action_id": action.action_id, "reason": reason})
            continue
        alternatives.append(GovernedAlternative(action=action, identity=identity, applicability=applicability.get(identity)))
    return alternatives[:MAX_ALTERNATIVES], excluded


def open_gapped_requirements(progression: TroubleshootingProgression, fault_id: str) -> list[EvidenceRequirement]:
    """This fault's unsatisfied requirements that carry an OPEN acquisition gap (exhausted branches)."""
    return [r for r in progression.open_requirements(fault_id)
            if any(g.requirement_id == r.requirement_id and g.status is GapStatus.OPEN for g in progression.acquisition_gaps)]


def recovery_instruction(requirement: EvidenceRequirement, alternatives: list[GovernedAlternative], *, repeated: bool) -> str:
    """One bounded instruction to the specialist: WHAT the server determined; never a choice for it."""
    state = (
        "was already found to have no governed acquisition method and nothing material has changed since"
        if repeated else "has no governed acquisition method in the current context"
    )
    return (
        f"SERVER PROGRESSION CHECK: the evidence you asked for ({requirement.description}) {state}. It is recorded as a "
        "governed acquisition gap: that BRANCH is exhausted for now, not the investigation. Do not propose it again "
        "without new evidence. The server identified these governed diagnostic actions from THIS run's AVAILABLE, "
        "applicability-MATCH evidence that this fault has not performed (identity only, no authority): "
        f"{[a.model_view() for a in alternatives]}. If one of them is technically appropriate for the recorded "
        "observations, select its source with knowledge_select_evidence (its exact selection_key) and set "
        "diagnostic_step.procedure_action_id to its procedure_action_id; the server re-derives it from your selection "
        "and re-authorizes it in this run. If none is appropriate, return escalation_required with the reason. "
        "Then return your complete final JSON response."
    )


def proposes_method(payloads: Iterable[Any], governed_command: Optional[GovernedCommand] = None) -> bool:
    """Any payload of the specialist's message names an acquisition method (command or procedure
    action) -- judged on RAW payloads too: an integrity callback may already have stripped it. With
    `governed_command`, a command that names no governed action of THIS run is not a method."""
    for raw in payloads or ():
        step = raw.get("diagnostic_step") if isinstance(raw, Mapping) else None
        if isinstance(step, Mapping) and _names_method(step, governed_command):
            return True
    return False


def action_choice_instruction(requirement: EvidenceRequirement, actions: list[GovernedAlternative]) -> str:
    """One bounded request: valid governed actions EXIST for the evidence the specialist named; it must
    choose one explicitly or say none applies. The server supplied only valid candidates; it chooses
    nothing and authorizes nothing here."""
    return (
        f"SERVER ACTION CHECK: you named the evidence needed ({requirement.description}) without choosing a governed "
        "procedure action. Valid governed diagnostic actions are available from the evidence you SELECTED in this run "
        f"(identity and purpose only; the server holds the commands): {[a.model_view() for a in actions]}. Choose ONE "
        "whose purpose obtains that evidence and set diagnostic_step.procedure_action_id to its exact procedure_action_id "
        "(never invent or modify an id); the server re-derives it from your selection and re-authorizes it in this run. "
        "If none of them obtains that evidence, keep your evidence need and state explicitly in technical_interpretation "
        "that none of the supplied actions applies -- do not invent an action. Then return your complete final JSON response."
    )


def resume_choice_instruction(answered: Mapping[str, list[str]], actions: list[GovernedAlternative]) -> str:
    """One bounded request after an applicability clarification resumed the investigation and the
    specialist proposed nothing actionable: valid governed diagnostic actions EXIST in the evidence it
    SELECTED in this run. It must choose one, or explicitly say none applies, or escalate. The server
    supplied only valid candidates (no order, no preference); it chooses nothing and authorizes nothing."""
    context = "; ".join(f"{k} = {', '.join(v)}" for k, v in answered.items()) or "the requested applicability context"
    return (
        f"SERVER CONTINUATION CHECK: the operator answered the applicability clarification this investigation was "
        f"waiting on ({context}). The governed evidence you SELECTED in this run now applies and the investigation is "
        "not resolved, but your response proposed no next step. Valid governed diagnostic actions are available from that "
        f"selected evidence (identity and purpose only; the server holds the commands): {[a.model_view() for a in actions]}. "
        "Resume the investigation with exactly ONE next diagnostic check: choose the action whose purpose is the most useful "
        "next check for this fault and set diagnostic_step.procedure_action_id to its exact procedure_action_id (never invent "
        "or modify an id); the server re-derives it from your selection and re-authorizes it in this run. If none of them "
        "applies, say so explicitly in technical_interpretation and list the evidence still required in required_evidence, "
        "or escalate. Then return your complete final JSON response."
    )


NON_TERMINAL_GAP_NOTE = (
    "This is a limitation of one diagnostic branch, not of the investigation: other governed diagnostic checks "
    "remain available for this fault in the current context. Ask to continue to proceed with one of them."
)


# ---------------------------------------------------------------------------------------------
# Acquisition-gap continuation: after the bounded governed recovery path, the gap ends one BRANCH;
# the specialist is asked ONCE to keep reasoning from the evidence already collected.
# ---------------------------------------------------------------------------------------------


def gap_continuation_instruction(requirement: EvidenceRequirement, gap_reason: Optional[str], *, repeated: bool) -> str:
    """One bounded request after the governed recovery path (current catalog, governed alternatives,
    one server search) established no grounded acquisition method for the specialist's evidence need.
    The server states WHAT it determined; the specialist owns the next hypothesis / evidence need, and
    the server still owns every method and all authority."""
    state = " It was already recorded as a gap and nothing material has changed since." if repeated else ""
    return (
        f"SERVER ACQUISITION GAP CONTINUATION: the evidence you requested ({requirement.description}) could not be acquired "
        "through currently selected and validated governed knowledge. No grounded acquisition method was found or "
        "cross-checked in the current governed knowledge for this evidence requirement; the server recorded it as a "
        f"governed acquisition gap ({gap_reason or 'no governed acquisition method'}).{state} That BRANCH is exhausted for "
        "now, not the investigation.\n"
        "Continue reasoning from the evidence already available. Determine the next best diagnostic hypothesis or "
        "evidence requirement.\n"
        "Do not invent a command. Do not imply that an ungrounded method is governed. Do not repeat the same "
        "unavailable acquisition requirement unless new evidence makes it relevant.\n"
        "The server resolves the method of your next evidence requirement as usual: search and select governed knowledge "
        "and set diagnostic_step.procedure_action_id only to an action_id of this run's procedure_action_catalog; when "
        "no governed method exists, keep command and procedure_action_id null with acquisition \"none\". State your "
        "hypothesis as a hypothesis in technical_interpretation. If the investigation cannot continue safely, return "
        "escalation_required with the reason. Then return your complete final JSON response."
    )


_GAP_NOTICE = (
    "The requested evidence ({description}) was identified as a useful diagnostic direction, but no governed method "
    "for obtaining it could be found or cross-checked in the currently selected and validated governed knowledge. No "
    "operational command is provided for that check; it has been recorded as a governed acquisition gap."
)
_GAP_NOTICE_CONTINUES = "The investigation continues from the evidence already collected."
_KNOWLEDGE_STATUS = (
    "Knowledge status: the diagnostic reasoning in this answer was generated by the troubleshooting model and was not "
    "directly cross-checked against currently selected governed knowledge"
)
_KNOWLEDGE_STATUS_GOVERNED_STEP = (
    "; the command of the next diagnostic step comes from an approved governed procedure and passed authorization in "
    "this turn."
)
_KNOWLEDGE_STATUS_NO_COMMAND = "; no operational command is provided for it."
_MODEL_HYPOTHESIS = (
    "Diagnostic hypothesis (generated by the troubleshooting model; not verified against current governed knowledge): "
    "{statement}"
)


def _model_hypothesis(execution: Mapping[str, Any]) -> Optional[str]:
    step = execution.get("diagnostic_step") if isinstance(execution.get("diagnostic_step"), Mapping) else {}
    statements = [step.get("tests_hypothesis")] + [
        u.get("statement") for u in execution.get("hypothesis_updates") or [] if isinstance(u, Mapping)
    ]
    return next((str(s).strip() for s in statements if isinstance(s, str) and s.strip()), None)


def render_acquisition_gap_notice(execution: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Server-rendered statement of the governed acquisition gaps the validated record continues past
    (GAP_CONTINUATION_KEY), and the knowledge status of what follows: model reasoning is labelled as
    such; only an authorized command of the record's own step is called governed. Built from server
    state only (requirement descriptions, the record's step) -- never a command, never synthesis prose.
    None when the record continues past no gap."""
    from backend.agents.technical_authority_engineer.synthesis_boundary import _sanitized_prose

    view = execution.get(GAP_CONTINUATION_KEY) if isinstance(execution, Mapping) else None
    gaps = [g for g in (view.get("gaps") or []) if isinstance(g, Mapping)] if isinstance(view, Mapping) else []
    if not gaps:
        return None
    sentences = []
    for gap in gaps:
        description = _sanitized_prose(gap.get("description")).rstrip(".") or "the evidence needed for that check"
        sentences.append(_GAP_NOTICE.format(description=description))
    step = execution.get("diagnostic_step") if isinstance(execution.get("diagnostic_step"), Mapping) else {}
    governed_step = execution.get("outcome") == "recommended" and bool(str(step.get("command") or "").strip())
    sections = [" ".join(sentences) + " " + _GAP_NOTICE_CONTINUES]
    hypothesis = _sanitized_prose(_model_hypothesis(execution))
    if hypothesis:
        sections.append(_MODEL_HYPOTHESIS.format(statement=hypothesis))
    sections.append(_KNOWLEDGE_STATUS + (_KNOWLEDGE_STATUS_GOVERNED_STEP if governed_step else _KNOWLEDGE_STATUS_NO_COMMAND))
    return "\n\n".join(sections)


_SERVER_LABEL = re.compile(
    r"^\W*(?:knowledge status:|diagnostic hypothesis \(generated by the troubleshooting model"
    r"|the requested evidence \(.*\) was identified as a useful diagnostic direction)",
    re.IGNORECASE,
)
"""Lines only the server renders (gap notice, knowledge status): a synthesis copy -- e.g. imitated from an
earlier answer in the conversation -- is never kept, so a label always reflects THIS turn's record."""


def with_acquisition_gap_notice(final_text: str, execution: Optional[Mapping[str, Any]]) -> str:
    """`final_text` without any synthesis copy of the server's gap / knowledge-status labels, preceded by
    the server-rendered gap notice of the validated record (once) when it continues past a gap."""
    if not isinstance(final_text, str):
        return final_text
    kept = "\n".join(line for line in final_text.splitlines() if not _SERVER_LABEL.match(line)).strip()
    text = re.sub(r"\n{3,}", "\n\n", kept) if kept else final_text
    notice = render_acquisition_gap_notice(execution)
    if not notice:
        return text
    return f"{notice}\n\n{text}" if kept else notice
