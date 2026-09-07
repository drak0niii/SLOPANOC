"""ADK specialist agent: incident_manager.

Bound into team_manager via `AgentTool` (see
backend/agents/team_manager/agent.py for the verified-against-source
rationale -- docs/AGENT_CONTRACT.md #4). incident_manager itself is a
normal ADK `Agent`; only the composition mechanism used by its caller
differs from a literal `sub_agents=[...]` declaration.

`disallow_transfer_to_parent`/`disallow_transfer_to_peers` are set for
defense-in-depth and to make the "this agent must never take over the
end-user conversation" invariant explicit in code, even though this
agent is never placed in anyone's `sub_agents` list in this build (so no
`transfer_to_agent` tool would be auto-injected regardless).

`root_agent` is exported so `incident_manager` can also be run standalone
via the ADK CLI (`adk run`/`adk web`) for local debugging -- this is a
developer convenience only; the real application never talks to the user
through incident_manager (docs/AGENT_CONTRACT.md #7).

`after_agent_callback=enforce_incident_manager_response_integrity` is the
deterministic enforcement point for "no fake provenance may reach team_
manager" -- see evidence.py. It composes two checks: THIRD pre-4H
correction pass's governed-knowledge provenance-compliance enforcement
(provenance_compliance.py -- "SEARCH RESULT != EVIDENCE USED", including
its own bounded one-retry mechanism) followed by the original Teams
evidence-stripping check (`strip_unverified_evidence`, unchanged).
Verified against the installed ADK (1.33.0) source (`agents/base_
agent.py`, `tools/agent_tool.py`): `after_agent_callback` runs once, after
the agent's whole turn (and supports an async callable -- `inspect.
isawaitable(...)` is awaited), and any `types.Content` it returns becomes
a new final event; `AgentTool.run_async` tracks the *last* event with
content in the stream as the tool's result and re-validates it against
`output_schema`, so a corrected `Content` from this callback transparently
supersedes incident_manager's original, unvalidated response before it
ever reaches team_manager.
"""
from __future__ import annotations

from google.adk.agents import Agent

from backend.agents.incident_manager.evidence import enforce_incident_manager_response_integrity
from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION
from backend.agents.incident_manager.schemas import (
    IncidentManagerRequest,
    IncidentManagerResponse,
)
from backend.agents.incident_manager.tool_call_diagnostics import log_incident_manager_tool_call
from backend.api.perf_timing import after_model_call, before_model_call
from backend.config.settings import get_settings, get_shared_llm
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence
from backend.tools.runtime_time import get_current_time_context
from backend.tools.teams.execute_write import teams_create_chat, teams_send_message
from backend.tools.teams.get_messages import teams_get_messages
from backend.tools.teams.list_chats import teams_list_chats
from backend.tools.teams.propose_write import teams_propose_create_chat, teams_propose_send_message

_settings = get_settings()

# Milestone 3B: `teams_propose_create_chat`/`teams_propose_send_message`
# may only ever CREATE a pending proposal; `teams_create_chat`/
# `teams_send_message` may only ever EXECUTE one already approved by the
# trusted application boundary (never by this agent). None of
# `approve_proposal`/`reject_proposal`/`consume_proposal`
# (backend/approval/service.py) is in this list, or reachable from
# anything in it -- see that module's "SECURITY CONTRACT" docstring.
incident_manager = Agent(
    name="incident_manager",
    # Latency pass: shared model client -- see team_manager/agent.py's own
    # comment and get_shared_llm's docstring (config/settings.py).
    model=get_shared_llm(_settings.gemini_model),
    description=(
        "Teams specialist. Discovers a Teams chat by exact name, retrieves "
        "its messages, and produces a grounded summary or answer using "
        "only retrieved Teams content. May also propose creating a Teams "
        "chat or sending a Teams message -- both require a trusted "
        "approval this agent cannot grant itself before anything is sent."
    ),
    instruction=INCIDENT_MANAGER_INSTRUCTION,
    tools=[
        teams_list_chats,
        teams_get_messages,
        get_current_time_context,
        teams_propose_create_chat,
        teams_propose_send_message,
        teams_create_chat,
        teams_send_message,
        # Phase 5.1J: the Generic Knowledge Management capability's first
        # real consumer -- see backend/tools/knowledge/__init__.py. Both
        # are read-only and model-visible with only their own narrow,
        # closed schemas (query_text/limit; selections[] of
        # knowledge_id/version_label/section_id) -- no repository,
        # as_of, applicability, or evidence-content parameter is ever
        # exposed to the model.
        knowledge_search,
        knowledge_select_evidence,
    ],
    input_schema=IncidentManagerRequest,
    output_schema=IncidentManagerResponse,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    after_agent_callback=enforce_incident_manager_response_integrity,
    # P4B AUDIT: safe, closed-vocabulary tool-name-only diagnostic (see
    # tool_call_diagnostics.py's own docstring) -- answers "what is each
    # pre-retrieval model round trip actually calling" without guessing,
    # for both the live AgentTool delegation path and read_continuation_
    # execution.py's own direct Runner invocation (both share this agent
    # object, or a `.model_copy` of it that inherits this callback).
    before_tool_callback=log_incident_manager_tool_call,
    # Latency-diagnosis pass: correlated model-call timing -- see team_
    # manager/agent.py's own comment and perf_timing.py's module
    # docstring. Fires identically whether this agent runs via a live
    # `AgentTool` delegation or `read_continuation_execution.py`'s own
    # direct Runner invocation -- both paths run through this SAME agent
    # object.
    before_model_callback=before_model_call("incident_manager"),
    after_model_callback=after_model_call("incident_manager"),
)

root_agent = incident_manager
