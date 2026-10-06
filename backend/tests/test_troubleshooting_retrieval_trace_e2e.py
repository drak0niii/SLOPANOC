"""End-to-end: a real ChatService turn with the real TechnicalAuthorityAgentTool persists
a per-turn governed-knowledge diagnostic trace (searches, ranked results, selection
state, command-authority decisions, TAE outcome) and logs it -- at WARNING when the
turn failed closed. Observability only: the trace is never read back as evidence.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from backend.tests.test_ess_service_unavailable_e2e_verification import (  # noqa: F401 (fixture)
    _S4_CONTENT,
    _S4_FILENAME,
    _S4_KID,
    _S4_SEARCH,
    _S4_SELECT,
    _S4Session,
    _s4_mop,
    _s4_response,
    isolated_km_repo,
)
from backend.tools.knowledge.diagnostic_trace import RETRIEVAL_DIAGNOSTICS_STATE_KEY

_USER = "Ericsson 5G: SFP Module Failure / Optical Signal Loss on a PlugInUnit. What should I check next?"


def _only_turn(state: dict[str, Any]) -> dict[str, Any]:
    persisted = state.get(RETRIEVAL_DIAGNOSTICS_STATE_KEY) or {}
    assert len(persisted) == 1
    return next(iter(persisted.values()))


@pytest.mark.asyncio
async def test_rejected_model_command_is_fully_reconstructable_from_persisted_trace(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_s4_mop())
    result = await _S4Session(monkeypatch).turn(
        [_S4_SEARCH, _S4_SELECT, _s4_response("st rilink", _S4_FILENAME, action="Check RiLink status")],
        "Check RiLink status. Run st rilink.",
        user_text=_USER,
    )
    assert "st rilink" not in result["final"]
    turn = _only_turn(result["state"])

    search = turn["searches"][0]
    assert search["query_text"] == "ESS Service Unavailable"
    assert search["status"] == "success" and search["retrieval_mode"] == "lexical"
    top = search["results"][0]
    assert (top["rank"], top["knowledge_id"], top["selection_state"]) == (1, _S4_KID, "SELECTED")
    assert top["applicability_outcome"] == "match" and top["lifecycle_status"] == "approved"
    assert top["sparse_score"] is not None and top["fused_score"] is not None

    assert turn["selections"][0]["status"] == "accepted"
    assert [s["knowledge_id"] for s in turn["selected"]] == [_S4_KID]

    rejected = [c for c in turn["command_authority"] if c["command"] == "st rilink"]
    assert rejected and rejected[0]["decision"] == "rejected" and rejected[0]["stage"] == "grounding"
    assert rejected[0]["reason"] == "command text/template not found (or prohibited) in the cited selected section"

    tae = turn["technical_authority"]
    assert tae["outcome"] is not None and tae["command"] is None
    assert turn["final_response_kind"] in ("answer", "no_governed_procedure_selected", "governed_fail_closed")
    # Identities and decisions only -- never procedure body text.
    assert "Restart the affected radio" not in json.dumps(turn)
    assert _S4_CONTENT.splitlines()[0] not in json.dumps(turn)


@pytest.mark.asyncio
async def test_fail_closed_turn_logs_trace_at_warning_and_persists_it(isolated_km_repo, monkeypatch, caplog) -> None:
    await isolated_km_repo.add(_s4_mop())
    escalation = _s4_response(None, None)
    escalation[0].text = json.dumps(
        {"outcome": "escalation_required", "technical_interpretation": "t", "escalation_reason": "r", "missing_information": []}
    )
    with caplog.at_level(logging.INFO, logger="backend.knowledge.retrieval_trace"):
        result = await _S4Session(monkeypatch).turn([_S4_SEARCH, escalation], "Run st rilink.", user_text=_USER)

    turn = _only_turn(result["state"])
    assert turn["final_response_kind"] == "governed_fail_closed"
    assert turn["searches"][0]["results"][0]["selection_state"] == "AVAILABLE"
    assert turn["selected"] == []
    assert turn["technical_authority"]["selection_contract"]["explicit_negative_selection"] is False
    records = [r for r in caplog.records if r.name == "backend.knowledge.retrieval_trace"]
    assert records and records[-1].levelno == logging.WARNING
    message = records[-1].getMessage()
    assert "final=governed_fail_closed" in message and "SEARCH 1" in message and "SELECTED none" in message


def test_tae_prompt_requires_focused_generic_search_queries() -> None:
    from backend.agents.technical_authority_engineer.prompts import TECHNICAL_AUTHORITY_ENGINEER_INSTRUCTION as p

    assert "CURRENT diagnostic objective" in p
    assert "fault/alarm identity, observed symptom or error code, affected component, and known vendor/technology" in p
    assert "never paste the whole conversation, raw command output, or prior answers into a query" in p
    for vendor_specific in ("SFP", "RiLink", "Ericsson"):
        assert vendor_specific not in p, "search guidance must stay generic"
