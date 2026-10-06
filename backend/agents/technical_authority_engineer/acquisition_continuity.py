"""Acquisition continuity: a server-known governed acquisition survives model omission.

    EvidenceRequirement (WHAT is needed)  --  AcquisitionCandidate[] (HOW: governed action identity)
    pending TroubleshootingStep           --  BlockedGovernedAction (identity of the blocked read)

Evidence continuity keeps the requirement; acquisition continuity keeps HOW it is obtained. When
the pending step's evidence has a known governed acquisition (a ProcedureAction identity derived
from governed evidence in an earlier run), the server -- not the model -- owns it:

    APPLICABILITY_CLARIFICATION_RESOLVED  the clarification that blocked the step is answered -- or,
                                          with no pending step at all, the fault's open applicability
                                          clarification itself is answered (clarification continuity
                                          is not pending-command continuity)
    ACQUISITION_REQUEST                   the operator asks HOW to obtain the pending evidence
                                          ("what command?", "how do I get that?"): the existing
                                          COMMAND_FOLLOW_UP turn, bound to the pending requirement
    GENERIC_CONTINUATION                  the operator asks to go on without naming a method and
                                          without adding anything of its own (no result, no new
                                          objective, no clarification answer): the pending step's
                                          known governed method is what there is to continue
    MODEL_OMISSION                        the specialist restates the pending need without its method

Each operator turn is first classified deterministically against server state (`ContinuationKind`):
RESULT_PROVIDED, NEW_OBJECTIVE, CLARIFICATION_ANSWER, COMMAND_FOLLOW_UP, GENERIC_CONTINUATION or
NONE. A generic continuation is only ever acted on when the state proves there is something valid
to continue: a pending step awaiting its result, a known governed acquisition that is not waiting for
an open clarification, and the known source not AVAILABLE in the current run -- then the server runs a
fresh governed discovery (AVAILABLE only). Selection always stays the specialist's explicit decision.

In each case the known identity is reconciled with the specialist's proposal: the specialist may
re-propose it or add others, but omitting it never removes it.

Identity is structural only; authority is always fresh. The identity is a LOOKUP KEY into THIS
run's ProcedureAction catalog, which is built only from THIS run's SELECTED governed evidence
(approved + applicability MATCH); the normal resolver and Command Authority then decide. Nothing
here grants authority, selects evidence, promotes AVAILABLE evidence or reads an earlier run's
authorization. Deterministic; no model call; no vendor, technology, fault or command vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from backend.agents.technical_authority_engineer.evidence_acquisition import (
    evidence_description,
    evidence_from_mechanism_item,
    is_acquisition_mechanism_item,
    match_open_requirement,
    proposed_ref,
    requirement_from_proposal,
    requirement_from_step,
    semantic_tokens,
    structural_governed_candidates,
)
from backend.cases.evidence_identity import EvidenceIdentity, identity_of
from backend.cases.evidence_model import EvidenceKind, EvidenceRequirement, RequirementStatus
from backend.cases.troubleshooting_progression import (
    ClarificationReason,
    ClarificationStatus,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
)

ACQUISITION_CONTINUITY_KEY = "acquisition_continuity"
"""Server-owned tool-result / trace key (never model-supplied)."""

_ACQUIRABLE = frozenset({EvidenceKind.DIAGNOSTIC_RESULT, EvidenceKind.LIVE_OPERATIONAL_CONTEXT})


RECONCILABLE_OUTCOMES = frozenset({"recommended", "insufficient_evidence"})
"""Specialist outcomes a known governed acquisition may be reconciled with."""


class ContinuationRule(str, Enum):
    APPLICABILITY_CLARIFICATION_RESOLVED = "applicability_clarification_resolved"
    ACQUISITION_REQUEST = "acquisition_request"
    GENERIC_CONTINUATION = "generic_continuation"
    MODEL_OMISSION = "model_omission"


class ContinuationKind(str, Enum):
    """What the operator's turn is, relative to the server-owned progression state."""

    RESULT_PROVIDED = "result_provided"
    """Output for the pending step (validated, or recorded as an unvalidated candidate)."""
    NEW_OBJECTIVE = "new_objective"
    """The operator cancelled / replaced the pending step or moved to another subject."""
    CLARIFICATION_ANSWER = "clarification_answer"
    COMMAND_FOLLOW_UP = "command_follow_up"
    """Asks for the pending step's acquisition method ("what command?", "how do I check that?")."""
    GENERIC_CONTINUATION = "generic_continuation"
    """Asks to go on with the pending step, adding nothing of its own ("what next?", "continue")."""
    NONE = "none"
    """No pending step, a report that the step cannot be performed, or a message with its own content."""


