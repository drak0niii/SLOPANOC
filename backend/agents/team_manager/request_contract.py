"""Phase 6A.13 -- Request Contract Foundation.

THE GAP THIS CLOSES: the DEF-0027 FINAL corrective pass proved that this
system's various boundaries -- Knowledge retrieval, command grounding,
governed-evidence continuity -- each independently reconstruct their OWN,
narrow guess at "what does the user actually mean right now," and a real
live defect showed the seams between those independent guesses: a
genuinely governed command (`accn FieldReplaceableUnit=RRU-9 restartunit
1 1 1`) was surfaced immediately after the user said "it's an RRU" --
NEVER having supplied the specific identifier `RRU-9` at all. The command
was grounded (DEF-0024/0027 proved it is a real, Approved, verbatim
string), but it was NOT correctly parameterized: `RRU-9` came from
governed Knowledge's own example content, not from anything the user
said. GROUNDED != CORRECTLY PARAMETERIZED.

WHAT THIS MODULE IS: the single, typed, structured record of team_
manager's OWN semantic understanding of the CURRENT request -- produced
once, by the SAME model reasoning that already exists (never a second
agent, never a second LLM call, never regex/keyword routing), and then
DETERMINISTICALLY validated and corrected by code before anything durable
is written. Mirrors this codebase's own already-proven "record_X" tool
family exactly (`conversation_target.py`'s `record_conversation_target`,
`source_requirements.py`'s `record_source_requirements`,
`case_tools.py`'s `record_case_analysis`): the model DECIDES, a plain
FunctionTool VALIDATES SHAPE, and (new to this module, because parameter
PROVENANCE is safety-critical here in a way none of those three tools
needed) a deterministic `after_tool_callback` re-verifies the model's own
claims against real, trusted text before anything is trusted further.

WHAT THIS MODULE IS NOT (6A.13's own explicit scope boundary): it does
NOT yet change what team_manager does this turn, does NOT gate `
knowledge_search`, does NOT replace `record_source_requirements`/
`IncidentManagerRequest.requires_governed_knowledge` (both remain
completely unchanged, still the authoritative signals `chat_service.py`'s
existing completion gates read), does NOT change specialist routing, and
does NOT prevent execution from an ambiguous/under-parameterized contract
-- Phase 6A.14 is the milestone that will make deterministic
execution/routing actually OBEY this contract; 6A.13 only produces,
validates, and durably stores it, exactly as instructed.

TWO SEPARATE VALIDATION LAYERS, NEVER CONFLATED:

  1. STRUCTURAL (`record_request_contract`, this module, inline): a
     malformed `intent`/`requested_output`/`provided_context` shape is
     rejected immediately, at the tool-call boundary, exactly like
     `record_conversation_target`'s own inline `target` validation --
     "Do not trust arbitrary model JSON merely because it parsed."

  2. PROVENANCE (`validate_and_persist_request_contract`, this module, an
     `after_tool_callback`): the SAFETY-CRITICAL half, and the direct fix
     for the live RRU-9 defect. A `provided_context` entry claiming
     `provenance=user` is trusted ONLY if its `value` is independently,
     deterministically verifiable against either (a) THIS turn's own
     real, literal user text (`tool_context.user_content` -- the SAME
     "this invocation's real top-level Content" guarantee `Multimodal
     AgentTool`/`evidence.capture_known_applicability_context` already
     rely on), or (b) a durable, SESSION-SCOPED store of parameters
     ALREADY verified this way on an earlier turn of the SAME
     conversation, for the SAME subject (continuation only -- an
     explicit subject change never inherits anything). Anything neither
     path can verify is silently DROPPED from what gets persisted --
     never trusted merely because the model's JSON happened to parse.

WHY A DEDICATED STATE KEY, NOT DEF-0026'S OWN
`LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY`: audited first, per
instruction ("do not create another continuity store if existing DEF-0026
state can be used as an input"). DEF-0026's own store is a narrower,
Knowledge-specific concept (which governed SECTION identity was actually
selected) -- `RequestContract` is a strictly broader concept, spanning
Teams-only requests, ACTION requests, and KNOWLEDGE_INVENTORY requests
that may never touch governed Knowledge evidence at all. Repurposing
DEF-0026's own store for this would conflate two genuinely different
questions ("which governed section was used" vs. "what did the user
actually ask, in what shape, with what parameters"), so a new, narrow,
ADDITIVE state key is used instead -- this is not the same class of
duplication the instruction warns against (DEF-0026's own machinery is
still reused wherever the underlying problem genuinely is the same, e.g.
`_verify_and_filter_provided_context`'s "trusted text or trusted
carried-forward session state, nothing else" discipline mirrors
`governed_evidence_continuity.py`'s own revalidation philosophy exactly).

SESSION ISOLATION: inherited "for free" from ADK's own existing session-
state architecture -- `tool_context.state` is ALREADY guaranteed
per-session, never cross-session, the SAME guarantee `selected_teams_
chat_id`/`last_teams_evidence`/DEF-0026's own store already rely on. No
new isolation mechanism was built or is needed.
"""
from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

from google.adk.tools import ToolContext
from pydantic import BaseModel, Field, ValidationError, field_validator

