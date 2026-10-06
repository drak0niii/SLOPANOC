"""Troubleshooting Progression Controller: the TAE proposes progression, the server owns it.

    ONE VALIDATED STEP -> OBSERVE RESULT -> VERIFY / INTERPRET -> REASSESS -> NEXT STEP
    NO RESULT = NO PROGRESSION  (unless the operator says the step cannot be performed:
                                 the step is recorded SKIPPED with the reason, then REASSESS)

Per operator turn (before the specialist runs):
    RESULT            -> bind the operator's verbatim output to the exact pending step
                         (OBSERVED -> VERIFIED -> COMPLETED, or FAILED), phase REASSESS
    BLOCKED           -> pending step SKIPPED with the operator's reason, phase REASSESS
    COMMAND_FOLLOW_UP -> ("what's next cmd?", "give me the command", ...) resolve the SAME pending
                         step: the turn objective becomes that step, not a fresh investigation
    OTHER             -> no transition; a pending step keeps waiting for its result

RESPONSIBILITY BOUNDARY (docs/TROUBLESHOOTING_STRATEGY.md "AI vs Server Responsibility Boundary"):
    AI optimizes: the agent decides which diagnostic is most useful, which branch / hypothesis to
    pursue, whether remediation or escalation is technically appropriate.
    Server constrains only when a hard invariant would be violated: every rejection below is
    TRUST, CONSISTENCY or SAFETY -- never "would I have chosen this step?". Document order and
    recommended sequences are guidance for the agent, never enforced.

Per specialist proposal (after validation, before any operational control or recording):
    candidate -> canonical identity (step_identity.py) -> compare with pending / completed /
    attempted steps of THIS fault thread -> decision
    several actions in one step  -> rejected (SAFETY: one operational action per step)
    pending step, no result      -> accepted only as the pending step's own resolution (same
                                    identity, same governed action with its target still open or
                                    re-stated by the operator, or the identical stated objective
                                    when the step has no action yet); anything else is rejected and
                                    the pending step re-presented (CONSISTENCY: no accidental loss).
                                    Its validated result, or the operator explicitly cancelling /
                                    replacing it, frees the agent to choose ANY next step
    same step completed          -> rejected (REJECTED_REPEATED_STEP)          \
    same observation known       -> rejected (REJECTED_KNOWN_RESULT)            } CONSISTENCY: known
    same step failed / skipped   -> rejected (REJECTED_FAILED_UNCHANGED)       /  results, no loops
    explicit governed MANDATORY prerequisite incomplete -> rejected (REJECTED_MANDATORY_PREREQUISITE)
    no pending step, no match    -> ONE new step (PROPOSED -> VALIDATED -> PRESENTED) -- any
                                    technically chosen diagnostic, in any order

A repeat is permitted ONLY for an explicit, recorded reason (RECHECK_PERMITTED, `recheck_of` +
`recheck_reason` on the new step):
    operator_requested_recheck        the operator's re-check request resolves to that ONE step
    result_stale_after_reopen         the operator reopened the fault
    governed_post_action_verification a declared POST_ACTION_VERIFICATION relationship after a
                                      completed state change
    result_stale_after_state_change   a state-changing step completed after the earlier result
    context_changed                   the governed source version or applicability outcome changed

Diagnosis -> remediation -> verified resolution (resolution_gates.py): a state-changing candidate
must pass the server-evaluated remediation gate (REJECTED_PREMATURE_REMEDIATION otherwise); its
result means ACTION_EXECUTED -> POST_ACTION_VERIFICATION_REQUIRED, never resolution; later checks
are VERIFICATION steps evaluated against grounded criteria -> RESOLVED / REASSESS / ESCALATION_REQUIRED.

Sequencing only: the controller never selects evidence, resolves parameters, authorizes, applies
policy, confirms targets or approves -- those boundaries are untouched and still run for every step.
Deterministic, no model call, no vendor vocabulary.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, MutableMapping, Optional

from backend.agents.technical_authority_engineer import resolution_gates as gates
from backend.agents.technical_authority_engineer.command_syntax import single_invocation_violation
from backend.agents.technical_authority_engineer.result_validation import (
    UNVALIDATED_CANDIDATES,
    ResultValidation,
    ResultValidationStatus,
    operator_intent,
    same_observation,
    validate_adapter_observation,
    validate_operator_observation,
)
from backend.agents.technical_authority_engineer.step_identity import (
    STATE_CHANGING_OPERATION_TYPES,
    candidate_identity,
    identity_summary,
    step_identity,
    target_values,
)
from backend.agents.technical_authority_engineer.turn_request import _content_tokens, _token_matches, _tokens, is_continuation_only
from backend.cases.evidence_identity import EvidenceIdentity, identity_of
from backend.cases.target_facts import case_target_facts
from backend.cases.troubleshooting_progression import (
    CriterionStatus,
    FaultProgression,
    HypothesisState,
    ProgressionPhase,
    RemediationRecord,
    RemediationState,
    ResolutionState,
    ResultSource,
    StepIdentity,
    StepPurpose,
    StepStatus,
    PROJECTION_MAX_STEPS,
    fault_stage,
    TroubleshootingProgression,
    TroubleshootingStep,
    VerificationStatus,
    load_or_adapt_progression,
    project_troubleshooting_state,
    save_progression,
)
from backend.cases.progression_policy import TroubleshootingProgressionPolicy, get_progression_policy
from backend.cases.troubleshooting_progression import EscalationRecord
from backend.cases.troubleshooting_state import TroubleshootingState

PROGRESSION_CONTROL_KEY = "progression_control"
"""Server-owned tool-result key describing what the controller did this turn."""

PENDING_STATUSES = frozenset({StepStatus.PROPOSED, StepStatus.VALIDATED, StepStatus.PRESENTED, StepStatus.BLOCKED_BY_CLARIFICATION})
_MAX_RESULT_CHARS = 8000


def awaits_result(step: TroubleshootingStep) -> bool:
    """A step awaits its result only while it is pending AND no result is bound to it: a recorded
    result consumes the step for good (structural, whatever its status field says)."""
    return step.status in PENDING_STATUSES and step.result is None

_BLOCKED = re.compile(
    r"\b(?:can(?:no|')?t|cannot|unable to|not able to|no access|(?:do not|don't) have (?:access|permission)|"
    r"not (?:possible|permitted|allowed)|permission denied|(?:does not|doesn't|won't|will not) work|"
    r"not reachable|unreachable|(?:skip|skipping) (?:it|this|that|the (?:check|step)))\b",
    re.IGNORECASE,
)
_MECHANISM_FOLLOW_UP = re.compile(
    r"\b(?:cmd|cmds|command|commands|syntax)\b|"
    r"\bhow (?:do|can|should|would) (?:i|we) (?:check|run|do|verify|execute|get|obtain|retrieve|collect|see|list|read|acquire)\b|"
    r"\bwhat (?:do|should|can) (?:i|we) (?:run|type|execute|enter|use)\b|"
    r"\bgive me\b|\bshow me how\b",
    re.IGNORECASE,
)
"""A follow-up that asks for the pending step's acquisition METHOD (COMMAND_FOLLOW_UP)."""
_FOLLOW_UP = re.compile(_MECHANISM_FOLLOW_UP.pattern + r"|\bwhat(?:'s| is)? next\b|\bnext (?:step|check)\b", re.IGNORECASE)
"""Any follow-up on the pending step: a method request, or a request to go on."""

_COMMAND_SEPARATORS = re.compile(r";|&&|\|\||\||\n")
_RECHECK_REQUEST = re.compile(
    r"\b(?:re-?check|re-?run|re-?validate|re-?verify|re-?test|retry|repeat|double[- ]check)\b|"
    r"\b(?:check|run|verify|validate|test|do|try)\b[^.?!\n]{0,40}\b(?:again|once more)\b",
    re.IGNORECASE,
)
_HAS_RESULT = frozenset({StepStatus.OBSERVED, StepStatus.VERIFIED, StepStatus.COMPLETED})
_REOPEN = re.compile(
    r"\b(?:re-?open\w*|came back|comes back|recurr\w*|re-?appear\w*|re-?occurr?\w*|still (?:active|present|there|down|failing|faulty))\b",
    re.IGNORECASE,
)
_CLOSED = frozenset({ResolutionState.RESOLVED, ResolutionState.ESCALATED})
_CANCEL = re.compile(
    r"\b(?:cancel|forget|drop|abort|abandon|scrap|disregard|ignore)\s+(?:about\s+)?(?:that|this|the|it)"
    r"(?:\s+(?:check|step|command|cmd|restart|reset|reboot|action|test|one))?\b"
    r"|\b(?:don'?t|do\s+not|no\s+need\s+to|stop)\s+(?:run(?:ning)?|do(?:ing)?|execut\w*|perform\w*|check(?:ing)?)\s+(?:that|this|the|it)"
    r"(?:\s+(?:check|step|command|cmd|restart|reset|reboot|action|test|one))?\b"
    r"|\bnever\s*mind\b",
    re.IGNORECASE,
)
_NEW_OBJECTIVE = re.compile(
    r"\binstead\b|\blet'?s\s+\w+|\b(?:investigate|look\s+(?:at|into)|focus\s+on|switch\s+to|move\s+on\s+to|check|troubleshoot)\b",
    re.IGNORECASE,
)
_INSTEAD = re.compile(r"\binstead\b", re.IGNORECASE)
_RECHECK_NOISE = frozenset({
    "re", "recheck", "check", "checks", "rerun", "run", "revalidate", "validate", "reverify", "verify", "retest", "test",
    "retry", "repeat", "double", "again", "once", "more", "please", "can", "you", "could", "would", "we", "it", "that",
    "this", "them", "those", "these", "the", "do", "one", "time", "another", "go", "ahead", "and", "to", "for", "me",
})
_RECHECK_PRONOUN = re.compile(r"\b(?:it|that|this|them|those)\b", re.IGNORECASE)
_WORD = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")


def explicit_objective_change(text: str) -> Optional[str]:
    """"superseded" (the pending step is explicitly abandoned for a stated new objective),
    "cancelled" (explicitly abandoned, nothing new stated) or None. Questions never qualify."""
    stripped = (text or "").strip()
    if stripped.endswith("?"):
        return None
    match = _CANCEL.search(stripped)
    if match is None:
        # "investigate the sync fault instead": an explicit redirect even without a cancel verb.
        return "superseded" if _INSTEAD.search(stripped) and _NEW_OBJECTIVE.search(stripped.replace("instead", "")) else None
    rest = stripped[: match.start()] + " " + stripped[match.end():]
    return "superseded" if _NEW_OBJECTIVE.search(rest) else "cancelled"


def _words(text: Optional[str]) -> set[str]:
    return {w.casefold() for w in _WORD.findall(text or "") if len(w) >= 2}


def _matches(word: str, descriptor: set[str]) -> bool:
    return any(word == d or (min(len(word), len(d)) >= 4 and (word.startswith(d) or d.startswith(word))) for d in descriptor)