def continuation_kind(
    turn_kind: str,
    *,
    clarification_answered: bool,
    new_objective: bool,
    mechanism_requested: bool,
) -> ContinuationKind:
    """Deterministic: from the controller's turn classification (`TurnKind` value), the server's
    clarification binding, the thread / request-contract decision (`new_objective`) and whether the
    message names an acquisition method. A follow-up turn (the controller's COMMAND_FOLLOW_UP) is a
    COMMAND_FOLLOW_UP when it names a method, else a GENERIC_CONTINUATION. No phrase list here."""
    if clarification_answered:
        return ContinuationKind.CLARIFICATION_ANSWER
    if new_objective or turn_kind == "objective_change":
        return ContinuationKind.NEW_OBJECTIVE
    if turn_kind in ("result", "unvalidated_result"):
        return ContinuationKind.RESULT_PROVIDED
    if turn_kind != "command_follow_up":
        return ContinuationKind.NONE
    return ContinuationKind.COMMAND_FOLLOW_UP if mechanism_requested else ContinuationKind.GENERIC_CONTINUATION


def _norm(value: Optional[str]) -> str:
    return " ".join(str(value or "").split()).casefold()


@dataclass(frozen=True)
class KnownGovernedAcquisition:
    """Structural identity of the pending step's governed acquisition (zero authority)."""

    step_id: str
    procedure_action_id: str
    source_id: Optional[str]
    """Canonical display string (diagnostics only; never parsed or compared as identity)."""
    normalized_template: Optional[str]
    origin: str
    """blocked_action | pending_step_action | requirement_candidate"""
    requirement_id: Optional[str] = None
    acquisition_candidate_id: Optional[str] = None
    blocked: bool = False
    clarification_id: Optional[str] = None
    clarification_open: bool = False
    identity: Optional[EvidenceIdentity] = None
    """Structured source identity (exact tuple); None for a legacy record that never stored one."""

    @property
    def selection_key(self) -> Optional[dict[str, str]]:
        """The exact selection key of the known source -- from its structured identity only."""
        return self.identity.selection_key() if self.identity is not None else None

    def view(self) -> dict[str, Any]:
        """Diagnostics (identifiers only)."""
        return {
            "step_id": self.step_id,
            "procedure_action_id": self.procedure_action_id,
            "source_id": self.source_id,
            "origin": self.origin,
            "requirement_id": self.requirement_id,
            "acquisition_candidate_id": self.acquisition_candidate_id,
            "blocked_action": {
                "step_id": self.step_id, "procedure_action_id": self.procedure_action_id, "source_id": self.source_id,
                "clarification_id": self.clarification_id, "clarification_open": self.clarification_open,
            } if self.blocked else None,
        }

    def model_view(self) -> dict[str, Any]:
        """What the specialist is told: identity only, and how it may be used."""
        return {
            "procedure_action_id": self.procedure_action_id,
            "selection_key": self.selection_key,
            "requirement_id": self.requirement_id,
            "awaiting_applicability_clarification": self.clarification_open,
            "authority": (
                "none -- structural identity only. In THIS run, select its source with knowledge_select_evidence if it "
                "applies and set diagnostic_step.procedure_action_id to this id; the server re-resolves it from this run's "
                "selected evidence and Command Authority re-authorizes it. Never repeat a command from an earlier turn."
            ),
        }


