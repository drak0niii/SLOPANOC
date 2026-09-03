"""Tests for Phase 4G's final hardening pass -- conversational branching
for editing a historical user message (chat_service.py's
`_active_events`/`_resolve_invocation_id_for_active_user_turn`/
`ChatService.rewind_before_user_turn`, and the `POST
/api/sessions/{id}/rewind` route in app.py).

BACKGROUND (see this pass's implementation notes / final report): tracing
`google.adk.runners.Runner.run_async` confirmed a backend-backed edit that
merely reuses the same session and appends the edited text would leak
stale future history into the model's context (the exact defect this
pass fixes). Tracing further found ADK already ships a first-class,
public mechanism for exactly this: `Runner.rewind_async(...)` appends one
new "rewind" event whose `actions.rewind_before_invocation_id` marks
everything from that invocation onward as excluded from future model
context (`google.adk.flows.llm_flows.contents._get_contents`'s own
rewind-filtering, verified against the installed 1.33.0 source) --
without deleting or mutating anything already stored, and with ADK's own
`_compute_state_delta_for_rewind` correctly reversing `session.state` back
to what it was immediately before the rewound turn. No new session is
ever created; `Chat.backendSessionId` never changes.

Section 1 unit-tests the pure resolver helpers directly (synthetic
duck-typed events, no ADK Runner needed). Section 2 tests
`ChatService.rewind_before_user_turn`'s own orchestration (locking,
ownership, failure-before-mutation) via `FakeRunner`'s recording
`rewind_async` stub. Section 3 proves no historical side effect is ever
replayed. Section 4 proves Case Context is untouched. Section 5 is one
deeper integration test using the REAL `google.adk.runners.Runner`
(agent=None is rejected by ADK's own validation, so a minimal real
`Agent` is used -- never invoked, since `rewind_async` never touches
`self.agent`) to prove the resolved invocation id genuinely integrates
with ADK's own mechanism, not just with a fake. Section 6 covers the HTTP
route.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

import backend.api.chat_service as chat_service_module
from backend.api.app import app
from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY
from backend.api.chat_service import (
    ChatService,
    _active_events,
    _resolve_invocation_id_for_active_user_turn,
)
from backend.api.session_service import ApiSessionService, get_session_service
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY, load_active_proposal
from backend.gateway.safe_error import SafeErrorException
from backend.tests._api_fakes import FakeRunner, append_user_turn, simulate_proposal


# --- 1. Pure resolver logic (synthetic events, no ADK Runner) --------------


class _Actions:
    def __init__(self, rewind_before_invocation_id=None, state_delta=None):
        self.rewind_before_invocation_id = rewind_before_invocation_id
        self.state_delta = state_delta or {}


class _Content:
    def __init__(self, role):
        self.role = role


class _Ev:
    def __init__(self, invocation_id, author, role=None, rewind_before_invocation_id=None):
        self.invocation_id = invocation_id
        self.author = author
        self.content = _Content(role) if role else None
        self.actions = _Actions(rewind_before_invocation_id=rewind_before_invocation_id)


def _user_turn(inv: str) -> list[_Ev]:
    return [_Ev(inv, "user", role="user"), _Ev(inv, "team_manager", role="model")]


def _rewind_marker(target_inv: str) -> _Ev:
    return _Ev("rewind-marker", "user", role=None, rewind_before_invocation_id=target_inv)


def test_active_events_returns_everything_when_no_rewind_marker_present() -> None:
    events = _user_turn("inv-1") + _user_turn("inv-2")
    assert _active_events(events) == events


def test_active_events_excludes_a_rewound_turn_and_everything_after() -> None:
    events = _user_turn("inv-1") + _user_turn("inv-2") + _user_turn("inv-3") + [_rewind_marker("inv-2")]
    active = _active_events(events)
    assert [e.invocation_id for e in active] == ["inv-1", "inv-1"]


def test_active_events_excludes_the_rewind_marker_event_itself() -> None:
    events = _user_turn("inv-1") + [_rewind_marker("inv-1")]
    active = _active_events(events)
    assert active == []


def test_active_events_handles_multiple_successive_rewinds() -> None:
    # Edit turn 2 (discarding inv-2/inv-3), producing inv-2b; then edit
    # inv-2b itself (discarding it too), producing inv-2c.
    events = (
        _user_turn("inv-1")
        + _user_turn("inv-2")
        + _user_turn("inv-3")
        + [_rewind_marker("inv-2")]
        + _user_turn("inv-2b")
        + [_rewind_marker("inv-2b")]
        + _user_turn("inv-2c")
    )
    active = _active_events(events)
    assert [e.invocation_id for e in active] == ["inv-1", "inv-1", "inv-2c", "inv-2c"]


def test_resolve_invocation_id_finds_the_nth_active_user_turn() -> None:
    events = _user_turn("inv-1") + _user_turn("inv-2") + _user_turn("inv-3")
    assert _resolve_invocation_id_for_active_user_turn(events, 0) == "inv-1"
    assert _resolve_invocation_id_for_active_user_turn(events, 1) == "inv-2"
    assert _resolve_invocation_id_for_active_user_turn(events, 2) == "inv-3"


def test_resolve_invocation_id_returns_none_when_index_out_of_range() -> None:
    events = _user_turn("inv-1")
    assert _resolve_invocation_id_for_active_user_turn(events, 1) is None
    assert _resolve_invocation_id_for_active_user_turn([], 0) is None


def test_resolve_invocation_id_only_counts_currently_active_turns() -> None:
    """After turn 2 was already rewound away by an earlier edit, "turn
    index 1" must resolve to whatever NOW occupies that position (inv-2b),
    never the discarded original inv-2 -- otherwise a second edit of an
    already-edited message would target the wrong (stale) invocation.
    """
    events = (
        _user_turn("inv-1")
        + _user_turn("inv-2")
        + [_rewind_marker("inv-2")]
        + _user_turn("inv-2b")
    )
    assert _resolve_invocation_id_for_active_user_turn(events, 1) == "inv-2b"


def test_resolve_invocation_id_ignores_non_user_content() -> None:
    events = [_Ev("inv-1", "user", role="user"), _Ev("inv-1", "team_manager", role="model")]
    # Only ONE user-authored, user-role event exists -- the model reply
    # must never be miscounted as a second user turn.
    assert _resolve_invocation_id_for_active_user_turn(events, 0) == "inv-1"
    assert _resolve_invocation_id_for_active_user_turn(events, 1) is None


# --- 2. ChatService.rewind_before_user_turn orchestration -------------------


@pytest.mark.asyncio
async def test_rewind_resolves_and_calls_through_with_the_correct_invocation_id() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    session = await append_user_turn(service, session, "inv-1", "first message")
    session = await append_user_turn(service, session, "inv-2", "second message")
    runner = FakeRunner(service)
    chat_service = ChatService(service, runner=runner)

    await chat_service.rewind_before_user_turn(session_id, 1)

    assert runner.rewind_calls == [
        {"user_id": "api-user", "session_id": session_id, "rewind_before_invocation_id": "inv-2"}
    ]


@pytest.mark.asyncio
async def test_rewind_unknown_session_raises_not_found_and_never_calls_the_runner() -> None:
    service = ApiSessionService()
    runner = FakeRunner(service)
    chat_service = ChatService(service, runner=runner)

    with pytest.raises(SafeErrorException) as exc_info:
        await chat_service.rewind_before_user_turn("does-not-exist", 0)

    assert exc_info.value.safe_error.error_code == "not_found"
    assert runner.rewind_calls == []


@pytest.mark.asyncio
async def test_rewind_out_of_range_index_raises_validation_error_and_never_calls_the_runner() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await append_user_turn(service, session, "inv-1", "only message")
    runner = FakeRunner(service)
    chat_service = ChatService(service, runner=runner)

    with pytest.raises(SafeErrorException) as exc_info:
        await chat_service.rewind_before_user_turn(session_id, 5)

    assert exc_info.value.safe_error.error_code == "validation_error"
    assert runner.rewind_calls == []


@pytest.mark.asyncio
async def test_rewind_a_foreign_session_is_indistinguishable_from_unknown() -> None:
    service = ApiSessionService()
    session_id = await service.create_session(user_id="alice")
    runner = FakeRunner(service)
    chat_service = ChatService(service, runner=runner)

    with pytest.raises(SafeErrorException) as exc_info:
        await chat_service.rewind_before_user_turn(session_id, 0, user_id="mallory")

    assert exc_info.value.safe_error.error_code == "not_found"
    assert runner.rewind_calls == []


# --- 3. Historical side effects are never replayed --------------------------


@pytest.mark.asyncio
async def test_rewinding_before_a_turn_that_created_a_proposal_reverts_it_and_never_recreates_one() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    session = await append_user_turn(service, session, "inv-1", "hello")

    # Simulate a tool call, during turn 2, that created a pending proposal.
    session = await append_user_turn(service, session, "inv-2", "send a message")
    await simulate_proposal(service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})
    session = await service.get_session(session_id)
    assert load_active_proposal(session.state) is not None

    # Use the REAL Runner.rewind_async (see section 5's own rationale) so
    # this test proves the ACTUAL state-delta reversal, not a recording
    # stub's approximation of it.
    real_runner = _make_real_runner(service)
    chat_service = ChatService(service, runner=real_runner)
    await chat_service.rewind_before_user_turn(session_id, 1)  # before turn 2 (inv-2)

    refreshed = await service.get_session(session_id)
    assert PENDING_ACTION_PROPOSAL_STATE_KEY not in refreshed.state or load_active_proposal(refreshed.state) is None


def _mentions_as_code(source: str, name: str) -> bool:
    """True only for an actual import/call site -- not a docstring/prose
    mention explaining the architecture. Mirrors the identical helper in
    test_api_security_contract.py.
    """
    return f"import {name}" in source or f"{name}(" in source


def test_rewind_source_never_mentions_execution_or_approval_modules() -> None:
    """Structural guarantee: rewinding is pure conversational-history
    bookkeeping -- it must never import or call anything from the
    execution/approval layer (which would imply re-running a write).
    """
    source = inspect.getsource(chat_service_module.ChatService.rewind_before_user_turn)
    for forbidden in ("execution_service", "approval_service", "approve_proposal", "consume_proposal", "authorize_write"):
        assert not _mentions_as_code(source, forbidden)


@pytest.mark.asyncio
async def test_rewind_alone_never_invokes_run_async() -> None:
    """A runner double whose `run_async` would fail the test if ever
    called (it's never given a return, so iterating it raises
    immediately) -- proves `rewind_before_user_turn` never starts a live
    agent turn on its own.
    """

    class _RunAsyncForbidden:
        def __init__(self, session_service):
            self._session_service = session_service
            self.rewind_calls: list[dict] = []

        def run_async(self, **kwargs):  # pragma: no cover -- must never be called
            raise AssertionError("run_async must never be called by rewind_before_user_turn")

        async def rewind_async(self, *, user_id, session_id, rewind_before_invocation_id, run_config=None):
            self.rewind_calls.append(rewind_before_invocation_id)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    await append_user_turn(service, session, "inv-1", "hello")
    runner = _RunAsyncForbidden(service)
    chat_service = ChatService(service, runner=runner)

    await chat_service.rewind_before_user_turn(session_id, 0)
    assert runner.rewind_calls == ["inv-1"]


# --- 4. Case Context is never duplicated/replayed ---------------------------


def test_rewind_source_never_references_the_case_service() -> None:
    source = inspect.getsource(chat_service_module.ChatService.rewind_before_user_turn)
    assert not _mentions_as_code(source, "case_service")
    assert not _mentions_as_code(source, "CaseService")
    assert not _mentions_as_code(source, "get_active_case_for_session")


@pytest.mark.asyncio
async def test_rewind_leaves_the_authoritative_case_session_link_untouched() -> None:
    """The Case<->session link (`CaseSessionLinkRecord`) is a separate
    store keyed by session_id, re-derived fresh on every read -- since
    rewind never creates a new session, this link cannot possibly need
    migrating, and must be completely unaffected either way.
    """
    from backend.api.case_service import get_active_case_for_session, link_session
    from backend.cases.service import CaseService

    service = ApiSessionService()
    case_service = CaseService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    session = await append_user_turn(service, session, "inv-1", "hello")
    await append_user_turn(service, session, "inv-2", "second turn")

    case = await case_service.create_case("api-user", "Investigation", "problem")
    await link_session(service, case_service, "api-user", case.case_id, session_id)

    before = await get_active_case_for_session(case_service, "api-user", session_id)
    assert before is not None and before.case_id == case.case_id

    real_runner = _make_real_runner(service)
    chat_service = ChatService(service, runner=real_runner)
    await chat_service.rewind_before_user_turn(session_id, 1)

    after = await get_active_case_for_session(case_service, "api-user", session_id)
    assert after is not None and after.case_id == case.case_id


# --- 5. Integration proof against the REAL ADK Runner.rewind_async ---------


def _make_real_runner(session_service: ApiSessionService):
    """A REAL `google.adk.runners.Runner`, never a fake -- `agent=None` is
    rejected by ADK's own constructor validation, so a minimal real
    `Agent` is supplied; it is never actually invoked here, since
    `rewind_async` (verified against the installed 1.33.0 source) never
    touches `self.agent` at all -- only `run_async` would.
    """
    from google.adk.agents import Agent
    from google.adk.runners import Runner

    agent = Agent(name="test_agent", model="gemini-2.0-flash")
    return Runner(app_name="slopanoc-api", agent=agent, session_service=session_service.adk_session_service)


@pytest.mark.asyncio
async def test_real_adk_rewind_excludes_the_discarded_turn_from_active_history() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    session = await append_user_turn(service, session, "inv-1", "first message", "first reply")
    session = await append_user_turn(service, session, "inv-2", "second message", "second reply")
    session = await append_user_turn(service, session, "inv-3", "third message", "third reply")

    real_runner = _make_real_runner(service)
    chat_service = ChatService(service, runner=real_runner)
    await chat_service.rewind_before_user_turn(session_id, 1)  # discard turn 2 and everything after

    refreshed = await service.get_session(session_id)
    active = _active_events(refreshed.events)
    active_invocation_ids = {e.invocation_id for e in active}
    assert active_invocation_ids == {"inv-1"}

    # A subsequent "edit" resolving turn index 1 again must now find the
    # NEXT genuinely active turn -- there is none yet, so it's out of range.
    assert _resolve_invocation_id_for_active_user_turn(refreshed.events, 1) is None
    assert _resolve_invocation_id_for_active_user_turn(refreshed.events, 0) == "inv-1"


@pytest.mark.asyncio
async def test_real_adk_rewind_reverses_session_state_to_the_pre_edit_point() -> None:
    """Proves ADK's own state-delta reversal (never reimplemented by this
    backend) correctly restores session.state, e.g. a Teams chat selected
    only during a since-discarded turn is genuinely un-selected again.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    session = await append_user_turn(service, session, "inv-1", "hello")
    session = await append_user_turn(service, session, "inv-2", "select the ops chat")
    await service.persist_state_delta(session, {"selected_teams_chat_id": "c1", "selected_teams_chat_topic": "Ops"})
    session = await service.get_session(session_id)
    assert session.state.get("selected_teams_chat_id") == "c1"

    real_runner = _make_real_runner(service)
    chat_service = ChatService(service, runner=real_runner)
    await chat_service.rewind_before_user_turn(session_id, 1)  # before the turn that selected the chat

    refreshed = await service.get_session(session_id)
    assert refreshed.state.get("selected_teams_chat_id") is None
    assert refreshed.state.get("selected_teams_chat_topic") is None


