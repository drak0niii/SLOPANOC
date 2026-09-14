"""LIVE-CORR-1 -- Controlled Diagnostic Milestone regression fixtures.

Originally an evidence-and-defect-registration pass only (per its own
explicit instruction: "Do not fix production/runtime behavior in this
pass"), where every test below was `xfail(strict=True)`, asserting the
CORRECT invariant the live Cloud SQL evidence (sessions `32c5a4a5-...`,
`abe35bdc-...`, `38dbd231-...`, `a6bbf7cf-...`) proved was violated, or a
plain PASSING confirmatory test narrowing a live discrepancy to outside
one pure function's own scope (DEF-0041).

LIVE-CORR-2 (Request & Context Policy Correction) subsequently
implemented DEF-0037, DEF-0038 (bounded interim), DEF-0039, and DEF-0043
-- their `xfail` markers were REMOVED here (never left as a silent
strict XPASS) once each behavior was genuinely fixed; see each test's own
updated docstring for exactly what changed. DEF-0041 stays the same
passing confirmatory test it always was (DEF-0041 itself remains OPEN --
this test only proves the PURE grounding function's own logic is
correct in isolation; the live runtime discrepancy was never isolated by
either LIVE-CORR-2 or LIVE-CORR-3). DEF-0042 remains untouched, out of
scope for both corrective passes.

LIVE-CORR-3 (Operational Guidance & Streaming Safety) subsequently
implemented DEF-0040 and DEF-0044 -- their `xfail` markers were REMOVED
here too, for the same reason. See `backend/tests/test_
livecorr2_request_context_policy_correction.py` and `backend/tests/
test_livecorr3_operational_guidance_and_streaming_safety.py` for each
pass's own, more thorough test matrix -- not duplicated here.

See `docs/DEFECT_REGISTER.md` (DEF-0037 through DEF-0044) for the full
root-cause record, live reproduction evidence, and required corrections
-- this file intentionally duplicates none of that narrative, only the
minimal deterministic reproduction each entry references.

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write, no fixture containing real governed document
content (all Knowledge fixtures below are synthetic, mirroring the same
"HW Partial Fault"/"HW Fault" heading-only style already established in
`test_def_0027_final_corrective_pass.py`, never a copy of real corpus
text).
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.agents.incident_manager.evidence import (
    CommandGroundingReason,
    _evaluate_command,
    enforce_procedure_scoped_command_grounding_with_reason,
)
from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode, TroubleshootingStep
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
    RequestExecutionDecision,
    RequestExecutionStatus,
    command_suppression_fallback_text,
    derive_execution_decision,
)
from backend.api.turn_context import bind_run_id, reset_run_id
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

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"


def _evidence_item(section_id: str, heading: str, content: str, knowledge_id: str = "livecorr1-doc") -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, heading=heading, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(
        knowledge_id=knowledge_id, version_label="v1", section_id=section_id, source_system="test", source_id=f"{knowledge_id}-doc"
    )
    return KnowledgeEvidenceItem(
        reference=reference,
        title="LIVE-CORR-1 Fixture Procedure",
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


# =============================================================================
# DEF-0037 -- ordinary conversational INFORMATION requests forced AMBIGUOUS
# =============================================================================


def test_def_0037_information_intent_with_no_subject_is_forced_ambiguous() -> None:
    """FIXED by LIVE-CORR-2 -- previously `xfail(strict=True)`; now
    PASSES against the corrected implementation (`INFORMATION` removed
    from `TARGET_SPECIFIC_INTENTS`; the "no subject" gate now uses the
    two-factor `is_operationally_shaped_request`). Live reproduction:
    session `32c5a4a5-4243-4139-904e-c6b49c54d66a`
    -- user said "hello"; `validated_request_contract` was exactly
    `{"intent": "information", "subject": null, "requested_output":
    "fact", "missing_context": [], "ambiguity": false}`; the model's own
    correct raw answer ("Hello! How can I help you today?") was replaced
    by the operational "I need to know which specific alarm or governed
    procedure you mean..." fallback."""
    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject=None,
        missing_context=[],
        ambiguity=False,
        run_id="def-0037-run",
    )
    decision = derive_execution_decision(contract, "def-0037-run")
    assert decision.status == RequestExecutionStatus.ALLOW


# =============================================================================
# DEF-0038 -- required_target_parameter_gaps is non-monotonic
# =============================================================================


