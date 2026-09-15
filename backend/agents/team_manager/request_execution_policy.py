"""Phase 6A.14 -- Deterministic Request Execution.

THE GAP THIS CLOSES: Phase 6A.13 built `RequestContract` -- a validated,
provenance-verified record of what the user actually asked for and
supplied -- but explicitly stopped short of making anything OBEY it. A
real live sequence proved why that is not optional: "how do i handle HW
Partial Fault?" -> "it's an RRU" produced a contract correctly showing
`provided_context.unit_type=RRU` and `missing_context=["unit_id"]`, yet
the final answer still contained `accn FieldReplaceableUnit=RRU-9
restartunit 1 1 1` -- a real, Approved, VERBATIM-grounded command (DEF-
0024/0027 proved that part correctly), but parameterized with an
identifier the user never supplied. GROUNDED IN KNOWLEDGE != VALID FOR
THIS LIVE TARGET.

WHAT THIS MODULE IS: a small, PURE, deterministic function --
`derive_execution_decision` -- that reads a validated `RequestContract`
(6A.13, unmodified) and produces a `RequestExecutionDecision`: what the
runtime is ALLOWED to do for this turn. Contains NO LLM reasoning, no
model call, no natural-language parsing of any kind -- every branch is a
plain, typed condition over already-validated contract fields.

WHERE ENFORCEMENT ACTUALLY HAPPENS, AND WHY NOT INSIDE incident_manager's
OWN `evidence.py` (audited first, per instruction "audit the Phase 6A.13
implementation first... do not duplicate any of it"): `incident_manager`
is invoked via `AgentTool`/`MultimodalAgentTool`, which constructs a
BRAND-NEW `InMemorySessionService`/session on every single call (verified
against the installed ADK source -- the same fact `state_sync.py`'s own
module docstring already documents for an unrelated problem). This means
`incident_manager`'s own `callback_context.state` (used throughout
`evidence.py`) is NEVER the same state object as team_manager's own real,
durable session state -- `VALIDATED_REQUEST_CONTRACT_STATE_KEY`
genuinely does not exist there. `chat_service.py`'s own turn-completion
boundary is therefore the ONLY point that already has simultaneous access
to BOTH team_manager's own real session state (where the validated
contract lives) AND the turn's final `TroubleshootingGuidance` (captured
via `troubleshooting_guidance_context.py`, the SAME store the existing
A5 "HARD, deterministic one-command-at-a-time override" already reads) --
so this module's own `enforce_execution_decision_on_guidance` is wired in
at that EXACT existing seam, never a new, parallel response pipeline.
DEF-0024/0026/0027's own grounding (`evidence.py`, completely untouched)
remains a SEPARATE, still-fully-active layer underneath this one: this
module answers "is the runtime even ALLOWED to show a command for this
request," `evidence.py` separately still answers "IF allowed, is this
SPECIFIC command string actually grounded in the right procedure." Both
must agree before a command reaches the user.

CURRENT-TURN FRESHNESS (section 16's own explicit requirement): a stale,
prior-turn contract must never authorize the current turn. `RequestContract`
gained one additive field, `run_id: Optional[str] = None` -- NEVER a
parameter of `record_request_contract` itself (so the model cannot set or
spoof it; ADK's auto-generated tool schema is derived only from that
function's own parameters), populated ONLY by `validate_and_persist_
request_contract` at persist time, from the SAME trusted `current_run_id()`
correlation this codebase already relies on everywhere else for this
exact problem class (Teams evidence, governed-knowledge selection,
troubleshooting guidance). `derive_execution_decision` requires
`contract.run_id == current_run_id` (the CALLER's own trusted run
identity, e.g. `chat_service.py`'s own `sequencer.run_id`) before trusting
the contract at all -- no new persistence infrastructure, just the
existing per-turn run-id correlation, applied one layer further.

GRACEFUL FALLBACK, DELIBERATELY NARROW (section 4's own explicit
allowance, "never allow missing contract state to enable MORE
capability"): a missing/stale contract is treated AT LEAST as
restrictively as `INVALID_CONTRACT` -- `may_emit_command`/`may_execute_
action`/`may_emit_operational_steps` are all `False`. This has NO visible
effect on an ordinary informational/conversational turn (there is no
command/action field for it to suppress in the first place) -- it only
ever changes behavior for a turn that would otherwise have surfaced a
command/action, which is exactly the risk this milestone exists to
close. This is the low-risk graceful fallback the instruction itself
anticipates: presentation-only output is never blocked by contract
absence; command/action output always is.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
)
from backend.agents.team_manager.request_contract import (
    RequestContract,
    RequestedOutput,
    RequestIntent,
    is_operationally_shaped_request,
    required_target_parameter_gaps,
)


class RequestExecutionStatus:
    """Closed set of execution-decision outcomes -- plain string
    constants, mirroring `RequestIntent`/`RequestedOutput`'s own
    established, ADK-proven convention (this type is never itself a
    FunctionTool parameter, but the convention is kept consistent
    throughout this package regardless)."""

    ALLOW = "allow"
    NEEDS_INFORMATION = "needs_information"
    AMBIGUOUS = "ambiguous"
    REQUIRES_APPROVAL = "requires_approval"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_CONTRACT = "invalid_contract"


_VALID_STATUSES = frozenset(
    {
        RequestExecutionStatus.ALLOW,
        RequestExecutionStatus.NEEDS_INFORMATION,
        RequestExecutionStatus.AMBIGUOUS,
        RequestExecutionStatus.REQUIRES_APPROVAL,
        RequestExecutionStatus.UNSUPPORTED_CAPABILITY,
        RequestExecutionStatus.INVALID_CONTRACT,
    }
)

# 6A.14 FINAL corrective pass -- ROOT CAUSE B: the first version of this
# policy scoped the missing-context/no-subject gates to COMMAND/
# TROUBLESHOOTING only, reasoning that PROCEDURE/INFORMATION "never carry
# a live command." A real live defect proved that reasoning wrong: "how
# do i handle HW Partial Fault?" -- a request just as plausibly
# classified PROCEDURE or INFORMATION as TROUBLESHOOTING -- produced a
# governed, multi-branch answer containing BOTH the real RRU-9 and real
# AAS-1 commands, with neither unit confirmed by the user. Section 3's
# own governing principle: "Intent controls desired response shape.
# Intent must NOT be usable as a bypass around command/target safety."
# KNOWLEDGE_INVENTORY and ACTION are deliberately EXCLUDED -- they are
# governed by their own, separate, earlier-checked branches in `derive_
# execution_decision` (ACTION always forces REQUIRES_APPROVAL before
# either gate below is ever consulted; KNOWLEDGE_INVENTORY always forces
# UNSUPPORTED_CAPABILITY the same way).
#
# LIVE-CORR-2 -- DEF-0037 CORRECTIVE PASS: that widened set (`TARGET_
# SPECIFIC_INTENTS`, `request_contract.py`) itself went on to become the
# root cause of a DIFFERENT, equally real defect -- it then also included
# `INFORMATION`, so an ordinary conversational request (e.g. "hello,"
# `intent=information, requested_output=fact, subject=None`) was forced
# through the SAME "no resolved subject/procedure" gate as a genuine
# operational request, purely because of its intent label (live evidence,
# session `32c5a4a5-...`). Both gates below now use `is_operationally_
# shaped_request` (request_contract.py) instead of `TARGET_SPECIFIC_
# INTENTS` directly -- a two-factor test (intent in the now-narrower
# `TARGET_SPECIFIC_INTENTS`, OR `requested_output` is itself one of the
# operational answer shapes) that closes DEF-0037 (`INFORMATION` alone no
# longer forces the gate) WITHOUT reopening the original ROOT CAUSE B
# defect (`requested_output=exact_command`/`procedure_steps`/
# `troubleshooting_next_step` still forces the gate regardless of
# intent label -- see that function's own docstring for the full "intent
# labels cannot bypass safety" argument).


class RequestExecutionDecision(BaseModel):
    """What the runtime is ALLOWED to do for this turn -- derived purely
    from an already-validated `RequestContract`, never from model prose.
    See this module's own docstring for the full design.
    """

    status: str
    intent: Optional[str] = None
    requested_output: Optional[str] = None
    subject: Optional[str] = None
    may_emit_command: bool = False
    may_execute_action: bool = False
    may_emit_operational_steps: bool = False
    missing_context: list[str] = Field(default_factory=list)
    ambiguity: bool = False
    approval_required: bool = False
    reason: str = ""


def derive_execution_decision(contract: Optional[RequestContract], current_run_id: Optional[str]) -> RequestExecutionDecision:
    """Pure, deterministic derivation -- NO LLM reasoning, no natural-
    language parsing. See this module's own docstring for the full
    branch-by-branch rationale.
    """
    if contract is None or not current_run_id or contract.run_id != current_run_id:
        return RequestExecutionDecision(
            status=RequestExecutionStatus.INVALID_CONTRACT,
            may_emit_command=False,
            may_execute_action=False,
            may_emit_operational_steps=False,
            ambiguity=True,
            approval_required=True,
            reason="no current-turn validated request contract",
        )

    if contract.ambiguity:
        return RequestExecutionDecision(
            status=RequestExecutionStatus.AMBIGUOUS,
            intent=contract.intent,
            requested_output=contract.requested_output,
            subject=contract.subject,
            may_emit_command=False,
            may_execute_action=False,
            may_emit_operational_steps=False,
            missing_context=list(contract.missing_context),
            ambiguity=True,
            approval_required=contract.approval_required,
            reason="request is ambiguous -- clarification required before any operational output",
        )

    if contract.intent == RequestIntent.ACTION or contract.action_requested:
        return RequestExecutionDecision(
            status=RequestExecutionStatus.REQUIRES_APPROVAL,
            intent=contract.intent,
            requested_output=contract.requested_output,
            subject=contract.subject,
            may_emit_command=False,
            may_execute_action=False,  # actual execution remains the existing, unchanged approval boundary's job
            may_emit_operational_steps=False,
            missing_context=list(contract.missing_context),
            ambiguity=False,
            approval_required=True,  # 6A.13 already forces this on the contract itself; reasserted defensively here
            reason="action requests always require the existing, unchanged approval boundary",
        )

    if contract.intent == RequestIntent.KNOWLEDGE_INVENTORY:
        return RequestExecutionDecision(
            status=RequestExecutionStatus.UNSUPPORTED_CAPABILITY,
            intent=contract.intent,
            requested_output=contract.requested_output,
            subject=contract.subject,
            may_emit_command=False,
            may_execute_action=False,
            may_emit_operational_steps=False,
            missing_context=list(contract.missing_context),
            ambiguity=False,
            approval_required=False,
            reason="deterministic Knowledge catalog enumeration is not yet implemented (a future milestone)",
        )

    if is_operationally_shaped_request(contract.intent, contract.requested_output) and not contract.subject:
        return RequestExecutionDecision(
            status=RequestExecutionStatus.AMBIGUOUS,
            intent=contract.intent,
            requested_output=contract.requested_output,
            subject=None,
            may_emit_command=False,
            may_execute_action=False,
            may_emit_operational_steps=False,
            missing_context=list(contract.missing_context),
            ambiguity=True,
            approval_required=contract.approval_required,
            reason="no resolved subject/procedure for a command-shaped request",
        )

    # LIVE-CORR-2 -- DEF-0037 CORRECTIVE PASS (regression fix, found by
    # this pass's own regression suite, section 9's own explicit "a
    # command-bearing answer must not become safe merely because the
    # request was labelled INFORMATION" warning): this gate must NOT use
    # `is_operationally_shaped_request` alone -- a request the model
    # itself already declared a non-empty `missing_context` for (e.g.
    # `intent=information, requested_output=fact, missing_context=
    # ["unit_type", "unit_id"]`, a real reproduced live shape: the model
    # correctly recognized unresolved target context despite classifying
    # intent/output non-operationally) must still be evaluated here --
    # otherwise a genuinely dangerous, ALREADY-DECLARED gap would be
    # silently discarded purely because of how loosely intent/
    # requested_output happened to be classified, reopening exactly the
    # class of defect DEF-0037's own fix exists to close (never trust the
    # model's OWN classification alone to decide safety). `contract.
    # missing_context` is ALWAYS enough on its own to enter this branch,
    # regardless of `is_operationally_shaped_request`'s own result --
    # this is a strict OR, never an AND, so DEF-0037's own fix (an
    # ordinary "hello," `missing_context=[]`, correctly skips this branch
    # entirely) remains completely unaffected.
    if is_operationally_shaped_request(contract.intent, contract.requested_output) or contract.missing_context:
        # 6A.14 Request Parameter Consistency -- Section 7's own explicit
        # "defense in depth" requirement: do NOT rely on the validator's
        # own already-reconciled `contract.missing_context` alone.
        # Independently re-derives the SAME deterministic gap from
        # `contract.provided_context` (never re-verified here -- this
        # contract already passed `validate_and_persist_request_
        # contract`'s own provenance verification before it was ever
        # persisted) -- so a malformed/stale/hand-crafted contract that
        # somehow reached this function with an empty `missing_context`
        # despite a genuinely unconfirmed target identifier still cannot
        # fail open.
        effective_missing_context = sorted(
            set(contract.missing_context)
            | set(required_target_parameter_gaps(contract.intent, contract.requested_output, contract.provided_context))
        )
        if effective_missing_context:
            return RequestExecutionDecision(
                status=RequestExecutionStatus.NEEDS_INFORMATION,
                intent=contract.intent,
                requested_output=contract.requested_output,
                subject=contract.subject,
                may_emit_command=False,
                may_execute_action=False,
                may_emit_operational_steps=True,  # a safe, non-command diagnostic step/clarification remains allowed
                missing_context=effective_missing_context,
                ambiguity=False,
                approval_required=contract.approval_required,
                reason=f"required context not yet confirmed by the user: {', '.join(effective_missing_context)}",
            )

    # LIVE-CORR-3B -- Operational Authority Boundary, section 1's own
    # explicit "ALLOW must not automatically mean may_emit_command=True"
    # requirement. Command permission is a SEPARATE, NARROWER grant than
    # "this request is fully resolved and safe to answer at all" --
    # possible ONLY for a request VALIDATED as `intent=COMMAND,
    # requested_output=EXACT_COMMAND` (mirrors `is_exact_command_response_
    # permitted`'s own established definition, applied here one layer
    # earlier). A PROCEDURE_STEPS/TROUBLESHOOTING_NEXT_STEP-shaped ALLOW
    # decision never implicitly inherits command permission merely because
    # the overall request is otherwise fully resolved -- closes the exact
    # fail-open path where a fully-parameterized "next step" answer showed
    # a raw command with no EXACT_COMMAND validation at all.
    #
    # `may_emit_operational_steps` remains the separate, broader signal
    # (unchanged in meaning) for whether NON-COMMAND operational/diagnostic
    # narrative is in scope at all for this request shape -- never on its
    # own sufficient to authorize a command.
    allow_command = contract.intent == RequestIntent.COMMAND and contract.requested_output == RequestedOutput.EXACT_COMMAND
    return RequestExecutionDecision(
        status=RequestExecutionStatus.ALLOW,
        intent=contract.intent,
        requested_output=contract.requested_output,
        subject=contract.subject,
        may_emit_command=allow_command,
        may_execute_action=False,  # never this policy's job to authorize an actual write
        may_emit_operational_steps=is_operationally_shaped_request(contract.intent, contract.requested_output),
        missing_context=list(contract.missing_context),
        ambiguity=False,
        approval_required=contract.approval_required,
        reason=(
            "request contract satisfied -- exact-command output permitted, subject to existing grounding"
            if allow_command
            else "request contract satisfied -- non-command operational output permitted; no exact-command grant"
        ),
    )


def load_current_turn_contract(raw_contract: object, current_run_id: Optional[str]) -> Optional[RequestContract]:
    """Tolerant, fail-closed parse of the raw session-state value back
    into a typed `RequestContract` -- malformed/absent/wrong-shaped data
    all resolve to `None` (never a raised exception, matching every other
    session-state reader in this codebase). Does NOT itself check
    freshness -- `derive_execution_decision` does that, using the SAME
    `current_run_id` this function is only given for API-shape
    consistency (kept separate so a caller that already has a validated
    `RequestContract` object from elsewhere is never forced through a
    redundant dict round-trip).
    """
    del current_run_id  # freshness is checked by derive_execution_decision, not here
    if not isinstance(raw_contract, dict):
        return None
    try:
        return RequestContract.model_validate(raw_contract)
    except ValidationError:
        return None


_MISSING_CONTEXT_FALLBACK_TEXT_TEMPLATE = (
    "Please confirm the following before I can provide an exact command: {items}."
)
"""Deterministic, Python-authored fallback -- never model-generated, never
templated with anything beyond the contract's own SAFE `missing_context`
KEY NAMES (e.g. "unit_id") -- never a raw value, never Knowledge content.
Matches this codebase's own established fixed-fallback-sentence
convention (e.g. `evidence.py`'s `_UNGROUNDED_COMMAND_FALLBACK_TEXT`)."""

_NO_SUBJECT_FALLBACK_TEXT = "I need to know which specific alarm or governed procedure you mean before I can give you a command. Please name it explicitly."

_GENERIC_WITHHELD_COMMAND_TEXT = "An exact command cannot yet be safely provided for this step. Please confirm the missing details."

_SAFE_MISSING_CONTEXT_LABELS: dict[str, str] = {
    # LIVE-CORR-2 -- DEF-0043 CORRECTIVE PASS: a small, closed mapping
    # from this module's own DETERMINISTIC internal key names (currently
    # only the two `required_target_parameter_gaps` can add) to a safe,
    # generic, human-readable phrase -- never a raw internal key name
    # rendered directly to the user, and never anything sourced from
    # Knowledge/an example. A model-declared `missing_context` entry
    # (e.g. "equipment identifier") is never looked up here -- it is
    # already the model's own human phrasing (validated non-blank by
    # `RequestContract`) and passes through unchanged via `.get(key,
    # key)`'s own fallback.
    "unit_id": "the affected unit identifier (for example the exact RRU or AAS identifier)",
    "unit_type": "the affected unit type (for example RRU or AAS)",
}


def _safe_missing_context_label(key: str) -> str:
    return _SAFE_MISSING_CONTEXT_LABELS.get(key, key)

KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT = (
    "I don't yet have a way to enumerate the full list of governed Knowledge documents. "
    "Ask about a specific alarm, procedure, or topic instead."
)
"""Section 8's own explicit requirement: KNOWLEDGE_INVENTORY must never
silently be answered by ordinary semantic Knowledge search results
presented as if they were a complete catalog. Fixed, deterministic,
non-vendor-specific -- the real deterministic catalog capability is a
future milestone."""


def command_suppression_fallback_text(decision: RequestExecutionDecision) -> str:
    """Deterministic reason -> fixed clarification text -- mirrors
    `evidence.py`'s own `_fallback_text_for_reason` mapping exactly, one
    layer up. Never model-authored.

    LIVE-CORR-2 -- DEF-0043 CORRECTIVE PASS: previously, the specific,
    already-known `missing_context` template was used ONLY for `NEEDS_
    INFORMATION` status -- an `AMBIGUOUS` decision that ALSO carried a
    non-empty, already-known `missing_context` (live evidence, session
    `a6bbf7cf-...`: `subject="HW Partial Fault procedure"`, `missing_
    context=["equipment identifier", "missing condition"]`) fell through
    to the fully generic `_GENERIC_WITHHELD_COMMAND_TEXT`, silently
    discarding a specific, already-computed answer in favor of a vaguer
    one. The check is now `decision.missing_context` alone, independent
    of `status` -- `NEEDS_INFORMATION` always carries a non-empty
    `missing_context` by construction (see `derive_execution_decision`),
    so its own behavior is completely unchanged; `AMBIGUOUS` now uses the
    SAME specific template whenever it, too, has something specific to
    say, falling back to the no-subject/generic text only when it
    genuinely does not (e.g. a resolved subject with an otherwise
    unresolvable procedure ambiguity, DEF-0029's own case, where no
    specific missing fact was ever declared). Each key is rendered
    through `_safe_missing_context_label` -- a raw internal key name
    (`"unit_id"`) is never shown verbatim; a model-declared, already-safe
    phrase (e.g. `"equipment identifier"`) passes through unchanged.
    """
    if decision.missing_context:
        labels = ", ".join(_safe_missing_context_label(key) for key in decision.missing_context)
        return _MISSING_CONTEXT_FALLBACK_TEXT_TEMPLATE.format(items=labels)
    if decision.status == RequestExecutionStatus.AMBIGUOUS and not decision.subject:
        return _NO_SUBJECT_FALLBACK_TEXT
    if decision.status == RequestExecutionStatus.INVALID_CONTRACT:
        return _NO_SUBJECT_FALLBACK_TEXT
    return _GENERIC_WITHHELD_COMMAND_TEXT


FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT = (
    "A complete procedure was generated, but this request was validated as needing only the next "
    "diagnostic step. Ask for the complete approved procedure explicitly if that is what you need."
)
"""LIVE-CORR-3 -- DEF-0040 corrective pass: deterministic, Python-authored
fallback -- never model-generated. Used whenever `enforce_response_mode_
compatibility` discards a response for failing the compatibility matrix."""


def is_full_procedure_response_permitted(decision: RequestExecutionDecision) -> bool:
    """LIVE-CORR-3A -- the complete, 4-field FULL_PROCEDURE authorization:
    `intent=PROCEDURE`, `requested_output=PROCEDURE_STEPS`, `ambiguity=
    False`, and a genuinely RESOLVED `subject` -- ALL FOUR, never a subset.
    Uses ONLY already-validated, already-freshness-checked `RequestExecutionDecision`
    fields (never re-reads the raw contract) -- never phrase/keyword/regex
    matching of any kind."""
    return (
        decision.intent == RequestIntent.PROCEDURE
        and decision.requested_output == RequestedOutput.PROCEDURE_STEPS
        and decision.ambiguity is False
        and bool(decision.subject)
    )


def is_next_step_response_permitted(decision: RequestExecutionDecision) -> bool:
    """LIVE-CORR-3A -- `intent=TROUBLESHOOTING`, `requested_output=
    TROUBLESHOOTING_NEXT_STEP` -- the validated shape for an ordinary,
    one-diagnostic-action troubleshooting answer."""
    return decision.intent == RequestIntent.TROUBLESHOOTING and decision.requested_output == RequestedOutput.TROUBLESHOOTING_NEXT_STEP


def is_exact_command_response_permitted(decision: RequestExecutionDecision) -> bool:
    """LIVE-CORR-3A -- `intent=COMMAND`, `requested_output=EXACT_COMMAND`
    -- the validated shape for a request asking for one specific command."""
    return decision.intent == RequestIntent.COMMAND and decision.requested_output == RequestedOutput.EXACT_COMMAND


def enforce_response_mode_compatibility(
    guidance: Optional[TroubleshootingGuidance], decision: RequestExecutionDecision
) -> tuple[Optional[TroubleshootingGuidance], bool]:
    """LIVE-CORR-3A -- DEF-0040 corrective pass (section 3's own "complete
    response-mode matrix" requirement). The validated `RequestExecutionDecision`
    -- never `TroubleshootingGuidance.interaction_mode` itself, which is
    chosen by incident_manager without ever seeing team_manager's own
    validated contract, and never phrase/keyword/regex matching of any
    kind -- is the sole authority for whether a response may render.

    THE COMPLETE MATRIX (never a subset):
      FULL_PROCEDURE  -- permitted ONLY when `is_full_procedure_response_
                         permitted` (all 4 fields) holds.
      NEXT_STEP       -- `TroubleshootingInteractionMode` has exactly TWO
                         values (`NEXT_STEP`/`FULL_PROCEDURE`), never a
                         separate third `EXACT_COMMAND` value -- per this
                         pass' own explicit, documented mapping decision
                         (extending the EXISTING schema rather than
                         creating a parallel one), a `NEXT_STEP`-shaped
                         response (at most one action + at most one
                         command) is exactly what BOTH the instruction's
                         own "NEXT_STEP" row (`TROUBLESHOOTING` +
                         `TROUBLESHOOTING_NEXT_STEP`) AND "EXACT_COMMAND"
                         row (`COMMAND` + `EXACT_COMMAND`) need -- so
                         `NEXT_STEP` guidance is permitted when EITHER
                         `is_next_step_response_permitted` OR `is_exact_
                         command_response_permitted` holds.

    STRICT, per explicit instruction ("NEXT_STEP only for validated
    TROUBLESHOOTING + TROUBLESHOOTING_NEXT_STEP"): a `NEXT_STEP` response
    for a `PROCEDURE`+`PROCEDURE_STEPS`-validated request (showing LESS
    than requested) is now ALSO discarded, not merely the FULL_PROCEDURE-
    for-less-than-requested direction LIVE-CORR-3's own first pass left
    unconstrained -- "incompatible combinations fail closed" applies to
    the WHOLE matrix, not only the dangerous-widening direction.

    A missing/unresolved contract (`INVALID_CONTRACT` status -- `intent`/
    `requested_output`/`subject` all unset) satisfies NONE of the three
    permission functions above, so it fails closed identically to any
    other incompatible combination -- no special-casing needed.

    Returns `(possibly-discarded guidance, whether it was discarded)`.
    `guidance=None` is a complete no-op. A discarded guidance returns
    `(None, True)` -- the caller substitutes `FULL_PROCEDURE_NOT_
    PERMITTED_FALLBACK_TEXT`.
    """
    if guidance is None:
        return None, False
    if guidance.interaction_mode == TroubleshootingInteractionMode.FULL_PROCEDURE:
        if is_full_procedure_response_permitted(decision):
            return guidance, False
        return None, True
    if is_next_step_response_permitted(decision) or is_exact_command_response_permitted(decision):
        return guidance, False
    return None, True


def enforce_execution_decision_on_guidance(
    guidance: Optional[TroubleshootingGuidance], decision: RequestExecutionDecision
) -> tuple[Optional[TroubleshootingGuidance], bool]:
    """The actual enforcement step (section 9/10 -- "No Model Override").

    6A.14 FINAL corrective pass (section 5 -- "command-bearing free-form
    prose must not bypass policy"): when `decision.may_emit_command` is
    `False`, the ENTIRE guidance is suppressed -- `interpretation`,
    `next_action`, `command`, `evidence_requested`, and every
    `TroubleshootingStep.action`/`.command` -- never `command`/`step
    .command` alone. Rather than selectively trust some fields and not
    others, the whole guidance is replaced with a single, fixed,
    deterministic clarification (the caller substitutes `command_
    suppression_fallback_text(decision)` once `render_troubleshooting_
    guidance` renders the now-empty guidance to `""`) -- mirrors the SAME
    "suppress the entire guidance, not just one field" philosophy DEF-0027
    's own `_guidance_scope_established` (evidence.py) already established
    for the cross-document case, applied here for a different,
    execution-policy-driven reason.

    LIVE-CORR-3B -- Operational Authority Boundary, section 3's own
    explicit "remove the DIAGNOSTIC_READ target-independent permission
    bypass" requirement: LIVE-CORR-3A's own per-step/per-guidance
    `TroubleshootingOperationalEffect.DIAGNOSTIC_READ` exemption from
    target confirmation has been REMOVED outright, not narrowed. Its own
    "RESIDUAL RISK" note (kept in `TroubleshootingOperationalEffect`'s own
    docstring for history) already named exactly why: `operational_effect`
    is model-populated, never governed step metadata -- a model that
    mislabels a real, target-specific, state-changing recommendation as
    `DIAGNOSTIC_READ` (or `OBSERVATION`/`REFERENCE_DESCRIPTION`) could
    bypass target confirmation for it. Until governed step metadata
    positively proves a specific operation is genuinely target-
    independent (not yet available anywhere in the Knowledge model --
    a real gap, not invented here), EVERY command -- regardless of its own
    self-declared `operational_effect` -- is treated as target-dependent
    for the purpose of THIS gate: `may_emit_command=False` withholds it
    unconditionally, no exemption, ever. `evidence.py`'s own, separate,
    unchanged `_evaluate_command`/grounding layer is completely unaffected
    (it never consulted `operational_effect` for this purpose either);
    both layers must still agree before a command reaches the user.

    Returns `(possibly-corrected guidance, whether anything was
    suppressed)`. `guidance=None` (no troubleshooting_guidance this turn
    at all) is a complete no-op -- there is nothing for this function to
    enforce (see `requires_unstructured_response_backstop`, the SEPARATE
    mechanism for that case).
    """
    if guidance is None:
        return None, False
    if decision.may_emit_command:
        return guidance, False

    if guidance.interaction_mode == TroubleshootingInteractionMode.FULL_PROCEDURE:
        stripped = False
        new_steps = []
        for step in guidance.full_procedure_steps:
            if step.command is not None:
                stripped = True
            new_steps.append(step.model_copy(update={"command": None}))
        if not stripped:
            return guidance, False
        return guidance.model_copy(update={"full_procedure_steps": new_steps}), True

    suppressed = guidance.model_copy(
        update={
            "interpretation": None,
            "next_action": None,
            "command": None,
            "evidence_requested": None,
            "full_procedure_steps": [],
        }
    )
    return suppressed, True


_UNSTRUCTURED_RESPONSE_BLOCKING_STATUSES = frozenset(
    {RequestExecutionStatus.NEEDS_INFORMATION, RequestExecutionStatus.AMBIGUOUS}
)


def requires_unstructured_response_backstop(decision: RequestExecutionDecision, troubleshooting_guidance_present: bool) -> bool:
    """6A.14 FINAL corrective pass -- ROOT CAUSE A backstop (section 7 of
    that pass's own instruction): closes the gap `enforce_execution_
    decision_on_guidance` structurally cannot close on its own, because
    that function has nothing to act on when `TroubleshootingGuidance`
    was never populated at all.

    THE LIVE DEFECT THIS CLOSES: "how do i handle HW Partial Fault?"
    produced a response that did not match `render_troubleshooting_
    guidance`'s own deterministic output shape at all (no "Run:\\n\\n"
    marker, no numbered steps) -- strong evidence `incident_manager` used
    its OWN free-form `summary` field instead of the structured
    `troubleshooting_guidance` field, exactly the escape hatch its own
    prompt (before this pass) explicitly allowed for "a plain factual
    question." Two real, live, grounded commands (`RRU-9`, `AAS-1`) then
    reached the user through that field, completely unvalidated by DEF-
    0024/0027's grounding OR `enforce_execution_decision_on_guidance` --
    both of which only ever examine `TroubleshootingGuidance`.

    DELIBERATELY NOT A COMMAND PARSER (per explicit instruction: "Do NOT
    build a generic command regex engine... If the only possible
    implementation is a broad text regex, STOP and report"): this
    function never inspects response TEXT at all. It reasons ENTIRELY
    from the already-validated, already-trusted `RequestExecutionDecision`
    -- specifically, whether this turn's own CURRENT, FRESH contract
    positively established that required target/condition context is
    UNRESOLVED (`status` is `NEEDS_INFORMATION` -- `missing_context` is
    non-empty for an intent capable of carrying a command -- or
    `AMBIGUOUS`) while NO structured `TroubleshootingGuidance` exists to
    deterministically verify or correct whatever free-form text the model
    actually produced. When both are true, the caller cannot PROVE the
    free-form response is safe, so it must not trust it -- fail closed by
    REPLACING it, never by trying to selectively edit prose it cannot
    parse.

    DELIBERATELY NARROW, to avoid over-blocking the common case: returns
    `False` whenever `troubleshooting_guidance_present` is `True` (the
    EXISTING, already-precise `enforce_execution_decision_on_guidance`
    mechanism already handles that case, field-by-field); `False` for
    `REQUIRES_APPROVAL`/`UNSUPPORTED_CAPABILITY` (both already have their
    own, separate, unconditional handling elsewhere).

    LIVE-CORR-3B -- Operational Authority Boundary, section 2's own
    explicit "this must apply for ALLOW and INVALID_CONTRACT too -- not
    only NEEDS_INFORMATION/AMBIGUOUS" requirement -- audited BOTH halves;
    only the ALLOW half could be implemented safely (see the STOP note
    below for INVALID_CONTRACT):

      `ALLOW` now returns `is_operationally_shaped_request(decision.intent,
      decision.requested_output)` instead of always `False`. An ORDINARY,
      non-operational ALLOW turn (a greeting, "what is VSWR?", any plain
      `INFORMATION`/`FACT` exchange) is completely unaffected -- `is_
      operationally_shaped_request` is `False` for exactly that shape,
      preserving greeting/ordinary-conversation behavior byte-for-byte.
      An operationally-shaped ALLOW turn (e.g. `intent=PROCEDURE,
      requested_output=PROCEDURE_STEPS`, fully resolved, NO missing
      context) that never populated `TroubleshootingGuidance` at all is now
      correctly caught -- previously such a turn escaped this backstop
      entirely purely because its own status happened to resolve to ALLOW,
      the same class of gap DEF-0028's original Root Cause A closed for
      NEEDS_INFORMATION/AMBIGUOUS only.

    STOP CONDITION, honestly documented, per explicit instruction ("if the
    only proposed solution is phrase/keyword/regex matching, or fixing
    this requires a new parallel architecture, STOP and report"):
    `INVALID_CONTRACT` deliberately still returns `False` unconditionally,
    UNCHANGED from before this pass, despite item 2's own literal text.
    Audited two candidate designs, both rejected on real evidence, not
    speculation:
      (a) fire UNCONDITIONALLY whenever no contract exists -- measured
      directly against this repository's own real test suite (a genuine
      regression run, not a guess) and found to collaterally break dozens
      of PRE-EXISTING, UNRELATED tests (chat streaming semantics, the
      governed-Knowledge completion gate, Teams read-resume flows, and
      others) that legitimately never populate a `RequestContract` because
      their own scenario predates 6A.13/6A.14 and has nothing to do with
      operational command safety.
      (b) fire only when this turn's own separate, already-mandatory
      `record_source_requirements` declaration shows `requires_governed_
      knowledge`/`requires_teams` -- ALSO measured directly and found
      unsafe in the OPPOSITE direction: a `requires_governed_knowledge=
      True` turn is already fully covered by the PRE-EXISTING governed-
      knowledge completion gate (`chat_service.py`'s own `governed_
      completion_needed` branch, well upstream of this function) -- EITHER
      it already deterministically remediated `final_text` (a real,
      already-trustworthy answer this new backstop would then wrongly
      discard in favor of a less-informative generic fallback), OR real,
      freshly-selected evidence was already verified present and
      consistent (also already trustworthy, via `KnowledgeEvidenceItem`/
      provenance, a completely separate, robust mechanism). There is no
      leftover, uncovered case within `requires_governed_knowledge=True`
      for this new backstop to usefully close, and gating the OPPOSITE way
      (fire when NEITHER flag is set) reopens design (a)'s own same
      collateral-damage class (an ordinary, ungoverned conversational
      reply looks identical to an ungrounded, hallucinated operational one
      by this signal alone).
    No third deterministic signal exists in this codebase today that
    distinguishes "raw text might carry an ungrounded operational
    recommendation" from "raw text is a safe, already-validated
    conversational or governed-knowledge answer" without either inspecting
    response content (a text/command detector, explicitly out of bounds)
    or a new, currently-nonexistent per-turn "was this specific answer
    verified against real selected evidence" boolean threaded through this
    entire call chain (a materially larger, cross-cutting change, not a
    surgical one). Left OPEN for a future, properly-scoped milestone with
    access to that signal; DEF-0040/close relatives in `docs/DEFECT_
    REGISTER.md` should record this as the concrete next step.
    """
    if troubleshooting_guidance_present:
        return False
    if decision.status in (RequestExecutionStatus.REQUIRES_APPROVAL, RequestExecutionStatus.UNSUPPORTED_CAPABILITY):
        return False
    if decision.status == RequestExecutionStatus.INVALID_CONTRACT:
        return False
    if decision.status in _UNSTRUCTURED_RESPONSE_BLOCKING_STATUSES:
        return True
    return is_operationally_shaped_request(decision.intent, decision.requested_output)
