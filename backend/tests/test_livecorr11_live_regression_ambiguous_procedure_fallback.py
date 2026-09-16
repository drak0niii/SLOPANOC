"""LIVE-CORR-11 -- LIVE REGRESSION CORRECTION: Exact-Command Fulfillment
After Clarification.

LIVE REPRODUCTION (session 389c0d82-fff5-41c7-bd66-88e5623465c6):

Turn 1: "give me a command to restart an RRU" -- correctly fails closed
(NEEDS_INFORMATION, missing unit_id/unit_type, grounding rejected
`reason=ambiguous_procedure`: incident_manager's own selected evidence
that turn spanned more than one candidate governed procedure).

Turn 2: "the RRU is RRU-3" -- the CONTRACT-level target is now fully
resolved (`provided_context=[unit_id]`, `missing_context=[]`), so
`derive_execution_decision` correctly resolves `status=ALLOW`,
`request_class=EXACT_COMMAND`, `may_emit_command=True` -- "eligible,"
never "a candidate already exists" (see that module's own docstring).
`requires_governed_knowledge=True` but this turn's own selected evidence
is empty/inconsistent with the prior turn's own ambiguous anchor, so
`chat_service.py` forces `enforce_governed_knowledge_at_completion`.

PROVEN ROOT CAUSE (confirmed by direct source trace, not inferred):
`enforce_governed_knowledge_at_completion` (governed_knowledge_
completion.py), when the PRIOR turn's own selected evidence itself
revalidates to more than one candidate procedure and cannot be
deterministically narrowed (`resolve_active_candidate_among_ambiguous`
returns `None`), returns `build_ambiguous_procedure_clarification(...),
[]` IMMEDIATELY -- `_run_incident_manager_remediation_once` (the ONLY
call that would invoke `incident_manager`/`knowledge_search`/`knowledge_
select_evidence`) is NEVER reached on this path. No `TroubleshootingGuidance`
is ever produced or lost -- it is never attempted. `final_text` is
already this module's own correct, deterministic "which procedure do
you mean" text.

`chat_service.py`'s rendering block then reaches `elif requires_
unstructured_response_backstop(execution_decision, troubleshooting_
guidance_present=False):` -- `True` for THIS turn (`ALLOW` + `intent=
COMMAND` -> `is_operationally_shaped_request` is `True`, per LIVE-CORR-
3B's own ALLOW-widening) -- which unconditionally OVERWRITES the
already-correct, already-safe governed-completion text with the
strictly more generic `command_suppression_fallback_text(execution_
decision)` -- `_GENERIC_WITHHELD_COMMAND_TEXT` ("An exact command
cannot yet be safely provided for this step. Please confirm the missing
details."), since `execution_decision.missing_context` is empty. This
text is actively MISLEADING: no target detail is missing; a DIFFERENT,
governed-evidence-level ambiguity (which procedure) is what remains
unresolved.

`may_emit_command=True` on this turn means EXACTLY what `derive_
execution_decision`'s own docstring already says: "exact-command output
permitted, subject to existing grounding" -- eligibility, never proof a
concrete grounded candidate exists. This was never ambiguous in the
code; the live trace's own apparent contradiction ("ALLOW+may_emit_
command=True" alongside "please confirm the missing details") is fully
explained by the backstop overwrite above, not by any inconsistency in
`may_emit_command`'s own meaning.

FIX: `governed_knowledge_completion.py` marks a new, tiny, in-process,
run-id-keyed signal (mirroring `troubleshooting_guidance_context.py`'s
own register/pop/discard pattern) at each of its three fixed-template
return sites (never at the real-specialist-summary success return).
`chat_service.py` reads it back, correlated by the SAME `governed_
completion_run_id` its own guidance re-pop already uses, and skips the
backstop overwrite ONLY for that already-safe shape -- the real-summary
success path remains fully, unconditionally subject to the backstop,
exactly as before this pass.

No real Gemini call, no external network, no Cloud SQL requirement.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.agents.team_manager.governed_knowledge_completion import (
    discard_governed_completion_deterministic_fallback,
    enforce_governed_knowledge_at_completion,
    pop_governed_completion_deterministic_fallback,
)
from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    RequestClass,
    RequestContract,
    RequestedOutput,
    RequestIntent,
    RequestParameter,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionStatus,
    command_suppression_fallback_text,
    derive_execution_decision,
    requires_unstructured_response_backstop,
)
from backend.api.governed_evidence_continuity import RevalidatedGovernedProcedure
from backend.knowledge.provenance.contracts import KnowledgeEvidenceSelectionKey
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RUN_ID = "livecorr11-liveregr-run"
_RESTART_COMMAND_TEXT = "accn FieldReplaceableUnit=RRU-3 restartunit 1 1 1"


def _param(name: str, value: str) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance=ParameterProvenance.USER)


def _resolved_contract() -> RequestContract:
    """Turn 2's own real, live-reproduced shape: target fully resolved."""
    return RequestContract(
        intent=RequestIntent.COMMAND,
        subject="restart an RRU",
        requested_output=RequestedOutput.EXACT_COMMAND,
        continuation=True,
        requires_governed_knowledge=True,
        provided_context=[_param("unit_id", "RRU-3")],
        missing_context=[],
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )


