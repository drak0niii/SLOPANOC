"""`MultimodalAgentTool` -- POST-5.1 B6's answer to "why doesn't Incident
Manager see the user's image", and the fix.

============================================================================
THE GAP, VERIFIED AGAINST THE INSTALLED ADK 1.33.0 SOURCE (not assumed)
============================================================================

`google.adk.tools.agent_tool.AgentTool.run_async` (`tools/agent_tool.py`)
builds the nested agent's ENTIRE user `Content` from exactly one place:

    input_value = input_schema.model_validate(args)
    content = types.Content(
        role='user',
        parts=[types.Part.from_text(text=input_value.model_dump_json(...))],
    )

`args` is team_manager's own MODEL-GENERATED tool-call arguments (i.e.
`IncidentManagerRequest`'s fields, as Gemini chose to fill them in) --
there is no code path in the base class that ever looks at anything else,
including the CALLING invocation's own original multimodal `Content`. So
a user's attached image, already visible to team_manager's own top-level
model call, is silently NEVER forwarded into Incident Manager's nested
Runner call at all -- team_manager can describe what it saw in prose (an
explicitly forbidden bridge -- CLAUDE.md B6 section 78, "NO TEAM-MANAGER
PARAPHRASING BRIDGE"), but the specialist itself never receives the
pixels.

============================================================================
THE FIX -- OPTION A: a narrow, supported subclass, not an ADK-internals hack
============================================================================

`AgentTool` is a public, exported, `@override`-annotated `BaseTool`
subclass -- exactly the kind of class this codebase's own established
`.model_copy` pattern (agent.py's `presentation_team_manager`, direct_
read_fast_path.py's `_fast_path_incident_manager`, read_continuation_
execution.py's `_CONTINUATION_INCIDENT_MANAGER`/`_SYNTHESIS_ONLY_
INCIDENT_MANAGER`) already treats as safe to specialize for a narrow,
additive purpose. `run_async` has no smaller extension point (verified: no
`_build_content`/`_build_nested_content` sub-method exists to override
alone -- the whole method is one linear block), so THIS is the narrowest
possible override: every single line below is either (a) copied verbatim
from the base implementation (session/runner construction, event-driving
loop, state-delta forwarding, output-schema validation, cleanup -- all
byte-for-byte unchanged) or (b) the one new step, appending trusted image
`Part`s to `content` before the nested Runner ever starts. `_get_
declaration`/`populate_name`/`from_config`/`name`/`description` are all
inherited UNCHANGED -- team_manager's own tool-calling schema for
`incident_manager` is byte-for-byte identical to before this pass.

WHERE THE TRUSTED IMAGE `Part`s COME FROM -- `tool_context.user_content`,
a PUBLIC, documented property (`google.adk.agents.readonly_context.
ReadonlyContext.user_content`: "The user content that started this
invocation. READONLY field." -- `ToolContext` is a bare alias for
`Context(ReadonlyContext)`, verified against `tools/tool_context.py`/
`agents/context.py`). Verified against `runners.py`
(`Runner.run_async`/`_new_invocation_context`): `invocation_context.
user_content` is set to exactly the `new_message` argument THAT SAME
top-level `Runner.run_async` call received -- i.e., for a live
`incident_manager` tool call, `tool_context._invocation_context` IS
team_manager's own, single, top-level `InvocationContext` for this turn
(the SAME one `AgentTool.run_async` already reads `.app_name`/`.user_id`/
`.plugin_manager`/`.credential_service` from), so `tool_context.
user_content` is EXACTLY `backend/api/chat_service.py`'s own trusted
`content` object for this turn -- built entirely from application code
(B5's `prepare_attachments_for_turn` + `Part.from_uri`), never from
anything team_manager's model supplied. This requires NO new run-scoped
registry for this call site: ADK's own per-invocation object graph
already gives exactly the isolation/no-cross-user-leakage/no-cleanup-
needed properties instruction section 10/11 asks for -- a DIFFERENT
concurrent turn has a DIFFERENT `InvocationContext` object entirely, and
nothing here is ever written to a shared/global structure.

ONLY `file_data` PARTS ARE EVER COPIED -- never a text part (team_
manager's own message text stays exactly where it already was, inside the
structured JSON `IncidentManagerRequest` text part; copying user_content's
OWN text part too would duplicate/leak team_manager's raw prompt text into
the nested call) and never an `inline_data`/bytes part (this codebase
never constructs one for a chat image -- B5's own locked rule -- but this
filter is a second, structural belt-and-suspenders guarantee that even if
one somehow existed it could never reach a nested Runner call through this
class). This also means: for a text-only turn, `user_content.parts` has
no `file_data` parts at all, so `content.parts` ends up IDENTICAL to what
base `AgentTool.run_async` would have built -- zero behavior change for
the text-only case (instruction section 18, "text-only fast-path
regression").

IMAGE ORDER PRESERVED: `user_content.parts` is iterated in the exact order
`chat_service.py` built it (B5's own locked, client-order-preserving
construction) -- never re-sorted.

NO GCS URI IN TEXT: only `Part.from_uri`'s own `file_data.file_uri` field
carries the URI -- it is never read, logged, or written into the
structured-request JSON text part built above.
"""
from __future__ import annotations

