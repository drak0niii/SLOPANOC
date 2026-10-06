"""Regression: a recovery request reached the correct APPROVED/MATCH governed section, but no usable
state-change ProcedureAction was produced (live: the model's re-targeted restart command was then
rejected as ungrounded).

Root causes (all generic, none vendor/document specific):
1. procedure-step command lines (step title + command line, no code formatting, no "commands:"
   label) were never extracted;
2. a document's keyed placeholder notation (`Key=xxxx`) was not recognised as a parameter;
3. an action invocation whose action word carries an object suffix was classified UNKNOWN.

The governed text -- never the model -- supplies the template; state change still needs trusted
target confirmation, policy and human approval. Prose-only recovery guidance stays a knowledge gap.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.tests._target_fixtures import FAULT, confirmed_and_validated, fact

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.agent_tool import build_server_validated_commands, classify_command_operation
from backend.agents.technical_authority_engineer.schemas import CommandOperationType, EvidenceReference
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_ess_service_unavailable_e2e_verification import _fc, isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _respond, _search

_KID, _VER, _SEC = "KID-RECOVERY", "v3", "sec-0000"
_SECTION = """Alarms List for Resolution:
Table:
Alarms Name | Remarks
Unit Degraded | Restart the affected unit
Restart the affected unit and after 5 mins check if the alarm clears.
HC Commands:
session xxxx
alt
st board
q
HC Logs:
Board Unit Reset
session xxxxx
lt all
acc Board=xxxx restartboard
y
plan
Shelf Unit Reset
session xxxxx
acc Shelf=xxx manualrestart
y
"""
_TEMPLATE = "acc Board=xxxx restartboard"


def _ev(content: str = _SECTION, applicability: str = "match") -> EvidenceReference:
    return EvidenceReference(
        source_id=f"{_KID}:{_VER}:{_SEC}",
        source_type="governed_knowledge",
        title="Unit Recovery Procedure",
        content_snippet=content,
        metadata={
            "knowledge_id": _KID, "version_label": _VER, "section_id": _SEC, "source_locator": "lines:1-24",
            "title": "Unit Recovery Procedure", "heading": None, "lifecycle_status": "approved", "applicability_outcome": applicability,
        },
    )


def _actions(content: str = _SECTION) -> dict[str, pa.ProcedureAction]:
    ev = _ev(content)
    return {a.command_template: a for a in pa.actions_for_evidence(ev, ev.metadata)[0]}


def test_step_block_recovery_command_is_materialized_verbatim_with_provenance() -> None:
    actions = _actions()
    restart = actions[_TEMPLATE]
    assert restart.action_type is pa.ProcedureActionType.STATE_CHANGE
    assert restart.operation_type is CommandOperationType.MUTATING_OPERATIONAL
    assert [(p.name, p.placeholder) for p in restart.parameters] == [("Board", "xxxx")]
    assert restart.command_template in _SECTION, "verbatim governed text"
    assert (restart.source.knowledge_id, restart.source.version_label, restart.source.section_id, restart.source.source_locator) == (
        _KID, _VER, _SEC, "lines:1-24"
    )
    assert restart.extraction_method == "argument_syntax_line" and restart.description == "Board Unit Reset"
    assert actions["acc Shelf=xxx manualrestart"].parameters[0].name == "Shelf"
    assert {"alt", "st board"} <= set(actions), "labelled read commands still extracted"


def test_prose_and_unkeyed_placeholders_never_become_commands() -> None:
    templates = set(_actions())
    assert not any(t.lower().startswith("restart the affected unit") for t in templates)
    assert "session xxxx" not in templates and "lt all" not in templates
    assert all("|" not in t for t in templates)


def test_prose_only_recovery_guidance_is_a_knowledge_gap() -> None:
    gap = "Alarms Name | Remarks\nUnit Degraded | Restart the affected unit\nRestart the affected unit and check the alarm.\n"
    assert not any(a.action_type is pa.ProcedureActionType.STATE_CHANGE for a in _actions(gap).values())
    assert pa.build_procedure_action_catalog([_ev(gap)]) == ([], [])


def test_prohibited_step_command_is_never_materialized() -> None:
    content = "Board Unit Reset\nDo not run acc Board=xxxx restartboard during business hours\n"
    assert _TEMPLATE not in _actions(content)


@pytest.mark.parametrize(
    "command,expected",
    [
        ("acc Board=X restartboard", CommandOperationType.MUTATING_OPERATIONAL),
        ("acc Board=X manualrestart", CommandOperationType.MUTATING_OPERATIONAL),
        ("acc Board=X lockboard", CommandOperationType.MUTATING_OPERATIONAL),
        ("acc Board=X statusreport", CommandOperationType.UNKNOWN),
    ],
)
def test_action_invocation_with_object_suffix_is_state_changing_never_read(command: str, expected: CommandOperationType) -> None:
    assert classify_command_operation(command) is expected


def _resolve(value: str, text: str):
    action = _actions()[_TEMPLATE]
    return pa.resolve_procedure_action(
        action.action_id, issued_ids={action.action_id}, selected_evidence=[_ev()],
        proposals=[{"name": "Board", "value": value}], operator_text=text,
        target_facts=[fact("Board", value)], fault_id=FAULT,
    )[0]


def test_trusted_rendering_passes_authority_only_with_target_confirmation() -> None:
    resolution = _resolve("4", "the unit is 4")
    assert resolution.candidate.command == "acc Board=4 restartboard"
    candidate = [resolution.candidate.as_authority_candidate("x")]
    assert build_server_validated_commands([_ev()], candidate) == [], "state change never authorized without trusted target confirmation"
    assert build_server_validated_commands([_ev()], candidate, trusted_context={"target_confirmed": True}) == [], "confirmation alone is not target validation"
    (authorized,) = build_server_validated_commands([_ev()], candidate, trusted_context=confirmed_and_validated(("Board", "4")))
    assert (authorized.command, authorized.operation_type) == ("acc Board=4 restartboard", CommandOperationType.MUTATING_OPERATIONAL)


@pytest.mark.parametrize(
    "command,ev",
    [
        ("acc Board=4 restartboard", _ev(applicability="unknown")),  # UNKNOWN != MATCH
        ("acc Board=4;reboot restartboard", _ev()),  # value outside the parameter charset
        ("acc Rack=4 restartboard", _ev()),  # not a rendering of the governed template
        ("acc Board=4 restartboard now", _ev()),
        ("acc Board=4 restartboard", _ev(content="Board Unit Reset\nRestart the affected unit\n")),  # knowledge gap
    ],
)
def test_ungoverned_or_non_matching_renderings_stay_rejected(command: str, ev: EvidenceReference) -> None:
    out = build_server_validated_commands([ev], [{"command": command, "source_id": ev.source_id}], trusted_context={"target_confirmed": True})
    assert out == []


def test_renderer_never_leaves_a_document_placeholder() -> None:
    assert pa.render_command_template(_TEMPLATE, []) is None
    bound = [pa.ParameterBinding(name="Board", state=pa.ParameterBindingState.VERIFIED, value="xxxx")]
    assert pa.render_command_template(_TEMPLATE, bound) is None


def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID, document_type=KnowledgeDocumentType.MOP, title="Unit Recovery Procedure",
        version=KnowledgeVersion(label=_VER, effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id="MOP_Recovery.docx", display_name="Recovery MOP"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading=None, sequence=0, content=_SECTION, source_locator="lines:1-24")],
    )


@pytest.mark.asyncio
async def test_recovery_request_yields_catalog_action_and_full_governed_path(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    restart_id = _actions()[_TEMPLATE].action_id
    conv = _Conversation(monkeypatch)
    # Remediation gate: the board thread is first diagnosed with an operator-observed governed read.
    select = _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": _KID, "version_label": _VER, "section_id": _SEC}]})
    await conv.turn("board unit 4 raised an alarm, what should I check?",
                    [_search("unit recovery restart"), select, _CATALOG, _respond(action_id=_actions()["st board"].action_id, action="Check the board state")],
                    "Run st board.", {"subject_component": "board unit"})
    await conv.turn("st board\nBoard=4 DISABLED", [_search("unit recovery restart"), select, _respond(outcome="insufficient_evidence")], "Noted.")
    turn = await conv.turn(
        "what about resetting the board unit 4?",
        [_search("unit recovery restart"), _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": _KID, "version_label": _VER, "section_id": _SEC}]}), _CATALOG,
         _respond(action_id=restart_id, params=[{"name": "Board", "value": "4"}], action="Recover the board unit")],
        "Run acc Board=**4** restartboard now.",
        {"subject_component": "board unit", "requested_operation": "resetting", "explicit_target": "4"},
    )
    catalog = turn["trace"]["action_catalogs"][0]["actions"]
    entry = next(a for a in catalog if a["action_id"] == restart_id)
    assert (entry["command_template"], entry["parameters"], entry["source"]) == (_TEMPLATE, ["Board"], f"{_KID}:{_VER}:{_SEC}")
    assert turn["trace"]["selections"][0]["status"] == "accepted", "evidence selection is explicit"
    record = turn["record"]
    assert record["procedure_action_resolution"]["rendered_command"] == "acc Board=4 restartboard"
    assert record["diagnostic_step"]["command"] is None, "never an in-turn command"
    assert record["operational_control"]["control_stage"] == "awaiting_confirmation"
    assert "restartboard" not in turn["final"], "model prose re-targeting the command is stripped"
    assert json.loads(json.dumps(turn["state"]["pending_action_proposal"]))["operation"] == "operational.confirmTarget"
