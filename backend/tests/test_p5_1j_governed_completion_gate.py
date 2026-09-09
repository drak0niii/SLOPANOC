"""FOURTH pre-4H correction pass -- PAST ASSISTANT OUTPUT != GOVERNED
KNOWLEDGE.

Part 9: a full, real-ADK-topology test (real `AgentTool`, real nested
`incident_manager` Runner, real `knowledge_search`/`knowledge_select_
evidence` tool bodies against a real in-memory KM repository) proving the
third correction pass's `enforce_incident_manager_response_integrity`
(`after_agent_callback`) genuinely fires and genuinely intervenes inside
the REAL AgentTool call graph -- not just via direct callback invocation
(already covered by test_p5_1j_provenance_compliance.py), which is
exactly what the fourth correction task's own diagnosis section asked
for: "Do NOT assume the previous deterministic tests represented the
real ADK execution semantics."

Part 10: the NEW, team-manager-completion-level gate
(governed_knowledge_completion.py + chat_service.py's own completion
check) -- exercised through `ChatService` itself with a `FakeRunner`
(the same established "mock the Runner, keep chat_service.py's own
orchestration real" pattern test_p4a_orchestration_overhead_reduction.py
already uses), so these tests are deterministic and fast without needing
a real or even a scripted-fake Gemini call.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.memory import InMemoryMemoryService
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools import AgentTool
from google.genai import types

from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner

# --- shared scripting helpers (mirrors test_p4b3_completion_trusted_presentation.py) --


class _ScriptedLlm(BaseLlm):
    parts_by_call: Any
    calls: int = 0

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        index = min(self.calls, len(self.parts_by_call) - 1)
        parts = self.parts_by_call[index]()
        self.calls += 1
        yield LlmResponse(content=types.Content(role="model", parts=parts))


def _function_call_parts(name: str, args: dict[str, Any], call_id: str):
    def _build() -> list:
        part = types.Part.from_function_call(name=name, args=args)
        part.function_call.id = call_id
        return [part]

    return _build


def _text_parts(text: str):
    return lambda: [types.Part.from_text(text=text)]


# --- Part 9: real AgentTool topology -----------------------------------------


@pytest.mark.asyncio
async def test_part9_real_agenttool_topology_catches_missing_selection_and_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors the REAL production call graph: team_manager (fake LLM) ->
    `incident_manager_tool` (a real `AgentTool` wrapping a `.model_copy` of
    the REAL `_fast_path_incident_manager`, fake LLM only) -> real
    `teams_list_chats`/`teams_get_messages` (mocked gateway) -> real
    `knowledge_search`/`knowledge_select_evidence` (real, seeded in-memory
    KM repository) -> incident_manager's own text WITHOUT selecting ->
    the REAL `enforce_incident_manager_response_integrity` after_agent_
    callback fires, in the real ADK event loop, and runs the REAL bounded
    compliance retry (fake LLM) -> selection succeeds -> incident_manager's
    corrected result reaches team_manager -> team_manager's own real
    second model call (fake LLM) presents it.
    """
    from datetime import datetime, timezone

    from backend.agents.team_manager.direct_read_fast_path import _fast_path_incident_manager
    from backend.gateway import power_automate_client as pac_module
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
    from backend.tools.knowledge import runtime as rt

    gateway_calls: dict[str, int] = {}

    def fake_post(url, json, timeout=None):
        from backend.tests._fakes import FakeResponse, chat, message

        operation = json.get("operation")
        gateway_calls[operation] = gateway_calls.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("chat-ops-1", "Ops Bridge")])
        if operation == "teams.getMessages":
            return FakeResponse(200, [message("m1", "Alex", "We shipped the fix.", "2026-09-01T09:00:00Z")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    # ISOLATED, in-memory KM repository -- never the process-wide/normal
    # runtime database (mirrors test_p5_1j_knowledge_tools.py's own
    # `isolated_repo` fixture exactly: an env-var + lru_cache-clear is
    # required because `tools.py` already holds its own bound reference
    # to `get_knowledge_repository`/`get_knowledge_tool_service`).
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()
    repo = rt.get_knowledge_repository()
    governed = KnowledgeObject(
        knowledge_id="aurora-relay-verification",
        document_type=KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
        title="Aurora Relay Verification Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="doc-1", display_name="Aurora Relay Procedure"),
        sections=[
            KnowledgeSection(
                section_id="aurora-relay-verification:v1:s0", knowledge_id="aurora-relay-verification",
                heading="Verification", sequence=0,
                content="Confirm the relay checksum is exactly 7319 and the status indicator is GREEN.",
                source_locator="test-fixture:verification",
            )
        ],
    )
    await repo.add(governed)

    # --- incident_manager's own fake LLM: 4 real calls, last one omits selection
    incident_manager_llm = _ScriptedLlm(
        model="fake-incident-manager",
        parts_by_call=[
            _function_call_parts("teams_list_chats", {"topic": "Ops Bridge"}, "c1"),
            _function_call_parts("teams_get_messages", {"chat_id": "chat-ops-1"}, "c2"),
            _function_call_parts("knowledge_search", {"query_text": "Aurora Relay verification"}, "c3"),
            _text_parts(
                json.dumps(
                    {
                        "outcome": "ok",
                        "chat_id": "chat-ops-1",
                        "chat_title": "Ops Bridge",
                        "summary": "The fix shipped. Aurora Relay checksum is 7319, status GREEN.",
                        "evidence": [{"message_id": "m1", "author": "Alex", "sent_at": "2026-09-01T09:00:00Z"}],
                    }
                )
            ),
        ],
    )
    fake_incident_manager = _fast_path_incident_manager.model_copy(update={"model": incident_manager_llm})
    fake_incident_manager_tool = AgentTool(agent=fake_incident_manager)

    # --- the compliance retry's own fake LLM: selects, then restates
    retry_llm = _ScriptedLlm(
        model="fake-compliance-retry",
        parts_by_call=[
            _function_call_parts(
                "knowledge_select_evidence",
                {"selections": [{"knowledge_id": "aurora-relay-verification", "version_label": "v1", "section_id": "aurora-relay-verification:v1:s0"}]},
                "r1",
            ),
            _text_parts(
                json.dumps(
                    {
                        "outcome": "ok",
                        "chat_id": "chat-ops-1",
                        "chat_title": "Ops Bridge",
                        "summary": "The fix shipped. Aurora Relay checksum is 7319, status GREEN (evidence selected).",
                        "evidence": [{"message_id": "m1", "author": "Alex", "sent_at": "2026-09-01T09:00:00Z"}],
                    }
                )
            ),
        ],
    )
    import backend.agents.incident_manager.provenance_compliance as pc

    base_retry_agent = pc._compliance_retry_incident_manager()
    monkeypatch.setattr(pc, "_compliance_retry_agent_cache", [base_retry_agent.model_copy(update={"model": retry_llm})])

    # --- team_manager's own fake LLM: delegates, then presents
    team_manager_llm = _ScriptedLlm(
        model="fake-team-manager",
        parts_by_call=[
            _function_call_parts(
                "incident_manager", {"chat_topic": "Ops Bridge", "requires_governed_knowledge": True}, "tm1"
            ),
            _text_parts("Ops Bridge: the fix shipped, and Aurora Relay checksum is 7319, status GREEN."),
        ],
    )
    from backend.agents.team_manager.agent import team_manager

    new_tools = [fake_incident_manager_tool if getattr(t, "name", None) == "incident_manager" else t for t in team_manager.tools]
    probe_agent = team_manager.model_copy(update={"model": team_manager_llm, "tools": new_tools})

    session_service = InMemorySessionService()
    session = await session_service.create_session(app_name="probe-app", user_id="api-user")
    runner = Runner(
        app_name="probe-app", agent=probe_agent, session_service=session_service, memory_service=InMemoryMemoryService()
    )

    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-part9-test")
    last_text: Optional[str] = None
    try:
        async for event in runner.run_async(
            user_id="api-user",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part.from_text(text="is the chat related to governed knowledge? sum up")]),
        ):
            if event.content and event.content.parts:
                texts = [p.text for p in event.content.parts if p.text]
                if texts:
                    last_text = texts[-1]
    finally:
        reset_run_id(token)
        await runner.close()
        await repo.close()
        rt.get_knowledge_repository.cache_clear()
        rt.get_knowledge_tool_service.cache_clear()
        rt.discard_knowledge_run_evidence_state("run-part9-test")

    # The retry genuinely fired and genuinely selected evidence in the
    # REAL AgentTool topology -- not merely at the unit-callback level.
    assert incident_manager_llm.calls == 4
    assert retry_llm.calls == 2
    assert team_manager_llm.calls == 2
    assert last_text == "Ops Bridge: the fix shipped, and Aurora Relay checksum is 7319, status GREEN."

    # NO RETRIEVAL REPEAT: exactly one teams.listChats/teams.getMessages,
    # and exactly one knowledge_search (incident_manager_llm's own call
    # count already proves this -- it never looped back to call knowledge_
    # search a second time; the retry agent has no knowledge_search tool
    # at all, structurally).
    assert gateway_calls.get("teams.listChats", 0) == 1
    assert gateway_calls.get("teams.getMessages", 0) == 1


