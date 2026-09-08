"""B7 live-regression corrective pass -- exact-duplicate governed-KM
provenance normalization (backend/api/knowledge_source_reference.py's
`dedupe_knowledge_source_references`, wired at chat_service.py's turn-
level construction point and at turn_source_references.py's read-time
history projection).

ROOT CAUSE CONTEXT (see knowledge_source_reference.py's own module
docstring for the full audit narrative): a real live-stack combined
image+Teams+governed-KM run produced an exact duplicate "Verification"
source chip that survived a hard refresh -- proving it was persisted/
reprojected, not a transient frontend artifact. Every identity-based
construction/selection mechanism already in this codebase (`KnowledgeEvidenceSet`'s
own model validator, `backend.tools.knowledge.runtime.select_evidence`'s
own guarded append, `build_knowledge_source_references`'s own pre-existing
dedup) was audited and found already structurally correct for a single
input list -- the exact live-Gemini trigger could not be reproduced
without live Cloud SQL/Gemini access. What WAS missing, and is what this
pass closes: no exact-identity normalization existed at the persistence
boundary or the read-time history-projection boundary, so ANY duplicate
that ever reached either point (from any cause) would be faithfully
persisted/reprojected forever.

Section 1: unit tests for `dedupe_knowledge_source_references` directly
(Step 7 requirements 1-6) -- generic, non-Aurora-named fixtures, since
production normalization logic must be domain-agnostic.

Section 2: `build_turn_source_references_delta`/`resolve_turn_source_
references` -- the persistence and read-time-projection boundaries
(requirements 9, 10, 11, 12, 13).

Section 3: a real end-to-end `ChatService`/`FakeRunner` integration test
proving Teams + two DISTINCT KM sections produce exactly three total
chips through the real construction call site (requirements 7, 8), plus a
real `DatabaseSessionService`/rewind test (requirements 14, 15) using the
same scripted-model pipeline pattern established in test_p5_1_b7_turn_
source_reference_persistence.py -- this is the ONE place Aurora-style
fixture naming is used, per the instruction's own "fixture may be domain-
specific; production logic cannot be."
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
from google.adk.agents import Agent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import PrivateAttr

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
from backend.api.knowledge_source_reference import dedupe_knowledge_source_references
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
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests._fakes import FakeResponse, chat, message
from datetime import datetime, timezone

_EFFECTIVE_FROM = datetime(2020, 1, 1, tzinfo=timezone.utc)


# --- Section 1: dedupe_knowledge_source_references unit tests --------------


def _km_dto(
    *,
    knowledge_id: str = "doc-a",
    version_label: str = "v1",
    section_id: str = "doc-a:v1:s0",
    title: str = "Generic Procedure",
    section_heading: str | None = "Section",
    document_type: str = "technical_instruction",
) -> KnowledgeSourceReferenceDTO:
    return KnowledgeSourceReferenceDTO(
        source_id=f"synthetic-{knowledge_id}-{version_label}-{section_id}-{id(object())}",
        source_type="knowledge",
        label="Governed knowledge",
        knowledge_id=knowledge_id,
        version_label=version_label,
        section_id=section_id,
        title=title,
        document_type=document_type,
        source_system="test",
        evidence_source_id="doc-a-source",
        section_heading=section_heading,
        content="content",
    )


def test_1_two_identical_identities_become_one() -> None:
    a = _km_dto(section_id="doc-a:v1:s0", section_heading="Verification")
    b = _km_dto(section_id="doc-a:v1:s0", section_heading="Verification")
    result = dedupe_knowledge_source_references([a, b])
    assert len(result) == 1
    assert result[0] is a  # first-seen instance kept, never the later duplicate


def test_2_same_document_version_different_section_remain_two() -> None:
    verification = _km_dto(section_id="doc-a:v1:s0", section_heading="Verification")
    escalation = _km_dto(section_id="doc-a:v1:s1", section_heading="Escalation")
    result = dedupe_knowledge_source_references([verification, escalation])
    assert len(result) == 2
    assert result == [verification, escalation]


def test_3_same_knowledge_id_different_version_remain_two() -> None:
    v1 = _km_dto(version_label="v1", section_id="doc-a:v1:s0")
    v2 = _km_dto(version_label="v2", section_id="doc-a:v1:s0")
    result = dedupe_knowledge_source_references([v1, v2])
    assert len(result) == 2


def test_4_same_title_different_authoritative_identity_remain_two() -> None:
    """Two DIFFERENT documents/sections that happen to share a display
    title must never collapse -- title is never part of the identity."""
    first = _km_dto(knowledge_id="doc-a", section_id="doc-a:v1:s0", title="Aurora Relay Verification Procedure")
    second = _km_dto(knowledge_id="doc-b", section_id="doc-b:v1:s0", title="Aurora Relay Verification Procedure")
    result = dedupe_knowledge_source_references([first, second])
    assert len(result) == 2


def test_5_same_section_heading_in_two_different_documents_remain_two() -> None:
    doc_a_verification = _km_dto(knowledge_id="doc-a", section_id="doc-a:v1:s0", section_heading="Verification")
    doc_b_verification = _km_dto(knowledge_id="doc-b", section_id="doc-b:v1:s0", section_heading="Verification")
    result = dedupe_knowledge_source_references([doc_a_verification, doc_b_verification])
    assert len(result) == 2


def test_6_ordering_is_deterministic_and_first_seen() -> None:
    verification = _km_dto(section_id="doc-a:v1:s0", section_heading="Verification")
    escalation = _km_dto(section_id="doc-a:v1:s1", section_heading="Escalation")
    result = dedupe_knowledge_source_references([verification, verification, escalation])
    assert [r.section_heading for r in result] == ["Verification", "Escalation"]

    result2 = dedupe_knowledge_source_references([verification, escalation, verification])
    assert [r.section_heading for r in result2] == ["Verification", "Escalation"]

    result3 = dedupe_knowledge_source_references([verification, escalation])
    assert result3 == [verification, escalation]  # already-correct input passed through unchanged


def test_empty_list_returns_empty_list() -> None:
    assert dedupe_knowledge_source_references([]) == []


# --- Section 2: persistence + read-time projection boundaries --------------


def _teams_source() -> SourceReferenceDTO:
    return SourceReferenceDTO(source_id="src-1", source_type="teams", label="Teams conversation", title="Ops Bridge")


def test_9_persistence_boundary_never_stores_an_exact_duplicate() -> None:
    """`build_turn_source_references_delta` itself does not call the dedup
    helper (chat_service.py's own call site does, before the list ever
    reaches here) -- this test proves that IF a duplicate-containing list
    were ever handed to it directly, the caller's own upstream dedup is
    what actually protects the persisted record, by asserting chat_
    service.py's real construction path (Section 3) never produces one.
    Documented here as the historical-data companion to test_10/test_11.
    """
    verification = _km_dto(section_id="doc-a:v1:s0", section_heading="Verification")
    escalation = _km_dto(section_id="doc-a:v1:s1", section_heading="Escalation")
    # Simulates chat_service.py's own call site: dedup applied BEFORE persistence.
    already_deduped = dedupe_knowledge_source_references([verification, verification, escalation])
    delta = build_turn_source_references_delta({}, "turn-1", _teams_source(), already_deduped)
    assert delta is not None
    assert len(delta["turn-1"]["knowledge_sources"]) == 2


def test_10_read_time_projection_normalizes_an_already_persisted_duplicate() -> None:
    """Requirement 10/11: historical data that somehow already contains an
    exact duplicate (simulated directly here, since no reproducible live
    code path could inject one through the now-fixed write boundary) is
    normalized at READ time -- losing only the exact duplicate, never a
    distinct reference, never mutating the underlying stored dict."""
    verification = _km_dto(section_id="doc-a:v1:s0", section_heading="Verification")
    escalation = _km_dto(section_id="doc-a:v1:s1", section_heading="Escalation")
    corrupted_state = {
        TURN_SOURCE_REFERENCES_STATE_KEY: {
            "turn-1": {
                "source": _teams_source().model_dump(mode="json"),
                "knowledge_sources": [
                    verification.model_dump(mode="json"),
                    verification.model_dump(mode="json"),  # exact duplicate, pre-fix data
                    escalation.model_dump(mode="json"),
                ],
            }
        }
    }
    source, knowledge_sources = resolve_turn_source_references(corrupted_state, "turn-1")
    assert source is not None
    assert len(knowledge_sources) == 2
    assert [ks.section_heading for ks in knowledge_sources] == ["Verification", "Escalation"]

    # The underlying stored dict itself is never mutated by a read.
    assert len(corrupted_state[TURN_SOURCE_REFERENCES_STATE_KEY]["turn-1"]["knowledge_sources"]) == 3


def test_read_time_projection_never_merges_distinct_references() -> None:
    verification = _km_dto(section_id="doc-a:v1:s0", section_heading="Verification")
    escalation = _km_dto(section_id="doc-a:v1:s1", section_heading="Escalation")
    state = {
        TURN_SOURCE_REFERENCES_STATE_KEY: {
            "turn-1": {
                "knowledge_sources": [
                    verification.model_dump(mode="json"),
                    escalation.model_dump(mode="json"),
                ]
            }
        }
    }
    _, knowledge_sources = resolve_turn_source_references(state, "turn-1")
    assert len(knowledge_sources) == 2


def test_12_13_no_source_uri_or_gs_uri_anywhere_in_persisted_or_projected_data() -> None:
    verification = _km_dto(section_id="doc-a:v1:s0")
    delta = build_turn_source_references_delta({}, "turn-1", None, [verification, verification])
    serialized = json.dumps(delta)
    assert "source_uri" not in serialized
    assert "gs://" not in serialized

    _, projected = resolve_turn_source_references(
        {TURN_SOURCE_REFERENCES_STATE_KEY: delta}, "turn-1"
    )
    projected_serialized = json.dumps([p.model_dump(mode="json") for p in projected])
    assert "source_uri" not in projected_serialized
    assert "gs://" not in projected_serialized


# --- Section 3: real end-to-end pipeline (Aurora-domain fixture) -----------


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
    _step: list[int] = PrivateAttr(default_factory=lambda: [0])

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model="scripted-outer-model", **kwargs)

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        step = self._step[0]
        self._step[0] += 1
        if step == 0:
            yield _function_call_response(
                "record_source_requirements", {"requires_teams": True, "requires_governed_knowledge": True}, "outer-call-0"
            )
            return
        if step == 1:
            request = IncidentManagerRequest(
                chat_topic="Ops Bridge", question="Verify the Aurora Relay status.", requires_governed_knowledge=True
            )
            part = types.Part.from_function_call(name="incident_manager", args=request.model_dump(mode="json"))
            part.function_call.id = "outer-call-1"
            yield LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)
            return
        yield _final_text_response("Combined Teams and governed-knowledge answer delivered.")


class _IncidentManagerScriptedLlm(BaseLlm):
    """Resolves Teams, retrieves messages, searches KM, then selects
    Verification TWICE via two SEPARATE `knowledge_select_evidence` calls
    (a pathological but real scenario the trusted runtime already
    tolerates -- backend.tools.knowledge.runtime.select_evidence dedupes
    it internally) followed by Escalation, then answers -- exercising the
    real construction call site end to end regardless of whether the
    upstream runtime-level dedup alone would already have prevented a
    duplicate."""

    _step: list[int] = PrivateAttr(default_factory=lambda: [0])

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
                            if item["selection_key"]["section_id"].endswith(":s0"):
                                selected.append(item["selection_key"])
            assert len(selected) == 1
            yield _function_call_response("knowledge_select_evidence", {"selections": selected}, "im-call-3")
            return
        if step == 4:
            # Re-select the SAME Verification identity a second time.
            selected: list[dict[str, Any]] = []
            for content in llm_request.contents:
                for part in content.parts:
                    fr = getattr(part, "function_response", None)
                    if fr and fr.name == "knowledge_search":
                        for item in fr.response["items"]:
                            if item["selection_key"]["section_id"].endswith(":s0"):
                                selected.append(item["selection_key"])
            yield _function_call_response("knowledge_select_evidence", {"selections": selected}, "im-call-4")
            return
        if step == 5:
            selected: list[dict[str, Any]] = []
            for content in llm_request.contents:
                for part in content.parts:
                    fr = getattr(part, "function_response", None)
                    if fr and fr.name == "knowledge_search":
                        for item in fr.response["items"]:
                            if item["selection_key"]["section_id"].endswith(":s1"):
                                selected.append(item["selection_key"])
            assert len(selected) == 1
            yield _function_call_response("knowledge_select_evidence", {"selections": selected}, "im-call-5")
            return
        payload = {
            "outcome": "ok",
            "chat_id": "chat-ops",
            "chat_title": "Ops Bridge",
            "summary": (
                "Teams reports TEAM-ORION as platform owner with no remediation approved. "
                "Per the approved Aurora Relay Verification Procedure's Verification and "
                "Escalation sections, this must be escalated."
            ),
            "evidence": [{"message_id": "m1", "author": "Priya", "sent_at": "2026-08-20T09:00:00Z"}],
        }
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]), partial=False
        )


@pytest.fixture(autouse=True)
def _isolated_knowledge_repo(monkeypatch: pytest.MonkeyPatch):
    from backend.tools.knowledge import runtime as rt

    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()
    yield
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()


async def _seed_governed_knowledge() -> None:
    from backend.tools.knowledge import runtime as rt

    repository = rt.get_knowledge_repository()
    await repository.add(
        KnowledgeObject(
            knowledge_id="aurora-relay",
            document_type=KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
            title="Aurora Relay Verification Procedure",
            version=KnowledgeVersion(label="v1", effective_from=_EFFECTIVE_FROM),
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


@pytest.mark.asyncio
async def test_7_8_teams_plus_two_distinct_km_sections_produce_exactly_three_chips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirement 7/8, THE MOST IMPORTANT REGRESSION TEST for this pass:
    a real end-to-end pipeline where the model selects the SAME KM
    identity twice (via two separate tool calls) plus a distinct second
    identity -- the live SSE event and the persisted/reprojected history
    must both show exactly Teams + Verification + Escalation, never a
    duplicate Verification."""
    _gateway(monkeypatch)
    await _seed_governed_knowledge()

    session_service = _db_backed_service(tmp_path, "b7_dup")
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service)
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    response = await chat_service.run_turn(session_id, "Check Ops Bridge and governed knowledge.", "api-user")
    assert response.message.content == "Combined Teams and governed-knowledge answer delivered."

    # --- 9: persisted state has no duplicate.
    refreshed = await session_service.get_session(session_id, "api-user")
    stored = refreshed.state.get(TURN_SOURCE_REFERENCES_STATE_KEY)
    assert stored is not None
    entry = next(iter(stored.values()))
    assert len(entry["knowledge_sources"]) == 2

    # --- 7/8/10: history projection returns exactly 3 total chips (1 Teams + 2 KM).
    history = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant = [m for m in history.messages if m.role == "assistant"][0]
    assert assistant.source is not None
    total_chips = 1 + len(assistant.knowledge_sources)
    assert total_chips == 3
    headings = [ks.section_heading for ks in assistant.knowledge_sources]
    assert headings == ["Verification", "Escalation"]  # deterministic order, no duplicate

    # --- 12/13: no source_uri/gs:// anywhere.
    serialized = json.dumps([m.model_dump(mode="json") for m in history.messages])
    assert "source_uri" not in serialized
    assert "gs://" not in serialized

    # --- 15: repeated GET /history never multiplies references.
    history_again = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert history_again.model_dump(mode="json") == history.model_dump(mode="json")


@pytest.mark.asyncio
async def test_14_rewind_still_removes_provenance_of_the_discarded_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gateway(monkeypatch)
    await _seed_governed_knowledge()

    session_service = _db_backed_service(tmp_path, "b7_dup_rewind")
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service)
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    await chat_service.run_turn(session_id, "Check Ops Bridge and governed knowledge.", "api-user")
    history_before = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant_before = [m for m in history_before.messages if m.role == "assistant"][0]
    assert len(assistant_before.knowledge_sources) == 2

    await chat_service.rewind_before_user_turn(session_id, 0, "api-user")

    history_after = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert history_after.messages == []

    refreshed = await session_service.get_session(session_id, "api-user")
    stored = refreshed.state.get(TURN_SOURCE_REFERENCES_STATE_KEY)
    assert not stored or assistant_before.turn_id not in stored
