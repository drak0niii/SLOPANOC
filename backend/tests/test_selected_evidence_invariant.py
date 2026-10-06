"""Regression: contradictory runtime state

    SELECTION = explicit_empty, SELECTED = none, ACTION CATALOG = 0, TAE outcome = RECOMMENDED

A governed operational recommendation cannot survive without SELECTED governed evidence and with an
empty ProcedureAction catalog. Only a genuinely manual observation (no command, source, action id,
parameters or operational instruction) may remain `recommended`, explicitly marked manual-only.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.team_manager.governed_knowledge_completion import SAFE_COMPLETION_FAILURE_TEXT
from backend.agents.technical_authority_engineer.agent_tool import (
    GOVERNED_RECOMMENDATION_INVARIANT_KEY,
    MANUAL_OBSERVATION_KEY,
    NO_SELECTED_GOVERNED_EVIDENCE_NOTE,
    enforce_selected_evidence_invariant,
)
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.agents.technical_authority_engineer.synthesis_boundary import NO_GOVERNED_PROCEDURE_SELECTED_TEXT
from backend.tests.test_ess_service_unavailable_e2e_verification import _fc, isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_procedure_action_tae_e2e import _Session, _mop

_SEARCH = _fc("knowledge_search", {"query_text": "node alarm state"})
_SELECT_NONE = _fc("knowledge_select_evidence", {"selections": []})
_CATALOG = _fc("procedure_action_catalog", {})


def _recommended(action: str, expected: str = "What you observe", **step: Any) -> dict[str, Any]:
    return {
        "outcome": "recommended",
        "technical_interpretation": "Interpretation.",
        "missing_information": [],
        "diagnostic_step": {"action": action, "reason": "r", "expected_evidence": expected, "command": None, "command_source": None, "restrictions": [], **step},
    }


def _governed_ev() -> EvidenceReference:
    return EvidenceReference(
        source_id="K:v1:s", source_type="governed_knowledge", content_snippet="Check: `show foo <target>`",
        metadata={"knowledge_id": "K", "version_label": "v1", "section_id": "s", "lifecycle_status": "approved", "applicability_outcome": "match"},
    )


@pytest.mark.parametrize(
    "result,claim",
    [
        (_recommended("Reset the radio unit to clear the fault"), "operational_instruction_in_action"),
        (_recommended("Check the unit", expected="Output of `st pluginunit`"), "operational_instruction_in_expected_evidence"),
        (_recommended("Check the unit", command="st pluginunit"), "command"),
        (_recommended("Check the unit", procedure_action_id="pa-x"), "procedure_action_id"),
        (_recommended("Check the unit", restrictions=["[Command stripped: unapproved operational command]"]), "stripped_governed_command"),
    ],
)
def test_governed_recommendation_without_selected_evidence_is_normalized(result: dict[str, Any], claim: str) -> None:
    out = enforce_selected_evidence_invariant(result, [], run_id=None)
    assert out["outcome"] == "insufficient_evidence" and out["diagnostic_step"] is None
    assert NO_SELECTED_GOVERNED_EVIDENCE_NOTE in out["missing_information"]
    assert out[GOVERNED_RECOMMENDATION_INVARIANT_KEY]["normalized"] is True
    assert claim in out[GOVERNED_RECOMMENDATION_INVARIANT_KEY]["claims"]
    assert out[MANUAL_OBSERVATION_KEY] is False


def test_escalation_reason_normalizes_to_escalation_required() -> None:
    out = enforce_selected_evidence_invariant({**_recommended("Restart the unit"), "escalation_reason": "Needs field team."}, [], run_id=None)
    assert out["outcome"] == "escalation_required" and out["diagnostic_step"] is None


def test_genuine_manual_observation_may_remain_recommended_marked_manual_only() -> None:
    out = enforce_selected_evidence_invariant(_recommended("Inspect the unit's status LEDs on site", expected="LED colours"), [], run_id=None)
    assert out["outcome"] == "recommended"
    assert out[MANUAL_OBSERVATION_KEY] is True
    step = out["diagnostic_step"]
    assert step["command"] is None and step["command_source"] is None
    assert any("Manual observation only" in r for r in step["restrictions"])


@pytest.mark.parametrize("outcome", ["insufficient_evidence", "escalation_required", "error"])
def test_non_recommended_outcomes_are_untouched(outcome: str) -> None:
    result = {"outcome": outcome, "technical_interpretation": "t", "diagnostic_step": None}
    assert enforce_selected_evidence_invariant(result, [], run_id=None) is result


def test_selected_governed_evidence_leaves_recommendation_untouched() -> None:
    result = _recommended("Reset the radio unit", command="show foo NODE-1")
    assert enforce_selected_evidence_invariant(result, [_governed_ev()], run_id=None) is result


def _turn_parts(payload: dict[str, Any]) -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps(payload))]


@pytest.mark.asyncio
async def test_live_contradictory_state_is_normalized_and_rendered_safely(isolated_km_repo, monkeypatch) -> None:
    """explicit_empty + no SELECTED + catalog 0 + RECOMMENDED (operational wording, no command)."""
    await isolated_km_repo.add(_mop())
    result = await _Session(monkeypatch).turn(
        [_SEARCH, _SELECT_NONE, _CATALOG, _turn_parts(_recommended("Reset the RRU to recover the unit", expected="Unit state after the reset"))],
        "Reset the RRU now.",
        user_text="what about resetting the rru?",
    )
    record = result["records"][-1]
    assert record["outcome"] == "insufficient_evidence" and record.get("diagnostic_step") is None
    assert record[GOVERNED_RECOMMENDATION_INVARIANT_KEY]["normalized"] is True
    assert record["server_selection_contract"]["explicit_negative_selection"] is True
    final = result["final"]
    assert final != SAFE_COMPLETION_FAILURE_TEXT
    assert final.startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    assert "reset the rru" not in final.lower()
    history = (result["state"].get("troubleshooting_state") or {}).get("diagnostic_history", [])
    assert history == [], "a normalized recommendation is never recorded as a recommended check"


@pytest.mark.asyncio
async def test_manual_observation_with_explicit_empty_selection_is_presented_not_failed(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    result = await _Session(monkeypatch).turn(
        [_SEARCH, _SELECT_NONE, _CATALOG, _turn_parts(_recommended("Inspect the unit's status LEDs on site", expected="LED colours"))],
        "Please check the LEDs.",
        user_text="the unit looks dead, anything I can look at?",
    )
    record = result["records"][-1]
    assert record["outcome"] == "recommended" and record[MANUAL_OBSERVATION_KEY] is True
    final = result["final"]
    assert final.startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    assert "Suggested manual observation (no command is provided): Inspect the unit's status LEDs on site" in final
    assert "Please report: LED colours" in final
