"""DEF-0027 corrective pass -- Active-Procedure Command Grounding &
Conditional Command Handling.

Closes a real, live-observed defect: a request for a governed procedure
that specifies DIFFERENT commands depending on an unknown condition (e.g.
which specific unit/equipment is affected) could produce a composite/
paraphrased command that DEF-0024's own grounding safeguard correctly
rejected -- but the UI then falsely claimed "The approved procedure does
not specify a command for this step," when the active procedure actually
DOES specify commands; the real problem was that the equipment condition
was not yet known / the generated command was not safely grounded.

Covers, per the milestone's own required test list (section 8):
   1.  explicit request resolves active procedure correctly
   2.  supporting sibling evidence does not become active
   3.  exact command in the active section passes
   4.  sibling-only command is rejected for the active procedure
   5.  supporting evidence cannot veto an active-procedure command
   6.  supporting evidence cannot authorize its own command
   7.  unknown condition returns no arbitrary command
   8.  unknown condition (well-behaved model) asks for the condition,
       never uses the no-command fallback (MISSING_CONDITION)
   9.  one conditional branch's exact command passes
   10. the OTHER conditional branch's exact command also passes
   11. a "no restart" branch correctly shows no command, no fallback text
   12. a composite multi-command string is rejected
   13. a paraphrased command is rejected
   14. TRUE_ABSENCE uses the original no-command fallback
   15. MISSING_CONDITION does NOT use the no-command fallback
   16. GROUNDING_REJECTED does NOT claim no command exists
   17. AMBIGUOUS_PROCEDURE asks for clarification
   18-20. DEF-0024/DEF-0026/explicit-topic-switching regressions are
       covered by their own existing, unmodified test files -- run as
       part of the full regression pass, not duplicated here.

Uses GENERIC placeholder text mirroring the real corpus SHAPE (a
conditional multi-branch alarm procedure: RRU / AAS / SupportUnit) --
the exact same placeholder command strings already established as
generic fixtures by `test_def_0024_procedure_grounding.py`/
`test_def_0026_governed_evidence_continuity.py` (never the real,
proprietary corpus content itself, and never any alarm-name-specific
test-only logic in production code).
"""
from __future__ import annotations

from backend.agents.incident_manager.evidence import (
    CommandGroundingReason,
    _GROUNDING_REJECTED_FALLBACK_TEXT,
    _UNGROUNDED_COMMAND_FALLBACK_TEXT,
    _capture_and_render_troubleshooting_guidance,
    enforce_procedure_scoped_command_grounding,
    enforce_procedure_scoped_command_grounding_with_reason,
)
from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceReference, KnowledgeEvidenceSelectionKey, KnowledgeEvidenceSet
from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
from backend.tools.knowledge import runtime as rt

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_AAS_COMMAND = "accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"

_HW_PARTIAL_FAULT_CONTENT = (
    "RRU:\n"
    f"{_RRU_COMMAND}\n\n"
    "AAS:\n"
    f"{_AAS_COMMAND}\n\n"
    "SupportUnit=---:\n"
    "No restart\n"
)

_HW_FAULT_CONTENT = (
    "Restart procedure differs from HW Partial Fault. "
    "Command: accn FieldReplaceableUnit=RRU-2 restartunit 1 1 1"
)


def _evidence_item(section_id: str, heading: str, content: str, knowledge_id: str = "def-0027-doc") -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, heading=heading, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(knowledge_id=knowledge_id, version_label="v1", section_id=section_id, source_system="test", source_id=f"{knowledge_id}-doc")
    return KnowledgeEvidenceItem(reference=reference, title="DEF-0027 Fixture Procedure", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)


_HWPF_ITEM = _evidence_item("s-hwpf", "HW Partial Fault", _HW_PARTIAL_FAULT_CONTENT)
_HW_FAULT_ITEM = _evidence_item("s-hwfault", "HW Fault", _HW_FAULT_CONTENT)


def _select(run_id: str, *items: KnowledgeEvidenceItem) -> None:
    rt.get_or_init_run_state(run_id)
    execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=list(items)))
    rt.record_search_result(run_id, execution)
    keys = [KnowledgeEvidenceSelectionKey(knowledge_id=item.reference.knowledge_id, version_label=item.reference.version_label, section_id=item.reference.section_id) for item in items]
    rt.select_evidence(run_id, keys)


def _next_step_guidance(command, next_action: str = "Do the next thing.", evidence_requested: str = "Provide the output.") -> TroubleshootingGuidance:
    return TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Interpretation.",
        next_action=next_action,
        command=command,
        evidence_requested=evidence_requested,
    )


# --- 1/2: active-procedure identity resolution ------------------------------


