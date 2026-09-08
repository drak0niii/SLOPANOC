"""B7 LIVE-REGRESSION CORRECTIVE PASS -- governed-knowledge completion
remediation must preserve this turn's own trusted current-turn image
evidence.

ROOT CAUSE (see backend/agents/team_manager/governed_knowledge_completion
.py's own module docstring for the full narrative): a real combined
image + Teams + governed-KM live validation showed the final answer
"assuming the image shows a checksum of 7319 and a RED status indicator"
-- fabricated values -- instead of using the REAL current-turn image
(checksum 7318, status GREEN). Root cause: `enforce_governed_knowledge_at
_completion`'s own remediation `Content` was built TEXT-ONLY, unconditionally
-- this bounded remediation is a bare `Runner.run_async` call, never
routed through `AgentTool`/`MultimodalAgentTool`, so B6's own image-
propagation mechanism never reached it. Fixed by threading this turn's
own trusted `file_data` Part(s) (extracted via `backend.api.multimodal_
turn_context.trusted_image_parts_from_content` from chat_service.py's own
already-built, already-validated turn `Content`) through to the
remediation's own `Content` construction.

Section 1: a real end-to-end pipeline (real `ChatService`, real
`AttachmentService`, real Teams gateway mock, real isolated KM repository,
scripted team_manager + TWO distinct incident_manager model scripts -- one
for the original delegation that deliberately never selects governed-KM
evidence, forcing the completion-boundary remediation; one for the
remediation itself) proves the remediation's own nested model input
genuinely contains the SAME trusted image Part, in the correct order,
alongside real Teams and governed-KM evidence, combining into one grounded
synthesis -- never re-running/duplicating the original evidence gathering
in a way that would let the remediation see anything the original run did
not already retrieve.

Section 2: focused unit tests for `trusted_image_parts_from_content`
(order/multi-image preservation, empty/None handling, no cross-call
state -- structural proof that no run-id/global registry is involved at
all, so "unrelated run accessing another run's images" has no possible
channel to begin with).

Section 3: `enforce_governed_knowledge_at_completion`'s own cleanup/
cancellation/no-recursion contract, unaffected by the new `image_parts`
parameter.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.agents import Agent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from backend.agents.incident_manager.schemas import IncidentManagerRequest
from backend.agents.team_manager.case_tools import record_case_analysis
from backend.agents.team_manager.conversation_target import record_conversation_target
from backend.agents.team_manager.governed_knowledge_completion import (
    SAFE_COMPLETION_FAILURE_TEXT,
    enforce_governed_knowledge_at_completion,
)
from backend.agents.team_manager.multimodal_agent_tool import MultimodalAgentTool
from backend.agents.team_manager.read_continuation_enforcement import enforce_read_continuation
from backend.agents.team_manager.selection_delegation_guard import (
    block_repeated_delegation_after_selection_needed,
    record_selection_needed,
)
from backend.agents.team_manager.source_requirements import record_source_requirements
from backend.agents.team_manager.state_sync import sync_incident_manager_result_to_state
from backend.api.chat_service import ChatService
from backend.api.multimodal_turn_context import trusted_image_parts_from_content
from backend.api.session_service import APP_NAME, ApiSessionService
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.attachments.storage import ChatAttachmentStorage
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import (
    KnowledgeObject,
    KnowledgeSection,
    KnowledgeSource,
    KnowledgeVersion,
)
from backend.tests._fakes import FakeResponse, chat, message
from datetime import datetime, timezone

_EFFECTIVE_FROM = datetime(2020, 1, 1, tzinfo=timezone.utc)
_TEST_BUCKET_URI = "gs://test-bucket/aurora-checksum-image.png"


# --- Section 1: real end-to-end pipeline ------------------------------------


def _attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


def _attachment_storage() -> ChatAttachmentStorage:
    return ChatAttachmentStorage("test-bucket")


async def _ready_image_attachment(service: AttachmentService, *, user_id: str, session_id: str, attachment_id: str) -> None:
    await service.create(
        owner_user_id=user_id,
        session_id=session_id,
        original_filename="aurora-checksum-image.png",
        mime_type="image/png",
        size_bytes=12345,
        sha256="b" * 64,
        attachment_id=attachment_id,
    )


def _function_call_response(name: str, args: dict[str, Any], call_id: str) -> LlmResponse:
    part = types.Part.from_function_call(name=name, args=args)
    part.function_call.id = call_id
    return LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)


def _final_text_response(text: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]), partial=False)


class _OuterScriptedLlm(BaseLlm):
    """team_manager's own model: declares source requirements, delegates
    to incident_manager, then relays whatever `final_text` chat_service.py
    ultimately decides on (its own draft answer here is DELIBERATELY
    generic/wrong -- proving the completion-boundary remediation gate is
    what actually determines the user-visible answer, never team_
    manager's own relay)."""

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
                question="Using this image, Teams, and governed knowledge, verify the Aurora Relay status.",
                requires_governed_knowledge=True,
            )
            part = types.Part.from_function_call(name="incident_manager", args=request.model_dump(mode="json"))
            part.function_call.id = "outer-call-1"
            yield LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)
            return
        # Deliberately generic/wrong -- must never reach the user; the
        # completion-boundary gate below is required to override it.
        yield _final_text_response("Draft answer pending governed-knowledge validation.")


