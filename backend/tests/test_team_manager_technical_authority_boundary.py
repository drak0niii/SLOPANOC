"""Unit and boundary tests for Step 3:
Team Manager must not introduce operational instructions beyond validated TAE output.

Proves:
A. TAE authorizes alt, TM says 'Run alt' -> alt survives
B. TAE authorizes alt, TM says 'Run alt then restart radio' -> alt survives, restart removed/blocked
C. TAE authorizes alt, TM adds st pluginunit -> st pluginunit removed
D. TAE authorizes no command, TM invents reset/restart -> operational instruction removed
E. Normal explanation with no new operation -> preserved
F. Source/provenance presentation remains unchanged -> still SELECTED evidence only
Prose leakage: operational instructions hidden in prose cannot bypass the boundary.
"""
from __future__ import annotations

import pytest

from backend.agents.technical_authority_engineer.synthesis_boundary import (
    SAFE_SYNTHESIS_FALLBACK,
    enforce_technical_authority_synthesis_boundary,
)


def _build_tae_execution(
    outcome: str = "recommended",
    command: str | None = None,
    command_source: str | None = None,
    action: str = "Inspect alarms",
    approved_commands: list[dict] | None = None,
    verified_evidence: list[dict] | None = None,
) -> dict:
    step = None
    if command or action:
        step = {
            "action": action,
            "reason": "Diagnostic check",
            "expected_evidence": "Alarms or logs",
            "command": command,
            "command_source": command_source,
            "restrictions": [],
        }
    return {
        "outcome": outcome,
        "technical_interpretation": "Observed technical condition.",
        "verified_evidence_citations": [command_source] if command_source else [],
        "diagnostic_step": step,
        "approved_commands_catalog": approved_commands or [],
        "verified_evidence": verified_evidence or [],
    }


def test_scenario_a_tae_authorizes_alt_survives() -> None:
    """A. TAE authorizes alt, TM says 'Run alt' -> alt survives."""
    tae_exec = _build_tae_execution(command="alt", command_source="ericsson_mop_1")
    tm_text = "Run alt."
    sanitized = enforce_technical_authority_synthesis_boundary(tm_text, tae_exec)
    assert "alt" in sanitized
    assert "Run alt." in sanitized


def test_scenario_b_tae_authorizes_alt_restart_radio_blocked() -> None:
    """B. TAE authorizes alt, TM says 'Run alt then restart radio' -> alt survives, restart removed."""
    tae_exec = _build_tae_execution(command="alt", command_source="ericsson_mop_1")

    # With comma
    tm_text_comma = "Run alt, then restart the radio."
    sanitized_comma = enforce_technical_authority_synthesis_boundary(tm_text_comma, tae_exec)
    assert "alt" in sanitized_comma
    assert "restart" not in sanitized_comma.lower()
    assert sanitized_comma == "Run alt."

    # Without comma
    tm_text_no_comma = "Run alt then restart radio"
    sanitized_no_comma = enforce_technical_authority_synthesis_boundary(tm_text_no_comma, tae_exec)
    assert "alt" in sanitized_no_comma
    assert "restart" not in sanitized_no_comma.lower()
    assert sanitized_no_comma == "Run alt."

    # With and
    tm_text_and = "Run alt and restart the radio."
    sanitized_and = enforce_technical_authority_synthesis_boundary(tm_text_and, tae_exec)
    assert "alt" in sanitized_and
    assert "restart" not in sanitized_and.lower()
    assert sanitized_and == "Run alt."


