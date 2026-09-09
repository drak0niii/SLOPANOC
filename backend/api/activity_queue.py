"""Run-scoped, ephemeral internal activity transport (Phase 2, Runtime
Activity Truthfulness milestone).

WHY THIS EXISTS: Phase 1's audit proved, from installed ADK source
(`google/adk/tools/agent_tool.py::AgentTool.run_async`), that Incident
Manager's own internal tool calls (`knowledge_search`,
`knowledge_select_evidence`, `teams_list_chats`, `teams_get_messages`, ...)
are consumed ENTIRELY inside the nested `AgentTool`/`MultimodalAgentTool`
call and never reach the outer Team Manager Runner's own event stream --
`chat_service.py`'s main loop structurally cannot observe them through
ADK events alone. This module is the one, narrow side-channel that lets
those real, already-happening tool calls report a closed, safe activity
fact that `chat_service.py` can surface as an ephemeral status -- it is
observability only, never a second orchestration mechanism, never a new
agent, never a source of trusted data.

TOOLS NEVER REPORT LABEL STRINGS. `report_activity` accepts only a closed
`ActivityKind` plus optional, allowlisted-only numeric metadata -- see
`_ALLOWED_ACTIVITY_METADATA_KEYS`. Turning an `ActivityKind` into a
user-facing `Stage`/label is `activity_translator.py`'s job alone (the
SAME "one centralized translation layer" discipline
`StatusTranslator`/`RunTraceTranslator` already enforce for ADK events).

RUN CORRELATION: `report_activity` derives the run identity exclusively
from `backend.api.turn_context.current_run_id()` -- the SAME ContextVar
`backend/tools/knowledge/runtime.py` already uses for run-scoped evidence
state. Callers cannot supply/spoof a different run_id. A call made outside
a bound turn (no `current_run_id()`) or for a run_id nobody registered a
channel for safely no-ops -- activity reporting can never fail or block
the real tool operation it observes.

THREAD SAFETY: `asyncio.Queue` is safe to use from any code running on the
SAME thread as the owning event loop. Verified against installed ADK
1.33.0 (`google/adk/tools/function_tool.py::FunctionTool._invoke_callable`):
a sync tool function (`teams_list_chats`, `teams_get_messages`) is called
DIRECTLY (`return target(**args_to_call)`), never via
`asyncio.to_thread`/an executor -- so a `report_activity` call from inside
either of those functions runs on the same thread as the queue. The one
exception in this codebase is `teams_get_members`, invoked via
`asyncio.to_thread(teams_get_members, chat_id)` from
`backend/api/source_reference.py` -- a genuine, different worker thread.
`teams_get_members` itself therefore NEVER calls `report_activity`;
instead the AWAITING caller (on the event-loop thread, before/after the
`asyncio.to_thread` call) reports START/SUCCEEDED/FAILED.

BOUNDED, NEVER BLOCKING: `report_activity` uses `put_nowait` and silently
drops the event on `asyncio.QueueFull` -- activity UI is observability,
never business logic; a full queue (which the small `_QUEUE_MAXSIZE`
below makes very unlikely for one turn's realistic activity volume) must
never stall or fail the actual Knowledge/Teams operation being observed.

NO DATABASE, NO PERSISTENCE, NO NEW AGENT. Pure in-process, per-run
in-memory state, registered before the turn's Runner starts and always
discarded in `chat_service.py`'s own existing `finally` block (the same
one that already calls `discard_knowledge_run_evidence_state`/
`reset_run_id`) -- success, exception, and `asyncio.CancelledError` all
reach it identically.
"""
from __future__ import annotations

import asyncio
from enum import Enum
from typing import Optional, TypedDict

from backend.api.turn_context import current_run_id

_QUEUE_MAXSIZE = 16
"""One turn realistically produces a handful of activity events (a
START/SUCCEEDED pair per real tool call, at most a few tool calls per
turn) -- generous headroom without allowing unbounded growth."""


