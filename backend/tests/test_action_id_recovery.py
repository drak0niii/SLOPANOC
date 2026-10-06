"""Technical Authority Engineer ProcedureAction id recovery: ONE bounded, tool-free re-selection.

Live defect (Prompt 4 acceptance, session 0233596d, turn 3): the server issued the current-run
ProcedureAction `pa-19ceca4905357d33fb50`; the specialist returned `pa-19ceca4905353d33fb50`; the
resolver correctly rejected it as UNKNOWN_ACTION and the turn failed closed.

Invariant under test:
    recommended step whose id this run never issued + a current-run issued catalog exists
    -> ONE tool-free re-selection over THAT catalog in the same specialist session
    -> exact match of an issued id? adopted, then the normal resolver / authority / progression chain
    -> otherwise the original answer fails closed through the existing UNKNOWN_ACTION path.
The server never corrects, completes or substitutes an id (no fuzzy, prefix or historical matching).

The specialist is the PRODUCTION agent (tools and callbacks) with only its model scripted.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from google.adk.models.llm_request import LlmRequest

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.action_id_recovery import unknown_action_id
from backend.tests.test_applicability_blocked_governed_action import CATALOG, DOC, SEARCH, _governed
from backend.tests.test_operational_continuation_routing import ALT_ID, PLUGIN_ID, _authorized, _events, _trace
from backend.tests.test_tae_structured_output_recovery import (  # noqa: F401 (fixtures)
    PLUGIN_STEP,
    _assert_alt_result_bound,
    _assert_tool_free,
    _requests,
    _result_turn,
    _tool_counts,
    _user_texts,
    conversation,
    isolated_km_repo,
)

RECOVERY_MARKER = "SERVER PROCEDUREACTION ID RECOVERY"
# One character changed, exactly the live corruption shape (an opaque id, never a typo to "fix").
MISTYPED = PLUGIN_ID[:-5] + ("3" if PLUGIN_ID[-5] != "3" else "4") + PLUGIN_ID[-4:]
OTHER_INVALID = "pa-" + "0" * (len(PLUGIN_ID) - 3)
HISTORICAL = "pa-" + "1" * (len(PLUGIN_ID) - 3)


def _recovery(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return _events(turn, "action_id_recovery")


def _resolution(turn: dict[str, Any]) -> dict[str, Any]:
    return _trace(turn)["action_resolutions"][-1]


def _recovery_catalog(request: LlmRequest) -> list[str]:
    text = next(t for t in reversed(_user_texts(request)) if RECOVERY_MARKER in t)
    return [e["action_id"] for e in json.loads(text.split("re-validated by the server):\n", 1)[1])]


def _assert_failed_closed(turn: dict[str, Any], action_id: str) -> None:
    resolution = _resolution(turn)
    assert resolution["action_id"] == action_id and resolution["status"] == "unknown_action"
    assert not [c for c in _trace(turn).get("command_authority", []) if c["decision"] == "authorized"]
    assert "`st pluginunit`" not in turn["final"]


async def _start(conversation: Any) -> Any:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    from backend.tests.test_operational_continuation_routing import _alt_presented

    await _alt_presented(conv)
    return conv


def test_mistyped_id_differs_by_one_character() -> None:
    assert MISTYPED != PLUGIN_ID and len(MISTYPED) == len(PLUGIN_ID)
    assert sum(a != b for a, b in zip(MISTYPED, PLUGIN_ID)) == 1


# 1. Valid current-run id -> no recovery call


@pytest.mark.asyncio
async def test_valid_current_run_id_makes_no_recovery_call(conversation) -> None:
    conv = await _start(conversation)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, PLUGIN_STEP])
    assert not _recovery(t2) and len(_requests(t2)) == 4
    assert _resolution(t2)["action_id"] == PLUGIN_ID and _authorized(t2, "st pluginunit")


# 2 / 3 / 8 / 9. Mistyped id -> ONE tool-free re-selection -> exact id -> normal validation continues


@pytest.mark.asyncio
async def test_mistyped_id_is_reselected_once_and_validated_normally(conversation) -> None:
    conv = await _start(conversation)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, _governed(MISTYPED, "Check plug-in unit states"), PLUGIN_STEP])
    request_event, result_event = _recovery(t2)
    assert request_event["initial_action_id"] == MISTYPED and request_event["reason"] == "unknown_action"
    assert request_event["retry"] == 1 and request_event["tools_enabled"] is False
    assert result_event["returned_action_id"] == PLUGIN_ID and result_event["exact_match"] is True
    # Exactly one extra, tool-free call over THIS run's issued catalog; nothing searched or issued again.
    assert len(_requests(t2)) == 5
    _assert_tool_free(_requests(t2)[-1])
    issued = [a["action_id"] for c in _trace(t2)["action_catalogs"] for a in c["actions"]]
    assert sorted(_recovery_catalog(_requests(t2)[-1])) == sorted(issued) and PLUGIN_ID in issued
    assert _tool_counts(t2) == (1, 1, 1)
    # The normal chain decided: resolver, Command Authority, progression.
    (resolution,) = _trace(t2)["action_resolutions"]
    assert resolution["action_id"] == PLUGIN_ID and resolution["status"] == "resolved"
    assert _authorized(t2, "st pluginunit") and "`st pluginunit`" in t2["final"]
    _assert_alt_result_bound(t2)


# 4. Re-selection returns another invalid id -> fail closed, never a second re-selection


@pytest.mark.asyncio
async def test_reselection_returning_another_invalid_id_fails_closed(conversation) -> None:
    conv = await _start(conversation)
    t2 = await _result_turn(conv, [
        SEARCH, DOC.select(), CATALOG, _governed(MISTYPED, "Check plug-in unit states"),
        _governed(OTHER_INVALID, "Check plug-in unit states"), PLUGIN_STEP,
    ])
    _, result_event = _recovery(t2)
    assert result_event["returned_action_id"] == OTHER_INVALID and result_event["exact_match"] is False
    assert len(_requests(t2)) == 5, "one re-selection; the scripted third answer is never requested"
    _assert_failed_closed(t2, MISTYPED)


# 5. Fuzzy-similar invalid id -> never auto-corrected by the server


def test_server_never_matches_a_similar_id() -> None:
    issued = {PLUGIN_ID, ALT_ID}
    for similar in (MISTYPED, PLUGIN_ID[:-1], PLUGIN_ID + "0", PLUGIN_ID.upper(), PLUGIN_ID[:12]):
        payload = {"outcome": "recommended", "diagnostic_step": {"procedure_action_id": similar}}
        assert unknown_action_id(payload, issued) == similar


@pytest.mark.asyncio
@pytest.mark.parametrize("reselected", [None, MISTYPED], ids=["declined", "same-near-miss"])
async def test_similar_id_is_never_substituted(conversation, reselected) -> None:
    conv = await _start(conversation)
    # Exactly one issued id is one character away; the re-selection names none, or the near miss again.
    t2 = await _result_turn(conv, [
        SEARCH, DOC.select(), CATALOG, _governed(MISTYPED, "Check plug-in unit states"),
        _governed(reselected, "Check plug-in unit states"),
    ])
    _, result_event = _recovery(t2)
    assert result_event["returned_action_id"] == reselected and result_event["exact_match"] is False
    _assert_failed_closed(t2, MISTYPED)
    assert PLUGIN_ID not in [r["action_id"] for r in _trace(t2)["action_resolutions"]]


# 6. Historical id -> never accepted


@pytest.mark.asyncio
async def test_historical_action_id_is_not_accepted(conversation) -> None:
    conv = await _start(conversation)
    pa.record_issued_actions("run-previous", [pa.ProcedureAction.model_construct(action_id=HISTORICAL)])
    try:
        t2 = await _result_turn(conv, [
            SEARCH, DOC.select(), CATALOG, _governed(HISTORICAL, "Check plug-in unit states"),
            _governed(HISTORICAL, "Check plug-in unit states"),
        ])
    finally:
        pa.discard_issued_actions("run-previous")
    request_event, result_event = _recovery(t2)
    assert request_event["initial_action_id"] == HISTORICAL and result_event["exact_match"] is False
    _assert_failed_closed(t2, HISTORICAL)


# 7. No current-run catalog -> fail closed without a recovery call


@pytest.mark.asyncio
async def test_no_current_run_catalog_fails_closed_without_recovery(conversation) -> None:
    conv = await _start(conversation)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), _governed(MISTYPED, "Check plug-in unit states"), PLUGIN_STEP])
    assert not _recovery(t2) and all(r.tools_dict for r in _requests(t2))
    _assert_failed_closed(t2, MISTYPED)


# 8 / 9. A tool call attempted during re-selection never executes


@pytest.mark.asyncio
async def test_tool_call_during_reselection_never_executes(conversation) -> None:
    conv = await _start(conversation)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, _governed(MISTYPED, "Check plug-in unit states"), SEARCH, CATALOG])
    assert _tool_counts(t2) == (1, 1, 1), "no search, selection or catalog issuance repeated"
    _assert_tool_free(_requests(t2)[4])
    _, result_event = _recovery(t2)
    assert result_event["exact_match"] is False
    _assert_failed_closed(t2, MISTYPED)


# 10. A KNOWN id rejected by a semantic gate is never re-selected


@pytest.mark.asyncio
async def test_known_id_rejected_downstream_is_not_reselected(conversation) -> None:
    conv = await _start(conversation)
    # ALT_ID is issued in this run but is the step whose result was just supplied: progression decides.
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, _governed(ALT_ID, "Check active alarms on the node")])
    assert not _recovery(t2) and len(_requests(t2)) == 4
    assert _resolution(t2)["action_id"] == ALT_ID and _resolution(t2)["status"] != "unknown_action"
