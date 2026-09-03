"""The expandable, sanitized run trace's central emitter (pre-4H
milestone).

`RunTraceRecorder` is the ONE place in this backend that turns a
candidate trace milestone (already deterministically detected by
`activity_translator.RunTraceTranslator`, or supplied directly by
`chat_service.py` at a few fixed boundary points -- run start of a
linked Case, selection preparation, response generated/failed) into an
actual `trace.step` `StreamEvent`. Mirrors `EventSequencer`'s own
"one small class owns exactly one cross-cutting concern" shape.

ALLOWLIST, NOT BLOCKLIST (instruction section 40): `_ALLOWED_METADATA_KEYS`
is a closed set of small, already-safe numeric counts. Any key not in this
set is silently dropped -- never forwarded "unless it looks dangerous".
This is deliberately the opposite of scanning for banned substrings
("token", "password", ...): nothing crosses this boundary unless it was
explicitly enumerated here as safe, so a future caller that accidentally
passes something sensitive (a raw id, a URL, a name) can never leak it
through this recorder by omission.

DEDUPLICATION (instruction sections 23/49): consecutive calls that would
produce the exact same `(category, label, status, safe_metadata)` tuple
collapse into a single emitted step -- guards against the same underlying
milestone surfacing twice because of an intermediate ADK event shape,
without suppressing a genuinely distinct repeat later in the same run
(e.g. a second, later call to `incident_manager` in the same turn that
legitimately reviews a different message count again).
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from backend.api.streaming_events import (
    EventSequencer,
    StreamEvent,
    StreamEventType,
    TraceCategory,
    TraceStepStatus,
    trace_step_data,
)

_ALLOWED_METADATA_KEYS = frozenset(
    {
        "message_count",
        "member_count",
        "evidence_count",
        "candidate_count",
        "document_count",
    }
)


def _sanitize_metadata(safe_metadata: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not safe_metadata:
        return None
    sanitized = {
        key: value
        for key, value in safe_metadata.items()
        if key in _ALLOWED_METADATA_KEYS and isinstance(value, int) and not isinstance(value, bool)
    }
    return sanitized or None


class RunTraceRecorder:
    """One instance per run (constructed alongside the run's own
    `EventSequencer` in `chat_service._run_turn_events`, exactly like
    `StatusTranslator`/`RunTraceTranslator`) -- `_last_signature` dedup
    state is therefore naturally scoped to a single run and never leaks
    across runs/sessions.
    """

    def __init__(self, sequencer: EventSequencer) -> None:
        self._sequencer = sequencer
        self._last_signature: Optional[tuple[Any, ...]] = None

    def record(
        self,
        category: TraceCategory,
        label: str,
        status: TraceStepStatus = TraceStepStatus.COMPLETED,
        safe_metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[StreamEvent]:
        sanitized_metadata = _sanitize_metadata(safe_metadata)
        signature = (
            category.value,
            label,
            status.value,
            tuple(sorted(sanitized_metadata.items())) if sanitized_metadata else None,
        )
        if signature == self._last_signature:
            return None
        self._last_signature = signature

        step_id = str(uuid.uuid4())
        data = trace_step_data(step_id, category, label, status, sanitized_metadata)
        return self._sequencer.build(StreamEventType.TRACE_STEP, data)
