"""DEF-0041 corrective-milestone isolation pass.

DEF-0041 (docs/DEFECT_REGISTER.md) recorded a live discrepancy: a
structured `TroubleshootingGuidance.full_procedure_steps[].command` value
that the PURE grounding functions (`_evaluate_command`/`resolve_active_
section_id`) correctly reject when called directly
(`test_livecorr1_diagnostics.py::test_def_0041_hget_command_should_fail_
grounding_against_real_content`, still passing, unchanged) nonetheless
reached the user, unstripped, in the real live runtime. The register's
own "confirmed root cause: UNRESOLVED" note named three unconfirmed
candidates: (1) a `run_id`/`current_run_id()` correlation timing gap
causing `snapshot_selected_knowledge_evidence(run_id)` to return
different data than the turn's own real selection; (2) `enforce_
procedure_scoped_command_grounding_with_reason` not being invoked at all
for this response; (3) another, unidentified mechanism.

WHAT THIS FILE ADDS, AND WHY IT WAS MISSING: every PRE-EXISTING test that
exercises the real `enforce_incident_manager_response_integrity`
`after_agent_callback` wiring (`test_evidence_troubleshooting_guidance.py`)
uses a `_FakeCallbackContext` that never sets `user_content` at all --
`_extract_incoming_question_text` therefore always resolves to `None` in
every one of those tests, silently exercising only the "no active section
could be resolved" fallback path (DEF-0024's original "grounded in every
selected section" rule) rather than DEF-0027's own active-section
heading-resolution path, which is what DEF-0041's own live scenario (a
question containing the active section's own heading, two selected
sections from the SAME governed document) actually depends on. This file
closes that specific test-coverage gap by driving the REAL
`enforce_incident_manager_response_integrity` callback -- not the
isolated pure functions -- with a `user_content` and run-scoped selected-
evidence state that faithfully reproduce DEF-0041's own documented shape.

RESULT (see this milestone's own closure report for the full trace):
every deterministic reconstruction attempted -- the exact documented
shape, a missing/absent `user_content` (question=None), a `user_content`
whose question text does not contain the active heading verbatim, and a
simulated total `run_id` correlation gap (no evidence ever selected under
the bound run_id at all) -- resolves the command to withheld (fail
closed), never to the unstripped, reached-the-user outcome DEF-0041
described. No discrepancy was found in this pipeline layer; DEF-0041
remains OPEN pending a live re-test with the existing `troubleshooting_
command_grounding` instrumentation (LIVE-CORR-3) -- see the defect
register for the full evidence and the honest "could not reproduce"
conclusion this pass reached. No production code in this file's own
scope was changed.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from backend.agents.incident_manager.evidence import enforce_incident_manager_response_integrity
from backend.api.troubleshooting_guidance_context import pop_troubleshooting_guidance
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import (
    KnowledgeEvidenceItem,
    KnowledgeEvidenceReference,
    KnowledgeEvidenceSelectionKey,
    KnowledgeEvidenceSet,
)
from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
from backend.tools.knowledge import runtime as rt

_HGET_COMMAND = "hget near Rfportref"


def _item(section_id: str, heading: str, content: str, knowledge_id: str) -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, content=content, heading=heading)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(knowledge_id=knowledge_id, version_label="v2", section_id=section_id, source_system="test", source_id=f"{knowledge_id}-doc")
    return KnowledgeEvidenceItem(reference=reference, title="A5-VALIDATION-DOCUMENT1", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)


def _select_two_sibling_sections(run_id: str) -> None:
    """DEF-0041's own documented shape: two sections of the SAME governed
    document (`knowledge_id="A5-VALIDATION-DOCUMENT1"`) -- an active "HW
    Partial Fault" section that does NOT contain `_HGET_COMMAND`, and a
    merely SUPPORTING sibling "HW Fault" section whose real content DOES.
    """
    active = _item(
        "A5-VALIDATION-DOCUMENT1:v2-def0024-fix:section-0001",
        "HW Partial Fault",
        "HW Partial Fault\nThis section covers partial hardware faults on the RRU.\nFor Antenna Group / Unit alarms, execute: alt\n",
        "A5-VALIDATION-DOCUMENT1",
    )
    supporting = _item(
        "A5-VALIDATION-DOCUMENT1:v2-def0024-fix:section-0002",
        "HW Fault",
        f"HW Fault\nThis section covers general hardware faults.\nFor Antenna Group / Unit alarms, execute the command to fetch the associated RRU: {_HGET_COMMAND}\n",
        "A5-VALIDATION-DOCUMENT1",
    )
    rt.get_or_init_run_state(run_id)
    execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=[active, supporting]))
    rt.record_search_result(run_id, execution)
    keys = [
        KnowledgeEvidenceSelectionKey(knowledge_id=i.reference.knowledge_id, version_label=i.reference.version_label, section_id=i.reference.section_id)
        for i in (active, supporting)
    ]
    rt.select_evidence(run_id, keys)


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.parts = parts


class _FakeEvent:
    def __init__(self, author: str, text: Optional[str] = None) -> None:
        self.author = author
        self.content = _FakeContent([_FakePart(text)]) if text is not None else None


class _FakeSession:
    def __init__(self, events: list[_FakeEvent]) -> None:
        self.events = events


class _FakeCallbackContext:
    """Unlike `test_evidence_troubleshooting_guidance.py`'s own fixture,
    this ALSO sets `user_content` -- the field `_extract_incoming_
    question_text` actually reads in the real live runtime -- so tests
    here exercise DEF-0027's active-section heading-resolution path, not
    only the `question=None` fallback every pre-existing test silently
    exercised instead.
    """

    def __init__(self, events: list[_FakeEvent], user_content_text: Optional[str], state: Optional[dict[str, Any]] = None) -> None:
        self.session = _FakeSession(events)
        self.state = dict(state or {})
        self.user_content = _FakeContent([_FakePart(user_content_text)]) if user_content_text is not None else None


def _full_procedure_response_with_ungrounded_step() -> str:
    """DEF-0041's own documented shape: a 6-step FULL_PROCEDURE response
    (a compact stand-in for the real 15-step one) whose LAST step's
    command is real, Approved content -- but drawn from the SUPPORTING
    sibling section, not the active one. Every step but the last is
    classified `operational_effect` as non-state-changing so LIVE-CORR-3A's
    unrelated whole-guidance structural-integrity rule (a STATE_CHANGE_
    RECOMMENDATION step with no command) does not itself suppress the
    guidance before DEF-0027's own per-step grounding check ever runs --
    this file isolates the GROUNDING mechanism specifically, the one
    DEF-0041 concerns.
    """
    return json.dumps(
        {
            "outcome": "ok",
            "summary": "placeholder -- must be overwritten by the deterministic renderer",
            "evidence": [],
            "candidate_titles": [],
            "detail": None,
            "troubleshooting_guidance": {
                "interaction_mode": "full_procedure",
                "interpretation": "This is a HW Partial Fault.",
                "full_procedure_steps": [
                    {"action": "Check the alarm log.", "command": None, "operational_effect": "diagnostic_read"},
                    {"action": "Review the antenna group.", "command": None, "operational_effect": "observation"},
                    {"action": "Confirm the fault type.", "command": None, "operational_effect": "observation"},
                    {"action": "Check the RRU status.", "command": None, "operational_effect": "diagnostic_read"},
                    {"action": "Escalate if unresolved.", "command": None, "operational_effect": "reference_description"},
                    {
                        "action": "For Antenna Group / Unit alarms, execute the command to fetch the associated RRU.",
                        "command": _HGET_COMMAND,
                        "operational_effect": "state_change_recommendation",
                    },
                ],
            },
        }
    )


_ACTIVE_HEADING_QUESTION = json.dumps(
    {"chat_topic": "HW Partial Fault", "question": "How do I troubleshoot HW Partial Fault using the approved procedure?"}
)


async def _run_callback(run_id: str, user_content_text: Optional[str]) -> tuple[Optional[Any], Optional[Any]]:
    token = bind_run_id(run_id)
    try:
        _select_two_sibling_sections(run_id)
        ctx = _FakeCallbackContext(events=[_FakeEvent("incident_manager", _full_procedure_response_with_ungrounded_step())], user_content_text=user_content_text)
        result = await enforce_incident_manager_response_integrity(ctx)
    finally:
        captured = pop_troubleshooting_guidance(run_id)
        reset_run_id(token)
    return result, captured


@pytest.mark.asyncio
async def test_def_0041_cross_section_command_stripped_through_real_callback_with_active_heading_question() -> None:
    """The exact DEF-0041 shape, through the REAL `after_agent_callback`
    (not the isolated pure functions): two same-document sections
    selected, question text containing the active section's own heading
    verbatim, FULL_PROCEDURE guidance whose last step's command is real
    content of the SUPPORTING (non-active) sibling section only."""
    result, captured = await _run_callback("def0041-active-heading", _ACTIVE_HEADING_QUESTION)

    assert result is not None
    corrected = json.loads(result.parts[0].text)
    assert _HGET_COMMAND not in corrected["summary"]

    assert captured is not None
    assert captured.full_procedure_steps[-1].command is None


@pytest.mark.asyncio
async def test_def_0041_cross_section_command_stripped_when_question_absent() -> None:
    """No `user_content` at all (question=None) -- falls back to DEF-0024's
    original "grounded in every selected section" rule; still withheld."""
    result, captured = await _run_callback("def0041-no-question", None)

    assert result is not None
    corrected = json.loads(result.parts[0].text)
    assert _HGET_COMMAND not in corrected["summary"]
    assert captured.full_procedure_steps[-1].command is None


@pytest.mark.asyncio
async def test_def_0041_cross_section_command_stripped_when_heading_not_matched() -> None:
    """`user_content` present, but the question text does not contain
    either candidate section's own heading verbatim -- active-section
    resolution is unresolved; still withheld via the same fail-closed
    fallback."""
    result, captured = await _run_callback("def0041-no-heading-match", json.dumps({"question": "please continue with the fault"}))

    assert result is not None
    corrected = json.loads(result.parts[0].text)
    assert _HGET_COMMAND not in corrected["summary"]
    assert captured.full_procedure_steps[-1].command is None


@pytest.mark.asyncio
async def test_def_0041_cross_section_command_stripped_when_run_scoped_evidence_missing() -> None:
    """Simulates candidate root cause (1) from the defect register -- a
    total `run_id` correlation gap, i.e. NO evidence was ever selected
    under the run_id the callback actually reads. Still fails closed
    (`TRUE_ABSENCE`, via `_evaluate_command`'s own empty-evidence branch),
    never fails open."""
    run_id = "def0041-no-evidence-selected"
    token = bind_run_id(run_id)
    try:
        # Deliberately skip `_select_two_sibling_sections` -- no knowledge_
        # search/selection happened under this run_id at all.
        ctx = _FakeCallbackContext(events=[_FakeEvent("incident_manager", _full_procedure_response_with_ungrounded_step())], user_content_text=_ACTIVE_HEADING_QUESTION)
        result = await enforce_incident_manager_response_integrity(ctx)
    finally:
        captured = pop_troubleshooting_guidance(run_id)
        reset_run_id(token)

    assert result is not None
    corrected = json.loads(result.parts[0].text)
    assert _HGET_COMMAND not in corrected["summary"]
    assert captured.full_procedure_steps[-1].command is None
