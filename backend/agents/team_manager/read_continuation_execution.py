"""Deterministic, application-orchestrated execution of a
`ResolvedReadContinuation`'s Incident Manager/read path -- production
hardening pass #2, revised in pass #3 to remove the standalone,
unrelated `InMemorySessionService()` runtime.

THE WEAKNESS PASS #3 FIXES: this module previously constructed a
`Runner(agent=incident_manager, session_service=InMemorySessionService())`
-- a brand-new, throwaway ADK runtime with no relationship to the app's
own configured session backend (`DatabaseSessionService` in production).
That worked (evidence validation still fired -- see below -- and no data
was lost), but it meant Incident Manager execution ran completely outside
the canonical authorized SLOPANOC user/session/runtime boundary: a
different backend, a different connection pool/transaction context, and
no way for a future specialist to legitimately read forwarded Case/Fault
context the way `AgentTool.run_async`'s own state forwarding already
allows for a live, model-initiated delegation.

FIX: this module now takes the CALLER's own canonical `session_service`
(the same `BaseSessionService` instance `ChatService`'s own team_manager
`Runner` already uses -- see `chat_service.py`'s `_build_runner`) and
runs `incident_manager` through it, in a session created via that SAME
service. This is option (A) from the instruction's own list ("canonical
SessionService + derived internal specialist session key tied to parent
session/run") -- verified as ADK-supported, not invented: `create_session`
and `delete_session` are both `abc.abstractmethod`s on `BaseSessionService`
itself (`sessions/base_session_service.py`), so every backend this app
could ever be configured with (`InMemorySessionService`,
`DatabaseSessionService`, ...) is REQUIRED to implement both -- using them
here relies on nothing backend-specific.

WHY A *DERIVED* SESSION, NOT THE PARENT session_id DIRECTLY (instruction
section 4's "do not corrupt the visible transcript"): `Runner.run_async`
builds a turn's model context from `session.events` -- ALL of them,
regardless of `author` (verified in an earlier milestone; this is exactly
how a normal multi-turn team_manager conversation already accumulates
context). Running `incident_manager` against the PARENT session_id would
(a) hand it the user's entire SLOPANOC conversation history as if it were
incident_manager's own prior turns -- context pollution with no benefit,
since incident_manager is deliberately stateless per its own contract
(`IncidentManagerRequest.question`'s docstring), and (b) append
incident_manager-authored events into the SAME event history the
frontend/`_active_events` render as the user-visible transcript, making
Incident Manager a second visible conversational participant -- exactly
what section 4 forbids. A session ID *derived from* the parent
(`{parent_session_id}::specialist::{run_id}`) keeps the SAME canonical
service/backend and the SAME `user_id` (the authorization boundary that
actually matters), while giving incident_manager a genuinely fresh,
isolated conversation of its own -- the same isolation an `AgentTool`
-wrapped call already gets from its own throwaway nested session, just
now backed by the real, canonical service instead of a disposable
in-memory one.

APP NAME NAMESPACING: uses `_INTERNAL_SPECIALIST_APP_NAME`
(`f"{APP_NAME}::specialist-internal"`), never the user-facing `APP_NAME`
itself -- so this session can never appear in a `list_sessions(app_name=
APP_NAME, ...)` call the way a real SLOPANOC chat would (e.g. the
sidebar's chat history), even for the brief window before it is deleted
below. `user_id` is still the SAME authorized user throughout.

STATE FORWARDING: the derived session's initial state is seeded from a
COPY of the parent session's CURRENT state (`_adk`-prefixed keys
excluded, mirroring `AgentTool.run_async`'s own established filtering)
plus `resolved_chat_id` (a PLAIN key -- see `_seeded_state`'s own
docstring, "P1 LIVE-INCIDENT FIX", for why `temp:`-prefixing this
specific value was itself the root cause of a live incident), so any
Case/Fault context state a future specialist might legitimately need is
available on the same terms, without this module needing to know its
shape. Only STATE is copied, never EVENTS -- this is what prevents
cross-talk into incident_manager's own reasoning while still giving it
access to
whatever contextual state it is entitled to.

CLEANUP: the derived session is deleted in a `finally` block immediately
after use (success or failure) -- this app's canonical backend may be a
durable `DatabaseSessionService`, and a specialist's raw retrieved-
content-derived result (summary/decisions/etc.) must not be left
persisted indefinitely in an unrelated, un-surfaced session (instruction
section 18). A `finally` block runs on every exit path, including
`asyncio.CancelledError` (a `BaseException`, not caught by any `except
Exception` anywhere in this chain) -- so a cancelled run still cleans up
its own internal session. Deletion failure is logged, never raised (a
cleanup failure must never mask the real result or a real execution
error).

EVIDENCE VALIDATION UNCHANGED: `after_agent_callback=strip_unverified_
evidence` is set on the `incident_manager` AGENT object itself
(agent.py), so it fires identically here, on this canonical-service-
backed Runner, exactly as it already did on the prior pass's standalone
one.

CANCELLATION: no exception-swallowing of its own -- the `async for event
in runner.run_async(...)` loop is a plain `await`-driving loop;
`asyncio.CancelledError` propagates through this function to
`chat_service.py`'s own existing cancellation-safe handling, exactly as
before. `finally` here still runs first (session cleanup), then the
exception continues propagating.

NO NEW AGENT, NO CLASSIFIER, NO MODEL CALL BEYOND THE ONE `incident_
manager` HAS ALWAYS MADE: unchanged from pass #2.
"""
from __future__ import annotations

import logging
import time
from contextvars import ContextVar
from typing import Any, Optional

from google.adk.agents.run_config import RunConfig, ToolThreadPoolConfig
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import BaseSessionService
from google.adk.tools import ToolContext
from google.genai import types
from pydantic import BaseModel, Field

from backend.agents.incident_manager.agent import incident_manager
from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION
from backend.agents.incident_manager.schemas import (
    IncidentManagerOutcome,
    IncidentManagerRequest,
    IncidentManagerResponse,
)
from backend.api.session_service import APP_NAME
from backend.gateway.safe_error import validation_error
from backend.selection.read_resume import build_read_resume_message
from backend.selection.schemas import PendingReadIntent, ResolvedReadContinuation
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY, DEFAULT_MAX_MESSAGES, teams_get_messages
from backend.tools.teams.list_chats import teams_list_chats

_logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("backend.perf")

_INTERNAL_SPECIALIST_APP_NAME = f"{APP_NAME}::specialist-internal"
"""Deliberately distinct from `APP_NAME` (see this module's own
docstring, "APP NAME NAMESPACING") -- never the app name any user-facing
session listing/lookup queries.
"""

_active_continuation_chat_id: ContextVar[Optional[str]] = ContextVar(
    "_active_continuation_chat_id", default=None
)
"""URGENT R2 FIX -- the ONE chat id this specific continuation execution is
authoritative for -- bound around this module's own `runner.run_async`
call (`execute_read_continuation`, below), read back by `get_resolved_
chat_messages` (below) to supply the destination ITSELF, never to validate
a model-supplied one. Same established ContextVar pattern as `backend.api
.turn_context.current_run_id` (propagation across `_call_tool_in_thread_
pool`'s executor-thread boundary already verified safe for that mechanism
-- see perf_timing.py/read_continuation_execution.py's own "LATENCY PASS"
docstring above -- the same guarantee applies here unchanged).

ROOT CAUSE OF THE LIVE REGRESSION THIS FIX REPLACES (found by direct
inspection, not guessed): the PREVIOUS design here (`_enforced_teams_get_
messages`) still exposed `chat_id: str` as a REQUIRED, model-visible
parameter on a `teams_get_messages`-shaped tool -- the model had to
correctly retype the resolved chat id (a long, exact identifier string)
from its own prompt's `{resolved_chat_id?}` placeholder into the tool
call's arguments, and this wrapper REJECTED the call outright on any
mismatch (returning a safe `{"error": ...}` to the model, never reaching
Power Automate). Live logs showed exactly this: a `function_call` was
made, but no `power_automate_gateway operation=teams.getMessages` line
ever appeared -- the model's own retyped `chat_id` argument did not
byte-for-byte match `_active_continuation_chat_id.get()`, so the call was
rejected before the gateway was ever reached, and the model then produced
a text response describing the failure rather than retrying with a
corrected value it had no way to obtain. This was an architecturally
wrong design: asking Gemini to supply/reconstruct a destination the
backend ALREADY has, then validating what it supplied, rather than never
asking for it at all.
"""

_resolved_chat_retrieval_used: ContextVar[bool] = ContextVar(
    "_resolved_chat_retrieval_used", default=False
)
"""URGENT R2 FIX -- set the moment `get_resolved_chat_messages` is invoked
for the current continuation (success OR failure), so any FURTHER call
this same continuation execution is rejected deterministically before it
could ever reach a second real gateway request -- "exactly one
`teams.getMessages`" as a structural guarantee, not merely an expectation
of well-behaved model output. Bound/reset in the SAME `try`/`finally` as
`_active_continuation_chat_id` (execute_read_continuation, below).
"""


