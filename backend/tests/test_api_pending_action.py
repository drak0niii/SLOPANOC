"""Tests for the deterministic pending-action mapper
(backend/api/pending_action.py) -- ADK session state -> safe frontend
DTO. Pure-function tests against plain dicts; no ADK objects needed here
(see test_api_chat_service.py for the end-to-end wiring through a real
session).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.api.pending_action import map_pending_action
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY, create_action_proposal

# `map_pending_action`/`effective_status` always compare against the REAL
# wall clock (no `now` override) -- so proposals used in "still pending"
# tests must be created relative to the real current time, not a fixed
# historical timestamp (only the dedicated expiry test below needs a
# fixed, deliberately-past timestamp).
_NOW = datetime.now(timezone.utc)


def test_no_pending_proposal_maps_to_none() -> None:
    assert map_pending_action({}) is None


def test_malformed_state_maps_to_none() -> None:
    assert map_pending_action({PENDING_ACTION_PROPOSAL_STATE_KEY: "not a dict"}) is None


def test_create_chat_proposal_maps_expected_fields() -> None:
    state: dict = {}
    create_action_proposal(
        "teams.createChat",
        {"title": "Ops Bridge", "members": ["a@example.com", "b@example.com"]},
        state,
        summary="Create Teams chat titled 'Ops Bridge'...",
        now=_NOW,
    )

    dto = map_pending_action(state)

    assert dto is not None
    assert dto.operation == "teams.createChat"
    assert dto.status == "pending"
    assert dto.title == "Ops Bridge"
    assert dto.members == ["a@example.com", "b@example.com"]
    assert dto.chat_id is None
    assert dto.message is None
    assert dto.summary == "Create Teams chat titled 'Ops Bridge'..."


def test_send_message_proposal_maps_expected_fields() -> None:
    state: dict = {}
    create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello team"}, state, now=_NOW
    )

    dto = map_pending_action(state)

    assert dto is not None
    assert dto.operation == "teams.sendMessage"
    assert dto.chat_id == "c1"
    assert dto.message == "Hello team"
    assert dto.title is None
    assert dto.members == []


def test_proposal_id_is_included() -> None:
    state: dict = {}
    proposal = create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)

    dto = map_pending_action(state)

    assert dto.proposal_id == proposal.proposal_id


def test_payload_hash_is_never_included() -> None:
    state: dict = {}
    create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)

    dto = map_pending_action(state)

    assert "payload_hash" not in dto.model_dump()
    assert not hasattr(dto, "payload_hash")


def test_no_secret_or_internal_gateway_fields_are_included() -> None:
    state: dict = {}
    create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)

    dto_fields = set(dto_field for dto_field in map_pending_action(state).model_dump())
    for forbidden in ("payload_hash", "gateway_url", "power_automate_url", "credentials", "secret"):
        assert forbidden not in dto_fields


def test_expiry_fields_are_populated() -> None:
    state: dict = {}
    create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=_NOW)

    dto = map_pending_action(state)

    assert dto.expires_in_seconds >= 0
    assert dto.expires_in_minutes >= 0
    assert dto.expires_at  # a non-empty ISO string


def test_expired_proposal_reports_expired_status_dynamically() -> None:
    """The status must reflect real elapsed time, not just whatever was
    last persisted (mirrors `effective_status`'s guarantee at the policy
    gate) -- a proposal created far enough in the past shows as expired
    even though its stored `status` field still says "pending".
    """
    state: dict = {}
    long_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    create_action_proposal("teams.sendMessage", {"chatId": "c1", "message": "Hi"}, state, now=long_ago)

    dto = map_pending_action(state)

    assert dto.status == "expired"
    assert dto.expires_in_seconds == 0
    assert dto.expires_in_minutes == 0
    assert state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "pending"  # untouched by the mapper
