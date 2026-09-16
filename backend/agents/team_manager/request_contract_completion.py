"""LIVE-CORR-12I -- Fresh RequestContract Guarantee on Every Normal User Turn.

THE GAP THIS CLOSES: team_manager's own model is instructed (prompts.py)
to call `record_request_contract` on every turn, but instruction-following
is not guaranteed -- a normal turn can complete (real answer, no error)
without ever calling it. When that happens, `VALIDATED_REQUEST_CONTRACT_
STATE_KEY` still holds whatever a PRIOR turn wrote, stamped with THAT
turn's own `run_id` -- `derive_execution_decision`'s own freshness check
(correct, unchanged) then rejects it as `INVALID_CONTRACT` for the
CURRENT turn. This mirrors the EXACT shape `source_requirements_
completion.py`'s own FIFTH pre-4H correction pass already closed for
`record_source_requirements` -- this module is the direct analogue for
`record_request_contract`, reusing the identical bounded-remediation
architecture (never a parallel one).

WHY A SEPARATE, MINIMAL REMEDIATION AGENT (never the full `team_manager`,
never `incident_manager`, never any write tool): the ONLY thing missing is
the structured contract itself -- not a new answer, not new retrieval, not
a second operational workflow. `_contract_only_agent` is a `.model_copy`
of the shared `team_manager` (same established pattern as `_declaration_
only_agent`/`_CONTINUATION_INCIDENT_MANAGER`/`presentation_team_manager`)
with `tools=[record_request_contract]` ONLY -- structurally, not just by
instruction, incapable of delegating to `incident_manager`, calling any
Teams tool, any write tool, or any other `record_*` tool.

PROVENANCE MUST BIND TO THE REAL CURRENT-TURN TEXT, NEVER THE
REMEDIATION'S OWN THROWAWAY SESSION: `validate_and_persist_request_
contract` (the SAME, completely unmodified, `after_tool_callback` a
normal turn already uses) verifies `provided_context` against `tool_
context.user_content` -- this module invokes it DIRECTLY (never via ADK's
own callback wiring, since the remediation's own throwaway Runner has
no callback registered) with a minimal `ToolContext`-shaped shim bound to
the REAL current-turn `user_content` the caller (`chat_service.py`)
already has, not the remediation's own internal message. This is the
SAME deterministic canonicalization/provenance-verification/request-class
-derivation logic a normal successful turn already relies on -- never a
second, parallel RequestContract architecture.

CURRENT-TURN FRESHNESS: `validate_and_persist_request_contract` stamps
`run_id` from `current_run_id()` (turn_context.py's own ContextVar) --
this module binds that ContextVar to the CALLER's own trusted `current_
run_id` (never a suffixed sub-run-id, unlike `governed_knowledge_
completion.py`'s own remediation, because the whole point here is for the
result to satisfy the OUTER turn's own freshness check) for the exact
duration of the persistence call only, then resets it -- mirrors `chat_
service.py`'s own top-level `bind_run_id`/`reset_run_id` pattern exactly.

BOUNDED TO EXACTLY ONE ATTEMPT: called at most once per turn, from
`chat_service.py`'s own completion code (never a loop). Returns `None` if
the remediation agent still does not call `record_request_contract`, or
its response fails structural/provenance validation -- the caller's own
job is to fail closed (`INVALID_CONTRACT`) in that case, never this
module's.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.agents.team_manager.request_contract import (
    REQUEST_CONTRACT_TOOL_NAME,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    RequestContract,
    validate_and_persist_request_contract,
)
from backend.api.session_service import APP_NAME
from backend.api.turn_context import bind_run_id, reset_run_id

_logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("backend.perf")

_APP_NAME = f"{APP_NAME}::request-contract-remediation"
_REMEDIATION_USER_ID = "request-contract-remediation"

_CONTRACT_ONLY_INSTRUCTION = """You are classifying ONE user message, reproduced below -- you are not answering it. Call `record_request_contract(...)` exactly once, describing THIS message's own intent, requested_output, subject, continuation, provided_context, and missing_context, using exactly the same judgment and rules you always apply when recording a request contract during a normal turn.

Call the tool once, then stop. Do not attempt to answer the user's own question, and do not call any other tool."""

_contract_only_agent_cache: list[Any] = []


