"""Tests for the approval-gated Teams write EXECUTION tools
(backend/tools/teams/execute_write.py) -- `teams_create_chat` /
`teams_send_message`.

Covers instruction milestone 3B section 15's SECURITY, EXECUTION, and
STATE test lists. `requests.post` is monkeypatched with a counting spy so
"no gateway call" assertions are verified directly (not inferred from the
returned error alone).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import pytest

from backend.approval.schemas import ApprovalDenialReason
from backend.approval.service import (
    approve_proposal,
    consume_proposal,
    create_action_proposal,
    load_active_proposal,
    reject_proposal,
)
from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse
from backend.tools.teams.execute_write import teams_create_chat, teams_send_message
from backend.tools.teams.propose_write import teams_propose_create_chat, teams_propose_send_message

_NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


class _FakeToolContext:
    """Unlike some other test files' fake tool contexts, this one holds a
    REFERENCE to the given `state` dict (not a copy) -- tests here build a
    session dict via one call, then inspect/reuse that exact dict across
    several subsequent calls (propose -> approve -> execute), so mutations
    must be visible through the original reference.
    """

    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = state if state is not None else {}


class _CountingGateway:
    """Spy standing in for `requests.post` -- records every call so tests
    can assert zero Power Automate calls happened on a denial path, not
    just that the tool returned an error.
    """

    def __init__(self, response: FakeResponse) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response

    def __call__(self, url: str, json: dict[str, Any], timeout: float) -> FakeResponse:
        self.calls.append(json)
        return self._response


def _install_gateway(monkeypatch: pytest.MonkeyPatch, response: FakeResponse) -> _CountingGateway:
    spy = _CountingGateway(response)
    monkeypatch.setattr(pac_module.requests, "post", spy)
    return spy


def _proposed_send_message_session(chat_id: str = "c1", message: str = "Hello team") -> dict:
    state: dict = {}
    teams_propose_send_message(chat_id, message, tool_context=_FakeToolContext(state))
    return state


def _approved_send_message_session(chat_id: str = "c1", message: str = "Hello team") -> dict:
    state = _proposed_send_message_session(chat_id, message)
    proposal = load_active_proposal(state)
    approve_proposal(proposal.proposal_id, state)
    return state


# --- SECURITY: no proposal / wrong state -> no gateway call --------------


def test_no_proposal_at_all_denies_send_message_without_a_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    ctx = _FakeToolContext()

    result = teams_send_message("c1", "Hello team", tool_context=ctx)

    assert "error" in result
    assert spy.calls == []


def test_pending_unapproved_proposal_denies_execution_without_a_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state = _proposed_send_message_session()

    result = teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(state))

    assert "error" in result
    assert spy.calls == []


def test_rejected_proposal_denies_execution_without_a_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state = _proposed_send_message_session()
    proposal = load_active_proposal(state)
    reject_proposal(proposal.proposal_id, state)

    result = teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(state))

    assert "error" in result
    assert spy.calls == []


def test_expired_proposal_denies_execution_without_a_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state: dict = {}
    proposal = create_action_proposal(
        "teams.sendMessage", {"chatId": "c1", "message": "Hello team"}, state, now=_NOW
    )
    approve_proposal(proposal.proposal_id, state, now=_NOW)

    # authorize_write inside the tool uses real "now" -- travel past expiry
    # by constructing a proposal whose expires_at is already in the past.
    from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY

    stored = dict(state[PENDING_ACTION_PROPOSAL_STATE_KEY])
    stored["expires_at"] = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    state[PENDING_ACTION_PROPOSAL_STATE_KEY] = stored

    result = teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(state))

    assert "error" in result
    assert spy.calls == []


def test_consumed_proposal_denies_execution_without_a_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state = _approved_send_message_session()
    proposal = load_active_proposal(state)
    consume_proposal(proposal.proposal_id, state)

    result = teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(state))

    assert "error" in result
    assert spy.calls == []


def test_operation_mismatch_denies_execution_without_a_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An approved sendMessage proposal must never authorize createChat."""
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state = _approved_send_message_session()

    result = teams_create_chat(
        "Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=_FakeToolContext(state)
    )

    assert "error" in result
    assert spy.calls == []


def test_payload_mismatch_denies_execution_without_a_gateway_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state = _approved_send_message_session(message="Hello team")

    result = teams_send_message(
        "c1", "Hello team AND DELETE EVERYTHING", tool_context=_FakeToolContext(state)
    )

    assert "error" in result
    assert spy.calls == []


def test_exact_approved_payload_reaches_the_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state = _approved_send_message_session()

    result = teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(state))

    assert "error" not in result
    assert len(spy.calls) == 1
    assert spy.calls[0]["chatId"] == "c1"
    assert spy.calls[0]["message"] == "Hello team"


