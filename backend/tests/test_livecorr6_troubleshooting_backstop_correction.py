"""LIVE-CORR-6 -- Valid Troubleshooting Response Incorrectly Replaced by
Command Backstop.

CONFIRMED LIVE DEFECT: "how can i troubleshoot: ESS Service Unavailable?"
-- a fully-resolved (`subject` set, `ambiguity=False`, `missing_context=
[]`), `status=ALLOW` TROUBLESHOOTING/TROUBLESHOOTING_NEXT_STEP request,
whose governed-knowledge remediation successfully found evidence and
whose Incident Manager grounding correctly ran (`interaction_mode=
next_step selected_count=1 command_present=False stripped=True
reason=unstructured_state_change`) -- was replaced by the generic
exact-command-style `_GENERIC_WITHHELD_COMMAND_TEXT` ("An exact command
cannot yet be safely provided for this step. Please confirm the missing
details."), even though no command/target-parameter question was ever
actually involved.

PROVEN ROOT CAUSE (two compounding, independently-provable defects):

1. `chat_service.py` pops `captured_troubleshooting_guidance` from the
   run-scoped store using `sequencer.run_id` ONCE, early (before the
   governed-knowledge-completion remediation branch ever runs).
   `enforce_governed_knowledge_at_completion` (governed_knowledge_
   completion.py) runs the REAL `incident_manager` a SECOND time, under a
   DIFFERENT, SUFFIXED run_id (`f"{sequencer.run_id}::governed-
   completion"`) -- so any `TroubleshootingGuidance` THAT call registers
   is written under a key `chat_service.py` never reads again.
   `captured_troubleshooting_guidance` therefore stays `None` even though
   a real, correctly-grounded guidance object was genuinely produced --
   confirmed directly via source inspection of both files, not inferred.

2. `enforce_execution_decision_on_guidance` (request_execution_policy.py)
   -- even once a guidance object IS correctly retrieved -- unconditionally
   wiped the ENTIRE `NEXT_STEP` guidance (`interpretation`/`next_action`/
   `evidence_requested`, not merely `command`) whenever `may_emit_command`
   is `False`. Since `TROUBLESHOOTING`+`TROUBLESHOOTING_NEXT_STEP` NEVER
   receives `may_emit_command=True` by LIVE-CORR-3B's own design (command
   permission exists only for `intent=COMMAND, requested_output=
   EXACT_COMMAND`), this wiped EVERY SINGLE troubleshooting_next_step
   guidance in the system, regardless of whether a command was ever
   proposed at all -- confirmed by direct execution (see this file's own
   audit trail in the LIVE-CORR-6 closure report).

FIX:
  - `backend/api/chat_service.py`: after the governed-completion
    remediation call, additionally pop the guidance registered under
    ITS OWN suffixed run_id and use it if present; and never let an
    empty structured re-render silently clobber an already-correct,
    non-empty `final_text` when this layer itself suppressed nothing.
  - `backend/agents/team_manager/request_execution_policy.py`:
    `enforce_execution_decision_on_guidance`'s NEXT_STEP branch now
    mirrors its own sibling FULL_PROCEDURE branch -- strips `command`
    only, when present; never touches `interpretation`/`next_action`/
    `evidence_requested`.

Neither change touches `evidence.py`'s own grounding/structural-integrity
layer, `derive_execution_decision`'s command-authority rules, or the
approval/action boundary -- all completely unchanged.

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
    RequestContract,
    RequestIntent,
    RequestedOutput,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionDecision,
    RequestExecutionStatus,
    command_suppression_fallback_text,
    derive_execution_decision,
    enforce_execution_decision_on_guidance,
)
from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RUN_ID = "livecorr6-run"
_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


# =============================================================================
# A/D -- `enforce_execution_decision_on_guidance` -- the core semantic fix
# =============================================================================


def test_a_safe_next_step_guidance_with_no_command_is_never_stripped() -> None:
    """A -- a fully-resolved, `may_emit_command=False` TROUBLESHOOTING_
    NEXT_STEP turn whose guidance is correctly classified as safe/
    diagnostic (`OBSERVATION`/`DIAGNOSTIC_READ`/`REFERENCE_DESCRIPTION` --
    no state-changing authority claimed) and never carried a command at
    all. Must reach the user completely unmodified -- NOT replaced by an
    exact-command-style fallback."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="This looks like a service availability issue.",
        next_action="Check the ESS service logs for connection errors.",
        command=None,
        evidence_requested="Report what the logs show.",
        operational_effect=TroubleshootingOperationalEffect.OBSERVATION,
    )
    decision = RequestExecutionDecision(
        status=RequestExecutionStatus.ALLOW,
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="ESS Service Unavailable",
        may_emit_command=False,
        missing_context=[],
    )
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is False
    assert corrected.interpretation == "This looks like a service availability issue."
    assert corrected.next_action == "Check the ESS service logs for connection errors."
    assert corrected.evidence_requested == "Report what the logs show."


