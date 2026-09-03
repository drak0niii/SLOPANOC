"""Unit tests for backend.agents.incident_manager.evidence --
deterministic evidence validation. "No fake provenance may reach
team_manager" is enforced here in code, not merely promised by prompt
instructions.

`strip_unverified_evidence` is ADK's `after_agent_callback`; it is tested
here against minimal fake stand-ins for `CallbackContext`/`Session`/
`Event` (exposing only the attributes the callback actually reads --
`.session.events`, `.author`, `.content.parts[].text`, `.state.get`),
not a real ADK Runner. Its *wiring* into the live ADK event/AgentTool
pipeline is verified against the installed ADK 1.33.0 source (see
agent.py's docstring) but cannot be exercised end-to-end without a live
model call.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from backend.agents.incident_manager.evidence import (
    strip_unverified_evidence,
    validate_evidence,
)
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY

# --- validate_evidence: the pure, directly-testable rule -------------------


def test_evidence_with_all_known_ids_all_survive() -> None:
    evidence = [
        {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"},
        {"message_id": "m2", "author": "Priya", "sent_at": "2026-08-31T13:05:00Z"},
    ]

    assert validate_evidence(evidence, known_message_ids={"m1", "m2"}) == evidence


def test_invented_evidence_id_is_removed() -> None:
    evidence = [
        {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"},
        {"message_id": "invented-id", "author": "Nobody", "sent_at": "2026-08-31T13:10:00Z"},
    ]

    result = validate_evidence(evidence, known_message_ids={"m1"})

    assert result == [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-31T13:00:00Z"}]


def test_referenced_message_evidence_allowed_when_present_in_reference_data() -> None:
    """A message id that was never independently retrieved -- only present
    inside another message's `message_references` -- is still valid
    evidence, because `known_message_ids` includes referenced ids too
    (see get_messages.py's `_record_known_message_ids`).
    """
    evidence = [
        {"message_id": "1788182857077", "author": "Referenced User", "sent_at": "2026-08-31T13:30:00Z"}
    ]

    result = validate_evidence(evidence, known_message_ids={"m2", "1788182857077"})

    assert result == evidence


def test_malformed_evidence_entries_are_dropped() -> None:
    evidence: list[Any] = [
        {"author": "no message_id field"},
        "not a dict",
        {"message_id": "m1", "author": "Alex", "sent_at": "x"},
    ]

    result = validate_evidence(evidence, known_message_ids={"m1"})

    assert result == [{"message_id": "m1", "author": "Alex", "sent_at": "x"}]


def test_empty_known_ids_rejects_everything() -> None:
    evidence = [{"message_id": "m1", "author": "Alex", "sent_at": "x"}]

    assert validate_evidence(evidence, known_message_ids=set()) == []


def test_empty_evidence_stays_empty() -> None:
    assert validate_evidence([], known_message_ids={"m1"}) == []


# --- strip_unverified_evidence: the ADK after_agent_callback wiring --------


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.parts = parts


class _FakeEvent:
    def __init__(self, author: str, text: Optional[str] = None) -> None:
        self.author = author
        self.content = _FakeContent([_FakePart(text)]) if text is not None else None


class _FakeSession:
    def __init__(self, events: list[_FakeEvent]) -> None:
        self.events = events


class _FakeCallbackContext:
    def __init__(self, events: list[_FakeEvent], state: Optional[dict[str, Any]] = None) -> None:
        self.session = _FakeSession(events)
        self.state = dict(state or {})


def _response_json(evidence: list[Any]) -> str:
    return json.dumps(
        {
            "outcome": "ok",
            "chat_id": "c1",
            "chat_title": "Test Chat",
            "summary": "Something happened.",
            "evidence": evidence,
            "candidate_titles": [],
            "detail": None,
        }
    )


def test_no_override_when_all_evidence_ids_are_known() -> None:
    evidence = [{"message_id": "m1", "author": "Alex", "sent_at": "x"}]
    ctx = _FakeCallbackContext(
        events=[_FakeEvent("incident_manager", _response_json(evidence))],
        state={KNOWN_MESSAGE_IDS_STATE_KEY: ["m1"]},
    )

    assert strip_unverified_evidence(ctx) is None


def test_override_strips_invented_evidence_id() -> None:
    evidence = [
        {"message_id": "m1", "author": "Alex", "sent_at": "x"},
        {"message_id": "invented", "author": "Nobody", "sent_at": "y"},
    ]
    ctx = _FakeCallbackContext(
        events=[_FakeEvent("incident_manager", _response_json(evidence))],
        state={KNOWN_MESSAGE_IDS_STATE_KEY: ["m1"]},
    )

    result = strip_unverified_evidence(ctx)

    assert result is not None
    corrected = json.loads(result.parts[0].text)
    assert corrected["evidence"] == [{"message_id": "m1", "author": "Alex", "sent_at": "x"}]
    # Everything else is preserved unchanged.
    assert corrected["summary"] == "Something happened."
    assert corrected["chat_id"] == "c1"
    assert corrected["outcome"] == "ok"


def test_no_override_when_no_evidence_field_present() -> None:
    ctx = _FakeCallbackContext(
        events=[_FakeEvent("incident_manager", json.dumps({"outcome": "no_result"}))],
        state={},
    )

    assert strip_unverified_evidence(ctx) is None


def test_no_override_when_evidence_is_empty() -> None:
    ctx = _FakeCallbackContext(
        events=[_FakeEvent("incident_manager", _response_json([]))],
        state={},
    )

    assert strip_unverified_evidence(ctx) is None


def test_no_override_when_response_is_not_valid_json() -> None:
    ctx = _FakeCallbackContext(events=[_FakeEvent("incident_manager", "not json")], state={})

    assert strip_unverified_evidence(ctx) is None


def test_ignores_events_from_other_authors() -> None:
    ctx = _FakeCallbackContext(
        events=[
            _FakeEvent("team_manager", "should be ignored"),
            _FakeEvent(
                "incident_manager",
                _response_json([{"message_id": "m1", "author": "Alex", "sent_at": "x"}]),
            ),
        ],
        state={KNOWN_MESSAGE_IDS_STATE_KEY: ["m1"]},
    )

    # m1 is known -- nothing to strip, so still no override, but this
    # proves the *correct* (incident_manager) event was the one read.
    assert strip_unverified_evidence(ctx) is None


def test_no_events_returns_none() -> None:
    ctx = _FakeCallbackContext(events=[], state={})
    assert strip_unverified_evidence(ctx) is None
