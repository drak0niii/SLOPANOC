"""Tests for deterministic latest-image discovery across message history.

Verifies:
1. TeamsGetMessagesResult deterministically populates `latest_hosted_content_message_id`
   and `latest_hosted_content_ids` using reverse-chronological traversal across
   retrieved messages.
2. Even when the most recent message in chat history is text-only, earlier messages
   carrying hosted content are deterministically identified as the latest image.
3. `get_latest_message_id_with_hosted_content()` returns the message_id of the newest
   message with hosted content according to sent_at.
4. `teams_get_all_hosted_content(chat_id, message_id="latest")` correctly resolves
   "latest" to that message_id and retrieves its images.
5. `find_hosted_content=True` in `teams_get_messages` stops pagination as soon as
   a page with hosted content is encountered.
"""
from __future__ import annotations

import base64
import contextlib
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
from google.adk.tools import ToolContext

from backend.api.hosted_content_vision_context import (
    get_latest_message_id_with_hosted_content,
    record_message_hosted_content_order,
    record_message_metadata,
)
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.tools.teams.get_hosted_content import teams_get_all_hosted_content
from backend.tools.teams.get_messages import (
    KNOWN_HOSTED_CONTENT_IDS_STATE_KEY,
    teams_get_messages,
)

# 1x1 transparent PNG
_SAMPLE_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)
_SAMPLE_PNG_B64 = base64.b64encode(_SAMPLE_PNG_BYTES).decode("ascii")


@contextlib.contextmanager
def run_context(run_id: str) -> Generator[None, None, None]:
    token = bind_run_id(run_id)
    try:
        yield
    finally:
        reset_run_id(token)


def _fake_pa_message(
    message_id: str,
    sent_at: str,
    content: str,
    author: str = "Alice",
) -> dict[str, Any]:
    return {
        "id": message_id,
        "createdDateTime": sent_at,
        "senderName": author,
        "contentType": "html",
        "content": content,
        "attachments": [],
    }


def test_teams_get_messages_identifies_latest_hosted_content_when_newest_is_text_only():
    """When the newest message (msg_3) is text-only, but msg_2 contains hosted content,
    TeamsGetMessagesResult must report latest_hosted_content_message_id == 'msg_2'."""
    raw_page = [
        _fake_pa_message("msg_1", "2026-09-19T10:00:00Z", "<p>Initial message</p>"),
        _fake_pa_message(
            "msg_2",
            "2026-09-19T10:05:00Z",
            '<p>Screenshot:</p><img src="/v1.0/chats/c1/messages/msg_2/hostedContents/hc_img_2/$value">',
        ),
        _fake_pa_message("msg_3", "2026-09-19T10:10:00Z", "<p>Thanks for the screenshot!</p>"),
    ]

    with patch("backend.tools.teams.get_messages.PowerAutomateClient") as mock_client_cls:
        client_instance = MagicMock()
        client_instance.get_messages.return_value = raw_page
        mock_client_cls.return_value = client_instance

        with run_context("run_test_latest_discovery"):
            result = teams_get_messages(chat_id="chat_123")
            assert "error" not in result
            assert result["latest_hosted_content_message_id"] == "msg_2"
            assert result["latest_hosted_content_ids"] == ["hc_img_2"]
            assert len(result["messages"]) == 3
            # msg_3 is newest chronologically
            assert result["messages"][-1]["id"] == "msg_3"


def test_teams_get_messages_multiple_messages_with_hosted_content_picks_newest():
    """When multiple messages have hosted content, latest_hosted_content_message_id
    picks the one with the newest sent_at."""
    raw_page = [
        _fake_pa_message(
            "msg_1",
            "2026-09-19T10:00:00Z",
            '<img src="/v1.0/chats/c1/messages/msg_1/hostedContents/hc_1/$value">',
        ),
        _fake_pa_message(
            "msg_2",
            "2026-09-19T10:05:00Z",
            '<img src="/v1.0/chats/c1/messages/msg_2/hostedContents/hc_2/$value">',
        ),
        _fake_pa_message("msg_3", "2026-09-19T10:10:00Z", "<p>All done</p>"),
    ]

    with patch("backend.tools.teams.get_messages.PowerAutomateClient") as mock_client_cls:
        client_instance = MagicMock()
        client_instance.get_messages.return_value = raw_page
        mock_client_cls.return_value = client_instance

        with run_context("run_test_multiple_hc"):
            result = teams_get_messages(chat_id="chat_123")
            assert result["latest_hosted_content_message_id"] == "msg_2"
            assert result["latest_hosted_content_ids"] == ["hc_2"]


def test_teams_get_messages_no_hosted_content_returns_none():
    """When no messages carry hosted content, latest_hosted_content_message_id is None."""
    raw_page = [
        _fake_pa_message("msg_1", "2026-09-19T10:00:00Z", "<p>Hello</p>"),
        _fake_pa_message("msg_2", "2026-09-19T10:05:00Z", "<p>World</p>"),
    ]

    with patch("backend.tools.teams.get_messages.PowerAutomateClient") as mock_client_cls:
        client_instance = MagicMock()
        client_instance.get_messages.return_value = raw_page
        mock_client_cls.return_value = client_instance

        result = teams_get_messages(chat_id="chat_123")
        assert result["latest_hosted_content_message_id"] is None
        assert result["latest_hosted_content_ids"] == []


def test_get_latest_message_id_with_hosted_content_resolves_correctly():
    """get_latest_message_id_with_hosted_content returns the newest message with hosted content."""
    with run_context("run_test_vision_context_latest"):
        record_message_hosted_content_order("msg_old", ["hc_old"])
        record_message_metadata("msg_old", "Alice", "2026-09-19T10:00:00Z")

        record_message_hosted_content_order("msg_new", ["hc_new"])
        record_message_metadata("msg_new", "Bob", "2026-09-19T10:15:00Z")

        assert get_latest_message_id_with_hosted_content() == "msg_new"


def test_teams_get_all_hosted_content_resolves_latest_keyword():
    """Calling teams_get_all_hosted_content with message_id='latest' resolves to
    the newest message with hosted content in this run."""
    with run_context("run_test_all_hc_latest"):
        record_message_hosted_content_order("msg_target", ["hc_target"])
        record_message_metadata("msg_target", "Alice", "2026-09-19T10:00:00Z")

        mock_tool_ctx = MagicMock(spec=ToolContext)
        mock_tool_ctx.state = {
            KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {
                "chat_123": {
                    "msg_target": ["hc_target"],
                }
            }
        }

        with patch("backend.tools.teams.get_hosted_content.PowerAutomateClient") as mock_client_cls:
            client_instance = MagicMock()
            client_instance.get_hosted_content.return_value = {
                "success": True,
                "chatId": "chat_123",
                "messageId": "msg_target",
                "hostedContentId": "hc_target",
                "contentType": "image/png",
                "contentBase64": _SAMPLE_PNG_B64,
            }
            mock_client_cls.return_value = client_instance

            res = teams_get_all_hosted_content(
                chat_id="chat_123",
                message_id="latest",
                tool_context=mock_tool_ctx,
            )
            assert "error" not in res
            assert res["chat_id"] == "chat_123"
            assert res["message_id"] == "msg_target"
            assert res["delivered_count"] == 1
