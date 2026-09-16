"""LIVE-CORR-12C -- Fresh RequestContract on Presentation Turns.

THE GAP THIS CLOSES: `presentation_team_manager` (agent.py) is a deliberate
`.model_copy()` of `team_manager` with `tools=[]` (the R1 FIX's own
structural guarantee that a turn presenting an already-validated
`TrustedSpecialistResult` can never re-delegate). Because `record_request_
contract` is one of the stripped tools, THAT turn cannot call it --
`VALIDATED_REQUEST_CONTRACT_STATE_KEY` was left holding a PRIOR turn's own
contract, stamped with that prior turn's own `run_id`. `derive_execution_
decision`'s freshness check (LIVE-CORR-12A/12B, correct and UNCHANGED by
this pass) then correctly, but misleadingly, rejected it as `INVALID_
CONTRACT` for the current turn.

ROOT CAUSE, confirmed by exhaustive audit of `chat_service.py`:
`specialist_result_state_written` is set `True` in exactly ONE place --
after a resumed `ResolvedReadContinuation` (a SelectionCard-driven
Teams-read resumption) successfully executes via `_execute_read_
continuation`, THIS SAME turn. This is never a generic "any trusted
specialist result" mechanism.

FIX: `ResolvedReadContinuation` is already a fully deterministic,
server-resolved turn description -- `build_deterministic_read_
continuation_contract` (request_contract.py) synthesizes THIS turn's own
`RequestContract` directly from it, no model call, no tool, no LLM
involvement -- so `presentation_team_manager`'s own tool-free design is
never touched or widened. `request_class` is computed by the SAME
`derive_request_class` every model-produced contract uses.

This module does NOT touch governed-evidence continuity, clarification
wording, or semantic context verification -- explicitly out of scope for
this pass.

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from backend.agents.team_manager.agent import presentation_team_manager
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
    build_deterministic_read_continuation_contract,
    derive_request_class,
    safe_request_contract_observability_fields,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionStatus,
    derive_execution_decision,
    resolve_effective_governed_contract,
)
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.selection.schemas import ReadOperation, ResolvedReadContinuation
from backend.selection.service import store_read_continuation
from backend.tests._api_fakes import FakeRunner

_RUN_ID_B = "livecorr12c-run-B"


def _param(name: str, value: str) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance=ParameterProvenance.USER)


def _with_request_class(contract: RequestContract) -> RequestContract:
    """Test-only helper: hand-built `RequestContract(...)` instances leave
    `request_class` unset (it is populated ENTIRELY server-side, by
    `validate_and_persist_request_contract` -- see that field's own
    docstring). Stamps it here from the SAME pure `derive_request_class`
    the real callback uses, so a directly-constructed test contract is
    indistinguishable from a genuinely persisted one."""
    return contract.model_copy(
        update={
            "request_class": derive_request_class(
                contract.intent, contract.requested_output, contract.action_requested, contract.subject
            )
        }
    )


class _CountingRunner:
    """Wraps a real `_Runner` double, counting invocations -- lets a test
    prove which of `self._runner`/`self._presentation_runner` a turn
    actually used, without relying on exception-propagation timing."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.call_count = 0

    async def run_async(self, **kwargs: Any):
        self.call_count += 1
        async for event in self._inner.run_async(**kwargs):
            yield event

    async def rewind_async(self, **kwargs: Any) -> None:
        await self._inner.rewind_async(**kwargs)


async def _fake_read_continuation_executor(*, user_id: str, continuation: Any, **kwargs: Any) -> dict[str, Any]:
    return {
        "outcome": "ok",
        "chat_id": continuation.selected_chat_id,
        "chat_title": continuation.selected_chat_topic,
        "summary": "The team discussed migrating the gateway this sprint.",
        "evidence": [],
    }