def test_d_state_change_recommendation_with_command_is_withheld_entirely() -> None:
    """D (non-regression) -- a GENUINE state-changing recommendation
    (`operational_effect` unset, defaulting to the strictest
    `STATE_CHANGE_RECOMMENDATION` interpretation -- e.g. "restart the
    unit") carrying a real, unauthorized command must still be withheld
    ENTIRELY -- unchanged from before this pass -- since a command
    smuggled into free text while `command` is left unset must be caught
    the same way regardless of whether `command` also happens to be
    populated. This is the "existing architecture does not support
    partial trust here" case section 6 of that pass' own instruction
    itself anticipates."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Confirm the unit type first.",
        next_action="Restart the unit.",
        command=_RRU_COMMAND,
        evidence_requested="Report the unit type.",
    )
    decision = RequestExecutionDecision(
        status=RequestExecutionStatus.ALLOW,
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        may_emit_command=False,
        missing_context=[],
    )
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is True
    assert corrected.command is None
    assert corrected.interpretation is None
    assert corrected.next_action is None
    assert _RRU_COMMAND not in command_suppression_fallback_text(decision)


def test_d_safe_classification_with_a_spurious_command_strips_command_keeps_text() -> None:
    """D (new defense-in-depth case) -- "remove unsafe command != remove
    the entire safe troubleshooting answer": a guidance explicitly
    classified as carrying NO operational/state-changing authority
    (`OBSERVATION`) that nonetheless (unusually) also populated `command`
    -- the command is still stripped (defense in depth, `may_emit_command=
    False`), but the genuinely safe interpretation/next_action text
    survives, since this classification never claimed to need the command
    in the first place."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Check the current alarm status.",
        next_action="Review the alarm log for the affected cell.",
        command=_RRU_COMMAND,
        evidence_requested=None,
        operational_effect=TroubleshootingOperationalEffect.OBSERVATION,
    )
    decision = RequestExecutionDecision(
        status=RequestExecutionStatus.ALLOW,
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        may_emit_command=False,
        missing_context=[],
    )
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is True
    assert corrected.command is None
    assert corrected.interpretation == "Check the current alarm status."
    assert corrected.next_action == "Review the alarm log for the affected cell."


def test_full_procedure_command_stripping_behavior_unchanged() -> None:
    """Non-regression: the sibling FULL_PROCEDURE branch, already correct,
    already tested -- untouched by this pass."""
    from backend.agents.incident_manager.schemas import TroubleshootingStep

    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[
            TroubleshootingStep(action="Check the alarm log.", command=None),
            TroubleshootingStep(action="Restart the unit.", command=_RRU_COMMAND),
        ],
    )
    decision = RequestExecutionDecision(status=RequestExecutionStatus.ALLOW, may_emit_command=False)
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is True
    assert corrected.full_procedure_steps[0].action == "Check the alarm log."
    assert corrected.full_procedure_steps[1].action == "Restart the unit."
    assert corrected.full_procedure_steps[1].command is None