from backend.api.turn_context import current_run_id
from backend.gateway.safe_error import validation_error

_logger = logging.getLogger(__name__)


class RequestIntent:
    """Closed set of request intents -- plain string constants (never a
    `pydantic`/`enum.Enum`) so ADK's automatic function-schema generation
    exposes `intent` to the model as a simple string parameter, exactly
    mirroring `conversation_target.py`'s own established, ADK-proven
    `ConversationTarget` pattern.
    """

    INFORMATION = "information"
    PROCEDURE = "procedure"
    COMMAND = "command"
    TROUBLESHOOTING = "troubleshooting"
    KNOWLEDGE_INVENTORY = "knowledge_inventory"
    ACTION = "action"


_VALID_INTENTS = frozenset(
    {
        RequestIntent.INFORMATION,
        RequestIntent.PROCEDURE,
        RequestIntent.COMMAND,
        RequestIntent.TROUBLESHOOTING,
        RequestIntent.KNOWLEDGE_INVENTORY,
        RequestIntent.ACTION,
    }
)


class RequestedOutput:
    """Closed set of desired answer shapes -- same plain-string-constant
    discipline as `RequestIntent` above. Exists so a downstream consumer
    (6A.14+) never has to re-infer "what shape of answer was actually
    wanted" from the raw question text again."""

    FACT = "fact"
    PROCEDURE_STEPS = "procedure_steps"
    EXACT_COMMAND = "exact_command"
    TROUBLESHOOTING_NEXT_STEP = "troubleshooting_next_step"
    KNOWLEDGE_LIST = "knowledge_list"
    ACTION = "action"


_VALID_REQUESTED_OUTPUTS = frozenset(
    {
        RequestedOutput.FACT,
        RequestedOutput.PROCEDURE_STEPS,
        RequestedOutput.EXACT_COMMAND,
        RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        RequestedOutput.KNOWLEDGE_LIST,
        RequestedOutput.ACTION,
    }
)


class ParameterProvenance:
    """Closed set of trust origins for one `RequestParameter`. `USER`
    covers a fact stated ANYWHERE by the user (this turn or an earlier
    one) -- the model does not need to (and cannot reliably) know which;
    `validate_and_persist_request_contract`'s own two-path verification
    (current-turn text, then session-confirmed carry-forward) is what
    actually decides whether a `USER`-provenance claim is trusted, never
    the model's own label alone. `SESSION` is available for a caller that
    already knows a value is being explicitly carried forward (used
    internally by this module's own merge logic below); the model itself
    is instructed to always use `USER` for anything the person it is
    talking to actually said."""

    USER = "user"
    SESSION = "session"


_VALID_PROVENANCE = frozenset({ParameterProvenance.USER, ParameterProvenance.SESSION})


class RequestParameter(BaseModel):
    """One user-supplied request-scoped fact -- e.g. `name="unit_type",
    value="RRU"`. NEVER a value copied from governed Knowledge/an example/
    a document merely because it looked plausible -- see this module's
    own docstring for the exact live defect this distinction closes.
    """

    name: str
    value: str
    provenance: str

    @field_validator("name", "value")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-blank string")
        return value

    @field_validator("provenance")
    @classmethod
    def _valid_provenance(cls, value: str) -> str:
        if value not in _VALID_PROVENANCE:
            raise ValueError(f"provenance must be one of: {sorted(_VALID_PROVENANCE)}")
        return value


class RequestContract(BaseModel):
    """The single, typed, authoritative record of team_manager's own
    structured interpretation of the CURRENT user request -- see this
    module's own docstring for the full design and its explicit 6A.13
    scope boundary (produced/validated/stored; Phase 6A.14 is what makes
    execution actually OBEY it -- see `request_execution_policy.py`).
    """

    intent: str
    subject: Optional[str] = None
    requested_output: str
    requires_governed_knowledge: bool = False
    requires_operational_context: bool = False
    continuation: bool = False
    provided_context: list[RequestParameter] = Field(default_factory=list)
    missing_context: list[str] = Field(default_factory=list)
    action_requested: bool = False
    approval_required: bool = False
    ambiguity: bool = False
    run_id: Optional[str] = None
    """Phase 6A.14 -- the CURRENT-TURN freshness marker. Deliberately NOT
    a parameter of `record_request_contract` (the model can never set or
    spoof it -- ADK's auto-generated tool schema is derived only from
    that function's own parameters, and this field is never one of them).
    Populated ONLY by `validate_and_persist_request_contract`, from the
    SAME trusted `current_run_id()` correlation this codebase already
    relies on everywhere else. A consumer (`request_execution_policy
    .derive_execution_decision`) MUST verify this matches the CURRENT
    turn's own trusted run identity before trusting anything else in this
    contract -- a stale, prior-turn contract must never authorize the
    current turn."""

    @field_validator("intent")
    @classmethod
    def _valid_intent(cls, value: str) -> str:
        if value not in _VALID_INTENTS:
            raise ValueError(f"intent must be one of: {sorted(_VALID_INTENTS)}")
        return value

    @field_validator("requested_output")
    @classmethod
    def _valid_requested_output(cls, value: str) -> str:
        if value not in _VALID_REQUESTED_OUTPUTS:
            raise ValueError(f"requested_output must be one of: {sorted(_VALID_REQUESTED_OUTPUTS)}")
        return value

    @field_validator("subject")
    @classmethod
    def _blank_subject_is_unresolved(cls, value: Optional[str]) -> Optional[str]:
        # A blank/whitespace-only subject is treated identically to an
        # absent one -- "unresolved," never a fake empty-string subject
        # that could later compare equal to another empty-string subject
        # across genuinely different requests.
        if value is not None and not value.strip():
            return None
        return value

    @field_validator("missing_context")
    @classmethod
    def _non_blank_missing_context(cls, values: list[str]) -> list[str]:
        for item in values:
            if not isinstance(item, str) or not item.strip():
                raise ValueError("missing_context entries must be non-blank strings")
        return values