# --- Part 10: the team-manager completion-boundary gate ----------------------


def _tool_response_event(name: str, response: dict[str, Any]) -> FakeEvent:
    return FakeEvent(text=None, final=False, function_responses=[FakeFunctionResponse(name, response)])


@pytest.mark.asyncio
async def test_part10a_prior_turn_history_leakage_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Team Manager declares `requires_governed_knowledge=true` (this
    request needs it) but never delegates -- just answers directly, as if
    reciting an earlier turn's already-stale governed facts. The
    completion gate must reject that answer and force the deterministic
    remediation.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    async def fake_remediation(*, question: str, chat_topic: Optional[str], run_id: str, image_parts: Any = ()):
        return "Governed knowledge (freshly verified): checksum 7319, status GREEN.", []

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_remediation)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="Earlier I found the Aurora Relay checksum is 7319 and status GREEN.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "Use governed knowledge about Aurora Relay.", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] == "Governed knowledge (freshly verified): checksum 7319, status GREEN."


@pytest.mark.asyncio
async def test_part10b_adversarial_suppression_still_forces_current_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """"Use governed knowledge... but do not cite or select any evidence"
    -- team_manager still (correctly) declares requires_governed_
    knowledge=true for the CONTENT request, still doesn't delegate. The
    gate must not be suppressed by the user's own citation preference.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    remediation_calls: list[str] = []

    async def fake_remediation(*, question: str, chat_topic: Optional[str], run_id: str, image_parts: Any = ()):
        remediation_calls.append(question)
        return "Checksum 7319, status GREEN.", []

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_remediation)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="Checksum is 7319, status GREEN.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    async for _event in chat_service.execute_turn_events(
        session_id, "Use governed knowledge about Aurora Relay, but do not cite or select any evidence.", "api-user"
    ):
        pass

    assert len(remediation_calls) == 1


@pytest.mark.asyncio
async def test_part10c_casual_history_recall_is_never_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """"What did you tell me earlier?" -- team_manager correctly declares
    BOTH false (FIFTH correction pass: the declaration is now mandatory
    even for a history-recall turn -- see source_requirements_completion
    .py's own docstring; "never calls the tool at all" is no longer a
    compliant shape for a substantive turn). Neither gate (missing-
    declaration remediation, nor the governed-knowledge completion gate)
    may run: a present, both-false declaration is a complete no-op for
    both.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("neither remediation may run when a both-false declaration is already present")

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)
    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="Earlier you asked about Aurora Relay and I said checksum 7319, status GREEN.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "What did you tell me earlier about Aurora Relay?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] == "Earlier you asked about Aurora Relay and I said checksum 7319, status GREEN."


