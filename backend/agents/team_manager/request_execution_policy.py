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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingOperationalEffect,
)
from backend.knowledge.domain.operation_descriptor import GovernedOperationDescriptor
from backend.agents.team_manager.request_contract import (
    PENDING_GOVERNED_REQUEST_STATE_KEY,
    TARGET_IDENTIFIER_PARAMETER_NAME,
    TARGET_TYPE_PARAMETER_NAME,
    PendingGovernedRequest,
    PendingGovernedRequestStatus,
    PendingRequestRelationship,
    RequestClass,
    RequestContract,
    RequestedOutput,
    RequestIntent,
    RequestParameter,
    RequestScope,
    authoritative_missing_context_names,
    derive_request_class,
    is_operationally_shaped_request,
    request_scope,
    required_target_parameter_gaps,
)


def _resolve_request_class(
    request_class: Optional[str], intent: Optional[str], requested_output: Optional[str], subject: Optional[str] = None
) -> Optional[str]:
    """LIVE-CORR-8 -- defensive fallback, used everywhere this module
    reads a `request_class`. A REAL, persisted `RequestContract` always
    has `request_class` populated by `validate_and_persist_request_
    contract` (request_contract.py) -- but a `RequestContract`/
    `RequestExecutionDecision` constructed DIRECTLY (every pre-existing
    test in this codebase's own suite that predates this milestone, and
    any future caller that does the same) may leave it unset. Re-derives
    it, on the fly, from the exact SAME pure `derive_request_class`
    function whenever it is `None` -- byte-identical to what persistence
    would have computed from the SAME `intent`/`requested_output`, so a
    caller that never set it explicitly behaves IDENTICALLY to one that
    did; this is never a second, independently-drifting classification.
    `action_requested` is not available on `RequestExecutionDecision`
    (only `intent`/`requested_output`/`subject` are) -- passed as `False`
    here, which only affects the `ACTION` class, itself irrelevant to
    every call site in this module that needs this fallback (all three
    are checked well after `derive_execution_decision`'s own separate,
    earlier, unconditional ACTION branch has already run). `None` `intent`
    or `requested_output` (e.g. `INVALID_CONTRACT`) returns `None`
    unchanged -- nothing to derive from.
    """
    if request_class is not None:
        return request_class
    if intent is None or requested_output is None:
        return None
    return derive_request_class(intent, requested_output, False, subject)


class PendingContinuationRelation:
    """POST-6A REPAIR 4 -- the closed, deterministic outcome vocabulary of
    `resolve_pending_continuation`, below. Distinct from `PendingRequest
    Relationship` (request_contract.py), which is only ever the MODEL's
    own proposal: this is what the runtime actually concluded.

    NONE       -- there is no pending governed request to relate to.
    ANSWERS    -- this turn deterministically answers/corrects it; the
                  pending governance identity is inherited.
    NEW        -- this turn is a genuinely separate request; the pending
                  record is simply not inherited (it is still rewritten
                  from this turn's own outcome, exactly as before).
    CANCELLED  -- the user explicitly withdrew the pending request.
    UNRESOLVED -- this turn supplies something that BELONGS to the
                  pending request's own parameter vocabulary, but the
                  structural signals do not establish that it is
                  answering it, and the model did not disown it either.
                  Neither resuming nor silently dropping is safe, so the
                  runtime must ASK -- see `resolve_pending_continuation`.
    """

    NONE = "none"
    ANSWERS = "answers"
    NEW = "new"
    CANCELLED = "cancelled"
    UNRESOLVED = "unresolved"


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
    request_class: Optional[str] = None
    """LIVE-CORR-8 -- the authoritative governance class, propagated
    unchanged from the validated `RequestContract.request_class` (itself
    ALWAYS deterministically derived -- see `RequestContract.request_
    class`'s own docstring). `None` only for `INVALID_CONTRACT` (no
    contract to read a class from at all)."""
    intent: Optional[str] = None
    requested_output: Optional[str] = None
    subject: Optional[str] = None
    may_emit_command: bool = False
    may_execute_action: bool = False
    may_emit_operational_steps: bool = False
    missing_context: list[str] = Field(default_factory=list)
    ambiguity: bool = False
    approval_required: bool = False
    pending_relationship: str = PendingContinuationRelation.NONE
    """POST-6A REPAIR 4 -- what `resolve_pending_continuation` concluded
    about this turn's relationship to a still-pending governed request
    (`PendingContinuationRelation`). Observability plus exactly ONE
    behavioral consumer: `command_suppression_fallback_text` renders the
    dedicated relationship clarification for `UNRESOLVED`, instead of a
    missing-parameter question the user has no way to act on. Never
    itself a permission -- every `may_*` grant above is derived from the
    already-effective contract, unchanged."""
    pending_subject: Optional[str] = None
    """POST-6A REPAIR 4 -- the pending request's own `subject` (the
    user's own earlier operation, e.g. "restart RRU"), carried so the
    `UNRESOLVED` clarification can name what it is asking about.
    `None` whenever there is no pending request."""
    reason: str = ""


def _turn_requires_no_independent_lookup(contract: RequestContract) -> bool:
    """LIVE-CORR-11 CORRECTIVE PASS -- ISSUE C. Reuses two EXISTING
    `RequestContract` fields (part of the 6A.13 schema since before this
    milestone; never previously consulted by the pending-continuity
    mechanism) as the PRIMARY discriminator between "a bare value/fact
    answering an outstanding question" and "a self-contained new question
    that happens to mention a same-shaped parameter": `requires_governed_
    knowledge`/`requires_operational_context` are the model's own existing
    declaration of whether THIS turn, on its own, needs to consult
    governed Knowledge or live operational context to be answered.

    CONFIRMED LIVE SHAPE (LIVE-CORR-10's own audit): the real "the RRU is
    rru-3" clarification-answer turns show BOTH `False` -- a bare fact
    needs no lookup of its own. A genuinely independent question such as
    "what is the status of RRU-9?" plausibly needs one or the other (a
    live status check is an operational-context lookup) -- `True` for
    either flag means this turn is answering ITS OWN question, not the
    pending one, regardless of what parameter names it happens to
    mention.
    """
    return not contract.requires_governed_knowledge and not contract.requires_operational_context


def _relates_to_pending_vocabulary(contract: RequestContract, relevant_names: frozenset[str]) -> bool:
    """LIVE-CORR-11 CORRECTIVE PASS -- ISSUE C's own explicit "prove the
    supplied structured context is actually satisfying/refining the
    pending request" requirement. `relevant_names` is the pending
    record's own closed vocabulary for THIS check -- `missing_context`
    (what is still needed) for an `UNRESOLVED` record, or the record's
    own `provided_context` NAMES (what was already supplied, and could
    now be corrected) for a `COMPLETED` one -- never the user's raw text,
    never a new taxonomy. A supplied parameter whose NAME does not appear
    in either set (e.g. `vendor=ericsson` mentioned while a `restart RRU`
    request only ever cared about `unit_id`/`unit_type`) does not relate,
    regardless of any other signal."""
    provided_names = {param.name for param in contract.provided_context}
    return bool(provided_names & relevant_names)


def _subject_consistent_with_pending(contract_subject: Optional[str], pending_subject: Optional[str]) -> bool:
    """LIVE-CORR-12G -- deterministic, non-fuzzy subject consistency check:
    exact match, or one containing the other verbatim, case-insensitively
    (mirrors `governed_evidence_continuity._subject_matches_heading`'s own
    established discipline exactly -- never a new comparison idiom). Both
    values are already-validated `RequestContract.subject`/`PendingGoverned
    Request.subject` strings -- never raw user text, never a keyword/regex
    match. `None`/blank on either side is never consistent with anything
    (a missing subject proves nothing, positively or negatively)."""
    if not contract_subject or not pending_subject:
        return False
    left = contract_subject.strip().lower()
    right = pending_subject.strip().lower()
    if not left or not right:
        return False
    return left == right or left in right or right in left


def _supplies_context_for_pending_request(
    contract: RequestContract, relevant_names: frozenset[str], pending_subject: Optional[str] = None
) -> bool:
    """LIVE-CORR-11 -- section 8's own explicit "deterministic/structured
    criterion, never keyword/regex routing" requirement.

    LIVE-CORR-11 CORRECTIVE PASS -- ISSUE B/C. TRUE when signals 1, 2, and
    4 below all agree AND EITHER signal 3 holds OR the LIVE-CORR-12G
    subject-consistency bypass applies:

      1. the CURRENT turn's own resolved class is `OPERATIONAL_
         INFORMATION` specifically -- never `GENERAL_CONVERSATION` (a
         genuine "hello" must never be promoted into command governance),
         and never an already-resolved `EXACT_COMMAND`/`PROCEDURE_
         TROUBLESHOOTING`/`ACTION` turn (which needs no inheritance -- it
         already classified itself correctly). This is the EXACT proven
         defect shape (section 2's own live evidence: a value-answer turn
         reclassifies to `OPERATIONAL_INFORMATION`/`FACT`).
      2. `contract.provided_context` is non-empty -- the model supplied
         SOME provenance-VERIFIED parameter this turn (already survived
         `_verify_and_filter_provided_context`'s own real-text/session-
         confirmed check before this function ever sees it -- never an
         unchecked model claim). A pure new QUESTION ("what does a VSWR
         alarm mean?") supplies no parameter at all and is correctly
         excluded here, distinguishing it from a genuine clarification
         ANSWER without inspecting the user's raw text.
      3. `_turn_requires_no_independent_lookup` -- this turn is not
         ITSELF a self-contained request needing its own governed-
         Knowledge/operational-context lookup (ISSUE C's own core fix:
         "what is the status of RRU-9?" is excluded HERE, even though it
         may supply a same-named `unit_id` parameter).
      4. `_relates_to_pending_vocabulary` -- the supplied parameter NAME
         actually belongs to the pending record's own relevant vocabulary
         (ISSUE C's own second, independent protection: an unrelated
         mention, e.g. `vendor=ericsson`, is excluded even when the other
         signals all hold).

    LIVE-CORR-12G -- PROVEN LIVE GAP: a real reproduction showed signal 3
    alone incorrectly excluding a turn whose OWN `subject` was already an
    exact, deterministic match for the pending operation's own subject
    ("the RRU is RRU-3", `subject="restart RRU"`, answering a pending
    "restart RRU" `EXACT_COMMAND` request) merely because that turn's own
    (unreliable, model-set) `requires_governed_knowledge`/`requires_
    operational_context` flags happened to be `True` -- signal 3 cannot
    distinguish "this turn needs its own independent lookup because it is
    a NEW, self-contained question" from "this turn needs a lookup only
    because completing the SAME pending operation always would." Signal 3
    is no longer an unconditional requirement: `contract.continuation is
    True` AND `_subject_consistent_with_pending(contract.subject,
    pending_subject)` is now an ALTERNATE, equally sufficient path -- an
    explicit, deterministic subject match combined with the model's own
    continuation claim is at least as strong a relatedness signal as
    "needs no independent lookup" was always only an approximation of.
    Never gated on raw user text, never fuzzy -- `pending_subject` is
    `None` for every pre-existing caller (LIVE-CORR-11's own suite, and
    the `COMPLETED`-status call site), so this bypass is inert unless a
    caller deliberately supplies it, preserving all prior behavior byte-
    for-byte for every case that does not.

    LIVE-CORR-11 CORRECTIVE PASS -- ISSUE B: `contract.continuation` alone
    (previously the SOLE gate) was already proven insufficient -- live
    evidence showed the model can emit `continuation=False` for a genuine
    same-operation/new-target turn. It remains, as of LIVE-CORR-12G, one
    half of the narrow subject-consistency bypass above, never a gate on
    its own.

    Still NOT gated on `contract.subject` matching the pending request's
    own `subject` as an UNCONDITIONAL requirement -- LIVE-CORR-10's own
    finding (value-answer turns with a placeholder subject like `"RRU
    ID"`) remains valid, and signal 3 remains the PRIMARY path for that
    shape; subject-consistency is only ever an ADDITIONAL, alternate way
    to satisfy relatedness, never a replacement for signals 1/2/4.

    POST-6A REPAIR 4 -- SIGNAL 1 WIDENED TO INCLUDE `GENERAL_
    CONVERSATION`: a value-only clarification answer ("RRU-3", "it's
    rru-3") legitimately classifies `intent=information`/`requested_
    output=fact`/`subject=None`, which `derive_request_class` resolves to
    `GENERAL_CONVERSATION`, not `OPERATIONAL_INFORMATION` -- so the
    original class check silently DROPPED the pending objective for
    exactly the shape this whole mechanism exists to preserve. The
    original rationale for excluding it ("a genuine 'hello' must never be
    promoted into command governance") is fully carried by SIGNAL 2
    instead, which it always was: a greeting supplies no verified
    `provided_context` at all, so it can never reach the remaining
    signals. Every already-resolved operational class (`EXACT_COMMAND`/
    `PROCEDURE_TROUBLESHOOTING`/`ACTION`) stays excluded exactly as
    before -- such a turn classified itself correctly and needs no
    inheritance.
    """
    resolved_class = _resolve_request_class(contract.request_class, contract.intent, contract.requested_output, contract.subject)
    if resolved_class not in (RequestClass.OPERATIONAL_INFORMATION, RequestClass.GENERAL_CONVERSATION):
        return False
    if not contract.provided_context:
        return False
    if not _turn_requires_no_independent_lookup(contract) and not (
        contract.continuation and _subject_consistent_with_pending(contract.subject, pending_subject)
    ):
        return False
    return _relates_to_pending_vocabulary(contract, relevant_names)


