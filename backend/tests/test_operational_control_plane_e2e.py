"""Tranche 3 end-to-end: real ChatService + real TechnicalAuthorityAgentTool (scripted LLMs),
then the real trusted approve / reject / execute service functions the HTTP routes call.

State change:  SELECTED -> ProcedureAction -> resolver -> Command Authority (target gate)
               -> Policy (clarification) -> operator target confirmation -> Command Authority
               -> Policy (approval_required) -> operator approval -> READY_FOR_EXECUTION (no execution).
Diagnostic read: SELECTED -> ProcedureAction -> Command Authority -> Policy (allowed)
               -> operator-requested controlled read via a TEST adapter -> observed evidence
               -> troubleshooting state -> next TAE evaluation sees it.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.api import approval_service, execution_service, operational_execution_service
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY, load_active_proposal
from backend.gateway.safe_error import SafeErrorException
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.operations import control_plane as cp
from backend.operations.execution import AdapterOutput, AuthorizedReadAction, ExecutionAdapter, ExecutionContext, get_execution_adapter_registry
from backend.operations.targets import TARGET_CONFIRMATIONS_STATE_KEY, confirmation_mismatch, load_target_confirmation
from backend.tests.test_ess_service_unavailable_e2e_verification import _fc, isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_procedure_action_tae_e2e import _Session
from backend.tools.knowledge.diagnostic_trace import RETRIEVAL_DIAGNOSTICS_STATE_KEY, format_diagnostic_trace

_KID = "KID-NODE-OPS"
_SEC = "sec-0001"
_CANONICAL = f"{_KID}:v1:{_SEC}"
_CONTENT = "Check the node alarm state: `show foo <target>`\nRecovery: `acc <mo> restart`\n"
_ACTIONS = {a.command_template: a.action_id for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=_CONTENT)[0]}
_READ_ID = _ACTIONS["show foo <target>"]
_WRITE_ID = _ACTIONS["acc <mo> restart"]
_SEARCH = _fc("knowledge_search", {"query_text": "node alarm state recovery"})
_SELECT = _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": _KID, "version_label": "v1", "section_id": _SEC}]})
_CATALOG = _fc("procedure_action_catalog", {})
_USER_WRITE = "PlugInUnit-3 is stuck after the fault. What is the recovery action?"
_USER_READ = "Node NODE-1 raised a node alarm. What should I check next?"


def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID,
        document_type=KnowledgeDocumentType.MOP,
        title="Node Operations Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id="MOP_Ops.docx", display_name="Ops MOP"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading="Node alarm", sequence=0, content=_CONTENT, source_locator="lines:1-2")],
    )


def _recommend(action_id: str, name: str, value: str, extra: Optional[dict[str, Any]] = None) -> list[types.Part]:
    payload: dict[str, Any] = {
        "outcome": "recommended",
        "technical_interpretation": "Governed procedure step selected.",
        "verified_evidence_citations": [],
        "diagnostic_step": {
            "action": "Perform the governed procedure step",
            "reason": "The governed procedure prescribes this step for the observed fault.",
            "expected_evidence": "Command output",
            "command": None,
            "command_source": None,
            "restrictions": [],
            "procedure_action_id": action_id,
            "parameter_values": [{"name": name, "value": value}],
        },
    }
    payload.update(extra or {})
    return [types.Part.from_text(text=json.dumps(payload))]


async def _state(session: _Session) -> dict[str, Any]:
    return dict((await session.session_service.get_session(session.session_id, "eng")).state)


class SpyAdapter(ExecutionAdapter):
    adapter_type = "test-spy"

    def __init__(self, output: str = "alarm state: CLEARED") -> None:
        self.calls: list[AuthorizedReadAction] = []
        self.output = output

    async def _execute_read(self, action: AuthorizedReadAction, context: ExecutionContext) -> AdapterOutput:
        self.calls.append(action)
        return AdapterOutput(stdout=self.output)


@pytest.fixture
def spy_registry(monkeypatch):
    registry = get_execution_adapter_registry()
    adapter = SpyAdapter()
    registry.register("test-sandbox", adapter)
    monkeypatch.setenv("SLOPANOC_DIAGNOSTIC_EXECUTION_CONTEXT", "test-sandbox")
    yield adapter
    registry.unregister("test-sandbox")


async def _diagnose(session: _Session, value: str = "PlugInUnit-3") -> None:
    """Remediation gate: the fault thread first needs a completed, operator-observed diagnostic."""
    no_step = [types.Part.from_text(text=json.dumps({"outcome": "insufficient_evidence", "technical_interpretation": "Observed.", "missing_information": []}))]
    await session.turn([_SEARCH, _SELECT, _CATALOG, _recommend(_READ_ID, "target", value)], f"Run show foo {value}.", user_text=f"{value} raised an alarm. What should I check?")
    await session.turn([_SEARCH, _SELECT, no_step], "Noted.", user_text=f"show foo {value}\n{value} alarm state: ACTIVE")
    session.diagnosed = True  # type: ignore[attr-defined]


async def _write_turn(session: _Session, value: str = "PlugInUnit-3", extra=None, tm: str = "Run acc PlugInUnit-3 restart now.", user: str = _USER_WRITE) -> dict[str, Any]:
    if not getattr(session, "diagnosed", False):
        await _diagnose(session)
    return await session.turn([_SEARCH, _SELECT, _CATALOG, _recommend(_WRITE_ID, "mo", value, extra)], tm, user_text=user)


def _control(state: dict[str, Any]) -> cp.OperationalControlRecord:
    controls = state[cp.OPERATIONAL_CONTROLS_STATE_KEY]
    return cp.OperationalControlRecord.model_validate(list(controls.values())[-1])


# --- T (+C, D, G, I, K, S): exact end-to-end state-change control flow ------------------------------
@pytest.mark.asyncio
async def test_t_state_change_flow_ends_at_ready_for_execution_without_execution(isolated_km_repo, monkeypatch, spy_registry) -> None:
    await isolated_km_repo.add(_mop())
    session = _Session(monkeypatch)
    result = await _write_turn(session)

    # C: authorized-pending-target, policy clarification, no command, no approval request yet.
    record = result["records"][-1]
    assert record["diagnostic_step"]["command"] is None
    control_summary = record["operational_control"]
    assert control_summary["control_stage"] == "awaiting_confirmation"
    assert control_summary["policy_decision"] == "clarification_required"
    assert control_summary["reason_codes"] == ["STATE_CHANGE", "TARGET_NOT_CONFIRMED"]
    assert "acc PlugInUnit-3 restart" not in result["final"]
    state = result["state"]
    confirm = load_active_proposal(state)
    assert confirm.operation.value == "operational.confirmTarget" and confirm.status.value == "pending"
    assert TARGET_CONFIRMATIONS_STATE_KEY not in state
    history = state["troubleshooting_state"]["diagnostic_history"][-1]
    assert (history["check_id"], history["control_stage"], history["procedure_action_id"]) == (control_summary["check_id"], "awaiting_confirmation", _WRITE_ID)
    trace = list(state[RETRIEVAL_DIAGNOSTICS_STATE_KEY].values())[-1]
    assert any(c["decision"] == "preview_authorized_if_target_confirmed" for c in trace["command_authority"])
    assert not any(c["decision"] == "authorized" for c in trace["command_authority"])
    text = format_diagnostic_trace(trace)
    assert "POLICY decision=clarification_required" in text and "TARGET required=True confirmed=False" in text

    # D: explicit operator target confirmation through the trusted endpoint service.
    response = await approval_service.approve(session.session_service, session.session_id, confirm.proposal_id, "eng")
    state = await _state(session)
    control = _control(state)
    confirmation = load_target_confirmation(state, control.confirmation_id)
    assert confirmation.confirmed_by == "eng" and confirmation.target.canonical_identifier == "PlugInUnit-3"
    assert confirmation_mismatch(confirmation, control.context) is None

    # G: deterministic ApprovalRequest generated from trusted state.
    assert control.stage is cp.ControlStage.AWAITING_APPROVAL
    card = response.pending_action
    assert card.operation == "operational.procedureAction" and card.status == "pending"
    details = card.operational
    assert (details.kind, details.command, details.risk, details.target, details.source_id) == (
        "approval", "acc PlugInUnit-3 restart", "state_changing", "PlugInUnit-3", _CANONICAL
    )
    assert details.confirmed_by == "eng" and details.reason and details.restrictions

    # I: real deterministic approval with approver identity.
    approved = await approval_service.approve(session.session_service, session.session_id, card.proposal_id, "eng")
    assert approved.result == "approved" and approved.pending_action.operational.approval_status == "approved"
    state = await _state(session)
    control = _control(state)
    assert control.approval_status is cp.OperationalApprovalStatus.APPROVED and control.approved_by == "eng"

    # K: READY_FOR_EXECUTION, eligibility holds, and nothing executes.
    assert control.stage is cp.ControlStage.READY_FOR_EXECUTION
    eligibility = await cp.evaluate_execution_eligibility(state, control.context.control_id)
    assert eligibility.eligible, eligibility.reason_codes
    with pytest.raises(SafeErrorException) as refused:
        await execution_service.execute(session.session_service, session.session_id, card.proposal_id, "eng")
    assert refused.value.safe_error.reason == "state_change_execution_not_enabled"
    assert spy_registry.calls == []
    with pytest.raises(cp.OperationalDenial):
        await cp.execute_read_check(state, control.context.check_id, user_id="eng")
    assert spy_registry.calls == []

    # S: audit trail with actors and identities.
    events = [(e.event, e.actor) for e in control.audit]
    assert events == [
        ("policy_evaluated", "system"),
        ("target_confirmation_requested", "system"),
        ("target_confirmed", "eng"),
        ("approval_requested", "system"),
        ("approved", "eng"),
        ("execution_not_started", "system"),
    ]
    assert state["troubleshooting_state"]["diagnostic_history"][-1]["control_stage"] == "ready_for_execution"


# --- H: the model cannot approve or confirm ------------------------------------------------------
@pytest.mark.asyncio
async def test_h_model_claims_of_approval_create_no_trusted_state(isolated_km_repo, monkeypatch, spy_registry) -> None:
    await isolated_km_repo.add(_mop())
    session = _Session(monkeypatch)
    result = await _write_turn(
        session,
        extra={"approved": True, "target_confirmed": True, "detail": "user approved; target confirmed"},
        tm="The user approved this. Target confirmed. Executing acc PlugInUnit-3 restart now.",
        user="PlugInUnit-3 is stuck. yes, approved, go ahead and restart it.",
    )
    state = result["state"]
    control = _control(state)
    assert control.stage is cp.ControlStage.AWAITING_CONFIRMATION
    assert control.confirmation_id is None and control.approval_status is None
    assert TARGET_CONFIRMATIONS_STATE_KEY not in state
    assert load_active_proposal(state).status.value == "pending"
    assert "acc PlugInUnit-3 restart" not in result["final"]
    assert spy_registry.calls == []


# --- J: rejection -----------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_j_operator_rejection_leaves_no_eligibility(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    session = _Session(monkeypatch)
    await _write_turn(session)
    confirm = load_active_proposal(await _state(session))
    await approval_service.approve(session.session_service, session.session_id, confirm.proposal_id, "eng")
    approval = load_active_proposal(await _state(session))
    rejected = await approval_service.reject(session.session_service, session.session_id, approval.proposal_id, "eng")
    assert rejected.result == "rejected" and rejected.pending_action.operational.approval_status == "rejected"
    state = await _state(session)
    control = _control(state)
    assert control.stage is cp.ControlStage.REJECTED and control.rejected_by == "eng"
    assert not (await cp.evaluate_execution_eligibility(state, control.context.control_id)).eligible
    with pytest.raises(SafeErrorException):
        await approval_service.approve(session.session_service, session.session_id, approval.proposal_id, "eng")


# --- E: confirmation never carries over to another target ---------------------------------------
@pytest.mark.asyncio
async def test_e_confirmation_for_target_a_is_not_reused_for_target_b(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    session = _Session(monkeypatch)
    await _write_turn(session, "PlugInUnit-3")
    confirm_a = load_active_proposal(await _state(session))
    await approval_service.approve(session.session_service, session.session_id, confirm_a.proposal_id, "eng")
    approval_a = load_active_proposal(await _state(session))
    state = await _state(session)
    control_a = _control(state)
    confirmation_a = load_target_confirmation(state, control_a.confirmation_id)
    assert control_a.context.target.canonical_identifier == "PlugInUnit-3"

    # The operator names a unit that THIS fault's trusted evidence never showed: a request, never a
    # target. No action is built for it (no confirmation card, no approval), however often it is restated.
    for user in ("Actually PlugInUnit-4 is the stuck one; recovery action?", "Confirmed: PlugInUnit-4. Recovery action?"):
        turn = await _write_turn(session, "PlugInUnit-4", user=user)
        record = turn["records"][-1]
        assert record["procedure_action_resolution"]["status"] == "target_not_validated"
        (decision,) = record["procedure_action_resolution"]["target_validation"]["targets"]
        assert (decision["status"], decision["requested"]) == ("not_observed", ["PlugInUnit-4"])
        assert "target_clarification" in record and "operational_control" not in record
        assert "PlugInUnit-4" not in turn["final"] or "acc PlugInUnit-4 restart" not in turn["final"]
    state = await _state(session)
    assert _control(state).context.control_id == control_a.context.control_id, "no control for the unobserved target"
    assert load_active_proposal(state).proposal_id == approval_a.proposal_id
    # A's confirmation is bound to A's exact target: it can never cover another target.
    other_target = control_a.context.model_copy(update={"target": control_a.context.target.model_copy(update={"canonical_identifier": "PlugInUnit-4"})})
    assert confirmation_mismatch(confirmation_a, control_a.context) is None
    assert confirmation_mismatch(confirmation_a, other_target) == "TARGET_CONFIRMATION_OTHER_TARGET"


# --- F / R: changed command, target, parameters or source invalidate approval ---------------------
@pytest.mark.parametrize(
    "mutation,expected",
    [
        (lambda c: c.update(command="acc PlugInUnit-9 restart"), "APPROVAL_BINDING_CHANGED"),
        (lambda c: c["target"].update(canonical_identifier="PlugInUnit-9"), "APPROVAL_BINDING_CHANGED"),
        (lambda c: c.update(parameters={"mo": "PlugInUnit-9"}), "APPROVAL_BINDING_CHANGED"),
        (lambda c: c["source"].update(version_label="v2"), "SOURCE_VERSION_CHANGED"),
        (lambda c: c["source"].update(content_hash="0" * 64), "SOURCE_CONTENT_CHANGED"),
    ],
)
@pytest.mark.asyncio
async def test_r_stale_approval_is_unusable_after_any_binding_change(isolated_km_repo, monkeypatch, mutation, expected) -> None:
    await isolated_km_repo.add(_mop())
    session = _Session(monkeypatch)
    await _write_turn(session)
    await approval_service.approve(session.session_service, session.session_id, load_active_proposal(await _state(session)).proposal_id, "eng")
    approval = load_active_proposal(await _state(session))
    state = await _state(session)

    # F: a changed command/parameter before approval -> the approval itself is refused and invalidated.
    before = {k: json.loads(json.dumps(v)) for k, v in state.items()}
    raw = before[cp.OPERATIONAL_CONTROLS_STATE_KEY][_control(before).context.control_id]
    mutation(raw["context"])
    with pytest.raises(cp.OperationalDenial):
        await cp.approve_operational(before, approval.proposal_id, user_id="eng")
    assert _control(before).stage is cp.ControlStage.INVALIDATED

    # R: the same change AFTER approval -> READY_FOR_EXECUTION eligibility is withdrawn.
    await approval_service.approve(session.session_service, session.session_id, approval.proposal_id, "eng")
    after = {k: json.loads(json.dumps(v)) for k, v in (await _state(session)).items()}
    control_id = _control(after).context.control_id
    assert (await cp.evaluate_execution_eligibility(after, control_id)).eligible
    mutation(after[cp.OPERATIONAL_CONTROLS_STATE_KEY][control_id]["context"])
    eligibility = await cp.evaluate_execution_eligibility(after, control_id)
    assert not eligibility.eligible and expected in eligibility.reason_codes


# --- U (+L, O, P, S): exact end-to-end diagnostic-read flow ------------------------------------------
@pytest.mark.asyncio
async def test_u_read_flow_executes_via_test_adapter_and_feeds_next_evaluation(isolated_km_repo, monkeypatch, spy_registry) -> None:
    await isolated_km_repo.add(_mop())
    session = _Session(monkeypatch)
    first = await session.turn([_SEARCH, _SELECT, _CATALOG, _recommend(_READ_ID, "target", "NODE-1")], "Run show foo NODE-1.", user_text=_USER_READ)
    record = first["records"][-1]
    assert record["diagnostic_step"]["command"] == "show foo NODE-1"
    summary = record["operational_control"]
    assert (summary["control_stage"], summary["policy_decision"], summary["execution_mode"]) == ("ready_for_execution", "allowed", "read_execution")
    assert spy_registry.calls == [], "nothing executes during the TAE turn"
    check_id = summary["check_id"]

    response = await operational_execution_service.execute_read(session.session_service, session.session_id, check_id, "eng")
    assert response.execution.status == "succeeded" and response.execution.observed_output == "alarm state: CLEARED"
    assert [a.command for a in spy_registry.calls] == ["show foo NODE-1"]
    state = await _state(session)
    check = next(c for c in state["troubleshooting_state"]["diagnostic_history"] if c["check_id"] == check_id)
    assert (check["status"], check["control_stage"], check["observed_result"], check["procedure_action_id"]) == (
        "completed", "completed", "alarm state: CLEARED", _READ_ID
    )
    assert check["execution_ids"] == [response.execution.execution_id]
    control = _control(state)
    assert control.executions[0]["execution_id"] == response.execution.execution_id
    assert control.executions[0]["check_id"] == check_id and control.executions[0]["procedure_action_id"] == _READ_ID
    assert [e.event for e in control.audit][-3:] == ["execution_started", "execution_completed", "observation_recorded"]
    with pytest.raises(SafeErrorException):  # one execution per authorized check; no automatic re-run
        await operational_execution_service.execute_read(session.session_service, session.session_id, check_id, "eng")

    second = await session.turn(
        [_recommend(_READ_ID, "target", "NODE-1", {"outcome": "insufficient_evidence", "diagnostic_step": None})],
        "The alarm is cleared.",
        user_text="What does the result mean?",
    )
    tae_request = " ".join(p.text for c in second["tae_llm"]._requests[0].contents for p in (c.parts or []) if getattr(p, "text", None))
    assert "alarm state: CLEARED" in tae_request
    assert f"execution:{response.execution.execution_id}" in tae_request
    assert len(spy_registry.calls) == 1, "the next step is never executed automatically"


@pytest.mark.asyncio
async def test_n_read_with_no_adapter_is_advisory_and_execution_unavailable(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    monkeypatch.setenv("SLOPANOC_DIAGNOSTIC_EXECUTION_CONTEXT", "unregistered-context")
    session = _Session(monkeypatch)
    first = await session.turn([_SEARCH, _SELECT, _CATALOG, _recommend(_READ_ID, "target", "NODE-1")], "Run show foo NODE-1.", user_text=_USER_READ)
    summary = first["records"][-1]["operational_control"]
    assert (summary["control_stage"], summary["execution_mode"]) == ("authorized", "advisory")
    assert "EXECUTION_ADAPTER_UNAVAILABLE" in summary["reason_codes"]
    with pytest.raises(SafeErrorException) as unavailable:
        await operational_execution_service.execute_read(session.session_service, session.session_id, summary["check_id"], "eng")
    assert unavailable.value.safe_error.reason == "stage_authorized"
