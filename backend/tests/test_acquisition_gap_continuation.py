"""Ungrounded diagnostic evidence requirements: a governed acquisition gap ends a BRANCH, the
investigation continues.

Live (session 9b5510b4): after `alt` and `st fieldr`, the specialist needed the operational state of the
radio units and wrote a command no selected procedure instructs (the text existed only in an image
transcription). Command Authority rejected it (correct), but the refused command was still treated as a
"blocked" governed method: the command-less step was presented and the operator was told to perform the
check themselves. No gap was recorded and no governed alternative was offered.

Invariants under test:
    LLM owns troubleshooting reasoning; the server owns operational authority.
    A model-written command that names no governed action of THIS run is no acquisition method.
    EvidenceRequirement -> current catalog -> governed alternatives -> one server search
        -> AcquisitionGap recorded -> ONE continuation request -> next requirement resolved as usual.
    The gap is stated from server state; model reasoning is labelled as such; no ungrounded command
    is ever displayed or authorized; the same gap is never re-asked in a loop.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

import backend.tests.test_clarification_continuity as tcc
from backend.agents.technical_authority_engineer.agent_tool import _ungrounded_command
from backend.agents.technical_authority_engineer.gap_recovery import (
    GAP_CONTINUATION_KEY,
    gap_continuation_instruction,
    proposes_method,
    render_acquisition_gap_notice,
    with_acquisition_gap_notice,
)
from backend.agents.technical_authority_engineer.procedure_actions import names_governed_action
from backend.cases.evidence_model import EvidenceKind, EvidenceRequirement, GapReason, GapStatus
from backend.cases.troubleshooting_progression import StepStatus
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests.test_applicability_blocked_governed_action import _fc, _payload, _trace, use_production_specialist
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_evidence_acquisition_architecture import ALARM_OUTPUT
from backend.tests.test_gap_recovery_and_continuation import SEARCH, _alt_completed, _evidence, _Mop
from backend.tools.knowledge.diagnostic_trace import format_diagnostic_trace

CONTINUATION_MARKER = "SERVER ACQUISITION GAP CONTINUATION"
CATALOG = _fc("procedure_action_catalog", {})
ALARM = _Mop("ALARM-MOP", "Node alarm check\nHC Commands:\nalt\n")
RADIO = _Mop("RADIO-MOP", "Node alarm check\nHC Commands:\nalt\nst ru\n")
RRU_NEED = "Operational state of the remote radio units"
NOT_CROSS_CHECKED = "could be found or cross-checked in the currently selected and validated governed knowledge"
HYPOTHESIS = "A lost clock reference blocks the service."


class _ClockDoc(_Mop):
    """A governed procedure the earlier searches cannot find (no shared vocabulary): reachable only by
    the specialist's own new search after the gap."""

    def knowledge(self) -> KnowledgeObject:
        return KnowledgeObject(
            knowledge_id=self.kid, document_type=KnowledgeDocumentType.MOP, title="Clock Reference Verification",
            version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
            lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={}),
            source=KnowledgeSource(source_system="test", source_id=self.filename, display_name=self.kid),
            sections=[KnowledgeSection(section_id=self.section, knowledge_id=self.kid, heading=None, sequence=0, content=self.content, source_locator="l")],
        )


CLOCK = _ClockDoc("CLOCK-MOP", "Clock reference verification\nHC Commands:\nst clockref\n")


class _RecordingLlm(tcc._ScriptedLlm):
    instances: list["_RecordingLlm"] = []

    def __init__(self, model: str, parts_by_call: list[list[types.Part]], **kwargs: Any) -> None:
        super().__init__(model=model, parts_by_call=parts_by_call, **kwargs)
        self._requests: list[LlmRequest] = []
        _RecordingLlm.instances.append(self)

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        self._requests.append(llm_request)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


@pytest.fixture
def conversation(isolated_km_repo, monkeypatch):  # noqa: F811
    use_production_specialist(monkeypatch)
    monkeypatch.setattr(tcc, "_ScriptedLlm", _RecordingLlm)
    _RecordingLlm.instances = []
    return isolated_km_repo, tcc._Conversation(monkeypatch)