def _merge_pending_provided_context(
    pending_provided_context: list["RequestParameter"], current_provided_context: list["RequestParameter"]
) -> list["RequestParameter"]:
    """Same "current turn's own verified value always wins, by parameter
    NAME" merge `validate_and_persist_request_contract`'s own `session_
    confirmed` carry-forward already uses (request_contract.py) --
    reapplied here, one layer up, for the pending-request store instead
    of the prior-turn-contract store. THE direct mechanism for section 9's
    "preserve parameter correction" requirement: "actually it is RRU-10"
    supplies a fresh, already-verified `unit_id` that overwrites the
    pending record's own stale `RRU-3`, never accumulates both."""
    merged: dict[str, "RequestParameter"] = {param.name: param for param in pending_provided_context}
    for param in current_provided_context:
        merged[param.name] = param
    return list(merged.values())


class PendingContinuationResolution(BaseModel):
    """POST-6A REPAIR 3 -- the ONE effective current request, resolved
    once from the raw extraction proposal plus existing pending state,
    and reused by every consumer that needs it (work permissions,
    governed-retrieval input, the authoritative prompt block, the final
    execution decision, and the pending-request continuity write).

    `contract` is the EFFECTIVE contract -- in-memory only, exactly as
    `resolve_effective_governed_contract` always was: the RAW, durable
    `VALIDATED_REQUEST_CONTRACT_STATE_KEY` contract is never rewritten by
    this resolution (see `PENDING_GOVERNED_REQUEST_STATE_KEY`'s own
    "never silently mutate history" note). Callers keep the raw
    extraction and this effective authority as separate values."""

    model_config = ConfigDict(frozen=True)

    relation: str
    contract: RequestContract
    pending_subject: Optional[str] = None


def _declared_relationship(contract: RequestContract) -> str:
    """The model's own declaration, defaulted defensively -- a contract
    built before this field existed (or by a test/caller that omits it)
    reads as `UNKNOWN`, i.e. "said nothing"."""
    declared = getattr(contract, "pending_request_relationship", None)
    return declared if isinstance(declared, str) and declared else PendingRequestRelationship.UNKNOWN


def _pending_relevant_names(pending_governed_request: PendingGovernedRequest) -> Optional[frozenset[str]]:
    """The pending record's own closed parameter vocabulary for
    relatedness checks -- extracted verbatim from `resolve_effective_
    governed_contract`'s own original inline derivation (ISSUE A), so
    both the ANSWERS path and the new UNRESOLVED path judge relatedness
    against the SAME set, never two drifting copies. `None` for a status
    this mechanism does not recognize."""
    pending_provided_names = frozenset(param.name for param in pending_governed_request.provided_context)
    if pending_governed_request.status == PendingGovernedRequestStatus.UNRESOLVED:
        # Still-needed keys ARE the primary vocabulary, but a real live
        # value-answer turn can re-supply an ALREADY-provided key's name
        # again (e.g. the model consistently tags an RRU identifier under
        # `unit_type` across multiple turns rather than `unit_id` -- a
        # separate, out-of-scope naming-accuracy quirk, LIVE-CORR-10's own
        # documented finding) -- included so that re-statement still
        # counts as relating to the SAME pending operation. This is never
        # what makes an UNRELATED mention (e.g. `vendor=ericsson`) match;
        # it only ever widens the ALREADY-narrow "known to this specific
        # operation" set, never opens it to an arbitrary parameter name.
        return frozenset(pending_governed_request.missing_context) | pending_provided_names
    if pending_governed_request.status == PendingGovernedRequestStatus.COMPLETED:
        return pending_provided_names
    return None


def _turn_is_self_contained_request(contract: RequestContract) -> bool:
    """POST-6A REPAIR 4 -- does THIS turn, on its own, already state a
    complete request of its own? Two already-validated, already-closed
    signals, either alone sufficient:

      - its resolved `request_class` is one of the operational classes a
        turn only ever reaches by classifying ITSELF operationally
        (`EXACT_COMMAND`/`PROCEDURE_TROUBLESHOOTING`/`ACTION`); or
      - it resolved a `subject` of its own -- a real topic/alarm/procedure
        the model could name for THIS message ("RRU status"), which a
        bare clarification answer ("rru-3") never has.

    This is what separates "a genuinely independent question that happens
    to mention a same-named parameter" (never worth interrupting the user
    over -- it is simply a new request) from "a fragment whose
    relationship to the pending request the runtime genuinely cannot
    determine" (which must ask). Never inspects raw user text.
    """
    resolved_class = _resolve_request_class(
        contract.request_class, contract.intent, contract.requested_output, contract.subject
    )
    if resolved_class in (RequestClass.EXACT_COMMAND, RequestClass.PROCEDURE_TROUBLESHOOTING, RequestClass.ACTION):
        return True
    return bool(contract.subject)


def resolve_pending_continuation(
    contract: RequestContract, pending_governed_request: Optional[PendingGovernedRequest]
) -> PendingContinuationResolution:
    """POST-6A REPAIR 3/4 -- the SINGLE resolution of "what is this turn
    actually asking, given what the runtime is still waiting on." Pure and
    deterministic: same inputs always produce the same effective request,
    so `chat_service.py` and `derive_execution_decision`/`derive_work_
    envelope` all agree without any of them having to pass the resolved
    value to each other.

    WHO GRANTS WHAT (the governing trust rule): continuation authority is
    granted ONLY by `_supplies_context_for_pending_request`'s own
    deterministic structural signals. The model's own `pending_request_
    relationship` declaration can never resume anything on its own -- it
    can only DROP pending authority (`CANCELS_PENDING`/`NEW_REQUEST`,
    both fail-safe directions) or break a tie the structural signals
    genuinely cannot decide.

    OUTCOMES, in fixed order:

      1. No pending record, or one that is not `EXACT_COMMAND`-class, or
         one with an unrecognized status -> `NONE`, contract unchanged.
      2. The model declared `CANCELS_PENDING` -> `CANCELLED`, contract
         unchanged. Honored directly: withdrawing a request only ever
         removes authority. The caller clears the pending record.
      3. The structural signals establish this turn answers/corrects the
         pending request -> `ANSWERS`, and the effective contract
         inherits the pending governance identity exactly as
         `resolve_effective_governed_contract` always did.
      4. The model declared `NEW_REQUEST` -> `NEW`, contract unchanged.
         This is the explicit escape hatch for a genuinely independent
         question that happens to mention a same-named parameter ("what
         is the status of RRU-9?" while a `restart RRU` request is
         pending) -- it drops pending authority, never grants any.
      5. Otherwise, if this turn supplies a verified parameter whose NAME
         belongs to the pending request's own vocabulary, step 3's
         signals did NOT agree, AND the turn is not itself a self-
         contained request (`_turn_is_self_contained_request` -- it
         resolved no subject and no operational class of its own)
         -> `UNRESOLVED`. Resuming would be the
         "an unrelated identifier mention silently resumes an old
         operation" failure; silently ignoring it would be the "the
         pending objective is lost" failure. Neither is acceptable, so
         the effective contract is rendered deliberately AMBIGUOUS while
         RETAINING the pending request's own identity/parameters -- which
         routes the turn, through the EXISTING, unmodified ambiguity
         branch of `derive_execution_decision`, to a deterministic
         clarification, and (through `build_pending_governed_request_
         state_update`, also unmodified) keeps the pending record alive
         so the user's answer can still resume it next turn. Note that
         the current turn's OWN supplied parameters are deliberately NOT
         merged in here: an unresolved relationship must never adopt a
         value as if it had answered the pending request.
      6. Otherwise -> `NEW`, contract unchanged (the ordinary,
         overwhelmingly common case: an unrelated turn that shares no
         parameter vocabulary with the pending request at all).
    """
    if pending_governed_request is None or pending_governed_request.request_class != RequestClass.EXACT_COMMAND:
        return PendingContinuationResolution(relation=PendingContinuationRelation.NONE, contract=contract)

    pending_subject = pending_governed_request.subject
    declared = _declared_relationship(contract)

    if declared == PendingRequestRelationship.CANCELS_PENDING:
        return PendingContinuationResolution(
            relation=PendingContinuationRelation.CANCELLED, contract=contract, pending_subject=pending_subject
        )

    relevant_names = _pending_relevant_names(pending_governed_request)
    if relevant_names is None:
        return PendingContinuationResolution(relation=PendingContinuationRelation.NONE, contract=contract)

    if _supplies_context_for_pending_request(contract, relevant_names, pending_subject):
        # POST-6A -- CONFIRMATION IS NOT GRANTED HERE.
        #
        # An earlier cut upgraded a parameter to `CONFIRMED` right here,
        # whenever its NAME appeared in the pending request's
        # `missing_context`. That is still an inference over message
        # content: it cannot tell whether the outstanding question is
        # still about the same operation, whether the governed operation
        # has been edited since, or whether the value has since been
        # corrected. All three are real ways for an answer to become
        # stale between being given and being used.
        #
        # Confirmation is now a bound RECORD, created and re-verified by
        # `target_confirmation.py` against the pending request, the
        # operation and the candidate revision. This function's job is
        # only to resolve WHICH request is effective; parameters keep
        # whatever confirmation state they legitimately carry.
        merged_provided_context = _merge_pending_provided_context(
            pending_governed_request.provided_context, contract.provided_context
        )
        effective = contract.model_copy(
            update={
                "intent": pending_governed_request.intent,
                "requested_output": RequestedOutput.EXACT_COMMAND,
                "subject": pending_subject,
                "provided_context": merged_provided_context,
                "missing_context": list(pending_governed_request.missing_context),
                "request_class": RequestClass.EXACT_COMMAND,
                "ambiguity": False,
            }
        )
        return PendingContinuationResolution(
            relation=PendingContinuationRelation.ANSWERS, contract=effective, pending_subject=pending_subject
        )

    if declared == PendingRequestRelationship.NEW_REQUEST:
        return PendingContinuationResolution(
            relation=PendingContinuationRelation.NEW, contract=contract, pending_subject=pending_subject
        )

    if _relates_to_pending_vocabulary(contract, relevant_names) and not _turn_is_self_contained_request(contract):
        unresolved = contract.model_copy(
            update={
                "intent": pending_governed_request.intent,
                "requested_output": RequestedOutput.EXACT_COMMAND,
                "subject": pending_subject,
                # Deliberately the PENDING record's own already-verified
                # parameters, never this turn's -- an unresolved
                # relationship must not adopt the mentioned value.
                "provided_context": list(pending_governed_request.provided_context),
                "missing_context": list(pending_governed_request.missing_context),
                "request_class": RequestClass.EXACT_COMMAND,
                "ambiguity": True,
            }
        )
        return PendingContinuationResolution(
            relation=PendingContinuationRelation.UNRESOLVED, contract=unresolved, pending_subject=pending_subject
        )

    return PendingContinuationResolution(
        relation=PendingContinuationRelation.NEW, contract=contract, pending_subject=pending_subject
    )


