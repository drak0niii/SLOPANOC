"""LIVE-CORR-11 -- Pending Governed Request Continuity and Final Command
Authority.

PROVEN DEFECT (see LIVE-CORR-10's own audit): an unresolved `EXACT_
COMMAND` request (`RequestExecutionStatus.NEEDS_INFORMATION`) has no
durable memory of that fact. A later turn that merely supplies the
missing target parameter (e.g. "the RRU is rru-3") can be freshly,
independently classified `OPERATIONAL_INFORMATION`/`FACT` by the model --
real, live-reproduced evidence -- which resolves to `ALLOW`/`may_emit_
command=False` with NO `TroubleshootingGuidance` to validate against,
routing Team Manager's own raw free text around BOTH existing command-
safety backstops.

LIVE-CORR-11 CORRECTIVE PASS (this file, before live validation): four
issues found auditing the ORIGINAL LIVE-CORR-11 pass against real code,
each with its own section/tests below:

  A. Pending state cleared on `ALLOW` alone -- insufficient for an
     IMMEDIATE correction after a successful resolution ("RRU-3" ->
     ALLOW -> "actually it is RRU-10"). Fixed by `PendingGovernedRequest
     Status.COMPLETED`, a deliberately narrow, one-more-turn-only
     lifecycle stage.
  B. Inheritance required `contract.continuation == True` as the SOLE
     gate -- live evidence shows the model can emit `continuation=False`
     for a genuine same-operation/new-target turn. `continuation` is no
     longer consulted; other, more specific structural signals now carry
     the safety weight.
  C. The predicate never checked that supplied context actually RELATES
     to the pending requirement -- "what is the status of RRU-9?"
     supplying `unit_id=RRU-9` could previously be misread as answering
     a pending "restart RRU" request. Fixed by `_turn_requires_no_
     independent_lookup` (reuses the EXISTING `requires_governed_
     knowledge`/`requires_operational_context` contract fields) plus
     `_relates_to_pending_vocabulary` (name-overlap against the pending
     record's own known parameter vocabulary).
  D. `resolve_effective_governed_contract` unconditionally forced
     `intent=COMMAND`, discarding the ORIGINAL semantic operation label
     (which need not literally be `COMMAND` -- `EXACT_COMMAND` is
     derived from `intent` OR `requested_output`). Fixed by `Pending
     GovernedRequest.intent`, restored verbatim instead of hardcoded.

LIVE-CORR-11 FINAL SAFETY CLOSURE PASS (this file's own final section --
sections "Concern A", "Concern B", "Concern C" below): three remaining
concerns audited before live validation.

  CONCERN A -- the corrective-pass predicate can still be defeated by a
  maximally adversarial model self-declaration (`requires_governed_
  knowledge=False` AND `requires_operational_context=False` for a
  request that is, in truth, independent of the pending operation, e.g.
  "what software version is RRU-9 running?"). AUDITED AND CONFIRMED: no
  additional, already-existing, deterministic, non-text signal closes
  this precisely without either (a) inspecting raw user text, (b)
  reintroducing `contract.subject`-equality (which would also reject the
  TRUE positive -- its own subject, e.g. "RRU ID", never matches the
  pending operation's own subject either), or (c) reintroducing
  `continuation` as a gate (explicitly preserved as removed, section 2).
  NOT surgically closable at the ADMISSION layer within this milestone
  -- see `test_concern_a_...` below for the honest, reproduced boundary.
  HOWEVER: proven bounded and SAFE -- see the Concern A/C interaction
  test -- because command CONTENT provenance is an entirely separate
  trust boundary (specialist delegation + `evidence.py` grounding, both
  untouched by this milestone); a wrongly-inherited `may_emit_command=
  True` can never smuggle in a REAL command for an operation the model
  was never asked to perform, and the wrong inheritance itself makes
  `is_operationally_shaped_request` `True`, which makes the EXISTING
  `requires_unstructured_response_backstop` fire, replacing whatever
  free text existed with the SAFE deterministic fallback -- the user
  gets a wrong/confusing answer, never a leaked command.

  CONCERN B -- CLOSED. `COMPLETED` could survive an intervening
  `INVALID_CONTRACT` turn untouched (the builder is never called for
  that turn), letting a LATER, non-immediate turn still treat itself as
  eligible for "immediate correction." Fixed by `expire_completed_
  pending_on_invalid_contract` (request_execution_policy.py), called
  from `chat_service.py`'s own existing `else` branch -- narrowly
  expires ONLY a `COMPLETED` record; `UNRESOLVED` (or no pending state)
  is left completely untouched, preserving genuine clarification
  recovery across a transient glitch.

  CONCERN C -- the GLOBAL "`may_emit_command=False` implies no command
  in ANY final output, for ANY reason" invariant is NOT fully provable
  within this milestone for a turn that is genuinely, correctly
  classified non-operationally-shaped (e.g. real `OPERATIONAL_
  INFORMATION`, no pending request involved at all) with no structured
  `TroubleshootingGuidance` -- `requires_unstructured_response_backstop`
  deliberately does not examine that shape's own free text (by design,
  to avoid suppressing every ordinary informational reply -- see test
  C2/C4). This is a PRE-EXISTING gap (predates LIVE-CORR-11, already
  honestly documented in that function's own docstring since LIVE-
  CORR-3B) -- unrelated to, and not introduced or worsened by, this
  milestone's own pending-continuity mechanism. What IS proven: for
  EVERY turn this milestone's own mechanism actually governs (a pending
  `EXACT_COMMAND` request, inherited or not), the invariant holds -- see
  test C1 (may_emit_command=False -> command-bearing free text is
  replaced) and the module-level report for the full scoped/unscoped
  distinction.

No real Gemini call, no external network, no Cloud SQL requirement.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.agents.team_manager.request_contract import (
    PENDING_GOVERNED_REQUEST_STATE_KEY,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    PendingGovernedRequest,
    PendingGovernedRequestStatus,
    RequestClass,
    RequestContract,
    RequestedOutput,
    RequestIntent,
    RequestParameter,
    parse_pending_governed_request,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionStatus,
    build_pending_governed_request_state_update,
    derive_execution_decision,
    expire_completed_pending_on_invalid_contract,
    requires_unstructured_response_backstop,
    resolve_effective_governed_contract,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RUN_ID = "run-livecorr11"
_RESTART_COMMAND_TEXT = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"


def _param(name: str, value: str, provenance: str = ParameterProvenance.USER) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance=provenance)


def _pending_restart_rru(
    missing: list[str],
    provided: list[RequestParameter],
    status: str = PendingGovernedRequestStatus.UNRESOLVED,
    intent: str = RequestIntent.COMMAND,
) -> PendingGovernedRequest:
    return PendingGovernedRequest(
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        intent=intent,
        status=status,
        subject="restart RRU",
        missing_context=missing,
        provided_context=provided,
    )


def _clarification_answer_contract(
    provided: list[RequestParameter],
    continuation: bool = True,
    subject: str = "RRU ID",
    requires_governed_knowledge: bool = False,
    requires_operational_context: bool = False,
) -> RequestContract:
    """Shapes a real, live-reproduced clarification-answer turn: the model
    classifies it `intent=information, requested_output=fact` -- the exact
    proven defect shape. `requires_governed_knowledge`/`requires_
    operational_context` default to `False`, matching the REAL live shape
    of a bare value-answer turn (LIVE-CORR-10's own audit)."""
    return RequestContract(
        intent=RequestIntent.INFORMATION,
        subject=subject,
        requested_output=RequestedOutput.FACT,
        continuation=continuation,
        requires_governed_knowledge=requires_governed_knowledge,
        requires_operational_context=requires_operational_context,
        provided_context=provided,
        missing_context=[],
        request_class=RequestClass.OPERATIONAL_INFORMATION,
        run_id=_RUN_ID,
    )


# ---------------------------------------------------------------------------
# A. Pending EXACT_COMMAND survives clarification answer
# ---------------------------------------------------------------------------


def test_pending_exact_command_survives_operational_information_clarification_turn():
    # Realistic progression: unit_type was already supplied on a prior
    # turn (real live shape); only unit_id is still missing.
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = _clarification_answer_contract(provided=[_param("unit_type", "RRU")])

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    # Effective governance is restored to EXACT_COMMAND -- the turn never
    # resolves as an ordinary ALLOW/operational-information answer.
    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.requested_output == RequestedOutput.EXACT_COMMAND
    assert decision.subject == "restart RRU"
    assert decision.may_emit_command is False
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION


def test_pending_exact_command_resolves_to_allow_once_fully_parameterized():
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    # Unlike the real (imperfect) live model tagging, this turn correctly
    # supplies BOTH required parameters -- demonstrates the clean,
    # fully-resolved path once the model attaches the right name.
    contract = _clarification_answer_contract(
        provided=[_param("unit_type", "RRU"), _param("unit_id", "RRU-3")]
    )

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.may_emit_command is True


def test_without_pending_state_the_same_clarification_turn_stays_unvalidated_allow():
    """Backward-compatibility/regression proof: omitting `pending_governed_
    request` (every pre-LIVE-CORR-11 caller) reproduces the ORIGINAL,
    proven-defective behavior byte-for-byte -- confirms the fix is
    additive, not a change to default behavior."""
    contract = _clarification_answer_contract(provided=[_param("unit_type", "RRU")])

    decision = derive_execution_decision(contract, _RUN_ID)

    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.request_class == RequestClass.OPERATIONAL_INFORMATION
    assert decision.may_emit_command is False


# ---------------------------------------------------------------------------
# B. No command when may_emit_command=False
# ---------------------------------------------------------------------------


def test_may_emit_command_false_is_the_sole_authority_signal_needs_information():
    """Whatever Team Manager's own free text says is irrelevant to this
    module's own output -- `RequestExecutionDecision` never carries or
    inspects response text at all. Proves the DECISION itself -- the one
    signal `requires_unstructured_response_backstop`/`enforce_execution_
    decision_on_guidance` (chat_service.py) key off of -- is NEEDS_
    INFORMATION, not ALLOW, for the dangerous case this milestone closes."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = _clarification_answer_contract(provided=[_param("unit_type", "RRU")])

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.may_emit_command is False
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    # NEEDS_INFORMATION is unconditionally in `_UNSTRUCTURED_RESPONSE_
    # BLOCKING_STATUSES` (request_execution_policy.py) -- chat_service.py's
    # own `requires_unstructured_response_backstop` therefore now fires
    # unconditionally for this turn, replacing any raw free text with the
    # deterministic fallback. That downstream wiring is exercised by the
    # existing LIVE-CORR-6 suite; this test proves the INPUT it depends on
    # is now correct.


def test_may_emit_command_false_is_the_sole_authority_signal_ambiguous():
    """A pending EXACT_COMMAND request combined with a current turn the
    model itself flagged ambiguous must still deny command authority --
    pending-request inheritance is never itself a source of permission."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[])
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        subject=None,
        requested_output=RequestedOutput.EXACT_COMMAND,
        continuation=False,
        provided_context=[],
        missing_context=[],
        ambiguity=True,
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.may_emit_command is False
    assert decision.status == RequestExecutionStatus.AMBIGUOUS


# ---------------------------------------------------------------------------
# C. Command allowed only after fresh grounding
# ---------------------------------------------------------------------------


def test_fully_resolved_pending_request_without_grounded_candidate_still_requires_target():
    """No `grounded_command_candidate` this turn (grounding never ran, or
    produced nothing) -- `required_target_parameter_gaps` fails CLOSED to
    the original blanket rule, exactly as it already does for an ordinary,
    non-pending EXACT_COMMAND contract. Pending-request continuity never
    weakens this."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[])
    contract = _clarification_answer_contract(provided=[])  # supplies nothing this turn

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    # No verified provided_context this turn -> does not even qualify as
    # "supplying context for the pending request" (section 8) -- treated
    # as an independent turn, its OWN classification (OPERATIONAL_
    # INFORMATION/ALLOW) stands. This documents the boundary: an empty
    # answer is not silently upgraded into a governed request either.
    assert decision.request_class == RequestClass.OPERATIONAL_INFORMATION


def test_inherited_pending_request_resolves_allow_once_identifier_supplied_with_grounding_present():
    """An inheriting turn that directly supplies the still-missing
    `unit_id` resolves ALLOW regardless of a `grounded_command_candidate`
    also being present this turn -- proves a candidate does not somehow
    interfere with or get required by the ordinary identifier-satisfied
    path. (The SEPARATE "candidate structurally proves the operation is
    NOT per-unit, narrowing `unit_type`/`unit_id` away entirely" path --
    LIVE-CORR-7/9's own mechanism -- only ever applies when `unit_type`
    was NEVER supplied at all; ISSUE C's own relation requirement means an
    INHERITING turn must supply a NAME the pending record already cares
    about, i.e. `unit_id` or `unit_type` -- so that specific narrowing
    path and pending-inheritance are mutually exclusive by construction.
    That narrowing path itself remains fully exercised, unaffected by this
    milestone, by test H's own non-inheriting LIVE-CORR-9 regression.)"""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[])
    contract = _clarification_answer_contract(provided=[_param("unit_id", "RRU-9")])

    decision = derive_execution_decision(
        contract,
        _RUN_ID,
        grounded_command_candidate="accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1",
        pending_governed_request=pending,
    )

    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is True


def test_policy_layer_parameter_resolution_is_independent_of_verbatim_grounding_verdict():
    """`derive_execution_decision` (this module) answers ONLY "is the
    runtime allowed to show a command for this request AT ALL" (target/
    parameter resolution) -- it never itself judges whether a specific
    command STRING is a verbatim match for governed content; that is
    `evidence.py`'s own, separate, unchanged job (`enforce_execution_
    decision_on_guidance`/`command_suppression_fallback_text`, chat_
    service.py, apply that verdict downstream). A fully-parameterized
    pending request resolves ALLOW here regardless of what a `grounded_
    command_candidate` string would independently be graded as -- "both
    layers must agree before a command reaches the user" (module
    docstring); this test exercises only this layer."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = _clarification_answer_contract(
        provided=[_param("unit_type", "RRU"), _param("unit_id", "RRU-3")]
    )

    decision = derive_execution_decision(
        contract,
        _RUN_ID,
        grounded_command_candidate="accn FieldReplaceableUnit=RRU-3 restartunit 1 1 1",
        pending_governed_request=pending,
    )

    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is True
    # Grounding verdict (verbatim/grounding_rejected) remains evidence.py's
    # own, separate, unchanged job -- not exercised by this pure-policy
    # test; see LIVE-CORR-6's existing suite for that layer.


# ---------------------------------------------------------------------------
# D. Topic switch breaks pending request
# ---------------------------------------------------------------------------


def test_topic_switch_to_general_conversation_does_not_inherit_pending_command_governance():
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        subject=None,
        requested_output=RequestedOutput.FACT,
        continuation=False,
        provided_context=[],
        missing_context=[],
        request_class=RequestClass.GENERAL_CONVERSATION,
        run_id=_RUN_ID,
    )

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.request_class == RequestClass.GENERAL_CONVERSATION
    assert decision.may_emit_command is False


def test_topic_switch_to_new_operational_information_question_does_not_inherit():
    """A genuinely new question ("what does a VSWR alarm mean?") is
    structurally IDENTICAL in `intent`/`requested_output`/`request_class`
    to the true clarification-answer case -- distinguished here by
    supplying no verified provided_context this turn AT ALL (the simplest
    exclusion reason); see test K, below, for the harder case where a
    genuinely new question DOES supply a same-named parameter."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = _clarification_answer_contract(provided=[], continuation=False, subject="VSWR alarm")

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.request_class == RequestClass.OPERATIONAL_INFORMATION
    assert decision.may_emit_command is False


def test_pending_state_update_clears_on_topic_switch_allow():
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        subject="VSWR alarm",
        requested_output=RequestedOutput.FACT,
        continuation=False,
        provided_context=[],
        missing_context=[],
        request_class=RequestClass.OPERATIONAL_INFORMATION,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)
    effective_contract = resolve_effective_governed_contract(contract, pending)

    update = build_pending_governed_request_state_update(decision, effective_contract)

    assert update == {PENDING_GOVERNED_REQUEST_STATE_KEY: None}
    assert parse_pending_governed_request(update[PENDING_GOVERNED_REQUEST_STATE_KEY]) is None


# ---------------------------------------------------------------------------
# E. Target correction (RRU-3 -> RRU-10) -- via the COMPLETED lifecycle
# ---------------------------------------------------------------------------


def test_target_correction_replaces_stale_identifier_never_accumulates():
    # ISSUE A: the operation already reached ALLOW for RRU-3 -- the
    # pending record is COMPLETED, not UNRESOLVED.
    pending = _pending_restart_rru(
        missing=[],
        provided=[_param("unit_type", "RRU"), _param("unit_id", "RRU-3")],
        status=PendingGovernedRequestStatus.COMPLETED,
    )
    # "actually it is RRU-10" -- same operation, corrected identifier.
    contract = _clarification_answer_contract(provided=[_param("unit_id", "RRU-10")])

    effective = resolve_effective_governed_contract(contract, pending)

    values_by_name = {p.name: p.value for p in effective.provided_context}
    assert values_by_name["unit_id"] == "RRU-10"
    assert values_by_name["unit_type"] == "RRU"
    assert effective.subject == "restart RRU"
    # Never both values present -- the stale RRU-3 is fully replaced, not
    # merely appended alongside the corrected value.
    assert len([p for p in effective.provided_context if p.name == "unit_id"]) == 1


def test_target_correction_resolves_using_the_corrected_identifier_only():
    pending = _pending_restart_rru(
        missing=[],
        provided=[_param("unit_type", "RRU"), _param("unit_id", "RRU-3")],
        status=PendingGovernedRequestStatus.COMPLETED,
    )
    contract = _clarification_answer_contract(provided=[_param("unit_id", "RRU-10")])

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    # Fully parameterized (unit_type carried forward + corrected unit_id)
    # -> resolves to ALLOW, command permission granted at THIS policy
    # layer. evidence.py's own, separate, unchanged verbatim-grounding
    # check (section 11 -- "grounding must stay fresh") still applies
    # underneath in the real pipeline for the CORRECTED target, RRU-10 --
    # never re-using any grounding result computed for RRU-3; that layer
    # is unexercised by this pure-policy test (see LIVE-CORR-6's suite).
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is True


# ---------------------------------------------------------------------------
# F. Same operation, new target
# ---------------------------------------------------------------------------


def test_new_target_same_operation_does_not_reuse_prior_authorization():
    """After RRU-3/RRU-10 fully resolved (ALLOW -> pending COMPLETED, then
    superseded), a fresh "restart RRU-4" turn the model classifies as its
    own, independent EXACT_COMMAND request must resolve purely from ITS
    OWN missing_context -- never inherit the previous target's resolved
    state."""
    pending = None
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        subject="restart RRU",
        requested_output=RequestedOutput.EXACT_COMMAND,
        continuation=False,
        provided_context=[_param("unit_type", "RRU")],
        missing_context=["unit_id"],
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.missing_context == ["unit_id"]
    assert decision.may_emit_command is False


def test_new_target_needs_information_writes_a_fresh_pending_record_not_stale_one():
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        subject="restart RRU",
        requested_output=RequestedOutput.EXACT_COMMAND,
        continuation=False,
        provided_context=[_param("unit_type", "RRU")],
        missing_context=["unit_id"],
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=None)

    update = build_pending_governed_request_state_update(decision, contract)

    fresh_pending = parse_pending_governed_request(update[PENDING_GOVERNED_REQUEST_STATE_KEY])
    assert fresh_pending is not None
    assert fresh_pending.status == PendingGovernedRequestStatus.UNRESOLVED
    assert fresh_pending.missing_context == ["unit_id"]
    # No RRU-3/RRU-10 value anywhere -- a brand-new record, not a mutation
    # of any prior one.
    assert [p.value for p in fresh_pending.provided_context] == ["RRU"]