def resolve_recheck_target(text: str, steps: list[TroubleshootingStep]) -> dict[str, Any]:
    """Which ONE earlier step does the operator's re-check request name? Deterministic:
    words of the request (re-check verbs / pronouns removed) are matched against each earlier
    step's objective, command, governed template, expected evidence and target; the unique best
    match wins. A bare pronoun ("run it again") means the most recent step. Anything else --
    generic ("recheck"), no match, or a tie -- is UNRESOLVED: no repeat permission is granted."""
    attempted = [s for s in sorted(steps, key=lambda s: s.sequence) if s.status in _HAS_RESULT | {StepStatus.FAILED, StepStatus.SKIPPED}]
    latest_by_identity: dict[str, TroubleshootingStep] = {}
    for step in attempted:
        latest_by_identity[step_identity(step).key] = step
    candidates = [s.objective for s in latest_by_identity.values()]
    if not attempted:
        return {"status": "no_prior_step", "target": None, "candidates": []}
    words = {w for w in _words(text) if w not in _RECHECK_NOISE}
    if not words:
        if _RECHECK_PRONOUN.search(text or ""):
            return {"status": "resolved", "target": attempted[-1], "rule": "pronoun refers to the most recent step", "candidates": candidates}
        return {"status": "unresolved", "target": None, "rule": "the request names no check", "candidates": candidates}
    scored = []
    for step in latest_by_identity.values():
        identity = step_identity(step)
        descriptor = _words(" ".join(filter(None, [step.objective, step.command, identity.template, step.expected_evidence, identity.target])))
        scored.append((sum(1 for w in words if _matches(w, descriptor)), step))
    best = max(score for score, _ in scored)
    top = [step for score, step in scored if score == best]
    if best == 0:
        return {"status": "unresolved", "target": None, "rule": "no earlier check matches the request", "candidates": candidates}
    if len(top) > 1:
        return {"status": "ambiguous", "target": None, "rule": "several earlier checks match equally", "candidates": [s.objective for s in top]}
    return {"status": "resolved", "target": top[0], "rule": "unique match on the request's words", "candidates": candidates}


class TurnKind(str, Enum):
    NO_PENDING = "no_pending"
    RESULT = "result"
    """A VALIDATED result (or an explicit command error) for the pending step."""
    UNVALIDATED_RESULT = "unvalidated_result"
    OBJECTIVE_CHANGE = "objective_change"
    """The operator explicitly cancelled / replaced the pending step (never a result)."""
    """Looked like output but could not be attributed to the pending step: recorded, not bound."""
    BLOCKED = "blocked"
    COMMAND_FOLLOW_UP = "command_follow_up"
    KNOWN_RESULT = "known_result"
    """The message re-submits output already recorded as an earlier step's trusted result (and is not
    a result of the pending step): nothing is bound, no candidate is recorded, no step reopens."""
    OTHER = "other"


class ProposalDecision(str, Enum):
    NONE = "none"
    NEW_STEP = "new_step"
    PENDING_STEP_RESOLVED = "pending_step_resolved"
    REJECTED_PENDING_AWAITS_RESULT = "rejected_pending_awaits_result"
    REJECTED_MULTIPLE_ACTIONS = "rejected_multiple_actions"
    REJECTED_REPEATED_STEP = "rejected_repeated_step"
    REJECTED_KNOWN_RESULT = "rejected_known_result"
    REJECTED_FAILED_UNCHANGED = "rejected_failed_unchanged"
    REJECTED_MANDATORY_PREREQUISITE = "rejected_mandatory_prerequisite"
    RECHECK_PERMITTED = "recheck_permitted"
    REJECTED_PREMATURE_REMEDIATION = "rejected_premature_remediation"
    REJECTED_FAULT_CLOSED = "rejected_fault_closed"


ACCEPTED_DECISIONS = frozenset({ProposalDecision.NEW_STEP, ProposalDecision.RECHECK_PERMITTED, ProposalDecision.PENDING_STEP_RESOLVED})
"""Decisions that record a step; every other decision records nothing and plans no control."""


def is_multi_action_command(command: Optional[str]) -> bool:
    """Independent sequencing check (Command Authority enforces the same syntactic boundary)."""
    return bool(command) and (bool(_COMMAND_SEPARATORS.search(command)) or single_invocation_violation(command) is not None)


def _governed_sections(evidence: Any) -> dict[str, tuple[str, Optional[str]]]:
    """SELECTED governed sections of this turn: source_id -> (content, applicability outcome)."""
    sections: dict[str, tuple[str, Optional[str]]] = {}
    for ev in evidence or ():
        get = ev.get if isinstance(ev, Mapping) else (lambda k, _e=ev: getattr(_e, k, None))
        if get("source_type") != "governed_knowledge" or not get("source_id"):
            continue
        meta = get("metadata") or {}
        sections[str(get("source_id"))] = (str(get("content_snippet") or ""), meta.get("applicability_outcome"))
    return sections


def _governed_versions(evidence: Any) -> dict[str, str]:
    """SELECTED governed sections of this turn: source_id -> structured version label (never parsed)."""
    versions: dict[str, str] = {}
    for ev in evidence or ():
        get = ev.get if isinstance(ev, Mapping) else (lambda k, _e=ev: getattr(_e, k, None))
        identity = identity_of(ev)
        if get("source_type") == "governed_knowledge" and get("source_id") and identity is not None:
            versions[str(get("source_id"))] = identity.version_label
    return versions


def _target_identity(target: Optional[Mapping[str, Any]]) -> Optional[dict[str, str]]:
    """Scalar identity fields of a control-plane target (nested attributes are not step identity)."""
    if not isinstance(target, Mapping):
        return None
    identity = {str(k): str(v) for k, v in target.items() if isinstance(v, (str, int, float)) and not isinstance(v, bool) and v != ""}
    return identity or None


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def withdraw_rejected_command(result: dict[str, Any]) -> dict[str, Any]:
    """A rejected proposal's command is NOT presented: remove every operational-envelope field that
    would still show it as actionable (the rendered/authorized ProcedureAction view and sentences of
    the technical interpretation naming it). Audit is unaffected: Command Authority and action
    resolutions stay in the run's diagnostic trace."""
    from backend.agents.technical_authority_engineer.procedure_actions import PROCEDURE_ACTION_RESOLUTION_KEY

    step = result.get("diagnostic_step") if isinstance(result.get("diagnostic_step"), dict) else {}
    resolution = result.get(PROCEDURE_ACTION_RESOLUTION_KEY) if isinstance(result.get(PROCEDURE_ACTION_RESOLUTION_KEY), dict) else None
    withdrawn = {c for c in (step.get("command"), (resolution or {}).get("rendered_command"), (resolution or {}).get("command_template")) if isinstance(c, str) and c.strip()}
    if resolution is not None:
        result[PROCEDURE_ACTION_RESOLUTION_KEY] = {
            "action_id": resolution.get("action_id"),
            "status": resolution.get("status"),
            "source": resolution.get("source"),
            "parameters": resolution.get("parameters") or [],
            "rendered_command": None,
            "command_authority": None,
            "presented": False,
            "reason": "proposal not accepted by troubleshooting progression; no command is presented",
            # The state-change target / condition decisions stay auditable (identities only; no command).
            "target_validation": resolution.get("target_validation"),
            "condition_check": resolution.get("condition_check"),
            "instance_semantics": resolution.get("instance_semantics"),
        }
    interpretation = result.get("technical_interpretation")
    if withdrawn and isinstance(interpretation, str) and interpretation.strip():
        mentions = [re.compile(rf"(?<![\w-]){re.escape(' '.join(c.split()))}(?![\w-])", re.IGNORECASE) for c in withdrawn]
        kept = [s for s in _SENTENCE_SPLIT.split(interpretation.strip()) if not any(m.search(s) for m in mentions)]
        result["technical_interpretation"] = " ".join(kept).strip() or None
    return result


