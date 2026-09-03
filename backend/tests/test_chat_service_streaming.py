"""Tests for backend/api/chat_service.py's canonical event pipeline
(instruction sections 48-52) -- `execute_turn_events`, and its
relationship to the pre-existing synchronous `run_turn`.
"""
from __future__ import annotations

import inspect

import pytest

from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.gateway.safe_error import SafeErrorException
from backend.tests._api_fakes import (
    FakeEvent,
    FakeFunctionCall,
    FakeFunctionResponse,
    FakeRunner,
    NoFinalTextRunner,
    RaisingRunner,
    append_state_delta,
    simulate_proposal,
    simulate_selection,
)


async def _collect(chat_service: ChatService, session_id: str, message: str, user_id: str = "api-user") -> list:
    return [event async for event in chat_service.execute_turn_events(session_id, message, user_id)]


def _seed_message_texts(texts: dict[str, str]):
    """A `FakeRunner` `side_effect` that records `texts` into
    `turn_context`'s in-process mailbox under the CURRENT turn's `run_id`
    -- simulates `teams_get_messages` having forwarded the turn's
    actually-retrieved message text (snippet-authenticity fix; see
    get_messages.py/turn_context.py/source_reference.py). Without this, a
    fake `incident_manager` "ok" response's `evidence` produces NO
    displayed Supporting Evidence examples at all, by design -- the
    backend never trusts model-reproduced text for the snippet. Relies on
    `chat_service.py` having already called `bind_run_id` before invoking
    the (fake) runner -- exactly like the real `teams_get_messages` relies
    on it having been bound before ANY tool call happens.
    """
    from backend.api.turn_context import current_run_id, record_message_texts

    async def side_effect(session_service, session, text):
        record_message_texts(current_run_id(), texts)

    return side_effect


async def _empty_contributors() -> list:
    """A no-op fake for `ChatService`'s injectable `teams_contributors_resolver`
    (contributor-accuracy fix) -- used by tests that produce a Teams
    source reference but don't care about `contributors`, so the default,
    real (network-bound) `teams_get_members` resolver is never exercised.
    """
    return []


# --- True message streaming (instruction section 48) -----------------------


@pytest.mark.asyncio
async def test_incremental_partials_become_message_delta_events() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text="The likely", final=False, partial=True),
        FakeEvent(text=" cause", final=False, partial=True),
        FakeEvent(text=" is X.", final=False, partial=True),
        FakeEvent(text="The likely cause is X.", final=True, partial=False),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    collected = await _collect(chat_service, session_id, "why?")

    deltas = [e for e in collected if e.type == StreamEventType.MESSAGE_DELTA]
    assert [d.data["text"] for d in deltas] == ["The likely", " cause", " is X."]


@pytest.mark.asyncio
async def test_delta_order_is_preserved() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text="1", final=False, partial=True),
        FakeEvent(text="2", final=False, partial=True),
        FakeEvent(text="3", final=False, partial=True),
        FakeEvent(text="123", final=True, partial=False),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    collected = await _collect(chat_service, session_id, "count")

    deltas = [e for e in collected if e.type == StreamEventType.MESSAGE_DELTA]
    assert [d.data["text"] for d in deltas] == ["1", "2", "3"]
    assert [d.sequence for d in deltas] == sorted(d.sequence for d in deltas)


@pytest.mark.asyncio
async def test_delta_contains_only_new_text_never_cumulative() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text="Hello", final=False, partial=True),
        FakeEvent(text=" world", final=False, partial=True),
        FakeEvent(text="Hello world", final=True, partial=False),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    collected = await _collect(chat_service, session_id, "hi")

    deltas = [e.data["text"] for e in collected if e.type == StreamEventType.MESSAGE_DELTA]
    for delta in deltas:
        assert "Hello world" not in delta or delta == "Hello world"  # no delta re-sends the accumulated string
    assert deltas == ["Hello", " world"]


@pytest.mark.asyncio
async def test_status_cleared_before_first_delta() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text="chunk", final=False, partial=True),
        FakeEvent(text="chunk", final=True, partial=False),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    collected = await _collect(chat_service, session_id, "hi")
    types_in_order = [e.type for e in collected]

    clear_index = types_in_order.index(StreamEventType.STATUS_CLEAR)
    first_delta_index = types_in_order.index(StreamEventType.MESSAGE_DELTA)
    assert clear_index < first_delta_index


@pytest.mark.asyncio
async def test_final_content_emitted_once() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text="a", final=False, partial=True),
        FakeEvent(text="b", final=False, partial=True),
        FakeEvent(text="ab", final=True, partial=False),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    collected = await _collect(chat_service, session_id, "hi")

    completed = [e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED]
    assert len(completed) == 1