def _ungrounded(mop: _Mop, command: str = "st rru", need: str = RRU_NEED) -> list[types.Part]:
    """The live shape: a typed diagnostic need, `acquisition` claimed governed, a command no selected
    procedure instructs."""
    return _payload({"action": f"Check the {need.lower()}.", "reason": "A disabled unit would explain the outage.",
                     "expected_evidence": need, "command": command, "command_source": mop.filename, "restrictions": [],
                     "evidence_requirement": {"kind": "diagnostic_result", "description": need}, "acquisition": "governed_action"},
                    "Observed: the service alarm and a hardware fault. Hypothesis: a disabled unit causes the outage.")


def _clock_step() -> list[types.Part]:
    return _payload({"action": "Verify the clock reference state.", "reason": "Rule out a synchronization cause.",
                     "expected_evidence": "Clock reference state", "procedure_action_id": CLOCK.action_id("st clockref"),
                     "command": None, "command_source": None, "restrictions": [], "tests_hypothesis": HYPOTHESIS,
                     "evidence_requirement": {"kind": "diagnostic_result", "description": "Clock reference state"}},
                    f"Hypothesis: {HYPOTHESIS}")


def _governed_ru() -> list[types.Part]:
    return _payload({"action": "Check the radio unit state.", "reason": "A radio unit fault is suspected.",
                     "expected_evidence": "Radio unit operational state", "procedure_action_id": RADIO.action_id("st ru"),
                     "command": None, "command_source": None, "restrictions": [],
                     "evidence_requirement": {"kind": "diagnostic_result", "description": "Radio unit operational state"}})


def _same_need() -> list[types.Part]:
    return [types.Part.from_text(text=json.dumps({
        "outcome": "insufficient_evidence", "technical_interpretation": "The unit state is still needed.",
        "verified_evidence_citations": [], "missing_information": [],
        "required_evidence": [{"kind": "diagnostic_result", "description": RRU_NEED}],
    }))]


CONTINUE_TO_CLOCK = [_fc("knowledge_search", {"query_text": "clock reference verification"}), CLOCK.select(), CATALOG, _clock_step()]


def _tae_requests() -> list[LlmRequest]:
    return [r for llm in _RecordingLlm.instances if llm.model == "scripted-tae" for r in llm._requests][-50:]


def _user_texts(request: LlmRequest) -> list[str]:
    return [p.text for c in request.contents or [] if c.role == "user" for p in c.parts or [] if p.text]


def _continuation_requests(turn_requests: list[LlmRequest]) -> list[LlmRequest]:
    """Requests that answer the continuation instruction directly (it is their final content); the
    specialist's tool round-trips inside that message are not further requests for continuation."""
    def _last_text(request: LlmRequest) -> str:
        last = (request.contents or [None])[-1]
        return " ".join(p.text for p in (last.parts or []) if p.text) if last is not None and last.role == "user" else ""

    return [r for r in turn_requests if CONTINUATION_MARKER in _last_text(r)]


def _authorized(turn: dict[str, Any]) -> list[str]:
    return [c["command"] for c in _trace(turn).get("command_authority", []) if c["decision"] == "authorized"]


