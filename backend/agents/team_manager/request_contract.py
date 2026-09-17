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

LIVE-CORR-8 -- REQUEST CLASS, AND HOW THE EXISTING FIELDS MAP TO THE
CONCEPTUAL `target`/`parameters` DIMENSIONS: `request_class` (below) is
the NEW, authoritative governance/risk-class dimension, deliberately
SEPARATE from `intent` (the semantic goal within that class) and
`requested_output` (the requested answer shape) -- see `RequestClass`'s
own docstring for the five closed classes and `derive_request_class`'s
own docstring for the deterministic derivation. This pass does NOT
introduce dedicated `target`/`parameters` fields -- doing so would be a
broader schema migration than this defect requires (per instruction,
"may establish the clean boundary without fully migrating every
persisted field"). Instead, the EXISTING structured representation
already carries both concepts, and the mapping is:

  target      = the `provided_context` entry (if any) named
                `TARGET_IDENTIFIER_PARAMETER_NAME` ("unit_id") -- the
                one CONCRETE entity/object a request may concern.
  parameters  = every OTHER `provided_context` entry (e.g. `unit_type`,
                or any other operation-specific value the user
                genuinely supplied) plus the corresponding names in
                `missing_context` for whatever has not yet been
                supplied -- see `required_target_parameter_gaps`
                (LIVE-CORR-7) for how these are derived per-operation,
                never as one generic, class-wide slot list.

`provided_context`/`missing_context` therefore already ARE the
`target`/`parameters` representation this module uses -- this docstring
documents the boundary rather than renaming the fields.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Mapping, Optional, Sequence

from google.adk.tools import ToolContext
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from backend.agents.team_manager.identifier_verification import (
    IdentifierVerificationStatus,
    TextSpan,
    canonical_identifier,
    is_identifier_shaped,
    verify_identifier_against_text,
)
from backend.knowledge.domain.operation_descriptor import (
    GovernedOperationDescriptor,
    OperationEffect,
    required_parameter_names_for_scope,
)
from backend.api.turn_context import current_run_id
from backend.gateway.safe_error import validation_error

_logger = logging.getLogger(__name__)


class RequestClass:
    """LIVE-CORR-8 -- Request Class Must Be the Authoritative Governance
    Boundary. Exactly FIVE closed, top-level governance/risk classes --
    never a sixth without explicit architectural justification.
    `RequestIntent`/`RequestedOutput` remain what they always were
    (semantic goal / answer shape) -- `request_class` is the NEW,
    SEPARATE governance dimension this milestone establishes, DERIVED
    deterministically (`derive_request_class`, below), never trusted
    merely because the model declared it.

    GENERAL_CONVERSATION -- normal conversational text only; no
      operational claim, procedure, command, or execution authority.
    OPERATIONAL_INFORMATION -- normal text or grounded operational
      facts; never automatically troubleshooting/command/execution
      authority. Target conditional.
    PROCEDURE_TROUBLESHOOTING -- grounded operational information or
      procedure/troubleshooting steps; command content remains
      SEPARATELY controlled (this class alone never grants exact-command
      authority). Target conditional on intent.
    EXACT_COMMAND -- an exact command, ONLY once the operation is
      resolved, required target/parameters (if applicable) are
      resolved, and grounding/applicability agree. Target usually, not
      universally, required. Granting this class alone never grants
      execution authority.
    ACTION -- actual execution; requires target, parameters,
      applicability, permission, tool availability, risk controls, and
      approval where required, before policy may ever return EXECUTE.

    `KNOWLEDGE_INVENTORY` (a legacy `RequestIntent`/`RequestedOutput`
    concept, kept for backwards compatibility) maps into
    `OPERATIONAL_INFORMATION` -- it never becomes a sixth class.
    """

    GENERAL_CONVERSATION = "general_conversation"
    OPERATIONAL_INFORMATION = "operational_information"
    PROCEDURE_TROUBLESHOOTING = "procedure_troubleshooting"
    EXACT_COMMAND = "exact_command"
    ACTION = "action"


_VALID_REQUEST_CLASSES = frozenset(
    {
        RequestClass.GENERAL_CONVERSATION,
        RequestClass.OPERATIONAL_INFORMATION,
        RequestClass.PROCEDURE_TROUBLESHOOTING,
        RequestClass.EXACT_COMMAND,
        RequestClass.ACTION,
    }
)


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


class PendingRequestRelationship:
    """POST-6A REPAIR 4 -- the closed vocabulary for the model's OWN
    declaration of how the CURRENT message relates to a governed request
    the runtime is still waiting on (a `PendingGovernedRequest`, below).

    A DECLARATION, NEVER AN AUTHORITY: this value can only ever NARROW
    what happens, never widen it. `resolve_pending_continuation`
    (request_execution_policy.py) grants continuation authority strictly
    from its own deterministic structural signals -- `ANSWERS_PENDING`
    alone never resumes anything, and a declaration that contradicts
    those signals resolves to "unresolved" (ask the user), never to a
    silent resume. `CANCELS_PENDING`/`NEW_REQUEST` only ever DROP pending
    authority, which is the fail-safe direction, so they are honored
    directly.

    UNKNOWN (the default, and what every pre-existing caller/contract
    produces) means the model said nothing -- identical behavior to
    before this repair for every shape where the structural signals
    already agree.
    """

    ANSWERS_PENDING = "answers_pending"
    """This message supplies or corrects information the assistant asked
    for in order to finish the pending request."""

    CANCELS_PENDING = "cancels_pending"
    """The user explicitly abandoned/withdrew the pending request."""

    NEW_REQUEST = "new_request"
    """A genuinely new, self-contained request -- whatever it mentions,
    it is not continuing the pending one."""

    UNKNOWN = "unknown"
    """Not declared (no pending request described to the model, or the
    model could not tell)."""


VALID_PENDING_REQUEST_RELATIONSHIPS = frozenset(
    {
        PendingRequestRelationship.ANSWERS_PENDING,
        PendingRequestRelationship.CANCELS_PENDING,
        PendingRequestRelationship.NEW_REQUEST,
        PendingRequestRelationship.UNKNOWN,
    }
)


class TargetConfirmation(str, Enum):
    """POST-6A PROMPT 3 -- how strongly this turn established that the
    parameter names the target the user actually intends to act on.

    THE GAP THIS CLOSES: repair 1 made mention-matching exact and removed
    negated/excluded/quoted mentions. But "the text mentions RRU-3 and we
    recognized no negation marker" is evidence that the value APPEARS --
    it is NOT evidence that the user INTENDS this unit as the target of a
    state-changing operation. Treating the absence of a recognized
    negation marker as positive confirmation is precisely the fail-open
    this vocabulary removes: the closed marker table is bounded, so
    absence of a marker means "we recognized nothing", never "we verified
    intent".

    MENTIONED -- the value is genuinely, exactly present in the user's own
      text (or carried forward from a same-subject turn), with no
      recognized exclusion. Enough for read-only/diagnostic use, and
      enough to carry the value forward. NOT enough to act on.
    CONFIRMED -- the runtime ASKED for this specific parameter and the
      user ANSWERED. That is a structured confirmation event the runtime
      itself originated, not an inference over prose. Required before a
      governed STATE_CHANGE operation may be authorized.

    A model DECLARATION alone never reaches CONFIRMED -- see
    `resolve_pending_continuation`, which is the only place the upgrade
    happens, and only for a parameter the runtime's own outstanding
    clarification actually named.
    """

    MENTIONED = "mentioned"
    CONFIRMED = "confirmed"


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
    source_span: Optional[TextSpan] = Field(
        default=None,
        description=(
            "POST-6A REPAIR 1 -- character offsets of the exact mention in the CURRENT turn's own user text that "
            "verified this value, when verification came from that text. Populated ONLY by "
            "`validate_and_persist_request_contract`'s own deterministic verification (never by the model, which "
            "has no way to set it: it is not a `record_request_contract` parameter). `None` for a value carried "
            "forward from a prior same-subject turn, and for any non-identifier value whose verification path does "
            "not produce a span."
        ),
    )
    confirmation: TargetConfirmation = Field(
        default=TargetConfirmation.MENTIONED,
        description=(
            "POST-6A PROMPT 3 -- whether this parameter is merely present in the user's own words "
            "(MENTIONED) or was explicitly confirmed in answer to the runtime's own request for it "
            "(CONFIRMED). Server-populated only; the model cannot set it (it is not a "
            "`record_request_contract` parameter). A governed STATE_CHANGE operation requires CONFIRMED."
        ),
    )
    corrects_prior_value: Optional[str] = Field(
        default=None,
        description=(
            "POST-6A REPAIR 1/5 -- the DIFFERENT value previously confirmed for this same parameter name in this "
            "same conversation, when this turn's own verified value replaces it (e.g. `unit_id` corrected from "
            "`RRU-3` to `RRU-10`). Server-populated only. Downstream, this is what lets a command candidate or a "
            "pending approval bound to the OLD target be deterministically invalidated rather than silently reused "
            "-- see `command_candidate_binding.py`."
        ),
    )

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
    pending_request_relationship: str = PendingRequestRelationship.UNKNOWN
    """POST-6A REPAIR 4 -- the model's OWN declaration of how this
    message relates to a still-pending governed request it was shown (see
    `PendingRequestRelationship`). Purely a narrowing signal: it can
    cancel or disown a pending request, and it can disambiguate an
    otherwise-unresolvable relationship, but it can NEVER by itself cause
    an old operation to be resumed -- that remains the exclusive job of
    `resolve_pending_continuation`'s own deterministic structural
    signals (request_execution_policy.py)."""
    request_class: Optional[str] = None
    """LIVE-CORR-8 -- the authoritative governance class. Mirrors `run_id`
    's own established "deliberately NOT a parameter of `record_request_
    contract` -- the model can never set or spoof it" pattern exactly:
    ADK's auto-generated tool schema is derived only from that function's
    own parameters, and this field is never one of them. Populated
    ENTIRELY server-side, by `validate_and_persist_request_contract`,
    from `derive_request_class`'s own deterministic output -- never
    trusted from the model, because there is nothing FOR the model to
    set in the first place. See `RequestClass`'s own docstring for the
    five governance classes, and `derive_request_class`'s own docstring
    for exactly how `intent`/`requested_output`/`action_requested`/
    `subject` combine to produce it."""
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

    @field_validator("request_class")
    @classmethod
    def _valid_request_class(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and value not in _VALID_REQUEST_CLASSES:
            raise ValueError(f"request_class must be one of: {sorted(_VALID_REQUEST_CLASSES)}")
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

    @field_validator("pending_request_relationship")
    @classmethod
    def _valid_pending_request_relationship(cls, value: str) -> str:
        if value not in VALID_PENDING_REQUEST_RELATIONSHIPS:
            raise ValueError(
                f"pending_request_relationship must be one of: {sorted(VALID_PENDING_REQUEST_RELATIONSHIPS)}"
            )
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


class RequestScope:
    """LIVE-CORR-5 -- General Conversation Must Not Trigger Operational
    Command Gating. A closed, two-value, PURELY DETERMINISTIC scope
    derived from an already-validated contract's own `intent`/
    `requested_output`/`action_requested` (never a model-set field, never
    a third source of truth to keep in sync) -- see `request_scope`,
    below, for the derivation. `GENERAL` means "this turn carries no
    operational/governed-procedure content at all" (a greeting, "who are
    you and what can you do?", a plain factual question with no subject);
    `OPERATIONAL` means "this turn's own classification concerns a
    specific governed procedure/command/troubleshooting/action/Knowledge-
    catalog request," where subject/ambiguity/target-parameter safety
    genuinely apply. This is NOT a new taxonomy -- it is a name for a
    distinction `is_operationally_shaped_request` (and, separately,
    `derive_execution_decision`'s own ACTION/KNOWLEDGE_INVENTORY branches)
    already made; see `request_scope`'s own docstring for exactly how the
    two combine.
    """

    GENERAL = "general"
    OPERATIONAL = "operational"


def request_scope(intent: str, requested_output: str, action_requested: bool = False) -> str:
    """LIVE-CORR-5 -- the single, deterministic derivation of `RequestScope`
    for an already-validated contract. Never model-set, never a free-text/
    keyword/phrase judgment of any kind -- purely a function of already-
    validated, already-closed-vocabulary `intent`/`requested_output`/
    `action_requested` fields, exactly the same inputs `is_operationally_
    shaped_request`/`derive_execution_decision`'s own ACTION/KNOWLEDGE_
    INVENTORY branches already read.

    `OPERATIONAL` whenever EITHER:
      - `is_operationally_shaped_request(intent, requested_output)` holds
        (a COMMAND/TROUBLESHOOTING/PROCEDURE intent, or a command/
        procedure/troubleshooting-shaped `requested_output` -- the same
        signal DEF-0037's own fix already established), OR
      - `intent` is `ACTION`/`KNOWLEDGE_INVENTORY`, or `action_requested`
        is `True` -- both are ALREADY governed by their own, separate,
        unconditional, earlier-checked branches in `derive_execution_
        decision` regardless of subject/ambiguity, so classifying them
        `OPERATIONAL` here preserves that existing, tested behavior
        byte-for-byte (this function only ever NARROWS which requests are
        additionally gated by `derive_execution_decision`'s own
        `contract.ambiguity` early-return -- see that function's own
        LIVE-CORR-5 comment -- never widens ACTION/KNOWLEDGE_INVENTORY's
        already-unconditional handling).

    `GENERAL` otherwise -- in practice, exactly `intent=INFORMATION,
    requested_output=FACT` with no action requested (every other
    `RequestIntent`/`RequestedOutput` combination is already claimed by
    one of the two `OPERATIONAL` conditions above): a plain conversational
    or informational exchange with no operational/governed-procedure
    purpose. A `GENERAL`-scope request legitimately has no `subject` and
    no meaningful notion of "ambiguity requiring operational
    clarification" -- see `derive_execution_decision`'s own use of this
    function for the concrete consequence.
    """
    if is_operationally_shaped_request(intent, requested_output):
        return RequestScope.OPERATIONAL
    if intent in (RequestIntent.ACTION, RequestIntent.KNOWLEDGE_INVENTORY) or action_requested:
        return RequestScope.OPERATIONAL
    return RequestScope.GENERAL


def derive_request_class(
    intent: str,
    requested_output: str,
    action_requested: bool = False,
    subject: Optional[str] = None,
) -> str:
    """LIVE-CORR-8 -- the single, PURE, deterministic derivation of
    `RequestClass` -- the authoritative governance boundary. Never model-
    set, never a free-text/keyword/phrase judgment: purely a function of
    already-validated, already-closed-vocabulary `intent`/`requested_
    output`/`action_requested`/`subject` fields.

    CONFIRMED LIVE ROOT CAUSE this closes: "give me the exact command to
    list current alarms" produced `intent=procedure, requested_output=
    exact_command` -- a combination NEITHER `is_exact_command_response_
    permitted` (required `intent==COMMAND`) NOR `is_full_procedure_
    response_permitted`/`is_next_step_response_permitted` (both require a
    DIFFERENT `requested_output`) ever authorized, so the correctly-
    grounded `EXACT_COMMAND`-shaped guidance was discarded and replaced
    with `FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT` -- text about a
    procedure the user never asked to see. `request_class` decouples
    "what governance class does this output shape belong to" from
    "which specific `RequestIntent` label the model happened to attach,"
    exactly per section 7's own "intent = semantic goal within class,
    request_class = governance dimension" split.

    Evaluated in a fixed, closed order (never ambiguous, never content-
    dependent):

      1. `ACTION` -- `intent==ACTION`, `action_requested`, or `requested_
         output==ACTION` (any one signal is enough -- mirrors `request_
         scope`'s own established "ACTION is unconditionally its own
         class" precedent).
      2. `EXACT_COMMAND` -- `intent==COMMAND` OR `requested_output==
         EXACT_COMMAND` (a deliberate OR, matching `is_operationally_
         shaped_request`'s own DEF-0037 "intent labels cannot bypass
         safety" argument: `requested_output` alone is enough regardless
         of how the model classified `intent`, and vice versa).
      3. `PROCEDURE_TROUBLESHOOTING` -- `intent` in `{TROUBLESHOOTING,
         PROCEDURE}` OR `requested_output` in `{TROUBLESHOOTING_NEXT_
         STEP, PROCEDURE_STEPS}`. THIS is the specific guarantee LIVE-
         CORR-6 depends on: selected EVIDENCE containing a command never
         enters this derivation at all (only the validated CONTRACT's own
         `intent`/`requested_output` do) -- a genuine troubleshooting
         request never becomes `EXACT_COMMAND` merely because the
         governed procedure it cites happens to contain one.
      4. `OPERATIONAL_INFORMATION` -- `intent==KNOWLEDGE_INVENTORY` OR
         `requested_output==KNOWLEDGE_LIST` (the legacy Knowledge-
         inventory concept, kept for backwards compatibility -- mapped
         here, never promoted to a sixth class, per `RequestClass`'s own
         docstring), OR a resolved `subject` (a real topic/alarm/procedure
         name the model could name -- mirrors `is_operationally_shaped_
         request`'s own already-established "resolved subject" signal,
         reused here rather than inventing a new one).
      5. `GENERAL_CONVERSATION` -- otherwise: no operational intent, no
         operational output shape, no resolved subject. In practice,
         exactly a plain conversational/informational exchange with
         nothing governed to resolve (a greeting, "who are you and what
         can you do?").

    Every branch is fail-SAFE, never fail-open: the WORST a wrong
    classification can do is grant `OPERATIONAL_INFORMATION` (still no
    command/troubleshooting/action authority) when `GENERAL_CONVERSATION`
    would have been more precise, or vice versa -- neither direction ever
    grants command, procedure, or execution authority by itself.
    """
    if intent == RequestIntent.ACTION or action_requested or requested_output == RequestedOutput.ACTION:
        return RequestClass.ACTION
    if intent == RequestIntent.COMMAND or requested_output == RequestedOutput.EXACT_COMMAND:
        return RequestClass.EXACT_COMMAND
    if intent in (RequestIntent.TROUBLESHOOTING, RequestIntent.PROCEDURE) or requested_output in (
        RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        RequestedOutput.PROCEDURE_STEPS,
    ):
        return RequestClass.PROCEDURE_TROUBLESHOOTING
    if intent == RequestIntent.KNOWLEDGE_INVENTORY or requested_output == RequestedOutput.KNOWLEDGE_LIST:
        return RequestClass.OPERATIONAL_INFORMATION
    if subject:
        return RequestClass.OPERATIONAL_INFORMATION
    return RequestClass.GENERAL_CONVERSATION


# =============================================================================
# LIVE-CORR-12C -- Fresh RequestContract on Presentation Turns
# =============================================================================
#
# THE GAP THIS CLOSES: `presentation_team_manager` (agent.py) is a
# deliberate `.model_copy()` of `team_manager` with `tools=[]` -- the R1
# FIX's own structural guarantee that a turn presenting an already-
# validated `TrustedSpecialistResult` can never re-delegate. Because
# `record_request_contract` is one of the tools stripped, THAT turn
# cannot call it -- `VALIDATED_REQUEST_CONTRACT_STATE_KEY` is therefore
# left holding whatever a PRIOR, genuinely-model-driven turn wrote,
# stamped with THAT prior turn's own `run_id`. `derive_execution_
# decision`'s freshness check (correct, and NEVER weakened by this pass --
# see that function's own docstring) then correctly rejects it as
# `INVALID_CONTRACT` for the CURRENT turn -- proven, live-reproducible,
# and root-caused by the LIVE-CORR-12A architectural audit.
#
# THE ONLY REAL TRIGGER FOR THIS PATH (audited, confirmed by exhaustive
# grep of `chat_service.py`): `specialist_result_state_written` is set
# `True` in exactly ONE place -- after a resumed `ResolvedReadContinuation`
# (a SelectionCard-driven Teams-read resumption) successfully executes,
# THIS SAME turn, via `_execute_read_continuation`. This is never a
# generic "any trusted specialist result" mechanism.
#
# THE FIX: `ResolvedReadContinuation` (backend/selection/schemas.py) is
# ALREADY a fully deterministic, server-resolved turn description --
# every field (`conversation_target`, `operation`, `selected_chat_id`/
# `selected_chat_topic`, `question`, `requested_time_range`) is copied
# verbatim from already-resolved selection state, NEVER re-derived from
# user text or a model call (see that schema's own docstring). It can
# therefore NEVER concern an operational command/action -- it is always a
# read-only presentation of Teams content a specialist call already
# retrieved and validated THIS SAME turn. `build_deterministic_read_
# continuation_contract`, below, synthesizes THIS turn's own
# `RequestContract` directly from that already-known fact -- no model
# call, no tool, no LLM involvement of any kind -- so `presentation_team_
# manager`'s own deliberately tool-free design is never touched or
# widened merely to solve this (Option B/C of the milestone instruction:
# split contract generation from presentation, using an existing,
# already-deterministic input, rather than giving the presentation agent
# back any operational tool authority).
#
# `request_class` is computed by the SAME `derive_request_class` every
# model-produced contract already uses -- never a hand-picked value --
# so this synthesized contract is subject to EXACTLY the same downstream
# governance as any other: `intent=INFORMATION`/`requested_output=FACT`/
# `subject=None` resolves to `RequestClass.GENERAL_CONVERSATION` --
# never `EXACT_COMMAND`/`ACTION`, so it can never grant command/action
# authority, and (having empty `provided_context`) it can never be
# mistaken for an answer to an unrelated, still-pending `EXACT_COMMAND`
# clarification either (see `request_execution_policy._supplies_
# context_for_pending_request`'s own `if not contract.provided_context:
# return False` guard, unchanged).
#
# `subject` IS DELIBERATELY LEFT UNSET, never the continuation's own
# `selected_chat_topic`: this exact log point (`chat_service.py`'s
# `trusted_result_presentation_mode` turn) has its own PRE-EXISTING,
# separately-tested "never log a chat id/title" safe-diagnostic contract
# (see `test_trusted_result_presentation_mode_logs_the_safe_diagnostic`,
# test_r1_r3_correctness_regression.py) -- and `RequestContract.subject`
# flows verbatim into `safe_request_contract_observability_fields`'s own
# logged projection. A real chat topic is exactly the kind of "may be
# real operational content" value that module's own docstring already
# warns never belongs in this projection. `GENERAL_CONVERSATION` is
# exactly as safe as `OPERATIONAL_INFORMATION` would have been (neither
# ever grants command/action authority) -- there is no safety reason to
# prefer the more specific class here, only a privacy reason to avoid it.
def build_deterministic_read_continuation_contract(run_id: str) -> RequestContract:
    """LIVE-CORR-12C -- the CURRENT turn's own genuinely fresh, `run_id`-
    stamped `RequestContract` for a resumed Teams-read-continuation/
    presentation-only turn -- see this section's own module-level comment
    for the full rationale, including why `subject` is deliberately left
    unset. Synthesized entirely in Python; the model never sees or
    influences this. `provided_context`/`missing_context` are
    deliberately left empty: this turn asks nothing of the user and
    confirms no target parameter.
    """
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        requires_operational_context=True,
    )
    request_class = derive_request_class(
        contract.intent, contract.requested_output, contract.action_requested, contract.subject
    )
    return contract.model_copy(update={"request_class": request_class, "run_id": run_id})


TARGET_TYPE_PARAMETER_NAME = "unit_type"
TARGET_IDENTIFIER_PARAMETER_NAME = "unit_id"
_TARGET_PARAMETER_NAMES = frozenset({TARGET_TYPE_PARAMETER_NAME, TARGET_IDENTIFIER_PARAMETER_NAME})
"""Section 4/5's own explicit instruction: reuse the ALREADY-ESTABLISHED
`unit_type`/`unit_id` vocabulary (the only parameter names this codebase's
own prompt/tests currently use for a live operational target), rather
than inventing a broader ontology. This is a SMALL, deliberately CLOSED,
documented, extensible mapping -- currently exactly one target-parameter
pair. Extending it to a different domain concept requires a deliberate
code change here, never an inference from free text.

PUBLIC (no leading underscore) since LIVE-CORR-7: mirrors `TARGET_
SPECIFIC_INTENTS`'s own established "moved here, public... reused by
both this module's own reconciliation and that module's own execution-
decision gate, never two independently-drifting copies" precedent --
`request_execution_policy.py`'s `derive_execution_decision` now also
needs these exact two names to correctly exclude them from its own
`contract.missing_context` union (see that function's own LIVE-CORR-7
comment)."""


# =============================================================================
# LIVE-CORR-12B -- Canonical Request-Context Parameter Authority
# =============================================================================
#
# THE GAP THIS CLOSES: the LIVE-CORR-12A architectural audit proved there
# was no canonical registry or alias table for `provided_context`/
# `missing_context` parameter NAMES -- only VALUES (`_canonicalize_if_
# identifier`, `extract_canonical_identifiers`) were ever normalized. The
# model was therefore free to name the SAME semantic parameter `unit_id`
# one turn and `RRU_ID` the next, and nothing anywhere recognized them as
# the same key -- live-reproduced: identical semantic input ("give me a
# command to restart an RRU" -> "the RRU is RRU-3") non-deterministically
# produced either `status=ALLOW` (when the model happened to use
# `unit_id`) or `status=AMBIGUOUS` (when it happened to use `RRU_ID`).
#
# WHAT THIS IS: a SMALL, explicit, closed alias table (`CANONICAL_
# PARAMETER_ALIASES`) plus two pure functions -- `canonical_parameter_
# name` (one name -> its canonical form, or `None` if unrecognized) and
# `authoritative_missing_context_names` (filters a `missing_context` list
# down to ONLY the names deterministic policy actually understands).
# Deliberately NOT a general ontology, NOT fuzzy/embedding matching, NOT
# LLM-assisted canonicalization -- a plain dict lookup, mirroring `_IDENTIFIER_
# CLASS_PREFIXES`'s own "a deliberate code change here, never inferred
# from free text" discipline exactly.
#
# WHAT THIS DOES NOT DO: it does not invent deterministic support for a
# parameter name merely because the model has emitted it (`vendor`,
# `technology`, `software_version`, `alarm_type`, `alarm_status`, `fault`
# were all audited -- NONE of them has any existing deterministic
# consumer anywhere in `request_contract.py`/`request_execution_policy.py`
# today, so none are added to this table; the TELCO Applicability model,
# a genuinely separate mechanism, operates on governed-document metadata
# at the KNOWLEDGE RETRIEVAL layer, never on `RequestContract` parameter
# names). A name absent from this table is NEVER promoted to policy
# authority -- see `authoritative_missing_context_names`'s own docstring.
CANONICAL_PARAMETER_ALIASES: dict[str, str] = {
    "unit_id": TARGET_IDENTIFIER_PARAMETER_NAME,
    "rru_id": TARGET_IDENTIFIER_PARAMETER_NAME,
    "unit_type": TARGET_TYPE_PARAMETER_NAME,
}
"""Lookup keys are lowercase (matched case-insensitively) -- values are
always one of the two existing canonical constants above. PUBLIC (no
leading underscore): consulted by `request_execution_policy.py` via
`authoritative_missing_context_names`, mirroring `TARGET_TYPE_PARAMETER_
NAME`/`TARGET_IDENTIFIER_PARAMETER_NAME`'s own established "shared,
never duplicated" precedent."""


def canonical_parameter_name(raw_name: str) -> Optional[str]:
    """Returns the closed-vocabulary canonical name for `raw_name`
    (case-insensitive, trimmed), or `None` when `raw_name` is not a
    recognized alias of anything this codebase's deterministic policy
    understands. Never raises, never guesses, never fuzzy-matches."""
    if not isinstance(raw_name, str):
        return None
    return CANONICAL_PARAMETER_ALIASES.get(raw_name.strip().lower())


def canonicalize_provided_context(provided_context: Sequence[RequestParameter]) -> list[RequestParameter]:
    """LIVE-CORR-12B -- collapses `provided_context` entries whose NAME is
    a recognized alias of the SAME canonical parameter down to one entry
    under its canonical name, BEFORE any value verification runs (the
    caller, `validate_and_persist_request_contract`, applies this ahead
    of `_verify_and_filter_provided_context` -- see that function's own
    call site). An entry whose name is not a recognized alias of anything
    (e.g. a genuinely free-text, non-target parameter) passes through
    completely unchanged -- this is never a general rename, only a
    closed-vocabulary merge.

    FAIL-CLOSED ALIAS CONFLICT (section 7's own explicit requirement):
    when two aliases of the SAME canonical parameter carry DIFFERENT
    values (e.g. `RRU_ID=RRU-3` and `unit_id=RRU-10`), neither is trusted
    -- both are dropped, and the canonical parameter is left completely
    ABSENT from the result, exactly as if neither alias had ever been
    supplied. This is a deliberate, minimal design choice: an absent
    canonical parameter is already the SAME "not yet resolved" state
    `required_target_parameter_gaps` already treats as a gap, so no new
    "ambiguous" concept or state is needed -- downstream policy simply,
    correctly, continues to require it. NEVER a silent pick based on
    dict/list iteration order. The identical raw VALUE supplied twice
    under two different alias names (e.g. `RRU_ID=RRU-3` and
    `unit_id=RRU-3`) is not a conflict -- it collapses to one entry.
    """
    canonical_by_name: dict[str, RequestParameter] = {}
    conflicted_names: set[str] = set()
    passthrough: list[RequestParameter] = []
    for param in provided_context:
        canonical_name = canonical_parameter_name(param.name)
        if canonical_name is None:
            passthrough.append(param)
            continue
        if canonical_name in conflicted_names:
            continue
        existing = canonical_by_name.get(canonical_name)
        if existing is None:
            canonical_by_name[canonical_name] = (
                param if param.name == canonical_name else param.model_copy(update={"name": canonical_name})
            )
            continue
        if existing.value.strip().lower() == param.value.strip().lower():
            continue  # same value via a different alias spelling -- not a conflict
        _logger.warning(
            "request_contract: conflicting aliases for canonical parameter name=%r "
            "(%r vs %r) -- dropping both, never guessing a winner",
            canonical_name,
            existing.value,
            param.value,
        )
        del canonical_by_name[canonical_name]
        conflicted_names.add(canonical_name)
    return passthrough + list(canonical_by_name.values())


def _canonicalize_missing_context_names(names: Sequence[str]) -> list[str]:
    """LIVE-CORR-12B -- applies the SAME closed alias table to model-
    declared `missing_context` NAMES (never values -- there are none to
    normalize here) before reconciliation, so a model that declares
    `missing_context=["RRU_ID"]` one turn and `["unit_id"]` the next is
    reconciled identically either way. A name that is not a recognized
    alias of anything passes through byte-for-byte unchanged -- this
    NEVER filters or drops a genuinely free-text, model-declared gap (see
    `authoritative_missing_context_names`, below, for the SEPARATE
    question of which names may drive POLICY). Deduplicates after
    canonicalization (two aliases of the same canonical name collapse to
    one entry), preserving first-seen order.
    """
    seen: set[str] = set()
    canonicalized: list[str] = []
    for name in names:
        canonical_name = canonical_parameter_name(name) or name
        if canonical_name in seen:
            continue
        seen.add(canonical_name)
        canonicalized.append(canonical_name)
    return canonicalized


def authoritative_missing_context_names(names: Sequence[str]) -> list[str]:
    """LIVE-CORR-12B -- section 8's own core fix: the ONLY subset of a
    `missing_context` list deterministic POLICY (`request_execution_
    policy.derive_execution_decision`) may treat as authoritative --
    i.e. capable of independently forcing `NEEDS_INFORMATION`. A name
    that does not resolve to a recognized canonical parameter (`canonical_
    parameter_name` returns `None`) is EXCLUDED here, regardless of how
    plausible or specific it looks (`"governed procedure"`, `"specific
    fault details"`, any future model-invented key) -- it remains fully
    present in `RequestContract.missing_context` itself (persisted,
    logged, and still rendered in an ALREADY-DECIDED `AMBIGUOUS`/`NEEDS_
    INFORMATION` decision's own fallback text via `command_suppression_
    fallback_text`, unchanged), it simply can never be the THING that
    independently causes that decision. This is the "model may propose,
    deterministic backend owns required fields" boundary made concrete:
    only a name this codebase's own deterministic policy actually
    understands may gate execution. Returns a sorted, deduplicated list.
    """
    return sorted({canonical for name in names if (canonical := canonical_parameter_name(name)) is not None})


def required_target_parameter_gaps(
    intent: str,
    requested_output: str,
    provided_context: Sequence[RequestParameter],
    grounded_command_candidate: Optional[str] = None,
    operation_descriptor: Optional[GovernedOperationDescriptor] = None,
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

    LIVE-CORR-7 -- Exact Commands Request Irrelevant Generic Context: the
    "future milestone" this docstring's own prior note anticipated.
    `grounded_command_candidate` (optional, additive, backward-compatible
    default `None`) is THIS turn's own real, already-grounded
    `TroubleshootingGuidance.command` text, when one already exists (the
    caller -- `derive_execution_decision`, via `chat_service.py`'s own
    already-captured guidance -- supplies it; this function never
    resolves it itself). Confirmed live root cause: a plain, generic
    alarm-listing request ("give me a command to check alarms present for
    Ericsson") was blanket-assigned `unit_id`/`unit_type` requirements
    purely because `requested_output == EXACT_COMMAND`, with zero
    knowledge of what the ACTUAL grounded operation (a system-wide
    listing, not a per-unit action) needed.

    LIVE-CORR-9 -- Exact-Command Continuation Loses Resolved Operation
    Identity: `grounded_command_candidate` is NOT always the surviving
    `command` field specifically -- a command evidence.py's own grounding
    correctly REJECTS (e.g. a real, live-reproduced `GROUNDING_REJECTED`:
    the model's own reconstructed proposal shared real token overlap with
    the active governed section but was not an exact verbatim match)
    leaves `TroubleshootingGuidance.command` `None`, which previously
    meant this function had NOTHING to consult and fell back to the
    generic blanket rule even though the RESOLVED operation was never
    per-unit in the first place -- conflating "was THIS specific candidate
    trustworthy enough to show the user" with "does this operation concern
    one physical unit at all," two separate questions. `chat_service.py`
    now falls back, in that specific case, to the ACTIVE governed
    SECTION's own real, already-revalidated content (via the existing 6A.14
    Active Procedure Continuity anchor, `governed_evidence_continuity.py`
    -- never a new continuity mechanism) -- this function itself is
    unchanged either way: it only ever inspects whatever text it is given
    via `command_text_references_target_identifier_class`, never
    resolving or trusting the candidate's own origin itself.

    Reuses `extract_canonical_identifiers` -- the SAME deterministic,
    non-keyword, non-regex identifier recognizer already used for
    provided-context normalization, never a new taxonomy, never text/
    keyword matching of the user's own words -- applied to the ACTUAL
    governed command CANDIDATE's own verbatim text (never the user's
    question). A real candidate containing NO recognized unit-class
    identifier at all positively proves this operation does not concern
    one specific unit -- no gap. Absence of a candidate (not yet
    grounded, or this turn never produced one) fails CLOSED to the
    EXISTING, unchanged, conservative blanket rule below -- this can only
    ever NARROW the requirement with positive structural proof, never
    widen it, and never grants MORE permission than today when no such
    proof exists. The `unit_type`-informed branches above (already
    correctly operation-aware once a `unit_type` is confirmed) are
    completely unaffected.
    """
    if not is_operationally_shaped_request(intent, requested_output):
        return []
    provided_names = {param.name for param in provided_context}

    # POST-6A PROMPT 3 -- A STATE-CHANGING GOVERNED OPERATION REQUIRES AN
    # EXPLICIT, STRUCTURED TARGET CONFIRMATION, NOT A MENTION.
    #
    # `TargetConfirmation.MENTIONED` means the value is exactly present in
    # the user's own words with no recognized exclusion marker. That is a
    # statement about the TEXT, not about INTENT -- and because the
    # exclusion vocabulary is deliberately closed and bounded, "no marker
    # recognized" can never be read as "intent verified". For an operation
    # governance positively classifies as `STATE_CHANGE`, the target must
    # instead have been CONFIRMED: the runtime asked for this specific
    # parameter and the user answered (see `resolve_pending_continuation`,
    # the only place that upgrade happens). Anything less keeps the
    # parameter in `missing_context`, which routes the turn to an explicit
    # confirmation request rather than to a command.
    #
    # Scoped deliberately: READ_ONLY and unknown-effect operations are
    # UNCHANGED -- a diagnostic read against a mentioned unit stays
    # available, and this never widens any existing permission.
    if (
        operation_descriptor is not None
        and operation_descriptor.is_approved
        and operation_descriptor.effect == OperationEffect.STATE_CHANGE
    ):
        unconfirmed = sorted(
            {
                param.name
                for param in provided_context
                if param.name in _TARGET_PARAMETER_NAMES
                and param.confirmation != TargetConfirmation.CONFIRMED
            }
        )
        missing_targets = sorted(_TARGET_PARAMETER_NAMES - provided_names)
        gaps = sorted(set(unconfirmed) | set(missing_targets))
        if gaps:
            return gaps
        return []

    if TARGET_IDENTIFIER_PARAMETER_NAME in provided_names:
        return []
    unit_type_value = next(
        (param.value for param in provided_context if param.name == TARGET_TYPE_PARAMETER_NAME), None
    )
    if unit_type_value is not None:
        normalized_unit_type = unit_type_value.strip().upper()
        if normalized_unit_type in _IDENTIFIER_CLASS_PREFIXES:
            return [TARGET_IDENTIFIER_PARAMETER_NAME]
        if normalized_unit_type in _TARGET_INDEPENDENT_UNIT_TYPES:
            # A confirmed unit_type in the small, VERIFIED allowlist (e.g.
            # "SupportUnit") is a real, verified fact establishing no
            # identifier is needed -- not a gap.
            return []
        # LIVE-CORR-3B: an unrecognized/unknown unit_type is NEITHER a
        # known identifier-bearing class NOR a positively verified
        # target-independent type -- conservatively requires the SAME
        # identifier confirmation an identifier-bearing type would, per
        # section 4's own "an unknown or other unit type must not become
        # permissive" requirement. Never treated as a verified fact merely
        # because it fails to match the identifier-bearing class.
        return [TARGET_IDENTIFIER_PARAMETER_NAME]
    if requested_output == RequestedOutput.EXACT_COMMAND:
        # POST-6A REPAIR 2 -- TARGET INDEPENDENCE MUST BE POSITIVELY
        # GOVERNED, NEVER INFERRED FROM ABSENCE.
        #
        # REMOVED: LIVE-CORR-7's own inference that "the grounded command
        # candidate contains no recognized RRU/AAS token, therefore this
        # operation does not concern a physical unit". That reasoning is
        # invalid in the one direction that matters: it cannot distinguish
        # "this operation is genuinely system-wide" from "this operation's
        # target syntax is one our closed two-prefix recognizer was never
        # given" (a different equipment family, a name-based target, an
        # index-based target, a template placeholder, ...). Absence of
        # evidence became evidence of safety, and the failure mode is a
        # state-changing command emitted with no confirmed target at all.
        # `grounded_command_candidate` is retained in the signature for
        # every existing caller, and is deliberately no longer consulted
        # for this decision.
        #
        # REPLACED BY: `required_parameter_names_for_scope`, which answers
        # ONLY from an APPROVED `GovernedOperationDescriptor` bound to the
        # governed section actually selected this turn. Its three outcomes
        # are distinct on purpose -- `()` is a positive "no target needed",
        # a tuple is the operation's own declared requirements, and `None`
        # means nothing was established (no descriptor, a CANDIDATE one,
        # or `UNKNOWN` scope), which falls through to the unchanged,
        # conservative blanket rule below. Unknown scope therefore stays
        # unresolved; it never becomes permissive.
        governed_required = required_parameter_names_for_scope(operation_descriptor)
        if governed_required is not None:
            return sorted({name for name in governed_required if name not in provided_names})
        return sorted([TARGET_TYPE_PARAMETER_NAME, TARGET_IDENTIFIER_PARAMETER_NAME])
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

_TARGET_INDEPENDENT_UNIT_TYPES = frozenset({"SUPPORTUNIT"})
"""LIVE-CORR-3B -- Operational Authority Boundary, section 4's own explicit
"do not limit target safety to RRU/AAS; an unknown or other unit type must
not become permissive" requirement. A SMALL, closed, documented allowlist
of `unit_type` values POSITIVELY VERIFIED (DEF-0030's own real, read-only
Cloud SQL audit of the governed "SupportUnit" branch: real content is "No
restart" -- there is genuinely nothing to identify) to require no
identifier. Before this pass, `required_target_parameter_gaps` treated
EVERY `unit_type` value OUTSIDE the small `RRU`/`AAS` identifier-bearing
class as equally verified-safe -- a model that declared any other string
at all (a genuine typo, a hallucinated unit-type label, or an entirely
unrecognized equipment family) silently satisfied the gate with zero
governed proof. Now only a unit_type in THIS allowlist is gap-free; every
other value -- known identifier-bearing types AND anything unrecognized --
conservatively still requires `unit_id`, since no governed step metadata
proves it is genuinely target-independent. Extending this set requires the
SAME kind of deliberate, documented, real-content verification DEF-0030
performed -- never inferred from the model's own unit_type label alone."""

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


def command_text_references_target_identifier_class(command_text: Optional[str]) -> bool:
    """LIVE-CORR-7 -- a narrower, SEPARATE counterpart to `extract_
    canonical_identifiers`, scoped specifically to real GOVERNED COMMAND
    TEXT -- never the user's own words (`extract_canonical_identifiers`
    remains the sole, unchanged recognizer for that; this function is
    never applied to it). Confirmed by direct execution against a real
    governed example command (`accn FieldReplaceableUnit=RRU-9
    restartunit 1 1 1`) that `extract_canonical_identifiers`'s own token
    rules -- built for natural language, where an identifier is never
    fused onto an unrelated word via `=` -- do NOT recognize an
    identifier embedded in a `key=value` command argument (the identifier
    is fused onto "FieldReplaceableUnit", not its own token); extending
    that function's own rules to treat `=` as a token boundary would
    change its behavior for every EXISTING natural-language caller
    (`_verify_and_filter_provided_context`), an unrelated, already-tested
    concern this pass does not touch. A dedicated, narrowly-scoped
    recognizer is safer than widening a shared one.

    Reuses the SAME closed `_IDENTIFIER_CLASS_PREFIXES` taxonomy (RRU/
    AAS) -- never a new one, never a new vocabulary. Deterministic
    token/boundary check only, no `re`, no NLP/semantic matching: splits
    on whitespace AND `=` (the one additional boundary real command
    syntax needs), then checks each resulting token for a class prefix
    immediately followed by a hyphen and digits, exactly mirroring
    `extract_canonical_identifiers`'s own fused-token rule (form 1) --
    the two-adjacent-token form (form 2) does not apply to command
    syntax, which never expresses an identifier as "RRU 9".
    """
    if not command_text:
        return False
    for raw_token in command_text.replace("=", " ").split():
        token = raw_token.strip(_IDENTIFIER_STRIP_CHARS).upper()
        for prefix in _IDENTIFIER_CLASS_PREFIXES:
            if token.startswith(prefix):
                remainder = token[len(prefix):]
                remainder = remainder[1:] if remainder.startswith("-") else remainder
                if remainder and remainder.isdigit():
                    return True
    return False


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
    pending_request_relationship: str = PendingRequestRelationship.UNKNOWN,
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
      pending_request_relationship: Only meaningful when an OUTSTANDING
        REQUEST block was supplied to you for this message. Exactly one
        of "answers_pending" (this message supplies or corrects what was
        asked for, so that request should continue), "cancels_pending"
        (the user explicitly dropped it), "new_request" (a genuinely
        separate request -- use this even when the message happens to
        mention a similar-looking identifier), or "unknown" (default --
        you were shown no outstanding request, or genuinely cannot tell;
        the application will ask the user rather than guess).
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
            pending_request_relationship=pending_request_relationship,
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


PENDING_GOVERNED_REQUEST_STATE_KEY = "pending_governed_request"
"""LIVE-CORR-11 -- Pending Governed Request Continuity and Final Command
Authority. THE GAP THIS CLOSES: a turn that leaves an `EXACT_COMMAND`
request unresolved (`RequestExecutionStatus.NEEDS_INFORMATION`) has NO
durable memory of that fact -- `derive_request_class` (above) derives
`request_class` purely from the CURRENT turn's own `intent`/`requested_
output`, so a later turn that merely answers the outstanding question
(e.g. "the RRU is rru-3") can be freshly, independently classified
`OPERATIONAL_INFORMATION`/`FACT` -- a real, live-reproduced sequence
(see `docs/DEFECT_REGISTER.md`) -- which routes team_manager's own raw
free text around BOTH existing command-safety backstops (`enforce_
execution_decision_on_guidance` has no `TroubleshootingGuidance` to
examine; `requires_unstructured_response_backstop` does not fire for an
`ALLOW`+non-operationally-shaped decision).

WHAT THIS IS: the SMALLEST additive session-state record of "a governed
request the runtime has not yet finished resolving" -- `request_class`,
`requested_output`, `subject` (the OPERATION, e.g. "restart RRU" -- never
the value-answer turn's own throwaway subject like "RRU ID"), the
governed request's own `missing_context`, and its own already-VERIFIED
`provided_context` (e.g. `unit_type=RRU`). Written ONLY by `chat_service
.py`'s own end-of-turn state delta (mirrors `governed_evidence_
continuity.build_active_governed_procedure_state_update`'s established
idiom exactly -- an additive, single-key update, never a new state-
management subsystem), from that turn's own already-computed
`RequestExecutionDecision` -- NEVER written by, or trusted from, the
model. Read ONLY by `request_execution_policy.derive_execution_decision`
(via a `pending_governed_request` parameter chat_service.py supplies from
this same key), which decides -- deterministically, never by scanning
user text -- whether the CURRENT turn's own contract is answering this
pending request or represents a genuinely new one. See `PendingGoverned
Request`'s own docstring and `resolve_effective_governed_contract`
(request_execution_policy.py) for the full mechanics.

WHAT THIS IS NOT: never a persisted command, grounding result, or
authorization -- see `PendingGovernedRequest`'s own docstring's explicit
"never persist prior command authorization" rule. `RequestClass` itself
(the five closed governance classes) is completely UNCHANGED -- this is
lifecycle state describing an unresolved (or recently-resolved, see
`PendingGovernedRequestStatus`) instance of the EXISTING `EXACT_COMMAND`
class, never a sixth class.

LIVE-CORR-11 CORRECTIVE PASS (pre-live-validation audit, four issues
found and fixed before any real traffic exercised this store):

  ISSUE A -- clearing on `ALLOW` alone left no way to safely re-target an
  IMMEDIATELY-following correction ("give me a command to restart an
  RRU" -> "RRU-3" -> ALLOW -> "actually it is RRU-10"): the correction
  turn reproduces the EXACT SAME `OPERATIONAL_INFORMATION`/`FACT`
  reclassification this whole milestone exists to close, with nothing
  left to correct AGAINST. Fixed by `PendingGovernedRequestStatus.
  COMPLETED` (below) -- a deliberately narrow, ONE-MORE-TURN-ONLY
  lifecycle stage (never a TTL/timestamp mechanism: `build_pending_
  governed_request_state_update`, request_execution_policy.py, already
  unconditionally rewrites this key EVERY turn, so a `COMPLETED` record
  is naturally superseded by whatever the VERY NEXT turn's own outcome
  is, matched or not -- no new expiry machinery needed).

  ISSUE C -- the ORIGINAL predicate never checked that supplied context
  actually RELATES to the pending request (see `PendingGovernedRequest
  Status`'s own docstring's sibling note): a genuinely independent
  question that happens to supply a same-named parameter (e.g. "what is
  the status of RRU-9?" supplying `unit_id=RRU-9` while a `restart RRU`
  request is pending with `missing_context=["unit_id"]`) could
  previously be misread as answering the pending request. Fixed in
  `request_execution_policy._supplies_context_for_pending_request`.

See that module's own updated docstrings for the full corrected
mechanics; this module's own additive schema changes are ISSUE A
(`status`) and ISSUE D (`intent`, below)."""


class PendingGovernedRequestStatus:
    """LIVE-CORR-11 CORRECTIVE PASS -- ISSUE A. Two closed lifecycle
    stages for a `PendingGovernedRequest` -- deliberately NOT a
    `RequestClass` (governance is unchanged; this is lifecycle state
    ABOUT an existing `EXACT_COMMAND`-class request, never a new
    governance dimension -- mirrors `RequestExecutionStatus`'s own
    "separate closed vocabulary, one layer removed from `RequestClass`"
    precedent).

    UNRESOLVED -- required target/parameter context is still missing
      (`NEEDS_INFORMATION`), or the request is `AMBIGUOUS` but a concrete
      operation/subject was still resolved (see `build_pending_governed_
      request_state_update`'s own docstring for exactly which `AMBIGUOUS`
      shape qualifies). A clarification-answer turn is matched against
      `missing_context` -- "does the supplied parameter relate to what is
      STILL NEEDED."
    COMPLETED -- the request reached `ALLOW` (command permission granted
      at THIS policy layer; downstream grounding/applicability is a
      SEPARATE, always-fresh question -- see `PendingGovernedRequest`'s
      own "never persist prior command authorization" rule). Retained for
      EXACTLY one more turn so an immediate correction ("actually it is
      RRU-10") can re-open the SAME operation -- matched against the
      request's own already-VERIFIED `provided_context` names -- "does
      the supplied parameter correct something ALREADY SUPPLIED." Any
      turn after that -- matched or not -- causes `build_pending_
      governed_request_state_update` to rewrite this key again from that
      turn's own fresh outcome, so a `COMPLETED` record can never survive
      more than one extra turn.
    """

    UNRESOLVED = "unresolved"
    COMPLETED = "completed"


_VALID_PENDING_GOVERNED_REQUEST_STATUSES = frozenset(
    {PendingGovernedRequestStatus.UNRESOLVED, PendingGovernedRequestStatus.COMPLETED}
)


class PendingGovernedRequest(BaseModel):
    """LIVE-CORR-11 -- the minimal state needed to represent "a governed
    request the runtime has not yet finished resolving, or has just
    finished resolving and remains correctable for one more turn."
    Deliberately scoped to `RequestClass.EXACT_COMMAND` only (this
    milestone's own proven defect shape; see `PENDING_GOVERNED_REQUEST_
    STATE_KEY`'s own docstring) -- a future milestone may widen this if a
    comparably real, proven defect is found for another class.

    Represents WHAT GOVERNED REQUEST IS STILL BEING COMPLETED (OR WAS
    JUST COMPLETED AND REMAINS CORRECTABLE), never WHAT ANSWER SHOULD BE
    REUSED: no command text, no grounding/applicability result, and no
    execution permission is ever stored here -- every field below is
    either a closed governance/output-shape/lifecycle label or a
    provenance-verified parameter, exactly the same trust class
    `RequestContract.provided_context` itself already carries (this is
    never a new provenance concept, only a narrower snapshot of the
    existing one). Re-grounding/re-applicability is ALWAYS required fresh
    before any command may be emitted for a resolved pending request --
    enforced by the EXISTING, completely unchanged `evidence.py`/
    `enforce_execution_decision_on_guidance` layers underneath, which this
    milestone does not touch.

    LIVE-CORR-11 CORRECTIVE PASS -- ISSUE D: `intent` (additive) is the
    ORIGINAL turn's own semantic `RequestIntent` label (e.g. `COMMAND`,
    but just as validly `PROCEDURE`/`TROUBLESHOOTING`/`INFORMATION` --
    see `derive_request_class`'s own docstring: `EXACT_COMMAND` is
    derived from `intent` OR `requested_output`, so the intent behind an
    `EXACT_COMMAND`-class request is not always literally `COMMAND`).
    Restoring THIS value (rather than unconditionally forcing
    `RequestIntent.COMMAND`, the ORIGINAL LIVE-CORR-11 pass's own
    shortcut) preserves the operation's true semantic identity across a
    clarification/correction turn -- `request_class` remains the sole
    governance/risk boundary; `intent` remains the semantic goal, exactly
    per this module's own "keep these concepts separate" architecture.
    """

    request_class: str
    requested_output: str
    intent: str
    status: str = PendingGovernedRequestStatus.UNRESOLVED
    subject: Optional[str] = None
    missing_context: list[str] = Field(default_factory=list)
    provided_context: list[RequestParameter] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        if value not in _VALID_PENDING_GOVERNED_REQUEST_STATUSES:
            raise ValueError(f"status must be one of: {sorted(_VALID_PENDING_GOVERNED_REQUEST_STATUSES)}")
        return value

    @field_validator("intent")
    @classmethod
    def _valid_intent(cls, value: str) -> str:
        if value not in _VALID_INTENTS:
            raise ValueError(f"intent must be one of: {sorted(_VALID_INTENTS)}")
        return value


def parse_pending_governed_request(raw: Any) -> Optional["PendingGovernedRequest"]:
    """Tolerant, fail-closed parse of the raw session-state value --
    mirrors `load_current_turn_contract` (request_execution_policy.py)
    exactly: malformed/absent/wrong-shaped data all resolve to `None`,
    never a raised exception. A `None`/absent pending record simply means
    "no unresolved governed request to preserve" -- the ordinary,
    overwhelmingly common case."""
    if not isinstance(raw, dict):
        return None
    try:
        return PendingGovernedRequest.model_validate(raw)
    except ValidationError:
        return None


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


# =============================================================================
# LIVE-CORR-12F -- Evidence-Bound Semantic Context Verification
# =============================================================================
#
# THE GAP THIS CLOSES: the LIVE-CORR-12A architectural audit proved
# `_verify_and_filter_provided_context`'s own three existing paths
# (literal substring, session-confirmed, identifier-class) can NEVER
# verify a qualitative state fact whose canonical VALUE is not itself a
# literal word the user said -- "the alarm is gone" never contains the
# literal string "cleared", so a model candidate `alarm_status=cleared`
# was unconditionally dropped, regardless of how unambiguously the user
# actually stated it. This is a real, live-reproduced architectural gap,
# not a defect in the existing three paths (which remain completely
# untouched and are still the ONLY verification for every other
# parameter name).
#
# WHAT THIS IS: a SMALL, CLOSED, deliberately narrow FOURTH verification
# path, scoped to EXACTLY ONE parameter name (`alarm_status`) and TWO
# canonical values (`active`/`cleared`) -- audited first, per instruction:
# `alarm_status`/`active_alarm_status`/`fault`/`alarm_text`/`alarm_
# severity` were all grepped across this codebase's own production code
# and found to have ZERO existing consumers; none is added here except
# `alarm_status`, deliberately, as this milestone's own explicit,
# narrowly-scoped decision -- never because a model happened to emit one
# of the others.
#
# HOW IT WORKS: `_ALARM_STATUS_EVIDENCE_PHRASES` is a small, closed,
# hardcoded ALLOWLIST mapping specific, deliberately unambiguous literal
# phrases to their canonical value -- never a general NLP classifier,
# never fuzzy/keyword/regex matching, never a synonym list driving
# routing (section 16's own explicit prohibition; this drives ONE
# parameter's VALUE, never `RequestClass`/intent/routing of any kind).
# A candidate `alarm_status` value is accepted ONLY when at least one
# phrase mapped to THAT SAME value is a literal, case-insensitive,
# CONTIGUOUS substring of the CURRENT TURN's own real user text --
# mirrors the existing three paths' own "compare only against real,
# already-trusted text" discipline exactly, applied to phrases instead
# of bare values.
#
# NEGATION SAFETY BY CONSTRUCTION, NEVER BY DETECTION (section 12's own
# explicit "if the design cannot safely handle polarity, fail closed"
# allowance): this design does not attempt to detect negation at all --
# it simply never recognizes a phrase shape it was not given. Each
# allowlisted phrase is long/specific enough that a natural negated
# variant ("the alarm is NOT gone", "the alarm has NOT cleared") fails
# to match STRUCTURALLY: the inserted negation word breaks the
# CONTIGUOUS substring the allowlist requires. This is a property of the
# deliberately-chosen phrase text, never an explicit "if 'not' appears,
# reject" rule -- there is no such rule anywhere in this code. A
# genuinely novel phrasing this table was never given (however
# semantically clear to a human) is NOT recognized and fails closed,
# exactly like any other unverifiable claim -- this design never claims
# to understand natural language; it only ever confirms a model's own
# candidate against a real, closed, pre-curated literal phrase.
#
# HONESTY (section 8's own explicit requirement): this is
# `MODEL_CANDIDATE_THEN_EVIDENCE_VALIDATED`, deliberately NEVER
# `AUTHORITATIVE_DETERMINISTIC` -- no code here independently derives
# meaning from arbitrary language; it only ever confirms the MODEL's own
# proposed value against a real, deterministic, closed phrase match.
#
# CANONICAL CONTEXT, NEVER AUTHORITATIVE REQUIRED CONTEXT (section 4/10's
# own explicit, critical requirement): `alarm_status` is deliberately
# NEVER added to `CANONICAL_PARAMETER_ALIASES` (LIVE-CORR-12B) -- that
# table feeds `authoritative_missing_context_names`/`required_target_
# parameter_gaps`, the SOLE authority for what can force `NEEDS_
# INFORMATION`/gate `may_emit_command`. A model that declares
# `missing_context=["alarm_status"]` is COMPLETELY UNAFFECTED by this
# module -- that name is not, and must never become, a recognized
# canonical alias, so `authoritative_missing_context_names` continues to
# silently exclude it exactly as it already excludes any other
# unrecognized name. `SEMANTIC_CONTEXT_PARAMETER_NAMES`, below, is a
# DELIBERATELY SEPARATE registry -- extending the canonical-CONTEXT set
# (what may be safely RETAINED once verified) never automatically
# extends the authoritative-REQUIRED set (what may be safely REQUIRED).
_ALARM_STATUS_PARAMETER_NAME = "alarm_status"

SEMANTIC_CONTEXT_PARAMETER_NAMES = frozenset({_ALARM_STATUS_PARAMETER_NAME})
"""LIVE-CORR-12F -- the closed set of parameter NAMES eligible for
evidence-bound semantic verification (`_verify_semantic_context_entry`).
Deliberately separate from `CANONICAL_PARAMETER_ALIASES` (LIVE-CORR-12B,
which governs NAME aliasing for the authoritative unit_id/unit_type
target parameters) -- see this section's own "CANONICAL CONTEXT, NEVER
AUTHORITATIVE REQUIRED CONTEXT" comment for why the two must never be
merged. Extending this set requires the SAME deliberate, audited,
documented code change `CANONICAL_PARAMETER_ALIASES`'s own discipline
already requires -- never inferred from an arbitrary model-invented
name."""

_ALARM_STATUS_VALUES = frozenset({"active", "cleared"})
"""Section 9's own explicit "closed value domain" requirement -- exactly
the two states this milestone's own live evidence justifies. No
`"unknown"`/other value: not yet proven genuinely useful, and adding one
speculatively would only widen the domain without a real justification."""

_ALARM_STATUS_EVIDENCE_PHRASES: dict[str, str] = {
    "the alarm is gone": "cleared",
    "alarm is gone": "cleared",
    "the alarm has cleared": "cleared",
    "alarm has cleared": "cleared",
    "the alarm cleared": "cleared",
    "alarm cleared": "cleared",
    "the alarm is clear": "cleared",
    "alarm is clear": "cleared",
    "the alarm is resolved": "cleared",
    "alarm is resolved": "cleared",
    "the alarm is still active": "active",
    "alarm is still active": "active",
    "the alarm is active": "active",
    "alarm is active": "active",
    "the alarm is still there": "active",
    "alarm is still there": "active",
    "the alarm is still present": "active",
    "alarm is still present": "active",
}
"""The COMPLETE closed allowlist -- see this section's own module-level
comment for the full "negation safety by construction" rationale. Every
key is lowercase (matched against lowercased current-turn text);
extending this set requires a deliberate code change here, verified
against the SAME "does a natural negated variant still fail to match"
property every existing entry was chosen to have -- never added merely
because a model happened to phrase something differently once."""


def _verify_semantic_context_entry(param: RequestParameter, current_turn_text: Optional[str]) -> Optional[RequestParameter]:
    """LIVE-CORR-12F -- the fourth, deliberately narrow verification path
    `_verify_and_filter_provided_context` consults ONLY for `param.name
    in SEMANTIC_CONTEXT_PARAMETER_NAMES`, and ONLY after that function's
    own existing three paths have already failed to verify the entry.
    Returns the verified param (value canonicalized to the closed
    lowercase form) on a genuine phrase match, else `None` (drop, fail
    closed) -- never raises, never fabricates a value, never trusts a
    value outside `_ALARM_STATUS_VALUES`. See this section's own module-
    level comment for the complete safety/honesty rationale.
    """
    if param.name != _ALARM_STATUS_PARAMETER_NAME:
        return None
    normalized_value = param.value.strip().lower()
    if normalized_value not in _ALARM_STATUS_VALUES:
        return None
    if not current_turn_text:
        return None
    current_lower = current_turn_text.lower()
    for phrase, canonical_value in _ALARM_STATUS_EVIDENCE_PHRASES.items():
        if canonical_value == normalized_value and phrase in current_lower:
            _logger.info(
                "request_contract: semantic_context_verification parameter=%s result=accepted "
                "verification_mode=evidence_bound",
                param.name,
            )
            return param.model_copy(update={"value": canonical_value})
    _logger.info(
        "request_contract: semantic_context_verification parameter=%s result=rejected "
        "verification_mode=evidence_bound",
        param.name,
    )
    return None


def _verify_and_filter_provided_context_list(
    provided_context: Sequence[RequestParameter],
    current_turn_text: Optional[str],
    session_confirmed: Mapping[str, str],
) -> list[RequestParameter]:
    """POST-6A REPAIR 1 -- the surviving-parameters-only projection of
    `_verify_and_filter_provided_context`, kept so every caller that only
    ever needed the list (and every test written against the original
    2-value semantics) keeps working unchanged after that function grew
    its second, `unresolved_target_parameter_names` result."""
    return _verify_and_filter_provided_context(provided_context, current_turn_text, session_confirmed).verified


def _with_correction_status(param: RequestParameter, confirmed_value: Optional[str]) -> RequestParameter:
    """POST-6A REPAIR 1/5 -- stamps `corrects_prior_value` when THIS
    turn's own freshly-verified value replaces a DIFFERENT value the same
    parameter name already carried in this same conversation. Purely a
    record of what happened (never itself a permission); `command_
    candidate_binding.py` is what turns it into invalidation of a command
    candidate or approval bound to the superseded target."""
    if confirmed_value is None or confirmed_value == param.value:
        return param
    return param.model_copy(update={"corrects_prior_value": confirmed_value})


class _VerificationOutcome(BaseModel):
    """POST-6A REPAIR 1 -- what `_verify_and_filter_provided_context`
    concluded, beyond the surviving parameter list alone.

    `unresolved_target_parameter_names` is the deterministic bridge to
    "require explicit confirmation when the intended target cannot be
    established safely": a parameter whose claimed identifier was
    POSITIVELY refuted by the current turn's own text (negated, excluded,
    quoted as an example, or contested by a competing identifier) is not
    merely dropped -- its canonical name is reported here so the caller
    forces it into `missing_context` regardless of what the model
    declared, and regardless of whether any other rule would have
    required it."""

    model_config = ConfigDict(frozen=True)

    verified: list[RequestParameter] = Field(default_factory=list)
    unresolved_target_parameter_names: list[str] = Field(default_factory=list)


def _verify_and_filter_provided_context(
    provided_context: Sequence[RequestParameter],
    current_turn_text: Optional[str],
    session_confirmed: Mapping[str, str],
) -> _VerificationOutcome:
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
    Anything no path can verify is silently DROPPED -- never trusted
    merely because the model's own JSON happened to parse.

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

    LIVE-CORR-12F -- a FOURTH path (`_verify_semantic_context_entry`) for
    the small, closed `SEMANTIC_CONTEXT_PARAMETER_NAMES` set (currently
    only `alarm_status`): evidence-bound qualitative-state verification
    (e.g. `alarm_status=cleared` from "the alarm is gone") -- see that
    function's own docstring for the full design, including its own
    negation-safety-by-construction argument.

    CRITICAL, LIVE-CORR-12F-CONFIRMED CAVEAT: for a `SEMANTIC_CONTEXT_
    PARAMETER_NAMES` entry, the FIRST path above (bare literal substring
    of `param.value` in current-turn text) is DELIBERATELY SKIPPED, never
    merely "tried first" -- a value like `"cleared"`/`"active"` is itself
    an ordinary English word that can trivially appear inside a NEGATED
    sentence ("the alarm is **not** cleared" literally contains
    "cleared"). Live-verified during this milestone's own test-writing:
    without this skip, path 1 alone would have verified `alarm_status=
    cleared` against "the alarm is not cleared" -- reproducing exactly
    the negation-blindness (DEF-0034) this milestone's own section 12
    explicitly warned against worsening. The THIRD (identifier-class)
    path is also irrelevant for this parameter family (its values are
    never RRU/AAS-shaped) and is likewise skipped for it. The SECOND
    (session-confirmed exact match) path is NOT skipped -- it compares
    against an ALREADY-VERIFIED prior value for the SAME name, never raw
    current-turn text, so it carries none of path 1's negation risk and
    remains the legitimate cross-turn carry-forward mechanism. Every
    OTHER (non-semantic-context) parameter name -- `unit_id`/`unit_type`
    included -- is completely UNAFFECTED: paths 1-3 run for them exactly
    as before this pass.
    """
    current_lower = current_turn_text.lower() if current_turn_text else ""
    verified: list[RequestParameter] = []
    unresolved_targets: set[str] = set()
    for param in provided_context:
        value_lower = param.value.lower()
        is_semantic_context_param = param.name in SEMANTIC_CONTEXT_PARAMETER_NAMES
        confirmed_value = session_confirmed.get(param.name)

        # POST-6A REPAIR 1 -- IDENTIFIER-SHAPED VALUES TAKE A COMPLETELY
        # SEPARATE, EXACT PATH. The substring test below is never reached
        # for them: `"RRU-9" in "restart rru-90"` is `True`, and a wrong
        # physical unit is the worst outcome this system can produce.
        if not is_semantic_context_param and is_identifier_shaped(param.value):
            canonical_value = canonical_identifier(param.value) or param.value
            verification = verify_identifier_against_text(param.value, current_turn_text)
            if verification.status == IdentifierVerificationStatus.VERIFIED:
                verified.append(
                    _with_correction_status(
                        param.model_copy(update={"value": canonical_value, "source_span": verification.span}),
                        confirmed_value,
                    )
                )
                continue
            if verification.requires_explicit_confirmation:
                # POSITIVELY refuted (negated/excluded/quoted example) or
                # genuinely contested. Never silently dropped: the caller
                # turns this into an explicit confirmation request.
                canonical_name = canonical_parameter_name(param.name) or param.name
                unresolved_targets.add(canonical_name)
                _logger.info(
                    "request_contract: refusing an identifier-shaped provided_context entry name=%r "
                    "status=%s -- explicit user confirmation required",
                    param.name,
                    verification.status.value,
                )
                continue
            # NOT_PRESENT in this turn's text: the unchanged same-subject
            # session-confirmed carry-forward remains the only other way
            # this value can be trusted -- compared on CANONICAL form, so
            # a differently-spelled but identical identifier still matches,
            # and a merely prefix-overlapping one still does not.
            if confirmed_value is not None and canonical_value == (
                canonical_identifier(confirmed_value) or confirmed_value
            ):
                verified.append(param.model_copy(update={"value": canonical_value}))
                continue
            _logger.info(
                "request_contract: dropping unverifiable identifier-shaped provided_context entry name=%r",
                param.name,
            )
            continue

        canonical_candidate = _canonicalize_if_identifier(param.value)

        if not is_semantic_context_param and value_lower and value_lower in current_lower:
            verified.append(_with_correction_status(param.model_copy(update={"value": canonical_candidate}), confirmed_value))
            continue

        if confirmed_value is not None and confirmed_value.lower() == value_lower:
            verified.append(param.model_copy(update={"value": canonical_candidate}))
            continue

        if param.name in SEMANTIC_CONTEXT_PARAMETER_NAMES:
            semantically_verified = _verify_semantic_context_entry(param, current_turn_text)
            if semantically_verified is not None:
                verified.append(_with_correction_status(semantically_verified, confirmed_value))
                continue

        _logger.info(
            "request_contract: dropping unverifiable provided_context entry name=%r "
            "(neither current-turn text, session-confirmed state, identifier-class "
            "normalization, nor evidence-bound semantic verification could establish it)",
            param.name,
        )
    return _VerificationOutcome(
        verified=verified, unresolved_target_parameter_names=sorted(unresolved_targets)
    )


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
        # LIVE-CORR-12B: the prior contract's own `provided_context` was
        # already name-canonicalized when IT was persisted (this same
        # code path), but is re-canonicalized here defensively too --
        # e.g. for a prior contract that predates this pass, or one built
        # directly by a test/caller -- so a carried-forward key can never
        # silently fail to line up with this turn's own canonical names.
        session_confirmed = {
            (canonical_parameter_name(param.name) or param.name): param.value
            for param in prior_contract.provided_context
        }

    # LIVE-CORR-12B -- Canonical Request-Context Parameter Authority:
    # collapses recognized-alias NAMES (e.g. `RRU_ID` -> `unit_id`) to one
    # canonical entry BEFORE value verification runs, so the SAME semantic
    # parameter is reconciled identically regardless of which spelling the
    # model happened to use this turn. See `canonicalize_provided_context`'s
    # own docstring for the fail-closed alias-conflict rule. Value
    # verification itself (below) is completely unchanged.
    canonical_provided_context = canonicalize_provided_context(contract.provided_context)

    current_turn_text = _extract_current_turn_user_text(tool_context)
    verification_outcome = _verify_and_filter_provided_context(
        canonical_provided_context, current_turn_text, session_confirmed
    )
    verified_this_turn = verification_outcome.verified

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
    #
    # LIVE-CORR-12B: the model's own declared `missing_context` NAMES are
    # canonicalized first (recognized aliases only, e.g. `RRU_ID` ->
    # `unit_id`; anything else passes through unchanged) so reconciliation
    # against `final_provided_context` (already canonical, above) removes
    # a satisfied key regardless of which spelling the model used THIS
    # turn versus a prior one. `contract.missing_context` -- the FULL,
    # still-unfiltered list, canonicalized names included -- remains the
    # persisted, observability-visible record; `authoritative_missing_
    # context_names` (request_execution_policy.py's own consumer) is the
    # SEPARATE, narrower question of which of these names may drive
    # policy -- never conflated here.
    reconciled_missing_context = reconcile_missing_context(
        contract.intent,
        contract.requested_output,
        final_provided_context,
        _canonicalize_missing_context_names(contract.missing_context),
    )

    # POST-6A REPAIR 1 -- EXPLICIT CONFIRMATION FOR A REFUTED TARGET.
    # `unresolved_target_parameter_names` names a parameter the CURRENT
    # turn's own text POSITIVELY refuted (negated, excluded, quoted as a
    # source example) or left genuinely contested between competing
    # units. That is categorically different from "not mentioned": a
    # merely-absent parameter may still be satisfied by the same-subject
    # carry-forward or by a target-independent operation, but a refuted
    # one must be confirmed by the user before anything may act on it.
    # Forced in here, AFTER reconciliation, so no other rule can remove
    # it -- and the corresponding carried-forward value is dropped too,
    # since a target the user just excluded must not survive from an
    # earlier turn either.
    if verification_outcome.unresolved_target_parameter_names:
        unresolved_names = set(verification_outcome.unresolved_target_parameter_names)
        final_provided_context = [param for param in final_provided_context if param.name not in unresolved_names]
        reconciled_missing_context = sorted(set(reconciled_missing_context) | unresolved_names)
        _logger.info(
            "request_contract: explicit target confirmation required for %s",
            sorted(unresolved_names),
        )

    # LIVE-CORR-8 -- Request Class Must Be the Authoritative Governance
    # Boundary: computed here, ALWAYS from `derive_request_class`'s own
    # pure, deterministic derivation over already-validated `intent`/
    # `requested_output`/`action_requested`/`subject` -- never from the
    # model (see `RequestContract.request_class`'s own docstring for why
    # there is nothing for the model to override in the first place).
    final_request_class = derive_request_class(
        contract.intent, contract.requested_output, contract.action_requested, contract.subject
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
            "request_class": final_request_class,
            "run_id": current_run_id(),
        }
    )

    tool_context.state[VALIDATED_REQUEST_CONTRACT_STATE_KEY] = final_contract.model_dump(mode="json")
    return None


def safe_request_contract_observability_fields(
    raw_contract: Any, current_run_id: Optional[str] = None
) -> Optional[dict[str, Any]]:
    """Section 16 (Observability) -- a SAFE projection of a validated
    contract for logging/diagnostics: intent/subject/requested_output/
    continuation/requires_governed_knowledge/requires_operational_context/
    ambiguity, plus only the KEY NAMES of `provided_context`/`missing_
    context` -- never a parameter VALUE (which may be real operational
    content). Returns `None` for anything that is not a valid, already-
    validated contract dict (never fabricates a log line from garbage).

    LIVE-CORR-12B -- Observability: the LIVE-CORR-12A audit proved this
    projection previously omitted `run_id` entirely, so a STALE contract
    (e.g. left behind in session state by a turn that ran through
    `presentation_team_manager`'s empty toolset, which cannot call
    `record_request_contract` at all) logged IDENTICALLY to a genuinely
    FRESH one -- the immediately-following `derive_execution_decision`
    rejection as `INVALID_CONTRACT` then looked, from the log alone, like
    an unexplained contradiction rather than the correct, deterministic
    freshness check it actually is. `contract_run_id` (always included --
    a correlation id, never sensitive, already logged bare elsewhere
    throughout this codebase) makes that visible on its own; passing the
    caller's own `current_run_id` (optional, backward-compatible default
    `None` -- every existing call site is unaffected) additionally
    computes `fresh` directly, so a log line alone answers "was this
    contract usable this turn" without needing to cross-reference a
    separate `run_id=...` log entry by hand.
    """
    if not isinstance(raw_contract, dict):
        return None
    try:
        contract = RequestContract.model_validate(raw_contract)
    except ValidationError:
        return None
    fields: dict[str, Any] = {
        "request_class": contract.request_class,
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
        "contract_run_id": contract.run_id,
    }
    if current_run_id is not None:
        fields["fresh"] = contract.run_id == current_run_id
    return fields
