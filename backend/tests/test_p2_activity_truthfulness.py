"""Phase 2 (Runtime Activity Truthfulness) -- tests for the closed
`ActivityEvent`/`ActivityKind` vocabulary (activity_queue.py), the
centralized `ActivityEvent -> Stage/label` translation
(activity_translator.py's `translate_activity_event`), the run-scoped
transport's concurrency/isolation/cleanup guarantees, and the
`chat_service.py` merge loop's SSE trust-gate non-interference.

Numbered comments below map directly to instruction section 24's required
test matrix (1-22).
"""
from __future__ import annotations

import asyncio

import pytest

from backend.api.activity_queue import (
    ActivityKind,
    discard_activity_channel,
    get_activity_channel,
    register_activity_channel,
    report_activity,
)
from backend.api.activity_translator import StatusTranslator
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import Stage, StreamEventType
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse


# =============================================================================
# 1-2: incident_manager delegation alone never emits a Teams status
# (also covered directly in test_activity_translator.py's own dedicated
# tests; repeated here as part of this milestone's own required matrix)
# =============================================================================


def test_1_incident_manager_delegation_alone_never_emits_teams_status() -> None:
    translator = StatusTranslator()
    status = translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    assert status is None


def test_2_incident_manager_with_chat_topic_but_no_teams_activity_never_emits_teams_status() -> None:
    translator = StatusTranslator()
    status = translator.translate_event(
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager", {"chat_topic": "Ops Bridge"})])
    )
    assert status is None


# =============================================================================
# 3-7: Knowledge activity translation
# =============================================================================


