"""LIVE-CORR-13.1 -- Close Empty Post-Policy Guidance Branches.

REAL LIVE RETEST AFTER LIVE-CORR-13 PROVED GENERAL_CONVERSATION fixed, but
TWO shapes still failed:

LIVE RESULT B -- EXACT_COMMAND still fails:

    "give me a command to restart an RRU"

Backend correctly computed `status=needs_information may_emit_command=
False`, yet `message_completed_emitted=False error_code=run_failure` --
no `final_response_path=clarification` was ever logged.

LIVE RESULT C -- PROCEDURE still fails:

    "how can i check alarms on ericsson enm ?"

Grounding correctly stripped a `cross_procedure_evidence` command
(`status=allow may_emit_command=False`), yet again `message_completed_
emitted=False error_code=run_failure` -- no safe remaining response ever
became `final_text`.

PROVEN ROOT CAUSE (confirmed by direct reproduction against the real
`ChatService` pipeline, not inferred): `chat_service.py`'s `if captured_
troubleshooting_guidance is not None:` branch, once entered, ONLY ever set
`final_text` when either `command_suppressed_by_policy` was `True` (via
`enforce_execution_decision_on_guidance`'s own suppression) or `rendered_
guidance_text` was non-empty. When guidance existed as an object but
carried NO renderable content at all -- because evidence.py's OWN upstream
grounding (a `DIAGNOSTIC_READ`/`OBSERVATION` classification whose `command`
was already `None`, or a `cross_procedure_evidence` rejection) had already
emptied it before `enforce_execution_decision_on_guidance` ever ran -- that
function correctly reports `stripped=False` (nothing left for IT to strip),
so `command_suppressed_by_policy` stays `False`, and `rendered_guidance_
text` is empty. LIVE-CORR-6's own documented "else: leave final_text alone,
something upstream already wrote it" branch then silently left `final_text`
untouched -- which is `None` for a turn whose Runner produced no
accompanying free text (LIVE-CORR-13's own proven shape). Because THIS
branch was already entered (`captured_troubleshooting_guidance is not
None`), the SIBLING `elif requires_unstructured_response_backstop(...)`
-- which correctly handles the "no guidance at all" shape -- was
structurally unreachable: branch shadowing, exactly as this milestone's own
instruction predicted.

THE FIX (backend/api/chat_service.py):
  1. `execution_decision.status in (NEEDS_INFORMATION, AMBIGUOUS)` is now
     checked FIRST, before `command_suppressed_by_policy`/`rendered_
     guidance_text` -- a specialist result is DATA, the final decision is
     AUTHORITY, and a genuinely unresolved target/condition must never be
     shadowed by whatever guidance content happens to remain. Produces the
     IDENTICAL text every previously-passing `command_suppressed_by_policy`
     case already produced (that branch's own `rendered_guidance_text` is
     always empty whenever `may_emit_command=False` wiped a `STATE_CHANGE_
     RECOMMENDATION` guidance).
  2. A new, final `else` closes the previously-silent gap: guidance existed
     but rendered nothing under a non-clarification status -- reuses the
     SAME deterministic `render_command_suppression_text` the sibling
     branches already call (never new wording; `ALLOW` with empty
     `missing_context` resolves to the existing, unmodified `_GENERIC_
     WITHHELD_COMMAND_TEXT`).

Neither `RequestContract`, `RequestClass`, `PendingGovernedRequest`,
`WorkEnvelope`, governed retrieval, grounding, `derive_execution_decision`,
`may_emit_command`, `validate_final_output`, nor presentation isolation are
touched by this pass.

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
)
from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    RequestClass,
    RequestContract,
    RequestIntent,
    RequestedOutput,
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

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_AAS_COMMAND = "accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"
_KNOWLEDGE_ID = "livecorr13-1-doc"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


def _no_final_text_event() -> FakeEvent:
    """The model's real, ordinary behavior after delegating to a
    specialist and letting a structured tool call carry the result."""
    return FakeEvent(text=None, final=True)


def _evidence_item(section_id: str, heading: str, content: str) -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=_KNOWLEDGE_ID, sequence=0, heading=heading, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{_KNOWLEDGE_ID}-doc")
    reference = KnowledgeEvidenceReference(
        knowledge_id=_KNOWLEDGE_ID, version_label="v1", section_id=section_id, source_system="test", source_id=f"{_KNOWLEDGE_ID}-doc"
    )
    return KnowledgeEvidenceItem(
        reference=reference, title=_KNOWLEDGE_ID, document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section
    )


def _select(run_id: str, *items: KnowledgeEvidenceItem) -> None:
    rt.get_or_init_run_state(run_id)
    execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=list(items)))
    rt.record_search_result(run_id, execution)
    keys = [
        KnowledgeEvidenceSelectionKey(knowledge_id=i.reference.knowledge_id, version_label=i.reference.version_label, section_id=i.reference.section_id)
        for i in items
    ]
    rt.select_evidence(run_id, keys)


async def _run_and_collect_completed(chat_service: Any, session_id: str, message: str) -> Optional[dict]:
    completed = None
    async for event in chat_service.execute_turn_events(session_id, message, "api-user"):
        if event.type.value == "message.completed":
            completed = event.data
    return completed


# =============================================================================
# TEST A -- captured guidance exists (but fully empty, exactly the proven
# live shape) + status=NEEDS_INFORMATION -> clarification wins.
# =============================================================================


@pytest.mark.asyncio
async def test_a_needs_information_wins_over_empty_captured_guidance() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, _evidence_item("s1", "RRU Restart", "RRU Restart procedure content."))
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            missing_context=["unit_id", "unit_type"],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        # The proven live shape: a real TroubleshootingGuidance OBJECT
        # exists, but carries nothing renderable -- evidence.py's own
        # upstream grounding already emptied it (no command could be
        # safely proposed without the missing unit info).
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

    completed = await _run_and_collect_completed(chat_service, session_id, "give me a command to restart an RRU")

    assert completed is not None
    content = completed["content"]
    assert content
    assert "unit" in content.lower()
    assert _RRU_COMMAND not in content


@pytest.mark.asyncio
async def test_a2_needs_information_wins_even_when_guidance_carries_a_command() -> None:
    """Non-regression companion to Test A: the ALREADY-correct shape (a
    populated `command`, defaulting to `STATE_CHANGE_RECOMMENDATION`) must
    keep working identically through the new top-priority status check."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            missing_context=["unit_id", "unit_type"],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                next_action="Restart the RRU.",
                command=_RRU_COMMAND,
            ),
        )

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        _no_final_text_event(),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = await _run_and_collect_completed(chat_service, session_id, "give me a command to restart an RRU")

    assert completed is not None
    content = completed["content"]
    assert content
    assert "unit" in content.lower()
    assert _RRU_COMMAND not in content