@pytest.mark.asyncio
async def test_accumulated_deltas_equal_message_completed_content() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text="The ", final=False, partial=True),
        FakeEvent(text="quick ", final=False, partial=True),
        FakeEvent(text="fox.", final=False, partial=True),
        FakeEvent(text="The quick fox.", final=True, partial=False),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    collected = await _collect(chat_service, session_id, "hi")

    deltas = [e.data["text"] for e in collected if e.type == StreamEventType.MESSAGE_DELTA]
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "".join(deltas) == completed.data["content"]


@pytest.mark.asyncio
async def test_thought_parts_never_become_deltas() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(text="private reasoning", final=False, partial=True, thought=True),
        FakeEvent(text="the answer", final=False, partial=True),
        FakeEvent(text="the answer", final=True, partial=False),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    collected = await _collect(chat_service, session_id, "hi")

    deltas = [e.data["text"] for e in collected if e.type == StreamEventType.MESSAGE_DELTA]
    assert "private reasoning" not in deltas
    assert deltas == ["the answer"]


# --- No fake streaming (instruction section 49) -----------------------------


def test_no_helper_splits_a_completed_string_into_fake_chunks() -> None:
    import backend.api.chat_service as module

    source = inspect.getsource(module)
    # No chunking/splitting helpers of the kind a fake-streaming
    # implementation would need.
    for forbidden in ("textwrap.wrap", ".split(' ')", "chunk_size", "for i in range(0, len(", "def _fake_stream"):
        assert forbidden not in source


