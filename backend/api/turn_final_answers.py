"""Authoritative final assistant response persistence.

THE PROBLEM THIS SOLVES:
When post-generation remediation (troubleshooting guidance override,
governed-knowledge completion remediation) replaces the model's initial
output, `chat_service.py` transmits the corrected response to the user via
the `MESSAGE_COMPLETED` SSE payload. However, the raw ADK model events stored
in `session.events` retain the original pre-remediation text for auditability.
Without a dedicated persistence mechanism for the authoritative final answer,
`session_history_service.py` was forced to reconstruct transcripts exclusively
from raw ADK events, causing the user interface to revert to un-remediated
content upon browser refresh or chat reload.

ARCHITECTURAL ALIGNMENT:
Modeled directly after `backend/api/turn_source_references.py`.
The authoritative final text is persisted in `session.state` under the plain
key `TURN_FINAL_ANSWERS_STATE_KEY = "turn_final_answers"`, mapped by ADK
`invocation_id` (`turn_id`).

WHY THIS SURVIVES REWIND FOR FREE:
Rewind reversal in Google ADK (`Runner._compute_state_delta_for_rewind`)
operates by replaying `state_delta` updates in chronological order up to the
rewind point. Because `build_turn_final_answers_delta` writes the full
accumulated dictionary on each turn, ADK's native rewind mechanism automatically
restores `session.state[TURN_FINAL_ANSWERS_STATE_KEY]` to its state prior to the
rewound turn, with zero custom database migrations or schema adjustments.

IMMUTABILITY OF AUDIT LOGS:
Raw ADK events in `session.events` remain completely untouched and immutable.
"""
from __future__ import annotations

from typing import Any, Optional

TURN_FINAL_ANSWERS_STATE_KEY = "turn_final_answers"
"""Plain session-state key holding the authoritative final assistant response
for each turn, keyed by the stable ADK invocation_id (`turn_id`).
Value shape: `{turn_id: "<authoritative final response text>"}`.
"""


def build_turn_final_answers_delta(
    existing: Any,
    turn_id: str,
    final_text: str,
) -> dict[str, str]:
    """Constructs the full updated dictionary to persist for
    `TURN_FINAL_ANSWERS_STATE_KEY`.

    `existing`: The current raw value of `session.state.get(TURN_FINAL_ANSWERS_STATE_KEY)`.
    Tolerates None, non-dict, or malformed data defensively without raising.

    `turn_id`: The stable ADK `invocation_id` for the turn.

    `final_text`: The authoritative final assistant response text.

    Idempotent: Repeated invocations with the same `turn_id` and `final_text`
    produce an identical state dictionary.
    """
    if not isinstance(turn_id, str) or not turn_id:
        return existing if isinstance(existing, dict) else {}

    base: dict[str, str] = dict(existing) if isinstance(existing, dict) else {}
    base[turn_id] = str(final_text) if final_text is not None else ""
    return base


def resolve_turn_final_answer(state: Any, turn_id: str) -> Optional[str]:
    """Resolves the authoritative final response text for a given `turn_id` from
    session state.

    Returns `None` if the state key is missing, if no entry exists for `turn_id`,
    or if the stored value is malformed. Callers fall back to raw ADK event
    extraction (`turn.final_text`) for historical turns created prior to this
    persistence mechanism.
    """
    if not hasattr(state, "get") or not isinstance(turn_id, str):
        return None

    all_answers = state.get(TURN_FINAL_ANSWERS_STATE_KEY)
    if not isinstance(all_answers, dict):
        return None

    answer = all_answers.get(turn_id)
    if isinstance(answer, str):
        return answer

    return None


