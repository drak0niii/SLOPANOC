"""Corrective-pass regression tests: `KNOWN_MESSAGE_IDS_STATE_KEY` must
survive an ADK rewind that cleared it.

REAL-STACK DEFECT: after an edit/rewind discarded a branch that had
written `known_message_ids`, the next Teams read crashed with
`TypeError: 'NoneType' object is not iterable` inside
`backend/tools/teams/get_messages.py`'s `_record_known_message_ids`.

ROOT CAUSE, verified against installed `google-adk==1.33.0` source
(`Runner._compute_state_delta_for_rewind`,
`DatabaseSessionService`/`InMemorySessionService.append_event`): ADK's own
rewind mechanism represents "this key must be reverted to before it
existed" as an explicit `None` written into the rewind event's
`state_delta`, and both session-service implementations persist that
`None` as a literal dict value rather than deleting the key. So after a
rewind, `KNOWN_MESSAGE_IDS_STATE_KEY` is PRESENT with value `None`, not
absent. Every call site that did `set(state.get(KNOWN_MESSAGE_IDS_STATE_
KEY, []))` crashed, because `.get`'s own default only fires when the key
is missing entirely -- not when it is present with value `None`.

FIX: `backend.tools.teams.get_messages.read_known_message_ids` is the one
normalization function every reader now goes through (`get_messages.py`
itself, `evidence.py`'s two callbacks, `direct_read_fast_path.py`'s
fast-path evidence seeding). This file proves: (1) the pure function
handles absent/None/populated state correctly, (2) `teams_get_messages`
no longer crashes and still calls the real Power Automate gateway
normally, (3) discarded-branch ids never reappear -- only newly retrieved
ids are known after a rewind, (4) the evidence-validation callbacks that
depend on this key survive the same rewind-cleared state, (5) the exact
real-stack code path (`_seeded_state` -> `_StateCapture` ->
`teams_get_messages`, the deterministic-retrieval branch
`execute_read_continuation` uses for both the direct fast path and a
resumed SelectionCard continuation) no longer crashes either.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from backend.agents.incident_manager.evidence import (
    enforce_incident_manager_response_integrity,
    strip_unverified_evidence,
    validate_evidence,
)
from backend.agents.team_manager import direct_read_fast_path as fast_path_module
from backend.agents.team_manager.direct_read_fast_path import (
    _FAST_PATH_PENDING_STATE_KEY,
    _fast_path_before_model_callback,
)
from backend.agents.team_manager.read_continuation_execution import _seeded_state, _StateCapture
from backend.gateway import power_automate_client as pac_module
from backend.selection.schemas import ReadOperation, ResolvedReadContinuation
from backend.tests._fakes import FakeResponse, message
from backend.tools.teams.get_messages import (
    KNOWN_MESSAGE_IDS_STATE_KEY,
    read_known_message_ids,
    teams_get_messages,
)

# --- A: read_known_message_ids -- the pure normalization function ---------


def test_absent_key_normalizes_to_empty_set() -> None:
    assert read_known_message_ids({}) == set()


def test_explicit_none_normalizes_to_empty_set() -> None:
    """The exact rewind-cleared representation ADK's own rewind state_delta
    writes -- proven against installed ADK source in this module's own
    docstring above.
    """
    assert read_known_message_ids({KNOWN_MESSAGE_IDS_STATE_KEY: None}) == set()


def test_existing_list_is_preserved() -> None:
    state = {KNOWN_MESSAGE_IDS_STATE_KEY: ["m1", "m2"]}
    assert read_known_message_ids(state) == {"m1", "m2"}


def test_duplicate_ids_are_deduplicated() -> None:
    state = {KNOWN_MESSAGE_IDS_STATE_KEY: ["m1", "m1", "m2"]}
    assert read_known_message_ids(state) == {"m1", "m2"}


def test_empty_list_normalizes_to_empty_set() -> None:
    assert read_known_message_ids({KNOWN_MESSAGE_IDS_STATE_KEY: []}) == set()


# --- B: teams_get_messages / _record_known_message_ids --------------------


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = dict(state or {})


def test_teams_get_messages_survives_rewind_cleared_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact real-stack crash reproduced at the tool-call boundary:
    KNOWN_MESSAGE_IDS_STATE_KEY present with value None (rewind-cleared)
    immediately before a new Teams retrieval.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [message("m3", "Alex", "back after rewind", "2026-09-01T09:00:00Z")]),
    )
    ctx = _FakeToolContext({KNOWN_MESSAGE_IDS_STATE_KEY: None})

    result = teams_get_messages(chat_id="c1", tool_context=ctx)

    assert "error" not in result
    assert ctx.state[KNOWN_MESSAGE_IDS_STATE_KEY] == ["m3"]


def test_teams_get_messages_absent_key_unaffected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-regression: the pre-existing "key never written yet" case must
    behave identically to before this fix.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [message("m1", "Alex", "hi", "2026-09-01T09:00:00Z")]),
    )
    ctx = _FakeToolContext()

    result = teams_get_messages(chat_id="c1", tool_context=ctx)

    assert "error" not in result
    assert ctx.state[KNOWN_MESSAGE_IDS_STATE_KEY] == ["m1"]


def test_teams_get_messages_accumulates_onto_existing_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-regression: a real, previously-accumulated list must still be
    merged with newly retrieved ids, unaffected by the fix.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [message("m2", "Priya", "second message", "2026-09-01T09:05:00Z")]),
    )
    ctx = _FakeToolContext({KNOWN_MESSAGE_IDS_STATE_KEY: ["m1"]})

    teams_get_messages(chat_id="c1", tool_context=ctx)

    assert set(ctx.state[KNOWN_MESSAGE_IDS_STATE_KEY]) == {"m1", "m2"}


def test_discarded_branch_ids_do_not_reappear_after_rewind(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn A retrieves ["m1", "m2"]; a rewind discards that branch
    (state_delta clears the key to None); the next retrieval must start
    from an empty set -- m1/m2 must never silently reappear.
    """
    ctx = _FakeToolContext({KNOWN_MESSAGE_IDS_STATE_KEY: ["m1", "m2"]})
    # Simulate exactly what ADK's own rewind state_delta does to this key.
    ctx.state[KNOWN_MESSAGE_IDS_STATE_KEY] = None

    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                message("m3", "Alex", "new branch message 1", "2026-09-01T09:10:00Z"),
                message("m4", "Priya", "new branch message 2", "2026-09-01T09:11:00Z"),
            ],
        ),
    )

    teams_get_messages(chat_id="c1", tool_context=ctx)

    known = set(ctx.state[KNOWN_MESSAGE_IDS_STATE_KEY])
    assert known == {"m3", "m4"}
    assert "m1" not in known
    assert "m2" not in known


