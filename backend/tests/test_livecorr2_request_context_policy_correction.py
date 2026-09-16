"""LIVE-CORR-2 -- Request & Context Policy Correction.

Implements the confirmed, live-reproduced request/context-policy defects
from LIVE-CORR-1: DEF-0037 (conversational/informational overblocking),
DEF-0038 (non-monotonic target-context safety -- bounded interim fix,
see below), DEF-0039 (unsupported Knowledge-inventory response retaining
misleading sources), and DEF-0043 (generic clarification losing the
actual missing requirement). Does NOT implement DEF-0040, DEF-0041,
DEF-0042, or DEF-0044 -- those remain untouched, still OPEN.

Covers the milestone's own required test list (section 10) beyond what
`test_livecorr1_diagnostics.py`'s own (now partly de-xfailed) reproduction
tests already assert -- this file intentionally does not duplicate those,
only extends them.

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write, no fixture containing real governed document
content.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    TARGET_SPECIFIC_INTENTS,
    is_operationally_shaped_request,
    required_target_parameter_gaps,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionDecision,
    RequestExecutionStatus,
    command_suppression_fallback_text,
    derive_execution_decision,
)
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import (
    KnowledgeEvidenceItem,
    KnowledgeEvidenceReference,
    KnowledgeEvidenceSelectionKey,
    KnowledgeEvidenceSet,
)
from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta
from backend.tools.knowledge import runtime as rt

_RUN_ID = "livecorr2-run"


def _evidence_item(section_id: str, heading: str, content: str, knowledge_id: str = "livecorr2-doc") -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, heading=heading, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(
        knowledge_id=knowledge_id, version_label="v1", section_id=section_id, source_system="test", source_id=f"{knowledge_id}-doc"
    )
    return KnowledgeEvidenceItem(
        reference=reference,
        title="LIVE-CORR-2 Fixture Procedure",
        document_type=KnowledgeDocumentType.SOP,
        lifecycle_status=LifecycleStatus.APPROVED,
        source=source,
        section=section,
    )


def _select(run_id: str, *items: KnowledgeEvidenceItem) -> None:
    rt.get_or_init_run_state(run_id)
    execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=list(items)))
    rt.record_search_result(run_id, execution)
    keys = [
        KnowledgeEvidenceSelectionKey(knowledge_id=item.reference.knowledge_id, version_label=item.reference.version_label, section_id=item.reference.section_id)
        for item in items
    ]
    rt.select_evidence(run_id, keys)


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


# =============================================================================
# DEF-0037 -- intent/output compatibility matrix
# =============================================================================


@pytest.mark.parametrize(
    "text",
    ["hello", "hi", "good morning", "thank you", "what can you do?"],
)
def test_ordinary_conversation_never_forced_through_subject_gate(text: str) -> None:
    """Every plain conversational opener, classified `INFORMATION`/`FACT`
    with no subject, must ALLOW a presentation-only response -- none of
    these ever require an alarm/procedure clarification."""
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject=None,
        missing_context=[],
        ambiguity=False,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    # `may_emit_command` is a policy signal for `enforce_execution_
    # decision_on_guidance` (only consulted if `TroubleshootingGuidance`
    # exists this turn at all) -- ordinary conversational text produces
    # no such guidance, so `ALLOW` here correctly means "nothing to
    # suppress," never "a command was authorized."


@pytest.mark.parametrize(
    "intent,requested_output",
    [
        (RequestIntent.PROCEDURE, RequestedOutput.PROCEDURE_STEPS),
        (RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND),
        (RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP),
    ]
)
def test_missing_subject_operational_requests_still_clarify(intent: str, requested_output: str) -> None:
    """"give me the first command" / "give me the procedure" / "how do I
    troubleshoot it?" -shaped requests (no subject) must still clarify."""
    contract = RequestContract(intent=intent, requested_output=requested_output, subject=None, run_id=_RUN_ID)
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.AMBIGUOUS
    assert decision.may_emit_command is False


@pytest.mark.parametrize(
    "intent,requested_output",
    [
        (RequestIntent.INFORMATION, RequestedOutput.EXACT_COMMAND),  # mislabeled intent, command-shaped output
        (RequestIntent.COMMAND, RequestedOutput.FACT),  # operational intent, factual-shaped output
        (RequestIntent.PROCEDURE, RequestedOutput.TROUBLESHOOTING_NEXT_STEP),
        (RequestIntent.TROUBLESHOOTING, RequestedOutput.PROCEDURE_STEPS),
    ],
)
def test_intent_label_cannot_bypass_operational_safety(intent: str, requested_output: str) -> None:
    """An operationally-shaped combination is gated on subject regardless
    of which specific intent label the model happened to attach -- an
    intent label alone can never grant a bypass."""
    assert is_operationally_shaped_request(intent, requested_output) is True
    contract = RequestContract(intent=intent, requested_output=requested_output, subject=None, run_id=_RUN_ID)
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.AMBIGUOUS
    assert decision.may_emit_command is False


