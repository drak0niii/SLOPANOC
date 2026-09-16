"""THE canonical Team Manager turn-execution pipeline (Phase 4A, refactored
in Phase 4E around one shared async event generator -- instruction
section 14).

ONE PIPELINE, TWO CONSUMERS: `execute_turn_events` is an async generator
that drives ADK's `Runner` exactly once per call and yields a normalized
sequence of `backend.api.streaming_events.StreamEvent`s. `run_turn`
(the pre-existing synchronous `POST /api/sessions/{id}/messages` path)
consumes that generator to completion and folds its terminal events into
a `ChatResponse`; the new SSE endpoint (`app.py`) forwards the same
events live. There is no second Runner-invocation implementation -- both
consumers observe the exact same agent turn.

ADK STREAMING, VERIFIED AGAINST THE INSTALLED 1.33.0 SOURCE (not assumed)
before implementing:
  - `google.adk.agents.run_config.RunConfig(streaming_mode=StreamingMode
    .SSE)`, passed to `Runner.run_async(..., run_config=...)`, is ADK's
    own documented mechanism for genuine incremental text (`run_config.py`'s
    extensive `StreamingMode.SSE` docstring). Without it, `run_async`
    only ever yields the single final aggregated event per turn
    (`StreamingMode.NONE`, the default) -- true partial output is opt-in.
  - `Event.partial=True` marks an intermediate streaming chunk;
    `Event.partial=False` (with `is_final_response()` true) marks the
    complete, aggregated response.
  - Traced into `google/adk/utils/streaming_utils.py`
    (`StreamingResponseAggregator.process_response`/`.close()`): each
    individual partial text chunk's `event.content.parts[].text` is the
    RAW per-chunk text straight from the underlying `generate_content_stream`
    response (Gemini's own streaming API, which yields new-text-only
    chunks) -- ADK's internal `_current_text_buffer` accumulation exists
    only to build the SEPARATE final aggregated event on `close()`. This
    is what proves `_extract_delta_text` below needs no diffing of its
    own: each partial chunk already IS the new-text-only delta
    (instruction section 25), and concatenating every delta equals the
    final `message.completed` content (instruction section 27) --
    confirmed from source, not assumed.
  - Partial events can also carry streaming function-call ARGUMENT
    chunks (`event.partial=True` with a `function_call` part, no display
    text) -- ADK's own `RunConfig` docstring explicitly says these are
    "typically NOT displayed to end users"; `_extract_delta_text` skips
    any partial event that carries a function call.
  - `Event.partial` also marks "thought" content on models that support
    it (`types.Part(text=..., thought=True)`) -- excluded from both
    `_extract_delta_text` and `_extract_final_text` (instruction section
    7: never stream chain-of-thought/hidden reasoning).
  - `AgentTool.run_async` (verified in an earlier milestone) fully
    encapsulates `incident_manager`'s own nested Runner/session -- its
    internal tool calls never appear in team_manager's own event stream
    at all, so this module only ever needs to translate team_manager's
    OWN two direct tools (`incident_manager`, `record_case_analysis`);
    see activity_translator.py.
  - Cancellation, HARDENED after the initial 4E pass (do not assume HTTP
    disconnect promptly closes anything): inspecting the installed
    Starlette 0.52.1 source showed that on the modern ASGI path used by
    Uvicorn (`spec_version >= 2.4`), `StreamingResponse` does NOT
    proactively cancel the response generator on client disconnect at
    all -- disconnect is only discovered as an `OSError` the next time
    `send()` is called, and nothing in that path ever calls `.aclose()`
    on the `body_iterator` chain (`app.py`'s SSE `event_source()` ->
    `execute_turn_events` -> `_run_turn_events` -> the ADK `Runner`'s own
    generator); it is simply abandoned to eventual GC finalization. This
    means the session lock's release can NEVER be allowed to depend on
    the HTTP-facing generator being closed/cancelled. `execute_turn_events`
    therefore runs the actual lock-holding, session-mutating work
    (`_run_turn_events`) in its own tracked `asyncio.Task`, decoupled from
    the HTTP consumer -- see that method's docstring for the full
    rationale. `Aclosing` is still used INSIDE that task (around both
    `_run_turn_events` and the ADK `Runner.run_async` generator it
    drives) so the task's OWN early exit (an unexpected internal
    exception) still tears its own generators down cleanly; it just is no
    longer the thing an HTTP disconnect can reach. ADK's own in-flight
    model/tool call is still not force-cancelled mid-await by any of this
    (no public API for that was found) -- see the final report's "known
    limitations": the task runs to its own natural completion regardless
    of the client, which is precisely what makes the session-lock
    invariant provable rather than best-effort.

NO SECOND AGENT RUNTIME: `_build_runner` imports and binds the exact same
canonical `team_manager` `Agent` instance already used everywhere else in
this backend.

STATUS EVENTS ARE EPHEMERAL (instruction section 8): everything yielded
here that is not `message.delta`/`message.completed` (i.e. every
`status`/`status.clear` event) is never written to ADK session state,
Case context, or any store -- it exists only for the lifetime of this
generator. Only the final response text (persisted automatically by
ADK's own `Runner`/`append_event` machinery, unchanged from before this
milestone) and the safe `PendingActionDTO`/`ActiveCaseDTO` derived from
already-persisted state survive past one call.
"""
from __future__ import annotations

import asyncio
import logging
from functools import lru_cache
from typing import Any, AsyncIterator, Awaitable, Callable, NamedTuple, Optional, Protocol, Sequence

from google.adk.agents.run_config import RunConfig, StreamingMode
from google.adk.utils.context_utils import Aclosing
from google.genai import types

from backend.agents.team_manager.direct_read_fast_path import (
    discard_pending_trusted_result,
    trust_validation_failed_for_run,
)
from backend.agents.team_manager.read_continuation_enforcement import (
    discard_active_read_continuation,
    stash_active_read_continuation,
)
from backend.agents.team_manager.read_continuation_execution import execute_read_continuation
from backend.agents.team_manager.read_continuation_presentation import (
    PENDING_SPECIALIST_RESULT_STATE_KEY,
    TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY,
    build_trusted_specialist_result_envelope,
    content_has_nonblank_text,
    pop_current_run_specialist_result,
    synthetic_incident_manager_call_event,
    synthetic_incident_manager_response_event,
    validate_trusted_envelope_for_run,
)
from backend.agents.team_manager.clarification_renderer import render_command_suppression_text
from backend.agents.team_manager.governed_knowledge_completion import (
    SAFE_COMPLETION_FAILURE_TEXT,
    discard_governed_completion_deterministic_fallback,
    enforce_governed_knowledge_at_completion,
    pop_governed_completion_deterministic_fallback,
)
from backend.agents.team_manager.authoritative_request_context import (
    AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY,
    render_authoritative_current_turn_request_context,
)
from backend.agents.team_manager.authorized_response import (
    build_authorized_response_context,
    extract_known_commands_from_guidance,
)
from backend.agents.team_manager.final_output_validator import validate_final_output
from backend.agents.team_manager.request_contract import (
    PENDING_GOVERNED_REQUEST_STATE_KEY,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    RequestClass,
    build_deterministic_read_continuation_contract,
    parse_pending_governed_request,
    safe_request_contract_observability_fields,
)
from backend.agents.team_manager.request_contract_completion import request_current_turn_contract
from backend.agents.team_manager.request_execution_policy import (
    FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT,
    KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT,
    RequestExecutionDecision,
    RequestExecutionStatus,
    WorkAuthority,
    WorkEnvelope,
    build_pending_governed_request_state_update,
    derive_execution_decision,
    derive_work_envelope,
    enforce_execution_decision_on_guidance,
    enforce_response_mode_compatibility,
    expire_completed_pending_on_invalid_contract,
    is_governed_evidence_continuity_permitted,
    load_current_turn_contract,
    requires_unstructured_response_backstop,
    resolve_effective_governed_contract,
)
from backend.agents.team_manager.source_requirements_completion import (
    SAFE_DECLARATION_FAILURE_TEXT,
    request_source_requirements_declaration,
)
from backend.agents.team_manager.state_sync import compute_state_updates
from backend.api.activity_translator import (
    RunTraceTranslator,
    StatusTranslator,
    case_context_loaded_trace_step,
    response_failed_trace_step,
    response_generated_trace_step,
    selection_prepared_trace_step,
    translate_case_context_status,
)
from backend.api.activity_queue import discard_activity_channel, get_activity_channel, register_activity_channel
from backend.api.attachment_service import PreparedAttachment, prepare_attachments_for_turn
from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY, get_active_case_for_session
from backend.api.conversation_target_capture import ConversationTargetCapture
from backend.api.pending_action import map_pending_action
from backend.api.source_requirements_capture import SourceRequirementsCapture
from backend.api.pending_selection import map_pending_selection
from backend.api.perf_timing import DelegationTimer, PerfTimer, discard_model_call_tracking
from backend.api.run_trace import RunTraceRecorder
from backend.api.schemas import ActiveCaseDTO, AssistantMessage, ChatResponse, PendingActionDTO
from backend.api.session_service import APP_NAME, DEFAULT_USER_ID, ApiSessionService, get_session_service
from backend.api.session_state_keys import record_user_turn_activity
from backend.api.governed_evidence_continuity import (
    ACTIVE_GOVERNED_PROCEDURE_STATE_KEY,
    LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY,
    build_active_governed_procedure_state_update,
    build_last_selected_governed_evidence_state_update,
    compute_fresh_active_procedure_anchor,
    detect_governed_evidence_anchor_mismatch,
    parse_active_governed_procedure,
    parse_last_selected_governed_evidence,
    revalidate_prior_governed_evidence,
)
from backend.api.knowledge_source_reference import (
    build_knowledge_source_references,
    dedupe_knowledge_source_references,
)
from backend.api.source_reference import (
    TeamsSourceCapture,
    ensure_source_reference_for_visual_evidence,
    resolve_authoritative_contributors,
)
from backend.api.hosted_content_vision_context import (
    build_visual_evidence,
    discard_pending_hosted_content_image,
    pop_delivered_visual_evidence,
)
from backend.api.multimodal_turn_context import (
    discard_run_images,
    register_run_images,
    trusted_image_parts_from_content,
)
from backend.agents.incident_manager.schemas import TroubleshootingGuidance
from backend.api.applicability_context_capture import discard_known_applicability_context
from backend.api.rejected_command_context import discard_rejected_commands, pop_rejected_commands
from backend.api.streaming_events import EventSequencer, Stage, StreamEvent, StreamEventType, status_data
from backend.api.troubleshooting_guidance_context import (
    discard_troubleshooting_guidance,
    pop_troubleshooting_guidance,
    render_troubleshooting_guidance,
)
from backend.api.turn_context import bind_run_id, pop_message_texts, reset_run_id
from backend.api.turn_source_references import (
    CANONICAL_RESULT_ENFORCEMENT_STATE_KEY,
    TURN_SOURCE_REFERENCES_STATE_KEY,
    CanonicalTurnResultConflictError,
    build_turn_failure_marker_delta,
    build_turn_source_references_delta,
)
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService, get_attachment_service
from backend.attachments.storage import ChatAttachmentStorage, get_attachment_storage
from backend.cases.service import CaseService, get_case_service
from backend.config.settings import get_settings
from backend.gateway.safe_error import SafeErrorException, run_failure, validation_error
from backend.selection.service import PENDING_READ_CONTINUATION_STATE_KEY, pop_read_continuation
from backend.tools.knowledge.runtime import (
    discard_knowledge_run_evidence_state,
    get_knowledge_repository,
    snapshot_selected_knowledge_evidence,
)
from backend.tools.teams.state_keys import (
    SELECTED_TEAMS_CHAT_ID_STATE_KEY,
    SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY,
)

_logger = logging.getLogger(__name__)

# Sentinel put on a turn's relay queue to mark "no more events, the
# logical turn -- and its hold on the session lock -- is over" (see
# `execute_turn_events`'s module-level docstring addendum below for why a
# background task + queue exists at all).
_TURN_DONE = object()

# POST-5.1 B5 -- an internal-only, fixed, generic descriptor used SOLELY
# as the `question=` argument to the two secondary/remediation helpers
# below (`request_source_requirements_declaration`,
# `enforce_governed_knowledge_at_completion`) when a turn's real
# `message_text` is blank (an image-only send). Both call sites only ever
# run AFTER team_manager's own turn already produced a real `final_text`
# -- this is never the user-facing answer, never written into
# conversation history, never used as the visible chat title (`derive_
# chat_title("")` already correctly falls back to "New chat" on its own,
# unrelated to this constant), and never presented as user-authored text.
# See `_remediation_question`'s own docstring for why a fixed fallback is
# safer here than passing an empty string into functions that treat their
# `question` argument as always-meaningful.
_IMAGE_ONLY_REMEDIATION_QUESTION_FALLBACK = "[The user sent an image with no accompanying text.]"


def _remediation_question(message_text: str) -> str:
    """See `_IMAGE_ONLY_REMEDIATION_QUESTION_FALLBACK`'s own module-level
    comment for the full safety contract. Only ever affects the two
    bounded, tools-scoped remediation calls in `_run_turn_events` -- never
    the real multimodal `Content` sent to the Runner (which correctly
    omits a text part entirely for an image-only turn -- see that
    method's own `content` construction).
    """
    return message_text if message_text.strip() else _IMAGE_ONLY_REMEDIATION_QUESTION_FALLBACK