def test_def_0038_less_context_never_grants_more_permission() -> None:
    """PARTIALLY FIXED by LIVE-CORR-2 (bounded interim foundation, see
    `docs/DEFECT_REGISTER.md`'s own DEF-0038 entry -- full step-aware
    precision remains a later milestone's scope) -- previously
    `xfail(strict=True)`; now PASSES. Direct, deterministic proof against
    the corrected implementation: supplying NO context at all about the
    operational target now returns a gap set that is AT LEAST as
    restrictive as supplying a PARTIAL identifier-bearing `unit_type`
    (but no `unit_id`) -- the safety gate can no longer be satisfied more
    easily by providing less information than by providing part of it."""
    gaps_with_no_context = required_target_parameter_gaps(RequestIntent.PROCEDURE, RequestedOutput.EXACT_COMMAND, [])
    gaps_with_partial_context = required_target_parameter_gaps(
        RequestIntent.PROCEDURE,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
    )
    # The correct, monotonic invariant: less context must never be LESS
    # restrictive than partial context.
    assert set(gaps_with_no_context) >= set(gaps_with_partial_context)


# =============================================================================
# DEF-0039 -- stale Knowledge source chips survive the KNOWLEDGE_INVENTORY
# unsupported-capability override
# =============================================================================


@pytest.mark.asyncio
async def test_def_0039_knowledge_inventory_override_clears_stale_sources() -> None:
    """FIXED by LIVE-CORR-2 -- previously `xfail(strict=True)`; now
    PASSES against the corrected implementation (`chat_service.py` now
    clears `selected_knowledge_evidence` in the same place `final_text`
    is overridden for `UNSUPPORTED_CAPABILITY`). Live reproduction:
    session `abe35bdc-e974-4fc7-a287-6733fd736faf`
    -- a KNOWLEDGE_INVENTORY-classified request correctly overrode
    `final_text` to the deterministic "I don't yet have a way to
    enumerate..." message, but the SAME turn's persisted
    `knowledge_sources` still carried 5 real, distinct selected evidence
    items from a real `knowledge_search`/`knowledge_select_evidence` call
    made before the override fired."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    item = _evidence_item("s-inventory", "Aurora Relay Verification", "Some governed content unrelated to this test's assertion.")

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        assert run_id is not None
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
        FakeEvent(text="Here are the documents I have: Aurora Relay Verification, Document1, Rogers 4G, Rogers 4G5G.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "What MOPs or governed Knowledge documents do you currently have available?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert "knowledge_sources" not in completed.data


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


# =============================================================================
# DEF-0040 -- an embedded command in TroubleshootingStep.action bypasses
# grounding, which only ever examines the separate `command` field
# =============================================================================


def test_def_0040_embedded_command_bypasses_grounding() -> None:
    """FIXED by LIVE-CORR-3, RE-FIXED STRUCTURALLY by LIVE-CORR-3A --
    previously `xfail(strict=True)`; now PASSES against the corrected
    implementation. LIVE-CORR-3's own substring-scanning `_detect_
    embedded_operational_content` was REMOVED outright by LIVE-CORR-3A
    per explicit instruction ("operational safety must come from typed
    structure... do not replace it with regex, keyword matching or
    free-text command detection") -- the SAME live defect is now closed
    structurally instead: a step with NO explicit `operational_effect`
    defaults to the strictest interpretation (`STATE_CHANGE_
    RECOMMENDATION`, see `TroubleshootingOperationalEffect`'s own
    docstring), and `enforce_structural_operational_integrity` rejects any
    such step that carries no `command` -- regardless of what its own
    free-text `action` says. Live reproduction: session
    `38dbd231-a704-4734-9488-80db882a5b7e` (Scenario 4) --
    `full_procedure_steps[8].action` carried both the real RRU and AAS
    restart commands with NO separate `command` value at all."""
    run_id = "def-0040-run"
    token = bind_run_id(run_id)
    try:
        _select(
            run_id,
            _evidence_item(
                "s-hwpf",
                "HW Partial Fault",
                f"RRU:\n{_RRU_COMMAND}\n\nSupportUnit=---:\nNo restart\n",
            ),
        )
        guidance = TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
            full_procedure_steps=[TroubleshootingStep(action=f"Restart the identified unit using: {_RRU_COMMAND}", command=None)],
        )
        corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, question="HW Partial Fault")
    finally:
        reset_run_id(token)

    assert stripped is True
    assert reason == CommandGroundingReason.UNSTRUCTURED_STATE_CHANGE
    assert corrected.full_procedure_steps == []
    assert _RRU_COMMAND not in (corrected.interpretation or "")


# =============================================================================
# DEF-0041 -- confirmatory: the PURE grounding function correctly rejects
# a cross-section command given the real live question/heading shape
# =============================================================================


def test_def_0041_hget_command_should_fail_grounding_against_real_content() -> None:
    """PASSES today -- proves `_evaluate_command`/active-section
    resolution is CORRECT in isolation for the exact live shape (real
    question text, real section headings) reproduced from session
    `38dbd231-a704-4734-9488-80db882a5b7e` (Scenario 4): the command
    "hget near Rfportref" is real content of the merely-SUPPORTING
    sibling section ("HW Fault"), never the ACTIVE "HW Partial Fault"
    section. This narrows DEF-0041's own live discrepancy to somewhere
    OUTSIDE this function -- the live turn nonetheless surfaced the
    command unstripped, which this test does not explain (see the
    defect register's own explicit "root cause: UNRESOLVED" note)."""
    section_texts_by_id = {
        "section-0001": "RRU:\nRestart the identified unit per this section's own procedure.\n\nSupportUnit=---:\nNo restart\n",
        "section-0002": "For Antenna Group / Unit alarms, execute: hget near Rfportref\n",
    }
    headings_by_id = {"section-0001": "HW Partial Fault", "section-0002": "HW Fault"}
    question = "How do I troubleshoot HW Partial Fault using the approved procedure?"

    grounded, reason = _evaluate_command("hget near Rfportref", section_texts_by_id, headings_by_id, question)

    assert grounded is False
    assert reason == CommandGroundingReason.TRUE_ABSENCE


# =============================================================================
# DEF-0043 -- AMBIGUOUS-status generic fallback discards known missing_context
# =============================================================================


def test_def_0043_ambiguous_status_discards_known_missing_context() -> None:
    """FIXED by LIVE-CORR-2 -- previously `xfail(strict=True)`; now
    PASSES against the corrected implementation (`command_suppression_
    fallback_text` now checks `decision.missing_context` regardless of
    `status`). Live reproduction: session `a6bbf7cf-258a-4ba1-a0b7-75c80fd94644`
    (Scenario 6) -- `validated_request_contract` already, specifically
    declared `missing_context: ["equipment identifier", "missing
    condition"]`, yet the final answer was the fully generic "An exact
    command cannot yet be safely provided for this step. Please confirm
    the missing details," never naming either known gap."""
    decision = RequestExecutionDecision(
        status=RequestExecutionStatus.AMBIGUOUS,
        subject="HW Partial Fault procedure",
        missing_context=["equipment identifier", "missing condition"],
        ambiguity=True,
    )
    text = command_suppression_fallback_text(decision)
    assert "equipment identifier" in text
    assert "missing condition" in text


# =============================================================================
# DEF-0044 -- raw, uncorrected model text streams live via MESSAGE_DELTA
# before completion-boundary correction runs
# =============================================================================


@pytest.mark.asyncio
async def test_def_0044_message_delta_exposes_pre_correction_text() -> None:
    """FIXED by LIVE-CORR-3 -- previously `xfail(strict=True)`; now PASSES
    against the corrected implementation (`chat_service.py`'s DEF-0044
    corrective pass: `message.delta` never carries raw model/specialist
    text at all -- see that module's own DEF-0044 comment above
    `run_config`). Drives a real `ChatService.execute_turn_events` turn
    (scripted events, no Gemini) whose raw, streamed text will be
    deterministically replaced by `requires_unstructured_response_
    backstop`'s own completion-boundary correction -- the SAME class of
    correction every scenario in this diagnostic pass relies on. Proves
    the accumulated `message.delta` text never contains the raw command,
    even though `RunConfig(streaming_mode=StreamingMode.SSE)` remains
    active and the Runner still yields real incremental partial events."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    raw_text = f"Use the command: {_RRU_COMMAND}"

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.PROCEDURE,
            requested_output=RequestedOutput.PROCEDURE_STEPS,
            subject="HW Partial Fault",
            missing_context=["unit_id"],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        # troubleshooting_guidance is deliberately never registered, so
        # requires_unstructured_response_backstop fires at completion.

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="Use the command: ", partial=True, final=False),
        FakeEvent(text=_RRU_COMMAND, partial=True, final=False),
        FakeEvent(text=raw_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    streamed_delta_text = ""
    completed = None
    async for event in chat_service.execute_turn_events(session_id, "how do i handle HW Partial Fault?", "api-user"):
        if event.type.value == "message.delta":
            streamed_delta_text += event.data.get("text", "")
        if event.type.value == "message.completed":
            completed = event

    assert completed is not None
    assert completed.data["content"] != raw_text  # the completion-boundary correction did run
    assert _RRU_COMMAND not in streamed_delta_text  # but the raw command was never streamed live either
