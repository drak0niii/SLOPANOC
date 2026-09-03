"""Tests for deterministic Teams write-payload normalization/validation
(backend/tools/teams/write_validation.py) -- shared by the propose and
execute tools.
"""
from __future__ import annotations

import pytest

from backend.gateway.safe_error import SafeErrorException
from backend.tools.teams.write_validation import (
    normalize_create_chat_payload,
    normalize_send_message_payload,
)


# --- createChat --------------------------------------------------------


def test_valid_create_chat_payload_is_normalized() -> None:
    payload = normalize_create_chat_payload(
        "  Ops Bridge  ", ["  user1@example.com ", "user2@example.com"]
    )

    assert payload == {"title": "Ops Bridge", "members": ["user1@example.com", "user2@example.com"]}


def test_create_chat_rejects_empty_title() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload("", ["user1@example.com", "user2@example.com"])


def test_create_chat_rejects_whitespace_only_title() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload("   ", ["user1@example.com", "user2@example.com"])


def test_create_chat_rejects_fewer_than_two_emails() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload("Ops Bridge", ["user1@example.com"])


def test_create_chat_rejects_zero_emails() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload("Ops Bridge", [])


def test_create_chat_rejects_invalid_email_syntax() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload("Ops Bridge", ["user1@example.com", "not-an-email"])


def test_create_chat_rejects_email_missing_domain_dot() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload("Ops Bridge", ["user1@example.com", "user2@localhost"])


def test_create_chat_rejects_email_with_embedded_space() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload("Ops Bridge", ["user1@example.com", "user 2@example.com"])


def test_create_chat_rejects_duplicate_emails() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload(
            "Ops Bridge", ["user1@example.com", "user1@example.com", "user2@example.com"]
        )


def test_create_chat_rejects_case_insensitive_duplicate_emails() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload(
            "Ops Bridge", ["User1@Example.com", "user1@example.com", "user2@example.com"]
        )


def test_create_chat_does_not_add_a_connection_owner() -> None:
    payload = normalize_create_chat_payload("Ops Bridge", ["user1@example.com", "user2@example.com"])
    assert len(payload["members"]) == 2


def test_create_chat_does_not_guess_an_incomplete_address() -> None:
    with pytest.raises(SafeErrorException):
        normalize_create_chat_payload("Ops Bridge", ["Jane Doe", "user2@example.com"])


# --- sendMessage --------------------------------------------------------


def test_valid_send_message_payload_is_normalized() -> None:
    payload = normalize_send_message_payload("  c1  ", "Hello team")
    assert payload == {"chatId": "c1", "message": "Hello team"}


def test_send_message_rejects_empty_message() -> None:
    with pytest.raises(SafeErrorException):
        normalize_send_message_payload("c1", "")


def test_send_message_rejects_whitespace_only_message() -> None:
    with pytest.raises(SafeErrorException):
        normalize_send_message_payload("c1", "   ")


def test_send_message_rejects_missing_chat_id() -> None:
    with pytest.raises(SafeErrorException):
        normalize_send_message_payload("", "Hello team")


def test_send_message_rejects_whitespace_only_chat_id() -> None:
    with pytest.raises(SafeErrorException):
        normalize_send_message_payload("   ", "Hello team")


def test_send_message_preserves_message_text_exactly_aside_from_validation() -> None:
    """The exact message displayed for approval must be the exact message
    sent -- normalization must never rewrite the message body itself
    (only title/emails are trimmed).
    """
    payload = normalize_send_message_payload("c1", "  Hello   team  ")
    assert payload["message"] == "  Hello   team  "