@pytest.mark.asyncio
async def test_fallback_with_no_true_streaming_still_emits_completed_normally() -> None:
    """No partial events at all -- the fallback sequence (instruction
    section 28) still ends with a normal, single message.completed.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))  # single final, non-partial event

    collected = await _collect(chat_service, session_id, "hi")

    types_in_order = [e.type for e in collected]
    assert StreamEventType.MESSAGE_DELTA not in types_in_order
    assert types_in_order.count(StreamEventType.MESSAGE_COMPLETED) == 1
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "echo: hi"


# --- Sync/stream shared pipeline (instruction section 50) -------------------


def test_run_turn_uses_execute_turn_events() -> None:
    source = inspect.getsource(ChatService.run_turn)
    assert "execute_turn_events(" in source


def test_only_one_runner_invocation_site_exists() -> None:
    """`turn_runner.run_async(` must appear exactly once in
    chat_service.py -- proving there is only one place that actually
    drives an ADK Runner, shared by both consumption paths. (R1 fix:
    `turn_runner` is a local variable selected, immediately before this
    one call site, between `self._runner`/`self._presentation_runner` --
    see `_run_turn_events`'s own R1 comment -- never two separate
    `.run_async(` call sites.)
    """
    import backend.api.chat_service as module

    source = inspect.getsource(module)
    assert source.count("self._runner.run_async(") == 0
    assert source.count("self._presentation_runner.run_async(") == 0
    assert source.count("turn_runner.run_async(") == 1


@pytest.mark.asyncio
async def test_sync_and_stream_produce_the_same_final_content() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    response = await chat_service.run_turn(session_id, "hi")

    service2 = ApiSessionService()
    session_id_2 = await service2.create_session()
    chat_service_2 = ChatService(service2, runner=FakeRunner(service2))
    collected = await _collect(chat_service_2, session_id_2, "hi")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert response.message.content == completed.data["content"]


@pytest.mark.asyncio
async def test_sync_and_stream_produce_the_same_pending_action() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    collected = await _collect(chat_service, session_id, "propose it")
    action_events = [e for e in collected if e.type == StreamEventType.ACTION_PENDING]
    assert len(action_events) == 1

    service2 = ApiSessionService()
    session_id_2 = await service2.create_session()
    chat_service_2 = ChatService(service2, runner=FakeRunner(service2, side_effect=side_effect))
    response = await chat_service_2.run_turn(session_id_2, "propose it")

    assert response.pending_action is not None
    assert response.pending_action.operation == action_events[0].data["operation"]


@pytest.mark.asyncio
async def test_sync_and_stream_produce_the_same_active_case_behavior() -> None:
    from backend.api.case_service import link_session
    from backend.cases.service import CaseService

    case_service = CaseService()
    case = await case_service.create_case("api-user", "Title", "Problem")

    service = ApiSessionService()
    session_id = await service.create_session(user_id="api-user")
    await link_session(service, case_service, "api-user", case.case_id, session_id)

    chat_service = ChatService(service, runner=FakeRunner(service), case_service=case_service)
    response = await chat_service.run_turn(session_id, "hi", user_id="api-user")

    assert response.active_case is not None
    assert response.active_case.case_id == case.case_id


def test_no_duplicate_orchestration_implementation_exists() -> None:
    """Structural: `_run_turn_events` (the actual per-turn logic) is
    defined exactly once, and `run_turn` never independently constructs
    a `types.Content`/calls the runner itself.
    """
    run_turn_source = inspect.getsource(ChatService.run_turn)
    assert "types.Content(" not in run_turn_source
    assert "self._runner" not in run_turn_source


# --- action.pending (instruction section 51) --------------------------------


@pytest.mark.asyncio
async def test_action_pending_event_emitted_when_proposal_created() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    collected = await _collect(chat_service, session_id, "propose it")

    action_events = [e for e in collected if e.type == StreamEventType.ACTION_PENDING]
    assert len(action_events) == 1
    assert action_events[0].data["operation"] == "teams.sendMessage"


@pytest.mark.asyncio
async def test_action_pending_uses_the_safe_pending_action_dto_shape() -> None:
    from backend.api.schemas import PendingActionDTO

    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    collected = await _collect(chat_service, session_id, "propose it")

    action_event = next(e for e in collected if e.type == StreamEventType.ACTION_PENDING)
    # Round-trips cleanly through the DTO -- proves the shape matches.
    dto = PendingActionDTO(**action_event.data)
    assert dto.operation == "teams.sendMessage"


@pytest.mark.asyncio
async def test_action_pending_never_includes_payload_hash_or_credentials() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    collected = await _collect(chat_service, session_id, "propose it")

    action_event = next(e for e in collected if e.type == StreamEventType.ACTION_PENDING)
    assert "payload_hash" not in action_event.data
    assert "payload_hash" not in str(action_event.data)


# --- selection.pending (interaction-capability extension) -------------------


@pytest.mark.asyncio
async def test_selection_pending_event_emitted_when_a_selection_is_created() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_selection(
            session_service, session, "Project Falcon Room", ["Project Falcon Room Test", "Project Falcon Test"]
        )

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    collected = await _collect(chat_service, session_id, "send a message to Project Falcon Room")

    selection_events = [e for e in collected if e.type == StreamEventType.SELECTION_PENDING]
    assert len(selection_events) == 1
    assert selection_events[0].data["requested_value"] == "Project Falcon Room"
    assert [o["label"] for o in selection_events[0].data["options"]] == [
        "Project Falcon Room Test",
        "Project Falcon Test",
    ]


@pytest.mark.asyncio
async def test_selection_pending_uses_the_safe_dto_shape_and_never_leaks_the_raw_chat_id() -> None:
    from backend.api.schemas import PendingSelectionDTO

    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_selection(session_service, session, "Project Falcon Room", ["Project Falcon Room Test"])

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    collected = await _collect(chat_service, session_id, "send it")

    selection_event = next(e for e in collected if e.type == StreamEventType.SELECTION_PENDING)
    dto = PendingSelectionDTO(**selection_event.data)
    assert dto.requested_value == "Project Falcon Room"
    assert "chat-0" not in str(selection_event.data)
    assert "option_targets" not in selection_event.data
    assert "pending_write_message" not in selection_event.data


@pytest.mark.asyncio
async def test_no_selection_event_when_no_selection_was_created() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    collected = await _collect(chat_service, session_id, "hello")

    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)


@pytest.mark.asyncio
async def test_action_pending_and_selection_pending_never_both_fire_for_the_same_turn() -> None:
    """A turn either produces a normal proposal OR needs disambiguation --
    never both at once, since a proposal requires an already-resolved
    chat id."""
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    collected = await _collect(chat_service, session_id, "propose it")

    assert any(e.type == StreamEventType.ACTION_PENDING for e in collected)
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)


@pytest.mark.asyncio
async def test_selection_remains_in_session_state_after_streaming() -> None:
    from backend.selection.service import PENDING_SELECTION_STATE_KEY

    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_selection(session_service, session, "Project Falcon Room", ["Project Falcon Room Test"])

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    await _collect(chat_service, session_id, "send it")

    refreshed = await service.get_session(session_id)
    assert PENDING_SELECTION_STATE_KEY in refreshed.state
    assert refreshed.state[PENDING_SELECTION_STATE_KEY]["status"] == "pending"


@pytest.mark.asyncio
async def test_no_selection_event_on_a_turn_that_supersedes_the_active_selection() -> None:
    """Once superseded (an exact-match resolution -- see
    test_teams_list_chats.py's own supersede tests), a selection is no
    longer PENDING, so `map_pending_selection` correctly stops
    re-affirming it -- this turn emits no `selection.pending` event at
    all, even though a selection still technically exists in session
    state."""
    from backend.selection.service import PENDING_SELECTION_STATE_KEY, supersede_active_selection

    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_selection(session_service, session, "Project Falcon Room", ["Project Falcon Room Test"])
        refreshed = await session_service.get_session(session.id, session.user_id)
        supersede_active_selection(refreshed.state)
        delta = {PENDING_SELECTION_STATE_KEY: refreshed.state[PENDING_SELECTION_STATE_KEY]}
        await append_state_delta(session_service, refreshed, delta)

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    collected = await _collect(chat_service, session_id, "send it")

    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)


@pytest.mark.asyncio
async def test_proposal_remains_in_session_state_after_streaming() -> None:
    from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY

    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect))
    await _collect(chat_service, session_id, "propose it")

    refreshed = await service.get_session(session_id)
    assert PENDING_ACTION_PROPOSAL_STATE_KEY in refreshed.state
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] == "pending"


@pytest.mark.asyncio
async def test_streaming_never_mutates_approval_status_itself() -> None:
    """Streaming a turn that creates a proposal must never itself approve
    it -- only `approval_service.approve`/`reject` (Phase 4B, untouched)
    can.
    """
    import backend.api.chat_service as module

    source = inspect.getsource(module)
    assert "approve_proposal" not in source
    assert "reject_proposal" not in source


# --- Errors (instruction section 52) ----------------------------------------


@pytest.mark.asyncio
async def test_safe_model_failure_emits_error_event() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("raw db url or secret, sql exception text")))

    collected = await _collect(chat_service, session_id, "hi")

    error_events = [e for e in collected if e.type == StreamEventType.ERROR]
    assert len(error_events) == 1
    assert "raw db url" not in error_events[0].data["message"]
    assert "sql exception" not in error_events[0].data["message"]


@pytest.mark.asyncio
async def test_internal_exception_text_is_suppressed_in_error_event() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    secret_text = "Traceback (most recent call last): ConnectionError: postgres://user:pw@host/db"
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError(secret_text)))

    collected = await _collect(chat_service, session_id, "hi")
    dumped = str([e.model_dump() for e in collected])

    assert secret_text not in dumped
    assert "postgres://" not in dumped


@pytest.mark.asyncio
async def test_status_cleared_on_error() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("boom")))

    collected = await _collect(chat_service, session_id, "hi")
    types_in_order = [e.type for e in collected]

    assert StreamEventType.STATUS_CLEAR in types_in_order
    error_index = types_in_order.index(StreamEventType.ERROR)
    clear_index = types_in_order.index(StreamEventType.STATUS_CLEAR)
    assert clear_index < error_index


@pytest.mark.asyncio
async def test_run_completed_emitted_after_error() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("boom")))

    collected = await _collect(chat_service, session_id, "hi")

    assert collected[-1].type == StreamEventType.RUN_COMPLETED
    assert collected[-1].data["outcome"] == "error"


@pytest.mark.asyncio
async def test_no_final_text_also_produces_error_and_run_completed() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=NoFinalTextRunner())

    collected = await _collect(chat_service, session_id, "hi")

    assert any(e.type == StreamEventType.ERROR for e in collected)
    assert collected[-1].type == StreamEventType.RUN_COMPLETED
    assert collected[-1].data["outcome"] == "error"


@pytest.mark.asyncio
async def test_session_lock_released_after_error() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("boom")))

    await _collect(chat_service, session_id, "hi")

    assert not service.lock_for(session_id).locked()


@pytest.mark.asyncio
async def test_run_turn_still_raises_safe_error_on_failure() -> None:
    """Backward compatibility: `run_turn` (used by the pre-4E synchronous
    endpoint) still raises `SafeErrorException`, exactly as before this
    refactor.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("boom")))

    with pytest.raises(SafeErrorException) as exc_info:
        await chat_service.run_turn(session_id, "hi")
    assert exc_info.value.safe_error.error_code == "run_failure"