def project_authoritative_answers_to_contents(
    callback_context: Any,
    llm_request: Any,
) -> None:
    """Projects authoritative final answers into the current LLM request contents.

    This ensures that subsequent ADK model turns receive the authoritative,
    remediated answer for earlier turns rather than unverified initial model
    text, fulfilling Requirement 1 while preserving raw ADK events in
    `session.events` completely intact and immutable for auditability.
    """
    invocation_context = getattr(callback_context, "_invocation_context", None)
    if invocation_context is None:
        return

    session = getattr(invocation_context, "session", None)
    if session is None or not getattr(session, "events", None):
        return

    state = getattr(session, "state", None)
    if not state or not hasattr(state, "get"):
        return

    turn_final_answers = state.get(TURN_FINAL_ANSWERS_STATE_KEY)
    if not isinstance(turn_final_answers, dict) or not turn_final_answers:
        return

    contents = getattr(llm_request, "contents", None)
    if not contents or not isinstance(contents, list):
        return

    agent = getattr(invocation_context, "agent", None)
    agent_name = getattr(agent, "name", "") if agent else ""
    current_branch = getattr(invocation_context, "branch", None)

    try:
        from google.adk.flows.llm_flows.contents import (
            _is_other_agent_reply,
            _present_other_agent_message,
            _process_compaction_events,
            _rearrange_events_for_async_function_responses_in_history,
            _rearrange_events_for_latest_function_response,
            _should_include_event_in_context,
        )
        from google.genai import types
    except ImportError:
        return

    # Reproduce ADK contents construction pipeline to map contents to events
    rewind_filtered_events = []
    i = len(session.events) - 1
    while i >= 0:
        event = session.events[i]
        if event.actions and event.actions.rewind_before_invocation_id:
            rewind_invocation_id = event.actions.rewind_before_invocation_id
            for j in range(0, i, 1):
                if session.events[j].invocation_id == rewind_invocation_id:
                    i = j
                    break
        else:
            rewind_filtered_events.append(event)
        i -= 1
    rewind_filtered_events.reverse()

    raw_filtered_events = [
        e
        for e in rewind_filtered_events
        if _should_include_event_in_context(current_branch, e)
    ]
    has_compaction_events = any(
        e.actions and e.actions.compaction for e in raw_filtered_events
    )
    events_to_process = (
        _process_compaction_events(raw_filtered_events)
        if has_compaction_events
        else raw_filtered_events
    )

    filtered_events = []
    for i in range(len(events_to_process)):
        event = events_to_process[i]
        if _is_other_agent_reply(agent_name, event):
            if converted_event := _present_other_agent_message(event):
                filtered_events.append(converted_event)
        else:
            filtered_events.append(event)

    result_events = _rearrange_events_for_latest_function_response(filtered_events)
    result_events = _rearrange_events_for_async_function_responses_in_history(
        result_events
    )

    event_content_indices = [e for e in result_events if getattr(e, "content", None)]

    if len(event_content_indices) != len(contents):
        return

    # Find the last model text event for each invocation_id in turn_final_answers
    last_model_text_idx_by_inv: dict[str, int] = {}
    for c_idx, event in enumerate(event_content_indices):
        inv_id = getattr(event, "invocation_id", None)
        if not inv_id or inv_id not in turn_final_answers:
            continue
        content = contents[c_idx]
        if getattr(content, "role", None) == "model" and any(
            bool(getattr(p, "text", None)) for p in (getattr(content, "parts", []) or [])
        ):
            last_model_text_idx_by_inv[inv_id] = c_idx

    for inv_id, c_idx in last_model_text_idx_by_inv.items():
        authoritative_text = turn_final_answers[inv_id]
        if authoritative_text is not None:
            content = contents[c_idx]
            new_parts = []
            replaced = False
            for p in getattr(content, "parts", []):
                if getattr(p, "text", None) is not None and not replaced:
                    new_parts.append(types.Part(text=authoritative_text))
                    replaced = True
                elif getattr(p, "text", None) is not None and replaced:
                    pass
                else:
                    new_parts.append(p)
            if not replaced:
                new_parts.append(types.Part(text=authoritative_text))
            content.parts = new_parts
