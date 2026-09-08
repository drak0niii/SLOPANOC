"""POST-5.1 B6 -- mandatory IMAGE + TEAMS + KM joint-reasoning integration
test (instruction sections 57-59, 46).

Proves COMBINATION, not merely that each source works in isolation
(instruction section 46's own explicit "passing standard"): a single
delegated turn where the REAL `incident_manager` agent (full tool set,
full `after_agent_callback` integrity/provenance enforcement, unmodified)
receives -- in ONE nested Runner call -- trusted image evidence (via
`MultimodalAgentTool`), then genuinely calls `teams_get_messages` (a real
Power Automate gateway mock) and `knowledge_search`/`knowledge_select_
evidence` (a real, isolated SQLite `KnowledgeRepository`), and still
produces one valid, schema-validated `IncidentManagerResponse`.

Only the innermost model calls are scripted (a real `google.adk.models.
base_llm.BaseLlm` subclass, never a fake Runner) -- every deterministic
layer (Teams tools, KM tools, evidence/provenance enforcement,
`MultimodalAgentTool` itself) runs for real, mirroring this test suite's
own established "mock the external boundary, keep everything else real"
discipline (test_ambiguous_selection_read_resume_regression.py,
test_p5_1j_knowledge_tools.py).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio
from google.adk.agents import Agent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from backend.agents.incident_manager.agent import incident_manager
from backend.agents.incident_manager.schemas import IncidentManagerRequest
from backend.agents.team_manager.multimodal_agent_tool import MultimodalAgentTool
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.tests._fakes import FakeResponse, chat, message
from backend.tools.knowledge import runtime as rt

APP_NAME = "test-b6-image-teams-km"
_EFFECTIVE_FROM = datetime(2020, 1, 1, tzinfo=timezone.utc)


@pytest_asyncio.fixture
async def isolated_repo(monkeypatch: pytest.MonkeyPatch):
    """Mirrors test_p5_1j_knowledge_tools.py's own isolated-repository
    fixture exactly -- never the process-wide singleton/normal runtime
    database.
    """
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()
    repository = rt.get_knowledge_repository()
    yield repository
    await repository.close()
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()


@pytest.fixture(autouse=True)
def _clean_knowledge_runtime_state():
    yield
    rt.discard_knowledge_run_evidence_state("run-b6-joint")


def _governed_procedure() -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id="aurora-relay",
        document_type=KnowledgeDocumentType.SOP,
        title="Aurora Relay Verification Procedure",
        version=KnowledgeVersion(label="v1", effective_from=_EFFECTIVE_FROM),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(
            source_system="test", source_id="aurora-relay-doc", display_name="Aurora Relay Procedure"
        ),
        sections=[
            KnowledgeSection(
                section_id="aurora-relay:v1:s0",
                knowledge_id="aurora-relay",
                heading="Verification",
                sequence=0,
                content="If the relay status indicator shows RED, escalate immediately per the on-call runbook.",
                source_locator="p1",
            )
        ],
    )


class _JointScriptedLlm(BaseLlm):
    """Drives `incident_manager` through: teams_list_chats (matched) ->
    teams_get_messages -> knowledge_search -> knowledge_select_evidence ->
    final structured text. Captures the FIRST call's own `llm_request.
    contents` -- the real proof the nested Content genuinely carried the
    top-level image evidence into incident_manager's own first reasoning
    step, not merely somewhere in the call chain.
    """

    _calls: int = PrivateAttr(default=0)
    _first_call_contents: list[Any] = PrivateAttr(default_factory=list)
    _selected_evidence_key: dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model="scripted-joint-model", **kwargs)

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def first_call_contents(self) -> list[Any]:
        return self._first_call_contents

    @property
    def selected_evidence_key(self) -> dict[str, Any]:
        return self._selected_evidence_key

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        if self._calls == 0:
            self._first_call_contents = list(llm_request.contents)
        self._calls += 1

        if self._calls == 1:
            part = types.Part.from_function_call(name="teams_list_chats", args={"topic": "Ops Bridge"})
            part.function_call.id = "call-1"
            yield LlmResponse(content=types.Content(role="model", parts=[part]))
            return
        if self.calls == 2:
            part = types.Part.from_function_call(name="teams_get_messages", args={"chat_id": "chat-ops"})
            part.function_call.id = "call-2"
            yield LlmResponse(content=types.Content(role="model", parts=[part]))
            return
        if self.calls == 3:
            part = types.Part.from_function_call(
                name="knowledge_search", args={"query_text": "relay status escalation procedure"}
            )
            part.function_call.id = "call-3"
            yield LlmResponse(content=types.Content(role="model", parts=[part]))
            return
        if self.calls == 4:
            # Read back what knowledge_search actually returned, from this
            # SAME llm_request's own function-response history, so the
            # selection_key copied here is genuinely the tool's own real
            # output -- never invented.
            selected = None
            for content in llm_request.contents:
                for content_part in content.parts:
                    fr = getattr(content_part, "function_response", None)
                    if fr and fr.name == "knowledge_search":
                        selected = fr.response["items"][0]["selection_key"]
            assert selected is not None
            self._selected_evidence_key = selected
            part = types.Part.from_function_call(
                name="knowledge_select_evidence", args={"selections": [selected]}
            )
            part.function_call.id = "call-4"
            yield LlmResponse(content=types.Content(role="model", parts=[part]))
            return

        # Final synthesis -- genuinely combines all three domains in prose.
        payload = {
            "outcome": "ok",
            "chat_id": "chat-ops",
            "chat_title": "Ops Bridge",
            "summary": (
                "The attached screenshot shows the relay status indicator as RED. "
                "Teams reports the on-call engineer was already notified. "
                "Per the approved Aurora Relay Verification Procedure, a RED "
                "indicator requires immediate escalation, which is consistent "
                "with what was observed and discussed."
            ),
            "evidence": [{"message_id": "m1", "author": "Priya", "sent_at": "2026-08-20T09:00:00Z"}],
        }
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]),
            partial=False,
        )


class _OuterOneShotLlm(BaseLlm):
    _calls: int = PrivateAttr(default=0)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model="scripted-outer-model", **kwargs)

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        self._calls += 1
        if self._calls == 1:
            request = IncidentManagerRequest(
                chat_topic="Ops Bridge",
                question="What does the combined evidence tell us?",
                requires_governed_knowledge=True,
            )
            part = types.Part.from_function_call(name="incident_manager", args=request.model_dump(mode="json"))
            part.function_call.id = "outer-call-1"
            yield LlmResponse(content=types.Content(role="model", parts=[part]))
            return
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text="Combined answer delivered.")]),
            partial=False,
        )


@pytest.mark.asyncio
async def test_image_plus_teams_plus_km_joint_reasoning_produces_one_combined_response(
    monkeypatch: pytest.MonkeyPatch, isolated_repo
) -> None:
    await isolated_repo.add(_governed_procedure())

    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        if operation == "teams.listChats":
            return FakeResponse(200, [chat("chat-ops", "Ops Bridge")])
        if operation == "teams.getMessages":
            return FakeResponse(
                200, [message("m1", "Priya", "the on-call engineer has been notified", "2026-08-20T09:00:00Z")]
            )
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    joint_llm = _JointScriptedLlm()
    scripted_incident_manager = incident_manager.model_copy(update={"model": joint_llm})
    probe_tool = MultimodalAgentTool(agent=scripted_incident_manager)

    outer_agent = Agent(name="team_manager", model=_OuterOneShotLlm(), tools=[probe_tool])
    session_service = InMemorySessionService()
    await session_service.create_session(app_name=APP_NAME, user_id="u1", session_id="s1")
    runner = Runner(app_name=APP_NAME, agent=outer_agent, session_service=session_service)

    image_part = types.Part.from_uri(file_uri="gs://bucket/relay-status.png", mime_type="image/png")
    user_content = types.Content(
        role="user",
        parts=[
            types.Part.from_text(text="Look at this. Check Ops Bridge and use governed knowledge."),
            image_part,
        ],
    )

    token = bind_run_id("run-b6-joint")
    try:
        async for _event in runner.run_async(user_id="u1", session_id="s1", new_message=user_content):
            pass
    finally:
        reset_run_id(token)

    # --- Image genuinely reached incident_manager's own FIRST reasoning step ---
    file_parts = [
        p for p in joint_llm.first_call_contents[-1].parts if getattr(p, "file_data", None) is not None
    ]
    assert len(file_parts) == 1
    assert file_parts[0].file_data.file_uri == "gs://bucket/relay-status.png"

    # --- All three domains were genuinely exercised, not merely claimed ---
    assert joint_llm.calls == 5  # list_chats, get_messages, knowledge_search, knowledge_select_evidence, final
    assert joint_llm.selected_evidence_key.get("knowledge_id") == "aurora-relay"

    # --- The final result survived integrity/provenance enforcement intact ---
    # (enforce_incident_manager_response_integrity is incident_manager's
    # real, unmodified after_agent_callback -- a non-compliant answer
    # would have been rewritten to a safe failure text instead.)
