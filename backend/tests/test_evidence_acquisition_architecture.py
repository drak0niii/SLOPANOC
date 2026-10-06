"""Evidence requirement / source / acquisition / authority / progression -- separate concepts.

Live defect (session 28620cd0): after the alarm output the specialist needed node synchronization
evidence for which NO approved acquisition method exists in governed knowledge. The step was
presented as an operator task ("please provide the synchronization source ..."), the operator's
"what command do I run?" was bound as that step's RESULT, and the final answer asked the operator to
"provide the command" -- with an unrelated restart section shown as Source.

Now: WHAT is needed (EvidenceRequirement) != WHERE knowledge comes from (EvidenceSourceRef) != HOW it
is acquired (AcquisitionCandidate[]) != WHETHER it is authorized now (AuthorityDecisionRecord, audit
only) != WHETHER it was obtained (requirement status). A completed discovery with no legitimate
method is a GOVERNED ACQUISITION GAP -- never an operator task, never a request for a command.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import pytest
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.agent_tool import _supporting_evidence, build_server_validated_commands
from backend.agents.technical_authority_engineer.gap_recovery import NON_TERMINAL_GAP_NOTE
from backend.agents.technical_authority_engineer.evidence_acquisition import (
    AcquisitionOutcome,
    DiscoveryState,
    GovernedActionState,
    authority_decision,
    decide_acquisition,
    gap_response,
    requirement_from_proposal,
    requirement_from_step,
    split_missing_information,
)
from backend.agents.technical_authority_engineer.evidence_sources import (
    LiveEvidenceResult,
    LiveEvidenceSource,
    classify_evidence,
    register_live_evidence_source,
    unregister_live_evidence_source,
)
from backend.agents.technical_authority_engineer.result_validation import ResultValidationStatus, validate_operator_observation
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.cases.evidence_model import (
    AcquisitionType,
    AuthorityDecisionRecord,
    AuthorityStatus,
    CandidateAvailability,
    EvidenceKind,
    EvidenceRequirement,
    EvidenceSourceType,
    ExecutionActor,
    GapReason,
    GapStatus,
    RequirementStatus,
    SourceAuthority,
)
from backend.cases.troubleshooting_progression import ResultSource, StepStatus, TroubleshootingProgression, TroubleshootingStep
from backend.knowledge.domain.enums import KnowledgeDocumentType
from backend.tests.test_applicability_blocked_governed_action import (
    CATALOG,
    DOC,
    SEARCH,
    _Doc,
    _governed,
    _legacy_alt,
    _progression_control,
    _trace,
    use_production_specialist,
)
from backend.tests.test_clarification_continuity import _Conversation, _fc
from backend.tests.test_clarification_retrieval_resumption import ALT_ID, TM_TURN1_ARGS, TM_TURN2_ARGS
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)

ALARM_OUTPUT = (
    "$ alt\n\n"
    "Alarm ID    Specific Problem                     Severity    Managed Object\n"
    "1003421     Service Unavailable                  CRITICAL    ManagedElement=1,SectorCarrier=ESS_L21_1\n"
    "  [Additional Info]: ESS Dynamic Sharing failed due to Synchronization Reference loss.\n"
    "1003488     Network Synchronization Degradation  MAJOR       ManagedElement=1,SynchronizationFunction=1\n"
)
COMMAND_QUESTION = (
    "right, but what is the command i need to run so that i can provide The synchronization source configured.\n"
    "Its current status (e.g., locked, unlocked, free-running).\nAny detected phase or frequency errors."
)
SYNC_REQ = {"kind": "diagnostic_result", "description": "Node synchronization source and status", "capability": "node_sync_status"}


def _step(action: str, **extra: Any) -> list[types.Part]:
    step = {"action": action, "reason": "r", "expected_evidence": extra.pop("expected_evidence", "e"), "command": None,
            "command_source": None, "restrictions": [], **extra}
    return [types.Part.from_text(text=json.dumps({
        "outcome": "recommended", "technical_interpretation": "Synchronization reference loss is indicated.",
        "verified_evidence_citations": [], "diagnostic_step": step,
    }))]


def _insufficient(missing: list[str], required: Optional[list[dict]] = None) -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "insufficient_evidence", "technical_interpretation": "RadioUnit_1 shows a sync fault.",
        "missing_information": missing, "required_evidence": required or [],
    }))]


@pytest.fixture
def conversation(isolated_km_repo, monkeypatch):  # noqa: F811
    use_production_specialist(monkeypatch)
    return isolated_km_repo, _Conversation(monkeypatch)


async def _to_alt_output(conv: _Conversation) -> dict[str, Any]:
    """Live turns 1-3: clarification, MATCH + fresh `alt`, operator pastes the alarm output."""
    t1 = await conv.turn("how can i troubleshoot ESS Service Unavailable ?", [SEARCH, DOC.select(), CATALOG, _legacy_alt()],
                         "Please provide the technology and vendor.", tae_args=TM_TURN1_ARGS)
    # (8) applicability unresolved is NOT a gap: blocked governed action, no gap recorded.
    (req1,) = t1["progression"].evidence_requirements
    assert req1.blocking_reason is GapReason.APPLICABILITY_UNRESOLVED and t1["progression"].acquisition_gaps == []
    assert req1.last_authority_decision.status is AuthorityStatus.BLOCKED_APPLICABILITY
    assert t1["completed"]["knowledge_sources"][0]["support_role"] == "supporting"  # Candidate source

    t2 = await conv.turn("4g, Ericsson", [DOC.select(), CATALOG, _governed(ALT_ID, "Check for active alarms on the node")],
                         "Run `alt`.", tae_args=TM_TURN2_ARGS)
    assert t2["records"][-1]["diagnostic_step"]["command"] == "alt"
    # (2) diagnostic result via an approved governed action; authority decided in THIS run.
    (req,) = [r for r in t2["progression"].evidence_requirements if r.originating_step_id]
    assert req.selected_acquisition.acquisition_type is AcquisitionType.GOVERNED_ACTION
    assert req.last_authority_decision.status is AuthorityStatus.AUTHORIZED
    assert req.last_authority_decision.execution_actor is ExecutionActor.OPERATOR
    assert req.last_authority_decision.run_id == _trace(t2)["run_id"]
    assert t2["completed"]["knowledge_sources"][0]["support_role"] == "supporting"
    return t2


# =============================================================================================
# 25. Exact live regression
# =============================================================================================


@pytest.mark.asyncio
async def test_live_sequence_sync_evidence_without_method_is_a_governed_gap_not_an_operator_task(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _to_alt_output(conv)

    # Turn 3: the alarm output satisfies the `alt` requirement; the specialist needs sync evidence
    # (typed) and knows no approved method.
    t3 = await conv.turn(
        ALARM_OUTPUT,
        [_fc("knowledge_search", {"query_text": "ESS Service Unavailable synchronization"}), DOC.select(), CATALOG,
         _step("Check the detailed synchronization status of the node", evidence_requirement=SYNC_REQ, acquisition="none",
               expected_evidence="Synchronization source, lock status and phase/frequency errors")],
        "Please provide the synchronization source configured and its current status.",
        tae_args={"problem_statement": "ESS Service Unavailable"},
    )
    progression = t3["progression"]
    alt_step, sync_step = progression.steps
    assert alt_step.status is StepStatus.COMPLETED
    assert progression.requirement(alt_step.evidence_requirement_id).status is RequirementStatus.SATISFIED
    # No fake PRESENTED step: PROPOSED -> VALIDATED -> BLOCKED_GOVERNED_ACQUISITION_GAP.
    assert sync_step.status is StepStatus.BLOCKED_GOVERNED_ACQUISITION_GAP
    assert [h.to_status for h in sync_step.status_history] == [StepStatus.PROPOSED, StepStatus.VALIDATED, StepStatus.BLOCKED_GOVERNED_ACQUISITION_GAP]
    assert sync_step.command is None and sync_step.status not in (StepStatus.PRESENTED, StepStatus.PROPOSED)
    requirement = progression.requirement(sync_step.evidence_requirement_id)
    assert requirement.kind is EvidenceKind.DIAGNOSTIC_RESULT and requirement.status is RequirementStatus.UNSATISFIED
    assert requirement.acquisition_candidates == [] and requirement.selected_acquisition_id is None
    assert requirement.blocking_reason is GapReason.NO_APPROVED_ACQUISITION_ACTION
    # (24) gap audit for governance.
    (gap,) = progression.acquisition_gaps
    assert (gap.step_id, gap.requirement_id, gap.gap_reason) == (sync_step.step_id, requirement.requirement_id, GapReason.NO_APPROVED_ACQUISITION_ACTION)
    assert gap.sources_searched and all(s["status"] == "ok" for s in gap.sources_searched)
    assert DOC.canonical in gap.sources_consulted and gap.supporting_evidence == [DOC.canonical]
    assert gap.run_id == _trace(t3)["run_id"] and gap.recorded_at is not None
    # (23) deterministic server-grounded response; no request for a command; nothing shown as Source.
    # Other governed checks of the selected procedure remain (the specialist declined them): the gap is
    # one branch's limitation, not a terminal answer (gap_recovery.py).
    assert t3["final"] == gap_response(requirement, GapReason.NO_APPROVED_ACQUISITION_ACTION) + "\n\n" + NON_TERMINAL_GAP_NOTE
    # The selected procedure offered valid governed reads: the specialist was asked once to choose one
    # (never a false gap); it explicitly kept its method-less need -> only then is the gap recorded.
    choice = t3["records"][-1]["action_choice"]
    assert choice["offered"] and choice["chosen"] is None and choice["declined"] is True
    assert t3["records"][-1]["evidence_acquisition"]["terminal"] is False
    assert "provide the command" not in t3["final"].lower() and "please provide the synchronization" not in t3["final"].lower()
    assert [k["support_role"] for k in t3["completed"]["knowledge_sources"]] == ["consulted"]
    assert _trace(t3)["final_response_kind"] == "evidence_acquisition"

    # Turn 4: "what is the command i need to run ..." -- never a result, authorizes nothing, the
    # requirement stays unresolved, the gap is explained (live T7 shape: untyped "specific command").
    t4 = await conv.turn(
        COMMAND_QUESTION,
        [_fc("knowledge_search", {"query_text": "ESS Service Unavailable node synchronization status command"}), DOC.select(),
         _insufficient(["The synchronization source configured for the node.",
                        "The specific command to retrieve detailed node synchronization status on an Ericsson 4G node."])],
        "We still need more information. Could you please provide the command to get the synchronization source and status of the node?",
        tae_args={"problem_statement": "ESS Service Unavailable"},
    )
    progression = t4["progression"]
    assert [s.status for s in progression.steps] == [StepStatus.COMPLETED, StepStatus.BLOCKED_GOVERNED_ACQUISITION_GAP]
    assert all(s.result is None for s in progression.steps[1:])
    assert not [c for c in _trace(t4)["command_authority"] if c["decision"] == "authorized"]
    assert "provide the command" not in t4["final"].lower() and "could you please provide the command" not in t4["final"].lower()
    assert t4["final"].startswith("No approved governed procedure or available operational capability")
    # Continuity: the SAME open requirement and the SAME gap. Nothing material changed (same consulted
    # source, no new result or hint): the gap is reused, no further discovery attempt (no retry loop).
    assert requirement.description in t4["final"] and "already found to have no governed acquisition method" in t4["final"]
    record = t4["records"][-1]
    assert record["evidence_acquisition"]["system_items_withheld"] == [
        "The specific command to retrieve detailed node synchronization status on an Ericsson 4G node."
    ]
    assert all("command" not in item.lower() for item in record.get("missing_information") or [])
    assert [k["support_role"] for k in t4["completed"]["knowledge_sources"]] == ["consulted"]
    (gap4,) = progression.acquisition_gaps
    assert gap4.gap_id == gap.gap_id and gap4.status is GapStatus.OPEN and len(gap4.discovery_attempts) == 1
    assert [r.requirement_id for r in progression.evidence_requirements if r.status is RequirementStatus.UNSATISFIED] == [requirement.requirement_id]
    assert record["evidence_acquisition"]["requirement"]["requirement_id"] == requirement.requirement_id
    assert record["evidence_acquisition"]["discovery_attempts"] == 1 and record["evidence_acquisition"]["gap_repeated"] is True
    assert record["evidence_acquisition"]["gap_id"] == gap.gap_id and record["evidence_acquisition"]["gap_is_new"] is False


@pytest.mark.asyncio
async def test_untyped_live_shape_command_less_sync_step_is_a_gap(conversation) -> None:
    """The specialist omits the typed fields (exact live T5 shape): a command-less step whose own
    text refers to command output cannot be a manual observation."""
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _to_alt_output(conv)
    t3 = await conv.turn(
        ALARM_OUTPUT,
        [SEARCH, DOC.select(), _step("Check the detailed synchronization status of the node and all connected radio units.",
                                     expected_evidence="Report the synchronization source configured, its status, any phase or frequency "
                                                       "errors. If available, also report the output of any node-specific synchronization commands.")],
        "Please provide the following: the synchronization source configured.",
        tae_args={"problem_statement": "ESS Service Unavailable"},
    )
    assert t3["progression"].steps[-1].status is StepStatus.BLOCKED_GOVERNED_ACQUISITION_GAP
    assert t3["final"].startswith("No approved governed procedure")


# =============================================================================================
# 26. Required tests
# =============================================================================================


def test_1_operator_fact_is_a_clarification_and_a_mechanism_never_is() -> None:
    requirement = requirement_from_proposal({"kind": "operator_fact", "description": "Node identifier"}, "F")
    assert requirement.kind is EvidenceKind.OPERATOR_FACT
    assert requirement_from_proposal({"kind": "operator_fact", "description": "The command to check sync"}, "F") is None
    evidence, system = split_missing_information(["Vendor", "The specific command to retrieve sync status", "The output of the alt command"])
    assert evidence == ["Vendor", "The output of the alt command"] and system == ["The specific command to retrieve sync status"]


@pytest.mark.asyncio
async def test_1b_operator_fact_selects_operator_fact_acquisition() -> None:
    requirement = requirement_from_proposal({"kind": "operator_fact", "description": "Node identifier"}, "F")
    decision = await decide_acquisition(requirement, progression=None, action=GovernedActionState(), discovery=DiscoveryState(), run_id="r")
    assert decision.outcome == AcquisitionOutcome.PRESENT
    assert requirement.selected_acquisition.acquisition_type is AcquisitionType.OPERATOR_FACT


class _FakeSource(LiveEvidenceSource):
    def __init__(self, source_id: str, source_type: EvidenceSourceType, capabilities: set[str], result: Optional[LiveEvidenceResult] = None, raises: bool = False):
        self.source_id, self.source_type, self.capabilities = source_id, source_type, frozenset(capabilities)
        self._result, self._raises, self.calls = result, raises, 0

    async def query(self, capability: str, context: Mapping[str, Any]) -> LiveEvidenceResult:
        self.calls += 1
        if self._raises:
            raise RuntimeError("connector error")
        return self._result


@pytest.fixture
def live_sources():
    registered: list[str] = []

    def _register(source: LiveEvidenceSource) -> LiveEvidenceSource:
        register_live_evidence_source(source)
        registered.append(source.source_id)
        return source

    yield _register
    for source_id in registered:
        unregister_live_evidence_source(source_id)


@pytest.mark.asyncio
async def test_3_evidence_obtained_directly_from_an_approved_live_source(conversation, live_sources) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    observed = datetime(2026, 10, 1, 14, 15, tzinfo=timezone.utc)
    onefm = live_sources(_FakeSource("onefm-test", EvidenceSourceType.ALARM_MANAGEMENT, {"active_alarms"},
                                     LiveEvidenceResult(status="ok", content="1003488 Network Synchronization Degradation MAJOR", observed_at=observed)))
    t1 = await conv.turn(
        "how can i troubleshoot ESS Service Unavailable on Ericsson 4G?",
        [SEARCH, DOC.select(), CATALOG, _step(
            "Check active alarms on the node", expected_evidence="Active alarm list", procedure_action_id=ALT_ID,
            evidence_requirement={"kind": "diagnostic_result", "description": "Active alarms on the node", "capability": "active_alarms"},
            acquisition="governed_action",
        )],
        "Run `alt`.",
        tae_args={"problem_statement": "ESS Service Unavailable", "known_applicability_facts": {"vendor": ["Ericsson"], "technology": ["4G"]}},
    )
    assert onefm.calls == 1
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.COMPLETED and step.result.source is ResultSource.CONTEXT_SOURCE and step.command is None
    requirement = t1["progression"].requirement(step.evidence_requirement_id)
    assert requirement.status is RequirementStatus.SATISFIED
    assert requirement.selected_acquisition.acquisition_type is AcquisitionType.MONITORING_QUERY
    assert requirement.satisfied_by[0].source_id == "onefm-test" and requirement.satisfied_by[0].observed_at == observed
    assert requirement.satisfied_by[0].authority is SourceAuthority.LIVE_OPERATIONAL_CONTEXT
    assert requirement.last_authority_decision.status is AuthorityStatus.NOT_REQUIRED
    assert t1["final"].startswith("Obtained Active alarms on the node from alarm_management (onefm-test")
    assert "`alt`" not in t1["final"] and "1003488" in t1["final"]


@pytest.mark.asyncio
async def test_4_one_requirement_many_candidates_and_5_selection_is_not_authority(live_sources) -> None:
    live_sources(_FakeSource("itsm-test", EvidenceSourceType.ITSM, {"node_sync_status"}, LiveEvidenceResult(status="not_found")))
    live_sources(_FakeSource("mon-test", EvidenceSourceType.MONITORING, {"node_sync_status"}, raises=True))
    requirement = requirement_from_proposal(SYNC_REQ, "F")
    governed = GovernedActionState(procedure_action_id="pa-sync", command_template="get sync", canonical_source_id="K:v1:S",
                                   applicability="match", blocking_reason=GapReason.ACTION_NOT_AUTHORIZED)
    decision = await decide_acquisition(requirement, progression=None, action=governed, discovery=DiscoveryState(governed_search_performed=True), run_id="r1")
    types_ = [c.acquisition_type for c in requirement.acquisition_candidates]
    assert types_ == [AcquisitionType.ITSM_QUERY, AcquisitionType.MONITORING_QUERY, AcquisitionType.GOVERNED_ACTION]
    assert requirement.selected_acquisition.acquisition_type is AcquisitionType.GOVERNED_ACTION
    # Selected, but blocked: a candidate is never authority and never a presentable command.
    assert requirement.selected_acquisition.availability is CandidateAvailability.BLOCKED
    assert authority_decision(requirement, None, "r1").status is AuthorityStatus.NOT_AUTHORIZED
    assert decision.outcome == AcquisitionOutcome.PRESENT


@pytest.mark.asyncio
async def test_6_previous_authorization_is_never_current_authority() -> None:
    requirement = requirement_from_proposal(SYNC_REQ, "F")
    requirement.record_authority(AuthorityDecisionRecord(acquisition_id="acq-old", status=AuthorityStatus.AUTHORIZED, run_id="run-1"))
    blocked_now = GovernedActionState(procedure_action_id=ALT_ID, command_template="alt", canonical_source_id=DOC.canonical,
                                      blocking_reason=GapReason.APPLICABILITY_UNRESOLVED)
    await decide_acquisition(requirement, progression=None, action=blocked_now, discovery=DiscoveryState(governed_search_performed=True), run_id="run-2")
    decision = authority_decision(requirement, None, "run-2")
    assert decision.status is AuthorityStatus.BLOCKED_APPLICABILITY and decision.run_id == "run-2"
    # Command Authority itself never reads the audit: UNKNOWN evidence authorizes nothing.
    unknown = EvidenceReference(source_id=DOC.canonical, source_type="governed_knowledge", title="t", content_snippet="alt\n",
                                metadata={"lifecycle_status": "approved", "applicability_outcome": "unknown", "source_id": DOC.filename})
    assert build_server_validated_commands([unknown], [{"command": "alt", "source_id": DOC.canonical}]) == []


@pytest.mark.asyncio
async def test_7_10_11_gap_vs_failures_are_distinct() -> None:
    def _req() -> EvidenceRequirement:
        return requirement_from_proposal(SYNC_REQ, "F")

    ok = [{"query_text": "q", "status": "ok", "result_count": 2}]
    found_match = DiscoveryState(governed_search_performed=True, searches=ok, available=[("K:v1:S", "match")], selected=["K:v1:S"])
    gap = await decide_acquisition(_req(), progression=None, action=GovernedActionState(), discovery=found_match, run_id="r")
    assert gap.outcome == AcquisitionOutcome.GAP and gap.gap.gap_reason is GapReason.NO_APPROVED_ACQUISITION_ACTION
    nothing = await decide_acquisition(_req(), progression=None, action=GovernedActionState(),
                                       discovery=DiscoveryState(governed_search_performed=True, searches=ok), run_id="r")
    assert nothing.outcome == AcquisitionOutcome.GAP and nothing.gap.gap_reason is GapReason.NO_RELEVANT_EVIDENCE_FOUND
    not_applicable = await decide_acquisition(_req(), progression=None, action=GovernedActionState(),
                                              discovery=DiscoveryState(governed_search_performed=True, searches=ok, available=[("K:v1:S", "not_applicable")]), run_id="r")
    assert not_applicable.gap.gap_reason is GapReason.NO_APPLICABLE_PROCEDURE
    # (11) retrieval failure / discovery not performed: not gaps.
    failed = await decide_acquisition(_req(), progression=None, action=GovernedActionState(),
                                      discovery=DiscoveryState(governed_search_performed=True, searches=[{"query_text": "q", "status": "error"}]), run_id="r")
    assert failed.outcome == AcquisitionOutcome.FAILED and failed.requirement.blocking_reason is GapReason.RETRIEVAL_FAILED and failed.gap is None
    incomplete = await decide_acquisition(_req(), progression=None, action=GovernedActionState(), discovery=DiscoveryState(), run_id="r")
    assert incomplete.outcome == AcquisitionOutcome.FAILED and incomplete.requirement.blocking_reason is GapReason.DISCOVERY_INCOMPLETE
    # (8) applicability unresolved: blocked, not a gap.
    unknown = await decide_acquisition(_req(), progression=None, action=GovernedActionState(),
                                       discovery=DiscoveryState(governed_search_performed=True, searches=ok, available=[("K:v1:S", "unknown")]), run_id="r")
    assert unknown.outcome == AcquisitionOutcome.BLOCKED and unknown.requirement.blocking_reason is GapReason.APPLICABILITY_UNRESOLVED


@pytest.mark.asyncio
async def test_10_tool_and_data_source_unavailable_are_not_gaps(live_sources) -> None:
    ok = [{"query_text": "q", "status": "ok", "result_count": 1}]
    discovery = DiscoveryState(governed_search_performed=True, searches=ok, available=[("K:v1:S", "match")])
    live_sources(_FakeSource("topo", EvidenceSourceType.TOPOLOGY, {"node_sync_status"}, LiveEvidenceResult(status="unavailable")))
    decision = await decide_acquisition(requirement_from_proposal(SYNC_REQ, "F"), progression=None, action=GovernedActionState(), discovery=discovery, run_id="r")
    assert decision.outcome == AcquisitionOutcome.FAILED and decision.requirement.blocking_reason is GapReason.TOOL_UNAVAILABLE and decision.gap is None
    unregister_live_evidence_source("topo")
    live_sources(_FakeSource("mon", EvidenceSourceType.MONITORING, {"node_sync_status"}, raises=True))
    decision = await decide_acquisition(requirement_from_proposal(SYNC_REQ, "F"), progression=None, action=GovernedActionState(), discovery=discovery, run_id="r")
    assert decision.requirement.blocking_reason is GapReason.DATA_SOURCE_UNAVAILABLE and decision.gap is None


@pytest.mark.asyncio
async def test_11_retrieval_failure_end_to_end_is_not_a_gap(conversation, monkeypatch) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _to_alt_output(conv)
    from backend.tools.knowledge import tools as knowledge_tools
    from backend.tools.knowledge.runtime import KnowledgeRuntimeError

    class _Broken:
        async def search(self, *a: Any, **k: Any) -> Any:
            raise KnowledgeRuntimeError("simulated")

    monkeypatch.setattr(knowledge_tools, "get_knowledge_tool_service", lambda: _Broken())
    t3 = await conv.turn(ALARM_OUTPUT, [SEARCH, _step("Check node synchronization status", evidence_requirement=SYNC_REQ, acquisition="none")],
                         "Please provide the sync status.", tae_args={"problem_statement": "ESS Service Unavailable"})
    assert t3["progression"].acquisition_gaps == []
    requirement = t3["progression"].evidence_requirements[-1]
    assert requirement.blocking_reason is GapReason.RETRIEVAL_FAILED and requirement.status is RequirementStatus.UNSATISFIED
    assert t3["final"].startswith("The required evidence (Node synchronization source and status) could not be acquired: governed knowledge retrieval failed")
    assert all(s.status is not StepStatus.PRESENTED or s.command for s in t3["progression"].steps)


@pytest.mark.asyncio
async def test_9_ungrounded_command_is_no_method_and_its_need_is_a_gap(conversation) -> None:
    # A command no selected procedure instructs is no acquisition method (unlike a governed action that
    # authority refuses): after the bounded governed path, its need is a recorded gap -- never kept as a
    # "blocked" method behind a command-less operator task.
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await conv.turn("how can i troubleshoot ESS Service Unavailable on Ericsson 4G?",
                         [SEARCH, DOC.select(), _legacy_alt(command="show everything-now")], "Checking.",
                         tae_args={"problem_statement": "ESS", "known_applicability_facts": {"vendor": ["Ericsson"], "technology": ["4G"]}})
    requirement = t1["progression"].evidence_requirements[-1]
    assert requirement.blocking_reason is GapReason.NO_APPROVED_ACQUISITION_ACTION
    assert [g.requirement_id for g in t1["progression"].acquisition_gaps] == [requirement.requirement_id]
    assert all(s.status is not StepStatus.PRESENTED for s in t1["progression"].steps)
    assert "show everything-now" not in t1["final"] and not (t1["records"][-1].get("diagnostic_step") or {}).get("command")


@pytest.mark.asyncio
async def test_12_genuine_manual_observation_is_presented(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    t1 = await conv.turn(
        "how can i troubleshoot ESS Service Unavailable on Ericsson 4G?",
        [SEARCH, DOC.select(), _step("Visually inspect whether the cabinet alarm LED is red",
                                     evidence_requirement={"kind": "observation", "description": "Cabinet alarm LED colour"},
                                     acquisition="manual_observation")],
        "Please check the cabinet LED.",
        tae_args={"problem_statement": "ESS", "known_applicability_facts": {"vendor": ["Ericsson"], "technology": ["4G"]}},
    )
    (step,) = t1["progression"].steps
    assert step.status is StepStatus.PRESENTED and t1["progression"].acquisition_gaps == []
    requirement = t1["progression"].requirement(step.evidence_requirement_id)
    assert requirement.selected_acquisition.acquisition_type is AcquisitionType.MANUAL_OBSERVATION
    assert requirement.last_authority_decision is None
    assert t1["completed"]["knowledge_sources"][0]["support_role"] == "supporting"


@pytest.mark.asyncio
async def test_13_operator_question_does_not_complete_the_pending_result(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _to_alt_output(conv)
    t3 = await conv.turn("so how do I check that", [_insufficient([])], "Run `alt` and paste its output.",
                         tae_args={"problem_statement": "ESS Service Unavailable"})
    (alt_step,) = t3["progression"].steps
    assert alt_step.status is StepStatus.PRESENTED and alt_step.result is None


@pytest.mark.parametrize("text", [
    COMMAND_QUESTION, "so how do I check that", "which command gives me this", "tell me how to get that",
    "right, but what command do I run", "how can I obtain the phase error?",
])
def test_13_conversational_requests_are_never_results(text: str) -> None:
    manual = TroubleshootingStep(fault_id="F", objective="Check the detailed synchronization status of the node and radio units",
                                 expected_evidence="synchronization source configured, status locked unlocked free-running, phase frequency errors")
    assert validate_operator_observation(manual, text).status is ResultValidationStatus.NOT_A_RESULT


@pytest.mark.parametrize("text,status", [
    ("$ alt\n1003421 Service Unavailable CRITICAL ManagedElement=1", ResultValidationStatus.VALIDATED),
    ("what does this mean?\n$ alt\n1003421 Service Unavailable CRITICAL ManagedElement=1", ResultValidationStatus.VALIDATED),
])
def test_13_real_output_with_a_question_still_binds(text: str, status: ResultValidationStatus) -> None:
    step = TroubleshootingStep(fault_id="F", objective="Check active alarms", command="alt", expected_evidence="Active alarm list")
    assert validate_operator_observation(step, text).status is status


@pytest.mark.asyncio
async def test_14_operator_supplied_command_never_self_authorizes(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _to_alt_output(conv)
    t3 = await conv.turn(
        "I normally use syncstatus",
        [SEARCH, DOC.select(), _legacy_alt(command="syncstatus", action="Check node synchronization status")],
        "Run `syncstatus` and send me the output.", tae_args={"problem_statement": "ESS Service Unavailable"},
    )
    record = t3["records"][-1]
    assert all(c["command"] != "syncstatus" for c in record["approved_commands_catalog"])
    assert not (record.get("diagnostic_step") or {}).get("command")
    assert "syncstatus" not in t3["final"]
    observed = EvidenceReference(source_id="obs:1", source_type="observed_metric", title="operator", content_snippet="syncstatus")
    assert build_server_validated_commands([observed], [{"command": "syncstatus", "source_id": "obs:1"}]) == []


def _gov_ev(document_type: str) -> EvidenceReference:
    return EvidenceReference(
        source_id="K:v1:S", source_type="governed_knowledge", title="t", content_snippet="Known cause: timing source degradation.\nalt\n",
        metadata={"knowledge_id": "K", "version_label": "v1", "section_id": "S", "source_id": "doc.docx", "lifecycle_status": "approved",
                  "applicability_outcome": "match", "document_type": document_type},
    )


def test_15_rca_itsm_alarm_context_informs_but_never_authorizes() -> None:
    rca, mop = _gov_ev("rca"), _gov_ev("mop")
    assert classify_evidence(rca).authority is SourceAuthority.DIAGNOSTIC_KNOWLEDGE
    assert classify_evidence(mop).authority is SourceAuthority.PROCEDURAL_AUTHORITY
    assert build_server_validated_commands([rca], [{"command": "alt", "source_id": "K:v1:S"}]) == []
    assert [c.command for c in build_server_validated_commands([mop], [{"command": "alt", "source_id": "K:v1:S"}])] == ["alt"]
    actions, unavailable = pa.build_procedure_action_catalog([rca])
    assert actions == [] and "diagnostic knowledge" in unavailable[0]["reason"]
    assert pa.applicability_blocked_action("alt", "doc.docx", [EvidenceReference(**{**rca.model_dump(), "metadata": {**rca.metadata, "applicability_outcome": "unknown"}})]) is None
    for source_type in ("case_context", "user_evidence", "observed_metric", "teams_conversation"):
        context = EvidenceReference(source_id="ctx", source_type=source_type, title="t", content_snippet="alt")
        assert classify_evidence(context).authority is not SourceAuthority.PROCEDURAL_AUTHORITY
        assert build_server_validated_commands([context], [{"command": "alt", "source_id": "ctx"}]) == []
    live = _FakeSource("onefm", EvidenceSourceType.ALARM_MANAGEMENT, {"active_alarms"})
    assert live.source_ref().authority is SourceAuthority.LIVE_OPERATIONAL_CONTEXT


def test_16_unsupported_selected_evidence_is_consulted_not_source() -> None:
    restart = {"source_type": "governed_knowledge", "source_id": "DOC1:v2:sec-7", "metadata": {}}
    alarm_mop = {"source_type": "governed_knowledge", "source_id": "MOP:v1:sec-0", "metadata": {}}
    insufficient = {"outcome": "insufficient_evidence", "diagnostic_step": None, "verified_evidence_citations": []}
    assert _supporting_evidence(insufficient, [restart], None) == []
    cited = {**insufficient, "verified_evidence_citations": ["MOP:v1:sec-0"]}
    assert _supporting_evidence(cited, [restart, alarm_mop], None) == ["MOP:v1:sec-0"]
    presented = {"outcome": "recommended", "diagnostic_step": {"command": "alt", "command_source": "MOP:v1:sec-0"}}
    assert _supporting_evidence(presented, [restart, alarm_mop], None) == ["MOP:v1:sec-0"]


@pytest.mark.parametrize("summary,status,actor", [
    ({"execution_mode": "advisory", "policy_decision": "allowed"}, AuthorityStatus.AUTHORIZED, ExecutionActor.OPERATOR),
    ({"execution_mode": "approval_required", "policy_decision": "approval_required"}, AuthorityStatus.APPROVAL_REQUIRED, ExecutionActor.ANOC),
    ({"execution_mode": "read_execution", "policy_decision": "allowed"}, AuthorityStatus.AUTHORIZED_BY_CURRENT_POLICY, ExecutionActor.ANOC),
])
@pytest.mark.asyncio
async def test_advisory_hitl_closed_loop_same_requirement_different_mode(summary: dict, status: AuthorityStatus, actor: ExecutionActor) -> None:
    requirement = requirement_from_proposal(SYNC_REQ, "F")
    action = GovernedActionState(procedure_action_id="pa-1", command_template="get sync", canonical_source_id="K:v1:S", authorized_command="get sync")
    await decide_acquisition(requirement, progression=None, action=action, discovery=DiscoveryState(governed_search_performed=True), run_id="r")
    decision = authority_decision(requirement, summary, "r")
    assert (decision.status, decision.execution_actor, decision.run_id) == (status, actor, "r")
    assert requirement.kind is EvidenceKind.DIAGNOSTIC_RESULT and requirement.description == SYNC_REQ["description"]


def test_untyped_inference_keeps_genuine_manual_steps() -> None:
    manual = requirement_from_step({"action": "Check the cabinet LED colour", "expected_evidence": "LED colour"}, "F", attempted_command=False)
    assert manual.kind is EvidenceKind.OBSERVATION
    needs_command = requirement_from_step({"action": "Check sync", "expected_evidence": "output of the sync commands"}, "F", attempted_command=False)
    assert needs_command.kind is EvidenceKind.DIAGNOSTIC_RESULT
    attempted = requirement_from_step({"action": "Check sync", "expected_evidence": "status"}, "F", attempted_command=True)
    assert attempted.kind is EvidenceKind.DIAGNOSTIC_RESULT


def test_old_progression_payload_loads_without_evidence_fields() -> None:
    legacy = TroubleshootingProgression().model_dump(mode="json")
    legacy.pop("evidence_requirements")
    legacy.pop("acquisition_gaps")
    loaded = TroubleshootingProgression.model_validate(legacy)
    assert loaded.evidence_requirements == [] and loaded.acquisition_gaps == []
