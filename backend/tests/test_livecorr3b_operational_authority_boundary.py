"""LIVE-CORR-3B -- Operational Authority Boundary.

Closes the remaining fail-open paths where model-generated classification,
missing structured guidance, or unrestricted summary text could bypass
operational safety, on top of the frozen 6A.12/6A.13/6A.14/6A.14A
foundation and the LIVE-CORR-1/2/3 corrective passes:

  1. `derive_execution_decision`'s ALLOW branch no longer implicitly grants
     `may_emit_command=True` for every fully-resolved request -- command
     permission is now possible ONLY for a validated
     `intent=COMMAND, requested_output=EXACT_COMMAND` request. A
     PROCEDURE_STEPS/TROUBLESHOOTING_NEXT_STEP-shaped ALLOW decision never
     implicitly inherits it.

  2. `requires_unstructured_response_backstop` now ALSO fires for `ALLOW`
     (whenever the validated request is operationally shaped) --
     previously only `NEEDS_INFORMATION`/`AMBIGUOUS` were covered, leaving
     an operational ALLOW turn free to answer via unrestricted free-form
     `summary` prose with no structured `TroubleshootingGuidance` to
     enforce against at all. `INVALID_CONTRACT` was AUDITED for the same
     widening and deliberately left UNCHANGED (`False`, as before this
     pass) -- see that function's own docstring for the two candidate
     designs measured directly against this repository's real test suite
     and rejected on real evidence (unconditional firing collaterally
     breaks dozens of unrelated pre-existing tests; gating on the turn's
     own `record_source_requirements` declaration is unsafe in the
     opposite direction, since a `requires_governed_knowledge=True` turn
     is already fully covered by the pre-existing governed-knowledge
     completion gate). A real, missing-contract turn that ALSO required
     governed Knowledge is still closed today -- by that pre-existing
     mechanism, not by this one (see test 1 below).

  3. The `TroubleshootingOperationalEffect.DIAGNOSTIC_READ` target-
     independent exemption (`enforce_execution_decision_on_guidance`) is
     REMOVED outright -- a model-mislabeled REFERENCE_DESCRIPTION/
     OBSERVATION/DIAGNOSTIC_READ command no longer bypasses target
     confirmation.

  4. `required_target_parameter_gaps` no longer treats an unrecognized/
     unknown `unit_type` as automatically gap-free -- only the small,
     positively VERIFIED `SUPPORTUNIT` allowlist is exempt; every other
     value (known identifier-bearing types AND anything unrecognized)
     conservatively still requires `unit_id`.

  5. `TroubleshootingStep`/`TroubleshootingGuidance` gained an additive
     `source_section_id` proposal field, deterministically verified
     against the backend-owned selected-evidence set
     (`evidence.py`'s `_verify_source_section_reference`) before a
     step/guidance's command is trusted -- rejects a section reference
     outside this turn's own selected evidence, or naming a section other
     than the resolved active procedure.

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.agents.incident_manager.evidence import (
    CommandGroundingReason,
    enforce_procedure_scoped_command_grounding_with_reason,
)
from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingOperationalEffect,
    TroubleshootingStep,
)
from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    required_target_parameter_gaps,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionStatus,
    derive_execution_decision,
    requires_unstructured_response_backstop,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_RUN_ID = "run-livecorr3b"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


def _contract(**overrides: Any) -> RequestContract:
    defaults: dict[str, Any] = dict(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        continuation=True,
        run_id=_RUN_ID,
    )
    defaults.update(overrides)
    return RequestContract(**defaults)


# =============================================================================
# Pure decision-level tests
# =============================================================================


def test_information_fact_never_grants_command_or_operational_steps() -> None:
    """Item 3: INFORMATION/FACT and ordinary conversation always have
    may_emit_command=False, may_emit_operational_steps=False."""
    decision = derive_execution_decision(
        RequestContract(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, run_id=_RUN_ID), _RUN_ID
    )
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is False
    assert decision.may_emit_operational_steps is False


def test_procedure_steps_allow_never_implicitly_grants_command() -> None:
    """A fully-resolved PROCEDURE_STEPS ALLOW decision may show operational
    steps, but never implicitly inherits exact-command permission."""
    contract = _contract(intent=RequestIntent.PROCEDURE, requested_output=RequestedOutput.PROCEDURE_STEPS, missing_context=[])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is False
    assert decision.may_emit_operational_steps is True


def test_troubleshooting_next_step_allow_never_implicitly_grants_command() -> None:
    contract = _contract(
        provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
                           RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER)],
        missing_context=[],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is False


def test_exact_command_allow_grants_command() -> None:
    contract = _contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
                           RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER)],
        missing_context=[],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is True


def test_missing_stale_invalid_contract_grants_no_permission() -> None:
    absent = derive_execution_decision(None, _RUN_ID)
    stale = derive_execution_decision(_contract(run_id="run-OLD"), _RUN_ID)
    for decision in (absent, stale):
        assert decision.status == RequestExecutionStatus.INVALID_CONTRACT
        assert decision.may_emit_command is False
        assert decision.may_execute_action is False
        assert decision.may_emit_operational_steps is False


def test_unknown_unit_type_without_identifier_requires_identifier() -> None:
    """Item 4: an unrecognized unit_type must not become permissive."""
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="unit_type", value="Foobar", provenance=ParameterProvenance.USER)],
    )
    assert gaps == ["unit_id"]


def test_supportunit_remains_the_one_verified_target_independent_type() -> None:
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="unit_type", value="SupportUnit", provenance=ParameterProvenance.USER)],
    )
    assert gaps == []


def test_backstop_still_does_not_fire_for_invalid_contract() -> None:
    """Item 2's own STOP condition, honestly recorded: `INVALID_CONTRACT`
    was audited for the same ALLOW-style widening and deliberately left
    unchanged -- see `requires_unstructured_response_backstop`'s own
    docstring for the two candidate designs measured directly against this
    repository's real test suite and rejected on real evidence."""
    decision = derive_execution_decision(None, _RUN_ID)
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is False