def test_exact_command_intent_still_permits_command_through_this_layer() -> None:
    """Non-regression: `may_emit_command=True` (a validated EXACT_COMMAND
    request) is still a complete no-op for this function."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        next_action="Restart the RRU.",
        command=_RRU_COMMAND,
    )
    decision = RequestExecutionDecision(status=RequestExecutionStatus.ALLOW, may_emit_command=True)
    corrected, stripped = enforce_execution_decision_on_guidance(guidance, decision)
    assert stripped is False
    assert corrected.command == _RRU_COMMAND


# =============================================================================
# B -- genuinely missing information still produces the correct clarification
# =============================================================================


def test_b_troubleshooting_with_genuinely_missing_context_still_clarifies() -> None:
    contract = RequestContract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        missing_context=["unit_type", "unit_id"],
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False
    text = command_suppression_fallback_text(decision)
    assert "unit" in text.lower()  # the specific, already-known gap -- never the generic fallback


# =============================================================================
# C -- exact command with unresolved required parameters remains blocked
# =============================================================================


def test_c_exact_command_with_unresolved_parameters_still_blocked() -> None:
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="HW Partial Fault",
        missing_context=["unit_type", "unit_id"],
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


# =============================================================================
# Full pipeline -- reproduces the exact live defect through ChatService,
# proving BOTH fixes (run_id retrieval + command-only stripping) together.
# =============================================================================


async def _run_turn_with_governed_completion(
    monkeypatch: pytest.MonkeyPatch, remediation_guidance: Optional[TroubleshootingGuidance], remediation_text: str
) -> str:
    from backend.api import chat_service as chat_service_module
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _fake_enforce_governed_knowledge_at_completion(
        *,
        question: str,
        chat_topic: Optional[str],
        run_id: str,
        image_parts: Any = (),
        prior_governed_evidence: Any = (),
        active_anchor: Any = None,
        request_contract_subject: Optional[str] = None,
    ) -> tuple[str, list[Any]]:
        # Mirrors the REAL function's own contract exactly: registers
        # whatever TroubleshootingGuidance the (real, unmodified)
        # incident_manager would have produced under ITS OWN run_id --
        # the SAME run_id chat_service.py passes in -- and returns the
        # SAME already-rendered text incident_manager's own after_agent_
        # callback (evidence.py) would have computed into `summary`.
        if remediation_guidance is not None:
            register_troubleshooting_guidance(run_id, remediation_guidance)
        return remediation_text, []

    monkeypatch.setattr(
        chat_service_module, "enforce_governed_knowledge_at_completion", _fake_enforce_governed_knowledge_at_completion
    )

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="ESS Service Unavailable",
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "how can i troubleshoot: ESS Service Unavailable ?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    return completed.data["content"]


@pytest.mark.asyncio
async def test_full_pipeline_exact_live_defect_safe_diagnostic_content_reaches_user(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE exact reported live defect, reproduced end to end through the
    real ChatService completion boundary: governed-knowledge remediation
    registers a real, safe, command-free NEXT_STEP guidance under its own
    suffixed run_id. Before this fix, `final_text` was unconditionally
    replaced with `_GENERIC_WITHHELD_COMMAND_TEXT`. Must now show the real
    diagnostic content."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="This looks like a service availability issue.",
        next_action="Check the ESS service logs for connection errors.",
        command=None,
        evidence_requested="Report what the logs show.",
        operational_effect=TroubleshootingOperationalEffect.OBSERVATION,
    )
    content = await _run_turn_with_governed_completion(
        monkeypatch,
        remediation_guidance=guidance,
        remediation_text="This looks like a service availability issue.\n\nCheck the ESS service logs for connection errors.",
    )
    assert "An exact command cannot yet be safely provided" not in content
    assert "Check the ESS service logs for connection errors." in content


@pytest.mark.asyncio
async def test_full_pipeline_state_change_command_in_remediation_guidance_withheld_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    """D (non-regression) at the pipeline level: the remediation's own
    guidance is a genuine state-changing recommendation (`operational_
    effect` unset, e.g. "restart the unit") carrying a real, unauthorized
    command -- the command must never reach the user, and (unchanged from
    before this pass) neither does the rest of the free text, since a
    command smuggled into prose while `command` is left unset must be
    caught the same way."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Confirm the unit type first.",
        next_action="Restart the unit.",
        command=_RRU_COMMAND,
        evidence_requested="Report the unit type.",
    )
    content = await _run_turn_with_governed_completion(
        monkeypatch,
        remediation_guidance=guidance,
        remediation_text=f"Confirm the unit type first.\n\nRestart the unit.\n\nRun:\n\n{_RRU_COMMAND}",
    )
    assert _RRU_COMMAND not in content
    assert "Restart the unit." not in content


