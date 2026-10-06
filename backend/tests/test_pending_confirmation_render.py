"""Operator-facing rendering of a governed state change awaiting target confirmation.

Live defect (Prompt 4 run cee498a5, T6-T8): the state change was resolved, its target VALIDATED,
the confirmation card created and the command correctly withheld -- yet the final text read "no
command for this diagnostic step has passed the current grounding and authorization checks"
(the projection's state-change guard fell back to the generic text). Rendering only: the state is
read from the validated record (operational control + resolution); nothing is created or authorized.
Identifiers below are fixtures only.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.agents.technical_authority_engineer import agent_tool as tae_agent_tool
from backend.agents.technical_authority_engineer.synthesis_boundary import (
    GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT,
    _project_current_step,
    enforce_response_completeness,
    render_pending_target_confirmation,
)
from backend.approval.service import load_active_proposal
from backend.operations import control_plane as cp
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _Conversation
from backend.tests.test_state_change_target_authority import _PARAM, _diagnosed, _gate, _mop, _restart

COMMAND = "acc Unit=U-2 restartunit"
GOVERNED = [{"source_type": "governed_knowledge", "source_id": "MOP:v1:sec"}]


def _execution(**overrides: Any) -> dict[str, Any]:
    record = {
        "outcome": "recommended",
        "diagnostic_step": {"action": "Restart the faulty unit", "command": None, "procedure_action_id": "pa-1", "expected_evidence": "Unit state"},
        "operational_control": {
            "control_id": "ctl-1", "procedure_action_id": "pa-1", "command": COMMAND, "operation_type": "mutating_operational",
            "target": {"target_type": "Unit", "canonical_identifier": "U-2", "display_value": "Unit=U-2"},
            "control_stage": "awaiting_confirmation", "target_confirmation_required": True,
        },
        "procedure_action_resolution": {
            "action_id": "pa-1", "action_type": "state_change", "rendered_command": None, "status": "resolved",
            "target_validation": {"passed": True, "targets": [{"key": "Unit", "value": "U-2", "status": "validated"}]},
        },
        tae_agent_tool.RESPONSE_COMPLETENESS_KEY: {"forced_route": True, "required": True, "satisfied": True, "elements": ["next_step"],
                                                   "pending_step": None, "consumed_step_ids": []},
        "verified_evidence": GOVERNED,
    }
    record.update(overrides)
    return record


def test_1_2_pending_confirmation_is_rendered_and_the_command_stays_hidden() -> None:
    text = enforce_response_completeness("Noted.", _execution())
    assert text.startswith("A governed corrective action is available for the validated target Unit=U-2.")
    assert "Please confirm Unit=U-2 on the action card to continue." in text
    assert COMMAND not in text and "restartunit" not in text
    assert "grounding" not in text and text != GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT


@pytest.mark.parametrize(
    "overrides",
    [
        {"operational_control": None},  # no control at all: a genuine authorization failure
        {"procedure_action_resolution": {"action_id": "pa-1", "action_type": "state_change", "rendered_command": None,
                                         "target_validation": {"passed": False, "targets": []}}},  # target not validated
    ],
    ids=["no_control", "target_not_validated"],
)
def test_4_genuine_grounding_or_authorization_failure_keeps_the_generic_fallback(overrides: dict[str, Any]) -> None:
    execution = _execution(**overrides)
    assert render_pending_target_confirmation(execution) is None
    assert _project_current_step(execution, GOVERNED) == GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT


@pytest.mark.parametrize(
    "control_overrides",
    [{"control_stage": "authorized", "target_confirmation_required": False}, {"control_stage": "invalidated", "target_confirmation_required": False}],
    ids=["authorized", "invalidated"],
)
def test_5_no_pending_confirmation_leaves_rendering_unchanged(control_overrides: dict[str, Any]) -> None:
    execution = _execution(operational_control={**_execution()["operational_control"], **control_overrides})
    assert render_pending_target_confirmation(execution) is None
    read = _execution(diagnostic_step={"action": "Check the unit state", "command": "st unit", "expected_evidence": "Unit state"},
                      operational_control=None, procedure_action_resolution=None)
    assert _project_current_step(read, GOVERNED) == "Check the unit state. Command: `st unit`. Please report: Unit state."


@pytest.mark.asyncio
async def test_3_live_shape_renders_confirmation_and_preserves_the_existing_card(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed(conv, "FieldReplaceableUnit=RRU-2   DISABLED   FAULTY")
    turn = await _restart(conv, "restart it", _PARAM)  # the synthesis ("Run the recovery.") omits the state
    assert _gate(turn)["passed"] is True
    assert "Please confirm FieldReplaceableUnit=RRU-2 on the action card to continue." in turn["final"]
    assert "restartunit" not in turn["final"] and "grounding" not in turn["final"]
    # The card created by the control plane is the one the operator is asked to use (one card, same id).
    card = load_active_proposal(turn["state"])
    control = cp.OperationalControlRecord.model_validate(list(turn["state"][cp.OPERATIONAL_CONTROLS_STATE_KEY].values())[-1])
    assert card is not None and card.proposal_id == control.confirmation_request_id
    assert control.stage is cp.ControlStage.AWAITING_CONFIRMATION
