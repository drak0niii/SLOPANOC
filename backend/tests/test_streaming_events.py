"""Tests for backend/api/streaming_events.py -- the public SLOPANOC event
model (instruction section 43).
"""
from __future__ import annotations

import json
from datetime import timezone

from backend.api.streaming_events import (
    EventSequencer,
    Stage,
    StreamEventType,
    TraceCategory,
    TraceStepStatus,
    format_sse,
    status_data,
    trace_step_data,
)


def test_all_valid_event_types_exist() -> None:
    expected = {
        "run.started",
        "status",
        "status.clear",
        "message.delta",
        "message.completed",
        "action.pending",
        # Interaction-capability extension.
        "selection.pending",
        "error",
        "run.completed",
        # Expandable, sanitized run trace (pre-4H milestone).
        "trace.step",
    }
    assert {t.value for t in StreamEventType} == expected


def test_run_id_is_server_generated_and_unique_per_sequencer() -> None:
    a = EventSequencer("session-1")
    b = EventSequencer("session-1")
    assert a.run_id
    assert a.run_id != b.run_id


def test_sequence_is_monotonically_increasing_within_a_run() -> None:
    sequencer = EventSequencer("session-1")
    events = [sequencer.build(StreamEventType.RUN_STARTED, {}) for _ in range(5)]
    assert [e.sequence for e in events] == [1, 2, 3, 4, 5]


def test_sequence_starts_at_one() -> None:
    sequencer = EventSequencer("session-1")
    event = sequencer.build(StreamEventType.RUN_STARTED, {})
    assert event.sequence == 1


def test_timestamps_are_timezone_aware_utc() -> None:
    sequencer = EventSequencer("session-1")
    event = sequencer.build(StreamEventType.STATUS, {})
    assert event.timestamp.tzinfo is not None
    assert event.timestamp.utcoffset().total_seconds() == 0
    assert event.timestamp.astimezone(timezone.utc) == event.timestamp


def test_event_is_json_serializable() -> None:
    sequencer = EventSequencer("session-1")
    event = sequencer.build(StreamEventType.STATUS, status_data(Stage.PROCESSING, "Processing your request"))
    dumped = event.model_dump_json()
    parsed = json.loads(dumped)
    assert parsed["type"] == "status"
    assert parsed["data"]["stage"] == "processing"


def test_envelope_has_exactly_the_expected_fields() -> None:
    sequencer = EventSequencer("session-1")
    event = sequencer.build(StreamEventType.RUN_STARTED, {})
    assert set(event.model_dump()) == {"type", "session_id", "run_id", "sequence", "timestamp", "data"}


def test_no_raw_adk_or_internal_fields_can_appear_in_the_envelope() -> None:
    """Structural guarantee: `StreamEvent` has no field for anything
    ADK-internal (agent name, tool_context, raw Content object) -- `data`
    is the only place per-event detail lives, and every producer builds
    it from already-safe primitives (see activity_translator.py).
    """
    from backend.api.streaming_events import StreamEvent

    fields = set(StreamEvent.model_fields)
    for forbidden in ("agent", "tool_context", "content", "adk_event", "invocation_context"):
        assert forbidden not in fields


def test_status_data_always_uses_replace_presentation() -> None:
    data = status_data(Stage.TEAMS_CONTEXT, "Reviewing the selected Teams conversation")
    assert data["presentation"] == "replace"


def test_status_data_carries_stable_stage_and_label() -> None:
    data = status_data(Stage.CASE_CONTEXT, "Preparing case context")
    assert data["stage"] == "case_context"
    assert data["label"] == "Preparing case context"


def test_format_sse_uses_proper_wire_format_not_python_repr() -> None:
    sequencer = EventSequencer("session-1")
    event = sequencer.build(StreamEventType.STATUS_CLEAR, {})
    wire = format_sse(event)

    assert wire.startswith("event: status.clear\n")
    assert "data: " in wire
    assert wire.endswith("\n\n")
    assert "StreamEvent(" not in wire  # no Python repr/dataclass-style output

    data_line = [line for line in wire.splitlines() if line.startswith("data: ")][0]
    parsed = json.loads(data_line[len("data: ") :])
    assert parsed["type"] == "status.clear"


def test_stage_taxonomy_matches_the_documented_set() -> None:
    expected = {
        "processing",
        "case_context",
        "external_retrieval",
        "teams_context",
        "evidence_processing",
        "recommendation",
        "action_preparation",
        "response_generation",
        # Phase 2 (Runtime Activity Truthfulness): governed-knowledge
        # activity had no Stage of its own before this milestone.
        "knowledge_retrieval",
    }
    assert {s.value for s in Stage} == expected


# --- Run trace schema (pre-4H milestone) ------------------------------------


def test_trace_category_taxonomy_matches_the_documented_set() -> None:
    expected = {"context", "teams", "evidence", "case", "selection", "action", "response", "system"}
    assert {c.value for c in TraceCategory} == expected


def test_trace_step_status_taxonomy_is_closed() -> None:
    assert {s.value for s in TraceStepStatus} == {"completed", "warning", "failed"}


def test_trace_step_data_carries_the_expected_fields() -> None:
    data = trace_step_data(
        "step-1", TraceCategory.TEAMS, "Used the selected Teams conversation", TraceStepStatus.COMPLETED
    )
    assert data == {
        "step_id": "step-1",
        "category": "teams",
        "label": "Used the selected Teams conversation",
        "status": "completed",
    }


def test_trace_step_data_includes_safe_metadata_only_when_present() -> None:
    with_metadata = trace_step_data(
        "step-1",
        TraceCategory.EVIDENCE,
        "Reviewed 42 retrieved messages",
        TraceStepStatus.COMPLETED,
        safe_metadata={"message_count": 42},
    )
    assert with_metadata["safe_metadata"] == {"message_count": 42}

    without_metadata = trace_step_data(
        "step-2", TraceCategory.RESPONSE, "Generated the response", TraceStepStatus.COMPLETED
    )
    assert "safe_metadata" not in without_metadata

    empty_metadata = trace_step_data(
        "step-3", TraceCategory.RESPONSE, "Generated the response", TraceStepStatus.COMPLETED, safe_metadata={}
    )
    assert "safe_metadata" not in empty_metadata


def test_trace_step_event_is_json_serializable() -> None:
    sequencer = EventSequencer("session-1")
    event = sequencer.build(
        StreamEventType.TRACE_STEP,
        trace_step_data("step-1", TraceCategory.RESPONSE, "Generated the response", TraceStepStatus.COMPLETED),
    )
    parsed = json.loads(event.model_dump_json())
    assert parsed["type"] == "trace.step"
    assert parsed["data"]["category"] == "response"
