"""Production hardening pass #3: trusted, structured server-side hand-off
of a deterministically-obtained `ResolvedReadContinuation` execution
result to team_manager's own turn -- replaces pass #2's `[INCIDENT_
MANAGER_RESULT]` text-marker mechanism.

THE WEAKNESS PASS #3 FIXES: pass #2 concatenated the validated result as
JSON, prefixed with a literal marker string, directly into the `new_
message` text team_manager's turn received -- the SAME channel arbitrary
user-authored text travels through. A user could type the identical
marker text themselves; nothing structurally prevented it from at least
LOOKING like a legitimate hand-off to whatever inspected the raw message
text. This module removes that channel entirely: the validated result
never enters `new_message` at all.

FIX -- SESSION-STATE TEMPLATING, THE SAME ESTABLISHED, ALREADY-TRUSTED
MECHANISM team_manager's prompt ALREADY uses for `{selected_teams_chat_
topic?}`/`{last_teams_evidence?}` (prompts.py, state_sync.py) and
incident_manager's prompt already uses for `{temp:resolved_chat_id?}`
(read_continuation_enforcement.py): `chat_service.py` persists the
validated result into `PENDING_SPECIALIST_RESULT_STATE_KEY` on the
PARENT session's own state (via the SAME `persist_state_delta` every
other trusted state write in this codebase already uses) BEFORE calling
team_manager's `Runner.run_async` -- never inside `new_message`. Team
manager's instruction (prompts.py) references it via ADK's native
`{pending_specialist_result?}` state-templating, which `LlmAgent.
canonical_instruction` applies automatically to any plain-string
instruction (verified against the installed ADK 1.33.0 source in an
earlier milestone) -- the SAME mechanism, not a new one.

WHY THIS SATISFIES THE TRUST BOUNDARY: `new_message`/`types.Content` is
the ONLY channel a user's own typed text ever reaches a turn through --
this mechanism never touches it. `PENDING_SPECIALIST_RESULT_STATE_KEY` is
written EXCLUSIVELY by `chat_service.py`'s own deterministic
orchestration code (never an ADK tool, never anything a model or a user
request can set) and is scoped to the authenticated session the SAME way
every other piece of session state already is (session ownership already
verified upstream by `get_session(session_id, user_id)`) -- a user typing
the literal old marker text, or anything else, lands in `new_message` as
ordinary prose and has no path to this state key at all.

WHY `temp:`-PREFIXING IS *NOT* USED HERE (unlike `resolved_chat_id`):
verified against `sessions/base_session_service.py`'s `append_event`
(`_apply_temp_state`/`_trim_temp_delta_state`) -- a `temp:` delta is
applied to the IN-MEMORY `Session` object `append_event` was called with,
but trimmed from what is durably stored, and `Runner.run_async` performs
its OWN fresh `session_service.get_session(...)` fetch internally at the
start of team_manager's turn -- a SEPARATE read than whatever local
`Session` object `chat_service.py` holds. For the `database` session
backend (this app's own production default), that fresh fetch reads
directly from the DB row, which never durably received a `temp:` value
in the first place (the exact same limitation `backend.api.turn_context`'s
own module docstring already documents in detail for an analogous
problem). A plain (non-`temp:`) key durably persists long enough for that
SAME-request fresh fetch to see it -- `chat_service.py` then explicitly
clears it again, in the SAME `finally` block that already cleans up the
read continuation and the Teams-snippet mailbox, so it never survives
between turns (instruction section 12/18) despite being a "durable" key
for the brief window it is actually needed.

SYNTHETIC EVENTS STILL EXIST HERE, NARROWED IN ROLE (instruction section
11): `synthetic_incident_manager_call_event`/`synthetic_incident_manager_
response_event` remain -- still purely an internal hand-off to `chat_
service.py`'s own existing per-event observers (`StatusTranslator`/
`RunTraceTranslator`/`TeamsSourceCapture`/`DelegationTimer`), never
appended to any ADK session, never seen by any model. They are fed FROM
the already-validated result dict (the authoritative source of truth,
persisted separately above) -- purely derived observability/provenance
plumbing, never themselves a second source of truth for what team_manager
presents.

PRODUCTION HARDENING PASS #4 -- HARD-CRASH STALE-STATE PROTECTION:
`finally` cleanup (chat_service.py) correctly handles every NORMAL exit
path (success, a caught exception, `asyncio.CancelledError`/server-side
Stop) -- but a `finally` block cannot run at all if the whole process
dies first (a hard crash, container/OOM kill, machine restart). If that
happened immediately after `PENDING_SPECIALIST_RESULT_STATE_KEY` was
durably persisted but before it was cleared, a LATER, unrelated run on
the SAME session would find it still present and -- under the pass #3
design alone -- team_manager's prompt would render it as if it were
THIS run's own result.

FIX: nothing is EVER written to `PENDING_SPECIALIST_RESULT_STATE_KEY`
(the key team_manager's prompt actually templates) directly anymore.
Instead, `chat_service.py` persists a `TrustedSpecialistResult` envelope
-- `{run_id, source, result, created_at}` -- into a SEPARATE,
run-id-bound key (`TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY`), and
`PENDING_SPECIALIST_RESULT_STATE_KEY` is populated ONLY by pushing that
envelope through `pop_current_run_specialist_result` below, which
deterministically (never asking the model) verifies `envelope.run_id ==
current_run_id` (the CURRENT turn's own `sequencer.run_id`) before
returning anything at all. `chat_service.py` also calls this SAME
function, unconditionally, at the very start of EVERY turn (mirroring
`pop_read_continuation`'s own turn-start-sweep pattern) -- at that point
this turn has not yet written anything of its own, so ANY envelope found
there necessarily belongs to a different (and therefore, by definition,
stale) run; it is popped and its removal persisted immediately, before
anything else happens, regardless of whether this turn ends up
having a continuation of its own.

FAIL-CLOSED, NEVER MODEL-ADJUDICATED: run-id/source/schema validation are
all plain Python/pydantic checks in `pop_current_run_specialist_result`
-- the model is never shown the envelope, never asked whether something
looks stale, and never has a code path to see a mismatched-run-id or
malformed result at all. A malformed/wrong-source/wrong-schema envelope
is treated identically to a stale one: `None` is returned and the raw
state is still removed.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, MutableMapping, Optional

from pydantic import BaseModel, ValidationError, field_validator

from backend.agents.incident_manager.schemas import IncidentManagerResponse

_logger = logging.getLogger(__name__)

PENDING_SPECIALIST_RESULT_STATE_KEY = "pending_specialist_result"
"""Session-state key holding this turn's validated `IncidentManagerResponse`
dict, if a `ResolvedReadContinuation` was just executed -- written only by
`chat_service.py`, read only via team_manager's own `{pending_specialist_
result?}` prompt placeholder (prompts.py), cleared in the same turn's
`finally` block regardless of outcome. Populated EXCLUSIVELY via
`pop_current_run_specialist_result`'s own return value -- never a raw
pass-through of the envelope below. See this module's own docstring for
the full rationale.
"""

TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY = "trusted_specialist_result_envelope"
"""Session-state key holding the DURABLE, run-id-bound `TrustedSpecialistResult`
envelope -- what actually survives a hard process crash (unlike a plain
in-memory guard would). Never templated into any prompt directly; only
ever read via `pop_current_run_specialist_result`.
"""

_INCIDENT_MANAGER_TOOL_NAME = "incident_manager"


class TrustedSpecialistResult(BaseModel):
    """Server-only envelope binding a validated `IncidentManagerResponse`
    to the exact SLOPANOC run that produced it. Constructed ONLY by
    `build_trusted_specialist_result_envelope` below (never accepted from
    request JSON, never derived from user text, never something a tool or
    the model can create) -- see this module's own docstring, "PRODUCTION
    HARDENING PASS #4".
    """

    run_id: str
    source: str = _INCIDENT_MANAGER_TOOL_NAME
    result: IncidentManagerResponse
    created_at: Optional[str] = None

    @field_validator("source")
    @classmethod
    def _closed_source(cls, value: str) -> str:
        if value != _INCIDENT_MANAGER_TOOL_NAME:
            raise ValueError(f"source must be {_INCIDENT_MANAGER_TOOL_NAME!r}")
        return value


def build_trusted_specialist_result_envelope(run_id: str, result: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Builds the envelope `chat_service.py` persists immediately after a
    `ResolvedReadContinuation` executes successfully. `result` must
    already be `execute_read_continuation`'s own validated dict -- this
    function re-validates it against `IncidentManagerResponse` regardless
    (fail-closed: a `None` return here means the caller must treat this
    exactly like any other specialist failure, never persist a malformed
    envelope). `run_id`/`source`/`created_at` are set here, by server
    code, exclusively -- there is no parameter through which a caller
    could supply an untrusted `run_id` or `source`.
    """
    try:
        envelope = TrustedSpecialistResult(
            run_id=run_id,
            result=IncidentManagerResponse.model_validate(result),
            created_at=datetime.now(timezone.utc).isoformat(),
        )
    except ValidationError:
        _logger.warning("read_continuation_presentation: failed to build trusted specialist result envelope")
        return None
    return envelope.model_dump(mode="json")


def validate_trusted_envelope_for_run(raw: Any, current_run_id: str) -> Optional[dict[str, Any]]:
    """THE single, fail-closed validation point for a `TrustedSpecialistResult`
    envelope -- shared by `pop_current_run_specialist_result` (the turn-
    start crash-recovery sweep and the normal same-turn consumption path
    both go through it) so there is exactly ONE place this check is ever
    performed, never a special-cased "trust this because I just wrote it"
    shortcut for the normal path.

    Returns the validated, safe-to-present `IncidentManagerResponse` dict
    ONLY when ALL of the following hold, checked deterministically here,
    never left to the model:
      - `raw` is a dict shaped like `TrustedSpecialistResult` (a missing/
        wrong-typed field fails validation)
      - `source` is exactly "incident_manager" (the one closed value)
      - `result` itself validates as a complete `IncidentManagerResponse`
      - `envelope.run_id == current_run_id` -- the actual hard-crash
        protection: at the START of any turn, `current_run_id` is a
        freshly-generated id no prior write could ever have used, so any
        envelope surviving from an earlier (possibly crashed) run
        necessarily fails this check and is discarded, never exposed.

    Safe logging only (section 11): on any failure, logs the outcome
    category and (when known) the envelope's own `source` value -- never
    the envelope's `result` content, never a chat id, never message text.
    """
    if not isinstance(raw, dict):
        _logger.info("trusted_specialist_result_rejected reason=malformed_type")
        return None

    try:
        envelope = TrustedSpecialistResult.model_validate(raw)
    except ValidationError:
        _logger.info("trusted_specialist_result_rejected reason=schema_invalid")
        return None

    if envelope.run_id != current_run_id:
        _logger.info("trusted_specialist_result_rejected reason=stale_run_id source=%s", envelope.source)
        return None

    return envelope.result.model_dump(mode="json", exclude_none=True)


def build_and_validate_trusted_envelope(run_id: str, result: dict[str, Any]) -> Optional[dict[str, Any]]:
    """P4B.3 COMPLETION PASS -- the shared "build envelope, then validate
    it for same-turn presentation" sequence `chat_service.py`'s own
    post-selection branch already performs inline (`build_trusted_
    specialist_result_envelope` immediately followed by `validate_trusted_
    envelope_for_run` with `current_run_id` equal to the SAME `run_id` the
    envelope was just built with -- the "normal same-turn consumption
    path" this module's own docstring already describes). Extracted here
    so `direct_read_fast_path.py`'s own trusted-presentation promotion
    (the direct-unique fast path) uses the IDENTICAL two calls, never a
    third envelope/validator implementation.

    Returns the validated, presentation-ready `IncidentManagerResponse`
    dict, or `None` if envelope construction or validation failed --
    fail-closed, exactly like every other caller of these two functions.
    """
    envelope = build_trusted_specialist_result_envelope(run_id, result)
    if envelope is None:
        return None
    return validate_trusted_envelope_for_run(envelope, current_run_id=run_id)


def pop_current_run_specialist_result(
    session_state: MutableMapping[str, Any], current_run_id: str
) -> tuple[Optional[dict[str, Any]], bool]:
    """Reads AND clears `TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY`
    from `session_state` in one call (mirrors `selection.service.
    pop_read_continuation`'s own "pop is the consume" contract), then
    validates it via `validate_trusted_envelope_for_run`. Returns
    `(validated_result_or_None, was_anything_present)` -- the second
    element lets the caller know whether a durable clearing write is
    actually needed (avoids an empty no-op event on every turn that never
    had anything to clear); it is `True` whenever a raw value existed at
    the key, REGARDLESS of whether it turned out to be valid, current-run,
    or garbage.
    """
    raw = session_state.pop(TRUSTED_SPECIALIST_RESULT_ENVELOPE_STATE_KEY, None)
    if raw is None:
        return None, False
    return validate_trusted_envelope_for_run(raw, current_run_id), True


class _SyntheticFunctionCall:
    """Duck-typed stand-in for `google.genai.types.FunctionCall` -- only
    `.name`/`.args`, exactly what `StatusTranslator`/`RunTraceTranslator`
    actually read.
    """

    def __init__(self, name: str, args: Optional[dict[str, Any]] = None) -> None:
        self.name = name
        self.args = args or {}


class _SyntheticFunctionResponse:
    """Duck-typed stand-in for `google.genai.types.FunctionResponse` --
    only `.name`/`.response`.
    """

    def __init__(self, name: str, response: Optional[dict[str, Any]] = None) -> None:
        self.name = name
        self.response = response or {}


class SyntheticIncidentManagerEvent:
    """Duck-typed stand-in for `google.adk.events.Event`, satisfying
    exactly the surface this backend's own event observers read
    (`get_function_calls`/`get_function_responses`/`.partial`) -- see this
    module's own docstring for why constructing one here, and feeding it
    through those SAME existing observers, is safe and never touches any
    ADK session or model.
    """

    partial = False

    def __init__(
        self,
        function_calls: Optional[list[Any]] = None,
        function_responses: Optional[list[Any]] = None,
    ) -> None:
        self._function_calls = function_calls or []
        self._function_responses = function_responses or []

    def get_function_calls(self) -> list[Any]:
        return self._function_calls

    def get_function_responses(self) -> list[Any]:
        return self._function_responses


def synthetic_incident_manager_call_event(chat_topic: str) -> SyntheticIncidentManagerEvent:
    """Mirrors the shape a real `incident_manager` AgentTool function-call
    event carries -- `chat_topic` only (the same argument
    `RunTraceTranslator._safe_chat_topic` already reads for its own trace
    label), never a chat id (which does not exist on this call's argument
    shape either way -- see `IncidentManagerRequest`).
    """
    return SyntheticIncidentManagerEvent(
        function_calls=[_SyntheticFunctionCall(_INCIDENT_MANAGER_TOOL_NAME, {"chat_topic": chat_topic})]
    )


def synthetic_incident_manager_response_event(result: dict[str, Any]) -> SyntheticIncidentManagerEvent:
    """Mirrors the shape a real `incident_manager` AgentTool function-
    response event carries -- `result` is the SAME validated
    `IncidentManagerResponse` dict `execute_read_continuation` returned,
    already schema- and evidence-validated -- never a value this module
    invents or reshapes. This is derived FROM the authoritative result
    purely for the existing observers' own bookkeeping (status/trace/
    Source drawer/delegation timing) -- it is never itself read back as
    the source of truth for what team_manager presents (that is
    `PENDING_SPECIALIST_RESULT_STATE_KEY`, set independently by the
    caller from the SAME `result`).
    """
    return SyntheticIncidentManagerEvent(
        function_responses=[_SyntheticFunctionResponse(_INCIDENT_MANAGER_TOOL_NAME, result)]
    )