def test_scenario_c_tae_authorizes_alt_st_pluginunit_removed() -> None:
    """C. TAE authorizes alt, TM adds st pluginunit -> st pluginunit removed."""
    tae_exec = _build_tae_execution(command="alt", command_source="ericsson_mop_1")

    # Multi-sentence
    tm_text_multi = "Run alt. Next, run st pluginunit."
    sanitized_multi = enforce_technical_authority_synthesis_boundary(tm_text_multi, tae_exec)
    assert "alt" in sanitized_multi
    assert "st pluginunit" not in sanitized_multi
    assert sanitized_multi == "Run alt."

    # Compound clause
    tm_text_compound = "Run alt and st pluginunit"
    sanitized_compound = enforce_technical_authority_synthesis_boundary(tm_text_compound, tae_exec)
    assert "alt" in sanitized_compound
    assert "st pluginunit" not in sanitized_compound
    assert sanitized_compound == "Run alt."

    # Comma separated
    tm_text_comma = "Run alt, st pluginunit."
    sanitized_comma = enforce_technical_authority_synthesis_boundary(tm_text_comma, tae_exec)
    assert "alt" in sanitized_comma
    assert "st pluginunit" not in sanitized_comma
    assert sanitized_comma == "Run alt."


def test_scenario_d_tae_authorizes_no_command_restart_reset_removed() -> None:
    """D. TAE authorizes no command, TM invents reset/restart -> operational instruction removed."""
    tae_exec = _build_tae_execution(outcome="insufficient_evidence", command=None, action="")

    # Explanation + restart instruction
    tm_text = "The alarm indicates potential optical degradation. Please restart the radio to clear the condition."
    sanitized = enforce_technical_authority_synthesis_boundary(tm_text, tae_exec)
    assert "The alarm indicates potential optical degradation." in sanitized
    assert "restart" not in sanitized.lower()

    # Reset node instruction
    tm_text_reset = "Fault on baseband unit. You should reset the node immediately."
    sanitized_reset = enforce_technical_authority_synthesis_boundary(tm_text_reset, tae_exec)
    assert "Fault on baseband unit." in sanitized_reset
    assert "reset" not in sanitized_reset.lower()

    # Pure unauthorized instruction -> safe fallback
    tm_text_pure = "Restart the radio."
    sanitized_pure = enforce_technical_authority_synthesis_boundary(tm_text_pure, tae_exec)
    assert sanitized_pure == SAFE_SYNTHESIS_FALLBACK


def test_scenario_e_normal_explanation_preserved() -> None:
    """E. Normal explanation with no new operation -> preserved byte-for-byte."""
    tae_exec = _build_tae_execution(outcome="insufficient_evidence", command=None, action="")
    explanation = (
        "Based on Ericsson MOP 04_121, alarm 1024 indicates a synchronization issue between the "
        "primary and secondary units. Additional diagnostic telemetry is required before any operational "
        "step can be authorized."
    )
    sanitized = enforce_technical_authority_synthesis_boundary(explanation, tae_exec)
    assert sanitized == explanation


def test_scenario_f_source_provenance_presentation_preserved() -> None:
    """F. Source/provenance presentation remains unchanged -> still SELECTED evidence only."""
    tae_exec = _build_tae_execution(command="alt", command_source="ericsson_mop_1")
    text_with_sources = (
        "Run alt to check active alarms.\n\n"
        "Sources:\n"
        "[1] Ericsson MOP 04_121 (Section 3.2 Alarms Verification)"
    )
    sanitized = enforce_technical_authority_synthesis_boundary(text_with_sources, tae_exec)
    assert "Run alt." in sanitized or "Run alt" in sanitized
    assert "Sources:" in sanitized
    assert "[1] Ericsson MOP 04_121 (Section 3.2 Alarms Verification)" in sanitized


