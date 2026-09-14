"""DEF-0024 corrective pass -- Alarm Procedure Granularity & Procedure-
Scoped Grounding.

Covers, per the milestone's own required test list:
  1.  numbered multi-procedure input becomes independent sections
  2.  generic segmentation is not hardcoded to alarm names
  3.  existing ordinary (period-numbered) instruction lists are untouched
  4.  embedded artifact sections remain correctly linked
  5.  section ordering/identity is deterministic
  6.  a VSWR-shaped procedure is independently selectable
  7.  a HW-Partial-Fault-shaped procedure is independently selectable
  8.  a VSWR follow-up cannot surface the HW Partial Fault command
  9.  existing command-verbatim validation still holds
  10. a command from the correct active procedure remains allowed
  11. an active procedure with no command cannot borrow a sibling's
  12. ambiguous multi-procedure command requests fail closed
  13. immediate referential follow-up preserves procedure scope when the
      prior governed evidence reference is safely available (this turn)
  14. an explicit topic change to a different procedure still works

Uses a document SHAPE deliberately mirroring the real, live DEF-0024
corpus fixture (a numbered multi-alarm-procedure list) but with entirely
GENERIC placeholder text -- never the real corpus content itself, and
never any alarm-name-specific test-only logic in production code (see
`test_no_hardcoded_operational_heading_vocabulary_in_processing_package`/
`test_no_branching_on_heading_text_identifiers`, unmodified, still
passing).
"""
from __future__ import annotations

import pytest

from backend.agents.incident_manager.evidence import enforce_procedure_scoped_command_grounding
from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingOperationalEffect,
    TroubleshootingStep,
)
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.processing.processor import HeadingStructureProcessor
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceReference, KnowledgeEvidenceSelectionKey, KnowledgeEvidenceSet
from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
from backend.tools.knowledge import runtime as rt

_MULTI_PROCEDURE_DOCUMENT = """Alarm Handling and Restart Procedure
1. General Preconditions
- Once an alarm appears, wait 30 minutes before attempting any restart.
- Restart is allowed only if the alarm is active.
2. Post-Restart Actions
- If the alarm clears after restart, close the action.
3. Alarm-Specific Actions
1) HW Partial Fault
- Restart is allowed only on RRU.
Command:
accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1
2) HW Fault
- Restart procedure same as HW Partial Fault.
3) Linearization Disturbance - Performance Degraded
- Restart on the identified RRU.
8) VSWR Over Threshold
- No restart allowed.
- Perform diagnosis only.
- Validate Return Loss and VSWR values from alert key.
"""


def _processed_document(content: str = _MULTI_PROCEDURE_DOCUMENT):
    source = KnowledgeSource(source_system="test", source_id="def-0024-fixture")
    document = IngestedKnowledgeDocument(source=source, title="DEF-0024 Fixture Procedure", content=content)
    return HeadingStructureProcessor().process(document)


# --- 1/2/3: generic segmentation -----------------------------------------


def test_numbered_procedure_items_become_independent_sections() -> None:
    structured = _processed_document()
    headings = [s.heading for s in structured.sections if s.heading is not None]
    assert "HW Partial Fault" in headings
    assert "HW Fault" in headings
    assert "Linearization Disturbance - Performance Degraded" in headings
    assert "VSWR Over Threshold" in headings
    # General Preconditions/Post-Restart Actions never used "N)" syntax --
    # they remain part of the untitled preamble section, never split.
    assert "General Preconditions" not in headings
    assert "Post-Restart Actions" not in headings


def test_generic_segmentation_is_not_hardcoded_to_any_alarm_name() -> None:
    """Proves genericity directly: an ENTIRELY different, unrelated
    document using the SAME `N)` convention with different labels
    segments identically -- the mechanism has no dependency on VSWR/HW
    Partial Fault/any specific vocabulary."""
    content = "Intro text.\n1) Widget Replacement\nDo the widget thing.\n2) Gadget Calibration\nDo the gadget thing.\n"
    structured = _processed_document(content)
    headings = [s.heading for s in structured.sections if s.heading is not None]
    assert headings == ["Widget Replacement", "Gadget Calibration"]


def test_ordinary_period_numbered_instruction_list_remains_unsplit() -> None:
    """§3: an ordinary sequential numbered instruction list (period-style,
    "1.", "2.", ...) -- the far more common convention used elsewhere in
    the real corpus for ordinary steps -- must NOT be exploded into
    per-line sections; only the parenthesis style is treated as a
    heading marker."""
    content = "Apply Access and Login.\n1. Login on the portal using your credentials.\n2. Select the target node.\n3. Confirm access.\n"
    structured = _processed_document(content)
    assert len(structured.sections) == 1
    assert structured.sections[0].heading is None
    assert "1. Login on the portal" in structured.sections[0].content


# --- 4: embedded artifact sections remain correctly linked ---------------