# =============================================================================
# 6A.14 Request Parameter Consistency & Identifier Normalization
# =============================================================================
#
# THE GAP THIS CLOSES: a live-observed follow-up sequence proved that
# `provided_context` verification (above) and `missing_context` were two
# COMPLETELY INDEPENDENT signals -- `_verify_and_filter_provided_context`
# deterministically proved `unit_id` was NEVER genuinely user-confirmed,
# but `missing_context` is the model's own unmediated self-report, with
# NOTHING reconciling the two. `derive_execution_decision`
# (request_execution_policy.py) trusts `contract.missing_context` alone,
# so a model that (correctly or not) declared `missing_context=[]`
# despite `unit_id` never having been verified let a governed Knowledge
# EXAMPLE identifier (`RRU-9`) reach the user as though it were the real,
# live target. Section 3's own governing invariant: execution permission
# must be based on VERIFIED provided_context, never solely on the model's
# own declared missing_context.
#
# A SECOND, INDEPENDENT GAP: even a genuinely, explicitly user-supplied
# identifier could be silently dropped by `_verify_and_filter_provided_
# context`'s own literal substring check purely because of formatting --
# "RRU 5" (the user's own natural phrasing) is not a literal substring of
# a model-canonicalized "RRU-5" value, and vice versa.
#
# TARGET_SPECIFIC_INTENTS (moved here, public, from `request_execution_
# policy.py`'s own former private `_TARGET_SPECIFIC_INTENTS` -- ONE
# definition, reused by both this module's own reconciliation and that
# module's own execution-decision gate, never two independently-drifting
# copies): intents whose PURPOSE is inherently target/procedure-specific.
# KNOWLEDGE_INVENTORY and ACTION are deliberately excluded -- both are
# governed by their own, separate, earlier-checked branches in `derive_
# execution_decision`.
#
# LIVE-CORR-2 -- DEF-0037 CORRECTIVE PASS: `INFORMATION` REMOVED from
# this set -- audited and found to be the direct root cause of DEF-0037.
# One broad, intent-only set was previously used to decide THREE
# conceptually different questions at once: (1) what shape of answer is
# being produced, (2) whether a subject/procedure is required, (3)
# whether operational output may be emitted. `INFORMATION` alone does
# NOT inherently signal target-specific/operational purpose (a plain
# "hello," `intent=information, requested_output=fact, subject=None`,
# was blanket-classified target-specific purely by this label, forcing
# the same "no resolved subject/procedure" clarification an operational
# command request gets -- session `32c5a4a5-...`, live evidence).
# `INFORMATION` remains a fully valid REQUEST_INTENT value; it simply no
# longer, on its own, triggers the subject/target-context gates below.
# This does NOT create a bypass: a request that IS actually operationally
# SHAPED (e.g. `intent=information, requested_output=exact_command`) is
# still caught by the SEPARATE, second factor, `_OPERATIONAL_OUTPUT_
# SHAPES`, below -- see `is_operationally_shaped_request`'s own docstring
# for the full "intent labels cannot bypass safety" argument.
TARGET_SPECIFIC_INTENTS = frozenset({RequestIntent.COMMAND, RequestIntent.TROUBLESHOOTING, RequestIntent.PROCEDURE})

# LIVE-CORR-2 -- DEF-0037 CORRECTIVE PASS: the SECOND, independent factor.
# `RequestedOutput` shapes that can structurally carry a live operational
# command/procedure step -- `FACT`/`KNOWLEDGE_LIST` never can (per the
# schema, neither `TroubleshootingGuidance.command` nor `full_procedure_
# steps` is populated for a plain factual/inventory answer); `ACTION` is
# governed by its own separate, earlier-checked branch. Kept deliberately
# narrow and closed, mirroring `_IDENTIFIER_CLASS_PREFIXES`'s own "a
# deliberate code change here, never inferred from free text" discipline.
_OPERATIONAL_OUTPUT_SHAPES = frozenset(
    {RequestedOutput.EXACT_COMMAND, RequestedOutput.PROCEDURE_STEPS, RequestedOutput.TROUBLESHOOTING_NEXT_STEP}
)