@pytest.mark.asyncio
async def test_successful_run_completed_has_ok_outcome() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    collected = await _collect(chat_service, session_id, "hi")

    assert collected[-1].type == StreamEventType.RUN_COMPLETED
    assert collected[-1].data["outcome"] == "ok"


@pytest.mark.asyncio
async def test_run_completion_leaves_no_active_status() -> None:
    """After run.completed, no further status is implied -- the last
    status-related event in a successful run is status.clear (before the
    response), never a status left dangling.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    collected = await _collect(chat_service, session_id, "hi")

    last_status_like = [e for e in collected if e.type in (StreamEventType.STATUS, StreamEventType.STATUS_CLEAR)][-1]
    assert last_status_like.type == StreamEventType.STATUS_CLEAR


# --- Expandable, sanitized run trace (pre-4H milestone) ---------------------


def _trace_steps(collected: list) -> list:
    return [e for e in collected if e.type == StreamEventType.TRACE_STEP]


@pytest.mark.asyncio
async def test_simple_conversation_produces_only_the_generic_terminal_trace_step() -> None:
    """A plain "hello"-style turn (no tool calls at all) must never
    fabricate Teams/case/evidence trace activity -- its only trace step
    is the unconditional terminal "Generated the response" milestone.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    collected = await _collect(chat_service, session_id, "hello")

    steps = _trace_steps(collected)
    assert len(steps) == 1
    assert steps[0].data["label"] == "Generated the response"
    assert steps[0].data["category"] == "response"
    assert steps[0].data["status"] == "completed"


