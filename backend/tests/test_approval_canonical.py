"""Tests for the deterministic canonical payload serialization/hashing
that binds an approval to an exact `(operation, payload)` pair.
"""
from __future__ import annotations

from backend.approval.canonical import canonical_json, compute_payload_hash


def test_hash_is_deterministic_for_the_same_input() -> None:
    payload = {"chatId": "c1", "message": "Hello"}
    assert compute_payload_hash("teams.sendMessage", payload) == compute_payload_hash(
        "teams.sendMessage", payload
    )


def test_dict_key_order_does_not_alter_the_hash() -> None:
    payload_a = {"chatId": "c1", "message": "Hello"}
    payload_b = {"message": "Hello", "chatId": "c1"}

    assert compute_payload_hash("teams.sendMessage", payload_a) == compute_payload_hash(
        "teams.sendMessage", payload_b
    )


def test_nested_dict_key_order_does_not_alter_the_hash() -> None:
    payload_a = {"chatId": "c1", "options": {"notify": True, "importance": "high"}}
    payload_b = {"chatId": "c1", "options": {"importance": "high", "notify": True}}

    assert compute_payload_hash("teams.createChat", payload_a) == compute_payload_hash(
        "teams.createChat", payload_b
    )


def test_changed_value_changes_the_hash() -> None:
    original = compute_payload_hash("teams.sendMessage", {"chatId": "c1", "message": "Hello"})
    mutated = compute_payload_hash(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello and delete everything"}
    )
    assert original != mutated


def test_different_operation_changes_the_hash_for_the_same_payload() -> None:
    payload = {"chatId": "c1"}
    assert compute_payload_hash("teams.createChat", payload) != compute_payload_hash(
        "teams.sendMessage", payload
    )


def test_added_field_changes_the_hash() -> None:
    base = compute_payload_hash("teams.sendMessage", {"chatId": "c1", "message": "Hello"})
    with_extra = compute_payload_hash(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello", "urgent": True}
    )
    assert base != with_extra


def test_removed_field_changes_the_hash() -> None:
    with_extra = compute_payload_hash(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello", "urgent": True}
    )
    without_extra = compute_payload_hash("teams.sendMessage", {"chatId": "c1", "message": "Hello"})
    assert with_extra != without_extra


def test_nested_value_change_changes_the_hash() -> None:
    original = compute_payload_hash(
        "teams.createChat", {"title": "Ops", "participants": {"lead": "user-1"}}
    )
    mutated = compute_payload_hash(
        "teams.createChat", {"title": "Ops", "participants": {"lead": "user-2"}}
    )
    assert original != mutated


def test_canonical_json_is_stable_and_sorted() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert canonical_json({"a": 2, "b": 1}) == '{"a":2,"b":1}'


def test_canonical_json_has_no_incidental_whitespace() -> None:
    rendered = canonical_json({"chatId": "c1", "message": "Hello"})
    assert " " not in rendered