def get_resolved_chat_messages(
    max_messages: int = DEFAULT_MAX_MESSAGES,
    from_datetime: Optional[str] = None,
    to_datetime: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Retrieve the messages in the Teams conversation the user already
    selected for this request -- the destination itself is already
    resolved and bound server-side; it is not a parameter you can set or
    change.

    Behaves exactly like retrieving messages for an already-known,
    already-confirmed Teams chat: paginates automatically, filters
    system/event content, and restricts to the given time range, if any.

    Args:
      max_messages: Safety ceiling on how many messages to retrieve
        across all pages. Defaults to a safe standard limit -- leave unset
        unless you have a specific reason to change it.
      from_datetime: Optional ISO-8601 UTC lower boundary (inclusive), if
        you computed one from a requested time range.
      to_datetime: Optional ISO-8601 UTC upper boundary (exclusive), if
        you computed one from a requested time range.
      tool_context: ADK-injected.

    Returns:
      The same shape `teams_get_messages` returns: on success, `chat_id`,
      `messages`, retrieval/coverage metadata; on failure, a dict with a
      single `error` key. May only ever be called once per request -- a
      further call returns a safe `error` without retrieving anything.
    """
    if _resolved_chat_retrieval_used.get():
        _logger.warning(
            "perf stage=resolved_chat_retrieval_rejected reason=duplicate_attempt"
        )
        return {
            "error": validation_error(
                "This request's Teams retrieval has already been completed."
            ).safe_error.to_dict()
        }

    authoritative_chat_id = _active_continuation_chat_id.get()
    if not authoritative_chat_id:
        # Should be structurally impossible (this tool only ever exists on
        # `_CONTINUATION_INCIDENT_MANAGER`, only ever run from inside
        # `execute_read_continuation`'s own bound scope below) -- fail
        # closed rather than assume/guess a destination.
        _logger.warning(
            "perf stage=resolved_chat_retrieval_rejected reason=missing_binding"
        )
        return {
            "error": validation_error(
                "No resolved Teams conversation is bound for this request."
            ).safe_error.to_dict()
        }

    _resolved_chat_retrieval_used.set(True)
    _logger.info("perf stage=resolved_chat_retrieval_start")
    result = teams_get_messages(
        authoritative_chat_id,
        max_messages=max_messages,
        from_datetime=from_datetime,
        to_datetime=to_datetime,
        tool_context=tool_context,
    )
    if isinstance(result, dict) and "error" in result:
        _logger.warning("perf stage=resolved_chat_retrieval_rejected reason=gateway_error")
    else:
        _logger.info("perf stage=resolved_chat_retrieval_complete")
    return result


_CONTINUATION_INCIDENT_MANAGER = incident_manager.model_copy(
    update={
        "tools": [
            get_resolved_chat_messages if tool is teams_get_messages else tool
            for tool in incident_manager.tools
            if tool is not teams_list_chats
        ]
    }
)
"""PRODUCTION-CORRECTNESS FIX (live incident): a `{resolved_chat_id?}`
prompt paragraph alone was NOT sufficient -- live logs showed `incident_
manager` calling `teams_list_chats` for a resumed continuation anyway
(`incident_manager model call -> teams.listChats -> incident_manager
model call -> teams.getMessages`), reopening exactly the destination-
discovery round trip this continuation already resolved. Investigation
found TWO compounding causes, both fixed here: (1) the forwarded id was
originally stored under a `temp:`-prefixed key, which `create_session`
silently drops entirely (see `_seeded_state`'s own "P1 LIVE-INCIDENT FIX"
docstring) -- so the value never even reached the child session, and (2)
even with that fixed, prompt compliance alone is not a structural
guarantee. Per instruction ("Do NOT solve this by adding stronger
wording only... destination discovery must be structurally impossible/
unnecessary"), (2) is fixed STRUCTURALLY, not by further prompt tuning:
a `.model_copy(update=
{"tools": [...]})` of the SAME `incident_manager` agent, with `teams_
list_chats` removed from its tool list -- everything else (name,
instruction, model, `input_schema`/`output_schema`,
`before_model_callback`/`after_model_callback`, `after_agent_callback`
-- verified identical/shared via direct inspection, not assumed) stays
exactly the same object. `Agent`/`LlmAgent` is a plain pydantic
`BaseModel` (`.model_fields` already relied on elsewhere in this
codebase), so `.model_copy` is standard, fully-supported pydantic API,
not an ADK-internal hack. `Agent.tools` holds the RAW tool callables
(verified: `incident_manager.tools` contains the bare `teams_list_chats`
function object itself, not a `FunctionTool` wrapper -- wrapping happens
later, per-invocation, from this list), so filtering by identity is
correct and stable.

With `teams_list_chats` absent from the function-calling schema this
Runner's model ever sees, there is no code path -- prompt-compliant or
not -- through which THIS invocation could ever call it: zero `teams.
listChats` gateway calls is a structural guarantee, not a hoped-for model
behavior.

URGENT R2 FIX (destination binding): `teams_get_messages` itself -- the
one whose function-calling schema includes a REQUIRED `chat_id: str`
parameter -- is ALSO absent from this reduced toolset now, replaced by
`get_resolved_chat_messages` (this module, above), whose schema has NO
`chat_id` parameter at all. The PREVIOUS design here still exposed
`chat_id` to the model (via a `teams_get_messages`-shaped wrapper that
only VALIDATED what the model supplied) -- live logs proved that
insufficient: the model's own retyped id did not always match the
authoritative one, and the wrapper's rejection meant the retrieval simply
never happened, with no way for the model to self-correct. Removing the
parameter from the schema ENTIRELY -- never merely validating it after
the fact -- makes "the model supplies the wrong destination" structurally
impossible, the same "remove the capability from the schema, don't just
police it" principle already applied to `teams_list_chats` above.
`incident_manager`'s `{resolved_chat_id?}` paragraph (prompts.py,
corrected alongside this fix) tells the model to call `get_resolved_chat_
messages` instead of step 4's normal `teams_get_messages` when this
paragraph is active -- passing only `from_datetime`/`to_datetime` (if
computed in step 1) and `max_messages` (rarely needed) -- so `requested_
time_range`/`question` fidelity is preserved exactly as `PendingReadIntent`
captured it, with no new date-parsing code and no duplicated retrieval
path. `get_resolved_chat_messages` internally calls the REAL, UNMODIFIED
`teams_get_messages` with the authoritative id it reads from `_active_
continuation_chat_id` (bound below) -- the exact same Power Automate
client, pagination, time filtering, HTML/system filtering, evidence-
validation (`KNOWN_MESSAGE_IDS_STATE_KEY`), and error handling every other
retrieval path already uses; nothing about that machinery is duplicated
or reimplemented.

Never applied to the LIVE, model-initiated `AgentTool` delegation path
(`team_manager/agent.py`'s own `incident_manager_tool = AgentTool(agent=
incident_manager)` still wraps the ORIGINAL, full-toolset agent, with the
real `teams_get_messages` and its own `chat_id` parameter unchanged) --
exact-match/ambiguous-discovery Teams reads still need `teams_list_chats`
for real chat-name resolution, and a chat id the model itself resolved
via `teams_list_chats` is legitimately something it must supply to
`teams_get_messages` on that path; this reduced variant exists ONLY for a
turn that already has an authoritative `selected_chat_id` bound
server-side and therefore never needs (or should be able) to supply one.
"""

_SYNTHESIS_ONLY_INCIDENT_MANAGER = incident_manager.model_copy(
    update={"tools": [], "instruction": INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION}
)
"""P4B FIX -- the second half of collapsing the resolved-continuation call
graph down to ONE Incident Manager model call: when the application has
ALREADY performed retrieval deterministically (see `_prefetch_evidence_
deterministically`/`execute_read_continuation` below -- only possible when
`continuation.requested_time_range` is `None`, so there is no natural-
language time expression the model would otherwise need to interpret),
there is nothing left for incident_manager to call a tool FOR: destination
is already bound (same as `_CONTINUATION_INCIDENT_MANAGER`), and the
evidence itself is already retrieved and handed to it directly in its own
first turn's content (`_ResolvedContinuationSynthesisRequest`, below).

Same `.model_copy` pattern already verified safe and used twice elsewhere
in this codebase for the identical reason (a reduced-capability variant of
an existing agent, sharing everything except what's overridden -- name,
model, schemas, all callbacks): `_CONTINUATION_INCIDENT_MANAGER` (above)
and `presentation_team_manager` (team_manager/agent.py, R1's own tools=[]
presentation-only fix). `tools=[]` here is what makes "the model reasons
about retrieval, or calls anything at all before synthesizing" structurally
impossible for this variant -- not merely discouraged by prompt wording,
the same principle already established by R1 and the URGENT R2 fix.

P4B.2 FIX: unlike `_CONTINUATION_INCIDENT_MANAGER`, this variant ALSO
overrides `instruction` -- `INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION`
(prompts.py) instead of the full generic `INCIDENT_MANAGER_INSTRUCTION`.
The generic instruction is built for a tool-calling agent that discovers
its own destination and interprets time ranges; none of that applies here
(tools=[], destination and time window already resolved before this call
ever happens), so the full ~9,900-token instruction was pure overhead on
every synthesis call. The synthesis instruction reuses the classification-
critical sections (RESPONSE STRUCTURE, MATERIALITY GATE, SEMANTIC
CLASSIFICATION, MESSAGE REFERENCES/CHRONOLOGY, COVERAGE WORDING,
consistency rules) verbatim from the generic instruction so classification
quality does not regress, while dropping tool-invocation/discovery/write/
approval content this agent structurally cannot reach.
`output_schema=IncidentManagerResponse` is still inherited unmodified, so
the model's one and only response is still schema-validated structured
output; Gemini's structured-output support does not require any tool to be
registered.
"""


class _PrefetchedEvidenceMessage(BaseModel):
    """P4B: the COMPACT, MODEL-FACING projection of one retrieved Teams
    message -- deliberately NOT the full `TeamsMessage` shape (see this
    module's own `_build_prefetched_evidence` docstring for exactly what
    is dropped and why). Field names deliberately match `TeamsMessage`'s
    own (`author`/`sent_at`/`text`) so incident_manager's EXISTING prompt
    instructions (MESSAGE REFERENCES, MESSAGE CHRONOLOGY, evidence citing)
    apply unchanged -- this is a narrower VIEW of the same data, never a
    differently-shaped one requiring new prompt rules.
    """

    message_id: str
    author: str
    sent_at: str
    text: str
    message_references: list[dict[str, Any]] = Field(default_factory=list)


class _ResolvedContinuationSynthesisRequest(BaseModel):
    """P4B: the first-and-only turn's content for `_SYNTHESIS_ONLY_
    INCIDENT_MANAGER` -- deliberately a SEPARATE shape from `Incident
    ManagerRequest` (never reused/extended), since this is architecturally
    a different kind of turn (no retrieval left to perform at all) rather
    than an optional variant of the normal request. `chat_topic`/`question`
    carry the exact same meaning/fidelity as the normal flow (`chat_topic`
    is the already-established `chat_title`, `question` is `build_read_
    resume_message`'s own output, unchanged); `prefetched_evidence`/
    `coverage` are the ONLY new data, and correspond exactly to what step
    3 ("matched") + step 4's tool result would otherwise have supplied via
    a tool call -- incident_manager's prompt (the new "PREFETCHED EVIDENCE"
    paragraph) tells it to treat this exactly as if it had just received
    that tool result, and go straight to step 5's synthesis.
    """

    chat_topic: str
    question: Optional[str] = None
    prefetched_evidence: list[_PrefetchedEvidenceMessage]
    coverage: dict[str, Any]


def _build_prefetched_evidence(retrieved_messages: list[Any]) -> list[_PrefetchedEvidenceMessage]:
    """P4B: the deterministic FULL-RECORD -> MODEL-FACING projection.

    AUDITED (instruction section 11/12) against the installed `TeamsMessage`
    schema (tools/teams/schemas.py) before deciding what to keep: the raw,
    internal record (unchanged, still fully retained -- see this function's
    own callers, which pass the FULL `TeamsMessage` objects to evidence
    validation/`SourceReference` construction separately, never only this
    projection) carries `id`/`author`/`text`/`sent_at`/`raw_content`/
    `content_type`/`message_references` per message. This view keeps only
    what incident_manager's OWN, UNCHANGED prompt instructions actually
    reason from:
      - `message_id`/`author`/`sent_at`/`text`: directly used throughout
        step 5, MESSAGE CHRONOLOGY, and evidence citing.
      - `message_references[].message_id`/`preview`/`sender_name`: used by
        the MESSAGE REFERENCES paragraph.
    Deliberately DROPPED: `raw_content` (the pre-cleaned HTML/original
    payload -- `text` is already the cleaned view the prompt tells the
    model to use; sending BOTH would duplicate the same message twice),
    `content_type` (transport metadata with no prompt rule that reads it),
    and `message_references[].sender_id` (an internal Teams user id no
    prompt rule reads -- only `sender_name` is used for prose). None of
    this is a retrieval-coverage reduction: every message that survived
    `teams_get_messages`'s own filtering is still projected here, in the
    same order -- see test coverage for the "no valid message silently
    dropped" guarantee.
    """
    projected: list[_PrefetchedEvidenceMessage] = []
    for retrieved in retrieved_messages:
        references = [
            {
                "message_id": ref.message_id,
                "preview": ref.preview,
                "sender_name": ref.sender_name,
            }
            for ref in getattr(retrieved, "message_references", []) or []
        ]
        projected.append(
            _PrefetchedEvidenceMessage(
                message_id=retrieved.id,
                author=retrieved.author,
                sent_at=retrieved.sent_at,
                text=retrieved.text,
                message_references=references,
            )
        )
    return projected


class _StateCapture:
    """P4B: the minimal duck-typed `ToolContext`-shaped object the REAL,
    UNMODIFIED `teams_get_messages` needs to run OUTSIDE of any ADK tool
    dispatch -- only `.state`, exactly what that function's own body reads
    (`tool_context.state.get(...)`/`tool_context.state[...] = ...`, see
    get_messages.py). Verified directly: `teams_get_messages` never calls
    any OTHER `ToolContext` method/attribute, so this is a complete,
    correct stand-in, not a partial/lucky one. Constructed with the
    continuation's own seeded state so `_record_known_message_ids`
    accumulates into the SAME dict `_seeded_state` below hands to the
    derived session's initial state -- one consistent state object, never
    copied or diverged.
    """

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


_SPECIALIST_RUN_CONFIG = RunConfig(tool_thread_pool_config=ToolThreadPoolConfig())
"""LATENCY PASS -- verified against the installed ADK 1.33.0 source
before enabling: `incident_manager`'s tool set (`teams_list_chats`/
`teams_get_messages`/`teams_get_members`/`get_current_time_context`/the
`teams_propose_*`/`teams_create_chat`/`teams_send_message` write tools)
are all plain synchronous `def` functions, wrapped by ADK as a
`FunctionTool`. `FunctionTool._invoke_callable` (google/adk/tools/
function_tool.py) calls a sync tool with a bare `return target(**args)`
-- directly on the calling coroutine, i.e. on the event loop thread,
BLOCKING it for the tool's entire duration (confirmed: `RunConfig`'s
default `tool_thread_pool_config=None` means "tools run in the main
event loop", straight from that field's own docstring) -- unless
`RunConfig.tool_thread_pool_config` is set, in which case `flows/
llm_flows/functions.py`'s own dispatch (`thread_pool_config =
invocation_context.run_config.tool_thread_pool_config; if
thread_pool_config is not None: ... _call_tool_in_thread_pool(...)`)
runs the tool via `loop.run_in_executor` instead -- a documented, fully
supported ADK mechanism (`ToolThreadPoolConfig`'s own extensive
docstring), not an unsupported/invented behavior. This directly
addresses a real, source-confirmed cost: every `teams.listChats`/
`teams.getMessages`/`teams.getMembers` HTTP call this specialist makes
would otherwise stall the SAME event loop this turn's own SSE stream
(and any other concurrent request in this process) depends on for the
entire round trip.

SCOPED ONLY to this module's own direct `incident_manager` Runner (the
deterministic continuation-execution path) -- NOT applied to team_
manager's own outer Runner (chat_service.py's `_build_runner`), because
`AgentTool.run_async` (the mechanism a live, model-initiated delegation
uses) never accepts or forwards a `run_config` of its own to its nested
Runner call (verified: its source contains no `run_config` reference at
all) -- so this setting could not reach that path even if set there, and
`_is_sync_tool` returns `False` for `AgentTool` itself (no `.func`
attribute), meaning enabling this on team_manager's OWN Runner would
instead route the ENTIRE `incident_manager` AgentTool delegation through
`_call_tool_in_thread_pool`'s "new event loop in a background thread"
branch -- a materially different, higher-risk change this pass has not
verified end-to-end and does not need: this module's own direct
invocation already bypasses AgentTool entirely, so its tools are
genuinely, individually sync and hit the SAFE, narrow "run this one
function in a thread pool executor" branch instead.

Context propagation already verified safe (an earlier milestone,
documented in `backend.api.turn_context`'s own module docstring):
`_call_tool_in_thread_pool` explicitly uses `contextvars.copy_context()`/
`ctx.run(...)`, which preserves `backend.api.turn_context`'s `run_id`
ContextVar (and this pass's own model-call-perf ContextVar usage, see
perf_timing.py) correctly across the executor-thread boundary.
"""


def _internal_session_id(parent_session_id: str, run_id: str) -> str:
    return f"{parent_session_id}::specialist::{run_id}"


def _seeded_state(parent_state: dict[str, Any], continuation: ResolvedReadContinuation) -> dict[str, Any]:
    """Mirrors `AgentTool.run_async`'s own established state-forwarding
    filter (`if not k.startswith('_adk')`) -- copies whatever contextual
    state the parent session currently holds (e.g. Case/Fault context,
    if this session is linked to one), then overlays the ONE value this
    continuation itself is authoritative for.

    P1 LIVE-INCIDENT FIX -- uses a PLAIN key (`resolved_chat_id`), never
    `temp:resolved_chat_id` (pass #2's original, broken choice): verified
    directly against the installed ADK 1.33.0 source (`google.adk.
    sessions._session_util.extract_state_delta`) that `create_session`'s
    own `state=` parameter SILENTLY DROPS every `temp:`-prefixed key
    entirely, for ALL THREE of its app/user/session buckets -- unlike
    `append_event`'s `state_delta` handling (`_apply_temp_state`), which
    applies a `temp:` value to the in-memory session before trimming it
    from what gets persisted. `create_session` has no such "apply, then
    trim" step -- a `temp:`-prefixed key passed to it is never stored
    ANYWHERE, not even in memory. This means the ORIGINAL `temp:resolved_
    chat_id` never actually reached the child session's state at all --
    `incident_manager`'s own `{temp:resolved_chat_id?}` prompt placeholder
    always rendered empty, so its short-circuit paragraph never had a
    chance to fire, and it always fell through to step 2 (`teams_list_
    chats`) -- this is the CONCRETE root cause of the live "incident_
    manager model call -> teams.listChats -> ..." incident, not merely
    "insufficient prompt compliance".

    A PLAIN key is safe here specifically because THIS session is always
    a throwaway, deleted in this module's own `finally` immediately after
    use (see `execute_read_continuation`) -- there is no "must never
    durably persist between turns" concern for a session that is deleted
    before this same turn even finishes, unlike a value written into the
    PARENT (user-facing) session's own durable state.
    """
    seeded = {k: v for k, v in parent_state.items() if not k.startswith("_adk")}
    seeded["resolved_chat_id"] = continuation.selected_chat_id
    return seeded


def _validated_response(response_text: str) -> Optional[dict[str, Any]]:
    """Mirrors `AgentTool.run_async`'s own `validate_schema` call for a
    plain `BaseModel` `output_schema` -- see this function's own prior
    docstring in earlier revisions of this module for the source
    verification. Returns `None` for anything that fails to parse/
    validate -- a safe failure, never raised past this point.
    """
    try:
        return IncidentManagerResponse.model_validate_json(response_text).model_dump(mode="json", exclude_none=True)
    except Exception:
        _logger.warning("read_continuation_execution: incident_manager response failed schema validation")
        return None


_RETRIEVAL_NOT_VERIFIED_DETAIL = (
    "The assistant could not verify Teams retrieval for this request. Please try again."
)

_OUTCOMES_REQUIRING_VERIFIED_RETRIEVAL = frozenset(
    {IncidentManagerOutcome.OK.value, IncidentManagerOutcome.NO_RESULT.value}
)


def _fail_closed_result() -> dict[str, Any]:
    """R2.4: the safe result substituted whenever `_authoritative_
    retrieval_verified` rejects what the model claimed -- never `None`
    (which callers -- `chat_service.py` -- already treat as a generic,
    equally safe run failure), an explicit `error` outcome instead, so the
    rejection is visibly distinguishable in logs/tests from an unrelated
    schema-validation failure. Every other field left unset, matching
    `IncidentManagerResponse`'s own documented invariant ("when outcome is
    anything other than 'ok', summary/evidence/... are left unset/empty").
    """
    return IncidentManagerResponse(
        outcome=IncidentManagerOutcome.ERROR, detail=_RETRIEVAL_NOT_VERIFIED_DETAIL
    ).model_dump(mode="json", exclude_none=True)


def _authoritative_retrieval_verified(outcome: Any, session_state: Any) -> bool:
    """R2 FIX: the deterministic close for the hole this pass's own
    investigation found -- `strip_unverified_evidence` (evidence.py)
    strips FAKE evidence *entries*, but never downgrades `outcome` itself,
    so a model that never calls `teams_get_messages` at all could still
    return `outcome="ok"` with a fabricated `summary` and have it survive
    evidence stripping down to an (unnoticed) empty `evidence` list.

    `teams_get_messages` (get_messages.py) writes `KNOWN_MESSAGE_IDS_STATE_
    KEY` into session state UNCONDITIONALLY whenever it actually runs and
    reaches the point of recording results -- including a legitimate
    zero-message chat, which still sets the key to `[]` (verified directly
    against that function's own body: `_record_known_message_ids` runs for
    an empty `messages` list too, and only a genuine gateway failure -- an
    early `return {"error": ...}` -- skips it entirely). So checking
    whether the key is PRESENT at all (`in`, never truthiness) is exactly
    "did a real, successful retrieval attempt happen this run" -- reusing
    existing machinery (evidence.py's own established mechanism) rather
    than adding a second, competing bookkeeping path.

    Only outcomes whose truth actually DEPENDS on retrieval having
    happened are checked -- "ok"/"no_result" (see `IncidentManagerOutcome`'s
    own docstrings: both mean a chat was actually queried). "error" needs
    no such proof (it may legitimately be reported without ever reaching
    retrieval, e.g. an invalid time range); "ambiguous"/"not_found"/
    "selection_needed" are structurally unreachable on this path already
    (`_CONTINUATION_INCIDENT_MANAGER` has no `teams_list_chats` at all --
    see this module's own "PRODUCTION-CORRECTNESS FIX" docstring above)
    and are treated as verified here for the same reason: nothing about
    them depends on `teams_get_messages` having run.
    """
    if outcome not in _OUTCOMES_REQUIRING_VERIFIED_RETRIEVAL:
        return True
    try:
        return KNOWN_MESSAGE_IDS_STATE_KEY in session_state
    except TypeError:
        return False


def _apply_authoritative_destination(result: dict[str, Any], continuation: ResolvedReadContinuation) -> dict[str, Any]:
    """P4B: "the model must never decide the destination again" (the
    URGENT R2 fix's own principle) applied one step further -- even in
    plain TEXT output, with no tool argument involved, a model could in
    principle mistype/paraphrase `chat_id`/`chat_title` in its own JSON
    response. Since the destination is ALREADY 100% known from `continuation`
    for every outcome that carries one ("ok"/"no_result"/"error" -- see
    incident_manager's own step 3/4 instructions, which already say to use
    the step-3-established `chat_id`/`chat_title` for exactly these
    outcomes), this deterministically overrides whatever the model produced
    with the authoritative values -- never trusts, never merely validates.
    A no-op for any outcome that never carries a destination in the first
    place (structurally unreachable here anyway -- see `_authoritative_
    retrieval_verified`'s own docstring on why "ambiguous"/"not_found"/
    "selection_needed" cannot occur on this path).
    """
    if result.get("outcome") in ("ok", "no_result", "error"):
        result["chat_id"] = continuation.selected_chat_id
        result["chat_title"] = continuation.selected_chat_topic
    return result


async def _run_specialist_and_collect(
    *,
    session_service: BaseSessionService,
    user_id: str,
    internal_session_id: str,
    agent: Any,
    content: types.Content,
) -> tuple[Optional[types.Content], dict[str, Any]]:
    """Shared Runner-driving loop for both continuation-execution shapes
    below -- construction, iteration, and the post-run fresh-session
    re-fetch (P0's own "reload the canonical session" discipline) are
    identical regardless of which agent variant or request shape is used;
    only session creation/seeding and cleanup differ per caller.
    """
    runner = Runner(
        app_name=_INTERNAL_SPECIALIST_APP_NAME,
        agent=agent,
        session_service=session_service,
        memory_service=InMemoryMemoryService(),
    )
    last_content: Optional[types.Content] = None
    try:
        async for event in runner.run_async(
            user_id=user_id,
            session_id=internal_session_id,
            new_message=content,
            run_config=_SPECIALIST_RUN_CONFIG,
        ):
            if event.content:
                last_content = event.content
        refreshed_session = await session_service.get_session(
            app_name=_INTERNAL_SPECIALIST_APP_NAME, user_id=user_id, session_id=internal_session_id
        )
        final_session_state = refreshed_session.state if refreshed_session is not None else {}
    finally:
        await runner.close()
    return last_content, final_session_state


def _finalize(
    last_content: Optional[types.Content], final_session_state: dict[str, Any], continuation: ResolvedReadContinuation
) -> Optional[dict[str, Any]]:
    """Shared post-processing for both continuation-execution shapes:
    schema validation, R2's own fail-closed retrieval verification
    (unchanged mechanism -- see `_authoritative_retrieval_verified`'s own
    docstring), and the P4B destination override above. Identical for
    both shapes because both ultimately produce the SAME `IncidentManager
    Response`-shaped text output, validated and verified the SAME way
    regardless of how retrieval happened.
    """
    if last_content is None or last_content.parts is None:
        return None
    merged_text = "\n".join(p.text for p in last_content.parts if p.text and not p.thought)
    if not merged_text.strip():
        return None
    validated = _validated_response(merged_text)
    if validated is None:
        return None

    if not _authoritative_retrieval_verified(validated.get("outcome"), final_session_state):
        _logger.warning(
            "read_continuation_execution: rejecting unverified retrieval result outcome=%s",
            validated.get("outcome"),
        )
        return _fail_closed_result()

    return _apply_authoritative_destination(validated, continuation)


async def _execute_via_model_driven_retrieval(
    *,
    session_service: BaseSessionService,
    user_id: str,
    internal_session_id: str,
    parent_state: dict[str, Any],
    continuation: ResolvedReadContinuation,
) -> Optional[dict[str, Any]]:
    """UNCHANGED FROM THE PRIOR PASS (URGENT R2 fix), just extracted into
    its own function: incident_manager itself calls `get_resolved_chat_
    messages` (no `chat_id` parameter -- see this module's own docstring)
    via its own reasoning. Used ONLY when `continuation.requested_time_
    range` is present -- interpreting an arbitrary natural-language time
    expression into UTC boundaries genuinely requires model reasoning (see
    `execute_read_continuation`'s own docstring, "WHY THE TIME-RANGE
    BRANCH STAYS MODEL-DRIVEN"), so this path intentionally still costs a
    `get_current_time_context` round trip (or two) before synthesis.
    """
    request = IncidentManagerRequest(
        chat_topic=continuation.selected_chat_topic,
        question=build_read_resume_message(
            PendingReadIntent(operation=continuation.operation, question=continuation.question)
        ),
        requested_time_range=continuation.requested_time_range,
    )
    content = types.Content(
        role="user", parts=[types.Part.from_text(text=request.model_dump_json(exclude_none=True))]
    )

    await session_service.create_session(
        app_name=_INTERNAL_SPECIALIST_APP_NAME,
        user_id=user_id,
        session_id=internal_session_id,
        state=_seeded_state(parent_state, continuation),
    )

    chat_id_token = _active_continuation_chat_id.set(continuation.selected_chat_id)
    retrieval_used_token = _resolved_chat_retrieval_used.set(False)
    try:
        last_content, final_session_state = await _run_specialist_and_collect(
            session_service=session_service,
            user_id=user_id,
            internal_session_id=internal_session_id,
            agent=_CONTINUATION_INCIDENT_MANAGER,
            content=content,
        )
    finally:
        _active_continuation_chat_id.reset(chat_id_token)
        _resolved_chat_retrieval_used.reset(retrieval_used_token)

    return _finalize(last_content, final_session_state, continuation)


async def _execute_via_deterministic_retrieval(
    *,
    session_service: BaseSessionService,
    user_id: str,
    internal_session_id: str,
    parent_state: dict[str, Any],
    continuation: ResolvedReadContinuation,
) -> Optional[dict[str, Any]]:
    """P4B: the application performs retrieval itself, in plain Python,
    BEFORE incident_manager's model ever runs -- used whenever `continuation
    .requested_time_range` is `None` (no natural-language time expression
    to interpret, so there is genuinely nothing left for the model to
    decide about retrieval at all -- see `execute_read_continuation`'s own
    docstring). Calls the REAL, UNMODIFIED `teams_get_messages` directly
    (never a second retrieval implementation): same Power Automate client,
    pagination, time/HTML/system filtering, `KNOWN_MESSAGE_IDS_STATE_KEY`
    bookkeeping, and snippet-mailbox forwarding as every other path.

    On a retrieval failure, returns a safe `error` result WITHOUT ever
    creating a derived session or invoking any model at all -- there is
    nothing incident_manager could usefully do with a failed retrieval it
    never attempted itself, and skipping the Runner entirely here also
    means a pure retrieval failure now costs zero model calls, not one.
    """
    state_capture = _StateCapture(dict(_seeded_state(parent_state, continuation)))
    _perf_logger.info("perf stage=teams_evidence_prepared")
    retrieval = teams_get_messages(continuation.selected_chat_id, tool_context=state_capture)

    if isinstance(retrieval, dict) and "error" in retrieval:
        detail = None
        error_payload = retrieval.get("error")
        if isinstance(error_payload, dict):
            detail = error_payload.get("userMessage")
        result = IncidentManagerResponse(
            outcome=IncidentManagerOutcome.ERROR, detail=detail
        ).model_dump(mode="json", exclude_none=True)
        return _apply_authoritative_destination(result, continuation)

    retrieved_raw = retrieval.get("messages", [])
    from backend.tools.teams.schemas import TeamsMessage

    prefetched = _build_prefetched_evidence([TeamsMessage.model_validate(entry) for entry in retrieved_raw])
    synthesis_request = _ResolvedContinuationSynthesisRequest(
        chat_topic=continuation.selected_chat_topic,
        question=build_read_resume_message(
            PendingReadIntent(operation=continuation.operation, question=continuation.question)
        ),
        prefetched_evidence=prefetched,
        coverage=retrieval.get("coverage", {}),
    )
    content = types.Content(
        role="user", parts=[types.Part.from_text(text=synthesis_request.model_dump_json(exclude_none=True))]
    )

    await session_service.create_session(
        app_name=_INTERNAL_SPECIALIST_APP_NAME,
        user_id=user_id,
        session_id=internal_session_id,
        # Seeds `KNOWN_MESSAGE_IDS_STATE_KEY` (already populated by the
        # `teams_get_messages` call above, into the SAME `state_capture
        # .state` dict) directly into this session's INITIAL state -- R2's
        # own `_authoritative_retrieval_verified` re-fetches this session
        # afterward and finds the key already present, exactly as if a
        # tool call had written it mid-run. Same verification mechanism,
        # unchanged, fed by a different (deterministic, pre-Runner) write.
        state=state_capture.state,
    )

    _perf_logger.info("perf stage=incident_synthesis_start")
    last_content, final_session_state = await _run_specialist_and_collect(
        session_service=session_service,
        user_id=user_id,
        internal_session_id=internal_session_id,
        agent=_SYNTHESIS_ONLY_INCIDENT_MANAGER,
        content=content,
    )
    _perf_logger.info("perf stage=incident_synthesis_complete")

    return _finalize(last_content, final_session_state, continuation)