# ---------------------------------------------------------------------------
# G. General conversation regression
# ---------------------------------------------------------------------------


def test_general_conversation_after_operational_flow_has_no_pending_leakage():
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        subject=None,
        requested_output=RequestedOutput.FACT,
        continuation=False,
        provided_context=[],
        missing_context=[],
        request_class=RequestClass.GENERAL_CONVERSATION,
        run_id=_RUN_ID,
    )

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.request_class == RequestClass.GENERAL_CONVERSATION
    assert decision.may_emit_command is False
    assert decision.subject is None


# ---------------------------------------------------------------------------
# H. Existing alarm-list continuation (LIVE-CORR-9) -- unaffected
# ---------------------------------------------------------------------------


def _alarm_listing_contract() -> RequestContract:
    """LIVE-CORR-9's own real, live-reproduced scenario ("no, i just need
    a cmd to run a check on alarms for ericsson", provided_context=
    [vendor], model-declared missing_context=[unit_id, unit_type])."""
    return RequestContract(
        intent=RequestIntent.COMMAND,
        subject="check alarms",
        requested_output=RequestedOutput.EXACT_COMMAND,
        continuation=False,
        provided_context=[_param("vendor", "ericsson")],
        missing_context=["unit_id", "unit_type"],
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )


def test_livecorr9_alarm_listing_no_longer_inferred_target_independent_from_token_absence():
    """POST-6A REPAIR 2 -- DELIBERATE BEHAVIOR CHANGE, recorded here
    rather than deleted.

    LIVE-CORR-9 granted `may_emit_command=True` for this scenario because
    the grounded candidate (`"alt"`) contained no recognized RRU/AAS
    token, which it read as proof the operation was target-independent.
    That inference is invalid in the direction that matters: it cannot
    distinguish "genuinely system-wide" from "this target syntax is not
    one our closed two-prefix recognizer was ever given", so absence of
    evidence became evidence of safety.

    With the inference removed and no governed metadata supplied, the
    turn correctly resolves UNRESOLVED -- the conservative blanket rule,
    unchanged, still requires the target parameters. The legitimate
    alarm-listing case is now served by positive metadata instead; see
    the next test.
    """
    decision = derive_execution_decision(_alarm_listing_contract(), _RUN_ID, grounded_command_candidate="alt")

    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False
    assert set(decision.missing_context) == {"unit_id", "unit_type"}


