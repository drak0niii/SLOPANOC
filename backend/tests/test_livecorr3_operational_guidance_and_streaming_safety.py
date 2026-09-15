"""LIVE-CORR-3 / LIVE-CORR-3A -- Operational Guidance & Streaming Safety
/ Surgical Operational-Safety Hardening.

Implements DEF-0040 (operational guidance bypassing structured, step-aware
safety) and DEF-0044 (unvalidated model output reaching the UI via
progressive streaming), plus narrowly-scoped instrumentation for DEF-0041.
Does NOT implement DEF-0042/6A.18 (Knowledge catalog) or 6A.15.

LIVE-CORR-3A superseded LIVE-CORR-3's own DEF-0040 mechanism outright, per
explicit instruction ("operational safety must come from typed structure...
do not replace it with regex, keyword matching or free-text command
detection"): the substring-scanning `_detect_embedded_operational_content`
was REMOVED; `TroubleshootingGuidance`/`TroubleshootingStep` gained a new
typed `operational_effect` field (`TroubleshootingOperationalEffect`),
and TWO deterministic mechanisms now use it:

  1. STRUCTURAL INTEGRITY (`evidence.py`'s `enforce_structural_
     operational_integrity`) -- a step/guidance classified (or defaulting
     to, when unset) `STATE_CHANGE_RECOMMENDATION` with no real `command`
     is a structural integrity failure -- whole-guidance suppressed. Its
     own direct proof lives in `backend/tests/test_livecorr1_diagnostics
     .py::test_def_0040_embedded_command_bypasses_grounding` (kept
     passing, updated for the new reason name) -- not duplicated here.

  2. RESPONSE-MODE COMPATIBILITY (`request_execution_policy.py`'s
     `enforce_response_mode_compatibility`, now taking the FULL
     `RequestExecutionDecision`, not merely `requested_output`) -- the
     COMPLETE 4-field FULL_PROCEDURE matrix (`intent=PROCEDURE`,
     `requested_output=PROCEDURE_STEPS`, `ambiguity=False`, `subject`
     resolved), plus the NEXT_STEP/EXACT_COMMAND rows. THIS is the focus
     of the "Pure unit tests" section below.

Step-aware target safety (DEF-0038, task item 4) is tested here too:
`enforce_execution_decision_on_guidance`'s new per-step `DIAGNOSTIC_READ`
exemption, allowing a proven, grounded read-only command through even
when target confirmation is otherwise unresolved -- `STATE_CHANGE_
RECOMMENDATION` never receives this exemption.

DEF-0044 (buffered, never-raw-streamed assistant text) is tested in
`backend/tests/test_chat_service_streaming.py` (rewritten) and `backend/
tests/test_p5_1j_governed_completion_gate.py`'s own Part 21 family
(updated) -- both already exercise the corrected invariant directly.

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingOperationalEffect,
    TroubleshootingStep,
)
from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    RequestContract,
    RequestIntent,
    RequestedOutput,
)
from backend.agents.team_manager.request_execution_policy import (
    FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT,
    RequestExecutionDecision,
    RequestExecutionStatus,
    enforce_execution_decision_on_guidance,
    enforce_response_mode_compatibility,
)
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_AAS_COMMAND = "accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"
_READ_COMMAND = "hget near Rfportref"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


def _full_procedure_guidance() -> TroubleshootingGuidance:
    return TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        interpretation="This procedure has two branches.",
        full_procedure_steps=[
            TroubleshootingStep(action="If the unit is an RRU, restart it.", command=_RRU_COMMAND, operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION),
            TroubleshootingStep(action="If the unit is an AAS, restart it.", command=_AAS_COMMAND, operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION),
        ],
    )


def _next_step_guidance(command: str = _RRU_COMMAND) -> TroubleshootingGuidance:
    return TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Confirm the unit type first.",
        next_action="Identify the affected unit.",
        command=command,
        evidence_requested="Report the unit type.",
        operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
    )


def _decision(
    *,
    status: str = RequestExecutionStatus.ALLOW,
    intent: str = RequestIntent.TROUBLESHOOTING,
    requested_output: str = RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
    subject: str = "HW Partial Fault",
    ambiguity: bool = False,
    may_emit_command: bool = True,
) -> RequestExecutionDecision:
    return RequestExecutionDecision(
        status=status,
        intent=intent,
        requested_output=requested_output,
        subject=subject,
        ambiguity=ambiguity,
        may_emit_command=may_emit_command,
    )


# =============================================================================
# Pure unit tests -- enforce_response_mode_compatibility (complete matrix)
# =============================================================================


def test_full_procedure_permitted_for_complete_procedure_steps_contract() -> None:
    """All FOUR required fields present and correct."""
    guidance = _full_procedure_guidance()
    decision = _decision(intent=RequestIntent.PROCEDURE, requested_output=RequestedOutput.PROCEDURE_STEPS, subject="HW Partial Fault", ambiguity=False)
    result, discarded = enforce_response_mode_compatibility(guidance, decision)
    assert discarded is False
    assert result is guidance


@pytest.mark.parametrize(
    "overrides",
    [
        {"intent": RequestIntent.TROUBLESHOOTING},  # wrong intent
        {"requested_output": RequestedOutput.TROUBLESHOOTING_NEXT_STEP},  # wrong output
        {"ambiguity": True},  # ambiguous
        {"subject": None},  # unresolved subject
    ],
)
def test_full_procedure_discarded_when_any_one_of_the_four_fields_fails(overrides: dict) -> None:
    """Section 3's own explicit requirement: FULL_PROCEDURE requires ALL
    FOUR fields, not a subset -- each parametrized case breaks exactly one
    field while the other three remain valid."""
    guidance = _full_procedure_guidance()
    base = dict(intent=RequestIntent.PROCEDURE, requested_output=RequestedOutput.PROCEDURE_STEPS, subject="HW Partial Fault", ambiguity=False)
    base.update(overrides)
    decision = _decision(**base)
    result, discarded = enforce_response_mode_compatibility(guidance, decision)
    assert discarded is True
    assert result is None


def test_full_procedure_discarded_for_troubleshooting_next_step_contract() -> None:
    """THE direct DEF-0040 proof: "how do I troubleshoot X, using the
    approved procedure" validates to TROUBLESHOOTING_NEXT_STEP -- a
    FULL_PROCEDURE-shaped response (both RRU and AAS commands together)
    must never render for it."""
    guidance = _full_procedure_guidance()
    decision = _decision(intent=RequestIntent.TROUBLESHOOTING, requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP)
    result, discarded = enforce_response_mode_compatibility(guidance, decision)
    assert discarded is True
    assert result is None


def test_full_procedure_discarded_for_exact_command_contract() -> None:
    guidance = _full_procedure_guidance()
    decision = _decision(intent=RequestIntent.COMMAND, requested_output=RequestedOutput.EXACT_COMMAND)
    result, discarded = enforce_response_mode_compatibility(guidance, decision)
    assert discarded is True
    assert result is None


def test_full_procedure_discarded_for_unresolved_contract() -> None:
    """A missing/stale contract (`INVALID_CONTRACT`, every field unset)
    satisfies none of the three permission functions -- fails closed
    identically to any other incompatible combination."""
    guidance = _full_procedure_guidance()
    decision = RequestExecutionDecision(status=RequestExecutionStatus.INVALID_CONTRACT, may_emit_command=False)
    result, discarded = enforce_response_mode_compatibility(guidance, decision)
    assert discarded is True
    assert result is None


def test_next_step_permitted_for_troubleshooting_next_step_contract() -> None:
    guidance = _next_step_guidance()
    decision = _decision(intent=RequestIntent.TROUBLESHOOTING, requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP)
    result, discarded = enforce_response_mode_compatibility(guidance, decision)
    assert discarded is False
    assert result is guidance


def test_next_step_permitted_for_exact_command_contract() -> None:
    """LIVE-CORR-3A's own documented mapping decision: `EXACT_COMMAND` has
    no distinct `TroubleshootingInteractionMode` value -- a `NEXT_STEP`
    -shaped response (at most one action + command) satisfies it too."""
    guidance = _next_step_guidance()
    decision = _decision(intent=RequestIntent.COMMAND, requested_output=RequestedOutput.EXACT_COMMAND)
    result, discarded = enforce_response_mode_compatibility(guidance, decision)
    assert discarded is False
    assert result is guidance


def test_next_step_discarded_for_procedure_steps_contract() -> None:
    """STRICT per explicit instruction: unlike LIVE-CORR-3's own looser
    first pass, `NEXT_STEP` is now ALSO discarded when the validated
    contract does not match either of its own two permitted rows --
    "incompatible combinations fail closed" applies to the whole matrix."""
    guidance = _next_step_guidance()
    decision = _decision(intent=RequestIntent.PROCEDURE, requested_output=RequestedOutput.PROCEDURE_STEPS, ambiguity=False)
    result, discarded = enforce_response_mode_compatibility(guidance, decision)
    assert discarded is True
    assert result is None


def test_next_step_discarded_for_information_fact_contract() -> None:
    """Operational content must never escape through an unvalidated
    presentation-only request shape."""
    guidance = _next_step_guidance()
    decision = _decision(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT)
    result, discarded = enforce_response_mode_compatibility(guidance, decision)
    assert discarded is True
    assert result is None


def test_none_guidance_is_a_complete_no_op() -> None:
    decision = _decision(intent=RequestIntent.PROCEDURE, requested_output=RequestedOutput.PROCEDURE_STEPS)
    result, discarded = enforce_response_mode_compatibility(None, decision)
    assert result is None
    assert discarded is False


# =============================================================================
# Pure unit tests -- step-aware target safety (DEF-0038 item 4)
# =============================================================================


def test_diagnostic_read_no_longer_bypasses_target_confirmation() -> None:
    """LIVE-CORR-3B -- Operational Authority Boundary: the LIVE-CORR-3A
    DIAGNOSTIC_READ target-independent exemption has been REMOVED outright
    (a model-populated `operational_effect` must never grant permission or
    relax target/context requirements). A proven, grounded, DIAGNOSTIC_
    READ-classified command is now withheld exactly like any other,
    whenever `may_emit_command=False`."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Look up the port reference for the affected antenna group.",
        next_action="Run the lookup.",
        command=_READ_COMMAND,
        evidence_requested="Report the output.",
        operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
    )
    decision = _decision(may_emit_command=False, status=RequestExecutionStatus.NEEDS_INFORMATION)
    corrected, suppressed = enforce_execution_decision_on_guidance(guidance, decision)
    assert suppressed is True
    assert corrected.command is None


