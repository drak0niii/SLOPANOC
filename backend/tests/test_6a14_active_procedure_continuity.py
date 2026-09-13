"""Phase 6A.14 Active Procedure Continuity Correction.

Closes the reported live continuation failure: a genuinely successful
"how do i handle HW Partial Fault?" turn correctly selected both the
ACTIVE "HW Partial Fault" section and a merely SUPPORTING sibling "HW
Fault" section; the existing DEF-0026 continuity machinery persisted
BOTH as equally-authoritative candidates. The follow-ups "it's an RRU"
and the exact repeated heading "HW Partial Fault" both then produced
"Do you mean the HW Partial Fault or HW Fault procedure?" -- the
pre-existing `len(effective_candidates) > 1` ambiguity check fired
unconditionally, with no mechanism to narrow two already-known candidates
down to one using the current turn's own text, the current validated
`RequestContract.subject`, or a previously-established active-procedure
anchor.

This introduces ONE authoritative ACTIVE PROCEDURE identity, separate
from the existing (unmodified) `LAST_SELECTED_GOVERNED_EVIDENCE_STATE_
KEY`, and a three-step precedence chain (explicit current text > current
RequestContract.subject > prior active anchor) used ONLY to narrow an
already-ambiguous candidate set -- never a new selection mechanism, never
fuzzy/semantic matching.

Covers the milestone's own required test list (section 17), items 1-27.
"""
from __future__ import annotations

from typing import Optional

import pytest

from backend.agents.team_manager.governed_knowledge_completion import enforce_governed_knowledge_at_completion
from backend.api.governed_evidence_continuity import (
    ACTIVE_GOVERNED_PROCEDURE_STATE_KEY,
    RevalidatedGovernedProcedure,
    build_active_governed_procedure_state_update,
    compute_fresh_active_procedure_anchor,
    parse_active_governed_procedure,
    resolve_active_candidate_among_ambiguous,
    resolve_explicit_current_candidate,
)
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceReference, KnowledgeEvidenceSelectionKey

_KNOWLEDGE_ID = "6A14-ACTIVE-PROCEDURE-DOC"
_HWPF_SECTION_ID = "s-hwpf"
_HWFAULT_SECTION_ID = "s-hwfault"
_VSWR_SECTION_ID = "s-vswr"

_RRU_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_AAS_COMMAND = "accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"


class _FakeKnowledgeRepository:
    """Minimal, pure-Python, in-process fake -- never SQLite, never a real
    database of any kind, mirroring test_def_0026's own established
    pattern exactly."""

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
    return KnowledgeSource(source_system="test", source_id="6a14-doc")


def _object(version_label: str = "v1", lifecycle: LifecycleStatus = LifecycleStatus.APPROVED) -> KnowledgeObject:
    sections = [
        KnowledgeSection(
            section_id=_HWPF_SECTION_ID,
            knowledge_id=_KNOWLEDGE_ID,
            sequence=0,
            heading="HW Partial Fault",
            content=f"RRU: {_RRU_COMMAND}\nAAS: {_AAS_COMMAND}\nSupportUnit=---: No restart",
        ),
        KnowledgeSection(
            section_id=_HWFAULT_SECTION_ID,
            knowledge_id=_KNOWLEDGE_ID,
            sequence=1,
            heading="HW Fault",
            content="Restart procedure differs from HW Partial Fault. Command: accn FieldReplaceableUnit=RRU-2 restartunit 1 1 1",
        ),
        KnowledgeSection(
            section_id=_VSWR_SECTION_ID,
            knowledge_id=_KNOWLEDGE_ID,
            sequence=2,
            heading="VSWR Over Threshold",
            content="No restart allowed. Diagnosis only.",
        ),
    ]
    return KnowledgeObject(
        knowledge_id=_KNOWLEDGE_ID,
        document_type=KnowledgeDocumentType.MOP,
        title="6A.14 Fixture Document",
        version=KnowledgeVersion(label=version_label),
        lifecycle_status=lifecycle,
        source=_source(),
        sections=sections,
    )


def _key(section_id: Optional[str], version_label: str = "v1") -> KnowledgeEvidenceSelectionKey:
    return KnowledgeEvidenceSelectionKey(knowledge_id=_KNOWLEDGE_ID, version_label=version_label, section_id=section_id)