def test_backstop_fires_for_operationally_shaped_allow_with_no_guidance() -> None:
    contract = _contract(intent=RequestIntent.PROCEDURE, requested_output=RequestedOutput.PROCEDURE_STEPS, missing_context=[])
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is True


def test_backstop_does_not_fire_for_ordinary_conversation_allow() -> None:
    """Preserves greeting/ordinary-conversation behavior."""
    contract = RequestContract(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, run_id=_RUN_ID)
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is False


def test_backstop_never_fires_when_guidance_is_present() -> None:
    decision = derive_execution_decision(None, _RUN_ID)
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=True) is False


# =============================================================================
# evidence.py -- source_section_id verification (item 5)
# =============================================================================


def test_command_from_unselected_section_is_rejected() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="x",
        next_action="Restart it.",
        command=_RRU_COMMAND,
        source_section_id="s-not-selected",
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id=None, question=None)
    assert stripped is True
    assert corrected.command is None
    assert reason == CommandGroundingReason.UNVERIFIED_SECTION_REFERENCE


def test_unset_source_section_id_is_a_complete_no_op() -> None:
    """The additive field defaults to unset for every pre-existing caller
    -- zero behavior change when it is never populated."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="The approved procedure documents both branches.",
        next_action="Review the documented branches.",
        command=None,
        operational_effect=TroubleshootingOperationalEffect.REFERENCE_DESCRIPTION,
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id=None, question=None)
    assert stripped is False
    assert reason is None


# =============================================================================
# REQUIRED PIPELINE TESTS -- real ChatService + FakeRunner
# =============================================================================


@pytest.mark.asyncio
async def test_1_missing_contract_and_raw_operational_summary_yields_safe_fallback() -> None:
    """No record_request_contract call at all this turn (INVALID_CONTRACT)
    -- a raw, operational-looking summary must never reach the user.
    Closed here by the PRE-EXISTING, unmodified governed-knowledge
    completion gate (`chat_service.py`'s own `governed_completion_needed`
    branch: `requires_governed_knowledge=True` declared, but no evidence
    was ever genuinely selected this turn, so the raw text is untrusted
    and deterministic remediation is forced) -- NOT by a new `RequestContract`
    -specific mechanism; see `requires_unstructured_response_backstop`'s
    own docstring for why extending IT to `INVALID_CONTRACT` was audited
    and intentionally not done (a documented STOP condition, not an
    oversight). This test proves the overall system-level invariant this
    milestone's OBJECTIVE cares about still holds for this exact scenario,
    via the mechanism that actually closes it."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    live_defect_text = f"To restart the unit, use the command: {_RRU_COMMAND}. This applies regardless of unit type."

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        return None  # deliberately never calls record_request_contract this turn

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text=live_defect_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "how do i restart it?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    content = completed.data["content"]
    assert content != live_defect_text
    assert _RRU_COMMAND not in content