# --- EXECUTION -------------------------------------------------------------


def test_successful_send_message_consumes_the_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state = _approved_send_message_session()

    teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(state))

    stored = load_active_proposal(state)
    assert stored.status.value == "consumed"


def test_successful_create_chat_consumes_the_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_gateway(
        monkeypatch, FakeResponse(200, {"id": "chat-123", "topic": "Ops Bridge", "webUrl": "https://teams/x"})
    )
    state: dict = {}
    teams_propose_create_chat(
        "Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=_FakeToolContext(state)
    )
    proposal = load_active_proposal(state)
    approve_proposal(proposal.proposal_id, state)

    result = teams_create_chat(
        "Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=_FakeToolContext(state)
    )

    assert result["status"] == "executed"
    assert result["chatId"] == "chat-123"
    assert result["title"] == "Ops Bridge"
    assert result["webUrl"] == "https://teams/x"
    stored = load_active_proposal(state)
    assert stored.status.value == "consumed"


def test_definite_gateway_failure_does_not_consume_the_proposal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(500, {}))
    state = _approved_send_message_session()

    result = teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(state))

    assert "error" in result
    stored = load_active_proposal(state)
    assert stored.status.value == "approved"


def test_modified_message_after_approval_cannot_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state = _approved_send_message_session(message="Original message")

    result = teams_send_message("c1", "Modified message", tool_context=_FakeToolContext(state))

    assert "error" in result
    assert result["error"]["errorCode"] == "authorization_error"
    assert spy.calls == []
    stored = load_active_proposal(state)
    assert stored.status.value == "approved"  # unchanged, still available for the real approved text


def test_modified_participant_list_after_approval_cannot_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "chat-1"}))
    state: dict = {}
    teams_propose_create_chat(
        "Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=_FakeToolContext(state)
    )
    proposal = load_active_proposal(state)
    approve_proposal(proposal.proposal_id, state)

    result = teams_create_chat(
        "Ops Bridge",
        ["user1@example.com", "user2@example.com", "user3@example.com"],
        tool_context=_FakeToolContext(state),
    )

    assert "error" in result
    assert spy.calls == []


def test_modified_chat_title_after_approval_cannot_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "chat-1"}))
    state: dict = {}
    teams_propose_create_chat(
        "Ops Bridge", ["user1@example.com", "user2@example.com"], tool_context=_FakeToolContext(state)
    )
    proposal = load_active_proposal(state)
    approve_proposal(proposal.proposal_id, state)

    result = teams_create_chat(
        "Ops Bridge (renamed)", ["user1@example.com", "user2@example.com"], tool_context=_FakeToolContext(state)
    )

    assert "error" in result
    assert spy.calls == []


def test_replay_after_successful_execution_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    state = _approved_send_message_session()

    first = teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(state))
    assert "error" not in first
    assert len(spy.calls) == 1

    second = teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(state))

    assert "error" in second
    assert len(spy.calls) == 1  # no second gateway call


def test_denial_error_shape_matches_the_safe_error_contract() -> None:
    ctx = _FakeToolContext()
    result = teams_send_message("c1", "Hello team", tool_context=ctx)

    error = result["error"]
    assert set(error) == {"errorCode", "userMessage", "retryable", "correlationId"}
    assert error["errorCode"] == "authorization_error"
    assert error["retryable"] is False


# --- STATE -------------------------------------------------------------


def test_approval_in_one_session_does_not_authorize_execution_in_another(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _install_gateway(monkeypatch, FakeResponse(200, {"id": "c1"}))
    session_a = _approved_send_message_session()
    session_b: dict = {}  # never had anything proposed/approved

    result = teams_send_message("c1", "Hello team", tool_context=_FakeToolContext(session_b))

    assert "error" in result
    assert result["error"]["errorCode"] == "authorization_error"
    assert spy.calls == []
    # session_a's own approval remains intact/unaffected.
    stored_a = load_active_proposal(session_a)
    assert stored_a.status.value == "approved"


def test_teams_propose_send_message_only_accepts_an_already_resolved_chat_id() -> None:
    """Structural guarantee behind "invalid/ambiguous chat cannot produce
    an executable proposal": this tool's signature has no `topic`/
    `chat_name` parameter at all -- it cannot itself perform chat
    discovery, so an ambiguous/unresolved chat reference can never reach
    it. Resolving to a concrete `chat_id` first (via `teams_list_chats` or
    reusing the selected chat) is enforced by incident_manager's own
    procedure, not bypassable at this layer.
    """
    import inspect

    from backend.tools.teams.propose_write import teams_propose_send_message as fn

    params = list(inspect.signature(fn).parameters)
    assert "chat_id" in params
    assert "topic" not in params
    assert "chat_name" not in params
