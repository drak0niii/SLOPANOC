"""Authority hardening: plain-line governed-template grounding is available ONLY to a candidate
minted by the ProcedureAction resolver (server-side HMAC attestation), never to a model-written or
caller-supplied free-form command, which keeps its previous (legacy) grounding exactly.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.agent_tool import build_server_validated_commands, ground_command_candidate
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _search, _select
from backend.tests._target_fixtures import FAULT, confirmed_and_validated, fact

_KID, _SEC = "KID-PLAIN-TEMPLATES", "sec-0000"
_CONTENT = (
    "Unit State Check\n"
    "session xxxxx\n"
    "hget Unit=xxxx\n"
    "\n"
    "Board Unit Reset\n"
    "session xxxxx\n"
    "acc Board=xxxx restartboard\n"
    "y\n"
)
_READ, _WRITE = "hget Unit=xxxx", "acc Board=xxxx restartboard"
# The state-change target (Board=4) passed the current-case target gate AND was confirmed.
CONFIRMED = confirmed_and_validated(("Board", "4"))


def _ev(content: str = _CONTENT, applicability: str = "match", version: str = "v1", lifecycle: str = "approved") -> EvidenceReference:
    return EvidenceReference(
        source_id=f"{_KID}:{version}:{_SEC}", source_type="governed_knowledge", title="Plain Template Procedure", content_snippet=content,
        metadata={"knowledge_id": _KID, "version_label": version, "section_id": _SEC, "source_locator": "lines:1-8",
                  "title": "Plain Template Procedure", "heading": None, "lifecycle_status": lifecycle, "applicability_outcome": applicability},
    )


def _resolved(template: str, name: str, value: str, ev: EvidenceReference | None = None) -> dict[str, Any]:
    ev = ev or _ev()
    action = next(a for a in pa.actions_for_evidence(ev, ev.metadata)[0] if a.command_template == template)
    resolution, _ = pa.resolve_procedure_action(
        action.action_id, issued_ids={action.action_id}, selected_evidence=[ev],
        proposals=[{"name": name, "value": value}], operator_text=f"the value is {value}",
        target_facts=[fact(name, value)], fault_id=FAULT,
    )
    assert resolution.candidate is not None
    return resolution.candidate.as_authority_candidate("step")


def _authorize(candidate: dict[str, Any], ev: EvidenceReference | None = None, trusted: dict[str, Any] | None = None) -> list[str]:
    return [c.command for c in build_server_validated_commands([ev or _ev()] if ev is not False else [], [candidate], trusted_context=trusted)]


def _free_form(command: str, ev: EvidenceReference | None = None) -> dict[str, Any]:
    return {"command": command, "source_id": (ev or _ev()).source_id, "procedure_section": "step", "restrictions": []}


# 1 -------------------------------------------------------------------------------------------------
def test_resolved_procedure_action_candidate_uses_the_template_grounding_fallback() -> None:
    candidate = _resolved(_WRITE, "Board", "4")
    assert candidate["command"] == "acc Board=4 restartboard"
    ev = _ev()
    grounded = ground_command_candidate(candidate, [ev], {ev.source_id: ev})
    assert grounded is not None and grounded.grounding_method == "procedure_action_template"
    assert _authorize(candidate, trusted=CONFIRMED) == ["acc Board=4 restartboard"]


# 2 -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "mutate",
    [
        lambda c: {k: c[k] for k in ("command", "source_id", "procedure_section", "restrictions")},  # plain model dict
        lambda c: {**c, "resolver_attestation": True},  # model-supplied "trust me" flag
        lambda c: {**c, "resolver_attestation": "0" * 64},  # forged signature
        lambda c: {**c, "resolver_attestation": ""},
    ],
)
def test_identical_free_form_model_command_cannot_use_the_fallback(mutate) -> None:
    forged = mutate(_resolved(_WRITE, "Board", "4"))
    ev = _ev()
    assert ground_command_candidate(forged, [ev], {ev.source_id: ev}) is None
    assert _authorize(forged, trusted=CONFIRMED) == []


# 3 -------------------------------------------------------------------------------------------------
def test_read_only_model_generated_value_cannot_gain_authority() -> None:
    assert _authorize(_resolved(_READ, "Unit", "4")) == ["hget Unit=4"], "trusted resolver path still works for reads"
    for model_command in ("hget Unit=4", "hget Unit=9", "hget Unit=RRU-2"):
        assert _authorize(_free_form(model_command)) == [], model_command


def test_legacy_code_formatted_template_grounding_is_unchanged() -> None:
    """Legacy behaviour kept exactly: a backtick template still grounds a model-written command."""
    ev = _ev(content="Check the unit: `show unit <target>`\n")
    assert _authorize(_free_form("show unit NODE-1", ev), ev=ev) == ["show unit NODE-1"]


# 4 -------------------------------------------------------------------------------------------------
def test_state_changing_resolved_candidate_still_requires_target_confirmation() -> None:
    candidate = _resolved(_WRITE, "Board", "4")
    assert _authorize(candidate) == []
    assert _authorize(candidate, trusted={"target_confirmed": False}) == []


# 5 -------------------------------------------------------------------------------------------------
def test_available_but_unselected_evidence_cannot_authorize() -> None:
    candidate = _resolved(_WRITE, "Board", "4")
    assert _authorize(candidate, ev=False, trusted=CONFIRMED) == [], "no SELECTED evidence handed to authority"
    action_id = candidate["procedure_action_id"]
    resolution, _ = pa.resolve_procedure_action(action_id, issued_ids={action_id}, selected_evidence=[],
                                                proposals=[{"name": "Board", "value": "4"}], operator_text="4")
    assert resolution.status is pa.ProcedureActionResolutionStatus.SOURCE_NOT_SELECTED


# 6 -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("ev", [_ev(applicability="unknown"), _ev(applicability="partial_match"), _ev(lifecycle="candidate")])
def test_non_match_or_non_approved_source_fails_closed(ev: EvidenceReference) -> None:
    candidate = _resolved(_WRITE, "Board", "4")
    assert _authorize(candidate, ev=ev, trusted=CONFIRMED) == []


# 7 -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "tamper",
    [
        lambda c: {**c, "command": "acc Board=5 restartboard"},
        lambda c: {**c, "bound_parameters": {"Board": "5"}},
        lambda c: {**c, "source_id": f"{_KID}:v2:{_SEC}"},
        lambda c: {**c, "command_template": "acc Board=xxxx restartboard now"},
        lambda c: {**c, "procedure_action_id": "pa-other"},
        lambda c: {**c, "operation_type": "read_only_diagnostic"},
    ],
)
def test_command_source_version_or_template_tampering_invalidates_the_candidate(tamper) -> None:
    tampered = tamper(_resolved(_WRITE, "Board", "4"))
    assert pa.verified_resolver_candidate(tampered) is None
    assert _authorize(tampered, trusted=CONFIRMED) == []
    assert _authorize(tampered, ev=_ev(version="v2"), trusted=CONFIRMED) == []


def test_attested_candidate_fails_when_the_governed_section_no_longer_contains_the_template() -> None:
    candidate = _resolved(_WRITE, "Board", "4")
    changed = _ev(content=_CONTENT.replace("restartboard", "restartshelf"))
    assert _authorize(candidate, ev=changed, trusted=CONFIRMED) == []


# Real TAE path: a model-written read command on the legacy path is not authorized ------------------
def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID, document_type=KnowledgeDocumentType.MOP, title="Plain Template Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id="MOP_Plain.docx", display_name="Plain MOP"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading=None, sequence=0, content=_CONTENT, source_locator="lines:1-8")],
    )


@pytest.mark.asyncio
async def test_tae_legacy_free_form_read_command_is_rejected_while_resolver_path_passes(isolated_km_repo, monkeypatch) -> None:
    from google.genai import types

    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    legacy = [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
        "diagnostic_step": {"action": "Check the unit state", "reason": "r", "expected_evidence": "e",
                            "command": "hget Unit=9", "command_source": f"{_KID}:v1:{_SEC}", "restrictions": []},
    }))]
    t1 = await conv.turn("unit 4 looks down, what should I check?", [_search("unit state check"), _select(_KID, _SEC), legacy], "Run hget Unit=9.",
                         {"subject_component": "unit"})
    assert t1["record"]["diagnostic_step"]["command"] is None
    assert any(c["command"] == "hget Unit=9" and c["decision"] == "rejected" and c["stage"] == "grounding" for c in t1["trace"]["command_authority"])
    assert "hget Unit=9" not in t1["final"]

    read_id = next(a.action_id for a in pa.actions_for_evidence(_ev(), _ev().metadata)[0] if a.command_template == _READ)
    action_step = [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
        "diagnostic_step": {"action": "Check the unit state", "reason": "r", "expected_evidence": "e", "command": "hget Unit=9",
                            "command_source": None, "restrictions": [], "procedure_action_id": read_id,
                            "parameter_values": [{"name": "Unit", "value": "4"}]},
    }))]
    t2 = await conv.turn("check unit 4 please", [_search("unit state check"), _select(_KID, _SEC), _CATALOG, action_step], "Run hget Unit=4.",
                         {"subject_component": "unit", "explicit_target": "4"})
    assert t2["record"]["diagnostic_step"]["command"] == "hget Unit=4"
    assert t2["record"]["procedure_action_resolution"]["model_command_ignored"] == "hget Unit=9"