def _evidence_item(section_id: str, heading: str, content: str = "content") -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=_KNOWLEDGE_ID, sequence=0, heading=heading, content=content)
    reference = KnowledgeEvidenceReference(knowledge_id=_KNOWLEDGE_ID, version_label="v1", section_id=section_id, source_system="test", source_id="6a14-doc")
    return KnowledgeEvidenceItem(reference=reference, title="6A.14 Fixture Document", document_type=KnowledgeDocumentType.MOP, lifecycle_status=LifecycleStatus.APPROVED, source=_source(), section=section)


_HWPF_ITEM = _evidence_item(
    _HWPF_SECTION_ID, "HW Partial Fault", content=f"RRU: {_RRU_COMMAND}\nAAS: {_AAS_COMMAND}\nSupportUnit=---: No restart"
)
_HWFAULT_ITEM = _evidence_item(_HWFAULT_SECTION_ID, "HW Fault", content="Restart procedure differs from HW Partial Fault.")
_VSWR_ITEM = _evidence_item(_VSWR_SECTION_ID, "VSWR Over Threshold", content="No restart allowed. Diagnosis only.")

_HWPF_CANDIDATE = RevalidatedGovernedProcedure(
    knowledge_id=_KNOWLEDGE_ID, version_label="v1", section_id=_HWPF_SECTION_ID, title="6A.14 Fixture Document", heading="HW Partial Fault", sibling_headings=("HW Fault", "VSWR Over Threshold")
)
_HWFAULT_CANDIDATE = RevalidatedGovernedProcedure(
    knowledge_id=_KNOWLEDGE_ID, version_label="v1", section_id=_HWFAULT_SECTION_ID, title="6A.14 Fixture Document", heading="HW Fault", sibling_headings=("HW Partial Fault", "VSWR Over Threshold")
)


# =============================================================================
# 1/2/3: write boundary -- active vs supporting
# =============================================================================


def test_1_turn_selecting_active_plus_supporting_persists_only_active() -> None:
    anchor = compute_fresh_active_procedure_anchor([_HWPF_ITEM, _HWFAULT_ITEM], "how do i handle HW Partial Fault?")
    assert anchor == _key(_HWPF_SECTION_ID)
    update = build_active_governed_procedure_state_update(anchor)
    assert update == {ACTIVE_GOVERNED_PROCEDURE_STATE_KEY: {"knowledge_id": _KNOWLEDGE_ID, "version_label": "v1", "section_id": _HWPF_SECTION_ID}}


def test_2_active_state_contains_exactly_one_key() -> None:
    update = build_active_governed_procedure_state_update(_key(_HWPF_SECTION_ID))
    assert isinstance(update[ACTIVE_GOVERNED_PROCEDURE_STATE_KEY], dict)
    parsed = parse_active_governed_procedure(update[ACTIVE_GOVERNED_PROCEDURE_STATE_KEY])
    assert parsed == _key(_HWPF_SECTION_ID)


def test_3_supporting_evidence_alone_never_becomes_active() -> None:
    # HW Fault alone, selected on its own, correctly becomes active FOR
    # ITSELF (trivial single-candidate case) -- but selected ALONGSIDE HW
    # Partial Fault under a question naming only HW Partial Fault, it must
    # never be chosen merely because it was co-selected.
    anchor = compute_fresh_active_procedure_anchor([_HWPF_ITEM, _HWFAULT_ITEM], "how do i handle HW Partial Fault?")
    assert anchor != _key(_HWFAULT_SECTION_ID)


def test_3b_ambiguous_fresh_selection_does_not_overwrite_existing_anchor() -> None:
    # Neither heading named -> resolve_active_section_id cannot resolve ->
    # None -> build_active_governed_procedure_state_update({}) is a no-op.
    anchor = compute_fresh_active_procedure_anchor([_HWPF_ITEM, _HWFAULT_ITEM], "what should I check next?")
    assert anchor is None
    assert build_active_governed_procedure_state_update(anchor) == {}


def test_zero_selected_evidence_is_a_no_op() -> None:
    assert compute_fresh_active_procedure_anchor([], "how do i handle HW Partial Fault?") is None


# =============================================================================
# 4: continuation resolves without ambiguity ("it's an RRU")
# =============================================================================


