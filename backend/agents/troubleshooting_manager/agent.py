"""ADK specialist agent: troubleshooting_manager (Phase 6A.9).

NOT bound into `team_manager` in this milestone -- there is no
`AgentTool(agent=troubleshooting_manager)` anywhere in this codebase yet
(that wiring is 6A.10's own explicit scope, docs/INTELLIGENCE_
ARCHITECTURE.md §2/§15). This module exists so the agent identity itself
-- model, instruction, output schema, trust callbacks -- is defined and
directly/internally invocable (`runtime.py`) for this milestone's own
controlled validation.

`tools=[]`: structurally, not just by prompt convention, this agent
cannot call Teams, Knowledge, Experience Memory, or any other capability
-- every input it reasons over was already deterministically resolved by
`skill_resolution.py`/`experience_support.py`/`runtime.py` before the
model is ever invoked (mirrors the existing `_SYNTHESIS_ONLY_INCIDENT_
MANAGER`/presentation_team_manager precedent of "remove the capability
from the schema, don't just police it via prompt wording").

`disallow_transfer_to_parent`/`disallow_transfer_to_peers` mirror
`incident_manager`'s own defense-in-depth choice (agent.py) -- this
agent is never placed in anyone's `sub_agents` list in this build, so no
`transfer_to_agent` tool would be auto-injected regardless, but the
"this agent must never take over the end-user conversation" invariant is
made explicit in code anyway.

`output_schema=TroubleshootingManagerResponse` guarantees the model's
final reply conforms to the structured contract rather than free-form
prose -- the SAME mechanism `incident_manager` already relies on.
`input_schema` is deliberately NOT set: this agent is not wrapped in an
`AgentTool` in this milestone, so there is no caller whose function-call
schema would need one (see `runtime.py`'s own docstring for how a turn's
trusted Intelligence Package actually reaches the model: as rendered text
inside the turn's own `Content`, never as ADK structured tool arguments).
"""
from __future__ import annotations

from google.adk.agents import Agent

from backend.agents.troubleshooting_manager.prompts import TROUBLESHOOTING_MANAGER_INSTRUCTION
from backend.agents.troubleshooting_manager.schemas import TroubleshootingManagerResponse
from backend.api.perf_timing import after_model_call, before_model_call
from backend.config.settings import get_settings, get_shared_llm

_settings = get_settings()

troubleshooting_manager = Agent(
    name="troubleshooting_manager",
    model=get_shared_llm(_settings.gemini_model),
    description=(
        "Troubleshooting specialist. Given already-trusted current context, "
        "already-selected governed evidence, an applicable methodology, and "
        "relevant historical experience, produces a structured, grounded "
        "next-step troubleshooting assessment -- never executes anything, "
        "never invents context or evidence."
    ),
    instruction=TROUBLESHOOTING_MANAGER_INSTRUCTION,
    tools=[],
    output_schema=TroubleshootingManagerResponse,
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
    before_model_callback=before_model_call("troubleshooting_manager"),
    after_model_callback=after_model_call("troubleshooting_manager"),
)

root_agent = troubleshooting_manager