def resolve_effective_governed_contract(
    contract: RequestContract, pending_governed_request: Optional[PendingGovernedRequest]
) -> RequestContract:
    """LIVE-CORR-11 -- section 6/7's own "execution-policy authority must
    use a deterministic effective governed request" requirement. Returns
    an IN-MEMORY-ONLY `RequestContract` (never persisted -- the REAL,
    durable `VALIDATED_REQUEST_CONTRACT_STATE_KEY` contract, and every
    field team_manager's own model actually declared this turn, is left
    completely untouched by this function; see `PENDING_GOVERNED_
    REQUEST_STATE_KEY`'s own docstring's "never silently mutate history"
    note) reflecting the EFFECTIVE governance this turn's execution
    decision must be computed from.

    A no-op (`contract` returned unchanged) unless BOTH a pending governed
    request exists AND `_supplies_context_for_pending_request` positively,
    deterministically establishes this turn is answering/correcting it.
    `intent`/`requested_output`/`subject`/`request_class` are overridden
    to the PENDING request's own values (restoring the governance class/
    answer shape/semantic operation a value-only clarification turn does
    not itself re-declare); `provided_context` is the deterministic
    pending+current merge (section 9); `missing_context` seeds from the
    pending record's own last-known gap and is then FULLY RECOMPUTED,
    unchanged, by the existing logic below this call site (never
    precomputed here) -- so a fresh `grounded_command_candidate` this
    turn's own grounding produced still narrows or confirms the gap
    exactly as it would for any ordinary `EXACT_COMMAND` contract
    (section 11's own "grounding must stay fresh" requirement -- this
    function never weakens or bypasses that layer).

    LIVE-CORR-11 CORRECTIVE PASS:

      ISSUE A -- branches on `pending_governed_request.status`
      (`PendingGovernedRequestStatus`): an `UNRESOLVED` record is matched
      against its own `missing_context` (what is still needed); a
      `COMPLETED` record -- the previous target/instance already reached
      `ALLOW` -- is matched against its own `provided_context` NAMES
      (what was already supplied, now being corrected). Either way, the
      relevant vocabulary is computed here and handed to `_supplies_
      context_for_pending_request`/`_relates_to_pending_vocabulary` --
      this is what lets "actually it is RRU-10" reopen an already-ALLOWed
      "restart RRU" request for exactly one more turn (see that status's
      own docstring for why no TTL/timestamp is needed).

      ISSUE D -- restores `intent = pending_governed_request.intent` (the
      ORIGINAL turn's own semantic label), never a hardcoded `RequestIntent
      .COMMAND`. Safe regardless of the original intent's own value:
      `requested_output` is unconditionally forced to `EXACT_COMMAND`
      here, and `is_operationally_shaped_request`/`required_target_
      parameter_gaps` (request_contract.py) are both already an OR over
      `intent` and `requested_output` -- `requested_output=EXACT_COMMAND`
      alone is always sufficient to keep every downstream gate's existing
      behavior, whatever the restored `intent` turns out to be.

    POST-6A REPAIR 3/4: the derivation itself now lives in `resolve_
    pending_continuation`, above -- ONE resolution, reused by every
    consumer. This function is the unchanged-signature projection of it
    (the effective contract alone), kept so every existing caller and
    test keeps working exactly as written. The one behavior change is the
    one this repair exists to make: an UNRESOLVED relationship now yields
    a deliberately AMBIGUOUS effective contract that retains the pending
    request's identity, instead of silently returning the raw contract as
    though the pending request had never existed.
    """
    return resolve_pending_continuation(contract, pending_governed_request).contract


# =============================================================================
# LIVE-CORR-12D -- Request-Scoped Governed Evidence Continuity
# =============================================================================
#
# THE GAP THIS CLOSES: the LIVE-CORR-12A architectural audit proved
# `chat_service.py`'s governed-knowledge completion boundary
# (`enforce_governed_knowledge_at_completion`, governed_knowledge_
# completion.py) consults `LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY`/
# `ACTIVE_GOVERNED_PROCEDURE_STATE_KEY` -- a PRIOR turn's own selected-
# evidence identity -- completely independently of this turn's own
# validated `RequestContract`: a genuinely NEW, unrelated request ("what
# is alt command doing?" after a prior "restart RRU" exchange, `request_
# class=OPERATIONAL_INFORMATION`, `continuation=False`) still inherited
# the OLD evidence as its own candidate universe, produced a genuinely
# stale ambiguity ("Do you mean MOP X or Document1?"), and -- critically
# -- that early-return NEVER gave the real `incident_manager`/fresh
# `knowledge_search`/TELCO-applicability-narrowing pipeline a chance to
# run for the NEW request at all.
#
# THE FIX: `is_governed_evidence_continuity_permitted`, below, is the
# missing CONTINUITY CHECK step the milestone's own target architecture
# names -- consulted by `chat_service.py` BEFORE it decides what to pass
# as `prior_governed_evidence`/`active_anchor` into `enforce_governed_
# knowledge_at_completion`. `False` means prior evidence must NOT be
# treated as this turn's own candidate universe -- the caller passes an
# EMPTY prior-evidence list and no anchor, so `enforce_governed_knowledge_
# at_completion`'s OWN, completely UNCHANGED revalidation/ambiguity logic
# naturally sees ZERO stale candidates and falls straight through to a
# fresh, UNSCOPED `_run_incident_manager_remediation_once` call -- the
# REAL `incident_manager` runs, the REAL, existing TELCO applicability
# narrowing runs, exactly as it would for a request with no prior
# governed history at all. This is a GATE on what evidence enters scope,
# never a change to how that evidence is revalidated/narrowed/graded once
# in scope -- `governed_evidence_continuity.py`/`governed_knowledge_
# completion.py` are completely untouched by this pass.
def is_governed_evidence_continuity_permitted(
    contract: Optional[RequestContract],
    current_run_id: Optional[str],
    pending_governed_request: Optional[PendingGovernedRequest] = None,
) -> bool:
    """LIVE-CORR-12D -- the CONTINUITY CHECK: may THIS turn's governed-
    knowledge completion boundary treat a PRIOR turn's own selected
    governed evidence as its own candidate universe? Two independent
    paths, either alone sufficient; a missing/stale/ambiguous contract
    fails CLOSED to `False` -- prior evidence is NEVER the default.

    PATH 1 -- A GENUINELY RELEVANT PendingGovernedRequest (section 12's
    own explicit "PendingGovernedRequest must remain the authority for
    request continuity; the evidence anchor must not independently
    override it" requirement). Reuses `resolve_effective_governed_
    contract` (this module, completely UNCHANGED) -- the EXACT SAME
    relevance judgment `derive_execution_decision` already makes, never a
    second, independently-drifting copy: if applying the pending record
    actually CHANGES the contract (that function's own documented
    no-op-unless-matched contract), this turn is genuinely answering/
    correcting THAT SAME pending operation. A pending record that merely
    EXISTS but is NOT relevant to this turn (e.g. an old, still-technically
    -pending EXACT_COMMAND clarification sitting untouched while the user
    asks something entirely unrelated) correctly falls through to PATH 2
    instead of being trusted merely because some pending record exists --
    `resolve_effective_governed_contract`'s own `provided_context`-
    emptiness/vocabulary-relevance checks already guard exactly this case.

    PATH 2 -- `contract.continuation is True` AND `contract.request_class`
    is a real, non-`GENERAL_CONVERSATION` governed class. Section 8's own
    explicit "continuation must not become the sole gate" requirement:
    NEITHER signal is trusted alone -- `continuation` is the model's own
    claim (independently proven unreliable in BOTH directions by LIVE-
    CORR-10/11's own findings), and `request_class` alone is derived from
    ANY truthy `subject` (`derive_request_class`'s own "a resolved subject
    is enough" rule), including a BRAND NEW one, so it cannot by itself
    distinguish "same topic" from "different topic" either. Combined, they
    require the model to BOTH explicitly claim this is a follow-up AND
    have produced a governed (non-conversational) request shape -- this is
    exactly what FAILS for "what is alt command doing?"/"Resource
    Activation Timeout on RRU -- help me fix it" (both live-confirmed
    `continuation=False`) and exactly what HOLDS for a genuine "here is
    the diagnostic output" follow-up. Never raw subject-string equality,
    never keyword/regex matching of any kind -- the EXISTING, separate,
    already-tested `resolve_active_candidate_among_ambiguous`/`detect_
    explicit_sibling_topic_override` machinery (governed_evidence_
    continuity.py, untouched) still independently narrows/overrides
    WITHIN whatever candidate set this gate admits.

    A missing (`None`), stale (`run_id` mismatch), or ambiguous contract
    fails CLOSED -- mirrors `derive_execution_decision`'s own established
    freshness-check discipline exactly, applied one layer earlier, so a
    turn with nothing trustworthy to check is NEVER treated as safe to
    reuse prior evidence merely by default.
    """
    if contract is None or not current_run_id or contract.run_id != current_run_id or contract.ambiguity:
        return False
    if pending_governed_request is not None:
        # POST-6A REPAIR 3/4: uses the SAME single resolution every other
        # consumer does, and branches on its explicit outcome rather than
        # on "did the contract object change" -- which, now that an
        # UNRESOLVED relationship also rewrites the contract, would
        # otherwise have granted evidence continuity to precisely the
        # turn whose relationship to the pending request is unknown.
        resolution = resolve_pending_continuation(contract, pending_governed_request)
        if resolution.relation == PendingContinuationRelation.ANSWERS:
            return True
        if resolution.relation in (
            PendingContinuationRelation.CANCELLED,
            PendingContinuationRelation.UNRESOLVED,
        ):
            return False
    if not contract.continuation:
        return False
    # `_resolve_request_class` (this module, already established): a REAL
    # persisted contract always has `request_class` populated, but a
    # directly-constructed one (every pre-LIVE-CORR-8 test, and any future
    # caller that does the same) may leave it unset -- re-derives it
    # on the fly from the SAME pure function, never a second,
    # independently-drifting classification.
    resolved_request_class = _resolve_request_class(
        contract.request_class, contract.intent, contract.requested_output, contract.subject
    )
    return resolved_request_class not in (None, RequestClass.GENERAL_CONVERSATION)


