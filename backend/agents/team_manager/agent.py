"""ADK root/orchestrator agent: team_manager.

Owns the user-facing conversation (docs/AGENT_CONTRACT.md #6). Delegates
all Teams-domain work to incident_manager and is the only agent whose
output is shown to the user for this turn.

WHY AgentTool INSTEAD OF NATIVE `sub_agents` TRANSFER -- see
docs/AGENT_CONTRACT.md #4 for the full rationale, verified against the
installed ADK (1.33.0) source. Summary: setting `sub_agents` on an
`LlmAgent` auto-injects a `transfer_to_agent` tool
(google.adk.flows.llm_flows.agent_transfer) whose effect is to hand the
active, end-user-facing turn to the named sub-agent -- a genuine
conversational hand-off, not a same-turn call/return. That conflicts with
the frozen contract's requirement that incident_manager "must not
communicate with the user directly, in any form" and that team_manager
alone authors the final response.

`google.adk.tools.AgentTool` is documented as: "allows an agent to be
called as a tool ... the agent's output is returned as the tool's
result" -- a same-turn call/return via a nested Runner/session, with no
hand-off of end-user conversation ownership. That is what
docs/AGENT_CONTRACT.md #8's delegation protocol actually specifies (a
synchronous request/response exchange, "not a new user-facing Message,
not a new Chat turn, and not a network request"), so incident_manager is
bound into team_manager's `tools=[...]` via AgentTool, not via
`sub_agents=[...]`. Both agents still run in the same single ADK
application/runtime, in-process, with no A2A and no separate services.

`after_tool_callback=sync_incident_manager_result_to_state` is the fix for
the cross-turn "which chat/evidence" regression: it deterministically
persists the resolved chat and validated evidence from each
`incident_manager` call into team_manager's own session state, which
`{selected_teams_chat_topic?}`/`{last_teams_evidence?}` in
TEAM_MANAGER_INSTRUCTION then read back on later turns. See
state_sync.py's module docstring for the full rationale (verified against
the installed ADK 1.33.0 source before implementing, per instruction).

`instruction=team_manager_instruction_provider` (Phase 4D): an ADK
`InstructionProvider` (a plain async callable, not the static
`TEAM_MANAGER_INSTRUCTION` string directly) -- it renders that same
string via the same `{var?}` session-state templating as before, then
appends a fresh, deterministically-built Case-context block when this
session is linked to a Case. See case_context.py's module docstring for
why this mechanism was chosen over the alternatives, verified against the
installed ADK 1.33.0 source. `record_case_analysis` (case_tools.py) is
the one, restricted Case-write capability -- see its own module
docstring.

`record_conversation_target` (conversation_target.py, semantic-scope bug
fix): a small, deterministic declaration tool -- team_manager calls it,
within the SAME reasoning turn, to state which of three semantic targets
(the current SLOPANOC conversation itself, the selected external Teams
conversation, or an explicitly-named external Teams conversation) a
"summarize/recap this conversation" style request actually refers to,
before proceeding. See that module's own docstring for the full
rationale -- not a second agent, not a second LLM call, and not any form
of natural-language pattern matching.

`before_tool_callback=enforce_read_continuation`
(read_continuation_enforcement.py, production hardening pass): for the one
turn that resumes a chosen `SelectionCard` read, deterministically
overrides the `incident_manager` tool call's `chat_topic`/`question`/
`requested_time_range` arguments (and forwards the authoritative chat id
via `temp:`-prefixed session state) from a single-use
`ResolvedReadContinuation`, so team_manager's model never re-decides
destination/operation/focus/time-range for that call. See that module's
own docstring for the full ADK-source-verified rationale.

P4B.3 -- `incident_manager_tool` wraps `_fast_path_incident_manager`
(direct_read_fast_path.py), not the base `incident_manager` agent
directly. Same name, same `IncidentManagerRequest`/`IncidentManagerResponse`
schema, same tools, same instruction -- team_manager's own tool-calling
surface and this file's `tools=[...]` wiring are unaffected. The variant
only adds an internal interception point so a FIRST-TIME direct/exact
Teams read that resolves to one unique chat converges into the same
deterministic fast pipeline P4B.1/P4B.2 already built for post-selection
continuations, in the SAME user turn -- see that module's own docstring
for the full ADK-source-verified mechanism.
"""
from __future__ import annotations

from google.adk.agents import Agent
from google.adk.tools import AgentTool

from backend.agents.team_manager.case_context import team_manager_instruction_provider
from backend.agents.team_manager.direct_read_fast_path import _fast_path_incident_manager
from backend.agents.team_manager.case_tools import record_case_analysis
from backend.agents.team_manager.conversation_target import record_conversation_target
from backend.agents.team_manager.read_continuation_enforcement import enforce_read_continuation
from backend.agents.team_manager.source_requirements import record_source_requirements
from backend.agents.team_manager.selection_delegation_guard import (
    block_repeated_delegation_after_selection_needed,
    record_selection_needed,
)
from backend.agents.team_manager.state_sync import sync_incident_manager_result_to_state
from backend.api.perf_timing import after_model_call, before_model_call
from backend.config.settings import get_settings, get_shared_llm