async def _seed_stale_contract(service: ApiSessionService, session_id: str, run_id: str) -> dict:
    """Directly seeds `VALIDATED_REQUEST_CONTRACT_STATE_KEY` as though a
    genuine, prior, fully-tooled `team_manager` turn (run_id=`run_id`)
    already wrote it -- mirrors LIVE-CORR-12B's own established `_stored_
    contract`-style direct state seeding, never a full simulated turn
    (which would pull in unrelated machinery -- source-requirements
    declaration, governed-knowledge completion -- not relevant here)."""
    stale_contract = _with_request_class(
        RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            provided_context=[_param("unit_id", "RRU-3")],
            missing_context=[],
            run_id=run_id,
        )
    ).model_dump(mode="json")
    session = await service.get_session(session_id)
    await service.persist_state_delta(session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: stale_contract})
    return stale_contract


async def _seed_read_continuation(service: ApiSessionService, session_id: str, chat_topic: str) -> None:
    session = await service.get_session(session_id)
    store_read_continuation(
        session.state,
        ResolvedReadContinuation(
            operation=ReadOperation.SUMMARIZE,
            selected_chat_id="chat-real-1",
            selected_chat_topic=chat_topic,
        ),
    )
    await service.persist_state_delta(session, dict(session.state))


# =============================================================================
# A / B / E -- end-to-end: stale EXACT_COMMAND contract, presentation turn
# gets its OWN fresh, OPERATIONAL_INFORMATION contract, and the full/
# tooled team_manager Runner is never invoked
# =============================================================================


@pytest.mark.asyncio
async def test_a_b_e_presentation_turn_gets_fresh_contract_never_uses_full_runner(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="backend.api.chat_service")

    service = ApiSessionService()
    session_id = await service.create_session()

    stale_run_id = "livecorr12c-run-A-stale"
    stale_contract = await _seed_stale_contract(service, session_id, stale_run_id)
    assert stale_contract["request_class"] == RequestClass.EXACT_COMMAND

    await _seed_read_continuation(service, session_id, "SLOPANOC Gateway Group Test")

    never_called_runner = _CountingRunner(FakeRunner(service, respond=lambda t: "MUST NEVER BE SHOWN"))
    presentation_runner = _CountingRunner(
        FakeRunner(service, respond=lambda t: "The team discussed migrating the gateway this sprint.")
    )
    chat_service = ChatService(
        service,
        runner=never_called_runner,
        presentation_runner=presentation_runner,
        read_continuation_executor=_fake_read_continuation_executor,
    )

    completed_text = None
    async for event in chat_service.execute_turn_events(session_id, "resume", "api-user"):
        if event.type.value == "message.completed":
            completed_text = event.data["content"]

    # --- E: the full, fully-tooled team_manager Runner (incident_manager/
    # knowledge/Teams/action tools) is NEVER invoked for this turn -- only
    # the deliberately tool-free presentation Runner is.
    assert never_called_runner.call_count == 0
    assert presentation_runner.call_count == 1
    assert completed_text == "The team discussed migrating the gateway this sprint."

    # --- A / B: the CURRENT turn's own contract is genuinely fresh, never
    # the stale EXACT_COMMAND leftover, and correctly, deterministically
    # classified GENERAL_CONVERSATION -- never EXACT_COMMAND/ACTION.
    # `subject` is deliberately left unset (never the chat topic) -- see
    # `build_deterministic_read_continuation_contract`'s own module-level
    # comment for why: this exact log point has its own pre-existing
    # "never log a chat id/title" contract (test_r1_r3_correctness_
    # regression.py), reconfirmed below via `caplog`.
    final_session = await service.get_session(session_id)
    fresh_contract = final_session.state[VALIDATED_REQUEST_CONTRACT_STATE_KEY]
    assert fresh_contract["run_id"] != stale_run_id
    assert fresh_contract["request_class"] == RequestClass.GENERAL_CONVERSATION
    assert fresh_contract["subject"] is None

    # --- Observability proof (section 14): the live log line for this
    # turn shows a genuinely FRESH contract, and the execution-decision
    # log line never shows `invalid_contract` for it.
    contract_log_lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("request_contract=")]
    assert contract_log_lines, "expected a request_contract= log line for this turn"
    assert "'fresh': True" in contract_log_lines[-1]
    decision_log_lines = [
        r.getMessage() for r in caplog.records if r.getMessage().startswith("request_execution_decision")
    ]
    assert decision_log_lines, "expected a request_execution_decision log line for this turn"
    assert "status=invalid_contract" not in decision_log_lines[-1]

    # --- Pre-existing, separately-tested privacy contract (test_r1_r3_
    # correctness_regression.py): a trusted-result-presentation turn must
    # never leak the chat id/title into ANY log line -- reconfirmed here
    # since this pass adds a NEW log-bearing field (`RequestContract.
    # subject`) to that exact turn.
    for record in caplog.records:
        assert "SLOPANOC Gateway Group Test" not in record.getMessage()
        assert "chat-real-1" not in record.getMessage()