def _log_unexpected_background_turn_exception(task: "asyncio.Task[None]") -> None:
    """Last-resort safety net for `execute_turn_events`'s background turn
    task (below): `_run_turn_events` already converts every expected
    failure into a safe `error` stream event rather than raising, so this
    should never actually fire in normal operation -- it exists only so
    that a genuinely unexpected bug surfaces in the server log instead of
    silently vanishing as an "exception was never retrieved" asyncio
    warning, since nothing else ever awaits this task directly.
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _logger.exception("Unexpected exception from a background chat turn task", exc_info=exc)


class _EventStream(Protocol):
    """Structural shape this module needs from whatever `Runner.run_async`
    (or a test double) yields -- see `google.adk.events.Event`, which
    satisfies this by construction.
    """

    def get_function_calls(self) -> list[Any]: ...

    def get_function_responses(self) -> list[Any]: ...

    def is_final_response(self) -> bool: ...

    partial: Any
    content: Any


class _Runner(Protocol):
    def run_async(
        self, *, user_id: str, session_id: str, new_message: types.Content, run_config: Optional[RunConfig] = None
    ): ...

    async def rewind_async(
        self,
        *,
        user_id: str,
        session_id: str,
        rewind_before_invocation_id: str,
        run_config: Optional[RunConfig] = None,
    ) -> None: ...


def _build_runner(session_service: ApiSessionService) -> _Runner:
    from google.adk.runners import Runner

    from backend.agents.team_manager.agent import operational_team_manager
    from backend.agents.team_manager.direct_read_fast_path import _present_fast_path_result_via_trusted_pipeline
    from backend.api.perf_timing import before_model_call

    # P4B.3 COMPLETION PASS (preserved) + CONTROL-PLANE-SEQ-03: this
    # Runner is now selected ONLY for a normal turn whose mandatory
    # current-turn preflight already succeeded AND whose Phase-A
    # deterministic policy already permits operational orchestration --
    # it therefore uses `operational_team_manager` (agent.py -- the SAME
    # `team_manager` role/identity, `record_request_contract`/`record_
    # source_requirements` structurally removed, since preflight already,
    # deterministically, owns both declarations for this turn) rather than
    # the base `team_manager`. The ONE additional, normally-inert P4B.3
    # `before_model_callback` (`_present_fast_path_result_via_trusted_
    # pipeline` -- see `direct_read_fast_path.get_fast_path_team_manager`'s
    # own docstring for the full ADK-source-verified mechanism, unchanged,
    # not reimplemented here) is composed the SAME way that helper already
    # does it, just over `operational_team_manager` instead of the base
    # agent -- every other aspect (instruction, remaining tools, other
    # callbacks) is identical to what this Runner already used before this
    # pass, minus the two now-preflight-owned governance tools.
    operational_fast_path_team_manager = operational_team_manager.model_copy(
        update={
            "before_model_callback": [
                _present_fast_path_result_via_trusted_pipeline,
                before_model_call("team_manager"),
            ]
        }
    )
    return Runner(
        app_name=APP_NAME,
        agent=operational_fast_path_team_manager,
        session_service=session_service.adk_session_service,
    )


def _build_incident_only_runner(session_service: ApiSessionService) -> _Runner:
    """CONTROL-PLANE-SEQ-04 §14 -- structural tool removal, one layer up:
    selected for a turn whose `WorkEnvelope` grants `may_route_incident_
    manager=True` but `may_route_troubleshooting_manager=False` (in
    practice, an `OPERATIONAL_INFORMATION`-class request -- see `derive_
    work_envelope`'s own docstring). Mirrors `_build_runner` exactly,
    including the SAME P4B.3 fast-path `before_model_callback` composition,
    over `operational_team_manager_incident_manager_only` (agent.py)
    instead of the full `operational_team_manager`.
    """
    from google.adk.runners import Runner

    from backend.agents.team_manager.agent import operational_team_manager_incident_manager_only
    from backend.agents.team_manager.direct_read_fast_path import _present_fast_path_result_via_trusted_pipeline
    from backend.api.perf_timing import before_model_call

    incident_only_fast_path_team_manager = operational_team_manager_incident_manager_only.model_copy(
        update={
            "before_model_callback": [
                _present_fast_path_result_via_trusted_pipeline,
                before_model_call("team_manager"),
            ]
        }
    )
    return Runner(
        app_name=APP_NAME,
        agent=incident_only_fast_path_team_manager,
        session_service=session_service.adk_session_service,
    )


def _build_troubleshooting_only_runner(session_service: ApiSessionService) -> _Runner:
    """CONTROL-PLANE-SEQ-04 §14 -- the mirror-image sibling of `_build_
    incident_only_runner`, immediately above: selected for a turn whose
    `WorkEnvelope` grants `may_route_troubleshooting_manager=True` but
    `may_route_incident_manager=False`. Not currently reachable by any
    `derive_work_envelope` branch (every class that grants `troubleshooting
    _manager` also grants `incident_manager`) -- defined for completeness,
    exactly mirroring `operational_team_manager_troubleshooting_manager_
    only`'s own construction in agent.py.
    """
    from google.adk.runners import Runner

    from backend.agents.team_manager.agent import operational_team_manager_troubleshooting_manager_only
    from backend.agents.team_manager.direct_read_fast_path import _present_fast_path_result_via_trusted_pipeline
    from backend.api.perf_timing import before_model_call

    troubleshooting_only_fast_path_team_manager = operational_team_manager_troubleshooting_manager_only.model_copy(
        update={
            "before_model_callback": [
                _present_fast_path_result_via_trusted_pipeline,
                before_model_call("team_manager"),
            ]
        }
    )
    return Runner(
        app_name=APP_NAME,
        agent=troubleshooting_only_fast_path_team_manager,
        session_service=session_service.adk_session_service,
    )


def _build_presentation_runner(session_service: ApiSessionService) -> _Runner:
    """R1 FIX: a SEPARATE `Runner`, bound to `presentation_team_manager`
    (agent.py -- the SAME "team_manager" role/identity, but with `tools=
    []`), used ONLY for the one turn presenting a just-validated
    `TrustedSpecialistResult` -- see `_run_turn_events`'s own selection
    logic below and `presentation_team_manager`'s own docstring for the
    full structural rationale.
    """
    from google.adk.runners import Runner

    from backend.agents.team_manager.agent import presentation_team_manager

    return Runner(
        app_name=APP_NAME,
        agent=presentation_team_manager,
        session_service=session_service.adk_session_service,
    )


async def _retry_trusted_presentation_once(
    *, user_id: str, run_id: str, validated_result: dict[str, Any], user_content: types.Content
) -> Optional[str]:
    """EMPTY-RESPONSE FIX (pre-4H correction pass): a single, bounded
    retry of ONLY the presentation turn (`presentation_team_manager`,
    `tools=[]`), used when the FIRST presentation attempt on the real
    session (`_run_turn_events`'s own main loop, above) produced no
    non-blank final text -- never a retry of `execute_read_continuation`
    or any Teams tool.

    Deliberately runs against a fresh, throwaway `InMemorySessionService`
    -- mirroring `backend/agents/team_manager/direct_read_fast_path.py`'s
    own already-proven `_run_trusted_presentation` pattern -- rather than
    the real canonical session: calling `Runner.run_async` a second time
    with the same `new_message` against the REAL session would append a
    second, duplicate user-turn event to genuine conversation history,
    which this function must never do. Seeded with only
    `PENDING_SPECIALIST_RESULT_STATE_KEY` (the same already-validated
    result the first attempt used) -- no other session state is needed
    for a `tools=[]` presentation-only turn. Returns the retried
    response's non-blank text, or `None` if the retry also produced
    nothing usable.
    """
    from google.adk.memory import InMemoryMemoryService
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    from backend.agents.team_manager.agent import presentation_team_manager

    session_service = InMemorySessionService()
    app_name = f"{presentation_team_manager.name}::retry-presentation"
    retry_session_id = f"retry-presentation::{run_id}"
    try:
        await session_service.create_session(
            app_name=app_name,
            user_id=user_id,
            session_id=retry_session_id,
            state={PENDING_SPECIALIST_RESULT_STATE_KEY: validated_result},
        )
        runner = Runner(
            app_name=app_name,
            agent=presentation_team_manager,
            session_service=session_service,
            memory_service=InMemoryMemoryService(),
        )
        last_content: Optional[types.Content] = None
        try:
            async for event in runner.run_async(user_id=user_id, session_id=retry_session_id, new_message=user_content):
                if event.content and content_has_nonblank_text(event.content):
                    last_content = event.content
        finally:
            await runner.close()
    finally:
        try:
            existing = await session_service.get_session(app_name=app_name, user_id=user_id, session_id=retry_session_id)
            if existing is not None:
                await session_service.delete_session(app_name=app_name, user_id=user_id, session_id=retry_session_id)
        except Exception:
            _logger.warning("chat_service: failed to delete internal retry-presentation session")

    if last_content is None or not last_content.parts:
        return None
    texts = [p.text for p in last_content.parts if not getattr(p, "thought", False) and getattr(p, "text", None)]
    return "\n".join(texts) or None


def _non_thought_text(part: Any) -> Optional[str]:
    """`None` for a "thought" part (never streamed/exposed -- instruction
    section 7) or a part with no text at all.
    """
    if getattr(part, "thought", False):
        return None
    return getattr(part, "text", None)


def _extract_final_text(event: _EventStream) -> Optional[str]:
    if not event.is_final_response():
        return None
    if not event.content or not event.content.parts:
        return None
    texts = [t for t in (_non_thought_text(p) for p in event.content.parts) if t]
    text = "\n".join(texts)
    return text or None


def _extract_delta_text(event: _EventStream) -> Optional[str]:
    """New text only (instruction section 25) -- see this module's
    docstring for why no diffing against previously-seen text is needed.
    """
    if not getattr(event, "partial", False):
        return None
    if not event.content or not event.content.parts:
        return None
    if event.get_function_calls():
        return None  # streaming function-call-argument chunk, never displayed
    texts = [t for t in (_non_thought_text(p) for p in event.content.parts) if t]
    text = "".join(texts)
    return text or None


class _MergedEvent(NamedTuple):
    """Tagged union yielded by `_merge_adk_and_activity_events` -- `"adk"`
    carries a real ADK `Event` (exactly what `async for event in agen`
    would have yielded on its own); `"activity"` carries an `ActivityEvent`
    from this turn's own activity channel (`activity_queue.py`). Never
    conflated: an ADK event has `.get_function_calls()`/`.content`/etc.,
    an `ActivityEvent` is a plain, closed `{"kind", "safe_metadata"}` dict
    -- the two shapes are never treated interchangeably downstream.
    """

    source: str
    item: Any


async def _merge_adk_and_activity_events(
    agen: AsyncIterator[Any], activity_channel: "Optional[asyncio.Queue[Any]]"
) -> AsyncIterator[_MergedEvent]:
    """Phase 2 (Runtime Activity Truthfulness): the smallest correct merge
    of (A) the outer Team Manager Runner's own real ADK event stream and
    (B) this turn's activity channel -- proven necessary by the Phase 1
    audit's own source-level finding that Incident Manager's internal
    tool calls (`knowledge_search`, `teams_get_messages`, ...) are
    entirely invisible to (A) on their own (`google.adk.tools.agent_tool
    .AgentTool.run_async` consumes its nested Runner's events internally
    and never yields them outward -- see activity_queue.py's own module
    docstring). Concurrently races `agen.__anext__()` against
    `activity_channel.get()` so an activity event reported WHILE the
    outer Runner is still blocked awaiting a nested `incident_manager`
    call (the common case) is not stuck behind it.

    `activity_channel` may be `None` (no channel was registered, e.g. a
    caller that constructs `_run_turn_events` directly in a test without
    going through the normal `register_activity_channel` call site) --
    in that case this degrades to a plain passthrough of `agen`, byte-
    identical to the pre-Phase-2 `async for event in agen` loop.

    Never duplicates or re-invokes the Runner -- `agen` is iterated
    exactly as before, one item at a time, in original order. Every
    pending task is cancelled and awaited in `finally`, so no orphaned
    asyncio task or generator reference survives this function --
    satisfies the same "no leak on any exit path" discipline this module
    already enforces for its other per-run resources.

    D2 CORRECTIVE PASS -- ONE PERSISTENT TASK DRIVES `agen`, NEVER ONE
    PER ITEM: an earlier revision of this function called
    `asyncio.ensure_future(agen.__anext__())` freshly on EVERY loop
    iteration -- each call wrapping that ONE resumption of `agen` in a
    brand-new asyncio Task. `asyncio.ensure_future`/`create_task` copies
    the current `contextvars.Context` at Task-creation time (proven
    elsewhere in this codebase for a different ContextVar -- see
    `direct_read_fast_path.py`'s own "ContextVar bridge never worked"
    correction-pass docstring for the identical, independently-verified
    mechanism), so each new Task got a DIFFERENT Context object than the
    one before it. `agen` (an ADK `Runner.run_async()` generator) wraps
    its own body in `with tracer.start_as_current_span('invocation'):`
    (installed `google-adk==1.33.0`, `runners.py`'s `_run_with_trace`) --
    a context manager whose `attach()` happens on whichever Task first
    resumes the generator and whose `detach()` happens on whichever Task
    resumes it LAST (at exhaustion). Handing every resumption to a fresh
    Task meant `attach()` and `detach()` almost always ran in different
    `contextvars.Context` objects, so OpenTelemetry's own
    `ContextVar.reset(token)` raised `ValueError: Token ... was created
    in a different Context` on effectively every multi-event turn --
    caught and merely logged by `opentelemetry.context.detach()`'s own
    `try/except`, so requests still succeeded, but the tracing lifecycle
    was genuinely broken. FIX: `_drain_agen` below is ONE task, created
    exactly once, that drives `agen` via a plain `async for` loop into an
    internal `asyncio.Queue` -- every resumption of `agen`, from the
    first to the last, now happens inside that SAME Task/Context, so
    `attach()`/`detach()` always pair correctly. The activity-channel
    side is unaffected -- it holds no ContextVar-sensitive state and
    keeps racing independently, exactly as before.
    """
    if activity_channel is None:
        async for event in agen:
            yield _MergedEvent("adk", event)
        return

    _ADK_ITEM = "item"
    _ADK_DONE = "done"
    _ADK_ERROR = "error"
    adk_queue: "asyncio.Queue[tuple[str, Any]]" = asyncio.Queue()

    async def _drain_agen() -> None:
        # The ONE and ONLY place `agen` is ever resumed -- see this
        # function's own docstring above. A real exception from `agen`
        # (never `StopAsyncIteration`, which `async for` already absorbs
        # as normal completion) is relayed, not swallowed, so it still
        # propagates out of `_merge_adk_and_activity_events` exactly as
        # it did before this correction.
        try:
            async for event in agen:
                await adk_queue.put((_ADK_ITEM, event))
        except BaseException as exc:  # noqa: BLE001 -- relayed to the consumer below, never swallowed
            # DEADLOCK FIX (found by the D2 focused-test/full-regression
            # pass, not merely theorized): `agen` -- e.g. a real ADK
            # Runner, or a fake one in tests that simulates a mid-stream
            # failure -- can raise `asyncio.CancelledError` directly, not
            # only via this Task being externally `.cancel()`'d. That is
            # a `BaseException`, not an `Exception` -- an `except
            # Exception:` clause here does NOT catch it, so it would
            # propagate straight out of `_drain_agen` WITHOUT ever
            # reaching either `adk_queue.put()` call below, leaving the
            # consumer's `adk_queue.get()` awaiting forever (a genuine
            # deadlock, not merely a missed error -- reproduced directly
            # by `test_chat_service_turn_context_lifecycle.py`'s own
            # `test_cleanup_after_asyncio_cancelled_error_raised_mid_run`,
            # whose fake runner does exactly this). Catching
            # `BaseException` here and relaying it through the SAME
            # queue as any other error restores the original,
            # pre-correction propagation contract exactly: the consumer
            # below re-raises whatever `agen` raised, byte-for-byte,
            # including `CancelledError`. If THIS Task is itself being
            # genuinely, externally cancelled at the same moment (the
            # `finally` block's own `task.cancel()` below), the `await
            # adk_queue.put(...)` call is safe either way -- `adk_queue`
            # is unbounded, so `put()` never truly suspends.
            await adk_queue.put((_ADK_ERROR, exc))
            return
        await adk_queue.put((_ADK_DONE, None))

    adk_task: "asyncio.Task[None]" = asyncio.ensure_future(_drain_agen())
    adk_get_task: "asyncio.Task[tuple[str, Any]]" = asyncio.ensure_future(adk_queue.get())
    queue_task: "asyncio.Task[Any]" = asyncio.ensure_future(activity_channel.get())
    try:
        while True:
            done, _pending = await asyncio.wait({adk_get_task, queue_task}, return_when=asyncio.FIRST_COMPLETED)

            if queue_task in done:
                activity_event = queue_task.result()
                yield _MergedEvent("activity", activity_event)
                queue_task = asyncio.ensure_future(activity_channel.get())

            if adk_get_task in done:
                kind, payload = adk_get_task.result()
                if kind == _ADK_ITEM:
                    yield _MergedEvent("adk", payload)
                    adk_get_task = asyncio.ensure_future(adk_queue.get())
                    continue

                # Either genuine completion or a real error -- both mean
                # the outer Runner is done producing events. A tool
                # called deep inside the LAST nested `incident_manager`
                # call may have reported activity with no `await` between
                # that call and the Runner's own final yield -- a real
                # race against `queue_task`'s own "done" propagation
                # (asyncio schedules a `put_nowait` waiter's wakeup on the
                # next loop tick, which is not guaranteed to land in the
                # SAME `asyncio.wait()` call as `adk_get_task`'s own
                # resolution). One final non-blocking drain here means a
                # genuinely-reported activity event is never silently
                # lost merely because it arrived on the very last
                # iteration.
                while not activity_channel.empty():
                    yield _MergedEvent("activity", activity_channel.get_nowait())
                if kind == _ADK_ERROR:
                    raise payload
                return
    finally:
        for task in (adk_get_task, queue_task, adk_task):
            if not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, StopAsyncIteration, Exception):
                    pass


def _active_events(events: list[Any]) -> list[Any]:
    """Mirrors ADK's own rewind-filtering algorithm exactly (verified
    against the installed 1.33.0 source,
    `google.adk.flows.llm_flows.contents._get_contents`) -- returns only
    the events that remain part of the CURRENT active conversation
    branch, with every rewound-away turn (and the rewind marker events
    themselves) excluded.

    Used here ONLY to resolve which currently-visible user turn a
    "rewind to before turn N" request refers to -- this module never
    builds model context itself; ADK's own `Runner` already does that
    identically, from the same underlying `session.events`, on every
    live turn.
    """
    result: list[Any] = []
    i = len(events) - 1
    while i >= 0:
        event = events[i]
        if event.actions and event.actions.rewind_before_invocation_id:
            target = event.actions.rewind_before_invocation_id
            for j in range(0, i):
                if events[j].invocation_id == target:
                    i = j
                    break
        else:
            result.append(event)
        i -= 1
    result.reverse()
    return result


def _resolve_invocation_id_for_active_user_turn(events: list[Any], turn_index: int) -> Optional[str]:
    """0-based: the invocation id of the `turn_index`-th user-authored
    turn still active in this session's history, in chronological order.
    `None` if `turn_index` is out of range for the currently active
    branch (e.g. a stale index from a client that hasn't refreshed, or a
    turn that was already rewound away by an earlier edit).
    """
    count = 0
    for event in _active_events(events):
        if event.author == "user" and event.content and event.content.role == "user":
            if count == turn_index:
                return event.invocation_id
            count += 1
    return None


class _ResponseMode:
    """LIVE-CORR-14 -- the closed set of FINAL RESPONSE MODES.

    THE INVARIANT THIS TYPE EXISTS TO MAKE STRUCTURAL: the already-
    validated, already-deterministic `RequestExecutionDecision` is the
    SOLE selector of which kind of response a turn produces. Specialist/
    model artifacts (`TroubleshootingGuidance`, the Runner's own free
    text, the governed-completion result) supply CONTENT for the selected
    mode -- they may never decide WHETHER the system clarifies,
    disambiguates, restricts, requests approval, answers, or fails.

    Authority order, enforced by `_select_response_mode` + the mode
    dispatch in `execute_turn_events`:

        RequestExecutionDecision > response mode > specialist/model
        content > final output validator

    PROVEN LIVE DEFECT THIS CLOSES ("give me a command to restart an
    RRU"): `derive_execution_decision` correctly produced `status=needs_
    information, missing_context=[unit_id, unit_type], may_emit_command=
    False`, yet finalization still emitted `message_completed_emitted=
    False error_code=run_failure`. Root cause was pure branch shadowing
    in the response-construction chain, not the decision: the status
    check lived INSIDE `if captured_troubleshooting_guidance is not
    None:`, and the three sibling `elif`s (`response_mode_incompatible`,
    `governed_completion_used_deterministic_fallback`, `requires_
    unstructured_response_backstop`) were each reachable only when the
    ones before them happened to be false -- so an artifact-shape
    combination (no/empty guidance, or a discarded-for-mode guidance, or
    a governed-completion deterministic fallback that left `final_text`
    unset) could consume the turn and never render the clarification the
    decision had already, correctly, required.

    NOT A NEW FRAMEWORK: these are plain string constants (mirroring
    `RequestExecutionStatus`/`RequestClass`'s own established convention
    in this codebase) used for exactly one dispatch and one log field.
    No mode grants any authority of its own, and no mode is derivable
    from anything except `decision.status`.
    """

    CLARIFICATION = "clarification"
    """`NEEDS_INFORMATION`/`AMBIGUOUS` -- render the existing natural
    clarification/disambiguation from AUTHORITATIVE missing/ambiguous
    context (`decision.missing_context`). ABSOLUTE: never consults
    guidance presence, model prose presence, or command presence."""

    RESTRICTION = "restriction"
    """`UNSUPPORTED_CAPABILITY` -- the existing deterministic restriction
    text. Its own, separate, pre-existing override (`KNOWLEDGE_INVENTORY_
    UNSUPPORTED_TEXT`, applied earlier in the same method) was ALREADY
    status-driven and is unchanged; this mode only guarantees that no
    later guidance/prose branch can shadow it."""

    APPROVAL = "approval"
    """`REQUIRES_APPROVAL` -- unchanged behavior: the approval boundary is
    its own separate state machine (proposal -> trusted approval ->
    re-authorized execution) and already owns this turn's user-facing
    proposal text/card. Nothing in the response layer rewrites it."""

    AUTHORIZED_RESPONSE = "authorized_response"
    """`ALLOW` -- the ONLY mode that consumes specialist/model content:
    structured guidance, then the governed/deterministic result, then
    safe free text, with the existing deterministic safe fallback when
    every permitted candidate is empty."""

    UNRESOLVED_CONTRACT = "unresolved_contract"
    """`INVALID_CONTRACT` -- deliberately retains the pre-existing,
    conservative pass-through behavior (no deterministic replacement, and
    the pre-existing "no final text at all" failure still applies).

    HONEST SCOPE NOTE (not an oversight): LIVE-CORR-3B already audited and
    REJECTED, against this repository's own real test suite, both
    candidate designs for turning a missing/stale contract into a
    deterministic restriction response -- see `requires_unstructured_
    response_backstop`'s own STOP CONDITION docstring for the measured
    collateral damage (dozens of pre-existing tests whose scenarios
    legitimately never record a `RequestContract`). `INVALID_CONTRACT` is
    also the status a turn that produced NO model output at all resolves
    to, and that case must keep failing closed as a genuine non-response
    (test_api_chat_service.py's own `test_no_final_text_produced_becomes_
    a_safe_error`, test_chat_service_saved_chat_marker.py's own sibling),
    which is why the hard non-empty-final-text invariant below is scoped
    to modes that represent a VALID policy state."""


_RESPONSE_MODE_BY_STATUS: dict[str, str] = {
    RequestExecutionStatus.NEEDS_INFORMATION: _ResponseMode.CLARIFICATION,
    RequestExecutionStatus.AMBIGUOUS: _ResponseMode.CLARIFICATION,
    RequestExecutionStatus.UNSUPPORTED_CAPABILITY: _ResponseMode.RESTRICTION,
    RequestExecutionStatus.REQUIRES_APPROVAL: _ResponseMode.APPROVAL,
    RequestExecutionStatus.ALLOW: _ResponseMode.AUTHORIZED_RESPONSE,
    RequestExecutionStatus.INVALID_CONTRACT: _ResponseMode.UNRESOLVED_CONTRACT,
}
"""Total over `RequestExecutionStatus`'s own closed set -- one entry per
status, no wildcards, no derivation from anything else."""


def _select_response_mode(decision: RequestExecutionDecision) -> str:
    """THE single response-mode selector (LIVE-CORR-14 section 3).

    Reads `decision.status` and NOTHING else -- never guidance presence,
    never `rendered_guidance_text`, never `command_suppressed_by_policy`,
    never `requires_unstructured_response_backstop`, never the Runner's
    own `final_text`, never a command candidate. An unknown/unexpected
    status value (structurally impossible -- `RequestExecutionDecision`
    validates `status` against `_VALID_STATUSES` at construction) falls
    closed to `UNRESOLVED_CONTRACT`, the most conservative mode.

    This function never re-derives, widens, or narrows `may_emit_command`/
    `may_execute_action`/`missing_context` -- it is a pure projection of
    an already-final decision onto the response shape that decision
    already implies.
    """
    return _RESPONSE_MODE_BY_STATUS.get(decision.status, _ResponseMode.UNRESOLVED_CONTRACT)


class ChatService:
    """`runner` is injectable so tests can exercise this entire service
    (session validation, locking, status translation, delta/final text
    extraction, pending-action mapping, response shaping) against a
    lightweight fake event stream instead of a real Gemini call -- the
    same "mock the external boundary, keep everything else real" approach
    already used throughout this backend's test suite.
    """

    def __init__(
        self,
        session_service: ApiSessionService,
        runner: Optional[_Runner] = None,
        presentation_runner: Optional[_Runner] = None,
        incident_only_runner: Optional[_Runner] = None,
        troubleshooting_only_runner: Optional[_Runner] = None,
        case_service: Optional[CaseService] = None,
        teams_contributors_resolver: Optional[Callable[[Optional[str]], Awaitable[list[str]]]] = None,
        read_continuation_executor: Optional[
            Callable[..., Awaitable[Optional[dict[str, Any]]]]
        ] = None,
        attachment_service: Optional[AttachmentService] = None,
        attachment_storage: Optional[ChatAttachmentStorage] = None,
    ) -> None:
        self._session_service = session_service
        self._runner = runner if runner is not None else _build_runner(session_service)
        # R1 FIX: injectable exactly like `runner` above -- the tools-free
        # Runner used ONLY for a turn presenting a just-validated
        # `TrustedSpecialistResult` (see `_build_presentation_runner`'s own
        # docstring and `_run_turn_events`'s runner-selection logic below).
        # Defaults to the real, `presentation_team_manager`-backed Runner
        # ONLY when NEITHER `runner` NOR `presentation_runner` was
        # explicitly given (i.e. real production construction) -- when a
        # caller injects `runner` (a test double) without also injecting
        # `presentation_runner`, this falls back to that SAME injected
        # `runner` rather than silently building a real ADK Runner (which
        # would try to make an actual, uncredentialed Gemini call in an
        # offline test): every existing test that never anticipated two
        # runners keeps working unchanged, since both code paths already
        # resolve to the one double it explicitly provided.
        if presentation_runner is not None:
            self._presentation_runner = presentation_runner
        elif runner is not None:
            self._presentation_runner = runner
        else:
            self._presentation_runner = _build_presentation_runner(session_service)
        # CONTROL-PLANE-SEQ-04 §14 -- injectable exactly like `presentation_
        # runner` immediately above, with the SAME "falls back to the
        # SAME injected `runner` double when not separately injected"
        # precedent: every existing test that injects only `runner` keeps
        # resolving every runner slot to that ONE double, never silently
        # building a real ADK Runner (an uncredentialed Gemini call) in an
        # offline test. See `_build_incident_only_runner`/`_build_
        # troubleshooting_only_runner`'s own docstrings for what each
        # represents in production.
        if incident_only_runner is not None:
            self._incident_only_runner = incident_only_runner
        elif runner is not None:
            self._incident_only_runner = runner
        else:
            self._incident_only_runner = _build_incident_only_runner(session_service)
        if troubleshooting_only_runner is not None:
            self._troubleshooting_only_runner = troubleshooting_only_runner
        elif runner is not None:
            self._troubleshooting_only_runner = runner
        else:
            self._troubleshooting_only_runner = _build_troubleshooting_only_runner(session_service)
        # Injectable exactly like `runner`/`teams_contributors_resolver`
        # above -- lets tests exercise the deterministic continuation-
        # execution path (production hardening pass #2) against a
        # lightweight fake instead of a real, network-bound `incident_
        # manager` Runner/Gemini call. Defaults to the real, authoritative
        # executor (read_continuation_execution.py).
        self._execute_read_continuation = (
            read_continuation_executor if read_continuation_executor is not None else execute_read_continuation
        )
        # Injectable exactly like `runner` above -- lets tests exercise
        # the Source drawer's `contributors` wiring against a lightweight
        # fake instead of a real (network-bound) `teams_get_members` call.
        # Defaults to the real, authoritative resolver (see
        # source_reference.py's own docstring for why `contributors` must
        # never be inferred from evidence authors).
        self._resolve_teams_contributors = (
            teams_contributors_resolver if teams_contributors_resolver is not None else resolve_authoritative_contributors
        )
        # Defaults to a fresh in-memory `CaseService` -- safe/fast for ad
        # hoc/test construction (mirrors `ApiSessionService()`'s own
        # bare-default philosophy); the real runtime default is wired only
        # by `get_chat_service()` below, via `get_case_service()`.
        self._case_service = case_service if case_service is not None else CaseService()
        # POST-5.1 B5 -- same bare-default philosophy as `case_service`
        # above: a fresh in-memory-SQLite-backed `AttachmentService`/an
        # unconfigured `ChatAttachmentStorage` (matching
        # `get_attachment_storage()`'s own "safe to construct even when
        # unset" design) for ad hoc/test construction; the real runtime
        # default is wired only by `get_chat_service()` below, via
        # `get_attachment_service()`/`get_attachment_storage()`.
        self._attachment_service = (
            attachment_service
            if attachment_service is not None
            else AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))
        )
        self._attachment_storage = attachment_storage if attachment_storage is not None else ChatAttachmentStorage(None)
        # Explicit ownership registry for background turn tasks (see
        # `execute_turn_events`) -- holds a strong reference to every
        # in-flight task so none can be silently garbage-collected
        # mid-turn, and lets tests introspect "is a turn for this session
        # still running" directly rather than inferring it from timing.
        self._background_turns: set[asyncio.Task] = set()
        # Pre-4H refinement -- real server-side run cancellation. One
        # entry per in-flight run, keyed by `(session_id, run_id)` (never
        # by `session_id` alone -- a session can have at most one ACTIVE
        # run at a time thanks to the session lock, but this keying is
        # what makes a stale/foreign `run_id` a provable, structural
        # no-op in `cancel_run` rather than an accidental cancel of
        # whatever run happens to be current). `run_id` is server-
        # generated (`EventSequencer.run_id`, a fresh uuid4 per run) and
        # already reaches the frontend via every event's own envelope, so
        # no new identifier concept is introduced here. Removed in the
        # exact same done-callback as `_background_turns`, so a finished
        # run's `run_id` can never be found (and therefore never
        # "cancelled") again.
        self._run_tasks: dict[tuple[str, str], asyncio.Task] = {}

    async def execute_turn_events(
        self,
        session_id: str,
        message_text: str,
        user_id: str = DEFAULT_USER_ID,
        attachment_ids: Sequence[str] = (),
    ) -> AsyncIterator[StreamEvent]:
        """THE canonical pipeline. PRECONDITION: the caller has already
        verified session ownership (`run_turn` below and the SSE route in
        app.py both do this identically -- `session_service.get_session
        (session_id, user_id)` -- BEFORE calling this method or opening an
        SSE response), so `run.started`, emitted first thing here, is
        honest about "session ownership already validated" (instruction
        section 17).

        WHY A BACKGROUND TASK, NOT A DIRECT `async for`/`yield` (hardening
        pass after the initial 4E implementation -- instruction: "session
        lock may be released only when the old turn can no longer mutate
        that session"): inspecting the installed Starlette 0.52.1 source
        (`StreamingResponse.__call__`) showed that on the modern ASGI
        path (`spec_version >= 2.4`, what Uvicorn negotiates today) a
        client disconnect is NOT proactively cancelled at all -- it is
        only discovered as an `OSError` the next time `stream_response`
        calls `send()`, and that exception then propagates all the way up
        through `ServerErrorMiddleware` WITHOUT anything ever calling
        `.aclose()` on the SSE endpoint's `body_iterator` chain
        (`app.py`'s `event_source()` -> this method -> `_run_turn_events`
        -> the ADK `Runner`'s own generator). The only thing that
        eventually tears that abandoned generator chain down is CPython's
        async-generator GC finalizer -- correct eventually, but not a
        deterministic release point, and NOT something the session lock's
        safety can be built on: a second same-session operation must
        never be able to start while the first's mutations could still be
        in flight, and "eventually GC runs" is not a bound this codebase
        can prove.

        The fix: the actual lock-holding, session-mutating work
        (`_run_turn_events`) runs in its OWN `asyncio.Task`
        (`_background_turns`, tracked for explicit ownership/cleanup, see
        `_log_unexpected_background_turn_exception`), completely
        independent of whatever happens to the HTTP response/consumer.
        That task acquires `self._session_service.lock_for(...)` itself,
        runs the turn to true completion (success or an internal error --
        `_run_turn_events` never raises in the normal case), and ONLY
        THEN releases the lock, in its own `finally`. This generator
        merely relays whatever the task puts on an in-memory
        `asyncio.Queue` to ITS OWN caller. If the caller (the SSE
        endpoint's `event_source()`, or a test) stops consuming --
        whether via an explicit `.aclose()` or by simply being abandoned
        -- that only stops relaying; it never cancels the background
        task, so the task's hold on the lock is never cut short by a
        disconnect, and the invariant ("the old turn can no longer mutate
        the session" before a new one may start) is guaranteed by the
        task's own `finally`, not by HTTP-layer cleanup timing.
        """
        sequencer = EventSequencer(session_id)
        # Performance pass (pre-4H latency investigation) -- constructed
        # here, at the earliest point this method has a `run_id`, so
        # "run_accepted" is genuinely the request-accepted boundary the
        # investigation asked for (not merely "the background task
        # started"). See perf_timing.py's module docstring for the full
        # safety contract: developer-log-only, never part of any SSE
        # event, never in the user-facing RunTrace.
        perf = PerfTimer(sequencer.run_id)
        yield sequencer.build(StreamEventType.RUN_STARTED, {})

        run_key = (session_id, sequencer.run_id)
        queue: "asyncio.Queue[Any]" = asyncio.Queue()

        async def _drive() -> None:
            try:
                async with self._session_service.lock_for(session_id, user_id):
                    async with Aclosing(
                        self._run_turn_events(sequencer, session_id, message_text, user_id, perf, attachment_ids)
                    ) as events:
                        async for event in events:
                            await queue.put(event)
            finally:
                # Always signals completion, even on an unexpected
                # exception OR a cancellation (see `cancel_run` below) --
                # so a caller still draining this generator (e.g.
                # `run_turn` below) can never hang waiting for an item
                # that will never come. Cancellation delivers
                # `asyncio.CancelledError` at whatever `await` this task
                # is currently suspended on (inside the `async with`
                # blocks above, or inside `_run_turn_events` itself) --
                # this `finally` still runs during that unwind (a single
                # `cancel()` call does not prevent one more `await` in a
                # `finally`), then the `CancelledError` re-raises and the
                # task ends in the "cancelled" state. Both `async with`
                # blocks release their resource (the session lock;
                # `Aclosing`'s own `.aclose()`) on ANY exit, cancellation
                # included -- nothing here is a new cancellation-safety
                # mechanism, this is Python's own `asyncio`/`async with`
                # semantics doing exactly what they already do.
                await queue.put(_TURN_DONE)

        task = asyncio.create_task(_drive())
        self._background_turns.add(task)
        self._run_tasks[run_key] = task
        task.add_done_callback(self._background_turns.discard)
        task.add_done_callback(lambda _task, key=run_key: self._run_tasks.pop(key, None))
        task.add_done_callback(_log_unexpected_background_turn_exception)

        while True:
            item = await queue.get()
            if item is _TURN_DONE:
                break
            yield item

    async def _run_turn_events(
        self,
        sequencer: EventSequencer,
        session_id: str,
        message_text: str,
        user_id: str,
        perf: Optional[PerfTimer] = None,
        attachment_ids: Sequence[str] = (),
    ) -> AsyncIterator[StreamEvent]:
        # `perf` defaults to a fresh timer so every existing/future direct
        # caller of this method (tests included) keeps working unchanged --
        # `execute_turn_events` above passes the SAME timer it constructed
        # at the true request-accepted boundary; a bare direct call (e.g.
        # a future/alternate entry point) still gets correct RELATIVE
        # stage timings, just anchored to this method's own start instead.
        if perf is None:
            perf = PerfTimer(sequencer.run_id)
        translator = StatusTranslator()
        trace_translator = RunTraceTranslator()
        trace_recorder = RunTraceRecorder(sequencer)
        source_capture = TeamsSourceCapture()
        delegation_timer = DelegationTimer()
        conversation_target_capture = ConversationTargetCapture()
        source_requirements_capture = SourceRequirementsCapture()

        # Contributor-accuracy fix + performance pass: the membership
        # fetch is started as soon as a `chat_id` becomes known DURING the
        # run loop below (see the loop body), not after it -- so its
        # Power Automate round trip overlaps with whatever generation the
        # model still has left to do, rather than adding fully on top of
        # the total turn time. `contributors_task_chat_id` tracks which
        # chat_id the in-flight/most-recent task was started for, so a
        # superseded capture (rare -- e.g. a second delegation resolves a
        # different chat later in the same turn) correctly restarts it.
        contributors_task: "Optional[asyncio.Task[list[str]]]" = None
        contributors_task_chat_id: Optional[str] = None

        # Immediate generic feedback (instruction section 11) -- emitted
        # before anything else is known to be happening, to eliminate the
        # silent period before the first real model/tool event.
        yield sequencer.build(StreamEventType.STATUS, status_data(Stage.PROCESSING, "Processing your request"))
        translator.note_external_status(Stage.PROCESSING)

        session = await self._session_service.get_session(session_id, user_id)
        perf.mark("session_loaded")

        # 6A.14A HARDENING PASS -- positive, durable canonical-result
        # ENFORCEMENT marker, established BEFORE any Runner call for this
        # turn (never during -- see CANONICAL_RESULT_ENFORCEMENT_STATE_
        # KEY's own docstring for why writing state DURING an active
        # Runner call is a proven-unsafe pattern in this codebase, and why
        # this marker is deliberately session-level rather than keyed by
        # the turn's own ADK invocation_id, which is not yet known here).
        # Idempotent: only written once per session, ever. If this ONE
        # write itself fails, the turn fails closed HERE, before any
        # specialist/model call of any kind -- so no raw assistant
        # final-response event can ever be appended for this attempt,
        # closing the fail-open history gap structurally rather than
        # relying on a second best-effort write after the fact.
        if session.state.get(CANONICAL_RESULT_ENFORCEMENT_STATE_KEY) is not True:
            try:
                await self._session_service.persist_state_delta(
                    session, {CANONICAL_RESULT_ENFORCEMENT_STATE_KEY: True}
                )
            except Exception:
                _logger.error(
                    "chat_service: canonical-result enforcement marker could not be established -- "
                    "failing the turn closed before any specialist/model execution run_id=%s",
                    sequencer.run_id,
                )
                yield sequencer.build(StreamEventType.STATUS_CLEAR, {})
                yield sequencer.build(
                    StreamEventType.ERROR,
                    {
                        "code": "run_failure",
                        "message": "The assistant could not start this request. Please try again.",
                    },
                )
                failed_trace = trace_recorder.record(**response_failed_trace_step())
                if failed_trace is not None:
                    yield failed_trace
                yield sequencer.build(StreamEventType.RUN_COMPLETED, {"outcome": "error"})
                perf.log_duration("total_run", perf.elapsed_seconds())
                return

        # Production-hardening pass: consume (single-use) whatever
        # `ResolvedReadContinuation` a prior turn's `selection_service
        # .choose()` may have stored for a resumed SelectionCard read.
        # Popped and its removal persisted UNCONDITIONALLY, before the
        # Runner ever starts -- regardless of whether this turn's model
        # actually ends up calling `incident_manager` -- so a cancelled/
        # superseded/duplicated turn can never leave it around to be
        # (mis)applied to a later, unrelated turn (see
        # `read_continuation_enforcement.py`'s own module docstring for
        # how it is applied, and `selection/service.py`'s
        # `pop_read_continuation` for why this is the single consumption
        # point). Stashing it in-process (never in persisted state) is
        # what lets `enforce_read_continuation` -- team_manager's
        # `before_tool_callback` -- find it for this SAME turn's
        # `incident_manager` call, if team_manager's model makes one.
        pending_read_continuation = pop_read_continuation(session.state)
        if pending_read_continuation is not None:
            await self._session_service.persist_state_delta(
                session, {PENDING_READ_CONTINUATION_STATE_KEY: None}
            )
            stash_active_read_continuation(session_id, pending_read_continuation)

        # Production hardening pass #4 -- hard-crash stale-trusted-result
        # protection: at the START of every turn, before this turn has
        # written anything of its own, sweep for a `TrustedSpecialistResult`
        # envelope left behind by a PRIOR run that crashed before its own
        # `finally` could clear it (a normal exit -- success, a caught
        # exception, cancellation -- already clears it below; only a hard
        # process death skips that). `sequencer.run_id` is freshly
        # generated for THIS turn, so any envelope found here can never
        # legitimately match it -- `pop_current_run_specialist_result`
        # discards it deterministically (never asking the model) and the
        # discard result is intentionally ignored: there is nothing safe
        # to do with a stale result except make sure it is gone before
        # team_manager's own turn could ever see it.
        #
        # ALSO sweeps `PENDING_SPECIALIST_RESULT_STATE_KEY` directly, not
        # only the envelope -- a crash could land AFTER that key was
        # written (it is written in a SEPARATE `persist_state_delta` call
        # right after the envelope, see below) but BEFORE `finally` runs,
        # leaving it stale independently of the envelope's own run-id
        # binding. team_manager's prompt reads this key directly, so it
        # must never be trusted to still be empty merely because this is
        # a fresh turn -- at turn start neither key can ever legitimately
        # hold anything of THIS turn's own yet, so presence alone (not
        # re-validation) is enough to know it must be discarded.
        _, stale_envelope_was_present = pop_current_run_specialist_result(
            session.state, current_run_id=sequencer.run_id
        )
        stale_pending_result_was_present = session.state.pop(PENDING_SPECIALIST_RESULT_STATE_KEY, None) is not None
        if stale_envelope_was_present or stale_pending_result_was_present:
            await self._session_service.persist_state_delta(
                session,
                {
                    TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY: None,
                    PENDING_SPECIALIST_RESULT_STATE_KEY: None,
                },
            )

        case_id = session.state.get(ACTIVE_CASE_ID_STATE_KEY)
        if case_id:
            # `active_case_id` is only a non-authoritative HINT (see
            # `case_service.py`'s module docstring) -- `_safe_case_title`
            # performs the EXACT SAME authorization check team_manager's
            # own frozen Phase 4D instruction provider performs
            # (`case_context.py`'s `CaseService.get_case(user_id,
            # case_id)`, catching `SafeErrorException` for a stale/
            # unlinked/revoked hint). The status must only be emitted when
            # that check actually SUCCEEDS -- otherwise the hint is stale,
            # the instruction provider will silently inject NO case
            # context for this exact turn, and claiming "Preparing case
            # context" here would misrepresent genuine work as having
            # happened when it did not (hardening pass finding: this used
            # to fire unconditionally on the hint's mere presence).
            case_title = await self._safe_case_title(user_id, case_id)
            if case_title is not None:
                yield sequencer.build(StreamEventType.STATUS, translate_case_context_status(case_title))
                translator.note_external_status(Stage.CASE_CONTEXT)
                case_trace = trace_recorder.record(**case_context_loaded_trace_step())
                if case_trace is not None:
                    yield case_trace
        # Marked unconditionally (not just inside the `if case_id:` branch)
        # so a case-less turn's own log line proves the near-zero cost --
        # direct evidence for the "is Case Context adding unnecessary work
        # to every general conversation" investigation.
        perf.mark("case_context_checked")

        error: Optional[tuple[str, str]] = None
        # LIVE-CORR-14.1 -- keep a response-generation failure separate from
        # the turn-level fatal error until the deterministic execution
        # decision has selected the final response mode. A response-generating
        # Runner failure must remain fatal for modes that depend on Runner/
        # specialist content, but it must not prevent a fully deterministic,
        # command-free clarification from being produced when the final policy
        # decision is NEEDS_INFORMATION/AMBIGUOUS.
        response_generation_error: Optional[tuple[str, str]] = None
        response_generation_started = False
        response_generation_completed = False
        prepared_attachments: list[PreparedAttachment] = []

        # POST-5.1 B5 -- structural "must have something" guard
        # (instruction section 9): a blank message with zero attachments
        # is invalid. Checked BEFORE attachment validation so a request
        # that fails both reasons reports the more fundamental one.
        # `error` set here (rather than raised) for the SAME reason every
        # other failure in this method uses the `error` tuple instead of
        # a bare raise -- this method has ALREADY yielded at least one
        # event (the STATUS event above) by this point, so raising here
        # would escape uncaught from `_drive()`'s `async for` in
        # `execute_turn_events` with no ERROR event ever reaching the
        # caller (verified against that method's own structure -- there
        # is no `except` around the `async for`). Setting `error` and
        # falling through to this method's own existing `if error is
        # None:` gate (below, before the Runner call) and final `if
        # error is not None: yield ERROR event` (much further down) reuses
        # the one, already-correct error-reporting path.
        if not message_text.strip() and not attachment_ids:
            error = ("validation_error", "Please include a message or an attachment.")
        elif attachment_ids:
            # Instruction section 12: every supplied attachment id is
            # validated server-side, BEFORE the Runner/Gemini ever sees
            # any of them -- existence, ownership, session, READY status,
            # MIME, and count/size limits (prepare_attachments_for_turn's
            # own docstring has the full list). Never trusts the
            # frontend's own upload/draft state.
            try:
                prepared_attachments = await prepare_attachments_for_turn(
                    attachment_service=self._attachment_service,
                    storage=self._attachment_storage,
                    settings=get_settings(),
                    user_id=user_id,
                    session_id=session_id,
                    attachment_ids=list(attachment_ids),
                )
            except SafeErrorException as exc:
                error = (exc.safe_error.error_code, exc.safe_error.user_message)

        if error is None and prepared_attachments:
            # POST-5.1 B6 -- makes this turn's own already-B5-validated
            # attachment ids available to `tools/teams/list_chats.py`'s
            # ambiguous-branch handling (deep inside a nested Incident
            # Manager call), keyed by `sequencer.run_id` -- see
            # multimodal_turn_context.py's own module docstring for why
            # this is a SEPARATE, narrower mechanism from the trusted
            # `Part`s themselves (which `MultimodalAgentTool` sources
            # directly from `tool_context.user_content`, needing no
            # registry at all). Registered here, before the Runner starts,
            # so it is already populated by the time any nested tool call
            # could read it; cleared unconditionally in this method's own
            # `finally` below, on every exit path.
            register_run_images(sequencer.run_id, [a.attachment_id for a in prepared_attachments])

        # POST-5.1 B5 -- multimodal Content construction. Text part first
        # (only when non-blank), then one `Part.from_uri(...)` per
        # validated attachment, in the EXACT order the caller supplied
        # `attachment_ids` (preserved end-to-end by
        # `prepare_attachments_for_turn` -- never SQL/dict iteration
        # order). NEVER `Part.from_bytes`/`Part.from_data`/base64 --
        # `PreparedAttachment.gcs_uri` is the ONLY durable-image
        # construction this codebase uses (see storage.py's `uri_for`
        # docstring and CLAUDE.md's own locked B0 rule). Built even on
        # the `error is not None` path (with whatever partial/empty
        # inputs exist) purely so `content` is always a defined value --
        # the `if error is None:` gate below ensures it is never actually
        # sent to a Runner on that path.
        content = types.Content(
            role="user",
            parts=[
                *([types.Part.from_text(text=message_text)] if message_text.strip() else []),
                *(
                    types.Part.from_uri(file_uri=a.gcs_uri, mime_type=a.mime_type)
                    for a in prepared_attachments
                ),
            ],
        )
        run_config = RunConfig(streaming_mode=StreamingMode.SSE)

        final_text: Optional[str] = None
        status_cleared = False
        first_event_seen = False
        # LIVE-CORR-3 -- DEF-0044 CORRECTIVE PASS -- FIXED, MANDATORY
        # BUFFERING POLICY (never a conditional/per-turn choice):
        #
        #   status/progress events  -> may stream immediately
        #   assistant/specialist text -> NEVER streamed raw, at all
        #   validated canonical response -> emitted ONCE, via
        #     `message.completed`, only after every deterministic
        #     correction/validation below AND durable canonical
        #     persistence have both succeeded
        #
        # Root cause this closes: `RunConfig(streaming_mode=StreamingMode
        # .SSE)` (above) makes the Runner yield real, incremental partial
        # events as the model produces them -- `_extract_delta_text`
        # previously turned each one into a live `message.delta` SSE
        # event, entirely INSIDE this loop, well BEFORE any of the
        # deterministic corrections further down this method run
        # (`derive_execution_decision`/`enforce_execution_decision_on_
        # guidance`/`command_suppression_fallback_text`/the KNOWLEDGE_
        # INVENTORY override/6A.14A canonical persistence) -- so even a
        # turn whose FINAL answer is later, correctly, replaced could
        # already have shown the raw, uncorrected text on screen. This
        # was a SYSTEMIC gap sitting upstream of every one of those
        # mechanisms; none of them can retroactively un-stream text
        # already rendered. Superseded by this pass: the ONLY per-turn
        # conditional streaming mechanism this method used to have (a
        # governed-knowledge-classification-gated buffer/release/discard
        # dance) is removed below, not merely bypassed -- it existed
        # purely to decide whether ALREADY-BUFFERED text should be
        # revealed live, a question that no longer has a "yes" answer
        # for ANY classification now that no turn ever reveals raw text
        # live. `_extract_delta_text`'s own return value is still
        # consulted below, ONLY to know a chunk has arrived (to clear
        # the "thinking" status indicator once) -- its TEXT is never
        # retained, buffered, or emitted. The turn's real, final text
        # is never derived from these per-chunk deltas at all (`final_
        # text` comes from `_extract_final_text`'s own separate,
        # complete/non-partial event, unchanged) -- so there is nothing
        # to lose by never accumulating them.
        #
        # Deliberately NOT optimized into conditional/partial streaming
        # in this pass (explicit instruction: "no conditional
        # conversational token streaming in this milestone") -- a real,
        # intentional, documented loss of progressive assistant-token
        # streaming, left for a later milestone's own separate, approved
        # design.
        # POST-5.1 B4B DEFECT FIX -- captured (in-memory only, no session
        # I/O) the moment the first event of a real turn is observed; the
        # ACTUAL saved-chat bookkeeping write is deferred until this
        # method's own `finally` block, once the Runner is fully done with
        # this session for this turn. See that `finally` block's own
        # comment, and session_state_keys.py's `record_user_turn_activity`
        # docstring, for the full story of why a MID-run external
        # `get_session()`/`append_event()` call is unsafe.
        turn_invocation_id: Optional[str] = None

        # Snippet-authenticity fix: binds this turn's own `run_id` into
        # `turn_context`'s ContextVar for the DURATION of the runner call
        # only -- `teams_get_messages` (running deep inside
        # `incident_manager`'s nested AgentTool call, on this SAME async
        # call chain) reads it back via `current_run_id()` to forward its
        # already-retrieved message text out to this turn, without ever
        # touching session persistence (see turn_context.py's own module
        # docstring for why session state -- `temp:`-prefixed or not --
        # was verified NOT to work for this). Always reset in `finally` so
        # a later, unrelated turn sharing this process never inherits a
        # stale `run_id`.
        message_texts_by_id: dict[str, str] = {}
        # Phase 5.1J correction pass (Part C): captured from this turn's
        # own `finally` below, BEFORE `discard_knowledge_run_evidence_state`
        # clears the run-scoped trusted evidence store -- mirrors
        # `message_texts_by_id`'s own "snapshot at cleanup time, use after
        # the try/except/finally" shape exactly. Trusted
        # `KnowledgeEvidenceItem`s the model explicitly selected this turn
        # (never every item `knowledge_search` merely returned -- SEARCH
        # RESULT != EVIDENCE USED), used below to build KM Source
        # references from authoritative backend data, never from model
        # text/agent_payload.
        selected_knowledge_evidence: list[Any] = []
        # A5 final corrective pass: same snapshot-before-discard shape as
        # `selected_knowledge_evidence` immediately above -- the
        # completion-boundary override that consumes this lives AFTER
        # this turn's own `finally` block (which must unconditionally
        # clear the run-scoped store on every exit path), so the value
        # must be captured into this local BEFORE that clear, never
        # re-popped from the (by then already-cleared) store later.
        captured_troubleshooting_guidance: Optional[TroubleshootingGuidance] = None
        # CONTROL-PLANE-SEQ-04A -- populated (if at all) by the mandatory
        # preflight's own deterministic governed-read stage, BEFORE the
        # response-generating Runner ever runs -- see that block's own
        # comment, below, for the full rationale. `preflight_governed_
        # read_attempted` alone (regardless of outcome) is what the
        # runner-selection step consults to prevent a duplicate `incident_
        # manager_tool` invocation for the SAME governed-read requirement
        # (§6); `preflight_governed_selected_evidence`/`preflight_governed_
        # troubleshooting_guidance` are merged into `selected_knowledge_
        # evidence`/`captured_troubleshooting_guidance` themselves further
        # below (this turn's own main-Runner `finally` block), never
        # overwritten by it.
        preflight_governed_read_attempted = False
        preflight_governed_selected_evidence: list[Any] = []
        preflight_governed_troubleshooting_guidance: Optional[TroubleshootingGuidance] = None
        # CONTROL-PLANE-SEQ-06 -- section 2's own residual command-
        # inventory gap: every exact command value THIS turn's own
        # grounding (evidence.py) genuinely possessed and structurally
        # rejected, across ALL of this turn's own grounding calls (main
        # Runner, SEQ-04A preflight governed read, and the reactive
        # governed-completion remediation alike) -- accumulated here,
        # folded into `known_commands_this_turn` (further below, alongside
        # `execution_decision`) so the final-output validator can
        # recognize the SAME already-rejected value if it separately
        # survives in free-form response text. See rejected_command_
        # context.py's own module docstring for the full "additive
        # side-channel, never a new grounding judgment" design.
        known_rejected_commands_this_turn: set[str] = set()
        run_id_token = bind_run_id(sequencer.run_id)
        # Phase 2 (Runtime Activity Truthfulness): registered in the SAME
        # place, for the SAME reason, as `bind_run_id` above -- this
        # turn's own bounded activity channel must exist before the
        # Runner (or a governed-completion/read-continuation remediation
        # call that reuses this same run_id, see activity_queue.py's own
        # docstring) can report into it. Discarded, unconditionally, in
        # this method's existing `finally` block below, alongside
        # `discard_knowledge_run_evidence_state`.
        register_activity_channel(sequencer.run_id)
        # Production hardening pass #3: tracks whether `PENDING_
        # SPECIALIST_RESULT_STATE_KEY` was actually written this turn, so
        # the `finally` block below only issues a clearing write when
        # there is genuinely something to clear -- an unconditional clear
        # on every turn would append a needless extra event to every
        # session's history, not just continuation-driven ones.
        specialist_result_state_written = False
        # P4B.3 URGENT SOURCE/PROVENANCE REGRESSION FIX: captured from the
        # early `finally` below (BEFORE `discard_pending_trusted_result`
        # clears it) and consulted much later, when deciding whether to
        # build a `SourceReference` -- see that `finally` block's own
        # comment for why this must be a local snapshot, not a live check
        # against direct_read_fast_path.py's own registry at that later
        # point (which would already have been cleared).
        direct_fast_path_trust_validation_failed = False
        try:
            perf.mark("runner_invocation_start")

            # Production hardening pass #2: if a `ResolvedReadContinuation`
            # was consumed for this turn (popped above, at session load),
            # its Incident Manager/read execution is now UNCONDITIONAL --
            # never left to team_manager's own model to decide whether to
            # delegate. Runs BEFORE team_manager's own turn, inside this
            # SAME cancellation-safe try/finally (a cancellation here
            # propagates exactly like a cancellation during team_manager's
            # own runner call below always already has -- see this
            # method's own `finally` comment). See read_continuation_
            # execution.py's module docstring for the full ADK-1.33.0-
            # verified mechanism.
            #
            # POST-5.1 B5: `error is None` added -- a turn whose
            # attachment validation (or the blank-message-and-no-
            # attachment guard) already failed above must never still run
            # a read-continuation resolution; nothing below this point
            # should execute at all once `error` is set.
            if error is None and pending_read_continuation is not None:
                call_event = synthetic_incident_manager_call_event(
                    pending_read_continuation.selected_chat_topic
                )
                status = translator.translate_event(call_event)
                if status is not None:
                    yield sequencer.build(StreamEventType.STATUS, status)
                trace_step = trace_translator.translate_event(call_event)
                if trace_step is not None:
                    trace_event = trace_recorder.record(**trace_step)
                    if trace_event is not None:
                        yield trace_event
                delegation_timer.observe(call_event)

                specialist_result = await self._execute_read_continuation(
                    session_service=self._session_service.adk_session_service,
                    user_id=user_id,
                    parent_session_id=session_id,
                    run_id=sequencer.run_id,
                    parent_state=dict(session.state),
                    continuation=pending_read_continuation,
                    # POST-5.1 B6 -- same trusted attachment plumbing this
                    # turn's own new-send path already uses; lets a
                    # resumed continuation re-validate and re-attach its
                    # own trusted image evidence (see read_continuation_
                    # execution.py's own module docstring).
                    attachment_service=self._attachment_service,
                    attachment_storage=self._attachment_storage,
                )
                perf.mark("read_continuation_executed")

                if specialist_result is None:
                    # Safe failure (section 11): never fall back to team_
                    # manager's own turn with stale/no data -- that risks
                    # exactly the hallucinated-summary outcome this pass
                    # must prevent. Reuses the SAME generic, sanitized
                    # failure this method already uses for any other
                    # unexpected runtime condition.
                    error = (
                        "run_failure",
                        "The assistant could not complete this request. Please try again.",
                    )
                else:
                    await self._session_service.persist_state_delta(
                        session, compute_state_updates(specialist_result)
                    )
                    response_event = synthetic_incident_manager_response_event(specialist_result)
                    status = translator.translate_event(response_event)
                    if status is not None:
                        yield sequencer.build(StreamEventType.STATUS, status)
                    trace_step = trace_translator.translate_event(response_event)
                    if trace_step is not None:
                        trace_event = trace_recorder.record(**trace_step)
                        if trace_event is not None:
                            yield trace_event
                    source_capture.observe(response_event)
                    delegation_timer.observe(response_event)

                    current_chat_id = source_capture.captured_chat_id()
                    if current_chat_id and current_chat_id != contributors_task_chat_id:
                        contributors_task_chat_id = current_chat_id
                        contributors_task = asyncio.create_task(
                            self._resolve_teams_contributors(current_chat_id)
                        )

                    # Production hardening pass #3/#4: team_manager's own
                    # turn still runs next with its ORIGINAL new_message
                    # (`content` is left untouched -- never overwritten
                    # with a text marker) -- it stays the only user-facing
                    # agent (section 3). The already-validated result is
                    # instead handed off through a run-id-bound
                    # `TrustedSpecialistResult` envelope (never through
                    # anything a user's own message could contain, and
                    # never exposed to team_manager's prompt except via
                    # the SAME deterministic `validate_trusted_envelope_
                    # for_run` check the turn-start crash-recovery sweep
                    # above already uses -- no special-cased "trust this
                    # because I just wrote it" shortcut). See read_
                    # continuation_presentation.py's module docstring for
                    # the full trust-boundary/hard-crash rationale. Both
                    # keys are cleared again in this method's own
                    # `finally` below regardless of outcome.
                    envelope = build_trusted_specialist_result_envelope(sequencer.run_id, specialist_result)
                    if envelope is None:
                        # Re-validation failure (should never happen for a
                        # value `execute_read_continuation` already
                        # validated once -- fails closed regardless).
                        error = (
                            "run_failure",
                            "The assistant could not complete this request. Please try again.",
                        )
                    else:
                        await self._session_service.persist_state_delta(
                            session, {TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY: envelope}
                        )
                        validated_for_presentation = validate_trusted_envelope_for_run(
                            envelope, current_run_id=sequencer.run_id
                        )
                        await self._session_service.persist_state_delta(
                            session, {PENDING_SPECIALIST_RESULT_STATE_KEY: validated_for_presentation}
                        )
                        # LIVE-CORR-12C -- Fresh RequestContract on
                        # Presentation Turns: this turn is about to run
                        # `presentation_team_manager` (tools=[]), which
                        # structurally cannot call `record_request_contract`
                        # -- without this, `VALIDATED_REQUEST_CONTRACT_
                        # STATE_KEY` would be left holding a PRIOR turn's
                        # own contract, stamped with THAT turn's own
                        # `run_id`, and `derive_execution_decision` would
                        # correctly (but misleadingly) reject it as
                        # `INVALID_CONTRACT` for THIS turn. A resumed
                        # `ResolvedReadContinuation` is already fully
                        # deterministic (see `build_deterministic_read_
                        # continuation_contract`'s own module-level
                        # comment) -- this contract is synthesized directly,
                        # never via a model call, so `presentation_team_
                        # manager`'s own tool-free design is untouched.
                        await self._session_service.persist_state_delta(
                            session,
                            {
                                VALIDATED_REQUEST_CONTRACT_STATE_KEY: build_deterministic_read_continuation_contract(
                                    run_id=sequencer.run_id
                                ).model_dump(mode="json")
                            },
                        )
                        specialist_result_state_written = True

            # ================================================================
            # CONTROL-PLANE-SEQ-04 -- Deterministic Work Envelope +
            # Authorized Routing (supersedes SEQ-03's own Phase-A-decision-
            # plus-exception-predicate mechanism as the NORMAL-path
            # routing authority -- see request_execution_policy.py's own
            # "CONTROL-PLANE-SEQ-04" module comment for the full
            # architectural rationale).
            # ================================================================
            #
            # For a NORMAL turn (never a `ResolvedReadContinuation` turn --
            # that path already has its own deterministic, already-
            # authorized contract and specialist read, see the block
            # immediately above / LIVE-CORR-12C), guarantee BEFORE
            # `incident_manager_tool`/`troubleshooting_manager` are even
            # reachable:
            #   1. a fresh, `run_id`-fresh current-turn `RequestContract`
            #      (reusing `request_current_turn_contract`, request_
            #      contract_completion.py -- the EXACT SAME bounded,
            #      tools=[record_request_contract]-only remediation
            #      machinery LIVE-CORR-12I already established, simply
            #      invoked FIRST instead of reactively);
            #   2. a source-requirements declaration (reusing `request_
            #      source_requirements_declaration`, source_requirements_
            #      completion.py, unmodified -- kept as a SEPARATE bounded
            #      call, deliberately NOT merged into one model turn with
            #      the contract call: correct sequencing, not call-count
            #      reduction, is this milestone's own stated priority);
            #   3. governed-evidence-continuity permission (`is_governed_
            #      evidence_continuity_permitted`, LIVE-CORR-12D, semantics
            #      completely unmodified -- moved earlier only);
            #   4. a deterministic `WorkEnvelope` (`derive_work_envelope`,
            #      request_execution_policy.py) -- WHAT WORK may be
            #      attempted this turn, never WHAT MAY REACH THE USER (that
            #      remains exclusively the SEPARATE, later Phase-B `derive_
            #      execution_decision` call, unchanged, §19/§20 of this
            #      pass's own instruction). `derive_work_envelope` reuses
            #      `derive_execution_decision`/`is_deferred_target_
            #      resolution` INTERNALLY (never duplicated) -- this call
            #      site itself no longer consults either directly (§23).
            #
            # A failed/absent preflight contract naturally, correctly
            # reproduces a blocked (`work_permitted=False`) envelope (the
            # SAME freshness check `derive_execution_decision` already,
            # unconditionally, performs, one layer inside `derive_work_
            # envelope`) -- no separate `error` branch is needed here; the
            # gate below routes that turn to the tools=[] presentation
            # runner exactly like every other hard-gated envelope outcome,
            # and the EXISTING LIVE-CORR-12I/12I.1 post-run remediation
            # (untouched, still present) remains available as a fallback
            # for a NEXT turn's own continuity even though THIS turn's own
            # operational orchestration is correctly skipped.
            work_envelope = WorkEnvelope()
            if error is None and not specialist_result_state_written:
                preflight_question = _remediation_question(message_text)
                try:
                    preflight_contract = await request_current_turn_contract(
                        question=preflight_question,
                        user_content=content,
                        run_id=f"{sequencer.run_id}::preflight-contract",
                        current_run_id=sequencer.run_id,
                    )
                except Exception:
                    _logger.warning(
                        "chat_service: contract preflight raised -- failing closed run_id=%s", sequencer.run_id
                    )
                    preflight_contract = None

                if preflight_contract is not None:
                    await self._session_service.persist_state_delta(
                        session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: preflight_contract.model_dump(mode="json")}
                    )

                try:
                    preflight_declaration = await request_source_requirements_declaration(
                        question=preflight_question, run_id=f"{sequencer.run_id}::preflight-declaration"
                    )
                except Exception:
                    _logger.warning(
                        "chat_service: source-requirements preflight raised -- failing closed run_id=%s",
                        sequencer.run_id,
                    )
                    preflight_declaration = None
                if preflight_declaration is not None:
                    requires_teams_declared, requires_governed_knowledge_declared = preflight_declaration
                    source_requirements_capture.record_external_declaration(
                        requires_teams_declared, requires_governed_knowledge_declared
                    )
                # `declared=False` here (preflight declaration failed) is
                # NOT itself a hard gate -- the EXISTING, unmodified post-
                # run source-requirements remediation (below, later in this
                # method) remains the fallback for exactly this case,
                # preserving today's own fail-closed completion-gate
                # behavior rather than duplicating it here.

                preflight_pending_governed_request = parse_pending_governed_request(
                    session.state.get(PENDING_GOVERNED_REQUEST_STATE_KEY)
                )
                preflight_governed_evidence_continuity_permitted = is_governed_evidence_continuity_permitted(
                    preflight_contract, sequencer.run_id, preflight_pending_governed_request
                )
                _logger.info(
                    "chat_service: preflight contract_run_id=%s fresh=%s governed_evidence_continuity=%s run_id=%s",
                    preflight_contract.run_id if preflight_contract is not None else None,
                    preflight_contract is not None and preflight_contract.run_id == sequencer.run_id,
                    "reused_same_request" if preflight_governed_evidence_continuity_permitted else "superseded_new_request",
                    sequencer.run_id,
                )

                # CONTROL-PLANE-SEQ-04 §5's own EXACT_COMMAND circularity
                # fix, and §9-13's own per-class routing rules, both live
                # entirely inside this one deterministic call -- see
                # `derive_work_envelope`'s own docstring for the complete
                # derivation (never duplicated here).
                work_envelope = derive_work_envelope(
                    preflight_contract,
                    sequencer.run_id,
                    pending_governed_request=preflight_pending_governed_request,
                    requires_governed_knowledge_declared=source_requirements_capture.requires_governed_knowledge,
                    requires_teams_declared=source_requirements_capture.requires_teams,
                )
                _logger.info(
                    "chat_service: work_envelope maximum_authority=%s work_permitted=%s "
                    "may_route_incident_manager=%s may_route_troubleshooting_manager=%s run_id=%s",
                    work_envelope.maximum_authority,
                    work_envelope.work_permitted,
                    work_envelope.may_route_incident_manager,
                    work_envelope.may_route_troubleshooting_manager,
                    sequencer.run_id,
                )

                # ============================================================
                # CONTROL-PLANE-SEQ-04A -- Deterministic Authorized-Read Stage
                # ============================================================
                #
                # THE GAP THIS CLOSES: `work_envelope.requires_governed_
                # knowledge=True` alone did not yet cause a governed read to
                # actually happen BEFORE the response-generating team_manager
                # turn -- the ONLY existing mechanism forcing one
                # (`governed_completion_needed`, further below in this
                # method) is still purely REACTIVE, running only AFTER that
                # turn already had its own chance to decide (unreliably)
                # whether to delegate. This block makes the SAME, completely
                # UNMODIFIED `enforce_governed_knowledge_at_completion` (§2 --
                # never a second Incident Manager stack, never duplicated
                # retrieval/selection/grounding/provenance/applicability
                # logic) the NORMAL, deterministic authorized-read path
                # whenever it is actually required, BEFORE any response-
                # generating Runner call.
                #
                # NEVER pays this path for a non-governed turn (§8): gated on
                # `work_envelope.requires_governed_knowledge`, which is
                # already unconditionally `False` for `GENERAL_CONVERSATION`
                # (derive_work_envelope, §9) and `False` whenever this turn's
                # own source-requirements declaration did not ask for it --
                # preserves EXISTING routing for every other request shape
                # untouched.
                #
                # GOVERNED-EVIDENCE CONTINUITY APPLIED PROACTIVELY (§5): uses
                # the ALREADY-computed `preflight_governed_evidence_
                # continuity_permitted` (LIVE-CORR-12D, unmodified predicate)
                # to decide whether prior evidence/anchor are even read at
                # all -- byte-for-byte the SAME gating the reactive call
                # below already applies, just evaluated once, earlier.
                #
                # EFFECTIVE GOVERNED REQUEST DRIVES THE READ, NEVER
                # REDEFINED (§4): `preflight_question`/`preflight_contract.
                # subject` are the SAME already-validated values `work_
                # envelope` was itself derived from -- this block performs
                # no independent request-class/authority/missing-context
                # judgment of its own.
                #
                # NO NEW PERSISTENT SPECIALIST STATE MODEL (§3): results are
                # held in plain local variables only (`preflight_governed_
                # selected_evidence`/`preflight_governed_troubleshooting_
                # guidance`/`preflight_governed_final_text`) -- merged into
                # this turn's own EXISTING `selected_knowledge_evidence`/
                # `captured_troubleshooting_guidance` locals further below
                # (this turn's own main-Runner `finally` block), never a new
                # durable session-state envelope.
                #
                # A deterministic FALLBACK/failure result from this call
                # (`pop_governed_completion_deterministic_fallback`, the
                # SAME existing signal the reactive call already relies on)
                # is deliberately NOT treated as "governed read performed" --
                # `selected_evidence`/`troubleshooting_guidance` stay empty,
                # so the EXISTING reactive `governed_completion_needed` gate
                # (§13, kept as defense-in-depth, unmodified) still correctly
                # detects missing evidence and runs its own, already-correct,
                # already-tested fallback logic for that shape -- this block
                # never tries to reproduce or improve on it.
                preflight_governed_final_text: Optional[str] = None
                preflight_governed_read_performed = False
                if work_envelope.work_permitted and work_envelope.requires_governed_knowledge:
                    preflight_governed_read_attempted = True
                    preflight_governed_run_id = f"{sequencer.run_id}::preflight-governed-read"
                    try:
                        preflight_chat_topic = (
                            session.state.get(SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY)
                            if source_requirements_capture.requires_teams
                            else None
                        )
                        preflight_prior_governed_evidence = (
                            parse_last_selected_governed_evidence(
                                session.state.get(LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY)
                            )
                            if preflight_governed_evidence_continuity_permitted
                            else []
                        )
                        preflight_active_procedure_anchor = (
                            parse_active_governed_procedure(session.state.get(ACTIVE_GOVERNED_PROCEDURE_STATE_KEY))
                            if preflight_governed_evidence_continuity_permitted
                            else None
                        )
                        preflight_request_contract_subject = (
                            preflight_contract.subject
                            if preflight_contract is not None and not preflight_contract.ambiguity
                            else None
                        )
                        (
                            preflight_governed_final_text,
                            preflight_governed_selected_evidence,
                        ) = await enforce_governed_knowledge_at_completion(
                            question=preflight_question,
                            chat_topic=preflight_chat_topic,
                            run_id=preflight_governed_run_id,
                            image_parts=trusted_image_parts_from_content(content),
                            prior_governed_evidence=preflight_prior_governed_evidence,
                            active_anchor=preflight_active_procedure_anchor,
                            request_contract_subject=preflight_request_contract_subject,
                        )
                        preflight_governed_troubleshooting_guidance = pop_troubleshooting_guidance(
                            preflight_governed_run_id
                        )
                        discard_troubleshooting_guidance(preflight_governed_run_id)
                        # CONTROL-PLANE-SEQ-06 -- same pop/discard pairing
                        # as troubleshooting_guidance immediately above,
                        # for the SAME `preflight_governed_run_id`.
                        known_rejected_commands_this_turn.update(pop_rejected_commands(preflight_governed_run_id))
                        discard_rejected_commands(preflight_governed_run_id)
                        preflight_governed_used_deterministic_fallback = pop_governed_completion_deterministic_fallback(
                            preflight_governed_run_id
                        )
                        discard_governed_completion_deterministic_fallback(preflight_governed_run_id)
                        preflight_governed_read_performed = not preflight_governed_used_deterministic_fallback
                        _logger.info(
                            "chat_service: preflight_governed_read performed=%s deterministic_fallback=%s "
                            "selected_evidence_count=%s run_id=%s",
                            preflight_governed_read_performed,
                            preflight_governed_used_deterministic_fallback,
                            len(preflight_governed_selected_evidence),
                            sequencer.run_id,
                        )
                    except Exception:
                        _logger.warning(
                            "chat_service: preflight governed read raised -- deferring to the existing "
                            "reactive completion gate run_id=%s",
                            sequencer.run_id,
                        )
                        preflight_governed_selected_evidence = []
                        preflight_governed_troubleshooting_guidance = None
                        preflight_governed_final_text = None
                        preflight_governed_read_performed = False
                        discard_troubleshooting_guidance(preflight_governed_run_id)
                        discard_governed_completion_deterministic_fallback(preflight_governed_run_id)
                        discard_rejected_commands(preflight_governed_run_id)

                # CONTROL-PLANE-SEQ-04 §17 -- AUTHORITATIVE CURRENT-TURN
                # REQUEST CONTEXT: a plain, deterministic, NEVER-persisted
                # (`temp:`-prefixed -- see that module's own docstring for
                # the ADK-source-verified "apply, then trim" mechanism this
                # relies on) prompt block the downstream operational turn's
                # own instruction provider (`team_manager_instruction_
                # provider`, case_context.py) appends. A no-op (nothing
                # written) whenever there is no validated contract to
                # summarize. CONTROL-PLANE-SEQ-04A §9: also carries this
                # turn's own already-retrieved governed specialist result
                # (when one was genuinely produced, never a deterministic
                # fallback/failure text), so the response-generating turn
                # can synthesize/explain from it directly instead of
                # re-requesting the same retrieval it can no longer even
                # reach (§6, below).
                authoritative_context_block = render_authoritative_current_turn_request_context(
                    preflight_contract,
                    work_envelope,
                    governed_specialist_result=(
                        preflight_governed_final_text if preflight_governed_read_performed else None
                    ),
                )
                if authoritative_context_block:
                    await self._session_service.persist_state_delta(
                        session, {AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY: authoritative_context_block}
                    )

            if error is None:
                # R1 FIX (correctness-regression pass): `specialist_result_
                # state_written` is set ONLY after `validate_trusted_
                # envelope_for_run` (R1.1's own same-run-id, server-side
                # check) accepted this turn's own, just-executed
                # `ResolvedReadContinuation` result -- never from user text,
                # model output, or stale state (R1.1). This is exactly the
                # one turn `presentation_team_manager` (agent.py, `tools=
                # []`) exists for: structurally, not just by prompt
                # wording, forbidding any further delegation.
                #
                # CONTROL-PLANE-SEQ-04: routing is now driven ENTIRELY by
                # `work_envelope` -- never a raw Phase-A status/exception-
                # predicate check at this call site (§23). GENERAL_
                # CONVERSATION (`maximum_authority==CONVERSATIONAL_
                # RESPONSE`) and every hard-blocked outcome
                # (`work_permitted=False`) both reuse the SAME tools=[]
                # `presentation_team_manager` runner -- `team_manager_
                # instruction_provider` (case_context.py) already,
                # independently, falls back to the ordinary `TEAM_MANAGER_
                # INSTRUCTION` whenever `PENDING_SPECIALIST_RESULT_STATE_
                # KEY` is absent (verified: this IS absent for this branch,
                # since `specialist_result_state_written` is `False` here),
                # so this reuse never risks a "present a trusted result
                # that doesn't exist" prompt mismatch. Otherwise, `work_
                # envelope.may_route_incident_manager`/`may_route_
                # troubleshooting_manager` select the correct capability-
                # filtered operational Runner -- structural (schema-level
                # tool removal, §14), never merely instructed -- mirroring
                # `operational_team_manager`/`presentation_team_manager`'s
                # own established `.model_copy` precedent one layer up (see
                # agent.py's own `select_operational_team_manager`, and
                # this class's own `_build_incident_only_runner`/`_build_
                # troubleshooting_only_runner`, for the construction side).
                # Whatever free-form text any of these restricted calls
                # produces is, exactly as for any other unauthorized-
                # command shape, unconditionally replaced by the EXISTING,
                # unmodified `requires_unstructured_response_backstop`/
                # `command_suppression_fallback_text`/clarification-
                # renderer machinery further down this method, driven by
                # the SEPARATE, later Phase-B `execution_decision` -- never
                # by any of these restricted calls' own output.
                #
                # CONTROL-PLANE-SEQ-04A §6 -- DUPLICATE-ROUTING PREVENTION:
                # `effective_may_route_incident_manager` is `work_envelope.
                # may_route_incident_manager` UNLESS the deterministic
                # governed-read stage immediately above already ATTEMPTED
                # (regardless of outcome -- a failed/ambiguous attempt must
                # not be silently retried via the model's own delegation
                # choice either) the SAME turn's own governed-read
                # requirement, in which case `incident_manager_tool` is
                # structurally removed from the response-generating Runner
                # too -- never merely left available and hoped-unused.
                # `work_envelope.may_route_troubleshooting_manager` is
                # COMPLETELY UNCHANGED by this (§7) -- that specialist's own
                # reachability never depends on whether Incident Manager
                # retrieval already ran.
                effective_may_route_incident_manager = (
                    work_envelope.may_route_incident_manager and not preflight_governed_read_attempted
                )
                if specialist_result_state_written:
                    turn_runner = self._presentation_runner
                elif not work_envelope.work_permitted or work_envelope.maximum_authority == WorkAuthority.CONVERSATIONAL_RESPONSE:
                    turn_runner = self._presentation_runner
                elif effective_may_route_incident_manager and work_envelope.may_route_troubleshooting_manager:
                    turn_runner = self._runner
                elif effective_may_route_incident_manager:
                    turn_runner = self._incident_only_runner
                elif work_envelope.may_route_troubleshooting_manager:
                    turn_runner = self._troubleshooting_only_runner
                else:
                    turn_runner = self._presentation_runner
                if specialist_result_state_written:
                    # Safe diagnostic only (never a chat id, message body,
                    # or trusted payload) -- mirrors perf_timing.py's own
                    # safe-logging contract.
                    _logger.info(
                        "perf stage=trusted_result_presentation_mode run_id=%s", sequencer.run_id
                    )
                elif turn_runner is self._presentation_runner:
                    _logger.info(
                        "perf stage=work_envelope_gated_presentation_mode run_id=%s", sequencer.run_id
                    )
                response_generation_started = True
                async with Aclosing(
                    turn_runner.run_async(
                        user_id=user_id, session_id=session_id, new_message=content, run_config=run_config
                    )
                ) as agen:
                    async for _merged in _merge_adk_and_activity_events(
                        agen, get_activity_channel(sequencer.run_id)
                    ):
                        if _merged.source == "activity":
                            # Phase 2 (Runtime Activity Truthfulness): a
                            # real, observed inner Knowledge/Teams tool
                            # boundary -- never an ADK `Event`, so none of
                            # the below (SSE trust-gate classification,
                            # source/delegation/conversation-target
                            # capture, trace translation, attachment
                            # linkage, ...) applies; it can ONLY ever
                            # produce an ephemeral status, never influence
                            # message-text trust or provenance (instruction
                            # section 11's absolute non-regression
                            # requirement).
                            activity_status = translator.translate_activity_event(_merged.item)
                            if activity_status is not None:
                                yield sequencer.build(StreamEventType.STATUS, activity_status)
                            continue
                        event = _merged.item
                        if not first_event_seen:
                            first_event_seen = True
                            perf.mark("first_model_event")
                            # POST-5.1 B4B DEFECT FIX -- in-memory capture
                            # ONLY. A LIVE PRODUCTION INCIDENT (real
                            # Vertex/Gemini turn, function-call tool use)
                            # proved that calling `get_session()`/
                            # `append_event()` HERE -- while `turn_runner
                            # .run_async`'s own generator is still
                            # actively producing more events for this SAME
                            # invocation -- corrupts ADK's session-
                            # revision tracking out from under the
                            # Runner's OWN internal session handle. The
                            # Runner appends each of its own events
                            # (`runners.py`: `append_event(session=
                            # session, ...)` THEN `yield`) using ONE
                            # session object held for the entire
                            # invocation; an external `get_session()` +
                            # `append_event()` call in between two of the
                            # Runner's own appends bumps the stored
                            # revision without the Runner's own handle
                            # ever finding out, so the Runner's NEXT
                            # internal append (e.g. the function's own
                            # response, or the continuation call) hits
                            # `DatabaseSessionService`'s "the session has
                            # been modified in storage" staleness
                            # `ValueError` -- silently swallowed by this
                            # method's own outer `except Exception:`
                            # below, surfacing to the user as the generic
                            # "The assistant could not complete this
                            # request" with NO further model continuation
                            # and no logged cause. Proven both by direct
                            # inspection of the installed ADK 1.33.0
                            # source and by a disposable local
                            # reproduction (`DatabaseSessionService` +
                            # SQLite): an external append between two
                            # Runner-held-session appends reliably
                            # reproduces the exact same `ValueError`.
                            #
                            # THE FIX: `record_user_turn_activity` (the
                            # actual bookkeeping write) is now called from
                            # this method's own `finally` block instead --
                            # strictly AFTER `turn_runner.run_async`'s
                            # generator has fully closed (success,
                            # failure, or cancellation all reach it) and
                            # can therefore no longer be using its own
                            # session handle for anything. Session-lock
                            # discipline is preserved: `execute_turn_
                            # events`'s own caller holds `lock_for(...)`
                            # for this method's ENTIRE execution, `finally`
                            # included, so no other turn on this session
                            # can race the finalization write either.
                            #
                            # `event.invocation_id` is this turn's real,
                            # ADK-assigned invocation id -- captured here
                            # (a pure in-memory read, no I/O) so `finally`
                            # can look up the matching genuine user event
                            # and its own real `.timestamp` once it is
                            # safe to do so.
                            turn_invocation_id = event.invocation_id

                            # POST-5.1 B5 -- atomic bulk attachment
                            # linkage, INLINE at this exact point (never
                            # deferred to `finally` like the B4B
                            # bookkeeping fix above needed) -- proven safe
                            # by direct source inspection:
                            # `AttachmentService.link_many_to_message`
                            # writes ONLY to the separate
                            # `slopanoc_chat_attachments` table via a plain
                            # SQLAlchemy UPDATE, never through
                            # `session_service.append_event()`; ADK's own
                            # session-revision marker
                            # (`StorageSession.get_update_marker()`,
                            # `google/adk/sessions/schemas/v1.py`, verified
                            # against the installed 1.33.0 source) is
                            # derived EXCLUSIVELY from the `sessions`
                            # table's own `update_time` column, which this
                            # write can never touch -- there is no
                            # trigger/FK/computed relationship between the
                            # two tables, and this call never goes through
                            # `DatabaseSessionService` at all. The real
                            # user turn (text and/or image Content) is
                            # already durably persisted at this point (the
                            # same B4A happens-before proof `record_user_
                            # turn_activity` relies on), so an attachment
                            # only ever becomes LINKED once it genuinely
                            # belongs to an ACTUAL persisted user turn
                            # (instruction section 22) -- never merely
                            # because the HTTP request arrived or
                            # validation passed.
                            if prepared_attachments:
                                try:
                                    await self._attachment_service.link_many_to_message(
                                        [a.attachment_id for a in prepared_attachments],
                                        user_id,
                                        session_id,
                                        turn_invocation_id,
                                    )
                                except Exception:
                                    # Instruction section 25: a link
                                    # failure AFTER the genuine user turn
                                    # exists must never present a
                                    # successful-looking response -- fail
                                    # closed with the existing safe error
                                    # contract, and stop consuming further
                                    # Runner events (no further model/tool
                                    # work is presented as having
                                    # succeeded). The user turn itself
                                    # still correctly stays durable/visible
                                    # (this method's own `finally` block
                                    # still runs, unaffected, below) --
                                    # only the attachment stays READY
                                    # (never fraudulently LINKED) and this
                                    # turn's own response is reported as a
                                    # failure.
                                    error = (
                                        "run_failure",
                                        "The assistant could not complete this request. Please try again.",
                                    )
                                    break

                        status = translator.translate_event(event)
                        if status is not None:
                            yield sequencer.build(StreamEventType.STATUS, status)

                        trace_step = trace_translator.translate_event(event)
                        if trace_step is not None:
                            trace_event = trace_recorder.record(**trace_step)
                            if trace_event is not None:
                                yield trace_event

                        source_capture.observe(event)
                        delegation_timer.observe(event)
                        conversation_target_capture.observe(event)
                        source_requirements_capture.observe(event)

                        # LIVE-CORR-3 -- DEF-0044: the buffer/release/
                        # discard reconciliation that previously lived here
                        # (gated on `source_requirements_capture`'s
                        # UNKNOWN/EXPLICIT-GOVERNED/EXPLICIT-NON-GOVERNED
                        # states) is REMOVED, not merely bypassed -- it
                        # existed only to decide whether text buffered
                        # while classification was still UNKNOWN should be
                        # revealed live once resolved. Under this pass' own
                        # fixed, unconditional buffering policy, text is
                        # NEVER revealed live regardless of classification,
                        # so that question has no "yes" answer left to
                        # compute -- `source_requirements_capture` itself,
                        # and every OTHER use of it later in this method
                        # (e.g. the governed-knowledge completion gate),
                        # is completely unaffected; only its former role in
                        # THIS streaming-presentation decision is gone.

                        # Contributor-accuracy fix + performance pass: start
                        # (or restart, for a superseded chat_id) the
                        # membership fetch the MOMENT a chat_id becomes known,
                        # not after this whole loop finishes -- see this
                        # method's own comment above `contributors_task` for
                        # why this is safe/correct. A stale in-flight task for
                        # an old chat_id is best-effort cancelled: it cannot
                        # actually interrupt an already-in-flight worker-thread
                        # HTTP call (same documented limitation as this class's
                        # own `cancel_run`), but its result is simply never
                        # awaited/used either way.
                        current_chat_id = source_capture.captured_chat_id()
                        if current_chat_id and current_chat_id != contributors_task_chat_id:
                            if contributors_task is not None and not contributors_task.done():
                                contributors_task.cancel()
                            contributors_task_chat_id = current_chat_id
                            contributors_task = asyncio.create_task(
                                self._resolve_teams_contributors(current_chat_id)
                            )

                        delta_text = _extract_delta_text(event)
                        if delta_text is not None and not status_cleared:
                            # LIVE-CORR-3 -- DEF-0044: a chunk has arrived,
                            # so the model/specialist has genuinely started
                            # producing output -- clear the "thinking"
                            # status indicator (a status/progress signal,
                            # explicitly permitted to stream immediately),
                            # but `delta_text` itself is discarded here,
                            # never buffered, never emitted as `message.
                            # delta`. This turn's real, fully-validated
                            # final text reaches the user exactly once, via
                            # `message.completed`, further down this
                            # method, only after canonical persistence
                            # succeeds. `final_text` is never derived from
                            # these per-chunk deltas either way -- see
                            # `text = _extract_final_text(event)` just
                            # below, which reads a SEPARATE, complete/
                            # non-partial event.
                            yield sequencer.build(StreamEventType.STATUS_CLEAR, {})
                            status_cleared = True
                            perf.mark("first_message_delta")

                        text = _extract_final_text(event)
                        if text is not None:
                            final_text = text

                if specialist_result_state_written and final_text is None:
                    # EMPTY-RESPONSE FIX (pre-4H correction pass): evidence
                    # was already successfully retrieved and validated --
                    # `specialist_result_state_written` is only ever set
                    # after a genuinely successful `ResolvedReadContinuation`
                    # + envelope validation, above. An empty presentation
                    # turn here must never silently surface as "the
                    # assistant did not produce a response" without at
                    # least one bounded retry. The retry reuses the SAME
                    # already-validated `validated_for_presentation` --
                    # never `execute_read_continuation`, never
                    # `teams_list_chats`/`teams_get_messages` again -- and
                    # runs against a throwaway, disposable session (mirrors
                    # direct_read_fast_path.py's own proven
                    # `_run_trusted_presentation` pattern) rather than the
                    # REAL session, so a second attempt on the same real
                    # session_id never appends a duplicate user-turn event
                    # to genuine conversation history.
                    _logger.warning(
                        "chat_service: trusted presentation produced no final text -- retrying once run_id=%s",
                        sequencer.run_id,
                    )
                    retry_text = await _retry_trusted_presentation_once(
                        user_id=user_id,
                        run_id=sequencer.run_id,
                        validated_result=validated_for_presentation,
                        user_content=content,
                    )
                    if retry_text:
                        # LIVE-CORR-3 -- DEF-0044: no raw delta emission
                        # here either -- `final_text` still reaches the
                        # user exactly once, already validated, via
                        # `message.completed` further down this method.
                        final_text = retry_text
                    else:
                        _logger.warning(
                            "chat_service: trusted presentation still produced no final text after retry -- "
                            "failing closed run_id=%s",
                            sequencer.run_id,
                        )
                        error = (
                            "run_failure",
                            "The assistant could not complete this request. Please try again.",
                        )
                response_generation_completed = True
        except Exception as exc:
            # LIVE-CORR-14.1 -- distinguish an exception raised specifically
            # while the response-generating Runner/presentation stage is
            # active from a genuine failure in the control-plane/preflight
            # stages. We intentionally do NOT clear or swallow the exception
            # here. It is held separately until RequestExecutionDecision has
            # deterministically selected the response mode.
            #
            # For CLARIFICATION only, the final response is derived entirely
            # from authoritative decision.missing_context and does not depend
            # on Runner prose, specialist presentation, or command output.
            # Every other mode keeps the exact previous fail-closed behavior.
            if response_generation_started and not response_generation_completed and error is None:
                response_generation_error = (
                    "run_failure",
                    "The assistant could not complete this request. Please try again.",
                )
                _logger.warning(
                    "chat_service: response-generation stage raised before deterministic finalization "
                    "exception_type=%s run_id=%s",
                    type(exc).__name__,
                    sequencer.run_id,
                )
            else:
                # Never propagate a raw model/runtime exception (could include
                # implementation detail) -- see errors.py's module docstring.
                error = ("run_failure", "The assistant could not complete this request. Please try again.")
        finally:
            # BUGFIX (evidence-mailbox lifecycle audit): both cleanup calls
            # MUST live in this `finally`, not as separate statements after
            # it. `except Exception:` above does NOT catch
            # `asyncio.CancelledError` (a `BaseException` since Python 3.8,
            # exactly what a real server-side Stop -- `cancel_run` -- or a
            # rejected/superseded run delivers here) -- a `finally` block
            # still always runs on that path, but ANY code written as a
            # plain statement AFTER this try/except/finally would NOT: the
            # CancelledError resumes propagating the instant `finally`
            # completes, unwinding this whole generator before reaching
            # that next line. Popping the mailbox here, unconditionally, is
            # what actually guarantees no retrieved Teams message text for
            # this run_id is ever left behind in `turn_context`'s
            # in-process store on ANY exit path -- see
            # test_chat_service_turn_context_lifecycle.py.
            message_texts_by_id = pop_message_texts(sequencer.run_id)
            reset_run_id(run_id_token)
            # POST-5.1 B6 -- same unconditional, every-exit-path cleanup
            # discipline as the mailbox above (success, a caught
            # exception, or asyncio.CancelledError -- a `finally` runs on
            # all three). Safe to call even when nothing was registered.
            discard_run_images(sequencer.run_id)
            # Teams Visual Evidence milestone: snapshot whatever images
            # were ACTUALLY delivered to Gemini this run, BEFORE the
            # unconditional discard immediately below would otherwise
            # clear that same store -- mirrors `selected_knowledge_
            # evidence`'s own "snapshot in this finally, use later, after
            # the try/except/finally" shape exactly. `[]` for any turn
            # that never delivered a Teams image (the overwhelming
            # majority of turns).
            delivered_visual_evidence = pop_delivered_visual_evidence(sequencer.run_id)
            # Teams Image Vision corrective milestone: same unconditional,
            # every-exit-path cleanup discipline as `discard_run_images`
            # immediately above -- guarantees a Teams-hosted image stashed
            # for delivery to Gemini never survives past the one turn/run
            # that retrieved it, even if no further model call ever
            # consumed it (e.g. a turn that errors immediately after the
            # tool call). Safe to call even when nothing was stashed.
            # (Redundant with the snapshot above for `_delivered` itself --
            # already popped -- but still the correct, single cleanup call
            # for `_pending`/`_message_order`/`_message_metadata`.)
            discard_pending_hosted_content_image(sequencer.run_id)
            # A5 final corrective pass -- mirrors `selected_knowledge_
            # evidence`'s own snapshot-before-discard shape immediately
            # above: `pop_troubleshooting_guidance` both reads AND clears
            # the run-scoped entry here, before the completion-boundary
            # override (later in this method, after this try/except/
            # finally) ever runs -- a later, second pop against the same
            # run_id would incorrectly see nothing. `discard_
            # troubleshooting_guidance` remains a safe, redundant backstop
            # for any exit path that somehow reaches here without a
            # registration ever happening (a no-op in that case).
            # CONTROL-PLANE-SEQ-04A: prefers THIS turn's own main-Runner
            # guidance (troubleshooting_manager may legitimately still run,
            # per `work_envelope.may_route_troubleshooting_manager` -- §7,
            # unaffected by the preflight governed read) when present;
            # falls back to the deterministic preflight governed-read
            # stage's own already-captured guidance (if any) otherwise --
            # never silently discarded merely because this run_id's own
            # store happens to be empty (expected whenever `incident_
            # manager_tool` was structurally removed from this turn's
            # Runner -- §6).
            captured_troubleshooting_guidance = (
                pop_troubleshooting_guidance(sequencer.run_id) or preflight_governed_troubleshooting_guidance
            )
            discard_troubleshooting_guidance(sequencer.run_id)
            # CONTROL-PLANE-SEQ-06 -- same pop/discard pairing, for the
            # SAME `sequencer.run_id`, folded into this turn's own running
            # accumulator (never overwritten -- ADDITIVE with whatever the
            # preflight governed-read stage already contributed).
            known_rejected_commands_this_turn.update(pop_rejected_commands(sequencer.run_id))
            discard_rejected_commands(sequencer.run_id)
            # A5 final corrective pass (Correction D) -- same unconditional
            # cleanup discipline: a registered known-applicability context
            # must never survive past the one turn that registered it,
            # whether or not knowledge_search was ever actually called
            # (which is what would otherwise consume/pop it).
            discard_known_applicability_context(sequencer.run_id)
            discard_active_read_continuation(session_id)
            # Latency-diagnosis pass: same "bind/store, try, finally:
            # clear" discipline -- guarantees no per-run model-call
            # bookkeeping entry (perf_timing.py) survives past this one
            # turn, on any exit path.
            discard_model_call_tracking(sequencer.run_id)
            # Phase 5.1J correction pass (Part C): snapshot exactly what
            # Incident Manager explicitly selected this turn -- BEFORE
            # discarding the run-scoped store below -- so the KM Source
            # reference built later in this method (after this try/except/
            # finally) still has it, mirroring `message_texts_by_id`'s own
            # snapshot-then-use-later shape. Never the model's own
            # agent_payload/text -- always the trusted backend accessor.
            # CONTROL-PLANE-SEQ-04A: same "this turn's own live selection
            # wins, deterministic preflight result is the fallback" merge
            # as `captured_troubleshooting_guidance` immediately above --
            # `incident_manager_tool` being structurally absent from this
            # turn's own Runner (§6, the normal governed-required case)
            # means this snapshot is legitimately empty; the deterministic
            # preflight governed-read stage's own already-selected evidence
            # (if any) is used instead, never lost.
            selected_knowledge_evidence = (
                snapshot_selected_knowledge_evidence(sequencer.run_id) or preflight_governed_selected_evidence
            )
            # Same discipline for the Generic KM tool adapter's own
            # run-id-keyed trusted evidence state
            # (backend/tools/knowledge/runtime.py) -- guarantees no
            # `KnowledgeRunEvidenceState` (available/selected evidence)
            # survives past the one turn/run that produced it, on any
            # exit path, exactly like every other piece of this turn's
            # own per-run bookkeeping cleaned up in this same block.
            discard_knowledge_run_evidence_state(sequencer.run_id)
            # Phase 2 (Runtime Activity Truthfulness): same unconditional
            # discard discipline as every other run-scoped store cleaned
            # up in this block -- success, exception, and
            # `asyncio.CancelledError` all reach it identically. Safe to
            # call even if nothing was ever registered (a no-op).
            discard_activity_channel(sequencer.run_id)
            # P4B.3 CORRECTION PASS: same discipline for direct_read_fast_
            # path.py's own run-id-keyed pending-trusted-result registry --
            # normally already self-cleaned by `_present_fast_path_result_
            # via_trusted_pipeline`'s own `.pop()`; this is only the
            # backstop for a genuinely unexpected crash/cancellation
            # landing before that ever runs (section 22 -- "no cross-run
            # leakage"). Always safe to call, in-memory only, no session
            # I/O -- mirrors `discard_model_call_tracking` immediately
            # above.
            #
            # URGENT SOURCE/PROVENANCE FIX: `trust_validation_failed_for_
            # run` MUST be read into the local snapshot above BEFORE
            # `discard_pending_trusted_result` clears its own backing
            # registry -- this `finally` runs immediately after the Runner
            # call, well BEFORE the much-later code that decides whether to
            # build a `SourceReference` (source_capture.build_source_
            # reference, below) -- a live check made there would always see
            # an already-cleared flag.
            direct_fast_path_trust_validation_failed = trust_validation_failed_for_run(sequencer.run_id)
            discard_pending_trusted_result(sequencer.run_id)
            if specialist_result_state_written:
                # Same production discipline as the evidence mailbox
                # above -- bind/store, try, finally: clear. Guarantees no
                # stale specialist result survives past the ONE turn it
                # was produced for, on every NORMAL exit path (success, a
                # caught exception, cancellation). Clears BOTH the durable
                # envelope and the derived presentation key together --
                # the pass #4 hard-crash sweep at this method's own start
                # is the remaining backstop for the one path this
                # `finally` cannot reach at all: the process dying before
                # it ever runs.
                #
                # P0 fix: MUST use a freshly-reloaded session, never the
                # `session` object loaded at this turn's own start --
                # team_manager's own Runner call above has, by this
                # point, already advanced the canonical session's storage
                # revision through its OWN, separate session object. See
                # `_reload_and_persist_cleanup_delta`'s own docstring for
                # the full ADK-source-verified root cause.
                await self._reload_and_persist_cleanup_delta(
                    session_id,
                    user_id,
                    {
                        TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY: None,
                        PENDING_SPECIALIST_RESULT_STATE_KEY: None,
                    },
                    perf=perf,
                )

            # POST-5.1 B4B DEFECT FIX -- the saved-chat bookkeeping write
            # itself, deferred here (see `turn_invocation_id`'s own
            # capture-site comment above and `_finalize_user_turn_
            # activity`'s docstring for the full story). Only fires when
            # a genuine turn actually started (`turn_invocation_id` was
            # captured from a real yielded event) -- a Runner that raised
            # before yielding anything (the user-content append itself
            # never completed) correctly leaves this session's visibility
            # untouched. Runs on every real turn regardless of how it
            # ended: normal completion, a caught exception (`error` is
            # set above), or cancellation -- this `finally` block already
            # runs on all three (see the BUGFIX comment at its own top).
            if turn_invocation_id is not None:
                await self._finalize_user_turn_activity(session_id, user_id, turn_invocation_id, message_text)

        perf.mark("generation_complete")
        if delegation_timer.call_count:
            perf.log_duration("incident_manager_delegation", delegation_timer.total_seconds)
        if conversation_target_capture.target is not None:
            # Semantic-scope bug fix: developer-diagnostic/testability
            # signal only (see conversation_target_capture.py's own
            # docstring) -- safe, structured, no user content (a fixed
            # enum-like string plus run_id, mirroring perf_timing.py's own
            # safety contract). Never influences behavior; team_manager's
            # own subsequent reasoning this same turn already decided
            # whether/how to delegate.
            _logger.info(
                "conversation_target=%s run_id=%s", conversation_target_capture.target, sequencer.run_id
            )

        # Fetched here (moved up from immediately after the MESSAGE_COMPLETED
        # yield, where it used to live) so `SELECTED_TEAMS_CHAT_ID_STATE_KEY`
        # -- written this same turn by team_manager's own
        # `sync_incident_manager_result_to_state` `after_tool_callback`
        # (state_sync.py), which already runs and persists BEFORE
        # `Runner.run_async`'s generator is exhausted -- is available for
        # the contributor-accuracy chat_id fallback below, AND for the
        # governed-knowledge completion gate immediately below. Reused
        # again further down for `pending_action`/`pending_selection`, so
        # this is still exactly one extra session read per turn, not two.
        refreshed_session = await self._session_service.get_session(session_id, user_id)

        # Phase 6A.13 (Request Contract Foundation) -- OBSERVABILITY ONLY:
        # logs the validated (and, if needed, provenance-corrected)
        # `RequestContract` this turn produced, if any -- never a raw
        # parameter VALUE, only key names (see `safe_request_contract_
        # observability_fields`'s own docstring). This is a pure read of
        # already-durable session state (written by team_manager's own
        # `validate_and_persist_request_contract` after_tool_callback) --
        # it does not change `final_text`, routing, or any execution
        # behavior; 6A.14 is the milestone that will make execution
        # actually obey this contract.
        # LIVE-CORR-12B: `current_run_id=sequencer.run_id` is now passed
        # through so the logged projection carries its own `contract_run_id`/
        # `fresh` fields -- a stale contract (e.g. left behind by a turn that
        # ran through `presentation_team_manager`'s empty toolset, which
        # cannot call `record_request_contract` at all) is now immediately
        # legible in this one log line, rather than looking identical to a
        # fresh one and only being explained by a SEPARATE `INVALID_CONTRACT`
        # log line further down this same method.
        observable_contract = safe_request_contract_observability_fields(
            refreshed_session.state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY), current_run_id=sequencer.run_id
        )
        if observable_contract is not None:
            _logger.info("request_contract=%s run_id=%s", observable_contract, sequencer.run_id)

        # 6A.14 Active Procedure Continuity Correction -- moved earlier
        # (was previously computed only much later, immediately before the
        # `TroubleshootingGuidance` output-shape enforcement) so the SAME
        # already-validated `RequestContract` this method already reads
        # for observability, above, is also available to the governed-
        # knowledge continuity disambiguation below -- see the
        # corresponding audit for why running continuity BEFORE this
        # contract was ever consumed was itself part of the live defect.
        # `load_current_turn_contract` is fail-closed-tolerant (malformed/
        # absent state -> `None`) and does NOT itself check freshness --
        # `request_contract_subject`, below, is deliberately only trusted
        # when this SAME turn's `run_id` matches AND the contract does not
        # itself declare `ambiguity=True` (an ambiguous contract's own
        # `subject` is not a safe disambiguation input). `execution_
        # decision` (6A.14's own output-shape policy) is still derived
        # from this SAME variable further below, at its original location
        # -- this is a pure re-ordering, never a duplicate contract read/
        # generation.
        current_turn_request_contract = load_current_turn_contract(
            refreshed_session.state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY), sequencer.run_id
        )
        # LIVE-CORR-11 -- Pending Governed Request Continuity: the SAME
        # fail-closed-tolerant read discipline as the contract load
        # immediately above, for whatever unresolved `EXACT_COMMAND`
        # request (if any) a PRIOR turn's own `RequestExecutionStatus.
        # NEEDS_INFORMATION` outcome left behind (`build_pending_governed_
        # request_state_update`, request_execution_policy.py, written at
        # the end of that prior turn). `None` is the ordinary,
        # overwhelmingly common case -- no unresolved governed request to
        # preserve.
        pending_governed_request = parse_pending_governed_request(
            refreshed_session.state.get(PENDING_GOVERNED_REQUEST_STATE_KEY)
        )
        request_contract_subject = (
            current_turn_request_contract.subject
            if current_turn_request_contract is not None
            and current_turn_request_contract.run_id == sequencer.run_id
            and not current_turn_request_contract.ambiguity
            else None
        )
        # LIVE-CORR-12D -- Request-Scoped Governed Evidence Continuity:
        # THE continuity check. Computed here (same seam as `request_
        # contract_subject`, immediately above, reusing the SAME already-
        # loaded `current_turn_request_contract`/`pending_governed_request`)
        # so it is available both where `prior_governed_evidence`/`active_
        # procedure_anchor` are gated (below) and at this turn's own
        # end-of-turn state-hygiene write (see that comment's own
        # `LIVE-CORR-12D` marker, further down this method). `False` means
        # this turn's own validated contract does NOT establish it is
        # continuing an existing governed operation -- prior evidence must
        # not become its candidate universe. See `is_governed_evidence_
        # continuity_permitted`'s own docstring for the full two-path design.
        governed_evidence_continuity_permitted = is_governed_evidence_continuity_permitted(
            current_turn_request_contract, sequencer.run_id, pending_governed_request
        )

        # LIVE-CORR-12I -- Fresh RequestContract Guarantee on Every Normal
        # User Turn: team_manager's own model is instructed to call
        # `record_request_contract` every turn, but instruction-following
        # is not guaranteed -- a normal turn can complete without ever
        # calling it, leaving `current_turn_request_contract` either
        # absent or stamped with a PRIOR turn's own `run_id` (both already
        # fail `derive_execution_decision`'s own, unchanged, freshness
        # check as `INVALID_CONTRACT`). Skipped for a `specialist_result_
        # state_written` turn -- that turn deterministically synthesizes
        # its own fresh, `run_id`-fresh contract already (LIVE-CORR-12C,
        # `build_deterministic_read_continuation_contract`), so `current_
        # turn_request_contract` is never stale/absent for it in the first
        # place.
        #
        # LIVE-CORR-12I.1 -- deliberately NOT gated on `final_text is not
        # None`: a `RequestContract` is control-plane state, produced (or
        # not) by whatever tool calls the model made this turn -- entirely
        # independent of whether the SAME turn also produced presentation-
        # plane text. A live-reproduced turn ending `kind=function_call`
        # (e.g. a delegation to `incident_manager`, no direct text of its
        # own) legitimately leaves `final_text=None` at this point in the
        # method while still needing its own fresh contract just as much
        # as a `kind=text` turn does -- coupling the two silently skipped
        # remediation for exactly that shape. The pre-existing, separate
        # "no final text at all" error path further down this method is
        # unaffected and still runs regardless of this remediation's own
        # outcome.
        if (
            error is None
            and not specialist_result_state_written
            and (current_turn_request_contract is None or current_turn_request_contract.run_id != sequencer.run_id)
        ):
            _logger.warning(
                "chat_service: no fresh current-turn RequestContract -- requesting one via bounded "
                "remediation run_id=%s",
                sequencer.run_id,
            )
            try:
                remediated_contract = await request_current_turn_contract(
                    question=_remediation_question(message_text),
                    user_content=content,
                    run_id=f"{sequencer.run_id}::contract-remediation",
                    current_run_id=sequencer.run_id,
                )
            except Exception:
                _logger.warning(
                    "chat_service: request-contract remediation raised -- failing closed run_id=%s",
                    sequencer.run_id,
                )
                remediated_contract = None

            if remediated_contract is not None:
                await self._session_service.persist_state_delta(
                    session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: remediated_contract.model_dump(mode="json")}
                )
                # Re-derive every value this method already computed from
                # the (previously stale/absent) contract, exactly as the
                # original computations above did -- never a duplicate,
                # independently-drifting derivation.
                current_turn_request_contract = remediated_contract
                request_contract_subject = (
                    current_turn_request_contract.subject if not current_turn_request_contract.ambiguity else None
                )
                governed_evidence_continuity_permitted = is_governed_evidence_continuity_permitted(
                    current_turn_request_contract, sequencer.run_id, pending_governed_request
                )
            else:
                _logger.warning(
                    "chat_service: request-contract remediation did not produce a fresh contract -- "
                    "current turn will fail closed to INVALID_CONTRACT run_id=%s",
                    sequencer.run_id,
                )

        if (
            error is None
            and final_text is not None
            and not specialist_result_state_written
            and not source_requirements_capture.declared
        ):
            # FIFTH pre-4H correction pass: the remaining structural gap --
            # "no declaration" was previously read the same as "declared
            # false", letting team_manager skip `record_source_
            # requirements` ENTIRELY and answer straight from conversation
            # history with no gate ever noticing. Skipped for a `
            # specialist_result_state_written` turn (the post-selection/
            # fast-path TRUSTED presentation path, `presentation_team_
            # manager`, tools=[]) -- that turn is ALREADY provenance-safe
            # via the SEPARATE, already-validated `TrustedSpecialistResult`
            # envelope mechanism, and is structurally incapable of calling
            # ANY tool (including this declaration one), so requiring a
            # declaration there would be a pure regression, not a safety
            # improvement. Also skipped whenever `final_text is None` --
            # a turn that produced no response at all has nothing
            # successful to gate; the pre-existing "no final text" error
            # path a few lines below already handles that failure mode,
            # and this gate exists to protect a successful-looking
            # completion, never to manufacture one from a genuine non-
            # response. See source_requirements_completion.py's own
            # module docstring for the full rationale of the bounded,
            # tools=[record_source_requirements]-only remediation below.
            _logger.warning(
                "chat_service: no current-turn source-requirements declaration -- "
                "requesting one via bounded remediation run_id=%s",
                sequencer.run_id,
            )
            try:
                declaration = await request_source_requirements_declaration(
                    question=_remediation_question(message_text),
                    run_id=f"{sequencer.run_id}::declaration-remediation",
                )
            except Exception:
                _logger.warning(
                    "chat_service: source-requirements declaration remediation raised -- failing closed run_id=%s",
                    sequencer.run_id,
                )
                declaration = None

            if declaration is None:
                _logger.warning(
                    "chat_service: source-requirements declaration still missing after remediation -- "
                    "failing closed run_id=%s",
                    sequencer.run_id,
                )
                final_text = SAFE_DECLARATION_FAILURE_TEXT
                selected_knowledge_evidence = []
            else:
                requires_teams_declared, requires_governed_knowledge_declared = declaration
                source_requirements_capture.record_external_declaration(
                    requires_teams_declared, requires_governed_knowledge_declared
                )
                # `declared=False` (the only way this branch was reached)
                # means `final_text` is UNVERIFIED against this now-known
                # requirement -- when neither source is required, team_
                # manager's own original answer already stands on its own
                # (a plain conversational/history-recall/greeting response
                # never needed a delegation in the first place), so it is
                # left untouched; the `requires_governed_knowledge` branch
                # immediately below still applies uniformly regardless of
                # whether the declaration came from the main turn or from
                # this remediation.

        governed_completion_needed = False
        # LIVE REGRESSION CORRECTIVE PASS: `True` only when THIS turn's
        # own governed-knowledge remediation returned one of its fixed,
        # deterministic, never-model-generated fallback/clarification
        # texts (see `governed_knowledge_completion.py`'s own `_mark_
        # governed_completion_deterministic_fallback` docstring) --
        # never for its "real specialist summary" success path, which
        # remains exactly as subject to `requires_unstructured_response_
        # backstop` as before this pass.
        governed_completion_used_deterministic_fallback = False
        # CONTROL-PLANE-SEQ-04 §8's own explicit "do not let a model
        # source-requirements declaration increase authority beyond what
        # RequestClass permits" requirement: a `GENERAL_CONVERSATION`-
        # classified turn (this turn's own already-derived `work_envelope
        # .request_class`, frozen from preflight -- correct, since
        # `operational_team_manager`/`presentation_team_manager` can no
        # longer alter the contract mid-turn) can no longer force this
        # deterministic governed-retrieval completion gate merely because
        # `record_source_requirements` over-declared `requires_governed_
        # knowledge=True`. `work_envelope.request_class is None` (no
        # validated contract at all this turn) deliberately does NOT
        # suppress this gate -- identical, conservative, fail-closed
        # behavior to before this pass, since there is nothing here to
        # positively prove the request was general conversation.
        # `source_requirements_capture.requires_governed_knowledge` itself
        # is read LIVE, not frozen -- unaffected by this addition, so the
        # existing reactive source-requirements remediation (above) still
        # correctly feeds this gate when preflight's own declaration call
        # failed but that later remediation succeeded.
        if (
            error is None
            and source_requirements_capture.requires_governed_knowledge
            and work_envelope.request_class != RequestClass.GENERAL_CONVERSATION
        ):
            if not selected_knowledge_evidence and not preflight_governed_read_performed:
                # FOURTH pre-4H correction pass: PAST ASSISTANT OUTPUT !=
                # GOVERNED KNOWLEDGE. team_manager's own turn declared (via
                # `record_source_requirements`, directly or via the
                # remediation just above) that THIS request requires
                # current governed knowledge, but this run's own trusted,
                # run-scoped SELECTED evidence (snapshotted above, in the
                # `finally` block) is empty -- whether because team_manager
                # never delegated to incident_manager at all, or because
                # its own free-form presentation did not carry a validated
                # result forward faithfully.
                #
                # CONTROL-PLANE-SEQ-06 section 10 -- `not preflight_
                # governed_read_performed` closes the ONE proven-redundant
                # duplicate SEQ-04A itself disclosed: a deterministic
                # preflight governed read that already completed with a
                # REAL, validated specialist response (`ok`/`no_result` --
                # never a deterministic fallback/failure, see `preflight_
                # governed_read_performed`'s own SEQ-04A definition) can
                # legitimately still have zero selected evidence (a
                # genuine "not found" `no_result` outcome, per `enforce_
                # governed_knowledge_at_completion`'s own docstring) --
                # previously indistinguishable here from "never attempted
                # at all," so this gate always re-ran the SAME deterministic
                # call a second time, redundantly, for that one shape.
                # `preflight_governed_read_performed=False` (preflight
                # never ran -- e.g. `work_envelope.work_permitted` was
                # `False`, or the preflight call itself raised/returned a
                # deterministic fallback) leaves this branch's OWN
                # behavior byte-for-byte unchanged -- still the sole,
                # required safety net for that case.
                governed_completion_needed = True
            else:
                # DEF-0027 FINAL corrective pass (Fix #1): the pre-existing
                # gate above only ever protected the EMPTY-evidence case --
                # a real, live-observed defect proved a turn that selected
                # SOME evidence, but from an unrelated governed document
                # entirely (a genuinely successful "HW Partial Fault" turn
                # followed by "it's a SupportUnit", where incident_manager's
                # own fresh retrieval selected an unrelated Rogers Resource
                # Timeout MOP instead), sailed straight through this gate
                # untouched. See `governed_evidence_continuity.py`'s own
                # `detect_governed_evidence_anchor_mismatch` docstring for
                # the full design -- this reuses the SAME revalidation/
                # override machinery the empty-evidence branch below
                # already relies on, never a new state system.
                #
                # 6A.14 Active Procedure Continuity Correction: the anchor
                # this check compares against is now the dedicated,
                # single-identity `ACTIVE_GOVERNED_PROCEDURE_STATE_KEY`
                # (never `LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY`'s own
                # full, possibly-multi-identity list gated by a `len == 1`
                # coincidence) -- more precise, and no longer accidentally
                # inert whenever a genuinely successful turn selected both
                # an active procedure and a merely supporting sibling.
                try:
                    active_key_for_consistency_check = parse_active_governed_procedure(
                        refreshed_session.state.get(ACTIVE_GOVERNED_PROCEDURE_STATE_KEY)
                    )
                    if active_key_for_consistency_check is not None:
                        revalidated_for_consistency_check = await revalidate_prior_governed_evidence(
                            [active_key_for_consistency_check], get_knowledge_repository()
                        )
                        if len(revalidated_for_consistency_check) == 1 and detect_governed_evidence_anchor_mismatch(
                            message_text, revalidated_for_consistency_check[0], selected_knowledge_evidence
                        ):
                            governed_completion_needed = True
                except Exception:
                    # Fails CLOSED (forces remediation) rather than silently
                    # trusting an unverified, possibly-contaminated answer
                    # merely because this NEW consistency check itself
                    # broke -- the remediation call below has its own
                    # robust safe-failure fallback either way.
                    _logger.warning(
                        "chat_service: governed-evidence anchor-consistency check raised -- "
                        "forcing deterministic remediation defensively run_id=%s",
                        sequencer.run_id,
                    )
                    governed_completion_needed = True

        if governed_completion_needed:
            # See the FOURTH pre-4H correction pass / DEF-0027 FINAL
            # corrective pass comments immediately above for why this
            # branch is entered -- either no current selected evidence, or
            # a selected-evidence/prior-anchor mismatch. `final_text` is
            # therefore UNTRUSTED for this governed-knowledge portion and
            # must not reach the user as-is. See governed_knowledge_
            # completion.py's own module docstring for the full
            # live-failure rationale -- this deterministically forces the
            # REAL, unmodified `incident_manager` to run, so ITS OWN
            # existing compliance retry (provenance_compliance.py, third
            # correction pass) is what actually enforces selection; this
            # is not a second, competing selection mechanism.
            _logger.warning(
                "chat_service: governed knowledge required but current selection is empty or inconsistent "
                "with the prior anchor -- forcing deterministic governed-knowledge remediation run_id=%s",
                sequencer.run_id,
            )
            try:
                chat_topic = (
                    refreshed_session.state.get(SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY)
                    if source_requirements_capture.requires_teams
                    else None
                )
                # LIVE-CORR-12D -- Request-Scoped Governed Evidence
                # Continuity: prior evidence identity is read from durable
                # session state ONLY when `governed_evidence_continuity_
                # permitted` (computed earlier in this method) is `True` --
                # i.e. THIS turn's own validated contract positively
                # establishes it is continuing an existing governed
                # operation (or genuinely answers/corrects a still-pending
                # one). Otherwise BOTH are passed empty/`None`, so `enforce_
                # governed_knowledge_at_completion`'s own, completely
                # UNCHANGED revalidation/ambiguity logic sees zero stale
                # candidates and falls straight through to a fresh,
                # unscoped `incident_manager` run -- the real applicability
                # narrowing gets a genuine chance to run for a genuinely
                # new/unrelated request, instead of a stale prior document
                # choice ever reaching the user. DEF-0026 corrective pass's
                # own revalidation/deduplication/ambiguity machinery below
                # this gate is completely untouched -- a prior, genuinely
                # successful turn's own selected evidence identity (never
                # prose, never an available-but-unselected item) is STILL
                # re-validated in real time by `enforce_governed_knowledge_
                # at_completion` itself before it is trusted for anything,
                # exactly as before this pass, whenever this gate admits it.
                prior_governed_evidence = (
                    parse_last_selected_governed_evidence(
                        refreshed_session.state.get(LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY)
                    )
                    if governed_evidence_continuity_permitted
                    else []
                )
                # 6A.14 Active Procedure Continuity Correction -- the
                # single, dedicated active-procedure anchor (revalidated
                # inside `enforce_governed_knowledge_at_completion` itself,
                # never trusted blindly here) and this turn's own already
                # provenance-verified `RequestContract.subject` (computed
                # earlier in this method, `None` whenever stale/ambiguous/
                # absent) -- both consulted ONLY to deterministically
                # narrow a genuinely ambiguous `prior_governed_evidence`
                # set down to one, never to select evidence themselves.
                # Gated by the SAME LIVE-CORR-12D continuity check as
                # `prior_governed_evidence`, immediately above.
                active_procedure_anchor = (
                    parse_active_governed_procedure(refreshed_session.state.get(ACTIVE_GOVERNED_PROCEDURE_STATE_KEY))
                    if governed_evidence_continuity_permitted
                    else None
                )
                # Section 19's own explicit observability request -- safe,
                # lifecycle-only diagnostic (request-class/lifecycle state,
                # never raw user text or governed content). Distinguishes
                # a genuinely NEW request that had stale prior evidence to
                # ignore (`superseded_new_request`) from one that had none
                # at all to begin with (`fresh_retrieval`) -- both take the
                # identical code path below; the distinction is purely
                # diagnostic.
                if governed_evidence_continuity_permitted:
                    evidence_continuity_state = "reused_same_request"
                elif refreshed_session.state.get(LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY) or refreshed_session.state.get(
                    ACTIVE_GOVERNED_PROCEDURE_STATE_KEY
                ):
                    evidence_continuity_state = "superseded_new_request"
                else:
                    evidence_continuity_state = "fresh_retrieval"
                _logger.info(
                    "chat_service: governed_evidence_continuity=%s run_id=%s",
                    evidence_continuity_state,
                    sequencer.run_id,
                )
                governed_completion_run_id = f"{sequencer.run_id}::governed-completion"
                final_text, selected_knowledge_evidence = await enforce_governed_knowledge_at_completion(
                    question=_remediation_question(message_text),
                    chat_topic=chat_topic,
                    run_id=governed_completion_run_id,
                    # B7 live-regression corrective pass -- this turn's own
                    # trusted image evidence (already validated once, into
                    # `content`, above) must remain available across this
                    # bounded remediation, exactly as it was for the
                    # original delegation -- see governed_knowledge_
                    # completion.py's own module docstring for the full
                    # live-failure narrative this closes.
                    image_parts=trusted_image_parts_from_content(content),
                    prior_governed_evidence=prior_governed_evidence,
                    active_anchor=active_procedure_anchor,
                    request_contract_subject=request_contract_subject,
                )
                # LIVE-CORR-6 -- this remediation runs the REAL incident_
                # manager under its OWN, DIFFERENT, suffixed run_id (above)
                # -- never `sequencer.run_id` itself. `captured_
                # troubleshooting_guidance` was already popped, using
                # `sequencer.run_id`, much earlier in this method (the
                # `finally` block following the ORIGINAL delegation) --
                # BEFORE this remediation ever ran, so it could never have
                # seen a guidance object this call is ABOUT to register.
                # A real, live-confirmed defect: incident_manager's own
                # `after_agent_callback` (evidence.py) genuinely registers
                # a correctly-grounded `TroubleshootingGuidance` here (e.g.
                # `interaction_mode=next_step`, an unauthorized command
                # correctly stripped, `reason=unstructured_state_change`)
                # under `governed_completion_run_id` -- but nothing ever
                # read it back, so the 6A.14 execution-policy layer below
                # (`enforce_response_mode_compatibility`/`enforce_
                # execution_decision_on_guidance`) saw `captured_
                # troubleshooting_guidance is None` and treated this turn
                # as though NO structured guidance existed at all, firing
                # `requires_unstructured_response_backstop` and discarding
                # `final_text` above -- itself already the correct,
                # grounding-corrected, safe troubleshooting answer -- in
                # favor of the generic exact-command-style fallback. This
                # pops the SAME run-scoped store this remediation's own
                # incident_manager call just wrote to, under the EXACT
                # run_id the call site above already uses -- no new
                # correlation mechanism, no new safety framework, reusing
                # the identical pop/discard pairing the original delegation
                # already relies on immediately above in this method. A
                # remediation that produced no structured guidance at all
                # (an ordinary factual governed-knowledge answer, the
                # common case) leaves `captured_troubleshooting_guidance`
                # untouched, exactly as before.
                remediation_troubleshooting_guidance = pop_troubleshooting_guidance(governed_completion_run_id)
                discard_troubleshooting_guidance(governed_completion_run_id)
                if remediation_troubleshooting_guidance is not None:
                    captured_troubleshooting_guidance = remediation_troubleshooting_guidance
                # CONTROL-PLANE-SEQ-06 -- same pop/discard pairing, for
                # the SAME `governed_completion_run_id`, folded ADDITIVELY
                # into this turn's own running accumulator.
                known_rejected_commands_this_turn.update(pop_rejected_commands(governed_completion_run_id))
                discard_rejected_commands(governed_completion_run_id)
                # LIVE REGRESSION CORRECTIVE PASS -- the SAME pop/discard
                # pairing immediately above, for the SAME `governed_
                # completion_run_id`, for the sibling signal: was
                # `final_text` (just assigned above) one of this
                # remediation's own fixed, deterministic fallback texts,
                # never a real (potentially-unvalidated) specialist
                # summary. See `governed_knowledge_completion.py`'s own
                # `_mark_governed_completion_deterministic_fallback`
                # docstring for the full live-defect rationale this
                # closes.
                governed_completion_used_deterministic_fallback = pop_governed_completion_deterministic_fallback(
                    governed_completion_run_id
                )
                discard_governed_completion_deterministic_fallback(governed_completion_run_id)
            except Exception:
                _logger.warning(
                    "chat_service: governed-knowledge completion remediation raised -- failing closed run_id=%s",
                    sequencer.run_id,
                )
                final_text = SAFE_COMPLETION_FAILURE_TEXT
                selected_knowledge_evidence = []
                # Defensive cleanup only -- see the success-path comment
                # above for why this key can exist at all; never trusted
                # here, since `final_text` was just forced to the safe
                # failure text regardless of what (if anything) this
                # remediation attempt managed to register before raising.
                discard_troubleshooting_guidance(f"{sequencer.run_id}::governed-completion")
                discard_governed_completion_deterministic_fallback(f"{sequencer.run_id}::governed-completion")
                discard_rejected_commands(f"{sequencer.run_id}::governed-completion")

        # Phase 6A.14 (Deterministic Request Execution) -- derives what
        # this turn's runtime is ALLOWED to do from the validated,
        # CURRENT-TURN `RequestContract` (6A.13), computed earlier in this
        # method (see the 6A.14 Active Procedure Continuity Correction
        # comment above `current_turn_request_contract`'s own definition)
        # -- never a duplicate read/generation. `derive_execution_
        # decision` treats a missing/stale contract (run_id mismatch
        # against `sequencer.run_id`) at LEAST as restrictively as
        # `INVALID_CONTRACT` -- see request_execution_policy.py's own
        # module docstring for the full design. This is still the one
        # point in the whole turn that has simultaneous access to team_
        # manager's own real session state AND the turn's final
        # `TroubleshootingGuidance`.
        # LIVE-CORR-7 -- `captured_troubleshooting_guidance` (already
        # popped above, INCLUDING this turn's own governed-completion
        # remediation re-pop) is THIS turn's own real, already-grounded
        # guidance, if any -- its `command` (when populated) is forwarded
        # so a genuinely target-independent grounded operation (e.g. a
        # system-wide alarm listing) is never blanket-assigned `unit_id`/
        # `unit_type` requirements it does not actually need. `None`
        # (no guidance captured this turn) preserves the exact prior,
        # unchanged, conservative behavior.
        grounded_command_candidate = (
            captured_troubleshooting_guidance.command if captured_troubleshooting_guidance is not None else None
        )
        # LIVE-CORR-9 -- Exact-Command Continuation Loses Resolved
        # Operation Identity: a command that evidence.py's OWN, separate,
        # unchanged grounding correctly REJECTED (e.g. `GROUNDING_
        # REJECTED` -- the model's own reconstructed proposal shared real
        # token overlap with the active section but was not an exact
        # verbatim match) leaves `grounded_command_candidate` `None` --
        # confirmed live root cause: "no, i just need a cmd to run a check
        # on alarms for ericsson" had its own reconstructed command
        # correctly stripped, and `required_target_parameter_gaps`
        # (LIVE-CORR-7) then had NOTHING to consult, falling CLOSED to the
        # generic `unit_id`/`unit_type` blanket requirement even though
        # the STRUCTURALLY RESOLVED operation (a system-wide alarm
        # listing) never needed one. Section 8's own governing distinction:
        # "is the candidate grounded" and "does this operation require a
        # physical target" are separate questions -- absence of a
        # SURVIVING command must not be treated as proof of the second.
        #
        # Reuses the EXISTING, revalidated, session-persisted active-
        # procedure identity (6A.14 Active Procedure Continuity
        # Correction, governed_evidence_continuity.py) -- never a new
        # continuity mechanism, never text/keyword matching of the user's
        # own words: THIS turn's own fresh resolution
        # (`compute_fresh_active_procedure_anchor`, the SAME call already
        # used for the end-of-turn anchor write, below) takes precedence;
        # a PRIOR turn's still-valid anchor is the fallback -- mirroring
        # that mechanism's own established "a turn that never establishes
        # a new anchor relies on whatever a prior turn already validly
        # set" precedent exactly. The RESOLVED section's own real,
        # governed content (never the user's question, never a rejected/
        # untrusted command string) is what `required_target_parameter_
        # gaps` actually inspects -- a genuinely target-independent
        # governed procedure's own text contains no unit-class identifier
        # REGARDLESS of what the model's own rejected proposal happened
        # to say. Still fails closed to the original blanket rule whenever
        # no active section can be resolved at all (no regression for a
        # turn that never established one).
        if grounded_command_candidate is None:
            active_section_key_for_target_cardinality = compute_fresh_active_procedure_anchor(
                selected_knowledge_evidence, _remediation_question(message_text)
            ) or parse_active_governed_procedure(refreshed_session.state.get(ACTIVE_GOVERNED_PROCEDURE_STATE_KEY))
            if active_section_key_for_target_cardinality is not None:
                for item in selected_knowledge_evidence:
                    if (
                        item.reference.knowledge_id == active_section_key_for_target_cardinality.knowledge_id
                        and item.reference.version_label == active_section_key_for_target_cardinality.version_label
                        and item.reference.section_id == active_section_key_for_target_cardinality.section_id
                    ):
                        grounded_command_candidate = item.section.content
                        break
        execution_decision = derive_execution_decision(
            current_turn_request_contract,
            sequencer.run_id,
            grounded_command_candidate=grounded_command_candidate,
            pending_governed_request=pending_governed_request,
        )
        _logger.info(
            "request_execution_decision status=%s request_class=%s may_emit_command=%s may_execute_action=%s run_id=%s",
            execution_decision.status,
            execution_decision.request_class,
            execution_decision.may_emit_command,
            execution_decision.may_execute_action,
            sequencer.run_id,
        )
        # LIVE-CORR-11 -- computed here (where `execution_decision`/
        # `current_turn_request_contract` are both available) but MERGED
        # into `end_of_turn_state_delta` further below, alongside this
        # method's other end-of-turn continuity writes (that dict is not
        # constructed until later in this method) -- refreshed EVERY turn,
        # never left stale. Built from the SAME effective contract
        # (pending+current merge, if any applied) this turn's own decision
        # was actually computed from -- never the raw, un-merged contract,
        # so a corrected/replaced target (sections 9/10) is what survives
        # into the next turn, not a stale prior value.
        #
        # LIVE-CORR-11 CORRECTIVE PASS -- ISSUE A: `ALLOW` for an `EXACT_
        # COMMAND` decision now writes a `COMPLETED` (not cleared) pending
        # record, retained for exactly one more turn so an immediate
        # correction ("actually it is RRU-10") does not fall through the
        # same `OPERATIONAL_INFORMATION`/`FACT` gap this milestone exists
        # to close -- see `build_pending_governed_request_state_update`'s
        # own docstring for the full, corrected lifecycle (also covers the
        # `AMBIGUOUS`-with-a-resolved-subject case). Left `None` entirely
        # (pending state read fresh next turn, untouched by this turn) when
        # this turn produced no validated contract at all
        # (`INVALID_CONTRACT`) -- a transient contract-recording failure
        # must not silently discard a real, still-pending governed request
        # (LIVE-CORR-10's own audit finding).
        # LIVE-CORR-11 FINAL SAFETY CLOSURE -- Concern B: when THIS turn
        # produced no validated contract at all (`current_turn_request_
        # contract is None` -- INVALID_CONTRACT), `build_pending_governed_
        # request_state_update` is never called (there is no decision/
        # contract to build from) -- previously left `pending_governed_
        # request_state_update` as an unconditional no-op `{}`, meaning a
        # `COMPLETED` record could survive an intervening invalid turn
        # and still be eligible for "immediate" correction on a LATER
        # turn, violating that stage's own one-turn-only contract.
        # `expire_completed_pending_on_invalid_contract` narrowly expires
        # ONLY a `COMPLETED` record in this specific case -- an
        # `UNRESOLVED` record (or no pending state at all) is still left
        # completely untouched, preserving genuine clarification recovery
        # across a transient contract-recording glitch.
        pending_governed_request_state_update: dict[str, object] = (
            build_pending_governed_request_state_update(
                execution_decision,
                resolve_effective_governed_contract(current_turn_request_contract, pending_governed_request),
            )
            if current_turn_request_contract is not None
            else expire_completed_pending_on_invalid_contract(pending_governed_request)
        )

        if error is None and execution_decision.status == RequestExecutionStatus.UNSUPPORTED_CAPABILITY:
            # Section 8's own explicit requirement: a KNOWLEDGE_INVENTORY-
            # shaped request must never be silently answered by ordinary
            # semantic Knowledge search results presented as though they
            # were a complete catalog -- overridden UNCONDITIONALLY,
            # regardless of what team_manager/incident_manager otherwise
            # produced this turn (mirrors the SAME "hard override"
            # philosophy the troubleshooting-guidance block below already
            # uses). The real, deterministic catalog capability is a
            # future milestone.
            final_text = KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT
            # LIVE-CORR-2 -- DEF-0039 CORRECTIVE PASS: `selected_knowledge_
            # evidence` may already hold real, genuinely-selected Knowledge
            # items from earlier in THIS turn (a real `knowledge_search`/
            # `knowledge_select_evidence` call made before this
            # capability decision was known) -- live evidence (session
            # `abe35bdc-...`) proved those items otherwise still reached
            # the user as `knowledge_sources` chips beside a message that
            # explicitly disclaims the ability to enumerate documents,
            # misleadingly appearing to support a fallback they never
            # informed. Cleared here, in the SAME place/turn `final_text`
            # is overridden, so every later consumer of this variable --
            # the live `knowledge_sources` SSE field (below), the
            # persisted canonical result (below, built from the SAME
            # `knowledge_sources`), AND the end-of-turn `LAST_SELECTED_
            # GOVERNED_EVIDENCE_STATE_KEY`/`ACTIVE_GOVERNED_PROCEDURE_
            # STATE_KEY` continuity-anchor writes (both also read from
            # THIS SAME variable, further down) -- all stay consistent
            # for free, never a second, independently-maintained cleanup.
            # `build_last_selected_governed_evidence_state_update([])`/
            # `compute_fresh_active_procedure_anchor([], ...)` both
            # already treat an empty list as "no change" (their own
            # documented contract), so a PRIOR turn's real continuity
            # anchor is correctly left untouched -- only the DISCARDED
            # inventory-turn evidence is prevented from ever becoming a
            # new one. Internal retrieval/selection activity already
            # emitted via this turn's own activity-channel events is
            # unaffected -- only this end-of-turn, user-facing/persisted
            # variable is cleared.
            selected_knowledge_evidence = []

        # ================================================================
        # CONTROL-PLANE-SEQ-05 -- Command Authority Inventory (section 6)
        # ================================================================
        #
        # Captured HERE, from `captured_troubleshooting_guidance` in its
        # own CURRENT, still-untouched-by-anything-below state (this
        # turn's own final merge of the main-Runner result and/or the
        # SEQ-04A deterministic governed-read result -- already settled,
        # well above this point) -- BEFORE `enforce_response_mode_
        # compatibility`/`enforce_execution_decision_on_guidance` (both
        # unmodified, immediately below) have any chance to discard or
        # strip anything. `known_commands_this_turn` is therefore every
        # exact command value THIS turn structurally knew about at all,
        # regardless of whether a later gate ends up authorizing,
        # discarding, or stripping it -- section 10's own "any command
        # embedded inside an authorized procedure step is STILL separately
        # subject to may_emit_command" requirement depends on this NOT
        # being narrowed prematurely. `grounded_command_candidate` (LIVE-
        # CORR-7/9, computed earlier, unmodified) is folded in too -- for
        # NEXT_STEP mode it is normally the SAME value as `captured_
        # troubleshooting_guidance.command`, but is included independently
        # since it can also be populated from the active-procedure-anchor
        # fallback (a distinct source `extract_known_commands_from_
        # guidance` cannot see).
        known_commands_this_turn = extract_known_commands_from_guidance(captured_troubleshooting_guidance)
        if grounded_command_candidate:
            known_commands_this_turn.add(grounded_command_candidate)
        # CONTROL-PLANE-SEQ-06 section 2 -- folds in every command value
        # this turn's own grounding already, structurally, rejected (see
        # `known_rejected_commands_this_turn`'s own declaration/accumulation
        # comments above) -- these can never be `authorized_commands_this_
        # turn` (a rejected command is never re-authorized further below),
        # so this union only ever WIDENS `prohibited_commands`, never
        # narrows it.
        known_commands_this_turn |= known_rejected_commands_this_turn
        authorized_commands_this_turn: set[str] = set()

        # ================================================================
        # LIVE-CORR-14 -- RequestExecutionDecision IS THE SOLE RESPONSE-
        # MODE AUTHORITY (sections 1-9)
        # ================================================================
        #
        # OLD (shadowed) precedence, replaced by this block:
        #
        #     if captured_troubleshooting_guidance is not None:   # artifact
        #         if status in (NEEDS_INFORMATION, AMBIGUOUS): ...
        #         elif command_suppressed_by_policy: ...
        #         elif rendered_guidance_text: ...
        #         elif final_text is None: ...
        #     elif response_mode_incompatible: ...                # artifact
        #     elif governed_completion_used_deterministic_fallback:
        #         ...                                             # sets NO text
        #     elif requires_unstructured_response_backstop(...): ...
        #
        # Every one of those four top-level conditions is an ARTIFACT-SHAPE
        # test, so the decision status was only ever consulted after an
        # artifact had already claimed the turn. PROVEN LIVE FAILURE ("give
        # me a command to restart an RRU"): `status=needs_information,
        # missing_context=[unit_id, unit_type], may_emit_command=False`
        # (correct) still finished `message_completed_emitted=False error_
        # code=run_failure`, because a shape reaching one of the two
        # text-less top-level branches (`governed_completion_used_
        # deterministic_fallback`, or a guidance object with nothing
        # renderable under a status the inner chain did not cover) made the
        # clarification branch structurally unreachable.
        #
        # NEW precedence -- status FIRST, content SECOND:
        #
        #     RequestExecutionDecision   (authority)
        #         -> response mode       (_select_response_mode, status only)
        #             -> specialist/model content  (data for that mode)
        #                 -> validate_final_output (final boundary)
        #
        # `requires_unstructured_response_backstop`, `command_suppressed_
        # by_policy`, `rendered_guidance_text`, `response_mode_
        # incompatible` and the governed-completion fallback signal all
        # REMAIN -- but strictly DEMOTED to CONTENT selection inside the
        # single `AUTHORIZED_RESPONSE` mode. None of them can grant
        # authority to clarify, disambiguate, restrict, or fail any more.
        #
        # Unchanged by this pass: `RequestContract`, `RequestClass`,
        # `PendingGovernedRequest`, the effective-governed-request merge,
        # `WorkEnvelope`, governed retrieval, evidence selection,
        # grounding, the rejected-command inventory, `derive_execution_
        # decision`, `may_emit_command`/`may_execute_action`,
        # `AuthorizedResponseContext`, `validate_final_output`, and action
        # execution.
        response_mode = _select_response_mode(execution_decision)

        # LIVE-CORR-14.1 -- the final deterministic decision now decides
        # whether a response-generation failure is actually relevant to this
        # turn's user-visible response.
        #
        # NEEDS_INFORMATION / AMBIGUOUS -> CLARIFICATION is rendered from
        # authoritative decision state below and is explicitly command/action
        # forbidden. Therefore missing Runner prose (or a failure in that
        # presentation-only stage) cannot veto the clarification.
        #
        # All other response modes still depend on their normal upstream
        # content/state and retain the existing fail-closed behavior.
        if response_generation_error is not None:
            if (
                response_mode == _ResponseMode.CLARIFICATION
                and not execution_decision.may_emit_command
                and not execution_decision.may_execute_action
            ):
                _logger.warning(
                    "chat_service: deterministic clarification supersedes response-generation failure "
                    "decision_status=%s run_id=%s",
                    execution_decision.status,
                    sequencer.run_id,
                )
                response_generation_error = None
            elif error is None:
                error = response_generation_error

        final_response_path = "conversational"
        # LIVE-CORR-14 section 13 -- every `run_failure` this method can
        # emit must carry an explicit, safe reason (never raw content).
        # `None` here means "no finalization-level failure": the generic
        # `upstream_turn_failure` label is used at the log site for an
        # `error` one of the EARLIER stages of this method already set
        # (a Runner exception, a cancelled/superseded run, a trusted-
        # presentation retry that still produced nothing, and so on --
        # each of which already logs its own, more specific warning at
        # the point it happened).
        run_failure_reason: Optional[str] = None
        # LIVE-CORR-13.1 section 9 / LIVE-CORR-14 section 13 -- diagnostic-
        # only observability locals (never read by, and never influencing,
        # any control-flow decision below -- populated as each stage
        # actually runs, logged once just before the "no final text"
        # failure so an empty-final-text turn is self-explanatory from the
        # logs alone, without needing raw text/commands).
        captured_guidance_present = captured_troubleshooting_guidance is not None
        enforced_guidance_present = False
        rendered_guidance_present = False
        clarification_rendered_present = False
        pre_mode_text_present = final_text is not None
        # LIVE-CORR-13 -- this block (guidance rendering, the unstructured-
        # response backstop, and the SEQ-05 final authority boundary/
        # validator immediately below) MUST run whenever `error is None`,
        # regardless of whether `final_text` already holds team_manager's
        # own free text. LIVE evidence proved the previous `and final_text
        # is not None` guard silently skipped this ENTIRE block for a turn
        # whose Runner ended on a structured/tool-call event with no
        # accompanying prose (the model's real, ordinary behavior after a
        # specialist delegation; `_extract_final_text` correctly returns
        # `None` for such an event). Every branch below already tolerates a
        # `None` starting `final_text`.
        if error is None:
            # ============================================================
            # STAGE 1 -- CONTENT PREPARATION (never authority)
            # ============================================================
            #
            # Both enforcement calls are UNCHANGED, and still run for every
            # mode, so this turn's own command inventories (`authorized_
            # commands_this_turn`, feeding `AuthorizedResponseContext`
            # below) are derived identically to before this pass -- what
            # changed is only that their RESULTS no longer select the
            # response mode.
            #
            # Phase 6A.14 -- `enforce_execution_decision_on_guidance`
            # applies the layer above DEF-0024/0026/0027's own `evidence
            # .py` grounding: grounding already answered "IF a command may
            # be shown, is THIS one actually grounded in the right
            # procedure"; this answers "is the runtime even ALLOWED to show
            # a command for this request AT ALL." Both must agree before a
            # command reaches the user.
            # LIVE-CORR-3 -- DEF-0040: `enforce_response_mode_
            # compatibility` first confirms the guidance's own
            # `interaction_mode` is even PERMITTED for the validated
            # contract's `requested_output` (`execution_decision.requested_
            # output` -- the SAME already-freshness-checked value
            # `derive_execution_decision` itself populated).
            captured_troubleshooting_guidance, response_mode_incompatible = enforce_response_mode_compatibility(
                captured_troubleshooting_guidance, execution_decision
            )
            command_suppressed_by_policy = False
            rendered_guidance_text = ""
            if captured_troubleshooting_guidance is not None:
                corrected_guidance, command_suppressed_by_policy = enforce_execution_decision_on_guidance(
                    captured_troubleshooting_guidance, execution_decision
                )
                enforced_guidance_present = bool(
                    corrected_guidance.command
                    or corrected_guidance.interpretation
                    or corrected_guidance.next_action
                    or corrected_guidance.evidence_requested
                    or corrected_guidance.full_procedure_steps
                )
                # CONTROL-PLANE-SEQ-05 section 7 -- the exact grounded
                # command allowlist: `enforce_execution_decision_on_
                # guidance` (unmodified) already leaves `corrected_
                # guidance` fully command-free whenever `execution_
                # decision.may_emit_command` is `False` -- the explicit
                # `if execution_decision.may_emit_command else set()`
                # guard is deliberate, redundant defense-in-depth (section
                # 4's own "structurally enforce this, never rely solely on
                # an upstream strip") rather than a second, independently-
                # drifting condition.
                authorized_commands_this_turn = (
                    extract_known_commands_from_guidance(corrected_guidance)
                    if execution_decision.may_emit_command
                    else set()
                )
                rendered_guidance_text = render_troubleshooting_guidance(corrected_guidance)
                rendered_guidance_present = bool(rendered_guidance_text)

            # ============================================================
            # STAGE 2 -- MODE DISPATCH (status already decided this)
            # ============================================================
            if response_mode == _ResponseMode.CLARIFICATION:
                # LIVE-CORR-14 section 4/5 -- ABSOLUTE. `NEEDS_INFORMATION`
                # and `AMBIGUOUS` always render clarification/
                # disambiguation from AUTHORITATIVE context, with ZERO
                # consultation of guidance presence, guidance content,
                # model prose, or a command candidate. This is the exact
                # branch the live RRU turn could not reach: it now fires
                # identically when `captured_guidance` is `None`, when it is
                # an EMPTY object, when the command was `grounding_
                # rejected`, when it was `cross_procedure` stripped, when
                # the Runner produced no final text at all, and when the
                # model emitted only a function call.
                #
                # LIVE-CORR-12E -- the renderer is the tool-free, policy-
                # validated natural renderer; `execution_decision.missing_
                # context` (never a raw model declaration) is the ONLY
                # thing that can make it attempt rendering at all, and any
                # renderer failure/field mismatch falls back to the exact
                # same deterministic sentence (`command_suppression_
                # fallback_text`) this call site always used -- so this
                # branch structurally cannot produce empty text.
                final_text = await render_command_suppression_text(
                    execution_decision,
                    run_id=sequencer.run_id,
                    known_context=current_turn_request_contract.provided_context
                    if current_turn_request_contract is not None
                    else (),
                )
                final_response_path = "clarification"
                clarification_rendered_present = True
            elif response_mode == _ResponseMode.RESTRICTION:
                # `UNSUPPORTED_CAPABILITY`. Its own deterministic override
                # (`KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT`, applied earlier
                # in this method -- ALREADY status-driven, unchanged) has
                # normally already written `final_text`; the `is None`
                # guard below reuses that SAME existing text rather than
                # inventing new wording. What this mode CHANGES is only
                # that a later guidance/prose branch can no longer shadow
                # it: under the old chain, guidance for this status was
                # discarded by `enforce_response_mode_compatibility` and the
                # `response_mode_incompatible` branch then OVERWROTE the
                # capability restriction with `FULL_PROCEDURE_NOT_
                # PERMITTED_FALLBACK_TEXT`.
                if final_text is None:
                    final_text = KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT
                final_response_path = "restriction"
            elif response_mode == _ResponseMode.APPROVAL:
                # `REQUIRES_APPROVAL` -- UNCHANGED behavior, deliberately.
                # The approval boundary is its own separate, deterministic
                # state machine (proposed write -> trusted approval/
                # rejection -> deterministic re-authorized execution) and
                # already owns this turn's user-facing proposal text and
                # approval card; nothing here rewrites, re-renders, or
                # second-guesses it. The only change is negative: guidance/
                # prose shape can no longer divert an approval turn into a
                # clarification or a `FULL_PROCEDURE_NOT_PERMITTED_
                # FALLBACK_TEXT` restriction.
                final_response_path = "approval"
            elif response_mode == _ResponseMode.AUTHORIZED_RESPONSE:
                # ========================================================
                # LIVE-CORR-14 section 6 -- the ONLY mode that consumes
                # specialist/model content. Priority INSIDE this mode is
                # deliberately the pre-existing priority (guidance ->
                # incompatibility -> governed-completion deterministic
                # fallback -> unstructured backstop -> safe free text), so
                # every currently-passing ALLOW behavior is preserved; the
                # ONLY change is that this whole ladder is now REACHABLE
                # ONLY for `status=ALLOW`.
                # ========================================================
                if captured_troubleshooting_guidance is not None:
                    if command_suppressed_by_policy:
                        # LIVE-CORR-12E -- policy-bound natural
                        # clarification, appended to whatever safe guidance
                        # text survived the suppression.
                        fallback_text = await render_command_suppression_text(
                            execution_decision,
                            run_id=sequencer.run_id,
                            known_context=current_turn_request_contract.provided_context
                            if current_turn_request_contract is not None
                            else (),
                        )
                        final_text = (
                            f"{rendered_guidance_text}\n\n{fallback_text}" if rendered_guidance_text else fallback_text
                        )
                        final_response_path = "clarification_suppressed"
                        clarification_rendered_present = True
                    elif rendered_guidance_text:
                        # A5 final corrective pass -- the HARD, one-command-
                        # at-a-time override: the deterministic Python
                        # rendering of the typed field, never team_manager's
                        # own free-form presentation of it (live testing
                        # proved that does not reliably stay bounded to one
                        # action).
                        final_text = rendered_guidance_text
                        final_response_path = "guidance_render"
                    elif final_text is None:
                        # LIVE-CORR-13.1 -- guidance existed but rendered
                        # nothing (e.g. `evidence.py`'s own upstream
                        # grounding already emptied it for `cross_procedure_
                        # evidence`) AND no earlier stage wrote a specific
                        # explanation. LIVE-CORR-6's own sibling case (an
                        # earlier stage this SAME turn already wrote a
                        # correct, already-safe explanation) is still left
                        # completely untouched by the `final_text is None`
                        # guard.
                        final_text = await render_command_suppression_text(
                            execution_decision,
                            run_id=sequencer.run_id,
                            known_context=current_turn_request_contract.provided_context
                            if current_turn_request_contract is not None
                            else (),
                        )
                        final_response_path = "empty_guidance_fallback"
                        clarification_rendered_present = True
                    # else: `final_text` already holds an earlier stage's
                    # own correct, already-safe explanation (LIVE-CORR-6's
                    # original documented case) -- left untouched.
                elif response_mode_incompatible:
                    # LIVE-CORR-3/DEF-0040 -- a FULL_PROCEDURE-shaped (or
                    # otherwise mode-incompatible) response for a request
                    # validated as needing a different output shape. Still
                    # decision-driven (`is_full_procedure_response_
                    # permitted`/`is_next_step_response_permitted`/`is_
                    # exact_command_response_permitted` all read the
                    # DECISION, never the model's own claim) -- but now
                    # scoped to `ALLOW`, so it can no longer shadow a
                    # clarification, a capability restriction, or an
                    # approval turn.
                    _logger.warning(
                        "chat_service: FULL_PROCEDURE guidance discarded -- validated contract requested_output=%s "
                        "does not permit full-procedure output run_id=%s",
                        execution_decision.requested_output,
                        sequencer.run_id,
                    )
                    final_text = FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT
                    final_response_path = "full_procedure_not_permitted"
                elif governed_completion_used_deterministic_fallback:
                    # LIVE REGRESSION CORRECTIVE PASS -- LIVE session
                    # 389c0d82-fff5-41c7-bd66-88e5623465c6: governed-
                    # knowledge remediation this SAME turn returned its own
                    # fixed, deterministic "which procedure do you mean"
                    # text (its prior-turn evidence was itself ambiguous
                    # across more than one candidate procedure and could
                    # not be deterministically narrowed), so `final_text`
                    # already holds a correct, never-model-generated
                    # answer -- and the backstop below would otherwise
                    # overwrite it with the strictly more generic, here
                    # actively MISLEADING command-suppression text (no
                    # target detail was missing; a DIFFERENT, governed-
                    # evidence-level ambiguity was). Left untouched.
                    final_response_path = "governed_completion_fallback"
                elif requires_unstructured_response_backstop(
                    execution_decision, troubleshooting_guidance_present=False
                ):
                    # Phase 6A.14 FINAL corrective pass -- ROOT CAUSE A
                    # backstop, now strictly a CONTENT helper: no structured
                    # guidance exists for this turn, so `evidence.py`'s own
                    # DEF-0024/0027 grounding and `enforce_execution_
                    # decision_on_guidance` both had NOTHING to examine,
                    # and whatever free-form text the model produced cannot
                    # be verified safe (it is never scanned/parsed -- see
                    # that function's own docstring for why not). For
                    # `ALLOW` it fires only for an operationally-shaped
                    # request (LIVE-CORR-3B), leaving ordinary conversation
                    # untouched. This is the direct, structural fix for the
                    # real live defect where `accn FieldReplaceableUnit=
                    # RRU-9 restartunit 1 1 1` reached the user via
                    # ordinary `summary` prose.
                    #
                    # NOTE (LIVE-CORR-14 section 8): this helper is no
                    # longer consulted for `NEEDS_INFORMATION`/`AMBIGUOUS`
                    # at all -- the CLARIFICATION mode above already owns
                    # those unconditionally -- so it can no longer be the
                    # thing that grants (or, when shadowed, withholds) a
                    # clarification.
                    _logger.warning(
                        "chat_service: operationally-shaped authorized response with no structured "
                        "troubleshooting_guidance to enforce against -- replacing free-form response "
                        "deterministically run_id=%s",
                        sequencer.run_id,
                    )
                    final_text = await render_command_suppression_text(
                        execution_decision,
                        run_id=sequencer.run_id,
                        known_context=current_turn_request_contract.provided_context
                        if current_turn_request_contract is not None
                        else (),
                    )
                    final_response_path = "unstructured_backstop"
                    clarification_rendered_present = True
                if not final_text:
                    # LIVE-CORR-14 sections 6/11 -- THE HARD INVARIANT for a
                    # VALID policy state: an `ALLOW` turn whose every
                    # permitted content candidate turned out empty must NOT
                    # become `final_text=None` + `run_failure` merely
                    # because a specialist/model returned nothing
                    # renderable. Reuses the EXISTING, deterministic,
                    # Python-authored `SAFE_DECLARATION_FAILURE_TEXT`
                    # (source_requirements_completion.py -- this codebase's
                    # own established "cannot trust this turn's own
                    # completion" wording, already used earlier in this
                    # method for the analogous declaration failure) --
                    # never new wording, never model-generated, and never a
                    # reconstruction of a rejected command. The
                    # operationally-shaped shapes are already covered, more
                    # specifically, by the branches above; this is the
                    # residual non-operational case that previously fell
                    # through to `run_failure`.
                    #
                    # `not final_text` (never `is None`) deliberately also
                    # covers an EMPTY-STRING candidate: `_extract_final_text`
                    # can never produce one, but a deterministic upstream
                    # stage returning `""` would otherwise reach
                    # `MESSAGE_COMPLETED` as a blank assistant message --
                    # section 11's own invariant is NON-EMPTY, not merely
                    # non-`None`.
                    final_text = SAFE_DECLARATION_FAILURE_TEXT
                    final_response_path = "authorized_response_empty_fallback"
            else:
                # `UNRESOLVED_CONTRACT` (`INVALID_CONTRACT`) -- see
                # `_ResponseMode.UNRESOLVED_CONTRACT`'s own docstring: the
                # pre-existing conservative pass-through is retained
                # deliberately (both candidate deterministic-restriction
                # designs were already audited and rejected against this
                # repository's own real test suite, and a turn that
                # produced no model output at all resolves to this status
                # and must keep failing closed as a genuine non-response).
                final_response_path = "unresolved_contract"

            # ============================================================
            # CONTROL-PLANE-SEQ-05 -- ONE FINAL AUTHORITY BOUNDARY (§24)
            # ============================================================
            #
            # Runs UNCONDITIONALLY, on whatever `final_text` ended up being
            # after EVERY branch above -- the deterministic guidance
            # render, any of the deterministic fallback/clarification
            # texts (all already safe by construction; this check on them
            # is a costless no-op, never special-cased away), or the one
            # remaining ordinary-conversational free-text path (`known_
            # commands_this_turn` is empty there, so this is ALSO a costless
            # no-op -- section 18's own "lightweight for genuine general
            # conversation" requirement). No response path is exempted --
            # this is the single choke point every path already converges
            # into before `MESSAGE_COMPLETED`, never a second, path-
            # specific validator.
            authorized_response_context = build_authorized_response_context(
                execution_decision,
                authorized_commands=authorized_commands_this_turn,
                known_commands=known_commands_this_turn,
            )
            final_text, final_output_validated = validate_final_output(
                final_text, execution_decision, authorized_response_context
            )
            if not final_output_validated:
                # Safe diagnostic only -- never the rejected text itself,
                # never a command value (section 16's own "do not leak the
                # rejected content inside the explanation," applied here to
                # this turn's own logs too).
                _logger.warning(
                    "chat_service: final output validation rejected a known-unauthorized command in the "
                    "candidate response -- replaced with the existing deterministic command-suppression "
                    "fallback run_id=%s",
                    sequencer.run_id,
                )
                final_response_path = "final_output_validator_fallback"

            # LIVE-CORR-13.1 section 9 -- pure, side-effect-free re-
            # derivation for logging only (never re-used for control flow,
            # never itself gating anything above): makes an empty-final-
            # text turn self-explanatory from the logs alone.
            clarification_required = execution_decision.status in (
                RequestExecutionStatus.NEEDS_INFORMATION,
                RequestExecutionStatus.AMBIGUOUS,
            )
            response_backstop_required = requires_unstructured_response_backstop(
                execution_decision, troubleshooting_guidance_present=captured_guidance_present
            )

            # LIVE-CORR-13/13.1 + LIVE-CORR-14 section 13 -- finalization
            # observability. Safe metadata only: never `final_text`
            # itself, never a command value, never the user's own
            # message. `selected_response_mode` is the one field that
            # makes the new authority order directly auditable from a
            # live log line: it is derived from `decision_status` ALONE,
            # so a line whose mode does not match its status would
            # itself be the defect.
            _logger.info(
                "chat_service: finalization final_response_path=%s decision_status=%s "
                "selected_response_mode=%s request_class=%s "
                "captured_guidance_present=%s enforced_guidance_present=%s rendered_guidance_present=%s "
                "clarification_required=%s clarification_rendered_present=%s response_backstop_required=%s "
                "runner_text_present=%s candidate_response_present=%s final_text_length=%d "
                "final_output_validator_passed=%s "
                "authorized_command_count=%d prohibited_command_count=%d run_id=%s",
                final_response_path,
                execution_decision.status,
                response_mode,
                execution_decision.request_class,
                captured_guidance_present,
                enforced_guidance_present,
                rendered_guidance_present,
                clarification_required,
                clarification_rendered_present,
                response_backstop_required,
                pre_mode_text_present,
                final_text is not None,
                len(final_text) if final_text is not None else 0,
                final_output_validated,
                len(authorized_response_context.authorized_commands),
                len(authorized_response_context.prohibited_commands),
                sequencer.run_id,
            )

        if error is None and final_text is None:
            # A turn that produced no final text at all is itself an
            # unexpected runtime condition, not a user input problem.
            #
            # LIVE-CORR-14 section 11 -- STILL REACHABLE, deliberately,
            # for exactly two shapes, and no longer for any VALID policy
            # state: (a) `UNRESOLVED_CONTRACT`/`INVALID_CONTRACT` -- a
            # turn whose model produced no output at all and recorded no
            # contract (see `_ResponseMode.UNRESOLVED_CONTRACT`), and
            # (b) `APPROVAL`, whose user-facing proposal text is owned by
            # the separate approval state machine. Every other mode now
            # guarantees a non-empty `final_text` before this point:
            # CLARIFICATION always renders (the deterministic sentence is
            # its own fallback), RESTRICTION always has its deterministic
            # text, and AUTHORIZED_RESPONSE ends in an explicit safe
            # fallback.
            run_failure_reason = "no_final_text_after_response_mode_dispatch"
            error = ("run_failure", "The assistant did not produce a response. Please try again.")

        if not status_cleared:
            # Error or a no-true-streaming fallback turn (instruction
            # section 28/32) -- the activity line must still disappear.
            yield sequencer.build(StreamEventType.STATUS_CLEAR, {})

        if error is not None:
            if contributors_task is not None and not contributors_task.done():
                contributors_task.cancel()
            code, message = error
            _logger.info(
                "chat_service: finalization message_completed_emitted=False error_code=%s "
                "run_failure_reason=%s selected_response_mode=%s decision_status=%s run_id=%s",
                code,
                run_failure_reason or "upstream_turn_failure",
                response_mode,
                execution_decision.status,
                sequencer.run_id,
            )
            yield sequencer.build(StreamEventType.ERROR, {"code": code, "message": message})
            failed_trace = trace_recorder.record(**response_failed_trace_step())
            if failed_trace is not None:
                yield failed_trace
            yield sequencer.build(StreamEventType.RUN_COMPLETED, {"outcome": "error"})
            perf.log_duration("total_run", perf.elapsed_seconds())
            return

        # Pre-4H UX/provenance milestone: structured Teams source/
        # provenance data, if this turn actually retrieved grounded
        # evidence -- rides message.completed's own payload (never a new
        # event type/streaming protocol -- instruction section 13) so it
        # is always delivered atomically with, and owned by, the exact
        # message it describes. Omitted entirely (not a null placeholder)
        # when there is nothing to show, keeping this payload backward-
        # compatible with any consumer that only reads `content`.
        message_completed_data: dict[str, Any] = {"content": final_text}
        # Snippet-authenticity fix: `message_texts_by_id` (popped above,
        # from turn_context.py's in-process mailbox) is the turn's
        # `{message_id: text}` map of ACTUALLY retrieved Teams messages --
        # passed straight through so the Source drawer's snippets are
        # built from that, never from anything the model reproduces.
        #
        # SOURCE/PROVENANCE REGRESSION FIX (urgent pass): a direct-unique
        # fast-path turn whose trust-validation check failed closed (see
        # direct_read_fast_path.py's own "_trust_validation_failed_runs"
        # docstring) may still have an EARLIER, genuinely-successful
        # `incident_manager` function-response event already captured by
        # `source_capture` -- that evidence is real, but the user-facing
        # answer this turn actually produced is the safe fail-closed text,
        # which has nothing to do with it. Suppressed here, once, rather
        # than changing `TeamsSourceCapture` itself -- never attach a
        # Source to a message it does not actually describe.
        source_reference = (
            None
            if direct_fast_path_trust_validation_failed
            else source_capture.build_source_reference(message_texts_by_id)
        )
        visual_evidence_internal: list[dict[str, str]] = []

        # CHAT_ID SOURCE (bugfix): prefer the SAME already-authoritative,
        # cross-turn-persisted `selected_teams_chat_id` team_manager's own
        # prompt already relies on for chat continuity (state_sync.py) over
        # `TeamsSourceCapture`'s own single-event capture -- both are
        # populated from the identical `incident_manager` tool response,
        # but the session-state value survives even if this turn's raw
        # event, for whatever reason, did not carry a usable `chat_id`
        # (see source_reference.py's `resolve_authoritative_contributors`
        # docstring for the full investigation notes). Falls back to the
        # per-turn capture only if session state has nothing (e.g. a bare/
        # unwired test). Computed here, UNCONDITIONALLY (not only inside
        # "if source_reference is not None"), because Visual Evidence
        # below needs it independently of whether text evidence existed --
        # see the "image-only turn" fix immediately below.
        chat_id = (
            None
            if direct_fast_path_trust_validation_failed
            else refreshed_session.state.get(SELECTED_TEAMS_CHAT_ID_STATE_KEY) or source_capture.captured_chat_id()
        )

        # TEAMS VISUAL EVIDENCE LIVE-VALIDATION BUGFIX: see `ensure_
        # source_reference_for_visual_evidence`'s own docstring -- a
        # genuinely image-focused turn can deliver real images while
        # `incident_manager`'s own textual `evidence` citation list stays
        # empty, which previously meant Visual Evidence had no Source to
        # attach to at all.
        source_reference = ensure_source_reference_for_visual_evidence(
            source_reference, chat_id, delivered_visual_evidence
        )

        if source_reference is not None:
            # Contributor-accuracy fix, hardening pass: `contributors` is
            # resolved authoritatively from real Teams chat membership,
            # never from `evidence` authors -- see source_reference.py's
            # own docstring ("CONTRIBUTOR-ACCURACY FIX") and
            # `resolve_authoritative_contributors`. Scoped only to turns
            # that already produced a Teams source reference (never on
            # every turn).
            #
            # Performance pass: reuse the task already started mid-loop
            # (above) for this exact chat_id -- it may already be done, in
            # which case awaiting it is instant, having overlapped its
            # Power Automate round trip with the rest of this turn's own
            # generation. Only start a fresh (blocking) call here if the
            # authoritative chat_id differs from whatever was captured
            # in-loop (rare -- e.g. session state had a value from a prior
            # turn that this turn's own event never re-confirmed).
            if contributors_task is not None and chat_id == contributors_task_chat_id:
                contributors = await contributors_task
            else:
                if contributors_task is not None and not contributors_task.done():
                    # The in-loop task was for a different (superseded)
                    # chat_id than the one we actually need -- cancel it
                    # rather than leaving an unneeded Power Automate call
                    # running in the background.
                    contributors_task.cancel()
                contributors = await self._resolve_teams_contributors(chat_id)
            perf.mark("source_reference_enrichment")

            # Teams Visual Evidence milestone: only images ACTUALLY
            # delivered to Gemini this run, AND belonging to the SAME chat
            # this Source is actually about -- never attach an unrelated
            # run's/chat's images to this Source (section 6's own
            # "Source/message binding" requirement). Pure, independently
            # unit-tested function -- see hosted_content_vision_context
            # .py's own docstring for the full "why a separate function"
            # rationale and why `image_id` is minted once, here, shared
            # identically by the PUBLIC DTO and the INTERNAL binding.
            visual_evidence_items, visual_evidence_internal = build_visual_evidence(
                delivered_visual_evidence, chat_id
            )

            source_reference = source_reference.model_copy(
                update={"contributors": contributors, "visual_evidence": visual_evidence_items}
            )
            message_completed_data["source"] = source_reference.model_dump(mode="json")
        elif contributors_task is not None and not contributors_task.done():
            # No source reference ended up being built after all (e.g. the
            # turn's evidence was empty) -- discard the now-unneeded
            # in-flight fetch rather than leaving it dangling unawaited.
            contributors_task.cancel()

        # Phase 5.1J correction pass (Part C): a SEPARATE, purely additive
        # `knowledge_sources` list -- never merged into/replacing `source`
        # above (Teams and KM provenance keep their own, differently-
        # shaped DTOs; see knowledge_source_reference.py's own docstring
        # for why forcing them into one shape would damage both). Built
        # ONLY from `selected_knowledge_evidence` (snapshotted above, in
        # this method's own `finally`, from the trusted, run-id-keyed
        # store) -- never from `agent_payload`/model text. A combined-
        # answer turn may legitimately carry both `source` and
        # `knowledge_sources` on the SAME message.completed event.
        # Deduplicated by `(knowledge_id, version_label, section_id)`,
        # preserving selection order. An empty `selected_knowledge_evidence`
        # (no `knowledge_select_evidence` call this turn, or it was never
        # called at all) correctly omits the key entirely -- SEARCH RESULT
        # != EVIDENCE USED.
        #
        # B7 live-regression corrective pass -- a second, defensive exact-
        # identity normalization pass (never title/heading/content-based --
        # see knowledge_source_reference.py's own docstring) applied HERE,
        # at the single point this turn's `knowledge_sources` list is
        # finalized, so the live SSE event below AND the persisted
        # provenance record (a few lines further down) are always built
        # from the SAME already-safe list -- never two independently
        # "mostly deduplicated" lists that could drift apart.
        knowledge_sources = dedupe_knowledge_source_references(
            build_knowledge_source_references(selected_knowledge_evidence)
        )
        if knowledge_sources:
            message_completed_data["knowledge_sources"] = [
                reference.model_dump(mode="json") for reference in knowledge_sources
            ]

        # B7 corrective pass, widened by 6A.14A -- durably persists the
        # SAME already-safe `source_reference`/`knowledge_sources` this
        # turn just built for the live SSE event above, AND (6A.14A,
        # DEF-0031) this turn's own already fully corrected `final_text`
        # -- ONE canonical per-turn entry, keyed by this turn's own real
        # ADK `invocation_id` (`turn_invocation_id`, captured earlier from
        # the Runner's first event -- the same identity `session_history_
        # service.py`'s `turn_id` already uses). See turn_source_
        # references.py's own module docstring for why plain ADK session
        # state (never a new table/migration) is sufficient, why this
        # correctly disappears again if the turn is later rewound away
        # (ADK's own event-order-based state-delta reversal, not a new
        # mechanism), and why `final_text` belongs in this SAME entry
        # rather than a second, independently-persisted structure. Written
        # BEFORE the MESSAGE_COMPLETED event that announces it, matching
        # this method's own "persist before announcing" discipline
        # elsewhere -- PERSIST BEFORE ANNOUNCE: if this write fails, the
        # turn fails closed below and MESSAGE_COMPLETED is NEVER emitted,
        # so the live response and what history can later reproduce can
        # never diverge merely because persistence itself failed. A turn
        # with neither a Teams/governed-KM source nor any text to persist
        # has nothing to write -- `build_turn_source_references_delta`
        # returns `None` for that case (never reachable for a genuinely
        # completed turn, since `final_text` is always a real string by
        # this point -- see the `error is None and final_text is None`
        # guard earlier in this method).
        canonical_persistence_failed = False
        if turn_invocation_id is not None:
            end_of_turn_state_delta: dict[str, Any] = {}
            try:
                turn_source_references_delta = build_turn_source_references_delta(
                    refreshed_session.state.get(TURN_SOURCE_REFERENCES_STATE_KEY),
                    turn_invocation_id,
                    source_reference,
                    knowledge_sources,
                    visual_evidence_internal,
                    final_text=final_text,
                )
            except CanonicalTurnResultConflictError:
                _logger.error(
                    "chat_service: canonical turn result conflict for an already-persisted turn -- "
                    "failing closed rather than overwriting a prior authoritative answer run_id=%s",
                    sequencer.run_id,
                )
                canonical_persistence_failed = True
                turn_source_references_delta = None
            if turn_source_references_delta is not None:
                end_of_turn_state_delta[TURN_SOURCE_REFERENCES_STATE_KEY] = turn_source_references_delta

            # LIVE-CORR-12D -- Request-Scoped Governed Evidence Continuity,
            # section 13's own explicit "minimum explicit supersession/
            # clear behavior" requirement. `governed_completion_needed`
            # (computed earlier in this method) combined with `not
            # governed_evidence_continuity_permitted` means THIS turn was
            # POSITIVELY confirmed, by its own validated contract, to be a
            # fresh/unrelated governed request -- NOT a continuation of
            # whatever prior evidence the two keys below may still hold.
            # Written FIRST, before the two builders immediately below --
            # a real, fresh, non-empty selection THIS turn (the ordinary
            # successful case) still overwrites this clear with its own
            # real identity via those same builders' own `.update()` calls,
            # exactly as it always has; this clear only ever has a lasting
            # effect when this turn's own fresh retrieval genuinely found
            # nothing to replace it with (e.g. a "not found" outcome), so a
            # stale, unrelated anchor can never silently outlive the
            # request that made it stale. Deliberately narrow -- never a
            # blind clear on every empty selection (section 13's own
            # explicit "do not clear evidence blindly" prohibition): a
            # clarification-answer/diagnostic-result/same-procedure-next-
            # step turn never reaches `governed_completion_needed` with
            # `governed_evidence_continuity_permitted=False` in the first
            # place (see `is_governed_evidence_continuity_permitted`'s own
            # docstring), so continuity is never disturbed for those cases.
            if governed_completion_needed and not governed_evidence_continuity_permitted:
                end_of_turn_state_delta[LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY] = None
                end_of_turn_state_delta[ACTIVE_GOVERNED_PROCEDURE_STATE_KEY] = None

            # DEF-0026 corrective pass -- written ONLY from this turn's own
            # trusted, already-provenance-validated `selected_knowledge_
            # evidence` (the SAME list `knowledge_sources` above was just
            # built from) -- a turn that selected nothing this turn leaves
            # this key completely untouched (see `build_last_selected_
            # governed_evidence_state_update`'s own "empty means no
            # change" contract), never blanking out a previously valid
            # continuity anchor. Combined into the SAME single session-
            # state write as the provenance/canonical-result persistence
            # above, avoiding a second round trip.
            end_of_turn_state_delta.update(
                build_last_selected_governed_evidence_state_update(selected_knowledge_evidence)
            )
            # 6A.14 Active Procedure Continuity Correction -- a SEPARATE,
            # single-identity anchor derived from the SAME turn-local
            # `resolve_active_section_id` (DEF-0024/0027) applied to this
            # SAME `selected_knowledge_evidence`, using this turn's own raw
            # question text. Returns no key at all (a no-op, exactly like
            # the call immediately above) whenever this turn's own fresh
            # selection does not uniquely resolve one active section --
            # never overwrites a previously valid anchor with an
            # ambiguous/absent result.
            end_of_turn_state_delta.update(
                build_active_governed_procedure_state_update(
                    compute_fresh_active_procedure_anchor(selected_knowledge_evidence, _remediation_question(message_text))
                )
            )
            # LIVE-CORR-11 -- Pending Governed Request Continuity: merges
            # the update computed earlier in this method (alongside
            # `execution_decision`, where the fields it needs were already
            # available) into this SAME single end-of-turn write. Empty
            # (`{}`) whenever this turn produced no validated contract at
            # all -- see that computation's own comment for why pending
            # state is deliberately left untouched, not cleared, in that
            # case.
            end_of_turn_state_delta.update(pending_governed_request_state_update)

            # 6A.14A -- PERSIST BEFORE ANNOUNCE: a failure here (including
            # the conflict case detected above) must never let a live
            # response reach the user that a refresh/reopen could not
            # reproduce identically (DEF-0031) -- fail the turn closed,
            # using the SAME safe ERROR/RUN_COMPLETED(outcome=error) shape
            # this method already uses for every other unrecoverable
            # failure, rather than letting an exception propagate uncaught
            # (which would leave the SSE stream ending with no completion
            # signal at all -- see `_drive`'s own "`_run_turn_events`
            # never raises in the normal case" invariant in this same
            # module). `asyncio.CancelledError` is a `BaseException`,
            # deliberately NOT caught here -- cancellation must continue to
            # propagate and unwind normally (mirrors the D2 corrective
            # pass's own `except Exception`, never `except BaseException`,
            # discipline).
            if not canonical_persistence_failed and end_of_turn_state_delta:
                try:
                    await self._session_service.persist_state_delta(refreshed_session, end_of_turn_state_delta)
                except Exception:
                    _logger.warning(
                        "chat_service: end-of-turn canonical result persistence failed -- failing the turn "
                        "closed rather than announcing a response history could not reproduce run_id=%s",
                        sequencer.run_id,
                    )
                    canonical_persistence_failed = True

            if canonical_persistence_failed:
                # See `_best_effort_mark_turn_failed`'s own docstring --
                # the ADK Runner's own raw final-response event for this
                # turn is already durably appended regardless of whether
                # THIS write succeeded; without this marker a later
                # refresh would fall back to displaying that raw,
                # uncorrected text (DEF-0031's own "no contradictory
                # history" requirement).
                await self._best_effort_mark_turn_failed(session_id, user_id, turn_invocation_id)
                if contributors_task is not None and not contributors_task.done():
                    contributors_task.cancel()
                _logger.info(
                    "chat_service: finalization message_completed_emitted=False error_code=run_failure "
                    "reason=canonical_persistence_failed run_id=%s",
                    sequencer.run_id,
                )
                yield sequencer.build(
                    StreamEventType.ERROR,
                    {
                        "code": "run_failure",
                        "message": "The assistant's response could not be saved. Please try again.",
                    },
                )
                failed_trace = trace_recorder.record(**response_failed_trace_step())
                if failed_trace is not None:
                    yield failed_trace
                yield sequencer.build(StreamEventType.RUN_COMPLETED, {"outcome": "error"})
                perf.log_duration("total_run", perf.elapsed_seconds())
                return

        _logger.info("chat_service: finalization message_completed_emitted=True run_id=%s", sequencer.run_id)
        yield sequencer.build(StreamEventType.MESSAGE_COMPLETED, message_completed_data)

        pending_action = map_pending_action(refreshed_session.state)
        if pending_action is not None:
            yield sequencer.build(StreamEventType.ACTION_PENDING, pending_action.model_dump(mode="json"))

        # Interaction-capability extension -- mirrors ACTION_PENDING
        # exactly: independently re-derived from session state (never
        # from anything the model said this turn), so the frontend
        # renders only candidates a deterministic tool actually returned.
        pending_selection = map_pending_selection(refreshed_session.state)
        if pending_selection is not None:
            yield sequencer.build(StreamEventType.SELECTION_PENDING, pending_selection.model_dump(mode="json"))
            selection_trace = trace_recorder.record(**selection_prepared_trace_step())
            if selection_trace is not None:
                yield selection_trace

        # Unconditional terminal milestone for every successful run --
        # its (category, label) signature is unique to this call site, so
        # in practice this is never suppressed by dedup, but the guard is
        # kept identical to every other `record(...)` call site for
        # consistency (this generator must never yield `None`).
        generated_trace = trace_recorder.record(**response_generated_trace_step())
        if generated_trace is not None:
            yield generated_trace

        yield sequencer.build(StreamEventType.RUN_COMPLETED, {"outcome": "ok"})
        perf.log_duration("total_run", perf.elapsed_seconds())

    async def _safe_case_title(self, user_id: str, case_id: str) -> Optional[str]:
        try:
            case = await self._case_service.get_case(user_id, case_id)
        except SafeErrorException:
            # Stale/inaccessible active_case_id hint -- fail safe, exactly
            # like case_context.py's own instruction-provider behavior.
            return None
        return case.title

    async def _reload_and_persist_cleanup_delta(
        self, session_id: str, user_id: str, delta: dict[str, Any], perf: Optional[PerfTimer] = None
    ) -> None:
        """P0 correctness fix: any `persist_state_delta` write attempted
        AFTER this turn's team_manager Runner call has already run MUST
        use a freshly-reloaded `Session` object, never the one loaded at
        this turn's own start.

        ROOT CAUSE, verified against the installed ADK 1.33.0 source
        (`DatabaseSessionService.append_event`): a session object loaded
        by `get_session()` carries an exact storage-revision marker
        (`Session._storage_update_marker`); `append_event` compares it
        against the CURRENT stored value and raises `ValueError("The
        session has been modified in storage since it was loaded...")`
        on any mismatch. Sequential `persist_state_delta` calls using the
        SAME session object stay valid (a successful `append_event`
        updates that object's own marker in place) -- but team_manager's
        own `Runner.run_async` internally fetches its OWN, SEPARATE
        session object for the SAME `session_id` and appends multiple
        events through it as the turn progresses, advancing the row's
        storage revision past whatever this turn's own, pre-Runner
        `session` variable last knew about. A cleanup write after the
        Runner call, using that stale local object, therefore always
        eventually raises, EVEN THOUGH the turn's own message.completed
        had already been produced and yielded successfully -- the live
        incident this fixes: a valid summary immediately followed by a
        spurious "The assistant could not be reached" from this
        `ValueError` escaping the `finally` block, well after the
        SSE stream had already sent a complete answer.

        NEVER RAISES: this is always a best-effort, defense-in-depth
        write -- `pop_current_run_specialist_result`'s own turn-start
        crash-recovery sweep (`_run_turn_events`'s own call, at the top
        of every turn) is the AUTHORITATIVE backstop that guarantees a
        stale `TrustedSpecialistResult` envelope can never reach team_
        manager's prompt, regardless of whether THIS specific write
        below ever succeeds. A failure reloading/persisting here must
        never corrupt an already-successful, already-streamed turn with
        a false failure -- logged safely (session id only, no state
        content) and swallowed.
        """
        try:
            fresh_session = await self._session_service.get_session(session_id, user_id)
            if perf is not None:
                perf.mark("session_reloaded_for_cleanup")
            await self._session_service.persist_state_delta(fresh_session, delta)
        except Exception:
            _logger.warning(
                "chat_service: cleanup persist_state_delta failed after session reload "
                "(session_id=%s) -- relying on next turn's crash-recovery sweep",
                session_id,
            )

    async def _best_effort_mark_turn_failed(self, session_id: str, user_id: str, turn_id: str) -> None:
        """6A.14A/DEF-0031 -- when a turn's OWN canonical-result
        persistence attempt itself fails (or conflicts), the ADK Runner's
        own raw final-response event is nonetheless ALREADY durably
        appended (a separate, earlier `append_event` call this method
        does not control) -- so without this marker, `session_history_
        service.py`'s legacy fallback would display that raw,
        uncorrected text as though it were a real completed answer on the
        next refresh, reintroducing exactly the live/refreshed divergence
        this milestone exists to close. Best-effort, mirroring `_reload_
        and_persist_cleanup_delta`'s own "always re-fetch a fresh session,
        never raise" discipline exactly (the session object this method's
        own failed write just used may itself now be storage-revision-
        stale, per that method's own docstring) -- a failure here is
        logged and swallowed, never turning an already-reported failure
        into a second, different one. RESIDUAL RISK, honestly documented:
        if this best-effort write ALSO fails (e.g. a sustained database
        outage), history falls back to displaying this turn's raw,
        uncorrected text until a later successful turn overwrites this
        key's session state -- an accepted, narrow edge case, not solved
        by this milestone (see the 6A.14A closure report's own residual-
        risks section).
        """
        try:
            fresh_session = await self._session_service.get_session(session_id, user_id)
            delta = {
                TURN_SOURCE_REFERENCES_STATE_KEY: build_turn_failure_marker_delta(
                    fresh_session.state.get(TURN_SOURCE_REFERENCES_STATE_KEY), turn_id
                )
            }
            await self._session_service.persist_state_delta(fresh_session, delta)
        except Exception:
            _logger.warning(
                "chat_service: best-effort canonical-turn-failure marker persistence also failed "
                "(session_id=%s, turn_id=%s) -- history may show this turn's raw, uncorrected text "
                "until a future successful turn overwrites this session's state",
                session_id,
                turn_id,
            )

    async def _finalize_user_turn_activity(
        self, session_id: str, user_id: str, invocation_id: str, message_text: str
    ) -> None:
        """POST-5.1 B4B DEFECT FIX -- the saved-chat bookkeeping write
        (`has_visible_message`/`chat_title`/`chat_activity_at`), deferred
        to run HERE, from `_run_turn_events`'s own `finally` block, AFTER
        `turn_runner.run_async`'s generator has fully closed -- never
        while it is still active.

        SAME ROOT CAUSE, SAME FIX SHAPE, AS `_reload_and_persist_cleanup_
        delta` ABOVE (that method's own docstring has the full ADK-
        source-verified mechanism): a session object fetched before the
        Runner ran is stale the instant the Runner appends anything
        through its own, separate session handle. This method always
        re-fetches fresh, exactly like that one -- the only difference is
        WHAT gets written (`record_user_turn_activity`'s own decision,
        session_state_keys.py) rather than a fixed cleanup delta.

        A LIVE PRODUCTION INCIDENT (real Vertex/Gemini turn, a function-
        call tool use) proved the ACTIVE-RUN version of this write is not
        just theoretically risky but reliably fatal: it corrupted the
        Runner's OWN session revision tracking mid-invocation, causing
        the Runner's next internal append (the tool's function response)
        to raise ADK's "session has been modified in storage" staleness
        `ValueError` -- silently swallowed by `_run_turn_events`'s own
        outer `except Exception:`, aborting the turn with no further
        model continuation and the generic "The assistant could not
        complete this request" message, with the true cause never
        logged. Reproduced deterministically with a disposable local
        `DatabaseSessionService` + SQLite fixture (see
        test_chat_service_function_call_continuation.py).

        NEVER RAISES: best-effort, exactly like `_reload_and_persist_
        cleanup_delta` -- a failure here must never turn an otherwise-
        successful (or already-failed-for-a-different-reason) turn into
        a second, different failure. If this write is ever lost (a crash
        between the Runner finishing and this call, or this call's own
        failure), the session's `has_visible_message` marker either
        stays absent (pre-existing sessions) or was already initialized
        `False` at creation -- `session_history_service.py`'s bounded
        legacy-marker/activity backfill is the standing, self-terminating
        repair path for exactly this gap, unchanged by this fix.
        """
        try:
            fresh_session = await self._session_service.get_session(session_id, user_id)
            await record_user_turn_activity(self._session_service, fresh_session, message_text, invocation_id)
        except Exception:
            _logger.warning(
                "chat_service: saved-chat activity finalization failed after session reload "
                "(session_id=%s) -- relying on the next GET /api/sessions legacy-marker repair",
                session_id,
            )

    async def run_turn(
        self,
        session_id: str,
        message_text: str,
        user_id: str = DEFAULT_USER_ID,
        attachment_ids: Sequence[str] = (),
    ) -> ChatResponse:
        # Raises `SafeErrorException` (not_found) for an unknown session, OR
        # a session that exists but belongs to a different user -- see
        # session_service.py's "OWNERSHIP, FOR FREE". This check happens
        # BEFORE calling into the canonical pipeline, so an invalid/foreign
        # session id never even starts a run (instruction section 17).
        await self._session_service.get_session(session_id, user_id)

        final_text: Optional[str] = None
        pending_action_data: Optional[dict] = None
        error_message: Optional[str] = None

        async with Aclosing(
            self.execute_turn_events(session_id, message_text, user_id, attachment_ids)
        ) as events:
            async for event in events:
                if event.type == StreamEventType.MESSAGE_COMPLETED:
                    final_text = event.data.get("content")
                elif event.type == StreamEventType.ACTION_PENDING:
                    pending_action_data = event.data
                elif event.type == StreamEventType.ERROR:
                    error_message = event.data.get("message")

        if error_message is not None:
            raise run_failure(error_message)

        pending_action = PendingActionDTO(**pending_action_data) if pending_action_data else None
        active_case: Optional[ActiveCaseDTO] = await get_active_case_for_session(
            self._case_service, user_id, session_id
        )

        return ChatResponse(
            session_id=session_id,
            message=AssistantMessage(content=final_text or ""),
            pending_action=pending_action,
            active_case=active_case,
        )

    async def rewind_before_user_turn(
        self, session_id: str, before_user_turn_index: int, user_id: str = DEFAULT_USER_ID
    ) -> None:
        """Phase 4G hardening pass -- conversational branching for editing
        a historical user message.

        Rewinds this session so that the user turn at
        `before_user_turn_index` (0-based, among the session's currently
        ACTIVE turns -- see `_resolve_invocation_id_for_active_user_turn`)
        and everything chronologically after it is excluded from every
        future turn's model context. A thin, safe wrapper around ADK's
        own `Runner.rewind_async` (verified against the installed 1.33.0
        source) -- nothing is deleted or mutated in place; ADK appends
        one new "rewind" event, and its own state-delta reversal restores
        `session.state` to what it was immediately before that turn (e.g.
        a Teams chat selected only during a since-discarded turn is
        correctly un-selected again). No tool is ever re-executed:
        appending an event is pure bookkeeping, never a live agent turn
        -- the SAME reason `session_service.py`'s own `persist_state_delta`
        (used by the approve/reject endpoints) is safe.

        SAME SESSION, NEVER A NEW ONE: this session's id
        (`Chat.backendSessionId` on the frontend) never changes -- there
        is nothing to "switch" the frontend onto. The authoritative
        Case<->session link (`CaseSessionLinkRecord`, a separate store
        keyed by `session_id`, re-derived fresh on every read -- see
        `case_service.py`) is therefore completely unaffected by a
        rewind; only the non-authoritative `active_case_id` HINT inside
        `session.state` is subject to the same automatic reversal as any
        other state key.

        Locked exactly like every other session-mutating operation in
        this backend (`session_service.py`'s module docstring) so this
        can never race a concurrently in-flight turn on the same session.

        Raises `SafeErrorException` -- `not_found` for an unknown/foreign
        session (checked before the lock, mirroring
        `approval_service.py`'s pattern), `validation_error` for an
        out-of-range/stale turn index -- and performs NO mutation at all
        in either case: callers must treat a raised exception as "nothing
        happened," never a partial rewind (instruction: fail before
        commit, never leave the frontend attached to a half-truncated
        conversation).
        """
        await self._session_service.get_session(session_id, user_id)  # 404 before ever taking the lock

        async with self._session_service.lock_for(session_id, user_id):
            session = await self._session_service.get_session(session_id, user_id)  # the authoritative, latest read
            invocation_id = _resolve_invocation_id_for_active_user_turn(session.events, before_user_turn_index)
            if invocation_id is None:
                raise validation_error(
                    "This message can no longer be edited. Please refresh and try again."
                )
            await self._runner.rewind_async(
                user_id=user_id, session_id=session_id, rewind_before_invocation_id=invocation_id
            )

    async def cancel_run(self, session_id: str, run_id: str, user_id: str = DEFAULT_USER_ID) -> bool:
        """Trusted server-side cancellation entry point (pre-4H
        refinement -- real Stop, not just a client-transport abort).
        Called only from the `/cancel` API route (app.py), never from
        anywhere an ADK tool/agent could reach (mirrors the security
        posture of `approval_service.approve`/`reject` and
        `selection_service.choose`/`skip` -- a trusted-boundary
        transition, never something the model can trigger for itself).

        SECURITY: session ownership is verified FIRST, exactly like every
        other session-scoped method here (`SafeErrorException`
        `not_found` for an unknown/foreign `session_id` -- the same 404 a
        client would get for a completely made-up session id, so a
        foreign user learns nothing about whether `run_id` exists for
        someone else's session).

        IDEMPOTENT / SAFE, returns `False` (never raises) for every "no
        actual work to do" case, uniformly:
          - the run already finished naturally before this call arrived
            (a normal race between a fast run and a slow Stop click);
          - `run_id` is stale -- a NEWER run has since started on this
            same session and now owns `self._run_tasks[(session_id, *)]`
            under its own, different key, so the old `run_id` simply
            isn't found;
          - `run_id` was never valid for this session at all.
        Returns `True` only when a genuinely still-running task's own
        `asyncio.Task.cancel()` was actually issued.

        WHAT THIS ACTUALLY CANCELS: exactly the one background
        `asyncio.Task` `execute_turn_events` created for this run (see
        that method's own docstring for why the turn already runs as an
        independent task, decoupled from the HTTP/SSE consumer) --
        `task.cancel()` delivers `CancelledError` at that task's current
        `await` point. Because that task is the ONLY thing driving this
        run's ADK `Runner`, translating its events, and yielding them
        onward, cancelling it means: no further model-continuation
        processing, no further tool-result processing, no final
        assistant response, and no further session-state mutation
        (including no new `ActionProposal`) from this run, from this
        point forward -- this is Python's own `asyncio` cancellation
        semantics doing the work, not a new mechanism invented here.

        WHAT THIS CANNOT DO: a synchronous, already-in-flight worker-
        thread call (e.g. a Power Automate HTTP request ADK is already
        running in a thread for a sync Teams tool function) is not
        forcibly interruptible -- that thread keeps running to its own
        completion in the background exactly as it would without this
        call. Cancelling the awaiting task only means nothing is left
        `await`-ing that thread's eventual result once it returns, so it
        is safely discarded rather than being mistaken for this
        (already-cancelled) run's outcome -- never claimed as "the
        backend operation itself was interrupted," because it was not.
        """
        await self._session_service.get_session(session_id, user_id)  # 404 for unknown/foreign session
        task = self._run_tasks.get((session_id, run_id))
        if task is None or task.done():
            return False
        task.cancel()
        return True


@lru_cache(maxsize=1)
def get_chat_service() -> ChatService:
    """Process-wide singleton (mirrors `get_session_service`) -- the real
    `Runner` is built once and reused across requests/sessions; `Runner`
    itself is stateless with respect to which session a given call targets
    (that's supplied per-call via `session_id`), so sharing it is safe.
    """
    return ChatService(
        get_session_service(),
        case_service=get_case_service(),
        attachment_service=get_attachment_service(),
        attachment_storage=get_attachment_storage(),
    )
