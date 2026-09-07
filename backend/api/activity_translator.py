"""Deterministic translation from observed ADK/tool runtime activity into
safe, user-facing SLOPANOC status data (Phase 4E, instruction sections
4/5/19/20/21/22/23).

BOUNDARY: this module is the ONLY place that inspects raw ADK `Event`
objects (function calls/responses, partial text) to decide what the
current activity status should say. It never receives, and never needs,
the user's own message text -- every decision is based on WHICH known
tool was called and WHAT its already-schema-validated structured result
contains, never on keyword/regex matching of anything the user typed
(instruction: "Do NOT derive progress from the user's wording.").

KNOWN CAPABILITIES ONLY: `_STAGE_FOR_TOOL` is a closed, explicit mapping
from the two tools team_manager can directly call
(`incident_manager`, `record_case_analysis`) to a stage -- an unknown
tool name (should one ever be added) safely falls through to the generic
`PROCESSING` stage rather than crashing or leaking an internal name.
Internal agent implementation details (that "incident_manager" is an
`AgentTool`, that Team Manager -> AgentTool -> Incident Manager is the
architecture) are never surfaced in a label -- see `_TOOL_CALL_LABELS`/
`_TOOL_CALL_STAGES` below, which describe CAPABILITIES ("reviewing the
selected Teams conversation"), never internal names ("calling
incident_manager").

REFINEMENT AFTER RESPONSE (instruction section 22): a function CALL
produces a generic stage label (we do not yet know the outcome); the
matching function RESPONSE, once it arrives, may refine that into a more
specific label using only already-validated structured fields (e.g. an
`evidence` list's length, or a "proposed"/"executed" outcome) -- never
raw tool arguments, never a raw tool response dict.

DEDUPLICATION (instruction section 23): `StatusTranslator` is stateful
across one run -- `translate_event` returns `None` (meaning: do not emit
a new status) whenever the newly-derived stage is identical to the last
one actually emitted.
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict

from backend.api.streaming_events import Stage, TraceCategory, TraceStepStatus

_INCIDENT_MANAGER_TOOL_NAME = "incident_manager"
_RECORD_CASE_ANALYSIS_TOOL_NAME = "record_case_analysis"

_TOOL_CALL_STAGES: dict[str, Stage] = {
    _INCIDENT_MANAGER_TOOL_NAME: Stage.TEAMS_CONTEXT,
    _RECORD_CASE_ANALYSIS_TOOL_NAME: Stage.RECOMMENDATION,
}

_TOOL_CALL_LABELS: dict[str, str] = {
    _INCIDENT_MANAGER_TOOL_NAME: "Reviewing the selected Teams conversation",
    _RECORD_CASE_ANALYSIS_TOOL_NAME: "Preparing case analysis",
}

# `kind` is a closed, already-validated vocabulary (see
# backend/cases/schemas.py's AGENT_ANALYSIS_KINDS) -- safe to use verbatim
# in a label because it can only ever be one of these three known values,
# never arbitrary text.
_CASE_ANALYSIS_KIND_LABELS: dict[str, str] = {
    "hypothesis": "Preparing a hypothesis",
    "recommendation": "Preparing a recommendation",
    "open_question": "Noting an open question",
}


def _is_aggregated(event: Any) -> bool:
    """Only the complete, aggregated function call/response event should
    drive a status -- partial argument-streaming chunks are ignored
    (instruction: never surface progressive function-call-argument
    streaming; ADK's own `RunConfig.StreamingMode.SSE` docs mark those
    `event.partial=True`).
    """
    return not getattr(event, "partial", False)


def translate_case_context_status(case_title: Optional[str]) -> dict[str, Any]:
    """Emitted once, deterministically, BEFORE the agent turn starts, only
    when the session's `active_case_id` state hint is present (instruction
    section 18) -- never for a session with no linked Case. `case_title`
    is `None` when the title could not be safely resolved (e.g. the Case
    lookup itself failed) -- the label degrades to a generic phrase rather
    than omitting the status or guessing.
    """
    from backend.api.streaming_events import status_data

    if case_title:
        label = f'Preparing context for case "{case_title}"'
    else:
        label = "Preparing case context"
    return status_data(Stage.CASE_CONTEXT, label)


class StatusTranslator:
    def __init__(self) -> None:
        self._last_stage: Optional[Stage] = None
        self._pending_tool_name: Optional[str] = None

    def _maybe_emit(self, stage: Stage, label: str) -> Optional[dict[str, Any]]:
        from backend.api.streaming_events import status_data

        if stage == self._last_stage:
            return None
        self._last_stage = stage
        return status_data(stage, label)

    def note_external_status(self, stage: Stage) -> None:
        """Lets the caller inform the translator that a status was
        emitted OUTSIDE this class (e.g. the pre-turn Case-context status)
        so deduplication still works correctly against it.
        """
        self._last_stage = stage

    def translate_event(self, event: Any) -> Optional[dict[str, Any]]:
        """Returns a `status_data(...)` dict to emit, or `None` if this
        event does not warrant a (new, non-duplicate) status change.
        """
        if not _is_aggregated(event):
            return None

        function_calls = event.get_function_calls()
        if function_calls:
            return self._translate_function_calls(function_calls)

        function_responses = event.get_function_responses()
        if function_responses:
            return self._translate_function_responses(function_responses)

        return None

    def _translate_function_calls(self, function_calls: list[Any]) -> Optional[dict[str, Any]]:
        for call in function_calls:
            name = getattr(call, "name", None)
            if name not in _TOOL_CALL_STAGES:
                continue
            self._pending_tool_name = name
            stage = _TOOL_CALL_STAGES[name]
            label = _TOOL_CALL_LABELS[name]
            if name == _RECORD_CASE_ANALYSIS_TOOL_NAME:
                args = getattr(call, "args", None) or {}
                kind = args.get("kind") if isinstance(args, dict) else None
                if isinstance(kind, str) and kind in _CASE_ANALYSIS_KIND_LABELS:
                    label = _CASE_ANALYSIS_KIND_LABELS[kind]
            return self._maybe_emit(stage, label)
        return None

    def _translate_function_responses(self, function_responses: list[Any]) -> Optional[dict[str, Any]]:
        for response in function_responses:
            name = getattr(response, "name", None)
            if name != _INCIDENT_MANAGER_TOOL_NAME:
                continue
            result = getattr(response, "response", None)
            if not isinstance(result, dict):
                continue
            return self._refine_incident_manager_response(result)
        return None

    def _refine_incident_manager_response(self, result: dict[str, Any]) -> Optional[dict[str, Any]]:
        outcome = result.get("outcome")
        if outcome == "ok":
            evidence = result.get("evidence")
            count = len(evidence) if isinstance(evidence, list) else 0
            if count > 0:
                noun = "message" if count == 1 else "messages"
                return self._maybe_emit(
                    Stage.EVIDENCE_PROCESSING, f"Reviewing {count} retrieved {noun}"
                )
            return None
        if outcome == "proposed":
            return self._maybe_emit(Stage.ACTION_PREPARATION, "Preparing an action for your approval")
        if outcome == "executed":
            return self._maybe_emit(Stage.ACTION_PREPARATION, "Confirming the completed action")
        return None


# --- Expandable, sanitized run trace (pre-4H milestone) --------------------
#
# `RunTraceTranslator` mirrors `StatusTranslator` above exactly (same
# module, same "only place that inspects raw ADK Event objects" boundary,
# same closed tool-name mapping, same "known capabilities only" fallback)
# but produces PAST-TENSE, PERMANENT milestone facts for the run trace
# instead of an ephemeral, overwritable current-activity label. The two
# translators are deliberately independent instances with independent
# dedup state: a `Stage` can be (re-)emitted as a status several times
# without every occurrence becoming a distinct trace step, and a trace
# milestone, once recorded, is never later "un-recorded" the way a status
# is replaced.

_CASE_ANALYSIS_KIND_TRACE_LABELS: dict[str, str] = {
    "hypothesis": "Recorded a hypothesis",
    "recommendation": "Recorded a recommendation",
    "open_question": "Noted an open question",
}

# Closed `WriteOperation` vocabulary (backend/approval/schemas.py) --
# verbatim strings only ever come from that enum's own `.value`, never
# from anything the model wrote itself.
_CREATE_CHAT_OPERATION = "teams.createChat"
_SEND_MESSAGE_OPERATION = "teams.sendMessage"


class TraceStepInput(TypedDict, total=False):
    category: TraceCategory
    label: str
    status: TraceStepStatus
    safe_metadata: Optional[dict[str, Any]]


def _trace_step(
    category: TraceCategory,
    label: str,
    status: TraceStepStatus = TraceStepStatus.COMPLETED,
    safe_metadata: Optional[dict[str, Any]] = None,
) -> TraceStepInput:
    return TraceStepInput(category=category, label=label, status=status, safe_metadata=safe_metadata)


def case_context_loaded_trace_step() -> TraceStepInput:
    """Mirrors `translate_case_context_status` -- emitted by the SAME
    caller-side gate (chat_service.py only calls this once the active
    Case hint has already been independently re-authorized -- see that
    module's docstring), so, like the status it mirrors, this is never
    invoked for a session with no linked/authorized Case. Deliberately
    omits the case title (unlike the status label) -- the title is
    already shown elsewhere in the UI (the active-case chip), and this
    milestone only needs to state that context loading happened.
    """
    return _trace_step(TraceCategory.CASE, "Loaded active case context")


def response_generated_trace_step() -> TraceStepInput:
    """Unconditional terminal milestone for every successfully-completed
    run (instruction section 19's "boundary step" allowance) -- true for
    every run that reaches this point, from the simplest "hello" to the
    most complex Teams/case turn, so it carries no run-specific
    information at all and needs no dedup/allowlist consideration.
    """
    return _trace_step(TraceCategory.RESPONSE, "Generated the response")


def response_failed_trace_step() -> TraceStepInput:
    """Terminal milestone for a run that ends in `error` -- mirrors
    `response_generated_trace_step` for the failure path (instruction
    section 29: never claim success). Deliberately generic; the raw
    exception/failure reason is never surfaced here (see
    chat_service.py's own `error` event, already sanitized).
    """
    return _trace_step(TraceCategory.RESPONSE, "Request could not be completed", TraceStepStatus.FAILED)


def selection_prepared_trace_step() -> TraceStepInput:
    """Emitted at the SAME deterministic call site as the `selection.pending`
    event itself (chat_service.py, right after `map_pending_selection`
    returns non-`None`) -- never derived from an ADK event, since the
    `PendingSelectionDTO` is already the authoritative, already-safe fact.
    """
    return _trace_step(TraceCategory.SELECTION, "Prepared chat selection for review")


def _safe_chat_topic(args: Any) -> Optional[str]:
    """The `chat_topic` call argument, if present/non-empty -- the exact
    Teams chat name/title `team_manager` is asking about (per
    `IncidentManagerRequest.chat_topic`, OPTIONAL since Phase 5.1J's
    correction pass -- absent entirely for a request that does not need
    an external Teams conversation, e.g. a governed-knowledge-only
    request): either the user's own typed words, or the exact candidate
    label they already picked from a `SelectionCard`, or the currently-
    authoritative selected topic. Deliberately NOT treated the same as
    `question` (never surfaced -- see
    `test_trace_step_never_contains_raw_tool_arguments...`):
    a chat name is already fully known to/provided by the user and already
    shown elsewhere in this UI (a write proposal's `target_display_name`,
    the `SelectionCard` the user picked from) -- unlike `question`, which
    can contain arbitrary retrieved-content-shaped free text this trace
    must never echo. Still never the raw `chat_id` -- that argument does
    not exist on this call at all (see IncidentManagerRequest's schema).
    """
    if not isinstance(args, dict):
        return None
    topic = args.get("chat_topic")
    return topic.strip() if isinstance(topic, str) and topic.strip() else None


def _write_action(result: dict[str, Any]) -> dict[str, Any]:
    write_action = result.get("write_action")
    return write_action if isinstance(write_action, dict) else {}


class RunTraceTranslator:
    """Ownership/dedup of the resulting steps is the caller's
    (`run_trace.RunTraceRecorder`'s) job, not this class's.
    """

    def translate_event(self, event: Any) -> Optional[TraceStepInput]:
        if not _is_aggregated(event):
            return None

        function_calls = event.get_function_calls()
        if function_calls:
            return self._translate_function_calls(function_calls)

        function_responses = event.get_function_responses()
        if function_responses:
            return self._translate_function_responses(function_responses)

        return None

    def _translate_function_calls(self, function_calls: list[Any]) -> Optional[TraceStepInput]:
        for call in function_calls:
            name = getattr(call, "name", None)
            if name == _INCIDENT_MANAGER_TOOL_NAME:
                args = getattr(call, "args", None)
                topic = _safe_chat_topic(args)
                if topic:
                    return _trace_step(TraceCategory.TEAMS, f'Used the "{topic}" conversation')
                if isinstance(args, dict) and args.get("chat_topic"):
                    # `chat_topic` was supplied but didn't resolve to a
                    # clean non-empty string -- still a Teams-scoped call.
                    return _trace_step(TraceCategory.TEAMS, "Used the selected Teams conversation")
                # Phase 5.1J: `chat_topic` is optional on `IncidentManagerRequest`
                # -- ABSENT means this delegation may not involve a Teams
                # conversation at all (e.g. a governed-knowledge-only
                # request). Never claim "Used the selected Teams
                # conversation" when no Teams destination was actually
                # supplied -- no trace step for this call-observation point
                # is more accurate than a fabricated one.
                return None
        return None

    def _translate_function_responses(self, function_responses: list[Any]) -> Optional[TraceStepInput]:
        for response in function_responses:
            name = getattr(response, "name", None)
            result = getattr(response, "response", None)
            if not isinstance(result, dict):
                continue
            if name == _INCIDENT_MANAGER_TOOL_NAME:
                step = self._incident_manager_response_trace(result)
                if step is not None:
                    return step
            elif name == _RECORD_CASE_ANALYSIS_TOOL_NAME:
                step = self._case_analysis_response_trace(result)
                if step is not None:
                    return step
        return None

    def _incident_manager_response_trace(self, result: dict[str, Any]) -> Optional[TraceStepInput]:
        outcome = result.get("outcome")

        if outcome == "ok":
            evidence = result.get("evidence")
            count = len(evidence) if isinstance(evidence, list) else 0
            if count == 0:
                return None
            noun = "message" if count == 1 else "messages"
            chat_title = result.get("chat_title")
            label = (
                f'Reviewed {count} retrieved {noun} from "{chat_title.strip()}"'
                if isinstance(chat_title, str) and chat_title.strip()
                else f"Reviewed {count} retrieved {noun}"
            )
            return _trace_step(TraceCategory.EVIDENCE, label, safe_metadata={"message_count": count})

        if outcome == "selection_needed":
            candidates = result.get("candidate_titles")
            count = len(candidates) if isinstance(candidates, list) else 0
            if count > 0:
                noun = "conversation" if count == 1 else "conversations"
                return _trace_step(
                    TraceCategory.SELECTION,
                    f"Found {count} similar Teams {noun}",
                    safe_metadata={"candidate_count": count},
                )
            return _trace_step(TraceCategory.SELECTION, "Searched available Teams conversations")

        if outcome in ("not_found", "no_result"):
            return _trace_step(
                TraceCategory.TEAMS, "No matching Teams content was found", TraceStepStatus.WARNING
            )

        if outcome == "ambiguous":
            return _trace_step(TraceCategory.TEAMS, "The request could not be resolved", TraceStepStatus.WARNING)

        if outcome == "error":
            return _trace_step(TraceCategory.TEAMS, "Could not complete the Teams request", TraceStepStatus.FAILED)

        if outcome == "proposed":
            operation = _write_operation(result)
            write_action = _write_action(result)
            if operation == _CREATE_CHAT_OPERATION:
                title = write_action.get("title")
                label = (
                    f'Prepared the Teams chat "{title.strip()}" for review'
                    if isinstance(title, str) and title.strip()
                    else "Prepared a Teams chat for review"
                )
            elif operation == _SEND_MESSAGE_OPERATION:
                # `target_display_name` -- never the raw `chat_id` -- is
                # already the exact, safe presentation field ApprovalCard
                # itself shows for this same proposal (schemas.py's
                # `PendingActionDTO.target_display_name` docstring).
                target = write_action.get("target_display_name")
                label = (
                    f'Prepared a message for "{target.strip()}" for review'
                    if isinstance(target, str) and target.strip()
                    else "Prepared a Teams message for review"
                )
            else:
                label = "Prepared an action for review"
            return _trace_step(TraceCategory.ACTION, label)

        if outcome == "executed":
            operation = _write_operation(result)
            write_action = _write_action(result)
            if operation == _CREATE_CHAT_OPERATION:
                title = write_action.get("title")
                label = (
                    f'Created the Teams chat "{title.strip()}"'
                    if isinstance(title, str) and title.strip()
                    else "Created the Teams chat"
                )
            elif operation == _SEND_MESSAGE_OPERATION:
                target = write_action.get("target_display_name")
                label = (
                    f'Sent the Teams message to "{target.strip()}"'
                    if isinstance(target, str) and target.strip()
                    else "Sent the Teams message"
                )
            else:
                label = "Completed the action"
            return _trace_step(TraceCategory.ACTION, label)

        return None

    def _case_analysis_response_trace(self, result: dict[str, Any]) -> Optional[TraceStepInput]:
        if "error" in result:
            return _trace_step(TraceCategory.CASE, "Could not record case analysis", TraceStepStatus.FAILED)
        kind = result.get("kind")
        if isinstance(kind, str) and kind in _CASE_ANALYSIS_KIND_TRACE_LABELS:
            return _trace_step(TraceCategory.CASE, _CASE_ANALYSIS_KIND_TRACE_LABELS[kind])
        return None


def _write_operation(result: dict[str, Any]) -> Optional[str]:
    write_action = result.get("write_action")
    if not isinstance(write_action, dict):
        return None
    operation = write_action.get("operation")
    return operation if isinstance(operation, str) else None
