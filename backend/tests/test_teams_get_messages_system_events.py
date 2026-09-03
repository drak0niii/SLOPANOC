"""End-to-end tests for teams_get_messages's system/event filtering --
system events and content-free entries must never reach
incident_manager's reasoning context, but a real message must never be
dropped just because its sender is missing.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse
from backend.tools.teams.get_messages import teams_get_messages


def _raw(
    msg_id: str,
    sent_at: str,
    content: str,
    *,
    content_type: str = "html",
    sender_name: Optional[str] = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": msg_id,
        "createdDateTime": sent_at,
        "content": content,
        "contentType": content_type,
    }
    if sender_name is not None:
        entry["senderName"] = sender_name
    return entry


def test_system_event_message_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                _raw("m1", "2026-08-30T09:00:00Z", "<systemEventMessage/>"),
                _raw("m2", "2026-08-30T09:01:00Z", "<p>Hello team</p>", sender_name="Alex"),
            ],
        ),
    )

    result = teams_get_messages(chat_id="c1")

    assert [m["id"] for m in result["messages"]] == ["m2"]
    assert result["retrieved_count_raw"] == 2
    assert result["retrieved_count"] == 1
    assert result["filtered_system_event_count"] == 1
    assert result["oldest_retrieved_at"] == "2026-08-30T09:01:00Z"
    assert result["newest_retrieved_at"] == "2026-08-30T09:01:00Z"


def test_empty_message_with_no_sender_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_raw("m1", "2026-08-30T09:00:00Z", "")]),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["messages"] == []
    assert result["retrieved_count_raw"] == 1
    assert result["retrieved_count"] == 0
    assert result["filtered_system_event_count"] == 1
    assert result["oldest_retrieved_at"] is None
    assert result["newest_retrieved_at"] is None


def test_missing_sender_with_meaningful_text_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requirement: never exclude solely because the sender is missing --
    only a clear system/event marker or empty content justifies exclusion.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, [_raw("m1", "2026-08-30T09:00:00Z", "<p>Deploy complete</p>")]
        ),
    )

    result = teams_get_messages(chat_id="c1")

    assert len(result["messages"]) == 1
    msg = result["messages"][0]
    assert msg["author"] == "Unknown"
    assert msg["text"] == "Deploy complete"
    assert result["filtered_system_event_count"] == 0
    assert result["retrieved_count"] == 1


def test_normal_user_message_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, [_raw("m1", "2026-08-30T09:00:00Z", "<p>Status update</p>", sender_name="Priya")]
        ),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["messages"][0]["author"] == "Priya"
    assert result["messages"][0]["text"] == "Status update"
    assert result["filtered_system_event_count"] == 0
    assert result["retrieved_count_raw"] == 1
    assert result["retrieved_count"] == 1


def test_pagination_counts_remain_correct_after_filtering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Filtering must never change pagination decisions: a full 50-item
    page containing some system events is still a "full page" for
    pagination purposes, and the cursor still advances off the raw oldest
    timestamp on that page, not a post-filter one.
    """

    def _timestamp(i: int) -> str:
        return f"2026-08-30T00:{i // 60:02d}:{i % 60:02d}.000Z"

    def _msg(i: int, is_system: bool = False) -> dict[str, Any]:
        content = "<systemEventMessage/>" if is_system else f"<p>msg {i}</p>"
        sender = None if is_system else "Alex"
        return _raw(f"m{i}", _timestamp(i), content, sender_name=sender)

    # Page 1: 50 raw items (indices 50..99), 5 of them system events.
    page1_indices = list(range(50, 100))
    page1 = [_msg(i, is_system=(i % 10 == 0)) for i in reversed(page1_indices)]
    oldest_of_page1 = _timestamp(50)

    # Page 2: partial page (natural end), 10 raw items, no system events.
    page2_indices = list(range(40, 50))
    page2 = [_msg(i) for i in reversed(page2_indices)]

    calls: list[Optional[str]] = []

    def fake_post(url, json, timeout):
        before = json.get("before")
        calls.append(before)
        if before is None:
            return FakeResponse(200, page1)
        assert before == oldest_of_page1
        return FakeResponse(200, page2)

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    result = teams_get_messages(chat_id="c1")

    # Pagination itself is unaffected by filtering: exactly 2 calls, the
    # second using the raw (unfiltered) oldest timestamp from page 1.
    assert calls == [None, oldest_of_page1]
    assert result["truncated"] is False
    assert result["next_before"] is None

    assert result["retrieved_count_raw"] == 60
    assert result["filtered_system_event_count"] == 5
    assert result["retrieved_count"] == 55
    assert len(result["messages"]) == 55
