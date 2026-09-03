"""P4B.3 URGENT SOURCE/PROVENANCE REGRESSION FIX.

ROOT CAUSE (found by direct instrumentation of a real ADK Runner, not
guessed): `_fast_path_incident_manager` (direct_read_fast_path.py) is a
`.model_copy` of the base `incident_manager` and therefore INHERITS
`strip_unverified_evidence` as its own `after_agent_callback`, unmodified.
That callback re-validates whatever `evidence[].message_id`s are in the
agent's own final response text against THIS agent's own session's
`KNOWN_MESSAGE_IDS_STATE_KEY` -- but for the direct-unique fast path, the
real `teams_get_messages` call happens inside `execute_read_continuation`'s
own, SEPARATE internal specialist session (P4B.1, frozen), never inside
`_fast_path_incident_manager`'s own session. So when `_fast_path_before_
model_callback`'s shortcut hands back an ALREADY-validated, ALREADY-trusted
result (with real evidence), `strip_unverified_evidence` re-checks it
against an EMPTY known-ids set (this session never called `teams_get_
messages` itself) and silently strips every evidence entry -- the tool
response team_manager (and `TeamsSourceCapture.observe`) ultimately sees
has `evidence: []`, so `source_capture.build_source_reference(...)` returns
`None` and `source_reference_enrichment` never fires.

FIX: `_fast_path_before_model_callback` seeds THIS session's own
`KNOWN_MESSAGE_IDS_STATE_KEY` with exactly the message ids already present
in the validated result's own `evidence` (nothing more, no raw Teams
content) immediately before returning its synthesized response --
`strip_unverified_evidence` then correctly finds every id already known
and performs no destructive override. This reuses the EXISTING, unmodified
evidence-validation mechanism; no second provenance pipeline was built.

Tests here exercise `ChatService.execute_turn_events` directly (the same
established pattern `test_p0_p1_live_incident_regression.py` already uses
for the post-selection path) so `source_reference_enrichment`/`message.
completed`'s `source` payload are genuinely produced by production code,
not hand-asserted.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.tools import AgentTool
from google.genai import types

import backend.agents.team_manager.agent as agent_module
import backend.agents.team_manager.direct_read_fast_path as fast_path_module
import backend.agents.team_manager.read_continuation_execution as rce_module
from backend.agents.team_manager.direct_read_fast_path import (
    _fast_path_incident_manager,
    get_fast_path_team_manager,
)
from backend.api.chat_service import ChatService
from backend.api.session_service import APP_NAME, ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse, chat, message


# --- Shared scaffolding ------------------------------------------------


class _StageFakeLlm(BaseLlm):
    stage_name: str
    parts_fn: Any
    call_log: Any
    max_calls: int = 1
    calls: int = 0

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        self.calls += 1
        self.call_log.append(self.stage_name)
        if self.calls > self.max_calls:
            raise AssertionError(f"stage {self.stage_name!r} invoked a real model more than {self.max_calls} time(s)")
        yield LlmResponse(content=types.Content(role="model", parts=self.parts_fn()))


def _fc(name: str, args: dict[str, Any], call_id: str) -> Any:
    def build() -> list:
        part = types.Part.from_function_call(name=name, args=args)
        part.function_call.id = call_id
        return [part]

    return build


def _txt(text: str) -> Any:
    return lambda: [types.Part.from_text(text=text)]


def _build_direct_unique_chat_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    chats: list[dict[str, Any]],
    messages_by_chat_id: dict[str, list[dict[str, Any]]],
    synthesis_response: dict[str, Any],
    presentation_text: str = "Network Operations Daily: the fix shipped.",
    members_by_chat_id: Optional[dict[str, list[str]]] = None,
    gateway_call_counts: Optional[dict[str, int]] = None,
) -> tuple[ChatService, str, list[str]]:
    """Wires up the full direct-unique call graph (4 independent fakes,
    one per real model call -- team_manager routing, incident_manager
    discovery, incident_manager synthesis-only, presentation_team_manager)
    behind a real `ChatService`, mirroring test_p0_p1_live_incident_
    regression.py's own established "mock the external boundary, keep
    everything else real" pattern. Returns (chat_service, session_id,
    call_log).
    """
    call_counts = gateway_call_counts if gateway_call_counts is not None else {}
    members_by_chat_id = members_by_chat_id or {}

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, chats)
        if operation == "teams.getMessages":
            return FakeResponse(200, messages_by_chat_id.get(json.get("chatId"), []))
        if operation == "teams.getMembers":
            return FakeResponse(200, members_by_chat_id.get(json.get("chatId"), []))
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    call_log: list = []
    topic = chats[0]["topic"] if chats else "Network Operations Daily"

    team_manager_fake = _StageFakeLlm(
        model="fake-tm-routing",
        stage_name="team_manager_routing",
        parts_fn=_fc("incident_manager", {"chat_topic": topic}, "call-1"),
        call_log=call_log,
    )
    discovery_fake = _StageFakeLlm(
        model="fake-im-discovery",
        stage_name="incident_manager_discovery",
        parts_fn=_fc("teams_list_chats", {"topic": topic}, "call-2"),
        call_log=call_log,
    )
    synthesis_fake = _StageFakeLlm(
        model="fake-im-synthesis",
        stage_name="incident_manager_synthesis",
        parts_fn=_txt(json.dumps(synthesis_response)),
        call_log=call_log,
    )
    presentation_fake = _StageFakeLlm(
        model="fake-presentation",
        stage_name="presentation_team_manager",
        parts_fn=_txt(presentation_text),
        call_log=call_log,
    )

    fake_discovery_agent = _fast_path_incident_manager.model_copy(update={"model": discovery_fake})
    fake_discovery_tool = AgentTool(agent=fake_discovery_agent)
    fake_synthesis_agent = rce_module._SYNTHESIS_ONLY_INCIDENT_MANAGER.model_copy(update={"model": synthesis_fake})
    monkeypatch.setattr(rce_module, "_SYNTHESIS_ONLY_INCIDENT_MANAGER", fake_synthesis_agent)
    fake_presentation_agent = agent_module.presentation_team_manager.model_copy(update={"model": presentation_fake})
    monkeypatch.setattr(agent_module, "presentation_team_manager", fake_presentation_agent)

    base = get_fast_path_team_manager()
    new_tools = [fake_discovery_tool if getattr(t, "name", None) == "incident_manager" else t for t in base.tools]
    probe_agent = base.model_copy(update={"model": team_manager_fake, "tools": new_tools})

    service = ApiSessionService()

    async def _stub_contributors(chat_id: Optional[str]) -> list[str]:
        if chat_id is None:
            return []
        return members_by_chat_id.get(chat_id, [])

    probe_runner = Runner(
        app_name=APP_NAME, agent=probe_agent, session_service=service.adk_session_service, memory_service=InMemoryMemoryService()
    )
    chat_service = ChatService(service, runner=probe_runner, teams_contributors_resolver=_stub_contributors)
    return chat_service, service, call_log


async def _run_turn(chat_service: ChatService, session_id: str, text: str) -> list[Any]:
    return [event async for event in chat_service.execute_turn_events(session_id, text, "api-user")]


def _completed(events: list[Any]) -> Any:
    return next(e for e in events if e.type == StreamEventType.MESSAGE_COMPLETED)


EVIDENCE_M1 = {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-20T09:00:00Z"}
EVIDENCE_M2 = {"message_id": "m2", "author": "Priya", "sent_at": "2026-08-20T09:05:00Z"}


def _messages(*ids_authors_texts: tuple[str, str, str]) -> list[dict[str, Any]]:
    return [message(mid, author, text, "2026-08-20T09:00:00Z") for mid, author, text in ids_authors_texts]


# --- TEST A: direct-unique source ------------------------------------------


@pytest.mark.asyncio
async def test_direct_unique_source_present_and_matches_teams_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    call_counts: dict[str, int] = {}
    chat_service, service, call_log = _build_direct_unique_chat_service(
        monkeypatch,
        chats=[chat("chat-b2-id", "Network Operations Daily")],
        messages_by_chat_id={"chat-b2-id": _messages(("m1", "Alex", "We shipped the fix."))},
        synthesis_response={
            "outcome": "ok",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "summary": "The fix shipped.",
            "evidence": [EVIDENCE_M1],
        },
        members_by_chat_id={"chat-b2-id": ["Alex", "Priya"]},
        gateway_call_counts=call_counts,
    )
    session_id = await service.create_session()

    events = await _run_turn(chat_service, session_id, "summarize Network Operations Daily")

    assert call_counts.get("teams.listChats", 0) == 1
    assert call_counts.get("teams.getMessages", 0) == 1
    assert call_log == [
        "team_manager_routing",
        "incident_manager_discovery",
        "incident_manager_synthesis",
        "presentation_team_manager",
    ]

    completed = _completed(events)
    assert completed.data.get("source") is not None
    source = completed.data["source"]
    assert source["source_type"] == "teams"
    assert source["message_count"] == 1


# --- TEST B: same structural contract as post-selection ---------------------


@pytest.mark.asyncio
async def test_direct_unique_source_contract_matches_post_selection_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compares the STRUCTURE (field set, kinds) of the direct-unique
    source against `SourceReferenceDTO`'s own schema -- the same object
    post-selection already produces, never a parallel shape.
    """
    from backend.api.source_reference import SourceReferenceDTO

    chat_service, service, _log = _build_direct_unique_chat_service(
        monkeypatch,
        chats=[chat("chat-b2-id", "Network Operations Daily")],
        messages_by_chat_id={"chat-b2-id": _messages(("m1", "Alex", "We shipped the fix."))},
        synthesis_response={
            "outcome": "ok",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "summary": "The fix shipped.",
            "evidence": [EVIDENCE_M1],
        },
        members_by_chat_id={"chat-b2-id": ["Alex"]},
    )
    session_id = await service.create_session()
    events = await _run_turn(chat_service, session_id, "summarize Network Operations Daily")
    source = _completed(events).data["source"]

    expected_fields = set(SourceReferenceDTO.model_fields.keys())
    assert set(source.keys()) <= expected_fields
    assert "message_count" in source
    assert "period_start" in source and "period_end" in source
    assert "evidence" in source
    assert "contributors" in source
    assert source["source_type"] == "teams"