def test_prose_leakage_hidden_imperatives_blocked() -> None:
    """Operational instructions hidden in natural language prose cannot bypass the boundary."""
    tae_exec = _build_tae_execution(command="alt", command_source="ericsson_mop_1")

    # Prose trying to sneak reboot/reloading/bounce
    prose1 = "Check alarms with alt. You can reboot the node if the failure persists."
    sanitized1 = enforce_technical_authority_synthesis_boundary(prose1, tae_exec)
    assert "Check alarms with alt." in sanitized1
    assert "reboot" not in sanitized1.lower()

    # Prose with manualrestart
    prose2 = "Run alt. Execute manualrestart on cell 1."
    sanitized2 = enforce_technical_authority_synthesis_boundary(prose2, tae_exec)
    assert "Run alt." in sanitized2
    assert "manualrestart" not in sanitized2.lower()

    # Prose with configuration changes
    prose3 = "Run alt. Set cell administrativeState locked to isolate."
    sanitized3 = enforce_technical_authority_synthesis_boundary(prose3, tae_exec)
    assert "Run alt." in sanitized3
    assert "administrativeState" not in sanitized3


def test_code_block_sanitization_removes_unauthorized_lines() -> None:
    """Code blocks containing unauthorized lines have those lines stripped."""
    tae_exec = _build_tae_execution(command="alt", command_source="ericsson_mop_1")
    code_block_text = (
        "Recommended diagnostic commands:\n"
        "```bash\n"
        "alt\n"
        "restart radio\n"
        "st pluginunit\n"
        "```"
    )
    sanitized = enforce_technical_authority_synthesis_boundary(code_block_text, tae_exec)
    assert "```bash\nalt\n```" in sanitized
    assert "restart radio" not in sanitized
    assert "st pluginunit" not in sanitized


def test_markdown_list_sanitization_removes_unauthorized_items() -> None:
    """List items instructing unauthorized operations are pruned."""
    tae_exec = _build_tae_execution(command="alt", command_source="ericsson_mop_1")
    list_text = (
        "Recommended actions:\n"
        "1. Run alt to check alarms\n"
        "2. Restart the radio to clear cache\n"
        "3. Run st pluginunit to inspect hardware\n"
    )
    sanitized = enforce_technical_authority_synthesis_boundary(list_text, tae_exec)
    assert "1. Run alt to check alarms." in sanitized or "1. Run alt to check alarms" in sanitized
    assert "Restart the radio" not in sanitized
    assert "st pluginunit" not in sanitized


def test_no_tae_execution_leaves_text_unmodified() -> None:
    """When TAE did not execute (e.g. non-troubleshooting request), boundary is a no-op."""
    raw_text = "General inquiry response without specialist delegation."
    assert enforce_technical_authority_synthesis_boundary(raw_text, None) == raw_text


def test_chat_service_completion_boundary_integration() -> None:
    """Emulates chat_service.py final completion boundary:

    Verifies that when technical_authority_execution is present, final_text is
    strictly sanitized by enforce_technical_authority_synthesis_boundary.
    """
    tae_exec = _build_tae_execution(command="alt", command_source="ericsson_mop_1")
    raw_tm_output = "Run alt to inspect alarms, then restart the radio."
    error = None

    final_text = raw_tm_output

    if error is None and final_text is not None:
        if tae_exec is not None:
            final_text = enforce_technical_authority_synthesis_boundary(
                final_text=final_text,
                technical_authority_execution=tae_exec,
            )

    assert final_text == "Run alt to inspect alarms."
    assert "restart" not in final_text.lower()


def test_delta_suppression_and_execution_lifecycle() -> None:
    """Verifies that has_technical_authority_executed correctly tracks run lifecycle."""
    from backend.agents.technical_authority_engineer.execution_context import (
        discard_technical_authority_execution,
        get_technical_authority_execution,
        has_technical_authority_executed,
        record_technical_authority_execution,
    )

    run_id = "test-run-step3-lifecycle"
    try:
        assert not has_technical_authority_executed(run_id)
        assert get_technical_authority_execution(run_id) is None

        exec_data = _build_tae_execution(command="alt", command_source="src1")
        record_technical_authority_execution(run_id, exec_data)

        assert has_technical_authority_executed(run_id)
        retrieved = get_technical_authority_execution(run_id)
        assert retrieved is not None
        assert retrieved["diagnostic_step"]["command"] == "alt"

        discard_technical_authority_execution(run_id)
        assert not has_technical_authority_executed(run_id)
        assert get_technical_authority_execution(run_id) is None
    finally:
        discard_technical_authority_execution(run_id)


