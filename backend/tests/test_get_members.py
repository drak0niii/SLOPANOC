"""Tests for backend/tools/teams/get_members.py -- deterministic,
authoritative Teams chat membership retrieval (contributor-accuracy fix).
All HTTP is mocked; the real gateway is never contacted.
"""
from __future__ import annotations

import pytest

from backend.gateway import power_automate_client as pac_module
from backend.gateway.safe_error import SafeErrorException
from backend.tests._fakes import FakeResponse
from backend.tools.teams.get_members import teams_get_members


def _member(member_id: str, display_name: str) -> dict:
    return {"id": member_id, "displayName": display_name}


def test_returns_members_with_id_and_display_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_member("m1", "Alex"), _member("m2", "Priya")]),
    )

    result = teams_get_members("chat-42")

    assert "error" not in result
    assert result["chat_id"] == "chat-42"
    assert [m["display_name"] for m in result["members"]] == ["Alex", "Priya"]
    assert [m["id"] for m in result["members"]] == ["m1", "m2"]


def test_empty_membership_is_a_valid_non_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, []))

    result = teams_get_members("chat-42")

    assert "error" not in result
    assert result["members"] == []


def test_malformed_entries_are_skipped_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a missing/malformed DISPLAY NAME is fatal to an entry (bugfix:
    `id` is decoupled -- see `test_a_member_with_no_recognizable_id_field_
    still_contributes_a_display_name` -- so `{"displayName": "No Id"}` and
    a non-string `id` both still produce a valid member here)."""
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                _member("m1", "Alex"),
                "not a dict",
                {"id": "m2"},  # missing displayName -- genuinely unusable
                {"displayName": "No Id"},
                {"id": 123, "displayName": "Non-string id"},
            ],
        ),
    )

    result = teams_get_members("chat-42")

    assert [m["display_name"] for m in result["members"]] == ["Alex", "No Id", "Non-string id"]


def test_requires_a_chat_id() -> None:
    result = teams_get_members("")
    assert "error" in result


def test_tolerates_alternate_display_name_key_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bugfix: don't blindly require exactly `displayName` -- this
    endpoint's shape was never live-verified (see get_messages.py's own
    module docstring on this class of live-shape surprise)."""
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                {"id": "m1", "displayName": "Alex"},
                {"id": "m2", "display_name": "Priya"},
                {"id": "m3", "name": "Sam"},
            ],
        ),
    )

    result = teams_get_members("chat-42")

    assert [m["display_name"] for m in result["members"]] == ["Alex", "Priya", "Sam"]


def test_tolerates_alternate_member_id_key_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                {"id": "m1", "displayName": "Alex"},
                {"memberId": "m2", "displayName": "Priya"},
                {"userId": "m3", "displayName": "Sam"},
            ],
        ),
    )

    result = teams_get_members("chat-42")

    assert [m["id"] for m in result["members"]] == ["m1", "m2", "m3"]


def test_a_member_with_no_recognizable_id_field_still_contributes_a_display_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bugfix: `id` is decoupled from whether the entry is usable at all --
    only `display_name` is actually needed by this milestone's caller."""
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [{"displayName": "No Id Here"}]),
    )

    result = teams_get_members("chat-42")

    assert [m["display_name"] for m in result["members"]] == ["No Id Here"]
    assert result["members"][0]["id"] is None


def test_gateway_failure_returns_a_safe_error_not_a_raised_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(500))

    result = teams_get_members("chat-42")

    assert "error" in result
    assert result["error"]["errorCode"] == "run_failure"


def test_never_raises_safe_error_exception_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(500))
    # Contract: callers (resolve_authoritative_contributors) rely on this
    # returning a dict with "error", never raising -- mirrors
    # teams_get_messages' own contract.
    try:
        teams_get_members("chat-42")
    except SafeErrorException:
        pytest.fail("teams_get_members must return {'error': ...}, never raise")