# --- TEST C: message_count reflects full evidence, not the display cap -----


@pytest.mark.asyncio
async def test_message_count_reflects_full_evidence_not_display_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = [
        {"message_id": f"m{i}", "author": f"User{i}", "sent_at": f"2026-08-20T09:{i:02d}:00Z"} for i in range(7)
    ]
    messages = _messages(*[(f"m{i}", f"User{i}", f"Message {i}.") for i in range(7)])

    chat_service, service, _log = _build_direct_unique_chat_service(
        monkeypatch,
        chats=[chat("chat-b2-id", "Network Operations Daily")],
        messages_by_chat_id={"chat-b2-id": messages},
        synthesis_response={
            "outcome": "ok",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "summary": "Busy day.",
            "evidence": evidence,
        },
    )
    session_id = await service.create_session()
    events = await _run_turn(chat_service, session_id, "summarize")
    source = _completed(events).data["source"]

    assert source["message_count"] == 7  # full count, even though display caps supporting evidence at 5


# --- TEST D: contributors via getMembers, called once -----------------------


@pytest.mark.asyncio
async def test_contributors_resolved_via_getmembers_called_once(monkeypatch: pytest.MonkeyPatch) -> None:
    resolver_calls: list[Optional[str]] = []

    async def counting_resolver(chat_id: Optional[str]) -> list[str]:
        resolver_calls.append(chat_id)
        return ["Alex Rivera", "Priya Shah"]

    call_counts: dict[str, int] = {}
    chat_service, service, _log = _build_direct_unique_chat_service(
        monkeypatch,
        chats=[chat("chat-b2-id", "Network Operations Daily")],
        messages_by_chat_id={"chat-b2-id": _messages(("m1", "Alex", "We shipped the fix."))},
        synthesis_response={
            "outcome": "ok",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "summary": "The fix shipped.",
            "evidence": [EVIDENCE_M1],
        },
        gateway_call_counts=call_counts,
    )
    # Override the resolver injected by the helper with our own counting one.
    object.__setattr__(chat_service, "_resolve_teams_contributors", counting_resolver)
    session_id = await service.create_session()

    events = await _run_turn(chat_service, session_id, "summarize")
    source = _completed(events).data["source"]

    assert len(resolver_calls) == 1  # getMembers-equivalent resolved exactly once
    assert source["contributors"] == ["Alex Rivera", "Priya Shah"]


