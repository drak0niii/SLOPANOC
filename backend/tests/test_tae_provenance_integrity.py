"""TAE provenance + citation integrity + false-gap classification (live session 8fe20400).

Investigation: a valid governed diagnostic action existed in the current-run ProcedureAction catalog,
but the integrity callback's citation check (validation.py) destroyed the recommendation BEFORE the
server resolved the action, because none of the model's free-text citation strings matched one of the
accepted aliases. Fresh live runs reproduced three shapes of the same weakness -- server-owned
structured provenance outranked by model-written source strings:
    1. catalog action id + unsupported citation        -> integrity changed the step to insufficient
    2. legacy free-text command + document filename     -> ambiguous across two SELECTED sections
    3. evidence need without action id + MATCH catalog  -> false "no approved acquisition action" gap

Authority order under test: SERVER-OWNED STRUCTURED PROVENANCE > MODEL-WRITTEN CITATION STRINGS.
A model action id alone is never trusted: the server re-derives it (issued this run, from this run's
SELECTED, approved, MATCH evidence) and Command Authority still decides; otherwise it fails closed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import agent_tool as tae_agent_tool
from backend.agents.technical_authority_engineer import evidence_acquisition as ea
from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer import validation
from backend.agents.technical_authority_engineer.evidence_acquisition import (
    AcquisitionOutcome,
    DiscoveryState,
    GovernedActionState,
    decide_acquisition,
    requirement_from_proposal,
)
from backend.agents.technical_authority_engineer.gap_recovery import action_choice_instruction, viable_governed_alternatives
from backend.agents.technical_authority_engineer.validation import (
    INTEGRITY_UNRESOLVED_CITATION_TEXT,
    integrity_decisions,
    resolve_command_source,
    validate_technical_authority_payload,
)
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.cases.evidence_identity import EvidenceIdentity
from backend.cases.evidence_model import GapReason
from backend.cases.troubleshooting_progression import FaultProgression, TroubleshootingProgression
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_applicability_blocked_governed_action import _fc, _payload, _trace, conversation  # noqa: F401
from backend.tests.test_evidence_acquisition_architecture import ALARM_OUTPUT
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tools.knowledge.diagnostic_trace import format_diagnostic_trace

# A live-shaped governed document: one filename, several sections; a section id containing ':'.
KID, VER = "A5-LIVE-MOP", "v1"
FILE = "MOP_Live Alarms Resolution.docx"
TITLE = "MOP_Live Alarms Resolution"
S_HC = "25:A5-LIVE-MOP2:v112:section-0000"
S_LOG = "25:A5-LIVE-MOP2:v112:section-0016"
S_HC2 = "25:A5-LIVE-MOP2:v112:section-0013"
HC_TEXT = "Service unavailable alarms resolution\nHC Commands:\nalt\nst ru\n"
LOG_TEXT = "Service unavailable alarms resolution logs\nHC Logs:\nVDES0589@scp-4-amos(FDDENM2) ~]$ amos NODE-1\nNODE-1> alt\nNr of active alarms are: 4\n"
HC2_TEXT = "Service unavailable alarms resolution repeat check\nHC Commands:\nalt\n"
EMPTY_KID = "A5-NOTES-MOP"
EMPTY_SECTION = "A5-NOTES-MOP:v1:section-0000"


def _doc(sections: list[tuple[str, str]], kid: str = KID, filename: str = FILE, title: str = TITLE) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=kid, document_type=KnowledgeDocumentType.MOP, title=title,
        version=KnowledgeVersion(label=VER, effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id=filename, display_name=filename),
        sections=[KnowledgeSection(section_id=sid, knowledge_id=kid, heading=None, sequence=i, content=text, source_locator=f"l{i}")
                  for i, (sid, text) in enumerate(sections)],
    )


def _key(section: str, kid: str = KID) -> dict[str, str]:
    return {"knowledge_id": kid, "version_label": VER, "section_id": section}


def _select(*sections: str) -> list[types.Part]:
    return _fc("knowledge_select_evidence", {"selections": [_key(s) for s in sections]})


def _aid(template: str, section: str = S_HC) -> str:
    return pa._action_id(KID, VER, section, template)


SEARCH = _fc("knowledge_search", {"query_text": "service unavailable alarms resolution"})
CATALOG = _fc("procedure_action_catalog", {})
ALT = _aid("alt")
UNSUPPORTED = "MOP Live Alarms (abridged copy)"


def _governed(action_id: Optional[str], citations: list[str]) -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": "Active alarms must be listed first.",
        "verified_evidence_citations": citations,
        "diagnostic_step": {"action": "Check for active alarms on the node.", "reason": "r", "expected_evidence": "Active alarm list",
                            "procedure_action_id": action_id, "command": None, "command_source": None, "restrictions": []},
    }))]


def _legacy(command: str, source: str) -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": "Active alarms must be listed first.", "verified_evidence_citations": [source],
        "diagnostic_step": {"action": "Check for active alarms on the node.", "reason": "r", "expected_evidence": "Active alarm list",
                            "command": command, "command_source": source, "restrictions": []},
    }))]


def _method_less(description: str = "Active alarm list of the node") -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "insufficient_evidence", "technical_interpretation": "The active alarms are needed.", "verified_evidence_citations": [],
        "missing_information": [], "required_evidence": [{"kind": "diagnostic_result", "description": description}],
    }))]


def _events(turn: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == stage]


def _authorized(turn: dict[str, Any], command: str) -> list[dict[str, Any]]:
    return [c for c in _trace(turn).get("command_authority", []) if c["decision"] == "authorized" and c["command"] == command]


def _record(turn: dict[str, Any]) -> dict[str, Any]:
    return turn["records"][-1]


async def _turn(conv: Any, calls: list[list[types.Part]], text: str = "how do I troubleshoot the service unavailable alarm?",
                tm_text: str = "Please run the check shown.") -> dict[str, Any]:
    return await conv.turn(text, calls, tm_text)


# =============================================================================================
# Live model shapes (e2e: production specialist agent + callbacks, scripted model)
# =============================================================================================


@pytest.mark.asyncio
async def test_a_shape1_action_id_with_filename_citation_resolves_authorizes_renders(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT), (S_LOG, LOG_TEXT)]))
    t = await _turn(conv, [SEARCH, _select(S_HC), CATALOG, _governed(ALT, [FILE])])
    assert _record(t)["diagnostic_step"]["command"] == "alt" and _authorized(t, "alt")
    assert _record(t)["server_selection_contract"]["operational_step_disposition"] == "accepted"
    (callback,) = [e for e in _events(t, "integrity") if e["phase"] == "integrity_callback"]
    assert callback["citation_resolution"] == ["alias"] and callback["check_triggered"] == "citation_matches_selected_evidence"


@pytest.mark.asyncio
async def test_b_shape2_action_id_with_unsupported_citation_still_resolves_through_server_provenance(conversation) -> None:  # noqa: F811
    """The live 8fe20400 shape: before the repair the integrity callback turned this into
    INSUFFICIENT_EVIDENCE and the resolver never ran."""
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT), (S_LOG, LOG_TEXT)]))
    t = await _turn(conv, [SEARCH, _select(S_HC), CATALOG, _governed(ALT, [UNSUPPORTED])],
                    tm_text="Please check the active alarms using the command `alt`.")
    record = _record(t)
    assert record["outcome"] == "recommended" and record["diagnostic_step"]["command"] == "alt" and _authorized(t, "alt")
    resolution = _trace(t)["action_resolutions"][-1]
    assert (resolution["action_id"], resolution["status"], resolution["command_authority"]) == (ALT, "resolved", "authorized")
    # The citation never became authority: it is replaced by the resolved action's own source.
    assert record["verified_evidence_citations"] == [f"{KID}:{VER}:{S_HC}"]
    phases = {e["phase"]: e for e in _events(t, "integrity")}
    assert phases["integrity_callback"]["check_triggered"] == "citation_unresolved_but_action_server_resolvable"
    assert phases["integrity_callback"]["action"] == "action_resolution_deferred" and phases["integrity_callback"]["final_outcome"] == "recommended"
    assert phases["post_resolution"]["check_triggered"] == "citation_replaced_by_action_provenance" and phases["post_resolution"]["action"] == "proceed"
    assert "`alt`" in t["final"]
    # O: trace proof.
    rendered = format_diagnostic_trace(_trace(t))
    assert "INTEGRITY phase=integrity_callback original=recommended final=recommended" in rendered
    assert "check=citation_unresolved_but_action_server_resolvable decision=action_resolution_deferred" in rendered
    assert "check=citation_replaced_by_action_provenance decision=proceed" in rendered


@pytest.mark.asyncio
async def test_g_shape3_legacy_filename_source_resolves_to_the_one_grounding_section(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT), (S_LOG, LOG_TEXT)]))
    t = await _turn(conv, [SEARCH, _select(S_HC, S_LOG), _legacy("alt", FILE)])
    record = _record(t)
    assert record["diagnostic_step"]["command"] == "alt" and record["diagnostic_step"]["command_source"] == f"{KID}:{VER}:{S_HC}"
    assert any(c["source_id"] == f"{KID}:{VER}:{S_HC}" for c in _authorized(t, "alt"))


@pytest.mark.asyncio
async def test_h_legacy_filename_source_grounded_by_two_sections_fails_closed(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT), (S_HC2, HC2_TEXT)]))
    t = await _turn(conv, [SEARCH, _select(S_HC, S_HC2), _legacy("alt", FILE), _legacy("alt", FILE)])
    assert not _authorized(t, "alt") and not (_record(t).get("diagnostic_step") or {}).get("command")
    assert _record(t)["server_selection_contract"]["operational_step_disposition"] == "command_authority_rejected"


@pytest.mark.asyncio
async def test_j_k_shape4_no_action_id_with_selected_catalog_asks_once_and_records_no_false_gap(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT), (S_LOG, LOG_TEXT)]))
    t = await _turn(conv, [SEARCH, _select(S_HC), _method_less(), _governed(ALT, [FILE])])
    assert t["tae_calls"] == 4, "exactly one bounded choose-action request"
    choice = _record(t)["action_choice"]
    assert set(choice["offered"]) == {ALT, _aid("st ru")} and choice["chosen"] == ALT and choice["declined"] is False
    assert _record(t)["diagnostic_step"]["command"] == "alt" and _authorized(t, "alt")
    assert t["progression"].acquisition_gaps == [], "a valid governed method exists: never a no-method gap"
    assert "ACTION CHOICE offered=" in format_diagnostic_trace(_trace(t))


@pytest.mark.asyncio
async def test_j_declined_choice_after_valid_offer_records_the_need_without_a_false_gap_claim(conversation) -> None:  # noqa: F811
    """No answer to the choose-action request (the specialist keeps a method-less need): only that
    explicit decision over the server-supplied valid set allows a gap, and it is not terminal."""
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT), (S_LOG, LOG_TEXT)]))
    t = await _turn(conv, [SEARCH, _select(S_HC), _method_less("Node synchronization status"), _method_less("Node synchronization status")])
    choice = _record(t)["action_choice"]
    assert choice["declined"] is True and choice["chosen"] is None
    assert _record(t)["evidence_acquisition"]["terminal"] is False
    assert not _authorized(t, "alt")


@pytest.mark.asyncio
async def test_l_shape5_no_action_id_and_truly_empty_catalog_allows_the_real_gap(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(EMPTY_SECTION, "Service unavailable alarms resolution notes: escalate unresolved cases to the regional team.\n")], kid=EMPTY_KID, filename="notes.docx", title="Notes"))
    t = await _turn(conv, [SEARCH, _fc("knowledge_select_evidence", {"selections": [_key(EMPTY_SECTION, EMPTY_KID)]}), _method_less()])
    assert "action_choice" not in _record(t)
    assert _record(t)["evidence_acquisition"]["outcome"] == "gap"
    assert _record(t)["evidence_acquisition"]["gap_reason"] == GapReason.NO_APPROVED_ACQUISITION_ACTION.value
    assert not [c for c in _trace(t).get("command_authority", []) if c["decision"] == "authorized"]


# =============================================================================================
# Negative cases: the model's id is never trusted on its own
# =============================================================================================


@pytest.mark.asyncio
async def test_c_m_action_id_without_current_selection_fails_closed_after_the_bounded_retry(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT)]))
    t = await _turn(conv, [SEARCH, _governed(ALT, [FILE]), _governed(ALT, [FILE])])
    assert t["tae_calls"] == 3, "the existing bounded selection retry ran"
    assert not _authorized(t, "alt") and not (_record(t).get("diagnostic_step") or {}).get("command")
    callback = [e for e in _events(t, "integrity") if e["phase"] == "integrity_callback"]
    assert callback[0]["check_triggered"] == "citation_unresolved_no_selected_evidence" and callback[0]["final_outcome"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_d_historical_action_id_issued_only_in_a_previous_run_fails_closed(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT)]))
    t1 = await _turn(conv, [SEARCH, _select(S_HC), CATALOG, _governed(ALT, [FILE])])
    assert _record(t1)["diagnostic_step"]["command"] == "alt"
    # The alt result completes the step; the specialist then reuses the old id without this run's catalog.
    t2 = await conv.turn(ALARM_OUTPUT, [SEARCH, _select(S_HC), _governed(ALT, [UNSUPPORTED])], "Okay.")
    assert _trace(t2)["action_resolutions"][-1]["status"] == "unknown_action"
    assert _record(t2)["outcome"] == "insufficient_evidence" and not _authorized(t2, "alt")
    post = [e for e in _events(t2, "integrity") if e["phase"] == "post_resolution"]
    assert post and post[-1]["check_triggered"] == "action_provenance_not_proven" and post[-1]["action"] == "fail_closed_insufficient_evidence"
    assert _record(t2)["server_selection_contract"]["operational_step_disposition"] == "integrity_rejected"


@pytest.mark.asyncio
async def test_e_invented_action_id_fails_closed(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT)]))
    t = await _turn(conv, [SEARCH, _select(S_HC), CATALOG, _governed("pa-00000000000000000000", [UNSUPPORTED])])
    assert _trace(t)["action_resolutions"][-1]["status"] == "unknown_action"
    assert _record(t)["outcome"] == "insufficient_evidence" and INTEGRITY_UNRESOLVED_CITATION_TEXT not in json.dumps(_record(t).get("missing_information"))
    assert not [c for c in _trace(t).get("command_authority", []) if c["decision"] == "authorized"]


@pytest.mark.asyncio
async def test_available_but_unselected_action_never_resolves(conversation) -> None:  # noqa: F811
    """AVAILABLE != SELECTED: the id of an action from a section that is only AVAILABLE is never issued."""
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT), (S_HC2, HC2_TEXT)]))
    t = await _turn(conv, [SEARCH, _select(S_HC2), CATALOG, _governed(ALT, [UNSUPPORTED])])
    assert _trace(t)["action_resolutions"][-1]["status"] == "unknown_action" and _record(t)["outcome"] == "insufficient_evidence"
    assert not _authorized(t, "alt")


# =============================================================================================
# Unit: citation identity, source disambiguation, contradictions, false-gap guard
# =============================================================================================


def _ev(section: str, content: str = HC_TEXT, applicability: str = "match") -> dict[str, Any]:
    return {"source_id": f"{KID}:{VER}:{section}", "source_type": "governed_knowledge", "title": TITLE, "content_snippet": content,
            "metadata": {**_key(section), "source_id": FILE, "title": TITLE, "lifecycle_status": "approved",
                         "applicability_outcome": applicability, "document_type": "mop"}}


def _rec(citations: list[str], action_id: Optional[str] = None, **step: Any) -> dict[str, Any]:
    return {"outcome": "recommended", "technical_interpretation": "x", "verified_evidence_citations": citations,
            "diagnostic_step": {"action": "Check alarms.", "procedure_action_id": action_id, "command": None, "command_source": None, **step}}


def _validate(payload: dict[str, Any], evidence: list[dict[str, Any]], run: str = "run-unit") -> tuple[dict[str, Any], list[dict[str, Any]]]:
    token = bind_run_id(run)
    try:
        out, _ = validate_technical_authority_payload(payload, {"verified_evidence": evidence, "approved_commands_catalog": []}, phase="integrity_callback")
        return out, integrity_decisions(run)
    finally:
        reset_run_id(token)
        validation.discard_integrity_decisions(run)


def test_f_structured_citation_identity_is_compared_structurally() -> None:
    selected = [_ev(S_HC)]
    same = json.dumps(_key(S_HC))
    elsewhere = json.dumps(_key(S_LOG))  # same document, same display shape -- a different tuple
    out, (decision,) = _validate(_rec([same]), selected)
    assert out["outcome"] == "recommended" and out["verified_evidence_citations"] == [f"{KID}:{VER}:{S_HC}"]
    assert decision["structured_identity_match"] is True
    out, (decision,) = _validate(_rec([elsewhere]), selected)
    assert out["outcome"] == "insufficient_evidence", "a mismatching structured identity never grounds (no alias fallback)"
    assert decision["citation_resolution"] == ["structured_mismatch"] and decision["check_triggered"] == "citation_unresolved_no_action_id"
    # With a server-resolvable action id the same mismatch is deferred to the resolver, not trusted.
    out, (decision,) = _validate(_rec([elsewhere], action_id=ALT), selected)
    assert out["outcome"] == "recommended" and decision["action"] == "action_resolution_deferred"


def test_non_procedure_action_recommendations_keep_strict_citation_grounding() -> None:
    out, (decision,) = _validate(_rec([UNSUPPORTED]), [_ev(S_HC)])
    assert out["outcome"] == "insufficient_evidence" and INTEGRITY_UNRESOLVED_CITATION_TEXT in out["missing_information"]
    assert decision["action"] == "fail_closed_insufficient_evidence"
    # No selected evidence at all: even an action id is not deferred (the bounded selection retry applies).
    out, (decision,) = _validate(_rec([FILE], action_id=ALT), [])
    assert out["outcome"] == "insufficient_evidence" and decision["check_triggered"] == "citation_unresolved_no_selected_evidence"


def test_n_inconsistent_payloads_are_normalized_deterministically_and_recorded() -> None:
    contradiction = {"outcome": "insufficient_evidence", "technical_interpretation": "x", "verified_evidence_citations": [],
                     "diagnostic_step": {"action": "Check alarms.", "procedure_action_id": ALT}}
    out, (decision,) = _validate(contradiction, [_ev(S_HC)])
    assert out["outcome"] == "insufficient_evidence" and out["diagnostic_step"] is None
    assert decision["check_triggered"] == "payload_contradiction" and decision["action"] == "step_discarded"
    invalid = {"outcome": "recommended", "technical_interpretation": "x", "verified_evidence_citations": [], "diagnostic_step": {"procedure_action_id": ALT}}
    out, _ = _validate(invalid, [_ev(S_HC)])
    assert out["outcome"] == "insufficient_evidence" and "Diagnostic step action was missing or invalid." in out["missing_information"]


def test_g_h_i_command_source_disambiguation_by_grounding() -> None:
    hc, log, hc2 = _ev(S_HC), _ev(S_LOG, LOG_TEXT), _ev(S_HC2, HC2_TEXT)
    assert resolve_command_source("alt", FILE, [hc]) == f"{KID}:{VER}:{S_HC}", "a unique alias stays as before"
    assert resolve_command_source("alt", FILE, [hc, log]) == f"{KID}:{VER}:{S_HC}", "G: only the instruction section grounds it"
    assert resolve_command_source("alt", FILE, [hc, hc2]) is None, "H: several sections ground it -> ambiguous, fail closed"
    assert resolve_command_source("st cell", FILE, [hc, log]) is None, "I: none grounds it -> fail closed"
    assert resolve_command_source("alt", FILE, []) is None and resolve_command_source(None, FILE, [hc, log]) is None


def _alternatives(evidence: list[dict[str, Any]]) -> list[Any]:
    progression = TroubleshootingProgression()
    progression.add_fault(FaultProgression(fault_id="F1", symptom_summary="fault"))
    return viable_governed_alternatives(evidence, progression, "F1")[0]


@pytest.mark.asyncio
async def test_j_l_no_approved_acquisition_action_only_when_the_selected_catalog_offers_nothing() -> None:
    progression = TroubleshootingProgression()
    progression.add_fault(FaultProgression(fault_id="F1", symptom_summary="fault"))
    searched = dict(governed_search_performed=True, searches=[{"status": "ok"}], available=[(f"{KID}:{VER}:{S_HC}", "match")],
                    available_identities=[(EvidenceIdentity(**_key(S_HC)), "match")], selected=[f"{KID}:{VER}:{S_HC}"])
    with_actions = DiscoveryState(**searched, selected_actions=[ALT])
    decision = await decide_acquisition(requirement_from_proposal({"kind": "diagnostic_result", "description": "Active alarm list"}, "F1"),
                                        progression=progression, action=GovernedActionState(), discovery=with_actions, run_id="r")
    assert decision.outcome == AcquisitionOutcome.BLOCKED and decision.gap is None
    assert decision.requirement.blocking_reason is GapReason.ACTION_NOT_CHOSEN
    declined = DiscoveryState(**searched, selected_actions=[ALT], actions_declined=True)
    decision = await decide_acquisition(requirement_from_proposal({"kind": "diagnostic_result", "description": "Node sync status"}, "F1"),
                                        progression=progression, action=GovernedActionState(), discovery=declined, run_id="r")
    assert decision.outcome == AcquisitionOutcome.GAP and decision.gap.gap_reason is GapReason.NO_APPROVED_ACQUISITION_ACTION
    empty = DiscoveryState(**searched)
    decision = await decide_acquisition(requirement_from_proposal({"kind": "diagnostic_result", "description": "Fan speed"}, "F1"),
                                        progression=progression, action=GovernedActionState(), discovery=empty, run_id="r")
    assert decision.outcome == AcquisitionOutcome.GAP


def test_k_choose_action_request_carries_distinguishing_structured_metadata() -> None:
    actions = _alternatives([_ev(S_HC)])
    text = action_choice_instruction(requirement_from_proposal({"kind": "diagnostic_result", "description": "Active alarm list"}, "F1"), actions)
    for action in actions:
        view = action.model_view()
        assert view["procedure_action_id"] in text and view["intent"] in text
        assert view["selection_key"] == _key(S_HC)
    assert "never invent" in text and "none of the supplied actions applies" in text


# =============================================================================================
# Mutation tests: the negative / repaired cases depend on the new guards
# =============================================================================================


@pytest.mark.asyncio
async def test_mutation_without_deferral_the_live_shape_regresses(conversation, monkeypatch) -> None:  # noqa: F811
    """Disable the deferral (the pre-repair citation check): shape 2 loses `alt` again."""
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT)]))
    original = validation.validate_technical_authority_payload

    def strict(response_payload: Any, request_payload: Any, *, phase: str = "validation"):
        stripped = dict(response_payload)
        if isinstance(stripped.get("diagnostic_step"), dict):
            stripped["diagnostic_step"] = {**stripped["diagnostic_step"], "procedure_action_id": None}
        out, modified = original(stripped, request_payload, phase=phase)
        if out.get("outcome") == "insufficient_evidence":
            return out, True
        return original(response_payload, request_payload, phase=phase)

    monkeypatch.setattr(validation, "validate_technical_authority_payload", strict)
    t = await _turn(conv, [SEARCH, _select(S_HC), CATALOG, _governed(ALT, [UNSUPPORTED])])
    assert not _authorized(t, "alt") and _record(t)["outcome"] == "insufficient_evidence"


@pytest.mark.asyncio
async def test_mutation_without_post_resolution_enforcement_an_unknown_action_survives(conversation, monkeypatch) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT)]))
    monkeypatch.setattr(tae_agent_tool, "_enforce_deferred_citation_check", lambda raw_result, *_: raw_result)
    t = await _turn(conv, [SEARCH, _select(S_HC), CATALOG, _governed("pa-00000000000000000000", [UNSUPPORTED])])
    assert _record(t)["outcome"] == "recommended", "the guard is what fails the unproven action closed"


def test_mutation_first_match_source_resolution_would_break_ambiguity() -> None:
    matches = validation.match_source_to_evidence(FILE, [_ev(S_HC), _ev(S_HC2, HC2_TEXT)])
    assert len(matches) == 2, "without the grounding guard a first-match choice would pick an arbitrary section"
    assert resolve_command_source("alt", FILE, [_ev(S_HC), _ev(S_HC2, HC2_TEXT)]) is None


@pytest.mark.asyncio
async def test_mutation_without_the_method_exists_guard_a_false_gap_is_recorded(conversation, monkeypatch) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(_doc([(S_HC, HC_TEXT)]))
    monkeypatch.setattr(tae_agent_tool, "_selected_action_alternatives", lambda *a, **k: [])
    t = await _turn(conv, [SEARCH, _select(S_HC), _method_less()])
    assert t["progression"].acquisition_gaps, "with the guard removed the false no-method gap returns"
    assert ea.GapReason.NO_APPROVED_ACQUISITION_ACTION.value == t["records"][-1]["evidence_acquisition"]["gap_reason"]
