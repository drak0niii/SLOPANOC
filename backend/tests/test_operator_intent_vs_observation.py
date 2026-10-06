"""Observation vs operator intent vs LLM proposal vs authority.

Live defect (Prompt 4 run 593e0828, T6): the operator pasted the bound `st ru` output (three units);
the specialist proposed restarting ONE unit; the target gate scanned the operator's raw message for
`key=value` and counted every unit in the pasted OUTPUT as an operator REQUEST -> AMBIGUOUS -> the
turn failed closed although the proposed target was a trusted observation of the fault.

Invariant: identifiers inside pasted / bound output are OBSERVATIONS (target facts). Operator
INTENT is only the operator's own words. The specialist's structured PROPOSAL is validated by the
server against trusted observations (AUTHORITY, unchanged). Identifiers below are fixtures only.
"""
from __future__ import annotations

import pytest

from backend.agents.technical_authority_engineer.result_validation import operator_intent
from backend.cases.troubleshooting_progression import PROGRESSION_STATE_KEY, StepStatus, TroubleshootingProgression
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _Conversation, _respond
from backend.tests.test_state_change_target_authority import _IDS, _PARAM, _READ, _STEP, _assert_blocked, _gate, _mop, _restart

UNITS_OUTPUT = (
    "FieldReplaceableUnit=RRU-1   ENABLED\n"
    "FieldReplaceableUnit=RRU-2   DISABLED   FAULTY\n"
    "FieldReplaceableUnit=RRU-3   ENABLED\n"
)
PASTED = f"{_READ} output:\n\n{UNITS_OUTPUT}"


@pytest.mark.parametrize(
    "text, carries_output, expected",
    [
        (PASTED, False, ""),  # echoed command + its output: observation only
        (f"this is the output: $ {_READ}\n{UNITS_OUTPUT}", True, ""),
        (UNITS_OUTPUT, False, ""),  # a block of output lines without an echo
        ("restart FieldReplaceableUnit=RRU-2", False, "restart FieldReplaceableUnit=RRU-2"),  # typed intent
        ("FieldReplaceableUnit=RRU-2", False, "FieldReplaceableUnit=RRU-2"),  # a single typed line stays intent
        ("FieldReplaceableUnit=RRU-2 DISABLED", True, ""),  # ... unless the server classified it as output
        (f"RRU-2 looks bad, please restart it\n{_READ}\n{UNITS_OUTPUT}", False, "RRU-2 looks bad, please restart it"),
    ],
)
def test_operator_intent_excludes_pasted_output(text: str, carries_output: bool, expected: str) -> None:
    assert operator_intent(text, [_READ], carries_output=carries_output) == expected


async def _read_presented(conv: _Conversation) -> None:
    t1 = await conv.turn("radio unit fault on the node, what should I check?", _STEP + [_respond(action_id=_IDS[_READ], action="Check the unit state")],
                         f"Run {_READ}.", {"subject_component": "radio unit"})
    assert t1["record"]["diagnostic_step"]["command"] == _READ


@pytest.mark.asyncio
async def test_pasted_output_with_several_units_is_observation_and_the_proposed_target_validates(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    """1 + 2 + 4: the result is bound (consumed) and its units become target FACTS, not requests; the
    specialist's structured proposal (one unit) is validated against them."""
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _read_presented(conv)
    t2 = await conv.turn(PASTED, _STEP + [_respond(action_id=_IDS[_PARAM], params=[{"name": "FieldReplaceableUnit", "value": "RRU-2"}],
                                                    action="Recover the faulty unit")], "Run the recovery.")
    progression = TroubleshootingProgression.model_validate(t2["state"][PROGRESSION_STATE_KEY])
    read = next(s for s in progression.steps if s.command == _READ)
    assert read.status is StepStatus.COMPLETED and read.result is not None  # pending-result consumption intact
    (decision,) = _gate(t2)["targets"]
    assert (decision["status"], decision["value"], decision["requested"]) == ("validated", "RRU-2", ["RRU-2"])
    assert t2["record"]["operational_control"]["control_stage"] == "awaiting_confirmation"  # HITL unchanged


@pytest.mark.asyncio
async def test_proposed_target_not_observed_is_still_rejected(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    """5: narrowing operator intent never widens authority."""
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _read_presented(conv)
    t2 = await conv.turn(PASTED, _STEP + [_respond(action_id=_IDS[_PARAM], params=[{"name": "FieldReplaceableUnit", "value": "RRU-7"}],
                                                    action="Recover the faulty unit")], "Run the recovery.")
    _assert_blocked(t2, "conflicting")


async def _diagnosed_with_units(conv: _Conversation) -> None:
    await _read_presented(conv)
    await conv.turn(PASTED, _STEP[:2] + [_respond(outcome="insufficient_evidence")], "Noted.")


@pytest.mark.asyncio
async def test_explicit_operator_target_intent_still_counts(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    """3: the operator's own typed target is a request (validated against the observations)."""
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed_with_units(conv)
    chosen = await _restart(conv, "restart FieldReplaceableUnit=RRU-3", _PARAM, "RRU-3")
    assert _gate(chosen)["passed"] is True and _gate(chosen)["targets"][0]["requested"] == ["RRU-3"]


@pytest.mark.asyncio
async def test_operator_intent_disagreeing_with_the_proposal_stays_ambiguous(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    await _diagnosed_with_units(conv)
    disagree = await _restart(conv, "restart FieldReplaceableUnit=RRU-1", _PARAM, "RRU-2")
    gate = _assert_blocked(disagree, "ambiguous")
    assert sorted(gate["targets"][0]["requested"]) == ["RRU-1", "RRU-2"]
