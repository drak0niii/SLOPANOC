"""P4B.3 -- fast path for first-time direct/exact Teams reads.

PROBLEM THIS SOLVES: P4B.1/P4B.2 collapsed the POST-SELECTION resolved-
continuation call graph to one deterministic `teams_get_messages` call plus
one synthesis-only `incident_manager` model call (see read_continuation_
execution.py -- frozen, unmodified by this pass). But a FIRST-TIME direct/
exact Teams request (no prior `SelectionCard`) still goes through the full,
generic `incident_manager` AgentTool: model call -> `teams_list_chats` ->
model call -> `teams_get_messages` -> model call (generic synthesis) --
three full-instruction (~9,900-token) model turns, live-measured at
prompt_tokens=30,552 for the final synthesis call alone.

WHY THIS CANNOT BE FIXED BY EDITING `read_continuation_execution.py` (that
module stays frozen, per this pass's own "do not reopen" instruction): the
optimization here is about intercepting the LIVE `incident_manager`
AgentTool invocation mid-flight, the moment `teams_list_chats` resolves to
exactly one chat for a READ -- a concern specific to the generic
delegation path, not the post-selection continuation path. This module is
the thin, separate bridge between the two: it detects the "unique match,
read request" moment inside `incident_manager`'s OWN nested Runner turn,
then calls read_continuation_execution.py's own PUBLIC, UNCHANGED
`execute_read_continuation` -- the SAME function the post-selection path
uses -- to actually perform the read. There is only ONE authoritative
Teams-read implementation; this module never duplicates it.

MECHANISM, VERIFIED AGAINST THE INSTALLED ADK 1.33.0 SOURCE (never guessed):

1. `_capture_unique_match_for_fast_path` is an `after_tool_callback`
   scoped to `teams_list_chats` calls made BY `incident_manager` itself.
   It never invents a new match-detection algorithm (section 4's own
   instruction) -- it only reads back `teams_list_chats`'s own,
   unmodified, already-deterministic result (`chat_resolution.py` via
   `list_chats.py`'s `_match`, both untouched by this pass).

   READ vs WRITE, WITHOUT ANY NEW SIGNAL OR TEXT INSPECTION: a
   `teams_list_chats` call carries `pending_write_message` ONLY when
   `incident_manager`'s own (unchanged) generic instruction is resolving
   the destination of a `teams.sendMessage` proposal -- verified directly
   in that instruction's own step 2 text ("leave `pending_write_message`
   unset for anything that is not a sendMessage write, including every
   read/summarize request"). This is an EXISTING, already-tested
   structural signal `incident_manager`'s own model already populates for
   an unrelated reason (ambiguity-resume) -- reusing it here to gate the
   fast path is not new NL/keyword routing, it is reading back a value
   the model already computes under an existing, unchanged prompt
   contract. `teams_propose_create_chat`/`teams_create_chat`/
   `teams_send_message` never call `teams_list_chats` at all, so they are
   structurally unreachable here regardless.

   On a "matched" result for what is provably a read, this callback
   writes a small `temp:`-prefixed marker (never persisted past this
   turn -- same convention as every other `temp:` key in this codebase)
   into `tool_context.state`, carrying only the matched chat id/title and
   the SAME `pending_question`/`pending_time_range`/`pending_operation`
   values `list_chats.py` already captures for its own ambiguity-resume
   mechanism (reusing `_safe_pending_question`/`_safe_read_operation`
   directly -- no new parsing). It always returns `None`: exactly like
   every other `after_tool_callback` in this codebase, it is a side
   effect only, never a substitute for what the model itself sees as
   `teams_list_chats`'s response (the model's own next turn still
   normally sees the real "matched" result -- unless the callback below
   short-circuits that turn entirely).

2. `_fast_path_before_model_callback` is a `before_model_callback` that
   runs BEFORE every one of `incident_manager`'s own model-call attempts
   (verified against `flows/llm_flows/base_llm_flow.py`'s
   `_call_llm_async`: `if response := await self._handle_before_model_
   callback(...): yield response; return` -- a non-`None` return value
   from a `before_model_callback` skips the real Gemini call entirely and
   is used as that turn's own response). On every turn EXCEPT the one
   immediately following a "matched read" `teams_list_chats` result, the
   marker from step 1 is absent, so this returns `None` and the real
   model call proceeds completely unchanged -- covering every other
   turn (first turn, writes, ambiguous/not_found reads) with zero
   behavior change.

   When the marker IS present, this callback -- instead of letting
   `incident_manager`'s OWN model spend another full-generic-instruction
   turn deciding to call `teams_get_messages` itself, then a THIRD turn
   synthesizing -- builds a `ResolvedReadContinuation` from the marker's
   already-resolved chat id/topic and the SAME `pending_question`/
   `pending_time_range`/`pending_operation` values, then calls read_
   continuation_execution.py's own `execute_read_continuation` (the
   IDENTICAL function the post-selection path already calls) directly.
   That function's own existing dispatch (unchanged) decides deterministic
   vs model-driven-time-range retrieval; either way, retrieval is exactly
   one `teams_get_messages`, synthesis is exactly one call to
   `_SYNTHESIS_ONLY_INCIDENT_MANAGER` (or, when a time range needs
   interpretation, `_CONTINUATION_INCIDENT_MANAGER` -- both already
   frozen, already tested, never duplicated here).

   The validated `IncidentManagerResponse`-shaped result is then wrapped
   in a plain, complete, non-streaming `LlmResponse` (a normal final text
   response, no function call) and returned -- `is_final_response()`
   (google.adk.events.event.py) is satisfied by this shape alone (no
   function calls/responses, not partial), so `incident_manager`'s own
   nested Runner stops here, and `AgentTool.run_async`'s own extraction
   (`tools/agent_tool.py`: joins the last event's text parts, then
   `validate_schema(output_schema, merged_text)`) validates it exactly as
   it would a real model response -- team_manager sees an ordinary
   `IncidentManagerResponse` with `outcome="ok"`/`"no_result"`/`"error"`,
   through the SAME, completely UNCHANGED `TEAM_MANAGER_INSTRUCTION` path
   it already uses today. No change to team_manager's own instruction,
   schema, or tool surface was needed for this pass.

   MUST RUN BEFORE P2's OWN `before_model_call` IN THE CALLBACK LIST:
   ADK invokes `canonical_before_model_callbacks` in order, and the FIRST
   one to return non-`None` short-circuits the rest (same "first non-None
   wins" contract already relied on for `before_tool_callback` --
   verified against the same source file). When this callback's shortcut
   fires, ZERO real Gemini calls happen for that turn -- if P2's own
   `before_model_call` ran first, it would log an unpaired `model_call_
   start` with no matching `model_call_end` (P2 is frozen; this must
   never happen). Ordering this callback FIRST in `_fast_path_incident_
   manager`'s `before_model_callback` list (see below) guarantees P2 never
   sees a turn that didn't actually happen.

3. `_fast_path_incident_manager` is a `.model_copy` of the shared
   `incident_manager` base agent -- the same established pattern already
   used for `_CONTINUATION_INCIDENT_MANAGER`/`_SYNTHESIS_ONLY_INCIDENT_
   MANAGER` (read_continuation_execution.py) and `presentation_team_
   manager` (agent.py, R1) -- adding ONLY the new `after_tool_callback`
   and prepending the new `before_model_callback`. The base `incident_
   manager` object (still used directly for local `adk run`/`adk web`
   debugging per its own module docstring) is completely untouched. This
   variant is wired into `AgentTool(agent=...)` in team_manager/agent.py
   in place of the base agent -- team_manager's own tool-calling schema
   is byte-for-byte identical (same name, same `IncidentManagerRequest`/
   `IncidentManagerResponse`), so `TEAM_MANAGER_INSTRUCTION` needed no
   changes at all.

WHAT THIS DELIBERATELY DOES NOT TOUCH: `chat_resolution.py` (matching
algorithm, untouched -- section 4), `list_chats.py` (untouched --
reused as-is), `read_continuation_execution.py` (frozen -- section 3/24),
`TEAM_MANAGER_INSTRUCTION`/`incident_manager_tool`'s schema (unchanged --
only the AgentTool's `agent=` target changes in agent.py), ambiguity/
not-found handling (the marker is never set for those outcomes, so
`incident_manager`'s own model reasons about them exactly as it always
has), and write actions (the marker is never set when `pending_write_
message` is set, so `incident_manager`'s own model keeps its normal
tools/instruction for that entire turn).

============================================================================
P4B.3 COMPLETION PASS -- CONVERGING INTO THE SAME TRUSTED-RESULT PRESENTATION
============================================================================

THE DEFECT THIS SECTION FIXES: everything above ends with `_fast_path_
before_model_callback` returning a normal `IncidentManagerResponse`-shaped
`LlmResponse` THROUGH the AgentTool boundary -- which reaches team_manager
as an ordinary tool response, so team_manager's own NEXT turn still uses
the FULL, heavy `TEAM_MANAGER_INSTRUCTION` (with `incident_manager_tool`/
`record_conversation_target` still structurally available) to present it,
instead of the cheap, structurally tools=[] `presentation_team_manager` +
`TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION` post-selection already uses.

WHY THE OBVIOUS FIX (persist `PENDING_SPECIALIST_RESULT_STATE_KEY` into the
REAL session, let team_manager's own next turn naturally re-render its
INSTRUCTION) IS NOT SAFE: `team_manager_instruction_provider` (case_
context.py) is state-driven and agent-object-agnostic -- it renders `TEAM_
MANAGER_TRUSTED_RESULT_INSTRUCTION` for BOTH `team_manager` and
`presentation_team_manager` once `PENDING_SPECIALIST_RESULT_STATE_KEY` is
present. But the SAFE ENFORCEMENT R1 depends on is not the instruction
text -- it is `presentation_team_manager`'s STRUCTURAL `tools=[]` (R1's own
docstring: prompt wording alone was proven, live, insufficient -- Gemini
called `incident_manager` again anyway when the tool was still present).
The FULL `team_manager` agent's own next turn still has `incident_manager_
tool`/`record_conversation_target` in its function-calling schema
regardless of which instruction text renders -- reusing it here would
silently reopen the exact R1 vulnerability this codebase already fixed
once. So the fix must ALSO get a genuinely `tools=[]` turn, not merely the
right instruction text.

WHY A LIVE SESSION MUTATION MID-RUNNER-EXECUTION IS ALSO AVOIDED: verified
empirically (not guessed) that state written via `callback_context.state
[...] = value` inside a SHORT-CIRCUITED `before_model_callback` does NOT
propagate back out through `AgentTool.run_async`'s own `state_delta`
forwarding the way an `after_tool_callback`'s mutation reliably does
(`sync_incident_manager_result_to_state`'s own, already-proven pattern) --
confirmed with a standalone probe script reproducing this exact nested-
AgentTool shape before relying on either mechanism. Rather than mutating
the REAL, still-running canonical session from deep inside a nested
AgentTool call (racy, unverified, and would need its OWN cleanup path
mirroring chat_service.py's `finally` block), this design keeps every
piece of `TrustedSpecialistResult` state entirely OUT of the real session
and confined to a throwaway ephemeral one used only to generate the
presentation text (see `_run_trusted_presentation` below) -- the real
session ends up with exactly ONE final event, authored by `team_manager`,
containing that generated text -- never a second, duplicate "presentation_
team_manager"-authored event polluting history.

============================================================================
CORRECTION PASS -- THE `ContextVar` BRIDGE ABOVE NEVER ACTUALLY WORKED
============================================================================

ROOT CAUSE, VERIFIED WITH A MINIMAL, ISOLATED REPRO (not guessed): ADK's own
`flows/llm_flows/functions.py`, `handle_function_call_list_async`, runs
EVERY tool call -- including the `incident_manager` AgentTool call this
whole mechanism lives inside -- via `asyncio.create_task(...)`
(`tasks = [asyncio.create_task(_execute_single_function_call_async(...))
for function_call in filtered_calls]`, then `await asyncio.gather(*tasks)`).
`asyncio.create_task` COPIES the current `contextvars.Context` at task-
creation time; a `ContextVar.set(...)` performed INSIDE that child task
never propagates back to the PARENT task's context -- this is documented,
fundamental Python `contextvars` behavior, confirmed here with a 15-line
standalone script reproducing the exact shape (`asyncio.create_task(child)`
where `child` calls `.set()`; the parent's own `.get()` afterward still
shows the ORIGINAL default). `_pending_trusted_result.set(result)`
(previously here, inside `_fast_path_before_model_callback`, itself running
inside the AgentTool's own child task) could therefore NEVER be visible to
team_manager's own before_model_callback (`_present_fast_path_result_via_
trusted_pipeline`, running back in the PARENT task, after `asyncio.gather`
returns the function response) -- `.get()` there always returned the
default (`None`). `current_run_id()` was (wrongly) cited as precedent for
"ContextVars propagate here" -- it does, but only because it is SET ONCE,
at the very top of chat_service.py, BEFORE any task-splitting occurs, and
only ever READ afterward: a value already in a context IS copied forward
into every child task, so reads of it succeed everywhere; the failure mode
is specific to a WRITE performed *inside* a child task trying to flow back
OUT to its parent, which this module's fast-path/presentation bridge (a
write inside the AgentTool's child task, read back by the parent) actually
needed and did not have.

PRACTICAL EFFECT: `_present_fast_path_result_via_trusted_pipeline` always
saw `None` and always returned `None` -- team_manager's own real next turn
ALWAYS ran normally (full `TEAM_MANAGER_INSTRUCTION`, `incident_manager_
tool` still present), for every direct-unique read, success or failure
alike. `presentation_team_manager` was never actually reached by this path
in production. A prior end-to-end test appeared to prove otherwise only
because its shared fake `BaseLlm` was scripted by CALL COUNT alone (not by
which agent/instruction was actually being served) -- it could not
distinguish "presentation_team_manager's turn" from "team_manager's own
normal fallback turn"; both would receive whatever canned response the
script's call counter pointed at. Reproduced directly (bypassing the fake):
invoking `_run_trusted_presentation` against the REAL `presentation_team_
manager` (real `.model`, no test credentials in this environment) raises
`ValueError: No API key was provided` -- which the earlier, passing test
never actually triggered, proving it never reached that code path at all.

FIX: `_pending_trusted_result` (a `ContextVar`) is replaced by `_pending_
trusted_result_by_run`, a plain, run-id-keyed, module-level dict. `run_id`
itself IS reliably available everywhere in this call chain (it is read-only
here, and read-only values already bound before a task is created DO
propagate into it -- the exact asymmetry above) via `current_run_id()`,
bound once at the very top of chat_service.py's own turn orchestration and
therefore unique per turn -- so keying by it, rather than relying on
context-local storage, sidesteps the task-boundary problem entirely: a
plain dict write from ANY task is visible to a plain dict read from ANY
other task in the same process (ordinary shared mutable state, no
`contextvars` semantics involved). Single-use: `_present_fast_path_result_
via_trusted_pipeline` `.pop()`s its own `run_id`'s entry, clearing it
immediately -- never left behind for a later turn or a different run to
find (see `discard_pending_trusted_result`, called from chat_service.py's
own existing turn-end `finally` block, for the crash/cancellation-before-
consumption backstop -- section 22).

MECHANISM (CORRECTED):

4. `_fast_path_before_model_callback` (above) stashes the validated result
   into `_pending_trusted_result_by_run[run_id]` (never `tool_context.
   state`, proven not to propagate through the AgentTool boundary either --
   see the ORIGINAL rationale above, still correct for that specific
   claim).

5. `_present_fast_path_result_via_trusted_pipeline` is a `before_model_
   callback` on team_manager itself. On every turn EXCEPT the one
   immediately following a fast-pathed `incident_manager` call, `_pending_
   trusted_result_by_run` has no entry for this `run_id`, so this returns
   `None` and team_manager's real model call proceeds completely unchanged
   (covers the routing/delegation turn, every ambiguous/not-found/write
   turn, and every turn of every OTHER conversation -- `run_id` is unique
   per turn, so concurrent turns never see each other's entry).

   When an entry IS present: pops it (single-use) and builds+same-run-
   validates a `TrustedSpecialistResult` envelope via `build_and_validate_
   trusted_envelope` (read_continuation_presentation.py -- the IDENTICAL
   two functions, `build_trusted_specialist_result_envelope`/`validate_
   trusted_envelope_for_run`, chat_service.py's own post-selection branch
   already calls; extracted as one small shared helper, never a second
   envelope/validator).

   TRUST BOUNDARY IS NOW MEANINGFUL (correction pass, section 7-9 of the
   task that requested this fix): if validation fails, OR `_run_trusted_
   presentation` itself returns no content, this callback NEVER returns
   `None` (which would let team_manager's own real, full-instruction turn
   run and present the tool_response in normal prose -- exactly the trust-
   boundary bypass this correction pass exists to close). Instead it
   returns a plain, hardcoded, safe `LlmResponse`
   (`_TRUST_VALIDATION_FAILURE_TEXT`, the SAME generic wording chat_
   service.py's own top-level failure path already uses elsewhere in this
   codebase) -- short-circuiting team_manager's own turn with a safe,
   non-fabricated answer, no second model call needed merely to phrase it,
   no tool/model fallback of any kind.

   On success, `_run_trusted_presentation` (below) runs `presentation_
   team_manager` (tools=[], `TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION`) --
   the SAME agent object R1/post-selection already use, unmodified -- via
   a fresh, throwaway `Runner`/`InMemorySessionService`, seeded with a
   copy of the real session's own state (the same non-`_adk`-prefixed
   filter `AgentTool.run_async`'s own established code already applies)
   plus `PENDING_SPECIALIST_RESULT_STATE_KEY`, and the SAME `user_content`
   this turn started with. Its final response text becomes THIS callback's
   own synthesized `LlmResponse` -- team_manager's own turn ends with
   exactly that text, authored once, by `team_manager`, in the real
   session -- `presentation_team_manager`'s own nested run never touches
   the real session at all, so there is no duplicate history entry.

6. `get_fast_path_team_manager()` lazily builds and memoizes a `.model_
   copy` of the shared `team_manager` base agent -- prepending the new
   before_model_callback, same established pattern as `_fast_path_
   incident_manager` above (lazy, because agent.py imports FROM this
   module, so this module cannot import the base `team_manager` back at
   module-load time). `chat_service.py`'s `_build_runner` (the NORMAL,
   non-continuation Runner every first-time turn uses) is updated to call
   this instead of importing the base `team_manager` directly; `root_
   agent` (agent.py, local `adk run`/`adk web` debugging only) is
   untouched -- this mechanism only does anything meaningful inside chat_
   service.py's own `run_id`-bound orchestration.

P2 INSTRUMENTATION, VERIFIED (not redesigned): `presentation_team_manager`
inherits P2's `before_model_call("team_manager")`/`after_model_call(
"team_manager")` unmodified from the base `team_manager` (never overridden
by ANY `.model_copy` in this codebase -- `get_fast_path_team_manager()`'s
own variant is a SEPARATE object, irrelevant to `presentation_team_manager`
itself), and `current_run_id()` (which P2's own callbacks read) is a value
already bound before any task-splitting, so it correctly reaches the nested
ephemeral presentation Runner's own model call the same way it already
reaches `incident_manager`'s nested AgentTool call. All four real model
calls (team_manager routing, incident_manager discovery, incident_manager
synthesis-only, presentation_team_manager) are therefore ALREADY correctly
P2-instrumented with no change needed here.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from google.adk.memory import InMemoryMemoryService
from google.adk.models import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.agents.incident_manager.agent import incident_manager
from backend.agents.incident_manager.schemas import IncidentManagerOutcome, IncidentManagerResponse
from backend.agents.team_manager.read_continuation_execution import execute_read_continuation
from backend.agents.team_manager.read_continuation_presentation import (
    PENDING_SPECIALIST_RESULT_STATE_KEY,
    build_and_validate_trusted_envelope,
    content_has_nonblank_text,
)
from backend.api.perf_timing import before_model_call
from backend.api.turn_context import current_run_id
from backend.attachments.service import get_attachment_service
from backend.attachments.storage import get_attachment_storage
from backend.selection.schemas import ReadOperation, ResolvedReadContinuation
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY
from backend.tools.teams.list_chats import _safe_pending_question, _safe_read_operation

_logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("backend.perf")

_FAST_PATH_APP_NAME_SUFFIX = "::direct-fast-path-presentation"
"""Distinct app-name namespace for the throwaway presentation Runner below
-- same "never the user-facing app name" convention as read_continuation_
execution.py's own `_INTERNAL_SPECIALIST_APP_NAME`.
"""

_FAST_PATH_PENDING_STATE_KEY = "temp:_p4b3_fast_path_pending"
"""`temp:`-prefixed (never persisted past this turn -- same convention as
every other `temp:` key in this codebase, e.g. `_active_continuation_chat_
id`'s session-state counterparts elsewhere). Scoped to `incident_manager`'s
OWN nested AgentTool session (an ephemeral `InMemorySessionService` session
`AgentTool.run_async` creates per call -- verified against `tools/agent_
tool.py`), never the outer team_manager/chat session.
"""

_pending_trusted_result_by_run: dict[str, dict[str, Any]] = {}
"""Carries the fast path's own (not-yet-trusted) `IncidentManagerResponse`
dict from `incident_manager`'s nested AgentTool turn (set in `_fast_path_
before_model_callback`) up to team_manager's own next turn (`_present_
fast_path_result_via_trusted_pipeline`), keyed by `run_id`.

NOT a `ContextVar` -- see this module's own docstring, "CORRECTION PASS":
`incident_manager`'s AgentTool call runs inside a CHILD `asyncio.Task`
(ADK's own `handle_function_call_list_async` wraps every tool call in
`asyncio.create_task`), and a `ContextVar.set(...)` performed inside a
child task never propagates back to the parent task's context -- verified
with a standalone repro before choosing this fix, not guessed. A plain
dict, keyed by the SAME `run_id` every entry is scoped to (unique per turn,
generated fresh by chat_service.py before this call chain starts), sidesteps
that entirely: ordinary shared mutable state is visible from any task in
the same process, unlike context-local storage. Read via `.pop(run_id,
None)` (single-use); `discard_pending_trusted_result` below is the
crash/cancellation-before-consumption backstop chat_service.py's own
turn-end `finally` block calls (section 22 -- "no cross-run leakage").
"""


_trust_validation_failed_runs: set[str] = set()
"""SOURCE/PROVENANCE REGRESSION FIX (urgent pass): `TeamsSourceCapture`
(source_reference.py) observes EVERY event streamed from team_manager's
own Runner, including the INTERMEDIATE `incident_manager` function-
response event -- which, when retrieval/synthesis genuinely succeeded,
already carries real, valid, non-fabricated evidence, `outcome="ok"` --
BEFORE `_present_fast_path_result_via_trusted_pipeline` even runs its own
trust check one turn later. If that check then fails closed, the SAFE
fail-closed text becomes team_manager's own final answer, but the EARLIER
event (with genuinely real evidence) has already been observed and
captured -- `TeamsSourceCapture` has no way to know a LATER turn discarded
it, so `chat_service.py` would otherwise still attach a SourceReference to
a message that has nothing to do with it. This is a genuinely new edge
case introduced by the trust-validation-failure path itself (no prior
design in this codebase ever discarded an already-successful specialist
result after the fact) -- verified by direct reproduction, not guessed.

Fixed with the SAME run-id-keyed, per-turn bookkeeping pattern as `_
pending_trusted_result_by_run` above: `_present_fast_path_result_via_
trusted_pipeline` records `run_id` here whenever it fails closed;
`trust_validation_failed_for_run` (below) lets chat_service.py check this,
once, right before building a `SourceReference`, and suppress it if this
turn's fast path failed closed -- regardless of what `TeamsSourceCapture`
itself already captured. `TeamsSourceCapture`/`build_teams_source_
reference` themselves are UNCHANGED -- this is the smallest correct fix,
a single extra check at the one call site that decides whether to use
what they built, not a second source pipeline.
"""


def discard_pending_trusted_result(run_id: str) -> None:
    """Backstop cleanup for `run_id` -- safe to call whether or not an
    entry exists (turn-end hygiene, mirrors chat_service.py's own existing
    "always clear these keys in `finally`, regardless of outcome" pattern
    for `PENDING_READ_CONTINUATION_STATE_KEY`/the trusted envelope keys).
    Guards against a genuinely unexpected crash/cancellation landing
    between `_fast_path_before_model_callback` writing an entry and team_
    manager's own next turn ever running to consume it -- the NORMAL path
    already self-cleans via `.pop()` in `_present_fast_path_result_via_
    trusted_pipeline`; this only prevents an orphaned entry from lingering
    in process memory forever in that rare case. Never a correctness risk
    even if never called (a leaked entry cannot be consumed by a DIFFERENT
    run -- `run_id` values are never reused), only a hygiene one.

    Also clears `_trust_validation_failed_runs`' own entry for `run_id`,
    for the identical reason -- same single "per-run fast-path bookkeeping"
    cleanup call site, never a second one.
    """
    _pending_trusted_result_by_run.pop(run_id, None)
    _trust_validation_failed_runs.discard(run_id)


def trust_validation_failed_for_run(run_id: str) -> bool:
    """Read-only check for chat_service.py -- see `_trust_validation_
    failed_runs`' own docstring above. Never clears the flag (cleanup is
    `discard_pending_trusted_result`'s own job, called unconditionally
    from chat_service.py's turn-end `finally`, exactly like every other
    piece of this module's per-run bookkeeping)."""
    return run_id in _trust_validation_failed_runs


_TRUST_VALIDATION_FAILURE_TEXT = "The assistant could not complete this request. Please try again."
"""Plain, hardcoded, safe fail-closed text -- reuses chat_service.py's own
existing generic run-failure wording verbatim (the SAME phrase `_safe_
fallback_error` below already uses for the analogous "execute_read_
continuation itself failed" case), rather than inventing new wording. No
raw ids, no Teams content, no internal trust terminology -- see section 9.
"""


def _has_image_evidence(tool_context: Any) -> bool:
    """POST-5.1 B6 -- structural gate (instruction section 17): a request
    delegated WITH trusted current-turn image evidence must never take
    this shortcut, because the shortcut's whole point is to let `incident_
    manager`'s own REAL model call be skipped entirely (`_fast_path_
    before_model_callback`, below, returns a synthesized `LlmResponse`
    without ever invoking Gemini for this turn) -- which would mean the
    image is silently never reasoned over by anything.

    `tool_context.user_content` here is `incident_manager`'s OWN nested
    invocation's user content -- i.e. exactly the `Content`
    `MultimodalAgentTool` (multimodal_agent_tool.py) already built for
    THIS call, structured-request text plus any trusted image `Part`s
    appended in order. Checking for a `file_data`-bearing part is a
    trusted, STRUCTURAL runtime fact (an application-controlled `Part`
    either is or isn't there) -- never natural-language/keyword routing
    over the user's own wording (instruction section 17's own explicit
    distinction).

    Deliberately checked INDEPENDENTLY of `_requires_governed_knowledge`
    (same function shape, different signal) -- an image-bearing request
    must bypass this optimization even when `requires_governed_knowledge`
    is false, since Teams-alone synthesis still cannot incorporate visual
    evidence the way a normal `incident_manager` turn (which DOES receive
    the same image `Part`s, per `MultimodalAgentTool`) can.
    """
    user_content = getattr(tool_context, "user_content", None)
    parts = getattr(user_content, "parts", None) if user_content else None
    if not parts:
        return False
    return any(getattr(part, "file_data", None) is not None for part in parts)


def _requires_governed_knowledge(tool_context: Any) -> bool:
    """Reads `IncidentManagerRequest.requires_governed_knowledge` back
    from `tool_context.user_content` -- the SAME `types.Content` ADK's
    own `AgentTool.run_async` built from `input_value.model_dump_json(...)`
    at the start of THIS incident_manager invocation (verified against
    the installed ADK source: `ToolContext`/`ReadonlyContext.user_content`
    is a public, documented "the user content that started this
    invocation" property, stable across every callback within one
    invocation -- never re-derived from model reasoning or from any
    tool's own call arguments, which never carry this field at all).

    FAILS CLOSED (returns `True`, i.e. "assume governed knowledge might be
    required, skip the fast path") on anything unexpected -- missing
    content, non-JSON text, or a missing/malformed field -- since
    incorrectly SKIPPING the optimization only costs latency, while
    incorrectly ENTERING it when governed knowledge was actually required
    is the exact correctness bug this pass fixes. This should not
    normally be reached in practice: the content is JSON this codebase
    itself produced via `IncidentManagerRequest.model_dump_json`.
    """
    user_content = getattr(tool_context, "user_content", None)
    parts = getattr(user_content, "parts", None) if user_content else None
    if not parts:
        return True
    text = "".join(p.text for p in parts if getattr(p, "text", None))
    if not text:
        return True
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return True
    if not isinstance(payload, dict):
        return True
    return bool(payload.get("requires_governed_knowledge", False))


def _capture_unique_match_for_fast_path(
    tool: Any, args: dict[str, Any], tool_context: Any, tool_response: Any
) -> None:
    """`after_tool_callback`, scoped to `teams_list_chats` -- see this
    module's own docstring, part 1. Always returns `None` (side effect
    only, exactly like `state_sync.py`'s `sync_incident_manager_result_
    to_state` and `selection_delegation_guard.py`'s `record_selection_
    needed`).

    FAST-PATH ELIGIBILITY (pre-4H correction pass): this optimization
    assumes the ENTIRE delegated request can be satisfied by a Teams
    read alone -- no longer a safe assumption once governed knowledge
    (5.1J) exists as an independent, equally-required context source. If
    `requires_governed_knowledge` is true on the request that produced
    this `teams_list_chats` call, the marker below is never set, so
    `_fast_path_before_model_callback` finds nothing pending and
    incident_manager's own NORMAL (non-shortcut) turn continues instead
    -- free to use Teams tools, `knowledge_search`, and
    `knowledge_select_evidence` together, exactly as a combined request
    requires. This never affects a Teams-ONLY request (`requires_
    governed_knowledge=False`, the default) -- the fast path remains
    fully eligible for that case, unchanged.
    """
    if getattr(tool, "name", None) != "teams_list_chats":
        return None
    if args.get("pending_write_message"):
        return None  # a write's own destination resolution -- untouched
    if not isinstance(tool_response, dict) or tool_response.get("match") != "matched":
        _log_resolution_outcome(tool_response)
        return None
    if _requires_governed_knowledge(tool_context):
        _perf_logger.info(
            "perf stage=fast_path_skipped_requires_governed_knowledge run_id=%s", current_run_id()
        )
        return None

    if _has_image_evidence(tool_context):
        # POST-5.1 B6 -- see `_has_image_evidence`'s own docstring: an
        # image-bearing delegation must always get incident_manager's own
        # real, image-seeing model turn, never this shortcut.
        _perf_logger.info(
            "perf stage=fast_path_skipped_has_image_evidence run_id=%s", current_run_id()
        )
        return None

    matched_chat = tool_response.get("matched_chat") or {}
    chat_id = matched_chat.get("chat_id")
    if not chat_id:
        return None  # defensive only -- "matched" always carries matched_chat

    topic = args.get("topic") or ""
    tool_context.state[_FAST_PATH_PENDING_STATE_KEY] = {
        "chat_id": chat_id,
        "chat_title": matched_chat.get("title") or topic,
        "question": _safe_pending_question(topic, args.get("pending_question")),
        "operation": _safe_read_operation(args.get("pending_operation")).value,
        "requested_time_range": args.get("pending_time_range"),
    }
    _log_resolution_outcome(tool_response)
    return None


def _log_resolution_outcome(tool_response: Any) -> None:
    """Section 6 safe diagnostic -- closed-vocabulary outcome only, never a
    chat title/id/candidate value.
    """
    match = tool_response.get("match") if isinstance(tool_response, dict) else None
    outcome = {"matched": "unique", "ambiguous": "ambiguous", "not_found": "not_found"}.get(match, "unknown")
    _perf_logger.info(
        "perf stage=chat_resolution_complete run_id=%s outcome=%s", current_run_id(), outcome
    )


def _safe_fallback_error() -> dict[str, Any]:
    """Mirrors `chat_service.py`'s own existing generic run-failure wording
    -- used ONLY for `execute_read_continuation` returning `None` (its own
    rare, genuinely-unexpected-failure path; a real gateway/retrieval
    error already comes back as a normal, non-`None` `outcome="error"`
    result -- see that function's own docstring). Never a second
    `teams_list_chats`, never a fallback to the generic model turn this
    callback just avoided (section 41's own "no generic Incident Manager
    fallback" requirement).
    """
    return IncidentManagerResponse(
        outcome=IncidentManagerOutcome.ERROR,
        detail="The assistant could not complete this request. Please try again.",
    ).model_dump(mode="json", exclude_none=True)


async def _fast_path_before_model_callback(callback_context: Any, llm_request: Any) -> Optional[LlmResponse]:
    """`before_model_callback` -- see this module's own docstring, part 2.
    MUST be listed BEFORE P2's `before_model_call` in `_fast_path_
    incident_manager`'s `before_model_callback` list (see below).
    """
    pending = callback_context.state.get(_FAST_PATH_PENDING_STATE_KEY)
    if not pending:
        return None

    # Single-use: cleared immediately so a later turn (should one ever
    # reach this same nested session again, e.g. a retry) never re-applies
    # a stale marker.
    callback_context.state[_FAST_PATH_PENDING_STATE_KEY] = None

    invocation_context = callback_context._invocation_context
    run_id = current_run_id()
    if run_id is None:
        # SOURCE/PROVENANCE REGRESSION FIX (urgent pass): safe no-op outside
        # a chat_service.py-driven turn -- matches P2's own established
        # convention (perf_timing.py's `before_model_call`: "Outside a
        # chat_service.py-driven turn (e.g. a standalone adk run/adk web
        # invocation, or a test that never bound a run_id) -- a safe
        # no-op"). Previously fell back to `invocation_context.invocation_
        # id` here -- found, by direct reproduction, to LEAK: that id is
        # this NESTED AgentTool invocation's own, different from the id
        # `_present_fast_path_result_via_trusted_pipeline` (team_manager's
        # OWN, separate outer invocation) would fall back to, so the two
        # never agreed on a key and the entry written below was never
        # consumed -- a permanent leak into `_pending_trusted_result_by_
        # run` for the rest of the process. Falls through to the real,
        # unoptimized generic incident_manager turn instead -- safe, just
        # not fast; this can only happen outside chat_service.py's own
        # orchestration, which always binds a run_id before this turn ever
        # starts.
        return None
    _perf_logger.info("perf stage=exact_read_fast_path_entered run_id=%s", run_id)

    try:
        operation = ReadOperation(pending.get("operation") or ReadOperation.SUMMARIZE.value)
    except ValueError:
        operation = ReadOperation.SUMMARIZE

    continuation = ResolvedReadContinuation(
        operation=operation,
        selected_chat_id=pending["chat_id"],
        selected_chat_topic=pending.get("chat_title") or pending["chat_id"],
        question=pending.get("question"),
        requested_time_range=pending.get("requested_time_range"),
    )

    result = await execute_read_continuation(
        session_service=invocation_context.session_service,
        user_id=invocation_context.user_id,
        parent_session_id=invocation_context.session.id,
        run_id=invocation_context.invocation_id,
        parent_state=dict(invocation_context.session.state),
        continuation=continuation,
        # POST-5.1 B6 -- defensive-only: `_has_image_evidence` (above)
        # already structurally prevents this fast path from ever engaging
        # for an image-bearing delegation, so `continuation.attachment_ids`
        # is always empty here in practice -- but `execute_read_
        # continuation` requires these explicitly (never a silently
        # unconfigured default), so the same real, global singletons
        # `chat_service.py`'s own `get_chat_service()` uses are wired here
        # too.
        attachment_service=get_attachment_service(),
        attachment_storage=get_attachment_storage(),
    )
    if result is None:
        result = _safe_fallback_error()

    # Handed to team_manager's own next turn via `_pending_trusted_result_
    # by_run` (a run-id-keyed dict, not a ContextVar -- see module
    # docstring, "CORRECTION PASS") regardless of `outcome` ("ok"/
    # "no_result"/"error" alike) -- mirrors post-selection's own behavior
    # exactly: `chat_service.py` promotes EVERY `execute_read_continuation`
    # result through the same envelope/presentation mechanism, never only
    # successful ones (section 15's "no generic fallback" applies to
    # failures too).
    _pending_trusted_result_by_run[run_id] = result

    # SOURCE/PROVENANCE REGRESSION FIX: `_fast_path_incident_manager`
    # (this agent) inherits `strip_unverified_evidence` as its OWN `after_
    # agent_callback`, unmodified, from the base `incident_manager` (see
    # this module's own docstring for why it must stay -- it is the real
    # safety net for the rare case this shortcut does NOT engage and the
    # discovery agent falls back to its own real `teams_get_messages` call
    # instead). That callback re-validates `result["evidence"]`'s own
    # `message_id`s against THIS session's `KNOWN_MESSAGE_IDS_STATE_KEY`
    # -- but retrieval for the fast path happened inside `execute_read_
    # continuation`'s own, SEPARATE internal specialist session, so this
    # discovery session's own copy of that key has never been populated
    # and is empty -- silently stripping ALREADY-validated evidence a
    # second time, against the wrong scope, discarding it entirely. This
    # is the exact, verified root cause of the missing Source chip
    # (confirmed with a real-ADK probe: `execute_read_continuation`
    # returns non-empty evidence; the discovery agent's own final tool
    # response reaches team_manager with `evidence: []`).
    #
    # FIX: seed THIS session's `KNOWN_MESSAGE_IDS_STATE_KEY` with exactly
    # the `message_id`s already present in `result["evidence"]` --
    # nothing more. This never expands trust: every one of these ids was
    # already validated once, in the correct scope, by `execute_read_
    # continuation`'s own internal `strip_unverified_evidence` run (see
    # read_continuation_execution.py, frozen, unchanged) -- this merely
    # lets the SAME, unmodified check succeed a second time instead of
    # incorrectly failing due to running in a session that was never
    # seeded. No raw Teams content crosses this boundary, only message
    # ids that are already part of the validated, trusted result.
    evidence = result.get("evidence")
    if isinstance(evidence, list) and evidence:
        already_known = set(callback_context.state.get(KNOWN_MESSAGE_IDS_STATE_KEY, []))
        already_known.update(
            item["message_id"] for item in evidence if isinstance(item, dict) and item.get("message_id")
        )
        callback_context.state[KNOWN_MESSAGE_IDS_STATE_KEY] = sorted(already_known)

    _perf_logger.info("perf stage=exact_read_fast_path_complete run_id=%s", run_id)

    response_text = json.dumps(result)
    return LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=response_text)]))