from typing import Any

from google.adk.memory import InMemoryMemoryService
from google.adk.tools import AgentTool
from google.adk.tools._forwarding_artifact_service import ForwardingArtifactService
from google.adk.tools.agent_tool import _get_input_schema, _get_output_schema
from google.adk.tools.tool_context import ToolContext
from google.adk.utils._schema_utils import validate_schema
from google.adk.utils.context_utils import Aclosing
from google.genai import types
from typing_extensions import override


def _trusted_image_parts(tool_context: ToolContext) -> list[types.Part]:
    """Extracts, in order, every `file_data`-bearing `Part` from the
    CALLING invocation's own trusted `user_content` -- see this module's
    docstring for exactly why that is safe/authoritative. Returns `[]` for
    a text-only turn, or when `tool_context.user_content` is absent
    entirely (e.g. a standalone `adk run`/test invocation with no
    multimodal content) -- both ordinary, safe outcomes.
    """
    user_content = getattr(tool_context, "user_content", None)
    parts = getattr(user_content, "parts", None) if user_content is not None else None
    if not parts:
        return []
    return [part for part in parts if getattr(part, "file_data", None) is not None]


class MultimodalAgentTool(AgentTool):
    """Drop-in replacement for `AgentTool` -- same `name`, `description`,
    `_get_declaration`, `from_config`; the ONLY behavioral difference is
    that the nested agent's `Content` also carries this turn's trusted
    current-turn image evidence, if any (see module docstring).
    """

    @override
    async def run_async(
        self,
        *,
        args: dict[str, Any],
        tool_context: ToolContext,
    ) -> Any:
        from google.adk.runners import Runner
        from google.adk.sessions.in_memory_session_service import InMemorySessionService

        if self.skip_summarization:
            tool_context.actions.skip_summarization = True

        input_schema = _get_input_schema(self.agent)
        if input_schema:
            input_value = input_schema.model_validate(args)
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=input_value.model_dump_json(exclude_none=True))],
            )
        else:
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=args["request"])],
            )

        # THE ONE NEW STEP (see module docstring): append this turn's
        # trusted image evidence, in order, after the structured-request
        # text part. A no-op (identical `content` to the base
        # implementation) for a text-only turn.
        content.parts.extend(_trusted_image_parts(tool_context))

        invocation_context = tool_context._invocation_context
        parent_app_name = invocation_context.app_name if invocation_context else None
        child_app_name = parent_app_name or self.agent.name
        plugins = (
            tool_context._invocation_context.plugin_manager.plugins
            if self.include_plugins
            else None
        )
        runner = Runner(
            app_name=child_app_name,
            agent=self.agent,
            artifact_service=ForwardingArtifactService(tool_context),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
            credential_service=tool_context._invocation_context.credential_service,
            plugins=plugins,
        )

        state_dict = {
            k: v for k, v in tool_context.state.to_dict().items() if not k.startswith("_adk")
        }
        session = await runner.session_service.create_session(
            app_name=child_app_name,
            user_id=tool_context._invocation_context.user_id,
            state=state_dict,
        )

        last_content = None
        last_grounding_metadata = None
        async with Aclosing(
            runner.run_async(user_id=session.user_id, session_id=session.id, new_message=content)
        ) as agen:
            async for event in agen:
                if event.actions.state_delta:
                    tool_context.state.update(event.actions.state_delta)
                if event.content:
                    last_content = event.content
                    last_grounding_metadata = event.grounding_metadata

        await runner.close()

        if last_content is None or last_content.parts is None:
            return ""
        merged_text = "\n".join(p.text for p in last_content.parts if p.text and not p.thought)
        output_schema = _get_output_schema(self.agent)
        if output_schema:
            tool_result = validate_schema(output_schema, merged_text)
        else:
            tool_result = merged_text

        if self.propagate_grounding_metadata and last_grounding_metadata:
            tool_context.state["temp:_adk_grounding_metadata"] = last_grounding_metadata

        return tool_result