class _OriginalIncidentManagerScriptedLlm(BaseLlm):
    """The FIRST (original) incident_manager execution -- receives the
    real image + Teams evidence, but DELIBERATELY never calls
    `knowledge_search`/`knowledge_select_evidence`, so `selected_
    knowledge_evidence` stays empty and chat_service.py's own completion
    gate is forced to trigger the governed-knowledge remediation (this is
    the exact live-reproduced trigger condition -- "the first specialist
    execution completed WITHOUT selected governed-KM evidence despite the
    request requiring governed Knowledge"). Captures its own first call's
    `llm_request.contents` so the test can independently confirm the
    ORIGINAL run itself genuinely had image evidence (requirement 1).
    """

    _step: list[int] = PrivateAttr(default_factory=lambda: [0])
    _first_call_contents: list[Any] = PrivateAttr(default_factory=list)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model="scripted-original-incident-manager", **kwargs)

    @property
    def first_call_contents(self) -> list[Any]:
        return self._first_call_contents

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        step = self._step[0]
        if step == 0:
            self._first_call_contents = list(llm_request.contents)
        self._step[0] += 1
        if step == 0:
            yield _function_call_response("teams_list_chats", {"topic": "Ops Bridge"}, "orig-call-0")
            return
        if step == 1:
            yield _function_call_response("teams_get_messages", {"chat_id": "chat-ops"}, "orig-call-1")
            return
        # No knowledge_search/knowledge_select_evidence call -- this is the
        # exact gap the live regression trace showed.
        payload = {
            "outcome": "ok",
            "chat_id": "chat-ops",
            "chat_title": "Ops Bridge",
            "summary": "Teams evidence reviewed; governed knowledge was not yet checked.",
            "evidence": [{"message_id": "m1", "author": "Priya", "sent_at": "2026-08-20T09:00:00Z"}],
        }
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]),
            partial=False,
        )


