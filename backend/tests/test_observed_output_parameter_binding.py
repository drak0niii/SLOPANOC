"""Regression: governed-template parameters must not depend on model-generated values (live: the
model rendered `**4**`) when the operator already pasted trusted diagnostic output containing the
literal value (e.g. `Board=4 ... DISABLED`).

Trust chain: governed check -> operator pastes output -> verbatim `operator_observation` on the
check (active thread) -> deterministic `<param>=<literal>` extraction -> binding -> resolver.
No model value, no markup cleanup; ambiguity asks; conflict fails closed; observed output is
parameter evidence only, never command authority.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _respond, _search, _select

V, M, A, C = (pa.ParameterBindingState.VERIFIED, pa.ParameterBindingState.MISSING, pa.ParameterBindingState.AMBIGUOUS, pa.ParameterBindingState.CONFLICTING)


def _bind(name: str, observed: list[str], proposals: list[dict[str, str]] | None = None, text: str = "restart it", confirmed: dict[str, str] | None = None):
    bindings, updated = pa.reconcile_parameter_bindings([name], proposals or [], text, confirmed or {}, observed)
    return bindings[0], updated


def test_literal_keyed_value_in_operator_output_binds() -> None:
    binding, updated = _bind("Board", ["st board\nBoard=4   ENABLED  DISABLED\n"])
    assert (binding.state, binding.value) == (V, "4")
    assert binding.detail == "from operator-provided output recorded in this investigation"
    assert updated == {"Board": "4"}


def test_key_match_is_case_insensitive_value_is_exact() -> None:
    binding, _ = _bind("Board", ["board=Unit-4B disabled"])
    assert (binding.state, binding.value) == (V, "Unit-4B")


@pytest.mark.parametrize("output", ["Board=**4** DISABLED", "Board=`4`", "Board=4** DISABLED", "Board=xxxx"])
def test_markup_or_placeholder_values_are_never_cleaned_up_or_extracted(output: str) -> None:
    binding, updated = _bind("Board", [output], proposals=[{"name": "Board", "value": "**4**"}])
    assert binding.state is M and binding.value is None and updated == {}


def test_several_observed_values_ask_for_clarification() -> None:
    binding, updated = _bind("Board", ["Board=1 ENABLED\nBoard=4 DISABLED"])
    assert binding.state is A and "1, 4" in binding.detail and updated == {}
    clarification = pa.build_parameter_clarification([binding])
    assert clarification["ambiguous_parameters"] == ["Board"]


def test_model_cannot_pick_among_several_observed_values() -> None:
    binding, _ = _bind("Board", ["Board=1 ENABLED\nBoard=4 DISABLED"], proposals=[{"name": "Board", "value": "4"}])
    assert binding.state is A


def test_confirmed_value_contradicted_by_operator_output_fails_closed() -> None:
    binding, updated = _bind("Board", ["Board=4 DISABLED"], confirmed={"Board": "2"})
    assert binding.state is C and updated == {}, "confirmation cleared; operator must restate"
    agreeing, _ = _bind("Board", ["Board=2 DISABLED"], confirmed={"Board": "2"})
    assert (agreeing.state, agreeing.value) == (V, "2")


def test_operator_current_statement_takes_precedence_over_observed_output() -> None:
    binding, _ = _bind("Board", ["Board=4 DISABLED"], proposals=[{"name": "Board", "value": "5"}], text="restart board 5 instead")
    assert (binding.state, binding.value, binding.detail) == (V, "5", "stated by operator")


def test_model_pointer_into_observed_output_for_unkeyed_placeholder() -> None:
    binding, _ = _bind("mo", ["Unit list: Slot-3 DISABLED"], proposals=[{"name": "mo", "value": "Slot-3"}])
    assert (binding.state, binding.value) == (V, "Slot-3")
    invented, _ = _bind("mo", ["Unit list: Slot-3 DISABLED"], proposals=[{"name": "mo", "value": "Slot-9"}])
    assert invented.state is M and "not stated" in invented.detail


def test_only_operator_or_execution_observations_are_trusted() -> None:
    ts = TroubleshootingState(fault_id="F", symptom_summary="s")
    ts.record_recommended_check(action="Check board", rationale="r", expected_observation="e", check_id="c1")
    ts.record_user_execution(check_id="c1", observed_result="Board=9 (caller summary)")
    assert ts.trusted_observation_texts() == [], "a caller/model-written summary is never parameter evidence"
    ts.record_user_execution(check_id="c1", operator_observation="Board=4 DISABLED")
    ts.record_recommended_check(action="Read board", rationale="r", expected_observation="e", check_id="c2")
    ts.record_adapter_execution(check_id="c2", execution_id="exec-1", observed_result="Board=4 LOCKED", succeeded=True)
    assert ts.trusted_observation_texts() == ["Board=4 LOCKED", "Board=4 DISABLED"]


# --- end-to-end trust chain -------------------------------------------------------------------------
_KID, _SEC = "KID-UNIT-RECOVERY", "sec-0000"
_CONTENT = "HC Commands:\nst board\n\nBoard Unit Reset\nsession xxxxx\nacc Board=xxxx restartboard\ny\n"
_ACTIONS = {a.command_template: a.action_id for a in pa.extract_procedure_actions(knowledge_id=_KID, version_label="v1", section_id=_SEC, content=_CONTENT)[0]}


def _mop() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=_KID, document_type=KnowledgeDocumentType.MOP, title="Unit Recovery Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
        source=KnowledgeSource(source_system="test", source_id="MOP_Unit.docx", display_name="Unit MOP"),
        sections=[KnowledgeSection(section_id=_SEC, knowledge_id=_KID, heading=None, sequence=0, content=_CONTENT, source_locator="lines:1-8")],
    )


@pytest.mark.asyncio
async def test_restart_template_binds_from_pasted_operator_output_not_model_value(isolated_km_repo, monkeypatch) -> None:
    await isolated_km_repo.add(_mop())
    conv = _Conversation(monkeypatch)
    step = [_search("board unit recovery"), _select(_KID, _SEC), _CATALOG]

    t1 = await conv.turn("board unit fault on the node, what should I check?", step + [_respond(action_id=_ACTIONS["st board"])], "Run st board.",
                         {"subject_component": "board unit"})
    assert t1["record"]["diagnostic_step"]["command"] == "st board"

    # The operator pastes the output; the caller's own summary even decorates the value.
    t2 = await conv.turn("st board output:\nBoard=4   DISABLED\n", step + [_respond(outcome="insufficient_evidence")], "Board **4** is disabled.")
    check = t2["state"]["troubleshooting_state"]["diagnostic_history"][0]
    assert check["operator_observation"] == "st board output:\nBoard=4   DISABLED"

    t3 = await conv.turn(
        "ok, how do I recover the board unit?",
        step + [_respond(action_id=_ACTIONS["acc Board=xxxx restartboard"], params=[{"name": "Board", "value": "**4**"}], action="Recover the board unit")],
        "Run acc Board=**4** restartboard.",
        {"subject_component": "board unit", "requested_operation": "recover"},
    )
    resolution = t3["record"]["procedure_action_resolution"]
    assert resolution["rendered_command"] == "acc Board=4 restartboard"
    (binding,) = resolution["parameters"]
    assert (binding["name"], binding["state"], binding["value"]) == ("Board", "verified", "4")
    # Bound from the trusted case target fact observed in the pasted output (never the model's value).
    assert binding["detail"].startswith("current-case target fact")
    (target,) = resolution["target_validation"]["targets"]
    assert (target["key"], target["value"], target["status"], target["requested"]) == ("Board", "4", "validated", [])
    assert target["provenance"][0]["source"] == "operator_message"
    # Observed output is parameter evidence only: the state change still needs the full path.
    assert t3["record"]["diagnostic_step"]["command"] is None
    assert t3["record"]["operational_control"]["control_stage"] == "awaiting_confirmation"
    assert t3["record"]["operational_control"]["target"]["canonical_identifier"] == "4"
    assert "restartboard" not in t3["final"]
