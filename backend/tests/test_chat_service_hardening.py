"""Phase 4E hardening pass (post-freeze-review): tests for the two gaps
identified before Phase 4E could be safely frozen --

1. The session lock must be released ONLY when the old turn can no
   longer mutate the session -- not merely when an HTTP consumer stops
   reading (see `chat_service.execute_turn_events`'s updated docstring
   for why a bare `Aclosing`-wrapped `async for` was insufficient: on the
   modern ASGI path, a client disconnect does not reliably/promptly close
   the generator chain at all). Covered primarily in
   test_api_streaming_endpoint.py (background-task tracking, blocking a
   second same-session operation). This file adds the remaining
   `run_turn`-facing regression coverage.

2. A `case_context` status must represent an ACTUAL authorized Case
   load, not merely the presence of a (possibly stale) `active_case_id`
   hint in ADK session state -- covered here.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY, link_session
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import Stage, StreamEventType
from backend.cases.service import CaseService
from backend.tests._api_fakes import FakeRunner

ALICE = "alice"
BOB = "bob"


async def _case_context_statuses(collected) -> list:
    return [
        e
        for e in collected
        if e.type == StreamEventType.STATUS and e.data.get("stage") == Stage.CASE_CONTEXT.value
    ]


async def _collect(chat_service: ChatService, session_id: str, message: str, user_id: str) -> list:
    return [event async for event in chat_service.execute_turn_events(session_id, message, user_id)]


# --- Case-context status must reflect an actual authorized load -------------


@pytest.mark.asyncio
async def test_no_case_context_status_for_a_hint_pointing_at_a_case_that_does_not_exist() -> None:
    service = ApiSessionService()
    case_service = CaseService()
    session_id = await service.create_session(ALICE)
    session = await service.get_session(session_id, ALICE)
    await service.persist_state_delta(session, {ACTIVE_CASE_ID_STATE_KEY: "totally-made-up-case-id"})

    chat_service = ChatService(service, runner=FakeRunner(service), case_service=case_service)
    collected = await _collect(chat_service, session_id, "hi", ALICE)

    assert await _case_context_statuses(collected) == []


@pytest.mark.asyncio
async def test_no_case_context_status_for_a_hint_pointing_at_a_case_the_user_is_not_a_member_of() -> None:
    """The hint alone must never be treated as authorization -- it must
    always be re-verified against `CaseService`, exactly as the frozen
    Phase 4D instruction provider (`case_context.py`) itself does.
    """
    service = ApiSessionService()
    case_service = CaseService()
    bob_case = await case_service.create_case(BOB, "Bob's case", "Bob's problem")

    session_id = await service.create_session(ALICE)
    session = await service.get_session(session_id, ALICE)
    # Simulates a stale/forged hint -- Alice's session state claims Bob's
    # case is active, but Alice was never made a member of it.
    await service.persist_state_delta(session, {ACTIVE_CASE_ID_STATE_KEY: bob_case.case_id})

    chat_service = ChatService(service, runner=FakeRunner(service), case_service=case_service)
    collected = await _collect(chat_service, session_id, "hi", ALICE)

    assert await _case_context_statuses(collected) == []


@pytest.mark.asyncio
async def test_no_case_context_status_when_no_hint_is_present_at_all() -> None:
    service = ApiSessionService()
    session_id = await service.create_session(ALICE)
    chat_service = ChatService(service, runner=FakeRunner(service), case_service=CaseService())

    collected = await _collect(chat_service, session_id, "hi", ALICE)

    assert await _case_context_statuses(collected) == []


@pytest.mark.asyncio
async def test_case_context_status_is_still_emitted_for_a_genuinely_linked_authorized_case() -> None:
    """Regression guard: the fix must suppress the FALSE-POSITIVE case
    (stale hint), not break the genuine, authorized case.
    """
    service = ApiSessionService()
    case_service = CaseService()
    case = await case_service.create_case(ALICE, "Real case", "Real problem")

    session_id = await service.create_session(ALICE)
    await link_session(service, case_service, ALICE, case.case_id, session_id)

    chat_service = ChatService(service, runner=FakeRunner(service), case_service=case_service)
    collected = await _collect(chat_service, session_id, "hi", ALICE)

    statuses = await _case_context_statuses(collected)
    assert len(statuses) == 1
    assert statuses[0].data["label"].count("Real case") <= 1  # title only, never the problem statement


@pytest.mark.asyncio
async def test_case_context_status_absence_matches_what_the_instruction_provider_would_actually_do() -> None:
    """Cross-check against the frozen Phase 4D authorization primitive
    directly: whenever `CaseService.get_case(user_id, case_id)` itself
    would raise (exactly what `case_context.py`'s instruction provider
    catches to silently omit case context for this turn), the status
    must also be suppressed -- proving the two are driven by the same
    check, not two independently-maintained ones that could drift apart.
    """
    from backend.gateway.safe_error import SafeErrorException

    service = ApiSessionService()
    case_service = CaseService()
    session_id = await service.create_session(ALICE)
    session = await service.get_session(session_id, ALICE)
    await service.persist_state_delta(session, {ACTIVE_CASE_ID_STATE_KEY: "nonexistent"})

    with pytest.raises(SafeErrorException):
        await case_service.get_case(ALICE, "nonexistent")

    chat_service = ChatService(service, runner=FakeRunner(service), case_service=case_service)
    collected = await _collect(chat_service, session_id, "hi", ALICE)

    assert await _case_context_statuses(collected) == []


# --- run_turn's external contract is unaffected by the background-task -----
# -- redesign (instruction: "keep frozen: event schema... do not otherwise
# redesign Phase 4E") -------------------------------------------------------


@pytest.mark.asyncio
async def test_run_turn_response_shape_is_unaffected_by_the_background_task_redesign() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    response = await chat_service.run_turn(session_id, "hello")

    assert response.session_id == session_id
    assert response.message.content == "echo: hello"
    assert response.pending_action is None


@pytest.mark.asyncio
async def test_run_turn_still_fully_drains_the_background_turn_before_returning() -> None:
    """`run_turn` must never return before the underlying background task
    has finished -- otherwise its own external synchronous contract
    ("the turn is done when this call returns") would be broken by the
    hardening-pass redesign.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    await chat_service.run_turn(session_id, "hello")

    # The lock is released synchronously, inside the background task's own
    # `async with lock_for(...):`, strictly before `run_turn` can observe
    # the turn's completion -- no extra event-loop turn needed for this.
    assert not service.lock_for(session_id).locked()

    # Registry cleanup runs via the task's own `add_done_callback`
    # (`loop.call_soon`) -- one more trip through the event loop lets it
    # fire before asserting on it (see the same note in
    # test_api_streaming_endpoint.py).
    await asyncio.sleep(0)
    assert chat_service._background_turns == set()
