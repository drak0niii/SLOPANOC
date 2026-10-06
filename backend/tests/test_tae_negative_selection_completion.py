"""TAE safe escalation / explicit negative evidence-selection completion boundary.

Live defect (run a13fe2a4-007f-4911-8e46-39214ec5e36b): the Technical Authority Engineer
searched governed knowledge three times, returned `escalation_required` with no command and
no selection, and chat_service replaced it with SAFE_COMPLETION_FAILURE_TEXT.

Contract under test (real ChatService + real TechnicalAuthorityAgentTool, scripted LLMs):
  - recommended + command + no SELECTED evidence            -> fail closed
  - escalation/insufficient + no command + explicit select([]) -> deterministic safe response
  - escalation/insufficient + no selection decision         -> fail closed (after one bounded
                                                               selection-contract remediation)
  - explicit select([]) never authorizes a command           -> fail closed
  - unresolved applicability                                 -> existing clarification unchanged
  - AVAILABLE evidence is never promoted to SELECTED
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.team_manager.governed_knowledge_completion import SAFE_COMPLETION_FAILURE_TEXT
from backend.agents.technical_authority_engineer.agent_tool import (
    SELECTION_CONTRACT_RECORD_KEY,
    proposes_operational_step,
    requires_selection_contract_remediation,
)
from backend.agents.technical_authority_engineer.synthesis_boundary import (
    NO_GOVERNED_PROCEDURE_SELECTED_TEXT,
    render_technical_authority_negative_selection_response,
)
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_ess_service_unavailable_e2e_verification import (  # noqa: F401 (fixture)
    _S4Session,
    _fc,
    isolated_km_repo,
)
from backend.tools.knowledge import runtime as rt

_SFP_KID = "SFP-OPTICAL-DIAG-MOP"
_SFP_SECTION = f"{_SFP_KID}:v1:section-0000"
_SFP_FILENAME = "MOP_SFP Optical Diagnostics.docx"
_SFP_TITLE = "SFP Optical Diagnostics Procedure"
_SFP_COMMAND = "get SfpModule"
_SFP_CONTENT = (
    "PlugInUnit SFP_ERR FAULTY optical diagnostics | Check SFP module optical levels\n"
    "HC Commands:\n"
    f"{_SFP_COMMAND}\n"
)

_LIVE_USER_TEXT = (
    "Ericsson 4G node. st pluginunit output:\n"
    "Equipment=1,Subrack=1,Slot=3,PlugInUnit=1 Adm=UNLOCKED Op=DISABLED FAULTY, SFP_ERR\n"
    "1. What command retrieves more detailed SFP diagnostics?\n"
    "2. What command/action enables the PlugInUnit from DISABLED?"
)

_SEARCH_1 = _fc("knowledge_search", {"query_text": "Ericsson 4G PlugInUnit SFP_ERR FAULTY"})
_SEARCH_2 = _fc("knowledge_search", {"query_text": "SFP optical diagnostics"})
_SEARCH_3 = _fc("knowledge_search", {"query_text": "enable disabled PlugInUnit recovery"})
_SELECT_NONE = _fc("knowledge_select_evidence", {"selections": []})
_SELECT_SFP = _fc(
    "knowledge_select_evidence",
    {"selections": [{"knowledge_id": _SFP_KID, "version_label": "v1", "section_id": _SFP_SECTION}]},
)

_INTERPRETATION = (
    "Observed: PlugInUnit Slot=3 is Adm=UNLOCKED, Op=DISABLED and reports FAULTY with SFP_ERR. "
    "Hypothesis: the SFP module on this unit is faulty or unseated. "
    "Run `deb Slot=3` to bring the unit back into service."
)
_ESCALATION_REASON = (
    "The unit is out of service due to a hardware fault indication (SFP_ERR) and no approved governed "
    "procedure covers SFP recovery for this node."
)
_MISSING = [
    "Physical inspection result of the SFP module in Slot=3",
    "Execute `acc PlugInUnit=1 restartUnit` and report the result",
]


def _sfp_mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_SFP_KID,
        document_type=KnowledgeDocumentType.MOP,
        title=_SFP_TITLE,
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id=_SFP_FILENAME, display_name="SFP MOP"),
        sections=[
            KnowledgeSection(
                section_id=_SFP_SECTION,
                knowledge_id=_SFP_KID,
                heading="SFP optical diagnostics",
                sequence=0,
                content=_SFP_CONTENT,
                source_locator="lines:1-3",
            )
        ],
    )


def _payload(outcome: str, step: Optional[dict[str, Any]] = None, **extra: Any) -> list[types.Part]:
    body: dict[str, Any] = {
        "outcome": outcome,
        "technical_interpretation": _INTERPRETATION,
        "verified_evidence_citations": [],
        "missing_information": list(_MISSING),
        "diagnostic_step": step,
    }
    if outcome == "escalation_required":
        body["escalation_reason"] = _ESCALATION_REASON
    body.update(extra)
    return [types.Part.from_text(text=json.dumps(body))]


def _escalation(**kw: Any) -> list[types.Part]:
    return _payload("escalation_required", **kw)


def _insufficient(**kw: Any) -> list[types.Part]:
    return _payload("insufficient_evidence", **kw)


def _recommend(command: str, source: str) -> list[types.Part]:
    return _payload(
        "recommended",
        step={
            "action": "Re-enable the PlugInUnit",
            "reason": "Bring the unit back into service",
            "expected_evidence": "Op=ENABLED",
            "command": command,
            "command_source": source,
            "restrictions": [],
        },
    )


async def _run(repo: Any, monkeypatch: Any, tae_calls: list[list[types.Part]], tm_final_text: str, seed: bool = True) -> dict[str, Any]:
    if seed:
        await repo.add(_sfp_mop())
    return await _S4Session(monkeypatch).turn(tae_calls, tm_final_text, user_text=_LIVE_USER_TEXT)


def _assert_no_operational_leak(final: str) -> None:
    lowered = final.lower()
    for leaked in ("deb slot", "deb ", "acc pluginunit", "restartunit", "hget", "sfpmodule", "restart", "unlock the", "unlock plug"):
        assert leaked not in lowered, f"operational text leaked: {leaked!r} in {final!r}"


def _assert_no_available_leak(result: dict[str, Any]) -> None:
    final = result["final"]
    assert _SFP_TITLE not in final
    assert _SFP_FILENAME not in final
    assert _SFP_KID not in final
    for turn_ref in (result["state"].get("turn_source_references") or {}).values():
        if isinstance(turn_ref, dict):
            assert not turn_ref.get("knowledge_sources"), "no knowledge citation without SELECTED evidence"


def _contract(result: dict[str, Any]) -> dict[str, Any]:
    return result["records"][-1][SELECTION_CONTRACT_RECORD_KEY]


# --- Test 1: exact live defect ----------------------------------------------------------
@pytest.mark.asyncio
async def test_live_defect_escalation_with_explicit_negative_selection_renders_safe_response(isolated_km_repo, monkeypatch) -> None:
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_1, _SEARCH_2, _SEARCH_3, _SELECT_NONE, _escalation()],
        tm_final_text="To enable it, run deb Slot=3. For SFP diagnostics run get SfpModule.",
    )
    final = result["final"]
    assert final != SAFE_COMPLETION_FAILURE_TEXT
    assert final.startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    assert "Escalation required:" in final
    assert "no approved governed procedure covers SFP recovery" in final
    assert "Observed: PlugInUnit Slot=3 is Adm=UNLOCKED, Op=DISABLED" in final
    assert "Physical inspection result of the SFP module in Slot=3" in final
    _assert_no_operational_leak(final)
    _assert_no_available_leak(result)
    record = result["records"][-1]
    assert record["outcome"] == "escalation_required"
    assert record.get("diagnostic_step") is None
    assert record["approved_commands_catalog"] == []
    assert not [e for e in record["verified_evidence"] if e.get("source_type") == "governed_knowledge"]
    assert _contract(result) == {
        "governed_search_performed": True,
        "explicit_negative_selection": True,
        "operational_step_proposed": False,
        "operational_step_disposition": "none",
    }
    assert len(result["tae_llm"]._requests) == 5, "no remediation when the contract is complete"


# --- Test 2: missing explicit negative selection -----------------------------------------
@pytest.mark.asyncio
async def test_escalation_without_selection_decision_fails_closed(isolated_km_repo, monkeypatch) -> None:
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_1, _SEARCH_2, _SEARCH_3, _escalation()],
        tm_final_text="To enable it, run deb Slot=3.",
    )
    assert result["final"] == SAFE_COMPLETION_FAILURE_TEXT
    assert len(result["tae_llm"]._requests) == 5, "exactly one bounded selection-contract remediation"
    assert _contract(result)["explicit_negative_selection"] is False


@pytest.mark.asyncio
async def test_selection_contract_remediation_completed_with_empty_selection_renders_safe_response(isolated_km_repo, monkeypatch) -> None:
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_1, _escalation(), _SELECT_NONE, _escalation()],
        tm_final_text="Run deb Slot=3.",
    )
    assert result["final"].startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    _assert_no_operational_leak(result["final"])
    _assert_no_available_leak(result)


@pytest.mark.asyncio
async def test_empty_selection_without_any_search_fails_closed(isolated_km_repo, monkeypatch) -> None:
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SELECT_NONE, _escalation()],
        tm_final_text="Run deb Slot=3.",
    )
    assert result["final"] == SAFE_COMPLETION_FAILURE_TEXT
    assert _contract(result)["governed_search_performed"] is False


# --- Test 3: ungrounded command ------------------------------------------------------------
@pytest.mark.asyncio
async def test_recommended_command_without_selected_evidence_fails_closed(isolated_km_repo, monkeypatch) -> None:
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_1, _recommend("deb Slot=3", _SFP_FILENAME), _recommend("deb Slot=3", _SFP_FILENAME)],
        tm_final_text="Run deb Slot=3 to enable the unit.",
    )
    assert result["final"] == SAFE_COMPLETION_FAILURE_TEXT
    _assert_no_operational_leak(result["final"])
    assert _contract(result)["operational_step_proposed"] is True


# --- Test 4: AVAILABLE is not SELECTED -----------------------------------------------------
@pytest.mark.asyncio
async def test_matching_available_sfp_evidence_is_never_promoted_to_selected(isolated_km_repo, monkeypatch) -> None:
    observed: dict[str, Any] = {}
    original = rt.snapshot_selected_knowledge_evidence

    def _spy(run_id: str) -> list[Any]:
        selected = original(run_id)
        available = rt.get_available_knowledge_evidence(run_id)
        observed.setdefault("available", []).append(len(available.items))
        observed.setdefault("selected", []).append(len(selected))
        return selected

    monkeypatch.setattr("backend.api.chat_service.snapshot_selected_knowledge_evidence", _spy)
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_2, _escalation()],
        tm_final_text=f"Use the SFP procedure: run {_SFP_COMMAND}.",
    )
    assert max(observed["available"]) >= 1, "the matching SFP MOP was retrieved into AVAILABLE"
    assert observed["selected"] == [0], "AVAILABLE evidence was never promoted to SELECTED"
    record = result["records"][-1]
    assert not [e for e in record["verified_evidence"] if e.get("source_type") == "governed_knowledge"]
    assert record["approved_commands_catalog"] == []
    assert result["final"] == SAFE_COMPLETION_FAILURE_TEXT
    _assert_no_operational_leak(result["final"])
    _assert_no_available_leak(result)


@pytest.mark.asyncio
async def test_positive_selection_of_sfp_evidence_keeps_normal_grounded_path(isolated_km_repo, monkeypatch) -> None:
    """Matrix row 1: explicitly SELECTED, approved, MATCH evidence -> normal grounded recommendation."""
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_2, _SELECT_SFP, _recommend(_SFP_COMMAND, _SFP_FILENAME)],
        tm_final_text=f"Run {_SFP_COMMAND} to retrieve SFP diagnostics.",
    )
    record = result["records"][-1]
    assert record["diagnostic_step"]["command"] == _SFP_COMMAND
    assert result["final"] != SAFE_COMPLETION_FAILURE_TEXT
    assert _SFP_COMMAND in result["final"]
    assert NO_GOVERNED_PROCEDURE_SELECTED_TEXT not in result["final"]


# --- Test 5: explicit empty selection never authorizes a command ----------------------------
@pytest.mark.asyncio
async def test_explicit_empty_selection_with_recommended_command_fails_closed(isolated_km_repo, monkeypatch) -> None:
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[
            _SEARCH_1,
            _SELECT_NONE,
            _recommend("deb Slot=3", _SFP_FILENAME),
            _recommend("deb Slot=3", _SFP_FILENAME),  # remediation ignored
        ],
        tm_final_text="Run deb Slot=3.",
    )
    record = result["records"][-1]
    assert record.get("diagnostic_step") is None
    assert record["approved_commands_catalog"] == []
    assert _contract(result)["operational_step_proposed"] is True
    assert result["final"] == SAFE_COMPLETION_FAILURE_TEXT


@pytest.mark.asyncio
async def test_escalation_carrying_a_command_fails_closed_even_with_empty_selection(isolated_km_repo, monkeypatch) -> None:
    """Defensive: validation strips a step from a non-recommended outcome, but the raw payload
    carried a command, so the safe path is never entered."""
    step = {"action": "Enable the unit", "reason": "r", "expected_evidence": "e", "command": "deb Slot=3", "command_source": None, "restrictions": []}
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_1, _SELECT_NONE, _escalation(step=step)],
        tm_final_text="Run deb Slot=3.",
    )
    assert result["records"][-1].get("diagnostic_step") is None
    assert _contract(result)["operational_step_proposed"] is True
    assert result["final"] == SAFE_COMPLETION_FAILURE_TEXT


# --- Test 6: applicability clarification unchanged ---------------------------------------------
@pytest.mark.asyncio
async def test_applicability_clarification_takes_precedence(isolated_km_repo, monkeypatch) -> None:
    mop = _sfp_mop().model_copy(update={"applicability": Applicability(dimensions={"dim_alpha": ["a1"]})})
    await isolated_km_repo.add(mop)
    result = await _S4Session(monkeypatch).turn(
        [_SEARCH_2, _insufficient()], "Run deb Slot=3.", user_text=_LIVE_USER_TEXT
    )
    final = result["final"]
    assert "To validate the applicable governed procedure I still need: dim_alpha." in final
    assert NO_GOVERNED_PROCEDURE_SELECTED_TEXT not in final
    assert final != SAFE_COMPLETION_FAILURE_TEXT
    assert len(result["tae_llm"]._requests) == 2, "clarification path is not subjected to selection remediation"
    _assert_no_operational_leak(final)


# --- Test 7: safe insufficient evidence ---------------------------------------------------------
@pytest.mark.asyncio
async def test_insufficient_evidence_with_explicit_negative_selection_renders_safe_response(isolated_km_repo, monkeypatch) -> None:
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_1, _SEARCH_3, _SELECT_NONE, _insufficient()],
        tm_final_text="Run deb Slot=3.",
    )
    final = result["final"]
    assert final.startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    assert "Escalation required:" not in final
    assert "Technical assessment:" in final
    assert "Missing information:" in final
    _assert_no_operational_leak(final)
    _assert_no_available_leak(result)


@pytest.mark.asyncio
async def test_insufficient_evidence_without_selection_decision_fails_closed(isolated_km_repo, monkeypatch) -> None:
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_1, _insufficient()],
        tm_final_text="Run deb Slot=3.",
    )
    assert result["final"] == SAFE_COMPLETION_FAILURE_TEXT


# --- Test 8: Team Manager free-text command leakage ----------------------------------------------
@pytest.mark.asyncio
async def test_team_manager_prose_command_never_reaches_safe_escalation(isolated_km_repo, monkeypatch) -> None:
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_1, _SELECT_NONE, _escalation()],
        tm_final_text="The unit is faulty. run deb Slot=3 and then acc PlugInUnit=1 restartUnit to recover it.",
    )
    final = result["final"]
    assert final.startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    assert "The unit is faulty." not in final, "Team Manager prose is never used on this path"
    _assert_no_operational_leak(final)


# --- Invariant 6 on the real path: TAE fail-closed never routes to Incident Manager remediation ----
@pytest.mark.asyncio
async def test_tae_fail_closed_does_not_invoke_incident_manager_remediation(isolated_km_repo, monkeypatch) -> None:
    invoked: list[Any] = []

    async def _forbidden(*args: Any, **kwargs: Any) -> Any:
        invoked.append(kwargs)
        return "IM Remediation Answer", []

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _forbidden)
    result = await _run(
        isolated_km_repo,
        monkeypatch,
        tae_calls=[_SEARCH_1, _escalation()],
        tm_final_text="Run deb Slot=3.",
    )
    assert invoked == []
    assert result["final"] == SAFE_COMPLETION_FAILURE_TEXT


# --- Renderer / predicate units (production functions) --------------------------------------------
def _record(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "outcome": "escalation_required",
        "technical_interpretation": "Observed fault.",
        "missing_information": [],
        "escalation_reason": "Hardware fault.",
        "diagnostic_step": None,
        "approved_commands_catalog": [],
        "verified_evidence": [],
        SELECTION_CONTRACT_RECORD_KEY: {
            "governed_search_performed": True,
            "explicit_negative_selection": True,
            "operational_step_proposed": False,
        },
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "overrides",
    [
        {"outcome": "recommended"},
        {"outcome": "error"},
        {"diagnostic_step": {"action": "x", "command": "deb Slot=3"}},
        {"approved_commands_catalog": [{"command": "alt"}]},
        {"verified_evidence": [{"source_type": "governed_knowledge", "source_id": "K:v1:s"}]},
        {"applicability_clarification": {"missing_dimensions": ["vendor"], "text": "t"}},
        {SELECTION_CONTRACT_RECORD_KEY: None},
        {SELECTION_CONTRACT_RECORD_KEY: {"governed_search_performed": True, "explicit_negative_selection": False, "operational_step_proposed": False}},
        {SELECTION_CONTRACT_RECORD_KEY: {"governed_search_performed": True, "explicit_negative_selection": True, "operational_step_proposed": True}},
        {SELECTION_CONTRACT_RECORD_KEY: {"governed_search_performed": True, "explicit_negative_selection": True}},
        {SELECTION_CONTRACT_RECORD_KEY: {"governed_search_performed": False, "explicit_negative_selection": True, "operational_step_proposed": False}},
    ],
)
def test_negative_selection_renderer_requires_every_condition(overrides: dict[str, Any]) -> None:
    assert render_technical_authority_negative_selection_response(_record(**overrides)) is None


def test_negative_selection_renderer_accepts_complete_contract() -> None:
    text = render_technical_authority_negative_selection_response(_record())
    assert text is not None and text.startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    assert render_technical_authority_negative_selection_response(None) is None


def test_proposes_operational_step_covers_all_outcomes() -> None:
    assert proposes_operational_step({"outcome": "recommended", "diagnostic_step": {"command": "alt"}})
    assert proposes_operational_step({"outcome": "escalation_required", "diagnostic_step": {"command_source": "X.docx"}})
    assert proposes_operational_step({"outcome": "insufficient_evidence", "diagnostic_step": {"action": "Enable the unit"}})
    assert not proposes_operational_step({"outcome": "recommended", "diagnostic_step": {"action": "Observe LEDs", "command": None}})
    assert not proposes_operational_step({"outcome": "escalation_required", "diagnostic_step": None})


def test_selection_contract_remediation_never_selects_on_behalf_of_specialist() -> None:
    run_id = "neg-selection-contract-unit"
    escalation = {"outcome": "escalation_required", "diagnostic_step": None}
    try:
        assert requires_selection_contract_remediation(run_id, [escalation]) is False, "no search -> no remediation"
        rt.get_or_init_run_state(run_id)
        assert requires_selection_contract_remediation(run_id, [escalation]) is True
        assert rt.snapshot_selected_knowledge_evidence(run_id) == []
        rt.select_evidence(run_id, [])
        assert rt.has_explicit_empty_knowledge_selection(run_id) is True
        assert rt.snapshot_selected_knowledge_evidence(run_id) == []
        assert requires_selection_contract_remediation(run_id, [escalation]) is False
    finally:
        rt.discard_knowledge_run_evidence_state(run_id)