@pytest.mark.asyncio
async def test_full_pipeline_safe_classification_with_spurious_command_strips_command_keeps_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D (new defense-in-depth case) at the pipeline level: a guidance
    explicitly classified as safe/diagnostic (`OBSERVATION`) that
    unusually also carries a command -- the command must never reach the
    user, but the genuinely safe diagnostic text does."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Check the current alarm status.",
        next_action="Review the alarm log for the affected cell.",
        command=_RRU_COMMAND,
        operational_effect=TroubleshootingOperationalEffect.OBSERVATION,
    )
    content = await _run_turn_with_governed_completion(
        monkeypatch,
        remediation_guidance=guidance,
        remediation_text=f"Check the current alarm status.\n\nReview the alarm log for the affected cell.\n\nRun:\n\n{_RRU_COMMAND}",
    )
    assert _RRU_COMMAND not in content
    assert "Check the current alarm status." in content
    assert "Review the alarm log for the affected cell." in content


@pytest.mark.asyncio
async def test_full_pipeline_upstream_structural_suppression_preserves_evidence_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other honest outcome this fix produces (section on residual
    behavior): when evidence.py's OWN, untouched, structural-integrity
    layer already suppressed the guidance entirely (a genuinely unsafe,
    bare state-change recommendation with no backing command), its own
    specific, already-computed explanation must survive -- never silently
    replaced by an empty string, and never by the WRONG, unrelated
    exact-command fallback text either."""
    empty_guidance = TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP)
    evidence_fallback_text = (
        "This step described an operational recommendation without a verified command to back it, so it has "
        "been withheld for safety. Please ask again."
    )
    content = await _run_turn_with_governed_completion(
        monkeypatch, remediation_guidance=empty_guidance, remediation_text=evidence_fallback_text
    )
    assert content == evidence_fallback_text
    assert "An exact command cannot yet be safely provided" not in content


@pytest.mark.asyncio
async def test_full_pipeline_no_remediation_guidance_registered_is_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-regression: an ORDINARY governed-knowledge remediation that
    never populates `troubleshooting_guidance` at all (a plain, non-
    operational factual governed-knowledge answer) is completely
    unaffected by this pass -- `captured_troubleshooting_guidance` stays
    `None`, exactly as before. Uses `intent=INFORMATION`/`FACT` (never
    operationally shaped, so the SEPARATE, pre-existing, unrelated `ALLOW`
    backstop -- which unconditionally fires for an operationally-shaped
    request with no guidance at all, LIVE-CORR-3B, untouched here -- does
    not confound this specific proof)."""
    from backend.agents.team_manager.request_contract import RequestIntent as _Intent
    from backend.agents.team_manager.request_contract import RequestedOutput as _Output

    from backend.api import chat_service as chat_service_module
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id

    async def _fake_enforce_governed_knowledge_at_completion(*, question, chat_topic, run_id, image_parts=(), prior_governed_evidence=(), active_anchor=None, request_contract_subject=None):
        return "A plain factual governed-knowledge answer.", []

    monkeypatch.setattr(
        chat_service_module, "enforce_governed_knowledge_at_completion", _fake_enforce_governed_knowledge_at_completion
    )

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = RequestContract(
            intent=_Intent.INFORMATION,
            requested_output=_Output.FACT,
            subject="VSWR threshold",
            missing_context=[],
            run_id=run_id,
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "what is the VSWR threshold for cell X?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] == "A plain factual governed-knowledge answer."
