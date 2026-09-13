"""DEF-0027 FINAL corrective pass -- Active Procedure Enforcement Across
Full Operational Guidance.

Closes the remaining HIGH-severity live failure found during manual
browser acceptance of the first DEF-0027 corrective pass: a genuinely
successful "HW Partial Fault" turn selecting evidence ALONGSIDE an
unrelated Rogers Resource Timeout MOP let that unrelated document's own
operational content (SSH/AMOS steps, DUS/Baseband Radio handling,
wait-time/escalation instructions, and a bare confirmation token
rendered as "Run:\n\ny") reach the user -- because the existing DEF-0024/
DEF-0027 grounding machinery only ever scoped `TroubleshootingGuidance
.command`, never the free-text `interpretation`/`next_action`/
`evidence_requested`/`TroubleshootingStep.action` fields, and DEF-0026's
own prior-evidence continuity only ever engaged when a turn's selected
evidence was EMPTY, never when it was merely WRONG.

Three fixes, each independently testable:

  Fix #1 (`backend/api/governed_evidence_continuity.py`,
  `detect_governed_evidence_anchor_mismatch`): extends DEF-0026's own
  revalidation/override machinery to the NORMAL (non-remediation)
  completion boundary -- a non-empty but cross-document-inconsistent
  selection, with no explicit textual justification, is now detected.

  Fix #2 (`backend/agents/incident_manager/evidence.py`,
  `_guidance_scope_established`): the ENTIRE `TroubleshootingGuidance`
  object -- never only `command` -- is suppressed whenever this turn's
  selected governed evidence spans more than one distinct governed
  document (`knowledge_id`).

  Fix #3 (`backend/agents/incident_manager/evidence.py`,
  `_is_confirmation_token`): a small, generic denylist of bare
  confirmation/response tokens (y/n/yes/no/0/1/ok/true/false) is rejected
  BEFORE any substring grounding check, closing the "Run:\n\ny" soundness
  gap without rejecting real short commands (e.g. the pre-existing
  "alt" fixture in `test_evidence_troubleshooting_guidance.py`).

Covers, per the milestone's own required test list (section 10):
   1.  prior active HW Partial Fault anchor survives a referential
       SupportUnit follow-up (consistent selection -> no mismatch)
   2.  non-empty but WRONG Knowledge selection is detected
   3.  wrong KnowledgeObject selection cannot bypass DEF-0026 continuity
       without an explicit textual justification
   4.  explicit new topic overrides prior anchor
   5.  supporting evidence (same governed document) may still contribute
       factual context -- narrative is not suppressed for a same-document
       selection
   6.  supporting evidence cannot introduce restart steps (cross-document)
   7.  supporting evidence cannot introduce reset steps (cross-document)
   8.  supporting evidence cannot introduce escalation/ticketing steps
       (cross-document)
   9.  supporting evidence cannot override the active procedure's own
       no-restart rule (cross-document)
   10. entire operational guidance fails closed when the turn's evidence
       spans more than one governed document
   11. command-only grounding remains strict (single-document paraphrase
       still rejected)
   12. "y" cannot be rendered as an operational command
   13. obvious confirmation-only tokens cannot pass command grounding
   14. real RRU command still passes
   15. real AAS command still passes
   16. SupportUnit returns no restart
   17-20. DEF-0024/DEF-0026/existing-DEF-0027/explicit-topic-change
       regressions -- covered by re-running their own existing,
       unmodified test files (not duplicated here); see this pass's own
       closure report for the regression run.
"""
from __future__ import annotations

import pytest

from backend.agents.incident_manager.evidence import (
    CommandGroundingReason,
    _CROSS_PROCEDURE_EVIDENCE_FALLBACK_TEXT,
    _GROUNDING_REJECTED_FALLBACK_TEXT,
    _capture_and_render_troubleshooting_guidance,
    enforce_procedure_scoped_command_grounding,
    enforce_procedure_scoped_command_grounding_with_reason,
)
from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode, TroubleshootingStep
from backend.api.governed_evidence_continuity import RevalidatedGovernedProcedure, detect_governed_evidence_anchor_mismatch
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


def _evidence_item(section_id: str, heading: str, content: str, knowledge_id: str = "def-0027-doc") -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, heading=heading, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(knowledge_id=knowledge_id, version_label="v1", section_id=section_id, source_system="test", source_id=f"{knowledge_id}-doc")
    return KnowledgeEvidenceItem(reference=reference, title="DEF-0027 Fixture Procedure", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)


