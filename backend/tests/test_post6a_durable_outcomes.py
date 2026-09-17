"""POST-6A PROMPT 4 -- focused verification, through production entry points.

Exactly the seven checks the instruction names. Fake providers only; no
live gateway, no real messages.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from backend.approval.execution_identity import (
    EXECUTION_RECORD_STATE_KEY,
    ExecutionStatus,
    derive_operation_id,
    parse_execution_record,
    payload_hash,
    retry_permitted,
)
from backend.approval.schemas import WriteOperation
from backend.gateway.write_envelope import (
    WriteEnvelopeMode,
    WriteEnvelopeOutcome,
    validate_write_envelope,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ===========================================================================
# 1. Indeterminate evidence cannot authorize an operational output
# ===========================================================================


def _contract(**overrides):
    from backend.agents.team_manager.request_contract import (
        RequestContract,
        RequestedOutput,
        RequestIntent,
        RequestParameter,
        TargetConfirmation,
        derive_request_class,
    )

    base = dict(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[
            RequestParameter(
                name="unit_id",
                value="RRU-3",
                provenance="user",
                confirmation=TargetConfirmation.CONFIRMED,
            ),
            RequestParameter(
                name="unit_type",
                value="RRU",
                provenance="user",
                confirmation=TargetConfirmation.CONFIRMED,
            ),
        ],
    )
    base.update(overrides)
    contract = RequestContract(**base)
    return contract.model_copy(
        update={
            "request_class": derive_request_class(
                contract.intent, contract.requested_output, contract.action_requested, contract.subject
            ),
            "run_id": "r1",
        }
    )


def test_indeterminate_evidence_cannot_authorize_an_operational_output() -> None:
    from backend.agents.team_manager.request_execution_policy import derive_execution_decision

    authorized = derive_execution_decision(_contract(), "r1", evidence_authorized=True)
    assert authorized.may_emit_command is True
    assert authorized.may_emit_operational_steps is True

    unproven = derive_execution_decision(_contract(), "r1", evidence_authorized=False)
    assert unproven.may_emit_command is False, "unproven applicability must not authorize a command"
    assert unproven.may_emit_operational_steps is False, "nor operational steps"
    assert unproven.may_execute_action is False


@pytest.mark.asyncio
async def test_shared_evidence_keeps_permitted_and_indeterminate_apart() -> None:
    """Through the real `SharedEvidenceService`: an unconstrained document
    is retrievable but NOT authorized, and the run-scoped authority
    registry says so."""
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import (
        Applicability,
        KnowledgeObject,
        KnowledgeSection,
        KnowledgeSource,
        KnowledgeVersion,
    )
    from backend.knowledge.provenance.service import KnowledgeProvenanceService
    from backend.knowledge.retrieval.service import KnowledgeRetrievalService
    from backend.knowledge.shared_evidence import (
        EvidenceRequest,
        SharedEvidenceService,
        discard_turn_evidence,
        is_version_authorized,
    )

    def _doc(kid: str, label: str, applicability: Applicability) -> KnowledgeObject:
        return KnowledgeObject(
            knowledge_id=kid,
            document_type=KnowledgeDocumentType.OPERATIONAL_PROCEDURE,
            title=kid,
            version=KnowledgeVersion(label=label),
            lifecycle_status=LifecycleStatus.APPROVED,
            source=KnowledgeSource(source_system="local", source_id=kid),
            applicability=applicability,
            sections=[
                KnowledgeSection(section_id=f"{kid}-s1", knowledge_id=kid, sequence=0, content="restart guidance")
            ],
        )

    class _Repo:
        def __init__(self, objects):
            self.objects = {(o.knowledge_id, o.version.label): o for o in objects}

        async def list_all(self):
            return list(self.objects.values())

        async def get(self, kid, label):
            return self.objects.get((kid, label))

    from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin
    from backend.context.domain.models import ContextAssertion, compute_context_state

    context = compute_context_state(
        [
            ContextAssertion(
                assertion_id="c1",
                dimension=ContextDimension.VENDOR,
                kind=AssertionKind.VALUE,
                raw_value="ericsson",
                canonical_value="ERICSSON",
                origin=ContextOrigin.USER,
            )
        ]
    )
    repo = _Repo(
        [
            _doc("MATCH", "1.0", Applicability(dimensions={"vendor": ["ericsson"]})),
            _doc("UNPROVEN", "1.0", Applicability()),
        ]
    )
    service = SharedEvidenceService(
        repo,
        retrieval_service=KnowledgeRetrievalService(repo),
        provenance_service=KnowledgeProvenanceService(repo),
    )
    try:
        result = await service.acquire(
            EvidenceRequest(run_id="run-app", query_text="restart guidance", as_of=_NOW), context
        )
        assert ("MATCH", "1.0") in result.permitted_version_keys
        assert ("UNPROVEN", "1.0") in result.indeterminate_version_keys
        assert ("UNPROVEN", "1.0") not in result.permitted_version_keys
        assert {i.reference.knowledge_id for i in result.authorized_items} == {"MATCH"}
        assert {i.reference.knowledge_id for i in result.indeterminate_items} == {"UNPROVEN"}

        assert is_version_authorized("run-app", "MATCH", "1.0") is True
        assert is_version_authorized("run-app", "UNPROVEN", "1.0") is False
    finally:
        discard_turn_evidence("run-app")
    assert is_version_authorized("run-app", "MATCH", "1.0") is False, "cleared with the turn"


def test_chat_service_withdraws_authority_for_unproven_evidence() -> None:
    import inspect

    from backend.api import chat_service

    source = inspect.getsource(chat_service)
    assert "evidence_authorized = is_version_authorized(" in source
    assert "governed_operation_descriptor = None" in source
    assert "evidence_authorized=evidence_authorized," in source


# ===========================================================================
# 2. Descriptor edit invalidates approval
# ===========================================================================


@pytest.mark.asyncio
async def test_descriptor_edit_invalidates_approval(tmp_path) -> None:
    from backend.api.knowledge_governance_service import KnowledgeGovernanceService
    from backend.knowledge.domain.operation_descriptor import (
        GovernedOperationDescriptor,
        OperationDescriptorAuthority,
        OperationTargetScope,
    )
    from backend.knowledge.governance.operation_approval_store import OperationApprovalStore
    from backend.tests.test_post6a_live_integration import FakeKnowledgeRepository, _doc
    from backend.knowledge.domain.enums import LifecycleStatus

    repository = FakeKnowledgeRepository([_doc("K1", "2.0", LifecycleStatus.APPROVED, "restart the unit")])
    store = OperationApprovalStore(
        f"sqlite+aiosqlite:///{tmp_path / 'edit.db'}",
        governors=frozenset({"governor"}),
        dev_mode=True,
        manage_schema=True,
    )
    service = KnowledgeGovernanceService(repository, store)

    # POST-6A -- drafting is permission-gated now (it withdraws any live
    # approval), so the author is named exactly like the approver.
    await service.author_operation(
        "K1",
        "2.0",
        "2.0-s1",
        GovernedOperationDescriptor(operation_id="op", target_scope=OperationTargetScope.SINGLE_TARGET),
        actor_user_id="governor",
    )
    await service.approve_operation("K1", "2.0", "2.0-s1", actor_user_id="governor")
    assert (await repository.get("K1", "2.0")).sections[0].operation.authority is (
        OperationDescriptorAuthority.APPROVED
    )

    # THE EDIT.
    await service.author_operation(
        "K1",
        "2.0",
        "2.0-s1",
        GovernedOperationDescriptor(operation_id="op", target_scope=OperationTargetScope.TARGET_INDEPENDENT),
        actor_user_id="governor",
    )
    edited = await repository.get("K1", "2.0")
    assert edited.sections[0].operation.authority is OperationDescriptorAuthority.CANDIDATE
    assert await store.active_approval_for(edited, "2.0-s1") is None
    rows = await store.list_approvals("K1")
    assert rows and rows[0].revoked is True, "the stored approval is explicitly revoked, not merely mismatched"
    await store.close()


def test_governance_mutations_fail_closed_without_dev_mode(tmp_path) -> None:
    from backend.knowledge.governance.operation_approval_store import (
        GovernanceUnavailableError,
        OperationApprovalStore,
    )

    store = OperationApprovalStore(
        f"sqlite+aiosqlite:///{tmp_path / 'closed.db'}", governors=frozenset({"governor"})
    )
    with pytest.raises(GovernanceUnavailableError):
        store.require_governance_permission("governor")


# ===========================================================================
# 3. Unrelated follow-up does not advance an investigation
# ===========================================================================


def _awaiting():
    from backend.agents.team_manager.investigation_state import (
        InvestigationState,
        PrerequisiteStatus,
        record_requested_evidence,
    )

    return record_requested_evidence(
        InvestigationState(objective="HW Partial Fault", prerequisite_status=PrerequisiteStatus.SATISFIED),
        "the output of the status check",
    )


@pytest.mark.parametrize(
    "message",
    [
        "yes",
        "what does that command actually do?",
        "actually forget it, cancel that",
        "no, I meant the other RRU",
        "separately -- how do I check optical levels?",
    ],
)
def test_unrelated_follow_up_does_not_advance_the_investigation(message: str) -> None:
    from backend.agents.incident_manager.schemas import ObservationInterpretation
    from backend.agents.team_manager.investigation_state import (
        StepLifecycle,
        may_advance_to_next_step,
        record_candidate_observation,
        record_validated_interpretation,
    )

    state = record_candidate_observation(_awaiting(), message, source_turn_id="inv-1")
    # Held as a CANDIDATE only -- the step has not moved.
    assert state.step_lifecycle is StepLifecycle.SUGGESTED
    assert state.received_observation is None
    assert state.is_awaiting_evidence is True

    # A specialist that tries to interpret output nobody reported is
    # rejected: there is nothing in these messages to quote.
    updated, reason = record_validated_interpretation(
        state,
        ObservationInterpretation(
            observation_reference="status: DEGRADED", meaning="still faulty", concludes_step=True
        ),
    )
    assert reason == "observation_reference_not_found"
    assert may_advance_to_next_step(updated) is False


def test_a_real_reported_result_still_advances() -> None:
    from backend.agents.incident_manager.schemas import ObservationInterpretation
    from backend.agents.team_manager.investigation_state import (
        StepLifecycle,
        advance_after_observation,
        may_advance_to_next_step,
        record_candidate_observation,
        record_validated_interpretation,
    )

    state = record_candidate_observation(
        _awaiting(), "ran it -- status: DEGRADED rc=3", source_turn_id="inv-2"
    )
    observed, reason = record_validated_interpretation(
        state,
        ObservationInterpretation(
            observation_reference="status: DEGRADED", meaning="The unit is still faulty.", concludes_step=True
        ),
    )
    assert reason == "accepted"
    assert observed.step_lifecycle is StepLifecycle.OBSERVED
    assert observed.received_observation == "ran it -- status: DEGRADED rc=3"
    assert may_advance_to_next_step(observed) is True
    assert advance_after_observation(observed).step_index == 1


# ===========================================================================
# 4. Failure during finalization produces a terminal outcome
# ===========================================================================


@pytest.mark.asyncio
async def test_finalization_failure_produces_a_terminal_outcome(monkeypatch) -> None:
    """Through the REAL streaming turn: a lifecycle-persistence failure at
    finalization must produce a terminal error event, never a silent
    `message.completed`."""
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_lifecycle import TURN_LIFECYCLE_STATE_KEY, TurnStatus, parse_turn_lifecycle
    from backend.tests._api_fakes import FakeEvent, FakeRunner

    service = ApiSessionService()
    session_id = await service.create_session()
    chat = ChatService(service, runner=FakeRunner(service, events=[FakeEvent(text="hello", final=True)]))

    original = ChatService._record_turn_status
    calls: list[str] = []

    async def _failing(self, session_id_, user_id_, turn_key, status, detail=""):
        calls.append(status.value)
        if status is TurnStatus.COMPLETED:
            return False  # storage unavailable exactly at finalization
        return await original(self, session_id_, user_id_, turn_key, status, detail)

    monkeypatch.setattr(ChatService, "_record_turn_status", _failing)

    events = [e async for e in chat.execute_turn_events(session_id, "hi")]
    kinds = [e.type.value for e in events]
    assert "message.completed" not in kinds, "success must not be announced when it could not be recorded"
    assert "error" in kinds
    completed = [e for e in events if e.type.value == "run.completed"]
    assert completed and completed[0].data["outcome"] == "error"
    assert TurnStatus.COMPLETED.value in calls


@pytest.mark.asyncio
async def test_an_interrupted_turn_is_reconciled_not_lost() -> None:
    from backend.api.turn_lifecycle import (
        TURN_LIFECYCLE_STATE_KEY,
        TurnStatus,
        build_turn_lifecycle_delta,
        parse_turn_lifecycle,
        reconcile_interrupted_turns,
    )

    accepted = build_turn_lifecycle_delta(None, "run-A", TurnStatus.ACCEPTED, worker_id="worker-1")
    # Turn A never reached a terminal state; the SAME worker now starts
    # turn B, so turn A is demonstrably abandoned.
    reconciled = await reconcile_interrupted_turns(
        accepted[TURN_LIFECYCLE_STATE_KEY], current_turn_key="run-B", worker_id="worker-1"
    )
    records = parse_turn_lifecycle(reconciled[TURN_LIFECYCLE_STATE_KEY])
    assert records["run-A"].status is TurnStatus.INTERRUPTED
    assert records["run-A"].is_terminal is True

    # ANOTHER worker's active turn is left completely alone: it may be
    # running right now, and declaring it interrupted would be a
    # fabrication about someone else's state. With no ownership probe
    # there is no evidence either way, and "we do not know" must never be
    # recorded as "it was interrupted".
    others = await reconcile_interrupted_turns(
        accepted[TURN_LIFECYCLE_STATE_KEY], current_turn_key="run-B", worker_id="worker-2"
    )
    assert others == {}, "a different worker's ACCEPTED turn must not be touched"

    # A terminal outcome is never rewritten by a late duplicate write.
    completed = build_turn_lifecycle_delta(
        reconciled[TURN_LIFECYCLE_STATE_KEY], "run-A", TurnStatus.COMPLETED
    )
    assert parse_turn_lifecycle(completed[TURN_LIFECYCLE_STATE_KEY])["run-A"].status is (
        TurnStatus.INTERRUPTED
    )


# ===========================================================================
# 5. HTTP 200 business failure is not reported as executed
# ===========================================================================


@pytest.mark.parametrize(
    "body",
    [
        {"success": False},
        {"success": False, "chatId": "c1"},
        {"error": {"code": "Forbidden", "message": "no access"}},
        "not-an-object",
    ],
)
def test_http_200_business_failure_is_not_executed(body: Any) -> None:
    envelope = validate_write_envelope(WriteOperation.TEAMS_CREATE_CHAT.value, body)
    assert envelope.executed is False
    assert envelope.outcome in (WriteEnvelopeOutcome.BUSINESS_FAILURE, WriteEnvelopeOutcome.MALFORMED)


@pytest.mark.asyncio
async def test_execute_does_not_consume_the_proposal_on_a_200_business_failure(monkeypatch) -> None:
    """Through the REAL execution service and the REAL write tool."""
    from backend.api import execution_service
    from backend.api.session_service import ApiSessionService
    from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY
    from backend.gateway.safe_error import SafeErrorException
    from backend.tests.test_api_execution_endpoints import (  # reuse the real fixtures
        FakeResponse,
        _approved_send_message,
        _install_gateway,
    )

    _install_gateway(monkeypatch, FakeResponse(200, {"success": False, "error": {"code": "Forbidden"}}))
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id, chat_id="c1", message="Hi")

    with pytest.raises(SafeErrorException):
        await execution_service.execute(service, session_id, proposal_id)

    refreshed = await service.get_session(session_id)
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] != "consumed"
    record = parse_execution_record(refreshed.state.get(EXECUTION_RECORD_STATE_KEY))
    assert record is not None and record.status is ExecutionStatus.FAILED


# ===========================================================================
# 6. Ambiguous timeout preserves identity and blocks blind resend
# ===========================================================================


def test_operation_identity_is_stable_and_bound() -> None:
    payload = {"chatId": "c1", "message": "Hi"}
    a = derive_operation_id("p1", "teams.sendMessage", "c1", payload)
    b = derive_operation_id("p1", "teams.sendMessage", "c1", dict(reversed(list(payload.items()))))
    assert a == b, "the same operation always derives the same id, across attempts"

    assert derive_operation_id("p2", "teams.sendMessage", "c1", payload) != a
    assert derive_operation_id("p1", "teams.sendMessage", "c2", payload) != a
    assert derive_operation_id("p1", "teams.sendMessage", "c1", {**payload, "message": "Bye"}) != a


@pytest.mark.asyncio
async def test_ambiguous_timeout_records_unknown_outcome_and_blocks_resend(monkeypatch) -> None:
    import requests

    from backend.api import execution_service
    from backend.api.session_service import ApiSessionService
    from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY
    from backend.gateway.safe_error import SafeErrorException
    from backend.tests.test_api_execution_endpoints import _approved_send_message, _install_gateway

    def _timeout(*_a, **_kw):
        raise requests.exceptions.Timeout()

    monkeypatch.setattr("backend.gateway.power_automate_client.requests.post", _timeout)
    monkeypatch.setenv("SLOPANOC_POWER_AUTOMATE_GATEWAY_URL", "https://example.invalid/flow")

    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id, chat_id="c1", message="Hi")

    with pytest.raises(SafeErrorException):
        await execution_service.execute(service, session_id, proposal_id)

    refreshed = await service.get_session(session_id)
    record = parse_execution_record(refreshed.state.get(EXECUTION_RECORD_STATE_KEY))
    assert record is not None
    assert record.status is ExecutionStatus.UNKNOWN_OUTCOME
    assert record.operation_id  # identity preserved for reconciliation
    # The proposal is NOT consumed -- we cannot claim it executed...
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] != "consumed"
    # ...and it is NOT retryable either, because it may already have sent.
    assert retry_permitted(record) is False

    with pytest.raises(SafeErrorException):
        await execution_service.execute(service, session_id, proposal_id)


def test_no_exactly_once_claim_without_gateway_support() -> None:
    from backend.approval.execution_identity import ExecutionRecord

    ambiguous = ExecutionRecord(
        operation_id="op",
        proposal_id="p1",
        operation="teams.sendMessage",
        destination="c1",
        payload_hash=payload_hash({"chatId": "c1"}),
        status=ExecutionStatus.UNKNOWN_OUTCOME,
    )
    assert retry_permitted(ambiguous) is False
    assert retry_permitted(ambiguous, gateway_deduplication_available=True) is True


# ===========================================================================
# 7. Repeated execution of a completed proposal does not dispatch again
# ===========================================================================


@pytest.mark.asyncio
async def test_repeated_execution_of_a_completed_proposal_does_not_dispatch(monkeypatch) -> None:
    from backend.api import execution_service
    from backend.api.session_service import ApiSessionService
    from backend.gateway.safe_error import SafeErrorException
    from backend.tests.test_api_execution_endpoints import (
        FakeResponse,
        _approved_send_message,
        _install_gateway,
    )

    spy = _install_gateway(monkeypatch, FakeResponse(200, {"messageId": "m-1"}))
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id, chat_id="c1", message="Hi")

    first = await execution_service.execute(service, session_id, proposal_id)
    assert first.result == "executed"
    dispatches_after_first = len(spy.calls)

    with pytest.raises(SafeErrorException):
        await execution_service.execute(service, session_id, proposal_id)

    assert len(spy.calls) == dispatches_after_first, "a consumed proposal must never dispatch again"