def test_state_change_recommendation_is_withheld_without_target_confirmation() -> None:
    """A `STATE_CHANGE_RECOMMENDATION`-classified command NEVER receives
    the DIAGNOSTIC_READ exemption -- withheld exactly like before this
    pass whenever target confirmation is unresolved."""
    guidance = _next_step_guidance(_RRU_COMMAND)  # operational_effect=STATE_CHANGE_RECOMMENDATION
    decision = _decision(may_emit_command=False, status=RequestExecutionStatus.NEEDS_INFORMATION)
    corrected, suppressed = enforce_execution_decision_on_guidance(guidance, decision)
    assert suppressed is True
    assert corrected.command is None
    assert corrected.next_action is None
    assert corrected.interpretation is None


def test_unclassified_command_defaults_to_the_strict_state_change_treatment() -> None:
    """Unset `operational_effect` never grants the permissive DIAGNOSTIC_
    READ exemption -- it is treated as `STATE_CHANGE_RECOMMENDATION`."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        next_action="Do the next thing.",
        command=_RRU_COMMAND,
        operational_effect=None,
    )
    decision = _decision(may_emit_command=False, status=RequestExecutionStatus.NEEDS_INFORMATION)
    corrected, suppressed = enforce_execution_decision_on_guidance(guidance, decision)
    assert suppressed is True
    assert corrected.command is None


def test_full_procedure_mixed_steps_diagnostic_read_no_longer_survives() -> None:
    """LIVE-CORR-3B: the removed DIAGNOSTIC_READ exemption applied at
    per-step granularity too -- a DIAGNOSTIC_READ step's command is now
    stripped exactly like a STATE_CHANGE_RECOMMENDATION step's, whenever
    target confirmation is unresolved."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        interpretation="Two steps.",
        full_procedure_steps=[
            TroubleshootingStep(action="Look up the port reference.", command=_READ_COMMAND, operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ),
            TroubleshootingStep(action="Restart the unit.", command=_RRU_COMMAND, operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION),
        ],
    )
    decision = _decision(may_emit_command=False, status=RequestExecutionStatus.NEEDS_INFORMATION)
    corrected, suppressed = enforce_execution_decision_on_guidance(guidance, decision)
    assert suppressed is True
    assert corrected.full_procedure_steps[0].command is None
    assert corrected.full_procedure_steps[1].command is None