@pytest.mark.asyncio
async def test_trace_step_attaches_to_the_same_run_id_as_every_other_event() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    collected = await _collect(chat_service, session_id, "hello")

    run_ids = {e.run_id for e in collected}
    assert len(run_ids) == 1


@pytest.mark.asyncio
async def test_trace_steps_appear_in_chronological_sequence_order() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "ok", "chat_id": "c1", "evidence": [{"message_id": "m1", "author": "A", "sent_at": "x"}] * 4},
                )
            ],
        ),
        FakeEvent(text="Here is a summary.", final=True),
    ]
    # A no-op fake resolver -- this test doesn't exercise contributors, but
    # the default resolver would otherwise attempt a real (network-bound)
    # `teams_get_members` call since a `chat_id` is present.
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=lambda _chat_id: _empty_contributors()
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")

    steps = _trace_steps(collected)
    labels_in_order = [s.data["label"] for s in steps]
    assert labels_in_order == [
        "Used the selected Teams conversation",
        "Reviewed 4 retrieved messages",
        "Generated the response",
    ]
    # sequence strictly increases across the WHOLE event stream, not just
    # among trace steps -- proves true chronological interleaving, never
    # a post-hoc reordering.
    assert [e.sequence for e in collected] == sorted(e.sequence for e in collected)


@pytest.mark.asyncio
async def test_teams_summary_trace_never_leaks_raw_identifiers_or_message_bodies() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "ok",
                        "chat_id": "19:super-secret-chat-id@thread.v2",
                        "summary": "Customer secret password is hunter2",
                        "evidence": [{"message_id": "m1", "author": "A", "sent_at": "x"}] * 4,
                    },
                )
            ],
        ),
        FakeEvent(text="Here is a summary.", final=True),
    ]
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=lambda _chat_id: _empty_contributors()
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    steps = _trace_steps(collected)

    blob = str([s.data for s in steps])
    assert "19:super-secret-chat-id@thread.v2" not in blob
    assert "hunter2" not in blob
    assert "incident_manager" not in blob
    assert "AgentTool" not in blob


@pytest.mark.asyncio
async def test_case_context_trace_step_emitted_only_when_case_is_authorized() -> None:
    from backend.api.case_service import link_session
    from backend.cases.service import CaseService

    case_service = CaseService()
    case = await case_service.create_case("api-user", "Title", "Problem")

    service = ApiSessionService()
    session_id = await service.create_session(user_id="api-user")
    await link_session(service, case_service, "api-user", case.case_id, session_id)

    chat_service = ChatService(service, runner=FakeRunner(service), case_service=case_service)
    collected = await _collect(chat_service, session_id, "hi", user_id="api-user")

    steps = _trace_steps(collected)
    assert steps[0].data["label"] == "Loaded active case context"
    assert case.title not in str(steps[0].data)


@pytest.mark.asyncio
async def test_no_case_trace_step_when_no_case_is_linked() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    collected = await _collect(chat_service, session_id, "hi")

    labels = [s.data["label"] for s in _trace_steps(collected)]
    assert "Loaded active case context" not in labels


@pytest.mark.asyncio
async def test_selection_needed_trace_reflects_safe_candidate_count_and_preparation() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_selection(
            session_service, session, "Project Falcon Room", ["Project Falcon Room Test", "Project Falcon Test"]
        )

    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "selection_needed", "candidate_titles": ["Project Falcon Room Test", "Project Falcon Test"]},
                )
            ],
        ),
        FakeEvent(text="Which chat did you mean?", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect, events=events))

    collected = await _collect(chat_service, session_id, "send a message to Project Falcon Room")
    labels = [s.data["label"] for s in _trace_steps(collected)]

    assert labels == [
        "Used the selected Teams conversation",
        "Found 2 similar Teams conversations",
        "Prepared chat selection for review",
        "Generated the response",
    ]
    # The trace itself never carries the raw candidate titles (unlike the
    # dedicated selection.pending event, whose whole purpose is to show
    # them) -- only a safe count.
    assert "Project Falcon Room Test" not in str([s.data for s in _trace_steps(collected)])


