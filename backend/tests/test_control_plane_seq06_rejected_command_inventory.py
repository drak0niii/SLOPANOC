"""CONTROL-PLANE-SEQ-06 section 2 -- Residual Command-Inventory Gap
Closure.

Proves `rejected_command_context.py`'s own register/pop/discard lifecycle
in isolation, and that `enforce_procedure_scoped_command_grounding_with_
reason` (evidence.py, semantics otherwise completely unmodified) now
forwards the EXACT structured command value it already possesses at each
of its own three existing rejection sites (FULL_PROCEDURE per-step,
NEXT_STEP, and the wholesale cross-procedure-evidence wipe) -- never a new
grounding judgment, never text scanning.

No real Gemini call, no external network, no Cloud SQL requirement.
"""
from __future__ import annotations

from backend.agents.incident_manager.evidence import (
    CommandGroundingReason,
    enforce_procedure_scoped_command_grounding_with_reason,
)
from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingOperationalEffect,
    TroubleshootingStep,
)
from backend.api.rejected_command_context import (
    discard_rejected_commands,
    pop_rejected_commands,
    register_rejected_commands,
)
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceReference, KnowledgeEvidenceSelectionKey, KnowledgeEvidenceSet
from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
from backend.tools.knowledge import runtime as rt

_HGET_COMMAND = "hget near Rfportref"
_ALT_COMMAND = "alt"
_ACTIVE_HEADING_QUESTION = "How do I troubleshoot HW Partial Fault using the approved procedure?"


def _evidence_item(section_id: str, heading: str, content: str, knowledge_id: str = "seq06-doc") -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, heading=heading, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(
        knowledge_id=knowledge_id, version_label="v1", section_id=section_id, source_system="test", source_id=f"{knowledge_id}-doc"
    )
    return KnowledgeEvidenceItem(
        reference=reference, title="SEQ-06 Fixture Procedure", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section
    )


def _select(run_id: str, *items: KnowledgeEvidenceItem) -> None:
    rt.get_or_init_run_state(run_id)
    execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=list(items)))
    rt.record_search_result(run_id, execution)
    keys = [
        KnowledgeEvidenceSelectionKey(knowledge_id=i.reference.knowledge_id, version_label=i.reference.version_label, section_id=i.reference.section_id)
        for i in items
    ]
    rt.select_evidence(run_id, keys)


_ACTIVE_ITEM = _evidence_item(
    "s-active", "HW Partial Fault", f"HW Partial Fault\nFor Antenna Group / Unit alarms, execute: {_ALT_COMMAND}\n", knowledge_id="doc1"
)
_SIBLING_ITEM = _evidence_item(
    "s-sibling",
    "HW Fault",
    f"HW Fault\nFor Antenna Group / Unit alarms, execute the command to fetch the associated RRU: {_HGET_COMMAND}\n",
    knowledge_id="doc1",
)
_CROSS_DOC_ITEM = _evidence_item("s-cross", "Resource Timeout", "SSH to ENM/AMOS. Wait 5 minutes.", knowledge_id="doc2")


# =============================================================================
# rejected_command_context.py in isolation
# =============================================================================


def test_register_then_pop_round_trip() -> None:
    register_rejected_commands("run-a", [_HGET_COMMAND])
    assert pop_rejected_commands("run-a") == frozenset({_HGET_COMMAND})
    # consumed -- a second pop finds nothing
    assert pop_rejected_commands("run-a") == frozenset()


def test_register_is_additive_within_one_run_id() -> None:
    register_rejected_commands("run-b", [_HGET_COMMAND])
    register_rejected_commands("run-b", [_ALT_COMMAND])
    assert pop_rejected_commands("run-b") == frozenset({_HGET_COMMAND, _ALT_COMMAND})


def test_register_ignores_none_and_empty_values() -> None:
    register_rejected_commands("run-c", ["", None])  # type: ignore[list-item]
    assert pop_rejected_commands("run-c") == frozenset()


def test_pop_missing_run_id_returns_empty() -> None:
    assert pop_rejected_commands("never-registered") == frozenset()
    assert pop_rejected_commands(None) == frozenset()


def test_discard_is_safe_whether_or_not_an_entry_exists() -> None:
    discard_rejected_commands("run-d")  # never registered -- must not raise
    register_rejected_commands("run-d", [_HGET_COMMAND])
    discard_rejected_commands("run-d")
    assert pop_rejected_commands("run-d") == frozenset()


# =============================================================================
# Wired into enforce_procedure_scoped_command_grounding_with_reason
# =============================================================================


def test_full_procedure_rejected_step_command_is_exposed() -> None:
    run_id = "seq06-full-procedure"
    _select(run_id, _ACTIVE_ITEM, _SIBLING_ITEM)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[
            TroubleshootingStep(action="Check the alarm log.", command=None, operational_effect=TroubleshootingOperationalEffect.DIAGNOSTIC_READ),
            TroubleshootingStep(
                action="For Antenna Group / Unit alarms, execute the command to fetch the associated RRU.",
                command=_HGET_COMMAND,
                operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION,
            ),
        ],
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is True
    assert corrected.full_procedure_steps[1].command is None
    assert pop_rejected_commands(run_id) == frozenset({_HGET_COMMAND})


def test_next_step_rejected_command_is_exposed() -> None:
    run_id = "seq06-next-step"
    _select(run_id, _ACTIVE_ITEM, _SIBLING_ITEM)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        next_action="Fetch the associated RRU.",
        command=_HGET_COMMAND,
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is True
    assert corrected.command is None
    assert pop_rejected_commands(run_id) == frozenset({_HGET_COMMAND})


def test_cross_procedure_evidence_wipe_exposes_both_next_step_command_and_step_commands() -> None:
    """The wholesale `_guidance_scope_established` failure -- the ENTIRE
    guidance (including every step's own command) is discarded at once;
    every command value it carried must still be exposed."""
    run_id = "seq06-cross-procedure"
    _select(run_id, _ACTIVE_ITEM, _CROSS_DOC_ITEM)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[
            TroubleshootingStep(action="Restart the RRU.", command=_ALT_COMMAND, operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION),
            TroubleshootingStep(action="Fetch the RRU.", command=_HGET_COMMAND, operational_effect=TroubleshootingOperationalEffect.STATE_CHANGE_RECOMMENDATION),
        ],
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is True
    assert reason == CommandGroundingReason.CROSS_PROCEDURE_EVIDENCE
    assert corrected.full_procedure_steps == []
    assert pop_rejected_commands(run_id) == frozenset({_ALT_COMMAND, _HGET_COMMAND})


def test_legitimately_grounded_command_is_never_registered_as_rejected() -> None:
    """Positive control: a genuinely grounded command (present in the
    active section's own real content) must NOT appear in the rejected-
    command store -- this mechanism only ever forwards ACTUAL rejections,
    never every command a turn happened to see."""
    run_id = "seq06-accepted"
    _select(run_id, _ACTIVE_ITEM, _SIBLING_ITEM)
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        next_action="Restart the RRU.",
        command=_ALT_COMMAND,
    )
    corrected, stripped, reason = enforce_procedure_scoped_command_grounding_with_reason(guidance, run_id, _ACTIVE_HEADING_QUESTION)
    assert stripped is False
    assert corrected.command == _ALT_COMMAND
    assert pop_rejected_commands(run_id) == frozenset()