def test_livecorr9_alarm_listing_is_permitted_by_positive_governed_metadata():
    """The same scenario, with an APPROVED, positively TARGET_INDEPENDENT
    governed operation descriptor for the section actually selected --
    which is what a genuinely system-wide listing now needs in order to
    be permitted. Proves the repair replaced the inference rather than
    simply removing the capability."""
    from backend.knowledge.domain.operation_descriptor import (
        GovernedOperationDescriptor,
        OperationEffect,
        OperationTargetScope,
    )
    from backend.tests._governed_operation_fixtures import approved_descriptor

    descriptor = approved_descriptor(
        GovernedOperationDescriptor(
            operation_id="list-alarms",
            target_scope=OperationTargetScope.TARGET_INDEPENDENT,
            effect=OperationEffect.READ_ONLY,
        )
    )

    decision = derive_execution_decision(
        _alarm_listing_contract(),
        _RUN_ID,
        grounded_command_candidate="alt",
        operation_descriptor=descriptor,
    )

    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.may_emit_command is True


def test_candidate_descriptor_never_grants_target_independence():
    """A descriptor that has not been through the human-gated governance
    transition is CANDIDATE, and CANDIDATE authority establishes
    nothing -- the conservative rule still applies."""
    from backend.knowledge.domain.operation_descriptor import (
        GovernedOperationDescriptor,
        OperationTargetScope,
        from_model_extraction,
    )

    candidate = from_model_extraction(
        GovernedOperationDescriptor(
            operation_id="list-alarms", target_scope=OperationTargetScope.TARGET_INDEPENDENT
        )
    )

    decision = derive_execution_decision(
        _alarm_listing_contract(), _RUN_ID, operation_descriptor=candidate
    )

    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


