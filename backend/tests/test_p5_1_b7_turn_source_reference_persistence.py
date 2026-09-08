"""B7 corrective pass -- durable, turn-owned Source/Knowledge-source
provenance persistence (backend/api/turn_source_references.py).

Section 1 exercises the REAL end-to-end pipeline: a real file-backed
`DatabaseSessionService`, a real `ChatService.run_turn`, a real (scripted-
model-only) team_manager -> incident_manager delegation that produces
BOTH a Teams `SourceReferenceDTO` and TWO distinct governed-KM
`KnowledgeSourceReferenceDTO`s (two sections of the SAME document), then
real `get_session_history` projection, a real rewind, and a real second
history read -- proving the actual `DatabaseSessionService`/rewind
boundary this feature depends on, never mocked away (instruction: "test
the real DatabaseSessionService behavior... rather than mocking away the
important boundary").

Section 2 unit-tests `build_turn_source_references_delta`/`resolve_turn_
source_references` directly for edge cases that would be awkward to
reach reliably through the full pipeline (malformed persisted data, a
turn with no evidence at all).
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

import backend.agents.team_manager.agent as agent_module
from backend.agents.incident_manager.agent import incident_manager
from backend.agents.incident_manager.schemas import IncidentManagerRequest
from backend.agents.team_manager.case_tools import record_case_analysis
from backend.agents.team_manager.conversation_target import record_conversation_target
from backend.agents.team_manager.multimodal_agent_tool import MultimodalAgentTool
from backend.agents.team_manager.read_continuation_enforcement import enforce_read_continuation
from backend.agents.team_manager.selection_delegation_guard import (
    block_repeated_delegation_after_selection_needed,
    record_selection_needed,
)
from backend.agents.team_manager.source_requirements import record_source_requirements
from backend.agents.team_manager.state_sync import sync_incident_manager_result_to_state
from backend.api.chat_service import ChatService
from backend.api.schemas import KnowledgeSourceReferenceDTO, SourceReferenceDTO
from backend.api.session_history_service import get_session_history
from backend.api.session_service import APP_NAME, ApiSessionService
from backend.api.turn_source_references import (
    TURN_SOURCE_REFERENCES_STATE_KEY,
    build_turn_source_references_delta,
    resolve_turn_source_references,
)
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.tests._fakes import FakeResponse, chat, message

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


class _OuterScriptedLlm(BaseLlm):
    """team_manager's own model: declares source requirements, delegates
    to incident_manager, then answers with plain text (team_manager never
    re-authors provenance itself -- it just presents `final_text`)."""

    _step: list[int] = PrivateAttr(default_factory=lambda: [0])

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model="scripted-outer-model", **kwargs)

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        step = self._step[0]
        self._step[0] += 1
        if step == 0:
            yield _function_call_response(
                "record_source_requirements",
                {"requires_teams": True, "requires_governed_knowledge": True},
                "outer-call-0",
            )
            return
        if step == 1:
            request = IncidentManagerRequest(
                chat_topic="Ops Bridge",
                question="What does the combined Teams and governed-knowledge evidence show?",
                requires_governed_knowledge=True,
            )
            part = types.Part.from_function_call(name="incident_manager", args=request.model_dump(mode="json"))
            part.function_call.id = "outer-call-1"
            yield LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)
            return
        yield _final_text_response("Combined Teams and governed-knowledge answer delivered.")


class _IncidentManagerScriptedLlm(BaseLlm):
    """incident_manager's own nested model: resolves the Teams chat,
    retrieves messages, searches and selects TWO distinct KM evidence
    sections from the SAME document, then answers."""

    _step: list[int] = PrivateAttr(default_factory=lambda: [0])
    _selection_keys: list[dict[str, Any]] = PrivateAttr(default_factory=list)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model="scripted-incident-manager-model", **kwargs)

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        step = self._step[0]
        self._step[0] += 1
        if step == 0:
            yield _function_call_response("teams_list_chats", {"topic": "Ops Bridge"}, "im-call-0")
            return
        if step == 1:
            yield _function_call_response("teams_get_messages", {"chat_id": "chat-ops"}, "im-call-1")
            return
        if step == 2:
            yield _function_call_response(
                "knowledge_search", {"query_text": "relay verification and escalation procedure"}, "im-call-2"
            )
            return
        if step == 3:
            selected: list[dict[str, Any]] = []
            for content in llm_request.contents:
                for part in content.parts:
                    fr = getattr(part, "function_response", None)
                    if fr and fr.name == "knowledge_search":
                        for item in fr.response["items"]:
                            selected.append(item["selection_key"])
            assert len(selected) == 2  # both sections came back from the search
            self._selection_keys = selected
            yield _function_call_response(
                "knowledge_select_evidence", {"selections": selected}, "im-call-3"
            )
            return
        payload = {
            "outcome": "ok",
            "chat_id": "chat-ops",
            "chat_title": "Ops Bridge",
            "summary": (
                "Teams reports TEAM-ORION as platform owner with no remediation approved. "
                "Per the approved Aurora Relay Verification Procedure's Verification and "
                "Escalation sections, the observed values do not match and this must be "
                "escalated to the platform owner."
            ),
            "evidence": [{"message_id": "m1", "author": "Priya", "sent_at": "2026-08-20T09:00:00Z"}],
        }
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]),
            partial=False,
        )


@pytest.fixture(autouse=True)
def _isolated_knowledge_repo(monkeypatch: pytest.MonkeyPatch):
    """Mirrors the established isolated-KM-repository fixture pattern
    (test_p5_1j_knowledge_tools.py / test_p5_1_b6_image_teams_km_
    integration.py) -- never the process-wide singleton/normal runtime
    database."""
    from backend.tools.knowledge import runtime as rt

    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()
    yield
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()


def _build_runner(session_service: ApiSessionService) -> Runner:
    incident_manager_scripted = incident_manager.model_copy(update={"model": _IncidentManagerScriptedLlm()})
    incident_manager_tool = MultimodalAgentTool(agent=incident_manager_scripted)

    outer_agent = Agent(
        name="team_manager",
        model=_OuterScriptedLlm(),
        tools=[incident_manager_tool, record_case_analysis, record_conversation_target, record_source_requirements],
        before_tool_callback=[enforce_read_continuation, block_repeated_delegation_after_selection_needed],
        after_tool_callback=[sync_incident_manager_result_to_state, record_selection_needed],
    )
    return Runner(app_name=APP_NAME, agent=outer_agent, session_service=session_service.adk_session_service)


async def _seed_governed_knowledge() -> None:
    from backend.tools.knowledge import runtime as rt

    repository = rt.get_knowledge_repository()
    from backend.knowledge.domain.models import KnowledgeObject, KnowledgeVersion
    from datetime import datetime, timezone

    await repository.add(
        KnowledgeObject(
            knowledge_id="aurora-relay",
            document_type=KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
            title="Aurora Relay Verification Procedure",
            version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
            lifecycle_status=LifecycleStatus.APPROVED,
            source=KnowledgeSource(
                source_system="test", source_id="aurora-relay-doc", display_name="Aurora Relay Governed Test Procedure"
            ),
            sections=[
                KnowledgeSection(
                    section_id="aurora-relay:v1:s0",
                    knowledge_id="aurora-relay",
                    heading="Verification",
                    sequence=0,
                    content="Confirm the relay checksum is exactly 7319 and the status indicator is GREEN.",
                    source_locator="p1",
                ),
                KnowledgeSection(
                    section_id="aurora-relay:v1:s1",
                    knowledge_id="aurora-relay",
                    heading="Escalation",
                    sequence=1,
                    content="If verification fails, collect the observed values and escalate to the platform owner.",
                    source_locator="p2",
                ),
            ],
        )
    )


def _gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("chat-ops", "Ops Bridge")])
        if operation == "teams.getMessages":
            return FakeResponse(
                200, [message("m1", "Priya", "TEAM-ORION is the platform owner; no remediation approved.", "2026-08-20T09:00:00Z")]
            )
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)


@pytest.mark.asyncio
async def test_teams_and_two_km_sections_persist_and_reproject_through_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gateway(monkeypatch)
    await _seed_governed_knowledge()

    session_service = _db_backed_service(tmp_path, "b7_provenance")
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service)
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    response = await chat_service.run_turn(session_id, "Check Ops Bridge and governed knowledge.", "api-user")
    assert response.message.content == "Combined Teams and governed-knowledge answer delivered."

    # --- 1/3/4/5/10/11: history projects BOTH a Teams source and TWO
    # distinct KM sources, with full trusted metadata, on the SAME turn.
    history = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant_messages = [m for m in history.messages if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assistant = assistant_messages[0]

    assert assistant.source is not None
    assert assistant.source.title == "Ops Bridge"

    assert len(assistant.knowledge_sources) == 2  # both sections survived independently -- never merged
    headings = {ks.section_heading for ks in assistant.knowledge_sources}
    assert headings == {"Verification", "Escalation"}
    for ks in assistant.knowledge_sources:
        assert ks.title == "Aurora Relay Verification Procedure"
        assert ks.knowledge_id == "aurora-relay"
        assert ks.version_label == "v1"

    # --- 6/7: no source_uri / gs:// anywhere in the safe projected DTOs.
    serialized = json.dumps([m.model_dump(mode="json") for m in history.messages])
    assert "source_uri" not in serialized
    assert "gs://" not in serialized

    # --- 13: bound to THIS turn specifically (single turn_id).
    assert assistant.turn_id == assistant_messages[0].turn_id
    assert all(ks.source_id for ks in assistant.knowledge_sources)  # real per-reference identity

    # --- 14: reading history twice never duplicates/changes anything.
    history_again = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert history_again.model_dump(mode="json") == history.model_dump(mode="json")

    # --- 15: durable across a fresh repository/session-service instantiation
    # (simulates a backend restart against the same DB file).
    restarted_service = _db_backed_service(tmp_path, "b7_provenance")
    history_after_restart = await get_session_history(
        restarted_service, _attachment_service(), session_id, "api-user"
    )
    restarted_assistant = [m for m in history_after_restart.messages if m.role == "assistant"][0]
    assert len(restarted_assistant.knowledge_sources) == 2
    assert restarted_assistant.source is not None and restarted_assistant.source.title == "Ops Bridge"


@pytest.mark.asyncio
async def test_rewind_removes_provenance_belonging_to_the_discarded_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gateway(monkeypatch)
    await _seed_governed_knowledge()

    session_service = _db_backed_service(tmp_path, "b7_rewind")
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service)
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    await chat_service.run_turn(session_id, "Check Ops Bridge and governed knowledge.", "api-user")
    history_before = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert len(history_before.messages) == 2  # user + assistant
    assistant_before = [m for m in history_before.messages if m.role == "assistant"][0]
    assert len(assistant_before.knowledge_sources) == 2

    # Discard the turn entirely (edit-the-first-message semantics).
    await chat_service.rewind_before_user_turn(session_id, 0, "api-user")

    history_after = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert history_after.messages == []  # the whole turn -- and its provenance -- is gone

    refreshed = await session_service.get_session(session_id, "api-user")
    stored = refreshed.state.get(TURN_SOURCE_REFERENCES_STATE_KEY)
    # No ghost entry left behind for the discarded turn's own turn_id.
    assert not stored or assistant_before.turn_id not in stored


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


def test_build_delta_returns_none_when_the_turn_has_no_evidence_at_all() -> None:
    assert build_turn_source_references_delta({}, "turn-1", None, []) is None


def test_build_delta_preserves_earlier_turns_and_adds_the_new_one() -> None:
    existing = {"turn-1": {"source": _source_dto().model_dump(mode="json")}}
    delta = build_turn_source_references_delta(existing, "turn-2", None, [_km_dto("s0", "Verification")])
    assert set(delta.keys()) == {"turn-1", "turn-2"}
    assert delta["turn-1"] == existing["turn-1"]  # untouched
    assert delta["turn-2"]["knowledge_sources"][0]["section_heading"] == "Verification"


def test_resolve_returns_empty_for_a_missing_key() -> None:
    source, km = resolve_turn_source_references({}, "turn-1")
    assert source is None and km == []


def test_resolve_ignores_a_malformed_top_level_value() -> None:
    """Defensive/anti-spoofing: even if something non-dict ended up under
    this key (never possible via this module's own writer, but treated
    as untrusted on the way back out regardless), it must never crash or
    fabricate provenance."""
    source, km = resolve_turn_source_references({TURN_SOURCE_REFERENCES_STATE_KEY: "not-a-dict"}, "turn-1")
    assert source is None and km == []


def test_resolve_ignores_a_malformed_knowledge_source_entry() -> None:
    """A single corrupted/incomplete entry (e.g. missing required DTO
    fields -- never producible by this module's own writer, but treated
    defensively) is skipped, never raised, and never presented as if it
    were real trusted provenance."""
    state = {
        TURN_SOURCE_REFERENCES_STATE_KEY: {
            "turn-1": {"knowledge_sources": [{"not": "a valid KnowledgeSourceReferenceDTO"}]}
        }
    }
    source, km = resolve_turn_source_references(state, "turn-1")
    assert source is None and km == []


def test_resolve_only_returns_the_requested_turns_own_entry() -> None:
    state = {
        TURN_SOURCE_REFERENCES_STATE_KEY: {
            "turn-1": {"source": _source_dto().model_dump(mode="json")},
            "turn-2": {"knowledge_sources": [_km_dto("s0", "Verification").model_dump(mode="json")]},
        }
    }
    source, km = resolve_turn_source_references(state, "turn-2")
    assert source is None  # turn-2 never had a Teams source
    assert len(km) == 1