def test_gateway_still_called_normally_after_rewind_cleared_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fix is a pure state-read normalization -- it must never affect
    whether/how the Power Automate gateway itself is called.
    """
    called: dict[str, Any] = {}

    def fake_post(url, json, timeout):
        called["chat_id"] = json.get("chatId")
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext({KNOWN_MESSAGE_IDS_STATE_KEY: None})

    teams_get_messages(chat_id="rewound-chat", tool_context=ctx)

    assert called["chat_id"] == "rewound-chat"


def _message_with_reference(msg_id: str = "m5", reference_message_id: str = "ref-1") -> dict[str, Any]:
    reference_payload = json.dumps({"messageId": reference_message_id, "messagePreview": "quoted text"})
    return {
        "id": msg_id,
        "createdDateTime": "2026-09-01T09:20:00Z",
        "senderName": "Alex",
        "contentType": "html",
        "content": f'<attachment id="{reference_message_id}"></attachment><p>Is this it?</p>',
        "attachments": [
            {"id": reference_message_id, "contentType": "messageReference", "content": reference_payload}
        ],
    }


def test_message_reference_ids_still_recorded_after_rewind_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    """Message-reference ids (see get_messages.py's own EVIDENCE-VALIDATION
    STATE docstring) must still be captured after a rewind clear, not only
    directly-retrieved message ids.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_message_with_reference()]),
    )
    ctx = _FakeToolContext({KNOWN_MESSAGE_IDS_STATE_KEY: None})

    teams_get_messages(chat_id="c1", tool_context=ctx)

    known = set(ctx.state[KNOWN_MESSAGE_IDS_STATE_KEY])
    assert "m5" in known
    assert "ref-1" in known


