"""LIVE-CORR-12D -- Request-Scoped Governed Evidence Continuity.

THE GAP THIS CLOSES: the LIVE-CORR-12A architectural audit proved governed-
evidence continuity (`LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY`/`ACTIVE_
GOVERNED_PROCEDURE_STATE_KEY`, consulted by `chat_service.py`'s governed-
completion boundary) operated as a SECOND, INDEPENDENT continuity engine --
entry into it never depended on `request_class`, `continuation`,
`PendingGovernedRequest`, or current-vs-prior operation identity. Live-
reproduced: after a "restart RRU" exchange selected governed evidence,
"what is alt command doing?" (`request_class=OPERATIONAL_INFORMATION`,
`continuation=False`) and "Resource Activation Timeout on RRU -- help me
fix it" (`request_class=PROCEDURE_TROUBLESHOOTING`) both still inherited
the STALE "restart RRU" evidence as their own candidate universe, produced
a stale "Do you mean MOP X or Document1?" clarification, and -- critically
-- NEVER gave the real `incident_manager`/fresh `knowledge_search`/TELCO-
applicability pipeline a chance to run for the actual current request.

FIX: `is_governed_evidence_continuity_permitted` (request_execution_
policy.py) is the missing CONTINUITY CHECK -- consulted by `chat_service.py`
BEFORE it decides what to pass as `prior_governed_evidence`/`active_anchor`
into `enforce_governed_knowledge_at_completion`. `False` means prior
evidence is never even loaded -- `enforce_governed_knowledge_at_completion`'s
own, completely UNCHANGED revalidation/ambiguity/scoping logic then
naturally sees zero stale candidates and falls straight through to a
fresh, unscoped `incident_manager` run. `governed_evidence_continuity.py`/
`governed_knowledge_completion.py` are untouched by this pass.

This module does NOT touch semantic parameter-value verification, natural
clarification wording, RequestClass/PendingGovernedRequest design, or
presentation-runner behavior -- explicitly out of scope for this pass.

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from backend.agents.team_manager.request_contract import (
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    PendingGovernedRequest,
    PendingGovernedRequestStatus,
    RequestClass,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    derive_request_class,
)
from backend.agents.team_manager.request_execution_policy import is_governed_evidence_continuity_permitted
from backend.api.chat_service import ChatService
from backend.api.governed_evidence_continuity import (
    ACTIVE_GOVERNED_PROCEDURE_STATE_KEY,
    LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY,
    build_active_governed_procedure_state_update,
)
from backend.api.session_service import ApiSessionService
from backend.api.turn_context import current_run_id
from backend.knowledge.provenance.contracts import KnowledgeEvidenceSelectionKey
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RUN_ID = "livecorr12d-run"
_RESTART_KEY = KnowledgeEvidenceSelectionKey(knowledge_id="doc-restart-rru", version_label="v1", section_id="s-restart")
_MOPX_KEY = KnowledgeEvidenceSelectionKey(knowledge_id="doc-mop-x", version_label="v1", section_id="s-mopx")


def _param(name: str, value: str) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance=ParameterProvenance.USER)


def _with_request_class(contract: RequestContract) -> RequestContract:
    """Hand-built `RequestContract(...)` instances leave `request_class`
    unset (populated ENTIRELY server-side) -- stamps it here from the SAME
    pure `derive_request_class` the real callback uses."""
    return contract.model_copy(
        update={
            "request_class": derive_request_class(
                contract.intent, contract.requested_output, contract.action_requested, contract.subject
            )
        }
    )


# =============================================================================
# Unit-level: `is_governed_evidence_continuity_permitted` -- the continuity
# check itself
# =============================================================================


def test_a_alt_command_request_does_not_permit_continuity() -> None:
    """Live Reproduction A -- 'what is alt command doing?' after a prior
    restart-RRU exchange."""
    contract = _with_request_class(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject="alt command",
            continuation=False,
            missing_context=[],
            run_id=_RUN_ID,
        )
    )
    assert contract.request_class == RequestClass.OPERATIONAL_INFORMATION
    assert is_governed_evidence_continuity_permitted(contract, _RUN_ID) is False


def test_b_new_troubleshooting_request_does_not_permit_continuity() -> None:
    """Live Reproduction B -- 'Resource Activation Timeout on an RRU --
    help me fix it' after a prior restart-RRU exchange."""
    contract = _with_request_class(
        RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="Resource Activation Timeout on RRU",
            continuation=False,
            run_id=_RUN_ID,
        )
    )
    assert contract.request_class == RequestClass.PROCEDURE_TROUBLESHOOTING
    assert is_governed_evidence_continuity_permitted(contract, _RUN_ID) is False