def test_reference_description_carries_no_operational_authority() -> None:
    """A REFERENCE_DESCRIPTION-classified entry with no command is
    unaffected by either the structural-integrity rule or the target
    gate -- it is descriptive content, never an operational
    recommendation, and renders as-is."""
    from backend.agents.incident_manager.evidence import enforce_structural_operational_integrity

    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="The approved procedure documents both an RRU and an AAS restart branch.",
        next_action="Review the procedure's own documented branches.",
        command=None,
        operational_effect=TroubleshootingOperationalEffect.REFERENCE_DESCRIPTION,
    )
    corrected, stripped, reason = enforce_structural_operational_integrity(guidance)
    assert stripped is False
    assert reason is None
    assert corrected is guidance


# =============================================================================
# Full chat_service.py integration -- the exact live scenario shapes
# =============================================================================


@pytest.mark.asyncio
async def test_integration_full_procedure_dump_narrowed_for_next_step_request() -> None:
    """Reproduces the exact confirmed live defect shape end to end: a
    validated TROUBLESHOOTING + TROUBLESHOOTING_NEXT_STEP contract, but
    incident_manager populated FULL_PROCEDURE guidance carrying BOTH the
    real RRU and AAS commands together. Neither command may reach the
    user; the deterministic fallback text is used instead."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="HW Partial Fault",
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(run_id, _full_procedure_guidance())

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "How do I troubleshoot HW Partial Fault using the approved procedure?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert _RRU_COMMAND not in completed.data["content"]
    assert _AAS_COMMAND not in completed.data["content"]
    assert completed.data["content"] == FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT


@pytest.mark.asyncio
async def test_integration_full_procedure_request_no_longer_shows_raw_commands() -> None:
    """LIVE-CORR-3B -- Operational Authority Boundary, item 1: "show me
    the complete approved procedure" validates to PROCEDURE + PROCEDURE_
    STEPS -- FULL_PROCEDURE guidance still renders (interaction-mode
    compatibility is unaffected), but PROCEDURE_STEPS output no longer
    implicitly grants exact-command permission, so neither real command is
    shown; only EXACT_COMMAND requests may. Supersedes this test's own
    prior non-regression claim (both commands previously shown), which
    encoded exactly the fail-open path this milestone closes."""
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
        section_id="s-hwpf", knowledge_id="doc1", sequence=0, heading="HW Partial Fault",
        content=f"RRU:\n{_RRU_COMMAND}\n\nAAS:\n{_AAS_COMMAND}\n",
    )
    source = KnowledgeSource(source_system="test", source_id="doc1-doc")
    reference = KnowledgeEvidenceReference(knowledge_id="doc1", version_label="v1", section_id="s-hwpf", source_system="test", source_id="doc1-doc")
    item = KnowledgeEvidenceItem(reference=reference, title="Fixture", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        rt.get_or_init_run_state(run_id)
        execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=[item]))
        rt.record_search_result(run_id, execution)
        rt.select_evidence(run_id, [KnowledgeEvidenceSelectionKey(knowledge_id="doc1", version_label="v1", section_id="s-hwpf")])
        contract = RequestContract(
            intent=RequestIntent.PROCEDURE,
            requested_output=RequestedOutput.PROCEDURE_STEPS,
            subject="HW Partial Fault",
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(run_id, _full_procedure_guidance())

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "Show me the complete approved procedure for HW Partial Fault.", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert _RRU_COMMAND not in completed.data["content"]
    assert _AAS_COMMAND not in completed.data["content"]


@pytest.mark.asyncio
async def test_integration_exact_command_request_also_narrowed() -> None:
    """"Give me the first approved command for that procedure" validates
    to COMMAND + EXACT_COMMAND -- a FULL_PROCEDURE-shaped response must
    be discarded for this contract too, not only TROUBLESHOOTING_NEXT_
    STEP. Canonical equality itself (live == persisted canonical ==
    refreshed history) is already proven generically, for ANY corrected
    `final_text`, by the untouched 6A.14A suite (`test_p6a14a_canonical_
    turn_result.py`) -- this DEF-0040 fallback text flows through that
    SAME, unmodified canonical-persistence pathway, so it is not
    re-proven here."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="HW Partial Fault",
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(run_id, _full_procedure_guidance())

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "Give me the first approved command for that procedure.", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] == FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT
    assert _RRU_COMMAND not in completed.data["content"]
    assert _AAS_COMMAND not in completed.data["content"]