def test_regression_catalog_has_a_and_b_selected_is_a_only_a_survives() -> None:
    """Regression 1: Dynamic check: catalog has command A + command B,

    diagnostic_step.command = A, TM outputs A + B -> only A survives.
    """
    cmd_a = "check_status_unit_alpha"
    cmd_b = "check_status_unit_beta"
    approved_catalog = [
        {"command": cmd_a, "is_authorized": True, "authorization_decision": "authorized"},
        {"command": cmd_b, "is_authorized": True, "authorization_decision": "authorized"},
    ]
    tae_exec = _build_tae_execution(
        command=cmd_a,
        command_source="dynamic_mop_source",
        approved_commands=approved_catalog,
    )

    tm_output = f"Run `{cmd_a}`, then execute `{cmd_b}`."
    sanitized = enforce_technical_authority_synthesis_boundary(tm_output, tae_exec)

    assert cmd_a in sanitized
    assert cmd_b not in sanitized
    assert sanitized == f"Run `{cmd_a}`."


def test_regression_catalog_has_commands_selected_is_none_no_command_survives() -> None:
    """Regression 2: Dynamic check: catalog has authorized commands,

    diagnostic_step.command = None -> no operational command survives.
    """
    cmd_a = "check_telemetry_stream"
    cmd_b = "dump_diagnostics_buffer"
    approved_catalog = [
        {"command": cmd_a, "is_authorized": True, "authorization_decision": "authorized"},
        {"command": cmd_b, "is_authorized": True, "authorization_decision": "authorized"},
    ]
    tae_exec = _build_tae_execution(
        command=None,
        action="",
        approved_commands=approved_catalog,
    )

    tm_output = f"Please run `{cmd_a}` or execute `{cmd_b}` immediately."
    sanitized = enforce_technical_authority_synthesis_boundary(tm_output, tae_exec)

    assert cmd_a not in sanitized
    assert cmd_b not in sanitized
    assert sanitized == SAFE_SYNTHESIS_FALLBACK




# ==============================================================================
# STEP 4 FINAL BOUNDARY: live-flow narrative operational leakage
# ==============================================================================

from backend.agents.technical_authority_engineer.synthesis_boundary import GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT

_LIVE_GOVERNED_EVIDENCE = [
    {
        "source_id": "MOP-X:v1:MOP-X:v1:section-0000",
        "source_type": "governed_knowledge",
        "title": "Governed alarm procedure",
        "content_snippet": (
            "Alarm A | Restart the affected radio(RRU)\n"
            "Check whether the radio is connected to unit type P or unit type Q.\n"
            "acc FieldReplaceableUnit=1 restartunit\n"
            "HC Commands:\nalt\nst pluginunit\n"
        ),
        "metadata": {"lifecycle_status": "approved", "applicability_outcome": "match"},
    }
]


def test_live_flow_observational_step_does_not_leak_future_restart() -> None:
    tae_exec = _build_tae_execution(
        command=None,
        action="Check the type of unit the affected radio is connected to (unit type P or unit type Q)",
        verified_evidence=_LIVE_GOVERNED_EVIDENCE,
    )
    tm_text = (
        "Check the type of unit the affected radio is connected to (unit type P or unit type Q). "
        "This distinction will determine the specific command to be used for a radio restart, "
        "which is suggested as the primary resolution step. "
        "Restart the affected radio(RRU) afterwards. "
        "Then restart the radio."
    )
    sanitized = enforce_technical_authority_synthesis_boundary(tm_text, tae_exec)
    assert sanitized.startswith("Check the type of unit the affected radio is connected to")
    for leaked in ("restart", "reset", "restartunit", "resolution step"):
        assert leaked not in sanitized.lower()


