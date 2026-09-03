"""Unit tests for teams_get_messages's deterministic multi-page retrieval.

The live Power Automate `teams.getMessages` flow returns up to 50
messages per call (Top=50, OrderBy=createdDateTime desc), newest first,
and accepts an optional `before` cursor for the next older page. These
tests simulate that behavior entirely offline via a stateful fake
`requests.post` that dispatches on the request body's `before` value, so
each test controls exactly what each successive page returns.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse
from backend.tools.teams.get_messages import (
    DEFAULT_MAX_MESSAGES,
    _PAGE_SIZE,
    teams_get_messages,
)


def _raw(msg_id: str, sent_at: str, sender: str = "Alex", content: str = "hi") -> dict[str, Any]:
    return {
        "id": msg_id,
        "createdDateTime": sent_at,
        "senderName": sender,
        "contentType": "text",
        "content": content,
    }


def _timestamp(i: int) -> str:
    """Monotonically increasing (with i) ISO-8601 timestamp, zero-padded
    so lexicographic order matches chronological order -- message index 0
    is the oldest, higher i is newer.
    """
    return f"2026-08-30T00:{i // 60:02d}:{i % 60:02d}.000Z"


def _page_of(indices: list[int]) -> list[dict[str, Any]]:
    """Build a raw page for the given message indices, newest-first
    (descending timestamp) -- matching the live gateway's OrderBy.
    """
    return [_raw(f"m{i}", _timestamp(i)) for i in sorted(indices, reverse=True)]


class _StatefulFakePost:
    """Dispatches canned pages by the request body's `before` cursor, and
    records every `before` value used, in call order.
    """

    def __init__(self, pages_by_before: dict[Optional[str], object]) -> None:
        self._pages_by_before = pages_by_before
        self.calls: list[Optional[str]] = []

    def __call__(self, url, json, timeout):
        before = json.get("before")
        self.calls.append(before)
        if before not in self._pages_by_before:
            raise AssertionError(f"Unexpected 'before' cursor requested: {before!r}")
        page = self._pages_by_before[before]
        if isinstance(page, int):
            return FakeResponse(page)  # simulate an HTTP error status
        return FakeResponse(200, page)


def _install(monkeypatch: pytest.MonkeyPatch, pages_by_before: dict) -> _StatefulFakePost:
    fake_post = _StatefulFakePost(pages_by_before)
    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    return fake_post


def test_single_partial_page_stops_after_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_post = _install(monkeypatch, {None: _page_of([0, 1, 2])})

    result = teams_get_messages(chat_id="c1")

    assert result["retrieved_count"] == 3
    assert result["truncated"] is False
    assert result["next_before"] is None
    assert fake_post.calls == [None]


def test_full_page_then_partial_second_page(monkeypatch: pytest.MonkeyPatch) -> None:
    page1_indices = list(range(50, 100))  # newest 50, indices 50..99
    page2_indices = list(range(40, 50))  # older partial page, 10 messages
    oldest_of_page1 = _timestamp(50)

    fake_post = _install(
        monkeypatch,
        {
            None: _page_of(page1_indices),
            oldest_of_page1: _page_of(page2_indices),
        },
    )

    result = teams_get_messages(chat_id="c1")

    assert result["retrieved_count"] == 60
    assert result["truncated"] is False
    assert result["next_before"] is None
    assert fake_post.calls == [None, oldest_of_page1]


def test_three_plus_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    # 120 messages total: two full 50-message pages, one 20-message page.
    page1 = list(range(70, 120))
    page2 = list(range(20, 70))
    page3 = list(range(0, 20))
    before1 = _timestamp(70)
    before2 = _timestamp(20)

    fake_post = _install(
        monkeypatch,
        {
            None: _page_of(page1),
            before1: _page_of(page2),
            before2: _page_of(page3),
        },
    )

    result = teams_get_messages(chat_id="c1")

    assert result["retrieved_count"] == 120
    assert result["truncated"] is False
    assert fake_post.calls == [None, before1, before2]


def test_stops_at_default_max_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    """5 full pages' worth of history exist (250 messages), but the
    default ceiling (200) must stop retrieval after exactly 4 pages.
    """
    assert DEFAULT_MAX_MESSAGES == 200
    assert _PAGE_SIZE == 50

    pages_by_before: dict[Optional[str], list[dict[str, Any]]] = {}
    cursor = None
    # Build 5 pages of 50, newest (page index 4) to oldest (page index 0).
    for page_index in reversed(range(5)):
        start = page_index * 50
        indices = list(range(start, start + 50))
        pages_by_before[cursor] = _page_of(indices)
        cursor = _timestamp(start)

    fake_post = _install(monkeypatch, pages_by_before)

    result = teams_get_messages(chat_id="c1")

    assert result["retrieved_count"] == 200
    assert result["truncated"] is True
    assert result["next_before"] == _timestamp(50)  # oldest of the 4th page fetched
    assert len(fake_post.calls) == 4  # never fetched the 5th page


def test_empty_next_page_stops_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = list(range(0, 50))
    oldest_of_page1 = _timestamp(0)

    fake_post = _install(
        monkeypatch,
        {
            None: _page_of(page1),
            oldest_of_page1: [],
        },
    )

    result = teams_get_messages(chat_id="c1")

    assert result["retrieved_count"] == 50
    assert result["truncated"] is False
    assert result["next_before"] is None
    assert fake_post.calls == [None, oldest_of_page1]


def test_deduplicates_messages_by_id_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """A message id appearing on more than one page (e.g. a boundary tie)
    is only counted/returned once.
    """
    page1 = _page_of(list(range(1, 51)))  # m1..m50
    # Page 2 re-sends m1 (the oldest of page 1, a boundary tie) plus 9
    # genuinely new, older messages.
    page2 = _page_of(list(range(-8, 2)))  # m-8..m1 (m1 duplicates page 1)
    before = _timestamp(1)

    fake_post = _install(monkeypatch, {None: page1, before: page2})

    result = teams_get_messages(chat_id="c1")

    ids = [m["id"] for m in result["messages"]]
    assert len(ids) == len(set(ids))  # no duplicate ids in the result
    assert result["retrieved_count"] == len(ids)
    # 50 (page 1) + 9 new from page 2 (m1 is the duplicate) = 59.
    assert result["retrieved_count"] == 59


def test_cursor_not_advancing_stops_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    """A misbehaving gateway response that doesn't actually return
    strictly-older messages must not loop forever.
    """
    stalled_timestamp = _timestamp(0)
    page1 = [_raw(f"a{i}", stalled_timestamp) for i in range(50)]
    # "Next" page reports the exact same oldest timestamp instead of an
    # older one -- the cursor cannot advance past it.
    page2 = [_raw(f"b{i}", stalled_timestamp) for i in range(50)]

    fake_post = _install(monkeypatch, {None: page1, stalled_timestamp: page2})

    result = teams_get_messages(chat_id="c1")

    assert result["retrieved_count"] == 100  # both pages still counted
    assert result["truncated"] is False  # stopped for cursor stall, not max_messages
    assert result["next_before"] == stalled_timestamp
    assert len(fake_post.calls) == 2  # did not loop a third time


def test_before_cursor_passed_correctly_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = _page_of(list(range(10, 60)))
    before = _timestamp(10)

    fake_post = _install(monkeypatch, {None: page1, before: []})

    teams_get_messages(chat_id="c1")

    assert fake_post.calls[0] is None
    assert fake_post.calls[1] == before


def test_final_messages_are_in_chronological_order(monkeypatch: pytest.MonkeyPatch) -> None:
    # Page arrives newest-first (as the live gateway sends it); the tool
    # must still return oldest -> newest.
    page1 = _page_of([2, 3, 4])
    fake_post = _install(monkeypatch, {None: page1})

    result = teams_get_messages(chat_id="c1")

    sent_ats = [m["sent_at"] for m in result["messages"]]
    assert sent_ats == sorted(sent_ats)
    assert [m["id"] for m in result["messages"]] == ["m2", "m3", "m4"]


def test_truncated_is_false_when_history_is_fully_retrieved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, {None: _page_of([0, 1])})

    result = teams_get_messages(chat_id="c1")

    assert result["truncated"] is False


def test_gateway_failure_on_a_later_page_maps_to_safe_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page1 = _page_of(list(range(50, 100)))
    before = _timestamp(50)

    # Page 2 fails with an HTTP 500.
    _install(monkeypatch, {None: page1, before: 500})

    result = teams_get_messages(chat_id="c1")

    assert "error" in result
    assert result["error"]["errorCode"] == "run_failure"
    assert "messages" not in result