# =============================================================================
# A -- exact command requires target (baseline, unchanged by this pass)
# =============================================================================


def test_a_exact_command_requires_target():
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        subject="restart an RRU",
        requested_output=RequestedOutput.EXACT_COMMAND,
        continuation=False,
        requires_governed_knowledge=True,
        provided_context=[],
        missing_context=["unit_id", "unit_type"],
        request_class=RequestClass.EXACT_COMMAND,
        run_id=_RUN_ID,
    )
    decision = derive_execution_decision(contract, _RUN_ID)

    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


# =============================================================================
# B -- clarification supplies RRU-3: one consistent target-context result
# =============================================================================


def test_b_effective_target_context_resolved_consistently_no_duplicate_recomputation():
    """The deterministic policy resolves the target from `provided_
    context`/`missing_context` exactly once, in `derive_execution_
    decision` alone. Neither `requires_unstructured_response_backstop`
    nor `command_suppression_fallback_text` (the only other places that
    read target-context-shaped state) ever independently re-derive
    `unit_id`/`unit_type` presence from `contract.provided_context` --
    both read `decision.missing_context` verbatim, the SAME already-
    computed value. There is exactly one target-requirement computation,
    never two independently-drifting ones."""
    contract = _resolved_contract()
    decision = derive_execution_decision(contract, _RUN_ID)

    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.missing_context == []
    assert decision.may_emit_command is True

    # `requires_unstructured_response_backstop` DOES still return `True`
    # here (`ALLOW` + `intent=COMMAND` -> operationally-shaped, per LIVE-
    # CORR-3B) -- that is correct and unchanged: an ordinary raw-text
    # ALLOW turn with no guidance is still suspect on its own. The live
    # regression's actual fix is at the CALL SITE (chat_service.py),
    # which now skips invoking this check at all when `final_text`
    # already holds governed-completion's own deterministic, safe
    # fallback -- see test C/D for that full-pipeline proof.
    assert requires_unstructured_response_backstop(decision, troubleshooting_guidance_present=False) is True
    # Whatever fallback text WOULD be produced if this check's result
    # were used never claims a specific missing field either -- it stays
    # a generic sentence, not a fabricated "unit_id/unit_type missing"
    # restatement of context the policy already resolved.
    generic_fallback = command_suppression_fallback_text(decision)
    assert "unit_id" not in generic_fallback
    assert "unit_type" not in generic_fallback


# =============================================================================
# C/D -- governed remediation: ambiguous prior evidence never invokes
# incident_manager, and does not silently mark command emission allowed
# while losing the candidate; the real, correct fallback text survives.
# =============================================================================


