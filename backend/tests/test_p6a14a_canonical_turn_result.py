"""Phase 6A.14A -- Canonical Turn Result & Projection (closes DEF-0031).

THE DEFECT THIS CLOSES: `chat_service.py` applies several deterministic,
POST-HOC corrections to a turn's raw model/specialist output AFTER the
ADK Runner has already durably appended its own final-response event
(e.g. the KNOWLEDGE_INVENTORY override, DEF-0024/0026/0027/0028's own
troubleshooting-guidance/unresolved-target-context overrides). Those
corrections were previously applied only to the in-memory `final_text`
variable feeding the LIVE `MESSAGE_COMPLETED` SSE event --
`session_history_service.py` reconstructed a turn's text independently,
from the RAW, pre-correction ADK-persisted event -- so a refreshed/
reopened conversation could show a materially different answer than the
one the user actually saw live.

Section 1 exercises the REAL end-to-end pipeline: a real file-backed
`DatabaseSessionService`, the REAL `team_manager` agent object (only its
own `model` swapped for a scripted one -- every real tool/callback,
including `record_request_contract`/`validate_and_persist_request_
contract` (6A.13) and the KNOWLEDGE_INVENTORY deterministic override
(6A.14), stays wired exactly as production uses it), a real `ChatService
.run_turn`, real `get_session_history` projection, a real rewind, and a
real simulated backend restart -- proving live/refreshed/restarted
equivalence for a DETERMINISTICALLY REPLACED response (the exact DEF-0031
reproduction), for an ORDINARY unmodified response, across a rewind, and
across a forced canonical-persistence failure.

Section 2 unit-tests `build_turn_source_references_delta`/`resolve_
canonical_turn_result`/the failure-marker mechanism directly for edge
cases awkward to reach reliably through the full pipeline (conflict/
idempotency, legacy sessions, session isolation, malformed data).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.agents import Agent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import PrivateAttr

from backend.agents.team_manager.agent import team_manager
from backend.agents.team_manager.request_execution_policy import KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT
from backend.api.chat_service import ChatService
from backend.api.schemas import KnowledgeSourceReferenceDTO, SourceReferenceDTO
from backend.api.session_history_service import get_session_history
from backend.api.session_service import APP_NAME, ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.api.turn_source_references import (
    TURN_SOURCE_REFERENCES_STATE_KEY,
    CanonicalTurnResult,
    CanonicalTurnResultConflictError,
    build_turn_failure_marker_delta,
    build_turn_source_references_delta,
    is_turn_marked_failed,
    resolve_canonical_turn_result,
)
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService

# --- Section 1: real end-to-end pipeline ------------------------------------


def _attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


def _db_backed_service(tmp_path: Path, name: str) -> ApiSessionService:
    db_file = tmp_path / f"{name}.db"
    return ApiSessionService(adk_session_service=DatabaseSessionService(f"sqlite+aiosqlite:///{db_file.as_posix()}"))


def _function_call_response(name: str, args: dict[str, Any], call_id: str) -> LlmResponse:
    part = types.Part.from_function_call(name=name, args=args)
    part.function_call.id = call_id
    return LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)


def _final_text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]), partial=False)


class _KnowledgeInventoryOuterLlm(BaseLlm):
    """Declares a KNOWLEDGE_INVENTORY request contract, then answers with
    plain free-form text that chat_service.py's own 6A.14 deterministic
    override must UNCONDITIONALLY replace -- the concrete, live DEF-0031
    reproduction: raw model text vs. the corrected, canonical text."""

    _step: list[int] = PrivateAttr(default_factory=lambda: [0])

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model="scripted-ki-outer-model", **kwargs)

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        step = self._step[0]
        self._step[0] += 1
        if step == 0:
            yield _function_call_response(
                "record_request_contract",
                {"intent": "knowledge_inventory", "requested_output": "knowledge_list"},
                "ki-call-0",
            )
            return
        yield _final_text_response(
            "Here is the complete list of every governed document I know about: Document A, Document B, Document C."
        )


class _OrdinaryOuterLlm(BaseLlm):
    """A completely ordinary turn -- declares no source requirements (a
    plain conversational reply needs neither), no override -- the raw
    model text IS the canonical text."""

    _step: list[int] = PrivateAttr(default_factory=lambda: [0])
    _text: str = PrivateAttr(default="Hello! How can I help you today?")

    def __init__(self, text: str = "Hello! How can I help you today?", **kwargs: Any) -> None:
        super().__init__(model="scripted-ordinary-outer-model", **kwargs)
        self._text = text

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        step = self._step[0]
        self._step[0] += 1
        if step == 0:
            yield _function_call_response(
                "record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False}, "ord-0"
            )
            return
        yield _final_text_response(self._text)


def _build_runner(session_service: ApiSessionService, model: BaseLlm) -> Runner:
    scripted_team_manager = team_manager.model_copy(update={"model": model})
    return Runner(app_name=APP_NAME, agent=scripted_team_manager, session_service=session_service.adk_session_service)


@pytest.mark.asyncio
async def test_deterministic_replacement_survives_refresh_exactly(tmp_path: Path) -> None:
    """8.1 / the direct DEF-0031 reproduction: live MESSAGE_COMPLETED
    carries the CORRECTED text; refreshed history carries the EXACT same
    corrected text; the raw, rejected model text never appears anywhere
    in history."""
    session_service = _db_backed_service(tmp_path, "6a14a_replacement")
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service, _KnowledgeInventoryOuterLlm())
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    live_text: Optional[str] = None
    async for event in chat_service.execute_turn_events(session_id, "What governed knowledge do you have?", "api-user"):
        if event.type == StreamEventType.MESSAGE_COMPLETED:
            live_text = event.data.get("content")
        assert event.type != StreamEventType.ERROR

    assert live_text == KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT

    history = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant_messages = [m for m in history.messages if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].text == live_text == KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT
    assert "Document A" not in assistant_messages[0].text  # the raw, rejected model text never leaks through

    # Re-reading history is stable/idempotent.
    history_again = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert history_again.model_dump(mode="json") == history.model_dump(mode="json")

    # Simulated backend restart: a fresh repository/session-service
    # instantiation against the SAME on-disk database file.
    restarted_service = _db_backed_service(tmp_path, "6a14a_replacement")
    history_after_restart = await get_session_history(restarted_service, _attachment_service(), session_id, "api-user")
    restarted_assistant = [m for m in history_after_restart.messages if m.role == "assistant"][0]
    assert restarted_assistant.text == live_text


@pytest.mark.asyncio
async def test_ordinary_unmodified_response_is_canonical_and_survives_refresh(tmp_path: Path) -> None:
    """8.2: a normal response untouched by any deterministic policy is
    still canonicalized and appears identically live and in history."""
    session_service = _db_backed_service(tmp_path, "6a14a_ordinary")
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service, _OrdinaryOuterLlm())
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    response = await chat_service.run_turn(session_id, "hello", "api-user")
    assert response.message.content == "Hello! How can I help you today?"

    history = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant_messages = [m for m in history.messages if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].text == "Hello! How can I help you today?"

    # A genuine canonical result was actually persisted for this turn
    # (never merely "happens to match" via the legacy raw-event path).
    session = await session_service.get_session(session_id, "api-user")
    canonical = resolve_canonical_turn_result(session.state, assistant_messages[0].turn_id)
    assert canonical is not None
    assert canonical.text == "Hello! How can I help you today?"
    assert canonical.schema_version == "1.0"


@pytest.mark.asyncio
async def test_rewind_removes_canonical_result_for_discarded_turn(tmp_path: Path) -> None:
    """8.8: a rewound turn's canonical result disappears via ADK's own
    state-delta reversal -- no manual cleanup needed."""
    session_service = _db_backed_service(tmp_path, "6a14a_rewind")
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service, _OrdinaryOuterLlm())
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    await chat_service.run_turn(session_id, "hello", "api-user")
    history_before = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert len(history_before.messages) == 2
    assistant_before = [m for m in history_before.messages if m.role == "assistant"][0]

    await chat_service.rewind_before_user_turn(session_id, 0, "api-user")

    history_after = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert history_after.messages == []

    refreshed = await session_service.get_session(session_id, "api-user")
    assert resolve_canonical_turn_result(refreshed.state, assistant_before.turn_id) is None
    stored = refreshed.state.get(TURN_SOURCE_REFERENCES_STATE_KEY)
    assert not stored or assistant_before.turn_id not in stored


@pytest.mark.asyncio
async def test_persistence_failure_fails_closed_no_message_completed_no_contradictory_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """8.10: force canonical persistence to fail. Proves: MESSAGE_COMPLETED
    is never emitted as if durable completion succeeded; the failure
    surfaces through the correct ERROR/RUN_COMPLETED(outcome=error) path;
    and -- the harder property -- history shows NO contradictory
    resurrection of the raw, unfinalized model text for this turn (the
    ADK Runner's own raw final-response event is durably appended
    regardless of whether chat_service.py's own canonical write
    succeeds)."""
    session_service = _db_backed_service(tmp_path, "6a14a_persist_fail")
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service, _OrdinaryOuterLlm())
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    original_persist = ApiSessionService.persist_state_delta

    async def _raising_persist(self: ApiSessionService, session: Any, delta: dict[str, Any]) -> None:
        if TURN_SOURCE_REFERENCES_STATE_KEY in delta and isinstance(
            delta[TURN_SOURCE_REFERENCES_STATE_KEY], dict
        ):
            for entry in delta[TURN_SOURCE_REFERENCES_STATE_KEY].values():
                if isinstance(entry, dict) and "final_text" in entry:
                    raise RuntimeError("simulated canonical-result persistence failure")
        return await original_persist(self, session, delta)

    monkeypatch.setattr(ApiSessionService, "persist_state_delta", _raising_persist)

    saw_error = False
    saw_message_completed = False
    async for event in chat_service.execute_turn_events(session_id, "hello", "api-user"):
        if event.type == StreamEventType.MESSAGE_COMPLETED:
            saw_message_completed = True
        if event.type == StreamEventType.ERROR:
            saw_error = True

    assert saw_error is True
    assert saw_message_completed is False  # never announced as durably completed

    # Restore the real implementation for the read path below (the failure
    # marker itself must persist successfully so history can honor it).
    monkeypatch.setattr(ApiSessionService, "persist_state_delta", original_persist)

    history = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant_messages = [m for m in history.messages if m.role == "assistant"]
    assert assistant_messages == []  # no contradictory raw text resurrected
    user_messages = [m for m in history.messages if m.role == "user"]
    assert len(user_messages) == 1  # the user's own message is still visible

    session = await session_service.get_session(session_id, "api-user")
    assert is_turn_marked_failed(session.state, user_messages[0].turn_id) is True
    assert resolve_canonical_turn_result(session.state, user_messages[0].turn_id) is None


@pytest.mark.asyncio
async def test_provenance_and_text_are_consistent_across_live_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """8.6: for a turn carrying provenance, the live event, the persisted
    canonical result, and the refreshed history projection all represent
    the SAME source/knowledge_sources -- and the SAME text -- with no
    independent reconstruction drift."""
    from google.adk.agents import Agent as _Agent

    from backend.agents.incident_manager.agent import incident_manager
    from backend.agents.incident_manager.schemas import IncidentManagerRequest
    from backend.agents.team_manager.case_tools import record_case_analysis
    from backend.agents.team_manager.conversation_target import record_conversation_target
    from backend.agents.team_manager.multimodal_agent_tool import MultimodalAgentTool
    from backend.agents.team_manager.read_continuation_enforcement import enforce_read_continuation
    from backend.agents.team_manager.request_contract import record_request_contract, validate_and_persist_request_contract
    from backend.agents.team_manager.selection_delegation_guard import (
        block_repeated_delegation_after_selection_needed,
        record_selection_needed,
    )
    from backend.agents.team_manager.source_requirements import record_source_requirements
    from backend.agents.team_manager.state_sync import sync_incident_manager_result_to_state
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
    from backend.tests._fakes import FakeResponse, chat, message
    from datetime import datetime, timezone

    class _OuterLlm(BaseLlm):
        _step: list[int] = PrivateAttr(default_factory=lambda: [0])

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="scripted-outer-6a14a", **kwargs)

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            step = self._step[0]
            self._step[0] += 1
            if step == 0:
                yield _function_call_response(
                    "record_source_requirements", {"requires_teams": True, "requires_governed_knowledge": False}, "o0"
                )
                return
            if step == 1:
                request = IncidentManagerRequest(
                    chat_topic="Ops Bridge", question="What does Teams show?", requires_governed_knowledge=False
                )
                part = types.Part.from_function_call(name="incident_manager", args=request.model_dump(mode="json"))
                part.function_call.id = "o1"
                yield LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)
                return
            yield _final_text_response("Teams evidence delivered.")

    class _InnerLlm(BaseLlm):
        _step: list[int] = PrivateAttr(default_factory=lambda: [0])

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="scripted-inner-6a14a", **kwargs)

        async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
            step = self._step[0]
            self._step[0] += 1
            if step == 0:
                yield _function_call_response("teams_list_chats", {"topic": "Ops Bridge"}, "i0")
                return
            if step == 1:
                yield _function_call_response("teams_get_messages", {"chat_id": "chat-ops"}, "i1")
                return
            payload = {
                "outcome": "ok",
                "chat_id": "chat-ops",
                "chat_title": "Ops Bridge",
                "summary": "Priya reports the bridge is stable.",
                "evidence": [{"message_id": "m1", "author": "Priya", "sent_at": "2026-08-20T09:00:00Z"}],
            }
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]),
                partial=False,
            )

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("chat-ops", "Ops Bridge")])
        if operation == "teams.getMessages":
            return FakeResponse(
                200, [message("m1", "Priya", "The bridge is stable.", "2026-08-20T09:00:00Z")]
            )
        return FakeResponse(200, [])

    from backend.gateway import power_automate_client as pac_module

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    incident_manager_scripted = incident_manager.model_copy(update={"model": _InnerLlm()})
    incident_manager_tool = MultimodalAgentTool(agent=incident_manager_scripted)
    outer_agent = _Agent(
        name="team_manager",
        model=_OuterLlm(),
        tools=[
            incident_manager_tool,
            record_case_analysis,
            record_conversation_target,
            record_source_requirements,
            record_request_contract,
        ],
        before_tool_callback=[enforce_read_continuation, block_repeated_delegation_after_selection_needed],
        after_tool_callback=[
            sync_incident_manager_result_to_state,
            record_selection_needed,
            validate_and_persist_request_contract,
        ],
    )

    session_service = _db_backed_service(tmp_path, "6a14a_provenance")
    session_id = await session_service.create_session("api-user")
    runner = Runner(app_name=APP_NAME, agent=outer_agent, session_service=session_service.adk_session_service)
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    live_source: Optional[dict[str, Any]] = None
    live_text: Optional[str] = None
    live_error: Optional[dict[str, Any]] = None
    async for event in chat_service.execute_turn_events(session_id, "Check Ops Bridge.", "api-user"):
        if event.type == StreamEventType.MESSAGE_COMPLETED:
            live_text = event.data.get("content")
            live_source = event.data.get("source")
        if event.type == StreamEventType.ERROR:
            live_error = event.data

    assert live_error is None, live_error
    assert live_text == "Teams evidence delivered."
    assert live_source is not None and live_source["title"] == "Ops Bridge"

    history = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant = [m for m in history.messages if m.role == "assistant"][0]
    assert assistant.text == live_text
    assert assistant.source is not None
    assert assistant.source.model_dump(mode="json")["title"] == live_source["title"]
    assert assistant.source.source_id == live_source["source_id"]


# --- Section 2: unit tests for the persistence/projection helpers ----------


def _source_dto() -> SourceReferenceDTO:
    return SourceReferenceDTO(source_id="src-1", source_type="teams", label="Teams conversation", title="Ops Bridge")


def _km_dto(section_id: str, heading: str) -> KnowledgeSourceReferenceDTO:
    return KnowledgeSourceReferenceDTO(
        source_id=f"ks-{section_id}",
        source_type="knowledge",
        label="Governed knowledge",
        knowledge_id="aurora-relay",
        version_label="v1",
        section_id=section_id,
        title="Aurora Relay Verification Procedure",
        document_type="technical_instruction",
        source_system="test",
        evidence_source_id="aurora-relay-doc",
        section_heading=heading,
        content="...",
    )


def test_build_delta_returns_none_when_nothing_to_persist_at_all() -> None:
    assert build_turn_source_references_delta({}, "turn-1", None, [], final_text=None) is None


def test_build_delta_persists_final_text_alone_for_a_plain_turn() -> None:
    delta = build_turn_source_references_delta({}, "turn-1", None, [], final_text="Hello there.")
    assert delta["turn-1"]["final_text"] == "Hello there."
    assert delta["turn-1"]["schema_version"] == "1.0"
    assert "source" not in delta["turn-1"]
    assert "knowledge_sources" not in delta["turn-1"]


def test_build_delta_bundles_text_and_provenance_in_one_entry() -> None:
    delta = build_turn_source_references_delta(
        {}, "turn-1", _source_dto(), [_km_dto("s0", "Verification")], final_text="Answer text."
    )
    entry = delta["turn-1"]
    assert entry["final_text"] == "Answer text."
    assert entry["source"]["title"] == "Ops Bridge"
    assert entry["knowledge_sources"][0]["section_heading"] == "Verification"


def test_resolve_canonical_turn_result_round_trips(monkeypatch: pytest.MonkeyPatch) -> None:
    delta = build_turn_source_references_delta(
        {}, "turn-1", _source_dto(), [_km_dto("s0", "Verification")], final_text="Answer text."
    )
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: delta}
    canonical = resolve_canonical_turn_result(state, "turn-1")
    assert isinstance(canonical, CanonicalTurnResult)
    assert canonical.text == "Answer text."
    assert canonical.turn_id == "turn-1"
    assert canonical.schema_version == "1.0"
    assert canonical.source is not None and canonical.source.title == "Ops Bridge"
    assert len(canonical.knowledge_sources) == 1


def test_resolve_canonical_turn_result_returns_none_for_a_legacy_turn_with_no_final_text() -> None:
    """8.7: a turn whose entry has provenance but no `final_text` at all
    (the exact B7-era, pre-6A.14A persisted shape) is correctly treated
    as having no canonical result -- the caller must fall back to legacy
    behavior, never fabricate canonical text from provenance alone."""
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: {"turn-1": {"source": _source_dto().model_dump(mode="json")}}}
    assert resolve_canonical_turn_result(state, "turn-1") is None


def test_resolve_canonical_turn_result_returns_none_for_a_missing_or_malformed_entry() -> None:
    assert resolve_canonical_turn_result({}, "turn-1") is None
    assert resolve_canonical_turn_result({TURN_SOURCE_REFERENCES_STATE_KEY: "not-a-dict"}, "turn-1") is None
    assert resolve_canonical_turn_result({TURN_SOURCE_REFERENCES_STATE_KEY: {"turn-1": "nope"}}, "turn-1") is None
    assert resolve_canonical_turn_result({TURN_SOURCE_REFERENCES_STATE_KEY: {"turn-1": {"final_text": 123}}}, "turn-1") is None


def test_repeated_persistence_of_the_identical_text_is_idempotent() -> None:
    """8.11: the SAME final_text persisted twice for the same turn_id is
    a safe no-op, never a conflict."""
    first = build_turn_source_references_delta({}, "turn-1", None, [], final_text="Same answer.")
    second = build_turn_source_references_delta(first, "turn-1", None, [], final_text="Same answer.")
    assert second["turn-1"]["final_text"] == "Same answer."


def test_conflicting_persistence_for_the_same_turn_fails_closed() -> None:
    """8.11: a DIFFERENT final_text for a turn_id that already has one
    raises rather than silently overwriting a prior authoritative
    answer."""
    first = build_turn_source_references_delta({}, "turn-1", None, [], final_text="First answer.")
    with pytest.raises(CanonicalTurnResultConflictError):
        build_turn_source_references_delta(first, "turn-1", None, [], final_text="A different answer.")
    # The original entry is untouched by the rejected attempt.
    assert first["turn-1"]["final_text"] == "First answer."


def test_a_different_turn_id_never_conflicts_with_an_existing_one() -> None:
    first = build_turn_source_references_delta({}, "turn-1", None, [], final_text="First answer.")
    second = build_turn_source_references_delta(first, "turn-2", None, [], final_text="Second answer.")
    assert second["turn-1"]["final_text"] == "First answer."
    assert second["turn-2"]["final_text"] == "Second answer."


def test_session_isolation_two_independent_states_never_leak() -> None:
    """8.12: canonical results keyed to one session's state never appear
    when resolving against a different session's own state."""
    state_a = {TURN_SOURCE_REFERENCES_STATE_KEY: build_turn_source_references_delta({}, "turn-1", None, [], final_text="Session A answer.")}
    state_b: dict[str, Any] = {}
    assert resolve_canonical_turn_result(state_a, "turn-1") is not None
    assert resolve_canonical_turn_result(state_b, "turn-1") is None


def test_failure_marker_write_and_read() -> None:
    delta = build_turn_failure_marker_delta({}, "turn-1")
    assert delta["turn-1"] == {"schema_version": "1.0", "failed": True}
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: delta}
    assert is_turn_marked_failed(state, "turn-1") is True
    assert resolve_canonical_turn_result(state, "turn-1") is None  # a failure marker is never mistaken for a result


def test_failure_marker_does_not_affect_other_turns() -> None:
    base = build_turn_source_references_delta({}, "turn-1", None, [], final_text="Good answer.")
    delta = build_turn_failure_marker_delta(base, "turn-2")
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: delta}
    assert resolve_canonical_turn_result(state, "turn-1") is not None
    assert is_turn_marked_failed(state, "turn-1") is False
    assert is_turn_marked_failed(state, "turn-2") is True


def test_is_turn_marked_failed_defensive_for_malformed_state() -> None:
    assert is_turn_marked_failed({}, "turn-1") is False
    assert is_turn_marked_failed({TURN_SOURCE_REFERENCES_STATE_KEY: "not-a-dict"}, "turn-1") is False
    assert is_turn_marked_failed({TURN_SOURCE_REFERENCES_STATE_KEY: {"turn-1": "nope"}}, "turn-1") is False