# ---------------------------------------------------------------------------
# I. Correction after successful ALLOW (ISSUE A)
# ---------------------------------------------------------------------------


def test_correction_after_allow_is_not_lost_to_the_information_fact_gap():
    """"restart RRU" -> "RRU-3" -> ALLOW -> "actually RRU-10", the latter
    deliberately modeled as `OPERATIONAL_INFORMATION`/`FACT` (the exact
    live reclassification pattern). End-to-end: the ALLOW turn's own
    `build_pending_governed_request_state_update` output feeds directly
    into the correction turn's `derive_execution_decision`."""
    allow_contract = _clarification_answer_contract(
        provided=[_param("unit_type", "RRU"), _param("unit_id", "RRU-3")]
    )
    allow_pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    allow_decision = derive_execution_decision(allow_contract, _RUN_ID, pending_governed_request=allow_pending)
    assert allow_decision.status == RequestExecutionStatus.ALLOW  # sanity check on the setup

    effective_allow_contract = resolve_effective_governed_contract(allow_contract, allow_pending)
    completed_update = build_pending_governed_request_state_update(allow_decision, effective_allow_contract)
    completed_pending = parse_pending_governed_request(completed_update[PENDING_GOVERNED_REQUEST_STATE_KEY])
    assert completed_pending is not None
    assert completed_pending.status == PendingGovernedRequestStatus.COMPLETED

    # "actually it is RRU-10" -- correction turn.
    correction_contract = _clarification_answer_contract(provided=[_param("unit_id", "RRU-10")])
    correction_decision = derive_execution_decision(
        correction_contract, _RUN_ID, pending_governed_request=completed_pending
    )

    # No authority bypass: still governed as EXACT_COMMAND, never a raw
    # ALLOW/OPERATIONAL_INFORMATION answer for the correction itself.
    assert correction_decision.request_class == RequestClass.EXACT_COMMAND
    assert correction_decision.subject == "restart RRU"
    # Fully re-parameterized (unit_type carried + corrected unit_id) with
    # NO grounded_command_candidate supplied this turn -> requires fresh
    # grounding exactly like any ordinary resolved EXACT_COMMAND turn;
    # this test does not itself grant a command, only proves the CORRECT
    # governance context and parameters are what get re-evaluated.
    assert correction_decision.status == RequestExecutionStatus.ALLOW
    assert correction_decision.may_emit_command is True


