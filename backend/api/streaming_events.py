"""The public SLOPANOC streaming event model (Phase 4E, instruction
sections 15-16).

Never a passthrough of raw ADK `Event` objects -- see
activity_translator.py for the deterministic boundary that turns
observed ADK/tool runtime activity into these types. A small, closed set
of event types, one consistent envelope, JSON-safe, frontend-independent
of ADK internals.

FUTURE AG-UI COMPATIBILITY (instruction section 41): this event model
was designed with AG-UI's event-stream shape in mind (a typed `type`
discriminator + a payload, sequential, one event per meaningful runtime
occurrence) without taking an AG-UI dependency now. A future mapping
would be roughly: `run.started`/`run.completed` -> AG-UI's
`RUN_STARTED`/`RUN_FINISHED`; `message.delta` -> `TEXT_MESSAGE_CONTENT`;
`message.completed` -> the end of a `TEXT_MESSAGE_*` sequence;
`status`/`status.clear` -> AG-UI's generic `STATE_DELTA`/custom activity
events (AG-UI has no single built-in "one-line current activity" concept,
so this would likely become a custom AG-UI event type); `action.pending`
-> a custom AG-UI event carrying the same safe DTO; `error` ->
`RUN_ERROR`. Nothing here is coupled to this specific stream format
tightly enough to block that mapping later.

FUTURE MODEL ARMOR COMPATIBILITY (instruction section 42): every user-
facing text (`message.delta`/`message.completed`) and every status label
flows through this one module's event-construction helpers before
leaving the process. Phase 4H can insert a screening step at exactly one
seam -- immediately before `_EventSequencer.build`/`StreamEvent`
construction for these event types -- without changing this event
contract or any downstream consumer (the SSE route, `run_turn`, or a
future frontend).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class StreamEventType(str, Enum):
    RUN_STARTED = "run.started"
    STATUS = "status"
    STATUS_CLEAR = "status.clear"
    MESSAGE_DELTA = "message.delta"
    MESSAGE_COMPLETED = "message.completed"
    ACTION_PENDING = "action.pending"
    # Interaction-capability extension -- mirrors ACTION_PENDING exactly:
    # a small, closed, sanitized event carrying the session's active
    # `PendingSelectionDTO` (never raw candidate-list Markdown for the
    # frontend to parse, never a raw Teams chat id -- see
    # pending_selection.py).
    SELECTION_PENDING = "selection.pending"
    ERROR = "error"
    RUN_COMPLETED = "run.completed"
    # Expandable, sanitized run trace (pre-4H milestone): one event per
    # deterministically-observed, safe runtime milestone. NEVER model
    # chain-of-thought/hidden reasoning -- see run_trace.py's module
    # docstring for the allowlist boundary this always passes through.
    TRACE_STEP = "trace.step"


class Stage(str, Enum):
    """Stable machine-readable stage codes (instruction section 3) --
    the frontend keys behavior off `stage`, never off the human-readable
    `label` text. `EXTERNAL_RETRIEVAL` is reserved for Phase 5 (tickets/
    alarms/knowledge sources) -- not emitted by anything in this
    milestone, kept here so the taxonomy does not need to change shape
    when those sources are integrated. `RESPONSE_GENERATION` is defined
    for the same forward-compatibility reason but is not force-emitted
    every turn (instruction section 24: "do not enforce a fixed number"
    of status transitions) -- it is available for a future refinement to
    use, never a scripted requirement.
    """

    PROCESSING = "processing"
    CASE_CONTEXT = "case_context"
    EXTERNAL_RETRIEVAL = "external_retrieval"
    TEAMS_CONTEXT = "teams_context"
    EVIDENCE_PROCESSING = "evidence_processing"
    RECOMMENDATION = "recommendation"
    ACTION_PREPARATION = "action_preparation"
    RESPONSE_GENERATION = "response_generation"
    # Phase 2 (Runtime Activity Truthfulness): governed-knowledge activity
    # has no prior Stage of its own -- `KNOWLEDGE_RETRIEVAL` covers both
    # "Searching governed knowledge" and "Validating supporting evidence"
    # (distinct `ActivityKind`s, see activity_queue.py); a knowledge
    # search's own SUCCESS-with-results moment reuses the existing
    # `EVIDENCE_PROCESSING` stage below, exactly like the Teams evidence-
    # review moment already does -- "reviewing what came back" is one
    # concept regardless of source.
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"


class TraceCategory(str, Enum):
    """Closed taxonomy for `trace.step`'s `category` field -- a small,
    stable, machine-readable grouping (never an implementation class/tool
    name -- instruction section 10). Deliberately mirrors `Stage`'s own
    "stable code, human-readable label separately" shape, but is its own
    enum: trace categories describe a MILESTONE (a completed/observed
    fact), while `Stage` describes a CURRENT, ephemeral, overwritable
    activity -- the two are related but not 1:1 (one `Stage` visited
    during a run can produce zero, one, or more trace milestones; a run
    boundary like "response generated" has no corresponding `Stage` at
    all).
    """

    CONTEXT = "context"
    TEAMS = "teams"
    EVIDENCE = "evidence"
    CASE = "case"
    SELECTION = "selection"
    ACTION = "action"
    RESPONSE = "response"
    SYSTEM = "system"


class TraceStepStatus(str, Enum):
    """Closed vocabulary for one trace step's own outcome -- distinct
    from the run's overall `outcome` (`run.completed`'s `ok`/`error`): a
    single step can be a safe, sanitized failure (instruction section 42,
    "Could not retrieve Teams messages") inside an otherwise still-running
    or even still-successful turn.
    """

    COMPLETED = "completed"
    WARNING = "warning"
    FAILED = "failed"


def trace_step_data(
    step_id: str,
    category: TraceCategory,
    label: str,
    status: TraceStepStatus,
    safe_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Builds a `trace.step` event's `data` payload. `safe_metadata` is
    assumed ALREADY allowlist-sanitized by the caller (see
    `run_trace.py`'s `RunTraceRecorder`) -- this function does no
    filtering of its own; it only shapes the already-safe fields into the
    wire dict, omitting `safe_metadata` entirely when empty/`None` rather
    than sending an empty object.
    """
    data: dict[str, Any] = {
        "step_id": step_id,
        "category": category.value,
        "label": label,
        "status": status.value,
    }
    if safe_metadata:
        data["safe_metadata"] = safe_metadata
    return data