def test_live_flow_evidence_state_changing_line_is_not_projected() -> None:
    tae_exec = _build_tae_execution(command=None, action="Check the unit type", verified_evidence=_LIVE_GOVERNED_EVIDENCE)
    sanitized = enforce_technical_authority_synthesis_boundary(
        "Check the unit type. The procedure then says: acc FieldReplaceableUnit=1 restartunit.", tae_exec
    )
    assert sanitized == "Check the unit type."


def test_live_flow_fully_stripped_synthesis_projects_current_step_only() -> None:
    tae_exec = _build_tae_execution(command=None, action="Check the unit type", verified_evidence=_LIVE_GOVERNED_EVIDENCE)
    sanitized = enforce_technical_authority_synthesis_boundary("Restart the radio.", tae_exec)
    assert sanitized.startswith("Check the unit type.")
    assert GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT in sanitized
    assert "restart" not in sanitized.lower()


def test_blocked_command_wording_when_governed_procedure_exists() -> None:
    tae_exec = _build_tae_execution(command=None, action="Check the unit type", verified_evidence=_LIVE_GOVERNED_EVIDENCE)
    sanitized = enforce_technical_authority_synthesis_boundary(
        "Check the unit type. I cannot provide the exact command because an approved governed procedure is required.",
        tae_exec,
    )
    assert "approved governed procedure is required" not in sanitized
    assert "cannot provide" not in sanitized.lower()
    assert GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT in sanitized
    # Without governed evidence the statement is left alone (no false claim of availability).
    no_governed = _build_tae_execution(command=None, action="Check the unit type")
    kept = enforce_technical_authority_synthesis_boundary("I cannot provide the exact command.", no_governed)
    assert GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT not in kept


def test_live_flow_authorized_command_projected_without_future_action() -> None:
    tae_exec = _build_tae_execution(
        command="alt",
        command_source="MOP-X:v1:MOP-X:v1:section-0000",
        action="Check active alarms",
        verified_evidence=_LIVE_GOVERNED_EVIDENCE,
        approved_commands=[{"command": "alt", "source_id": "MOP-X:v1:MOP-X:v1:section-0000"}],
    )
    sanitized = enforce_technical_authority_synthesis_boundary(
        "Check active alarms with `alt`. If the alarm persists, a radio restart is the resolution. Also run st pluginunit.",
        tae_exec,
    )
    assert sanitized == "Check active alarms with `alt`."


def test_negated_restart_never_becomes_instruction_or_authority() -> None:
    tae_exec = _build_tae_execution(command=None, action="Check the unit type", verified_evidence=_LIVE_GOVERNED_EVIDENCE)
    sanitized = enforce_technical_authority_synthesis_boundary(
        "Check the unit type. Do not restart the radio.", tae_exec
    )
    assert sanitized == "Check the unit type."
    only_negation = enforce_technical_authority_synthesis_boundary("Do not restart the radio.", tae_exec)
    assert "restart" not in only_negation.lower()
    assert only_negation.startswith("Check the unit type.")
    # With an authorized read-only command, a negated restart still grants nothing and is removed.
    alt_exec = _build_tae_execution(command="alt", action="Check active alarms", verified_evidence=_LIVE_GOVERNED_EVIDENCE)
    assert enforce_technical_authority_synthesis_boundary("Run alt. Do not restart the radio.", alt_exec) == "Run alt."


def test_inflected_state_change_forms_are_classifier_derived() -> None:
    tae_exec = _build_tae_execution(command=None, action="Check the unit type", verified_evidence=_LIVE_GOVERNED_EVIDENCE)
    sanitized = enforce_technical_authority_synthesis_boundary(
        "Check the unit type. Restarting the radio usually clears it. The node was rebooted earlier.", tae_exec
    )
    assert sanitized == "Check the unit type."