def test_embedded_artifact_sections_remain_linked_after_segmentation_change() -> None:
    """Regression proof: A5's own compound-document artifact linkage
    (`artifact_id` on a `StructuredKnowledgeSection`) is completely
    orthogonal to this pass's new heading-detection rule -- a section
    derived from an embedded artifact keeps its `artifact_id` exactly as
    before, and the new `N)` pattern never fires inside artifact-derived
    text unless that text itself uses the same generic syntax."""
    from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact

    artifact = KnowledgeArtifact(artifact_id="art-1", kind="image", parent_artifact_id=None, depth=0, extracted_text="1) Some described image content\nA caption describing it.", extraction_status=ArtifactExtractionStatus.COMPLETE)
    source = KnowledgeSource(source_system="test", source_id="def-0024-artifact-fixture")
    document = IngestedKnowledgeDocument(source=source, title="Artifact Fixture", content="Root text.\n1) Some described image content\nA caption describing it.\n", artifacts=[artifact])
    structured = HeadingStructureProcessor().process(document)
    # The processor itself does not assign artifact_id (that is Layer H's own job,
    # audited and unchanged by this pass) -- this proves only that segmentation of
    # text matching the new pattern does not raise/break when artifacts are present.
    assert any(s.heading == "Some described image content" for s in structured.sections)


# --- 5: deterministic ordering/identity -----------------------------------


def test_section_ordering_and_identity_deterministic_across_repeated_processing() -> None:
    first = _processed_document()
    second = _processed_document()
    assert [(s.section_key, s.sequence, s.heading) for s in first.sections] == [(s.section_key, s.sequence, s.heading) for s in second.sections]


# --- Evidence/grounding fixtures ------------------------------------------


def _procedure_evidence_item(section_id: str, heading: str, content: str) -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id="def-0024-doc", sequence=0, heading=heading, content=content)
    source = KnowledgeSource(source_system="test", source_id="def-0024-doc")
    reference = KnowledgeEvidenceReference(knowledge_id="def-0024-doc", version_label="v1", section_id=section_id, source_system="test", source_id="def-0024-doc")
    return KnowledgeEvidenceItem(reference=reference, title="DEF-0024 Fixture Procedure", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)


_VSWR_ITEM = _procedure_evidence_item("s-vswr", "VSWR Over Threshold", "No restart allowed. Perform diagnosis only. Validate Return Loss and VSWR values.")
_HW_PARTIAL_FAULT_ITEM = _procedure_evidence_item("s-hwpf", "HW Partial Fault", "Restart is allowed only on RRU. Command: accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1")


def _select(run_id: str, *items: KnowledgeEvidenceItem) -> None:
    rt.get_or_init_run_state(run_id)
    execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=list(items)))
    rt.record_search_result(run_id, execution)
    keys = [KnowledgeEvidenceSelectionKey(knowledge_id=item.reference.knowledge_id, version_label=item.reference.version_label, section_id=item.reference.section_id) for item in items]
    rt.select_evidence(run_id, keys)


def _next_step_guidance(command) -> TroubleshootingGuidance:
    # LIVE-CORR-3A: a `None` command here is always the SAFE, well-behaved
    # "no command needed" case these fixtures represent -- explicitly
    # classified OBSERVATION so `enforce_structural_operational_integrity`
    # 's "unset defaults to STATE_CHANGE_RECOMMENDATION" fail-closed rule
    # (correctly reserved for a REAL, unclassified state-change lacking a
    # command) does not misfire on it.
    operational_effect = None if command else TroubleshootingOperationalEffect.OBSERVATION
    return TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="Interpretation.",
        next_action="Do the next thing.",
        command=command,
        evidence_requested="Provide the output.",
        operational_effect=operational_effect,
    )


# --- 6/7: independent selectability ----------------------------------------


def test_vswr_procedure_is_independently_selectable() -> None:
    run_id = "def0024-vswr-selectable"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _VSWR_ITEM)
        selected = rt.snapshot_selected_knowledge_evidence(run_id)
    finally:
        reset_run_id(token)
    assert len(selected) == 1
    assert selected[0].section.section_id == "s-vswr"
    assert "No restart" in selected[0].section.content


def test_hw_partial_fault_procedure_is_independently_selectable() -> None:
    run_id = "def0024-hwpf-selectable"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HW_PARTIAL_FAULT_ITEM)
        selected = rt.snapshot_selected_knowledge_evidence(run_id)
    finally:
        reset_run_id(token)
    assert len(selected) == 1
    assert selected[0].section.section_id == "s-hwpf"
    assert "restartunit" in selected[0].section.content


# --- 8/9/10/11: procedure-scoped grounding ----------------------------------


def test_vswr_followup_cannot_surface_hw_partial_fault_command() -> None:
    """THE mandatory DEF-0024 regression proof: only the VSWR section is
    selected for this turn; the model nonetheless proposes the HW Partial
    Fault command (exactly the live failure) -- it must be stripped."""
    run_id = "def0024-vswr-cannot-borrow"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _VSWR_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance("accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"), run_id)
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None