def test_explicit_request_resolves_active_procedure_correctly() -> None:
    """§1: both HW Partial Fault (active) and HW Fault (supporting) are
    selected this turn; the current question explicitly names HW Partial
    Fault -- its own real RRU command must be allowed."""
    run_id = "def0027-explicit-active-resolution"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _HW_FAULT_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(
            _next_step_guidance(_RRU_COMMAND), run_id, question="how do i handle HW Partial Fault?"
        )
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == _RRU_COMMAND


def test_supporting_sibling_evidence_does_not_become_active() -> None:
    """§2/§4/§6: same dual-selection, same explicit question -- but the
    proposed command belongs ONLY to the supporting HW Fault section, not
    the active HW Partial Fault section. Must be rejected -- HW Fault
    never becomes active merely by being co-selected."""
    run_id = "def0027-supporting-not-active"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _HW_FAULT_ITEM)
        sibling_only_command = "accn FieldReplaceableUnit=RRU-2 restartunit 1 1 1"
        guidance, stripped = enforce_procedure_scoped_command_grounding(
            _next_step_guidance(sibling_only_command), run_id, question="how do i handle HW Partial Fault?"
        )
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None


# --- 3/5: active-procedure command allowed, supporting sibling cannot veto --


def test_exact_command_in_active_section_passes() -> None:
    """§3."""
    run_id = "def0027-exact-command-passes"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _HW_FAULT_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(
            _next_step_guidance(_RRU_COMMAND), run_id, question="how do i handle HW Partial Fault?"
        )
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == _RRU_COMMAND


def test_supporting_evidence_cannot_veto_active_procedure_command() -> None:
    """§5: THE key relaxation from DEF-0024's own "grounded in every
    selected section" rule -- the RRU command is real and grounded in the
    ACTIVE HW Partial Fault section, but does NOT appear anywhere in the
    supporting HW Fault section's own content. It must still be allowed,
    because HW Fault is merely supporting, never authoritative to veto."""
    run_id = "def0027-supporting-cannot-veto"
    token = bind_run_id(run_id)
    try:
        assert _RRU_COMMAND not in _HW_FAULT_CONTENT  # sanity: genuinely absent from the sibling
        _select(run_id, _HWPF_ITEM, _HW_FAULT_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(
            _next_step_guidance(_RRU_COMMAND), run_id, question="how do i handle HW Partial Fault?"
        )
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == _RRU_COMMAND


# --- 7/8: unknown equipment condition ---------------------------------------


def test_unknown_condition_returns_no_arbitrary_command() -> None:
    """§7: only HW Partial Fault (the conditional, multi-branch procedure)
    is selected; the model proposes a composite/garbled command instead of
    asking which unit is affected -- it must be stripped, never shown."""
    run_id = "def0027-unknown-condition-no-command"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        composite_guess = "accn FieldReplaceableUnit=RRU-9 AAS-1 restartunit 1 1 1"
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(composite_guess), run_id)
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None


def test_unknown_condition_asks_for_condition_never_uses_no_command_fallback() -> None:
    """§8/§15: the WELL-BEHAVED model response -- `command` left unset,
    `next_action`/`evidence_requested` phrased as a clarifying question.
    Nothing is stripped (there is nothing to strip), and the rendered
    text never contains the "does not specify a command" fallback."""
    run_id = "def0027-missing-condition-asks"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance = _next_step_guidance(
            None,
            next_action="The approved procedure specifies a different action depending on the affected unit type.",
            evidence_requested="Please confirm whether the affected unit is an RRU, AAS, or SupportUnit.",
        )
        corrected, stripped = enforce_procedure_scoped_command_grounding(guidance, run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert corrected.command is None
    from backend.api.troubleshooting_guidance_context import render_troubleshooting_guidance

    rendered = render_troubleshooting_guidance(corrected)
    assert _UNGROUNDED_COMMAND_FALLBACK_TEXT not in rendered
    assert "RRU, AAS, or SupportUnit" in rendered


# --- 9/10/11: correct conditional-branch resolution once known -------------


def test_rru_condition_returns_exact_rru_command() -> None:
    """§9."""
    run_id = "def0027-rru-followup"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(_RRU_COMMAND), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == _RRU_COMMAND


def test_aas_condition_returns_exact_aas_command() -> None:
    """§10."""
    run_id = "def0027-aas-followup"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(_AAS_COMMAND), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == _AAS_COMMAND


def test_supportunit_condition_returns_no_restart_no_command() -> None:
    """§11: the model correctly determines no command applies (SupportUnit
    -> No restart) and leaves `command` unset -- never stripped, never a
    fallback sentence, since nothing was ever proposed."""
    run_id = "def0027-supportunit-no-restart"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance = _next_step_guidance(None, next_action="No restart is permitted for a SupportUnit.", evidence_requested=None)
        corrected, stripped = enforce_procedure_scoped_command_grounding(guidance, run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert corrected.command is None


# --- 12/13: composite/paraphrased commands rejected -------------------------


def test_composite_multi_command_string_is_rejected() -> None:
    """§12."""
    run_id = "def0027-composite-rejected"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        composite = f"{_RRU_COMMAND} AND {_AAS_COMMAND}"
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(composite), run_id)
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None


def test_paraphrased_command_is_rejected() -> None:
    """§13."""
    run_id = "def0027-paraphrase-rejected"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        paraphrased = "Restart the RRU unit using accn FieldReplaceableUnit=RRU-9 restartunit"
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(paraphrased), run_id)
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None


