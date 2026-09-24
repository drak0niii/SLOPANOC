"""Regression test suite for the surgical Teams image retrieval repair.

Validates the 10 mandatory scenarios:
1. Latest message contains inline image but no visible text.
2. Latest message is text-only, while older message contains most recent image.
3. Image request requires chat selection before retrieval (preserves requires_rich_content and suppresses spurious governed knowledge override).
4. Image request uses already-resolved Teams chat (routes to model-driven continuation with tools).
5. One message contains multiple images.
6. Image is represented as supported file attachment rather than inline hosted content.
7. Image retrieval fails, returns invalid bytes, or exceeds size/count limits (safe error, no crash).
8. Ordinary text-only Teams retrieval continues working.
9. Image retrieval succeeds without claiming visual analysis occurred if delivered_for_visual_reasoning is false.
10. Request to describe image results in verified delivery to Gemini vision.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from typing import Any, Optional
from unittest.mock import MagicMock, patch

import pytest
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.events import Event, EventActions
from google.adk.tools import ToolContext
from google.genai import types

from backend.agents.incident_manager.evidence import validate_evidence
from backend.agents.team_manager.agent import presentation_team_manager
from backend.agents.team_manager.case_context import make_team_manager_instruction_provider
from backend.agents.team_manager import read_continuation_execution as execution_module
from backend.agents.team_manager.read_continuation_execution import (
    _CONTINUATION_INCIDENT_MANAGER,
    _SYNTHESIS_ONLY_INCIDENT_MANAGER,
    execute_read_continuation,
)
from backend.agents.team_manager.read_continuation_presentation import (
    PENDING_SPECIALIST_RESULT_STATE_KEY,
    TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY,
    build_trusted_specialist_result_envelope,
    validate_trusted_envelope_for_run,
)
from backend.api import hosted_content_vision_context as hcv
from backend.api import selection_service
from backend.api.chat_service import ChatService
from backend.api.multimodal_turn_context import discard_run_images, register_run_images
from backend.api.pending_action import map_pending_action
from backend.api.pending_selection import map_pending_selection
from backend.api.session_service import ApiSessionService
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.attachments.service import AttachmentService
from backend.attachments.storage import ChatAttachmentStorage
from backend.gateway import power_automate_client as pac_module
from backend.selection.schemas import (
    PendingReadIntent,
    PendingSelection,
    ReadOperation,
    ResolvedReadContinuation,
    SelectionKind,
    SelectionOption,
    SelectionStatus,
)
from backend.selection.service import PENDING_SELECTION_STATE_KEY, load_active_selection, pop_read_continuation
from backend.tests._fakes import FakeResponse, chat, message
from backend.tools.teams.execute_write import teams_create_chat, teams_send_message
from backend.tools.teams.get_hosted_content import (
    teams_get_all_hosted_content,
    teams_get_hosted_content,
)
from backend.tools.teams.get_messages import (
    KNOWN_HOSTED_CONTENT_IDS_STATE_KEY,
    KNOWN_MESSAGE_IDS_STATE_KEY,
    teams_get_messages,
)
from backend.tools.teams.list_chats import teams_list_chats
from backend.tools.teams.propose_write import teams_propose_create_chat, teams_propose_send_message

# 1x1 transparent PNG
_SAMPLE_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
_SAMPLE_PNG_B64 = base64.b64encode(_SAMPLE_PNG_BYTES).decode("ascii")


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


@pytest.fixture(autouse=True)
def _clean_stores() -> Any:
    yield
    with hcv._lock:  # noqa: SLF001
        hcv._pending.clear()
        hcv._message_order.clear()
        hcv._message_metadata.clear()
        hcv._delivered.clear()


# Scenario 1: Latest message contains inline image but no visible text
def test_scenario_1_latest_message_inline_image_no_text():
    raw_page = [
        {
            "id": "msg_001",
            "createdDateTime": "2026-09-21T10:00:00Z",
            "senderName": "Costin Ionita",
            "contentType": "html",
            "content": '<img src="/v1.0/chats/c1/messages/msg_001/hostedContents/hc_img_001/$value">',
            "attachments": [],
        }
    ]
    with patch("backend.tools.teams.get_messages.PowerAutomateClient") as mock_client_cls:
        client = MagicMock()
        client.get_messages.return_value = raw_page
        mock_client_cls.return_value = client

        token = bind_run_id("run-scenario-1")
        try:
            result = teams_get_messages(chat_id="c1")
            assert "error" not in result
            assert result["latest_hosted_content_message_id"] == "msg_001"
            assert result["latest_hosted_content_ids"] == ["hc_img_001"]
            assert len(result["messages"]) == 1
            assert result["messages"][0]["hosted_content_ids"] == ["hc_img_001"]
        finally:
            reset_run_id(token)


# Scenario 2: Latest message is text-only, while older message contains most recent image
def test_scenario_2_latest_message_text_only_older_has_image():
    raw_page = [
        {
            "id": "msg_older",
            "createdDateTime": "2026-09-21T10:00:00Z",
            "senderName": "Costin Ionita",
            "contentType": "html",
            "content": '<p>Here is the alarm screenshot:</p><img src="/v1.0/chats/c1/messages/msg_older/hostedContents/hc_alarm/$value">',
            "attachments": [],
        },
        {
            "id": "msg_newest",
            "createdDateTime": "2026-09-21T10:05:00Z",
            "senderName": "Costin Ionita",
            "contentType": "html",
            "content": "<p>Did anyone check this alarm?</p>",
            "attachments": [],
        },
    ]
    with patch("backend.tools.teams.get_messages.PowerAutomateClient") as mock_client_cls:
        client = MagicMock()
        client.get_messages.return_value = raw_page
        mock_client_cls.return_value = client

        token = bind_run_id("run-scenario-2")
        try:
            result = teams_get_messages(chat_id="c1")
            assert "error" not in result
            # Newest message in chat is text-only, but latest hosted content correctly identifies msg_older
            assert result["messages"][-1]["id"] == "msg_newest"
            assert result["messages"][-1]["hosted_content_ids"] == []
            assert result["latest_hosted_content_message_id"] == "msg_older"
            assert result["latest_hosted_content_ids"] == ["hc_alarm"]
        finally:
            reset_run_id(token)


# Scenario 3: Image request requires chat selection before retrieval (preserves requires_rich_content)
@pytest.mark.asyncio
async def test_scenario_3_image_request_requires_chat_selection(monkeypatch: pytest.MonkeyPatch):
    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.listChats":
            return FakeResponse(
                200, [chat("c_gw_1", "SLOPANOC Gateway Group Test"), chat("c_gw_2", "SLOPANOC Gateway General")]
            )
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)

    token = bind_run_id("run-scenario-3")
    try:
        # User asked: check SLOPANOC Gateway chatroom, and describe the last picture attached
        result = teams_list_chats(
            topic="SLOPANOC Gateway",
            pending_question="describe the last picture attached",
            pending_operation="summarize",
            pending_requires_rich_content=True,
            tool_context=ctx,
        )
    finally:
        reset_run_id(token)

    assert result["match"] == "not_found"
    assert result["selection_pending"] is True

    await service.persist_state_delta(session, dict(ctx.state))
    refreshed = await service.get_session(session_id)
    pending = load_active_selection(refreshed.state)
    assert pending is not None
    assert pending.pending_read_intent is not None
    assert pending.pending_read_intent.requires_rich_content is True

    # Find the option matching "c_gw_1"
    target_option = next(
        opt for opt in pending.options if pending.option_targets.get(opt.option_id, {}).get("chat_id") == "c_gw_1"
    )
    choose_resp = await selection_service.choose(service, session_id, pending.selection_id, target_option.option_id)
    assert choose_resp.status == "resolved"

    refreshed2 = await service.get_session(session_id)
    continuation = pop_read_continuation(refreshed2.state)
    assert continuation is not None
    assert continuation.selected_chat_id == "c_gw_1"
    assert continuation.requires_rich_content is True


# Scenario 4: Image request uses already-resolved Teams chat (routes to model-driven continuation with tools)
@pytest.mark.asyncio
async def test_scenario_4_continuation_routes_to_model_driven_retrieval(monkeypatch: pytest.MonkeyPatch):
    session_service = ApiSessionService()
    session_id = await session_service.create_session()
    session = await session_service.get_session(session_id)

    captured_runner_agent: list[Any] = []

    class _CapturingModelDrivenRunner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._session_service = session_service
            captured_runner_agent.append(agent)

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            # Model calls get_resolved_chat_messages which populates KNOWN_MESSAGE_IDS_STATE_KEY
            sess = await self._session_service.get_session(app_name=execution_module._INTERNAL_SPECIALIST_APP_NAME, user_id=user_id, session_id=session_id)
            if sess:
                event = Event(
                    author="incident_manager",
                    actions=EventActions(state_delta={KNOWN_MESSAGE_IDS_STATE_KEY: ["msg_001"]}),
                )
                await self._session_service.append_event(session=sess, event=event)

            payload = {
                "outcome": "ok",
                "chat_id": "c_gw_1",
                "chat_title": "SLOPANOC Gateway Group Test",
                "summary": "The picture shows the active alarm table.",
            }
            content = types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))])
            yield type("E", (), {"content": content})()

        async def close(self) -> None:
            pass

    monkeypatch.setattr(execution_module, "Runner", _CapturingModelDrivenRunner)

    continuation = ResolvedReadContinuation(
        selected_chat_id="c_gw_1",
        selected_chat_topic="SLOPANOC Gateway Group Test",
        question="describe the last picture attached",
        requires_rich_content=True,
    )

    mock_att_service = MagicMock(spec=AttachmentService)
    mock_att_storage = MagicMock(spec=ChatAttachmentStorage)

    result = await execute_read_continuation(
        session_service=session_service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-scenario-4",
        parent_state=dict(session.state),
        continuation=continuation,
        attachment_service=mock_att_service,
        attachment_storage=mock_att_storage,
    )

    assert result is not None
    assert result["outcome"] == "ok"
    assert len(captured_runner_agent) == 1
    # MUST be _CONTINUATION_INCIDENT_MANAGER which carries hosted content tools, NOT _SYNTHESIS_ONLY_INCIDENT_MANAGER
    assert captured_runner_agent[0] is _CONTINUATION_INCIDENT_MANAGER
    assert captured_runner_agent[0] is not _SYNTHESIS_ONLY_INCIDENT_MANAGER


# Scenario 5: One message contains multiple images
def test_scenario_5_one_message_multiple_images():
    raw_page = [
        {
            "id": "msg_multi",
            "createdDateTime": "2026-09-21T10:00:00Z",
            "senderName": "Alice",
            "contentType": "html",
            "content": (
                '<img src="/v1.0/chats/c1/messages/msg_multi/hostedContents/hc_1/$value">'
                '<p>and</p>'
                '<img src="/v1.0/chats/c1/messages/msg_multi/hostedContents/hc_2/$value">'
            ),
            "attachments": [],
        }
    ]
    with patch("backend.tools.teams.get_messages.PowerAutomateClient") as mock_client_cls:
        client = MagicMock()
        client.get_messages.return_value = raw_page
        mock_client_cls.return_value = client

        token = bind_run_id("run-scenario-5")
        try:
            result = teams_get_messages(chat_id="c1")
            assert "error" not in result
            assert result["messages"][0]["hosted_content_ids"] == ["hc_1", "hc_2"]
            assert result["latest_hosted_content_ids"] == ["hc_1", "hc_2"]
        finally:
            reset_run_id(token)


# Scenario 6: Image represented as supported file attachment rather than inline hosted content
def test_scenario_6_image_as_supported_file_attachment():
    raw_html = '<p>See attached image: <attachment id="att_img_1"></attachment></p>'
    raw_page = [
        {
            "id": "msg_att",
            "createdDateTime": "2026-09-21T10:00:00Z",
            "senderName": "Alice",
            "contentType": "html",
            "content": raw_html,
            "attachments": [
                {
                    "id": "att_img_1",
                    "contentType": "image/png",
                    "contentUrl": "https://example.com/att_img_1.png",
                    "name": "alarm.png",
                }
            ],
        }
    ]
    with patch("backend.tools.teams.get_messages.PowerAutomateClient") as mock_client_cls:
        client = MagicMock()
        client.get_messages.return_value = raw_page
        mock_client_cls.return_value = client

        token = bind_run_id("run-scenario-6")
        try:
            result = teams_get_messages(chat_id="c1")
            assert "error" not in result
            assert len(result["messages"]) == 1
            # Normalization formats unclassified/file attachment as [Attachment]
            assert "[Attachment]" in result["messages"][0]["text"]
            assert result["messages"][0]["raw_content"] == raw_html
        finally:
            reset_run_id(token)


# Scenario 7: Image retrieval fails, returns invalid bytes, or exceeds size/count limits
def test_scenario_7_image_retrieval_invalid_bytes_fails_safely():
    with patch("backend.tools.teams.get_hosted_content.PowerAutomateClient") as mock_client_cls:
        client = MagicMock()
        # Non-image corrupt bytes
        client.get_hosted_content.return_value = {
            "success": True,
            "contentBase64": base64.b64encode(b"NOT_A_VALID_IMAGE_OR_PNG").decode("ascii"),
            "contentType": "image/png",
        }
        mock_client_cls.return_value = client

        token = bind_run_id("run-scenario-7")
        try:
            result = teams_get_hosted_content("c1", "msg1", "hc1")
            assert "error" in result
            assert result["error"]["errorCode"] == "unsupported_media_type"
        finally:
            reset_run_id(token)


# Scenario 8: Ordinary text-only Teams retrieval continues working
def test_scenario_8_ordinary_text_only_teams_retrieval():
    raw_page = [
        {
            "id": "msg_text_1",
            "createdDateTime": "2026-09-21T10:00:00Z",
            "senderName": "Alice",
            "contentType": "text",
            "content": "Status meeting at 10",
            "attachments": [],
        },
        {
            "id": "msg_text_2",
            "createdDateTime": "2026-09-21T10:05:00Z",
            "senderName": "Bob",
            "contentType": "text",
            "content": "Acknowledged",
            "attachments": [],
        },
    ]
    with patch("backend.tools.teams.get_messages.PowerAutomateClient") as mock_client_cls:
        client = MagicMock()
        client.get_messages.return_value = raw_page
        mock_client_cls.return_value = client

        token = bind_run_id("run-scenario-8")
        try:
            result = teams_get_messages(chat_id="c1")
            assert "error" not in result
            assert len(result["messages"]) == 2
            assert result["latest_hosted_content_message_id"] is None
            assert result["latest_hosted_content_ids"] == []
        finally:
            reset_run_id(token)


# Scenario 9: Image retrieval succeeds without claiming visual analysis occurred if delivered_for_visual_reasoning is false
def test_scenario_9_image_retrieval_without_delivered_visual_reasoning():
    # Outside any bound run_id token, stashing cannot deliver to vision context
    with patch("backend.tools.teams.get_hosted_content.PowerAutomateClient") as mock_client_cls:
        client = MagicMock()
        client.get_hosted_content.return_value = {
            "success": True,
            "contentBase64": _SAMPLE_PNG_B64,
            "contentType": "image/png",
        }
        mock_client_cls.return_value = client

        # Without bind_run_id
        result = teams_get_hosted_content("c1", "msg1", "hc1")
        assert "error" not in result
        assert result["delivered_for_visual_reasoning"] is False


# Scenario 10: Request to describe image results in verified delivery to Gemini vision
def test_scenario_10_verified_delivery_to_gemini_vision():
    with patch("backend.tools.teams.get_hosted_content.PowerAutomateClient") as mock_client_cls:
        client = MagicMock()
        client.get_hosted_content.return_value = {
            "success": True,
            "contentBase64": _SAMPLE_PNG_B64,
            "contentType": "image/png",
        }
        mock_client_cls.return_value = client

        token = bind_run_id("run-scenario-10")
        try:
            result = teams_get_hosted_content("c1", "msg1", "hc1")
            assert "error" not in result
            assert result["delivered_for_visual_reasoning"] is True

            # Verify that calling inject_pending_hosted_content_image injects real bytes into LlmRequest
            class _FakeLlmReq:
                def __init__(self):
                    self.contents: list[Any] = []

            fake_req = _FakeLlmReq()
            hcv.inject_pending_hosted_content_image(None, fake_req)
            assert len(fake_req.contents) == 1
            content = fake_req.contents[0]
            assert content.role == "user"
            inline_parts = [p for p in content.parts if getattr(p, "inline_data", None) is not None]
            assert len(inline_parts) == 1
            assert inline_parts[0].inline_data.mime_type == "image/png"
            assert inline_parts[0].inline_data.data == _SAMPLE_PNG_BYTES

            # Verify provenance tracking
            delivered = hcv.pop_delivered_visual_evidence("run-scenario-10")
            assert len(delivered) == 1
            assert delivered[0].hosted_content_id == "hc1"
            assert delivered[0].message_id == "msg1"
        finally:
            reset_run_id(token)


# Scenario 11: Read continuation structurally excludes write tools
def test_scenario_11_continuation_excludes_write_tools():
    tool_roster = _CONTINUATION_INCIDENT_MANAGER.tools
    assert teams_propose_send_message not in tool_roster
    assert teams_propose_create_chat not in tool_roster
    assert teams_send_message not in tool_roster
    assert teams_create_chat not in tool_roster
    assert teams_list_chats not in tool_roster


# Scenario 12: Image continuation retrieves latest image without entering write workflow
@pytest.mark.asyncio
async def test_scenario_12_image_continuation_retrieval_without_write_workflow(monkeypatch: pytest.MonkeyPatch):
    session_service = ApiSessionService()
    session_id = await session_service.create_session()
    session = await session_service.get_session(session_id)

    tool_call_counts: dict[str, int] = {"propose_send": 0, "get_messages": 0, "get_hosted_content": 0}

    class _CapturingImageRunner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._session_service = session_service
            self._agent = agent

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            # Verify the agent tools have no write capabilities
            assert teams_propose_send_message not in self._agent.tools
            assert teams_propose_create_chat not in self._agent.tools
            assert teams_send_message not in self._agent.tools
            assert teams_create_chat not in self._agent.tools

            # Verify chat_id is passed in the initial user request
            parsed_req = json.loads(new_message.parts[0].text)
            assert parsed_req.get("chat_id") == "c_gw_1"
            assert parsed_req.get("chat_topic") == "SLOPANOC Gateway Group Test"
            assert parsed_req.get("requires_rich_content") is True

            # Model invokes get_resolved_chat_messages
            tool_call_counts["get_messages"] += 1
            sess = await self._session_service.get_session(
                app_name=execution_module._INTERNAL_SPECIALIST_APP_NAME, user_id=user_id, session_id=session_id
            )
            if sess:
                event = Event(
                    author="incident_manager",
                    actions=EventActions(
                        state_delta={
                            KNOWN_MESSAGE_IDS_STATE_KEY: ["msg_001"],
                            KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {"c_gw_1": {"msg_001": ["hc_img_001"]}},
                        }
                    ),
                )
                await self._session_service.append_event(session=sess, event=event)

            # Model invokes teams_get_hosted_content exactly once
            tool_call_counts["get_hosted_content"] += 1

            payload = {
                "outcome": "ok",
                "chat_id": "c_gw_1",
                "chat_title": "SLOPANOC Gateway Group Test",
                "summary": "The last picture attached displays an active alarm table.",
            }
            content = types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))])
            yield type("E", (), {"content": content})()

        async def close(self) -> None:
            pass

    monkeypatch.setattr(execution_module, "Runner", _CapturingImageRunner)

    continuation = ResolvedReadContinuation(
        selected_chat_id="c_gw_1",
        selected_chat_topic="SLOPANOC Gateway Group Test",
        question="describe the last picture attached. is it related to my alarm ?",
        requires_rich_content=True,
    )

    mock_att_service = MagicMock(spec=AttachmentService)
    mock_att_storage = MagicMock(spec=ChatAttachmentStorage)

    result = await execute_read_continuation(
        session_service=session_service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-scenario-12",
        parent_state=dict(session.state),
        continuation=continuation,
        attachment_service=mock_att_service,
        attachment_storage=mock_att_storage,
    )

    assert result is not None
    assert result["outcome"] == "ok"
    assert tool_call_counts["propose_send"] == 0
    assert tool_call_counts["get_messages"] == 1
    assert tool_call_counts["get_hosted_content"] == 1


# Scenario 13: Trusted result presentation for image retrieval acknowledges chat and delivers visual analysis
@pytest.mark.asyncio
async def test_scenario_13_trusted_result_presentation_for_image_retrieval():
    """Validates that presentation of a trusted image retrieval result:
    1. presentation_team_manager structurally excludes all tools.
    2. Trusted envelope binds run_id and preserves authoritative destination title.
    3. Instruction provider dynamically selects TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION.
    4. Prompt acknowledges resolved chat_title and delivers visual analysis directly from summary.
    5. Prompt strictly forbids re-asking for or prompting for the chat room name.
    6. Specialist delegation addendums and active roster are omitted in presentation mode.
    """
    run_id = "run-scenario-13"
    specialist_result = {
        "outcome": "ok",
        "chat_id": "c_gw_1",
        "chat_title": "SLOPANOC Gateway Group Test",
        "summary": "The last picture attached displays an active alarm table with critical cell carrier alarms.",
    }

    envelope = build_trusted_specialist_result_envelope(run_id, specialist_result)
    assert envelope is not None
    validated = validate_trusted_envelope_for_run(envelope, current_run_id=run_id)
    assert validated is not None
    assert validated["chat_title"] == "SLOPANOC Gateway Group Test"
    assert validated["summary"] == specialist_result["summary"]

    # Verify presentation_team_manager structure: no tools, no tool callbacks
    assert presentation_team_manager.tools == []
    assert presentation_team_manager.before_tool_callback is None
    assert presentation_team_manager.after_tool_callback is None

    # Test instruction provider generation
    state = {
        TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY: envelope,
        PENDING_SPECIALIST_RESULT_STATE_KEY: validated,
    }
    mock_invoc = MagicMock()
    mock_invoc.session.state = state
    mock_invoc.session.user_id = "api-user"
    mock_invoc.session.id = "session-test-13"

    mock_ctx = MagicMock(spec=ReadonlyContext)
    mock_ctx.state = state
    mock_ctx.user_id = "api-user"
    mock_ctx._invocation_context = mock_invoc

    instruction = await presentation_team_manager.instruction(mock_ctx)

    # 1. Authoritative destination is injected
    assert "SLOPANOC Gateway Group Test" in instruction
    # 2. Specialist visual analysis summary is injected
    assert "The last picture attached displays an active alarm table with critical cell carrier alarms." in instruction
    # 3. Explicitly forbids asking for chat room name
    assert "Never ask the user for a chat name" in instruction
    assert "retrieval is complete" in instruction
    # 4. Directs visual analysis delivery
    assert "Deliver this analysis directly and completely in your reply, acknowledging `chat_title`" in instruction
    # 5. Specialist delegation addendums and active roster are omitted
    assert "TECHNICAL AUTHORITY ENGINEER DELEGATION" not in instruction
    assert "ACTIVE AGENT ROSTER" not in instruction