_fast_path_incident_manager = incident_manager.model_copy(
    update={
        "after_tool_callback": _capture_unique_match_for_fast_path,
        "before_model_callback": [_fast_path_before_model_callback, before_model_call("incident_manager")],
    }
)
"""See this module's own docstring, part 3. Wired into `AgentTool(agent=
...)` in team_manager/agent.py in place of the base `incident_manager`."""


async def _run_trusted_presentation(
    *, validated_result: dict[str, Any], seed_state: dict[str, Any], user_content: Any, run_id: str
) -> Optional[types.Content]:
    """Runs `presentation_team_manager` (tools=[], `TEAM_MANAGER_TRUSTED_
    RESULT_INSTRUCTION` -- the SAME agent object R1/post-selection already
    use, imported lazily below to avoid a circular import with agent.py,
    which imports THIS module) against a fresh, throwaway session --
    mirrors read_continuation_execution.py's own "create an internal
    session, run a nested Runner, delete it" pattern (`_run_specialist_
    and_collect`), never the real canonical session (see module docstring
    for why). Returns the final response `Content`, or `None` if the
    nested run produced no USABLE (non-blank-text) content -- the caller
    (`_present_fast_path_result_via_trusted_pipeline`) treats `None` as a
    signal to retry once, then fail closed, NEVER as a signal to fall
    back to team_manager's own normal presentation (section 8's own
    explicit prohibition).

    EMPTY-RESPONSE FIX (pre-4H correction pass): the loop below now keeps
    only a content whose OWN `content_has_nonblank_text` check passes --
    previously `if event.content:` alone was used, which is true for a
    `types.Content` with an empty `parts` list or a thought-only/blank-
    text part, silently forwarding an effectively-empty "success" up to
    the caller instead of the honest "no content" this function's own
    contract already promised to signal.
    """
    from backend.agents.team_manager.agent import presentation_team_manager

    session_service = InMemorySessionService()
    app_name = f"{presentation_team_manager.name}{_FAST_PATH_APP_NAME_SUFFIX}"
    internal_session_id = f"fast-path-presentation::{run_id}"
    try:
        await session_service.create_session(
            app_name=app_name,
            user_id="direct-fast-path",
            session_id=internal_session_id,
            state={**seed_state, PENDING_SPECIALIST_RESULT_STATE_KEY: validated_result},
        )
        runner = Runner(
            app_name=app_name,
            agent=presentation_team_manager,
            session_service=session_service,
            memory_service=InMemoryMemoryService(),
        )
        last_content: Optional[types.Content] = None
        try:
            async for event in runner.run_async(
                user_id="direct-fast-path", session_id=internal_session_id, new_message=user_content
            ):
                if event.content and content_has_nonblank_text(event.content):
                    last_content = event.content
        finally:
            await runner.close()
        return last_content
    finally:
        try:
            existing = await session_service.get_session(
                app_name=app_name, user_id="direct-fast-path", session_id=internal_session_id
            )
            if existing is not None:
                await session_service.delete_session(
                    app_name=app_name, user_id="direct-fast-path", session_id=internal_session_id
                )
        except Exception:
            _logger.warning("direct_read_fast_path: failed to delete internal presentation session")