def derive_execution_decision(
    contract: Optional[RequestContract],
    current_run_id: Optional[str],
    grounded_command_candidate: Optional[str] = None,
    pending_governed_request: Optional[PendingGovernedRequest] = None,
    operation_descriptor: Optional[GovernedOperationDescriptor] = None,
    evidence_authorized: bool = True,
) -> RequestExecutionDecision:
    """Pure, deterministic derivation -- NO LLM reasoning, no natural-
    language parsing. See this module's own docstring for the full
    branch-by-branch rationale.

    LIVE-CORR-7 -- `grounded_command_candidate` (optional, additive,
    backward-compatible default `None`): THIS turn's own real, already-
    captured `TroubleshootingGuidance.command` text (the caller --
    `chat_service.py`, which already has it available at this exact call
    site), forwarded unchanged to `required_target_parameter_gaps` so a
    genuinely target-independent grounded operation (e.g. a system-wide
    alarm listing) is not blanket-assigned `unit_id`/`unit_type`
    requirements it never actually needs -- see that function's own
    docstring for the full rationale. Every existing caller that omits
    this parameter is completely unaffected.

    POST-6A -- `evidence_authorized` (optional, additive, default
    `True`): `False` when this turn's governed evidence comes from a
    version whose applicability narrowing could not PROVE. Withdraws
    command/operational-step/action permission at the final boundary --
    indeterminate evidence may inform a clarification or an explicitly
    qualified reference, but it never authorizes direction. Every
    existing caller that omits it is unaffected.

    POST-6A REPAIR 2 -- `operation_descriptor` (optional, additive,
    backward-compatible default `None`): the APPROVED `GovernedOperation
    Descriptor` bound to the governed section this turn actually selected,
    when one exists. Forwarded unchanged to `required_target_parameter_
    gaps`, which is the ONE place it is consulted. `None` (no descriptor,
    a CANDIDATE one, or one whose scope is `UNKNOWN`) keeps that
    function's own conservative blanket requirement -- this parameter can
    only ever NARROW a requirement with positive governed proof, never
    widen permission.

    LIVE-CORR-11 -- `pending_governed_request` (optional, additive,
    backward-compatible default `None`): the CALLER's own already-parsed
    `PendingGovernedRequest` (`request_contract.py`), when one survived
    from a prior turn's unresolved `EXACT_COMMAND` request. Applied via
    `resolve_effective_governed_contract`, below, BEFORE any other branch
    in this function ever runs -- every branch after that point already
    operates on whatever `contract` refers to, unchanged, so this is a
    single, additive, minimal-blast-radius correction at the top of the
    function rather than a second, parallel decision path. Every existing
    caller that omits this parameter is completely unaffected.
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

    # POST-6A REPAIR 3/4 -- ONE resolution of the effective current
    # request, from the raw extraction proposal plus existing pending
    # state. `resolve_pending_continuation` is pure, so `chat_service.py`
    # calling it for its own consumers (retrieval input, authoritative
    # prompt block, continuity write) and this function calling it here
    # always agree on the same effective request -- there is no second,
    # independently-drifting derivation, and no need for the caller to
    # thread the resolved value through.
    pending_resolution = resolve_pending_continuation(contract, pending_governed_request)
    contract = pending_resolution.contract
    pending_relationship = pending_resolution.relation
    pending_subject = pending_resolution.pending_subject

    # LIVE-CORR-8 -- computed ONCE here, reused for every branch below via
    # `resolved_request_class`: a REAL, persisted contract already has
    # `request_class` populated by `validate_and_persist_request_
    # contract`; `_resolve_request_class`'s own fallback re-derives it,
    # identically, for a directly-constructed contract that left it unset
    # (every pre-LIVE-CORR-8 test in this codebase's own suite) -- so
    # both behave identically, never a second, independently-drifting
    # classification.
    resolved_request_class = _resolve_request_class(contract.request_class, contract.intent, contract.requested_output, contract.subject)

    # LIVE-CORR-5 -- General Conversation Must Not Trigger Operational
    # Command Gating. A model-set `ambiguity=True` is only a SAFETY-
    # RELEVANT signal for a request `request_scope` classifies as
    # `OPERATIONAL` -- subject/ambiguity are concepts about which specific
    # governed procedure/command/action a request concerns, and simply do
    # not apply to a `GENERAL` request (a greeting, "who are you and what
    # can you do?", any plain conversational/informational exchange with
    # no operational purpose). Confirmed live root cause: the model was
    # (and, defensively, still may be) instructed to set `ambiguity=true`
    # whenever `subject` is unset, with no distinction between "subject
    # genuinely unclear for an operational request" and "subject does not
    # apply because this is not an operational request at all" -- the
    # SAME conflation `is_operationally_shaped_request` (DEF-0037) already
    # fixed for the SEPARATE "no resolved subject/procedure" gate further
    # below, applied here to this contract's own explicit `ambiguity`
    # flag. This is a DETERMINISTIC, code-enforced correction, not a
    # prompt-only fix (prompts.py's own guidance is also corrected, but
    # this gate does not rely on the model actually following it).
    #
    # This narrows ONLY this `ambiguity`-triggered branch. This branch
    # runs BEFORE the ACTION/KNOWLEDGE_INVENTORY branches below in source
    # order, so `request_scope` deliberately classifies ACTION/KNOWLEDGE_
    # INVENTORY intents `OPERATIONAL` too -- an ambiguous ACTION/KNOWLEDGE_
    # INVENTORY contract still resolves `AMBIGUOUS` here, exactly as
    # before this pass (see `request_scope`'s own docstring). Existing
    # operational-ambiguity behavior (COMMAND/TROUBLESHOOTING/PROCEDURE
    # intents, or a command/procedure-shaped `requested_output`) is
    # completely unaffected.
    if contract.ambiguity and request_scope(contract.intent, contract.requested_output, contract.action_requested) == RequestScope.OPERATIONAL:
        return RequestExecutionDecision(
            status=RequestExecutionStatus.AMBIGUOUS,
            request_class=resolved_request_class,
            intent=contract.intent,
            requested_output=contract.requested_output,
            subject=contract.subject,
            may_emit_command=False,
            may_execute_action=False,
            may_emit_operational_steps=False,
            missing_context=list(contract.missing_context),
            ambiguity=True,
            pending_relationship=pending_relationship,
            pending_subject=pending_subject,
            approval_required=contract.approval_required,
            reason="request is ambiguous -- clarification required before any operational output",
        )

    if contract.intent == RequestIntent.ACTION or contract.action_requested:
        return RequestExecutionDecision(
            status=RequestExecutionStatus.REQUIRES_APPROVAL,
            request_class=resolved_request_class,
            intent=contract.intent,
            requested_output=contract.requested_output,
            subject=contract.subject,
            may_emit_command=False,
            may_execute_action=False,  # actual execution remains the existing, unchanged approval boundary's job
            may_emit_operational_steps=False,
            missing_context=list(contract.missing_context),
            ambiguity=False,
            pending_relationship=pending_relationship,
            pending_subject=pending_subject,
            approval_required=True,  # 6A.13 already forces this on the contract itself; reasserted defensively here
            reason="action requests always require the existing, unchanged approval boundary",
        )

    if contract.intent == RequestIntent.KNOWLEDGE_INVENTORY:
        return RequestExecutionDecision(
            status=RequestExecutionStatus.UNSUPPORTED_CAPABILITY,
            request_class=resolved_request_class,
            intent=contract.intent,
            requested_output=contract.requested_output,
            subject=contract.subject,
            may_emit_command=False,
            may_execute_action=False,
            may_emit_operational_steps=False,
            missing_context=list(contract.missing_context),
            ambiguity=False,
            pending_relationship=pending_relationship,
            pending_subject=pending_subject,
            approval_required=False,
            reason="deterministic Knowledge catalog enumeration is not yet implemented (a future milestone)",
        )

    if is_operationally_shaped_request(contract.intent, contract.requested_output) and not contract.subject:
        return RequestExecutionDecision(
            status=RequestExecutionStatus.AMBIGUOUS,
            request_class=resolved_request_class,
            intent=contract.intent,
            requested_output=contract.requested_output,
            subject=None,
            may_emit_command=False,
            may_execute_action=False,
            may_emit_operational_steps=False,
            missing_context=list(contract.missing_context),
            ambiguity=True,
            pending_relationship=pending_relationship,
            pending_subject=pending_subject,
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
    # model's OWN classification alone to decide safety).
    #
    # LIVE-CORR-12B -- Canonical Request-Context Parameter Authority:
    # `contract.missing_context` is no longer, on its own, enough to enter
    # this branch -- only its AUTHORITATIVE subset is (`authoritative_
    # missing_context_names`, request_contract.py: recognized canonical
    # names only, e.g. `unit_id`/`unit_type`). The LIVE-CORR-12A audit
    # proved the model can freely invent ANY `missing_context` name
    # (`"governed procedure"`, `"specific fault details"`, a differently-
    # spelled alias of an already-canonical key) with zero deterministic
    # backing -- letting such a name alone force `NEEDS_INFORMATION` is
    # exactly the "arbitrary string becomes a mandatory policy
    # requirement" authority gap this milestone closes. The DEF-0037 live
    # shape this comment originally described used the CANONICAL names
    # `unit_type`/`unit_id` -- `authoritative_missing_context_names`
    # recognizes both, so that regression is completely unaffected; this
    # is a strict OR, never an AND, so DEF-0037's own fix (an ordinary
    # "hello," `missing_context=[]`, correctly skips this branch entirely)
    # also remains completely unaffected.
    authoritative_declared_missing_context = set(authoritative_missing_context_names(contract.missing_context))
    if is_operationally_shaped_request(contract.intent, contract.requested_output) or authoritative_declared_missing_context:
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
        # LIVE-CORR-7: `contract.missing_context` was persisted at
        # `validate_and_persist_request_contract` time (request_
        # contract.py), BEFORE any governed evidence for this turn could
        # possibly have been grounded -- for an `EXACT_COMMAND` request
        # specifically, its own contribution of `unit_type`/`unit_id` (if
        # present purely because of the deterministic blanket rule, never
        # because the MODEL genuinely declared them as a real condition)
        # reflects ONLY that original, ungrounded rule, never this turn's
        # own grounded command candidate. Excluded here, for `EXACT_
        # COMMAND` ONLY, so those two specific keys come EXCLUSIVELY from
        # the fresh call below (which DOES have `grounded_command_
        # candidate`, when one exists) -- otherwise a stale, ungrounded
        # requirement already baked into `contract.missing_context` would
        # survive this union regardless of what the fresh, better-informed
        # call concludes. For every OTHER `requested_output` (e.g.
        # `PROCEDURE_STEPS`/`TROUBLESHOOTING_NEXT_STEP`, where `unit_type`/
        # `unit_id` can be a genuine, model-declared CONDITIONAL-branch
        # signal per prompts.py's own "CONDITIONAL COMMAND HANDLING"
        # instruction -- `required_target_parameter_gaps`'s own blanket
        # rule never even applies), the AUTHORITATIVE contribution is left
        # completely untouched, exactly as before this pass (only now
        # drawn from the canonical subset, never the raw model list).
        if contract.requested_output == RequestedOutput.EXACT_COMMAND:
            contract_missing_context_contribution = authoritative_declared_missing_context - {
                TARGET_TYPE_PARAMETER_NAME,
                TARGET_IDENTIFIER_PARAMETER_NAME,
            }
        else:
            contract_missing_context_contribution = authoritative_declared_missing_context
        effective_missing_context = sorted(
            contract_missing_context_contribution
            | set(
                required_target_parameter_gaps(
                    contract.intent,
                    contract.requested_output,
                    contract.provided_context,
                    grounded_command_candidate,
                    # POST-6A REPAIR 2: positive governed metadata for the
                    # section actually selected this turn, when one exists.
                    # `None` keeps the conservative blanket rule -- unknown
                    # scope stays unresolved, never permissive.
                    operation_descriptor,
                )
            )
        )
        if effective_missing_context:
            return RequestExecutionDecision(
                status=RequestExecutionStatus.NEEDS_INFORMATION,
                request_class=resolved_request_class,
                intent=contract.intent,
                requested_output=contract.requested_output,
                subject=contract.subject,
                may_emit_command=False,
                may_execute_action=False,
                may_emit_operational_steps=True,  # a safe, non-command diagnostic step/clarification remains allowed
                missing_context=effective_missing_context,
                ambiguity=False,
                pending_relationship=pending_relationship,
                pending_subject=pending_subject,
                approval_required=contract.approval_required,
                reason=f"required context not yet confirmed by the user: {', '.join(effective_missing_context)}",
            )

    # LIVE-CORR-3B -- Operational Authority Boundary, section 1's own
    # explicit "ALLOW must not automatically mean may_emit_command=True"
    # requirement. Command permission is a SEPARATE, NARROWER grant than
    # "this request is fully resolved and safe to answer at all" --
    # possible ONLY for a request VALIDATED as belonging to the
    # `EXACT_COMMAND` governance class WITH `requested_output=EXACT_
    # COMMAND` (mirrors `is_exact_command_response_permitted`'s own
    # established definition, applied here one layer earlier). A
    # PROCEDURE_STEPS/TROUBLESHOOTING_NEXT_STEP-shaped ALLOW decision
    # never implicitly inherits command permission merely because the
    # overall request is otherwise fully resolved -- closes the exact
    # fail-open path where a fully-parameterized "next step" answer showed
    # a raw command with no EXACT_COMMAND validation at all.
    #
    # LIVE-CORR-8 -- Request Class Must Be the Authoritative Governance
    # Boundary: this check previously required `contract.intent ==
    # RequestIntent.COMMAND` specifically -- the CONFIRMED live root cause
    # of "give me the exact command to list current alarms" being
    # rejected: the model classified `intent=procedure` (a semantically
    # reasonable label for a request grounded in a governed procedure
    # document) with `requested_output=exact_command`, a combination the
    # OLD `intent==COMMAND` check never recognized, regardless of how
    # clearly the requested OUTPUT shape said "exact command." `request_
    # class` (`derive_request_class`, request_contract.py) is DERIVED
    # from `intent` OR `requested_output` (either signal is enough,
    # mirroring DEF-0037's own "intent labels cannot bypass safety"
    # argument) -- so `contract.request_class == RequestClass.EXACT_
    # COMMAND` is TRUE for this exact live shape, closing the gap, while
    # `requested_output == EXACT_COMMAND` is still independently required
    # too (defense in depth -- a contract whose `request_class` was
    # somehow `EXACT_COMMAND` for an unrelated reason still cannot grant
    # command permission for a DIFFERENT `requested_output`).
    #
    # `may_emit_operational_steps` remains the separate, broader signal
    # (unchanged in meaning) for whether NON-COMMAND operational/diagnostic
    # narrative is in scope at all for this request shape -- never on its
    # own sufficient to authorize a command.
    allow_command = resolved_request_class == RequestClass.EXACT_COMMAND and contract.requested_output == RequestedOutput.EXACT_COMMAND

    # POST-6A -- INDETERMINATE APPLICABILITY AUTHORIZES NOTHING.
    #
    # `evidence_authorized=False` means the governed evidence this turn is
    # standing on comes from a version whose applicability could NOT be
    # proven against the context known so far. Such evidence is still
    # legitimately RETRIEVED -- it is what a clarification or an
    # explicitly-qualified reference is built from -- but an operational
    # instruction grounded in knowledge we cannot show applies is exactly
    # the failure the permitted/indeterminate split exists to prevent.
    #
    # So command, operational-step and action permissions are all
    # withdrawn here, at the final policy boundary, regardless of how
    # correct the contract and the descriptor themselves are. The turn
    # remains answerable -- it simply answers with reference and
    # clarification rather than with direction.
    allow_operational_steps = is_operationally_shaped_request(contract.intent, contract.requested_output)
    if not evidence_authorized:
        allow_command = False
        allow_operational_steps = False

    return RequestExecutionDecision(
        status=RequestExecutionStatus.ALLOW,
        request_class=resolved_request_class,
        intent=contract.intent,
        requested_output=contract.requested_output,
        subject=contract.subject,
        may_emit_command=allow_command,
        may_execute_action=False,  # never this policy's job to authorize an actual write
        may_emit_operational_steps=allow_operational_steps,
        missing_context=list(contract.missing_context),
        ambiguity=False,
        pending_relationship=pending_relationship,
        pending_subject=pending_subject,
        approval_required=contract.approval_required,
        reason=(
            "request contract satisfied -- exact-command output permitted, subject to existing grounding"
            if allow_command
            else "request contract satisfied -- non-command operational output permitted; no exact-command grant"
        ),
    )


def is_deferred_target_resolution(
    contract: Optional[RequestContract], phase_a_decision: RequestExecutionDecision
) -> bool:
    """CONTROL-PLANE-SEQ-03 -- the ONE proven, narrow Phase-A circular-
    dependency exception identified by CONTROL-PLANE-SEQ-02's own audit.

    THE GAP: a fresh, valid `EXACT_COMMAND`-classified contract whose own
    `provided_context` never supplied `unit_type` at all cannot yet be
    distinguished, by a PRE-EXECUTION policy call alone (`derive_
    execution_decision(..., grounded_command_candidate=None, ...)`), from
    a genuinely target-independent operation (e.g. a system-wide alarm
    listing) -- `required_target_parameter_gaps`'s own documented
    "absence of a candidate fails CLOSED to the blanket rule" design
    (LIVE-CORR-7) means a Phase-A `NEEDS_INFORMATION` in EXACTLY this
    shape is not yet a proven requirement; only a SECOND policy call, made
    AFTER specialist work has produced a real grounded candidate (Phase
    B), can prove it either way. If Phase A hard-gated this shape,
    specialist work could never run, and Phase B could never get the
    chance to disprove the blanket assumption -- a genuine circular
    dependency.

    THE FIX: this predicate identifies EXACTLY that shape, and ONLY that
    shape -- never widens any other `NEEDS_INFORMATION`, `AMBIGUOUS`,
    `INVALID_CONTRACT`, `REQUIRES_APPROVAL`, or `UNSUPPORTED_CAPABILITY`
    outcome. Returns `True` only when ALL of the following hold:

      1. `phase_a_decision.status == NEEDS_INFORMATION` -- every other
         status is untouched by this predicate.
      2. the resolved request class is `EXACT_COMMAND` (via the SAME
         `_resolve_request_class` fallback every other caller in this
         module already uses -- never a second classification).
      3. `contract.provided_context` supplies NEITHER `unit_type` NOR
         `unit_id` at all. The identifier-bearing (`unit_type` supplied,
         identifier-class) and target-independent-verified (`unit_type`
         in the small `SupportUnit`-style allowlist) branches of
         `required_target_parameter_gaps` are BOTH stable across
         `grounded_command_candidate` values (they never consult it) --
         this predicate never defers those; they remain unconditional
         Phase-A hard gates.
      4. `phase_a_decision.missing_context` is EXACTLY `{unit_type,
         unit_id}` -- the blanket fallback's own, and ONLY its own,
         contribution. Any OTHER authoritative missing-context name
         (canonical or model-declared) keeps this predicate `False`, so
         a request with some UNRELATED genuine gap is never deferred
         merely because it also happens to lack a target.

    Never inspects raw user text, never keyword/regex matching, never
    guesses intent or grounding outcome -- reuses `_resolve_request_class`
    and the two existing canonical target-parameter-name constants,
    exactly as `derive_execution_decision` itself already does
    internally. Never a second, independently-drifting classification.

    SAFETY: this predicate NEVER grants `may_emit_command`, and is never
    itself consulted by anything that could. It only ever controls
    whether OPERATIONAL_EXECUTION (specialist delegation/grounding) is
    PERMITTED TO RUN AT ALL for this one narrow shape -- so that a real
    `grounded_command_candidate` can eventually be produced for Phase B
    to evaluate. Phase B -- a SEPARATE, later `derive_execution_decision`
    call, made with the REAL candidate -- remains the sole, completely
    unmodified authority for command emission (`allow_command`'s own
    computation is untouched by this function, and does not consult it).
    A request deferred here that Phase B later finds DOES require a
    target still correctly returns `NEEDS_INFORMATION` at that point --
    there is no permanent bypass.
    """
    if contract is None:
        return False
    if phase_a_decision.status != RequestExecutionStatus.NEEDS_INFORMATION:
        return False
    resolved_request_class = _resolve_request_class(
        contract.request_class, contract.intent, contract.requested_output, contract.subject
    )
    if resolved_request_class != RequestClass.EXACT_COMMAND:
        return False
    provided_names = {param.name for param in contract.provided_context}
    if TARGET_TYPE_PARAMETER_NAME in provided_names or TARGET_IDENTIFIER_PARAMETER_NAME in provided_names:
        return False
    return set(phase_a_decision.missing_context) == {TARGET_TYPE_PARAMETER_NAME, TARGET_IDENTIFIER_PARAMETER_NAME}


# =============================================================================
# CONTROL-PLANE-SEQ-04 -- Deterministic Work Envelope + Authorized Routing
# =============================================================================
#
# THE GAP THIS CLOSES: CONTROL-PLANE-SEQ-03 made specialist/retrieval work
# structurally unreachable until AFTER a fresh `RequestContract` and a
# pre-execution ("Phase-A") policy decision existed -- but it did so by
# reusing `derive_execution_decision(..., grounded_command_candidate=None,
# ...)`, a function whose actual JOB is "what may reach the user," pressed
# into service one call too early to answer a DIFFERENT question ("what
# work may the system even attempt"). The one narrow shape where those two
# questions structurally disagree -- a fresh `EXACT_COMMAND` contract with
# no `unit_type`/`unit_id` supplied at all, where `required_target_
# parameter_gaps`'s own candidate-dependent blanket rule cannot yet be
# proven either way -- required a SEPARATE, bolted-on exception predicate
# (`is_deferred_target_resolution`, above) purely to keep the (correct)
# Phase-A/Phase-B split from becoming a circular dependency.
#
# THE FIX: `WorkEnvelope` is a SEPARATE, explicitly-scoped deterministic
# answer to "what work may the system perform this turn," never "what may
# reach the user" (that remains exclusively `RequestExecutionDecision`'s
# job, computed LATER, from real post-specialist evidence -- completely
# unmodified by this section). `derive_work_envelope`, below, is the ONE
# pure function that owns this question -- and it answers it by REUSING
# `derive_execution_decision`/`is_deferred_target_resolution` internally
# (as proven, tested building blocks -- never a second, independently-
# drifting copy of their freshness/ambiguity/subject/missing-context
# logic), then RE-INTERPRETING their combined output through the "work
# permitted" lens instead of the "user-visible" lens. Concretely: a Phase-A-
# shaped `NEEDS_INFORMATION` that `is_deferred_target_resolution` already
# proves is the ONE candidate-dependent EXACT_COMMAND shape now correctly
# PERMITS specialist work (`may_generate_command_candidate=True`) while
# still NEVER granting `may_emit_command` (that field does not exist on
# this type at all) -- eliminating the circular dependency by construction,
# not by a bolted-on exception `chat_service.py` has to remember to check.
#
# `chat_service.py`'s own normal-turn routing now consults ONLY
# `WorkEnvelope` fields -- never `phase_a_decision.status`/`is_deferred_
# target_resolution` directly (section 23's own explicit "the normal
# successful flow must be validated contract -> effective request ->
# WorkEnvelope -> authorized routing, not: fake early final decision ->
# exception predicate -> routing" requirement). `is_deferred_target_
# resolution` itself is NOT deleted (kept for transitional/back-compat
# reachability, per explicit instruction) -- it is simply no longer a
# ChatService-level branching predicate; it is now purely an internal
# implementation detail `derive_work_envelope` reuses.
class WorkAuthority:
    """Closed, ordered vocabulary for `WorkEnvelope.maximum_authority` --
    the HIGHEST CLASS of candidate work the runtime may prepare this turn,
    never a grant of `may_emit_command`/`may_execute_action` (those remain
    exclusively `RequestExecutionDecision`'s job -- see `WorkEnvelope`'s
    own docstring for the full "maximum authority is not final authority"
    distinction, instruction section 4)."""

    NONE = "none"
    CONVERSATIONAL_RESPONSE = "conversational_response"
    OPERATIONAL_INFORMATION = "operational_information"
    PROCEDURE_CANDIDATE = "procedure_candidate"
    COMMAND_CANDIDATE = "command_candidate"
    ACTION_CANDIDATE = "action_candidate"


class WorkEnvelope(BaseModel):
    """WHAT WORK MAY THE SYSTEM PERFORM this turn -- deterministic, LOCAL
    to the current turn, immutable (a plain, frozen-by-convention pydantic
    value -- never mutated after `derive_work_envelope` returns it), and
    NEVER persisted. No LLM ever writes or influences a single field on
    this type -- it is produced entirely from already-validated,
    already-authoritative control state (see `derive_work_envelope`'s own
    docstring for the exact deterministic inputs/rules).

    THIS TYPE DOES NOT ANSWER "what may reach the user" (that remains
    `RequestExecutionDecision`, computed separately, later, from REAL
    post-specialist evidence) and DOES NOT ANSWER "may an action execute"
    (there is no such field here at all -- see instruction section 13:
    actual execution remains a completely separate, later boundary this
    milestone does not implement). `maximum_authority` is the highest
    CLASS of candidate work permitted -- e.g. `COMMAND_CANDIDATE` means a
    command STRING may be internally generated/grounded this turn, never
    that it may be shown to the user; `may_emit_command=True` is a
    RequestExecutionDecision-only concept and intentionally has no
    equivalent field on this type at all, so no code can ever mistake one
    for the other by reading the wrong object.
    """

    request_class: Optional[str] = None
    maximum_authority: str = WorkAuthority.NONE
    work_permitted: bool = False
    """Whether ANY specialist/retrieval work may be attempted at all this
    turn -- `False` for every candidate-INDEPENDENT blocker (invalid/stale
    contract, unresolved ambiguity, no resolved subject for an
    operationally-shaped request, an unsupported capability) -- see
    `derive_work_envelope`'s own docstring. Conversational response
    generation (`may_generate_conversational_response`) is independent of
    this flag -- a blocked turn can, and normally does, still need to
    generate a clarification/refusal via the tool-free presentation path."""
    clarification_required_before_work: bool = False
    may_generate_conversational_response: bool = True
    may_use_operational_context: bool = False
    may_use_governed_knowledge: bool = False
    requires_governed_knowledge: bool = False
    """Section 8 -- consumes `SourceRequirementsCapture.requires_governed_
    knowledge` (the model's OWN declaration, via `record_source_
    requirements` -- a COMPLETELY SEPARATE mechanism from `RequestContract
    .requires_governed_knowledge`, never merged, per explicit instruction),
    narrowed so a declaration alone can never grant more authority than
    `request_class` itself permits (`False`, unconditionally, for
    `GENERAL_CONVERSATION` -- section 9's own explicit structural
    prohibition)."""
    may_route_incident_manager: bool = False
    may_route_troubleshooting_manager: bool = False
    may_generate_procedure_candidate: bool = False
    may_generate_command_candidate: bool = False
    may_prepare_action_candidate: bool = False
    reason: str = ""


def derive_work_envelope(
    contract: Optional[RequestContract],
    current_run_id: Optional[str],
    pending_governed_request: Optional[PendingGovernedRequest] = None,
    requires_governed_knowledge_declared: bool = False,
    requires_teams_declared: bool = False,
) -> WorkEnvelope:
    """CONTROL-PLANE-SEQ-04 -- the ONE pure, deterministic function that
    owns "what work may the system perform this turn." See this section's
    own module-level comment for the full architectural rationale.

    INPUTS, ALL ALREADY-AUTHORITATIVE (never raw user text, never keyword/
    regex matching, never model prose):
      - `contract`/`current_run_id`/`pending_governed_request`: passed
        straight through to `derive_execution_decision` (unmodified) --
        the SAME freshness/pending-resolution/ambiguity/missing-context
        logic every other caller already relies on.
      - `requires_governed_knowledge_declared`/`requires_teams_declared`:
        THIS turn's own `SourceRequirementsCapture` values (declared via
        `record_source_requirements`, section 8 -- a source-requirements
        DECLARATION, never `RequestContract.requires_governed_knowledge`).
        Used ONLY for the `requires_governed_knowledge`/`may_use_governed_
        knowledge` OBSERVABILITY fields on the returned envelope -- NEVER
        to decide `may_route_incident_manager` (see below for why).

    DERIVATION:
      1. Computes a Phase-A-shaped `RequestExecutionDecision` via `derive_
         execution_decision(contract, current_run_id, grounded_command_
         candidate=None, pending_governed_request=pending_governed_
         request)` -- reused verbatim, never duplicated. This ALREADY
         performs pending-resolution (`resolve_effective_governed_
         contract`, exactly once, internally -- this function never
         separately resolves pending state itself, so there is no risk of
         double-resolution), freshness checking, ambiguity/no-subject/
         missing-context gating, and request-class derivation.
      2. Computes `is_deferred_target_resolution(contract, phase_decision)`
         -- reused verbatim (section 6: kept, not deleted, but now only
         ever consulted FROM HERE, never directly by `chat_service.py`).
      3. Maps the combined result onto `WorkEnvelope`:
           - `INVALID_CONTRACT` -> blocked, `work_permitted=False`,
             `clarification_required_before_work=True` (a missing/stale
             contract fails at LEAST as restrictively as before).
           - `AMBIGUOUS` -> blocked, `clarification_required_before_
             work=True` -- a candidate-INDEPENDENT blocker (instruction
             section 7's own explicit "deterministic ambiguity that
             prevents identifying the governed request" example).
           - `UNSUPPORTED_CAPABILITY` (KNOWLEDGE_INVENTORY) -> blocked, but
             NOT a clarification -- the existing, deterministic `KNOWLEDGE_
             INVENTORY_UNSUPPORTED_TEXT` fallback (unchanged, downstream)
             already answers this without any specialist work.
           - `NEEDS_INFORMATION` and NOT `is_deferred_target_resolution`
             -> blocked, `clarification_required_before_work=True` -- every
             OTHER authoritative missing-context shape (a resolved
             `unit_type` still needing `unit_id`, a genuinely model-
             declared canonical gap for a NON-`EXACT_COMMAND` request, ...)
             is candidate-INDEPENDENT (instruction section 7's own explicit
             "other authoritative missing context... does NOT depend on
             the eventual grounded candidate" example) -- `required_target_
             parameter_gaps`'s own `unit_type`-informed branches never even
             consult `grounded_command_candidate`, so there is nothing a
             specialist call could prove here that this function does not
             already know.
           - `NEEDS_INFORMATION` and `is_deferred_target_resolution` ->
             PERMITTED (instruction section 5's own core fix): the ONE
             genuinely candidate-DEPENDENT shape -- `maximum_authority=
             COMMAND_CANDIDATE`, `may_generate_command_candidate=True`,
             specialist routing permitted so a real grounded candidate can
             be produced for Phase B to evaluate. Never sets `clarification
             _required_before_work` -- whether clarification is ultimately
             needed is exactly the question Phase B, with real evidence,
             will answer.
           - `ALLOW`/`REQUIRES_APPROVAL` -> PERMITTED, `maximum_authority`
             and routing flags keyed off the decision's own `request_class`
             (`GENERAL_CONVERSATION` -> `CONVERSATIONAL_RESPONSE`, no
             operational capability at all, per instruction section 9;
             `OPERATIONAL_INFORMATION` -> `OPERATIONAL_INFORMATION`, may
             route `incident_manager` only, per instruction section 10;
             `PROCEDURE_TROUBLESHOOTING` -> `PROCEDURE_CANDIDATE`, may
             route both specialists, per instruction section 11;
             `EXACT_COMMAND` -> `COMMAND_CANDIDATE`, may route
             `incident_manager` and generate a command candidate, per
             instruction section 12; `ACTION` -> `ACTION_CANDIDATE`,
             read-only preparation only -- there is no execution field on
             this type at all, per instruction section 13).

    ROUTING (`may_route_incident_manager`/`may_route_troubleshooting_
    manager`) IS CLASS-DRIVEN, DELIBERATELY NOT DECLARATION-DRIVEN: the
    ONLY unconditional structural prohibition instruction section 9 gives a
    concrete example for is "`GENERAL_CONVERSATION` must never reach either
    specialist" -- a closed, deterministic, already-safe signal
    (`resolved_request_class`). Gating tool REACHABILITY itself (as opposed
    to whether governed retrieval is ANSWER-COMPLETION-MANDATORY, which
    remains the SEPARATE, existing `governed_completion_needed` gate,
    unchanged in spirit) on `requires_governed_knowledge_declared`/
    `requires_teams_declared` was deliberately REJECTED: those are the
    SAME per-turn model declarations LIVE-CORR-12I's own remediation
    machinery exists BECAUSE they are not always reliably produced -- using
    an unreliable per-turn declaration as a HARD gate on a tool team_
    manager's model has always been free to reach for every other class
    would risk a new class of regression (a genuinely Teams-only or
    genuinely governed-knowledge-needing request silently losing access to
    `incident_manager_tool` merely because the SEPARATE, narrower preflight
    declaration call under-declared). `may_use_governed_knowledge`/
    `requires_governed_knowledge` (declaration-driven, narrowed by class)
    remain the correct, existing signal for the SEPARATE "is governed
    retrieval mandatory for a complete answer" question.

    NEVER inspects raw user text, never keyword/regex matching, never
    guesses grounding outcome -- every branch reads only already-validated,
    already-typed fields.
    """
    phase_decision = derive_execution_decision(
        contract, current_run_id, grounded_command_candidate=None, pending_governed_request=pending_governed_request
    )
    deferred_target = is_deferred_target_resolution(contract, phase_decision)

    if phase_decision.status == RequestExecutionStatus.INVALID_CONTRACT:
        return WorkEnvelope(
            request_class=None,
            maximum_authority=WorkAuthority.NONE,
            work_permitted=False,
            clarification_required_before_work=True,
            may_generate_conversational_response=True,
            reason="no current-turn validated request contract",
        )

    if phase_decision.status == RequestExecutionStatus.AMBIGUOUS:
        return WorkEnvelope(
            request_class=phase_decision.request_class,
            maximum_authority=WorkAuthority.NONE,
            work_permitted=False,
            clarification_required_before_work=True,
            may_generate_conversational_response=True,
            reason=phase_decision.reason,
        )

    if phase_decision.status == RequestExecutionStatus.UNSUPPORTED_CAPABILITY:
        return WorkEnvelope(
            request_class=phase_decision.request_class,
            maximum_authority=WorkAuthority.NONE,
            work_permitted=False,
            clarification_required_before_work=False,
            may_generate_conversational_response=True,
            reason=phase_decision.reason,
        )

    if phase_decision.status == RequestExecutionStatus.NEEDS_INFORMATION and not deferred_target:
        return WorkEnvelope(
            request_class=phase_decision.request_class,
            maximum_authority=WorkAuthority.NONE,
            work_permitted=False,
            clarification_required_before_work=True,
            may_generate_conversational_response=True,
            reason=phase_decision.reason,
        )

    # From here: status is ALLOW, REQUIRES_APPROVAL, or the one deferred-
    # target NEEDS_INFORMATION shape -- all genuinely permit SOME
    # candidate-independent work to proceed.
    resolved_request_class = phase_decision.request_class
    requires_governed_knowledge = bool(requires_governed_knowledge_declared) and resolved_request_class != RequestClass.GENERAL_CONVERSATION
    may_use_governed_knowledge = requires_governed_knowledge

    if resolved_request_class == RequestClass.GENERAL_CONVERSATION:
        return WorkEnvelope(
            request_class=resolved_request_class,
            maximum_authority=WorkAuthority.CONVERSATIONAL_RESPONSE,
            work_permitted=True,
            clarification_required_before_work=False,
            may_generate_conversational_response=True,
            reason="general conversation -- no operational capability required",
        )

    if resolved_request_class == RequestClass.OPERATIONAL_INFORMATION:
        return WorkEnvelope(
            request_class=resolved_request_class,
            maximum_authority=WorkAuthority.OPERATIONAL_INFORMATION,
            work_permitted=True,
            clarification_required_before_work=False,
            may_generate_conversational_response=True,
            may_use_operational_context=True,
            may_use_governed_knowledge=may_use_governed_knowledge,
            requires_governed_knowledge=requires_governed_knowledge,
            may_route_incident_manager=True,
            reason="operational information request -- informational specialist routing permitted",
        )

    if resolved_request_class == RequestClass.PROCEDURE_TROUBLESHOOTING:
        return WorkEnvelope(
            request_class=resolved_request_class,
            maximum_authority=WorkAuthority.PROCEDURE_CANDIDATE,
            work_permitted=True,
            clarification_required_before_work=False,
            may_generate_conversational_response=True,
            may_use_operational_context=True,
            may_use_governed_knowledge=may_use_governed_knowledge,
            requires_governed_knowledge=requires_governed_knowledge,
            may_route_incident_manager=True,
            may_route_troubleshooting_manager=True,
            may_generate_procedure_candidate=True,
            reason="procedure/troubleshooting request -- specialist candidate generation permitted",
        )

    if resolved_request_class == RequestClass.EXACT_COMMAND:
        return WorkEnvelope(
            request_class=resolved_request_class,
            maximum_authority=WorkAuthority.COMMAND_CANDIDATE,
            work_permitted=True,
            clarification_required_before_work=False,
            may_generate_conversational_response=True,
            may_use_operational_context=True,
            may_use_governed_knowledge=may_use_governed_knowledge,
            requires_governed_knowledge=requires_governed_knowledge,
            may_route_incident_manager=True,
            may_route_troubleshooting_manager=True,
            may_generate_command_candidate=True,
            reason=(
                "target parameters not yet confirmed -- specialist grounding permitted to determine "
                "whether a target is actually required before final policy decides"
                if deferred_target
                else "exact-command request resolved -- specialist grounding permitted to produce a candidate"
            ),
        )

    if resolved_request_class == RequestClass.ACTION:
        return WorkEnvelope(
            request_class=resolved_request_class,
            maximum_authority=WorkAuthority.ACTION_CANDIDATE,
            work_permitted=True,
            clarification_required_before_work=False,
            may_generate_conversational_response=True,
            may_use_operational_context=True,
            may_use_governed_knowledge=may_use_governed_knowledge,
            requires_governed_knowledge=requires_governed_knowledge,
            may_route_incident_manager=True,
            may_route_troubleshooting_manager=True,
            may_prepare_action_candidate=True,
            reason="action request -- read-only preparation permitted; execution remains a separate, unimplemented approval boundary",
        )

    # Unreachable in practice -- every RequestClass is handled above, and
    # `_resolve_request_class`/`derive_request_class` are both closed,
    # exhaustive functions. Fails closed regardless.
    return WorkEnvelope(
        request_class=resolved_request_class,
        maximum_authority=WorkAuthority.NONE,
        work_permitted=False,
        clarification_required_before_work=True,
        may_generate_conversational_response=True,
        reason="unrecognized request class",
    )


def build_pending_governed_request_state_update(
    decision: RequestExecutionDecision, contract: RequestContract
) -> dict[str, object]:
    """LIVE-CORR-11 -- the caller-facing (`chat_service.py`) counterpart
    to `resolve_effective_governed_contract`: builds THIS turn's own
    outcome into the SAME additive, single-key session-state update shape
    `governed_evidence_continuity.build_active_governed_procedure_state_
    update` already established, for the CALLER to merge into its own
    `end_of_turn_state_delta` and persist via the existing `persist_state_
    delta` call -- never a new persistence mechanism.

    UNLIKE that existing sibling builder (which never blanks a previously
    valid continuity anchor), this key is DELIBERATELY, ALWAYS refreshed
    every turn -- section 10's own "must not accidentally inherit stale
    target/authorization from the previous target" requirement, and
    section 8's "a pending EXACT_COMMAND request must NOT contaminate a
    genuinely new request" requirement, both demand active clearing, not
    stickiness. `decision.request_class != EXACT_COMMAND` always clears
    immediately, regardless of status. For an `EXACT_COMMAND`-class
    decision:

      - `NEEDS_INFORMATION` -- the proven defect shape -- writes an
        `UNRESOLVED` `PendingGovernedRequest`.
      - `AMBIGUOUS` -- LIVE-CORR-11 CORRECTIVE PASS, section 6's own
        "AMBIGUOUS is not necessarily terminal" audit: writes `UNRESOLVED`
        too, but ONLY when `contract.subject is not None` -- an ambiguity
        that never even resolved a subject/operation (`derive_execution_
        decision`'s own separate "no resolved subject/procedure" branch)
        has no concrete operation identity worth preserving; that shape
        clears, exactly as before.
      - `ALLOW` -- LIVE-CORR-11 CORRECTIVE PASS -- ISSUE A: writes
        `COMPLETED` (not a clear) -- see `PendingGovernedRequestStatus`'s
        own docstring for why this is safe: it is unconditionally
        rewritten again by THIS SAME function on the very next turn
        (matched-and-superseded, or unmatched-and-cleared), so it can
        never survive more than one extra turn, and it never itself
        carries a command/grounding result -- only the resolved
        governance identity and its own already-verified parameters.
      - `REQUIRES_APPROVAL`/`UNSUPPORTED_CAPABILITY`/`INVALID_CONTRACT`
        (the last never actually reaches this function -- `chat_service
        .py` only calls it when a validated contract exists) -- clears,
        unchanged from before this pass.

    Built from THIS turn's own already-verified `contract.provided_
    context` and (for `UNRESOLVED`) `decision.missing_context` (already
    the fully fresh-grounding-aware value `derive_execution_decision`
    computed) -- never a copy of any OLDER pending record, so a
    corrected/replaced target (sections 9/10) can never leak a stale
    value forward. `intent=contract.intent` preserves the ORIGINAL
    semantic operation (ISSUE D) across however many turns this record
    survives.
    """
    if decision.request_class != RequestClass.EXACT_COMMAND:
        return {PENDING_GOVERNED_REQUEST_STATE_KEY: None}
    if decision.status == RequestExecutionStatus.NEEDS_INFORMATION:
        status = PendingGovernedRequestStatus.UNRESOLVED
        missing = list(decision.missing_context)
    elif decision.status == RequestExecutionStatus.AMBIGUOUS and (
        contract.subject is not None
        # POST-6A REPAIR 4: an UNRESOLVED relationship is an ambiguity
        # ABOUT the pending request itself -- the runtime is asking the
        # user whether to continue it. Clearing it here (which a pending
        # record that never resolved a subject of its own would otherwise
        # do) would destroy the very thing the question is about, so the
        # user's answer next turn would have nothing left to resume.
        or decision.pending_relationship == PendingContinuationRelation.UNRESOLVED
    ):
        status = PendingGovernedRequestStatus.UNRESOLVED
        missing = list(decision.missing_context)
    elif decision.status == RequestExecutionStatus.ALLOW:
        status = PendingGovernedRequestStatus.COMPLETED
        missing = []  # resolved -- ISSUE A's own "recently completed" stage, never a stale gap
    else:
        return {PENDING_GOVERNED_REQUEST_STATE_KEY: None}
    pending = PendingGovernedRequest(
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        intent=contract.intent,
        status=status,
        subject=contract.subject,
        missing_context=missing,
        provided_context=list(contract.provided_context),
    )
    return {PENDING_GOVERNED_REQUEST_STATE_KEY: pending.model_dump(mode="json")}


def expire_completed_pending_on_invalid_contract(
    pending_governed_request: Optional[PendingGovernedRequest],
) -> dict[str, object]:
    """LIVE-CORR-11 FINAL SAFETY CLOSURE -- Concern B. Called ONLY when
    THIS turn produced no validated `RequestContract` at all (`chat_
    service.py`'s own `current_turn_request_contract is None` case --
    the SAME shape `derive_execution_decision` itself reports as `INVALID_
    CONTRACT`) -- the one case `build_pending_governed_request_state_
    update` is never itself invoked for (there is no `RequestExecution
    Decision`/contract to build a fresh record from in the first place).

    Deliberately narrow, NEVER a global "clear all pending state on
    INVALID_CONTRACT" rule (section 8's own explicit prohibition -- that
    would regress genuine `UNRESOLVED` clarification recovery, LIVE-
    CORR-10's own finding about transient contract-recording failures):

      - `COMPLETED` -- `PendingGovernedRequestStatus`'s own docstring is
        explicit that this stage exists ONLY to support an IMMEDIATE
        correction/re-targeting of the just-completed operation, for
        exactly one more turn. A turn whose own contract could not even
        be validated is definitionally not that immediate next turn,
        whatever the user actually said -- expires it here, the one seam
        `build_pending_governed_request_state_update`'s own "rewritten
        every turn" refresh cannot reach (it is never called this turn).
      - `UNRESOLVED`/absent -- the user still owes the SAME missing
        information regardless of one glitchy turn; left completely
        untouched -- an EMPTY update (no key at all), mirroring this
        codebase's own established "empty means no change" convention
        (e.g. `governed_evidence_continuity.build_last_selected_governed_
        evidence_state_update`), never an explicit no-op write.
    """
    if pending_governed_request is not None and pending_governed_request.status == PendingGovernedRequestStatus.COMPLETED:
        return {PENDING_GOVERNED_REQUEST_STATE_KEY: None}
    return {}


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

_INVALID_CONTRACT_FALLBACK_TEXT = (
    "I don't have enough verified context for this turn to safely provide that yet. Could you clarify what you'd like me to do?"
)
"""LIVE-CORR-5 -- section 6's own explicit finding: an internal, missing/
stale `RequestContract` (`INVALID_CONTRACT`) is, by itself, NOT evidence
that the user asked about a specific alarm or governed procedure -- it
could just as easily be a genuinely ordinary conversational turn that
happened to carry a populated `TroubleshootingGuidance` from an unrelated
prior state, or any other internal condition unrelated to what the user
actually asked. `_NO_SUBJECT_FALLBACK_TEXT`'s own "which specific alarm or
governed procedure you mean" wording presumes an operational request that
was never actually established here -- this neutral text asks for
clarification without manufacturing an operational framing the contract
never established. Never changes the underlying SAFETY decision (a command
is still withheld either way) -- wording only."""

EVIDENCE_RETRIEVAL_UNAVAILABLE_TEXT = (
    "I couldn't reach governed knowledge just now, so I can't ground an answer for this. "
    "That's a problem on my side, not missing detail from you -- please try again shortly."
)
"""POST-6A -- the deterministic text for
`EvidenceAvailability.RETRIEVAL_UNAVAILABLE`.

Says three things explicitly, because the failure this closes was saying
none of them: WHAT failed (reaching governed knowledge), WHOSE fault it
is (ours), and that supplying more context will not help. The
alternative -- falling through to the missing-context clarification --
asks the engineer to keep typing detail at an outage, which wastes their
time and hides a real fault."""

_PENDING_RELATIONSHIP_UNRESOLVED_TEXT_TEMPLATE = (
    "I'm still waiting on details for your earlier request about {subject}, and I can't tell whether "
    "this message answers that or starts something new. Do you want me to continue with {subject}, "
    "or should I treat this as a separate request?"
)
_PENDING_RELATIONSHIP_UNRESOLVED_TEXT_NO_SUBJECT = (
    "I'm still waiting on details for an earlier request of yours, and I can't tell whether this "
    "message answers that or starts something new. Should I continue the earlier request, or treat "
    "this as a separate one?"
)
"""POST-6A REPAIR 4 -- the deterministic, Python-authored clarification
for `PendingContinuationRelation.UNRESOLVED`. Never model-generated, and
templated with NOTHING except the pending request's own `subject` -- the
user's own earlier operation name, already carried in the validated
contract that produced the pending record (never Knowledge content, never
a parameter value, never raw user text). Matches this codebase's own
established fixed-fallback-sentence convention exactly."""

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
    # POST-6A REPAIR 4 -- checked FIRST, ahead of the missing-context
    # template: for an UNRESOLVED relationship the outstanding question
    # is not "which unit?" (the user may well have just told us one) but
    # "is this the same request at all?" -- asking the parameter question
    # here would be unanswerable noise, and answering it would be the
    # silent-resume failure this repair closes.
    if decision.pending_relationship == PendingContinuationRelation.UNRESOLVED:
        if decision.pending_subject:
            return _PENDING_RELATIONSHIP_UNRESOLVED_TEXT_TEMPLATE.format(subject=decision.pending_subject)
        return _PENDING_RELATIONSHIP_UNRESOLVED_TEXT_NO_SUBJECT
    if decision.missing_context:
        labels = ", ".join(_safe_missing_context_label(key) for key in decision.missing_context)
        return _MISSING_CONTEXT_FALLBACK_TEXT_TEMPLATE.format(items=labels)
    if decision.status == RequestExecutionStatus.AMBIGUOUS and not decision.subject:
        return _NO_SUBJECT_FALLBACK_TEXT
    if decision.status == RequestExecutionStatus.INVALID_CONTRACT:
        # LIVE-CORR-5 -- section 6: a missing/stale contract alone is not
        # evidence the user asked about an alarm/procedure -- see
        # `_INVALID_CONTRACT_FALLBACK_TEXT`'s own docstring.
        return _INVALID_CONTRACT_FALLBACK_TEXT
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
    matching of any kind.

    LIVE-CORR-8 audit note: kept on the ORIGINAL, narrower `intent==
    RequestIntent.PROCEDURE` check, deliberately NOT widened to `request_
    class==PROCEDURE_TROUBLESHOOTING` (which would also accept `intent=
    TROUBLESHOOTING`) -- no live defect proves this specific combination
    needs to change, and an existing, explicit regression
    (`test_full_procedure_discarded_when_any_one_of_the_four_fields_
    fails`) affirmatively requires `intent=troubleshooting` to be
    DISCARDED here. Only `is_exact_command_response_permitted`, below --
    the function with a CONFIRMED live defect -- was widened."""
    return (
        decision.intent == RequestIntent.PROCEDURE
        and decision.requested_output == RequestedOutput.PROCEDURE_STEPS
        and decision.ambiguity is False
        and bool(decision.subject)
    )


def is_next_step_response_permitted(decision: RequestExecutionDecision) -> bool:
    """LIVE-CORR-3A -- `intent=TROUBLESHOOTING`, `requested_output=
    TROUBLESHOOTING_NEXT_STEP` -- the validated shape for an ordinary,
    one-diagnostic-action troubleshooting answer.

    LIVE-CORR-8 audit note: kept on the ORIGINAL, narrower intent-exact
    check for the same reason as `is_full_procedure_response_permitted`
    immediately above -- no confirmed live defect for this specific
    combination; only `is_exact_command_response_permitted` (the function
    with a confirmed live defect) was widened to use `request_class`."""
    return decision.intent == RequestIntent.TROUBLESHOOTING and decision.requested_output == RequestedOutput.TROUBLESHOOTING_NEXT_STEP


def is_exact_command_response_permitted(decision: RequestExecutionDecision) -> bool:
    """LIVE-CORR-3A -- `requested_output=EXACT_COMMAND` -- the validated
    shape for a request asking for one specific command.

    LIVE-CORR-8 -- Request Class Must Be the Authoritative Governance
    Boundary: THE direct fix for the confirmed live defect ("give me the
    exact command to list current alarms" produced `intent=procedure,
    requested_output=exact_command`, a combination the former `intent==
    RequestIntent.COMMAND` check never recognized, discarding the
    correctly-grounded response). `request_class=EXACT_COMMAND` (derived
    from `intent==COMMAND` OR `requested_output==EXACT_COMMAND` -- either
    signal is enough, mirroring DEF-0037's own "intent labels cannot
    bypass safety" argument) replaces the narrower intent-only check."""
    return (
        _resolve_request_class(decision.request_class, decision.intent, decision.requested_output, decision.subject)
        == RequestClass.EXACT_COMMAND
        and decision.requested_output == RequestedOutput.EXACT_COMMAND
    )


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

    LIVE-CORR-6 -- Valid Troubleshooting Response Incorrectly Replaced by
    Command Backstop: for `NEXT_STEP` guidance, when `decision.may_emit_
    command` is `False`, this branch previously wiped the ENTIRE guidance
    -- `interpretation`/`next_action`/`evidence_requested` included --
    UNCONDITIONALLY, regardless of whether a command was ever proposed at
    all. Since `TROUBLESHOOTING`+`TROUBLESHOOTING_NEXT_STEP` requests
    structurally NEVER get `may_emit_command=True` (LIVE-CORR-3B: command
    permission exists only for a validated `intent=COMMAND, requested_
    output=EXACT_COMMAND` request), this wiped EVERY SINGLE troubleshooting_
    next_step response's safe diagnostic content, confirmed live: "how can
    i troubleshoot: ESS Service Unavailable?" -- a fully-resolved
    (`missing_context=[]`), `status=ALLOW` troubleshooting request -- lost
    its entire grounded diagnostic answer to this branch, replaced by a
    generic exact-command-style fallback that never even applied (no
    command was ever involved).

    THE FIX reuses the SAME typed `TroubleshootingOperationalEffect`
    classification `evidence.py`'s own `enforce_structural_operational_
    integrity` already established for exactly this class of problem
    (LIVE-CORR-3A/DEF-0040) -- never a new safety framework, never text/
    keyword inspection of `interpretation`/`next_action`:

      - `operational_effect` IS (or, unset, DEFAULTS TO -- same "strictest,
        safest interpretation" rule as evidence.py's own `_effective_
        operational_effect`) `STATE_CHANGE_RECOMMENDATION`: the ENTIRE
        guidance is still suppressed, exactly as before this pass --
        UNCHANGED, non-regression-tested (`test_state_change_
        recommendation_is_withheld_without_target_confirmation`,
        `test_interpretation_cannot_bypass_may_emit_command_false`, and
        siblings) -- a state-changing recommendation carries operational
        authority that must be either fully verified (`may_emit_command=
        True`, the early-return above) or withheld ENTIRELY; a command
        smuggled into `interpretation`/`next_action` free text while
        `command` itself is left unset must still be caught, and IS,
        because the WHOLE guidance -- not merely `command` -- is removed
        whenever this classification applies and `may_emit_command` is
        `False`. A guidance that is ALREADY fully empty (e.g. `evidence.py`
        already performed this exact suppression upstream) is returned
        completely unchanged, `stripped=False` -- nothing left to strip,
        and the caller (`chat_service.py`) must not report a suppression
        that did not happen here.
      - `operational_effect` is `REFERENCE_DESCRIPTION`/`OBSERVATION`/
        `DIAGNOSTIC_READ` -- content the model itself classified as
        carrying NO operational/state-changing authority at all (a safe
        diagnostic check, an observation, descriptive text): only
        `command` is stripped, when present (defense in depth -- this
        classification should not carry one in the first place); the
        genuinely safe `interpretation`/`next_action`/`evidence_requested`
        text is preserved and reaches the user. This is the NEW behavior
        this pass adds -- exactly mirroring the SIBLING `FULL_PROCEDURE`
        branch immediately below (already correct, already tested,
        UNCHANGED by this pass), one level up.

    A command that IS present and genuinely unauthorized is STILL always
    removed, in every classification. This fix narrows WHAT gets
    suppressed for the genuinely non-operational classifications only --
    it never weakens the STATE_CHANGE_RECOMMENDATION protection, and never
    lets `operational_effect` grant command authority (that remains solely
    `decision.may_emit_command`'s job, checked before this function's body
    even runs). `evidence.py`'s own, separate, completely unchanged
    grounding layer (DEF-0024/0026/0027/LIVE-CORR-3A/3B) is unaffected
    either way -- both layers must still independently agree before a
    command reaches the user.

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

    # LIVE-CORR-6 -- see this function's own docstring for the full
    # rationale. `operational_effect` unset defaults to the strictest
    # interpretation (`STATE_CHANGE_RECOMMENDATION`), mirroring evidence
    # .py's own `_effective_operational_effect` exactly.
    effective_effect = guidance.operational_effect or TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION
    if effective_effect == TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION:
        if guidance.interpretation is None and guidance.next_action is None and guidance.command is None and guidance.evidence_requested is None:
            # Already fully empty (e.g. evidence.py's own structural-
            # integrity suppression already ran upstream) -- nothing for
            # THIS function to strip; reporting `stripped=True` here would
            # incorrectly tell the caller a NEW suppression happened.
            return guidance, False
        suppressed = guidance.model_copy(
            update={"interpretation": None, "next_action": None, "command": None, "evidence_requested": None, "full_procedure_steps": []}
        )
        return suppressed, True

    # REFERENCE_DESCRIPTION / OBSERVATION / DIAGNOSTIC_READ -- no
    # operational/state-changing authority claimed at all; strip `command`
    # only, when present (defense in depth), never the safe descriptive/
    # diagnostic text.
    if guidance.command is None:
        return guidance, False
    return guidance.model_copy(update={"command": None}), True


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