# --- 14/15/16/17: typed fallback-reason model -------------------------------


def test_true_absence_uses_the_no_command_fallback() -> None:
    """§14: the active section (HW Partial Fault) shares NO tokens at all
    with the proposed string -- a wholly foreign proposal -- classified as
    TRUE_ABSENCE, using the original "does not specify a command" text."""
    import json as _json

    run_id = "def0027-true-absence"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        foreign_command = "totally unrelated string xyz123"
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _next_step_guidance(foreign_command), run_id
        )
        assert stripped is True
        assert reason == CommandGroundingReason.TRUE_ABSENCE

        text = _next_step_response_text(_next_step_guidance(foreign_command))
        rendered = _capture_and_render_troubleshooting_guidance(text)
    finally:
        reset_run_id(token)
    assert rendered is not None
    assert _UNGROUNDED_COMMAND_FALLBACK_TEXT in _json.loads(rendered)["summary"]


def test_missing_condition_never_uses_the_no_command_fallback() -> None:
    """§15: re-stated at the reason-model level -- a well-behaved,
    command-unset response produces NO reason at all (nothing to strip),
    so the no-command fallback text is never appended."""
    run_id = "def0027-missing-condition-reason"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance = _next_step_guidance(None, evidence_requested="Please confirm whether the affected unit is an RRU, AAS, or SupportUnit.")
        corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert reason is None
    assert corrected.command is None


def test_grounding_rejected_does_not_claim_no_command_exists() -> None:
    """§16: a paraphrase/composite that plausibly overlaps the active
    section's own real content is GROUNDING_REJECTED -- the rendered
    fallback text must never claim the procedure "does not specify a
    command" (that would be false)."""
    import json as _json

    run_id = "def0027-grounding-rejected-wording"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        composite = f"{_RRU_COMMAND} AND {_AAS_COMMAND}"
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _next_step_guidance(composite), run_id
        )
        assert stripped is True
        assert reason == CommandGroundingReason.GROUNDING_REJECTED

        text = _next_step_response_text(_next_step_guidance(composite))
        rendered = _capture_and_render_troubleshooting_guidance(text)
    finally:
        reset_run_id(token)
    assert rendered is not None
    summary = _json.loads(rendered)["summary"]
    assert _GROUNDING_REJECTED_FALLBACK_TEXT in summary
    assert "does not specify a command" not in summary


def test_ambiguous_procedure_asks_for_clarification() -> None:
    """§17: two sections selected, no question given (so no active
    procedure can be resolved), and the command is grounded in only ONE
    of the two -- AMBIGUOUS_PROCEDURE, asking which procedure is meant."""
    import json as _json

    run_id = "def0027-ambiguous-clarification"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _HW_FAULT_ITEM)
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _next_step_guidance(_RRU_COMMAND), run_id
        )
        assert stripped is True
        assert reason == CommandGroundingReason.AMBIGUOUS_PROCEDURE
        assert guidance.command is None

        text = _next_step_response_text(_next_step_guidance(_RRU_COMMAND))
        rendered = _capture_and_render_troubleshooting_guidance(text)
    finally:
        reset_run_id(token)
    assert rendered is not None
    summary = _json.loads(rendered)["summary"]
    assert "confirm which governed procedure" in summary
    assert "does not specify a command" not in summary


def _next_step_response_text(guidance: TroubleshootingGuidance) -> str:
    """Builds the same raw JSON `text` shape `_capture_and_render_
    troubleshooting_guidance` expects, from an already-constructed
    `TroubleshootingGuidance` -- avoids duplicating field lists across
    every reason-model rendering test above."""
    import json

    return json.dumps(
        {
            "outcome": "ok",
            "summary": "placeholder -- overwritten by the renderer",
            "evidence": [],
            "candidate_titles": [],
            "detail": None,
            "troubleshooting_guidance": guidance.model_dump(mode="json"),
        }
    )
