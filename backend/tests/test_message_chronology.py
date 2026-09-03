"""Deterministic foundation for incident_manager's "latest/earliest
message" prompt guidance (see MESSAGE CHRONOLOGY in
backend/agents/incident_manager/prompts.py).

teams_get_messages already returns `messages` ordered chronologically
oldest -> newest (proven elsewhere, e.g.
test_teams_get_messages_pagination.py). These tests confirm the specific
property that guidance depends on: `messages[-1]` is always the newest
retrieved message and `messages[0]` is always the oldest retrieved
message, including when retrieval is truncated (`latest_window`/
`partial_range`), and that sender/timestamp/content for both ends are
available directly on the message entries themselves -- never something
that needs to be reconstructed from `evidence` (which never carries
message content).
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


def test_last_entry_is_newest_message_under_complete_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                _msg("m1", "2026-08-25T09:00:00Z", content="<p>first</p>", sender="User A"),
                _msg("m2", "2026-08-26T09:00:00Z", content="<p>middle</p>", sender="User B"),
                _msg("m3", "2026-08-27T09:00:00Z", content="<p>latest one</p>", sender="User C"),
            ],
        ),
    )

    result = teams_get_messages(chat_id="c1")

    newest = result["messages"][-1]
    assert newest["id"] == "m3"
    assert newest["author"] == "User C"
    assert newest["sent_at"] == "2026-08-27T09:00:00Z"
    assert newest["text"] == "latest one"
    assert result["coverage"]["status"] == "complete"


def test_first_entry_is_oldest_message_under_complete_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                _msg("m1", "2026-08-25T09:00:00Z", content="<p>oldest one</p>", sender="User A"),
                _msg("m2", "2026-08-26T09:00:00Z", content="<p>middle</p>", sender="User B"),
                _msg("m3", "2026-08-27T09:00:00Z", content="<p>last</p>", sender="User C"),
            ],
        ),
    )

    result = teams_get_messages(chat_id="c1")

    oldest = result["messages"][0]
    assert oldest["id"] == "m1"
    assert oldest["author"] == "User A"
    assert oldest["sent_at"] == "2026-08-25T09:00:00Z"
    assert oldest["text"] == "oldest one"
    assert result["coverage"]["status"] == "complete"


def test_last_entry_is_newest_message_under_latest_window_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reuses the established 5-page/250-message pagination fixture: the
    default max_messages ceiling (200) truncates retrieval before reaching
    the oldest messages, but this must never affect which message is
    identified as newest -- retrieval always starts from the newest end.
    """
    pages_by_before: dict[Optional[str], list[dict[str, Any]]] = {}
    cursor: Optional[str] = None
    for page_index in reversed(range(5)):
        start = page_index * 50
        pages_by_before[cursor] = [
            _msg(f"m{i}", _timestamp(i), content=f"<p>content {i}</p>") for i in reversed(range(start, start + 50))
        ]
        cursor = _timestamp(start)
    # The very newest message in the whole (unretrieved-in-full) history.
    pages_by_before[None][0] = _msg(
        "m249", _timestamp(249), content="<p>the newest message</p>", sender="User Z"
    )

    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda url, json, timeout: FakeResponse(200, pages_by_before[json.get("before")]),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["coverage"]["status"] == "latest_window"
    assert result["coverage"]["retrieved_count"] == 200
    newest = result["messages"][-1]
    assert newest["id"] == "m249"
    assert newest["author"] == "User Z"
    assert newest["text"] == "the newest message"


def test_first_entry_under_latest_window_is_oldest_of_retrieved_portion_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Under truncated coverage, `messages[0]` is still deterministically
    correct as "the oldest message actually retrieved" -- it is
    incident_manager's prompt-level responsibility (not this deterministic
    layer's) to qualify that as "in the retrieved portion" rather than
    "the first message ever sent". This test only pins the deterministic
    fact the prompt guidance relies on.
    """
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

    assert result["coverage"]["status"] == "latest_window"
    oldest_retrieved = result["messages"][0]
    # The 200-message ceiling (5 pages of 50, newest-first) retrieves
    # indices 50..249, so the oldest *retrieved* message is m50 -- not m0,
    # which exists in the chat's full history but was never fetched.
    assert oldest_retrieved["id"] == "m50"
    assert oldest_retrieved["sent_at"] == _timestamp(50)


def test_newest_message_content_and_author_are_on_the_message_not_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity check on the schema boundary the prompt guidance relies on:
    `messages` entries carry `text`, but `TeamsEvidence` (built separately,
    from `evidence.py`) never does -- so "what was the latest message" must
    be answered from `messages[-1]`, never reconstructed from `evidence`.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, [_msg("m1", "2026-08-31T13:31:00Z", content="<p>hello there</p>", sender="User A")]
        ),
    )

    result = teams_get_messages(chat_id="c1")

    newest = result["messages"][-1]
    assert "text" in newest and newest["text"] == "hello there"

    from backend.agents.incident_manager.schemas import TeamsEvidence

    # Snippet-authenticity fix: `TeamsEvidence` never carries message
    # text/content at all -- the Source drawer's excerpt is now built
    # entirely by deterministic backend code (source_reference.py) from
    # the retrieved message's own text, looked up by `message_id`, never
    # from anything on this model-facing schema.
    assert "text" not in TeamsEvidence.model_fields
    assert "content" not in TeamsEvidence.model_fields
    assert "snippet" not in TeamsEvidence.model_fields
    assert set(TeamsEvidence.model_fields) == {"message_id", "author", "sent_at"}