def test_knowledge_inventory_plus_procedure_steps_still_unsupported_capability() -> None:
    """KNOWLEDGE_INVENTORY's own earlier-checked branch remains
    unconditional, regardless of `requested_output`."""
    contract = RequestContract(
        intent=RequestIntent.KNOWLEDGE_INVENTORY, requested_output=RequestedOutput.PROCEDURE_STEPS, run_id=_RUN_ID
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.UNSUPPORTED_CAPABILITY


def test_action_plus_exact_command_still_requires_approval() -> None:
    """ACTION's own earlier-checked branch remains unconditional,
    regardless of `requested_output`."""
    contract = RequestContract(
        intent=RequestIntent.ACTION, requested_output=RequestedOutput.EXACT_COMMAND, action_requested=True, run_id=_RUN_ID
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.REQUIRES_APPROVAL
    assert decision.may_execute_action is False


def test_information_with_resolved_subject_and_no_gaps_still_allowed() -> None:
    """Non-regression: a genuinely factual, fully-resolved INFORMATION
    request (e.g. "what is the VSWR threshold for cell X?") is
    unaffected."""
    contract = RequestContract(
        intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, subject="VSWR threshold", missing_context=[], run_id=_RUN_ID
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW


def test_missing_context_still_blocks_regardless_of_intent_for_operational_shapes() -> None:
    """Non-regression for the ORIGINAL ROOT CAUSE B fix (6A.14 FINAL
    corrective pass) -- a request with a genuinely unresolved target,
    classified PROCEDURE/INFORMATION/TROUBLESHOOTING/COMMAND, still
    cannot ALLOW merely because of its intent label, as long as its
    OUTPUT shape is operational."""
    for intent in (RequestIntent.PROCEDURE, RequestIntent.INFORMATION, RequestIntent.TROUBLESHOOTING, RequestIntent.COMMAND):
        contract = RequestContract(
            intent=intent,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="HW Partial Fault",
            missing_context=["unit_type", "unit_id"],
            run_id=_RUN_ID,
        )
        decision = derive_execution_decision(contract, _RUN_ID)
        assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION, intent
        assert decision.may_emit_command is False, intent


# =============================================================================
# DEF-0038 -- monotonic target-context safety (bounded interim fix)
# =============================================================================


def test_no_target_context_exact_command_requires_confirmation() -> None:
    gaps = required_target_parameter_gaps(RequestIntent.PROCEDURE, RequestedOutput.EXACT_COMMAND, [])
    assert set(gaps) == {"unit_type", "unit_id"}


def test_partial_target_context_requires_only_the_identifier() -> None:
    gaps = required_target_parameter_gaps(
        RequestIntent.PROCEDURE,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
    )
    assert set(gaps) == {"unit_id"}


def test_complete_target_context_requires_nothing() -> None:
    gaps = required_target_parameter_gaps(
        RequestIntent.PROCEDURE,
        RequestedOutput.EXACT_COMMAND,
        [
            RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
            RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
        ],
    )
    assert gaps == []


def test_confirmed_non_identifier_bearing_unit_type_requires_nothing() -> None:
    """A genuinely confirmed `unit_type` OUTSIDE the identifier-bearing
    class (e.g. the real governed "SupportUnit" branch) is a real,
    verified fact establishing no identifier is needed -- never a gap."""
    gaps = required_target_parameter_gaps(
        RequestIntent.PROCEDURE,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="unit_type", value="SupportUnit", provenance=ParameterProvenance.USER)],
    )
    assert gaps == []


def test_monotonic_permission_ordering_across_three_context_levels() -> None:
    """THE direct DEF-0038 proof: permission(no context) <= permission
    (partial context) <= permission(complete context) -- expressed as gap
    COUNT, where MORE gaps means LESS permission."""
    no_context = required_target_parameter_gaps(RequestIntent.PROCEDURE, RequestedOutput.EXACT_COMMAND, [])
    partial_context = required_target_parameter_gaps(
        RequestIntent.PROCEDURE,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
    )
    complete_context = required_target_parameter_gaps(
        RequestIntent.PROCEDURE,
        RequestedOutput.EXACT_COMMAND,
        [
            RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
            RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
        ],
    )
    assert len(no_context) >= len(partial_context) >= len(complete_context) == 0
    assert set(no_context) >= set(partial_context)


def test_bounded_scope_procedure_steps_not_overblocked_by_the_interim_fix() -> None:
    """DEF-0038's own bounded scope, proven explicitly: the interim
    fail-closed rule applies ONLY to `EXACT_COMMAND` (the confirmed live
    defect shape) -- `PROCEDURE_STEPS`/`TROUBLESHOOTING_NEXT_STEP` may
    still legitimately describe a procedure's branches conceptually with
    no target confirmed yet, matching the pre-existing, unmodified
    `test_procedure_can_still_describe_branches_when_fully_resolved`."""
    gaps = required_target_parameter_gaps(RequestIntent.PROCEDURE, RequestedOutput.PROCEDURE_STEPS, [])
    assert gaps == []
    gaps = required_target_parameter_gaps(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, [])
    assert gaps == []


def test_non_operational_request_never_gains_a_target_gap() -> None:
    """A genuinely non-operational request (`FACT`/`KNOWLEDGE_LIST`)
    never gains a target-context gap regardless of `provided_context`."""
    assert required_target_parameter_gaps(RequestIntent.INFORMATION, RequestedOutput.FACT, []) == []
    assert required_target_parameter_gaps(RequestIntent.KNOWLEDGE_INVENTORY, RequestedOutput.KNOWLEDGE_LIST, []) == []


def test_def_0038_status_remains_open_bounded_not_fully_closed() -> None:
    """Documents the bounded nature of this fix, per instruction section
    6's own explicit "do not fake closure" requirement: this policy still
    has NO signal for whether the specific selected governed operation
    genuinely requires a target at all (e.g. a target-independent
    read-only discovery command) -- it fails closed instead of guessing.
    `TARGET_SPECIFIC_INTENTS` no longer includes `INFORMATION` (DEF-0037),
    confirmed here as a fixed contract, not re-derived."""
    assert RequestIntent.INFORMATION not in TARGET_SPECIFIC_INTENTS
    assert TARGET_SPECIFIC_INTENTS == frozenset({RequestIntent.COMMAND, RequestIntent.TROUBLESHOOTING, RequestIntent.PROCEDURE})


# =============================================================================
# DEF-0039 -- Knowledge-inventory evidence cleanup, including continuity
# anchors (not covered by test_livecorr1_diagnostics.py's own reproduction)
# =============================================================================


@pytest.mark.asyncio
async def test_inventory_turn_does_not_create_a_continuity_anchor_from_discarded_evidence() -> None:
    """A KNOWLEDGE_INVENTORY turn that happened to retrieve/select real
    Knowledge evidence before the capability decision was known must not
    let that discarded evidence become the turn's own persisted
    `last_selected_governed_evidence`/`active_governed_procedure`
    continuity anchor for a LATER, genuinely operational turn to
    (incorrectly) inherit."""
    from backend.api.chat_service import ChatService
    from backend.api.governed_evidence_continuity import (
        ACTIVE_GOVERNED_PROCEDURE_STATE_KEY,
        LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY,
    )
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    item = _evidence_item("s-inventory-anchor", "Aurora Relay Verification", "Some governed content unrelated to this test's assertion.")

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, item)
        contract = RequestContract(
            intent=RequestIntent.KNOWLEDGE_INVENTORY,
            requested_output=RequestedOutput.KNOWLEDGE_LIST,
            subject=None,
            requires_governed_knowledge=True,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="Here are the documents I have.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    async for _event in chat_service.execute_turn_events(session_id, "What MOPs do you have?", "api-user"):
        pass

    refreshed = await service.get_session(session_id, "api-user")
    assert refreshed.state.get(LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY) in (None, [])
    assert refreshed.state.get(ACTIVE_GOVERNED_PROCEDURE_STATE_KEY) in (None, {})


@pytest.mark.asyncio
async def test_inventory_turn_never_overwrites_a_genuinely_prior_continuity_anchor() -> None:
    """`build_last_selected_governed_evidence_state_update([])`'s own
    documented "empty means no change" contract means a PRIOR turn's
    real, valid continuity anchor is never blanked out merely because a
    LATER turn happened to be an unsupported inventory request."""
    from backend.api.chat_service import ChatService
    from backend.api.governed_evidence_continuity import LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    real_item = _evidence_item("s-real-procedure", "HW Partial Fault", "RRU:\nSome real content.\n\nSupportUnit=---:\nNo restart\n")
    inventory_item = _evidence_item("s-inventory-anchor-2", "Aurora Relay Verification", "Unrelated content.")

    async def _turn_1_side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, real_item)
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="HW Partial Fault",
            requires_governed_knowledge=True,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    async def _turn_2_side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, inventory_item)
        contract = RequestContract(
            intent=RequestIntent.KNOWLEDGE_INVENTORY,
            requested_output=RequestedOutput.KNOWLEDGE_LIST,
            subject=None,
            requires_governed_knowledge=True,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()

    events_1 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="Restart the RRU per the real procedure.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_turn_1_side_effect, events=events_1))
    async for _event in chat_service.execute_turn_events(session_id, "how do i handle HW Partial Fault?", "api-user"):
        pass

    after_turn_1 = await service.get_session(session_id, "api-user")
    anchor_after_turn_1 = after_turn_1.state.get(LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY)
    assert anchor_after_turn_1  # a real anchor was recorded

    events_2 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="Here are the documents I have.", final=True),
    ]
    chat_service_2 = ChatService(service, runner=FakeRunner(service, side_effect=_turn_2_side_effect, events=events_2))
    async for _event in chat_service_2.execute_turn_events(session_id, "What MOPs do you have?", "api-user"):
        pass

    after_turn_2 = await service.get_session(session_id, "api-user")
    assert after_turn_2.state.get(LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY) == anchor_after_turn_1


