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

from backend.agents.incident_manager.schemas import TroubleshootingGuidance
from backend.agents.team_manager.request_contract import (
    TARGET_SPECIFIC_INTENTS,
    RequestContract,
    RequestIntent,
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

# Intents whose output can carry a live, target-specific operational
# command -- the missing-context/no-subject gates apply to ALL of these
# (6A.14 FINAL corrective pass -- ROOT CAUSE B: the first version of this
# policy scoped the gate to COMMAND/TROUBLESHOOTING only, reasoning that
# PROCEDURE/INFORMATION "never carry a live command." A real live defect
# proved that reasoning wrong: "how do i handle HW Partial Fault?" -- a
# request just as plausibly classified PROCEDURE or INFORMATION as
# TROUBLESHOOTING -- produced a governed, multi-branch answer containing
# BOTH the real RRU-9 and real AAS-1 commands, with neither unit
# confirmed by the user. Section 3's own governing principle: "Intent
# controls desired response shape. Intent must NOT be usable as a bypass
# around command/target safety." KNOWLEDGE_INVENTORY and ACTION are
# deliberately EXCLUDED -- they are governed by their own, separate,
# earlier-checked branches in `derive_execution_decision` (ACTION always
# forces REQUIRES_APPROVAL before this set is ever consulted;
# KNOWLEDGE_INVENTORY always forces UNSUPPORTED_CAPABILITY the same way).
#
# 6A.14 Request Parameter Consistency & Identifier Normalization: this
# set now lives in `request_contract.py` as the public `TARGET_SPECIFIC_
# INTENTS` (imported above) -- ONE definition, reused by both that
# module's own `required_target_parameter_gaps` reconciliation and this
# module's own gate below, never two independently-drifting copies. Kept
# as a local alias so every existing reference in this file needs no
# further change.
_TARGET_SPECIFIC_INTENTS = TARGET_SPECIFIC_INTENTS


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

    if contract.intent in _TARGET_SPECIFIC_INTENTS and not contract.subject:
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

    if contract.intent in _TARGET_SPECIFIC_INTENTS:
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

    return RequestExecutionDecision(
        status=RequestExecutionStatus.ALLOW,
        intent=contract.intent,
        requested_output=contract.requested_output,
        subject=contract.subject,
        may_emit_command=True,
        may_execute_action=False,  # never this policy's job to authorize an actual write
        may_emit_operational_steps=True,
        missing_context=list(contract.missing_context),
        ambiguity=False,
        approval_required=contract.approval_required,
        reason="request contract satisfied -- command/operational output permitted, subject to existing grounding",
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
    layer up. Never model-authored."""
    if decision.status == RequestExecutionStatus.NEEDS_INFORMATION and decision.missing_context:
        return _MISSING_CONTEXT_FALLBACK_TEXT_TEMPLATE.format(items=", ".join(decision.missing_context))
    if decision.status == RequestExecutionStatus.AMBIGUOUS and not decision.subject:
        return _NO_SUBJECT_FALLBACK_TEXT
    if decision.status == RequestExecutionStatus.INVALID_CONTRACT:
        return _NO_SUBJECT_FALLBACK_TEXT
    return _GENERIC_WITHHELD_COMMAND_TEXT


def enforce_execution_decision_on_guidance(
    guidance: Optional[TroubleshootingGuidance], decision: RequestExecutionDecision
) -> tuple[Optional[TroubleshootingGuidance], bool]:
    """The actual enforcement step (section 9/10 -- "No Model Override").

    6A.14 FINAL corrective pass (section 5 -- "command-bearing free-form
    prose must not bypass policy"): when `decision.may_emit_command` is
    `False`, the ENTIRE guidance is suppressed -- `interpretation`,
    `next_action`, `command`, `evidence_requested`, and every
    `TroubleshootingStep.action`/`.command` -- never `command`/`step
    .command` alone. The first version of this function stripped only
    the two structured command fields, leaving narrative fields
    untouched; nothing in this codebase can verify, without the
    explicitly-forbidden broad text parser, that a command string was
    not ALSO embedded in `next_action`/`interpretation`/a step's own
    `action` while `command` itself was correctly left unset -- e.g. "Use
    the command accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1" typed
    directly into `next_action`. Rather than selectively trust some
    fields and not others, the whole guidance is replaced with a single,
    fixed, deterministic clarification (the caller substitutes
    `command_suppression_fallback_text(decision)` once `render_
    troubleshooting_guidance` renders the now-empty guidance to `""`) --
    mirrors the SAME "suppress the entire guidance, not just one field"
    philosophy DEF-0027's own `_guidance_scope_established` (evidence.py)
    already established for the cross-document case, applied here for a
    different, execution-policy-driven reason.

    Both this layer AND `evidence.py`'s own, separate, still-fully-active
    DEF-0024/0027 grounding must agree before a command reaches the user
    -- neither replaces the other.

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
    mechanism already handles that case, field-by-field); `False`
    whenever `decision.status` is `ALLOW` (the ordinary, fully-resolved
    case -- e.g. "what is VSWR?" with `missing_context=[]` -- is
    completely unaffected, regardless of whether a contract exists at
    all); `False` for `INVALID_CONTRACT` (a turn with NO contract at all
    is a materially different, lower-confidence signal than a turn with a
    contract that POSITIVELY shows unresolved context -- deliberately not
    conflated, to avoid this NEW backstop firing on every ordinary
    governed-knowledge turn merely because `record_request_contract`
    was not called); `False` for `REQUIRES_APPROVAL`/`UNSUPPORTED_
    CAPABILITY` (both already have their own, separate, unconditional
    handling elsewhere).
    """
    if troubleshooting_guidance_present:
        return False
    return decision.status in _UNSTRUCTURED_RESPONSE_BLOCKING_STATUSES