# --- C: evidence.py -- provenance validation survives the same state ------


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


def test_strip_unverified_evidence_survives_rewind_cleared_state() -> None:
    """Provenance still rejects ids not (yet) retrieved this turn -- a
    rewind-cleared state (None) must reject everything, exactly like an
    empty/absent state, never crash and never trust anything.
    """
    evidence = [{"message_id": "m1", "author": "Alex", "sent_at": "x"}]
    ctx = _FakeCallbackContext(
        events=[_FakeEvent("incident_manager", _response_json(evidence))],
        state={KNOWN_MESSAGE_IDS_STATE_KEY: None},
    )

    result = strip_unverified_evidence(ctx)

    assert result is not None
    corrected = json.loads(result.parts[0].text)
    assert corrected["evidence"] == []  # nothing survives -- nothing was known this turn


def test_strip_unverified_evidence_accepts_ids_retrieved_after_rewind() -> None:
    """Provenance still accepts ids actually retrieved: once a real
    post-rewind teams_get_messages call recorded "m9", evidence citing it
    survives.
    """
    evidence = [{"message_id": "m9", "author": "Alex", "sent_at": "x"}]
    ctx = _FakeCallbackContext(
        events=[_FakeEvent("incident_manager", _response_json(evidence))],
        state={KNOWN_MESSAGE_IDS_STATE_KEY: ["m9"]},
    )

    assert strip_unverified_evidence(ctx) is None  # nothing to strip -- m9 is known


@pytest.mark.asyncio
async def test_enforce_incident_manager_response_integrity_survives_rewind_cleared_state() -> None:
    """The combined after_agent_callback (Teams + governed-knowledge) must
    survive the same rewind-cleared state at its own, separate
    `read_known_message_ids` call site.
    """
    evidence = [{"message_id": "m1", "author": "Alex", "sent_at": "x"}]
    ctx = _FakeCallbackContext(
        events=[_FakeEvent("incident_manager", _response_json(evidence))],
        state={KNOWN_MESSAGE_IDS_STATE_KEY: None},
    )

    result = await enforce_incident_manager_response_integrity(ctx)

    assert result is not None
    corrected = json.loads(result.parts[0].text)
    assert corrected["evidence"] == []


def test_validate_evidence_still_rejects_stale_ids_not_retrieved_this_turn() -> None:
    """Direct unit proof that the normalization never loosens provenance:
    an id from a discarded branch is never accepted merely because the
    state key was recently None.
    """
    known_ids = read_known_message_ids({KNOWN_MESSAGE_IDS_STATE_KEY: None})
    # Simulate the real post-rewind retrieval that follows.
    known_ids |= {"m3", "m4"}

    evidence = [
        {"message_id": "m3", "author": "Alex", "sent_at": "x"},
        {"message_id": "m1", "author": "Nobody", "sent_at": "y"},  # stale, discarded-branch id
    ]

    result = validate_evidence(evidence, known_ids)

    assert result == [{"message_id": "m3", "author": "Alex", "sent_at": "x"}]


# --- D: direct unique-match fast path works after rewind -------------------