_HWPF_ITEM = _evidence_item("s-hwpf", "HW Partial Fault", _HW_PARTIAL_FAULT_CONTENT, knowledge_id="doc1")
_HW_FAULT_ITEM = _evidence_item("s-hwfault", "HW Fault", "General hardware fault context. Same document as HW Partial Fault.", knowledge_id="doc1")
_ROGERS_ITEM = _evidence_item(
    "s-rogers-timeout",
    "Resource Activation Timeout",
    "SSH to ENM/AMOS. Check active alarms. DUS Radio and Baseband Radio handling. Confirm restart with: y. Wait 5 minutes. Escalate via ticket if unresolved.",
    knowledge_id="rogers-mop",
)


def _select(run_id: str, *items: KnowledgeEvidenceItem) -> None:
    rt.get_or_init_run_state(run_id)
    execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=list(items)))
    rt.record_search_result(run_id, execution)
    keys = [KnowledgeEvidenceSelectionKey(knowledge_id=item.reference.knowledge_id, version_label=item.reference.version_label, section_id=item.reference.section_id) for item in items]
    rt.select_evidence(run_id, keys)


def _next_step_guidance(command, next_action: str = "Do the next thing.", evidence_requested: str = "Provide the output.", interpretation: str = "Interpretation.") -> TroubleshootingGuidance:
    return TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation=interpretation,
        next_action=next_action,
        command=command,
        evidence_requested=evidence_requested,
    )


def _full_procedure_guidance(steps: list[tuple[str, object]]) -> TroubleshootingGuidance:
    return TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        interpretation="Interpretation.",
        full_procedure_steps=[TroubleshootingStep(action=action, command=command) for action, command in steps],
    )


def _next_step_response_text(guidance: TroubleshootingGuidance) -> str:
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


# =============================================================================
# Fix #1 -- prior-anchor consistency even when evidence is non-empty
# (detect_governed_evidence_anchor_mismatch, pure function)
# =============================================================================


def _anchor(knowledge_id: str = "doc1", heading: str = "HW Partial Fault") -> RevalidatedGovernedProcedure:
    return RevalidatedGovernedProcedure(
        knowledge_id=knowledge_id,
        version_label="v2-def0024-fix",
        section_id="s-hwpf",
        title="Document1",
        heading=heading,
        sibling_headings=("HW Fault", "VSWR Over Threshold"),
    )


def test_prior_active_anchor_survives_referential_followup_with_consistent_selection() -> None:
    """#1: current turn's own selection includes the anchor's own
    knowledge_id (even alongside other evidence) -- consistent, no
    mismatch."""
    mismatch = detect_governed_evidence_anchor_mismatch("it's a SupportUnit", _anchor(), [_HWPF_ITEM])
    assert mismatch is False


def test_nonempty_wrong_selection_is_detected_as_mismatch() -> None:
    """#2: the exact live-reproduced shape -- current selection is
    entirely from a DIFFERENT governed document (Rogers), the anchor
    (Document1/HW Partial Fault) is not represented at all, and the raw
    user text ("it's a SupportUnit") names no real heading of the
    selected Rogers evidence."""
    mismatch = detect_governed_evidence_anchor_mismatch("it's a SupportUnit", _anchor(), [_ROGERS_ITEM])
    assert mismatch is True


def test_wrong_knowledgeobject_cannot_bypass_continuity_without_explicit_justification() -> None:
    """#3: even with MULTIPLE selected items this turn, if NONE of them
    belong to the anchor's own knowledge_id and none of their real
    headings are named in the question, it is still a mismatch."""
    other_rogers_item = _evidence_item("s-rogers-other", "Resource Degraded", "Different Rogers section.", knowledge_id="rogers-mop")
    mismatch = detect_governed_evidence_anchor_mismatch("it's a SupportUnit", _anchor(), [_ROGERS_ITEM, other_rogers_item])
    assert mismatch is True


def test_explicit_new_topic_overrides_prior_anchor() -> None:
    """#4: the question verbatim-names the REAL heading of the (different-
    document) evidence actually selected this turn -- an explicit textual
    basis for the switch, e.g. "how do I handle Resource Activation
    Timeout?" -- must be allowed to leave the prior anchor."""
    mismatch = detect_governed_evidence_anchor_mismatch(
        "how do I handle Resource Activation Timeout?", _anchor(), [_ROGERS_ITEM]
    )
    assert mismatch is False


def test_empty_current_selection_is_not_a_mismatch() -> None:
    """The empty-evidence case remains the pre-existing gate's own job --
    this function's scope is narrower (non-empty-but-wrong only)."""
    mismatch = detect_governed_evidence_anchor_mismatch("it's a SupportUnit", _anchor(), [])
    assert mismatch is False