# ---------------------------------------------------------------------------
# J. Same-operation/new-target with continuation=False (ISSUE B)
# ---------------------------------------------------------------------------


def test_continuation_false_does_not_by_itself_break_pending_inheritance():
    """Model variability in `continuation` must not, by itself, destroy
    the safe governed lifecycle -- the ORIGINAL LIVE-CORR-11 pass required
    `continuation=True` as the sole gate; this turn is otherwise identical
    to test A's true-positive shape but declares `continuation=False`."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = _clarification_answer_contract(provided=[_param("unit_type", "RRU")], continuation=False)

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


# ---------------------------------------------------------------------------
# K. New informational request containing pending parameter (ISSUE C)
# ---------------------------------------------------------------------------


def test_new_informational_request_with_pending_parameter_name_is_not_inherited():
    """Pending: restart RRU, missing unit_id. Current: "what is the status
    of RRU-9?" with verified `unit_id=RRU-9`. Must resolve OPERATIONAL_
    INFORMATION, NOT an inherited restart command -- the exact false-
    inheritance case ISSUE C closes. Modeled with `requires_operational_
    context=True` -- a live status check plausibly needs live operational
    data of its own, unlike a bare value-answer turn (both `False` in the
    real, live-reproduced defect shape -- see `_clarification_answer_
    contract`'s own docstring)."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[])
    contract = _clarification_answer_contract(
        provided=[_param("unit_id", "RRU-9")],
        subject="RRU status",
        requires_operational_context=True,
    )

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.request_class == RequestClass.OPERATIONAL_INFORMATION
    assert decision.may_emit_command is False
    assert decision.subject == "RRU status"


def test_new_informational_request_unrelated_parameter_is_not_inherited():
    """Second, independent protection (ISSUE C): even with NEITHER lookup
    flag set, a supplied parameter whose NAME does not relate to the
    pending request's own vocabulary at all (`vendor`, while `restart RRU`
    only ever concerns `unit_id`/`unit_type`) is not inherited."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = _clarification_answer_contract(provided=[_param("vendor", "ericsson")])

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.request_class == RequestClass.OPERATIONAL_INFORMATION
    assert decision.may_emit_command is False


# ---------------------------------------------------------------------------
# L. Ambiguous exact-command clarification (section 6)
# ---------------------------------------------------------------------------


def test_ambiguous_exact_command_with_resolved_subject_preserves_pending_lifecycle():
    """An EXACT_COMMAND request that resolves AMBIGUOUS (the model's own
    `ambiguity=True`) while STILL resolving a concrete subject/operation
    is unresolved, not terminal -- the subsequent clarification answer
    must still retain governed EXACT_COMMAND authority."""
    ambiguous_contract = RequestContract(
        intent=RequestIntent.COMMAND,
        subject="restart RRU",
        requested_output=RequestedOutput.EXACT_COMMAND,
        continuation=False,
        provided_context=[],
        missing_context=["unit_id", "unit_type"],
        ambiguity=True,
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )
    ambiguous_decision = derive_execution_decision(ambiguous_contract, _RUN_ID)
    assert ambiguous_decision.status == RequestExecutionStatus.AMBIGUOUS  # sanity check

    update = build_pending_governed_request_state_update(ambiguous_decision, ambiguous_contract)
    pending = parse_pending_governed_request(update[PENDING_GOVERNED_REQUEST_STATE_KEY])
    assert pending is not None
    assert pending.status == PendingGovernedRequestStatus.UNRESOLVED

    # "the RRU is rru-3" -- clarification answer.
    contract = _clarification_answer_contract(provided=[_param("unit_type", "RRU")])
    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.subject == "restart RRU"
    assert decision.may_emit_command is False  # unit_id still missing


def test_ambiguous_exact_command_without_resolved_subject_clears_pending():
    """The OTHER AMBIGUOUS shape -- no subject/procedure resolved at all
    -- has no concrete operation identity worth preserving; clears,
    exactly as before this corrective pass."""
    ambiguous_contract = RequestContract(
        intent=RequestIntent.COMMAND,
        subject=None,
        requested_output=RequestedOutput.EXACT_COMMAND,
        continuation=False,
        provided_context=[],
        missing_context=[],
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )
    ambiguous_decision = derive_execution_decision(ambiguous_contract, _RUN_ID)
    assert ambiguous_decision.status == RequestExecutionStatus.AMBIGUOUS  # sanity check

    update = build_pending_governed_request_state_update(ambiguous_decision, ambiguous_contract)

    assert update == {PENDING_GOVERNED_REQUEST_STATE_KEY: None}


# ---------------------------------------------------------------------------
# M. Semantic operation preservation (ISSUE D)
# ---------------------------------------------------------------------------


def test_semantic_intent_survives_inheritance_instead_of_generic_command():
    """A pending request whose ORIGINAL turn was classified `intent=
    PROCEDURE` (a real, live-reproduced shape -- LIVE-CORR-8's own "give
    me the exact command to list current alarms" defect) must restore
    THAT intent on an inheriting turn, never a generically-forced
    `RequestIntent.COMMAND`."""
    pending = _pending_restart_rru(
        missing=["unit_id"], provided=[_param("unit_type", "RRU")], intent=RequestIntent.PROCEDURE
    )
    contract = _clarification_answer_contract(provided=[_param("unit_type", "RRU")])

    effective = resolve_effective_governed_contract(contract, pending)

    assert effective.intent == RequestIntent.PROCEDURE
    assert effective.request_class == RequestClass.EXACT_COMMAND
    assert effective.requested_output == RequestedOutput.EXACT_COMMAND

    # And the resulting decision is unaffected by which intent survived --
    # requested_output=EXACT_COMMAND alone already carries every
    # downstream gate (module docstring's own "OR over intent/requested_
    # output" argument).
    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)
    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION


def test_pending_record_carries_original_intent_across_multiple_unresolved_turns():
    """The intent restored on an inheriting-but-still-unresolved turn is
    itself carried into the FRESH pending record written at the end of
    THAT turn -- never reset back to a generic label."""
    pending = _pending_restart_rru(
        missing=["unit_id"], provided=[_param("unit_type", "RRU")], intent=RequestIntent.PROCEDURE
    )
    contract = _clarification_answer_contract(provided=[_param("unit_type", "RRU")])
    effective_contract = resolve_effective_governed_contract(contract, pending)
    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION  # still unresolved

    update = build_pending_governed_request_state_update(decision, effective_contract)
    fresh_pending = parse_pending_governed_request(update[PENDING_GOVERNED_REQUEST_STATE_KEY])

    assert fresh_pending is not None
    assert fresh_pending.intent == RequestIntent.PROCEDURE


# ---------------------------------------------------------------------------
# Concern A -- Clarification vs Independent Request
# ---------------------------------------------------------------------------


def test_concern_a1_bare_parameter_answer_inherits():
    """"RRU-9" as a bare, structured value supply -- no independent
    semantic operation of its own -- inherits, exactly like the real
    "the RRU is rru-3" defect shape."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[])
    contract = _clarification_answer_contract(provided=[_param("unit_id", "RRU-9")])

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.subject == "restart RRU"


def test_concern_a2_independent_informational_request_same_parameter_boundary():
    """Documents a genuine, acknowledged limitation -- not an approval.

    Pending: restart RRU, missing unit_id. Current: "what software
    version is RRU-9 running?", modeled with the maximally adversarial
    self-declaration this concern describes verbatim -- `requires_
    governed_knowledge=False` AND `requires_operational_context=False`
    for a request that is, in truth, independent.

    Audited (see this module's own docstring, "CONCERN A"): no additional
    already-existing, deterministic, non-text signal closes this
    precisely without reintroducing something this milestone explicitly
    preserves as removed (continuation as a gate, or subject equality,
    which would also reject the TRUE positive). This test asserts the
    CURRENT, real, documented behavior at the admission layer --
    inheritance still occurs here -- specifically so the gap is visible
    and tracked, not silently hidden. The very next test proves this
    specific shape's own actual safety consequence is bounded.
    """
    pending = _pending_restart_rru(missing=["unit_id"], provided=[])
    contract = _clarification_answer_contract(
        provided=[_param("unit_id", "RRU-9")],
        subject="RRU-9 software version",
        requires_governed_knowledge=False,
        requires_operational_context=False,
    )

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    # Documents the boundary precisely -- see the module docstring's own
    # "CONCERN A" section for why this is not closed here, and the
    # companion test below for why it is not a safety violation.
    assert decision.request_class == RequestClass.EXACT_COMMAND


def test_concern_a2_boundary_case_never_leaks_an_unauthorized_command():
    """The bounding proof for the above: even in the documented worst
    case, no command reaches the user. Wrong inheritance forces
    `requested_output=EXACT_COMMAND`, which makes `is_operationally_
    shaped_request` True, which makes the EXISTING, unmodified
    `requires_unstructured_response_backstop` fire for this turn's own
    outcome -- replacing whatever free text existed with the safe,
    command-free deterministic fallback."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])
    contract = _clarification_answer_contract(
        provided=[_param("unit_id", "RRU-9")],
        subject="RRU-9 software version",
        requires_governed_knowledge=False,
        requires_operational_context=False,
    )

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.request_class == RequestClass.EXACT_COMMAND
    # The safety backstop for a raw, unstructured answer to this shape:
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is True


def test_concern_a3_independent_governed_lookup_does_not_inherit():
    """"what is the alarm status on RRU-9?" -- a live, per-unit status
    check plausibly needing live operational context of its own --
    modeled with `requires_operational_context=True`. Current request
    wins; the pending restart is not silently completed."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[])
    contract = _clarification_answer_contract(
        provided=[_param("unit_id", "RRU-9")],
        subject="RRU-9 alarm status",
        requires_operational_context=True,
    )

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.request_class == RequestClass.OPERATIONAL_INFORMATION
    assert decision.subject == "RRU-9 alarm status"
    assert decision.may_emit_command is False


def test_concern_a4_explicit_new_exact_command_request_evaluated_normally():
    """"restart RRU-9" -- the model correctly, independently classifies
    this as its own EXACT_COMMAND request. Pending inheritance never
    overrides an already-governed current request -- it is simply not
    consulted, because the current turn never resolves OPERATIONAL_
    INFORMATION in the first place."""
    pending = _pending_restart_rru(missing=["unit_id"], provided=[])
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        subject="restart RRU",
        requested_output=RequestedOutput.EXACT_COMMAND,
        continuation=False,
        provided_context=[_param("unit_id", "RRU-9")],
        missing_context=[],
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )

    effective = resolve_effective_governed_contract(contract, pending)
    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert effective is contract  # no-op: already self-governed, nothing to merge
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is True


# ---------------------------------------------------------------------------
# Concern B -- COMPLETED must be immediate-correction only
# ---------------------------------------------------------------------------


def test_concern_b1_completed_eligible_for_immediate_correction():
    pending = _pending_restart_rru(
        missing=[],
        provided=[_param("unit_type", "RRU"), _param("unit_id", "RRU-3")],
        status=PendingGovernedRequestStatus.COMPLETED,
    )
    contract = _clarification_answer_contract(provided=[_param("unit_id", "RRU-10")])

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.status == RequestExecutionStatus.ALLOW


def test_concern_b2_completed_expires_across_an_invalid_contract_turn():
    """COMPLETED restart RRU-3 -> INVALID_CONTRACT turn -> RRU-10. The
    old COMPLETED record is no longer eligible once an intervening turn's
    contract could not even be validated."""
    completed = _pending_restart_rru(
        missing=[],
        provided=[_param("unit_type", "RRU"), _param("unit_id", "RRU-3")],
        status=PendingGovernedRequestStatus.COMPLETED,
    )

    # The intervening INVALID_CONTRACT turn: chat_service.py's own
    # `current_turn_request_contract is None` branch.
    expiry_update = expire_completed_pending_on_invalid_contract(completed)
    assert expiry_update == {PENDING_GOVERNED_REQUEST_STATE_KEY: None}
    surviving_pending = parse_pending_governed_request(expiry_update[PENDING_GOVERNED_REQUEST_STATE_KEY])
    assert surviving_pending is None

    # "RRU-10" now arrives with nothing left to correct against.
    contract = _clarification_answer_contract(provided=[_param("unit_id", "RRU-10")])
    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=surviving_pending)

    assert decision.request_class == RequestClass.OPERATIONAL_INFORMATION


