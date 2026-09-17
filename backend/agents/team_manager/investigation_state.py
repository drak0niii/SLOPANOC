"""POST-6A -- Minimum persistent investigation state for iterative
troubleshooting, plus the interpret-before-advance rule.

THE GAP THIS CLOSES: troubleshooting was, structurally, a sequence of
independent turns. `TroubleshootingGuidance` is explicitly an OUTPUT
SHAPE for one turn ("THIS IS NOT TROUBLESHOOTING STATE", its own
docstring), `PendingGovernedRequest` tracks one unresolved command, and
Case Context stores durable findings -- but nothing recorded WHERE AN
INVESTIGATION HAD GOT TO. So the runtime could not tell:

  - what was actually being investigated across turns;
  - which governed procedure/version/step it was on;
  - whether the prerequisites of that step were satisfied;
  - what evidence it had ASKED the engineer for;
  - whether that evidence had come BACK;
  - and therefore whether it was entitled to move on at all.

Without the last two, "what next?" advanced the procedure simply because
the previous turn had suggested something -- treating a SUGGESTION as if
it were an OBSERVATION. That is the single most misleading failure an
operational assistant can have: it reports progress through a procedure
that nobody actually performed.

WHAT THIS IS NOT: a second memory system. It reuses what already exists:
  - durable findings stay in CASE CONTEXT (`backend/cases`) -- this
    module records no observations of its own, only the fact that one was
    received and what it was interpreted as;
  - the request/permission layer stays `RequestContract`/
    `PendingGovernedRequest`/`RequestExecutionDecision`;
  - governed procedure identity stays the knowledge_id/version_label/
    section_id triple every other layer already uses;
  - persistence is ONE additive session-state key written by
    `chat_service.py`'s existing end-of-turn delta, exactly like
    `PENDING_GOVERNED_REQUEST_STATE_KEY`.

THE LIFECYCLE IS THE POINT. `StepLifecycle` keeps four things apart that
free prose constantly conflates:

    SUGGESTED  -- the assistant proposed a check. Nothing has happened.
    APPROVED   -- a trusted party authorized it (the existing approval
                  boundary; this module never grants it).
    EXECUTED   -- someone reported actually running it.
    OBSERVED   -- its real output came back and was interpreted.

Only OBSERVED entitles the investigation to advance
(`may_advance_to_next_step`). A procedure never moves forward because the
model said something.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

__all__ = [
    "INVESTIGATION_STATE_KEY",
    "InvestigationState",
    "PrerequisiteStatus",
    "StepLifecycle",
    "advance_after_observation",
    "may_advance_to_next_step",
    "parse_investigation_state",
    "record_observation",
    "record_requested_evidence",
    "record_candidate_observation",
    "record_user_observation",
    "record_validated_interpretation",
    "validate_interpretation",
]

INVESTIGATION_STATE_KEY = "active_investigation"
"""Plain, overwritable session-state value -- one per session, rewritten
by the turn that changes it and cleared when the objective is abandoned.
Mirrors `PENDING_GOVERNED_REQUEST_STATE_KEY`'s own established idiom; not
a new persistence mechanism."""


class StepLifecycle(str, Enum):
    """See this module's docstring. Deliberately ordered from least to
    most established -- but never compared ordinally in code, because
    "further along" is not the question; "did it reach OBSERVED" is."""

    SUGGESTED = "suggested"
    APPROVED = "approved"
    EXECUTED = "executed"
    OBSERVED = "observed"


class PrerequisiteStatus(str, Enum):
    """Whether the current step's governed prerequisites hold.

    UNKNOWN is the default and is NOT permissive: a step whose
    prerequisites have not been established is not a step that may be
    performed, it is a step whose prerequisites still need establishing.
    """

    UNKNOWN = "unknown"
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"


class InvestigationState(BaseModel):
    """The minimum durable record of an in-progress investigation."""

    model_config = ConfigDict(frozen=True)

    objective: str
    """What is being investigated, in the user's own framing (the
    validated `RequestContract.subject` this started from). Never
    re-derived per turn -- that is how a topic-free "what next?" keeps
    its subject."""

    # --- active procedure identity (the same triple everywhere else uses)
    knowledge_id: Optional[str] = None
    version_label: Optional[str] = None
    section_id: Optional[str] = None
    step_index: int = 0

    prerequisite_status: PrerequisiteStatus = PrerequisiteStatus.UNKNOWN
    step_lifecycle: StepLifecycle = StepLifecycle.SUGGESTED

    requested_evidence: Optional[str] = None
    """What the assistant asked the engineer to go and find out. Present
    means "we are waiting"; this is the other half of the
    interpret-before-advance rule."""

    candidate_observation: Optional[str] = None
    """POST-6A -- the engineer's latest message, held as a CANDIDATE only.

    An outstanding check does not make every subsequent message a result
    for that check. The message may be a question, a correction, a topic
    change, a cancellation, or an ambiguous "yes". So incoming text lands
    HERE first and is associated with the check only when the specialist
    can quote something real out of it (`record_validated_interpretation`)
    -- at which point, and only then, it is promoted to
    `received_observation`."""

    received_observation: Optional[str] = None
    """The engineer's reported result, ASSOCIATED with the outstanding
    check. Only ever promoted from `candidate_observation` by a validated
    interpretation -- never set directly from a message."""

    observation_source_turn_id: Optional[str] = None
    """POST-6A -- PROVENANCE. The invocation whose user message supplied
    the observation. Without it, a recorded observation is an
    unattributable string that cannot be checked against what was
    actually said."""

    observation_attachment_ids: tuple[str, ...] = ()
    """POST-6A -- the validated attachment ids supplied alongside the
    observation (a pasted screenshot of the output). Identity only; the
    binaries stay in the existing attachment store."""

    interpretation: Optional[str] = None
    """What the received observation was taken to MEAN. Required before
    advancing: an uninterpreted observation is data, not progress."""

    next_permitted_check: Optional[str] = None
    """The single next check the runtime considers permitted. Advisory
    for the next turn; it grants nothing on its own -- command/action
    authority remains entirely with the existing policy layers."""

    updated_at: Optional[datetime] = None

    @property
    def is_awaiting_evidence(self) -> bool:
            # A CANDIDATE message does not end the wait -- only an
        # associated, validated observation does.
        return bool(self.requested_evidence) and not self.received_observation

    @property
    def procedure_key(self) -> Optional[tuple[str, Optional[str], str]]:
        if not self.knowledge_id or not self.section_id:
            return None
        return (self.knowledge_id, self.version_label, self.section_id)


def parse_investigation_state(raw: Any) -> Optional[InvestigationState]:
    """Tolerant, fail-closed parse -- mirrors `parse_pending_governed_
    request` exactly. Malformed/absent data resolves to `None`, which
    simply means "no investigation in progress"."""
    if not isinstance(raw, dict):
        return None
    try:
        return InvestigationState.model_validate(raw)
    except ValidationError:
        return None


def may_advance_to_next_step(state: Optional[InvestigationState]) -> bool:
    """THE interpret-before-advance rule.

    An investigation may move to the next step ONLY when the evidence it
    asked for actually came back AND was interpreted. Explicitly NOT
    sufficient:

      - the previous turn suggested an action (`SUGGESTED`);
      - a write was approved (`APPROVED`) -- approval authorizes running
        something, it does not report a result;
      - the engineer said they ran it (`EXECUTED`) but reported no
        output -- "I restarted it" is not an observation of what
        happened;
      - the model produced confident prose about what probably happened.

    `None` (no investigation) is `False`: there is no next step to
    advance to.
    """
    if state is None:
        return False
    if state.step_lifecycle != StepLifecycle.OBSERVED:
        return False
    return bool(state.received_observation) and bool(state.interpretation)


def record_requested_evidence(
    state: InvestigationState, requested_evidence: str, *, now: Optional[datetime] = None
) -> InvestigationState:
    """The assistant has asked for something. This RESETS the observation
    half: a new request means whatever was observed before belongs to the
    previous step and must not satisfy this one."""
    return state.model_copy(
        update={
            "requested_evidence": requested_evidence,
            "received_observation": None,
            "candidate_observation": None,
            "interpretation": None,
            "step_lifecycle": StepLifecycle.SUGGESTED,
            "updated_at": now,
        }
    )


def record_observation(
    state: InvestigationState,
    observation: str,
    interpretation: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> InvestigationState:
    """Real output came back. Reaches `OBSERVED` only when it was also
    INTERPRETED -- an uninterpreted result stays `EXECUTED`, which
    `may_advance_to_next_step` correctly refuses to advance on, so the
    next turn's job is to interpret rather than to move on."""
    lifecycle = StepLifecycle.OBSERVED if interpretation else StepLifecycle.EXECUTED
    return state.model_copy(
        update={
            "received_observation": observation,
            "interpretation": interpretation,
            "step_lifecycle": lifecycle,
            "updated_at": now,
        }
    )