class ActivityKind(str, Enum):
    """Closed vocabulary of observable runtime activity boundaries.
    Only boundaries that genuinely exist in current tool/runtime code --
    never a fabricated lifecycle phase invented merely for UX (Phase 2
    instruction section 3).
    """

    KNOWLEDGE_SEARCH_STARTED = "knowledge_search_started"
    KNOWLEDGE_SEARCH_SUCCEEDED = "knowledge_search_succeeded"
    KNOWLEDGE_SEARCH_FAILED = "knowledge_search_failed"

    KNOWLEDGE_EVIDENCE_SELECTION_STARTED = "knowledge_evidence_selection_started"
    KNOWLEDGE_EVIDENCE_SELECTION_SUCCEEDED = "knowledge_evidence_selection_succeeded"
    KNOWLEDGE_EVIDENCE_SELECTION_FAILED = "knowledge_evidence_selection_failed"

    TEAMS_CHAT_DISCOVERY_STARTED = "teams_chat_discovery_started"
    TEAMS_CHAT_DISCOVERY_SUCCEEDED = "teams_chat_discovery_succeeded"
    TEAMS_CHAT_DISCOVERY_FAILED = "teams_chat_discovery_failed"

    TEAMS_MESSAGES_RETRIEVAL_STARTED = "teams_messages_retrieval_started"
    TEAMS_MESSAGES_RETRIEVAL_SUCCEEDED = "teams_messages_retrieval_succeeded"
    TEAMS_MESSAGES_RETRIEVAL_FAILED = "teams_messages_retrieval_failed"

    TEAMS_MEMBERS_RETRIEVAL_STARTED = "teams_members_retrieval_started"
    TEAMS_MEMBERS_RETRIEVAL_SUCCEEDED = "teams_members_retrieval_succeeded"
    TEAMS_MEMBERS_RETRIEVAL_FAILED = "teams_members_retrieval_failed"


# Deliberately the SAME closed set of safe, bounded numeric fields
# `backend/api/run_trace.py`'s `_ALLOWED_METADATA_KEYS` already uses for
# `trace.step` -- one allowlist discipline, reused, not reinvented.
_ALLOWED_ACTIVITY_METADATA_KEYS = frozenset(
    {"document_count", "evidence_count", "candidate_count", "message_count", "member_count"}
)


class ActivityEvent(TypedDict):
    kind: ActivityKind
    safe_metadata: Optional[dict[str, int]]


def _sanitize_metadata(safe_metadata: Optional[dict[str, int]]) -> Optional[dict[str, int]]:
    if not safe_metadata:
        return None
    sanitized = {
        key: value
        for key, value in safe_metadata.items()
        if key in _ALLOWED_ACTIVITY_METADATA_KEYS and isinstance(value, int) and not isinstance(value, bool)
    }
    return sanitized or None


_channels: dict[str, "asyncio.Queue[ActivityEvent]"] = {}


def register_activity_channel(run_id: str) -> None:
    """Creates this turn's bounded queue -- called once, at the same call
    site `bind_run_id`/the knowledge-evidence run state are established
    (`chat_service.py`), before the turn's Runner starts.
    """
    _channels[run_id] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)


def discard_activity_channel(run_id: str) -> None:
    """Idempotent -- safe to call even if no channel was ever registered
    for this `run_id` (e.g. a run that failed before registration)."""
    _channels.pop(run_id, None)


def get_activity_channel(run_id: str) -> "Optional[asyncio.Queue[ActivityEvent]]":
    """Read-only lookup for the consumer side (`chat_service.py`'s own
    merge loop) -- never used by tool/runtime reporters, which always go
    through `report_activity` instead.
    """
    return _channels.get(run_id)


def report_activity(kind: ActivityKind, safe_metadata: Optional[dict[str, int]] = None) -> None:
    """Best-effort, non-blocking, never-raising activity report. Derives
    the run identity exclusively from `current_run_id()` -- a caller can
    never target a different run's channel. Safely no-ops when:
      - called outside a bound visible turn (`current_run_id()` is
        `None` -- e.g. a standalone `adk run`/test invocation), or
      - no channel was registered for the current run_id (e.g. this
        function is reached before registration, or after discard), or
      - the channel is full (activity is observability, never load-
        bearing -- silently dropped, never blocks the real tool call).
    """
    run_id = current_run_id()
    if run_id is None:
        return
    queue = _channels.get(run_id)
    if queue is None:
        return
    event: ActivityEvent = {"kind": kind, "safe_metadata": _sanitize_metadata(safe_metadata)}
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        pass
