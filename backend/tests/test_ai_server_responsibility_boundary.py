"""AI vs Server responsibility boundary (docs/TROUBLESHOOTING_STRATEGY.md).

    AI optimizes: which diagnostic is most useful next, which branch / hypothesis, whether
    remediation or escalation is technically appropriate.
    Server constrains ONLY when a hard invariant would be violated (TRUST / CONSISTENCY / SAFETY).

Ordering / dependency cases run end to end through the real ChatService + TechnicalAuthorityAgentTool
(scripted LLMs); the remaining guardrails are exercised at the controller with real resolutions.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.agent_tool import build_server_validated_commands
from backend.agents.technical_authority_engineer.progression_controller import ProposalDecision, TurnKind
from backend.cases.troubleshooting_progression import ProgressionPhase, ResolutionState, StepStatus, load_progression
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_ess_service_unavailable_e2e_verification import _fc, isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _respond, _search
from backend.tests.test_progression_resolution_gates import _DISABLED, _HGET, _Flow, _ev, _executed, _remediated

D = ProposalDecision
_KID, _SEC = "KID-RADIO-BRANCHES", "sec-0000"
_A = "Radio unit state check: `st ru`\n"
_B = "Cell state check: `st cell`\n"
_C = "Plug-in unit state check: `st pluginunit`\n"
_D = "Transport link check: `st transport`\n"
_OBJECTIVE = {"st ru": "Check the radio unit state", "st cell": "Check the cell state", "st pluginunit": "Check the plug-in unit state",
              "st transport": "Check the transport link"}
_OUTPUT = {"st ru": "$ st ru\nRadioUnit=1 OPER=DISABLED", "st cell": "$ st cell\nNRCellDU=1 OPER=DISABLED",
           "st pluginunit": "$ st pluginunit\nPlugInUnit=1 OPER=ENABLED"}


def _mop(content: str) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID, document_type=KnowledgeDocumentType.MOP, title="Radio Fault Branches",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id=f"{_KID}.docx", display_name="Radio Fault Branches"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading="Radio fault", sequence=0, content=content, source_locator="lines:1-6")],
    )


def _ids(content: str) -> dict[str, str]:
    return {a.command_template: a.action_id for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=content)[0]}


def _propose(ids: dict[str, str], command: str) -> list[list[types.Part]]:
    select = _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": _KID, "version_label": "v1", "section_id": _SEC}]})
    return [_search("radio fault"), select, _CATALOG, _respond(action_id=ids[command], action=_OBJECTIVE[command])]


def _event(turn: dict[str, Any]) -> dict[str, Any]:
    return [e for e in turn["trace"]["operational_events"] if e.get("stage") == "progression"][-1]


async def _after_a(isolated_km_repo, monkeypatch, content: str) -> tuple[_Conversation, dict[str, str]]:
    """Diagnostic A (st ru) presented and its result validated; the agent then chooses freely."""
    await isolated_km_repo.add(_mop(content))
    ids = _ids(content)
    conv = _Conversation(monkeypatch)
    t1 = await conv.turn("Ericsson 5G: radio unit fault alarm on the node. What should I check?", _propose(ids, "st ru"), "Run st ru.",
                         {"subject_component": "radio unit"})
    assert t1["record"]["diagnostic_step"]["command"] == "st ru"
    return conv, ids


# 1. alternative path ------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_alternative_path_c_after_a_is_allowed_without_mandatory_dependencies(isolated_km_repo, monkeypatch) -> None:
    conv, ids = await _after_a(isolated_km_repo, monkeypatch, _A + _B + _C)
    t2 = await conv.turn(_OUTPUT["st ru"], _propose(ids, "st pluginunit"), "Run st pluginunit.")
    assert (_event(t2)["decision"], t2["record"]["diagnostic_step"]["command"]) == ("new_step", "st pluginunit"), "B appearing before C is not a rule"


# 2. mandatory dependency ---------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_explicit_mandatory_dependency_blocks_until_completed(isolated_km_repo, monkeypatch) -> None:
    content = _A + _B + "`st cell` must be completed before `st pluginunit`.\n" + _C
    conv, ids = await _after_a(isolated_km_repo, monkeypatch, content)
    t2 = await conv.turn(_OUTPUT["st ru"], _propose(ids, "st pluginunit"), "Run st pluginunit.")
    assert _event(t2)["decision"] == "rejected_mandatory_prerequisite"
    assert t2["record"]["diagnostic_step"] is None and "st pluginunit" not in t2["final"]
    reason = t2["record"]["missing_information"][-1]
    assert reason == "mandatory prerequisite not completed: st cell (governed line 3: explicit mandatory dependency language ('<A> before <B>'))"
    t3 = await conv.turn("what next?", _propose(ids, "st cell"), "Run st cell.")
    assert _event(t3)["decision"] == "new_step"
    t4 = await conv.turn(_OUTPUT["st cell"], _propose(ids, "st pluginunit"), "Run st pluginunit.")
    assert (_event(t4)["decision"], t4["record"]["diagnostic_step"]["command"]) == ("new_step", "st pluginunit")


# 3. recommended order ------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_recommended_order_does_not_block(isolated_km_repo, monkeypatch) -> None:
    content = _A + _B + "Run `st cell` before `st pluginunit` where possible.\n" + _C
    assert pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=content)[0][2].related(
        pa.RelationshipType.RECOMMENDED_BEFORE
    )
    conv, ids = await _after_a(isolated_km_repo, monkeypatch, content)
    t2 = await conv.turn(_OUTPUT["st ru"], _propose(ids, "st pluginunit"), "Run st pluginunit.")
    assert _event(t2)["decision"] == "new_step"


# 4. unknown relationship -----------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unknown_relationship_creates_no_restriction(isolated_km_repo, monkeypatch) -> None:
    content = _A + "Prerequisite: `st cell`, `st transport` and `st pluginunit`\n"
    actions = {a.command_template: a for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=content)[0]}
    assert [r.relationship_type for r in actions["st pluginunit"].relationships] == [pa.RelationshipType.UNKNOWN]
    assert actions["st pluginunit"].mandatory_prerequisite_ids == []
    conv, ids = await _after_a(isolated_km_repo, monkeypatch, content)
    t2 = await conv.turn(_OUTPUT["st ru"], _propose(ids, "st pluginunit"), "Run st pluginunit.")
    assert _event(t2)["decision"] == "new_step"


# 5 + 6. duplicate blocked / different diagnostic allowed ----------------------------------------------------------
@pytest.mark.asyncio
async def test_known_result_is_protected_but_a_different_diagnostic_is_allowed(isolated_km_repo, monkeypatch) -> None:
    conv, ids = await _after_a(isolated_km_repo, monkeypatch, _A + _B + _C + _D)
    t2 = await conv.turn(_OUTPUT["st ru"], _propose(ids, "st ru"), "Run st ru again.")
    assert _event(t2)["decision"] == "rejected_repeated_step" and "st ru" not in t2["final"]
    t3 = await conv.turn("what next?", _propose(ids, "st transport"), "Run st transport.")
    assert (_event(t3)["decision"], t3["record"]["diagnostic_step"]["command"]) == ("new_step", "st transport"), "a different branch is the agent's call"


# 7. pending continuity -------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pending_step_continuity_on_a_command_question(isolated_km_repo, monkeypatch) -> None:
    conv, ids = await _after_a(isolated_km_repo, monkeypatch, _A + _B)
    t2 = await conv.turn("what command?", _propose(ids, "st ru"), "Run st ru.")
    assert (_event(t2)["turn_kind"], _event(t2)["decision"]) == ("command_follow_up", "pending_step_resolved")
    assert [s.status for s in load_progression(t2["state"]).steps] == [StepStatus.PRESENTED]


# 8. explicit switch ----------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_explicit_switch_supersedes_with_audit_and_the_new_objective_proceeds(isolated_km_repo, monkeypatch) -> None:
    conv, ids = await _after_a(isolated_km_repo, monkeypatch, _A + _B + _D)
    t2 = await conv.turn("check the transport link instead", _propose(ids, "st transport"), "Run st transport.")
    ru, transport = load_progression(t2["state"]).steps
    assert (ru.status, ru.result, ru.status_history[-1].reason) == (StepStatus.SUPERSEDED, None, "operator: check the transport link instead")
    assert (transport.command, transport.status, _event(t2)["decision"]) == ("st transport", StepStatus.PRESENTED, "new_step")


# 9. result integrity ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["is Unit=4 the one you mean?", "hmm ok", "the customer called again"])
def test_random_or_question_text_never_completes_a_diagnostic(text: str) -> None:
    flow = _Flow()
    flow.operator("unit 4 is down")
    flow.propose(_HGET, action="Check the unit state")
    assert flow.operator(text) is not TurnKind.RESULT
    step = flow.c.progression.steps_for("FAULT-U4")[0]
    assert step.status is StepStatus.PRESENTED and step.result is None and flow.thread.trusted_observation_texts() == []


# 10. command authority -------------------------------------------------------------------------------------------
def test_composed_command_still_fails_command_authority() -> None:
    ev = _ev().model_copy(update={"content_snippet": "Combined: `st ru; st cell`\n"})
    assert build_server_validated_commands([ev], [{"command": "st ru; st cell", "source_id": ev.source_id}], trusted_context={"target_confirmed": True}) == []


# 11. resolution -------------------------------------------------------------------------------------------------
def test_successful_remediation_alone_never_resolves() -> None:
    flow = _Flow()
    _remediated(flow)
    _executed(flow, "acc Unit=4 restart\nrestart completed successfully\nUnit=4 operationalState=ENABLED")
    assert (flow.fault.phase, flow.fault.resolution) == (ProgressionPhase.POST_ACTION_VERIFICATION_REQUIRED, ResolutionState.UNRESOLVED)


# The server never interprets on the agent's behalf ------------------------------------------------------------------
def test_server_does_not_reinterpret_hypotheses_after_a_failed_verification() -> None:
    flow = _Flow()
    _remediated(flow)
    _executed(flow)
    flow.operator("")
    flow.propose(_HGET, action="Verify the unit state")
    flow.operator(_DISABLED)
    assert flow.fault.phase is ProgressionPhase.REASSESS
    assert [h.state.value for h in flow.c.progression.hypotheses_for("FAULT-U4")] == ["active"], "only the agent may propose WEAKENED"
