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
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Protocol, Sequence

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
from backend.agents.team_manager.governed_knowledge_completion import (
    SAFE_COMPLETION_FAILURE_TEXT,
    enforce_governed_knowledge_at_completion,
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
from backend.api.knowledge_source_reference import (
    build_knowledge_source_references,
    dedupe_knowledge_source_references,
)
from backend.api.source_reference import TeamsSourceCapture, resolve_authoritative_contributors
from backend.api.multimodal_turn_context import (
    discard_run_images,
    register_run_images,
    trusted_image_parts_from_content,
)
from backend.agents.incident_manager.schemas import TroubleshootingGuidance
from backend.api.applicability_context_capture import discard_known_applicability_context
from backend.api.streaming_events import EventSequencer, Stage, StreamEvent, StreamEventType, status_data
from backend.api.troubleshooting_guidance_context import (
    discard_troubleshooting_guidance,
    pop_troubleshooting_guidance,
    render_troubleshooting_guidance,
)
from backend.api.turn_context import bind_run_id, pop_message_texts, reset_run_id
from backend.api.turn_source_references import TURN_SOURCE_REFERENCES_STATE_KEY, build_turn_source_references_delta
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService, get_attachment_service
from backend.attachments.storage import ChatAttachmentStorage, get_attachment_storage
from backend.cases.service import CaseService, get_case_service
from backend.config.settings import get_settings
from backend.gateway.safe_error import SafeErrorException, run_failure, validation_error
from backend.selection.service import PENDING_READ_CONTINUATION_STATE_KEY, pop_read_continuation
from backend.tools.knowledge.runtime import discard_knowledge_run_evidence_state, snapshot_selected_knowledge_evidence
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

    from backend.agents.team_manager.direct_read_fast_path import get_fast_path_team_manager

    # P4B.3 COMPLETION PASS: `get_fast_path_team_manager()` is a `.model_
    # copy` of `team_manager` (agent.py) with one additional, normally-
    # inert `before_model_callback` prepended -- see that module's own
    # docstring for the full ADK-source-verified mechanism. Every other
    # aspect (instruction, tools, other callbacks) is identical to the
    # base `team_manager` this Runner used before this pass.
    return Runner(
        app_name=APP_NAME,
        agent=get_fast_path_team_manager(),
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
        # A5 live UI corrective pass -- FINAL trust-gate closure: turn-
        # local only (never session state, never Case context, never a
        # source reference -- see the delta-emission block below for the
        # full three-state rationale). Holds team_manager's own text
        # chunks for exactly as long as this turn's source-requirements
        # classification (`source_requirements_capture`) is UNKNOWN
        # (`declared is False`) -- released verbatim, in order, the
        # instant classification resolves to explicitly non-governed;
        # discarded, never emitted, the instant it resolves to explicitly
        # governed. Goes out of scope (and is never inspected again) the
        # moment this generator returns, on every exit path.
        buffered_delta_texts: list[str] = []
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
        run_id_token = bind_run_id(sequencer.run_id)
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
                        specialist_result_state_written = True

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
                turn_runner = self._presentation_runner if specialist_result_state_written else self._runner
                if specialist_result_state_written:
                    # Safe diagnostic only (never a chat id, message body,
                    # or trusted payload) -- mirrors perf_timing.py's own
                    # safe-logging contract.
                    _logger.info(
                        "perf stage=trusted_result_presentation_mode run_id=%s", sequencer.run_id
                    )
                async with Aclosing(
                    turn_runner.run_async(
                        user_id=user_id, session_id=session_id, new_message=content, run_config=run_config
                    )
                ) as agen:
                    async for event in agen:
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

                        # A5 live UI corrective pass -- FINAL trust-gate
                        # closure. `SourceRequirementsCapture` has THREE
                        # semantic states, not two, even though it is stored
                        # as two booleans:
                        #   UNKNOWN               -- declared is False
                        #   EXPLICIT NON-GOVERNED -- declared True, requires_governed_knowledge False
                        #   EXPLICIT GOVERNED     -- declared True, requires_governed_knowledge True
                        # The prior pass's fix only gated the THIRD state --
                        # `declared is False` (UNKNOWN, the state every turn
                        # starts in, before team_manager's own `record_
                        # source_requirements` call is observed) was
                        # silently treated the same as explicit-False, so a
                        # turn that never declares at all during the main
                        # loop (caught only by the EXISTING post-loop
                        # declaration-remediation further below) could still
                        # stream team_manager's own untrusted prose live
                        # during the loop, before that remediation ever ran.
                        #
                        # This check runs on EVERY event (not only ones that
                        # themselves carry text) and reacts the INSTANT
                        # classification resolves -- required because the
                        # declaration itself arrives as a text-less function-
                        # response event; if classification resolved to
                        # non-governed with no FURTHER delta event ever
                        # following in the same turn, gating only inside the
                        # delta-handling block below would leave the
                        # buffered text released nowhere, corrupting
                        # `message.delta`'s own promise to carry the full
                        # answer for a turn with no other exposure path.
                        if buffered_delta_texts and source_requirements_capture.declared:
                            if source_requirements_capture.requires_governed_knowledge:
                                # EXPLICIT GOVERNED -- permanently discard
                                # everything buffered while still UNKNOWN.
                                # `final_text` for this turn is decided later
                                # in this method (troubleshooting_guidance
                                # override / governed-knowledge completion
                                # remediation) from TRUSTED state, never from
                                # team_manager's own live prose -- live-
                                # reproduced proof (VSWR follow-up turn):
                                # team_manager streamed "...within the
                                # acceptable range..." while the trusted,
                                # remediated `message.completed` carried a
                                # materially different, correctly-grounded
                                # answer. Never emitted, never held onto past
                                # this point.
                                buffered_delta_texts = []
                            else:
                                # EXPLICIT NON-GOVERNED -- release everything
                                # buffered while UNKNOWN, in original order,
                                # immediately -- a single coherent reveal,
                                # regardless of whether THIS event itself
                                # carries any further text.
                                pending_texts = buffered_delta_texts
                                buffered_delta_texts = []
                                for pending_text in pending_texts:
                                    if not status_cleared:
                                        yield sequencer.build(StreamEventType.STATUS_CLEAR, {})
                                        status_cleared = True
                                        perf.mark("first_message_delta")
                                    yield sequencer.build(StreamEventType.MESSAGE_DELTA, {"text": pending_text})

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
                        if delta_text is not None:
                            # By this point in the loop iteration, the
                            # buffer-reconciliation check above has already
                            # resolved any classification THIS event itself
                            # carried, so only three cases remain for this
                            # event's OWN delta text specifically:
                            if not source_requirements_capture.declared:
                                # Still UNKNOWN -- buffer verbatim, turn-
                                # local only (never session state/Case
                                # context/a source reference -- goes out of
                                # scope with this generator on every exit
                                # path). See the buffer-reconciliation
                                # check above for the full three-state
                                # rationale and what happens once
                                # classification resolves.
                                buffered_delta_texts.append(delta_text)
                            elif source_requirements_capture.requires_governed_knowledge:
                                # EXPLICIT GOVERNED -- discard; never
                                # emitted, never held onto past this point.
                                pass
                            else:
                                # EXPLICIT NON-GOVERNED -- the buffer is
                                # already empty (flushed above the instant
                                # classification resolved), so this is
                                # simply live streaming, byte-identical to
                                # before this pass for a turn whose
                                # declaration arrives before its first delta
                                # (the common case).
                                if not status_cleared:
                                    yield sequencer.build(StreamEventType.STATUS_CLEAR, {})
                                    status_cleared = True
                                    perf.mark("first_message_delta")
                                yield sequencer.build(StreamEventType.MESSAGE_DELTA, {"text": delta_text})

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
                        yield sequencer.build(StreamEventType.MESSAGE_DELTA, {"text": retry_text})
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
        except Exception:
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
            captured_troubleshooting_guidance = pop_troubleshooting_guidance(sequencer.run_id)
            discard_troubleshooting_guidance(sequencer.run_id)
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
            selected_knowledge_evidence = snapshot_selected_knowledge_evidence(sequencer.run_id)
            # Same discipline for the Generic KM tool adapter's own
            # run-id-keyed trusted evidence state
            # (backend/tools/knowledge/runtime.py) -- guarantees no
            # `KnowledgeRunEvidenceState` (available/selected evidence)
            # survives past the one turn/run that produced it, on any
            # exit path, exactly like every other piece of this turn's
            # own per-run bookkeeping cleaned up in this same block.
            discard_knowledge_run_evidence_state(sequencer.run_id)
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

        if error is None and source_requirements_capture.requires_governed_knowledge and not selected_knowledge_evidence:
            # FOURTH pre-4H correction pass: PAST ASSISTANT OUTPUT != GOVERNED
            # KNOWLEDGE. team_manager's own turn declared (via `record_
            # source_requirements`, directly or via the remediation just
            # above) that THIS request requires current governed
            # knowledge, but this run's own trusted, run-scoped SELECTED
            # evidence (snapshotted above, in the `finally` block) is
            # empty -- whether because team_manager never delegated to
            # incident_manager at all, or because its own free-form
            # presentation did not carry a validated result forward
            # faithfully. `final_text` is therefore UNTRUSTED for this
            # governed-knowledge portion and must not reach the user as-is.
            # See governed_knowledge_completion.py's own module docstring
            # for the full live-failure rationale -- this deterministically
            # forces the REAL, unmodified `incident_manager` to run, so ITS
            # OWN existing compliance retry (provenance_compliance.py,
            # third correction pass) is what actually enforces selection;
            # this is not a second, competing selection mechanism.
            _logger.warning(
                "chat_service: requires_governed_knowledge declared but no current selected evidence -- "
                "forcing deterministic governed-knowledge remediation run_id=%s",
                sequencer.run_id,
            )
            try:
                chat_topic = (
                    refreshed_session.state.get(SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY)
                    if source_requirements_capture.requires_teams
                    else None
                )
                final_text, selected_knowledge_evidence = await enforce_governed_knowledge_at_completion(
                    question=_remediation_question(message_text),
                    chat_topic=chat_topic,
                    run_id=f"{sequencer.run_id}::governed-completion",
                    # B7 live-regression corrective pass -- this turn's own
                    # trusted image evidence (already validated once, into
                    # `content`, above) must remain available across this
                    # bounded remediation, exactly as it was for the
                    # original delegation -- see governed_knowledge_
                    # completion.py's own module docstring for the full
                    # live-failure narrative this closes.
                    image_parts=trusted_image_parts_from_content(content),
                )
            except Exception:
                _logger.warning(
                    "chat_service: governed-knowledge completion remediation raised -- failing closed run_id=%s",
                    sequencer.run_id,
                )
                final_text = SAFE_COMPLETION_FAILURE_TEXT
                selected_knowledge_evidence = []

        if error is None and final_text is not None:
            # A5 final corrective pass -- the HARD, deterministic one-
            # command-at-a-time override: if incident_manager populated
            # `troubleshooting_guidance` anywhere in this turn (captured
            # by evidence.py's own after_agent_callback the instant its
            # structured response was parsed, regardless of which of the
            # call paths above produced it), the final answer the user
            # sees is UNCONDITIONALLY replaced with the deterministic
            # Python rendering of that typed field -- never team_
            # manager's own free-form presentation of it, which live
            # testing proved does not reliably stay bounded to one
            # action on its own. A turn that never populated the field
            # (every non-troubleshooting request) is completely
            # unaffected: `pop_troubleshooting_guidance` returns `None`
            # and `final_text` is left exactly as team_manager produced
            # it.
            if captured_troubleshooting_guidance is not None:
                final_text = render_troubleshooting_guidance(captured_troubleshooting_guidance)

        if error is None and final_text is None:
            # A turn that produced no final text at all is itself an
            # unexpected runtime condition, not a user input problem.
            error = ("run_failure", "The assistant did not produce a response. Please try again.")

        if not status_cleared:
            # Error or a no-true-streaming fallback turn (instruction
            # section 28/32) -- the activity line must still disappear.
            yield sequencer.build(StreamEventType.STATUS_CLEAR, {})

        if error is not None:
            if contributors_task is not None and not contributors_task.done():
                contributors_task.cancel()
            code, message = error
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
        if source_reference is not None:
            # Contributor-accuracy fix, hardening pass: `contributors` is
            # resolved authoritatively from real Teams chat membership,
            # never from `evidence` authors -- see source_reference.py's
            # own docstring ("CONTRIBUTOR-ACCURACY FIX") and
            # `resolve_authoritative_contributors`. Scoped only to turns
            # that already produced a Teams source reference (never on
            # every turn).
            #
            # CHAT_ID SOURCE (bugfix): prefer the SAME already-authoritative,
            # cross-turn-persisted `selected_teams_chat_id` team_manager's
            # own prompt already relies on for chat continuity
            # (state_sync.py) over `TeamsSourceCapture`'s own single-event
            # capture -- both are populated from the identical
            # `incident_manager` tool response, but the session-state value
            # survives even if this turn's raw event, for whatever reason,
            # did not carry a usable `chat_id` (see source_reference.py's
            # `resolve_authoritative_contributors` docstring for the full
            # investigation notes). Falls back to the per-turn capture only
            # if session state has nothing (e.g. a bare/unwired test).
            chat_id = refreshed_session.state.get(
                SELECTED_TEAMS_CHAT_ID_STATE_KEY
            ) or source_capture.captured_chat_id()

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

            source_reference = source_reference.model_copy(update={"contributors": contributors})
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

        # B7 corrective pass -- durably persists the SAME already-safe
        # `source_reference`/`knowledge_sources` this turn just built for
        # the live SSE event above, keyed by this turn's own real ADK
        # `invocation_id` (`turn_invocation_id`, captured earlier from the
        # Runner's first event -- the same identity `session_history_
        # service.py`'s `turn_id` already uses). See turn_source_
        # references.py's own module docstring for why plain ADK session
        # state (never a new table/migration) is sufficient, and why this
        # correctly disappears again if the turn is later rewound away
        # (ADK's own event-order-based state-delta reversal, not a new
        # mechanism). Written BEFORE the MESSAGE_COMPLETED event that
        # announces it, matching this method's own "persist before
        # announcing" discipline elsewhere. A turn with neither a Teams
        # nor a governed-KM source has nothing to persist -- `build_turn_
        # source_references_delta` returns `None` for that case, and no
        # write happens at all.
        if turn_invocation_id is not None:
            turn_source_references_delta = build_turn_source_references_delta(
                refreshed_session.state.get(TURN_SOURCE_REFERENCES_STATE_KEY),
                turn_invocation_id,
                source_reference,
                knowledge_sources,
            )
            if turn_source_references_delta is not None:
                await self._session_service.persist_state_delta(
                    refreshed_session, {TURN_SOURCE_REFERENCES_STATE_KEY: turn_source_references_delta}
                )

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