# =============================================================================
# Fix #2 -- full operational-guidance procedure boundary
# =============================================================================


def test_supporting_evidence_same_document_may_still_contribute_factual_context() -> None:
    """#5: HW Fault is merely supporting, but SAME governed document as
    the active HW Partial Fault section -- narrative fields are NOT
    suppressed purely because more than one section from the SAME
    document was selected (this is the pre-existing, already-accepted
    DEF-0024 behavior for same-document co-selection, untouched)."""
    run_id = "def0027-final-same-doc-supporting-context"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _HW_FAULT_ITEM)
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _next_step_guidance(_RRU_COMMAND), run_id, question="how do i handle HW Partial Fault?"
        )
    finally:
        reset_run_id(token)
    assert stripped is False
    assert reason is None
    assert guidance.command == _RRU_COMMAND
    assert guidance.next_action == "Do the next thing."


def test_supporting_evidence_cross_document_cannot_introduce_restart_steps() -> None:
    """#6: HW Partial Fault (active, correctly named by the question) is
    selected ALONGSIDE an unrelated Rogers MOP. The model's narrative
    describes a restart step drawn from Rogers -- the ENTIRE guidance
    must be suppressed, not merely its command."""
    run_id = "def0027-final-cross-doc-restart"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _ROGERS_ITEM)
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _next_step_guidance(
                None,
                next_action="SSH to ENM/AMOS, check active alarms, then restart the DUS Radio unit.",
                evidence_requested="Confirm the restart completed.",
            ),
            run_id,
            question="how do i handle HW Partial Fault?",
        )
    finally:
        reset_run_id(token)
    assert stripped is True
    assert reason == CommandGroundingReason.CROSS_PROCEDURE_EVIDENCE
    assert guidance.next_action is None
    assert guidance.interpretation is None
    assert guidance.evidence_requested is None
    assert guidance.command is None


def test_supporting_evidence_cross_document_cannot_introduce_reset_steps() -> None:
    """#7: same shape, a "reset" instruction instead of "restart"."""
    run_id = "def0027-final-cross-doc-reset"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _ROGERS_ITEM)
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _next_step_guidance(None, next_action="Reset the Baseband Radio unit and wait 5 minutes."),
            run_id,
            question="how do i handle HW Partial Fault?",
        )
    finally:
        reset_run_id(token)
    assert stripped is True
    assert reason == CommandGroundingReason.CROSS_PROCEDURE_EVIDENCE
    assert guidance.next_action is None


def test_supporting_evidence_cross_document_cannot_introduce_escalation_steps() -> None:
    """#8: an escalation/ticketing instruction from the unrelated Rogers
    MOP must not reach the user either."""
    run_id = "def0027-final-cross-doc-escalation"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _ROGERS_ITEM)
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _full_procedure_guidance(
                [
                    ("Check active alarms via AMOS.", None),
                    ("If unresolved after 5 minutes, escalate via ticket.", None),
                ]
            ),
            run_id,
            question="how do i handle HW Partial Fault?",
        )
    finally:
        reset_run_id(token)
    assert stripped is True
    assert reason == CommandGroundingReason.CROSS_PROCEDURE_EVIDENCE
    assert guidance.full_procedure_steps == []


def test_supporting_evidence_cannot_override_active_procedure_no_restart_rule() -> None:
    """#9: the active procedure's own real rule is "SupportUnit -> No
    restart" -- an unrelated, cross-document Rogers section must not be
    allowed to suggest a restart path instead, even indirectly through
    free-text guidance."""
    run_id = "def0027-final-cross-doc-no-restart-override"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _ROGERS_ITEM)
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _next_step_guidance(
                None,
                next_action="Find the RRU identifier and restart it, since the unit type could not be confirmed as SupportUnit.",
            ),
            run_id,
            question="how do i handle HW Partial Fault?",
        )
    finally:
        reset_run_id(token)
    assert stripped is True
    assert reason == CommandGroundingReason.CROSS_PROCEDURE_EVIDENCE
    assert guidance.next_action is None

    rendered_text = _next_step_response_text(
        _next_step_guidance(None, next_action="Find the RRU identifier and restart it.")
    )
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _ROGERS_ITEM)
        rendered = _capture_and_render_troubleshooting_guidance(rendered_text, "how do i handle HW Partial Fault?")
    finally:
        reset_run_id(token)
    assert rendered is not None
    import json as _json

    summary = _json.loads(rendered)["summary"]
    assert "restart" not in summary.lower() or "RRU" not in summary
    assert _CROSS_PROCEDURE_EVIDENCE_FALLBACK_TEXT in summary