class ProgressionController:
    def __init__(
        self,
        state: MutableMapping[str, Any],
        thread: TroubleshootingState,
        *,
        session_id: Optional[str] = None,
        case_id: Optional[str] = None,
        activate: bool = True,
        progression: Optional[TroubleshootingProgression] = None,
        policy: Optional[TroubleshootingProgressionPolicy] = None,
    ) -> None:
        """`progression` is the AUTHORITATIVE progression (loaded by ProgressionRepository: Case or
        session scope). Without it, the session-state document is used (session scope). `thread`
        identifies the fault; it is a read projection that this controller re-derives, never reads."""
        self.state = state
        self._activate = activate
        self.policy = policy or get_progression_policy()
        self.thread = thread
        self.progression: TroubleshootingProgression = (
            progression
            if progression is not None
            else load_or_adapt_progression(state, session_id=session_id, case_id=case_id) or TroubleshootingProgression(case_id=case_id)
        )
        if session_id and session_id not in self.progression.session_ids:
            self.progression.session_ids.append(session_id)
        self.turn_kind: TurnKind = TurnKind.NO_PENDING
        self.operator_text: str = ""
        self.intent_text: str = ""
        """The operator's own words of this turn (pasted / bound output excluded; see classify_turn)."""
        self.events: list[dict[str, Any]] = []
        self._candidate: Optional[StepIdentity] = None
        self._recheck: Optional[dict[str, str]] = None
        self._duplicate: Optional[dict[str, Any]] = None
        self._gate: Optional[dict[str, dict[str, Any]]] = None
        self._sections: dict[str, tuple[str, Optional[str]]] = {}
        self._actions: dict[str, Any] = {}
        self.bound_result_ids: list[str] = []
        """Trusted observations bound THIS turn (the only evidence a hypothesis update may cite)."""
        self._bound_step_id: Optional[str] = None
        self.consumed_step_ids: list[str] = []
        """Steps whose result an operator message / controlled execution bound THIS turn. Such a step
        can never be pending again in this turn (`pending_step`) nor rendered as awaiting its result."""
        self.result_binding: Optional[dict[str, Any]] = None
        """This turn's operator-result binding decision (identifiers and statuses only)."""
        self.known_result_step_id: Optional[str] = None
        """The earlier step whose already-recorded result this message re-submitted (KNOWN_RESULT)."""
        self.presented_pending_step_id: Optional[str] = None
        """The pending step the specialist's final proposal resolves / re-presents (server decision)."""
        self.validation: Optional[ResultValidation] = None
        self.objective_change: Optional[str] = None
        self.mechanism_requested: bool = False
        """The latest message names the pending step's acquisition method (set by classify_turn)."""
        self._objective_change_event: Optional[dict[str, Any]] = None
        self._recheck_request: Optional[dict[str, Any]] = None
        self._escalation: Optional[dict[str, Any]] = None
        self._blocked: Any = None
        """This turn's server-derived applicability-blocked governed action (no authority)."""
        self._blocked_continuation = False
        self._resumed_clarification_step_id: Optional[str] = None
        """The step the fault's RESOLVED applicability clarification originated from."""
        self._sync_from_thread()

    def resume_from_clarification(self, step_id: Optional[str]) -> None:
        """The fault's applicability clarification that originated from `step_id` is RESOLVED (server
        state; see `_against_pending`)."""
        self._resumed_clarification_step_id = step_id

    # ---- state alignment with the legacy runtime projection ------------------------------------
    @property
    def fault_id(self) -> str:
        return self.thread.fault_id

    def _sync_from_thread(self) -> None:
        """Register the fault this turn works on (thread resolution decides WHICH fault; the
        progression owns everything about it). Nothing else is read from the thread projection:
        steps, results, control links and statuses originate in the progression only."""
        progression, ts = self.progression, self.thread
        if ts.fault_id not in progression.faults:
            progression.add_fault(
                FaultProgression(fault_id=ts.fault_id, symptom_summary=ts.symptom_summary, subject_component=ts.subject_component, node_id=ts.node_id),
                activate=self._activate,
            )
        elif self._activate and progression.active_fault_id != ts.fault_id:
            progression.activate_fault(ts.fault_id)
        self.refresh_projection()

    def refresh_projection(self) -> None:
        """Re-derive this fault's TroubleshootingState (in place, so every holder of the object sees
        it) from the authoritative progression. The projection is never an input."""
        projected = project_troubleshooting_state(self.progression, self.fault_id, PROJECTION_MAX_STEPS)
        if projected is None:
            return
        for name in type(projected).model_fields:
            setattr(self.thread, name, getattr(projected, name))

    def record_controlled_execution(
        self,
        check_id: str,
        execution_id: str,
        output: Optional[str],
        succeeded: bool,
        control_id: Optional[str] = None,
        control_stage: Optional[str] = None,
    ) -> bool:
        """Progression-first binding of a controlled execution to the exact step it was run for.
        Returns True when the output became the step's trusted result."""
        step = next((s for s in self.progression.steps_for(self.fault_id) if s.check_id == check_id), None)
        if step is None:
            return False
        step.control_id = control_id or step.control_id
        step.control_stage = control_stage or step.control_stage
        bound = False
        if awaits_result(step) and step.step_id not in self.consumed_step_ids:
            validation = (
                validate_adapter_observation(step, execution_id, output, self.state)
                if succeeded and output
                else ResultValidation(ResultValidationStatus.AMBIGUOUS, ["the controlled execution did not produce an observation"])
            )
            if validation.bindable:
                self._complete_with_result(step, output or "", ResultSource.EXECUTION_ADAPTER, execution_id, validation)
                bound = True
            elif not any(c.execution_id == execution_id for c in step.candidate_observations):
                self.progression.record_candidate_observation(step.step_id, output or "", ResultSource.EXECUTION_ADAPTER, validation.as_dict(), execution_id)
                self.events.append({"event": "adapter_output_not_bound", "step_id": step.step_id, "reasons": validation.reasons})
        self.refresh_projection()
        return bound

    # ---- pending step ------------------------------------------------------------------------------
    def pending_step(self) -> Optional[TroubleshootingStep]:
        """The step awaiting its result, read from the CURRENT authoritative progression on every call
        (never a snapshot). A step whose result was bound -- this turn or earlier -- is never pending:
        a new execution of the same check is a new step."""
        pending = [
            s for s in self.progression.steps_for(self.fault_id)
            if awaits_result(s) and s.step_id not in self.consumed_step_ids
        ]
        return pending[-1] if pending else None

    def result_consumption(self) -> dict[str, Any]:
        """PendingResultConsumption of this turn (server-owned; identifiers and statuses only):
        whether the operator provided a result, which exact step it was bound to, and that the step was
        consumed (recorded and no longer pending)."""
        binding = dict(self.result_binding or {})
        bound_step_id = self._bound_step_id or (self.consumed_step_ids[-1] if self.consumed_step_ids else None)
        bound = self.progression.step(bound_step_id) if bound_step_id else None
        pending = self.pending_step()
        return {
            "result_provided": self.turn_kind in (TurnKind.RESULT, TurnKind.UNVALIDATED_RESULT, TurnKind.KNOWN_RESULT),
            "turn_kind": self.turn_kind.value,
            "binding": binding.get("binding", "none"),
            "bound_step_id": bound_step_id,
            "result_bound": bound is not None,
            "result_recorded": bound is not None and bound.result is not None,
            "old_step_consumed": bound is not None and bound.result is not None and (pending is None or pending.step_id != bound.step_id),
            "consumed_step_ids": list(self.consumed_step_ids),
            "known_result_step_id": self.known_result_step_id,
            # As of the binding (before the specialist proposed anything); the current pending otherwise.
            "pending_after_binding": binding["pending_after_binding"] if "pending_after_binding" in binding
            else pending.step_id if pending is not None else None,
        }

    def pending_context(self) -> Optional[dict[str, Any]]:
        step = self.pending_step()
        if step is None:
            return None
        from backend.agents.technical_authority_engineer.acquisition_continuity import known_governed_acquisition

        known = known_governed_acquisition(self.progression, self.fault_id, step)
        clarification_answered = known is not None and known.blocked and not known.clarification_open
        return {
            "step_id": step.step_id,
            "objective": step.objective,
            "procedure_action_id": step.procedure_action_id,
            "command": step.command,
            # Exact structured selection keys (knowledge_select_evidence input); a canonical source-id
            # string is never handed over to be split back into its parts.
            "selected_evidence": [identity.selection_key() for identity in step.selected_evidence],
            "expected_evidence": step.expected_evidence,
            "turn_kind": self.turn_kind.value,
            "last_candidate": step.candidate_observations[-1].validation if self.turn_kind is TurnKind.UNVALIDATED_RESULT and step.candidate_observations else None,
            "required_output": (
                f"The output of `{step.command}` for this step" if step.command
                else f"The output of this step's governed procedure action: {step.objective}" if known is not None
                else f"The observation for: {step.objective}"
            ),
            "blocked_by_clarification": step.status is StepStatus.BLOCKED_BY_CLARIFICATION,
            # Server-known governed acquisition of this step: identity only, never authority.
            "governed_acquisition": known.model_view() if known is not None else None,
            "rule": (
                "This step is still awaiting its result. Resolve ONLY this step (re-select its governed evidence with the "
                "exact selection keys in selected_evidence, copied field by field, and choose the procedure action that "
                "performs it). Its validated result, or the operator explicitly "
                "cancelling / replacing it, frees you to choose ANY technically appropriate next step."
                + (
                    " Its applicability clarification has been answered: re-select its governed source in this run "
                    "(governed_acquisition.selection_key) and choose its procedure action "
                    "(governed_acquisition.procedure_action_id) when it applies."
                    if clarification_answered
                    else " Its governed command is blocked until the procedure's applicability is confirmed; once it applies, "
                    "choose the procedure action that performs this same check."
                    if step.status is StepStatus.BLOCKED_BY_CLARIFICATION
                    else ""
                )
            ),
        }

    # ---- operator turn -----------------------------------------------------------------------------
    def classify_turn(self, operator_text: str, clarification_answer: bool = False) -> TurnKind:
        """`clarification_answer`: the server bound this message to the fault's pending clarification
        (requested context such as applicability values) -- it is never an observation for the
        pending step, so no result is bound and the step keeps waiting."""
        pending = self.pending_step()
        text = (operator_text or "").strip()
        self.operator_text = text
        # The operator's OWN words (intent), never pasted output (observation): every intent decision
        # below -- cancel / redirect, re-check, reopen, "cannot perform", a stated target -- reads
        # only these. Refined once the server knows the message carries output.
        commands = self._known_commands()
        self.intent_text = operator_intent(text, commands)
        self.objective_change = explicit_objective_change(self.intent_text)
        self._recheck_request = None
        self.mechanism_requested = bool(_MECHANISM_FOLLOW_UP.search(text)) or bool(
            pending is not None and pending.command and pending.command.lower() in text.lower()
        )
        self.known_result_step_id = None
        if pending is None:
            known_step = None if clarification_answer else self._known_result_step(text)
            if known_step is not None:
                self.known_result_step_id = known_step.step_id
                self.intent_text = operator_intent(text, commands, carries_output=True)
                self.objective_change = explicit_objective_change(self.intent_text)
                self.turn_kind = TurnKind.KNOWN_RESULT
                return self.turn_kind
            self.turn_kind = TurnKind.NO_PENDING
            return self.turn_kind
        if clarification_answer and not self.objective_change:
            self.validation = None
            self.turn_kind = TurnKind.OTHER
            return self.turn_kind
        known = [c for st in self.progression.steps if st.step_id != pending.step_id for c in (st.command, st.identity.result_key if st.identity else None) if c]
        self.validation = validate_operator_observation(pending, text, known)
        status = self.validation.status
        known_step = None if self.validation.bindable else self._known_result_step(text)
        if self.validation.bindable or status in UNVALIDATED_CANDIDATES or known_step is not None:
            self.intent_text = operator_intent(text, commands, carries_output=True)
            self.objective_change = explicit_objective_change(self.intent_text)
        if self.validation.bindable:
            self.turn_kind = TurnKind.RESULT
        elif known_step is not None and not self.objective_change:
            # The same output re-submitted after it was consumed: never a candidate for the pending
            # step, never a second result, never a reopened step.
            self.known_result_step_id = known_step.step_id
            self.turn_kind = TurnKind.KNOWN_RESULT
        elif self.objective_change:
            self.turn_kind = TurnKind.OBJECTIVE_CHANGE
        elif _BLOCKED.search(self.intent_text):
            # Only the operator's own words can say the step cannot be performed -- never text inside
            # pasted output ("Cell is unable to provide service").
            self.turn_kind = TurnKind.BLOCKED
        elif status in UNVALIDATED_CANDIDATES:
            self.turn_kind = TurnKind.UNVALIDATED_RESULT
        elif _FOLLOW_UP.search(text) or is_continuation_only(text) or self.mechanism_requested:
            self.turn_kind = TurnKind.COMMAND_FOLLOW_UP
        else:
            self.turn_kind = TurnKind.OTHER
        return self.turn_kind

    def _known_commands(self) -> list[str]:
        """Commands / governed templates of every recorded step (any fault): what an echo can name."""
        return [
            c for step in self.progression.steps
            for c in (step.command, step.identity.result_key if step.identity else None, step.identity.template if step.identity else None) if c
        ]

    def _known_result_step(self, text: str) -> Optional[TroubleshootingStep]:
        """The latest step of this fault whose recorded trusted result is the same observation as `text`."""
        for step in sorted(self.progression.steps_for(self.fault_id), key=lambda s: s.sequence, reverse=True):
            if step.result is not None and same_observation(text, step.result.text):
                return step
        return None

    def _complete_with_result(
        self, step: TroubleshootingStep, text: str, source: ResultSource, execution_id: Optional[str], validation: ResultValidation
    ) -> None:
        """RESULT_RECEIVED -> RESULT_VALIDATED -> trusted result -> COMPLETED (or FAILED). Only a
        validated candidate reaches this point."""
        if not validation.bindable:
            raise ValueError("only a validated observation can complete a step")
        progression = self.progression
        progression.set_phase(self.fault_id, ProgressionPhase.RESULT_RECEIVED)
        progression.set_phase(self.fault_id, ProgressionPhase.RESULT_VALIDATED, reason="; ".join(validation.reasons))
        progression.record_step_result(
            step.step_id, text[:_MAX_RESULT_CHARS], source, execution_id=execution_id, status=StepStatus.OBSERVED, validation=validation.as_dict()
        )
        failed = validation.status is ResultValidationStatus.COMMAND_FAILED
        requirement = self.progression.requirement(step.evidence_requirement_id)
        if not failed and requirement is not None and requirement.status.value == "unsatisfied":
            from backend.cases.evidence_model import EvidenceSourceRef, EvidenceSourceType, SourceAuthority

            origin = {
                ResultSource.EXECUTION_ADAPTER: EvidenceSourceType.CONTROLLED_EXECUTION,
                ResultSource.OPERATOR_MESSAGE: EvidenceSourceType.OPERATOR,
            }.get(source, EvidenceSourceType.OPERATOR)
            self.progression.satisfy_requirement(requirement.requirement_id, [EvidenceSourceRef(
                source_type=origin, source_id=step.result.result_id if step.result else step.step_id,
                authority=SourceAuthority.OBSERVED_EVIDENCE, provenance={"step_id": step.step_id, "execution_id": execution_id},
            )])
        if failed:
            step.verification_status = VerificationStatus.INCONCLUSIVE
            progression.set_step_status(step.step_id, StepStatus.FAILED, reason="the step's command did not produce a valid result")
        else:
            step.verification_status = VerificationStatus.VERIFIED
            progression.set_step_status(step.step_id, StepStatus.VERIFIED, reason=f"validated result bound to this exact step ({source.value})")
            progression.set_step_status(step.step_id, StepStatus.COMPLETED)
            self.bound_result_ids.append(step.result.result_id)
            self._bound_step_id = step.step_id
        # Consumed: the bound step leaves pending-result eligibility for the rest of this turn (and,
        # holding a result, for good) BEFORE anything reasons about what comes next.
        self.consumed_step_ids.append(step.step_id)
        self.events.append({"event": "result_bound", "step_id": step.step_id, "status": step.status.value, "source": source.value})
        if step.purpose is StepPurpose.REMEDIATION:
            self._after_remediation(step, failed)
        elif step.purpose is StepPurpose.VERIFICATION:
            self._after_verification()
        else:
            progression.set_phase(self.fault_id, ProgressionPhase.REASSESS)

    # ---- remediation / verification ------------------------------------------------------------------
    def _fault(self) -> FaultProgression:
        return self.progression.faults[self.fault_id]

    def verification_required(self) -> bool:
        record = self._fault().active_remediation
        return record is not None and record.state is RemediationState.VERIFICATION_REQUIRED

    def _after_remediation(self, step: TroubleshootingStep, failed: bool) -> None:
        """COMMAND SUCCEEDED != INCIDENT RESOLVED: the action's own output never verifies it."""
        progression, record = self.progression, self.progression.remediation_for_step(step.step_id)
        if record is None:
            progression.set_phase(self.fault_id, ProgressionPhase.REASSESS)
            return
        record.criteria_frozen = True
        if failed:
            record.state, record.outcome_reason = RemediationState.ACTION_FAILED, "the remediation command did not produce a valid result"
            progression.set_phase(self.fault_id, ProgressionPhase.REASSESS, reason=record.outcome_reason)
            self.events.append({"event": "remediation_action_failed", "remediation_id": record.remediation_id})
            return
        record.state, record.executed_result_id = RemediationState.VERIFICATION_REQUIRED, step.result.result_id
        progression.set_phase(self.fault_id, ProgressionPhase.ACTION_EXECUTED, reason="remediation action executed; not a resolution")
        criteria = "; ".join(c.description for c in record.criteria)
        progression.set_phase(
            self.fault_id, ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED,
            reason=f"verify: {criteria}" if criteria else "no explicit verification criteria: resolution cannot be confirmed",
        )
        if not record.criteria:
            progression.add_open_question(self.fault_id, "No explicit verification criteria were established for this remediation; it cannot be confirmed as resolved.")
        self.events.append({"event": "action_executed_verification_required", "remediation_id": record.remediation_id, "criteria": len(record.criteria)})

    def _after_verification(self) -> None:
        progression, fault = self.progression, self._fault()
        record = fault.active_remediation
        if record is None or record.state is not RemediationState.VERIFICATION_REQUIRED:
            progression.set_phase(self.fault_id, ProgressionPhase.REASSESS)
            return
        gates.evaluate_criteria(record, gates.post_action_observations(progression, record))
        outcome = gates.verification_outcome(record)
        evidence = sorted({e for c in record.criteria for e in c.evidence_ids})
        hypothesis = next((h for h in progression.hypotheses if h.hypothesis_id == record.hypothesis_id), None)
        if outcome is CriterionStatus.MET:
            record.state, record.outcome_reason = RemediationState.VERIFIED, "all verification criteria met"
            progression.set_resolution(self.fault_id, ResolutionState.RESOLVED, reason=record.outcome_reason)
            progression.set_phase(self.fault_id, ProgressionPhase.RESOLVED, reason=record.outcome_reason)
            if hypothesis is not None and hypothesis.state is not HypothesisState.REJECTED:
                progression.transition_hypothesis(hypothesis.hypothesis_id, HypothesisState.CONFIRMED, evidence, reason="remediation verification criteria met")
        elif outcome is CriterionStatus.NOT_MET:
            record.state = RemediationState.VERIFICATION_FAILED
            record.outcome_reason = "; ".join(f"{c.kind.value}: {c.detail}" for c in record.criteria if c.status is CriterionStatus.NOT_MET)
            # Interpreting what the failed verification means for the hypothesis is the agent's call
            # (it may propose WEAKENED / REJECTED with this verification result as evidence).
            gate = gates.escalation_gate(progression, self.fault_id, self.policy)
            if gate["passed"] and gate["rule"] == "failed_verifications_reached":
                self._escalate(gate, proposer="server")
            else:
                progression.set_phase(self.fault_id, ProgressionPhase.REASSESS, reason=f"verification failed: {record.outcome_reason}")
        else:
            pending = [c.description for c in record.criteria if c.status is not CriterionStatus.MET]
            progression.set_phase(
                self.fault_id, ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED,
                reason=f"verification incomplete: {pending}" if record.criteria else "no explicit verification criteria",
            )
        self.events.append({"event": "verification_evaluated", "remediation_id": record.remediation_id, "state": record.state.value})

    def apply_operator_turn(self, operator_text: str, legacy_observation: Optional[str] = None, previous_fault_id: Optional[str] = None) -> None:
        """Reopen a closed fault on the operator's explicit report; apply RESULT / BLOCKED to the
        pending step. Only the progression is mutated; the thread is re-derived from it.
        `previous_fault_id`: the fault that was active before this turn's thread resolution -- an
        explicit cancellation ("forget that check ...") refers to ITS pending step."""
        self._reopen_if_operator_reports_recurrence()
        if self.objective_change:
            self._cancel_pending_step(previous_fault_id)
        step = self.pending_step()
        if self.turn_kind is TurnKind.KNOWN_RESULT:
            self.result_binding = {
                "binding": "known_result", "step_id": step.step_id if step is not None else None,
                "known_result_step_id": self.known_result_step_id, "result_source": ResultSource.OPERATOR_MESSAGE.value,
                "old_status": step.status.value if step is not None else None, "new_status": step.status.value if step is not None else None,
                "consumed": False,
            }
            self.events.append({"event": "known_result_resubmitted", "step_id": self.known_result_step_id, "pending_step_id": step.step_id if step else None})
            self.refresh_projection()
            return
        if step is None or self.turn_kind is TurnKind.OBJECTIVE_CHANGE:
            self.refresh_projection()
            return
        text = (operator_text or "").strip()
        old_status = step.status
        if self.turn_kind is TurnKind.UNVALIDATED_RESULT and text and self.validation is not None:
            self.progression.record_candidate_observation(step.step_id, text, ResultSource.OPERATOR_MESSAGE, self.validation.as_dict())
            self.progression.set_phase(self.fault_id, ProgressionPhase.RESULT_RECEIVED)
            self.progression.set_phase(self.fault_id, self._awaiting_phase(step), reason="candidate not validated: " + "; ".join(self.validation.reasons))
            self.events.append({"event": "result_not_validated", "step_id": step.step_id, "status": self.validation.status.value, "reasons": self.validation.reasons})
            self.result_binding = {
                "binding": "foreign" if self.validation.status is ResultValidationStatus.FOREIGN else "not_attributable",
                "step_id": step.step_id, "result_source": ResultSource.OPERATOR_MESSAGE.value,
                "old_status": old_status.value, "new_status": step.status.value, "consumed": False,
                "validation": self.validation.status.value,
            }
        elif self.turn_kind is TurnKind.RESULT and text and self.validation is not None:
            # Bind -> record -> transition -> leave pending eligibility, all BEFORE the specialist
            # reasons about what comes next (it sees the post-binding progression only).
            self._complete_with_result(step, text, ResultSource.OPERATOR_MESSAGE, None, self.validation)
            # Summary shown in prior-step context (derived from the operator's own words). Only a
            # VALIDATED result is trusted evidence; the projection enforces that for parameter use.
            step.observation_summary = legacy_observation or text[:200]
            self.result_binding = {
                "binding": "matched", "step_id": step.step_id, "result_source": ResultSource.OPERATOR_MESSAGE.value,
                "old_status": old_status.value, "new_status": step.status.value,
                "consumed": step.step_id in self.consumed_step_ids and self.pending_step() is not step,
                "validation": self.validation.status.value,
            }
        elif self.turn_kind is TurnKind.BLOCKED:
            self.progression.set_step_status(step.step_id, StepStatus.SKIPPED, reason=f"operator: {text[:500]}")
            step.observation_summary = text[:500]
            record = self.progression.remediation_for_step(step.step_id) if step.purpose is StepPurpose.REMEDIATION else None
            if record is not None:
                record.state, record.outcome_reason = RemediationState.NOT_PERFORMED, f"operator: {text[:300]}"
            if step.purpose is StepPurpose.VERIFICATION and self.verification_required():
                self.progression.set_phase(self.fault_id, ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED, reason="verification check not performed")
            else:
                self.progression.set_phase(self.fault_id, ProgressionPhase.REASSESS, reason="step could not be performed; evaluate an alternate path")
            step.observation_summary = f"Not performed: {text[:500]}"
            self.events.append({"event": "step_blocked", "step_id": step.step_id})
        if self.result_binding is not None:
            after = self.pending_step()
            self.result_binding["pending_after_binding"] = after.step_id if after is not None else None
        self.refresh_projection()

    def _cancel_pending_step(self, previous_fault_id: Optional[str]) -> None:
        """Server-owned PRESENTED -> CANCELLED / SUPERSEDED transition, only on the operator's own
        explicit words (never on a vague follow-up, never by the model). Other fault threads keep
        their pending steps."""
        fault_id = previous_fault_id if previous_fault_id and previous_fault_id in self.progression.faults else self.fault_id
        pending = [s for s in self.progression.steps_for(fault_id) if s.status in PENDING_STATUSES]
        if not pending:
            return
        step = pending[-1]
        status = StepStatus.SUPERSEDED if self.objective_change == "superseded" else StepStatus.CANCELLED
        reason = f"operator: {self.operator_text[:300]}"
        self.progression.set_step_status(step.step_id, status, reason=reason)
        step.observation_summary = f"{status.value.capitalize()}: {self.operator_text[:300]}"
        record = self.progression.remediation_for_step(step.step_id) if step.purpose is StepPurpose.REMEDIATION else None
        if record is not None and not record.criteria_frozen:
            record.state, record.outcome_reason = RemediationState.NOT_PERFORMED, reason
        self.progression.set_phase(fault_id, ProgressionPhase.REASSESS, reason=f"pending step {status.value} by the operator; evaluate the new objective")
        self._objective_change_event = {"event": f"pending_step_{status.value}", "fault_id": fault_id, "step_id": step.step_id, "reason": reason}
        self.events.append(dict(self._objective_change_event))

    def _reopen_if_operator_reports_recurrence(self) -> None:
        """A RESOLVED / ESCALATED fault progresses again only on the operator's own explicit words
        (recurrence or re-check request), when the policy allows it; earlier results become stale."""
        fault = self._fault()
        if fault.resolution not in _CLOSED or not (_REOPEN.search(self.intent_text) or _RECHECK_REQUEST.search(self.intent_text)):
            return
        if not self.policy.allow_reopen_after_resolution:
            self.events.append({"event": "reopen_refused", "policy": self.policy.ref("allow_reopen_after_resolution")})
            return
        previous = fault.resolution
        fault.reopened_at_sequence = max((s.sequence for s in self.progression.steps_for(self.fault_id)), default=0)
        self.progression.set_resolution(self.fault_id, ResolutionState.UNRESOLVED, reason=f"reopened by the operator: {self.operator_text[:200]}")
        self.progression.set_phase(self.fault_id, ProgressionPhase.REASSESS, reason=f"{previous.value} fault reopened by the operator")
        self.events.append({"event": "fault_reopened", "previous_resolution": previous.value, "policy": self.policy.ref("allow_reopen_after_resolution")})

    def _awaiting_phase(self, step: TroubleshootingStep) -> ProgressionPhase:
        if step.purpose is StepPurpose.REMEDIATION:
            return ProgressionPhase.REMEDIATION
        if step.purpose is StepPurpose.VERIFICATION:
            return ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED
        return ProgressionPhase.AWAITING_OBSERVATION

    def continuity_objective(self) -> Optional[str]:
        step = self.pending_step()
        if step is None or self.turn_kind not in (TurnKind.COMMAND_FOLLOW_UP, TurnKind.UNVALIDATED_RESULT):
            return None
        return f"Resolve the pending diagnostic step: {step.objective}"

    # ---- specialist proposal ------------------------------------------------------------------------
    def evaluate_proposal(
        self, result: Any, resolution: Any = None, evidence: Any = (), proposed_command: Optional[str] = None,
        blocked_action: Any = None,
    ) -> tuple[ProposalDecision, Any]:
        """candidate -> canonical identity -> sequencing / duplicate decision -> remediation gate.
        `blocked_action`: the server-derived applicability-blocked governed action of this proposal
        (procedure_actions.applicability_blocked_action) -- identity only, never authority."""
        self._gate = None
        self._blocked = blocked_action
        self._blocked_continuation = False
        self.presented_pending_step_id = None
        fault = self._fault()
        if fault.resolution in _CLOSED and isinstance(result, dict) and result.get("outcome") == "recommended" and isinstance(result.get("diagnostic_step"), dict):
            self._candidate = self._recheck = self._duplicate = None
            return ProposalDecision.REJECTED_FAULT_CLOSED, self._reject(
                result, f"This fault is {fault.resolution.value}; it progresses again only when the operator reports a recurrence or asks to re-check."
            )
        decision, out = self._sequence(result, resolution, evidence, proposed_command)
        if decision is ProposalDecision.PENDING_STEP_RESOLVED:
            resolved = self.pending_step()
            self.presented_pending_step_id = resolved.step_id if resolved is not None else None
        if decision not in ACCEPTED_DECISIONS:
            return decision, out
        action = getattr(resolution, "action", None)
        if not gates.is_state_changing(self._candidate):
            # The agent chooses the diagnostic; the server blocks it ONLY for an explicit governed
            # MANDATORY prerequisite that is incomplete (never for document order or advice).
            if action is not None and (decision is not ProposalDecision.PENDING_STEP_RESOLVED or self._blocked_continuation):
                prerequisites = gates.structured_prerequisites(action, self._candidate, self.progression.steps_for(self.fault_id), self._actions)
                if not prerequisites["met"]:
                    self._gate = {"prerequisites": prerequisites}
                    self.events.append({"event": "mandatory_prerequisite_incomplete", "detail": prerequisites["detail"]})
                    return ProposalDecision.REJECTED_MANDATORY_PREREQUISITE, self._reject(result, prerequisites["detail"])
            return decision, out
        self._gate = gates.remediation_gate(self.progression, self.fault_id, self._candidate, resolution, self._sections, self._actions)
        if gates.gate_passed(self._gate):
            self.events.append({"event": "remediation_gate_passed"})
            return decision, out
        missing = gates.unmet(self._gate)
        self.events.append({"event": "remediation_gate_failed", "unmet": missing})
        pending = self.pending_step() if decision is ProposalDecision.PENDING_STEP_RESOLVED else None
        return ProposalDecision.REJECTED_PREMATURE_REMEDIATION, self._reject(
            result, "Remediation is not yet justified by the recorded investigation: " + "; ".join(missing), pending
        )

    def _sequence(self, result: Any, resolution: Any = None, evidence: Any = (), model_command: Optional[str] = None) -> tuple[ProposalDecision, Any]:
        """candidate -> canonical identity -> compare with pending / completed / attempted steps."""
        self._candidate = self._recheck = self._duplicate = None
        if not isinstance(result, dict) or result.get("outcome") != "recommended" or not isinstance(result.get("diagnostic_step"), dict):
            return ProposalDecision.NONE, result
        step = result["diagnostic_step"]
        proposed_command = step.get("command")
        if is_multi_action_command(proposed_command) or is_multi_action_command(model_command):
            return ProposalDecision.REJECTED_MULTIPLE_ACTIONS, self._reject(
                result, "One operational diagnostic action per step: the proposal combined several actions."
            )
        sections = _governed_sections(evidence)
        self._sections, self._actions = sections, gates.section_actions(sections, evidence)
        identity = candidate_identity(
            self.fault_id, step, resolution, {sid: app for sid, (_, app) in sections.items()}, _governed_versions(evidence)
        )
        self._candidate = identity
        action = getattr(resolution, "action", None)
        action_id = getattr(action, "action_id", None) or step.get("procedure_action_id")
        pending = self.pending_step()
        if pending is not None:
            return self._against_pending(result, step, identity, action, action_id, pending, sections)

        prior, kind = self._prior_duplicate(identity, action_id)
        if prior is None:
            return ProposalDecision.NEW_STEP, result
        self._duplicate = {"kind": kind, "of_step_id": prior.step_id, "identity": identity.key}
        permit = self._recheck_permission(prior, identity, sections)
        if permit is not None:
            reason, detail = permit
            self._recheck = {"of_step_id": prior.step_id, "reason": reason, "detail": detail}
            self.events.append({"event": "recheck_permitted", "of_step_id": prior.step_id, "reason": reason, "detail": detail})
            return ProposalDecision.RECHECK_PERMITTED, result
        decision, message = {
            "completed": (ProposalDecision.REJECTED_REPEATED_STEP, f"'{prior.objective}' was already performed in this investigation; its result is recorded."),
            "known_result": (ProposalDecision.REJECTED_KNOWN_RESULT, f"The result of this check is already known from '{prior.objective}'."),
            "failed": (ProposalDecision.REJECTED_FAILED_UNCHANGED, f"'{prior.objective}' already failed and nothing relevant has changed since."),
            "skipped": (ProposalDecision.REJECTED_FAILED_UNCHANGED, f"'{prior.objective}' could not be performed and nothing relevant has changed since."),
        }[kind]
        message += " A repeat needs an explicit reason: an operator re-check request, post-action verification, or changed governed context."
        request = self.recheck_request()
        if request is not None:
            target = request.get("target")
            if target is None:
                message += f" The re-check request does not identify one earlier check (candidates: {request.get('candidates')}); say which check to repeat."
            else:
                message += f" The re-check request refers to '{target.objective}', not to this step."
        return decision, self._reject(result, message)

    def _against_pending(
        self,
        result: dict[str, Any],
        step: Mapping[str, Any],
        identity: StepIdentity,
        action: Any,
        action_id: Optional[str],
        pending: TroubleshootingStep,
        sections: Mapping[str, tuple[str, Optional[str]]],
    ) -> tuple[ProposalDecision, Any]:
        blocked = pending.blocked_candidate if pending.status is StepStatus.BLOCKED_BY_CLARIFICATION else None
        if blocked is not None:
            # The pending step is a governed action blocked only by unresolved applicability. Its
            # identity is SERVER STRUCTURE (ProcedureAction id + canonical source + template), so it
            # is continued only by that same structure -- never by wording -- and a different action
            # never replaces it.
            if self._continues_blocked(blocked, action, sections) or self._legacy_continues_blocked(blocked, action, step, sections):
                self._blocked_continuation = True
                self.events.append({
                    "event": "blocked_step_continued", "step_id": pending.step_id,
                    "procedure_action_id": blocked.procedure_action_id, "source_id": blocked.source_id,
                })
                return ProposalDecision.PENDING_STEP_RESOLVED, result
            mine = self._blocked
            if (
                mine is not None
                and mine.procedure_action_id == blocked.procedure_action_id
                and (blocked.identity is None or mine.identity == blocked.identity)
            ):
                self.events.append({"event": "blocked_step_still_blocked", "step_id": pending.step_id, "reason": blocked.blocking_reason})
                return ProposalDecision.PENDING_STEP_RESOLVED, result
            return ProposalDecision.REJECTED_PENDING_AWAITS_RESULT, self._reject(
                result, f"The pending step '{pending.objective}' is still awaiting its result; a different step cannot be accepted before it.", pending
            )
        pending_identity = step_identity(pending)
        same_action = (bool(action_id) and action_id == pending.procedure_action_id) or (
            pending.identity is not None and pending_identity.action_key == identity.action_key
        )
        if same_action:
            if not pending_identity.target or not identity.target or pending_identity.target == identity.target:
                if pending_identity.key == identity.key:
                    self.events.append({"event": "duplicate_of_pending_suppressed", "step_id": pending.step_id})
                return ProposalDecision.PENDING_STEP_RESOLVED, result
            if self._stated_by_operator(identity.target):
                self.events.append({"event": "pending_step_retargeted", "step_id": pending.step_id, "target": identity.target})
                return ProposalDecision.PENDING_STEP_RESOLVED, result
        if pending.procedure_action_id is None and gates.hypothesis_key(pending.objective) == gates.hypothesis_key(str(step.get("action") or "")):
            return ProposalDecision.PENDING_STEP_RESOLVED, result  # same stated objective, now with a governed action
        if (
            self._resumed_clarification_step_id == pending.step_id
            and pending.status is StepStatus.PRESENTED
            and not pending.command
            and not pending.procedure_action_id
            and pending.blocked_candidate is None
            and getattr(getattr(action, "action_type", None), "value", None) == "diagnostic_read"
        ):
            # The pending step was recorded while governed applicability was unresolved and carries
            # nothing executable (no command, no governed action, no blocked identity): the operator
            # could not perform it. The RESOLVED clarification it was waiting on resumes it with
            # the governed diagnostic read resolved from THIS run's selected MATCH evidence -- by
            # server structure, never by wording. Authority is unchanged (resolver + Command Authority).
            self.events.append({
                "event": "unexecutable_step_continued_after_clarification", "step_id": pending.step_id,
                "procedure_action_id": action_id,
            })
            return ProposalDecision.PENDING_STEP_RESOLVED, result
        return ProposalDecision.REJECTED_PENDING_AWAITS_RESULT, self._reject(
            result, f"The pending step '{pending.objective}' is still awaiting its result; a different step cannot be accepted before it.", pending
        )

    @staticmethod
    def _continues_blocked(blocked: Any, action: Any, sections: Mapping[str, tuple[str, Optional[str]]]) -> bool:
        """True only when THIS turn's server-resolved ProcedureAction is the blocked action itself
        (same deterministic action id -- a digest of the exact source tuple and template --, the same
        structured source identity when the record carries one, the same normalized template) and
        that source's applicability evaluated in this turn is MATCH. Authority is NOT decided here:
        the command must still have been rendered and authorized by Command Authority this turn."""
        if action is None or getattr(action, "action_id", None) != blocked.procedure_action_id:
            return False
        source = getattr(action, "source", None)
        if blocked.identity is not None and identity_of(source) != blocked.identity:
            return False
        if " ".join(str(getattr(action, "command_template", "")).split()).casefold() != blocked.normalized_command:
            return False
        _, applicability = sections.get(str(getattr(source, "canonical_source_id", "") or ""), (None, None))
        return str(applicability or "").lower() == "match"

    def _legacy_continues_blocked(
        self, blocked: Any, action: Any, step: Mapping[str, Any], sections: Mapping[str, tuple[str, Optional[str]]]
    ) -> bool:
        """Legacy command path (no ProcedureAction resolved this turn): the blocked step continues only
        when the command Command Authority AUTHORIZED in this turn is the blocked action's own governed
        template, cited from the section of THIS turn's selection that re-derives the blocked action id
        (the exact source tuple, never a source-id string from an earlier turn), now MATCH, and the step
        is structurally linked to this fault's applicability clarification. Wording never participates."""
        if action is not None or not blocked.clarification_id:
            return False
        if not any(q.question_id == blocked.clarification_id and q.fault_id == self.fault_id for q in self.progression.open_questions):
            return False
        command = " ".join(str(step.get("command") or "").split()).casefold()
        if not command or command != blocked.normalized_command:
            return False
        current = (self._actions or {}).get(blocked.procedure_action_id)
        source = getattr(current, "source", None)
        if current is None or step.get("command_source") != getattr(source, "canonical_source_id", None):
            return False
        if blocked.identity is not None and identity_of(source) != blocked.identity:
            return False
        _, applicability = sections.get(str(source.canonical_source_id), (None, None))
        return str(applicability or "").lower() == "match"

    def _stated_by_operator(self, target: str) -> bool:
        values = target_values(target)
        text = self.intent_text.casefold()  # a value inside pasted output is an observation, not a statement
        return bool(values) and all(v and re.search(rf"(?<![\w-]){re.escape(v)}(?![\w-])", text) for v in values)

    def _prior_duplicate(self, identity: StepIdentity, action_id: Optional[str]) -> tuple[Optional[TroubleshootingStep], str]:
        """Latest earlier step of THIS fault thread that is the same step, or whose observation is known."""
        steps = sorted(self.progression.steps_for(self.fault_id), key=lambda s: s.sequence, reverse=True)
        for prior in steps:
            prior_identity = step_identity(prior)
            same = prior_identity.key == identity.key or (
                prior.identity is None and bool(action_id) and prior.procedure_action_id == action_id and not identity.target
            )
            if not same:
                continue
            if prior.status in _HAS_RESULT:
                return prior, "completed"
            if prior.status is StepStatus.FAILED:
                return prior, "failed"
            if prior.status is StepStatus.SKIPPED:
                return prior, "skipped"
        if identity.result_key:
            for prior in steps:
                if prior.status in _HAS_RESULT and step_identity(prior).result_key == identity.result_key:
                    return prior, "known_result"
        return None, ""

    def recheck_request(self) -> Optional[dict[str, Any]]:
        """The operator's explicit re-check request of this turn, resolved to ONE earlier step (or not)."""
        if not _RECHECK_REQUEST.search(self.intent_text):
            return None
        if self._recheck_request is None:
            self._recheck_request = resolve_recheck_target(self.intent_text, self.progression.steps_for(self.fault_id))
        return self._recheck_request

    def _recheck_permission(
        self, prior: TroubleshootingStep, identity: StepIdentity, sections: Mapping[str, tuple[str, Optional[str]]]
    ) -> Optional[tuple[str, str]]:
        """Explicit rules only; the first that applies is recorded as the reason."""
        policy = self.policy
        request = self.recheck_request()
        if request is not None and policy.allow_operator_requested_recheck:
            target = request.get("target")
            if target is not None and (target.step_id == prior.step_id or step_identity(target).key == step_identity(prior).key):
                return "operator_requested_recheck", f"operator asked to re-check {target.step_id} ({target.objective}): {self.operator_text[:160]}"
        stale = policy.result_staleness == "state_change_or_reopen"
        reopened = self._fault().reopened_at_sequence
        if stale and reopened is not None and prior.sequence <= reopened:
            return "result_stale_after_reopen", f"the operator reopened the fault after {prior.step_id}"
        remediation = next(
            (
                s for s in sorted(self.progression.steps_for(self.fault_id), key=lambda s: s.sequence, reverse=True)
                if s.sequence > prior.sequence and s.status is StepStatus.COMPLETED
                and step_identity(s).operation_type in STATE_CHANGING_OPERATION_TYPES
            ),
            None,
        )
        if remediation is not None:
            if self._governed_verification_of(remediation, identity):
                return "governed_post_action_verification", f"after {remediation.step_id} ({remediation.objective}); declared VERIFIED_BY relationship"
            if stale:
                return "result_stale_after_state_change", f"after {remediation.step_id} ({remediation.objective}): the earlier result predates this state change"
        before = step_identity(prior)
        if before.version_label and identity.version_label and before.version_label != identity.version_label:
            return "context_changed", f"governed source version {before.version_label} -> {identity.version_label}"
        if before.applicability and identity.applicability and before.applicability != identity.applicability:
            return "context_changed", f"applicability {before.applicability} -> {identity.applicability}"
        return None

    def _governed_verification_of(self, remediation: TroubleshootingStep, identity: StepIdentity) -> bool:
        """Structured: does the remediation's governed action DECLARE this check as its verification?"""
        remediation_key = step_identity(remediation).action_key
        action = next((a for a in self._actions.values() if gates.action_key(a) == remediation_key), None)
        if action is None:
            return False
        return identity.action_key in {gates.action_key(self._actions[aid]) for aid in action.verification_action_ids if aid in self._actions}

    # ---- escalation (TAE proposes; the server gate decides) -------------------------------------------
    def evaluate_escalation(self, result: Any, evidence: Any = (), *, knowledge_gap: bool = False) -> tuple[Optional[str], Any]:
        """A TAE `escalation_required` outcome is a PROPOSAL. The deterministic escalation gate
        decides; only an accepted proposal transitions the fault. A rejected one is returned as
        `insufficient_evidence` with the gate's reasons (model prose never escalates)."""
        if not isinstance(result, dict) or result.get("outcome") != "escalation_required":
            return None, result
        self._sections = _governed_sections(evidence)
        self._actions = gates.section_actions(self._sections, evidence)
        gate = gates.escalation_gate(self.progression, self.fault_id, self.policy, knowledge_gap=knowledge_gap)
        self._escalation = {"proposed_by": "technical_authority_engineer", "accepted": gate["passed"], "rule": gate.get("rule"), "reasons": gate["reasons"]}
        if gate["passed"]:
            self._escalate(gate, proposer="technical_authority_engineer", proposed_reason=result.get("escalation_reason"))
            return "accepted", result
        out = dict(result)
        out["outcome"] = "insufficient_evidence"
        out["escalation_reason"] = None
        out["missing_information"] = [
            *(result.get("missing_information") or []),
            "Escalation is not yet justified by the recorded investigation: " + "; ".join(gate["reasons"]),
        ]
        self.events.append({"event": "escalation_proposal_rejected", "reasons": gate["reasons"]})
        return "rejected", out

    def _escalate(self, gate: Mapping[str, Any], *, proposer: str, proposed_reason: Optional[str] = None) -> EscalationRecord:
        fault, progression = self._fault(), self.progression
        for pending in [s for s in progression.steps_for(self.fault_id) if s.status in PENDING_STATUSES]:
            progression.set_step_status(pending.step_id, StepStatus.SUPERSEDED, reason=f"superseded by accepted escalation ({gate['rule']})")
            pending.observation_summary = f"Superseded: the fault was escalated ({gate['rule']})"
        record = EscalationRecord(
            fault_id=self.fault_id, rule=str(gate["rule"]), reason="; ".join(gate.get("reasons") or []),
            evidence_step_ids=list(gate.get("evidence_step_ids") or []), evidence_result_ids=list(gate.get("evidence_result_ids") or []),
            policy=self.policy.ref(str(gate["rule"])), proposer=proposer, proposed_reason=(str(proposed_reason)[:500] if proposed_reason else None),
        )
        fault.escalations.append(record)
        progression.set_resolution(self.fault_id, ResolutionState.ESCALATED, reason=record.reason)
        progression.set_phase(self.fault_id, ProgressionPhase.ESCALATION_REQUIRED, reason=f"{record.rule} ({record.policy['policy_id']} v{record.policy['version']})")
        self.events.append({"event": "escalated", "rule": record.rule, "policy": record.policy, "proposer": proposer, "escalation_id": record.escalation_id})
        self.refresh_projection()
        return record

    def _reject(self, result: dict[str, Any], reason: str, pending: Optional[TroubleshootingStep] = None) -> dict[str, Any]:
        from backend.agents.technical_authority_engineer.validation import repair_removed_command_references

        out = withdraw_rejected_command(dict(result))
        # The step the final proposal presents is the pending step re-presented (or nothing pending).
        self.presented_pending_step_id = pending.step_id if pending is not None else None
        if pending is not None:
            out["diagnostic_step"] = {
                "action": pending.objective,
                "reason": "This is the current diagnostic step; its result is needed before the investigation can progress.",
                "expected_evidence": repair_removed_command_references(pending.expected_evidence or "") or "The output of this check",
                "command": None,
                "command_source": None,
                "restrictions": [f"[{reason}]"],
                "procedure_action_id": None,
            }
        else:
            out["outcome"] = "insufficient_evidence"
            out["diagnostic_step"] = None
            out["missing_information"] = [*(result.get("missing_information") or []), reason]
        self.events.append({"event": "proposal_rejected", "reason": reason, **({"duplicate": self._duplicate} if self._duplicate else {})})
        return out

    def check_id_for(self, decision: ProposalDecision, new_check_id: str) -> Optional[str]:
        if decision is ProposalDecision.PENDING_STEP_RESOLVED:
            pending = self.pending_step()
            return pending.check_id if pending and pending.check_id else new_check_id
        if decision in (ProposalDecision.NEW_STEP, ProposalDecision.RECHECK_PERMITTED):
            return new_check_id
        return None

    def record(
        self,
        decision: ProposalDecision,
        result: Any,
        check_id: Optional[str],
        *,
        selected_evidence_ids: list[str],
        applicability: Mapping[str, Any],
        target: Optional[dict[str, str]] = None,
        control_id: Optional[str] = None,
        control_stage: Optional[str] = None,
        blocked_candidate: Any = None,
        selected_evidence: Optional[list[EvidenceIdentity]] = None,
        command_source_identity: Optional[EvidenceIdentity] = None,
    ) -> Optional[TroubleshootingStep]:
        """Record the accepted step in the authoritative progression (and return it). Only a final
        `recommended` result with a diagnostic step is recorded. This is the ONLY place a step (and
        so a projected check record) is created. `blocked_candidate`: a server-derived
        applicability-blocked governed action, kept only while an applicability clarification is open.
        `selected_evidence` / `command_source_identity`: structured identities of the selected sections
        and of the command's source (the id strings are display only)."""
        step = self._record(
            decision, result, check_id, selected_evidence_ids=selected_evidence_ids, applicability=applicability, target=target,
            blocked_candidate=blocked_candidate, selected_evidence=selected_evidence, command_source_identity=command_source_identity,
        )
        if step is not None:
            step.control_id = control_id or step.control_id
            step.control_stage = control_stage or step.control_stage
        self.refresh_projection()
        return step

    def _record(
        self,
        decision: ProposalDecision,
        result: Any,
        check_id: Optional[str],
        *,
        selected_evidence_ids: list[str],
        applicability: Mapping[str, Any],
        target: Optional[dict[str, str]] = None,
        blocked_candidate: Any = None,
        selected_evidence: Optional[list[EvidenceIdentity]] = None,
        command_source_identity: Optional[EvidenceIdentity] = None,
    ) -> Optional[TroubleshootingStep]:
        if decision not in ACCEPTED_DECISIONS:
            return None
        if not isinstance(result, dict) or result.get("outcome") != "recommended" or not isinstance(result.get("diagnostic_step"), dict):
            return None
        step_data = result["diagnostic_step"]
        progression = self.progression
        target = _target_identity(target)
        if decision is ProposalDecision.PENDING_STEP_RESOLVED and self.pending_step() is not None:
            step = self.pending_step()
            step.procedure_action_id = step_data.get("procedure_action_id") or step.procedure_action_id
            step.command = step_data.get("command") or step.command
            if step_data.get("command_source"):
                step.command_source_identity = command_source_identity
            step.command_source_id = step_data.get("command_source") or step.command_source_id
            if selected_evidence_ids:
                step.selected_evidence = list(selected_evidence or [])
            step.selected_evidence_ids = selected_evidence_ids or step.selected_evidence_ids
            step.applicability_snapshot = dict(applicability) or step.applicability_snapshot
            step.target = target or step.target
            step.safety_constraints = list(step_data.get("restrictions") or step.safety_constraints)
            if self._candidate is not None and (step.identity is None or self._candidate.target or not step.identity.target):
                step.identity = self._candidate
            if step.status is StepStatus.BLOCKED_BY_CLARIFICATION and self._blocked_continuation and step.blocked_candidate is not None:
                # Applicability now MATCH and the SAME governed action re-resolved: the blocked step
                # continues (no duplicate step). Its command is only what Command Authority
                # authorized in this turn; the blocked metadata itself never supplied it.
                step.blocked_candidate.released_at = datetime.now(timezone.utc)
                step.blocked_candidate.released_by_action_id = step_data.get("procedure_action_id")
                step.expected_evidence = str(step_data.get("expected_evidence") or "").strip() or step.expected_evidence
                progression.set_step_status(step.step_id, StepStatus.VALIDATED, reason="applicability MATCH: the same governed action was re-resolved")
                progression.set_step_status(step.step_id, StepStatus.PRESENTED, reason="presented; authority, target confirmation, policy and HITL still apply")
                progression.set_phase(self.fault_id, ProgressionPhase.AWAITING_OBSERVATION, reason="blocked governed step continued")
            record = progression.remediation_for_step(step.step_id) if step.purpose is StepPurpose.REMEDIATION else None
            if record is not None and not record.criteria_frozen:
                record.gate = self._gate or record.gate
                record.target = step.identity.target if step.identity else record.target
                if step_data.get("verification_criteria"):
                    self._ground_criteria(record, step_data.get("verification_criteria"))
            self.events.append({"event": "pending_step_resolved", "step_id": step.step_id})
            return step
        purpose = (
            StepPurpose.REMEDIATION if gates.is_state_changing(self._candidate)
            else StepPurpose.VERIFICATION if self.verification_required()
            else StepPurpose.DIAGNOSTIC
        )
        hypothesis = (
            gates.find_or_add_hypothesis(progression, self.fault_id, str(step_data["tests_hypothesis"])) if step_data.get("tests_hypothesis") else None
        )
        step = progression.append_step(
            TroubleshootingStep(
                fault_id=self.fault_id,
                objective=str(step_data.get("action") or ""),
                procedure_action_id=step_data.get("procedure_action_id"),
                command=step_data.get("command"),
                command_source_id=step_data.get("command_source"),
                command_source_identity=command_source_identity if step_data.get("command_source") else None,
                target=target,
                selected_evidence_ids=selected_evidence_ids,
                selected_evidence=list(selected_evidence or []),
                applicability_snapshot=dict(applicability),
                expected_evidence=str(step_data.get("expected_evidence") or ""),
                safety_constraints=list(step_data.get("restrictions") or []),
                check_id=check_id,
                identity=self._candidate,
                purpose=purpose,
                hypothesis_being_tested=hypothesis.hypothesis_id if hypothesis else None,
                recheck_of=(self._recheck or {}).get("of_step_id") if decision is ProposalDecision.RECHECK_PERMITTED else None,
                recheck_reason=(
                    f"{self._recheck['reason']}: {self._recheck['detail']}" if decision is ProposalDecision.RECHECK_PERMITTED and self._recheck else None
                ),
            )
        )
        if purpose is StepPurpose.REMEDIATION:
            progression.set_phase(self.fault_id, ProgressionPhase.REMEDIATION_CANDIDATE, reason="remediation gate passed on server state")
            record = progression.add_remediation(
                RemediationRecord(
                    fault_id=self.fault_id, step_id=step.step_id, procedure_action_id=step.procedure_action_id,
                    source_id=self._candidate.source_id if self._candidate else None, target=self._candidate.target if self._candidate else "",
                    hypothesis_id=hypothesis.hypothesis_id if hypothesis else None, gate=self._gate or {},
                )
            )
            self._ground_criteria(record, step_data.get("verification_criteria"))
            progression.set_step_status(step.step_id, StepStatus.VALIDATED, reason="passed server validation and the remediation gate")
            progression.set_step_status(step.step_id, StepStatus.PRESENTED, reason="presented; authority, target confirmation, policy and HITL still apply")
            progression.set_phase(self.fault_id, ProgressionPhase.REMEDIATION)
        elif purpose is StepPurpose.VERIFICATION:
            progression.set_step_status(step.step_id, StepStatus.VALIDATED, reason="passed server validation")
            progression.set_step_status(step.step_id, StepStatus.PRESENTED, reason="post-action verification check presented")
            progression.set_phase(self.fault_id, ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED, reason="verification check presented")
        elif blocked_candidate is not None and not step.command:
            # Governed diagnostic action blocked ONLY by unresolved applicability: its structural
            # identity is kept; it carries no command, no command source and no authority.
            step.blocked_candidate = blocked_candidate
            progression.set_phase(self.fault_id, ProgressionPhase.STEP_PROPOSED)
            progression.set_step_status(step.step_id, StepStatus.VALIDATED, reason="passed server validation")
            progression.set_step_status(
                step.step_id, StepStatus.BLOCKED_BY_CLARIFICATION,
                reason=f"governed action blocked: {blocked_candidate.blocking_reason}; no execution authority",
            )
            progression.set_phase(self.fault_id, ProgressionPhase.BLOCKED_MISSING_INFORMATION, reason=blocked_candidate.blocking_reason)
            self.events.append({"event": "step_blocked_by_clarification", "step_id": step.step_id, "reason": blocked_candidate.blocking_reason})
            return step
        else:
            progression.set_phase(self.fault_id, ProgressionPhase.STEP_PROPOSED)
            progression.set_step_status(step.step_id, StepStatus.VALIDATED, reason="passed server validation")
            progression.set_phase(self.fault_id, ProgressionPhase.STEP_VALIDATED)
            progression.set_step_status(step.step_id, StepStatus.PRESENTED, reason="presented to the operator")
            progression.set_phase(self.fault_id, ProgressionPhase.AWAITING_OBSERVATION)
        self.events.append({"event": "step_presented", "step_id": step.step_id, "purpose": purpose.value})
        return step

    # ---- evidence acquisition outcomes (evidence_acquisition.py decides; the progression records) ----
    def _gap_or_acquired_step(self, decision: ProposalDecision, step_data: Mapping[str, Any], check_id: Optional[str]) -> TroubleshootingStep:
        pending = self.pending_step() if decision is ProposalDecision.PENDING_STEP_RESOLVED else None
        if pending is not None:
            return pending
        step = self.progression.append_step(
            TroubleshootingStep(
                fault_id=self.fault_id,
                objective=str(step_data.get("action") or ""),
                expected_evidence=str(step_data.get("expected_evidence") or ""),
                check_id=check_id,
                identity=self._candidate,
                purpose=StepPurpose.VERIFICATION if self.verification_required() else StepPurpose.DIAGNOSTIC,
            )
        )
        self.progression.set_phase(self.fault_id, ProgressionPhase.STEP_PROPOSED)
        self.progression.set_step_status(step.step_id, StepStatus.VALIDATED, reason="passed server validation")
        return step

    def record_gap_step(
        self, decision: ProposalDecision, step_data: Mapping[str, Any], requirement: Any, gap: Any, check_id: Optional[str]
    ) -> TroubleshootingStep:
        """The step needs evidence for which discovery completed and NO legitimate acquisition method
        exists: PROPOSED -> VALIDATED -> BLOCKED_GOVERNED_ACQUISITION_GAP. Never PRESENTED, never an
        operator task, not pending (another path or an escalation remains possible)."""
        previous = self.progression.step(requirement.originating_step_id) if requirement.originating_step_id else None
        if previous is not None and previous.status is StepStatus.BLOCKED_GOVERNED_ACQUISITION_GAP:
            # Same unresolved need, same gap: the existing gap step stands (no duplicate step).
            gap.step_id = gap.step_id or previous.step_id
            self.progression.add_requirement(requirement)
            self.progression.record_acquisition_gap(gap)
            self.events.append({"event": "governed_acquisition_gap_continued", "step_id": previous.step_id, "requirement_id": requirement.requirement_id})
            self.refresh_projection()
            return previous
        step = self._gap_or_acquired_step(decision, step_data, check_id)
        step.command = step.command_source_id = step.command_source_identity = step.procedure_action_id = None
        step.evidence_requirement_id = requirement.requirement_id
        step.observation_summary = f"Not performed: no approved acquisition method ({gap.gap_reason.value})"
        requirement.originating_step_id = step.step_id
        gap.step_id = gap.step_id or step.step_id
        self.progression.set_step_status(
            step.step_id, StepStatus.BLOCKED_GOVERNED_ACQUISITION_GAP, reason=f"no legitimate acquisition method: {gap.gap_reason.value}"
        )
        self.progression.add_requirement(requirement)
        self.progression.record_acquisition_gap(gap)
        self.progression.set_phase(self.fault_id, ProgressionPhase.BLOCKED_MISSING_INFORMATION, reason=f"governed acquisition gap: {gap.gap_reason.value}")
        self.events.append({"event": "governed_acquisition_gap", "step_id": step.step_id, "requirement_id": requirement.requirement_id, "reason": gap.gap_reason.value})
        self.refresh_projection()
        return step

    def record_acquired_step(
        self, decision: ProposalDecision, step_data: Mapping[str, Any], requirement: Any, source: Any, content: str, check_id: Optional[str]
    ) -> TroubleshootingStep:
        """The server acquired the step's evidence itself from an approved live / context source:
        the step completes with that observed evidence (CONTEXT_SOURCE); nothing is presented to run."""
        step = self._gap_or_acquired_step(decision, step_data, check_id)
        step.command = step.command_source_id = step.command_source_identity = step.procedure_action_id = None
        step.evidence_requirement_id = requirement.requirement_id
        requirement.originating_step_id = step.step_id
        self.progression.add_requirement(requirement)
        validation = ResultValidation(ResultValidationStatus.VALIDATED, [f"acquired from approved source {source.source_type.value}:{source.source_id}"])
        self._complete_with_result(step, content or "", ResultSource.CONTEXT_SOURCE, None, validation)
        self.events.append({"event": "evidence_acquired_from_source", "step_id": step.step_id, "source": f"{source.source_type.value}:{source.source_id}"})
        self.refresh_projection()
        return step

    def link_requirement(self, step: Optional[TroubleshootingStep], requirement: Any) -> None:
        """Persist the requirement of a presented / blocked step (the acquisition decided for it).
        A step that already carries an unsatisfied requirement keeps ONE requirement: this run's
        candidates, selection and authority audit are folded into it."""
        existing = self.progression.requirement(step.evidence_requirement_id) if step is not None else None
        if existing is requirement:
            # Continuity already reused this very requirement (server-matched): nothing to fold.
            self.progression.add_requirement(requirement)
            return
        if existing is not None and existing.status.value == "unsatisfied":
            from backend.agents.technical_authority_engineer.evidence_acquisition import (
                merge_governed_candidates,
                same_governed_identity,
                structural_governed_candidates,
            )

            known = structural_governed_candidates(existing)
            renamed: dict[str, str] = {}
            for candidate in requirement.acquisition_candidates:
                same = next((k for k in known if same_governed_identity(k, candidate)), None)
                if same is not None and same.acquisition_id != candidate.acquisition_id:
                    # The same structural method keeps its identity across runs.
                    renamed[candidate.acquisition_id] = same.acquisition_id
                    candidate.acquisition_id = same.acquisition_id
            # Never replace: a known governed method this run did not re-evaluate is kept (no authority).
            existing.acquisition_candidates = merge_governed_candidates(known, requirement.acquisition_candidates)
            existing.selected_acquisition_id = renamed.get(requirement.selected_acquisition_id or "", requirement.selected_acquisition_id)
            existing.blocking_reason = requirement.blocking_reason
            for decision in requirement.authority_history:
                if decision.acquisition_id in renamed:
                    decision = decision.model_copy(update={"acquisition_id": renamed[decision.acquisition_id]})
                existing.record_authority(decision)
            self.progression._touch("evidence_requirement_updated", existing.fault_id, requirement_id=existing.requirement_id)
            return
        if step is not None:
            step.evidence_requirement_id = requirement.requirement_id
            requirement.originating_step_id = step.step_id
        self.progression.add_requirement(requirement)

    def _ground_criteria(self, record: RemediationRecord, proposals: Any) -> None:
        accepted, rejected = gates.ground_criteria(
            proposals or [], baseline=gates.pre_action_observations(self.progression, self.fault_id),
            sections=self._sections, actions=self._actions, target=record.target,
        )
        record.criteria, record.rejected_criteria = accepted, rejected
        self.events.append({"event": "verification_criteria", "accepted": len(accepted), "rejected": [r["reason"] for r in rejected]})

    # ---- hypotheses ----------------------------------------------------------------------------------
    def apply_hypothesis_updates(self, proposals: Any) -> list[dict[str, Any]]:
        """TAE-proposed hypothesis states, applied only with trusted observations bound THIS turn."""
        if not proposals:
            return []
        return gates.apply_hypothesis_updates(self.progression, self.fault_id, proposals, list(self.bound_result_ids), self._bound_step_id)

    # ---- views -----------------------------------------------------------------------------------------
    def _remediation_view(self) -> Optional[dict[str, Any]]:
        record = self._fault().active_remediation
        if record is None:
            return None
        return {
            "remediation_id": record.remediation_id,
            "state": record.state.value,
            "target": record.target,
            "gate": {k: v.get("met") for k, v in record.gate.items()},
            "criteria": [{"kind": c.kind.value, "description": c.description, "status": c.status.value, "detail": c.detail} for c in record.criteria],
            "rejected_criteria": list(record.rejected_criteria),
            "outcome_reason": record.outcome_reason,
        }

    def investigation_context(self) -> dict[str, Any]:
        """Server-built state handed to the specialist (never taken from the caller)."""
        fault = self._fault()
        return {
            "stage": fault_stage(fault),
            "phase": fault.phase.value,
            "resolution": fault.resolution.value,
            "remediation": self._remediation_view(),
            "hypotheses": [{"statement": h.statement, "state": h.state.value} for h in self.progression.hypotheses_for(self.fault_id)],
            "open_evidence_requirements": self.open_requirements_view(),
            # Targets PROVEN by this fault's trusted results (key=value exactly as observed). A
            # state-changing action is offered only for one of these; no alias is ever implied.
            "case_target_facts": sorted({f.identity for f in case_target_facts(self.progression, self.fault_id)})[:40],
            "rule": (
                "Remediation is accepted only when the server can establish diagnostic evidence, an isolated target, an applicable "
                "governed remediation and completed declared prerequisites. A command succeeding is not resolution: after a "
                "remediation, propose the verification checks its criteria need. Only the server marks a fault resolved or a "
                "hypothesis confirmed."
            ),
        }

    def open_requirements_view(self, limit: int = 5) -> list[dict[str, Any]]:
        """This fault's UNSATISFIED evidence requirements (never another fault's) with their open
        acquisition gap and operator hints, so the specialist can reference an existing need."""
        view = []
        for requirement in self.progression.open_requirements(self.fault_id)[:limit]:
            gap = self.progression.open_gap(requirement.requirement_id)
            view.append({
                "requirement_id": requirement.requirement_id,
                "kind": requirement.kind.value,
                "semantic_key": requirement.semantic_key,
                "capability": requirement.capability,
                "description": requirement.description,
                "status": requirement.status.value,
                "acquisition_gap": {
                    "gap_id": gap.gap_id, "reason": gap.gap_reason.value, "discovery_attempts": len(gap.all_attempts()),
                } if gap is not None else None,
                "acquisition_hints": [
                    {"value": h.value, "type": h.hint_type.value, "authority": "none (untrusted operator suggestion; search term only)"}
                    for h in requirement.acquisition_hints
                ],
            })
        return view

    def summary(self, decision: ProposalDecision) -> dict[str, Any]:
        pending = self.pending_step()
        fault = self.progression.faults.get(self.fault_id)
        return {
            "fault_id": self.fault_id,
            "turn_kind": self.turn_kind.value,
            "decision": decision.value,
            "phase": fault.phase.value if fault else None,
            "pending_step": {
                "step_id": pending.step_id, "objective": pending.objective, "command": pending.command, "status": pending.status.value,
                "blocked": {
                    "reason": pending.blocked_candidate.blocking_reason,
                    "clarification_id": pending.blocked_candidate.clarification_id,
                } if pending.status is StepStatus.BLOCKED_BY_CLARIFICATION and pending.blocked_candidate else None,
            } if pending else None,
            "candidate_identity": identity_summary(self._candidate),
            "duplicate": self._duplicate,
            "recheck": self._recheck,
            "lifecycle_stage": fault_stage(fault) if fault else None,
            "resolution": fault.resolution.value if fault else None,
            "remediation_gate": self._gate,
            "escalation": self._escalation,
            "objective_change": self._objective_change_event,
            "recheck_request": (
                {k: (v.step_id if k == "target" and v is not None else v) for k, v in self._recheck_request.items()} if self._recheck_request else None
            ),
            "policy": {"policy_id": self.policy.policy_id, "version": self.policy.version},
            "remediation": self._remediation_view() if fault else None,
            "requirements": self._requirements_view(),
            "events": list(self.events),
        }

    def _requirements_view(self, limit: int = 5) -> list[dict[str, Any]]:
        """This fault's latest evidence requirements with their gap state (identifiers only)."""
        view = []
        for requirement in self.progression.requirements_for(self.fault_id)[-limit:]:
            gap = next((g for g in reversed(self.progression.acquisition_gaps) if g.requirement_id == requirement.requirement_id), None)
            view.append({
                "requirement_id": requirement.requirement_id,
                "status": requirement.status.value,
                "blocking_reason": requirement.blocking_reason.value if requirement.blocking_reason else None,
                "gap": {"gap_id": gap.gap_id, "status": gap.status.value, "reason": gap.gap_reason.value, "attempts": len(gap.all_attempts())}
                if gap is not None else None,
            })
        return view

    def save(self) -> None:
        """SESSION-scope persistence (no Case owns this progression) + read projections. Case-scope
        callers persist through ProgressionRepository (versioned) instead."""
        from backend.agents.technical_authority_engineer.troubleshooting_threads import save_projections

        save_progression(self.state, self.progression)
        save_projections(self.state, self.progression, self.fault_id if self._activate else None)