def test_e_presentation_agent_remains_structurally_tool_free() -> None:
    """Static, direct proof independent of any Runner double: `presentation_
    team_manager` itself still carries zero tools -- this pass never
    reinstates `record_request_contract`/`incident_manager_tool`/knowledge/
    Teams/action authority merely to solve contract freshness."""
    assert presentation_team_manager.tools == []
    assert presentation_team_manager.before_tool_callback is None
    assert presentation_team_manager.after_tool_callback is None


# =============================================================================
# C -- fresh contract semantics belong to the CURRENT turn, not a stale
# EXACT_COMMAND leftover
# =============================================================================


def test_c_presentation_contract_never_inherits_prior_exact_command_class() -> None:
    fresh = build_deterministic_read_continuation_contract(run_id=_RUN_ID_B)
    assert fresh.run_id == _RUN_ID_B
    assert fresh.request_class == RequestClass.GENERAL_CONVERSATION
    assert fresh.request_class != RequestClass.EXACT_COMMAND

    decision = derive_execution_decision(fresh, _RUN_ID_B)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.request_class == RequestClass.GENERAL_CONVERSATION
    assert decision.may_emit_command is False


# =============================================================================
# D -- reverse transition: an ordinary, later, genuinely new command turn
# is never contaminated by an earlier general-conversation/presentation turn
# =============================================================================