def _clarification_state(progression: TroubleshootingProgression, fault_id: str, clarification_id: Optional[str]) -> tuple[Optional[str], bool]:
    """(clarification id, still OPEN?) of the applicability clarification blocking a step. An
    unlinked block counts as open while the fault has an OPEN applicability clarification."""
    if clarification_id:
        question = next((q for q in progression.open_questions if q.question_id == clarification_id and q.fault_id == fault_id), None)
        if question is not None:
            return clarification_id, question.status is ClarificationStatus.OPEN
    pending = progression.pending_clarification(fault_id, ClarificationReason.APPLICABILITY)
    if pending is not None:
        return pending.question_id, True
    return clarification_id, False


def known_governed_acquisition(
    progression: TroubleshootingProgression, fault_id: str, step: Optional[TroubleshootingStep]
) -> Optional[KnownGovernedAcquisition]:
    """The pending step's known governed acquisition, from (in order) its applicability-blocked
    governed action, its own resolved ProcedureAction, or its open requirement's governed
    candidate. None when the step has no structural governed identity (wording never creates one)."""
    if step is None or step.fault_id != fault_id:
        return None
    requirement = progression.requirement(step.evidence_requirement_id) if step.evidence_requirement_id else None
    if requirement is not None and requirement.status is not RequirementStatus.UNSATISFIED:
        requirement = None
    governed = structural_governed_candidates(requirement) if requirement is not None else []
    governed = sorted(governed, key=lambda c: c.acquisition_id != (requirement.selected_acquisition_id if requirement else None))

    def _candidate_for(action_id: Optional[str]) -> Any:
        return next((c for c in governed if action_id and c.procedure_action_id == action_id), None)

    blocked = step.blocked_candidate if step.status is StepStatus.BLOCKED_BY_CLARIFICATION else None
    if blocked is not None and blocked.procedure_action_id and blocked.released_at is None:
        clarification_id, is_open = _clarification_state(progression, fault_id, blocked.clarification_id)
        candidate = _candidate_for(blocked.procedure_action_id)
        return KnownGovernedAcquisition(
            step_id=step.step_id, procedure_action_id=blocked.procedure_action_id, source_id=blocked.source_id,
            normalized_template=_norm(blocked.normalized_command) or None, origin="blocked_action",
            requirement_id=requirement.requirement_id if requirement else None,
            acquisition_candidate_id=candidate.acquisition_id if candidate else None,
            blocked=True, clarification_id=clarification_id, clarification_open=is_open, identity=blocked.identity,
        )
    if step.procedure_action_id:
        candidate = _candidate_for(step.procedure_action_id)
        template = (candidate.command_template if candidate else None) or (step.identity.template if step.identity else None)
        own_source = bool(step.command_source_id)
        return KnownGovernedAcquisition(
            step_id=step.step_id, procedure_action_id=step.procedure_action_id,
            source_id=step.command_source_id if own_source else (candidate.canonical_source_id if candidate else None),
            normalized_template=_norm(template) or None, origin="pending_step_action",
            requirement_id=requirement.requirement_id if requirement else None,
            acquisition_candidate_id=candidate.acquisition_id if candidate else None,
            identity=step.command_source_identity if own_source else (candidate.source_identity if candidate else None),
        )
    candidate = next((c for c in governed if c.procedure_action_id), None)
    if candidate is not None:
        return KnownGovernedAcquisition(
            step_id=step.step_id, procedure_action_id=candidate.procedure_action_id, source_id=candidate.canonical_source_id,
            normalized_template=_norm(candidate.command_template) or None, origin="requirement_candidate",
            requirement_id=requirement.requirement_id if requirement else None, acquisition_candidate_id=candidate.acquisition_id,
            identity=candidate.source_identity,
        )
    return None


# ---------------------------------------------------------------------------------------------
# Acquisition request -> the open requirement it is about
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AcquisitionBinding:
    requirement_id: str
    rule: str
    """content_match | pending_step | single_open_requirement"""
    pending_requirement: bool


