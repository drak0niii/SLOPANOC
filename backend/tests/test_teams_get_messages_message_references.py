"""End-to-end tests for Teams message-reference handling in
teams_get_messages: parsing, text-normalization suppression (never
"[Attachment]" for a quote/reply), and known-message-id tracking for
evidence validation.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY, teams_get_messages


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = dict(state or {})


def _reference_json(
    message_id: str = "1788182857077",
    preview: Optional[str] = "Test message sent from SLOPANOC Gateway",
    sender_name: Optional[str] = "Referenced User",
    sender_id: Optional[str] = "user-ref-1",
) -> str:
    payload: dict[str, Any] = {"messageId": message_id}
    if preview is not None:
        payload["messagePreview"] = preview
    if sender_name is not None or sender_id is not None:
        user: dict[str, Any] = {}
        if sender_id is not None:
            user["id"] = sender_id
        if sender_name is not None:
            user["displayName"] = sender_name
        payload["messageSender"] = {"user": user}
    return json.dumps(payload)


def _message_with_reference(
    msg_id: str = "m2",
    author: str = "Current User",
    sent_at: str = "2026-08-31T13:31:00Z",
    reference_attachment_id: str = "1788182857077",
    reference_message_id: str = "1788182857077",
) -> dict[str, Any]:
    return {
        "id": msg_id,
        "createdDateTime": sent_at,
        "senderName": author,
        "contentType": "html",
        "content": f'<attachment id="{reference_attachment_id}"></attachment><p>Is this it?</p>',
        "attachments": [
            {
                "id": reference_attachment_id,
                "contentType": "messageReference",
                "content": _reference_json(message_id=reference_message_id),
            }
        ],
    }


def test_message_reference_no_longer_becomes_attachment_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_message_with_reference()])
    )

    result = teams_get_messages(chat_id="c1")

    text = result["messages"][0]["text"]
    assert text == "Is this it?"
    assert "[Attachment]" not in text


def test_message_reference_is_parsed_into_message_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_message_with_reference()])
    )

    result = teams_get_messages(chat_id="c1")

    refs = result["messages"][0]["message_references"]
    assert refs == [
        {
            "message_id": "1788182857077",
            "preview": "Test message sent from SLOPANOC Gateway",
            "sender_name": "Referenced User",
            "sender_id": "user-ref-1",
        }
    ]


def test_author_attribution_remains_correct(monkeypatch: pytest.MonkeyPatch) -> None:
    """The replying message's own author must never be confused with the
    referenced message's sender.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_message_with_reference(author="Current User")]),
    )

    result = teams_get_messages(chat_id="c1")

    msg = result["messages"][0]
    assert msg["author"] == "Current User"
    assert msg["message_references"][0]["sender_name"] == "Referenced User"
    assert msg["author"] != msg["message_references"][0]["sender_name"]


def test_malformed_message_reference_json_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = _message_with_reference()
    entry["attachments"][0]["content"] = "{not valid json"

    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [entry]))

    result = teams_get_messages(chat_id="c1")

    assert "error" not in result
    msg = result["messages"][0]
    # The <attachment> tag is still suppressed -- it is structurally known
    # to be a message reference even though its content could not be
    # parsed -- but no reference object is invented for it.
    assert msg["text"] == "Is this it?"
    assert msg["message_references"] == []


def test_genuine_attachment_still_becomes_attachment_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {
        "id": "m1",
        "createdDateTime": "2026-08-31T13:00:00Z",
        "senderName": "Alex",
        "contentType": "html",
        "content": '<p>See attached: <attachment id="file-1"></attachment></p>',
        "attachments": [{"id": "file-1", "contentType": "file", "content": "irrelevant"}],
    }
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [entry]))

    result = teams_get_messages(chat_id="c1")

    msg = result["messages"][0]
    assert msg["text"] == "See attached: [Attachment]"
    assert msg["message_references"] == []


def test_multiple_references_are_all_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = {
        "id": "m3",
        "createdDateTime": "2026-08-31T14:00:00Z",
        "senderName": "Current User",
        "contentType": "html",
        "content": (
            '<attachment id="ref-1"></attachment>'
            '<attachment id="ref-2"></attachment>'
            "<p>Both, actually</p>"
        ),
        "attachments": [
            {
                "id": "ref-1",
                "contentType": "messageReference",
                "content": _reference_json(message_id="ref-msg-1", sender_name="User A"),
            },
            {
                "id": "ref-2",
                "contentType": "messageReference",
                "content": _reference_json(message_id="ref-msg-2", sender_name="User B"),
            },
        ],
    }
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [entry]))

    result = teams_get_messages(chat_id="c1")

    msg = result["messages"][0]
    assert msg["text"] == "Both, actually"
    assert "[Attachment]" not in msg["text"]
    ref_ids = {r["message_id"] for r in msg["message_references"]}
    assert ref_ids == {"ref-msg-1", "ref-msg-2"}


def test_normal_message_without_references_is_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = {
        "id": "m1",
        "createdDateTime": "2026-08-31T13:00:00Z",
        "senderName": "Alex",
        "contentType": "html",
        "content": "<p>Just a normal message</p>",
    }
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [entry]))

    result = teams_get_messages(chat_id="c1")

    msg = result["messages"][0]
    assert msg["text"] == "Just a normal message"
    assert msg["message_references"] == []


def test_known_message_ids_recorded_in_session_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_message_with_reference(msg_id="m2")]),
    )
    ctx = _FakeToolContext()

    teams_get_messages(chat_id="c1", tool_context=ctx)

    known = set(ctx.state[KNOWN_MESSAGE_IDS_STATE_KEY])
    assert "m2" in known
    assert "1788182857077" in known  # the referenced message's id too


def test_known_message_ids_accumulate_without_a_context_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tool_context is optional -- omitting it must not raise or change
    retrieval behavior.
    """
    monkeypatch.setattr(
        pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_message_with_reference()])
    )

    result = teams_get_messages(chat_id="c1")

    assert "error" not in result
    assert len(result["messages"]) == 1