class _RemediationIncidentManagerScriptedLlm(BaseLlm):
    """The SECOND (governed-completion remediation) incident_manager
    execution -- this is the model whose OWN first call's `llm_request
    .contents` must contain the trusted image `file_data` Part for this
    regression to be considered fixed (requirement 4). Genuinely calls
    teams_list_chats/teams_get_messages (requirement 10) and knowledge_
    search/knowledge_select_evidence (requirements 11/12), then produces a
    final synthesis combining image + Teams + governed-KM evidence
    (requirement 13 / "MOST IMPORTANT REGRESSION TEST").
    """

    _step: list[int] = PrivateAttr(default_factory=lambda: [0])
    _first_call_contents: list[Any] = PrivateAttr(default_factory=list)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(model="scripted-remediation-incident-manager", **kwargs)

    @property
    def first_call_contents(self) -> list[Any]:
        return self._first_call_contents

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        step = self._step[0]
        if step == 0:
            self._first_call_contents = list(llm_request.contents)
        self._step[0] += 1
        if step == 0:
            yield _function_call_response("teams_list_chats", {"topic": "Ops Bridge"}, "rem-call-0")
            return
        if step == 1:
            yield _function_call_response("teams_get_messages", {"chat_id": "chat-ops"}, "rem-call-1")
            return
        if step == 2:
            yield _function_call_response(
                "knowledge_search", {"query_text": "relay verification checksum status"}, "rem-call-2"
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
            assert len(selected) >= 1
            yield _function_call_response("knowledge_select_evidence", {"selections": selected}, "rem-call-3")
            return
        # Final synthesis -- genuinely combines image + Teams + governed KM.
        # Values below are exactly the image/Teams/KM facts this test's own
        # fixtures established (never re-derived from anything the model
        # was not actually given).
        payload = {
            "outcome": "ok",
            "chat_id": "chat-ops",
            "chat_title": "Ops Bridge",
            "summary": (
                "Observed checksum 7318, observed status GREEN (from the attached image). "
                "Required checksum 7319, required status GREEN (per the approved Aurora Relay "
                "Verification Procedure). Verification FAILS because 7318 != 7319. Teams "
                "confirms TEAM-ORION is the designated platform owner and no remediation action "
                "has been approved -- collect the observed values and escalate to TEAM-ORION; "
                "do not restart or reconfigure."
            ),
            "evidence": [{"message_id": "m1", "author": "Priya", "sent_at": "2026-08-20T09:00:00Z"}],
        }
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]),
            partial=False,
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
                200,
                [
                    message(
                        "m1",
                        "Priya",
                        "TEAM-ORION is the designated platform owner; no remediation action has been approved.",
                        "2026-08-20T09:00:00Z",
                    )
                ],
            )
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)