def _requirement_tokens(requirement: EvidenceRequirement) -> set[str]:
    return set(requirement.semantic_tokens or semantic_tokens(requirement.description, (requirement.capability or "").replace("_", " ")))


def bind_acquisition_request(
    progression: TroubleshootingProgression, fault_id: str, text: str, pending: Optional[TroubleshootingStep]
) -> Optional[AcquisitionBinding]:
    """The OPEN acquirable requirement of this fault that an acquisition request ("what command do
    I use?") is about: the one whose content the message names; else the pending step's own
    requirement; else the fault's only open acquirable requirement. Never "the most recent one"
    by default, never another fault's; None when it cannot be told apart."""
    open_requirements = [r for r in progression.open_requirements(fault_id) if r.kind in _ACQUIRABLE]
    if not open_requirements:
        return None
    pending_id = pending.evidence_requirement_id if pending is not None else None
    if pending_id not in {r.requirement_id for r in open_requirements}:
        pending_id = None
    tokens = set(semantic_tokens(text))
    scored = [(len(tokens & _requirement_tokens(r)), r) for r in open_requirements]
    best = max((n for n, _ in scored), default=0)
    if best > 0:
        top = [r for n, r in scored if n == best]
        if len(top) == 1:
            return AcquisitionBinding(top[0].requirement_id, "content_match", top[0].requirement_id == pending_id)
        if pending_id in {r.requirement_id for r in top}:
            return AcquisitionBinding(pending_id, "content_match", True)
        return None
    if pending_id:
        return AcquisitionBinding(pending_id, "pending_step", True)
    if len(open_requirements) == 1:
        return AcquisitionBinding(open_requirements[0].requirement_id, "single_open_requirement", False)
    return None


def is_acquisition_request(text: str) -> bool:
    """The command follow-up wording that asks for an acquisition METHOD ("what command?", "how can I
    check that?"), independent of whether a step is pending. A bare request to go on ("what next?")
    is a continuation, not an acquisition request."""
    from backend.agents.technical_authority_engineer.progression_controller import _MECHANISM_FOLLOW_UP

    return bool(_MECHANISM_FOLLOW_UP.search(text or ""))


def pre_run_rule(
    known: Optional[KnownGovernedAcquisition],
    *,
    clarification_answered: bool,
    command_follow_up: bool,
    binding: Optional[AcquisitionBinding],
    continuation: Optional[ContinuationKind] = None,
    applicability_clarification_resolved: bool = False,
) -> Optional[ContinuationRule]:
    """The continuation the server owns BEFORE the specialist runs (None: none applies). A generic
    continuation applies only with a known governed acquisition that no open clarification blocks
    (`known`); without one there is nothing valid to continue and nothing is forced.

    `applicability_clarification_resolved`: THIS turn's answer resolved the fault's OPEN applicability
    clarification. That dependency is server-owned on its own (`OpenQuestion`), so the investigation
    it blocked resumes whether or not a pending step exists and whatever outcome label the specialist
    gave on the earlier turn. Resuming grants nothing: discovery, selection, ProcedureAction issuance
    and Command Authority all happen afresh in this run."""
    if applicability_clarification_resolved and known is None:
        return ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED
    if known is None or known.clarification_open:
        return None
    if clarification_answered and known.blocked:
        return ContinuationRule.APPLICABILITY_CLARIFICATION_RESOLVED
    if continuation is ContinuationKind.GENERIC_CONTINUATION:
        return ContinuationRule.GENERIC_CONTINUATION
    if continuation in (ContinuationKind.RESULT_PROVIDED, ContinuationKind.NEW_OBJECTIVE, ContinuationKind.NONE):
        return None
    if command_follow_up and (binding is None or known.requirement_id is None or binding.requirement_id == known.requirement_id):
        return ContinuationRule.ACQUISITION_REQUEST
    return None


# ---------------------------------------------------------------------------------------------
# Model omission: the specialist restates the pending need without its method
# ---------------------------------------------------------------------------------------------