def test_entire_guidance_fails_closed_when_evidence_spans_multiple_documents() -> None:
    """#10: the FULL_PROCEDURE-shaped, real live-reproduced symptom --
    multiple narrative steps plus a bare confirmation-token "command"
    ("y") -- all suppressed together, not merely the command."""
    run_id = "def0027-final-full-procedure-cross-doc"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM, _ROGERS_ITEM)
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _full_procedure_guidance(
                [
                    ("SSH to ENM/AMOS.", None),
                    ("Check active alarms.", None),
                    ("Restart the DUS Radio or Baseband Radio as applicable.", None),
                    ("Confirm the restart.", "y"),
                    ("Wait 5 minutes.", None),
                    ("Escalate via ticket if unresolved.", None),
                ]
            ),
            run_id,
            question="how do i handle HW Partial Fault?",
        )
    finally:
        reset_run_id(token)
    assert stripped is True
    assert reason == CommandGroundingReason.CROSS_PROCEDURE_EVIDENCE
    assert guidance.full_procedure_steps == []
    assert guidance.command is None
    assert guidance.next_action is None
    assert guidance.interpretation is None
    assert guidance.evidence_requested is None


# =============================================================================
# Fix #3 -- command shape / meaningfulness guard
# =============================================================================


def test_command_only_grounding_remains_strict() -> None:
    """#11: a single-document paraphrase is still rejected exactly as
    before this pass."""
    run_id = "def0027-final-strict-paraphrase"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        paraphrased = "Restart the RRU unit using accn FieldReplaceableUnit=RRU-9 restartunit"
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(paraphrased), run_id)
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None


def test_y_cannot_be_rendered_as_an_operational_command() -> None:
    """#12: THE exact reported live symptom -- "Run:\n\ny"."""
    run_id = "def0027-final-y-command"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _next_step_guidance("y"), run_id
        )
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None
    assert reason == CommandGroundingReason.GROUNDING_REJECTED

    from backend.api.troubleshooting_guidance_context import render_troubleshooting_guidance

    rendered = render_troubleshooting_guidance(guidance)
    assert "Run:" not in rendered
    assert rendered.strip() != "y"


@pytest.mark.parametrize("token_value", ["n", "yes", "no", "0", "1", "ok", "true", "false", "Y", "N", " y ", "YES"])
def test_confirmation_only_tokens_cannot_pass_command_grounding(token_value: str) -> None:
    """#13: the full denylist, including case-insensitivity and
    surrounding whitespace -- none of these may ever be trusted as a
    real operational command, regardless of what section is selected."""
    run_id = f"def0027-final-confirmation-token-{abs(hash(token_value))}"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(
            _next_step_guidance(token_value), run_id
        )
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None
    assert reason == CommandGroundingReason.GROUNDING_REJECTED


def test_real_alt_command_is_not_rejected_by_the_confirmation_guard() -> None:
    """Negative proof for Fix #3 -- a real, legitimate short command
    ("alt", already relied upon by `test_evidence_troubleshooting_
    guidance.py`'s own fixture) must remain fully valid; the denylist
    targets confirmation SEMANTICS, never mere shortness."""
    alt_item = _evidence_item("s-alt", "Procedure", "The command is: alt", knowledge_id="doc1")
    run_id = "def0027-final-alt-command-allowed"
    token = bind_run_id(run_id)
    try:
        _select(run_id, alt_item)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance("alt"), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == "alt"


def test_real_rru_command_still_passes() -> None:
    """#14."""
    run_id = "def0027-final-rru-passes"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(_RRU_COMMAND), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == _RRU_COMMAND


def test_real_aas_command_still_passes() -> None:
    """#15."""
    run_id = "def0027-final-aas-passes"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(_AAS_COMMAND), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == _AAS_COMMAND


def test_supportunit_returns_no_restart() -> None:
    """#16: the model correctly determines no command applies -- never
    stripped, never a fallback, since nothing was ever proposed."""
    run_id = "def0027-final-supportunit-no-restart"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HWPF_ITEM)
        guidance = _next_step_guidance(None, next_action="No restart is permitted for a SupportUnit.", evidence_requested=None)
        corrected, stripped = enforce_procedure_scoped_command_grounding(guidance, run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert corrected.command is None
    assert corrected.next_action == "No restart is permitted for a SupportUnit."