@pytest.mark.asyncio
async def test_write_proposal_trace_never_claims_the_message_was_sent() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "proposed", "write_action": {"operation": "teams.sendMessage", "proposal_id": "p1"}},
                )
            ],
        ),
        FakeEvent(text="Ready to send, pending your approval.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect, events=events))

    collected = await _collect(chat_service, session_id, "propose it")
    labels = [s.data["label"] for s in _trace_steps(collected)]

    assert labels == [
        "Used the selected Teams conversation",
        "Prepared a Teams message for review",
        "Generated the response",
    ]
    assert not any("sent" in label.lower() for label in labels)


@pytest.mark.asyncio
async def test_error_run_trace_ends_with_a_sanitized_failure_step_never_a_success_header() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("raw secret db url")))

    collected = await _collect(chat_service, session_id, "hi")
    steps = _trace_steps(collected)

    assert len(steps) == 1
    assert steps[0].data["label"] == "Request could not be completed"
    assert steps[0].data["status"] == "failed"
    assert "Generated the response" not in str(steps[0].data)
    assert "raw secret db url" not in str(steps[0].data)


@pytest.mark.asyncio
async def test_error_run_trace_step_is_the_last_event_before_run_completed() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=RaisingRunner(RuntimeError("boom")))

    collected = await _collect(chat_service, session_id, "hi")

    assert collected[-1].type == StreamEventType.RUN_COMPLETED
    assert collected[-1].data["outcome"] == "error"
    assert collected[-2].type == StreamEventType.TRACE_STEP
    assert collected[-2].data["status"] == "failed"


@pytest.mark.asyncio
async def test_no_final_text_failure_also_produces_the_failure_trace_step() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=NoFinalTextRunner())

    collected = await _collect(chat_service, session_id, "hi")
    steps = _trace_steps(collected)

    assert len(steps) == 1
    assert steps[0].data["status"] == "failed"


@pytest.mark.asyncio
async def test_multiple_runs_on_the_same_session_each_get_their_own_distinct_trace() -> None:
    """Ownership: run A's trace steps must never bleed into run B's --
    each run gets its own `run_id`, and each run's steps are scoped to
    that run_id only (mirrors ApprovalCard/SelectionCard's "one card, one
    originating message" ownership model on the frontend side)."""
    service = ApiSessionService()
    session_id = await service.create_session()

    events_a = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "ok", "evidence": [{"message_id": "m1", "author": "A", "sent_at": "x"}] * 2},
                )
            ],
        ),
        FakeEvent(text="Summary A.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events_a))
    collected_a = await _collect(chat_service, session_id, "summarize Ops Bridge")

    chat_service_2 = ChatService(service, runner=FakeRunner(service))  # plain "hello"-style run
    collected_b = await _collect(chat_service_2, session_id, "thanks")

    run_id_a = collected_a[0].run_id
    run_id_b = collected_b[0].run_id
    assert run_id_a != run_id_b

    labels_a = [s.data["label"] for s in _trace_steps(collected_a)]
    labels_b = [s.data["label"] for s in _trace_steps(collected_b)]
    assert "Reviewed 2 retrieved messages" in labels_a
    assert "Reviewed 2 retrieved messages" not in labels_b
    assert all(s.run_id == run_id_a for s in _trace_steps(collected_a))
    assert all(s.run_id == run_id_b for s in _trace_steps(collected_b))


@pytest.mark.asyncio
async def test_duplicate_underlying_events_never_produce_duplicate_trace_steps() -> None:
    """Two consecutive aggregated events that both map to the exact same
    milestone (e.g. an intermediate ADK event shape re-reporting the same
    fact) collapse into one trace step -- see run_trace.py's dedup.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(text="ok", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    collected = await _collect(chat_service, session_id, "hi")
    labels = [s.data["label"] for s in _trace_steps(collected)]

    assert labels.count("Used the selected Teams conversation") == 1


# --- Structured Teams source/provenance on message.completed (pre-4H UX) ----


@pytest.mark.asyncio
async def test_message_completed_carries_a_structured_source_when_evidence_exists() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "ok",
                        "chat_title": "Ops Bridge",
                        "evidence": [
                            {"message_id": f"m{i}", "author": f"User {i}", "sent_at": f"2026-08-2{i}T09:00:00Z"}
                            for i in range(3)
                        ],
                    },
                )
            ],
        ),
        FakeEvent(text="Here is a summary.", final=True),
    ]
    texts = {f"m{i}": f"Original retrieved message body {i}." for i in range(3)}
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events, side_effect=_seed_message_texts(texts))
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert "source" in completed.data
    source = completed.data["source"]
    assert source["source_type"] == "teams"
    assert source["title"] == "Ops Bridge"
    assert source["message_count"] == 3
    assert len(source["evidence"]) == 3
    assert all(item["snippet"] for item in source["evidence"])


@pytest.mark.asyncio
async def test_message_completed_omits_source_when_no_evidence_was_retrieved() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))  # plain "hello"-style run

    collected = await _collect(chat_service, session_id, "hello")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert "source" not in completed.data


@pytest.mark.asyncio
async def test_source_never_contains_a_raw_chat_id_or_message_id() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "ok",
                        "chat_id": "19:super-secret-chat-id@thread.v2",
                        "chat_title": "Ops Bridge",
                        "evidence": [{"message_id": "m-secret-1", "author": "User A", "sent_at": "2026-08-26T09:00:00Z"}],
                    },
                )
            ],
        ),
        FakeEvent(text="Here is a summary.", final=True),
    ]

    # A fake, injected contributors resolver (mirrors `runner` injection) --
    # asserts the chat_service actually passed it the captured `chat_id`
    # (never leaked to the frontend), and its own fake "member id" must
    # not leak into the SSE payload either, exactly like `message_id`.
    async def fake_resolver(chat_id):
        assert chat_id == "19:super-secret-chat-id@thread.v2"
        return ["User A"]

    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=fake_resolver
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    blob = str(completed.data["source"])
    assert "19:super-secret-chat-id@thread.v2" not in blob
    assert "m-secret-1" not in blob
    assert "chat_id" not in blob
    assert completed.data["source"]["contributors"] == ["User A"]


@pytest.mark.asyncio
async def test_message_completed_never_carries_a_source_for_a_write_proposal_turn() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()

    async def side_effect(session_service, session, text):
        await simulate_proposal(session_service, session, "teams.sendMessage", {"chatId": "c1", "message": "Hi"})

    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "proposed", "write_action": {"operation": "teams.sendMessage", "proposal_id": "p1"}},
                )
            ],
        ),
        FakeEvent(text="Ready to send, pending your approval.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=side_effect, events=events))

    collected = await _collect(chat_service, session_id, "propose it")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert "source" not in completed.data


# --- Supporting evidence snippets and the 5-item display cap (UX polish) ----


@pytest.mark.asyncio
async def test_message_completed_source_snippet_comes_from_forwarded_message_text_not_the_model() -> None:
    """Snippet-authenticity fix: even if a raw evidence dict smuggled a
    `snippet` key (a malformed/legacy payload), it must be ignored --
    ONLY the text forwarded via turn_context (simulating
    `teams_get_messages`'s own retrieved message body) is ever used."""
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "ok",
                        "chat_title": "Ops Bridge",
                        "evidence": [
                            {
                                "message_id": "m0",
                                "author": "User 0",
                                "sent_at": "2026-08-26T09:00:00Z",
                                "snippet": "MODEL-REPRODUCED TEXT, must never be used",
                            }
                        ],
                    },
                )
            ],
        ),
        FakeEvent(text="Here is a summary.", final=True),
    ]
    texts = {"m0": "The real retrieved message body."}
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events, side_effect=_seed_message_texts(texts))
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    snippet = completed.data["source"]["evidence"][0]["snippet"]
    assert snippet == "The real retrieved message body."
    assert "MODEL-REPRODUCED" not in snippet