def advance_after_observation(
    state: InvestigationState, *, next_permitted_check: Optional[str] = None, now: Optional[datetime] = None
) -> InvestigationState:
    """Move to the next step. Refuses (returns the state unchanged) when
    `may_advance_to_next_step` is `False` -- the guard lives HERE, not in
    the caller, so no call site can advance by forgetting to check."""
    if not may_advance_to_next_step(state):
        return state
    return state.model_copy(
        update={
            "step_index": state.step_index + 1,
            "prerequisite_status": PrerequisiteStatus.UNKNOWN,
            "step_lifecycle": StepLifecycle.SUGGESTED,
            "requested_evidence": None,
            "received_observation": None,
            "candidate_observation": None,
            "interpretation": None,
            "next_permitted_check": next_permitted_check,
            "updated_at": now,
        }
    )


def build_investigation_state_update(state: Optional[InvestigationState]) -> dict[str, object]:
    """The additive, single-key session-state update `chat_service.py`
    merges into its existing end-of-turn delta -- the same shape
    `build_pending_governed_request_state_update` already produces."""
    return {INVESTIGATION_STATE_KEY: state.model_dump(mode="json") if state is not None else None}


def render_investigation_context(state: Optional[InvestigationState]) -> str:
    """Deterministic, Python-authored rendering for the authoritative
    prompt block -- never model-generated, and never the observation's
    raw content beyond what the engineer themselves reported.

    Says explicitly when the runtime is WAITING, so the reasoning layer
    cannot mistake an outstanding request for a completed step.
    """
    if state is None:
        return ""
    lines = [
        "ACTIVE INVESTIGATION (deterministic runtime state -- do not restate or contradict):",
        f"- objective: {state.objective}",
        f"- active procedure: {state.knowledge_id or '(none)'} "
        f"version={state.version_label or '(none)'} section={state.section_id or '(none)'} "
        f"step={state.step_index}",
        f"- prerequisites: {state.prerequisite_status.value}",
        f"- step status: {state.step_lifecycle.value}",
    ]
    if state.requested_evidence:
        lines.append(f"- evidence requested from the engineer: {state.requested_evidence}")
    if state.received_observation:
        lines.append(f"- observation received: {state.received_observation}")
    if state.interpretation:
        lines.append(f"- interpretation: {state.interpretation}")
    if state.is_awaiting_evidence:
        lines.append(
            "- STATUS: waiting for that evidence. Nothing has been executed or observed for this step; "
            "do not describe it as done and do not move to a later step."
        )
    if state.next_permitted_check:
        lines.append(f"- next permitted check: {state.next_permitted_check}")
    return "\n".join(lines)


