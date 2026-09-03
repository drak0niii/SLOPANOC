"""End-to-end tests for teams_get_messages's time-range support
(`from_datetime`/`to_datetime`).
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse
from backend.tools.teams.get_messages import teams_get_messages


def _raw(msg_id: str, sent_at: str, sender: str = "Alex") -> dict[str, Any]:
    return {
        "id": msg_id,
        "createdDateTime": sent_at,
        "senderName": sender,
        "contentType": "html",
        "content": f"<p>msg {msg_id}</p>",
    }


def _timestamp(i: int) -> str:
    return f"2026-08-30T00:{i // 60:02d}:{i % 60:02d}.000Z"


def test_no_time_range_behaves_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_raw("m1", "2026-08-27T09:00:00Z")]),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["requested_from"] is None
    assert result["requested_to"] is None
    assert result["range_fully_covered"] is True
    assert result["filtered_out_of_range_count"] == 0
    assert result["retrieved_count"] == 1


def test_from_datetime_only_includes_lower_boundary_and_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        _raw("m1", "2026-08-24T23:00:00Z"),  # before the range
        _raw("m2", "2026-08-25T00:00:00Z"),  # exactly on the lower boundary
        _raw("m3", "2026-08-26T12:00:00Z"),  # within range
    ]
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, messages))

    result = teams_get_messages(chat_id="c1", from_datetime="2026-08-25T00:00:00Z")

    assert [m["id"] for m in result["messages"]] == ["m2", "m3"]
    assert result["requested_from"] == "2026-08-25T00:00:00Z"
    assert result["requested_to"] is None
    assert result["filtered_out_of_range_count"] == 1


def test_to_datetime_seeds_the_first_request_before_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(url, json, timeout):
        captured["before"] = json.get("before")
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    teams_get_messages(chat_id="c1", to_datetime="2026-08-28T00:00:00Z")

    assert captured["before"] == "2026-08-28T00:00:00Z"


def test_to_datetime_only_excludes_messages_at_or_after_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gateway itself applies `createdDateTime lt before` once we
    seed the cursor -- simulated here so this also proves rule 1 (don't
    unnecessarily fetch newer messages).
    """
    all_messages = [
        _raw("m1", "2026-08-26T09:00:00Z"),
        _raw("m2", "2026-08-27T09:00:00Z"),
        _raw("m3", "2026-08-28T09:00:00Z"),  # newer than to_datetime
    ]

    def fake_post(url, json, timeout):
        before = json.get("before")
        page = [m for m in all_messages if before is None or m["createdDateTime"] < before]
        return FakeResponse(200, page)

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    result = teams_get_messages(chat_id="c1", to_datetime="2026-08-28T00:00:00Z")

    assert [m["id"] for m in result["messages"]] == ["m1", "m2"]
    assert result["requested_to"] == "2026-08-28T00:00:00Z"


def test_message_exactly_on_upper_boundary_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defense in depth: even if the (mocked) gateway returns a message
    at exactly `to_datetime`, the tool's own final filter must still
    exclude it (exclusive upper bound).
    """
    messages = [
        _raw("m1", "2026-08-27T09:00:00Z"),
        _raw("m2", "2026-08-28T00:00:00Z"),  # == to_datetime -> excluded
    ]
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, messages))

    result = teams_get_messages(chat_id="c1", to_datetime="2026-08-28T00:00:00Z")

    assert [m["id"] for m in result["messages"]] == ["m1"]
    assert result["filtered_out_of_range_count"] == 1


def test_both_boundaries(monkeypatch: pytest.MonkeyPatch) -> None:
    messages = [
        _raw("m1", "2026-08-24T23:00:00Z"),  # before range
        _raw("m2", "2026-08-25T00:00:00Z"),  # == from -> included
        _raw("m3", "2026-08-26T12:00:00Z"),  # within range
        _raw("m4", "2026-08-28T00:00:00Z"),  # == to -> excluded
    ]
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, messages))

    result = teams_get_messages(
        chat_id="c1",
        from_datetime="2026-08-25T00:00:00Z",
        to_datetime="2026-08-28T00:00:00Z",
    )

    assert [m["id"] for m in result["messages"]] == ["m2", "m3"]
    assert result["requested_from"] == "2026-08-25T00:00:00Z"
    assert result["requested_to"] == "2026-08-28T00:00:00Z"
    assert result["filtered_out_of_range_count"] == 2


def test_pagination_stops_once_lower_boundary_is_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page1 = [_raw(f"m{i}", _timestamp(i)) for i in reversed(range(50, 100))]
    page2 = [_raw(f"m{i}", _timestamp(i)) for i in reversed(range(0, 50))]

    calls: list[Optional[str]] = []

    def fake_post(url, json, timeout):
        before = json.get("before")
        calls.append(before)
        return FakeResponse(200, page1 if before is None else page2)

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    from_datetime = _timestamp(30)
    result = teams_get_messages(chat_id="c1", from_datetime=from_datetime)

    assert len(calls) == 2  # never fetched a (nonexistent) third page
    assert result["range_fully_covered"] is True
    assert result["truncated"] is False
    assert {m["id"] for m in result["messages"]} == {f"m{i}" for i in range(30, 100)}


def test_max_messages_reached_before_lower_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page1 = [_raw(f"m{i}", _timestamp(i)) for i in reversed(range(100, 150))]
    page2 = [_raw(f"m{i}", _timestamp(i)) for i in reversed(range(50, 100))]
    page3 = [_raw(f"m{i}", _timestamp(i)) for i in reversed(range(0, 50))]

    calls: list[Optional[str]] = []

    def fake_post(url, json, timeout):
        before = json.get("before")
        calls.append(before)
        if before is None:
            return FakeResponse(200, page1)
        if before == _timestamp(100):
            return FakeResponse(200, page2)
        return FakeResponse(200, page3)

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    result = teams_get_messages(chat_id="c1", max_messages=100, from_datetime=_timestamp(10))

    assert len(calls) == 2  # stopped at the ceiling, never reached page 3
    assert result["truncated"] is True
    assert result["range_fully_covered"] is False
    assert result["retrieved_count_raw"] == 100


def test_invalid_from_datetime_fails_safely_without_calling_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("must not call the gateway for an invalid timestamp")

    monkeypatch.setattr(pac_module.requests, "post", fail)

    result = teams_get_messages(chat_id="c1", from_datetime="not-a-timestamp")

    assert result["error"]["errorCode"] == "validation_error"


def test_invalid_to_datetime_fails_safely_without_calling_the_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("must not call the gateway for an invalid timestamp")

    monkeypatch.setattr(pac_module.requests, "post", fail)

    result = teams_get_messages(chat_id="c1", to_datetime="yesterday")

    assert result["error"]["errorCode"] == "validation_error"


def test_chronological_ordering_is_preserved_with_a_time_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        _raw("m3", "2026-08-27T09:00:00Z"),
        _raw("m1", "2026-08-25T09:00:00Z"),
        _raw("m2", "2026-08-26T09:00:00Z"),
    ]
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, messages))

    result = teams_get_messages(
        chat_id="c1",
        from_datetime="2026-08-25T00:00:00Z",
        to_datetime="2026-08-28T00:00:00Z",
    )

    assert [m["id"] for m in result["messages"]] == ["m1", "m2", "m3"]
