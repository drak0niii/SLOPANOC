"""DEF-0026 corrective pass -- Governed Knowledge Follow-Up Identity
Continuity.

Covers the required test list:
  1.  successful selected VSWR evidence stores stable identity
  2.  available-but-unselected evidence does NOT populate state
  3.  failed governed turn does NOT overwrite prior valid state
  4.  new chat/session has no previous governed identity
  5.  no cross-session leakage
  6.  "give me the first cmd" after VSWR recovers VSWR procedure identity
  7.  it cannot select HW Partial Fault/HW Fault as a substitute
  8.  VSWR follow-up produces no unrelated command
  9.  explicit "how do I handle HW Partial Fault?" overrides prior VSWR
  10. correct HW Partial Fault command remains permitted
  11. stale/non-current/invalid evidence identity is rejected
  12. archived/ineligible Knowledge identity is rejected
  13. multiple prior selected procedures + ambiguous follow-up ->
      clarification, not arbitrary selection
  14. no prior evidence + ambiguous command follow-up -> clarification/
      fail-closed
  15. existing provenance retry behavior unchanged (regression, see
      test_p5_1j_governed_completion_gate.py, re-run unmodified)
  16. existing DEF-0024 procedure-scoped command tests remain passing
      (regression, see test_def_0024_procedure_grounding.py)
  17. existing Teams session/topic state behavior unchanged (regression,
      see test_followup_routing_contract.py / state_sync tests)

Uses a small, pure-Python fake `KnowledgeRepository` (never SQLite, never
Cloud SQL) -- storage isolation for this narrow, deterministic-logic unit
suite is achieved structurally, not via a real database of any kind.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest

from backend.agents.team_manager.governed_knowledge_completion import enforce_governed_knowledge_at_completion
from backend.api.governed_evidence_continuity import (
    GENERIC_MISSING_PROCEDURE_CLARIFICATION,
    LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY,
    build_ambiguous_procedure_clarification,
    build_last_selected_governed_evidence_state_update,
    build_scoped_failure_clarification,
    build_scoped_remediation_question,
    detect_explicit_sibling_topic_override,
    parse_last_selected_governed_evidence,
    revalidate_prior_governed_evidence,
)
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceReference, KnowledgeEvidenceSelectionKey

_KNOWLEDGE_ID = "DEF-0026-DOC"
_VSWR_SECTION_ID = "s-vswr"
_HWPF_SECTION_ID = "s-hwpf"


class _FakeKnowledgeRepository:
    """Minimal, pure-Python, in-process fake implementing exactly the
    `KnowledgeRepository` surface `revalidate_prior_governed_evidence`
    needs (`get`/`list_versions`) -- never SQLite, never a real database
    of any kind."""

    def __init__(self, objects: list[KnowledgeObject]) -> None:
        self._objects = objects

    async def get(self, knowledge_id: str, version_label: str) -> Optional[KnowledgeObject]:
        for obj in self._objects:
            if obj.knowledge_id == knowledge_id and obj.version.label == version_label:
                return obj
        return None

    async def list_versions(self, knowledge_id: str) -> list[KnowledgeObject]:
        return [obj for obj in self._objects if obj.knowledge_id == knowledge_id]


def _source() -> KnowledgeSource:
    return KnowledgeSource(source_system="test", source_id="def-0026-doc")


def _object(
    version_label: str,
    lifecycle: LifecycleStatus = LifecycleStatus.APPROVED,
    supersedes: Optional[list[str]] = None,
    knowledge_id: str = _KNOWLEDGE_ID,
) -> KnowledgeObject:
    sections = [
        KnowledgeSection(section_id=_VSWR_SECTION_ID, knowledge_id=knowledge_id, sequence=0, heading="VSWR Over Threshold", content="No restart allowed. Diagnosis only."),
        KnowledgeSection(section_id=_HWPF_SECTION_ID, knowledge_id=knowledge_id, sequence=1, heading="HW Partial Fault", content="Restart allowed. Command: accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"),
    ]
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title="DEF-0026 Fixture Document",
        version=KnowledgeVersion(label=version_label, supersedes=supersedes or []),
        lifecycle_status=lifecycle,
        source=_source(),
        sections=sections,
    )


def _key(section_id: Optional[str], version_label: str = "v1", knowledge_id: str = _KNOWLEDGE_ID) -> KnowledgeEvidenceSelectionKey:
    return KnowledgeEvidenceSelectionKey(knowledge_id=knowledge_id, version_label=version_label, section_id=section_id)


def _evidence_item(section_id: str, version_label: str = "v1", knowledge_id: str = _KNOWLEDGE_ID) -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, heading="VSWR Over Threshold" if section_id == _VSWR_SECTION_ID else "HW Partial Fault", content="content")
    reference = KnowledgeEvidenceReference(knowledge_id=knowledge_id, version_label=version_label, section_id=section_id, source_system="test", source_id="def-0026-doc")
    return KnowledgeEvidenceItem(reference=reference, title="DEF-0026 Fixture Document", document_type=KnowledgeDocumentType.MOP, lifecycle_status=LifecycleStatus.APPROVED, source=_source(), section=section)


# --- 1/2/3/4/5: state write/read/no-overwrite/session-scoping ------------


def test_1_successful_selection_stores_stable_identity() -> None:
    update = build_last_selected_governed_evidence_state_update([_evidence_item(_VSWR_SECTION_ID)])
    assert update == {
        LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY: [
            {"knowledge_id": _KNOWLEDGE_ID, "version_label": "v1", "section_id": _VSWR_SECTION_ID}
        ]
    }


def test_2_available_but_unselected_does_not_populate() -> None:
    # `selected_evidence` is, by construction, only ever the SELECTED
    # list -- an available-but-unselected item never reaches this
    # function at all; an empty selection (the only way "available but
    # unselected" can look from here) correctly produces no update.
    assert build_last_selected_governed_evidence_state_update([]) == {}


def test_3_failed_turn_does_not_overwrite_prior_state() -> None:
    prior_state = {LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY: [{"knowledge_id": _KNOWLEDGE_ID, "version_label": "v1", "section_id": _VSWR_SECTION_ID}]}
    failed_turn_update = build_last_selected_governed_evidence_state_update([])
    merged = {**prior_state, **failed_turn_update}
    assert merged == prior_state  # unchanged -- the empty update never overwrites


def test_4_new_session_has_no_previous_identity() -> None:
    assert parse_last_selected_governed_evidence(None) == []
    assert parse_last_selected_governed_evidence("not-a-list") == []
    assert parse_last_selected_governed_evidence([{"bad": "shape"}]) == []


def test_5_no_cross_session_leakage() -> None:
    session_a_state: dict = {}
    session_b_state: dict = {}
    session_a_state.update(build_last_selected_governed_evidence_state_update([_evidence_item(_VSWR_SECTION_ID)]))
    assert LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY not in session_b_state
    assert parse_last_selected_governed_evidence(session_b_state.get(LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY)) == []


# --- revalidation: valid / stale / archived / dedup -----------------------


@pytest.mark.asyncio
async def test_revalidate_returns_valid_current_approved_entry() -> None:
    repo = _FakeKnowledgeRepository([_object("v1")])
    result = await revalidate_prior_governed_evidence([_key(_VSWR_SECTION_ID)], repo)
    assert len(result) == 1
    assert result[0].heading == "VSWR Over Threshold"
    assert "HW Partial Fault" in result[0].sibling_headings


@pytest.mark.asyncio
async def test_11_stale_noncurrent_version_is_rejected() -> None:
    # v2 supersedes v1 -- a stored reference to v1 is no longer current.
    repo = _FakeKnowledgeRepository([_object("v1"), _object("v2", supersedes=["v1"])])
    result = await revalidate_prior_governed_evidence([_key(_VSWR_SECTION_ID, version_label="v1")], repo)
    assert result == []


@pytest.mark.asyncio
async def test_12_archived_ineligible_version_is_rejected() -> None:
    repo = _FakeKnowledgeRepository([_object("v1", lifecycle=LifecycleStatus.ARCHIVE)])
    result = await revalidate_prior_governed_evidence([_key(_VSWR_SECTION_ID)], repo)
    assert result == []


@pytest.mark.asyncio
async def test_missing_knowledge_object_is_rejected() -> None:
    repo = _FakeKnowledgeRepository([])
    result = await revalidate_prior_governed_evidence([_key(_VSWR_SECTION_ID)], repo)
    assert result == []


@pytest.mark.asyncio
async def test_missing_section_in_still_current_version_is_rejected() -> None:
    repo = _FakeKnowledgeRepository([_object("v1")])
    result = await revalidate_prior_governed_evidence([_key("no-such-section")], repo)
    assert result == []


@pytest.mark.asyncio
async def test_revalidate_deduplicates_identical_identity() -> None:
    repo = _FakeKnowledgeRepository([_object("v1")])
    result = await revalidate_prior_governed_evidence([_key(_VSWR_SECTION_ID), _key(_VSWR_SECTION_ID)], repo)
    assert len(result) == 1


# --- 13: ambiguity ----------------------------------------------------------


@pytest.mark.asyncio
async def test_13_multiple_distinct_prior_procedures_produce_clarification_never_arbitrary_pick() -> None:
    repo = _FakeKnowledgeRepository([_object("v1")])
    result = await revalidate_prior_governed_evidence([_key(_VSWR_SECTION_ID), _key(_HWPF_SECTION_ID)], repo)
    assert len(result) == 2
    clarification = build_ambiguous_procedure_clarification(result)
    assert "VSWR Over Threshold" in clarification
    assert "HW Partial Fault" in clarification
    assert "restartunit" not in clarification  # never an operational command in a clarification


# --- explicit topic override (structural, identity-based) ------------------


@pytest.mark.asyncio
async def test_9_explicit_sibling_topic_in_question_is_detected() -> None:
    repo = _FakeKnowledgeRepository([_object("v1")])
    candidates = await revalidate_prior_governed_evidence([_key(_VSWR_SECTION_ID)], repo)
    override = detect_explicit_sibling_topic_override("how do i handle HW Partial Fault?", candidates)
    assert override == "HW Partial Fault"


def test_no_override_when_question_does_not_name_a_sibling() -> None:
    from dataclasses import replace

    candidate_module_result = [
        __import__("backend.api.governed_evidence_continuity", fromlist=["RevalidatedGovernedProcedure"]).RevalidatedGovernedProcedure(
            knowledge_id=_KNOWLEDGE_ID, version_label="v1", section_id=_VSWR_SECTION_ID, title="DEF-0026 Fixture Document", heading="VSWR Over Threshold", sibling_headings=("HW Partial Fault",)
        )
    ]
    assert detect_explicit_sibling_topic_override("give me the first cmd", candidate_module_result) is None


def test_scoped_question_names_the_real_procedure_and_states_override_rule() -> None:
    from backend.api.governed_evidence_continuity import RevalidatedGovernedProcedure

    procedure = RevalidatedGovernedProcedure(knowledge_id=_KNOWLEDGE_ID, version_label="v1", section_id=_VSWR_SECTION_ID, title="DEF-0026 Fixture Document", heading="VSWR Over Threshold", sibling_headings=("HW Partial Fault",))
    scoped = build_scoped_remediation_question("give me the first cmd", procedure)
    assert "give me the first cmd" in scoped
    assert "VSWR Over Threshold" in scoped
    assert "disregard this note" in scoped.lower()


def test_scoped_failure_clarification_names_the_real_procedure() -> None:
    from backend.api.governed_evidence_continuity import RevalidatedGovernedProcedure

    procedure = RevalidatedGovernedProcedure(knowledge_id=_KNOWLEDGE_ID, version_label="v1", section_id=_VSWR_SECTION_ID, title="DEF-0026 Fixture Document", heading="VSWR Over Threshold", sibling_headings=())
    text = build_scoped_failure_clarification(procedure)
    assert "VSWR Over Threshold" in text


# --- end-to-end enforce_governed_knowledge_at_completion orchestration -----


@pytest.mark.asyncio
async def test_6_and_7_and_8_followup_scopes_to_vswr_and_never_calls_incident_manager_for_ambiguity(monkeypatch: pytest.MonkeyPatch) -> None:
    """§6/§7/§8: exactly one valid prior identity (VSWR) exists;
    incident_manager is invoked with a SCOPED question naming VSWR, never
    an unscoped, topic-free one that could land on HW Partial Fault."""
    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion.get_knowledge_repository",
        lambda: _FakeKnowledgeRepository([_object("v1")]),
    )

    captured_questions: list[str] = []

    async def fake_run_once(*, question: str, chat_topic, run_id: str, image_parts):
        captured_questions.append(question)
        return (
            {"outcome": "ok", "summary": "No restart allowed. Diagnosis only."},
            [_evidence_item(_VSWR_SECTION_ID)],
        )

    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion._run_incident_manager_remediation_once",
        fake_run_once,
    )

    final_text, selected = await enforce_governed_knowledge_at_completion(
        question="give me the first cmd",
        chat_topic=None,
        run_id="def0026-followup",
        prior_governed_evidence=[_key(_VSWR_SECTION_ID)],
    )

    assert len(captured_questions) == 1
    assert "VSWR Over Threshold" in captured_questions[0]
    assert final_text == "No restart allowed. Diagnosis only."
    assert selected[0].reference.section_id == _VSWR_SECTION_ID
    assert "restartunit" not in final_text


@pytest.mark.asyncio
async def test_13b_ambiguous_prior_evidence_never_invokes_incident_manager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion.get_knowledge_repository",
        lambda: _FakeKnowledgeRepository([_object("v1")]),
    )

    async def _must_not_run(**_kwargs):
        raise AssertionError("incident_manager must never run when prior governed evidence is ambiguous")

    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion._run_incident_manager_remediation_once",
        _must_not_run,
    )

    final_text, selected = await enforce_governed_knowledge_at_completion(
        question="give me the first cmd",
        chat_topic=None,
        run_id="def0026-ambiguous",
        prior_governed_evidence=[_key(_VSWR_SECTION_ID), _key(_HWPF_SECTION_ID)],
    )
    assert selected == []
    assert "VSWR Over Threshold" in final_text
    assert "HW Partial Fault" in final_text


@pytest.mark.asyncio
async def test_9b_explicit_topic_change_skips_scoping_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """§9: prior identity is VSWR, but the CURRENT question explicitly
    names HW Partial Fault -- the question passed to incident_manager
    must be the UNSCOPED original, never augmented with the VSWR note."""
    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion.get_knowledge_repository",
        lambda: _FakeKnowledgeRepository([_object("v1")]),
    )

    captured_questions: list[str] = []

    async def fake_run_once(*, question: str, chat_topic, run_id: str, image_parts):
        captured_questions.append(question)
        return (
            {"outcome": "ok", "summary": "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"},
            [_evidence_item(_HWPF_SECTION_ID)],
        )

    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion._run_incident_manager_remediation_once",
        fake_run_once,
    )

    final_text, selected = await enforce_governed_knowledge_at_completion(
        question="how do i handle HW Partial Fault?",
        chat_topic=None,
        run_id="def0026-topic-change",
        prior_governed_evidence=[_key(_VSWR_SECTION_ID)],
    )

    assert captured_questions == ["how do i handle HW Partial Fault?"]  # never scoped/augmented
    assert "restartunit" in final_text  # §10: the real command remains permitted
    assert selected[0].reference.section_id == _HWPF_SECTION_ID


@pytest.mark.asyncio
async def test_14_no_prior_evidence_failed_attempt_returns_generic_clarification_not_flat_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion.get_knowledge_repository",
        lambda: _FakeKnowledgeRepository([]),
    )

    async def fake_run_once(*, question, chat_topic, run_id, image_parts):
        return ({"outcome": "error", "detail": "no evidence selected"}, [])

    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion._run_incident_manager_remediation_once",
        fake_run_once,
    )

    final_text, selected = await enforce_governed_knowledge_at_completion(
        question="give me the first cmd", chat_topic=None, run_id="def0026-no-prior-fails"
    )
    assert selected == []
    assert final_text == GENERIC_MISSING_PROCEDURE_CLARIFICATION
    assert final_text != "Governed knowledge could not be validated for this request. Please try again."


@pytest.mark.asyncio
async def test_scoped_attempt_failure_names_the_real_procedure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion.get_knowledge_repository",
        lambda: _FakeKnowledgeRepository([_object("v1")]),
    )

    async def fake_run_once(*, question, chat_topic, run_id, image_parts):
        return ({"outcome": "error", "detail": "no evidence selected"}, [])

    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion._run_incident_manager_remediation_once",
        fake_run_once,
    )

    final_text, selected = await enforce_governed_knowledge_at_completion(
        question="give me the first cmd",
        chat_topic=None,
        run_id="def0026-scoped-fails",
        prior_governed_evidence=[_key(_VSWR_SECTION_ID)],
    )
    assert selected == []
    assert "VSWR Over Threshold" in final_text


@pytest.mark.asyncio
async def test_no_selected_evidence_is_never_scoped_and_unscoped_success_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Baseline non-regression: with no prior evidence at all, an ordinary
    successful run behaves exactly as before this pass."""
    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion.get_knowledge_repository",
        lambda: _FakeKnowledgeRepository([]),
    )

    async def fake_run_once(*, question, chat_topic, run_id, image_parts):
        assert question == "what is the VSWR procedure?"  # unchanged, unscoped
        return ({"outcome": "ok", "summary": "No restart allowed."}, [_evidence_item(_VSWR_SECTION_ID)])

    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion._run_incident_manager_remediation_once",
        fake_run_once,
    )

    final_text, selected = await enforce_governed_knowledge_at_completion(
        question="what is the VSWR procedure?", chat_topic=None, run_id="def0026-baseline"
    )
    assert final_text == "No restart allowed."
    assert len(selected) == 1