def _contract_only_agent() -> Any:
    """Lazily-built, memoized `.model_copy` of the base `team_manager` --
    see this module's own docstring for the full rationale. Lazy for the
    same reason `direct_read_fast_path.get_fast_path_team_manager`/
    `source_requirements_completion._declaration_only_agent` are."""
    if not _contract_only_agent_cache:
        from backend.agents.team_manager.agent import team_manager
        from backend.agents.team_manager.request_contract import record_request_contract

        _contract_only_agent_cache.append(
            team_manager.model_copy(
                update={
                    "tools": [record_request_contract],
                    "instruction": _CONTRACT_ONLY_INSTRUCTION,
                    "before_tool_callback": None,
                    "after_tool_callback": None,
                }
            )
        )
    return _contract_only_agent_cache[0]


class _CapturedToolContext:
    """Minimal `ToolContext`-shaped shim -- only the two attributes
    `validate_and_persist_request_contract` actually reads. `user_content`
    is bound to the REAL current-turn message (never this remediation's
    own throwaway session text) so provided_context provenance
    verification is genuine; `state` is a plain, throwaway dict this
    module reads back from directly afterward -- never the real session's
    own state object (the caller, `chat_service.py`, is responsible for
    persisting the result via its own existing `persist_state_delta`
    call, exactly like every other end-of-turn state write in that
    method)."""

    def __init__(self, user_content: types.Content) -> None:
        self.user_content = user_content
        self.state: dict[str, Any] = {}


class _NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


async def request_current_turn_contract(
    *, question: str, user_content: types.Content, run_id: str, current_run_id: str
) -> Optional[RequestContract]:
    """Runs the contract-only remediation agent exactly once, against a
    throwaway session, and -- if it called `record_request_contract` --
    re-runs the SAME deterministic `validate_and_persist_request_contract`
    logic a normal turn's own `after_tool_callback` would have, bound to
    the REAL `user_content` and stamped with the REAL `current_run_id`.
    Returns the resulting, already-`run_id`-fresh `RequestContract`, or
    `None` for any expected failure shape (no tool call, structural
    validation failure, or a contract that -- even after re-verification
    -- failed to persist) -- the caller fails closed on `None`, never this
    function.
    """
    content = types.Content(role="user", parts=[types.Part.from_text(text=question)])

    session_service = InMemorySessionService()
    session_id = f"request-contract-remediation::{run_id}"
    _perf_logger.info("perf stage=request_contract_remediation_start run_id=%s", run_id)
    runner = Runner(
        app_name=_APP_NAME, agent=_contract_only_agent(), session_service=session_service, memory_service=InMemoryMemoryService()
    )
    try:
        await session_service.create_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
        captured_response: Optional[dict[str, Any]] = None
        async for event in runner.run_async(user_id=_REMEDIATION_USER_ID, session_id=session_id, new_message=content):
            if getattr(event, "partial", False):
                continue
            for response in event.get_function_responses():
                if getattr(response, "name", None) != REQUEST_CONTRACT_TOOL_NAME:
                    continue
                result = getattr(response, "response", None)
                if isinstance(result, dict):
                    captured_response = result

        if captured_response is None or "error" in captured_response:
            _logger.warning(
                "request_contract_completion: remediation did not produce a usable contract run_id=%s", run_id
            )
            _perf_logger.info("perf stage=request_contract_remediation_failed run_id=%s", run_id)
            return None

        run_id_token = bind_run_id(current_run_id)
        try:
            tool_context = _CapturedToolContext(user_content)
            validate_and_persist_request_contract(
                _NamedTool(REQUEST_CONTRACT_TOOL_NAME), {}, tool_context, captured_response
            )
        finally:
            reset_run_id(run_id_token)

        raw = tool_context.state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY)
        if raw is None:
            _logger.warning(
                "request_contract_completion: remediation response failed re-validation run_id=%s", run_id
            )
            _perf_logger.info("perf stage=request_contract_remediation_failed run_id=%s", run_id)
            return None

        contract = RequestContract.model_validate(raw)
        _perf_logger.info("perf stage=request_contract_remediation_ok run_id=%s", run_id)
        return contract
    finally:
        await runner.close()
        try:
            existing = await session_service.get_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
            if existing is not None:
                await session_service.delete_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
        except Exception:
            _logger.warning("request_contract_completion: failed to delete internal remediation session run_id=%s", run_id)