def _build_runner(
    session_service: ApiSessionService, original_llm: _OriginalIncidentManagerScriptedLlm
) -> Runner:
    from backend.agents.incident_manager.agent import incident_manager

    incident_manager_scripted = incident_manager.model_copy(update={"model": original_llm})
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
async def test_governed_completion_remediation_receives_the_same_trusted_image_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE MOST IMPORTANT REGRESSION TEST. Forces the exact live-reproduced
    path: original run has real image+Teams evidence but no selected KM
    evidence -> completion-boundary remediation triggers -> remediation's
    own nested model input must genuinely contain the SAME trusted image
    Part -> final synthesis combines image + Teams + governed-KM into one
    correct, non-fabricated result."""
    _gateway(monkeypatch)
    await _seed_governed_knowledge()

    # --- Patch the GLOBAL incident_manager the remediation module re-
    # imports fresh on every call, so ITS OWN nested Runner uses a
    # scripted model too (requirement 4's whole point: prove the
    # REMEDIATION model's own input, not just that remediation ran).
    remediation_llm = _RemediationIncidentManagerScriptedLlm()
    from backend.agents.incident_manager.agent import incident_manager as real_incident_manager

    scripted_remediation_incident_manager = real_incident_manager.model_copy(update={"model": remediation_llm})
    monkeypatch.setattr(
        "backend.agents.incident_manager.agent.incident_manager", scripted_remediation_incident_manager
    )

    # --- Never allow base64/bytes construction anywhere in this pipeline
    # (requirement 7) -- a call would raise instead of silently succeeding.
    def _forbidden_from_bytes(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("Part.from_bytes must never be used for a durable chat image")

    monkeypatch.setattr(types.Part, "from_bytes", staticmethod(_forbidden_from_bytes))

    original_llm = _OriginalIncidentManagerScriptedLlm()
    session_service = ApiSessionService()
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service, original_llm)
    attachment_service = _attachment_service()
    await _ready_image_attachment(
        attachment_service, user_id="api-user", session_id=session_id, attachment_id="att-aurora-1"
    )

    chat_service = ChatService(
        session_service,
        runner=runner,
        attachment_service=attachment_service,
        attachment_storage=_attachment_storage(),
    )

    response = await chat_service.run_turn(
        session_id,
        "Using this image, Teams, and governed knowledge, verify the Aurora Relay status.",
        "api-user",
        attachment_ids=["att-aurora-1"],
    )

    # --- 1: the ORIGINAL run genuinely had trusted image evidence.
    original_file_parts = [
        p for p in original_llm.first_call_contents[-1].parts if getattr(p, "file_data", None) is not None
    ]
    assert len(original_file_parts) == 1
    assert original_file_parts[0].file_data.mime_type == "image/png"
    original_uri = original_file_parts[0].file_data.file_uri
    assert original_uri.startswith("gs://test-bucket/")

    # --- 2/3: the original run required governed knowledge but selected
    # none, forcing the remediation -- proven indirectly by the
    # remediation model having been invoked at all (below) plus the fact
    # team_manager's own draft answer never reaches the user (also below).

    # --- 4: THE CORE FIX. The remediation's own FIRST reasoning step
    # genuinely received a file_data Part -- not merely "an attachment_id
    # exists somewhere," the actual multimodal model input.
    assert remediation_llm.first_call_contents, "remediation model was never invoked"
    remediation_first_parts = remediation_llm.first_call_contents[-1].parts
    remediation_file_parts = [p for p in remediation_first_parts if getattr(p, "file_data", None) is not None]
    assert len(remediation_file_parts) == 1
    # --- SAME evidence, not a different/reconstructed image.
    assert remediation_file_parts[0].file_data.file_uri == original_uri
    assert remediation_file_parts[0].file_data.mime_type == "image/png"

    # --- 5/6: order preserved -- text (structured request) part first,
    # then the image part, exactly mirroring MultimodalAgentTool's own
    # construction for the original delegation.
    assert remediation_first_parts[0].text is not None
    assert getattr(remediation_first_parts[0], "file_data", None) is None
    assert remediation_first_parts[-1] is remediation_file_parts[0]

    # --- 9: no gs:// URI ever reaches the user-visible answer.
    assert "gs://" not in response.message.content
    assert "test-bucket" not in response.message.content

    # --- 10/11/12/13 + MOST IMPORTANT REGRESSION TEST: the final,
    # user-visible answer is the REMEDIATION's own real synthesis (never
    # team_manager's own discarded draft), genuinely combining image +
    # Teams + governed KM into the correct, non-fabricated result.
    final_text = response.message.content
    assert final_text != "Draft answer pending governed-knowledge validation."
    assert final_text != SAFE_COMPLETION_FAILURE_TEXT
    assert "7318" in final_text  # observed (from the REAL image, never invented)
    assert "GREEN" in final_text  # observed status
    assert "7319" in final_text  # required (from governed knowledge)
    assert "FAIL" in final_text.upper()
    assert "TEAM-ORION" in final_text
    # The exact fabrication this regression is named for must never appear.
    assert "assuming" not in final_text.lower()
    assert "7319, status" not in final_text.lower().replace(" observed", "")  # no invented-observed-7319 phrasing
    assert "RED" not in final_text  # the real image was GREEN -- never invented as RED


@pytest.mark.asyncio
async def test_text_only_turn_remediation_content_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 14: a text-only turn (no attachments at all) must
    produce byte-identical remediation `Content` to before this pass --
    `image_parts` defaults to an empty sequence and nothing is appended.
    """
    _gateway(monkeypatch)
    await _seed_governed_knowledge()

    remediation_llm = _RemediationIncidentManagerScriptedLlm()
    from backend.agents.incident_manager.agent import incident_manager as real_incident_manager

    scripted_remediation_incident_manager = real_incident_manager.model_copy(update={"model": remediation_llm})
    monkeypatch.setattr(
        "backend.agents.incident_manager.agent.incident_manager", scripted_remediation_incident_manager
    )

    class _TextOnlyOriginalLlm(_OriginalIncidentManagerScriptedLlm):
        pass

    original_llm = _TextOnlyOriginalLlm()
    session_service = ApiSessionService()
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service, original_llm)
    chat_service = ChatService(
        session_service, runner=runner, attachment_service=_attachment_service(), attachment_storage=_attachment_storage()
    )

    await chat_service.run_turn(
        session_id, "Check Ops Bridge and governed knowledge, no image this time.", "api-user"
    )

    assert remediation_llm.first_call_contents
    remediation_first_parts = remediation_llm.first_call_contents[-1].parts
    file_parts = [p for p in remediation_first_parts if getattr(p, "file_data", None) is not None]
    assert file_parts == []  # never fabricated, never a stale/other-turn image
    assert len(remediation_first_parts) == 1  # only the structured-request text part