class StreamEvent(BaseModel):
    """One event envelope. `sequence` is monotonically increasing within
    one `run_id`, starting at 1. `timestamp` is always timezone-aware
    UTC. `data` is a small, JSON-safe dict -- never a raw ADK/SQLAlchemy
    object; every producer in this codebase builds it from already-safe,
    already-validated values (see activity_translator.py).
    """

    type: StreamEventType
    session_id: str
    run_id: str
    sequence: int
    timestamp: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class EventSequencer:
    """Generates one `run_id` (server-side, per instruction section 16)
    and stamps every event for that run with a strictly increasing
    `sequence` and a fresh UTC timestamp.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.run_id = str(uuid.uuid4())
        self._next_sequence = 1

    def build(self, event_type: StreamEventType, data: dict[str, Any]) -> StreamEvent:
        event = StreamEvent(
            type=event_type,
            session_id=self.session_id,
            run_id=self.run_id,
            sequence=self._next_sequence,
            timestamp=datetime.now(timezone.utc),
            data=data,
        )
        self._next_sequence += 1
        return event


def status_data(stage: Stage, label: str, activity_kind: Optional[str] = None) -> dict[str, Any]:
    """`presentation: "replace"` (instruction section 9) is the only
    value this milestone ever produces -- every `status` event replaces
    whatever was previously visible; there is no "append" mode. The field
    still exists explicitly (rather than being implied) so a future
    presentation mode does not require a breaking contract change.

    `activity_kind` (Phase 2, Runtime Activity Truthfulness): the plain
    `.value` string of the `ActivityKind` (activity_queue.py) that
    produced this status, when there is one -- omitted entirely (never
    `null`) for every pre-existing, non-activity-driven status (Case
    context, Recommendation, Action preparation, generic Processing).
    Exists ONLY so the frontend can pick a semantic icon deterministically
    (several distinct `ActivityKind`s intentionally share one `Stage` --
    see activity_translator.py's own dedup-signature docstring -- so
    `stage` alone is not always precise enough for icon selection); the
    frontend already keys all BEHAVIOR off `stage`, never off `label` or
    `activity_kind` text content, and this field is exactly as safe as
    `stage` itself (a closed, non-sensitive enum value, never raw tool
    data).
    """
    data: dict[str, Any] = {"stage": stage.value, "label": label, "presentation": "replace"}
    if activity_kind is not None:
        data["activity_kind"] = activity_kind
    return data


def format_sse(event: StreamEvent) -> str:
    """Proper SSE wire format (instruction section 35) -- never Python
    `repr`, never raw ADK serialization. `model_dump_json` already
    produces compact, JSON-safe output.
    """
    return f"event: {event.type.value}\ndata: {event.model_dump_json()}\n\n"