def is_operationally_shaped_request(intent: str, requested_output: str) -> bool:
    """LIVE-CORR-2 -- DEF-0037 CORRECTIVE PASS: the single, shared
    replacement for "is this request target-specific" -- consulted by
    BOTH the "no resolved subject/procedure" gate (`request_execution_
    policy.derive_execution_decision`) and `required_target_parameter_
    gaps` below, so the two can never independently drift.

    TRUE whenever EITHER factor signals operational purpose: `intent` is
    one of `TARGET_SPECIFIC_INTENTS` (COMMAND/TROUBLESHOOTING/PROCEDURE --
    the model's own semantic classification of PURPOSE; `INFORMATION` is
    deliberately excluded, see that frozenset's own DEF-0037 docstring),
    OR `requested_output` is one of `_OPERATIONAL_OUTPUT_SHAPES` (the
    ANSWER SHAPE actually being produced). This is a deliberate OR, not
    an AND: instruction section 5's own explicit non-negotiable is that
    "intent labels cannot be used to bypass operational safety" --
    `intent=information, requested_output=exact_command` (a mislabeled-
    but-still-command-shaped request) and `intent=command, requested_
    output=fact` (an operational intent that happened to declare a
    factual answer shape) must BOTH remain restrictively gated; only a
    request that is NEITHER operationally-INTENDED nor operationally-
    SHAPED (the ordinary "hello"/"what can you do?" case) is exempt.
    `KNOWLEDGE_INVENTORY`/`ACTION` are excluded from `TARGET_SPECIFIC_
    INTENTS` and never appear in `_OPERATIONAL_OUTPUT_SHAPES` either --
    both remain governed exclusively by their own, separate, earlier-
    checked branches in `derive_execution_decision`, unaffected by this
    function.
    """
    return intent in TARGET_SPECIFIC_INTENTS or requested_output in _OPERATIONAL_OUTPUT_SHAPES


_TARGET_TYPE_PARAMETER_NAME = "unit_type"
_TARGET_IDENTIFIER_PARAMETER_NAME = "unit_id"
"""Section 4/5's own explicit instruction: reuse the ALREADY-ESTABLISHED
`unit_type`/`unit_id` vocabulary (the only parameter names this codebase's
own prompt/tests currently use for a live operational target), rather
than inventing a broader ontology. This is a SMALL, deliberately CLOSED,
documented, extensible mapping -- currently exactly one target-parameter
pair. Extending it to a different domain concept requires a deliberate
code change here, never an inference from free text."""


def required_target_parameter_gaps(
    intent: str, requested_output: str, provided_context: Sequence[RequestParameter]
) -> list[str]:
    """Section 4/5's own deterministic target-parameter rule: for an
    operationally-shaped request (`is_operationally_shaped_request`)
    whose own VERIFIED `provided_context` establishes a target TYPE
    (`unit_type`) that itself requires an IDENTIFIER before a live
    command can concern one specific unit -- but no `unit_id` is yet
    present -- the identifier is deterministically required, REGARDLESS
    of what the model itself declared in `missing_context`.

    "Requires an identifier" is deliberately narrower than "any `unit_
    type` value at all": reuses the SAME small, closed `_IDENTIFIER_
    CLASS_PREFIXES` set the identifier normalizer already defines (RRU/
    AAS) -- a `unit_type` value OUTSIDE that set (e.g. a real governed
    `"SupportUnit"` branch, whose own real content is "No restart" with
    no per-unit command at all) never triggers this rule; there is
    nothing to identify -- that is a real, VERIFIED fact, not an
    unresolved gap.

    LIVE-CORR-2 -- DEF-0038 CORRECTIVE PASS (bounded foundation, NOT a
    full/final fix -- see `docs/DEFECT_REGISTER.md`'s own DEF-0038 entry
    for why): live evidence proved this rule was NON-MONOTONIC --
    supplying NO information about the target (`unit_type` entirely
    absent) previously returned `[]` (fully permissive), while supplying
    PARTIAL information (`unit_type="RRU"`, no `unit_id`) correctly
    returned `["unit_id"]` (restricted) -- the opposite of the required
    "less context must never grant MORE permission than partial context"
    invariant. This function has NO deterministic signal, within this
    milestone's own strict scope, for whether the specific governed
    operation actually selected this turn requires a target at all (that
    would require coupling this policy to Knowledge-evidence selection
    state -- explicitly DEF-0040/6A.20 scope, not this pass's). Rather
    than guess, the bounded, fail-closed interim rule below treats
    "`unit_type` never even stated" AT LEAST as restrictively as
    "`unit_type` stated but `unit_id` missing" -- but ONLY for
    `RequestedOutput.EXACT_COMMAND` (the confirmed shape of the live
    DEF-0038 defect, and the one output shape that can carry a raw,
    ready-to-run command string) -- never for `PROCEDURE_STEPS`/
    `TROUBLESHOOTING_NEXT_STEP`, which can legitimately describe a
    procedure's branches conceptually without yet committing to one
    target (see `test_procedure_can_still_describe_branches_when_fully_
    resolved`, unchanged). A future milestone with access to the
    selected governed operation's own target-cardinality classification
    (whether it is genuinely target-independent, e.g. a read-only
    discovery lookup) can safely relax this -- until then, DEF-0038
    remains open at the step-aware precision layer even though this
    monotonicity gap is closed.
    """
    if not is_operationally_shaped_request(intent, requested_output):
        return []
    provided_names = {param.name for param in provided_context}
    if _TARGET_IDENTIFIER_PARAMETER_NAME in provided_names:
        return []
    unit_type_value = next(
        (param.value for param in provided_context if param.name == _TARGET_TYPE_PARAMETER_NAME), None
    )
    if unit_type_value is not None:
        if unit_type_value.strip().upper() in _IDENTIFIER_CLASS_PREFIXES:
            return [_TARGET_IDENTIFIER_PARAMETER_NAME]
        # A confirmed unit_type OUTSIDE the identifier-bearing class (e.g.
        # "SupportUnit") is a real, verified fact establishing no
        # identifier is needed -- not a gap.
        return []
    if requested_output == RequestedOutput.EXACT_COMMAND:
        return sorted([_TARGET_TYPE_PARAMETER_NAME, _TARGET_IDENTIFIER_PARAMETER_NAME])
    return []