def _restated_requirements(payload: Mapping[str, Any], fault_id: str) -> list[EvidenceRequirement]:
    out: list[EvidenceRequirement] = []
    step = payload.get("diagnostic_step")
    if payload.get("outcome") == "recommended" and isinstance(step, Mapping):
        out.append(requirement_from_step(step, fault_id, attempted_command=False))
    for proposal in payload.get("required_evidence") or []:
        if isinstance(proposal, Mapping):
            requirement = requirement_from_proposal(proposal, fault_id)
            if requirement is not None:
                out.append(requirement)
    for item in payload.get("missing_information") or []:
        if not isinstance(item, str) or not item.strip():
            continue
        description = evidence_from_mechanism_item(item) if is_acquisition_mechanism_item(item) else item
        description = evidence_description(description or "")
        if description:
            requirement = requirement_from_proposal({"kind": "diagnostic_result", "description": description}, fault_id)
            if requirement is not None:
                out.append(requirement)
    return out


def model_restates_requirement(
    payload: Optional[Mapping[str, Any]], progression: TroubleshootingProgression, fault_id: str, requirement_id: Optional[str]
) -> bool:
    """True when the specialist's output names the pending requirement (by validated reference or
    server-matched content) -- i.e. it describes the same evidence need, with or without a method."""
    if not isinstance(payload, Mapping) or not requirement_id:
        return False
    for candidate in _restated_requirements(payload, fault_id):
        if proposed_ref(candidate) == requirement_id:
            return True
        matched, _ = match_open_requirement(progression, candidate)
        if matched is not None and matched.requirement_id == requirement_id:
            return True
    return False


def model_chose(payload: Optional[Mapping[str, Any]], known: KnownGovernedAcquisition) -> bool:
    step = payload.get("diagnostic_step") if isinstance(payload, Mapping) else None
    return isinstance(step, Mapping) and str(step.get("procedure_action_id") or "").strip() == known.procedure_action_id


def model_offers_other_method(payload: Optional[Mapping[str, Any]], known: KnownGovernedAcquisition) -> bool:
    """The specialist proposed a method of its own (a command, or another procedure action): that is
    a different proposal for the sequencing gates to judge, not an omission."""
    step = payload.get("diagnostic_step") if isinstance(payload, Mapping) else None
    if not isinstance(step, Mapping):
        return False
    action_id = str(step.get("procedure_action_id") or "").strip()
    return bool(str(step.get("command") or "").strip()) or (bool(action_id) and action_id != known.procedure_action_id)


def effective_rule(
    rule: Optional[ContinuationRule],
    payload: Optional[Mapping[str, Any]],
    progression: TroubleshootingProgression,
    fault_id: str,
    known: KnownGovernedAcquisition,
    raw_payloads: Iterable[Mapping[str, Any]] = (),
) -> Optional[ContinuationRule]:
    """The pre-run continuation, else MODEL_OMISSION when the specialist restated the pending need
    WITHOUT proposing any method (no command, no other procedure action) -- judged on its RAW
    payloads too, since an integrity callback may already have stripped a refused command."""
    if isinstance(payload, Mapping) and payload.get("outcome") not in RECONCILABLE_OUTCOMES:
        return None  # escalation / error: their own server gates decide, never pre-empted here
    if rule is not None:
        return rule
    if any(model_offers_other_method(p, known) for p in [payload, *raw_payloads]):
        return None
    if model_restates_requirement(payload, progression, fault_id, known.requirement_id):
        return ContinuationRule.MODEL_OMISSION
    return None


# ---------------------------------------------------------------------------------------------
# Reconstruction from THIS run's selected evidence (the only path to authority)
# ---------------------------------------------------------------------------------------------