@pytest.mark.asyncio
async def test_message_completed_source_omits_evidence_when_no_message_text_was_forwarded() -> None:
    """The other half of the fix: with NO forwarded text at all (e.g. a
    legacy/malformed turn), Supporting Evidence is empty -- never author/
    timestamp-only rows -- even though message_count/title are still
    fully populated."""
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "ok",
                        "chat_title": "Ops Bridge",
                        "evidence": [{"message_id": "m0", "author": "User 0", "sent_at": "2026-08-26T09:00:00Z"}],
                    },
                )
            ],
        ),
        FakeEvent(text="Here is a summary.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))  # no text forwarded

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    source = completed.data["source"]
    assert source["evidence"] == []
    assert source["message_count"] == 1
    assert source["title"] == "Ops Bridge"


@pytest.mark.asyncio
async def test_message_completed_source_evidence_is_capped_at_five_even_with_more_retrieved() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "ok",
                        "chat_title": "Ops Bridge",
                        "evidence": [
                            {"message_id": f"m{i}", "author": f"User {i}", "sent_at": f"2026-08-{10 + i:02d}T09:00:00Z"}
                            for i in range(29)
                        ],
                    },
                )
            ],
        ),
        FakeEvent(text="Here is a summary.", final=True),
    ]
    texts = {f"m{i}": f"Original retrieved message body {i}." for i in range(29)}
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events, side_effect=_seed_message_texts(texts))
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    source = completed.data["source"]
    assert source["message_count"] == 29
    assert len(source["evidence"]) == 5


# --- Contributors sourced authoritatively from Teams membership, not -------
# --- evidence authors (contributor-accuracy fix) ---------------------------


def _teams_ok_events(evidence_authors: list[str], chat_id: Optional[str] = "c1") -> list:
    response: dict = {
        "outcome": "ok",
        "chat_title": "Ops Bridge",
        "evidence": [
            {"message_id": f"m{i}", "author": author, "sent_at": f"2026-08-{10 + i:02d}T09:00:00Z"}
            for i, author in enumerate(evidence_authors)
        ],
    }
    if chat_id is not None:
        # `chat_id=None` simulates the live-bug scenario: this turn's raw
        # incident_manager response simply did not carry a `chat_id` at
        # all (omitted entirely, mirroring `exclude_none=True` on the real
        # ADK output-schema dump -- see chat_service.py's own fallback
        # comment for why this must still resolve contributors correctly).
        response["chat_id"] = chat_id
    return [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(final=False, function_responses=[FakeFunctionResponse("incident_manager", response)]),
        FakeEvent(text="Here is a summary.", final=True),
    ]


