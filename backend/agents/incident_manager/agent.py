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

from backend.agents.incident_manager.evidence import capture_known_applicability_context, enforce_incident_manager_response_integrity
from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION
from backend.agents.incident_manager.schemas import (
    IncidentManagerRequest,
    IncidentManagerResponse,
)
from backend.agents.incident_manager.tool_call_diagnostics import log_incident_manager_tool_call
from backend.api.hosted_content_vision_context import inject_pending_hosted_content_image
from backend.api.perf_timing import after_model_call, before_model_call
from backend.config.settings import get_settings, get_shared_llm
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence
from backend.tools.runtime_time import get_current_time_context
from backend.tools.teams.execute_write import teams_create_chat, teams_send_message
from backend.tools.teams.get_hosted_content import teams_get_all_hosted_content, teams_get_hosted_content
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
        # Teams Rich Content milestone (single-image scope), Teams Image
        # Vision corrective pass: retrieves AND validates one Teams-hosted
        # image; the validated bytes are then delivered into this agent's
        # own next model call as real Gemini multimodal input via the
        # `inject_pending_hosted_content_image` before_model_callback
        # below -- see get_hosted_content.py's and hosted_content_vision_
        # context.py's own module docstrings for the full mechanism.
        # `hosted_content_id` values are only ever ones `teams_get_
        # messages` itself already returned this turn, for the SAME chat
        # -- enforced deterministically, never by prompt wording alone
        # (see that tool's own provenance enforcement).
        teams_get_hosted_content,
        # Deterministic All-Image Retrieval milestone: the exhaustive
        # counterpart to teams_get_hosted_content -- call this ONCE (never
        # teams_get_hosted_content repeatedly) when the user's request
        # requires reviewing ALL of a message's images. Reads the
        # authoritative, already-discovered hosted_content_ids itself;
        # the model never enumerates individual ids for this case -- see
        # that tool's own module docstring for the full root-cause
        # rationale (real live validation proved model-driven iteration
        # over individual ids unreliable).
        teams_get_all_hosted_content,
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
    # A5 final corrective pass (Correction D): captures this turn's
    # trusted `known_applicability_facts` (if any) BEFORE the agent's
    # real turn runs, so its own first `knowledge_search` call already
    # sees a seeded ApplicabilityContext -- see evidence.py's own
    # docstring. ALWAYS returns None (never skips the real turn).
    before_agent_callback=capture_known_applicability_context,
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
    # Teams Image Vision corrective milestone: a LIST -- ADK's own
    # documented multi-callback mechanism (`LlmAgent.canonical_before_
    # model_callbacks`, verified against the installed 1.33.0 source) --
    # additive alongside the existing perf-timing callback, never
    # replacing it. NOTE: team_manager actually delegates to `_fast_path_
    # incident_manager` (direct_read_fast_path.py), a `.model_copy` that
    # explicitly OVERRIDES `before_model_callback` with its own list --
    # this base agent's own list matters for standalone `adk run`/test use
    # of `incident_manager` directly; the live-turn wiring that matters
    # for a real user turn is that module's own list, kept in sync with
    # this one.
    before_model_callback=[before_model_call("incident_manager"), inject_pending_hosted_content_image],
    after_model_callback=after_model_call("incident_manager"),
)

root_agent = incident_manager