def _safe_trust_failure_response() -> LlmResponse:
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part.from_text(text=_TRUST_VALIDATION_FAILURE_TEXT)])
    )


async def _present_fast_path_result_via_trusted_pipeline(
    callback_context: Any, llm_request: Any
) -> Optional[LlmResponse]:
    """`before_model_callback` on team_manager itself -- see this module's
    own docstring, part 5. MUST be listed BEFORE P2's own `before_model_
    call` in `_fast_path_team_manager`'s `before_model_callback` list (see
    below) -- same "never log an unpaired model_call_start" reasoning as
    part 2 above.

    TRUST BOUNDARY: once an entry exists for this `run_id`, this callback
    ALWAYS returns a non-`None` `LlmResponse` -- either the genuine
    `presentation_team_manager` output, or a safe, fail-closed error. It
    NEVER returns `None` past this point, because `None` means "let team_
    manager's own real, full-instruction, `incident_manager_tool`-bearing
    turn run" -- exactly the trust-boundary bypass this correction pass
    exists to close (section 8: "validation fails -> return None -> let
    full Team Manager continue" is explicitly forbidden).
    """
    run_id = current_run_id()
    if run_id is None:
        # Same safe no-op as `_fast_path_before_model_callback`'s own --
        # if `current_run_id()` was never bound, the write side already
        # bailed out too (nothing was ever staged under any key this
        # callback could reconstruct), so there is nothing to consume here
        # either. See that function's own docstring for the full
        # leak-reproduction rationale.
        return None
    result = _pending_trusted_result_by_run.pop(run_id, None)
    if result is None:
        return None  # no fast-path result pending this turn -- unaffected

    validated = build_and_validate_trusted_envelope(run_id, result)
    if validated is None:
        _logger.warning("direct_read_fast_path: trusted envelope validation failed -- failing closed")
        _perf_logger.info("perf stage=direct_fast_path_trust_validation_failed run_id=%s", run_id)
        _trust_validation_failed_runs.add(run_id)
        return _safe_trust_failure_response()

    _perf_logger.info("perf stage=direct_fast_path_trusted_result_ready run_id=%s", run_id)

    # Same non-`_adk`-prefixed filter `AgentTool.run_async`'s own established
    # state-forwarding code already applies (tools/agent_tool.py) -- carries
    # Case-context/etc. forward into the throwaway presentation session.
    seed_state = {k: v for k, v in callback_context.state.to_dict().items() if not k.startswith("_adk")}

    _perf_logger.info("perf stage=direct_fast_path_presentation_mode run_id=%s", run_id)
    content = await _run_trusted_presentation(
        validated_result=validated,
        seed_state=seed_state,
        user_content=callback_context.user_content,
        run_id=run_id,
    )
    if content is None:
        # EMPTY-RESPONSE FIX (pre-4H correction pass): `validated` (the
        # already-validated `IncidentManagerResponse`) is REUSED unchanged
        # for this one bounded retry -- this repeats ONLY the presentation
        # step (a fresh throwaway session/Runner over `presentation_team_
        # manager`, tools=[]), never `execute_read_continuation`, never
        # `teams_list_chats`/`teams_get_messages` again. At most one retry
        # -- if it also produces no usable text, this falls through to the
        # same deterministic safe-failure response as before, never an
        # unbounded loop.
        _logger.warning(
            "direct_read_fast_path: trusted presentation produced no usable text -- retrying once run_id=%s",
            run_id,
        )
        _perf_logger.info("perf stage=direct_fast_path_presentation_retry run_id=%s", run_id)
        content = await _run_trusted_presentation(
            validated_result=validated,
            seed_state=seed_state,
            user_content=callback_context.user_content,
            run_id=run_id,
        )
    if content is None:
        _logger.warning(
            "direct_read_fast_path: trusted presentation still produced no usable text after retry -- failing closed run_id=%s",
            run_id,
        )
        _trust_validation_failed_runs.add(run_id)
        return _safe_trust_failure_response()
    return LlmResponse(content=content)


