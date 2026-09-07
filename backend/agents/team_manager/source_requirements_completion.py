"""FIFTH pre-4H correction pass -- the bounded remediation for a missing
current-turn source-requirements declaration.

THE GAP THIS CLOSES: the fourth pass's completion gate only ever enforced
governed-knowledge provenance when team_manager had ALREADY declared
`requires_governed_knowledge=true` via `record_source_requirements`
(source_requirements.py). It never questioned the case where team_manager
skipped that declaration ENTIRELY -- `SourceRequirementsCapture` silently
defaulted to "false" for a turn with no declaration at all, which is
indistinguishable, at the completion boundary, from a turn that genuinely
does not need governed knowledge. A user asking a fresh governed-knowledge
question could still be answered straight from conversation history with
zero enforcement, simply by team_manager never calling the declaration
tool. `EVERY substantive turn must declare` is the invariant this module
exists to make structurally checkable, never merely prompt-requested.

WHY A SEPARATE, MINIMAL REMEDIATION AGENT (never the full `team_manager`,
never `incident_manager`, never any write tool): the ONLY thing missing is
the structured declaration itself -- not a new answer, not new retrieval.
`_declaration_only_agent` is a `.model_copy` of the shared `team_manager`
(same established pattern as every other reduced-capability variant in
this codebase -- `_CONTINUATION_INCIDENT_MANAGER`, `_SYNTHESIS_ONLY_
INCIDENT_MANAGER`, `presentation_team_manager`) with `tools=[record_
source_requirements]` ONLY -- structurally, not just by instruction,
incapable of delegating to `incident_manager`, calling any Teams tool, any
write tool, or `record_case_analysis`/`record_conversation_target`. This
satisfies the correction task's own "restrict the remediation tool set;
at minimum, no operational write tools" requirement by construction.

WHY MESSAGE-ONLY, NOT THE FULL CONVERSATION HISTORY: classifying whether
THIS message needs a current Teams/governed-knowledge lookup is a
judgment about the CURRENT request's own shape (e.g. "what did you tell
me earlier" is recognizable as historical recall from its own wording
alone) -- it does not require literally replaying prior turns, and doing
so would mean copying real conversation content into a second, throwaway
session for no benefit this module needs. This mirrors `governed_
knowledge_completion.py`'s own `enforce_governed_knowledge_at_completion`,
which is likewise message-only.

NO RUN-SCOPED STATE TOUCHED: `record_source_requirements` has no side
effect beyond its own return value (see source_requirements.py) -- this
remediation never binds `current_run_id()`, never touches `backend.tools.
knowledge.runtime`, and therefore can never repeat a search or a Teams
read merely by running. `run_id` is accepted only for session-id
namespacing/log correlation, exactly like every other throwaway-session
helper in this codebase.

BOUNDED TO EXACTLY ONE ATTEMPT: called at most once per turn, from
`chat_service.py`'s own completion code (never a loop). Returns `None`
if the remediation agent still does not call `record_source_requirements`
-- the caller's own job (chat_service.py) is to fail closed in that case,
never this module's.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.api.session_service import APP_NAME
from backend.api.source_requirements_capture import SourceRequirementsCapture

_logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("backend.perf")

_APP_NAME = f"{APP_NAME}::source-requirements-declaration-remediation"
_REMEDIATION_USER_ID = "source-requirements-declaration-remediation"

SAFE_DECLARATION_FAILURE_TEXT = "The assistant could not complete this request. Please try again."
"""Deterministic, Python-authored safe-failure text for the case where
NEITHER the main turn NOR the one bounded remediation attempt produced a
current-turn source-requirements declaration -- reuses the SAME generic,
already-established wording `direct_read_fast_path.py`'s own `_TRUST_
VALIDATION_FAILURE_TEXT` and `read_continuation_execution.py`'s own
`_RETRIEVAL_NOT_VERIFIED_DETAIL`-adjacent failures use elsewhere in this
codebase for an analogous "cannot trust this turn's own completion"
case -- never exposes which internal check failed.
"""

_DECLARATION_ONLY_INSTRUCTION = """You are classifying ONE user message, reproduced below -- you are not answering it. Call `record_source_requirements(requires_teams, requires_governed_knowledge)` exactly once, from your own semantic judgment (never a fixed phrase or keyword), declaring whether answering THIS message would need a current Microsoft Teams conversation's content and/or current governed/documented knowledge (procedures, technical instructions, KB content).

A message that only asks what was already said or discussed earlier IN THIS SAME conversation (recalling your own prior turns, e.g. "what did you tell me earlier", "summarize our discussion", "repeat that value") needs neither, even if the recalled content happens to involve Teams or governed facts -- declare both false for that case. A plain greeting or a request unrelated to Teams/governed content also needs neither.

Call the tool once, then stop. Do not attempt to answer the user's own question, and do not call any other tool."""

_declaration_only_agent_cache: list[Any] = []


def _declaration_only_agent() -> Any:
    """Lazily-built, memoized `.model_copy` of the base `team_manager` --
    see this module's own docstring for the full rationale. Lazy for the
    same reason `direct_read_fast_path.get_fast_path_team_manager` is:
    `agent.py` cannot be imported at THIS module's own load time without
    risking a cycle (chat_service.py imports this module at load time;
    agent.py does not import this module, so there is no actual cycle
    today, but the lazy pattern is kept for consistency with every other
    reduced-capability variant in this codebase and to avoid constructing
    a second `Agent` object before it is ever needed).
    """
    if not _declaration_only_agent_cache:
        from backend.agents.team_manager.agent import team_manager
        from backend.agents.team_manager.source_requirements import record_source_requirements

        _declaration_only_agent_cache.append(
            team_manager.model_copy(
                update={
                    "tools": [record_source_requirements],
                    "instruction": _DECLARATION_ONLY_INSTRUCTION,
                    "before_tool_callback": None,
                    "after_tool_callback": None,
                }
            )
        )
    return _declaration_only_agent_cache[0]


async def request_source_requirements_declaration(*, question: str, run_id: str) -> Optional[tuple[bool, bool]]:
    """Runs the declaration-only remediation agent exactly once, against a
    throwaway session, and returns `(requires_teams, requires_governed_
    knowledge)` if it called `record_source_requirements`, or `None` if it
    did not (the caller fails closed on `None` -- this function never
    raises for that expected outcome).
    """
    content = types.Content(role="user", parts=[types.Part.from_text(text=question)])

    session_service = InMemorySessionService()
    session_id = f"source-requirements-declaration::{run_id}"
    _perf_logger.info("perf stage=source_requirements_declaration_remediation_start run_id=%s", run_id)
    runner = Runner(
        app_name=_APP_NAME, agent=_declaration_only_agent(), session_service=session_service, memory_service=InMemoryMemoryService()
    )
    try:
        await session_service.create_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
        capture = SourceRequirementsCapture()
        async for event in runner.run_async(user_id=_REMEDIATION_USER_ID, session_id=session_id, new_message=content):
            capture.observe(event)
        if not capture.declared:
            _logger.warning(
                "source_requirements_completion: remediation did not declare source requirements run_id=%s", run_id
            )
            _perf_logger.info("perf stage=source_requirements_declaration_remediation_failed run_id=%s", run_id)
            return None
        _perf_logger.info("perf stage=source_requirements_declaration_remediation_ok run_id=%s", run_id)
        return capture.requires_teams, capture.requires_governed_knowledge
    finally:
        await runner.close()
        try:
            existing = await session_service.get_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
            if existing is not None:
                await session_service.delete_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
        except Exception:
            _logger.warning("source_requirements_completion: failed to delete internal remediation session run_id=%s", run_id)
