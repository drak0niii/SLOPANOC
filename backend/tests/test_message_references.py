"""Unit tests for backend.tools.teams.message_references -- deterministic
parsing of Teams "messageReference" attachments (quotes/replies), not
file/image attachments. Pure functions, no mocking needed.
"""
from __future__ import annotations

import json
from typing import Any

from backend.tools.teams.message_references import (
    is_message_reference_attachment,
    parse_message_reference,
)


def _reference_attachment(**overrides: Any) -> dict[str, Any]:
    payload = {
        "messageId": "1788182857077",
        "messagePreview": "Test message sent from SLOPANOC Gateway",
        "messageSender": {"user": {"id": "user-ref-1", "displayName": "Referenced User"}},
    }
    content = overrides.pop("content", json.dumps(payload))
    attachment = {
        "id": "1788182857077",
        "contentType": "messageReference",
        "content": content,
    }
    attachment.update(overrides)
    return attachment


def test_is_message_reference_attachment_true_for_message_reference_type() -> None:
    assert is_message_reference_attachment(_reference_attachment()) is True


def test_is_message_reference_attachment_false_for_other_content_types() -> None:
    assert is_message_reference_attachment({"id": "x", "contentType": "file"}) is False


def test_is_message_reference_attachment_false_for_non_dict() -> None:
    assert is_message_reference_attachment("not a dict") is False
    assert is_message_reference_attachment(None) is False


def test_valid_message_reference_parsing() -> None:
    reference = parse_message_reference(_reference_attachment())

    assert reference is not None
    assert reference.message_id == "1788182857077"
    assert reference.preview == "Test message sent from SLOPANOC Gateway"
    assert reference.sender_name == "Referenced User"
    assert reference.sender_id == "user-ref-1"


def test_sender_name_parsing_without_sender_id() -> None:
    attachment = _reference_attachment(
        content=json.dumps(
            {
                "messageId": "m1",
                "messagePreview": "hi",
                "messageSender": {"user": {"displayName": "Only Name"}},
            }
        )
    )

    reference = parse_message_reference(attachment)

    assert reference is not None
    assert reference.sender_name == "Only Name"
    assert reference.sender_id is None


def test_sender_id_parsing() -> None:
    reference = parse_message_reference(_reference_attachment())
    assert reference is not None
    assert reference.sender_id == "user-ref-1"


def test_referenced_message_preview_parsing() -> None:
    reference = parse_message_reference(_reference_attachment())
    assert reference is not None
    assert reference.preview == "Test message sent from SLOPANOC Gateway"


def test_missing_preview_is_none_not_invented() -> None:
    attachment = _reference_attachment(
        content=json.dumps(
            {"messageId": "m1", "messageSender": {"user": {"displayName": "X"}}}
        )
    )

    reference = parse_message_reference(attachment)

    assert reference is not None
    assert reference.preview is None


def test_malformed_json_returns_none_not_raises() -> None:
    attachment = {"id": "x", "contentType": "messageReference", "content": "{not valid json"}
    assert parse_message_reference(attachment) is None


def test_missing_message_id_returns_none() -> None:
    attachment = _reference_attachment(content=json.dumps({"messagePreview": "no id here"}))
    assert parse_message_reference(attachment) is None


def test_content_not_a_string_returns_none() -> None:
    attachment = {"id": "x", "contentType": "messageReference", "content": {"already": "a dict"}}
    assert parse_message_reference(attachment) is None


def test_content_json_not_an_object_returns_none() -> None:
    attachment = {
        "id": "x",
        "contentType": "messageReference",
        "content": json.dumps(["a", "list"]),
    }
    assert parse_message_reference(attachment) is None


def test_missing_content_key_returns_none() -> None:
    assert parse_message_reference({"id": "x", "contentType": "messageReference"}) is None