def reconcile_missing_context(
    intent: str,
    requested_output: str,
    verified_provided_context: Sequence[RequestParameter],
    model_declared_missing_context: Sequence[str],
) -> list[str]:
    """THE direct fix for the live RRU-9-before-confirmation defect.
    Deterministically reconciles the model's own declared `missing_
    context` against VERIFIED `provided_context` (Section 6's own four
    rules):

      A. every model-declared missing key whose name is NOT already
         satisfied by a verified value survives unchanged.
      B. `required_target_parameter_gaps` adds any deterministically-
         required key the model omitted, regardless of the model's own
         claim.
      C. a key is removed (never carried into the result) once its own
         name genuinely appears in `verified_provided_context` -- this
         function itself never re-verifies anything; it trusts ONLY the
         already-verified list the caller supplies.
      D/E. Knowledge examples and assistant prose can never satisfy a
         key here, structurally -- `verified_provided_context` (built by
         `_verify_and_filter_provided_context`) can never contain a
         Knowledge-sourced or prose-sourced value in the first place.
      F. a stale/prior-turn session value can only ever appear in
         `verified_provided_context` via `_verify_and_filter_provided_
         context`'s own existing same-subject-continuation gate --
         unchanged, untouched by this function.

    Returns a sorted, deduplicated list -- deterministic output for
    identical input, regardless of the model's own key ordering.
    """
    verified_names = {param.name for param in verified_provided_context}
    reconciled = {name for name in model_declared_missing_context if name not in verified_names}
    reconciled.update(required_target_parameter_gaps(intent, requested_output, verified_provided_context))
    return sorted(reconciled)


_IDENTIFIER_CLASS_PREFIXES = ("RRU", "AAS")
"""Section 5's own explicit instruction: a SMALL, deliberately CLOSED,
documented, extensible set of recognized operational unit-class prefixes
this milestone's identifier normalizer understands -- currently the two
prefixes this codebase's own real governed corpus and live-reported
defects actually use. Adding a new class (e.g. a different equipment
family) requires a deliberate code change here, never inferred from free
text or Knowledge content."""

_IDENTIFIER_STRIP_CHARS = ".,;:()[]{}\"'?!"
"""Light trailing/leading punctuation trimming only -- mirrors `evidence
.py`'s own established `_tokenize` discipline exactly, never a general
NLP/regex tokenizer."""


def extract_canonical_identifiers(text: Optional[str]) -> set[str]:
    """Deterministic, token/boundary-safe extraction of recognized
    operational unit identifiers (e.g. `RRU-5`, `AAS-3`) from raw text --
    no `re`, no fuzzy/semantic matching, no bare-numeric-alone inference
    (Section 8's own explicit "prefer the stricter behavior" instruction).

    Recognizes exactly two written forms per class, both requiring the
    class prefix to be immediately, structurally adjacent to the number
    (never merely present somewhere else in the same text):
      1. ONE token spelling both together, hyphenated or not
         ("RRU-5", "RRU5", "rru-5", "rru5").
      2. TWO adjacent tokens -- a bare prefix token immediately followed
         by a purely-numeric token ("RRU 5", "rru 5", "RRU -5").

    A number is matched ONLY when the immediately preceding token is
    exactly one of `_IDENTIFIER_CLASS_PREFIXES` (form 2) or fused onto it
    (form 1) -- "RRU 5 not 9" therefore yields `{"RRU-5"}` only ("9" has
    no adjacent class-prefix token), "AAS 3" never yields an `RRU-*`
    match, and "RRU 15" yields `{"RRU-15"}`, never `{"RRU-5"}` (exact,
    whole-token digit extraction, never substring containment).
    """
    if not text:
        return set()

    raw_tokens = [token.strip(_IDENTIFIER_STRIP_CHARS) for token in text.split()]
    tokens = [token for token in raw_tokens if token]

    found: set[str] = set()
    for index, token in enumerate(tokens):
        token_upper = token.upper()
        for prefix in _IDENTIFIER_CLASS_PREFIXES:
            if token_upper.startswith(prefix):
                remainder = token_upper[len(prefix):]
                remainder = remainder[1:] if remainder.startswith("-") else remainder
                if remainder and remainder.isdigit():
                    found.add(f"{prefix}-{remainder}")
                    continue
            if token_upper == prefix and index + 1 < len(tokens):
                next_token = tokens[index + 1]
                next_clean = next_token[1:] if next_token.startswith("-") else next_token
                if next_clean and next_clean.isdigit():
                    found.add(f"{prefix}-{next_clean}")

    return found


