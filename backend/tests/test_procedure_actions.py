"""Tranche 2 unit tests: governed ProcedureAction extraction, server-issued catalog,
deterministic resolution, trusted parameter binding, and hand-off to the EXISTING
Command Authority (`build_server_validated_commands`) -- which stays the only authorizing step.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from backend.tests._target_fixtures import FAULT, fact

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.agent_tool import (
    build_server_validated_commands,
    build_server_validated_evidence,
)
from backend.agents.technical_authority_engineer.schemas import CommandOperationType, EvidenceReference
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tools.knowledge import diagnostic_trace as dt
from backend.tools.knowledge import runtime as rt
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

_KID = "KID-NODE-DIAG"
_SEC = "sec-0001"
_CONTENT = (
    "Check the node alarm state: `show foo <target>`\n"
    "Inspect optical levels.\n"
    "Do not run `restart foo` during business hours.\n"
    "Recovery commands:\n"
    "acc <mo> restart\n"
)


def _ev(
    content: str = _CONTENT,
    version: str = "v1",
    applicability: str = "match",
    lifecycle: str = "approved",
    kid: str = _KID,
    section: str = _SEC,
) -> EvidenceReference:
    return EvidenceReference(
        source_id=f"{kid}:{version}:{section}",
        source_type="governed_knowledge",
        title="Node Diagnostics Procedure",
        content_snippet=content,
        metadata={
            "knowledge_id": kid,
            "version_label": version,
            "section_id": section,
            "source_locator": "lines:1-5",
            "heading": "Diagnostics",
            "title": "Node Diagnostics Procedure",
            "source_id": "MOP_Node.docx",
            "lifecycle_status": lifecycle,
            "applicability_outcome": applicability,
            "unresolved_applicability_dimensions": [] if applicability == "match" else ["technology"],
        },
    )


def _actions(ev: EvidenceReference) -> list[pa.ProcedureAction]:
    return pa.actions_for_evidence(ev, ev.metadata)[0]


def _by_template(actions: list[pa.ProcedureAction], template: str) -> pa.ProcedureAction:
    return next(a for a in actions if a.command_template == template)


def _resolve(ev: EvidenceReference, template: str, proposals=(), text: str = "", confirmed=None, issued=None, facts=None):
    action = _by_template(_actions(ev), template)
    return pa.resolve_procedure_action(
        action.action_id,
        issued_ids=issued if issued is not None else {action.action_id},
        selected_evidence=[ev],
        proposals=proposals,
        operator_text=text,
        confirmed_parameters=confirmed,
        target_facts=facts,
        fault_id=FAULT,
    )


# --- A: governed action extraction with exact provenance ---------------------------------------
def test_a_explicit_command_template_becomes_action_with_exact_provenance() -> None:
    action = _by_template(_actions(_ev()), "show foo <target>")
    assert action.action_type is pa.ProcedureActionType.DIAGNOSTIC_READ
    assert action.operation_type is CommandOperationType.READ_ONLY_DIAGNOSTIC
    assert [p.name for p in action.parameters] == ["target"]
    assert action.source.model_dump() == {
        "knowledge_id": _KID,
        "version_label": "v1",
        "section_id": _SEC,
        "source_locator": "lines:1-5",
        "canonical_source_id": f"{_KID}:v1:{_SEC}",
        "title": "Node Diagnostics Procedure",
        "heading": "Diagnostics",
    }
    assert action.command_template in _CONTENT, "template is verbatim source text"
    assert action.extraction_method == "inline_code"
    assert action.description == "Check the node alarm state"
    assert action.action_id == _by_template(_actions(_ev()), "show foo <target>").action_id, "deterministic id"


def test_command_block_lines_are_extracted_and_classified_by_existing_classifier() -> None:
    action = _by_template(_actions(_ev()), "acc <mo> restart")
    assert action.extraction_method == "command_block"
    assert action.action_type is pa.ProcedureActionType.STATE_CHANGE
    assert action.operation_type is CommandOperationType.MUTATING_OPERATIONAL


# --- B: no invented command ------------------------------------------------------------------
def test_b_prose_without_command_syntax_never_becomes_a_command() -> None:
    actions, _ = pa.extract_procedure_actions(
        knowledge_id="K", version_label="v1", section_id="s", content="Inspect optical levels.\nCheck the fibre."
    )
    assert actions == []
    assert all("Inspect optical levels" not in a.command_template for a in _actions(_ev()))


# --- C: prohibited command -------------------------------------------------------------------
def test_c_command_in_prohibition_context_is_never_materialized() -> None:
    actions, skipped = pa.extract_procedure_actions(
        knowledge_id="K", version_label="v1", section_id="s", content="Do not run `restart foo`."
    )
    assert actions == []
    assert skipped == [{"template": "restart foo", "reason": "prohibited in source", "semantics": "fixed_instance"}]
    assert all(a.command_template != "restart foo" for a in _actions(_ev()))


def test_unclassified_or_unsupported_templates_are_skipped() -> None:
    actions, skipped = pa.extract_procedure_actions(
        knowledge_id="K", version_label="v1", section_id="s", content="Values: `SFP_ERR` and `show $TARGET`"
    )
    assert actions == []
    assert {s["reason"] for s in skipped} == {"unclassified operation type", "unsupported placeholder syntax"}


# --- D: source version binding ---------------------------------------------------------------
def test_d_v1_action_never_authorizes_against_v2() -> None:
    v1 = _by_template(_actions(_ev(version="v1")), "show foo <target>")
    v2 = _by_template(_actions(_ev(version="v2")), "show foo <target>")
    assert v1.action_id != v2.action_id
    resolution, _ = pa.resolve_procedure_action(
        v1.action_id,
        issued_ids={v1.action_id},
        selected_evidence=[_ev(version="v2")],
        proposals=[{"name": "target", "value": "NODE-1"}],
        operator_text="NODE-1",
    )
    assert resolution.status is pa.ProcedureActionResolutionStatus.SOURCE_NOT_SELECTED
    assert resolution.candidate is None


# --- E: AVAILABLE is insufficient ------------------------------------------------------------
@pytest.fixture
def km_repo(monkeypatch):
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_RETRIEVAL_MODE", "lexical")
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()
    repository = rt.get_knowledge_repository()
    yield repository
    asyncio.run(repository.close())
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()


def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID,
        document_type=KnowledgeDocumentType.MOP,
        title="Node Diagnostics Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id="MOP_Node.docx", display_name="Node MOP"),
        sections=[
            KnowledgeSection(
                section_id=_SEC, knowledge_id=_KID, heading="Diagnostics", sequence=0, content=_CONTENT, source_locator="lines:1-5"
            )
        ],
    )


@pytest.mark.asyncio
async def test_e_available_but_unselected_evidence_yields_no_usable_action(km_repo) -> None:
    await km_repo.add(_mop())
    run_id = "t2-e"
    token = bind_run_id(run_id)
    try:
        payload = await knowledge_search("node alarm state")
        assert payload["items"], "evidence is AVAILABLE"
        catalog = await pa.procedure_action_catalog()
        assert catalog["actions"] == [] and pa.issued_action_ids(run_id) == frozenset()
        forced = _by_template(_actions(_ev()), "show foo <target>").action_id
        resolution, _ = pa.resolve_procedure_action(
            forced,
            issued_ids={forced},  # even if an id were somehow issued
            selected_evidence=build_server_validated_evidence(run_id, None, []),
            proposals=[{"name": "target", "value": "NODE-1"}],
            operator_text="NODE-1",
        )
        assert resolution.status is pa.ProcedureActionResolutionStatus.SOURCE_NOT_SELECTED

        # Once explicitly SELECTED, the same action becomes issuable.
        await knowledge_select_evidence([payload["items"][0]["selection_key"]])
        catalog = await pa.procedure_action_catalog()
        assert forced in {a["action_id"] for a in catalog["actions"]}
        assert forced in pa.issued_action_ids(run_id)
        assert "command_template" not in catalog["actions"][0], "model never sees the authoritative template"
    finally:
        reset_run_id(token)
        rt.discard_knowledge_run_evidence_state(run_id)
        pa.discard_issued_actions(run_id)
        dt.discard_diagnostic_trace(run_id)


# --- F: UNKNOWN applicability ----------------------------------------------------------------
@pytest.mark.parametrize("applicability,lifecycle", [("unknown", "approved"), ("partial_match", "approved"), ("match", "candidate")])
def test_f_non_match_or_non_approved_source_never_yields_command(applicability: str, lifecycle: str) -> None:
    ev = _ev(applicability=applicability, lifecycle=lifecycle)
    actions, unavailable = pa.build_procedure_action_catalog([ev])
    assert actions == [] and unavailable and "required" in unavailable[0]["reason"]
    resolution, _ = _resolve(ev, "show foo <target>", [{"name": "target", "value": "NODE-1"}], "NODE-1")
    assert resolution.status is pa.ProcedureActionResolutionStatus.SOURCE_NOT_AUTHORITATIVE
    assert resolution.candidate is None


# --- G (unit): fabricated id ------------------------------------------------------------------
def test_g_fabricated_action_id_fails_closed_even_if_derivable() -> None:
    real = _by_template(_actions(_ev()), "show foo <target>").action_id
    resolution, _ = pa.resolve_procedure_action(
        real, issued_ids=set(), selected_evidence=[_ev()], proposals=[{"name": "target", "value": "NODE-1"}], operator_text="NODE-1"
    )
    assert resolution.status is pa.ProcedureActionResolutionStatus.UNKNOWN_ACTION
    fabricated, _ = pa.resolve_procedure_action("pa-madeup", issued_ids={real}, selected_evidence=[_ev()])
    assert fabricated.status is pa.ProcedureActionResolutionStatus.UNKNOWN_ACTION


# --- H: deterministic parameter binding ---------------------------------------------------------
def test_h_exact_render_from_operator_stated_value() -> None:
    resolution, confirmed = _resolve(
        _ev(), "show foo <target>", [{"name": "target", "value": "NODE-1"}], "Alarm on NODE-1 since 10:00"
    )
    assert resolution.status is pa.ProcedureActionResolutionStatus.RESOLVED
    assert resolution.candidate.command == "show foo NODE-1"
    assert resolution.candidate.source_id == f"{_KID}:v1:{_SEC}"
    assert resolution.bindings[0].state is pa.ParameterBindingState.VERIFIED
    assert confirmed == {"target": "NODE-1"}


def test_session_confirmed_value_binds_without_new_statement() -> None:
    resolution, _ = _resolve(_ev(), "show foo <target>", [], "what next?", confirmed={"target": "NODE-1"})
    assert resolution.candidate.command == "show foo NODE-1"
    assert resolution.bindings[0].detail == "confirmed earlier in session"


# --- I: missing parameter ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "proposals,text,detail",
    [
        ([], "what next?", None),
        ([{"name": "target", "value": "NODE-9"}], "what next?", "value was not stated by the operator"),
        ([{"name": "target", "value": "node-1"}], "NODE-1", "value was not stated by the operator"),
        ([{"name": "target", "value": "N1;reboot"}], "N1;reboot", "value contains characters not permitted in a command parameter"),
    ],
)
def test_i_missing_or_untrusted_parameter_yields_no_command_and_clarification(proposals, text, detail) -> None:
    resolution, confirmed = _resolve(_ev(), "show foo <target>", proposals, text)
    assert resolution.status is pa.ProcedureActionResolutionStatus.PARAMETERS_UNRESOLVED
    assert resolution.candidate is None
    assert resolution.bindings[0].state is pa.ParameterBindingState.MISSING
    assert resolution.bindings[0].detail == detail
    assert confirmed == {}
    clarification = pa.build_parameter_clarification(resolution.bindings)
    assert clarification["missing_parameters"] == ["target"]
    assert "<target>" not in clarification["text"]


# --- J: ambiguous / conflicting ---------------------------------------------------------------
def test_j_ambiguous_values_yield_no_command() -> None:
    resolution, _ = _resolve(
        _ev(), "show foo <target>", [{"name": "target", "value": "NODE-1"}, {"name": "target", "value": "NODE-2"}], "NODE-1 or NODE-2"
    )
    assert resolution.candidate is None
    assert resolution.bindings[0].state is pa.ParameterBindingState.AMBIGUOUS
    assert pa.build_parameter_clarification(resolution.bindings)["ambiguous_parameters"] == ["target"]


def test_j_conflict_with_confirmed_value_fails_closed_and_clears_confirmation() -> None:
    resolution, confirmed = _resolve(
        _ev(), "show foo <target>", [{"name": "target", "value": "NODE-2"}], "now NODE-2", confirmed={"target": "NODE-1"}
    )
    assert resolution.candidate is None
    assert resolution.bindings[0].state is pa.ParameterBindingState.CONFLICTING
    assert confirmed == {}, "operator must restate the intended target"


def test_render_never_emits_partial_or_unresolved_placeholders() -> None:
    bindings = [pa.ParameterBinding(name="a", state=pa.ParameterBindingState.VERIFIED, value="X")]
    assert pa.render_command_template("show <a> {b}", bindings) is None
    assert pa.render_command_template("show <a>", bindings) == "show X"


# --- K / L: hand-off to the EXISTING Command Authority -------------------------------------------
def test_k_read_only_candidate_is_authorized_by_existing_command_authority() -> None:
    ev = _ev()
    resolution, _ = _resolve(ev, "show foo <target>", [{"name": "target", "value": "NODE-1"}], "NODE-1")
    catalog = build_server_validated_commands([ev], [resolution.candidate.as_authority_candidate("Check")])
    assert [(c.command, c.source_id, c.operation_type) for c in catalog] == [
        ("show foo NODE-1", f"{_KID}:v1:{_SEC}", CommandOperationType.READ_ONLY_DIAGNOSTIC)
    ]


def test_l_state_changing_candidate_is_rejected_without_trusted_target_confirmation() -> None:
    ev = _ev(content="Recovery: `acc <mo> restart`\n")
    # Operator words alone never establish a state-change target: no candidate at all.
    resolution, _ = _resolve(ev, "acc <mo> restart", [{"name": "mo", "value": "PlugInUnit-3"}], "PlugInUnit-3")
    assert resolution.status is pa.ProcedureActionResolutionStatus.TARGET_NOT_VALIDATED and resolution.candidate is None
    # With the target observed in this fault's trusted evidence it resolves -- still not authorization.
    resolution, _ = _resolve(ev, "acc <mo> restart", [{"name": "mo", "value": "PlugInUnit-3"}], "PlugInUnit-3", facts=[fact("", "PlugInUnit-3")])
    assert resolution.status is pa.ProcedureActionResolutionStatus.RESOLVED, "resolution is not authorization"
    assert build_server_validated_commands([ev], [resolution.candidate.as_authority_candidate("x")]) == []


def test_bare_parameterized_command_block_template_grounds_only_as_an_exact_governed_rendering() -> None:
    """Formerly a documented limitation (bare-line templates never grounded). Grounding now accepts
    such a template only when the deterministic extractor re-derives it verbatim from the same
    authorized section and the command is a strict charset-valid rendering of it."""
    ev = _ev(content="Diagnostic commands:\nst <mo>\n")
    resolution, _ = _resolve(ev, "st <mo>", [{"name": "mo", "value": "PlugInUnit-3"}], "PlugInUnit-3")
    assert resolution.candidate.command == "st PlugInUnit-3"
    assert [c.command for c in build_server_validated_commands([ev], [resolution.candidate.as_authority_candidate("x")])] == ["st PlugInUnit-3"]
    for not_a_rendering in ("st PlugInUnit-3 extra", "st PlugInUnit-3;deb 1", "show PlugInUnit-3"):
        assert build_server_validated_commands([ev], [{"command": not_a_rendering, "source_id": ev.source_id}]) == []
    assert build_server_validated_commands(
        [_ev(content="Diagnostic commands:\nst <mo>\n", applicability="unknown")], [resolution.candidate.as_authority_candidate("x")]
    ) == []