def reconstruct_known_action(known: KnownGovernedAcquisition, selected_evidence: Iterable[Any]) -> tuple[Optional[Any], str]:
    """The known action re-derived from THIS run's SELECTED governed evidence, or (None, why not).
    The catalog admits only approved, applicability-MATCH procedural sources; the action must be the
    same deterministic id (a digest of the exact source tuple and template), from the same structured
    source identity, with the same template, and a diagnostic read (evidence acquisition never
    re-presents a state change on its own). Sources are located by exact identity tuple only."""
    from backend.agents.technical_authority_engineer.procedure_actions import ProcedureActionType, build_procedure_action_catalog

    evidence = list(selected_evidence or [])
    actions, unavailable = build_procedure_action_catalog(evidence)
    action = next((a for a in actions if a.action_id == known.procedure_action_id), None)
    if action is None:
        if known.identity is None:
            return None, "source_identity_unstructured"  # a legacy record: its source is never parsed from a string
        governed = [
            (e.get("metadata") if isinstance(e, Mapping) else getattr(e, "metadata", None)) or {}
            for e in evidence
            if (e.get("source_type") if isinstance(e, Mapping) else getattr(e, "source_type", None)) == "governed_knowledge"
            and identity_of(e) == known.identity
        ]
        if not governed:
            return None, "source_not_selected_in_this_run"
        meta = governed[0]
        applicability = str(meta.get("applicability_outcome") or "").lower()
        if applicability and applicability != "match":
            return None, f"source_applicability_{applicability}_in_this_run"
        from backend.agents.technical_authority_engineer.procedure_actions import _source_authority

        if _source_authority(meta) is not None:
            return None, "source_not_authoritative_in_this_run"
        return None, "action_not_derivable_from_current_source"
    if known.identity is not None and identity_of(action.source) != known.identity:
        return None, "source_identity_mismatch"
    if known.normalized_template and _norm(action.command_template) != known.normalized_template:
        return None, "template_identity_mismatch"
    if action.action_type is not ProcedureActionType.DIAGNOSTIC_READ:
        return None, "not_a_diagnostic_read"
    return action, "reconstructed_from_current_selected_evidence"


def is_definitive_rejection(reason: Optional[str]) -> bool:
    """THIS run's selected governed evidence positively no longer supports the known action (as
    opposed to: its source was not selected, its applicability is still unresolved, or a legacy
    record carries no structured source identity to locate it by)."""
    if not reason or reason in ("source_not_selected_in_this_run", "not_a_diagnostic_read", "source_identity_unstructured"):
        return False  # not selected (the specialist's decision) / a valid action the server never auto-continues
    return reason not in ("source_applicability_unknown_in_this_run", "source_applicability_partial_match_in_this_run")


RECONCILED_INTERPRETATION = (
    "The pending diagnostic step continues with its governed procedure action, rediscovered in this run's selected "
    "governed evidence; only a command Command Authority authorized in this run is presented."
)


def reconciled_proposal(
    known: KnownGovernedAcquisition,
    step: TroubleshootingStep,
    requirement: Optional[EvidenceRequirement],
    model_result: Mapping[str, Any],
) -> dict[str, Any]:
    """The pending step proposed with its known governed action. Server-built from server state:
    no model wording, no model command, no model citation; only its (evidence-bound) hypothesis
    proposals are kept."""
    diagnostic_step: dict[str, Any] = {
        "action": step.objective,
        "reason": "This is the current diagnostic step; its result is needed before the investigation can progress.",
        "expected_evidence": step.expected_evidence or (requirement.description if requirement else ""),
        "procedure_action_id": known.procedure_action_id,
        "command": None,
        "command_source": None,
        "restrictions": [],
        "parameter_values": [],
        "acquisition": "governed_action",
    }
    if requirement is not None:
        diagnostic_step["evidence_requirement"] = {
            "kind": requirement.kind.value, "description": requirement.description,
            "capability": requirement.capability, "requirement_ref": requirement.requirement_id,
        }
    return {
        "outcome": "recommended",
        "technical_interpretation": RECONCILED_INTERPRETATION,
        "verified_evidence_citations": [known.source_id] if known.source_id else [],
        "hypothesis_updates": list(model_result.get("hypothesis_updates") or []),
        "missing_information": [],
        "required_evidence": [],
        "diagnostic_step": diagnostic_step,
    }
