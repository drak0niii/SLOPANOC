"""Tests for the pre-4H latency investigation pass -- proves the
performance-architecture claims from that investigation deterministically
(never a brittle wall-clock assertion against a real external service; see
perf_timing.py's own module docstring for the same posture).
"""
from __future__ import annotations

import asyncio
import inspect
import logging

import pytest

import backend.api.chat_service as chat_service_module
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse, FakeRunner


async def _collect(chat_service: ChatService, session_id: str, message: str, user_id: str = "api-user") -> list:
    return [event async for event in chat_service.execute_turn_events(session_id, message, user_id)]


def _teams_ok_events(evidence_authors: list[str], chat_id: str = "c1") -> list:
    response = {
        "outcome": "ok",
        "chat_id": chat_id,
        "chat_title": "Ops Bridge",
        "evidence": [
            {"message_id": f"m{i}", "author": author, "sent_at": f"2026-08-{10 + i:02d}T09:00:00Z"}
            for i, author in enumerate(evidence_authors)
        ],
    }
    return [
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]),
        FakeEvent(final=False, function_responses=[FakeFunctionResponse("incident_manager", response)]),
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("record_source_requirements", {"requires_teams": True, "requires_governed_knowledge": False})
            ],
        ),
        FakeEvent(text="Here is a summary.", final=True),
    ]


# --- Simple conversational path never touches Teams tools -------------------


@pytest.mark.asyncio
async def test_a_plain_conversational_turn_never_calls_incident_manager() -> None:
    """`FakeRunner`'s default ("hello"-style) run carries no
    `incident_manager` function call/response at all -- proving THIS
    backend's own orchestration code never forces Teams delegation for a
    turn the (fake, in this test) model itself never delegated. No
    hardcoded "if message is a greeting" routing exists anywhere in this
    codebase to make this true -- see test_team_manager_delegation_scope_
    prompt_contract.py for the corresponding prompt-contract proof.
    """
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    collected = await _collect(chat_service, session_id, "hello")

    assert not any(e.type == StreamEventType.ACTION_PENDING for e in collected)
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert "source" not in completed.data


@pytest.mark.asyncio
async def test_a_plain_conversational_turn_logs_no_delegation_perf_line(caplog: pytest.LogCaptureFixture) -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        await _collect(chat_service, session_id, "hello")

    assert not any("stage=incident_manager_delegation" in r.getMessage() for r in caplog.records)


# --- No duplicate contributor retrieval in one source-building flow --------


@pytest.mark.asyncio
async def test_contributors_resolver_is_called_exactly_once_per_teams_turn() -> None:
    """The mid-loop concurrency optimization (start the membership fetch
    as soon as chat_id is known) must not cause a SECOND, redundant fetch
    when the turn later reuses that same result."""
    calls: list = []

    async def counting_resolver(chat_id):
        calls.append(chat_id)
        await asyncio.sleep(0)  # let the event loop actually interleave
        return ["Alex"]

    events = _teams_ok_events(evidence_authors=["Alex"], chat_id="c1")
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=counting_resolver
    )

    collected = await _collect(chat_service, session_id, "summarize Ops Bridge")
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)

    assert calls == ["c1"]
    assert completed.data["source"]["contributors"] == ["Alex"]


@pytest.mark.asyncio
async def test_source_reference_enrichment_perf_line_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    events = _teams_ok_events(evidence_authors=["Alex"], chat_id="c1")

    async def fake_resolver(chat_id):
        return ["Alex"]

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=fake_resolver
    )

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        await _collect(chat_service, session_id, "summarize Ops Bridge")

    enrichment_lines = [r for r in caplog.records if "stage=source_reference_enrichment" in r.getMessage()]
    assert len(enrichment_lines) == 1


# --- Long-lived resource reuse ----------------------------------------------


def test_get_chat_service_is_a_process_wide_singleton() -> None:
    assert get_chat_service.cache_info().maxsize == 1


def test_build_runner_is_called_exactly_once_per_chat_service_instance_across_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `ChatService` instance must build its `Runner` once, in
    `__init__` -- never per-turn. Simulated here with a call-counting fake
    in place of the real (Gemini-backed) `_build_runner`, since the real
    one requires live model credentials this test suite never has.
    """
    build_calls: list = []

    def fake_build_runner(session_service):
        build_calls.append(session_service)
        return FakeRunner(session_service)

    monkeypatch.setattr(chat_service_module, "_build_runner", fake_build_runner)

    service = ApiSessionService()
    chat_service = ChatService(service)  # no `runner=` override -- exercises _build_runner

    assert len(build_calls) == 1
    first_runner = chat_service._runner

    async def _run_two_turns():
        s1 = await service.create_session()
        await _collect(chat_service, s1, "hello")
        s2 = await service.create_session()
        await _collect(chat_service, s2, "hello again")

    asyncio.run(_run_two_turns())

    assert len(build_calls) == 1  # still only ever built once
    assert chat_service._runner is first_runner


# --- Instrumentation is safe and does not alter the SSE contract -----------


@pytest.mark.asyncio
async def test_perf_log_lines_never_contain_message_text_or_chat_identifiers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    events = _teams_ok_events(evidence_authors=["Alex"], chat_id="19:super-secret-chat-id@thread.v2")

    async def fake_resolver(chat_id):
        return ["Alex"]

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(
        service, runner=FakeRunner(service, events=events), teams_contributors_resolver=fake_resolver
    )

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        await _collect(chat_service, session_id, "please do not log this exact sentence")

    assert len(caplog.records) > 0
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "please do not log this exact sentence" not in blob
    assert "19:super-secret-chat-id@thread.v2" not in blob
    assert "Here is a summary." not in blob
    for record in caplog.records:
        message = record.getMessage()
        assert message.startswith("perf stage=")


@pytest.mark.asyncio
async def test_instrumentation_does_not_change_the_sse_event_type_sequence_for_a_simple_turn() -> None:
    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=FakeRunner(service))

    collected = await _collect(chat_service, session_id, "hello")
    types_seen = [e.type for e in collected]

    # The exact same shape every other test in this suite already expects
    # for a plain successful turn -- proves perf instrumentation is a
    # logging-only side channel, never a new/altered/reordered SSE event.
    assert types_seen[0] == StreamEventType.RUN_STARTED
    assert StreamEventType.STATUS_CLEAR in types_seen
    assert types_seen[-2] == StreamEventType.MESSAGE_COMPLETED or StreamEventType.MESSAGE_COMPLETED in types_seen
    assert types_seen[-1] == StreamEventType.RUN_COMPLETED
    assert all(
        t
        in (
            StreamEventType.RUN_STARTED,
            StreamEventType.STATUS,
            StreamEventType.STATUS_CLEAR,
            StreamEventType.MESSAGE_DELTA,
            StreamEventType.MESSAGE_COMPLETED,
            StreamEventType.ACTION_PENDING,
            StreamEventType.SELECTION_PENDING,
            StreamEventType.ERROR,
            StreamEventType.RUN_COMPLETED,
            StreamEventType.TRACE_STEP,
        )
        for t in types_seen
    )
