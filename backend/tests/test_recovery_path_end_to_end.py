"""End-to-end recovery path (real ChatService + TechnicalAuthorityAgentTool + approve/execute services,
scripted LLMs), reproducing the live sequence:

    alarms -> "5G Ericsson" -> st fieldr -> operator output FieldReplaceableUnit=4 DISABLED
    -> "let's fix it" -> "give me the next cmd"

The recovery command must come ONLY from the SELECTED governed section's ProcedureAction template
plus a parameter literally present in recorded operator output; the model's `**4**` / free-form
command never reaches resolution; the state change stops at target confirmation / HITL and is
never executed. When the approved section carries only prose recovery guidance, the result is a
knowledge gap -- no synthetic restart command.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.api import approval_service, execution_service
from backend.approval.service import load_active_proposal
from backend.gateway.safe_error import SafeErrorException
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.operations import control_plane as cp
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _search, _select

_KID, _SEC = "KID-ALARM-RESOLUTION", "sec-0000"
_DIMS = {"vendor": ["ericsson"], "technology": ["4g", "5g"]}
_HEAD = """Alarms List for Resolution:
Table:
Alarms Name | Remarks
Service Degraded | Restart the affected radio
Resource Allocation Failure | Restart the affected radio
If alarm still active after short live time, Rule will reset the Radio once in 24 hours
HC Commands:
amos xxxx
lt all
alt
st fieldr
st ru
q
HC Logs:
If Fieldreplaceableunit => BB node
"""
_STEP = """Baseband Radio Reset
amos xxxxx
lt all
acc FieldReplaceableUnit=xxxx restartunit
y
plan
"""
_ACTION_SECTION = _HEAD + _STEP
_GAP_SECTION = _HEAD  # approved + MATCH, but recovery guidance is prose only
_TEMPLATE = "acc FieldReplaceableUnit=xxxx restartunit"
_OUTPUT = "st fieldr\nFieldReplaceableUnit=4   1 (UNLOCKED)   0 (DISABLED)   FAULTY\n"


def _mop(content: str) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID, document_type=KnowledgeDocumentType.MOP, title="Alarms Resolution Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions=_DIMS),
        source=KnowledgeSource(source_system="test", source_id="MOP_Alarms.docx", display_name="Alarms MOP"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading=None, sequence=0, content=content, source_locator="lines:1-24")],
    )


def _ids(content: str) -> dict[str, str]:
    return {a.command_template: a.action_id for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=content)[0]}


def _respond(outcome: str = "recommended", action_id: Optional[str] = None, params: Optional[list[dict[str, str]]] = None,
             action: str = "Perform the governed step", command: Optional[str] = None) -> list[types.Part]:
    step = None
    if outcome == "recommended":
        step = {"action": action, "reason": "Governed step for the current request.", "expected_evidence": "Command output",
                "command": command, "command_source": "MOP_Alarms.docx" if command else None, "restrictions": [],
                "procedure_action_id": action_id, "parameter_values": params or []}
    return [types.Part.from_text(text=json.dumps({"outcome": outcome, "technical_interpretation": "Interpretation.", "missing_information": [], "diagnostic_step": step}))]


_FACTS = {"known_applicability_facts": {"vendor": ["Ericsson"], "technology": ["5G"]}}


async def _prelude(conv: _Conversation, fieldr_id: str) -> dict[str, Any]:
    """alarms -> 5G Ericsson (st fieldr authorized) -> operator output recorded verbatim."""
    t1 = await conv.turn(
        "alarms: Service Degraded and Resource Allocation Failure on the node",
        [_search("Service Degraded Resource Allocation Failure alarm"), _respond(outcome="insufficient_evidence")],
        "Which vendor and technology is this?",
    )
    assert "To validate the applicable governed procedure I still need" in t1["final"], "UNKNOWN != MATCH: applicability is asked, not assumed"

    t2 = await conv.turn(
        "5G Ericsson",
        [_search("Service Degraded alarm resolution"), _select(_KID, _SEC), _CATALOG, _respond(action_id=fieldr_id, action="Check the field replaceable unit state")],
        "Run st fieldr.",
        {"vendor": "Ericsson", "technology": "5G", "continues_active_objective": True},
        _FACTS,
    )
    assert t2["record"]["diagnostic_step"]["command"] == "st fieldr"

    t3 = await conv.turn(_OUTPUT, [_search("alarm resolution"), _select(_KID, _SEC), _respond(outcome="insufficient_evidence")], "FieldReplaceableUnit **4** is faulty.")
    check = t3["state"]["troubleshooting_state"]["diagnostic_history"][0]
    assert check["grounded_command"] == "st fieldr" and check["operator_observation"] == _OUTPUT.strip()
    return t3


@pytest.mark.asyncio
async def test_recovery_command_comes_only_from_governed_template_and_operator_evidence(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop(_ACTION_SECTION))
    ids = _ids(_ACTION_SECTION)
    conv = _Conversation(monkeypatch)
    await _prelude(conv, ids["st fieldr"])
    step = [_search("radio recovery restart procedure"), _select(_KID, _SEC), _CATALOG]
    model_command = "acc FieldReplaceableUnit=**4** restartunit"

    t4 = await conv.turn(
        "let's fix it",
        step + [_respond(action_id=ids[_TEMPLATE], params=[{"name": "FieldReplaceableUnit", "value": "**4**"}], action="Recover the faulty unit", command=model_command)],
        f"Run {model_command} now.",
        {"requested_operation": "fix", "continues_active_objective": True},
    )
    # 1-2. Retrieval stayed on the recovery objective; the action-bearing section was explicitly SELECTED.
    search = t4["trace"]["searches"][0]
    assert search["query_text"] == "radio recovery restart procedure"
    assert [r["selection_state"] for r in search["results"]] == ["SELECTED"]
    assert search["results"][0]["applicability_outcome"] == "match" and search["results"][0]["lifecycle_status"] == "approved"
    assert t4["trace"]["selections"][0]["status"] == "accepted"
    # 3. The governed recovery action is in the catalog, verbatim, with exact provenance.
    catalog = t4["trace"]["action_catalogs"][0]["actions"]
    entry = next(a for a in catalog if a["action_id"] == ids[_TEMPLATE])
    assert (entry["command_template"], entry["parameters"], entry["source"], entry["action_type"]) == (
        _TEMPLATE, ["FieldReplaceableUnit"], f"{_KID}:v1:{_SEC}", "state_change"
    )
    # 4-7. The model's command and `**4**` never reach resolution; `4` comes from operator output.
    resolution = t4["record"]["procedure_action_resolution"]
    assert resolution["model_command_ignored"] == model_command
    assert resolution["command_template"] == _TEMPLATE
    assert resolution["rendered_command"] == "acc FieldReplaceableUnit=4 restartunit"
    (binding,) = resolution["parameters"]
    assert (binding["name"], binding["state"], binding["value"]) == ("FieldReplaceableUnit", "verified", "4")
    # The target is the current-case fact observed in the operator's validated `st fieldr` output.
    assert binding["detail"].startswith("current-case target fact")
    (target,) = resolution["target_validation"]["targets"]
    assert (target["key"], target["value"], target["status"]) == ("FieldReplaceableUnit", "4", "validated")
    # 8-9. Command Authority re-validates and withholds it pending trusted target confirmation.
    authority = t4["trace"]["command_authority"]
    assert any(
        c["decision"] == "preview_authorized_if_target_confirmed" and c["command"] == "acc FieldReplaceableUnit=4 restartunit" for c in authority
    ), "grounded against the governed template; authorized only under a HYPOTHETICAL confirmed target"
    assert not any(c["decision"] == "authorized" and "restartunit" in c["command"] for c in authority)
    assert t4["record"]["diagnostic_step"]["command"] is None, "never displayed as immediately executable"
    control = t4["record"]["operational_control"]
    assert (control["control_stage"], control["policy_decision"], control["reason_codes"]) == (
        "awaiting_confirmation", "clarification_required", ["STATE_CHANGE", "TARGET_NOT_CONFIRMED"]
    )
    assert "restartunit" not in t4["final"] and "**4**" not in t4["final"]

    # "give me the next cmd": same investigation, same governed action, still not executable.
    t5 = await conv.turn(
        "give me the next cmd",
        step + [_respond(action_id=ids[_TEMPLATE], action="Recover the faulty unit")],
        f"Next command: {model_command}",
        {"intent": "next command", "continues_active_objective": True},
    )
    assert t5["state"]["troubleshooting_state"]["fault_id"] == t4["state"]["troubleshooting_state"]["fault_id"]
    assert t5["record"]["procedure_action_resolution"]["rendered_command"] == "acc FieldReplaceableUnit=4 restartunit"
    assert t5["record"]["diagnostic_step"]["command"] is None and "restartunit" not in t5["final"]
    card = load_active_proposal(t5["state"])
    assert card.operation.value == "operational.confirmTarget"

    # Target confirmation (operator) -> Command Authority with the confirmation -> Policy -> HITL approval.
    confirmed = await approval_service.approve(conv.sessions, conv.session_id, card.proposal_id, "eng")
    approval = confirmed.pending_action.operational
    # The card names the validated typed target exactly as observed (key=value), not a bare value.
    assert (approval.kind, approval.command, approval.target, approval.source_id) == (
        "approval", "acc FieldReplaceableUnit=4 restartunit", "FieldReplaceableUnit=4", f"{_KID}:v1:{_SEC}"
    )
    approved = await approval_service.approve(conv.sessions, conv.session_id, confirmed.pending_action.proposal_id, "eng")
    assert approved.pending_action.operational.control_stage == "ready_for_execution"
    with pytest.raises(SafeErrorException) as refused:
        await execution_service.execute(conv.sessions, conv.session_id, confirmed.pending_action.proposal_id, "eng")
    assert refused.value.safe_error.reason == "state_change_execution_not_enabled"
    state = dict((await conv.sessions.get_session(conv.session_id, "eng")).state)
    record = cp.OperationalControlRecord.model_validate(state[cp.OPERATIONAL_CONTROLS_STATE_KEY][approval.control_id])
    assert [e.event for e in record.audit] == [
        "policy_evaluated", "target_confirmation_requested", "target_confirmed", "approval_requested", "approved", "execution_not_started"
    ]
    assert record.context.command == "acc FieldReplaceableUnit=4 restartunit" and record.context.parameters == {"FieldReplaceableUnit": "4"}


@pytest.mark.asyncio
async def test_prose_only_recovery_guidance_is_a_knowledge_gap_with_no_synthetic_command(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop(_GAP_SECTION))
    ids = _ids(_GAP_SECTION)
    assert _TEMPLATE not in ids and not any("restart" in t for t in ids)
    conv = _Conversation(monkeypatch)
    await _prelude(conv, ids["st fieldr"])
    model_command = "acc FieldReplaceableUnit=**4** restartunit"
    t4 = await conv.turn(
        "let's fix it",
        [_search("radio recovery restart procedure"), _select(_KID, _SEC), _CATALOG,
         _respond(action="Restart the affected radio", command=model_command)],
        f"Run {model_command} now.",
        {"requested_operation": "fix", "continues_active_objective": True},
    )
    catalog = t4["trace"]["action_catalogs"][0]["actions"]
    assert not [a for a in catalog if a["action_type"] == "state_change"], "no governed recovery action exists"
    rejected = [c for c in t4["trace"]["command_authority"] if c["command"] == model_command]
    assert rejected and rejected[0]["decision"] == "rejected" and rejected[0]["stage"] == "grounding"
    assert t4["record"]["diagnostic_step"]["command"] is None
    assert "operational_control" not in t4["record"]
    assert "restartunit" not in t4["final"] and "**4**" not in t4["final"]
    assert load_active_proposal(t4["state"]) is None, "no confirmation/approval card for a command that does not exist"
