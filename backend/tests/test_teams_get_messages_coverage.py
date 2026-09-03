"""End-to-end tests: teams_get_messages's `coverage` field is wired
correctly from real (mocked) retrieval outcomes, and does not affect --
or get affected by -- unrelated fields like `messages`/`message_references`.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse
from backend.tools.teams.get_messages import teams_get_messages


def _msg(msg_id: str, sent_at: str, content: str = "<p>hi</p>", sender: str = "Alex") -> dict[str, Any]:
    return {
        "id": msg_id,
        "createdDateTime": sent_at,
        "senderName": sender,
        "contentType": "html",
        "content": content,
    }


def _timestamp(i: int) -> str:
    return f"2026-08-30T00:{i // 60:02d}:{i % 60:02d}.000Z"


def test_full_range_coverage_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_msg("m1", "2026-08-25T09:00:00Z")]),
    )

    result = teams_get_messages(
        chat_id="c1",
        from_datetime="2026-08-25T00:00:00Z",
        to_datetime="2026-08-26T00:00:00Z",
    )

    coverage = result["coverage"]
    assert coverage["status"] == "full_range"
    assert coverage["requested_from"] == "2026-08-25T00:00:00Z"
    assert coverage["requested_to"] == "2026-08-26T00:00:00Z"
    assert coverage["retrieved_count"] == 1
    assert "next_before" not in coverage


def test_partial_range_coverage_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two full pages, but max_messages=100 stops retrieval before the
    # requested from_datetime (deep in a third, never-fetched page).
    page1 = [_msg(f"m{i}", _timestamp(i)) for i in reversed(range(100, 150))]
    page2 = [_msg(f"m{i}", _timestamp(i)) for i in reversed(range(50, 100))]

    def fake_post(url, json, timeout):
        before = json.get("before")
        if before is None:
            return FakeResponse(200, page1)
        return FakeResponse(200, page2)

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    result = teams_get_messages(chat_id="c1", max_messages=100, from_datetime=_timestamp(10))

    coverage = result["coverage"]
    assert coverage["status"] == "partial_range"
    assert coverage["retrieved_count"] == 100
    assert "next_before" not in coverage


def test_latest_window_coverage_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    # 5 full pages of 50 (250 messages) with no time range -- the default
    # max_messages ceiling (200) truncates retrieval.
    pages_by_before: dict[Optional[str], list[dict[str, Any]]] = {}
    cursor: Optional[str] = None
    for page_index in reversed(range(5)):
        start = page_index * 50
        pages_by_before[cursor] = [_msg(f"m{i}", _timestamp(i)) for i in reversed(range(start, start + 50))]
        cursor = _timestamp(start)

    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda url, json, timeout: FakeResponse(200, pages_by_before[json.get("before")]),
    )

    result = teams_get_messages(chat_id="c1")

    coverage = result["coverage"]
    assert coverage["status"] == "latest_window"
    assert coverage["requested_from"] is None
    assert coverage["requested_to"] is None
    assert coverage["retrieved_count"] == 200
    assert "next_before" not in coverage


def test_complete_coverage_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_msg("m1", "2026-08-30T09:00:00Z")]),
    )

    result = teams_get_messages(chat_id="c1")

    coverage = result["coverage"]
    assert coverage["status"] == "complete"
    assert coverage["retrieved_count"] == 1


def test_coverage_oldest_newest_match_the_messages_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [_msg("m1", "2026-08-25T09:00:00Z"), _msg("m2", "2026-08-26T09:00:00Z")],
        ),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["coverage"]["oldest_retrieved_at"] == result["oldest_retrieved_at"]
    assert result["coverage"]["newest_retrieved_at"] == result["newest_retrieved_at"]
    assert result["coverage"]["oldest_retrieved_at"] == "2026-08-25T09:00:00Z"
    assert result["coverage"]["newest_retrieved_at"] == "2026-08-26T09:00:00Z"


def test_coverage_does_not_affect_message_references_or_evidence_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coverage classification is computed independently of message-level
    content -- adding it must not alter `messages`/`message_references` in
    any way.
    """
    import json as json_module

    reference_content = json_module.dumps(
        {
            "messageId": "ref-1",
            "messagePreview": "Original text",
            "messageSender": {"user": {"id": "u1", "displayName": "Referenced User"}},
        }
    )
    entry = {
        "id": "m2",
        "createdDateTime": "2026-08-31T13:31:00Z",
        "senderName": "Current User",
        "contentType": "html",
        "content": '<attachment id="ref-1"></attachment><p>Is this it?</p>',
        "attachments": [{"id": "ref-1", "contentType": "messageReference", "content": reference_content}],
    }
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [entry]))

    result = teams_get_messages(chat_id="c1")

    assert result["messages"][0]["text"] == "Is this it?"
    assert result["messages"][0]["message_references"] == [
        {
            "message_id": "ref-1",
            "preview": "Original text",
            "sender_name": "Referenced User",
            "sender_id": "u1",
        }
    ]
    # Coverage is present and correct alongside the unaffected message data.
    assert result["coverage"]["status"] == "complete"
    assert result["coverage"]["retrieved_count"] == 1