_fast_path_team_manager_cache: list[Any] = []
"""Lazily-built, memoized singleton -- see `get_fast_path_team_manager`
below. A one-element list (not a plain `None`-checked module global) only
to make the "build once, cache" intent explicit without a `global`
statement; never mutated from more than one place."""


def get_fast_path_team_manager() -> Any:
    """Returns the `.model_copy` of `team_manager` (agent.py) with `_present_
    fast_path_result_via_trusted_pipeline` prepended to its `before_model_
    callback` list -- built lazily, on first call, and memoized, because
    `agent.py` imports `_fast_path_incident_manager` from THIS module at
    module-load time, so this module cannot import the base `team_manager`
    from agent.py at module-load time in return (agent.py would not have
    finished defining it yet) -- the same lazy-import-inside-a-function
    pattern `chat_service.py`'s own `_build_presentation_runner` already
    uses for `presentation_team_manager`. `chat_service.py`'s `_build_
    runner` calls this in place of importing the base `team_manager`
    directly.
    """
    if not _fast_path_team_manager_cache:
        from backend.agents.team_manager.agent import team_manager

        _fast_path_team_manager_cache.append(
            team_manager.model_copy(
                update={
                    "before_model_callback": [
                        _present_fast_path_result_via_trusted_pipeline,
                        before_model_call("team_manager"),
                    ]
                }
            )
        )
    return _fast_path_team_manager_cache[0]