# =============================================================================
# TEST B -- captured guidance exists, status=ALLOW, command already
# stripped for cross_procedure_evidence, nothing left to render -> safe
# policy/grounding fallback, never final_text=None.
# =============================================================================


@pytest.mark.asyncio
async def test_b_allow_empty_guidance_after_cross_procedure_evidence_gets_safe_fallback() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(
            run_id,
            _evidence_item("s1", "ENM Alarm List A", "Open the ENM Alarm List application to view alarms."),
            _evidence_item("s2", "ENM Alarm List B", "Open the ENM Alarm List application to view alarms."),
        )
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="check alarms on Ericsson ENM",
            missing_context=[],
            request_class=RequestClass.PROCEDURE_TROUBLESHOOTING,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        # Mirrors cross_procedure_evidence: evidence.py's own upstream
        # grounding already emptied the ENTIRE guidance (not merely
        # `command`) because it could not be safely attributed to one
        # procedure across the >1 selected candidates.
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

    completed = await _run_and_collect_completed(chat_service, session_id, "how can i check alarms on ericsson enm ?")

    assert completed is not None
    content = completed["content"]
    assert content
    assert _RRU_COMMAND not in content
    assert _AAS_COMMAND not in content


# =============================================================================
# TEST C -- captured guidance exists, status=ALLOW, SAFE non-command
# guidance remains -> normal guidance response, no unnecessary fallback.
# =============================================================================


@pytest.mark.asyncio
async def test_c_allow_with_safe_remaining_guidance_renders_normally_not_the_fallback() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, _evidence_item("s1", "ENM Alarm List", "Open the ENM Alarm List application to view alarms."))
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="check alarms on Ericsson ENM",
            missing_context=[],
            request_class=RequestClass.PROCEDURE_TROUBLESHOOTING,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
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

    completed = await _run_and_collect_completed(chat_service, session_id, "how can i check alarms on ericsson enm ?")

    assert completed is not None
    content = completed["content"]
    assert "Open the ENM Alarm List application and review current alarms." in content
    assert "An exact command cannot yet be safely provided" not in content
    assert "confirm the following before I can provide an exact command" not in content


# =============================================================================
# TEST D -- GENERAL_CONVERSATION: existing LIVE-CORR-13 behavior unchanged.
# =============================================================================


@pytest.mark.asyncio
async def test_d_general_conversation_unaffected() -> None:
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
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="Hello there! How can I help you today?", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = await _run_and_collect_completed(chat_service, session_id, "hello")

    assert completed is not None
    assert completed["content"] == "Hello there! How can I help you today?"