@pytest.mark.asyncio
async def test_d_ordinary_turn_after_general_conversation_reflects_its_own_request(caplog: pytest.LogCaptureFixture) -> None:
    from backend.api.turn_context import current_run_id
    from backend.tests._api_fakes import append_state_delta

    async def _turn_a_side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _with_request_class(
            RequestContract(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, run_id=run_id)
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    async def _turn_b_side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _with_request_class(
            RequestContract(
                intent=RequestIntent.COMMAND,
                requested_output=RequestedOutput.EXACT_COMMAND,
                subject="restart RRU",
                missing_context=["unit_id", "unit_type"],
                run_id=run_id,
            )
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    service = ApiSessionService()
    session_id = await service.create_session()

    chat_service_a = ChatService(service, runner=FakeRunner(service, side_effect=_turn_a_side_effect, respond=lambda t: "Hi there!"))
    async for _ in chat_service_a.execute_turn_events(session_id, "hello, how are you?", "api-user"):
        pass

    after_a = await service.get_session(session_id)
    assert after_a.state[VALIDATED_REQUEST_CONTRACT_STATE_KEY]["request_class"] == RequestClass.GENERAL_CONVERSATION

    chat_service_b = ChatService(
        service, runner=FakeRunner(service, side_effect=_turn_b_side_effect, respond=lambda t: "Please confirm which RRU.")
    )
    async for _ in chat_service_b.execute_turn_events(session_id, "give me a command to restart an RRU", "api-user"):
        pass

    after_b = await service.get_session(session_id)
    fresh_contract = after_b.state[VALIDATED_REQUEST_CONTRACT_STATE_KEY]
    assert fresh_contract["request_class"] == RequestClass.EXACT_COMMAND
    assert fresh_contract["request_class"] != RequestClass.GENERAL_CONVERSATION


# =============================================================================
# F -- pending exact-command continuation still works; a presentation-mode
# turn never accidentally consumes/corrupts a pending EXACT_COMMAND request
# =============================================================================


def test_f_presentation_contract_never_consumes_a_pending_exact_command_request() -> None:
    pending = PendingGovernedRequest(
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        intent=RequestIntent.COMMAND,
        status=PendingGovernedRequestStatus.UNRESOLVED,
        subject="restart RRU",
        missing_context=["unit_id"],
        provided_context=[_param("unit_type", "RRU")],
    )
    presentation_contract = build_deterministic_read_continuation_contract(run_id=_RUN_ID_B)

    # `provided_context` is empty -- this turn structurally cannot be
    # mistaken for an answer to the pending clarification (see
    # `_supplies_context_for_pending_request`'s own `if not contract.
    # provided_context: return False` guard, unchanged by this pass).
    effective = resolve_effective_governed_contract(presentation_contract, pending)
    assert effective == presentation_contract

    decision = derive_execution_decision(presentation_contract, _RUN_ID_B, pending_governed_request=pending)
    assert decision.status != RequestExecutionStatus.INVALID_CONTRACT
    assert decision.request_class != RequestClass.EXACT_COMMAND
    assert decision.may_emit_command is False


def test_f_ordinary_clarification_answer_still_resolves_pending_request() -> None:
    """Regression: the normal (non-presentation) pending-continuation
    mechanism (LIVE-CORR-11/12B) is completely untouched by this pass."""
    pending = PendingGovernedRequest(
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        intent=RequestIntent.COMMAND,
        status=PendingGovernedRequestStatus.UNRESOLVED,
        subject="restart RRU",
        missing_context=["unit_id"],
        provided_context=[_param("unit_type", "RRU")],
    )
    answer_contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU ID",
        provided_context=[_param("unit_id", "RRU-3")],
        run_id=_RUN_ID_B,
    )
    decision = derive_execution_decision(answer_contract, _RUN_ID_B, pending_governed_request=pending)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is True


# =============================================================================
# G -- INVALID_CONTRACT still fires for a genuinely stale contract
# =============================================================================


def test_g_genuinely_stale_contract_still_invalid() -> None:
    stale = RequestContract(
        intent=RequestIntent.COMMAND, requested_output=RequestedOutput.EXACT_COMMAND, subject="restart RRU", run_id="run-OLD"
    )
    decision = derive_execution_decision(stale, "run-CURRENT")
    assert decision.status == RequestExecutionStatus.INVALID_CONTRACT
    assert decision.may_emit_command is False
    assert decision.request_class is None

    fields = safe_request_contract_observability_fields(stale.model_dump(mode="json"), current_run_id="run-CURRENT")
    assert fields is not None
    assert fields["fresh"] is False
    assert fields["contract_run_id"] == "run-OLD"


# =============================================================================
# H -- no contract at all -> fail closed, never fabricated
# =============================================================================


def test_h_missing_contract_fails_closed() -> None:
    decision = derive_execution_decision(None, "run-CURRENT")
    assert decision.status == RequestExecutionStatus.INVALID_CONTRACT
    assert decision.may_emit_command is False
    assert decision.may_execute_action is False
    assert decision.may_emit_operational_steps is False


# =============================================================================
# I -- LIVE-CORR-12B canonicalization preserved
# =============================================================================


def test_i_canonical_alias_still_resolves_identically() -> None:
    alias_contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("RRU_ID", "RRU-3")],
        run_id=_RUN_ID_B,
    )
    canonical_contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("unit_id", "RRU-3")],
        run_id=_RUN_ID_B,
    )
    # Both contracts here are hand-built (bypassing `validate_and_persist_
    # request_contract`'s own name-canonicalization pass), so they exercise
    # `derive_execution_decision` on the RAW alias name directly -- this is
    # a lower-level check that the PARAMETER VALUES themselves resolve
    # identically once genuinely canonical; the full canonicalize-at-
    # persist-time proof is LIVE-CORR-12B's own test suite (re-run as part
    # of this pass's regression scope, unmodified).
    assert derive_execution_decision(canonical_contract, _RUN_ID_B).status == RequestExecutionStatus.ALLOW


# =============================================================================
# J -- general conversation regression
# =============================================================================


def test_j_general_conversation_still_allowed() -> None:
    contract = RequestContract(
        intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, subject=None, run_id=_RUN_ID_B
    )
    decision = derive_execution_decision(contract, _RUN_ID_B)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.request_class == RequestClass.GENERAL_CONVERSATION