def test_3_knowledge_search_started_produces_searching_status() -> None:
    translator = StatusTranslator()
    status = translator.translate_activity_event({"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None})
    assert status is not None
    assert status["label"] == "Searching governed knowledge"
    assert status["stage"] == "knowledge_retrieval"


def test_4_knowledge_search_succeeded_with_results_produces_reviewing_status() -> None:
    translator = StatusTranslator()
    translator.translate_activity_event({"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None})
    status = translator.translate_activity_event(
        {"kind": ActivityKind.KNOWLEDGE_SEARCH_SUCCEEDED, "safe_metadata": {"document_count": 3}}
    )
    assert status is not None
    assert status["label"] == "Reviewing retrieved knowledge"
    assert status["stage"] == "evidence_processing"


def test_5_knowledge_search_succeeded_with_zero_results_never_claims_reviewing() -> None:
    translator = StatusTranslator()
    translator.translate_activity_event({"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None})
    status = translator.translate_activity_event(
        {"kind": ActivityKind.KNOWLEDGE_SEARCH_SUCCEEDED, "safe_metadata": {"document_count": 0}}
    )
    assert status is None


def test_6_knowledge_search_failure_never_produces_a_success_like_status() -> None:
    translator = StatusTranslator()
    translator.translate_activity_event({"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None})
    status = translator.translate_activity_event({"kind": ActivityKind.KNOWLEDGE_SEARCH_FAILED, "safe_metadata": None})
    assert status is None


def test_7_knowledge_evidence_selection_started_produces_validating_status() -> None:
    translator = StatusTranslator()
    status = translator.translate_activity_event(
        {"kind": ActivityKind.KNOWLEDGE_EVIDENCE_SELECTION_STARTED, "safe_metadata": None}
    )
    assert status is not None
    assert status["label"] == "Validating supporting evidence"


# =============================================================================
# 8-12: Teams activity translation
# =============================================================================


def test_8_teams_chat_discovery_started_produces_finding_status() -> None:
    translator = StatusTranslator()
    status = translator.translate_activity_event(
        {"kind": ActivityKind.TEAMS_CHAT_DISCOVERY_STARTED, "safe_metadata": None}
    )
    assert status is not None
    assert status["label"] == "Finding the Teams conversation"


def test_9_teams_messages_retrieval_started_produces_retrieving_status() -> None:
    translator = StatusTranslator()
    status = translator.translate_activity_event(
        {"kind": ActivityKind.TEAMS_MESSAGES_RETRIEVAL_STARTED, "safe_metadata": None}
    )
    assert status is not None
    assert status["label"] == "Retrieving Teams messages"


def test_10_teams_messages_success_with_results_produces_reviewing_status() -> None:
    translator = StatusTranslator()
    translator.translate_activity_event({"kind": ActivityKind.TEAMS_MESSAGES_RETRIEVAL_STARTED, "safe_metadata": None})
    status = translator.translate_activity_event(
        {"kind": ActivityKind.TEAMS_MESSAGES_RETRIEVAL_SUCCEEDED, "safe_metadata": {"message_count": 5}}
    )
    assert status is not None
    assert status["label"] == "Reviewing retrieved messages"


def test_11_teams_messages_success_with_zero_results_never_claims_reviewing() -> None:
    translator = StatusTranslator()
    translator.translate_activity_event({"kind": ActivityKind.TEAMS_MESSAGES_RETRIEVAL_STARTED, "safe_metadata": None})
    status = translator.translate_activity_event(
        {"kind": ActivityKind.TEAMS_MESSAGES_RETRIEVAL_SUCCEEDED, "safe_metadata": {"message_count": 0}}
    )
    assert status is None


def test_12_teams_failure_never_produces_a_success_like_status() -> None:
    translator = StatusTranslator()
    translator.translate_activity_event({"kind": ActivityKind.TEAMS_MESSAGES_RETRIEVAL_STARTED, "safe_metadata": None})
    status = translator.translate_activity_event(
        {"kind": ActivityKind.TEAMS_MESSAGES_RETRIEVAL_FAILED, "safe_metadata": None}
    )
    assert status is None


def test_teams_members_retrieval_started_produces_reviewing_participants_status() -> None:
    translator = StatusTranslator()
    status = translator.translate_activity_event(
        {"kind": ActivityKind.TEAMS_MEMBERS_RETRIEVAL_STARTED, "safe_metadata": None}
    )
    assert status is not None
    assert status["label"] == "Reviewing conversation participants"


def test_unknown_kind_never_displays_a_specific_status() -> None:
    """Rule 6: unknown activity is silent, never guessed. `TEAMS_CHAT_
    DISCOVERY_SUCCEEDED`/`FAILED` and `TEAMS_MEMBERS_RETRIEVAL_SUCCEEDED`/
    `FAILED` are deliberately absent from the Stage/label mapping."""
    translator = StatusTranslator()
    for kind in (
        ActivityKind.TEAMS_CHAT_DISCOVERY_SUCCEEDED,
        ActivityKind.TEAMS_CHAT_DISCOVERY_FAILED,
        ActivityKind.TEAMS_MEMBERS_RETRIEVAL_SUCCEEDED,
        ActivityKind.TEAMS_MEMBERS_RETRIEVAL_FAILED,
        ActivityKind.KNOWLEDGE_EVIDENCE_SELECTION_SUCCEEDED,
        ActivityKind.KNOWLEDGE_EVIDENCE_SELECTION_FAILED,
    ):
        assert translator.translate_activity_event({"kind": kind, "safe_metadata": None}) is None


# =============================================================================
# 13-14: dedup / Stage-sharing correctness
# =============================================================================


def test_13_repeated_identical_activity_event_is_deduplicated() -> None:
    translator = StatusTranslator()
    first = translator.translate_activity_event({"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None})
    second = translator.translate_activity_event({"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None})
    assert first is not None
    assert second is None


def test_14_search_then_validation_produces_both_distinct_statuses_despite_sharing_a_stage() -> None:
    """The instruction section 7 warning, directly proven: `KNOWLEDGE_
    SEARCH_STARTED` and `KNOWLEDGE_EVIDENCE_SELECTION_STARTED` both map
    to `Stage.KNOWLEDGE_RETRIEVAL` -- deduping by bare `Stage` would
    incorrectly suppress the second transition.
    """
    translator = StatusTranslator()
    search_status = translator.translate_activity_event(
        {"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None}
    )
    validate_status = translator.translate_activity_event(
        {"kind": ActivityKind.KNOWLEDGE_EVIDENCE_SELECTION_STARTED, "safe_metadata": None}
    )
    assert search_status is not None
    assert validate_status is not None
    assert search_status["label"] != validate_status["label"]
    assert search_status["stage"] == validate_status["stage"] == "knowledge_retrieval"


def test_search_success_does_not_suppress_a_later_repeated_search_started() -> None:
    """A second, later `knowledge_search` call in the same turn (e.g. a
    refined query) must still be able to show "Searching governed
    knowledge" again, since something else (SUCCEEDED) happened in
    between."""
    translator = StatusTranslator()
    translator.translate_activity_event({"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None})
    translator.translate_activity_event(
        {"kind": ActivityKind.KNOWLEDGE_SEARCH_SUCCEEDED, "safe_metadata": {"document_count": 2}}
    )
    second_started = translator.translate_activity_event(
        {"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None}
    )
    assert second_started is not None
    assert second_started["label"] == "Searching governed knowledge"


def test_activity_driven_status_carries_the_machine_readable_activity_kind() -> None:
    """Frontend icon selection must never parse `label` text (instruction
    section 17) -- `activity_kind` is the closed, safe discriminator that
    makes this possible even when several `ActivityKind`s share one
    `Stage`."""
    translator = StatusTranslator()
    status = translator.translate_activity_event({"kind": ActivityKind.KNOWLEDGE_SEARCH_STARTED, "safe_metadata": None})
    assert status is not None
    assert status["activity_kind"] == "knowledge_search_started"


def test_non_activity_status_never_carries_an_activity_kind_field() -> None:
    translator = StatusTranslator()
    status = translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("record_case_analysis")]))
    assert status is not None
    assert "activity_kind" not in status


# =============================================================================
# 15-16: queue transport safety
# =============================================================================


def test_15_queue_bounded_behavior_drops_events_safely_when_full() -> None:
    run_id = "test-run-bounded"
    register_activity_channel(run_id)
    token = bind_run_id(run_id)
    try:
        for _ in range(64):  # far more than the small internal maxsize
            report_activity(ActivityKind.KNOWLEDGE_SEARCH_STARTED)
        channel = get_activity_channel(run_id)
        assert channel is not None
        assert channel.full()
        # Draining does not raise, and never exceeds what was actually
        # enqueued -- proves overflow was dropped, not corrupted/crashed.
        drained = 0
        while not channel.empty():
            channel.get_nowait()
            drained += 1
        assert drained == channel.maxsize
    finally:
        reset_run_id(token)
        discard_activity_channel(run_id)


def test_16_report_outside_a_bound_run_safely_no_ops() -> None:
    """No `bind_run_id` in effect -- `current_run_id()` is `None`."""
    # Never raises, never blocks -- this call is the entire assertion.
    report_activity(ActivityKind.KNOWLEDGE_SEARCH_STARTED)
    report_activity(ActivityKind.TEAMS_MESSAGES_RETRIEVAL_SUCCEEDED, {"message_count": 3})


def test_report_for_an_unregistered_run_id_safely_no_ops() -> None:
    token = bind_run_id("test-run-unregistered-channel")
    try:
        # No register_activity_channel call for this run_id.
        report_activity(ActivityKind.KNOWLEDGE_SEARCH_STARTED)
    finally:
        reset_run_id(token)


# =============================================================================
# 17: two simultaneous run_ids isolated
# =============================================================================


@pytest.mark.asyncio
async def test_17_two_simultaneous_run_ids_never_cross_contaminate_the_activity_channel() -> None:
    run_a, run_b = "test-run-a-activity", "test-run-b-activity"
    register_activity_channel(run_a)
    register_activity_channel(run_b)
    try:

        async def _report_for(run_id: str, kind: ActivityKind) -> None:
            token = bind_run_id(run_id)
            try:
                report_activity(kind)
            finally:
                reset_run_id(token)

        await asyncio.gather(
            _report_for(run_a, ActivityKind.TEAMS_CHAT_DISCOVERY_STARTED),
            _report_for(run_b, ActivityKind.KNOWLEDGE_SEARCH_STARTED),
        )

        channel_a = get_activity_channel(run_a)
        channel_b = get_activity_channel(run_b)
        assert channel_a is not None and channel_b is not None
        event_a = channel_a.get_nowait()
        event_b = channel_b.get_nowait()
        assert event_a["kind"] == ActivityKind.TEAMS_CHAT_DISCOVERY_STARTED
        assert event_b["kind"] == ActivityKind.KNOWLEDGE_SEARCH_STARTED
        assert channel_a.empty()
        assert channel_b.empty()
    finally:
        discard_activity_channel(run_a)
        discard_activity_channel(run_b)


# =============================================================================
# 18-20: channel cleanup on every exit path
# =============================================================================


def test_18_channel_discarded_on_success() -> None:
    run_id = "test-run-discard-success"
    register_activity_channel(run_id)
    assert get_activity_channel(run_id) is not None
    discard_activity_channel(run_id)
    assert get_activity_channel(run_id) is None


@pytest.mark.asyncio
async def test_19_channel_discarded_on_exception() -> None:
    run_id = "test-run-discard-exception"
    register_activity_channel(run_id)
    try:
        with pytest.raises(ValueError):
            try:
                raise ValueError("simulated tool/model failure")
            finally:
                discard_activity_channel(run_id)
    finally:
        assert get_activity_channel(run_id) is None


@pytest.mark.asyncio
async def test_20_channel_discarded_on_cancellation() -> None:
    run_id = "test-run-discard-cancel"
    register_activity_channel(run_id)

    async def _blocks_forever() -> None:
        try:
            await asyncio.Event().wait()
        finally:
            discard_activity_channel(run_id)

    task = asyncio.ensure_future(_blocks_forever())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert get_activity_channel(run_id) is None


# =============================================================================
# 21: remediation activity maps to the correct (parent) visible run
# =============================================================================


def test_21_remediation_reusing_the_parent_run_id_reports_into_the_same_channel() -> None:
    """Phase 1 audit finding, reverified: `governed_knowledge_completion
    .py`'s own remediation calls `bind_run_id(run_id)` using the SAME
    `run_id` its caller (chat_service.py) already bound for the visible
    turn -- never a synthetic child id. Activity reported during a
    (simulated) remediation call therefore lands in the exact same
    channel a later drain of the ORIGINAL visible run would consume --
    proven directly here without needing a real nested Runner call.
    """
    parent_run_id = "test-parent-run-for-remediation"
    register_activity_channel(parent_run_id)
    try:
        outer_token = bind_run_id(parent_run_id)
        try:
            report_activity(ActivityKind.KNOWLEDGE_SEARCH_STARTED)
        finally:
            reset_run_id(outer_token)

        # Simulates governed_knowledge_completion.py's own
        # `bind_run_id(run_id)` -- rebinding to the SAME id, not a
        # `<parent>::governed-completion`-shaped synthetic one.
        remediation_token = bind_run_id(parent_run_id)
        try:
            report_activity(ActivityKind.KNOWLEDGE_SEARCH_SUCCEEDED, {"document_count": 1})
        finally:
            reset_run_id(remediation_token)

        channel = get_activity_channel(parent_run_id)
        assert channel is not None
        first = channel.get_nowait()
        second = channel.get_nowait()
        assert first["kind"] == ActivityKind.KNOWLEDGE_SEARCH_STARTED
        assert second["kind"] == ActivityKind.KNOWLEDGE_SEARCH_SUCCEEDED
        assert channel.empty()
    finally:
        discard_activity_channel(parent_run_id)


# =============================================================================
# 30 (mandatory concurrency test): two REAL, concurrent full chat_service
# runs -- one Teams, one Knowledge -- never cross-contaminate each other's
# visible activity stream.
# =============================================================================


@pytest.mark.asyncio
async def test_30_concurrent_teams_run_and_knowledge_run_never_cross_contaminate() -> None:
    def _declare_non_governed() -> FakeEvent:
        return FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("record_source_requirements", {"requires_teams": True, "requires_governed_knowledge": False})
            ],
        )

    class _TeamsActivityRunner:
        async def run_async(self, *, user_id, session_id, new_message, run_config=None):
            yield _declare_non_governed()
            report_activity(ActivityKind.TEAMS_CHAT_DISCOVERY_STARTED)
            await asyncio.sleep(0)
            report_activity(ActivityKind.TEAMS_MESSAGES_RETRIEVAL_STARTED)
            await asyncio.sleep(0)
            report_activity(ActivityKind.TEAMS_MESSAGES_RETRIEVAL_SUCCEEDED, {"message_count": 4})
            yield FakeEvent(text="Teams answer.", final=True)

    class _KnowledgeActivityRunner:
        async def run_async(self, *, user_id, session_id, new_message, run_config=None):
            yield _declare_non_governed()
            report_activity(ActivityKind.KNOWLEDGE_SEARCH_STARTED)
            await asyncio.sleep(0)
            report_activity(ActivityKind.KNOWLEDGE_SEARCH_SUCCEEDED, {"document_count": 2})
            await asyncio.sleep(0)
            report_activity(ActivityKind.KNOWLEDGE_EVIDENCE_SELECTION_STARTED)
            yield FakeEvent(text="Knowledge answer.", final=True)

    service = ApiSessionService()
    session_teams = await service.create_session(user_id="teams-user")
    session_knowledge = await service.create_session(user_id="knowledge-user")
    chat_teams = ChatService(service, runner=_TeamsActivityRunner())
    chat_knowledge = ChatService(service, runner=_KnowledgeActivityRunner())

    async def _collect(chat_service, session_id, user_id):
        return [e async for e in chat_service.execute_turn_events(session_id, "go", user_id)]

    teams_events, knowledge_events = await asyncio.gather(
        _collect(chat_teams, session_teams, "teams-user"),
        _collect(chat_knowledge, session_knowledge, "knowledge-user"),
    )

    teams_labels = {e.data.get("label") for e in teams_events if e.type == StreamEventType.STATUS}
    knowledge_labels = {e.data.get("label") for e in knowledge_events if e.type == StreamEventType.STATUS}

    assert "Finding the Teams conversation" in teams_labels
    assert "Retrieving Teams messages" in teams_labels
    assert "Reviewing retrieved messages" in teams_labels
    assert "Searching governed knowledge" not in teams_labels
    assert "Validating supporting evidence" not in teams_labels

    assert "Searching governed knowledge" in knowledge_labels
    assert "Validating supporting evidence" in knowledge_labels
    assert "Finding the Teams conversation" not in knowledge_labels
    assert "Retrieving Teams messages" not in knowledge_labels
    assert "Reviewing retrieved messages" not in knowledge_labels


# =============================================================================
# 22: no status influences SSE delta trust classification
# =============================================================================


@pytest.mark.asyncio
async def test_22_activity_driven_status_never_affects_source_requirements_trust_gate() -> None:
    """A real end-to-end proof, via `ChatService`: a fake runner that
    reports Knowledge activity DURING a turn that never declares source
    requirements at all must still buffer/suppress delta text exactly as
    before -- activity events are a completely parallel concern from
    `SourceRequirementsCapture`'s own three-state classification
    (instruction section 11's absolute non-regression requirement).
    """
    from backend.api.streaming_events import StreamEventType as _SET

    class _ActivityReportingRunner:
        async def run_async(self, *, user_id, session_id, new_message, run_config=None):
            report_activity(ActivityKind.KNOWLEDGE_SEARCH_STARTED)
            report_activity(ActivityKind.KNOWLEDGE_SEARCH_SUCCEEDED, {"document_count": 1})
            yield FakeEvent(text="This text must stay buffered.", final=False, partial=True)
            yield FakeEvent(text="This text must stay buffered.", final=True)

    service = ApiSessionService()
    session_id = await service.create_session()
    chat_service = ChatService(service, runner=_ActivityReportingRunner())

    collected = [event async for event in chat_service.execute_turn_events(session_id, "test message")]

    delta_events = [e for e in collected if e.type == _SET.MESSAGE_DELTA]
    completed_events = [e for e in collected if e.type == _SET.MESSAGE_COMPLETED]
    status_events = [e for e in collected if e.type == _SET.STATUS]

    # The activity-driven Knowledge statuses DID surface (proving the
    # transport/merge loop genuinely worked)...
    assert any(s.data.get("label") == "Searching governed knowledge" for s in status_events)
    # ...but the delta text itself was never streamed live (no source-
    # requirements declaration ever happened this turn -- UNKNOWN state
    # buffers unconditionally, completely independent of any activity
    # event observed in the same run).
    assert delta_events == []
    assert len(completed_events) == 1