async def execute_read_continuation(
    *,
    session_service: BaseSessionService,
    user_id: str,
    parent_session_id: str,
    run_id: str,
    parent_state: dict[str, Any],
    continuation: ResolvedReadContinuation,
) -> Optional[dict[str, Any]]:
    """Runs `incident_manager`'s read path directly and deterministically
    for `continuation`, using the CALLER's own canonical `session_service`
    -- see this module's own docstring for the full rationale. Returns the
    validated `IncidentManagerResponse` dict on success, or `None` for a
    safe failure (a genuine runtime error propagates normally, exactly
    like any other `Runner.run_async` failure -- the caller's
    responsibility to handle, unchanged from the prior pass).

    `question`/`requested_time_range`/`chat_topic` are built ONLY from
    `continuation`'s own structured fields -- never anything reconstructed
    from prose (unchanged from the prior pass).

    P4B -- WHY THE TIME-RANGE BRANCH STAYS MODEL-DRIVEN: `requested_time_
    range` (`PendingReadIntent`'s own field) is captured VERBATIM, natural-
    language text (e.g. "the last 7 days", "since Monday") -- never
    normalized ISO-8601 (verified directly against `PendingReadIntent`'s
    own docstring/field type). Converting an arbitrary relative expression
    into UTC boundaries genuinely requires semantic interpretation (what is
    "now", what does "last week" mean relative to it) -- this codebase
    never does that with regex/keyword date parsing (instruction section 7
    explicitly forbids it), so when a time range is present, incident_
    manager's OWN existing reasoning (TIME RANGE INTERPRETATION, its
    prompt) still performs it via `get_current_time_context`, exactly as
    before this pass. When `requested_time_range` is `None` -- the common
    case for a plain resumed "summarize this chat" -- there is no such
    requirement at all, so retrieval moves entirely into the application
    layer and the model gets exactly one, synthesis-only call.
    """
    internal_session_id = _internal_session_id(parent_session_id, run_id)
    try:
        if continuation.requested_time_range is None:
            result = await _execute_via_deterministic_retrieval(
                session_service=session_service,
                user_id=user_id,
                internal_session_id=internal_session_id,
                parent_state=parent_state,
                continuation=continuation,
            )
        else:
            result = await _execute_via_model_driven_retrieval(
                session_service=session_service,
                user_id=user_id,
                internal_session_id=internal_session_id,
                parent_state=parent_state,
                continuation=continuation,
            )
    finally:
        # P4B: a pure retrieval-failure early return in `_execute_via_
        # deterministic_retrieval` never creates this session at all (see
        # that function's own docstring) -- checking existence first
        # avoids a spurious "failed to delete" warning for a session that
        # legitimately never existed, while still safely deleting one that
        # does (the normal case for every other path/outcome, unchanged).
        try:
            existing = await session_service.get_session(
                app_name=_INTERNAL_SPECIALIST_APP_NAME, user_id=user_id, session_id=internal_session_id
            )
            if existing is not None:
                await session_service.delete_session(
                    app_name=_INTERNAL_SPECIALIST_APP_NAME, user_id=user_id, session_id=internal_session_id
                )
        except Exception:
            _logger.warning(
                "read_continuation_execution: failed to delete internal specialist session %s",
                internal_session_id,
            )

    return result