def test_c_genuine_same_procedure_continuation_permits_continuity() -> None:
    """'here is the diagnostic result...' -- a genuine follow-up that keeps
    the SAME operational output shape (`continuation=True`, still
    troubleshooting) -- both required signals present."""
    contract = _with_request_class(
        RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject=None,
            continuation=True,
            run_id=_RUN_ID,
        )
    )
    assert contract.request_class == RequestClass.PROCEDURE_TROUBLESHOOTING
    assert is_governed_evidence_continuity_permitted(contract, _RUN_ID) is True


def test_c_continuation_alone_is_not_sufficient() -> None:
    """Section 8's own explicit 'continuation must not be the sole gate'
    requirement -- `continuation=True` with a GENERAL_CONVERSATION-shaped
    contract (no operational intent/output/subject) is NOT enough."""
    contract = _with_request_class(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject=None,
            continuation=True,
            run_id=_RUN_ID,
        )
    )
    assert contract.request_class == RequestClass.GENERAL_CONVERSATION
    assert is_governed_evidence_continuity_permitted(contract, _RUN_ID) is False


def test_d_relevant_pending_exact_command_permits_continuity_even_without_continuation() -> None:
    """Section 12 -- PendingGovernedRequest remains the authority for
    EXACT_COMMAND continuity; the evidence anchor must never independently
    override it. A value-answer turn ('the RRU is RRU-3') that genuinely
    resolves the pending request permits continuity even though its OWN
    `continuation` field is the well-known unreliable `False`."""
    pending = PendingGovernedRequest(
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        intent=RequestIntent.COMMAND,
        status=PendingGovernedRequestStatus.UNRESOLVED,
        subject="restart RRU",
        missing_context=["unit_id"],
        provided_context=[_param("unit_type", "RRU")],
    )
    answer_contract = _with_request_class(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject="RRU ID",
            continuation=False,
            provided_context=[_param("unit_id", "RRU-3")],
            run_id=_RUN_ID,
        )
    )
    assert is_governed_evidence_continuity_permitted(answer_contract, _RUN_ID, pending) is True


def test_d_unrelated_pending_exact_command_does_not_permit_continuity() -> None:
    """The mirror image of the test above: a pending EXACT_COMMAND record
    merely EXISTING must never, by itself, grant continuity to a genuinely
    unrelated fresh question."""
    pending = PendingGovernedRequest(
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        intent=RequestIntent.COMMAND,
        status=PendingGovernedRequestStatus.UNRESOLVED,
        subject="restart RRU",
        missing_context=["unit_id"],
        provided_context=[_param("unit_type", "RRU")],
    )
    unrelated_contract = _with_request_class(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject="alt command",
            continuation=False,
            provided_context=[],
            run_id=_RUN_ID,
        )
    )
    assert is_governed_evidence_continuity_permitted(unrelated_contract, _RUN_ID, pending) is False


def test_e_general_conversation_never_permits_continuity() -> None:
    contract = _with_request_class(
        RequestContract(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, run_id=_RUN_ID)
    )
    assert contract.request_class == RequestClass.GENERAL_CONVERSATION
    assert is_governed_evidence_continuity_permitted(contract, _RUN_ID) is False