@pytest.mark.asyncio
async def test_2_allow_operational_contract_with_missing_guidance_yields_safe_fallback() -> None:
    """A fully-resolved, ALLOW-status, operationally-shaped contract that
    never populated TroubleshootingGuidance -- free-form command/procedure
    prose in `summary` must never reach the user."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    live_defect_text = f"Step 1: check the alarm log. Step 2: restart the unit using {_RRU_COMMAND}."

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.PROCEDURE,
            requested_output=RequestedOutput.PROCEDURE_STEPS,
            subject="HW Partial Fault",
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        # troubleshooting_guidance is DELIBERATELY never registered.

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text=live_defect_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "show me the complete approved procedure", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    content = completed.data["content"]
    assert content != live_defect_text
    assert _RRU_COMMAND not in content


@pytest.mark.asyncio
async def test_4_troubleshooting_next_step_with_no_target_withholds_state_change_command() -> None:
    """Item 4/test-list-4: TROUBLESHOOTING_NEXT_STEP with no target info +
    a STATE_CHANGE_RECOMMENDATION command -- withheld using the real
    derived decision, not a hand-built one."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _contract(missing_context=["unit_type", "unit_id"], run_id=run_id)
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                interpretation="Confirm the unit type first.",
                next_action="Restart the unit.",
                command=_RRU_COMMAND,
                evidence_requested="Report the unit type.",
                operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "how do i handle HW Partial Fault?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert _RRU_COMMAND not in completed.data["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operational_effect",
    [TroubleshootingOperationalEffect.DIAGNOSTIC_READ, TroubleshootingOperationalEffect.OBSERVATION],
)
async def test_5_restart_command_mislabeled_diagnostic_or_observation_still_withheld(operational_effect: TroubleshootingOperationalEffect) -> None:
    """Item 3/test-list-5: mislabeling a real restart command as
    DIAGNOSTIC_READ/OBSERVATION must not bypass enforcement -- the removed
    DIAGNOSTIC_READ exemption no longer exists at all."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _contract(missing_context=["unit_type", "unit_id"], run_id=run_id)
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                interpretation="x",
                next_action="Restart the unit.",
                command=_RRU_COMMAND,
                evidence_requested="z",
                operational_effect=operational_effect,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "how do i handle HW Partial Fault?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert _RRU_COMMAND not in completed.data["content"]


@pytest.mark.asyncio
async def test_6_unknown_unit_type_without_identifier_withholds_exact_command() -> None:
    """Item 4/test-list-6: an unrecognized unit_type, with no identifier
    and no model-declared missing_context, still withholds the command --
    the deterministic backstop in derive_execution_decision, not the
    model's own self-report, is what catches it."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    unknown_type_command = "accn FieldReplaceableUnit=XYZ-1 restartunit 1 1 1"

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _contract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            provided_context=[RequestParameter(name="unit_type", value="XYZ", provenance=ParameterProvenance.USER)],
            missing_context=[],  # model itself (wrongly) declares nothing missing
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                interpretation="x",
                next_action="Restart the unit.",
                command=unknown_type_command,
                evidence_requested="z",
                operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "restart the XYZ unit", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert unknown_type_command not in completed.data["content"]


