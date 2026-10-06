"""Deterministic gap recovery: a gapped evidence branch is not an exhausted investigation.

    EvidenceRequirement A -> acquisition resolution -> no governed method -> GAP(A)
        -> A is an exhausted BRANCH for the current context (kept in state and audit, never hidden)
        -> the server evaluates the remaining VALID governed diagnostic paths of the active fault
              alternatives exist -> the specialist chooses among them (it may rank and explain);
                                    the chosen one goes through the normal governed path
              none exist         -> the gap is the current limitation (terminal) / escalation

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

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Optional

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


def proposed_branch_requirement(payload: Optional[Mapping[str, Any]], fault_id: str) -> tuple[Optional[EvidenceRequirement], bool]:
    """(the acquirable evidence requirement a specialist payload proposes WITHOUT any acquisition
    method -- the shape that ends in a governed acquisition gap --, whether it was derived from a
    "no method for X" item). An `insufficient_evidence` outcome naming the evidence, or a
    `recommended` step with no command and no procedure action. Mirrors the server's own requirement
    derivation (evidence_acquisition / agent_tool); (None, False) when it proposes a method or nothing."""
    if not isinstance(payload, Mapping):
        return None, False
    outcome = payload.get("outcome")
    requirement: Optional[EvidenceRequirement] = None
    from_mechanism = False
    if outcome == "recommended":
        step = payload.get("diagnostic_step")
        if not isinstance(step, Mapping) or str(step.get("command") or "").strip() or str(step.get("procedure_action_id") or "").strip():
            return None, False
        requirement = requirement_from_step(step, fault_id, attempted_command=False)
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


def proposes_method(payloads: Iterable[Any]) -> bool:
    """Any payload of the specialist's message names an acquisition method (command or procedure
    action) -- judged on RAW payloads too: an integrity callback may already have stripped it."""
    for raw in payloads or ():
        step = raw.get("diagnostic_step") if isinstance(raw, Mapping) else None
        if isinstance(step, Mapping) and (str(step.get("command") or "").strip() or str(step.get("procedure_action_id") or "").strip()):
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
