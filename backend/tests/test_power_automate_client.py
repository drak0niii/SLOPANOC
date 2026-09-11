"""Unit tests for PowerAutomateClient -- the single, deterministic Power
Automate HTTP client abstraction. All HTTP calls are mocked; the real
gateway is never contacted. See backend/tests/manual/ for the live-gateway
check.
"""
from __future__ import annotations

import pytest
import requests

from backend.gateway import power_automate_client as pac_module
from backend.gateway.power_automate_client import PowerAutomateClient
from backend.gateway.safe_error import SafeErrorException
from backend.tests._fakes import FakeResponse
from backend.tests.conftest import FAKE_GATEWAY_URL


def test_list_chats_sends_expected_request_body(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return FakeResponse(200, {"chats": []})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    PowerAutomateClient().list_chats()

    assert captured["url"] == FAKE_GATEWAY_URL
    assert captured["json"]["version"] == "1.0"
    assert captured["json"]["operation"] == "teams.listChats"
    assert captured["json"]["requestId"]
    assert "chatId" not in captured["json"]


def test_get_messages_sends_resolved_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return FakeResponse(200, {"messages": []})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    PowerAutomateClient().get_messages("chat-42")

    assert captured["json"]["operation"] == "teams.getMessages"
    assert captured["json"]["chatId"] == "chat-42"


def test_get_members_sends_resolved_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return FakeResponse(200, {"members": []})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    PowerAutomateClient().get_members("chat-42")

    assert captured["json"]["operation"] == "teams.getMembers"
    assert captured["json"]["chatId"] == "chat-42"


def test_get_members_requires_a_chat_id() -> None:
    with pytest.raises(SafeErrorException) as exc_info:
        PowerAutomateClient().get_members("")

    assert exc_info.value.safe_error.error_code == "validation_error"


def test_get_hosted_content_sends_exact_operation_and_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Teams Rich Content milestone (single-image scope) -- proves the
    OUTGOING request shape exactly (test matrix case G): the fixed
    `teams.getHostedContent` operation with `chatId`/`messageId`/
    `hostedContentId` and nothing else project-specific added.
    """
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["json"] = json
        return FakeResponse(200, {"success": True, "contentType": "image/png", "contentBase64": "AA=="})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    PowerAutomateClient().get_hosted_content("chat-42", "msg-1", "content-abc")

    assert captured["json"]["operation"] == "teams.getHostedContent"
    assert captured["json"]["chatId"] == "chat-42"
    assert captured["json"]["messageId"] == "msg-1"
    assert captured["json"]["hostedContentId"] == "content-abc"


@pytest.mark.parametrize(
    "chat_id,message_id,hosted_content_id",
    [
        ("", "msg-1", "content-abc"),
        ("chat-42", "", "content-abc"),
        ("chat-42", "msg-1", ""),
        ("   ", "msg-1", "content-abc"),
    ],
)
def test_get_hosted_content_requires_all_three_ids(chat_id: str, message_id: str, hosted_content_id: str) -> None:
    with pytest.raises(SafeErrorException) as exc_info:
        PowerAutomateClient().get_hosted_content(chat_id, message_id, hosted_content_id)

    assert exc_info.value.safe_error.error_code == "validation_error"


def test_timeout_maps_to_run_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args, **kwargs):
        raise requests.exceptions.Timeout("timed out")

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    with pytest.raises(SafeErrorException) as exc_info:
        PowerAutomateClient().list_chats()

    assert exc_info.value.safe_error.error_code == "run_failure"
    assert exc_info.value.safe_error.retryable is True


def test_connection_error_never_leaks_url_in_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(*args, **kwargs):
        # requests' real ConnectionError messages typically echo the URL --
        # this is exactly the leak this client must prevent.
        raise requests.exceptions.ConnectionError(
            f"Failed to establish a new connection to {FAKE_GATEWAY_URL}"
        )

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    with pytest.raises(SafeErrorException) as exc_info:
        PowerAutomateClient().list_chats()

    safe_error = exc_info.value.safe_error
    assert safe_error.error_code == "run_failure"
    assert FAKE_GATEWAY_URL not in safe_error.user_message
    assert "sig=" not in safe_error.user_message


def test_rate_limit_status_maps_to_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(429))

    with pytest.raises(SafeErrorException) as exc_info:
        PowerAutomateClient().list_chats()

    assert exc_info.value.safe_error.error_code == "rate_limited"
    assert exc_info.value.safe_error.retryable is True


def test_server_error_status_maps_to_run_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(500))

    with pytest.raises(SafeErrorException) as exc_info:
        PowerAutomateClient().list_chats()

    assert exc_info.value.safe_error.error_code == "run_failure"


def test_successful_call_returns_parsed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"chats": [{"chatId": "c1", "title": "X"}]}),
    )

    result = PowerAutomateClient().list_chats()

    assert result == {"chats": [{"chatId": "c1", "title": "X"}]}


def test_successful_call_logs_safe_perf_timing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, []))

    with caplog.at_level("INFO", logger="backend.perf"):
        PowerAutomateClient().get_messages("chat-42")

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "stage=power_automate_gateway" in message
    assert "operation=teams.getMessages" in message
    assert "outcome=ok" in message
    assert "duration_ms=" in message
    assert FAKE_GATEWAY_URL not in message
    assert "chat-42" not in message


def test_failed_call_still_logs_perf_timing_with_a_safe_outcome(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(500))

    with caplog.at_level("INFO", logger="backend.perf"):
        with pytest.raises(SafeErrorException):
            PowerAutomateClient().list_chats()

    message = caplog.records[0].getMessage()
    assert "outcome=http_500" in message


def test_missing_gateway_configuration_is_reported_as_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SLOPANOC_POWER_AUTOMATE_GATEWAY_URL", raising=False)
    monkeypatch.delenv("SLOPANOC_POWER_AUTOMATE_SECRET_RESOURCE", raising=False)

    with pytest.raises(SafeErrorException) as exc_info:
        PowerAutomateClient().list_chats()

    assert exc_info.value.safe_error.error_code == "internal_error"