def _events(turn: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    return [e for e in _trace(turn).get("operational_events", []) if e.get("stage") == stage]


def _rru_requirement(turn: dict[str, Any]) -> EvidenceRequirement:
    (requirement,) = [r for r in turn["progression"].evidence_requirements if r.description == RRU_NEED]
    return requirement


async def _alarm_turn(conv: Any, continuation: list[list[types.Part]], mop: _Mop = ALARM) -> dict[str, Any]:
    """Turn 2 of the live shape: the alarm output is supplied; the specialist needs evidence that no
    selected procedure obtains and writes its own command for it."""
    before = len(_tae_requests())
    turn = await conv.turn(ALARM_OUTPUT, [SEARCH, mop.select(), _ungrounded(mop), *continuation], "Please run the check yourself.")
    turn["tae_requests"] = _tae_requests()[before:]
    return turn


# =============================================================================================
# 1. A grounded acquisition candidate -> the normal governed next step, nothing else
# =============================================================================================


@pytest.mark.asyncio
async def test_1_grounded_acquisition_candidate_presents_the_normal_governed_step(conversation) -> None:
    repo, conv = conversation
    await repo.add(RADIO.knowledge())
    await _alt_completed(conv, RADIO)
    before = len(_tae_requests())
    t2 = await conv.turn(ALARM_OUTPUT, [SEARCH, RADIO.select(), CATALOG, _governed_ru()], "Run `st ru`.")
    assert "st ru" in _authorized(t2) and "`st ru`" in t2["final"]
    assert not _continuation_requests(_tae_requests()[before:]), "no continuation when a governed method exists"
    assert t2["progression"].acquisition_gaps == [] and GAP_CONTINUATION_KEY not in t2["records"][-1]
    assert NOT_CROSS_CHECKED not in t2["final"]


# =============================================================================================
# 2-7, 9. No governed method -> no command, gap recorded and stated, ONE continuation, next
#         requirement resolved normally, model reasoning labelled
# =============================================================================================


@pytest.mark.asyncio
async def test_2_to_7_9_gap_is_recorded_stated_and_the_investigation_continues_to_a_governed_step(conversation) -> None:
    repo, conv = conversation
    await repo.add(ALARM.knowledge())
    await repo.add(CLOCK.knowledge())
    await _alt_completed(conv, ALARM)
    t2 = await _alarm_turn(conv, CONTINUE_TO_CLOCK)

    # 2. The ungrounded command is never shown, never authorized.
    assert "st rru" not in t2["final"] and "st rru" not in _authorized(t2)
    rejected = [c for c in _trace(t2)["command_authority"] if c["command"] == "st rru"]
    assert rejected and rejected[0]["decision"] == "rejected" and rejected[0]["stage"] == "grounding"
    # 3. The need is recorded as a governed acquisition gap (requirement kept, never hidden).
    rru = _rru_requirement(t2)
    (gap,) = [g for g in t2["progression"].acquisition_gaps if g.requirement_id == rru.requirement_id]
    assert gap.status is GapStatus.OPEN and gap.gap_reason is GapReason.NO_APPROVED_ACQUISITION_ACTION
    assert rru.kind is EvidenceKind.DIAGNOSTIC_RESULT and len(gap.discovery_attempts) == 1
    # The bounded governed path came first: no alternative existed, one server search looked for one.
    (recovery,) = _events(t2, "gap_recovery")
    assert recovery["alternatives"] == [] and recovery["discovery"] == "searched" and recovery["offered"] is False
    # 4. The operator is told plainly; never asked to perform the ungrounded check.
    assert f"The requested evidence ({RRU_NEED}) was identified as a useful diagnostic direction" in t2["final"]
    assert NOT_CROSS_CHECKED in t2["final"] and "yourself" not in t2["final"]
    # 5. ONE continuation request, tools still enabled (the specialist searched, selected, listed the catalog).
    (continuation,) = _continuation_requests(t2["tae_requests"])
    instruction = _user_texts(continuation)[-1]
    for required in ("could not be acquired through currently selected and validated governed knowledge",
                     "Continue reasoning from the evidence already available", "Do not invent a command",
                     "Do not imply that an ungrounded method is governed", "Do not repeat the same unavailable acquisition requirement"):
        assert required in instruction
    assert continuation.tools_dict, "the continuation keeps the governed tools"
    searches = [s["query_text"] for s in _trace(t2)["searches"]]
    assert searches[-1] == "clock reference verification"
    # 6. A different next evidence requirement, proposed by the specialist.
    record = t2["records"][-1]
    assert record["evidence_acquisition"]["requirement"]["requirement_id"] != rru.requirement_id
    assert record["evidence_acquisition"]["requirement"]["description"] == "Clock reference state"
    # 7. Its governed method is resolved and presented normally.
    assert record["diagnostic_step"]["command"] == "st clockref" and "st clockref" in _authorized(t2)
    clock_step = t2["progression"].steps[-1]
    assert clock_step.status is StepStatus.PRESENTED and clock_step.procedure_action_id == CLOCK.action_id("st clockref")
    assert "`st clockref`" in t2["final"] or "st clockref" in t2["final"]
    # 9. Model reasoning is labelled; only the authorized step is called governed.
    assert f"Diagnostic hypothesis (generated by the troubleshooting model; not verified against current governed knowledge): {HYPOTHESIS}" in t2["final"]
    assert "Knowledge status: the diagnostic reasoning in this answer was generated by the troubleshooting model" in t2["final"]
    assert "the command of the next diagnostic step comes from an approved governed procedure" in t2["final"]
    # Observability.
    (event,) = _events(t2, "gap_continuation")
    assert event["continued"] is True and event["gaps"][0]["requirement_id"] == rru.requirement_id
    assert event["governed_command_presented"] is True
    rendered = format_diagnostic_trace(_trace(t2))
    assert f"GAP CONTINUATION continued=True gaps=[('{rru.requirement_id}', '{gap.gap_id}', 'no_approved_acquisition_action')]" in rendered


# =============================================================================================
# 10 (section 10). A governed alternative is preferred: offered before any model-only continuation
# =============================================================================================


@pytest.mark.asyncio
async def test_governed_alternative_is_offered_before_continuation_and_needs_no_continuation(conversation) -> None:
    repo, conv = conversation
    await repo.add(RADIO.knowledge())
    await _alt_completed(conv, RADIO)
    # Selected evidence offers an unperformed governed read: the current catalog is offered first.
    t2 = await _alarm_turn(conv, [RADIO.select(), _governed_ru()], RADIO)
    (choice,) = _events(t2, "action_choice")
    assert choice["offered"] == [RADIO.action_id("st ru")] and choice["chosen"] == RADIO.action_id("st ru")
    assert not _continuation_requests(t2["tae_requests"])
    assert "st ru" in _authorized(t2) and "st rru" not in t2["final"]
    assert t2["progression"].acquisition_gaps == [], "a governed method was found for the need: no gap"


# =============================================================================================
# 8. The same gap is not immediately repeated (bounded: one continuation, gap reused, no loop)
# =============================================================================================


@pytest.mark.asyncio
async def test_8_same_gap_is_not_repeated_and_never_loops(conversation) -> None:
    repo, conv = conversation
    await repo.add(ALARM.knowledge())
    await _alt_completed(conv, ALARM)
    # The specialist ignores the instruction and asks for the same evidence again (twice).
    t2 = await _alarm_turn(conv, [_ungrounded(ALARM), _ungrounded(ALARM)])
    assert len(_continuation_requests(t2["tae_requests"])) == 1, "one continuation request, never a loop"
    rru = _rru_requirement(t2)
    (gap,) = t2["progression"].acquisition_gaps
    assert gap.requirement_id == rru.requirement_id and len(gap.discovery_attempts) == 1, "recorded once"
    assert "st rru" not in t2["final"] and not _authorized(t2), "nothing authorized"
    assert t2["final"].count(RRU_NEED) == 1, "the gap is stated once (no notice on top of the gap's own text)"

    # Next turn, nothing new: the same open gap is reused (no new discovery attempt) and not re-asked.
    before = len(_tae_requests())
    t3 = await conv.turn("what next?", [_same_need(), _same_need()], "Okay.")
    requests = _tae_requests()[before:]
    (same,) = t3["progression"].acquisition_gaps
    assert same.gap_id == gap.gap_id and len(same.discovery_attempts) == 1
    assert len(_continuation_requests(requests)) <= 1
    assert "already found to have no governed acquisition method" in t3["final"]
    assert "st rru" not in t3["final"]


# =============================================================================================
# 10. An ungrounded command stays blocked, also when proposed in the continuation
# =============================================================================================


@pytest.mark.asyncio
async def test_10_ungrounded_command_stays_blocked_in_the_continuation(conversation) -> None:
    repo, conv = conversation
    await repo.add(ALARM.knowledge())
    await _alt_completed(conv, ALARM)
    other = "Clock reference state"
    t2 = await _alarm_turn(conv, [_ungrounded(ALARM, command="st clock", need=other)])
    assert len(_continuation_requests(t2["tae_requests"])) == 1
    for command in ("st rru", "st clock"):
        assert command not in t2["final"] and command not in _authorized(t2)
    gaps = {g.requirement_description for g in t2["progression"].acquisition_gaps}
    assert gaps == {RRU_NEED, other}, "both method-less needs are recorded gaps"
    assert all(s.status is not StepStatus.PRESENTED or s.command for s in t2["progression"].steps), "no command-less operator task"
    assert NOT_CROSS_CHECKED in t2["final"] and "Knowledge status:" in t2["final"]
    assert "no operational command is provided for it" in t2["final"]


# =============================================================================================
# Unit: what counts as a governed method; the server texts stay generic
# =============================================================================================


def test_only_a_command_naming_a_governed_action_of_the_run_is_a_method() -> None:
    selected = [_evidence(RADIO)]
    assert names_governed_action("st ru", RADIO.filename, selected) and names_governed_action("ST  RU", None, selected)
    assert not names_governed_action("st rru", RADIO.filename, selected), "never fuzzy-matched to a governed action"
    assert not names_governed_action("st ru", RADIO.filename, []), "nothing selected: no governed action"
    assert not names_governed_action("st ru", RADIO.filename, [_evidence(RADIO, applicability="not_applicable")])
    blocked = [_evidence(RADIO, applicability="unknown")]
    assert names_governed_action("st ru", RADIO.identity.canonical, blocked), "applicability-blocked governed read is a method"
    # Not judged as ungrounded: composed commands (several actions) and state changes (remediation gate).
    assert _ungrounded_command("st rru", RADIO.filename, selected)
    assert not _ungrounded_command("st ru; st rru", RADIO.filename, selected)
    assert not _ungrounded_command("acc Unit=1 restartunit", RADIO.filename, selected)
    payloads = [{"diagnostic_step": {"command": "st rru", "command_source": RADIO.filename}}]
    assert proposes_method(payloads) and not proposes_method(payloads, lambda c, s: not _ungrounded_command(c, s, selected))


def test_server_labels_are_never_taken_from_synthesis() -> None:
    """A Team Manager copy of an earlier answer's labels (imitated from the conversation) is dropped; only
    THIS turn's record decides whether a gap notice and knowledge status are shown."""
    gap = {"gaps": [{"requirement_id": "r", "gap_id": "g", "reason": "x", "description": "Some component state"}]}
    copied = (
        "The requested evidence (Old need) was identified as a useful diagnostic direction, but no governed method ...\n\n"
        "Knowledge status: the command of the next diagnostic step comes from an approved governed procedure.\n\n"
        "Run the next check."
    )
    no_gap = with_acquisition_gap_notice(copied, {"outcome": "insufficient_evidence"})
    assert no_gap == "Run the next check."
    with_gap = with_acquisition_gap_notice(copied, {GAP_CONTINUATION_KEY: gap, "outcome": "insufficient_evidence"})
    assert with_gap.startswith("The requested evidence (Some component state)") and "Old need" not in with_gap
    assert with_gap.count("Knowledge status:") == 1 and "no operational command is provided for it" in with_gap
    assert with_gap.endswith("Run the next check.")
    assert with_acquisition_gap_notice(with_gap, {GAP_CONTINUATION_KEY: gap, "outcome": "insufficient_evidence"}) == with_gap


def test_server_texts_are_generic_and_label_model_reasoning() -> None:
    requirement = EvidenceRequirement(fault_id="F1", kind=EvidenceKind.DIAGNOSTIC_RESULT, description="Some component state")
    instruction = gap_continuation_instruction(requirement, "no_approved_acquisition_action", repeated=False)
    gap = {"gaps": [{"requirement_id": "r", "gap_id": "g", "reason": "x", "description": "Some component state"}]}
    governed = render_acquisition_gap_notice({GAP_CONTINUATION_KEY: gap, "outcome": "recommended",
                                              "diagnostic_step": {"command": "show x", "tests_hypothesis": "H holds."}})
    model_only = render_acquisition_gap_notice({GAP_CONTINUATION_KEY: gap, "outcome": "insufficient_evidence"})
    assert render_acquisition_gap_notice({"outcome": "recommended"}) is None
    assert "approved governed procedure and passed authorization" in governed and "Diagnostic hypothesis" in governed
    assert "no operational command is provided for it" in model_only and "approved governed procedure" not in model_only
    for text in (instruction, governed, model_only):
        assert not re.search(r"\b(?:ericsson|nokia|huawei|rru|radio|4g|5g|lte|nr|st ru)\b", text, re.IGNORECASE), text
        assert "yourself" not in text.casefold()