@pytest.mark.asyncio
async def test_7_grounded_exact_command_with_verified_target_and_active_section_is_allowed() -> None:
    """Item 7: a properly grounded exact command, with a fully verified
    target (unit_type=RRU, unit_id=RRU-9) and a resolved active governed
    section (`source_section_id` matching the turn's own real selected
    evidence), is allowed through."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
    from backend.knowledge.provenance.contracts import (
        KnowledgeEvidenceItem,
        KnowledgeEvidenceReference,
        KnowledgeEvidenceSelectionKey,
        KnowledgeEvidenceSet,
    )
    from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
    from backend.tools.knowledge import runtime as rt

    section = KnowledgeSection(
        section_id="s-hwpf", knowledge_id="doc1", sequence=0, heading="HW Partial Fault", content=f"RRU:\n{_RRU_COMMAND}\n"
    )
    source = KnowledgeSource(source_system="test", source_id="doc1-doc")
    reference = KnowledgeEvidenceReference(knowledge_id="doc1", version_label="v1", section_id="s-hwpf", source_system="test", source_id="doc1-doc")
    item = KnowledgeEvidenceItem(
        reference=reference, title="Fixture", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section
    )

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        rt.get_or_init_run_state(run_id)
        execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=[item]))
        rt.record_search_result(run_id, execution)
        rt.select_evidence(run_id, [KnowledgeEvidenceSelectionKey(knowledge_id="doc1", version_label="v1", section_id="s-hwpf")])
        contract = _contract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            provided_context=[
                RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
                RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
            ],
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                interpretation="RRU confirmed.",
                next_action="Restart the RRU.",
                command=_RRU_COMMAND,
                evidence_requested="Confirm the restart completed.",
                operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
                source_section_id="s-hwpf",
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
    async for event in chat_service.execute_turn_events(session_id, "Give me the exact command for HW Partial Fault, unit is RRU-9.", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert _RRU_COMMAND in completed.data["content"]


@pytest.mark.asyncio
async def test_8_live_completed_text_equals_refreshed_canonical_history_text(tmp_path: Any) -> None:
    """Item 8: for a turn this milestone's own new ALLOW-path backstop
    (item 2) deterministically replaces, the live `message.completed` text
    equals the refreshed canonical-history projection, byte-for-byte.

    Uses a REAL `google.adk.runners.Runner` + a scripted `BaseLlm` driving
    the REAL `team_manager` agent object against a file-backed
    `DatabaseSessionService` -- mirrors `test_p6a14a_canonical_turn_result
    .py`'s own established, working pattern for this exact class of proof
    (`FakeRunner` does not durably persist real ADK session events the way
    `session_history_service.py`'s projection requires -- every existing
    canonical live/history equality test in this codebase already uses a
    real, scripted `Runner` for that reason, never `FakeRunner`)."""
    from typing import AsyncGenerator

    from google.adk.models import BaseLlm, LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions import DatabaseSessionService
    from google.genai import types
    from pydantic import PrivateAttr

    from backend.agents.team_manager.agent import team_manager
    from backend.api.chat_service import ChatService
    from backend.api.session_history_service import get_session_history
    from backend.api.session_service import APP_NAME, ApiSessionService
    from backend.attachments.repository import AttachmentRepository
    from backend.attachments.service import AttachmentService

    live_defect_text = f"Step 1: check the alarm log. Step 2: restart the unit using {_RRU_COMMAND}."

    class _OperationalAllowNoGuidanceOuterLlm(BaseLlm):
        """Declares a fully-resolved, ALLOW-status, operationally-shaped
        (PROCEDURE_STEPS) contract, then answers with raw, command-bearing
        free-form text -- `troubleshooting_guidance` is never populated at
        all, exactly this milestone's own item-2 backstop scenario."""

        _step: list[int] = PrivateAttr(default_factory=lambda: [0])

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="scripted-livecorr3b-outer-model", **kwargs)

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            step = self._step[0]
            self._step[0] += 1
            if step == 0:
                part = types.Part.from_function_call(
                    name="record_request_contract",
                    args={
                        "intent": "procedure",
                        "requested_output": "procedure_steps",
                        "subject": "HW Partial Fault",
                        "missing_context": [],
                    },
                )
                part.function_call.id = "livecorr3b-call-0"
                yield LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)
                return
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=live_defect_text)]), partial=False)

    db_file = tmp_path / "livecorr3b_history.db"
    session_service = ApiSessionService(adk_session_service=DatabaseSessionService(f"sqlite+aiosqlite:///{db_file.as_posix()}"))
    session_id = await session_service.create_session("api-user")
    scripted_team_manager = team_manager.model_copy(update={"model": _OperationalAllowNoGuidanceOuterLlm()})
    runner = Runner(app_name=APP_NAME, agent=scripted_team_manager, session_service=session_service.adk_session_service)
    attachment_service = AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))
    chat_service = ChatService(session_service, runner=runner, attachment_service=attachment_service)

    live_text = None
    async for event in chat_service.execute_turn_events(session_id, "show me the complete approved procedure", "api-user"):
        if event.type.value == "message.completed":
            live_text = event.data["content"]
    assert live_text is not None
    assert live_text != live_defect_text
    assert _RRU_COMMAND not in live_text

    history = await get_session_history(session_service, attachment_service, session_id, "api-user")
    assistant_messages = [m for m in history.messages if m.role == "assistant"]
    assert assistant_messages
    assert assistant_messages[-1].text == live_text