def _normalize(text: str) -> str:
    return " ".join(text.replace("\r\n", "\n").replace("\r", "\n").split()).lower()


def validate_interpretation(
    state: Optional[InvestigationState], interpretation: Any
) -> tuple[bool, str]:
    """POST-6A -- does this interpretation actually refer to the
    observation we recorded?

    Returns `(accepted, reason)`. Accepted requires ALL of:

      1. there IS a recorded observation for the current step -- an
         interpretation with nothing to interpret is not evidence;
      2. `observation_reference` appears LITERALLY (whitespace-normalized,
         case-insensitively) inside that observation -- so the specialist
         cannot interpret something the engineer never reported, and
         cannot quote the procedure back at us instead of their result;
      3. it carries a non-empty `meaning`.

    Whitespace/case normalization is the ONLY latitude given: terminal
    output wraps and cases differ, but the substance must be genuinely
    present. No fuzzy matching, no semantic similarity -- an
    interpretation that cannot be tied to real reported output must not
    be able to advance a procedure.
    """
    # POST-6A -- validates against the CANDIDATE, which is what makes
    # association earned: the specialist must quote real content out of
    # the message before that message counts as this check's result.
    source = (state.candidate_observation or state.received_observation) if state is not None else None
    if state is None or not source:
        return False, "no_observation_recorded"
    reference = getattr(interpretation, "observation_reference", "") or ""
    meaning = getattr(interpretation, "meaning", "") or ""
    if not reference.strip():
        return False, "no_observation_reference"
    if not meaning.strip():
        return False, "no_meaning"
    if _normalize(reference) not in _normalize(source):
        return False, "observation_reference_not_found"
    return True, "accepted"