# --- 6. HTTP route -----------------------------------------------------------


@pytest.fixture()
def rewind_http_session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture()
def rewind_http_client(rewind_http_session_service: ApiSessionService):
    from backend.api.chat_service import get_chat_service

    fake_runner = FakeRunner(rewind_http_session_service)
    app.dependency_overrides[get_session_service] = lambda: rewind_http_session_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(rewind_http_session_service, runner=fake_runner)
    try:
        yield TestClient(app), fake_runner
    finally:
        app.dependency_overrides.clear()


def test_rewind_route_success_returns_the_same_session_id(rewind_http_client, rewind_http_session_service) -> None:
    client, fake_runner = rewind_http_client
    session_id = client.post("/api/sessions").json()["session_id"]

    async def _seed():
        session = await rewind_http_session_service.get_session(session_id)
        await append_user_turn(rewind_http_session_service, session, "inv-1", "hello")

    import asyncio

    asyncio.run(_seed())

    response = client.post(f"/api/sessions/{session_id}/rewind", json={"before_user_turn_index": 0})

    assert response.status_code == 200
    assert response.json() == {"session_id": session_id}
    assert fake_runner.rewind_calls == [
        {"user_id": "api-user", "session_id": session_id, "rewind_before_invocation_id": "inv-1"}
    ]