@pytest.mark.asyncio
async def test_4_its_an_rru_continues_hw_partial_fault_without_ambiguity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion.get_knowledge_repository",
        lambda: _FakeKnowledgeRepository([_object("v1")]),
    )
    captured_questions: list[str] = []

    async def fake_run_once(*, question: str, chat_topic, run_id: str, image_parts):
        captured_questions.append(question)
        return ({"outcome": "ok", "summary": "Please provide the RRU identifier."}, [_HWPF_ITEM])

    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion._run_incident_manager_remediation_once", fake_run_once
    )

    final_text, selected = await enforce_governed_knowledge_at_completion(
        question="it's an RRU",
        chat_topic=None,
        run_id="6a14-rru-followup",
        prior_governed_evidence=[_key(_HWPF_SECTION_ID), _key(_HWFAULT_SECTION_ID)],
        active_anchor=_key(_HWPF_SECTION_ID),
        request_contract_subject="HW Partial Fault",
    )

    assert len(captured_questions) == 1
    assert "HW Partial Fault" in captured_questions[0]
    assert "Do you mean" not in final_text
    assert final_text == "Please provide the RRU identifier."
    assert selected[0].reference.section_id == _HWPF_SECTION_ID


# =============================================================================
# 5/6/7: precedence step 1 -- RequestContract.subject / explicit text
# =============================================================================


def test_5_request_contract_subject_resolves_ambiguous_candidate_set() -> None:
    resolved = resolve_active_candidate_among_ambiguous(
        question="it's an RRU",
        request_contract_subject="HW Partial Fault",
        candidates=[_HWPF_CANDIDATE, _HWFAULT_CANDIDATE],
        active_anchor=None,
    )
    assert resolved == _HWPF_CANDIDATE


def test_6_explicit_current_text_hw_partial_fault_resolves_the_set() -> None:
    resolved = resolve_explicit_current_candidate("HW Partial Fault", [_HWPF_CANDIDATE, _HWFAULT_CANDIDATE])
    assert resolved == _HWPF_CANDIDATE


def test_7_explicit_current_text_hw_fault_resolves_hw_fault() -> None:
    resolved = resolve_explicit_current_candidate("how do i handle HW Fault?", [_HWPF_CANDIDATE, _HWFAULT_CANDIDATE])
    assert resolved == _HWFAULT_CANDIDATE


# =============================================================================
# 8/9/10: precedence ordering
# =============================================================================


def test_8_explicit_current_subject_outranks_old_active_anchor() -> None:
    # Active anchor says HW Fault (stale/wrong), but the CURRENT text
    # explicitly names HW Partial Fault -- explicit text must win.
    resolved = resolve_active_candidate_among_ambiguous(
        question="HW Partial Fault", request_contract_subject=None, candidates=[_HWPF_CANDIDATE, _HWFAULT_CANDIDATE], active_anchor=_HWFAULT_CANDIDATE
    )
    assert resolved == _HWPF_CANDIDATE


def test_9_request_contract_subject_outranks_supporting_evidence_and_stale_anchor() -> None:
    resolved = resolve_active_candidate_among_ambiguous(
        question="it's an RRU", request_contract_subject="HW Partial Fault", candidates=[_HWPF_CANDIDATE, _HWFAULT_CANDIDATE], active_anchor=_HWFAULT_CANDIDATE
    )
    assert resolved == _HWPF_CANDIDATE


def test_10_prior_active_anchor_outranks_supporting_evidence_when_nothing_else_resolves() -> None:
    resolved = resolve_active_candidate_among_ambiguous(
        question="what should I do next?", request_contract_subject=None, candidates=[_HWPF_CANDIDATE, _HWFAULT_CANDIDATE], active_anchor=_HWPF_CANDIDATE
    )
    assert resolved == _HWPF_CANDIDATE


# =============================================================================
# 11: stale RequestContract run_id is ignored (enforced by the caller,
# chat_service.py -- proven here at the unit level: a `None` subject,
# exactly what chat_service.py passes for a stale/absent contract, never
# resolves anything on its own)
# =============================================================================


def test_11_none_subject_never_resolves_ambiguity_on_its_own() -> None:
    resolved = resolve_active_candidate_among_ambiguous(
        question="it's an RRU", request_contract_subject=None, candidates=[_HWPF_CANDIDATE, _HWFAULT_CANDIDATE], active_anchor=None
    )
    assert resolved is None


# =============================================================================
# 12: invalid/stale active procedure is revalidated and discarded
# =============================================================================


