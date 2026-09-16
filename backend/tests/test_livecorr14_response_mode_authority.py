"""LIVE-CORR-14 -- Make RequestExecutionDecision the Sole Response-Mode
Authority.

PROVEN LIVE FAILURE ("give me a command to restart an RRU"):

    request_class=exact_command, fresh=True
    WorkEnvelope maximum_authority=command_candidate
    grounding: command_present=True stripped=True reason=grounding_rejected
    decision: status=needs_information
              missing_context=[unit_id, unit_type]
              may_emit_command=False may_execute_action=False

    -> message_completed_emitted=False error_code=run_failure

The deterministic decision was CORRECT. The response ORCHESTRATION was
wrong: `chat_service.py`'s finalization selected the top-level response
shape from ARTIFACT state, not from the decision --

    if captured_troubleshooting_guidance is not None:   # artifact shape
        if status in (NEEDS_INFORMATION, AMBIGUOUS): ...
        elif command_suppressed_by_policy: ...
        elif rendered_guidance_text: ...
        elif final_text is None: ...
    elif response_mode_incompatible: ...               # artifact shape
    elif governed_completion_used_deterministic_fallback: ...  # sets no text
    elif requires_unstructured_response_backstop(...): ...

-- so whichever artifact-shaped condition matched FIRST claimed the turn,
and the clarification the decision already required became structurally
unreachable for any shape that landed in a sibling branch.

THE FIX (backend/api/chat_service.py): `_select_response_mode(decision)`
derives the response mode from `RequestExecutionDecision.status` ALONE,
immediately after the final decision exists, and the finalization block
dispatches on that mode. Guidance presence, rendered-guidance text,
`command_suppressed_by_policy`, `response_mode_incompatible`, the
governed-completion deterministic-fallback signal and `requires_
unstructured_response_backstop` all REMAIN -- demoted to CONTENT selection
inside the single `AUTHORIZED_RESPONSE` (status=ALLOW) mode.

Authority order now:

    RequestExecutionDecision > response mode > specialist/model content
        > validate_final_output

Unchanged by this pass, and asserted as such below where observable:
`RequestContract`, `RequestClass`, `PendingGovernedRequest`, the
effective-governed-request merge, `WorkEnvelope`, governed retrieval,
evidence selection, grounding, the rejected-command inventory,
`derive_execution_decision`, `may_emit_command`/`may_execute_action`,
`AuthorizedResponseContext`, `validate_final_output`, action execution.

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingOperationalEffect,
    TroubleshootingStep,
)
from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    RequestClass,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
)
from backend.agents.team_manager.request_execution_policy import (
    KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT,
    RequestExecutionDecision,
    RequestExecutionStatus,
)
from backend.api.chat_service import _RESPONSE_MODE_BY_STATUS, _ResponseMode, _select_response_mode
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_RESTART_RRU_QUESTION = "give me a command to restart an RRU"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


def _no_final_text_event() -> FakeEvent:
    """The proven live shape: the turn's own final Runner event is
    `is_final_response()=True` with NO accompanying text -- the model
    emitted only a function call (`_extract_final_text` correctly returns
    `None`)."""
    return FakeEvent(text=None, final=True)


def _exact_command_needs_information_contract(run_id: str) -> RequestContract:
    """The EXACT live contract shape: `request_class=exact_command`,
    unresolved `unit_id`/`unit_type` -> `derive_execution_decision`
    resolves `status=NEEDS_INFORMATION, may_emit_command=False`."""
    return RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        missing_context=["unit_id", "unit_type"],
        request_class=RequestClass.EXACT_COMMAND,
        run_id=run_id,
    )


async def _collect(chat_service: Any, session_id: str, message: str) -> tuple[Optional[dict], list[Any]]:
    """Returns `(message.completed payload or None, every event)` so a test
    can assert BOTH the response content and that `MESSAGE_COMPLETED` was
    actually emitted (never an `error` event instead)."""
    completed: Optional[dict] = None
    events: list[Any] = []
    async for event in chat_service.execute_turn_events(session_id, message, "api-user"):
        events.append(event)
        if event.type.value == "message.completed":
            completed = event.data
    return completed, events


def _event_types(events: list[Any]) -> list[str]:
    return [e.type.value for e in events]


async def _run_rru_turn(
    guidance: Optional[TroubleshootingGuidance],
    *,
    register_rejected: bool = True,
) -> tuple[Optional[dict], list[Any]]:
    """The live RRU turn, parameterised ONLY by the specialist artifact --
    the single variable this milestone proves must NOT influence the
    response mode. `register_rejected=True` mirrors the live
    `reason=grounding_rejected` trace: the rejected command value is in
    this turn's own SEQ-06 inventory, so `validate_final_output` would
    catch it if any path reconstructed it.
    """
    from backend.api.chat_service import ChatService
    from backend.api.rejected_command_context import register_rejected_commands
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _exact_command_needs_information_contract(run_id)
        await append_state_delta(
            session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        )
        if register_rejected:
            register_rejected_commands(run_id, [_RRU_COMMAND])
        if guidance is not None:
            register_troubleshooting_guidance(run_id, guidance)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))
    return await _collect(chat_service, session_id, _RESTART_RRU_QUESTION)


def _assert_clarification(completed: Optional[dict], events: list[Any]) -> str:
    """Section 4's own acceptance criteria, applied identically to TESTS
    1-3: non-empty clarification, no command, validator ran (a rejected
    command in the inventory means `prohibited_commands` is non-empty, so
    `validate_final_output` did real work), MESSAGE_COMPLETED emitted."""
    assert completed is not None, "MESSAGE_COMPLETED was never emitted"
    content = completed["content"]
    assert content and content.strip(), "clarification must be non-empty"
    assert _RRU_COMMAND not in content
    assert "unit" in content.lower(), "clarification must name the authoritative missing context"
    assert "message.completed" in _event_types(events)
    assert "error" not in _event_types(events)
    return content


# =============================================================================
# TEST 1 -- LIVE RRU EXACT SHAPE: no guidance at all, no Runner text, a
# grounding-rejected command, status=NEEDS_INFORMATION.
# =============================================================================


@pytest.mark.asyncio
async def test_1_live_rru_exact_shape_produces_clarification() -> None:
    completed, events = await _run_rru_turn(None)
    _assert_clarification(completed, events)


# =============================================================================
# TEST 2 -- SAME, but an EMPTY TroubleshootingGuidance object exists
# (evidence.py's own upstream grounding already emptied it).
# =============================================================================


@pytest.mark.asyncio
async def test_2_same_shape_with_empty_guidance_object_is_identical() -> None:
    completed, events = await _run_rru_turn(
        TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
            operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
        )
    )
    _assert_clarification(completed, events)


# =============================================================================
# TEST 3 -- SAME, but with FULLY POPULATED guidance, command included:
# clarification still wins. The artifact may not out-rank the decision.
# =============================================================================


@pytest.mark.asyncio
async def test_3_same_shape_with_populated_guidance_clarification_still_wins() -> None:
    completed, events = await _run_rru_turn(
        TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
            interpretation="The RRU appears to need a restart.",
            next_action="Restart the RRU.",
            command=_RRU_COMMAND,
        )
    )
    content = _assert_clarification(completed, events)
    # The specialist's own renderable prose must not be presented as an
    # authorized answer for a turn the decision says needs information.
    assert "Restart the RRU." not in content


@pytest.mark.asyncio
async def test_3b_full_procedure_guidance_does_not_divert_to_the_mode_restriction_text() -> None:
    """The specific old-chain shadowing this milestone closes: a FULL_
    PROCEDURE guidance for an EXACT_COMMAND contract was discarded by
    `enforce_response_mode_compatibility`, and the `response_mode_
    incompatible` branch then produced `FULL_PROCEDURE_NOT_PERMITTED_
    FALLBACK_TEXT` INSTEAD of the clarification the decision required."""
    completed, events = await _run_rru_turn(
        TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
            full_procedure_steps=[
                TroubleshootingStep(action="Identify the faulty RRU.", command=None),
                TroubleshootingStep(action="Restart it.", command=_RRU_COMMAND),
            ],
        )
    )
    content = _assert_clarification(completed, events)
    assert "A complete procedure was generated" not in content


# =============================================================================
# TEST 4 -- PROCEDURE ALLOW + safe guidance survives: the authorized
# specialist content is returned (no clarification, no fallback).
# =============================================================================


@pytest.mark.asyncio
async def test_4_procedure_allow_with_safe_guidance_returns_authorized_content() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="check alarms on Ericsson ENM",
            ambiguity=False,
            missing_context=[],
            request_class=RequestClass.PROCEDURE_TROUBLESHOOTING,
            run_id=run_id,
        )
        await append_state_delta(
            session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        )
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                interpretation="This is a routine alarm check.",
                next_action="Open the ENM Alarm List application and review current alarms.",
                operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed, collected = await _collect(chat_service, session_id, "how can i check alarms on ericsson enm ?")

    assert completed is not None
    content = completed["content"]
    assert "Open the ENM Alarm List application and review current alarms." in content
    assert "An exact command cannot yet be safely provided" not in content
    assert "message.completed" in _event_types(collected)


# =============================================================================
# TEST 5 -- PROCEDURE ALLOW + guidance left EMPTY by grounding: the
# existing safe deterministic fallback, never run_failure.
# =============================================================================


@pytest.mark.asyncio
async def test_5_procedure_allow_with_empty_guidance_falls_back_safely_not_run_failure() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.rejected_command_context import register_rejected_commands
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="check alarms on Ericsson ENM",
            ambiguity=False,
            missing_context=[],
            request_class=RequestClass.PROCEDURE_TROUBLESHOOTING,
            run_id=run_id,
        )
        await append_state_delta(
            session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        )
        # cross_procedure_evidence: grounding already emptied the ENTIRE
        # guidance and recorded the command it rejected.
        register_rejected_commands(run_id, [_RRU_COMMAND])
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed, collected = await _collect(chat_service, session_id, "how can i check alarms on ericsson enm ?")

    assert completed is not None
    content = completed["content"]
    assert content and content.strip()
    assert _RRU_COMMAND not in content
    assert "error" not in _event_types(collected)


# =============================================================================
# TEST 6 -- GENERAL_CONVERSATION: current passing behavior preserved
# byte-for-byte (the model's own text is returned untouched).
# =============================================================================


@pytest.mark.asyncio
async def test_6_general_conversation_behavior_is_unchanged() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject=None,
            ambiguity=False,
            missing_context=[],
            request_class=RequestClass.GENERAL_CONVERSATION,
            run_id=run_id,
        )
        await append_state_delta(
            session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="Hello there! How can I help you today?", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed, _ = await _collect(chat_service, session_id, "hello")

    assert completed is not None
    assert completed["content"] == "Hello there! How can I help you today?"


# =============================================================================
# TEST 7 -- AMBIGUOUS + guidance present: disambiguation wins.
# =============================================================================


@pytest.mark.asyncio
async def test_7_ambiguous_with_guidance_present_disambiguation_wins() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject=None,
            ambiguity=True,
            missing_context=[],
            request_class=RequestClass.PROCEDURE_TROUBLESHOOTING,
            run_id=run_id,
        )
        await append_state_delta(
            session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        )
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                interpretation="Assuming you mean the RRU restart procedure.",
                next_action="Restart the RRU.",
                operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed, collected = await _collect(chat_service, session_id, "restart it")

    assert completed is not None
    content = completed["content"]
    assert content and content.strip()
    # The guidance's own assumed-subject content must never stand in for
    # the disambiguation the decision required.
    assert "Restart the RRU." not in content
    assert "message.completed" in _event_types(collected)
    assert "error" not in _event_types(collected)


# =============================================================================
# TEST 8 -- RESTRICTION (UNSUPPORTED_CAPABILITY) + guidance present: the
# capability restriction wins. Under the old chain the discarded guidance
# diverted this turn into FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT.
# =============================================================================


@pytest.mark.asyncio
async def test_8_unsupported_capability_restriction_wins_over_guidance() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.KNOWLEDGE_INVENTORY,
            requested_output=RequestedOutput.KNOWLEDGE_LIST,
            subject="all governed documents",
            ambiguity=False,
            missing_context=[],
            request_class=RequestClass.OPERATIONAL_INFORMATION,
            run_id=run_id,
        )
        await append_state_delta(
            session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        )
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
                full_procedure_steps=[TroubleshootingStep(action="Open the document library.", command=None)],
                operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed, collected = await _collect(chat_service, session_id, "list every MOP you have")

    assert completed is not None
    assert completed["content"] == KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT
    assert "A complete procedure was generated" not in completed["content"]
    assert "Open the document library." not in completed["content"]
    assert "error" not in _event_types(collected)


# =============================================================================
# TEST 9 -- the selector itself: total over the status set, and derived
# from `status` ALONE.
# =============================================================================


def test_9_response_mode_selector_is_total_over_every_status() -> None:
    expected = {
        RequestExecutionStatus.NEEDS_INFORMATION: _ResponseMode.CLARIFICATION,
        RequestExecutionStatus.AMBIGUOUS: _ResponseMode.CLARIFICATION,
        RequestExecutionStatus.UNSUPPORTED_CAPABILITY: _ResponseMode.RESTRICTION,
        RequestExecutionStatus.REQUIRES_APPROVAL: _ResponseMode.APPROVAL,
        RequestExecutionStatus.ALLOW: _ResponseMode.AUTHORIZED_RESPONSE,
        RequestExecutionStatus.INVALID_CONTRACT: _ResponseMode.UNRESOLVED_CONTRACT,
    }
    assert _RESPONSE_MODE_BY_STATUS == expected
    for status, mode in expected.items():
        assert _select_response_mode(RequestExecutionDecision(status=status, reason="t")) == mode


def test_9b_response_mode_ignores_every_non_status_field() -> None:
    """Section 3's own prohibition: guidance presence, model prose and
    command presence may not influence the mode -- structurally
    guaranteed because the selector only ever receives the decision, and
    only ever reads `status` from it."""
    permissive = RequestExecutionDecision(
        status=RequestExecutionStatus.NEEDS_INFORMATION,
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        missing_context=["unit_id", "unit_type"],
        may_emit_command=True,
        may_emit_operational_steps=True,
        may_execute_action=True,
        reason="t",
    )
    restrictive = RequestExecutionDecision(
        status=RequestExecutionStatus.NEEDS_INFORMATION,
        may_emit_command=False,
        reason="t",
    )
    assert _select_response_mode(permissive) == _ResponseMode.CLARIFICATION
    assert _select_response_mode(restrictive) == _ResponseMode.CLARIFICATION

    allowed = RequestExecutionDecision(status=RequestExecutionStatus.ALLOW, may_emit_command=False, reason="t")
    assert _select_response_mode(allowed) == _ResponseMode.AUTHORIZED_RESPONSE


# =============================================================================
# TEST 10 -- non-regression: the governed-completion deterministic fallback
# still wins inside ALLOW (LIVE-CORR-11's own live regression), and an
# ALLOW turn can never finish with empty text.
# =============================================================================


@pytest.mark.asyncio
async def test_10_governed_completion_deterministic_fallback_still_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.agents.team_manager import governed_knowledge_completion as gkc
    from backend.api import chat_service as chat_service_module
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    ambiguous_procedure_text = (
        "Do you mean the AuxPluginUnit Restart or FieldReplaceableUnit Restart procedure? "
        "Please confirm which one you mean so I can give you the correct governed guidance."
    )

    async def _fake_completion(
        *,
        question: str,
        chat_topic: Optional[str],
        run_id: str,
        image_parts: Any = (),
        prior_governed_evidence: Any = (),
        active_anchor: Any = None,
        request_contract_subject: Optional[str] = None,
    ) -> tuple[str, list[Any]]:
        gkc._mark_governed_completion_deterministic_fallback(run_id)
        return ambiguous_procedure_text, []

    monkeypatch.setattr(chat_service_module, "enforce_governed_knowledge_at_completion", _fake_completion)

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            ambiguity=False,
            continuation=True,
            requires_governed_knowledge=True,
            missing_context=[],
            provided_context=[RequestParameter(name="unit_id", value="RRU-3", provenance=ParameterProvenance.USER)],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(
            session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed, collected = await _collect(chat_service, session_id, "the RRU is RRU-3")

    assert completed is not None
    assert completed["content"] == ambiguous_procedure_text
    assert "error" not in _event_types(collected)


@pytest.mark.asyncio
async def test_10b_allow_turn_with_no_renderable_content_never_run_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LIVE-CORR-14 section 11's hard invariant, for the exact hole the old
    chain left open: the governed-completion deterministic-fallback branch
    set `final_response_path` but no text, so an empty/absent result there
    fell straight through to `final_text=None` -> `run_failure`. A valid
    `ALLOW` policy state must now always finish with non-empty text."""
    from backend.agents.team_manager import governed_knowledge_completion as gkc
    from backend.api import chat_service as chat_service_module
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _fake_completion(
        *,
        question: str,
        chat_topic: Optional[str],
        run_id: str,
        image_parts: Any = (),
        prior_governed_evidence: Any = (),
        active_anchor: Any = None,
        request_contract_subject: Optional[str] = None,
    ) -> tuple[str, list[Any]]:
        gkc._mark_governed_completion_deterministic_fallback(run_id)
        return "", []

    monkeypatch.setattr(chat_service_module, "enforce_governed_knowledge_at_completion", _fake_completion)

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject="VSWR",
            ambiguity=False,
            missing_context=[],
            request_class=RequestClass.OPERATIONAL_INFORMATION,
            run_id=run_id,
        )
        await append_state_delta(
            session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed, collected = await _collect(chat_service, session_id, "what is VSWR?")

    assert completed is not None, "a valid ALLOW policy state must not fail closed to run_failure"
    assert completed["content"].strip()
    assert "error" not in _event_types(collected)