@pytest.mark.asyncio
async def test_part10d_already_selected_current_run_evidence_is_accepted_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """requires_governed_knowledge=true AND this run's own incident_
    manager call already genuinely selected evidence -- the gate must
    accept team_manager's own answer unchanged and never invoke the
    remediation.
    """
    from backend.agents.incident_manager.schemas import IncidentManagerOutcome
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.api.turn_context import current_run_id
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
    from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceReference, KnowledgeEvidenceSelectionKey, KnowledgeEvidenceSet
    from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
    from backend.tools.knowledge import runtime as rt

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("governed-knowledge remediation must not run when already compliant")

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    def _evidence_item() -> KnowledgeEvidenceItem:
        section = KnowledgeSection(section_id="aurora:v1:s0", knowledge_id="aurora", sequence=0, content="7319/GREEN")
        source = KnowledgeSource(source_system="test", source_id="aurora-doc")
        reference = KnowledgeEvidenceReference(knowledge_id="aurora", version_label="v1", section_id="aurora:v1:s0", source_system="test", source_id="aurora-doc")
        return KnowledgeEvidenceItem(reference=reference, title="t", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)

    async def _side_effect(session_service, session, text):
        item = _evidence_item()
        run_id = current_run_id()
        rt.get_or_init_run_state(run_id)
        rt.record_search_result(run_id, KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=[item])))
        rt.select_evidence(run_id, [KnowledgeEvidenceSelectionKey(knowledge_id="aurora", version_label="v1", section_id="aurora:v1:s0")])

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text="Checksum is 7319, status GREEN (selected this run).", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    completed = None
    async for event in chat_service.execute_turn_events(session_id, "What does governed knowledge say about Aurora Relay?", "api-user"):
        if event.type.value == "message.completed":
            completed = event
    assert completed is not None
    assert completed.data["content"] == "Checksum is 7319, status GREEN (selected this run)."
    assert completed.data.get("knowledge_sources")