class _FakeInvocationContext:
    def __init__(self, session_service: Any, user_id: str, session: Any, invocation_id: str) -> None:
        self.session_service = session_service
        self.user_id = user_id
        self.session = session
        self.invocation_id = invocation_id


class _FakeFastPathSession:
    def __init__(self, session_id: str, state: dict) -> None:
        self.id = session_id
        self.state = state


class _FakeFastPathCallbackContext:
    def __init__(self, state: dict, invocation_context: Any) -> None:
        self.state = state
        self._invocation_context = invocation_context


@pytest.mark.asyncio
async def test_fast_path_seeds_known_ids_after_rewind_cleared_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """The direct unique-match fast path's own evidence-seeding step
    (direct_read_fast_path.py, `_fast_path_before_model_callback`) must not
    crash when this discovery session's own copy of the state key was left
    at None by an earlier rewind.
    """

    async def fake_execute_read_continuation(**kwargs: Any) -> dict[str, Any]:
        return {
            "outcome": "ok",
            "chat_id": "chat-1",
            "chat_title": "Network Operations Daily",
            "summary": "Done.",
            "evidence": [{"message_id": "m7", "author": "Alex", "sent_at": "x"}],
        }

    monkeypatch.setattr(fast_path_module, "execute_read_continuation", fake_execute_read_continuation)
    monkeypatch.setattr(fast_path_module, "current_run_id", lambda: "run-rewind-test")

    state = {
        _FAST_PATH_PENDING_STATE_KEY: {
            "chat_id": "chat-1",
            "chat_title": "Network Operations Daily",
            "question": None,
            "operation": "summarize",
            "requested_time_range": None,
        },
        KNOWN_MESSAGE_IDS_STATE_KEY: None,  # rewind-cleared before this turn
    }
    invocation_context = _FakeInvocationContext(
        session_service=object(),
        user_id="api-user",
        session=_FakeFastPathSession("inner-session", dict(state)),
        invocation_id="inv-1",
    )
    ctx = _FakeFastPathCallbackContext(state, invocation_context)

    result = await _fast_path_before_model_callback(ctx, llm_request=None)

    assert result is not None
    assert set(ctx.state[KNOWN_MESSAGE_IDS_STATE_KEY]) == {"m7"}


# --- E: read-continuation deterministic-retrieval path works after rewind -
#
# This reproduces the EXACT real-stack code path from the reported crash:
# `_seeded_state` (copies the parent session's own state, including a
# rewind-cleared `known_message_ids: None`) -> `_StateCapture` (the
# duck-typed ToolContext stand-in `_execute_via_deterministic_retrieval`
# constructs) -> the real, unmodified `teams_get_messages`. This same
# function backs BOTH the direct fast path (section D above) and a resumed
# SelectionCard continuation -- proving it here covers selection-
# continuation too, since neither path reimplements retrieval separately.


def test_seeded_state_and_teams_get_messages_survive_parent_rewind_cleared_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_state: dict[str, Any] = {
        KNOWN_MESSAGE_IDS_STATE_KEY: None,  # exactly what a rewind leaves in the PARENT session
        "some_unrelated_key": "preserved",
    }
    continuation = ResolvedReadContinuation(
        operation=ReadOperation.SUMMARIZE,
        selected_chat_id="chat-real-1",
        selected_chat_topic="Network Operations Daily",
    )

    seeded = _seeded_state(parent_state, continuation)
    assert seeded[KNOWN_MESSAGE_IDS_STATE_KEY] is None  # verbatim copy, unmodified by _seeded_state itself
    assert seeded["some_unrelated_key"] == "preserved"

    state_capture = _StateCapture(dict(seeded))

    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [message("m8", "Alex", "post-rewind retrieval", "2026-09-01T09:30:00Z")]),
    )

    retrieval = teams_get_messages("chat-real-1", tool_context=state_capture)

    assert "error" not in retrieval
    assert state_capture.state[KNOWN_MESSAGE_IDS_STATE_KEY] == ["m8"]