# =============================================================================
# LIVE-CORR-3A -- required test list (paraphrase bypass, no-raw-SSE-text,
# canonical live/history equality)
# =============================================================================


def test_paraphrased_state_changing_prose_cannot_bypass_typed_validation() -> None:
    """A model that classifies a step `STATE_CHANGE_RECOMMENDATION` but
    tries to convey the actual instruction as PARAPHRASED free text
    (never the exact governed command, so the OLD substring-scan
    mechanism would not even have matched it) is rejected purely because
    `command` is unpopulated -- the structural rule never inspects the
    free text's own wording/content at all, so paraphrase quality is
    irrelevant to the outcome."""
    from backend.agents.incident_manager.evidence import enforce_structural_operational_integrity

    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="The unit needs to be power-cycled to clear the fault condition.",
        next_action="Perform a restart of the affected radio unit using the standard field-replaceable-unit procedure.",
        command=None,
        operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
    )
    corrected, stripped, reason = enforce_structural_operational_integrity(guidance)
    assert stripped is True
    assert reason.value == "unstructured_state_change"
    assert corrected.next_action is None
    assert corrected.interpretation is None


@pytest.mark.asyncio
async def test_raw_full_procedure_text_never_appears_in_any_sse_event_before_completion() -> None:
    """Combined DEF-0040 + DEF-0044 proof: even though the Runner still
    yields real incremental partial events containing the (about-to-be-
    discarded) raw text, NEITHER the raw model text NOR the discarded
    real commands ever appear in any `message.delta` event -- only the
    final, narrowed, canonical text reaches the user, exactly once, via
    `message.completed`."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.streaming_events import StreamEventType
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    raw_text = f"To handle HW Partial Fault: RRU -> {_RRU_COMMAND}; AAS -> {_AAS_COMMAND}."

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="HW Partial Fault",
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(run_id, _full_procedure_guidance())

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text=raw_text, final=False, partial=True),
        FakeEvent(text=raw_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    all_events = []
    async for event in chat_service.execute_turn_events(session_id, "How do I troubleshoot HW Partial Fault using the approved procedure?", "api-user"):
        all_events.append(event)

    for event in all_events:
        if event.type != StreamEventType.MESSAGE_COMPLETED:
            assert _RRU_COMMAND not in str(event.data)
            assert _AAS_COMMAND not in str(event.data)
        assert event.type != StreamEventType.MESSAGE_DELTA or event.data.get("text") in (None, "")

    completed = next(e for e in all_events if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT


@pytest.mark.asyncio
async def test_canonical_live_and_history_response_remain_identical_after_narrowing() -> None:
    """Section 17's own "canonical equality" requirement, proven directly
    (not merely inferred from the generic 6A.14A suite) for a DEF-0040-
    narrowed response: live `message.completed` and refreshed history
    projection carry the exact same, already-corrected text."""
    from backend.api.chat_service import ChatService
    from backend.api.session_history_service import get_session_history
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id
    from backend.attachments.repository import AttachmentRepository
    from backend.attachments.service import AttachmentService

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="HW Partial Fault",
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(run_id, _full_procedure_guidance())

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    attachment_service = AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events), attachment_service=attachment_service)

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "How do I troubleshoot HW Partial Fault using the approved procedure?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    live_text = completed.data["content"]
    assert live_text == FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT

    history = await get_session_history(service, attachment_service, session_id, "api-user")
    assistant_messages = [m for m in history.messages if m.role == "assistant"]
    assert assistant_messages
    assert assistant_messages[-1].text == live_text
    assert _RRU_COMMAND not in assistant_messages[-1].text
    assert _AAS_COMMAND not in assistant_messages[-1].text