# =============================================================================
# DEF-0043 -- typed clarification reasons, broader coverage
# =============================================================================


def test_needs_information_status_unchanged_by_this_pass() -> None:
    """Non-regression: `NEEDS_INFORMATION` always carried a non-empty
    `missing_context` by construction before this pass -- its own
    rendering path is completely unaffected."""
    decision = RequestExecutionDecision(status=RequestExecutionStatus.NEEDS_INFORMATION, missing_context=["unit_id"])
    text = command_suppression_fallback_text(decision)
    assert "unit_id" not in text  # now safely labelled, never the raw key
    assert "unit identifier" in text


def test_ambiguous_no_subject_no_missing_context_keeps_no_subject_text() -> None:
    decision = RequestExecutionDecision(status=RequestExecutionStatus.AMBIGUOUS, subject=None, missing_context=[])
    text = command_suppression_fallback_text(decision)
    assert "alarm or governed procedure" in text


def test_ambiguous_with_subject_and_no_missing_context_uses_generic_text() -> None:
    """A resolved subject with a genuinely unresolvable procedure
    ambiguity (DEF-0029's own case) and NO specific declared gap still
    correctly falls back to the generic message -- nothing specific was
    ever computed to say."""
    decision = RequestExecutionDecision(status=RequestExecutionStatus.AMBIGUOUS, subject="HW Partial Fault", missing_context=[])
    text = command_suppression_fallback_text(decision)
    assert text == "An exact command cannot yet be safely provided for this step. Please confirm the missing details."


