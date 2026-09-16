"""CONTROL-PLANE-SEQ-05 -- Final Response Authority Boundary + DEF-0045
Closure.

Narrowly focused unit tests for the ONE new final authority boundary this
milestone adds: `AuthorizedResponseContext`/`build_authorized_response_
context`/`extract_known_commands_from_guidance` (authorized_response.py)
and `validate_final_output` (final_output_validator.py). These are pure,
deterministic functions -- exercised directly, never through a full
`ChatService`/`FakeRunner` turn, matching this codebase's own established
style for `request_execution_policy.py`'s own unit-level suites (e.g.
test_livecorr3b_operational_authority_boundary.py).

No real Gemini call, no external network, no Cloud SQL requirement.
"""
from __future__ import annotations

from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingStep,
)
from backend.agents.team_manager.authorized_response import (
    AuthorizedResponseContext,
    build_authorized_response_context,
    extract_known_commands_from_guidance,
)
from backend.agents.team_manager.final_output_validator import validate_final_output
from backend.agents.team_manager.request_execution_policy import RequestExecutionDecision, RequestExecutionStatus

_GROUNDED_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_OTHER_COMMAND = "accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"


def _decision(
    *,
    status: str = RequestExecutionStatus.ALLOW,
    may_emit_command: bool = False,
    may_emit_operational_steps: bool = False,
    may_execute_action: bool = False,
    request_class: str = "exact_command",
) -> RequestExecutionDecision:
    return RequestExecutionDecision(
        status=status,
        request_class=request_class,
        intent="command",
        requested_output="exact_command",
        subject="restart RRU",
        may_emit_command=may_emit_command,
        may_emit_operational_steps=may_emit_operational_steps,
        may_execute_action=may_execute_action,
        missing_context=[],
        ambiguity=False,
        approval_required=False,
        reason="test fixture",
    )


# =============================================================================
# extract_known_commands_from_guidance
# =============================================================================


def test_extract_known_commands_none_guidance_returns_empty_set() -> None:
    assert extract_known_commands_from_guidance(None) == set()


def test_extract_known_commands_next_step_command() -> None:
    guidance = TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, command=_GROUNDED_COMMAND)
    assert extract_known_commands_from_guidance(guidance) == {_GROUNDED_COMMAND}


def test_extract_known_commands_full_procedure_steps() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[
            TroubleshootingStep(action="restart RRU", command=_GROUNDED_COMMAND),
            TroubleshootingStep(action="restart AAS", command=_OTHER_COMMAND),
            TroubleshootingStep(action="visual check only"),
        ],
    )
    assert extract_known_commands_from_guidance(guidance) == {_GROUNDED_COMMAND, _OTHER_COMMAND}


# =============================================================================
# build_authorized_response_context
# =============================================================================


def test_build_context_forces_empty_authorized_commands_when_may_emit_command_false() -> None:
    """Section 4's own non-negotiable rule, enforced structurally even
    when a caller (mistakenly) supplies a non-empty allowlist."""
    context = build_authorized_response_context(
        _decision(may_emit_command=False),
        authorized_commands=[_GROUNDED_COMMAND],
        known_commands=[_GROUNDED_COMMAND],
    )
    assert context.authorized_commands == frozenset()
    assert context.prohibited_commands == frozenset({_GROUNDED_COMMAND})


def test_build_context_preserves_authorized_commands_when_may_emit_command_true() -> None:
    context = build_authorized_response_context(
        _decision(may_emit_command=True),
        authorized_commands=[_GROUNDED_COMMAND],
        known_commands=[_GROUNDED_COMMAND, _OTHER_COMMAND],
    )
    assert context.authorized_commands == frozenset({_GROUNDED_COMMAND})
    assert context.prohibited_commands == frozenset({_OTHER_COMMAND})


def test_build_context_clarification_required_flag() -> None:
    assert build_authorized_response_context(_decision(status=RequestExecutionStatus.NEEDS_INFORMATION)).clarification_required is True
    assert build_authorized_response_context(_decision(status=RequestExecutionStatus.AMBIGUOUS)).clarification_required is True
    assert build_authorized_response_context(_decision(status=RequestExecutionStatus.ALLOW)).clarification_required is False


# =============================================================================
# validate_final_output -- the 9 required acceptance scenarios (section 27)
# =============================================================================


def test_1_may_emit_command_false_structured_command_absent() -> None:
    decision = _decision(may_emit_command=False)
    context = build_authorized_response_context(decision, known_commands=[_GROUNDED_COMMAND])
    text = f"Run:\n\n{_GROUNDED_COMMAND}"
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is False
    assert _GROUNDED_COMMAND not in rendered


def test_2_may_emit_command_false_command_duplicated_in_action_free_text() -> None:
    decision = _decision(may_emit_command=False)
    context = build_authorized_response_context(decision, known_commands=[_GROUNDED_COMMAND])
    text = f"Next, restart the unit by running {_GROUNDED_COMMAND} on the affected RRU."
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is False
    assert _GROUNDED_COMMAND not in rendered


def test_3_may_emit_command_false_command_duplicated_in_interpretation_or_summary() -> None:
    decision = _decision(may_emit_command=False)
    context = build_authorized_response_context(decision, known_commands=[_GROUNDED_COMMAND])
    text = f"Based on the evidence, the recommended fix is: {_GROUNDED_COMMAND}. Please confirm the unit id first."
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is False
    assert _GROUNDED_COMMAND not in rendered


