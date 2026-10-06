"""Command Authority defense in depth: one command = ONE invocation.

A composed candidate (`;`, `&&`, `||`, `|`, newline, substitution, background, redirection) is never
classified read-only and never authorized -- even when that exact composed string is present in
APPROVED, SELECTED, applicability-MATCH governed knowledge, whatever path produced it (legacy
free-form model command, ProcedureAction resolver, forged resolver attestation). The Progression
Controller rejects multi-action steps independently.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.tests._target_fixtures import confirmed_and_validated

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.agent_tool import (
    authorize_grounded_command,
    build_server_validated_commands,
    classify_command_operation,
)
from backend.agents.technical_authority_engineer.command_syntax import single_invocation_violation
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController, ProposalDecision, is_multi_action_command
from backend.agents.technical_authority_engineer.schemas import CommandOperationType, EvidenceReference, GroundedCommand
from backend.cases.troubleshooting_state import TroubleshootingState

_SRC = "KID-CMD:v1:sec-0000"
CONFIRMED = confirmed_and_validated(("Board", "4"))  # target validated by the gate AND confirmed

COMPOSED = [
    ("st pluginunit; st fieldr", "command_separator"),
    ("st pluginunit && st fieldr", "and_list"),
    ("st pluginunit || st fieldr", "or_list"),
    ("st pluginunit | grep fieldr", "pipeline"),
    ("st pluginunit\nst fieldr", "multiple_lines"),
    ("st `reboot`", "command_substitution"),
    ("hget $(reboot)", "command_substitution"),
    ("hget ${x}", "command_substitution"),
    ("st pluginunit & st fieldr", "background_operator"),
    ("hget near Rfportref > out.txt", "redirection"),
    ("acc Board=4 restartboard; acc Board=5 restartboard", "command_separator"),
]
SINGLE_READS = ["st ru", "alt", "hget near Rfportref", "lget Equipment=1,Slot=2", "show foo NODE-1", "st .*"]


def _ev(content: str, applicability: str = "match") -> EvidenceReference:
    return EvidenceReference(
        source_id=_SRC, source_type="governed_knowledge", title="Command Procedure", content_snippet=content,
        metadata={"knowledge_id": "KID-CMD", "version_label": "v1", "section_id": "sec-0000", "source_locator": "lines:1-9",
                  "title": "Command Procedure", "heading": None, "lifecycle_status": "approved", "applicability_outcome": applicability},
    )


def _authorized(command: str, content: Optional[str] = None, trusted: Optional[dict[str, Any]] = None) -> list[str]:
    ev = _ev(content if content is not None else f"Governed step: `{command}`\n")
    return [c.command for c in build_server_validated_commands([ev], [{"command": command, "source_id": _SRC, "procedure_section": "s"}], trusted_context=trusted)]


def _grounded(command: str) -> GroundedCommand:
    return GroundedCommand(command=command, source_id=_SRC, knowledge_id="KID-CMD", version_label="v1", section_id="sec-0000",
                           procedure_section="s", grounding_method="exact_match", raw_snippet=command)


# 1 + 2. exact governed composition is never authorized ----------------------------------------------------------
@pytest.mark.parametrize(("command", "reason"), COMPOSED)
def test_composed_command_is_never_authorized_even_when_governed_verbatim(command: str, reason: str, monkeypatch) -> None:
    assert single_invocation_violation(command) == reason
    assert classify_command_operation(command) is not CommandOperationType.READ_ONLY_DIAGNOSTIC
    records: list[dict[str, Any]] = []
    monkeypatch.setattr("backend.agents.technical_authority_engineer.agent_tool.record_command_authority", lambda **kw: records.append(kw))
    assert authorize_grounded_command(_grounded(command), {}, CONFIRMED) is None, "rejected before classification, target confirmed or not"
    assert records[-1]["reason"] == f"not a single command invocation: {reason}"
    # End to end through grounding: the exact composed text sits in approved, selected, MATCH evidence.
    governed = command if "\n" not in command else command.replace("\n", " ; ")
    assert _authorized(command, content=f"Step: `{governed}`\n{command}\n", trusted=CONFIRMED) == []
    assert _authorized(f"`{command}`", content=f"Step: `{governed}`\n{command}\n", trusted=CONFIRMED) == [], "markdown wrapping changes nothing"


def test_the_live_chained_read_is_rejected_at_command_authority_itself() -> None:
    assert _authorized("st pluginunit; st fieldr") == []
    assert _authorized("st pluginunit; st fieldr", trusted=CONFIRMED) == []


# 3. normal single reads still work --------------------------------------------------------------------------------
@pytest.mark.parametrize("command", SINGLE_READS)
def test_single_governed_read_commands_are_still_authorized(command: str) -> None:
    assert single_invocation_violation(command) is None
    assert classify_command_operation(command) is CommandOperationType.READ_ONLY_DIAGNOSTIC
    assert _authorized(command) == [command]


@pytest.mark.parametrize("command", ["acc <mo> restart", "show $TARGET", "restart board <board_slot> --graceful", "`st ru`", "**st ru**"])
def test_placeholders_and_markdown_wrapping_are_not_mistaken_for_composition(command: str) -> None:
    assert single_invocation_violation(command) is None


# 4. single state-changing commands still reach their existing safety path ------------------------------------------
def test_single_state_change_still_reaches_target_confirmation(monkeypatch) -> None:
    command = "acc Board=4 restartboard"
    records: list[dict[str, Any]] = []
    monkeypatch.setattr("backend.agents.technical_authority_engineer.agent_tool.record_command_authority", lambda **kw: records.append(kw))
    assert authorize_grounded_command(_grounded(command), {}, None) is None
    assert records[-1]["reason"] == (
        "mutating_operational requires a validated current-case target: no passed current-case target validation"
    ), "the state-change target gate, not the composition gate"
    assert authorize_grounded_command(_grounded(command), {}, {"target_confirmed": True}) is None, "confirmation alone never suffices"
    authorized = authorize_grounded_command(_grounded(command), {}, CONFIRMED)
    assert authorized is not None and authorized.operation_type is CommandOperationType.MUTATING_OPERATIONAL


# 5. the ProcedureAction path cannot bypass the boundary ----------------------------------------------------------------
_SECTION = "Unit check: `st pluginunit`\nCombined check: `st pluginunit; st fieldr`\nPiped check: `st pluginunit | grep x`\nRecovery: `acc Board=xxxx restartboard`\n"


def test_composed_governed_template_is_never_extracted_as_a_procedure_action() -> None:
    ev = _ev(_SECTION)
    actions, skipped = pa.actions_for_evidence(ev, ev.metadata)
    assert [a.command_template for a in actions] == ["st pluginunit", "acc Board=xxxx restartboard"]
    reasons = {s["template"]: s["reason"] for s in skipped}
    assert reasons["st pluginunit; st fieldr"] == "not a single command invocation (command_separator)"
    assert "st pluginunit | grep x" in reasons


def test_resolver_rejects_a_composed_action_even_if_one_were_issued(monkeypatch) -> None:
    ev = _ev(_SECTION)
    real = pa.actions_for_evidence(ev, ev.metadata)[0][0]
    forged = real.model_copy(update={"action_id": "pa-forged", "command_template": "st pluginunit; st fieldr"})
    monkeypatch.setattr(pa, "actions_for_evidence", lambda e, m: ([forged], []))
    resolution, _ = pa.resolve_procedure_action("pa-forged", issued_ids={"pa-forged"}, selected_evidence=[ev])
    assert resolution.status is pa.ProcedureActionResolutionStatus.NOT_SINGLE_INVOCATION and resolution.candidate is None


def test_a_validly_attested_composed_candidate_is_still_refused_by_command_authority() -> None:
    ev = _ev(_SECTION)
    real = pa.actions_for_evidence(ev, ev.metadata)[0][0]
    forged_action = real.model_copy(update={"command_template": "st pluginunit; st fieldr"})
    candidate = pa._signed_candidate(forged_action, "st pluginunit; st fieldr", {}).as_authority_candidate("s")
    assert pa.verified_resolver_candidate(candidate) is not None, "attestation itself is valid"
    assert [c.command for c in build_server_validated_commands([ev], [candidate], trusted_context=CONFIRMED)] == []


# 6. legacy free-form model commands cannot bypass it --------------------------------------------------------------------
@pytest.mark.parametrize("command", ["st pluginunit; st fieldr", "`st pluginunit; st fieldr`", "st pluginunit && st fieldr", "st pluginunit | grep x"])
def test_legacy_free_form_composed_command_is_refused(command: str) -> None:
    ev = _ev(_SECTION)
    assert [c.command for c in build_server_validated_commands([ev], [{"command": command, "source_id": _SRC}], trusted_context=CONFIRMED)] == []


# 7. progression still rejects multi-action steps independently -------------------------------------------------------------
@pytest.mark.parametrize("command", [c for c, _ in COMPOSED])
def test_progression_controller_rejects_composed_steps_independently(command: str) -> None:
    assert is_multi_action_command(command)
    controller = ProgressionController({}, TroubleshootingState(fault_id="F", symptom_summary="s"), session_id="s1")
    proposal = {"outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
                "diagnostic_step": {"action": "Check the units", "reason": "r", "expected_evidence": "e", "command": None, "command_source": None, "restrictions": []}}
    # Command Authority already removed the command; the controller still sees the model's legacy proposal.
    decision, result = controller.evaluate_proposal(proposal, None, [], proposed_command=command)
    assert decision is ProposalDecision.REJECTED_MULTIPLE_ACTIONS and result["diagnostic_step"] is None
    assert controller.progression.steps_for("F") == []


def test_single_command_proposal_is_not_rejected_by_the_controller() -> None:
    assert not is_multi_action_command("st ru")
    controller = ProgressionController({}, TroubleshootingState(fault_id="F", symptom_summary="s"), session_id="s1")
    proposal = {"outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
                "diagnostic_step": {"action": "Check the radio unit", "reason": "r", "expected_evidence": "e", "command": "st ru", "command_source": _SRC, "restrictions": []}}
    assert controller.evaluate_proposal(proposal, None, [_ev("`st ru`")])[0] is ProposalDecision.NEW_STEP
