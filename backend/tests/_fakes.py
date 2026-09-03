"""Shared, non-collected test doubles for the backend test suite.

Named with a leading underscore (not `test_*`) so pytest never tries to
collect it as a test module itself.
"""
from __future__ import annotations

from typing import Any, Optional, Union

JsonBody = Union[list, dict]


class FakeResponse:
    """Minimal stand-in for `requests.Response`."""

    def __init__(self, status_code: int, json_body: Optional[JsonBody] = None) -> None:
        self.status_code = status_code
        self._json_body: JsonBody = json_body if json_body is not None else {}

    def json(self) -> JsonBody:
        return self._json_body


def chat(
    chat_id: str,
    topic: str,
    last_updated: str = "2026-08-30T12:00:00Z",
) -> dict[str, Any]:
    """Build one raw `teams.listChats` array item, matching the live
    gateway's proven field names (`id`, `topic`, `createdDateTime`,
    `lastUpdatedDateTime`) -- not the earlier, unverified `chatId`/`title`/
    `participantCount`/`lastActivityAt` shape.
    """
    return {
        "id": chat_id,
        "topic": topic,
        "createdDateTime": "2026-08-01T00:00:00Z",
        "lastUpdatedDateTime": last_updated,
    }


def message(msg_id: str, sender_name: str, content: str, created_at: str) -> dict[str, Any]:
    """Build one raw `teams.getMessages` array item, matching the live
    gateway's proven field names (`id`, `createdDateTime`,
    `lastModifiedDateTime`, `senderName`, `senderId`, `contentType`,
    `content`, `webUrl`) -- not the earlier, unverified `author`/`text`/
    `sentAt` shape.
    """
    return {
        "id": msg_id,
        "createdDateTime": created_at,
        "lastModifiedDateTime": created_at,
        "senderName": sender_name,
        "senderId": f"user-{sender_name.lower()}",
        "contentType": "text",
        "content": content,
        "webUrl": f"https://teams.microsoft.com/l/message/{msg_id}",
    }