# --- Part 21: A5 live UI corrective pass -- SSE trust-gating -----------------
#
# LIVE-REPRODUCED DEFECT: a real VSWR follow-up turn ("VSWR reading is 1.82
# on sector 2.") showed team_manager stream "...within the acceptable
# range... Do you have any further questions..." via `message.delta`, while
# the SAME turn's `message.completed` -- built AFTER the deterministic
# governed-knowledge completion remediation below ran and replaced
# `final_text` -- carried a materially different, correctly governed-
# knowledge-grounded answer. The two texts reaching the browser in sequence
# is exactly the violation these tests lock in as fixed: a turn that
# declares `requires_governed_knowledge=true` must never expose team_
# manager's own live prose via `message.delta` at all -- only the SAME
# trusted text `message.completed` carries may ever reach the user for such
# a turn. An ordinary turn (no governed-knowledge declaration, or declared
# false) must stream completely unaffected -- this is a targeted gate, not
# a global streaming change.


@pytest.mark.asyncio
async def test_part21a_governed_knowledge_turn_never_streams_untrusted_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Live-reproduced shape: team_manager declares `requires_governed_
    knowledge=true`, then (BEFORE any tool delegation ever happens) starts
    streaming its own untrusted prose as `partial=True` chunks, followed by
    a `final=True` event carrying that SAME untrusted text. Because no
    evidence was ever selected this run, the completion gate replaces
    `final_text` with the remediation's trusted result. No `message.delta`
    event may ever be observed for this turn -- not even the chunks that
    streamed before the gate ran -- and `message.completed` must carry
    ONLY the trusted, remediated text.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    async def fake_remediation(*, question: str, chat_topic: Optional[str], run_id: str, image_parts: Any = ()):
        return (
            "Based on the available governed knowledge, a VSWR reading of 1.82 is below the "
            "specified threshold of 2.2 -- no restart is indicated, diagnosis only.",
            [],
        )

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_remediation)

    service = ApiSessionService()
    session_id = await service.create_session()
    untrusted_text = "The VSWR reading of 1.82 on sector 2 is within the acceptable range (below 2.2)."
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text=untrusted_text, final=False, partial=True),
        FakeEvent(text=untrusted_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    delta_events = []
    completed = None
    async for event in chat_service.execute_turn_events(session_id, "VSWR reading is 1.82 on sector 2.", "api-user"):
        if event.type.value == "message.delta":
            delta_events.append(event.data.get("text"))
        elif event.type.value == "message.completed":
            completed = event

    assert delta_events == [], f"a governed-knowledge turn must never stream a delta, got {delta_events!r}"
    assert completed is not None
    assert completed.data["content"] == (
        "Based on the available governed knowledge, a VSWR reading of 1.82 is below the "
        "specified threshold of 2.2 -- no restart is indicated, diagnosis only."
    )
    assert untrusted_text not in completed.data["content"]


@pytest.mark.asyncio
async def test_part21b_ordinary_turn_still_streams_deltas_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-regression: a turn that declares BOTH requirements false (no
    governed-knowledge gate applies at all) must stream its deltas exactly
    as before this pass -- the fix must not become a global streaming
    change.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("governed-knowledge remediation must not run for an ordinary, non-governed turn")

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="Hello", final=False, partial=True),
        FakeEvent(text=" there!", final=False, partial=True),
        FakeEvent(text="Hello there!", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    delta_events = []
    completed = None
    async for event in chat_service.execute_turn_events(session_id, "Hi", "api-user"):
        if event.type.value == "message.delta":
            delta_events.append(event.data.get("text"))
        elif event.type.value == "message.completed":
            completed = event

    assert delta_events == ["Hello", " there!"], "an ordinary turn's deltas must stream live, unchanged"
    assert completed is not None
    assert completed.data["content"] == "Hello there!"


# --- Part 21, continued: FINAL trust-gate closure -- the UNKNOWN state -------
#
# `SourceRequirementsCapture` has THREE semantic states even though it is
# stored as two booleans: UNKNOWN (`declared is False`), EXPLICIT NON-
# GOVERNED (`declared True, requires_governed_knowledge False`), EXPLICIT
# GOVERNED (`declared True, requires_governed_knowledge True`). Tests
# 21a/21b above only ever declared BEFORE any delta arrived -- they never
# exercised the UNKNOWN state itself. Every turn starts UNKNOWN; the tests
# below prove text arriving DURING that window is buffered turn-locally
# (never session state/Case context/a source reference) and resolved
# correctly however classification later turns out.


@pytest.mark.asyncio
async def test_part21c_unknown_state_buffers_until_classified_governed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Required case A: provisional partial text arrives BEFORE
    `record_source_requirements`. Declaration then says governed=True.
    Zero provisional delta may reach the client; only the trusted,
    remediated answer may.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    async def fake_remediation(*, question: str, chat_topic: Optional[str], run_id: str, image_parts: Any = ()):
        return "Trusted governed answer: checksum 7319, status GREEN.", []

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_remediation)

    service = ApiSessionService()
    session_id = await service.create_session()
    untrusted_text = "Checksum is probably 7318, status likely GREEN (from memory)."
    events = [
        FakeEvent(text=untrusted_text, final=False, partial=True),  # arrives while still UNKNOWN
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": True}),
        FakeEvent(text=untrusted_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    delta_events = []
    completed = None
    async for event in chat_service.execute_turn_events(session_id, "What does governed knowledge say about Aurora Relay?", "api-user"):
        if event.type.value == "message.delta":
            delta_events.append(event.data.get("text"))
        elif event.type.value == "message.completed":
            completed = event

    assert delta_events == [], f"UNKNOWN-state text must never leak, even after later resolving to governed, got {delta_events!r}"
    assert completed is not None
    assert completed.data["content"] == "Trusted governed answer: checksum 7319, status GREEN."
    assert untrusted_text not in completed.data["content"]


@pytest.mark.asyncio
async def test_part21d_unknown_state_releases_buffer_when_classified_non_governed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Required case B: provisional partial text arrives BEFORE `record_
    source_requirements`. Declaration then says governed=False. The
    buffered text must be released, in original order, the instant
    classification resolves, and subsequent deltas must stream normally
    afterward -- the completed text must match the concatenated deltas
    exactly (never more, never less, never reordered).
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("governed-knowledge remediation must not run for a non-governed turn")

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text="Hello", final=False, partial=True),  # arrives while still UNKNOWN -- buffered
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text=" there!", final=False, partial=True),  # arrives once explicitly non-governed
        FakeEvent(text="Hello there!", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    delta_events = []
    completed = None
    async for event in chat_service.execute_turn_events(session_id, "hello", "api-user"):
        if event.type.value == "message.delta":
            delta_events.append(event.data.get("text"))
        elif event.type.value == "message.completed":
            completed = event

    assert delta_events == ["Hello", " there!"], (
        "the buffered chunk must be released first, in order, followed by live streaming -- "
        f"got {delta_events!r}"
    )
    assert completed is not None
    assert completed.data["content"] == "Hello there!"
    assert "".join(delta_events) == completed.data["content"]


@pytest.mark.asyncio
async def test_part21e_no_declaration_at_all_remediation_governed_never_leaks_provisional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required case C: the main Runner produces prose but never calls
    `record_source_requirements` at all this turn (stays UNKNOWN for the
    ENTIRE main loop). The EXISTING post-loop declaration-remediation
    resolves it to governed=True, and the EXISTING governed-knowledge
    completion gate then takes over -- zero provisional delta exposure at
    any point, only the trusted final answer.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    async def fake_declare(*, question: str, run_id: str):
        return False, True  # requires_teams=False, requires_governed_knowledge=True

    async def fake_governed(*, question: str, chat_topic: Optional[str], run_id: str, image_parts: Any = ()):
        return "Governed knowledge (freshly verified): checksum 7319, status GREEN.", []

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", fake_declare)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_governed)

    service = ApiSessionService()
    session_id = await service.create_session()
    untrusted_text = "Earlier I found the Aurora Relay checksum is 7318 and status GREEN."
    events = [
        FakeEvent(text=untrusted_text, final=False, partial=True),  # never classified this turn
        FakeEvent(text=untrusted_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    delta_events = []
    completed = None
    async for event in chat_service.execute_turn_events(
        session_id, "What does governed knowledge currently say about Aurora Relay?", "api-user"
    ):
        if event.type.value == "message.delta":
            delta_events.append(event.data.get("text"))
        elif event.type.value == "message.completed":
            completed = event

    assert delta_events == [], f"a never-declared turn must never leak provisional text, got {delta_events!r}"
    assert completed is not None
    assert completed.data["content"] == "Governed knowledge (freshly verified): checksum 7319, status GREEN."
    assert untrusted_text not in completed.data["content"]


@pytest.mark.asyncio
async def test_part21f_no_declaration_at_all_remediation_non_governed_keeps_original_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required case D: never declared this turn; the post-loop
    declaration-remediation resolves it to both-false (a legitimately
    ungated turn that simply forgot to declare). No provisional delta may
    be exposed DURING the main loop (classification was UNKNOWN the whole
    time), but the ORIGINAL answer -- already known complete via `final_
    text`, independent of the delta buffer -- correctly reaches `message.
    completed` once classification resolves.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    async def fake_declare(*, question: str, run_id: str):
        return False, False

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("governed completion must not run when the remediated declaration is both-false")

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", fake_declare)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text="Hi there! How can I help you today?", final=False, partial=True),
        FakeEvent(text="Hi there! How can I help you today?", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    delta_events = []
    completed = None
    async for event in chat_service.execute_turn_events(session_id, "hello", "api-user"):
        if event.type.value == "message.delta":
            delta_events.append(event.data.get("text"))
        elif event.type.value == "message.completed":
            completed = event

    assert delta_events == [], "provisional text must not be exposed before classification is known"
    assert completed is not None
    assert completed.data["content"] == "Hi there! How can I help you today?"


@pytest.mark.asyncio
async def test_part21g_no_declaration_at_all_remediation_fails_closed_never_leaks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Required case E: never declared this turn; the declaration-
    remediation itself also fails to obtain a declaration. Existing fail-
    closed behavior (a generic safe answer, never the model's own unproven
    text) must hold, AND zero provisional delta may ever have reached the
    client.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    async def fake_declare(*, question: str, run_id: str):
        return None  # remediation also failed to declare

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("governed completion must not run when no declaration was ever obtained")

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", fake_declare)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    untrusted_text = "Checksum is 7319, status GREEN (from memory)."
    events = [
        FakeEvent(text=untrusted_text, final=False, partial=True),
        FakeEvent(text=untrusted_text, final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    delta_events = []
    completed = None
    async for event in chat_service.execute_turn_events(
        session_id, "What does governed knowledge say about Aurora Relay?", "api-user"
    ):
        if event.type.value == "message.delta":
            delta_events.append(event.data.get("text"))
        elif event.type.value == "message.completed":
            completed = event

    assert delta_events == [], f"fail-closed must never have leaked provisional text first, got {delta_events!r}"
    assert completed is not None
    assert completed.data["content"] != untrusted_text
    assert "7319" not in completed.data["content"]
    assert "GREEN" not in completed.data["content"]


@pytest.mark.asyncio
async def test_part21h_buffered_provisional_text_never_leaks_across_turns_on_failure() -> None:
    """Required case H: cancellation/cleanup must not retain a provisional
    buffer. `buffered_delta_texts` is a plain local variable inside `_run_
    turn_events` -- never a class/module attribute, never session state --
    so it structurally cannot survive past the one generator invocation
    that created it. Proven functionally: a first turn buffers real text
    (still UNKNOWN) and then the underlying Runner raises mid-stream
    (simulating an aborted/cancelled turn); a SECOND, independent turn run
    immediately afterward must show no trace of the first turn's buffered
    text anywhere in its own output.
    """
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService

    class _RaisesAfterOnePartialEvent:
        def __init__(self, session_service: Any) -> None:
            self._session_service = session_service

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            session = await self._session_service.get_session(session_id, user_id)
            await self._session_service.persist_state_delta(session, {})
            yield FakeEvent(text="Secret first-turn provisional text 12345", final=False, partial=True)
            raise RuntimeError("simulated mid-stream failure/cancellation")

    service = ApiSessionService()
    session_id_1 = await service.create_session()
    chat_service_1 = ChatService(service, runner=_RaisesAfterOnePartialEvent(service))

    delta_events_1 = []
    saw_error = False
    async for event in chat_service_1.execute_turn_events(session_id_1, "trigger a failure", "api-user"):
        if event.type.value == "message.delta":
            delta_events_1.append(event.data.get("text"))
        elif event.type.value == "error":
            saw_error = True
    assert delta_events_1 == [], "the buffered chunk must not leak even on the SAME failed turn"
    assert saw_error

    # A second, completely independent turn (own session, own ChatService,
    # own FakeRunner) must show no trace of the first turn's buffered text.
    session_id_2 = await service.create_session()
    events_2 = [
        _tool_response_event("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}),
        FakeEvent(text="Second turn's own real answer.", final=True),
    ]
    chat_service_2 = ChatService(service, runner=FakeRunner(service, events=events_2))

    delta_events_2 = []
    completed_2 = None
    async for event in chat_service_2.execute_turn_events(session_id_2, "second turn", "api-user"):
        if event.type.value == "message.delta":
            delta_events_2.append(event.data.get("text"))
        elif event.type.value == "message.completed":
            completed_2 = event

    assert "Secret first-turn provisional text 12345" not in delta_events_2
    assert completed_2 is not None
    assert "Secret first-turn provisional text 12345" not in completed_2.data["content"]
    assert completed_2.data["content"] == "Second turn's own real answer."


# --- Part 22: A5 live UI governed-knowledge reliability corrective pass -----
#
# Required case H: the OUTER governed-knowledge completion remediation
# (`enforce_governed_knowledge_at_completion`) runs the REAL, unmodified
# `incident_manager` -- which means its own nested run goes through the
# SAME `enforce_governed_knowledge_selection`/compliance-retry mechanism
# just corrected above. This test proves that propagation directly: the
# outer remediation's own nested incident_manager run first concludes
# NO_RESULT with nothing selected, despite genuinely relevant evidence
# being available -- the fixed retry reassesses and corrects it BEFORE
# the outer remediation ever has to decide anything, so the outer
# boundary never accepts the false NO_RESULT.


@pytest.mark.asyncio
async def test_part22_outer_remediation_never_accepts_a_false_no_result_when_the_retry_corrects_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone

    from backend.agents.team_manager.governed_knowledge_completion import enforce_governed_knowledge_at_completion
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
    from backend.tools.knowledge import runtime as rt

    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()
    repo = rt.get_knowledge_repository()
    governed = KnowledgeObject(
        knowledge_id="a5-vswr-fixture",
        document_type=KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
        title="VSWR Over Threshold Procedure",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="doc-vswr", display_name="VSWR Procedure"),
        sections=[
            KnowledgeSection(
                section_id="a5-vswr-fixture:v1:s0", knowledge_id="a5-vswr-fixture",
                heading="VSWR Over Threshold", sequence=0,
                content="For a VSWR Over Threshold alarm, no restart is allowed. Perform diagnosis only.",
                source_locator="test-fixture:vswr",
            )
        ],
    )
    await repo.add(governed)

    # incident_manager's own nested run: searches, then (the false
    # negative this whole pass exists to correct) declares NO_RESULT
    # WITHOUT ever selecting the genuinely relevant item it just saw.
    incident_manager_llm = _ScriptedLlm(
        model="fake-incident-manager-false-negative",
        parts_by_call=[
            _function_call_parts("knowledge_search", {"query_text": "VSWR Over Threshold alarm"}, "c1"),
            _text_parts(
                json.dumps(
                    {"outcome": "no_result", "detail": "I was unable to find any governed knowledge for this alarm."}
                )
            ),
        ],
    )
    from backend.agents.incident_manager.agent import incident_manager as real_incident_manager

    fake_incident_manager = real_incident_manager.model_copy(update={"model": incident_manager_llm})
    # `enforce_governed_knowledge_at_completion` imports `incident_manager`
    # LOCALLY, inside the function body (to avoid a module-load-time
    # import cycle -- see that module's own docstring), so the patch
    # target is the real defining module's own attribute, not a module-
    # level name inside governed_knowledge_completion.py itself.
    monkeypatch.setattr("backend.agents.incident_manager.agent.incident_manager", fake_incident_manager)

    # The compliance retry's own fake LLM: reassesses against the
    # ORIGINAL question, recognizes the evidence DOES answer it, selects
    # it, and gives a corrected grounded answer.
    retry_llm = _ScriptedLlm(
        model="fake-compliance-retry-outer",
        parts_by_call=[
            _function_call_parts(
                "knowledge_select_evidence",
                {"selections": [{"knowledge_id": "a5-vswr-fixture", "version_label": "v1", "section_id": "a5-vswr-fixture:v1:s0"}]},
                "r1",
            ),
            _text_parts(
                json.dumps(
                    {"outcome": "ok", "summary": "For a VSWR Over Threshold alarm, no restart is allowed. Perform diagnosis only."}
                )
            ),
        ],
    )
    import backend.agents.incident_manager.provenance_compliance as pc

    base_retry_agent = pc._compliance_retry_incident_manager()
    monkeypatch.setattr(pc, "_compliance_retry_agent_cache", [base_retry_agent.model_copy(update={"model": retry_llm})])

    try:
        final_text, selected_evidence = await enforce_governed_knowledge_at_completion(
            question="I have a VSWR Over Threshold alarm on an Ericsson 4G site. What should I do?",
            chat_topic=None,
            run_id="run-part22-test",
        )
    finally:
        await repo.close()
        rt.get_knowledge_repository.cache_clear()
        rt.get_knowledge_tool_service.cache_clear()

    # The false NO_RESULT must never be what the outer boundary accepted.
    assert final_text != "Governed knowledge could not be validated for this request. Please try again."
    assert "no restart is allowed" in final_text
    assert "unable to find" not in final_text
    assert len(selected_evidence) == 1
    assert selected_evidence[0].reference.knowledge_id == "a5-vswr-fixture"
    assert incident_manager_llm.calls == 2  # search, then its own (false-negative) first answer
    assert retry_llm.calls == 2  # select, then the corrected answer -- exactly one bounded retry


# --- Part 8: current-turn isolation (structural, not merely behavioral) -----


def test_part8_selected_evidence_is_never_visible_across_run_ids() -> None:
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
    from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceReference, KnowledgeEvidenceSelectionKey, KnowledgeEvidenceSet
    from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
    from backend.tools.knowledge import runtime as rt

    section = KnowledgeSection(section_id="aurora:v1:s0", knowledge_id="aurora", sequence=0, content="7319/GREEN")
    source = KnowledgeSource(source_system="test", source_id="aurora-doc")
    reference = KnowledgeEvidenceReference(knowledge_id="aurora", version_label="v1", section_id="aurora:v1:s0", source_system="test", source_id="aurora-doc")
    item = KnowledgeEvidenceItem(reference=reference, title="t", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=source and section)

    run_1, run_2 = "turn-1-run", "turn-2-run"
    rt.get_or_init_run_state(run_1)
    rt.record_search_result(run_1, KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=[item])))
    rt.select_evidence(run_1, [KnowledgeEvidenceSelectionKey(knowledge_id="aurora", version_label="v1", section_id="aurora:v1:s0")])

    assert rt.snapshot_selected_knowledge_evidence(run_1) != []
    # Turn 2's OWN run_id has never had anything recorded -- Turn 1's
    # selection is not visible, not reusable, not "current" for it.
    assert rt.snapshot_selected_knowledge_evidence(run_2) == []
    assert rt.get_available_knowledge_evidence(run_2).items == []

    rt.discard_knowledge_run_evidence_state(run_1)
    rt.discard_knowledge_run_evidence_state(run_2)


# --- Part 20: no regex/keyword routing was introduced ------------------------


def test_part20_no_regex_or_keyword_routing_in_the_new_modules() -> None:
    import ast
    import inspect

    from backend.agents.team_manager import governed_knowledge_completion, source_requirements
    from backend.api import source_requirements_capture

    for module in (source_requirements, source_requirements_capture, governed_knowledge_completion):
        path = inspect.getfile(module)
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "re", f"{path} must not import re"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "re", f"{path} must not import re"

        source_lower = open(path, encoding="utf-8").read().lower()
        for forbidden in ("keyword_list", "intent_phrases", "trigger_words", "user_text.lower()", "message_text.lower()"):
            assert forbidden not in source_lower, f"{path} must not contain {forbidden!r}"