async def cleanup_orphaned_internal_specialist_sessions(
    session_service: BaseSessionService,
    *,
    max_age_seconds: float = 3600.0,
    now: Optional[float] = None,
) -> int:
    """Bounded maintenance helper for the lower-severity counterpart of
    this module's hard-crash concern: a process death between `create_
    session` and this module's own `finally` (above) can leave an orphan
    internal specialist session behind. A NORMAL run always deletes its
    own derived session immediately regardless of outcome -- a real Teams
    read completes in seconds, never anywhere close to `max_age_seconds`
    -- so anything this helper ever finds is either still genuinely
    in-flight (nowhere near the age threshold) or an orphan from a crash.

    NOT WIRED INTO ANY SCHEDULER/BACKGROUND TASK/CRON IN THIS CODEBASE
    (deliberately -- instruction: "Do NOT build ... a scheduler ... unless
    absolutely required, which is not expected"). This is a plain,
    stand-alone async function an operator-triggered maintenance path
    (a manual admin endpoint, a deploy-time job, an external cron
    invoking a small script that calls this) can call later; nothing here
    assumes or requires one to exist yet.

    SAFETY, VERIFIED AGAINST THE INSTALLED ADK 1.33.0 SOURCE:
      - Only ever calls `list_sessions`/`delete_session` with `app_name=
        _INTERNAL_SPECIALIST_APP_NAME` -- the dedicated namespace this
        module already uses for every derived session it creates,
        structurally distinct from the user-facing `APP_NAME` (see this
        module's own "APP NAME NAMESPACING" docstring). A normal user
        session lives under a DIFFERENT `app_name` entirely and can never
        be listed or reached by this function, regardless of `user_id`.
      - Staleness is decided from `Session.last_update_time` (a real
        `float` field `BaseSessionService`'s own `Session` model
        maintains authoritatively -- verified via
        `google.adk.sessions.session.Session`'s own field declaration),
        never by parsing/guessing from the session-id string (instruction
        section 13's explicit prohibition).
      - `list_sessions(app_name=...)` with no `user_id` lists across all
        users under that one namespace -- appropriate here specifically
        because that namespace never holds anything but this module's own
        internal, non-user-facing sessions.
      - A single deletion failure is logged and skipped, never raised --
        one bad/racy entry must not abort the rest of a cleanup batch.

    Returns the number of sessions actually deleted.
    """
    response = await session_service.list_sessions(app_name=_INTERNAL_SPECIALIST_APP_NAME)
    current_time = now if now is not None else time.time()
    deleted = 0
    for candidate in response.sessions:
        age_seconds = current_time - candidate.last_update_time
        if age_seconds < max_age_seconds:
            continue
        try:
            await session_service.delete_session(
                app_name=_INTERNAL_SPECIALIST_APP_NAME, user_id=candidate.user_id, session_id=candidate.id
            )
            deleted += 1
        except Exception:
            _logger.warning(
                "cleanup_orphaned_internal_specialist_sessions: failed to delete a candidate orphan session"
            )
    return deleted