def test_g_missing_or_stale_contract_fails_closed() -> None:
    assert is_governed_evidence_continuity_permitted(None, _RUN_ID) is False
    stale = _with_request_class(
        RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="Resource Activation Timeout",
            continuation=True,
            run_id="a-different-run",
        )
    )
    assert is_governed_evidence_continuity_permitted(stale, _RUN_ID) is False


def test_g_ambiguous_contract_fails_closed() -> None:
    ambiguous = _with_request_class(
        RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="Resource Activation Timeout",
            continuation=True,
            ambiguity=True,
            run_id=_RUN_ID,
        )
    )
    assert is_governed_evidence_continuity_permitted(ambiguous, _RUN_ID) is False


# =============================================================================
# End-to-end: chat_service.py wiring proof, mirroring test_6a14_active_
# procedure_continuity.py's own established
# "monkeypatch enforce_governed_knowledge_at_completion, capture kwargs"
# pattern
# =============================================================================


def _seed_stale_evidence_state(pending_update_key: KnowledgeEvidenceSelectionKey) -> dict[str, Any]:
    return build_active_governed_procedure_state_update(pending_update_key)


@pytest.mark.asyncio
async def test_a_end_to_end_alt_command_ignores_stale_restart_rru_evidence(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger="backend.api.chat_service")
    captured_calls: list[dict] = []

    async def fake_enforce(**kwargs: Any) -> tuple[str, list]:
        captured_calls.append(kwargs)
        return "Alt command lists active alarms.", []

    service = ApiSessionService()
    session_id = await service.create_session()

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _with_request_class(
            RequestContract(
                intent=RequestIntent.INFORMATION,
                requested_output=RequestedOutput.FACT,
                subject="alt command",
                continuation=False,
                run_id=run_id,
            )
        )
        delta: dict[str, Any] = {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        # Stale evidence from a PRIOR, unrelated "restart RRU" exchange --
        # already sitting in durable session state before this turn began.
        delta[LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY] = [_RESTART_KEY.model_dump(mode="json")]
        delta.update(build_active_governed_procedure_state_update(_RESTART_KEY))
        await append_state_delta(session_service, session, delta)

    events = [
        FakeEvent(
            text=None,
            final=False,
            function_responses=[FakeFunctionResponse("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True})],
        ),
        FakeEvent(text="unused", final=True),
    ]
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_enforce)
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))
    async for _event in chat_service.execute_turn_events(session_id, "what is alt command doing?", "api-user"):
        pass

    assert len(captured_calls) == 1
    call = captured_calls[0]
    # --- THE core proof: stale "restart RRU" evidence never reaches the
    # remediation call at all -- an empty candidate universe, never a
    # stale document to be ambiguous about.
    assert call["prior_governed_evidence"] == []
    assert call["active_anchor"] is None

    # --- Observability proof (section 19).
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("chat_service: governed_evidence_continuity=")]
    assert lines and "superseded_new_request" in lines[-1]


@pytest.mark.asyncio
async def test_b_end_to_end_new_troubleshooting_request_ignores_stale_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    captured_calls: list[dict] = []

    async def fake_enforce(**kwargs: Any) -> tuple[str, list]:
        captured_calls.append(kwargs)
        return "Next diagnostic step for Resource Activation Timeout.", []

    service = ApiSessionService()
    session_id = await service.create_session()

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _with_request_class(
            RequestContract(
                intent=RequestIntent.TROUBLESHOOTING,
                requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
                subject="Resource Activation Timeout on RRU",
                continuation=False,
                run_id=run_id,
            )
        )
        delta: dict[str, Any] = {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        delta[LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY] = [_RESTART_KEY.model_dump(mode="json")]
        delta.update(build_active_governed_procedure_state_update(_RESTART_KEY))
        await append_state_delta(session_service, session, delta)

    events = [
        FakeEvent(
            text=None,
            final=False,
            function_responses=[FakeFunctionResponse("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True})],
        ),
        FakeEvent(text="unused", final=True),
    ]
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_enforce)
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))
    async for _event in chat_service.execute_turn_events(
        session_id, "i have this issue Resource Activation Timeout on a RRU . help me fix it", "api-user"
    ):
        pass

    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert call["prior_governed_evidence"] == []
    assert call["active_anchor"] is None


