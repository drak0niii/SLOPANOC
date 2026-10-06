"""Target-aware state-change authority.

Invariant: THIS governed operation must be valid for THIS exact target in THIS active fault under
THIS run's evidence BEFORE confirmation or approval is offered:

    selected governed evidence -> ProcedureAction -> target resolution + validation (current-case
    target facts) -> parameters -> Command Authority -> operator confirmation -> HITL approval

Case target facts come only from trusted, validated step results of the same fault (key=value as
written; no aliasing). Operator / model / earlier-confirmed values are requests, never proof.
Flows use the production ChatService + TechnicalAuthorityAgentTool with scripted models. Target
identifiers below are fixtures only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.agent_tool import authorize_grounded_command, state_change_target_problem
from backend.api import approval_service
from backend.approval.service import load_active_proposal
from backend.cases.target_facts import case_target_facts, extract_identifier_pairs
from backend.cases.troubleshooting_progression import (
    PROGRESSION_STATE_KEY,
    FaultProgression,
    ResultSource,
    StepStatus,
    TroubleshootingProgression,
    TroubleshootingStep,
)
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.operations import control_plane as cp
from backend.tests._target_fixtures import confirmed_and_validated
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _respond, _search, _select
from backend.tools.knowledge.diagnostic_trace import format_diagnostic_trace

_KID, _SEC = "TGT-UNIT-MOP", "sec-0000"
_READ, _PARAM, _FIXED = "st fru", "acc FieldReplaceableUnit=xxxx restartunit", "acc FieldReplaceableUnit=RRU-9 restartunit"
_CONTENT = (
    f"Unit state check: `{_READ}`\n"
    f"Unit recovery: `{_PARAM}`\n"
    f"Recovery of the reference unit: `{_FIXED}`\n"
)
_IDS = {a.command_template: a.action_id for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=_CONTENT)[0]}
_STEP = [_search("unit recovery"), _select(_KID, _SEC), _CATALOG]
_RESTART_REQUEST = {"subject_component": "radio unit", "requested_operation": "restart", "continues_active_objective": True}


def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID, document_type=KnowledgeDocumentType.MOP, title="Unit Recovery",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id="Unit.docx", display_name="Unit Recovery"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading="Unit recovery", sequence=0, content=_CONTENT, source_locator="lines:1-3")],
    )


async def _diagnosed(conv: _Conversation, output_lines: str) -> dict[str, Any]:
    """J: the read is presented and its validated output is bound -- the only source of case targets."""
    t1 = await conv.turn("radio unit fault on the node, what should I check?", _STEP + [_respond(action_id=_IDS[_READ], action="Check the unit state")],
                         f"Run {_READ}.", {"subject_component": "radio unit"})
    assert t1["record"]["diagnostic_step"]["command"] == _READ, "J: reads keep their normal path"
    assert not _gates(t1), "J: the target gate never runs for a read"
    t2 = await conv.turn(f"{_READ}\n{output_lines}", _STEP[:2] + [_respond(outcome="insufficient_evidence")], "Noted.")
    return t2


async def _restart(conv: _Conversation, text: str, template: str, value: Optional[str] = None) -> dict[str, Any]:
    params = [{"name": "FieldReplaceableUnit", "value": value}] if value else []
    return await conv.turn(text, _STEP + [_respond(action_id=_IDS[template], params=params, action="Recover the faulty unit")],
                           "Run the recovery.", _RESTART_REQUEST)


def _gates(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in turn["trace"].get("operational_events", []) if e.get("stage") == "target_gate"]


def _gate(turn: dict[str, Any]) -> dict[str, Any]:
    (gate,) = _gates(turn)
    return gate


def _assert_blocked(turn: dict[str, Any], status: str) -> dict[str, Any]:
    """No candidate, no Command Authority decision for it, no confirmation card, no approval."""
    gate = _gate(turn)
    assert gate["passed"] is False and [t["status"] for t in gate["targets"]] == [status]
    record = turn["record"]
    assert record["procedure_action_resolution"]["rendered_command"] is None
    assert not (record.get("diagnostic_step") or {}).get("command")
    assert "operational_control" not in record and load_active_proposal(turn["state"]) is None
    assert not [c for c in turn["trace"].get("command_authority", []) if "restartunit" in c["command"] and c["decision"] != "rejected"]
    assert "restartunit" not in turn["final"]
    return gate


# =============================================================================================
# A. Wrong fixed target / B. matching fixed target
# =============================================================================================


@pytest.mark.asyncio
async def test_a_wrong_fixed_target_never_reaches_authority_or_confirmation(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed(conv, "FieldReplaceableUnit=RRU-2   DISABLED   FAULTY")
    t3 = await _restart(conv, "restart it", _FIXED)
    gate = _assert_blocked(t3, "fixed_target_not_in_case")
    assert gate["targets"][0]["value"] == "RRU-9" and "RRU-2" in gate["targets"][0]["detail"]
    assert [f["identity"] for f in gate["case_target_facts"]] == ["FieldReplaceableUnit=RRU-2"]
    assert t3["record"]["target_clarification"]["text"]
    assert _progression_gate(t3)["target_isolated"]["met"] is False


@pytest.mark.asyncio
async def test_b_matching_fixed_target_continues_through_confirmation_and_approval(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed(conv, "FieldReplaceableUnit=RRU-9   DISABLED   FAULTY")
    t3 = await _restart(conv, "restart it", _FIXED)
    gate = _gate(t3)
    assert gate["passed"] is True and gate["targets"][0]["status"] == "validated" and gate["targets"][0]["fact_ids"]
    control = t3["record"]["operational_control"]
    assert control["control_stage"] == "awaiting_confirmation"
    card = load_active_proposal(t3["state"])
    assert card.operation.value == "operational.confirmTarget"
    confirmed = await approval_service.approve(conv.sessions, conv.session_id, card.proposal_id, "eng")
    assert confirmed.pending_action.operational.command == _FIXED and confirmed.pending_action.operational.target == "FieldReplaceableUnit=RRU-9"


def _progression_gate(turn: dict[str, Any]) -> dict[str, Any]:
    return [e for e in turn["trace"]["operational_events"] if e.get("stage") == "progression"][-1]["remediation_gate"] or {}


# =============================================================================================
# C / L. Missing target, and no alias between different keys
# =============================================================================================


@pytest.mark.asyncio
async def test_c_l_missing_target_and_no_alias_between_keys(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed(conv, "Equipment=1,RadioUnit=2   DISABLED")
    t3 = await _restart(conv, "restart it", _PARAM)
    gate = _assert_blocked(t3, "missing")
    # RadioUnit=2 is proven; FieldReplaceableUnit is required: never converted, never guessed.
    assert {f["identity"] for f in gate["case_target_facts"]} == {"Equipment=1", "RadioUnit=2"}
    assert "FieldReplaceableUnit" in gate["targets"][0]["detail"] and "RadioUnit" in gate["targets"][0]["detail"]
    assert "RRU-2" not in json.dumps(t3["record"])


# =============================================================================================
# D. Ambiguous target / K. explicit placeholder bound from the case
# =============================================================================================


@pytest.mark.asyncio
async def test_d_two_observed_targets_need_a_choice(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed(conv, "FieldReplaceableUnit=RRU-2   DISABLED   FAULTY\nFieldReplaceableUnit=RRU-3   DISABLED   FAULTY")
    t3 = await _restart(conv, "restart it", _PARAM)
    _assert_blocked(t3, "ambiguous")
    assert "RRU-2" in t3["record"]["target_clarification"]["text"] and "RRU-3" in t3["record"]["target_clarification"]["text"]
    # Naming one of the OBSERVED targets is a valid choice.
    t4 = await _restart(conv, "restart FieldReplaceableUnit=RRU-3", _PARAM, "RRU-3")
    assert _gate(t4)["passed"] is True and t4["record"]["operational_control"]["control_stage"] == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_k_parameterized_slot_binds_only_from_the_trusted_case_target(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed(conv, "FieldReplaceableUnit=RRU-2   DISABLED   FAULTY")
    t3 = await _restart(conv, "restart it", _PARAM)
    gate = _gate(t3)
    (decision,) = gate["targets"]
    assert (decision["status"], decision["value"], decision["requested"]) == ("validated", "RRU-2", [])
    assert decision["provenance"][0]["source"] == "operator_message"
    binding = t3["record"]["procedure_action_resolution"]["parameters"][0]
    assert binding["value"] == "RRU-2" and binding["detail"].startswith("current-case target fact")
    assert t3["record"]["diagnostic_step"]["command"] is None, "a state change is never an in-turn command"
    assert any(c["decision"] == "preview_authorized_if_target_confirmed" and c["command"] == "acc FieldReplaceableUnit=RRU-2 restartunit"
               for c in t3["trace"]["command_authority"])
    rendered = format_diagnostic_trace(t3["trace"])
    assert "TARGET GATE" in rendered and "TARGET FieldReplaceableUnit VALIDATED value='RRU-2'" in rendered

    # Confirmation -> approval: target re-validated against the case at every transition.
    card = load_active_proposal(t3["state"])
    approval = (await approval_service.approve(conv.sessions, conv.session_id, card.proposal_id, "eng")).pending_action
    assert (approval.operational.command, approval.operational.target) == ("acc FieldReplaceableUnit=RRU-2 restartunit", "FieldReplaceableUnit=RRU-2")
    state = dict((await conv.sessions.get_session(conv.session_id, "eng")).state)
    control = cp.OperationalControlRecord.model_validate(list(state[cp.OPERATIONAL_CONTROLS_STATE_KEY].values())[-1])
    assert await cp.revalidate_target(control.context, state) is None
    # A reopen makes the earlier results stale: the same target no longer holds for approval.
    stale = dict(state)
    progression = TroubleshootingProgression.model_validate(state[PROGRESSION_STATE_KEY])
    fault = progression.faults[control.context.fault_id]
    fault.reopened_at_sequence = max(s.sequence for s in progression.steps)
    stale[PROGRESSION_STATE_KEY] = progression.model_dump(mode="json")
    assert await cp.revalidate_target(control.context, stale) == "TARGET_NO_LONGER_IN_CASE"


# =============================================================================================
# E. Operator-only target / F. conflicting target / G. model-invented target
# =============================================================================================


@pytest.mark.asyncio
async def test_e_operator_named_target_never_observed_is_not_authority(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed(conv, "Equipment=1   OK")
    t3 = await _restart(conv, "restart FieldReplaceableUnit=RRU-7", _PARAM, "RRU-7")
    gate = _assert_blocked(t3, "not_observed")
    assert gate["targets"][0]["requested"] == ["RRU-7"]


@pytest.mark.asyncio
async def test_f_operator_target_conflicting_with_the_case_is_rejected(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed(conv, "FieldReplaceableUnit=RRU-2   DISABLED   FAULTY")
    t3 = await _restart(conv, "restart FieldReplaceableUnit=RRU-9 instead", _PARAM, "RRU-9")
    gate = _assert_blocked(t3, "conflicting")
    assert gate["targets"][0]["requested"] == ["RRU-9"] and "RRU-2" in gate["targets"][0]["detail"]


@pytest.mark.asyncio
async def test_g_model_invented_target_is_rejected_not_silently_replaced(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed(conv, "FieldReplaceableUnit=RRU-2   DISABLED   FAULTY")
    t3 = await _restart(conv, "restart it", _PARAM, "RRU-9")  # the model, not the operator, names RRU-9
    gate = _assert_blocked(t3, "conflicting")
    assert gate["targets"][0]["value"] is None, "never swapped for the observed RRU-2 behind the request's back"


# =============================================================================================
# H. Cross-fault / I. stale target  (authoritative progression, unit level)
# =============================================================================================


def _progression_with_result(*faults: str, text: str = "st fru\nFieldReplaceableUnit=RRU-9   FAULTY") -> TroubleshootingProgression:
    progression = TroubleshootingProgression()
    for fault in faults:
        progression.add_fault(FaultProgression(fault_id=fault, symptom_summary=fault))
    step = progression.append_step(TroubleshootingStep(fault_id=faults[0], objective="Check the unit state", command=_READ))
    progression.record_step_result(step.step_id, text, ResultSource.OPERATOR_MESSAGE, validation={"status": "validated"})
    progression.set_step_status(step.step_id, StepStatus.COMPLETED)
    return progression


def _action(template: str) -> pa.ProcedureAction:
    return next(a for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=_CONTENT)[0] if a.command_template == template)


def test_h_a_target_proven_in_fault_a_never_authorizes_fault_b() -> None:
    progression = _progression_with_result("FAULT-A", "FAULT-B")
    assert [f.identity for f in case_target_facts(progression, "FAULT-A")] == ["FieldReplaceableUnit=RRU-9"]
    assert case_target_facts(progression, "FAULT-B") == []
    for template, proposals in ((_FIXED, []), (_PARAM, [{"name": "FieldReplaceableUnit", "value": "RRU-9"}])):
        for facts in (case_target_facts(progression, "FAULT-B"), case_target_facts(progression, "FAULT-A")):
            validation = pa.validate_action_targets(_action(template), facts, fault_id="FAULT-B", proposals=proposals)
            assert validation.passed is False, "fault A's facts are filtered out for fault B"


def test_i_untrusted_failed_or_stale_results_establish_no_target() -> None:
    progression = _progression_with_result("FAULT-A")
    fault = progression.faults["FAULT-A"]
    fault.reopened_at_sequence = max(s.sequence for s in progression.steps)
    assert case_target_facts(progression, "FAULT-A") == [], "results before a reopen are stale"
    for source, validation in ((ResultSource.CALLER_SUMMARY, "validated"), (ResultSource.OPERATOR_MESSAGE, "command_failed")):
        p = _progression_with_result("FAULT-X")
        p.steps[0].result.source, p.steps[0].result.validation = source, {"status": validation}
        assert case_target_facts(p, "FAULT-X") == []
    # Facts are exactly what the trusted text writes: no key is invented for a bare value.
    assert extract_identifier_pairs("FieldReplaceableUnit=RRU-9 and RRU-7") == [("FieldReplaceableUnit", "RRU-9", "FieldReplaceableUnit=RRU-9")]


# =============================================================================================
# Command Authority itself: confirmation is not target validation
# =============================================================================================


def test_command_authority_requires_the_validated_target_to_be_what_the_command_acts_on() -> None:
    from backend.agents.technical_authority_engineer.schemas import GroundedCommand

    grounded = GroundedCommand(command=_FIXED, source_id=f"{_KID}:v1:{_SEC}", grounding_method="exact_match", raw_snippet=_CONTENT)
    assert authorize_grounded_command(grounded, {}, {"target_confirmed": True}) is None, "approval/confirmation alone never suffices"
    assert state_change_target_problem(_FIXED, confirmed_and_validated(("FieldReplaceableUnit", "RRU-2"))["target_validation"])
    assert authorize_grounded_command(grounded, {}, confirmed_and_validated(("FieldReplaceableUnit", "RRU-2"))) is None, "case target A, command target B"
    assert authorize_grounded_command(grounded, {}, {**confirmed_and_validated(("FieldReplaceableUnit", "RRU-9")), "target_confirmed": False}) is None, "validation is not confirmation either"
    authorized = authorize_grounded_command(grounded, {}, confirmed_and_validated(("FieldReplaceableUnit", "RRU-9")))
    assert authorized is not None and authorized.command == _FIXED
    # Reads are unaffected.
    read = GroundedCommand(command=_READ, source_id=f"{_KID}:v1:{_SEC}", grounding_method="exact_match", raw_snippet=_CONTENT)
    assert authorize_grounded_command(read, {}, None) is not None


def test_action_target_schema_is_explicit_only() -> None:
    assert [t.view() for t in _action(_PARAM).targets] == [{"key": "FieldReplaceableUnit", "kind": "parameterized", "parameter": "FieldReplaceableUnit", "fixed_value": None}]
    assert [t.view() for t in _action(_FIXED).targets] == [{"key": "FieldReplaceableUnit", "kind": "fixed", "parameter": None, "fixed_value": "RRU-9"}]
    assert [t.view() for t in pa.action_targets("acc <mo> restart")] == [{"key": None, "kind": "parameterized", "parameter": "mo", "fixed_value": None}]
    assert pa.action_targets("st fru") == []
    no_target = _action(_FIXED).model_copy(update={"targets": []})
    assert pa.validate_action_targets(no_target, [], fault_id="F").reason == "state change declares no target"