def test_concern_b3_unresolved_survives_an_invalid_contract_turn():
    """UNRESOLVED restart RRU -> INVALID_CONTRACT turn -> valid
    clarification. UNRESOLVED remains recoverable -- the user still owes
    the same missing information regardless of one glitchy turn."""
    unresolved = _pending_restart_rru(missing=["unit_id"], provided=[_param("unit_type", "RRU")])

    expiry_update = expire_completed_pending_on_invalid_contract(unresolved)
    assert expiry_update == {}  # untouched -- no key at all

    # Session state was never written to for this key this turn -- the
    # SAME unresolved record is what the next turn reads back.
    contract = _clarification_answer_contract(provided=[_param("unit_type", "RRU")])
    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=unresolved)

    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION


# ---------------------------------------------------------------------------
# Concern C -- Absolute Final Output Authority (full ChatService pipeline)
# ---------------------------------------------------------------------------


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


async def _run_turn(contract: RequestContract, raw_text: str, requires_governed_knowledge: bool = False) -> str:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        await append_state_delta(
            session_service,
            session,
            {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_copy(update={"run_id": run_id}).model_dump(mode="json")},
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event(
            "record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": requires_governed_knowledge}
        ),
        FakeEvent(text=raw_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "test turn -- contract is seeded directly", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    return completed.data["content"]


@pytest.mark.asyncio
async def test_concern_c1_command_bearing_free_text_never_reaches_final_output_when_may_emit_command_false():
    """decision: may_emit_command=False (NEEDS_INFORMATION). Team
    Manager's own raw free text contains an exact command, with no
    structured TroubleshootingGuidance ever registered (simulating the
    proven live defect: the model answered directly, unvalidated). The
    command must not reach the final response."""
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        missing_context=["unit_id"],
        request_class=RequestClass.EXACT_COMMAND,
    )
    content = await _run_turn(contract, f"Sure, here you go: {_RESTART_COMMAND_TEXT}")

    assert _RESTART_COMMAND_TEXT not in content