@pytest.mark.asyncio
async def test_contributors_come_from_the_injected_membership_resolver_not_evidence_authors() -> None:
    events = _teams_ok_events(evidence_authors=["Alex", "Priya"])

    async def fake_resolver(chat_id):
        assert chat_id == "c1"
        # Deliberately DISJOINT from the evidence authors above -- proves
        # contributors is never derived from `evidence`.
        return ["Completely Different Name"]

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=fake_resolver
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert completed.data["source"]["contributors"] == ["Completely Different Name"]


@pytest.mark.asyncio
async def test_a_silent_participant_still_appears_in_contributors() -> None:
    """The exact scenario the fix targets: a real chat member who authored
    none of the retrieved/cited evidence must still show up."""
    events = _teams_ok_events(evidence_authors=["Alex"])

    async def fake_resolver(chat_id):
        return ["Alex", "Silent Participant"]

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=fake_resolver
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert "Silent Participant" in completed.data["source"]["contributors"]


@pytest.mark.asyncio
async def test_contributors_is_empty_when_membership_resolution_fails() -> None:
    """Degrades safely: no crash, source still present, Contributors
    simply omitted -- never falls back to evidence authors."""
    events = _teams_ok_events(evidence_authors=["Alex", "Priya"])

    async def failing_resolver(chat_id):
        return []  # mirrors resolve_authoritative_contributors' own fail-safe return

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=failing_resolver
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert completed.data["source"]["contributors"] == []
    # The rest of the source is unaffected by the membership failure.
    assert completed.data["source"]["message_count"] == 2


@pytest.mark.asyncio
async def test_membership_resolver_is_never_called_when_there_is_no_source() -> None:
    """Performance: the membership call is scoped only to turns that
    actually produced a Teams source reference, never every turn."""
    calls: list = []

    async def tracking_resolver(chat_id):
        calls.append(chat_id)
        return []

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service), teams_contributors_resolver=tracking_resolver)

    await _collect(chat_service, session_id, "hello")

    assert calls == []


@pytest.mark.asyncio
async def test_contributors_are_resolved_from_session_state_chat_id_when_the_turn_event_omits_it() -> None:
    """BUGFIX regression test for the reported "Contributors missing"
    live bug: `TeamsSourceCapture` only ever sees THIS turn's raw event,
    but `incident_manager`'s structured output uses `exclude_none=True`
    (verified against the installed ADK source), so a `chat_id` the model
    left unset is simply ABSENT from the event's function-response dict,
    not present-but-null. `chat_service.py` must still resolve contributors
    correctly by falling back to `selected_teams_chat_id` -- the same
    already-authoritative, cross-turn-persisted state key team_manager's
    own prompt already relies on for chat continuity (state_sync.py),
    written earlier in the SAME turn by
    `sync_incident_manager_result_to_state`'s `after_tool_callback`.
    """
    from backend.tools.teams.state_keys import SELECTED_TEAMS_CHAT_ID_STATE_KEY

    events = _teams_ok_events(evidence_authors=["Alex"], chat_id=None)  # THIS turn's event omits chat_id

    async def side_effect(session_service, session, text):
        # Simulates state_sync.py's after_tool_callback having already
        # written the authoritative selected chat id this same turn (or a
        # prior one) -- a real, persisted state delta, not a hand-waved
        # assumption.
        await append_state_delta(session_service, session, {SELECTED_TEAMS_CHAT_ID_STATE_KEY: "c-authoritative"})

    async def fake_resolver(chat_id):
        assert chat_id == "c-authoritative"
        return ["Alex", "Silent Participant"]

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, events=events, side_effect=side_effect),
        teams_contributors_resolver=fake_resolver,
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert completed.data["source"]["contributors"] == ["Alex", "Silent Participant"]


@pytest.mark.asyncio
async def test_contributors_fall_back_to_the_per_turn_capture_when_session_state_has_nothing() -> None:
    """The session-state fallback above must not become the ONLY path --
    a bare/first-ever Teams turn (nothing in session state yet) still
    resolves contributors from this turn's own captured `chat_id`."""
    events = _teams_ok_events(evidence_authors=["Alex"], chat_id="c-this-turn")

    async def fake_resolver(chat_id):
        assert chat_id == "c-this-turn"
        return ["Alex"]

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=fake_resolver
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert completed.data["source"]["contributors"] == ["Alex"]
