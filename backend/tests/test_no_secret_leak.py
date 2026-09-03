"""Cross-cutting tests: no Power Automate secret ever appears in tool
output, under success and failure paths, across both tools and the
client itself.
"""
from __future__ import annotations

import pytest
import requests

from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse, chat, message
from backend.tests.conftest import FAKE_GATEWAY_URL
from backend.tools.teams.get_messages import teams_get_messages
from backend.tools.teams.list_chats import teams_list_chats

_SECRET_MARKER = "THIS_IS_A_FAKE_TEST_SECRET_VALUE_1234567890"


def _assert_no_leak(payload: object) -> None:
    serialized = repr(payload)
    assert FAKE_GATEWAY_URL not in serialized
    assert _SECRET_MARKER not in serialized
    assert "sig=" not in serialized


def test_no_leak_on_successful_list_chats(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"chats": [chat("c1", "Ops Bridge")]}),
    )
    _assert_no_leak(teams_list_chats(topic="Ops Bridge"))


def test_no_leak_on_successful_get_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, {"messages": [message("m1", "Alex", "hi", "2026-08-30T09:00:00Z")]}
        ),
    )
    _assert_no_leak(teams_get_messages(chat_id="c1"))


def test_no_leak_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args, **kwargs):
        raise requests.exceptions.ConnectionError(f"Could not connect to {FAKE_GATEWAY_URL}")

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    _assert_no_leak(teams_list_chats(topic="Anything"))
    _assert_no_leak(teams_get_messages(chat_id="c1"))


def test_no_leak_on_http_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(500))

    _assert_no_leak(teams_list_chats(topic="Anything"))
    _assert_no_leak(teams_get_messages(chat_id="c1"))


def test_no_leak_on_missing_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SLOPANOC_POWER_AUTOMATE_GATEWAY_URL", raising=False)
    monkeypatch.delenv("SLOPANOC_POWER_AUTOMATE_SECRET_RESOURCE", raising=False)

    _assert_no_leak(teams_list_chats(topic="Anything"))
    _assert_no_leak(teams_get_messages(chat_id="c1"))


def test_client_repr_never_holds_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """PowerAutomateClient must not expose the URL via a naive repr/str --
    it resolves it fresh inside `_call` every time, never stores it as a
    persistent, printable attribute.
    """
    client = pac_module.PowerAutomateClient()
    assert FAKE_GATEWAY_URL not in repr(client)
    assert _SECRET_MARKER not in repr(client)