@pytest.mark.asyncio
async def test_concern_c2_harmless_information_remains_usable_when_may_emit_command_false():
    """decision: may_emit_command=False (ALLOW/OPERATIONAL_INFORMATION,
    a resolved, harmless informational topic). Safety must not be
    achieved by suppressing every informational response -- the real,
    harmless answer remains visible."""
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="VSWR alarm",
        missing_context=[],
        request_class=RequestClass.OPERATIONAL_INFORMATION,
    )
    content = await _run_turn(contract, "VSWR stands for Voltage Standing Wave Ratio.")

    assert content == "VSWR stands for Voltage Standing Wave Ratio."


@pytest.mark.asyncio
async def test_concern_c3_authorized_command_with_grounded_guidance_still_renders():
    """decision: may_emit_command=True, with a real, already-captured,
    grounded TroubleshootingGuidance.command. The authorized command
    still reaches the final response -- this milestone's own safety
    tightening must not collaterally block the legitimate case."""
    from backend.agents.incident_manager.schemas import (
        TroubleshootingGuidance,
        TroubleshootingInteractionMode,
        TroubleshootingOperationalEffect,
    )
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("unit_type", "RRU"), _param("unit_id", "RRU-9")],
        missing_context=[],
        request_class=RequestClass.EXACT_COMMAND,
    )

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        await append_state_delta(
            session_service,
            session,
            {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_copy(update={"run_id": run_id}).model_dump(mode="json")},
        )
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                next_action="Run the command below.",
                command=_RESTART_COMMAND_TEXT,
                operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "restart RRU-9", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert _RESTART_COMMAND_TEXT in completed.data["content"]


