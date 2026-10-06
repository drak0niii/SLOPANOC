"""Universal operational-command egress boundary.

Live defect (session c70e3d84, turn e-40206b0d, run 1ed56d64):
    "what is the command to restart an rru ? same ericsson / 4g"
    Team Manager: requires_governed_knowledge=True, no TAE call, a SAFE refusal
    forced governed-knowledge completion -> Incident Manager -> governed section ->
    "The command to restart an RRU ... is `accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1`."
    -> replaced the refusal; no ProcedureAction, Command Authority or synthesis boundary ran.

Invariant: NO operational command reaches user-visible text unless that exact command is backed by
a valid CURRENT-TURN authorization record (this run's TAE record: Command Authority authorized it,
the validated step presents it, the fault is the one in focus) -- whatever the producer, framing or
source. The literal commands below are regression fixtures only.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
from google.genai import types

from backend.api.chat_service import ChatService
from backend.api.command_egress import (
    NO_AUTHORIZED_COMMAND_TEXT,
    CommandAuthorization,
    StreamingEgressGate,
    current_turn_command_authorizations,
    enforce_command_egress,
    find_command_candidates,
    governed_command_strings,
)
from backend.api.session_service import ApiSessionService
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner
from backend.tests.test_applicability_blocked_governed_action import DOC, _trace
from backend.tests.test_applicability_blocked_governed_action import conversation  # noqa: F401 (fixture)
from backend.tests.test_clarification_continuity import _fc
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tools.knowledge.diagnostic_trace import RETRIEVAL_DIAGNOSTICS_STATE_KEY, format_diagnostic_trace

RESTART = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
LIVE_QUESTION = "what is the command to restart an rru ? same ericsson / 4g"
LIVE_REFUSAL = (
    "My capabilities are limited to providing read-only diagnostic steps and recommendations based on approved governed "
    "procedures. I cannot provide commands that restart or modify the state of network equipment like an RRU."
)
LIVE_UNSAFE = f"The command to restart an RRU (Remote Radio Unit) in an Ericsson 4G network is `{RESTART}`. Restart is allowed only on RRU."
SECTION = (
    "• Restart is allowed only on RRU.\nCommand:\n" + RESTART + "\n• If:\nSupportUnit=---\n→ No restart\n"
    "• For AAS units:\naccn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"
)


def _declare(governed: bool, teams: bool = False) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[
        FakeFunctionResponse("record_source_requirements", {"requires_teams": teams, "requires_governed_knowledge": governed})
    ])


async def _run(events: list[FakeEvent], message: str = LIVE_QUESTION, side_effect: Any = None) -> dict[str, Any]:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat = ChatService(service, runner=FakeRunner(service, events=events, side_effect=side_effect))
    deltas: list[str] = []
    completed: Optional[dict[str, Any]] = None
    async for event in chat.execute_turn_events(session_id, message, "api-user"):
        if event.type.value == "message.delta":
            deltas.append(event.data["text"])
        if event.type.value == "message.completed":
            completed = dict(event.data)
    session = await service.get_session(session_id, "api-user")
    state = dict(session.state)
    diagnostics = list((state.get(RETRIEVAL_DIAGNOSTICS_STATE_KEY) or {}).values())
    return {"final": (completed or {}).get("content") or "", "deltas": deltas, "state": state,
            "egress": (diagnostics[-1].get("command_egress") if diagnostics else None), "diagnostics": diagnostics}


def _no_command(text: str, *commands: str) -> None:
    for command in commands or (RESTART,):
        assert command not in text
        assert command.split()[0] not in text.split(), command
    assert "restartunit" not in text


# =============================================================================================
# 13. Exact live regression -- production-shaped: real forced completion + real Incident Manager
#     agent (callbacks, KM tools, compliance check) with only its model scripted.
# =============================================================================================


def _governed_doc() -> Any:
    from backend.knowledge.domain.applicability import Applicability
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion

    return KnowledgeObject(
        knowledge_id="EGRESS-RESTART-MOP", document_type=KnowledgeDocumentType.MOP, title="Alarm Handling and Restart Procedure",
        version=KnowledgeVersion(label="v2", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions={"vendor": ["ericsson"], "technology": ["4g"]}),
        source=KnowledgeSource(source_system="test", source_id="Document1.docx", display_name="Document1.docx"),
        sections=[KnowledgeSection(section_id="EGRESS-RESTART-MOP:v2:section-0001", knowledge_id="EGRESS-RESTART-MOP",
                                   heading="HW Partial Fault", sequence=1, content=SECTION, source_locator="lines:20-27")],
    )


@pytest.mark.asyncio
async def test_exact_live_regression_forced_completion_never_surfaces_the_restart_command(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    import backend.agents.incident_manager.agent as im_agent
    from backend.tests.test_clarification_continuity import _ScriptedLlm

    await isolated_km_repo.add(_governed_doc())
    key = {"knowledge_id": "EGRESS-RESTART-MOP", "version_label": "v2", "section_id": "EGRESS-RESTART-MOP:v2:section-0001"}
    scripted = _ScriptedLlm(model="scripted-im", parts_by_call=[
        _fc("knowledge_search", {"query_text": "restart RRU command"}),
        _fc("knowledge_select_evidence", {"selections": [key]}),
        [types.Part.from_text(text=json.dumps({"outcome": "ok", "summary": LIVE_UNSAFE}))],
    ])
    monkeypatch.setattr(im_agent, "incident_manager", im_agent.incident_manager.model_copy(update={"model": scripted}))

    result = await _run([_declare(governed=True), FakeEvent(text=LIVE_REFUSAL, final=True)])
    assert scripted._call_count == 3, "the forced completion really ran the Incident Manager agent"
    _no_command(result["final"])
    assert "Restart is allowed only on RRU." in result["final"], "governed facts may enrich the answer"
    assert NO_AUTHORIZED_COMMAND_TEXT in result["final"], "the refusal is never replaced by a command"
    assert all("restartunit" not in d for d in result["deltas"])
    # The persisted (history / session view) answer is the sanitized one.
    assert "restartunit" not in json.dumps(result["state"].get("turn_final_answers"))
    egress = result["egress"]
    assert egress["producer"] == "governed_completion+incident_manager" and egress["decision"] == "sanitized"
    assert {(c["candidate"], c["decision"], c["reason"]) for c in egress["candidates"]} >= {(RESTART, "removed", "no_current_turn_authorization")}
    assert egress["authorizations"] == []
    assert "EGRESS producer=governed_completion+incident_manager decision=sanitized" in format_diagnostic_trace(result["diagnostics"][-1])


# =============================================================================================
# C. Forced completion leak (safe refusal + completion carrying a command)
# =============================================================================================


@pytest.mark.asyncio
async def test_c_forced_completion_cannot_replace_a_refusal_with_a_command(monkeypatch) -> None:
    async def fake_remediation(**_: Any) -> tuple[str, list[Any]]:
        return LIVE_UNSAFE, []

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_remediation)
    result = await _run([_declare(governed=True), FakeEvent(text=LIVE_REFUSAL, final=True)])
    _no_command(result["final"])
    assert result["final"].endswith(NO_AUTHORIZED_COMMAND_TEXT)


# =============================================================================================
# A / B / E / F / K. Incident Manager, Team Manager direct, example / syntax framing, plain prose
# =============================================================================================


@pytest.mark.asyncio
async def test_a_incident_manager_command_never_reaches_the_answer() -> None:
    im_result = {"outcome": "ok", "summary": f"The procedure says to run `{RESTART}` on the unit.", "chat_title": "Ops"}
    result = await _run([
        _declare(governed=False, teams=True),
        FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse("incident_manager", im_result)]),
        FakeEvent(text=f"Alex confirmed the radio is down. The chat says to run `{RESTART}`.", final=True),
    ], message="summarize the ops chat")
    _no_command(result["final"])
    assert "Alex confirmed the radio is down." in result["final"]


@pytest.mark.asyncio
async def test_b_team_manager_direct_command_is_blocked_including_live_stream() -> None:
    result = await _run([
        _declare(governed=False),
        FakeEvent(text="To bounce the radio, ", final=False, partial=True),
        FakeEvent(text="run `accn FieldReplace", final=False, partial=True),
        FakeEvent(text="ableUnit=RRU-2 restartunit 1 1 1`. ", final=False, partial=True),
        FakeEvent(text="Then check the alarms.", final=False, partial=True),
        FakeEvent(text="To bounce the radio, run `accn FieldReplaceableUnit=RRU-2 restartunit 1 1 1`. Then check the alarms.", final=True),
    ], message="give me the cmd to bounce radio 2")
    streamed = "".join(result["deltas"])
    assert "restartunit" not in streamed and "accn" not in streamed and "FieldReplace" not in streamed
    _no_command(result["final"], "accn FieldReplaceableUnit=RRU-2 restartunit 1 1 1")
    assert result["egress"]["producer"] == "team_manager"


@pytest.mark.parametrize("framing", [
    f"For example, run {RESTART}.",
    f"For example, run `{RESTART}`.",
    f"Example syntax: {RESTART}",
    f"Syntax: {RESTART}",
    f"A typical command is {RESTART}.",
    f"You can use `{RESTART}` for that.",
    f"For reference, the command used is {RESTART}.",
    f"```\n{RESTART}\n```",
])
@pytest.mark.asyncio
async def test_e_f_example_and_syntax_framing_is_not_an_authority_class(framing: str) -> None:
    result = await _run([_declare(governed=False), FakeEvent(text=framing, final=True)], message="give me an example")
    _no_command(result["final"])
    assert result["final"] == NO_AUTHORIZED_COMMAND_TEXT or NO_AUTHORIZED_COMMAND_TEXT in result["final"]


@pytest.mark.asyncio
async def test_k_non_command_technical_prose_is_untouched() -> None:
    prose = (
        "The governed procedure contains a restart step. A restart action exists in the selected procedure.\n\n"
        "I cannot provide an executable command because no command has passed the current authorization boundary. "
        "RadioUnit=2 is DISABLED and its link shows High BER on `Equipment=1,RadioUnit=2`."
    )
    result = await _run([_declare(governed=False), FakeEvent(text=prose, final=True)], message="what does the procedure say?")
    assert result["final"] == prose
    assert result["egress"] is None, "no candidate, no decision recorded"


# =============================================================================================
# D. Troubleshooting-guidance renderer
# =============================================================================================


@pytest.mark.asyncio
async def test_d_troubleshooting_guidance_renderer_command_is_blocked() -> None:
    from backend.agents.incident_manager.schemas import TroubleshootingGuidance
    from backend.api.troubleshooting_guidance_context import register_troubleshooting_guidance
    from backend.api.turn_context import current_run_id

    async def side_effect(_service: Any, _session: Any, _text: str) -> None:
        register_troubleshooting_guidance(current_run_id(), TroubleshootingGuidance.model_validate({
            "interaction_mode": "next_step", "interpretation": "The radio unit restart is the documented recovery.",
            "next_action": "Restart the faulty radio unit.", "command": RESTART, "evidence_requested": "Alarm list after restart",
        }))

    result = await _run([_declare(governed=True), FakeEvent(text="ignored", final=True)], side_effect=side_effect)
    _no_command(result["final"])
    assert result["egress"]["producer"] == "troubleshooting_guidance"


# =============================================================================================
# G / H. Valid current-turn TAE command survives; historical authorization does not
# =============================================================================================


@pytest.mark.asyncio
async def test_g_h_current_tae_authorization_survives_historical_one_does_not(conversation) -> None:  # noqa: F811
    from backend.tests.test_clarification_retrieval_resumption import ALT_ID
    from backend.tests.test_applicability_blocked_governed_action import CATALOG, SEARCH, _governed

    repo, conv = conversation
    await repo.add(DOC.knowledge())
    tm_args = {"problem_statement": "ESS Service Unavailable", "known_applicability_facts": {"vendor": ["ericsson"], "technology": ["4g"]}}
    t1 = await conv.turn("how can i troubleshoot ESS Service Unavailable on ericsson 4g?",
                         [SEARCH, DOC.select(), CATALOG, _governed(ALT_ID, "Check active alarms on the node.")],
                         "**Command:** `alt`\n\nPlease run this command and provide the output.", tae_args=tm_args)
    assert "`alt`" in t1["final"], "G: the current-turn TAE authorization renders"
    (egress,) = [d["command_egress"] for d in [list((t1["state"].get(RETRIEVAL_DIAGNOSTICS_STATE_KEY) or {}).values())[-1]]]
    assert [(c["candidate"], c["decision"], c["procedure_action_id"]) for c in egress["candidates"]] == [("alt", "kept", ALT_ID)]
    assert egress["authorizations"][0]["run_id"] == _trace(t1)["run_id"]

    # H: next turn, no TAE run -- the command authorized in the previous run cannot be repeated.
    t2 = await conv.turn("ok, and again?", [], "Run `alt` again and paste the output.", delegate=False, requires_governed_knowledge=False)
    assert "alt" not in t2["final"].replace(NO_AUTHORIZED_COMMAND_TEXT, "").split() and "`alt`" not in t2["final"]
    assert NO_AUTHORIZED_COMMAND_TEXT in t2["final"]


# =============================================================================================
# I / J / authority record unit proofs
# =============================================================================================


def _record(command: Optional[str] = "alt", *, catalog: Optional[list[dict[str, Any]]] = None, run_id: str = "run-now",
            fault_id: str = "FAULT-A") -> dict[str, Any]:
    return {
        "run_id": run_id, "fault_id": fault_id,
        "diagnostic_step": {"command": command, "command_source": "K:v1:S", "procedure_action_id": "pa-1"},
        "approved_commands_catalog": catalog if catalog is not None else [{"command": "alt", "source_id": "K:v1:S", "authorization_decision": "authorized"}],
    }


def test_i_authorization_for_another_fault_or_run_never_satisfies_the_boundary() -> None:
    valid, rejected = current_turn_command_authorizations("run-now", _record(), active_fault_id="FAULT-A")
    assert [(a.command, a.procedure_action_id, a.run_id) for a in valid] == [("alt", "pa-1", "run-now")] and rejected == []
    valid, rejected = current_turn_command_authorizations("run-now", _record(), active_fault_id="FAULT-B")
    assert valid == [] and rejected[0]["reason"] == "different_fault"
    valid, rejected = current_turn_command_authorizations("run-now", _record(run_id="run-old"), active_fault_id="FAULT-A")
    assert valid == [] and rejected[0]["reason"] == "different_run"
    out = enforce_command_egress("Run `alt`.", authorizations=valid, rejected_authorizations=rejected, producer="t", run_id="run-now")
    assert "alt" not in out.text.replace(NO_AUTHORIZED_COMMAND_TEXT, "").split() and out.rejected_authorizations[0]["reason"] == "different_run"


def test_no_auto_authorization_from_catalog_text_or_approval() -> None:
    # A step command Command Authority did not authorize this run (absent from the approved catalog).
    assert current_turn_command_authorizations("r", _record(catalog=[]))[0] == []
    # Approval-required / awaiting-confirmation actions carry no presented command: nothing to authorize.
    assert current_turn_command_authorizations("r", _record(command=None))[0] == []
    # A catalog entry alone (not presented by the validated step) does not authorize.
    assert current_turn_command_authorizations("r", {**_record(command=None), "approved_commands_catalog": [{"command": RESTART}]})[0] == []
    # Governed text never authorizes: the command is in the corpus, not in any authorization.
    out = enforce_command_egress(f"Use {RESTART} now.", producer="t", run_id="r", corpus=governed_command_strings([SECTION]))
    _no_command(out.text)


def test_j_one_authorized_and_one_unauthorized_command() -> None:
    alt = CommandAuthorization("alt", "alt", "run-now", "pa-1", "K:v1:S", "FAULT-A")
    for text in ("Run `alt`, then run `st ru`.", "Run `alt` to list alarms. Then run `st ru` for the radios."):
        out = enforce_command_egress(text, authorizations=[alt], producer="t", run_id="run-now")
        assert "`alt`" in out.text and "st ru" not in out.text
        assert {(d["candidate"], d["decision"]) for d in out.decisions} == {("alt", "kept"), ("st ru", "removed")}
        assert NO_AUTHORIZED_COMMAND_TEXT not in out.text, "an authorized command remains: no blanket refusal"


@pytest.mark.parametrize("text", [
    "what command should I run?", "how would I restart it?", "The command cannot be provided right now.",
    "No command is provided, and none is needed from you: this has been recorded as a governed acquisition gap.",
    "The governed procedure is available, but no command for this diagnostic step has passed the current grounding and authorization checks.",
    "Inspect the CPRI/eCPRI optical connection for RadioUnit=2.", "Restart the identified RRU after 30 minutes.",
])
def test_server_and_explanatory_texts_contain_no_command_candidates(text: str) -> None:
    assert find_command_candidates(text) == []


def test_governed_corpus_catches_unformatted_copies_and_streaming_gate_never_emits_a_command() -> None:
    corpus = governed_command_strings([SECTION])
    assert RESTART in corpus and "accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1" in corpus
    assert find_command_candidates(f"Operators usually go with {RESTART} on such units", corpus) == [RESTART]
    gate = StreamingEgressGate()
    chunks = ["Here is what to do. ", "Use ", "accn FieldReplaceableUnit", "=RRU-9 restartunit 1 1 1 ", "on the node."]
    streamed = "".join(filter(None, (gate.accept(c) for c in chunks)))
    assert streamed == "Here is what to do. " and gate.stopped
    plain = StreamingEgressGate()
    assert "".join(filter(None, (plain.accept(c) for c in ["The radio ", "is down. ", "Check the fiber."]))) == "The radio is down. Check the fiber."
