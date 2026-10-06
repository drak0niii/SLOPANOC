"""Structured governed-evidence identity (no canonical-string parsing).

Live defect (session 715f13a4): the governed section `25:A5-...:section-0000` of version `v1` was
handed to the specialist as the canonical string `knowledge_id:v1:25:A5-...:section-0000` with the
instruction to re-select it; the model split the string and selected version `v1:25` + section
`A5-...:section-0000` -- a different (non-existent) identity.

Invariant under test: identity is the exact (knowledge_id, version_label, section_id) tuple. The
canonical string is display / logging only: it is never parsed back, never handed to the specialist
as something to re-select, and never decides authority. A malformed or stale identity is rejected.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from backend.agents.technical_authority_engineer import agent_tool as tae_agent_tool
from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.acquisition_continuity import (
    KnownGovernedAcquisition,
    known_governed_acquisition,
    reconstruct_known_action,
)
from backend.agents.technical_authority_engineer.evidence_acquisition import (
    SUPPORTING_EVIDENCE_IDENTITIES_KEY,
    SUPPORTING_EVIDENCE_KEY,
    supporting_identities,
)
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.cases.evidence_identity import EvidenceIdentity, identity_from_fields, identity_of, stored_identity
from backend.cases.evidence_model import AcquisitionCandidate, AcquisitionType
from backend.cases.troubleshooting_progression import BlockedGovernedAction, StepStatus, TroubleshootingProgression, TroubleshootingStep
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.knowledge.provenance.contracts import KnowledgeEvidenceSelectionKey
from backend.tests.test_applicability_blocked_governed_action import (
    _CONTENT,
    SEARCH,
    TURN1_TEXT,
    TURN2_TEXT,
    _Doc,
    _fc,
    _governed,
    _legacy_alt,
    _trace,
    conversation,  # noqa: F401 (fixture)
)
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)

# The live shape: a section id that itself contains ':' (and the knowledge id inside it).
LIVE = _Doc("A5-RU-MOP", "MOP_A5 Radio Unit.docx")
LIVE.section = "25:A5-RU-MOP:section-0000"
LIVE.canonical = f"{LIVE.kid}:{LIVE.ver}:{LIVE.section}"
LIVE_ID = EvidenceIdentity(knowledge_id=LIVE.kid, version_label=LIVE.ver, section_id=LIVE.section)
LIVE_ALT = LIVE.action_id("alt")
# What the model reconstructed from the canonical string: a DIFFERENT tuple with the same display string.
MISPARSED = EvidenceIdentity(knowledge_id=LIVE.kid, version_label="v1:25", section_id="A5-RU-MOP:section-0000")


def _evidence(identity: EvidenceIdentity, applicability: str = "match", content: str = _CONTENT) -> EvidenceReference:
    return EvidenceReference(
        source_id=identity.canonical, source_type="governed_knowledge", title="t", content_snippet=content,
        metadata={**identity.selection_key(), "lifecycle_status": "approved", "applicability_outcome": applicability, "document_type": "mop"},
    )


# =============================================================================================
# Identity value: structure, round trip, malformed / stale rejected
# =============================================================================================


@pytest.mark.parametrize(
    ("knowledge_id", "version_label", "section_id"),
    [
        ("A5-RU-MOP", "v1", "25:A5-RU-MOP:section-0000"),  # section containing ':'
        ("KB 1042", "Release 2024 Q3 (rev. B)", "sec-0"),  # version is ordinary text
        ("K", "v2", "a:b:c:d:e"),  # several ':' in the section
        ("K:ns", "v:1", "s"),  # ':' in the other components too
    ],
)
def test_identity_round_trips_exactly_and_is_never_parsed(knowledge_id: str, version_label: str, section_id: str) -> None:
    identity = EvidenceIdentity(knowledge_id=knowledge_id, version_label=version_label, section_id=section_id)
    assert identity.key == (knowledge_id, version_label, section_id)
    assert identity.canonical == f"{knowledge_id}:{version_label}:{section_id}"  # display only
    selection = KnowledgeEvidenceSelectionKey.model_validate(identity.selection_key())
    assert identity_of(selection) == identity and identity_of(_evidence(identity)) == identity
    assert identity_of(_evidence(identity).model_dump(mode="json")) == identity
    assert EvidenceIdentity.model_validate_json(identity.model_dump_json()) == identity
    assert stored_identity(json.loads(identity.model_dump_json())) == identity
    # The display string is never an identity: nothing is reconstructed from it.
    assert identity_of(identity.canonical) is None and stored_identity(identity.canonical) is None


def test_same_canonical_string_different_tuples_are_different_identities() -> None:
    assert LIVE_ID.canonical == MISPARSED.canonical
    assert LIVE_ID != MISPARSED and len({LIVE_ID, MISPARSED}) == 2
    assert pa._action_id(*LIVE_ID.key, "alt") != pa._action_id(*MISPARSED.key, "alt"), "ProcedureAction ids digest the tuple"


def test_malformed_identities_are_rejected_without_breaking_the_record() -> None:
    for bad in ("K:v1:s", {"knowledge_id": "K", "version_label": "v1"}, {"knowledge_id": "K", "version_label": "v1", "section_id": " "},
                {"knowledge_id": "K", "version_label": "v1", "section_id": "s", "content": "x"}, {"knowledge_id": None, "version_label": "v", "section_id": "s"}, 7):
        assert stored_identity(bad) is None, bad
    assert identity_from_fields("K", "", "s") is None and identity_from_fields("K", "v", None) is None
    with pytest.raises(ValueError):
        EvidenceIdentity(knowledge_id="K", version_label=" ", section_id="s")
    with pytest.raises(ValueError):
        EvidenceIdentity(knowledge_id="K", version_label="v", section_id="s", extra="x")  # type: ignore[call-arg]
    # A persisted record with a malformed identity loads; the identity is dropped (rejected).
    step = TroubleshootingStep.model_validate({
        "fault_id": "F", "objective": "o", "command_source_id": LIVE.canonical, "command_source_identity": LIVE.canonical,
        "selected_evidence": [LIVE.canonical, LIVE_ID.selection_key(), {"knowledge_id": "K"}],
    })
    assert step.command_source_identity is None and step.selected_evidence == [LIVE_ID]
    candidate = AcquisitionCandidate.model_validate({"requirement_id": "r", "acquisition_type": "governed_action", "source_identity": LIVE.canonical})
    assert candidate.source_identity is None


def test_progression_records_round_trip_structured_identity() -> None:
    step = TroubleshootingStep(
        fault_id="F", objective="o", command_source_id=LIVE.canonical, command_source_identity=LIVE_ID, selected_evidence=[LIVE_ID, MISPARSED],
        blocked_candidate=BlockedGovernedAction(procedure_action_id=LIVE_ALT, normalized_command="alt", source_id=LIVE.canonical,
                                                knowledge_id=LIVE.kid, version_label=LIVE.ver, section_id=LIVE.section, blocking_reason="applicability_unknown"),
    )
    loaded = TroubleshootingStep.model_validate(json.loads(step.model_dump_json()))
    assert loaded.command_source_identity == LIVE_ID and loaded.selected_evidence == [LIVE_ID, MISPARSED]
    assert loaded.blocked_candidate.identity == LIVE_ID
    candidate = AcquisitionCandidate(requirement_id="r", acquisition_type=AcquisitionType.GOVERNED_ACTION, source_identity=LIVE_ID)
    assert AcquisitionCandidate.model_validate(json.loads(candidate.model_dump_json())).source_identity == LIVE_ID
    # A legacy blocked record (no section) has no identity -- never derived from `source_id`.
    legacy = BlockedGovernedAction(procedure_action_id=LIVE_ALT, normalized_command="alt", source_id=LIVE.canonical,
                                   knowledge_id=LIVE.kid, version_label=LIVE.ver, blocking_reason="applicability_unknown")
    assert legacy.identity is None


# =============================================================================================
# Pending context / acquisition continuity: structured keys only
# =============================================================================================


def _pending_controller(identity: EvidenceIdentity) -> tuple[ProgressionController, TroubleshootingStep]:
    progression = TroubleshootingProgression()
    controller = ProgressionController({}, TroubleshootingState(fault_id="F-1", symptom_summary="ru"), progression=progression)
    blocked = pa.applicability_blocked_action("alt", identity.canonical, [_evidence(identity, "unknown")])
    result = {"outcome": "recommended", "diagnostic_step": {"action": "Check active alarms.", "reason": "r", "expected_evidence": "e",
                                                            "command": None, "command_source": None}}
    decision, out = controller.evaluate_proposal(result, None, [_evidence(identity, "unknown")], proposed_command="alt", blocked_action=blocked)
    step = controller.record(decision, out, "chk-1", selected_evidence_ids=[identity.canonical], applicability={identity.canonical: "unknown"},
                             blocked_candidate=blocked, selected_evidence=[identity])
    return controller, step


def test_pending_context_hands_over_exact_structured_selection_keys() -> None:
    controller, step = _pending_controller(LIVE_ID)
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION and step.blocked_candidate.identity == LIVE_ID
    context = controller.pending_context()
    assert context["selected_evidence"] == [{"knowledge_id": "A5-RU-MOP", "version_label": "v1", "section_id": "25:A5-RU-MOP:section-0000"}]
    assert context["governed_acquisition"]["selection_key"] == LIVE_ID.selection_key()
    assert "selected_evidence_ids" not in context
    assert LIVE.canonical not in json.dumps(context), "no canonical string is handed over to be re-selected"
    assert "selection keys in selected_evidence" in context["rule"]


def test_known_acquisition_is_located_by_exact_tuple_only() -> None:
    known = KnownGovernedAcquisition(step_id="s", procedure_action_id=LIVE_ALT, source_id=LIVE.canonical, normalized_template="alt",
                                     origin="blocked_action", blocked=True, identity=LIVE_ID)
    action, reason = reconstruct_known_action(known, [_evidence(LIVE_ID)])
    assert action is not None and identity_of(action.source) == LIVE_ID and reason == "reconstructed_from_current_selected_evidence"
    # Same display string, different tuple selected: never the known source.
    assert reconstruct_known_action(known, [_evidence(MISPARSED)]) == (None, "source_not_selected_in_this_run")
    # Stale: an earlier version of the same section is not the known source either.
    stale = KnownGovernedAcquisition(step_id="s", procedure_action_id=pa._action_id(LIVE.kid, "v0", LIVE.section, "alt"), source_id=None,
                                     normalized_template="alt", origin="blocked_action", identity=LIVE_ID.model_copy(update={"version_label": "v0"}))
    assert reconstruct_known_action(stale, [_evidence(LIVE_ID)]) == (None, "source_not_selected_in_this_run")


def test_blocked_step_continues_only_for_the_exact_tuple() -> None:
    controller, step = _pending_controller(LIVE_ID)
    blocked = step.blocked_candidate
    other = pa.build_procedure_action_catalog([_evidence(MISPARSED)])[0]
    alt_other = next(a for a in other if a.command_template == "alt")
    alt_live = next(a for a in pa.build_procedure_action_catalog([_evidence(LIVE_ID)])[0] if a.command_template == "alt")
    sections = {LIVE.canonical: (_CONTENT, "match")}
    assert controller._continues_blocked(blocked, alt_live, sections) is True
    assert controller._continues_blocked(blocked, alt_other, sections) is False, "same display string, different tuple"


def test_supporting_evidence_is_read_from_structured_identities_only() -> None:
    record = {SUPPORTING_EVIDENCE_KEY: [LIVE.canonical], SUPPORTING_EVIDENCE_IDENTITIES_KEY: [LIVE_ID.selection_key()]}
    assert supporting_identities(record) == {LIVE_ID.key}
    assert supporting_identities({SUPPORTING_EVIDENCE_KEY: [LIVE.canonical]}) is None, "display strings are never parsed"
    assert supporting_identities({SUPPORTING_EVIDENCE_IDENTITIES_KEY: [LIVE.canonical, {"knowledge_id": "K"}]}) == set()


# =============================================================================================
# Selection validation + trusted evidence: exact tuple, collisions fail closed
# =============================================================================================


def _km_item(identity: EvidenceIdentity) -> Any:
    return SimpleNamespace(
        reference=SimpleNamespace(**identity.selection_key()), title="t", lifecycle_status="approved", document_type="mop", artifact=None,
        section=SimpleNamespace(content=_CONTENT, heading=None, section_type=None, source_locator="l"), source=SimpleNamespace(source_id="f.docx"),
    )


def test_trusted_evidence_dedupes_by_tuple_and_withholds_display_collisions(monkeypatch: Any) -> None:
    selected: list[Any] = []
    monkeypatch.setattr(tae_agent_tool, "snapshot_selected_knowledge_evidence", lambda run_id: list(selected))
    monkeypatch.setattr(tae_agent_tool, "get_evidence_annotation", lambda *a, **k: None)
    selected[:] = [_km_item(LIVE_ID), _km_item(LIVE_ID)]
    (only,) = tae_agent_tool.build_server_validated_evidence("run-ident", None, [])
    assert identity_of(only) == LIVE_ID and only.source_id == LIVE.canonical
    # Two different tuples rendering the same display string: neither may ground or authorize anything.
    selected[:] = [_km_item(LIVE_ID), _km_item(MISPARSED)]
    assert tae_agent_tool.build_server_validated_evidence("run-ident", None, []) == []
    other = EvidenceIdentity(knowledge_id="K", version_label="v1", section_id="s:1")
    selected[:] = [_km_item(LIVE_ID), _km_item(MISPARSED), _km_item(other)]
    assert [identity_of(e) for e in tae_agent_tool.build_server_validated_evidence("run-ident", None, [])] == [other]


# =============================================================================================
# End to end: live shape (section id containing ':'), across runs
# =============================================================================================


@pytest.mark.asyncio
async def test_live_shape_identity_is_stable_across_runs_and_misparsed_key_is_rejected(conversation) -> None:  # noqa: F811
    repo, conv = conversation
    await repo.add(LIVE.knowledge())
    t1 = await conv.turn(TURN1_TEXT, [SEARCH, LIVE.select(), _legacy_alt(source=LIVE.filename)], "Please provide the technology and vendor.")
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.BLOCKED_BY_CLARIFICATION
    assert step.blocked_candidate.identity == LIVE_ID and step.selected_evidence == [LIVE_ID]
    known = known_governed_acquisition(t1["progression"], step.fault_id, step)
    assert known.identity == LIVE_ID and known.model_view()["selection_key"] == LIVE_ID.selection_key()

    # Turn 2 -- the live mistake: the misparsed key is not this run's AVAILABLE evidence -> rejected,
    # nothing selected, no authority; the known action is never issued for it.
    misparsed_select = _fc("knowledge_select_evidence", {"selections": [MISPARSED.selection_key()]})
    t2 = await conv.turn(TURN2_TEXT, [SEARCH, misparsed_select, _governed(LIVE_ALT, "Check active alarms on the node."),
                                      _governed(LIVE_ALT, "Check active alarms on the node.")], "Run `alt`.")
    trace = _trace(t2)
    assert [s["status"] for s in trace["selections"]][:1] == ["rejected_unavailable"]
    assert not [r for r in trace["searches"][-1]["results"] if r["selection_state"] == "SELECTED"]
    assert not [c for c in trace.get("command_authority", []) if c["decision"] == "authorized"]
    assert not (t2["records"][-1].get("diagnostic_step") or {}).get("command") and "`alt`" not in t2["final"]
    (step2,) = t2["progression"].steps
    assert step2.step_id == step.step_id and step2.command is None

    # Turn 3 -- the structured key from the pending context: same tuple, same action id, authorized freshly.
    t3 = await conv.turn("what is the cmd to list the alarms?", [SEARCH, _fc("knowledge_select_evidence", {"selections": [LIVE_ID.selection_key()]}),
                                                                  _governed(LIVE_ALT, "Check active alarms on the node.")], "Run `alt`.")
    record = t3["records"][-1]
    assert record["diagnostic_step"]["command"] == "alt" and record["diagnostic_step"]["procedure_action_id"] == LIVE_ALT
    assert record["diagnostic_step"]["command_source"] == LIVE.canonical
    assert record[SUPPORTING_EVIDENCE_IDENTITIES_KEY] == [LIVE_ID.selection_key()]
    (step3,) = t3["progression"].steps
    assert step3.step_id == step.step_id and step3.status is StepStatus.PRESENTED and step3.procedure_action_id == LIVE_ALT
    assert step3.command_source_identity == LIVE_ID and step3.selected_evidence == [LIVE_ID]
    assert t3["completed"]["knowledge_sources"][0]["section_id"] == LIVE.section
    assert "`alt`" in t3["final"]