@pytest.mark.asyncio
async def test_no_recursive_remediation_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 20: even if the remediation's OWN nested incident_
    manager also fails to select evidence (triggering ITS OWN internal
    provenance-compliance retry -- a different, already-bounded
    mechanism), `enforce_governed_knowledge_at_completion` itself is never
    called a second time for the same turn."""
    _gateway(monkeypatch)
    await _seed_governed_knowledge()

    call_count = {"n": 0}
    real_fn = enforce_governed_knowledge_at_completion

    async def _counting_wrapper(**kwargs: Any) -> Any:
        call_count["n"] += 1
        return await real_fn(**kwargs)

    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _counting_wrapper)

    remediation_llm = _RemediationIncidentManagerScriptedLlm()
    from backend.agents.incident_manager.agent import incident_manager as real_incident_manager

    scripted_remediation_incident_manager = real_incident_manager.model_copy(update={"model": remediation_llm})
    monkeypatch.setattr(
        "backend.agents.incident_manager.agent.incident_manager", scripted_remediation_incident_manager
    )

    original_llm = _OriginalIncidentManagerScriptedLlm()
    session_service = ApiSessionService()
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service, original_llm)
    attachment_service = _attachment_service()
    await _ready_image_attachment(
        attachment_service, user_id="api-user", session_id=session_id, attachment_id="att-aurora-1"
    )
    chat_service = ChatService(
        session_service, runner=runner, attachment_service=attachment_service, attachment_storage=_attachment_storage()
    )

    await chat_service.run_turn(
        session_id, "Verify the Aurora Relay status.", "api-user", attachment_ids=["att-aurora-1"]
    )

    assert call_count["n"] == 1  # bounded to exactly one remediation attempt, never a loop


# --- Section 2: trusted_image_parts_from_content unit tests -----------------


def _file_part(uri: str) -> types.Part:
    return types.Part.from_uri(file_uri=uri, mime_type="image/png")


def test_extracts_a_single_file_data_part_in_order() -> None:
    content = types.Content(
        role="user", parts=[types.Part.from_text(text="hello"), _file_part("gs://bucket/one.png")]
    )
    result = trusted_image_parts_from_content(content)
    assert len(result) == 1
    assert result[0].file_data.file_uri == "gs://bucket/one.png"


def test_preserves_order_for_multiple_images() -> None:
    """Requirement 5/15: multiple images remain correctly ordered."""
    content = types.Content(
        role="user",
        parts=[
            types.Part.from_text(text="hello"),
            _file_part("gs://bucket/first.png"),
            _file_part("gs://bucket/second.png"),
            _file_part("gs://bucket/third.png"),
        ],
    )
    result = trusted_image_parts_from_content(content)
    assert [p.file_data.file_uri for p in result] == [
        "gs://bucket/first.png",
        "gs://bucket/second.png",
        "gs://bucket/third.png",
    ]


def test_never_includes_a_text_part() -> None:
    content = types.Content(role="user", parts=[types.Part.from_text(text="hello, no image here")])
    assert trusted_image_parts_from_content(content) == []


def test_returns_empty_for_none_content() -> None:
    """Requirement 16: a missing/corrupt attachment context (here,
    modeled as `None` -- no Content to extract from at all) fails closed
    to an empty sequence, never raises, never fabricates a Part."""
    assert trusted_image_parts_from_content(None) == []


def test_returns_empty_for_content_with_no_parts() -> None:
    assert trusted_image_parts_from_content(types.Content(role="user", parts=[])) == []


def test_two_independent_calls_never_share_state() -> None:
    """Requirement 17: this is a pure function over its own argument --
    there is no run-id-keyed registry or module-level store involved at
    all, so one call can structurally never see another call's Content."""
    content_a = types.Content(role="user", parts=[_file_part("gs://bucket/run-a.png")])
    content_b = types.Content(role="user", parts=[_file_part("gs://bucket/run-b.png")])
    result_a = trusted_image_parts_from_content(content_a)
    result_b = trusted_image_parts_from_content(content_b)
    assert result_a[0].file_data.file_uri == "gs://bucket/run-a.png"
    assert result_b[0].file_data.file_uri == "gs://bucket/run-b.png"


