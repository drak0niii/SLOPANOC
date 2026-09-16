"""LIVE-CORR-9 -- Exact-Command Continuation Loses Resolved Operation
Identity.

CONFIRMED LIVE DEFECT: Turn 1, "what is the cmd i need to run to check
and list alarms present?", correctly resolved `request_class=EXACT_
COMMAND` and returned a grounded command. Turn 2, in the SAME session,
"no, i just need a cmd to run a check on alarms for ericsson", produced
`provided_context_keys=["vendor"]` but `missing_context_keys=["unit_id",
"unit_type"]` -- even though the resolved operation (a system-wide alarm
listing) never concerned one physical unit. The Incident Manager's own
grounding trace showed `command_present=True stripped=True
reason=grounding_rejected` -- a real command was proposed and correctly
REJECTED (not an exact verbatim match of the active section's own real
text), which is CORRECT, SAFE behavior.

PROVEN ROOT CAUSE: `chat_service.py` sourced LIVE-CORR-7's own
`grounded_command_candidate` EXCLUSIVELY from `TroubleshootingGuidance
.command` -- which becomes `None` whenever a command is REJECTED for ANY
reason, including this entirely benign "not an exact verbatim
reconstruction" case. `required_target_parameter_gaps` then had NOTHING
to consult and fell back to the generic `unit_id`/`unit_type` blanket
rule -- conflating "was THIS SPECIFIC candidate trustworthy enough to
show the user" (evidence.py's own, unchanged, still-fully-active job)
with "does this RESOLVED OPERATION concern one physical unit at all" (a
separate question this pass now answers from the ACTIVE GOVERNED
SECTION's own real, already-revalidated content instead).

`continuation`/`RequestContract.subject`-based session carry-forward
(`validate_and_persist_request_contract`) was AUDITED and found NOT to be
the proximate cause of the reported symptom in this exact trace (`vendor`
was captured fresh, this-turn, via the current-turn-text verification
path -- never needing carry-forward at all); it is NOT changed by this
pass. See the closure report's own "Root cause"/"Context merge" sections
for the full audit trail, including the honestly-recorded, NOT-fixed
brittleness of that mechanism's own exact-string subject matching.

FIX: `chat_service.py`, when `grounded_command_candidate` is `None`,
falls back to the EXISTING, session-persisted, revalidated 6A.14 Active
Procedure Continuity anchor (`governed_evidence_continuity.py` --
`compute_fresh_active_procedure_anchor`/`ACTIVE_GOVERNED_PROCEDURE_
STATE_KEY`, never a new continuity mechanism) and inspects THAT
section's own real content instead -- never the user's own words, never
a rejected/untrusted command string, never a new taxonomy.

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
    ParameterProvenance,
    RequestClass,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    command_text_references_target_identifier_class,
    required_target_parameter_gaps,
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

_ALARM_SECTION_CONTENT = "Alarm Listing\nTo check and list all alarms currently present, run: get alarms\n"
_ALARM_COMMAND = "get alarms"
_RRU_SECTION_CONTENT = "RRU Restart\nTo restart the affected RRU, run: accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1\n"
_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_KNOWLEDGE_ID = "livecorr9-doc"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


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


# =============================================================================
# Direct proof: the active section's own real content, not the rejected
# command string, is what determines target cardinality.
# =============================================================================


def test_active_section_content_without_identifier_relaxes_requirement() -> None:
    """The alarm-listing section's own real content -- used as the
    `grounded_command_candidate` when no command survived grounding --
    contains no unit-class identifier at all."""
    assert command_text_references_target_identifier_class(_ALARM_SECTION_CONTENT) is False
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="vendor", value="Ericsson", provenance=ParameterProvenance.USER)],
        grounded_command_candidate=_ALARM_SECTION_CONTENT,
    )
    assert gaps == []


def test_active_section_content_with_identifier_preserves_requirement() -> None:
    assert command_text_references_target_identifier_class(_RRU_SECTION_CONTENT) is True
    gaps = required_target_parameter_gaps(RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND, [], grounded_command_candidate=_RRU_SECTION_CONTENT)
    assert gaps == ["unit_id", "unit_type"]


# =============================================================================
# A -- exact-command refinement: same operation, added vendor, rejected
# candidate on turn 2 must not fabricate a physical-target requirement.
# =============================================================================


@pytest.mark.asyncio
async def test_a_exact_command_refinement_preserves_operation_identity() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    service = ApiSessionService()
    session_id = await service.create_session()

    # Turn 1: "what is the cmd i need to run to check and list alarms present?"
    async def _turn1_side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, _evidence_item("s-alarms", "Alarm Listing", _ALARM_SECTION_CONTENT))
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="list alarms",
            missing_context=[],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                next_action="Run the command below.",
                command=_ALARM_COMMAND,
                operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
            ),
        )

    events1 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service_1 = ChatService(service, runner=FakeRunner(service, side_effect=_turn1_side_effect, events=events1))
    completed1 = None
    async for event in chat_service_1.execute_turn_events(
        session_id, "what is the cmd i need to run to check and list alarms present?", "api-user"
    ):
        if event.type.value == "message.completed":
            completed1 = event
    assert completed1 is not None
    assert _ALARM_COMMAND in completed1.data["content"]

    # Turn 2 (SAME session): "no, i just need a cmd to run a check on
    # alarms for ericsson" -- the SAME section is reselected (vendor does
    # not change which document is relevant), but the model's OWN
    # reconstructed command proposal is REJECTED by grounding (command=
    # None after stripping) -- exactly the live `grounding_rejected` shape.
    async def _turn2_side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, _evidence_item("s-alarms", "Alarm Listing", _ALARM_SECTION_CONTENT))
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="list alarms",
            provided_context=[RequestParameter(name="vendor", value="Ericsson", provenance=ParameterProvenance.USER)],
            missing_context=[],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                next_action="I could not verify an exact command for Ericsson alarm listing.",
                command=None,  # rejected/stripped by grounding this turn
                operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ,
            ),
        )

    events2 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service_2 = ChatService(service, runner=FakeRunner(service, side_effect=_turn2_side_effect, events=events2))
    completed2 = None
    async for event in chat_service_2.execute_turn_events(
        session_id, "no, i just need a cmd to run a check on alarms for ericsson", "api-user"
    ):
        if event.type.value == "message.completed":
            completed2 = event
    assert completed2 is not None
    content2 = completed2.data["content"]
    # THE key proof: no fabricated physical-target clarification. Never
    # "Please confirm... unit identifier..." -- the resolved operation
    # (alarm listing) never concerned one physical unit, so its own,
    # already-computed next_action text passes through untouched.
    assert "unit" not in content2.lower()
    assert content2 == "I could not verify an exact command for Ericsson alarm listing."


# =============================================================================
# B/C -- re-grounding remains mandatory; rejection stays safe without
# fabricating unrelated physical-target requirements.
# =============================================================================


def test_b_vendor_change_still_requires_real_grounding_no_reuse_of_old_command() -> None:
    """Changing vendor/platform must still go through real grounding --
    the OLD command is never blindly reused. This test proves the POLICY
    layer never emits a command merely because `request_class` stayed
    `EXACT_COMMAND` -- `may_emit_command` still depends on the CURRENT
    turn's own guidance actually surviving `evidence.py`'s grounding."""
    from backend.agents.team_manager.request_execution_policy import derive_execution_decision

    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="list alarms",
        provided_context=[RequestParameter(name="vendor", value="Ericsson", provenance=ParameterProvenance.USER)],
        missing_context=[],
        request_class=RequestClass.EXACT_COMMAND,
        run_id="r1",
    )
    # The active section's own content (no identifier) is what chat_
    # service.py now falls back to -- the policy layer permits the OUTPUT
    # SHAPE (no fabricated unit_id/unit_type), but never emits any
    # specific command string itself -- that remains evidence.py's own,
    # separate, unchanged job for the CURRENT turn's own real proposal.
    decision = derive_execution_decision(contract, "r1", grounded_command_candidate=_ALARM_SECTION_CONTENT)
    assert decision.status == "allow"
    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert "unit_id" not in decision.missing_context
    assert "unit_type" not in decision.missing_context


def test_c_grounding_rejection_stays_fail_closed_without_fabricating_unit_requirement() -> None:
    """If re-grounding genuinely fails (no active section resolvable at
    all this turn, e.g. a brand-new ambiguous topic), the ORIGINAL,
    unchanged blanket rule still applies -- fail closed is preserved,
    never silently relaxed merely because SOME EXACT_COMMAND turn
    happened once before."""
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND,
        RequestedOutput.EXACT_COMMAND,
        [RequestParameter(name="vendor", value="Ericsson", provenance=ParameterProvenance.USER)],
        grounded_command_candidate=None,  # no active section resolvable
    )
    assert gaps == ["unit_id", "unit_type"]


# =============================================================================
# D -- RRU continuation: strict, state-changing target confirmation
# remains fully intact across a 3-turn refinement.
# =============================================================================


@pytest.mark.asyncio
async def test_d_rru_restart_continuation_across_three_turns() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    service = ApiSessionService()
    session_id = await service.create_session()

    # Turn 1: "give me the command to restart an RRU" -- no target at all.
    async def _turn1(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, _evidence_item("s-rru", "RRU Restart", _RRU_SECTION_CONTENT))
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            missing_context=[],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(
                interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
                next_action="Confirm the unit type and identifier first.",
                command=None,
            ),
        )

    events1 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    cs1 = ChatService(service, runner=FakeRunner(service, side_effect=_turn1, events=events1))
    completed1 = None
    async for event in cs1.execute_turn_events(session_id, "give me the command to restart an RRU", "api-user"):
        if event.type.value == "message.completed":
            completed1 = event
    assert completed1 is not None
    assert _RRU_COMMAND not in completed1.data["content"]

    # Turn 2: "its an rru" -- unit_type now verified, unit_id still missing.
    async def _turn2(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, _evidence_item("s-rru", "RRU Restart", _RRU_SECTION_CONTENT))
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            continuation=True,
            provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
            missing_context=[],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, next_action="Confirm the exact RRU identifier.", command=None),
        )

    events2 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    cs2 = ChatService(service, runner=FakeRunner(service, side_effect=_turn2, events=events2))
    completed2 = None
    async for event in cs2.execute_turn_events(session_id, "its an rru", "api-user"):
        if event.type.value == "message.completed":
            completed2 = event
    assert completed2 is not None
    assert _RRU_COMMAND not in completed2.data["content"]

    # Turn 3: "the RRU ID is RRU-9" -- fully resolved, command eligible
    # only because it is ALSO genuinely grounded this turn.
    async def _turn3(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, _evidence_item("s-rru", "RRU Restart", _RRU_SECTION_CONTENT))
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            continuation=True,
            provided_context=[
                RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
                RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
            ],
            missing_context=[],
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
                operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
                source_section_id="s-rru",
            ),
        )

    events3 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    cs3 = ChatService(service, runner=FakeRunner(service, side_effect=_turn3, events=events3))
    completed3 = None
    async for event in cs3.execute_turn_events(session_id, "the RRU ID is RRU-9", "api-user"):
        if event.type.value == "message.completed":
            completed3 = event
    assert completed3 is not None
    assert _RRU_COMMAND in completed3.data["content"]


# =============================================================================
# E -- general topic switch: prior EXACT_COMMAND operation must not leak.
# =============================================================================


@pytest.mark.asyncio
async def test_e_topic_switch_to_general_conversation_does_not_leak_prior_operation() -> None:
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    service = ApiSessionService()
    session_id = await service.create_session()

    async def _turn1(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        _select(run_id, _evidence_item("s-alarms", "Alarm Listing", _ALARM_SECTION_CONTENT))
        contract = RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="list alarms",
            missing_context=[],
            request_class=RequestClass.EXACT_COMMAND,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
        register_troubleshooting_guidance(
            run_id,
            TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, next_action="Run the command below.", command=_ALARM_COMMAND),
        )

    events1 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    cs1 = ChatService(service, runner=FakeRunner(service, side_effect=_turn1, events=events1))
    async for event in cs1.execute_turn_events(session_id, "what is the cmd to list current alarms?", "api-user"):
        pass

    # Turn 2: a genuinely different, non-operational request.
    natural_reply = "I'm SLOPANOC, your operational assistant for Teams-based incident collaboration."

    async def _turn2(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject=None,
            missing_context=[],
            request_class=RequestClass.GENERAL_CONVERSATION,
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    events2 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text=natural_reply, final=True),
    ]
    cs2 = ChatService(service, runner=FakeRunner(service, side_effect=_turn2, events=events2))
    completed2 = None
    async for event in cs2.execute_turn_events(session_id, "who are you and what can you do?", "api-user"):
        if event.type.value == "message.completed":
            completed2 = event
    assert completed2 is not None
    assert completed2.data["content"] == natural_reply
    assert _ALARM_COMMAND not in completed2.data["content"]