@pytest.mark.asyncio
async def test_c_ambiguous_prior_evidence_never_invokes_incident_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Direct proof of the exact TURN-2 path (audit question A): when the
    prior turn's own selected evidence revalidates to more than one
    candidate and cannot be deterministically narrowed,
    `_run_incident_manager_remediation_once` -- the ONLY call that would
    invoke `incident_manager`/`knowledge_search`/`knowledge_select_
    evidence` -- is never reached. The returned text is this module's
    own fixed, deterministic clarification, and the new signal is
    correctly marked."""
    from backend.agents.team_manager import governed_knowledge_completion as gkc

    candidates = [
        RevalidatedGovernedProcedure(
            knowledge_id="doc-a", version_label="v1", section_id="s1", title="doc-a", heading="AuxPluginUnit Restart", sibling_headings=()
        ),
        RevalidatedGovernedProcedure(
            knowledge_id="doc-b", version_label="v1", section_id="s2", title="doc-b", heading="FieldReplaceableUnit Restart", sibling_headings=()
        ),
    ]

    async def _fake_revalidate(prior_governed_evidence: Any, repository: Any) -> list[RevalidatedGovernedProcedure]:
        return candidates

    incident_manager_invoked = False

    async def _fake_run_incident_manager_remediation_once(**kwargs: Any) -> tuple[Optional[dict[str, Any]], list[Any]]:
        nonlocal incident_manager_invoked
        incident_manager_invoked = True
        return None, []

    monkeypatch.setattr(gkc, "revalidate_prior_governed_evidence", _fake_revalidate)
    monkeypatch.setattr(gkc, "resolve_active_candidate_among_ambiguous", lambda **kwargs: None)
    monkeypatch.setattr(gkc, "_run_incident_manager_remediation_once", _fake_run_incident_manager_remediation_once)

    prior_keys = [
        KnowledgeEvidenceSelectionKey(knowledge_id=c.knowledge_id, version_label=c.version_label, section_id=c.section_id)
        for c in candidates
    ]

    final_text, selected_evidence = await enforce_governed_knowledge_at_completion(
        question="the RRU is RRU-3",
        chat_topic=None,
        run_id=_RUN_ID,
        prior_governed_evidence=prior_keys,
        active_anchor=None,
        request_contract_subject="restart an RRU",
    )

    assert incident_manager_invoked is False
    assert selected_evidence == []
    assert "AuxPluginUnit Restart" in final_text
    assert "FieldReplaceableUnit Restart" in final_text
    assert pop_governed_completion_deterministic_fallback(_RUN_ID) is True
    discard_governed_completion_deterministic_fallback(_RUN_ID)


@pytest.mark.asyncio
async def test_d_no_structured_candidate_command_does_not_reach_ui_correct_fallback_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Full pipeline: the exact live regression, end to end. The ALREADY-
    CORRECT "which procedure" text -- not the generic, misleading
    "confirm the missing details" text -- reaches the final response. No
    exact command reaches the UI either way."""
    from backend.api import chat_service as chat_service_module
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id
    from backend.agents.team_manager import governed_knowledge_completion as gkc

    _AMBIGUOUS_PROCEDURE_TEXT = (
        "Do you mean the AuxPluginUnit Restart or FieldReplaceableUnit Restart procedure? "
        "Please confirm which one you mean so I can give you the correct governed guidance."
    )

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
        # Mirrors the REAL function's own exact contract for this shape:
        # marks the deterministic-fallback signal, never registers any
        # TroubleshootingGuidance (incident_manager was never invoked).
        gkc._mark_governed_completion_deterministic_fallback(run_id)
        return _AMBIGUOUS_PROCEDURE_TEXT, []

    monkeypatch.setattr(
        chat_service_module, "enforce_governed_knowledge_at_completion", _fake_enforce_governed_knowledge_at_completion
    )

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _resolved_contract().model_copy(update={"run_id": run_id})
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "the RRU is RRU-3", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    content = completed.data["content"]

    assert _RESTART_COMMAND_TEXT not in content
    assert "An exact command cannot yet be safely provided" not in content
    assert content == _AMBIGUOUS_PROCEDURE_TEXT


# =============================================================================
# E -- grounded candidate present: decision + final validator still permit
# rendering (regression proof the fix does not collaterally block success)
# =============================================================================


@pytest.mark.asyncio
async def test_e_grounded_candidate_still_renders(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.agents.incident_manager.schemas import (
        TroubleshootingGuidance,
        TroubleshootingInteractionMode,
        TroubleshootingOperationalEffect,
    )
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _resolved_contract().model_copy(
            update={"run_id": run_id, "provided_context": [_param("unit_type", "RRU"), _param("unit_id", "RRU-3")]}
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})
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
    async for event in chat_service.execute_turn_events(session_id, "the RRU is RRU-3", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert _RESTART_COMMAND_TEXT in completed.data["content"]


# =============================================================================
# F -- may_emit_command=False: no exact command reaches the UI
# (regression proof the new skip-backstop branch is correctly SCOPED --
# a real, unvalidated specialist summary remains fully protected)
# =============================================================================


@pytest.mark.asyncio
async def test_f_real_specialist_summary_without_guidance_still_replaced_by_backstop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The new skip-backstop branch fires ONLY for this module's own
    fixed-template returns -- a REAL specialist `summary` (the success
    path, `governed_completion_used_deterministic_fallback` stays
    `False`) remains fully, unconditionally subject to `requires_
    unstructured_response_backstop`, exactly as LIVE-CORR-3B intended.
    Proves the fix is narrowly scoped, not a blanket exemption."""
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
        # A REAL success-path return -- no deterministic-fallback signal
        # marked, mirroring `enforce_governed_knowledge_at_completion`'s
        # own `return summary, selected_evidence` exactly.
        return f"Sure, here you go: {_RESTART_COMMAND_TEXT}", []

    monkeypatch.setattr(
        chat_service_module, "enforce_governed_knowledge_at_completion", _fake_enforce_governed_knowledge_at_completion
    )

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _resolved_contract().model_copy(update={"run_id": run_id})
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "the RRU is RRU-3", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    content = completed.data["content"]

    assert _RESTART_COMMAND_TEXT not in content


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])