@pytest.mark.asyncio
async def test_concern_c_documents_the_pre_existing_unscoped_gap_not_introduced_by_this_milestone():
    """HONEST BOUNDARY, NOT AN APPROVAL. Unlike test C1, this turn has NO
    pending governed request involved at all -- a genuinely, correctly
    classified `OPERATIONAL_INFORMATION`/`ALLOW` turn (may_emit_command=
    False, `is_operationally_shaped_request` is False for this shape by
    design, so `requires_unstructured_response_backstop` does not
    examine it -- deliberately, to avoid suppressing every ordinary
    informational reply, see test C2/C4). If Team Manager's own raw free
    text happens to contain a command-like string for ANY reason
    (hallucination, or a copy-pasted fragment from something it read),
    nothing in this pipeline inspects or replaces it.

    This is a PRE-EXISTING gap, already honestly documented in `requires_
    unstructured_response_backstop`'s own docstring since LIVE-CORR-3B --
    not introduced or worsened by LIVE-CORR-11's own pending-continuity
    mechanism, and not surgically closable within this milestone without
    either a primary regex/content-inspection mechanism (explicitly
    forbidden) or a materially larger "verified against real selected
    evidence" provenance redesign (that function's own docstring already
    identifies this as the concrete next step). Recorded here, reproduced
    directly, so the limitation is tracked rather than assumed away.
    """
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU-9 software version",
        missing_context=[],
        request_class=RequestClass.OPERATIONAL_INFORMATION,
    )
    content = await _run_turn(contract, f"By the way, you could also try: {_RESTART_COMMAND_TEXT}")

    # DOCUMENTS the current, real, unscoped limitation -- the command
    # passes through untouched. See this milestone's final report for
    # the minimum future architectural prerequisite to close it.
    assert _RESTART_COMMAND_TEXT in content


@pytest.mark.asyncio
async def test_concern_c4_general_conversation_remains_untouched():
    """"hello, how are you?" -- GENERAL_CONVERSATION, may_emit_command=
    False (there is no command field for it to suppress in the first
    place). The ordinary free-form answer must pass through unaffected."""
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject=None,
        missing_context=[],
        request_class=RequestClass.GENERAL_CONVERSATION,
    )
    content = await _run_turn(contract, "I'm doing well, thank you for asking! How can I help?")

    assert content == "I'm doing well, thank you for asking! How can I help?"