def _canonicalize_if_identifier(value: str) -> str:
    """If `value` resolves to EXACTLY ONE recognized operational
    identifier, returns its canonical hyphenated form (`RRU-5`);
    otherwise returns `value` completely unchanged -- never mutates a
    value that is not unambiguously identifier-shaped (e.g. `unit_type`'s
    own bare `"RRU"` value, which resolves to zero matches, and any
    genuinely free-text value)."""
    identifiers = extract_canonical_identifiers(value)
    if len(identifiers) == 1:
        (only,) = identifiers
        return only
    return value


async def record_request_contract(
    intent: str,
    requested_output: str,
    subject: Optional[str] = None,
    requires_governed_knowledge: bool = False,
    requires_operational_context: bool = False,
    continuation: bool = False,
    provided_context: Optional[list[RequestParameter]] = None,
    missing_context: Optional[list[str]] = None,
    action_requested: bool = False,
    approval_required: bool = False,
    ambiguity: bool = False,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Record your own structured interpretation of the CURRENT request --
    see "REQUEST CONTRACT" in prompts.py for exactly when and how to call
    this. Deterministic code (never you) decides what happens next; this
    call does not by itself change your own behavior this turn.

    Args:
      intent: Exactly one of "information", "procedure", "command",
        "troubleshooting", "knowledge_inventory", "action" -- your own
        semantic judgment, never a keyword/phrase match.
      requested_output: The shape of answer you are about to give --
        exactly one of "fact", "procedure_steps", "exact_command",
        "troubleshooting_next_step", "knowledge_list", "action".
      subject: The real procedure/topic/alarm this request concerns, in
        its own governed name if you know it (e.g. "HW Partial Fault") --
        leave unset if you genuinely cannot tell; never invent one.
      requires_governed_knowledge: True when current governed knowledge is
        a required source for this request -- your own independent
        judgment; setting this here does not replace `record_source_
        requirements`, which you must still call separately.
      requires_operational_context: True when this request needs CURRENT
        operational context (e.g. a Teams conversation's live content) as
        opposed to only static governed knowledge.
      continuation: True when this request builds on the SAME subject as
        an earlier turn in this conversation (e.g. a follow-up naming no
        new topic itself).
      provided_context: Facts the USER THEMSELVES actually stated -- this
        turn or an earlier one you are continuing -- never a value you
        recall from governed knowledge, an example, or a document. Each
        entry's `value` must be something the user's own words genuinely
        establish; `provenance` is always "user" for a fact the person
        actually said. An identifier the user never actually supplied
        (e.g. a specific unit id from a governed example) belongs in
        `missing_context`, never invented here.
      missing_context: Plain names of information this request still
        needs before it could be fully answered/executed (e.g.
        "unit_id") -- never fabricated from a governed example merely
        because a plausible-looking value exists somewhere in Knowledge.
      action_requested: True for a request asking you to perform a Teams
        write action.
      approval_required: Your own judgment of whether approval is needed
        -- deterministic code always enforces approval for any real write
        regardless of what you set here; this field is informational only.
      ambiguity: True when you genuinely cannot resolve the subject/
        procedure this request concerns (e.g. "give me the command" with
        no active procedure) -- set this rather than guessing.
      tool_context: Auto-injected by ADK in real use (never supplied by
        the model).

    Returns:
      On success, the recorded contract's own fields (structurally
      validated only -- see this module's own docstring for the SEPARATE,
      deterministic provenance-verification pass that runs afterward, via
      `after_tool_callback`). On failure (an unrecognized `intent`/
      `requested_output`, or a malformed `provided_context`/
      `missing_context` entry), a dict with a single `error` key --
      reconsider the contract rather than retrying the same shape.
    """
    try:
        contract = RequestContract(
            intent=intent,
            subject=subject,
            requested_output=requested_output,
            requires_governed_knowledge=bool(requires_governed_knowledge),
            requires_operational_context=bool(requires_operational_context),
            continuation=bool(continuation),
            provided_context=provided_context or [],
            missing_context=missing_context or [],
            action_requested=bool(action_requested),
            approval_required=bool(approval_required),
            ambiguity=bool(ambiguity),
        )
    except ValidationError as exc:
        message = exc.errors()[0]["msg"] if exc.errors() else "Invalid request contract."
        return {"error": validation_error(f"Invalid request contract: {message}").safe_error.to_dict()}

    return contract.model_dump(mode="json")


REQUEST_CONTRACT_TOOL_NAME = "record_request_contract"

VALIDATED_REQUEST_CONTRACT_STATE_KEY = "validated_request_contract"
"""Plain, OVERWRITABLE session-state value (mirrors `state_sync.py`'s own
"current continuity anchor" pattern, NOT `turn_source_references.py`'s
per-turn ACCUMULATING dict) -- holds the LATEST deterministically
validated+provenance-corrected `RequestContract` (as
`.model_dump(mode="json")`) for this session. Written only by
`validate_and_persist_request_contract`, below."""


def _extract_current_turn_user_text(tool_context: Any) -> Optional[str]:
    """The literal text of THIS invocation's own top-level user message --
    team_manager is the ROOT agent, so `tool_context.user_content` here is
    exactly what the person typed this turn (the same public, documented
    `ToolContext.user_content` property `MultimodalAgentTool`/`evidence
    .capture_known_applicability_context` already rely on for the
    identical guarantee, applied here to team_manager's own top-level
    call instead of a nested one)."""
    user_content = getattr(tool_context, "user_content", None)
    parts = getattr(user_content, "parts", None) if user_content else None
    if not parts:
        return None
    text = "".join(part.text for part in parts if getattr(part, "text", None))
    return text or None


def _verify_and_filter_provided_context(
    provided_context: Sequence[RequestParameter],
    current_turn_text: Optional[str],
    session_confirmed: Mapping[str, str],
) -> list[RequestParameter]:
    """THE direct fix for the live RRU-9 defect. A `provided_context`
    entry is kept ONLY if its `value` is independently verifiable --
    a literal, case-insensitive substring of THIS turn's own real user
    text, or an EXACT match (case-insensitive) of a value already
    confirmed for the SAME parameter `name` earlier in this same,
    same-subject conversation (`session_confirmed`, populated by the
    caller ONLY when `continuation` is true and the subject genuinely
    matches -- see `validate_and_persist_request_contract`). Never a
    fuzzy/semantic match -- mirrors `governed_evidence_continuity.py`'s
    own "compare only against real, already-trusted text" discipline.
    Anything neither path can verify is silently DROPPED -- never
    trusted merely because the model's own JSON happened to parse.

    6A.14 Request Parameter Consistency & Identifier Normalization: a
    THIRD verification path handles the case where the literal spelling
    differs only by recognized identifier-class formatting (a space
    where the model wrote a hyphen, or vice versa) -- "RRU 5" (the
    user's own natural phrasing) verifies a model-claimed "RRU-5" value,
    and the reverse. This is deterministic, token/boundary-safe exact
    identifier matching (`extract_canonical_identifiers`) -- never fuzzy,
    never bare-numeric-alone inference. Whenever a param verifies via ANY
    path, its stored `value` is canonicalized (`_canonicalize_if_
    identifier`) ONLY when it is unambiguously identifier-shaped --
    `unit_type`'s own bare `"RRU"` value and any genuinely free-text
    value pass through byte-for-byte unchanged.
    """
    current_lower = current_turn_text.lower() if current_turn_text else ""
    current_turn_identifiers = extract_canonical_identifiers(current_turn_text)
    verified: list[RequestParameter] = []
    for param in provided_context:
        value_lower = param.value.lower()
        canonical_candidate = _canonicalize_if_identifier(param.value)

        if value_lower and value_lower in current_lower:
            verified.append(param.model_copy(update={"value": canonical_candidate}))
            continue

        confirmed_value = session_confirmed.get(param.name)
        if confirmed_value is not None and confirmed_value.lower() == value_lower:
            verified.append(param.model_copy(update={"value": canonical_candidate}))
            continue

        # Identifier-class matching applies whenever `param.value` is
        # itself unambiguously identifier-shaped -- regardless of whether
        # canonicalization happened to change the spelling (the model may
        # already have submitted the canonical form directly, e.g.
        # "RRU-5", in which case `canonical_candidate == param.value`, but
        # this path must still run: the substring check above only fails
        # because the USER's own raw text used a DIFFERENT, equally valid
        # spelling, e.g. "RRU 5").
        is_identifier_shaped_value = len(extract_canonical_identifiers(param.value)) == 1
        if is_identifier_shaped_value:
            if canonical_candidate in current_turn_identifiers:
                verified.append(param.model_copy(update={"value": canonical_candidate}))
                continue
            if confirmed_value is not None and canonical_candidate == _canonicalize_if_identifier(confirmed_value):
                verified.append(param.model_copy(update={"value": canonical_candidate}))
                continue

        _logger.info(
            "request_contract: dropping unverifiable provided_context entry name=%r "
            "(neither current-turn text, session-confirmed state, nor identifier-class "
            "normalization could establish it)",
            param.name,
        )
    return verified


def validate_and_persist_request_contract(
    tool: Any, args: dict[str, Any], tool_context: Any, tool_response: Any
) -> None:
    """ADK `after_tool_callback` for team_manager (wired in agent.py) --
    the DETERMINISTIC provenance-verification and session-continuity
    layer this module's own docstring describes. Always returns `None`:
    a pure side effect (mirrors `state_sync.sync_incident_manager_result_
    to_state`'s own established "never replace what the LLM sees"
    contract exactly) -- this NEVER rewrites the tool's own visible
    response, only writes trusted state for later/future consumption.

    A structurally invalid `tool_response` (the tool's own inline
    validation already failed, or -- defensively, never assuming the
    tool's own success claim alone -- a response that still fails
    `RequestContract.model_validate` here) leaves `VALIDATED_REQUEST_
    CONTRACT_STATE_KEY` completely UNTOUCHED for this turn: a malformed
    contract is never partially trusted, and the PRIOR turn's own last
    validated contract (if any) is never overwritten by garbage.
    """
    if getattr(tool, "name", None) != REQUEST_CONTRACT_TOOL_NAME:
        return None
    if not isinstance(tool_response, dict) or "error" in tool_response:
        return None

    try:
        contract = RequestContract.model_validate(tool_response)
    except ValidationError:
        _logger.warning("request_contract: tool_response failed re-validation in after_tool_callback -- discarding")
        return None

    prior_contract: Optional[RequestContract] = None
    prior_raw = tool_context.state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY)
    if isinstance(prior_raw, dict):
        try:
            prior_contract = RequestContract.model_validate(prior_raw)
        except ValidationError:
            prior_contract = None

    # Session-confirmed carry-forward is available ONLY for a genuine
    # same-subject continuation -- an explicit topic change (a different
    # `subject`, or no prior contract at all) starts completely fresh,
    # never inheriting a previously confirmed unit_type/unit_id/etc.
    session_confirmed: dict[str, str] = {}
    if (
        contract.continuation
        and prior_contract is not None
        and prior_contract.subject
        and contract.subject
        and prior_contract.subject.strip().lower() == contract.subject.strip().lower()
    ):
        session_confirmed = {param.name: param.value for param in prior_contract.provided_context}

    current_turn_text = _extract_current_turn_user_text(tool_context)
    verified_this_turn = _verify_and_filter_provided_context(contract.provided_context, current_turn_text, session_confirmed)

    merged_by_name: dict[str, RequestParameter] = {
        name: RequestParameter(name=name, value=value, provenance=ParameterProvenance.SESSION)
        for name, value in session_confirmed.items()
    }
    for param in verified_this_turn:
        merged_by_name[param.name] = param  # this turn's own verified value always wins over a carried-forward one

    # Deterministic consistency corrections -- never trust the model
    # alone for either of these (section 12's own explicit requirements):
    final_continuation = contract.continuation
    final_ambiguity = contract.ambiguity
    if final_continuation and not contract.subject:
        # A claimed continuation with no resolvable subject at all is
        # internally inconsistent -- there is nothing valid to continue.
        final_continuation = False
        final_ambiguity = True

    final_approval_required = contract.approval_required
    if contract.intent == RequestIntent.ACTION or contract.action_requested:
        # ACTION can never silently omit approval semantics -- forced
        # true deterministically, mirroring this codebase's own existing
        # "no model-controlled approval, ever" trust principle exactly
        # (docs/AGENT_CONTRACT.md; the real approval GATE remains the
        # existing, completely unchanged `backend/approval/` boundary --
        # this field is this contract's own honest record of that fact,
        # never a second approval mechanism).
        final_approval_required = True

    final_provided_context = list(merged_by_name.values())

    # 6A.14 Request Parameter Consistency -- Section 3's own core
    # invariant: execution permission must be based on VERIFIED provided_
    # context, never solely on the model's own declared `missing_
    # context`. Reconciles the model's own claim against the SAME
    # `final_provided_context` this contract is about to durably store --
    # never a second, independently-computed provided_context.
    reconciled_missing_context = reconcile_missing_context(
        contract.intent, contract.requested_output, final_provided_context, contract.missing_context
    )

    # Phase 6A.14 -- the CURRENT-TURN freshness marker (see RequestContract
    # .run_id's own docstring): stamped here, from the SAME trusted
    # correlation `evidence.py`/`troubleshooting_guidance_context.py`
    # already rely on for this exact problem class -- never from the
    # model, never from `tool_response`.
    final_contract = contract.model_copy(
        update={
            "provided_context": final_provided_context,
            "missing_context": reconciled_missing_context,
            "continuation": final_continuation,
            "ambiguity": final_ambiguity,
            "approval_required": final_approval_required,
            "run_id": current_run_id(),
        }
    )

    tool_context.state[VALIDATED_REQUEST_CONTRACT_STATE_KEY] = final_contract.model_dump(mode="json")
    return None


def safe_request_contract_observability_fields(raw_contract: Any) -> Optional[dict[str, Any]]:
    """Section 16 (Observability) -- a SAFE projection of a validated
    contract for logging/diagnostics: intent/subject/requested_output/
    continuation/requires_governed_knowledge/requires_operational_context/
    ambiguity, plus only the KEY NAMES of `provided_context`/`missing_
    context` -- never a parameter VALUE (which may be real operational
    content). Returns `None` for anything that is not a valid, already-
    validated contract dict (never fabricates a log line from garbage).
    """
    if not isinstance(raw_contract, dict):
        return None
    try:
        contract = RequestContract.model_validate(raw_contract)
    except ValidationError:
        return None
    return {
        "intent": contract.intent,
        "subject": contract.subject,
        "requested_output": contract.requested_output,
        "continuation": contract.continuation,
        "requires_governed_knowledge": contract.requires_governed_knowledge,
        "requires_operational_context": contract.requires_operational_context,
        "ambiguity": contract.ambiguity,
        "action_requested": contract.action_requested,
        "approval_required": contract.approval_required,
        "provided_context_keys": [param.name for param in contract.provided_context],
        "missing_context_keys": list(contract.missing_context),
    }
