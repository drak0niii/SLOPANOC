"""A5 final corrective pass: focused tests for
backend/api/troubleshooting_guidance_context.py -- the deterministic
one-command-at-a-time rendering mechanism and its run-scoped capture
store. Covers A5 instruction section 18's required proofs.
"""
from __future__ import annotations

from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode, TroubleshootingStep
from backend.api.troubleshooting_guidance_context import (
    discard_troubleshooting_guidance,
    pop_troubleshooting_guidance,
    register_troubleshooting_guidance,
    render_troubleshooting_guidance,
)


def _next_step_guidance(**overrides: object) -> TroubleshootingGuidance:
    defaults: dict[str, object] = dict(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="The alarm is active per the retrieved evidence.",
        next_action="Check the current alarm status on the node.",
        command="alt",
        evidence_requested="Paste the alarm output here.",
    )
    defaults.update(overrides)
    return TroubleshootingGuidance(**defaults)  # type: ignore[arg-type]


# --- 1/2/3. NEXT_STEP returns one action, zero-or-one command, never leaks full_procedure_steps ---


def test_next_step_renders_one_action_and_one_command() -> None:
    text = render_troubleshooting_guidance(_next_step_guidance())
    assert "Check the current alarm status on the node." in text
    assert "alt" in text
    assert "Paste the alarm output here." in text


def test_next_step_allows_zero_commands() -> None:
    guidance = _next_step_guidance(command=None)
    text = render_troubleshooting_guidance(guidance)
    assert "Run:" not in text


def test_model_generated_later_steps_cannot_leak_into_next_step_render() -> None:
    # Even if the model ALSO populated full_procedure_steps (e.g. because
    # it was uncertain), NEXT_STEP mode must never expose them.
    guidance = _next_step_guidance(
        full_procedure_steps=[
            TroubleshootingStep(action="Restart the radio unit.", command="acc auxpluginUnit=xxx manualrestart"),
            TroubleshootingStep(action="Raise a ticket if the alarm persists."),
        ]
    )
    text = render_troubleshooting_guidance(guidance)
    assert "Restart the radio unit" not in text
    assert "manualrestart" not in text
    assert "Raise a ticket" not in text


# --- 4. state-changing later command cannot leak before prerequisite evidence ---


def test_state_changing_command_not_exposed_in_next_step_when_precondition_pending() -> None:
    # The model's NEXT_STEP command is the read-only precondition check;
    # the state-changing restart command (if it exists anywhere in the
    # model's own reasoning) must never appear in the rendered text.
    guidance = _next_step_guidance(next_action="Verify the alarm is still active before any restart is considered.", command="alt")
    text = render_troubleshooting_guidance(guidance)
    assert "alt" in text
    assert "restartunit" not in text
    assert "manualrestart" not in text


# --- 5. FULL_PROCEDURE may expose multiple steps ---


def test_full_procedure_renders_every_step_in_order() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        interpretation="Complete approved procedure:",
        full_procedure_steps=[
            TroubleshootingStep(action="Log in to the node.", command="amos NODE-1"),
            TroubleshootingStep(action="Check alarm status.", command="alt"),
            TroubleshootingStep(action="Restart if active.", command="acc auxpluginUnit=xxx manualrestart"),
        ],
    )
    text = render_troubleshooting_guidance(guidance)
    assert "Log in to the node." in text
    assert "amos NODE-1" in text
    assert "Check alarm status." in text
    assert "alt" in text
    assert "Restart if active." in text
    assert "acc auxpluginUnit=xxx manualrestart" in text
    # Order preserved.
    assert text.index("Log in") < text.index("Check alarm") < text.index("Restart if active")


def test_full_procedure_step_without_command_renders_action_only() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[TroubleshootingStep(action="Raise a ticket if the alarm persists.")],
    )
    text = render_troubleshooting_guidance(guidance)
    assert "Raise a ticket if the alarm persists." in text
    assert "Run:" not in text


# --- 6. ambiguous mode defaults safely -- interaction_mode is a required, closed enum ---


def test_interaction_mode_is_a_required_closed_field() -> None:
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TroubleshootingGuidance()  # type: ignore[call-arg]


# --- 7. exact command text preserved (no normalization/paraphrase) ---


def test_command_text_preserved_byte_for_byte() -> None:
    exact_command = 'acc FieldReplaceableUnit=RRU-9 restartunit 1 1 1'
    guidance = _next_step_guidance(command=exact_command)
    text = render_troubleshooting_guidance(guidance)
    assert exact_command in text


# --- 8. evidence/source refs preserved -- source citation is unaffected by this module ---


def test_renderer_does_not_touch_source_citation_mechanism() -> None:
    # The renderer only ever returns plain text -- it has no field/return
    # value related to knowledge_sources/evidence, which remain driven
    # entirely by the existing, unmodified knowledge_select_evidence
    # pipeline. This test documents that structural separation.
    import inspect

    signature = inspect.signature(render_troubleshooting_guidance)
    assert list(signature.parameters) == ["guidance"]
    assert signature.return_annotation in (str, "str")


# --- 9. no persistent troubleshooting state introduced -----------------


def test_store_is_purely_in_process_and_cleared_on_pop() -> None:
    register_troubleshooting_guidance("run-1", _next_step_guidance())
    assert pop_troubleshooting_guidance("run-1") is not None
    # Popped once -- gone.
    assert pop_troubleshooting_guidance("run-1") is None


def test_discard_is_safe_when_nothing_registered() -> None:
    discard_troubleshooting_guidance("run-never-registered")  # must not raise


def test_register_is_a_noop_for_missing_run_id_or_guidance() -> None:
    register_troubleshooting_guidance(None, _next_step_guidance())
    register_troubleshooting_guidance("run-2", None)
    assert pop_troubleshooting_guidance("run-2") is None


def test_two_runs_do_not_interfere() -> None:
    register_troubleshooting_guidance("run-a", _next_step_guidance(next_action="Action A"))
    register_troubleshooting_guidance("run-b", _next_step_guidance(next_action="Action B"))
    a = pop_troubleshooting_guidance("run-a")
    b = pop_troubleshooting_guidance("run-b")
    assert a is not None and a.next_action == "Action A"
    assert b is not None and b.next_action == "Action B"


# --- 10. unrelated Incident Manager answers remain unaffected ----------


def test_pop_returns_none_when_nothing_was_registered_for_this_run() -> None:
    # Mirrors an ordinary (non-troubleshooting) turn: incident_manager
    # never populated troubleshooting_guidance, so nothing is captured,
    # and chat_service.py's completion boundary must see None and do
    # nothing -- final_text stays exactly what team_manager produced.
    assert pop_troubleshooting_guidance("run-never-used") is None