_settings = get_settings()

incident_manager_tool = AgentTool(agent=_fast_path_incident_manager)

team_manager = Agent(
    name="team_manager",
    # Latency pass: a process-lifetime-shared `BaseLlm` instance rather
    # than a bare model string -- see `get_shared_llm`'s own docstring
    # (config/settings.py) for the ADK-source-verified rationale (avoids
    # rebuilding the underlying model client from scratch before every
    # single model call).
    model=get_shared_llm(_settings.gemini_model),
    description=(
        "SLOPANOC's orchestrator. Owns the user conversation, asks only "
        "for missing information, and delegates Teams-domain work to the "
        "incident_manager specialist."
    ),
    instruction=team_manager_instruction_provider,
    tools=[incident_manager_tool, record_case_analysis, record_conversation_target, record_source_requirements],
    # R3 FIX (correctness-regression pass): a LIST of callbacks -- ADK's
    # own documented multi-callback mechanism (verified against the
    # installed 1.33.0 source, `flows/llm_flows/functions.py`'s `_run_
    # with_trace`: each entry in `canonical_before_tool_callbacks`/
    # `canonical_after_tool_callbacks` runs in order; the first `before_
    # tool_callback` to return non-`None` short-circuits the rest AND the
    # real tool call) -- `block_repeated_delegation_after_selection_
    # needed` structurally forbids a second `incident_manager` delegation
    # in the same turn once an earlier one already returned "selection_
    # needed" (see selection_delegation_guard.py's own module docstring).
    before_tool_callback=[enforce_read_continuation, block_repeated_delegation_after_selection_needed],
    after_tool_callback=[sync_incident_manager_result_to_state, record_selection_needed],
    # Latency-diagnosis pass: correlated model-call timing (see perf_
    # timing.py's own module docstring, "MODEL-CALL INSTRUMENTATION") --
    # ADK's own documented before/after-model-callback extension points,
    # never a hand-rolled substitute. Logs only run_id/agent/index/
    # duration/response-shape/token-counts -- never a prompt, message, or
    # model output.
    before_model_callback=before_model_call("team_manager"),
    after_model_callback=after_model_call("team_manager"),
)

presentation_team_manager = team_manager.model_copy(
    update={
        "tools": [],
        "before_tool_callback": None,
        "after_tool_callback": None,
    }
)
"""R1 FIX (correctness-regression pass): the STRUCTURAL half of "trusted
result presentation mode" -- prompt wording alone (the `TEAM_MANAGER_
TRUSTED_RESULT_INSTRUCTION` text P4A already introduced, telling the model
not to delegate) was proven, live, to be insufficient: Gemini ignored it
and called `incident_manager` again anyway, because the tool was still
present in `team_manager.tools` for that turn. This is the SAME agent
role/identity ("team_manager" -- same `name`, same model, same `before_
model_callback`/`after_model_callback` so P2's instrumentation keeps
attributing these calls to "team_manager" unchanged, same `instruction`
callable), a `.model_copy` (standard pydantic API, `Agent`/`LlmAgent` is a
plain `BaseModel` -- already relied on elsewhere in this codebase, e.g.
`_CONTINUATION_INCIDENT_MANAGER` in read_continuation_execution.py) with
ONLY `tools=[]` (ideally empty per instruction, not merely narrowed) and
both tool callbacks cleared (dead weight with no tools to intercept, and
`enforce_read_continuation`/`sync_incident_manager_result_to_state` only
ever act on an `incident_manager` call that can no longer happen here).

With an empty `tools=[]`, ADK's function-calling schema sent to Gemini for
this Runner contains NO callable functions at all -- there is no code path,
prompt-compliant or not, through which this specific Runner invocation
could ever emit a `record_conversation_target` or `incident_manager`
function call. This mirrors `_CONTINUATION_INCIDENT_MANAGER`'s own,
already-proven "remove the tool from the schema, not just the prompt"
fix for the analogous P1 incident (teams_list_chats).

MODE SELECTION IS SERVER-TRUSTED, NEVER MODEL-CHOSEN (R1.1): which of
`team_manager`/`presentation_team_manager` a turn's `Runner` uses is
decided entirely by `chat_service.py`, from `pending_read_continuation`
(itself derived only from `pop_read_continuation`'s own server-side,
single-use, session-scoped state -- never user text, never model output).
The model itself never sees or chooses between these two agent objects.
`team_manager_instruction_provider` (case_context.py) independently makes
the SAME determination from `PENDING_SPECIALIST_RESULT_STATE_KEY` (which
`chat_service.py` writes ONLY via the same validated `TrustedSpecialistResult`
path, gated by `validate_trusted_envelope_for_run`'s same-run-id check) --
so even if these two signals were ever to disagree, the INSTRUCTION half
already refuses to reference anything for an empty/absent result, and the
TOOLS half here independently, structurally forbids delegation regardless
of what the instruction says. Two independent enforcement layers, not one.
"""

root_agent = team_manager