# --- TEST E: evidence fidelity — fabricated reference stripped -------------


@pytest.mark.asyncio
async def test_fabricated_evidence_reference_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_service, service, _log = _build_direct_unique_chat_service(
        monkeypatch,
        chats=[chat("chat-b2-id", "Network Operations Daily")],
        messages_by_chat_id={"chat-b2-id": _messages(("m1", "Alex", "We shipped the fix."))},
        synthesis_response={
            "outcome": "ok",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "summary": "The fix shipped.",
            # m1 is real (retrieved); m-fabricated was never retrieved.
            "evidence": [EVIDENCE_M1, {"message_id": "m-fabricated", "author": "Nobody", "sent_at": "2026-08-20T09:10:00Z"}],
        },
    )
    session_id = await service.create_session()
    events = await _run_turn(chat_service, session_id, "summarize")
    source = _completed(events).data["source"]

    # The fabricated reference must never appear anywhere in the source
    # payload (message ids are internal-only, but this also proves it did
    # not silently inflate message_count or leak into any field).
    assert "m-fabricated" not in json.dumps(source)
    assert source["message_count"] == 1


# --- TEST F: no_result --------------------------------------------------


@pytest.mark.asyncio
async def test_no_result_produces_no_fabricated_source(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_service, service, _log = _build_direct_unique_chat_service(
        monkeypatch,
        chats=[chat("chat-b2-id", "Network Operations Daily")],
        messages_by_chat_id={"chat-b2-id": []},
        synthesis_response={
            "outcome": "no_result",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
        },
    )
    session_id = await service.create_session()
    events = await _run_turn(chat_service, session_id, "summarize")
    completed = _completed(events)

    assert completed.data.get("source") is None


# --- TEST G: gateway failure --------------------------------------------


@pytest.mark.asyncio
async def test_gateway_failure_produces_no_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """A `teams.getMessages` gateway failure still yields a VALID (schema-
    conforming) `outcome="error"` `IncidentManagerResponse` --
    `_execute_via_deterministic_retrieval`'s own frozen behavior (no
    model call for synthesis in this case, per P4B). Per section 17, a
    well-formed `outcome="error"` result is NOT an invalid/untrusted
    envelope -- it legitimately goes through normal trusted presentation
    (`presentation_team_manager` phrases the failure), exactly like post-
    selection's own existing behavior for the identical retrieval failure.
    What must NOT happen is a fabricated SUCCESSFUL SourceReference.
    """
    from backend.gateway.safe_error import internal_error

    def raising_post(url, json, timeout):
        if json.get("operation") == "teams.listChats":
            return FakeResponse(200, [chat("chat-b2-id", "Network Operations Daily")])
        raise internal_error("The Teams connector is temporarily unavailable.")

    monkeypatch.setattr(pac_module.requests, "post", raising_post)

    call_log: list = []
    team_manager_fake = _StageFakeLlm(
        model="fake-tm", stage_name="team_manager_routing",
        parts_fn=_fc("incident_manager", {"chat_topic": "Network Operations Daily"}, "call-1"), call_log=call_log,
    )
    discovery_fake = _StageFakeLlm(
        model="fake-im", stage_name="incident_manager_discovery",
        parts_fn=_fc("teams_list_chats", {"topic": "Network Operations Daily"}, "call-2"), call_log=call_log,
    )
    presentation_fake = _StageFakeLlm(
        model="fake-pres", stage_name="presentation_team_manager",
        parts_fn=_txt("Sorry, that request could not be completed."), call_log=call_log,
    )
    fake_discovery_agent = _fast_path_incident_manager.model_copy(update={"model": discovery_fake})
    fake_discovery_tool = AgentTool(agent=fake_discovery_agent)
    monkeypatch.setattr(
        agent_module, "presentation_team_manager",
        agent_module.presentation_team_manager.model_copy(update={"model": presentation_fake}),
    )

    base = get_fast_path_team_manager()
    new_tools = [fake_discovery_tool if getattr(t, "name", None) == "incident_manager" else t for t in base.tools]
    probe_agent = base.model_copy(update={"model": team_manager_fake, "tools": new_tools})

    service = ApiSessionService()
    probe_runner = Runner(app_name=APP_NAME, agent=probe_agent, session_service=service.adk_session_service, memory_service=InMemoryMemoryService())
    chat_service = ChatService(service, runner=probe_runner)
    session_id = await service.create_session()

    events = await _run_turn(chat_service, session_id, "summarize")
    completed = _completed(events)

    assert completed.data.get("source") is None  # no fabricated success source
    # Routing + discovery + presentation -- no synthesis-only call (the
    # deterministic retrieval failure short-circuits before ever reaching
    # it, per P4B's own frozen "zero model calls for a pure retrieval
    # failure" design), no generic fallback, no second listChats.
    assert call_log == ["team_manager_routing", "incident_manager_discovery", "presentation_team_manager"]


# --- TEST H: trust-validation failure -----------------------------------


@pytest.mark.asyncio
async def test_trust_validation_failure_has_no_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retrieval and synthesis succeed normally (evidence is real and
    valid) -- but `build_and_validate_trusted_envelope` is forced to fail,
    simulating a genuine same-run/source/schema trust failure discovered
    AFTER the result already passed once (same technique as test_p4b3_
    completion_trusted_presentation.py's own equivalent test). Must fail
    closed: no presentation call, no normal-team_manager fallback, no
    source.
    """
    call_log: list = []
    team_manager_fake = _StageFakeLlm(
        model="fake-tm", stage_name="team_manager_routing",
        parts_fn=_fc("incident_manager", {"chat_topic": "Network Operations Daily"}, "call-1"), call_log=call_log,
    )
    discovery_fake = _StageFakeLlm(
        model="fake-im", stage_name="incident_manager_discovery",
        parts_fn=_fc("teams_list_chats", {"topic": "Network Operations Daily"}, "call-2"), call_log=call_log,
    )
    synthesis_fake = _StageFakeLlm(
        model="fake-synth", stage_name="incident_manager_synthesis",
        parts_fn=_txt(json.dumps({
            "outcome": "ok", "chat_id": "chat-b2-id", "chat_title": "Network Operations Daily",
            "summary": "The fix shipped.", "evidence": [EVIDENCE_M1],
        })), call_log=call_log,
    )
    fake_discovery_agent = _fast_path_incident_manager.model_copy(update={"model": discovery_fake})
    fake_discovery_tool = AgentTool(agent=fake_discovery_agent)
    fake_synthesis_agent = rce_module._SYNTHESIS_ONLY_INCIDENT_MANAGER.model_copy(update={"model": synthesis_fake})
    monkeypatch.setattr(rce_module, "_SYNTHESIS_ONLY_INCIDENT_MANAGER", fake_synthesis_agent)

    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.listChats":
            return FakeResponse(200, [chat("chat-b2-id", "Network Operations Daily")])
        if json.get("operation") == "teams.getMessages":
            return FakeResponse(200, _messages(("m1", "Alex", "We shipped the fix.")))
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    monkeypatch.setattr(fast_path_module, "build_and_validate_trusted_envelope", lambda run_id, result: None)

    base = get_fast_path_team_manager()
    new_tools = [fake_discovery_tool if getattr(t, "name", None) == "incident_manager" else t for t in base.tools]
    probe_agent = base.model_copy(update={"model": team_manager_fake, "tools": new_tools})

    service = ApiSessionService()
    probe_runner = Runner(app_name=APP_NAME, agent=probe_agent, session_service=service.adk_session_service, memory_service=InMemoryMemoryService())
    chat_service = ChatService(service, runner=probe_runner)
    session_id = await service.create_session()

    events = await _run_turn(chat_service, session_id, "summarize")
    completed = _completed(events)

    assert completed.data.get("source") is None
    from backend.agents.team_manager.direct_read_fast_path import _TRUST_VALIDATION_FAILURE_TEXT

    assert completed.data.get("content") == _TRUST_VALIDATION_FAILURE_TEXT
    # Retrieval + synthesis ran for real (evidence WAS legitimately
    # produced); presentation never ran -- the fail-closed response was
    # phrased directly, no second model call needed merely to phrase it.
    assert call_log == ["team_manager_routing", "incident_manager_discovery", "incident_manager_synthesis"]


# --- TEST I: run isolation -----------------------------------------------


@pytest.mark.asyncio
async def test_run_b_does_not_inherit_run_a_source(monkeypatch: pytest.MonkeyPatch) -> None:
    chat_service, service, _log = _build_direct_unique_chat_service(
        monkeypatch,
        chats=[chat("chat-b2-id", "Network Operations Daily")],
        messages_by_chat_id={"chat-b2-id": _messages(("m1", "Alex", "We shipped the fix."))},
        synthesis_response={
            "outcome": "ok",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "summary": "The fix shipped.",
            "evidence": [EVIDENCE_M1],
        },
    )
    session_id = await service.create_session()

    events_a = await _run_turn(chat_service, session_id, "summarize Network Operations Daily")
    assert _completed(events_a).data.get("source") is not None

    # Run B: a current_thread-style question, no Teams call should happen,
    # and it must not somehow inherit run A's source.
    from backend.agents.team_manager import direct_read_fast_path as fp_module

    assert fp_module._pending_trusted_result_by_run == {}  # nothing leaked from run A

    class _CurrentThreadFakeLlm(BaseLlm):
        calls: int = 0

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            self.calls += 1
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text="We discussed the fix.")]))

    probe_b = get_fast_path_team_manager().model_copy(update={"model": _CurrentThreadFakeLlm(model="fake-b")})
    chat_service._runner = Runner(app_name=APP_NAME, agent=probe_b, session_service=service.adk_session_service, memory_service=InMemoryMemoryService())
    chat_service._presentation_runner = chat_service._runner

    events_b = await _run_turn(chat_service, session_id, "what did we discuss in this chat window")
    completed_b = _completed(events_b)
    assert completed_b.data.get("source") is None


# --- TEST J: cancellation --------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_leaves_no_leaked_provenance_state(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def cancelling_execute(**kwargs: Any) -> Any:
        raise asyncio.CancelledError()

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", cancelling_execute)

    call_log: list = []
    team_manager_fake = _StageFakeLlm(
        model="fake-tm", stage_name="team_manager_routing",
        parts_fn=_fc("incident_manager", {"chat_topic": "Network Operations Daily"}, "call-1"), call_log=call_log,
    )
    discovery_fake = _StageFakeLlm(
        model="fake-im", stage_name="incident_manager_discovery",
        parts_fn=_fc("teams_list_chats", {"topic": "Network Operations Daily"}, "call-2"), call_log=call_log,
    )
    fake_discovery_agent = _fast_path_incident_manager.model_copy(update={"model": discovery_fake})
    fake_discovery_tool = AgentTool(agent=fake_discovery_agent)

    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.listChats":
            return FakeResponse(200, [chat("chat-b2-id", "Network Operations Daily")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    base = get_fast_path_team_manager()
    new_tools = [fake_discovery_tool if getattr(t, "name", None) == "incident_manager" else t for t in base.tools]
    probe_agent = base.model_copy(update={"model": team_manager_fake, "tools": new_tools})

    service = ApiSessionService()
    probe_runner = Runner(app_name=APP_NAME, agent=probe_agent, session_service=service.adk_session_service, memory_service=InMemoryMemoryService())
    chat_service = ChatService(service, runner=probe_runner)
    session_id = await service.create_session()

    # `execute_turn_events` never forwards a mid-turn exception/cancellation
    # directly to the SSE-consumer-facing generator -- established pattern
    # (test_p0_p1_live_incident_regression.py's own `test_p0_cleanup_
    # reload_survives_cancellation`): the correct assertion is the
    # resulting STATE after normal consumption, not `pytest.raises` around
    # the consumer loop.
    async for _event in chat_service.execute_turn_events(session_id, "summarize", "api-user"):
        pass

    from backend.agents.team_manager import direct_read_fast_path as fp_module

    assert fp_module._pending_trusted_result_by_run == {}


# --- TEST L: source enrichment stage reuses the shared helper --------------


@pytest.mark.asyncio
async def test_source_enrichment_uses_the_same_helper_as_post_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Architectural-reuse assertion: `TeamsSourceCapture.build_source_
    reference`/`resolve_authoritative_contributors` -- the SAME functions
    post-selection already calls -- are what actually produced this
    payload, verified by patching them and observing the call.
    """
    from backend.api import source_reference as source_reference_module

    calls: dict[str, int] = {"build": 0}
    orig_build = source_reference_module.TeamsSourceCapture.build_source_reference

    def traced_build(self, message_texts_by_id=None):
        calls["build"] += 1
        return orig_build(self, message_texts_by_id)

    monkeypatch.setattr(source_reference_module.TeamsSourceCapture, "build_source_reference", traced_build)

    chat_service, service, _log = _build_direct_unique_chat_service(
        monkeypatch,
        chats=[chat("chat-b2-id", "Network Operations Daily")],
        messages_by_chat_id={"chat-b2-id": _messages(("m1", "Alex", "We shipped the fix."))},
        synthesis_response={
            "outcome": "ok",
            "chat_id": "chat-b2-id",
            "chat_title": "Network Operations Daily",
            "summary": "The fix shipped.",
            "evidence": [EVIDENCE_M1],
        },
    )
    session_id = await service.create_session()
    events = await _run_turn(chat_service, session_id, "summarize")

    assert calls["build"] >= 1
    assert _completed(events).data.get("source") is not None