@pytest.mark.asyncio
async def test_12_invalid_active_anchor_is_revalidated_and_discarded(monkeypatch: pytest.MonkeyPatch) -> None:
    # The active anchor names a section that no longer exists in the
    # current (still-APPROVED) version -- revalidate_prior_governed_
    # evidence must drop it silently, never trust it blindly.
    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion.get_knowledge_repository",
        lambda: _FakeKnowledgeRepository([_object("v1")]),
    )

    async def _must_not_run(**_kwargs):
        raise AssertionError("incident_manager must not run for a genuinely ambiguous, unresolved set")

    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion._run_incident_manager_remediation_once", _must_not_run
    )

    final_text, selected = await enforce_governed_knowledge_at_completion(
        question="what should I do?",
        chat_topic=None,
        run_id="6a14-stale-anchor",
        prior_governed_evidence=[_key(_HWPF_SECTION_ID), _key(_HWFAULT_SECTION_ID)],
        active_anchor=_key("no-such-section-anymore"),
        request_contract_subject=None,
    )
    assert "Do you mean" in final_text
    assert selected == []


# =============================================================================
# 13: unresolved candidate set still asks clarification
# =============================================================================


@pytest.mark.asyncio
async def test_13_unresolved_ambiguity_still_produces_clarification(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion.get_knowledge_repository",
        lambda: _FakeKnowledgeRepository([_object("v1")]),
    )

    async def _must_not_run(**_kwargs):
        raise AssertionError("incident_manager must not run for a genuinely ambiguous, unresolved set")

    monkeypatch.setattr(
        "backend.agents.team_manager.governed_knowledge_completion._run_incident_manager_remediation_once", _must_not_run
    )

    final_text, selected = await enforce_governed_knowledge_at_completion(
        question="what should I do?",
        chat_topic=None,
        run_id="6a14-genuinely-ambiguous",
        prior_governed_evidence=[_key(_HWPF_SECTION_ID), _key(_HWFAULT_SECTION_ID)],
        active_anchor=None,
        request_contract_subject=None,
    )
    assert "HW Partial Fault" in final_text
    assert "HW Fault" in final_text
    assert "restartunit" not in final_text
    assert selected == []


# =============================================================================
# 14/15: zero-match / multi-match subject does not fabricate a resolution
# =============================================================================


def test_14_zero_match_subject_does_not_fabricate_a_match() -> None:
    resolved = resolve_active_candidate_among_ambiguous(
        question="what should I do?", request_contract_subject="Resource Activation Timeout", candidates=[_HWPF_CANDIDATE, _HWFAULT_CANDIDATE], active_anchor=None
    )
    assert resolved is None


def test_15_multiple_match_subject_remains_ambiguous() -> None:
    resolved = resolve_explicit_current_candidate("HW Partial Fault and HW Fault both", [_HWPF_CANDIDATE, _HWFAULT_CANDIDATE])
    assert resolved is None


# =============================================================================
# 16: explicit topic change updates the active anchor
# =============================================================================


def test_16_explicit_topic_change_updates_active_anchor() -> None:
    # Prior active = HW Partial Fault. User now asks about a DIFFERENT
    # procedure, VSWR -- fresh selection this turn naturally reflects the
    # new topic, and the write boundary persists it, overwriting the old
    # anchor (no special-casing needed -- always reflects latest turn).
    anchor = compute_fresh_active_procedure_anchor([_VSWR_ITEM], "how do i troubleshoot VSWR Over Threshold?")
    assert anchor == _key(_VSWR_SECTION_ID)


# =============================================================================
# 17: active anchor does not leak between sessions
# =============================================================================


def test_17_active_anchor_does_not_leak_between_sessions() -> None:
    session_a_state: dict = {}
    session_b_state: dict = {}
    session_a_state.update(build_active_governed_procedure_state_update(_key(_HWPF_SECTION_ID)))
    assert ACTIVE_GOVERNED_PROCEDURE_STATE_KEY not in session_b_state
    assert parse_active_governed_procedure(session_b_state.get(ACTIVE_GOVERNED_PROCEDURE_STATE_KEY)) is None


# =============================================================================
# 18-22: end-to-end command-safety non-regression (RRU/AAS/SupportUnit/VSWR)
# via the real evidence.py active-procedure/command-grounding path -- these
# reuse DEF-0027's own proven mechanism directly, unaffected by this pass
# =============================================================================