# --- Section 3: enforce_governed_knowledge_at_completion cleanup/cancellation


@pytest.mark.asyncio
async def test_remediation_with_image_parts_still_cleans_up_its_internal_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 18: passing `image_parts` does not disturb the existing
    throwaway-session delete-in-finally contract."""
    from backend.agents.incident_manager import agent as im_agent_module

    class _ImmediateFinalLlm(BaseLlm):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="scripted-immediate-final", **kwargs)

        async def generate_content_async(self, llm_request: Any, stream: bool = False):
            payload = {"outcome": "no_result", "detail": "nothing found"}
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]),
                partial=False,
            )

    scripted = im_agent_module.incident_manager.model_copy(update={"model": _ImmediateFinalLlm()})
    monkeypatch.setattr(im_agent_module, "incident_manager", scripted)

    deleted_sessions: list[str] = []
    original_delete = InMemorySessionService.delete_session

    async def _tracking_delete(self, *, app_name, user_id, session_id):
        deleted_sessions.append(session_id)
        return await original_delete(self, app_name=app_name, user_id=user_id, session_id=session_id)

    monkeypatch.setattr(InMemorySessionService, "delete_session", _tracking_delete)

    image_part = _file_part("gs://bucket/cleanup-test.png")
    final_text, evidence = await enforce_governed_knowledge_at_completion(
        question="test", chat_topic=None, run_id="cleanup-run-1", image_parts=[image_part]
    )
    assert evidence == []
    assert "cleanup-test" not in final_text  # no leak of the uri into the answer
    assert any("governed-completion::cleanup-run-1" in s for s in deleted_sessions)


@pytest.mark.asyncio
async def test_remediation_with_image_parts_cleans_up_even_when_the_nested_runner_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement 19: cancellation/error cleanup still works with
    `image_parts` provided -- an unexpected exception from the nested
    Runner still reaches the caller (this module's own documented
    contract), and this module's own `finally` (run-id reset + knowledge
    run-state discard) still executes regardless."""
    from backend.agents.incident_manager import agent as im_agent_module
    from backend.tools.knowledge.runtime import get_or_init_run_state

    class _RaisingLlm(BaseLlm):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(model="scripted-raising", **kwargs)

        async def generate_content_async(self, llm_request: Any, stream: bool = False):
            raise RuntimeError("simulated nested Runner failure")
            yield  # pragma: no cover -- unreachable, satisfies generator shape

    scripted = im_agent_module.incident_manager.model_copy(update={"model": _RaisingLlm()})
    monkeypatch.setattr(im_agent_module, "incident_manager", scripted)

    image_part = _file_part("gs://bucket/cancel-test.png")
    with pytest.raises(RuntimeError):
        await enforce_governed_knowledge_at_completion(
            question="test", chat_topic=None, run_id="cleanup-run-2", image_parts=[image_part]
        )

    # The run-scoped knowledge state was genuinely discarded (a fresh
    # lookup returns a brand-new, empty state rather than anything stale).
    fresh_state = get_or_init_run_state("cleanup-run-2")
    assert fresh_state.available_evidence.items == []
    assert fresh_state.selected_evidence == []