def test_rewind_route_unknown_session_is_a_safe_404(rewind_http_client) -> None:
    client, fake_runner = rewind_http_client

    response = client.post("/api/sessions/does-not-exist/rewind", json={"before_user_turn_index": 0})

    assert response.status_code == 404
    assert response.json()["errorCode"] == "not_found"
    assert fake_runner.rewind_calls == []


def test_rewind_route_missing_field_is_a_safe_400(rewind_http_client) -> None:
    client, _ = rewind_http_client
    session_id = client.post("/api/sessions").json()["session_id"]

    response = client.post(f"/api/sessions/{session_id}/rewind", json={})

    assert response.status_code in (400, 422)


def test_rewind_route_out_of_range_index_is_a_safe_error(rewind_http_client, rewind_http_session_service) -> None:
    client, fake_runner = rewind_http_client
    session_id = client.post("/api/sessions").json()["session_id"]

    async def _seed():
        session = await rewind_http_session_service.get_session(session_id)
        await append_user_turn(rewind_http_session_service, session, "inv-1", "hello")

    import asyncio

    asyncio.run(_seed())

    response = client.post(f"/api/sessions/{session_id}/rewind", json={"before_user_turn_index": 9})

    assert response.status_code == 400
    assert response.json()["errorCode"] == "validation_error"
    assert fake_runner.rewind_calls == []


def test_rewind_route_never_appears_to_send_anything_to_teams() -> None:
    """Structural guarantee at the route level: the handler function body
    itself never references the execution/gateway layer.
    """
    from backend.api import app as app_module

    source = inspect.getsource(app_module)
    rewind_start = source.index("async def rewind_session")
    rewind_end = source.index("async def approve_proposal_endpoint")
    handler_body = source[rewind_start:rewind_end]
    for forbidden in ("execution_service", "power_automate", "PowerAutomate"):
        assert forbidden not in handler_body