def test_4_may_emit_command_true_authorized_grounded_command_renders() -> None:
    decision = _decision(may_emit_command=True)
    context = build_authorized_response_context(
        decision, authorized_commands=[_GROUNDED_COMMAND], known_commands=[_GROUNDED_COMMAND]
    )
    text = f"Run:\n\n{_GROUNDED_COMMAND}"
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is True
    assert rendered == text


def test_5_may_emit_command_true_second_non_authorized_command_fails_closed() -> None:
    decision = _decision(may_emit_command=True)
    context = build_authorized_response_context(
        decision, authorized_commands=[_GROUNDED_COMMAND], known_commands=[_GROUNDED_COMMAND, _OTHER_COMMAND]
    )
    text = f"Run:\n\n{_OTHER_COMMAND}"
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is False
    assert _OTHER_COMMAND not in rendered


def test_6_cross_procedure_stripped_command_cannot_reappear() -> None:
    """The cross-procedure-evidence acceptance case (section 20):
    `may_emit_command` is False (the command was never authorized in the
    first place, exactly like an evidence.py CROSS_PROCEDURE_EVIDENCE
    rejection), and the rejected command is embedded inside otherwise
    plausible clarification-style prose."""
    decision = _decision(may_emit_command=False, status=RequestExecutionStatus.NEEDS_INFORMATION)
    context = build_authorized_response_context(decision, known_commands=[_GROUNDED_COMMAND])
    text = (
        "There are two possible procedures for this alarm. One of them recommends "
        f"running {_GROUNDED_COMMAND}, but I need to confirm which unit is affected first."
    )
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is False
    assert _GROUNDED_COMMAND not in rendered


def test_7_operational_steps_true_command_false_steps_render_without_command() -> None:
    decision = _decision(may_emit_command=False, may_emit_operational_steps=True)
    context = build_authorized_response_context(decision, known_commands=[_GROUNDED_COMMAND])
    safe_text = "Step 1: confirm the unit is powered on.\nStep 2: check the alarm log for recurrence."
    rendered, ok = validate_final_output(safe_text, decision, context)
    assert ok is True
    assert rendered == safe_text

    unsafe_text = f"Step 1: run {_GROUNDED_COMMAND}."
    rendered2, ok2 = validate_final_output(unsafe_text, decision, context)
    assert ok2 is False
    assert _GROUNDED_COMMAND not in rendered2


def test_8_natural_clarification_preserved_unless_it_leaks_a_rejected_command() -> None:
    decision = _decision(status=RequestExecutionStatus.NEEDS_INFORMATION, may_emit_command=False)
    context = build_authorized_response_context(decision, known_commands=[_GROUNDED_COMMAND])

    safe_clarification = "Could you confirm the exact unit ID before I provide the restart command?"
    rendered, ok = validate_final_output(safe_clarification, decision, context)
    assert ok is True
    assert rendered == safe_clarification

    leaking_clarification = f"Just to confirm before running {_GROUNDED_COMMAND} -- is RRU-9 correct?"
    rendered2, ok2 = validate_final_output(leaking_clarification, decision, context)
    assert ok2 is False
    assert _GROUNDED_COMMAND not in rendered2


def test_9_general_conversation_ordinary_response_unaffected() -> None:
    decision = _decision(
        status=RequestExecutionStatus.ALLOW,
        may_emit_command=False,
        may_emit_operational_steps=False,
        request_class="general_conversation",
    )
    context = build_authorized_response_context(decision)
    assert context.known_commands == frozenset()
    assert context.prohibited_commands == frozenset()
    text = "Hello! I'm SLOPANOC, your operational assistant. How can I help today?"
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is True
    assert rendered == text


# =============================================================================
# Additional boundary/normalization checks
# =============================================================================


def test_none_final_text_is_a_complete_no_op() -> None:
    decision = _decision(may_emit_command=False)
    context = build_authorized_response_context(decision, known_commands=[_GROUNDED_COMMAND])
    rendered, ok = validate_final_output(None, decision, context)
    assert rendered is None
    assert ok is True


def test_no_prohibited_commands_skips_the_check_entirely() -> None:
    decision = _decision(may_emit_command=True)
    context = build_authorized_response_context(decision, authorized_commands=[_GROUNDED_COMMAND], known_commands=[_GROUNDED_COMMAND])
    text = f"Run:\n\n{_GROUNDED_COMMAND}\n\nThen wait 30 seconds and re-check the alarm status."
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is True
    assert rendered == text


def test_crlf_normalization_does_not_cause_a_false_negative() -> None:
    """Section 15 -- CRLF/whitespace normalization only, never a
    semantic rewrite. A response reproducing the rejected command with
    different line endings/surrounding whitespace than the stored value
    must still be caught."""
    decision = _decision(may_emit_command=False)
    context = build_authorized_response_context(decision, known_commands=[f"  {_GROUNDED_COMMAND}  "])
    text = f"Run:\r\n\r\n{_GROUNDED_COMMAND}"
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is False
    assert _GROUNDED_COMMAND not in rendered


def test_case_is_never_folded() -> None:
    """Section 15 -- never lowercase/case-fold. A response that alters the
    case of a prohibited command must NOT be treated as a match (command
    syntax can be case-sensitive) -- this is a deliberate, documented
    limit of exact-substring matching, not a bug."""
    decision = _decision(may_emit_command=False)
    context = build_authorized_response_context(decision, known_commands=[_GROUNDED_COMMAND])
    text = f"Run:\n\n{_GROUNDED_COMMAND.upper()}"
    rendered, ok = validate_final_output(text, decision, context)
    assert ok is True
    assert rendered == text