@pytest.mark.asyncio
async def test_f_i_ambiguous_prior_evidence_never_reaches_remediation_for_a_fresh_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """F/I combined: seed TWO distinct stale candidates (a genuinely
    ambiguous prior set) -- for a confirmed FRESH, unrelated request, the
    remediation call must receive an EMPTY candidate universe, so the
    pre-existing `build_ambiguous_procedure_clarification`
    ("Do you mean Document1 or MOP X?") early-return inside
    `enforce_governed_knowledge_at_completion` can never even be reached --
    fresh retrieval is what runs instead."""
    captured_calls: list[dict] = []

    async def fake_enforce(**kwargs: Any) -> tuple[str, list]:
        captured_calls.append(kwargs)
        # Simulates the REAL, unmodified `enforce_governed_knowledge_at_
        # completion` behavior for THIS shape: zero prior candidates means
        # its own ambiguity branch is structurally unreachable, so a real
        # fresh-retrieval outcome is returned instead.
        assert kwargs["prior_governed_evidence"] == []
        return "Alt command shows the current active alarm list.", []

    service = ApiSessionService()
    session_id = await service.create_session()

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _with_request_class(
            RequestContract(
                intent=RequestIntent.INFORMATION,
                requested_output=RequestedOutput.FACT,
                subject="alt command",
                continuation=False,
                run_id=run_id,
            )
        )
        delta: dict[str, Any] = {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        # TWO distinct, genuinely ambiguous stale candidates.
        delta[LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY] = [
            _RESTART_KEY.model_dump(mode="json"),
            _MOPX_KEY.model_dump(mode="json"),
        ]
        await append_state_delta(session_service, session, delta)

    events = [
        FakeEvent(
            text=None,
            final=False,
            function_responses=[FakeFunctionResponse("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True})],
        ),
        FakeEvent(text="unused", final=True),
    ]
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_enforce)
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))
    completed_text = None
    async for event in chat_service.execute_turn_events(session_id, "what is alt command doing?", "api-user"):
        if event.type.value == "message.completed":
            completed_text = event.data["content"]

    assert len(captured_calls) == 1
    assert "Do you mean" not in (completed_text or "")
    assert completed_text == "Alt command shows the current active alarm list."


@pytest.mark.asyncio
async def test_state_hygiene_stale_anchor_cleared_when_fresh_request_finds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 13 -- a confirmed fresh/unrelated request whose OWN fresh
    retrieval genuinely finds nothing must not leave the OLD, unrelated
    anchor sitting in state to potentially mislead a LATER turn's own
    (separately gated) continuity decision."""
    service = ApiSessionService()
    session_id = await service.create_session()

    async def fake_enforce(**kwargs: Any) -> tuple[str, list]:
        return "I could not find governed guidance for that.", []

    async def _side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _with_request_class(
            RequestContract(
                intent=RequestIntent.INFORMATION,
                requested_output=RequestedOutput.FACT,
                subject="alt command",
                continuation=False,
                run_id=run_id,
            )
        )
        delta: dict[str, Any] = {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        delta[LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY] = [_RESTART_KEY.model_dump(mode="json")]
        delta.update(build_active_governed_procedure_state_update(_RESTART_KEY))
        await append_state_delta(session_service, session, delta)

    events = [
        FakeEvent(
            text=None,
            final=False,
            function_responses=[FakeFunctionResponse("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True})],
        ),
        FakeEvent(text="unused", final=True),
    ]
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_enforce)
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))
    async for _event in chat_service.execute_turn_events(session_id, "what is alt command doing?", "api-user"):
        pass

    final_session = await service.get_session(session_id)
    assert final_session.state.get(LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY) is None
    assert final_session.state.get(ACTIVE_GOVERNED_PROCEDURE_STATE_KEY) is None