def record_validated_interpretation(
    state: InvestigationState, interpretation: Any, *, now: Optional[datetime] = None
) -> tuple[InvestigationState, str]:
    """Record an interpretation ONLY if it validates against the recorded
    observation, and only advance the lifecycle to OBSERVED when the
    specialist also states the observation CONCLUDES the requested check.

    A rejected or inconclusive interpretation leaves the step exactly
    where it was -- which is the correct outcome: the next turn's job is
    to get a usable result, not to move on without one.
    """
    accepted, reason = validate_interpretation(state, interpretation)
    if not accepted:
        return state, reason
    associated = state.candidate_observation or state.received_observation
    if not getattr(interpretation, "concludes_step", False):
        # Genuinely about this check, but not conclusive. The observation
        # IS now associated (it was quotable), so it is promoted and the
        # step reaches EXECUTED -- but not OBSERVED, so nothing advances.
        return (
            state.model_copy(
                update={
                    "received_observation": associated,
                    "candidate_observation": None,
                    "interpretation": None,
                    "step_lifecycle": StepLifecycle.EXECUTED,
                    "updated_at": now,
                }
            ),
            "inconclusive",
        )
    return (
        state.model_copy(
            update={
                "received_observation": associated,
                "candidate_observation": None,
                "interpretation": interpretation.meaning,
                "step_lifecycle": StepLifecycle.OBSERVED,
                "updated_at": now,
            }
        ),
        "accepted",
    )


def record_candidate_observation(
    state: InvestigationState,
    observation_text: str,
    *,
    source_turn_id: Optional[str] = None,
    attachment_ids: Sequence[str] = (),
    now: Optional[datetime] = None,
) -> InvestigationState:
    """POST-6A -- hold this message as a CANDIDATE result, with
    provenance, WITHOUT associating it with the outstanding check.

    The lifecycle deliberately does NOT move: a message arriving while a
    check is outstanding is not evidence that the check was performed.
    Association happens only in `record_validated_interpretation`, which
    requires the specialist to quote real content out of this text. A
    question, a correction, a topic change, a cancellation, or a bare
    "yes" therefore all land here and go no further -- there is nothing
    in them to quote.
    """
    return state.model_copy(
        update={
            "candidate_observation": observation_text,
            "observation_source_turn_id": source_turn_id,
            "observation_attachment_ids": tuple(attachment_ids),
            "updated_at": now,
        }
    )


def record_user_observation(
    state: InvestigationState,
    observation_text: str,
    *,
    source_turn_id: Optional[str] = None,
    attachment_ids: Sequence[str] = (),
    now: Optional[datetime] = None,
) -> InvestigationState:
    """Capture what the engineer actually reported, with provenance.

    ONLY called with real user input for a turn where a check was
    genuinely outstanding -- see `chat_service.py`'s own call site.
    Reaching `EXECUTED` (not `OBSERVED`) is deliberate: the result exists
    but has not been interpreted yet, and `may_advance_to_next_step`
    correctly refuses to advance on it.

    A SUGGESTION, AN APPROVAL OR A BARE "yes" IS NOT EXECUTION EVIDENCE:
    this records the TEXT the engineer supplied as the observation, and
    `validate_interpretation` then requires the specialist to quote
    something real out of it. A message with nothing to quote produces no
    valid interpretation and therefore never advances the step.
    """
    return state.model_copy(
        update={
            "received_observation": observation_text,
            "observation_source_turn_id": source_turn_id,
            "observation_attachment_ids": tuple(attachment_ids),
            "interpretation": None,
            "step_lifecycle": StepLifecycle.EXECUTED,
            "updated_at": now,
        }
    )