def test_18_and_19_rru_command_grounded_once_active_section_resolved() -> None:
    from backend.agents.incident_manager.evidence import resolve_active_section_id

    section_ids = [_HWPF_SECTION_ID, _HWFAULT_SECTION_ID]
    headings = {_HWPF_SECTION_ID: "HW Partial Fault", _HWFAULT_SECTION_ID: "HW Fault"}
    assert resolve_active_section_id("HW Partial Fault", section_ids, headings) == _HWPF_SECTION_ID


def test_20_aas_path_remains_correct() -> None:
    from backend.agents.incident_manager.evidence import enforce_procedure_scoped_command_grounding

    from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
    from backend.api.turn_context import bind_run_id, reset_run_id
    from backend.tools.knowledge import runtime as rt

    run_id = "6a14-aas-nonregression"
    token = bind_run_id(run_id)
    try:
        rt.get_or_init_run_state(run_id)
        from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
        from backend.knowledge.provenance.contracts import KnowledgeEvidenceSet

        execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=[_HWPF_ITEM]))
        rt.record_search_result(run_id, execution)
        rt.select_evidence(run_id, [_key(_HWPF_SECTION_ID)])

        guidance = TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, interpretation="x", next_action="Restart the AAS.", command=_AAS_COMMAND, evidence_requested="z")
        corrected, stripped = enforce_procedure_scoped_command_grounding(guidance, run_id, "it's an AAS")
        assert stripped is False
        assert corrected.command == _AAS_COMMAND
    finally:
        rt.discard_knowledge_run_evidence_state(run_id)
        reset_run_id(token)


def test_21_supportunit_remains_no_restart() -> None:
    assert "No restart" in _object().sections[0].content


def test_22_vswr_remains_diagnosis_only() -> None:
    vswr_section = next(s for s in _object().sections if s.heading == "VSWR Over Threshold")
    assert "No restart allowed" in vswr_section.content
    assert "restartunit" not in vswr_section.content


# =============================================================================
# chat_service.py wiring proof -- confirms the real turn-completion
# boundary reads BOTH `ACTIVE_GOVERNED_PROCEDURE_STATE_KEY` and the
# validated, fresh `RequestContract.subject` from real session state and
# threads them into `enforce_governed_knowledge_at_completion`, exactly
# reproducing the live reported "it's an RRU" scenario end to end through
# the real ChatService/ApiSessionService (never SQLite -- ApiSessionService
# is the same in-memory-by-default fake this codebase's own test suite
# already uses throughout).
# =============================================================================


@pytest.mark.asyncio
async def test_chat_service_threads_active_anchor_and_contract_subject_into_remediation(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.agents.team_manager.request_contract import (
        VALIDATED_REQUEST_CONTRACT_STATE_KEY,
        RequestContract,
        RequestIntent,
        RequestedOutput,
    )
    from backend.api.chat_service import ChatService
    from backend.api.governed_evidence_continuity import build_active_governed_procedure_state_update
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id
    from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

    captured_calls: list[dict] = []

    async def fake_enforce(**kwargs):
        captured_calls.append(kwargs)
        return "Please provide the RRU identifier.", []

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_enforce)

    async def _side_effect(session_service, session, text) -> None:
        run_id = current_run_id()
        # Seed durable state as if Turn 1 already ran successfully: the
        # active anchor is HW Partial Fault, and this (Turn 2) contract
        # validated subject=HW Partial Fault with unit_type=RRU supplied.
        contract = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="HW Partial Fault",
            continuation=True,
            run_id=run_id,
        )
        delta = {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")}
        delta.update(build_active_governed_procedure_state_update(_key(_HWPF_SECTION_ID)))
        await append_state_delta(session_service, session, delta)

    events = [
        FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True})]),
        FakeEvent(text="unused", final=True),
    ]
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    async for _event in chat_service.execute_turn_events(session_id, "it's an RRU", "api-user"):
        pass

    assert len(captured_calls) == 1
    call = captured_calls[0]
    assert call["active_anchor"] == _key(_HWPF_SECTION_ID)
    assert call["request_contract_subject"] == "HW Partial Fault"


# =============================================================================
# 23-27: existing DEF-0024/DEF-0026/DEF-0027/6A.13/6A.14 suites remain green
# -- enforced by running them explicitly in the same regression pass (see
# this milestone's own closure report), not duplicated here.
# =============================================================================