def test_invalid_contract_status_no_longer_presumes_an_alarm_or_procedure() -> None:
    """LIVE-CORR-5 correction (section 6 of that pass's own instruction):
    a missing/stale contract alone is not evidence the user asked about a
    specific alarm/procedure -- previously asserted the OPPOSITE
    ("alarm or governed procedure" IN text); now asserts the corrected,
    neutral wording. The underlying safety decision (command withheld) is
    unaffected by this wording-only change -- see `command_suppression_
    fallback_text`'s own `_INVALID_CONTRACT_FALLBACK_TEXT` docstring."""
    decision = RequestExecutionDecision(status=RequestExecutionStatus.INVALID_CONTRACT, missing_context=[])
    text = command_suppression_fallback_text(decision)
    assert "alarm or governed procedure" not in text
    assert text


def test_model_declared_missing_context_phrase_passes_through_unmodified() -> None:
    """A model-declared, already-safe phrase (never a raw internal key)
    is never altered by the safe-label mapping."""
    decision = RequestExecutionDecision(
        status=RequestExecutionStatus.AMBIGUOUS, subject="HW Partial Fault procedure", missing_context=["equipment identifier", "missing condition"]
    )
    text = command_suppression_fallback_text(decision)
    assert "equipment identifier" in text
    assert "missing condition" in text


def test_scenario_6_exact_request_shape() -> None:
    """Session `a6bbf7cf-258a-4ba1-a0b7-75c80fd94644`'s exact request:
    "For the approved HW Partial Fault procedure, give me the first
    command. Do not assume any equipment identifier or missing
    condition." """
    contract = RequestContract(
        intent=RequestIntent.PROCEDURE,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="HW Partial Fault procedure",
        ambiguity=True,
        missing_context=["equipment identifier", "missing condition"],
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    text = command_suppression_fallback_text(decision)
    assert decision.may_emit_command is False
    assert "equipment identifier" in text
    assert "missing condition" in text
    assert text != "An exact command cannot yet be safely provided for this step. Please confirm the missing details."