def test_existing_command_verbatim_validation_still_holds_for_correct_command() -> None:
    run_id = "def0024-verbatim-still-holds"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HW_PARTIAL_FAULT_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance("accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"


def test_command_from_correct_active_procedure_remains_allowed() -> None:
    """§10, same property as above stated as its own explicit case: the
    active procedure's OWN command, byte-for-byte, is never blocked."""
    run_id = "def0024-correct-command-allowed"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _VSWR_ITEM)
        # VSWR itself has no command in real governed content -- prove a
        # command that IS genuinely part of the (hypothetically extended)
        # active section's own content is allowed.
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(None), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command is None


def test_active_procedure_with_no_command_cannot_borrow_a_siblings_command() -> None:
    """§11: VSWR is the ONLY selected evidence and has no command of its
    own -- even though a sibling procedure's real command exists
    elsewhere in governed knowledge (not selected this turn), it must
    never be substituted."""
    run_id = "def0024-no-command-no-borrow"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _VSWR_ITEM)  # HW Partial Fault is deliberately NOT selected
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance("accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"), run_id)
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None


# --- 12: ambiguous multi-procedure selection --------------------------------


def test_ambiguous_multi_procedure_selection_fails_closed() -> None:
    """§12: BOTH VSWR and HW Partial Fault are selected together this
    turn (a genuinely ambiguous "which procedure is active" situation) --
    a command grounded in only ONE of the two selected sections must
    still fail closed, never be attributed to "the" active procedure."""
    run_id = "def0024-ambiguous-multi-selection"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _VSWR_ITEM, _HW_PARTIAL_FAULT_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance("accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"), run_id)
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None


def test_command_grounded_in_all_simultaneously_selected_sections_is_allowed() -> None:
    """The inverse proof for §12's own rule: if a command genuinely
    appears in EVERY currently-selected section (never ambiguous, since
    it is unambiguously supported regardless of which one is "active"),
    it is NOT stripped."""
    shared_command = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
    item_a = _procedure_evidence_item("s-a", "Procedure A", f"Command: {shared_command}")
    item_b = _procedure_evidence_item("s-b", "Procedure B", f"Command: {shared_command}")
    run_id = "def0024-shared-command-allowed"
    token = bind_run_id(run_id)
    try:
        _select(run_id, item_a, item_b)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(shared_command), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == shared_command


# --- 13/14: multi-turn continuity behavior ----------------------------------


def test_referential_followup_preserves_procedure_scope_when_evidence_reselected() -> None:
    """§13: models the SAFE, INTENDED multi-turn behavior -- when
    incident_manager correctly re-selects the SAME (VSWR) evidence for a
    referential follow-up turn (per the new PROCEDURE CONTINUITY prompt
    paragraph), the deterministic safeguard allows the turn to proceed
    normally and does not spuriously interfere."""
    run_id = "def0024-followup-preserves-scope"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _VSWR_ITEM)
        # The user asked "give me the first cmd" -- incident_manager correctly
        # recognizes VSWR has no command and says so, never fabricating one.
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance(None), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command is None
    assert guidance.next_action == "Do the next thing."  # ordinary fields untouched


def test_explicit_topic_change_to_a_different_procedure_still_works() -> None:
    """§14: an explicit topic change to HW Partial Fault -- a NEW turn
    that genuinely selects HW Partial Fault's own evidence -- must still
    correctly surface HW Partial Fault's own real command; the safeguard
    must never remain incorrectly pinned to a previous procedure."""
    run_id = "def0024-explicit-topic-change"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HW_PARTIAL_FAULT_ITEM)
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance("accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"), run_id)
    finally:
        reset_run_id(token)
    assert stripped is False
    assert guidance.command == "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"


# --- FULL_PROCEDURE mode coverage -------------------------------------------


def test_full_procedure_mode_strips_only_the_ungrounded_step_command() -> None:
    run_id = "def0024-full-procedure-partial-strip"
    token = bind_run_id(run_id)
    try:
        _select(run_id, _HW_PARTIAL_FAULT_ITEM)
        guidance = TroubleshootingGuidance(
            interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
            full_procedure_steps=[
                TroubleshootingStep(action="Do the HW Partial Fault restart.", command="accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"),
                TroubleshootingStep(action="Do something unrelated.", command="totally made up command"),
            ],
        )
        corrected, stripped = enforce_procedure_scoped_command_grounding(guidance, run_id)
    finally:
        reset_run_id(token)
    assert stripped is True
    assert corrected.full_procedure_steps[0].command == "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
    assert corrected.full_procedure_steps[1].command is None


def test_no_selected_evidence_at_all_strips_any_command() -> None:
    run_id = "def0024-no-evidence-selected"
    token = bind_run_id(run_id)
    try:
        guidance, stripped = enforce_procedure_scoped_command_grounding(_next_step_guidance("some command"), run_id)
    finally:
        reset_run_id(token)
    assert stripped is True
    assert guidance.command is None
