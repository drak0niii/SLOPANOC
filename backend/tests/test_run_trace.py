"""Tests for backend/api/run_trace.py -- the central `trace.step` emitter
(expandable, sanitized run trace, pre-4H milestone).
"""
from __future__ import annotations

from backend.api.run_trace import RunTraceRecorder
from backend.api.streaming_events import EventSequencer, StreamEventType, TraceCategory, TraceStepStatus


def _recorder() -> RunTraceRecorder:
    return RunTraceRecorder(EventSequencer("session-1"))


def test_record_builds_a_trace_step_event() -> None:
    recorder = _recorder()
    event = recorder.record(TraceCategory.TEAMS, "Used the selected Teams conversation")
    assert event is not None
    assert event.type == StreamEventType.TRACE_STEP
    assert event.data["category"] == "teams"
    assert event.data["label"] == "Used the selected Teams conversation"
    assert event.data["status"] == "completed"


def test_each_step_gets_a_fresh_step_id() -> None:
    recorder = _recorder()
    first = recorder.record(TraceCategory.TEAMS, "Used the selected Teams conversation")
    second = recorder.record(TraceCategory.EVIDENCE, "Reviewed 3 retrieved messages")
    assert first is not None and second is not None
    assert first.data["step_id"] != second.data["step_id"]


def test_events_share_the_run_id_and_have_increasing_sequence() -> None:
    sequencer = EventSequencer("session-1")
    recorder = RunTraceRecorder(sequencer)
    first = recorder.record(TraceCategory.TEAMS, "Used the selected Teams conversation")
    second = recorder.record(TraceCategory.EVIDENCE, "Reviewed 3 retrieved messages")
    assert first.run_id == second.run_id == sequencer.run_id
    assert second.sequence > first.sequence


# --- Deduplication (instruction sections 23/49) -----------------------------


def test_consecutive_identical_signature_is_deduplicated() -> None:
    recorder = _recorder()
    first = recorder.record(TraceCategory.TEAMS, "Used the selected Teams conversation")
    second = recorder.record(TraceCategory.TEAMS, "Used the selected Teams conversation")
    assert first is not None
    assert second is None


def test_a_different_label_after_dedup_emits_again() -> None:
    recorder = _recorder()
    recorder.record(TraceCategory.TEAMS, "Used the selected Teams conversation")
    refined = recorder.record(TraceCategory.EVIDENCE, "Reviewed 5 retrieved messages")
    assert refined is not None


def test_a_repeated_step_after_a_different_one_in_between_emits_again() -> None:
    """Dedup only ever compares against the immediately previous signature
    -- a genuinely repeated milestone later in the same run (e.g. a
    second, later Teams call) is never silently suppressed.
    """
    recorder = _recorder()
    recorder.record(TraceCategory.TEAMS, "Used the selected Teams conversation")
    recorder.record(TraceCategory.EVIDENCE, "Reviewed 5 retrieved messages")
    third = recorder.record(TraceCategory.TEAMS, "Used the selected Teams conversation")
    assert third is not None


def test_dedup_considers_safe_metadata_not_just_label() -> None:
    recorder = _recorder()
    first = recorder.record(
        TraceCategory.EVIDENCE, "Reviewed retrieved messages", safe_metadata={"message_count": 5}
    )
    second = recorder.record(
        TraceCategory.EVIDENCE, "Reviewed retrieved messages", safe_metadata={"message_count": 7}
    )
    assert first is not None
    assert second is not None  # different count -- a genuinely different fact, not a duplicate


# --- Allowlist sanitization (instruction section 40) ------------------------


def test_unknown_metadata_keys_are_dropped_not_forwarded() -> None:
    recorder = _recorder()
    event = recorder.record(
        TraceCategory.TEAMS,
        "Used the selected Teams conversation",
        safe_metadata={"message_count": 3, "chat_id": "abc-123", "session_token": "shh"},
    )
    assert event is not None
    assert event.data["safe_metadata"] == {"message_count": 3}
    assert "chat_id" not in event.data.get("safe_metadata", {})
    assert "chat_id" not in str(event.data)
    assert "shh" not in str(event.data)


def test_non_integer_metadata_values_are_dropped() -> None:
    recorder = _recorder()
    event = recorder.record(
        TraceCategory.TEAMS,
        "Used the selected Teams conversation",
        safe_metadata={"message_count": "forty-two", "evidence_count": True},
    )
    assert event is not None
    assert "safe_metadata" not in event.data


def test_empty_metadata_is_omitted_entirely() -> None:
    recorder = _recorder()
    event = recorder.record(TraceCategory.TEAMS, "Used the selected Teams conversation", safe_metadata={})
    assert event is not None
    assert "safe_metadata" not in event.data


def test_warning_and_failed_statuses_are_carried_through() -> None:
    recorder = _recorder()
    event = recorder.record(TraceCategory.TEAMS, "No matching Teams content was found", TraceStepStatus.WARNING)
    assert event is not None
    assert event.data["status"] == "warning"

    failed = recorder.record(TraceCategory.TEAMS, "Could not complete the Teams request", TraceStepStatus.FAILED)
    assert failed is not None
    assert failed.data["status"] == "failed"
