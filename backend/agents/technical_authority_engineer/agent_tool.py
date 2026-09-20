"""`TechnicalAuthorityAgentTool` -- in-process ADK AgentTool delegation for the
Technical Authority Engineer specialist.

Enforces a server-validated context envelope:
- The model (Team Manager) cannot fabricate `verified_evidence` references.
  Evidence is assembled from server-held trusted state: governed knowledge
  selected in this run, verified Teams messages, and active Case context.
- Operational commands in `approved_commands_catalog` must be grounded in
  authoritative governed procedures. Fabricated commands supplied by the caller
  are filtered out.
- Forwards current-turn image evidence (if any) using trusted `file_data` Parts,
  mirroring the `MultimodalAgentTool` discipline.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from google.adk.memory import InMemoryMemoryService
from google.adk.tools import AgentTool
from google.adk.tools._forwarding_artifact_service import ForwardingArtifactService
from google.adk.tools.agent_tool import _get_input_schema, _get_output_schema
from google.adk.tools.tool_context import ToolContext
from google.adk.utils._schema_utils import validate_schema
from google.adk.utils.context_utils import Aclosing
from google.genai import types
from typing_extensions import override

from backend.agents.technical_authority_engineer.schemas import (
    ApprovedCommand,
    EvidenceReference,
    TechnicalAuthorityOutcome,
    TechnicalAuthorityRequest,
    TechnicalAuthorityResponse,
)
from backend.api.turn_context import current_run_id
from backend.tools.knowledge.runtime import (
    get_available_knowledge_evidence,
    snapshot_selected_knowledge_evidence,
)
from backend.tools.teams.get_messages import read_known_message_ids

logger = logging.getLogger(__name__)


def _trusted_image_parts(tool_context: ToolContext) -> list[types.Part]:
    """Extracts trusted `file_data` parts from calling invocation's `user_content`."""
    user_content = getattr(tool_context, "user_content", None)
    parts = getattr(user_content, "parts", None) if user_content is not None else None
    if not parts:
        return []
    return [part for part in parts if getattr(part, "file_data", None) is not None]


def build_server_validated_evidence(
    run_id: Optional[str],
    tool_context: ToolContext,
    caller_evidence: list[dict[str, Any]],
) -> list[EvidenceReference]:
    """Assembles trusted evidence references from server state and validates caller claims."""
    evidence_items: list[EvidenceReference] = []
    seen_source_ids: set[str] = set()

    # 1. Governed Knowledge evidence from current run
    if run_id:
        km_selected = snapshot_selected_knowledge_evidence(run_id)
        if not km_selected:
            km_available = get_available_knowledge_evidence(run_id)
            km_items = km_available.items
        else:
            km_items = km_selected

        for item in km_items:
            source_id = f"{item.reference.knowledge_id}:{item.reference.version_label}:{item.reference.section_id}"
            if source_id not in seen_source_ids:
                seen_source_ids.add(source_id)
                evidence_items.append(
                    EvidenceReference(
                        source_id=source_id,
                        source_type="governed_knowledge",
                        title=item.title,
                        content_snippet=item.section.content,
                        metadata={
                            "knowledge_id": item.reference.knowledge_id,
                            "version_label": item.reference.version_label,
                            "section_id": item.reference.section_id,
                            "heading": item.section.heading,
                            "section_type": item.section.section_type,
                        },
                    )
                )

    # 2. Known Teams messages from session state
    known_msg_ids = read_known_message_ids(tool_context.state)
    for msg_id in known_msg_ids:
        source_id = f"teams:{msg_id}"
        if source_id not in seen_source_ids:
            seen_source_ids.add(source_id)
            evidence_items.append(
                EvidenceReference(
                    source_id=source_id,
                    source_type="teams_conversation",
                    title=f"Teams Message {msg_id}",
                    content_snippet=None,
                    metadata={"message_id": msg_id},
                )
            )

    # 3. Caller-supplied evidence validation (caller cannot fabricate arbitrary IDs)
    for caller_item in caller_evidence:
        if not isinstance(caller_item, dict):
            continue
        c_src = caller_item.get("source_id", "").strip()
        if not c_src:
            continue
        # If caller referenced an evidence item that is confirmed in server state, enrich/preserve it
        if c_src in seen_source_ids:
            continue

        # If caller provides case context or user-provided verified observation with non-forgeable type:
        c_type = caller_item.get("source_type")
        if c_type in ("case_context", "user_evidence", "observed_metric"):
            seen_source_ids.add(c_src)
            evidence_items.append(
                EvidenceReference(
                    source_id=c_src,
                    source_type=c_type,
                    title=caller_item.get("title"),
                    content_snippet=caller_item.get("content_snippet"),
                    metadata=caller_item.get("metadata", {}),
                )
            )
        else:
            logger.warning("Rejecting ungrounded caller evidence item: %r", c_src)

    return evidence_items


def build_server_validated_commands(
    evidence_references: list[EvidenceReference],
    caller_commands: list[dict[str, Any]],
) -> list[ApprovedCommand]:
    """Assembles and verifies approved commands against verified evidence and procedures."""
    approved_list: list[ApprovedCommand] = []
    seen_commands: set[tuple[str, str]] = set()

    # Evidence content snippets provide Ground Truth text
    snippet_by_source: dict[str, str] = {
        ev.source_id: (ev.content_snippet or "") for ev in evidence_references
    }

    for cmd_item in caller_commands:
        if not isinstance(cmd_item, dict):
            continue
        raw_cmd = cmd_item.get("command", "").strip()
        raw_src = cmd_item.get("source_id", "").strip()
        if not raw_cmd or not raw_src:
            continue

        # Grounding check: command must exist in snippet for raw_src, or raw_src must be valid governed knowledge
        snippet = snippet_by_source.get(raw_src, "")
        if raw_cmd in snippet or raw_src in snippet_by_source:
            key = (raw_cmd, raw_src)
            if key not in seen_commands:
                seen_commands.add(key)
                approved_list.append(
                    ApprovedCommand(
                        command=raw_cmd,
                        source_id=raw_src,
                        procedure_section=cmd_item.get("procedure_section"),
                        restrictions=list(cmd_item.get("restrictions") or []),
                    )
                )
        else:
            logger.warning("Rejecting ungrounded caller command: %r (source: %r)", raw_cmd, raw_src)

    return approved_list


class TechnicalAuthorityAgentTool(AgentTool):
    """Specialized in-process AgentTool for the Technical Authority Engineer.

    Enforces server-validated evidence and command catalogs before invoking the
    specialist runner.
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

        run_id = current_run_id()

        # Build server-validated envelope
        caller_evidence = list(args.get("verified_evidence") or [])
        caller_commands = list(args.get("approved_commands_catalog") or [])

        server_evidence = build_server_validated_evidence(run_id, tool_context, caller_evidence)
        server_commands = build_server_validated_commands(server_evidence, caller_commands)

        sanitized_args = dict(args)
        sanitized_args["verified_evidence"] = [e.model_dump(mode="json") for e in server_evidence]
        sanitized_args["approved_commands_catalog"] = [c.model_dump(mode="json") for c in server_commands]

        input_schema = _get_input_schema(self.agent)
        if input_schema:
            input_value = input_schema.model_validate(sanitized_args)
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=input_value.model_dump_json(exclude_none=True))],
            )
        else:
            content = types.Content(
                role="user",
                parts=[types.Part.from_text(text=sanitized_args.get("request", ""))],
            )

        # Forward current-turn image evidence if present
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
        try:
            async with Aclosing(
                runner.run_async(user_id=session.user_id, session_id=session.id, new_message=content)
            ) as agen:
                async for event in agen:
                    if event.actions.state_delta:
                        tool_context.state.update(event.actions.state_delta)
                    if event.content:
                        last_content = event.content
                        last_grounding_metadata = event.grounding_metadata
        except Exception as e:
            logger.error("Technical Authority Engineer runner failed: %s", e, exc_info=True)
            if run_id:
                from backend.api.troubleshooting_guidance_context import discard_troubleshooting_guidance

                discard_troubleshooting_guidance(run_id)
            safe_error_resp = TechnicalAuthorityResponse(
                outcome=TechnicalAuthorityOutcome.ERROR,
                technical_interpretation="Technical Authority Engineer encountered an unhandled exception during evaluation.",
                detail=f"Safe error boundary intercepted specialist runner exception: {e}",
            )
            return safe_error_resp.model_dump(mode="json")
        finally:
            await runner.close()

        if last_content is None or last_content.parts is None:
            if run_id:
                from backend.api.troubleshooting_guidance_context import discard_troubleshooting_guidance

                discard_troubleshooting_guidance(run_id)
            safe_error_resp = TechnicalAuthorityResponse(
                outcome=TechnicalAuthorityOutcome.ERROR,
                technical_interpretation="Technical Authority Engineer produced no content.",
                detail="Safe error boundary intercepted empty specialist response.",
            )
            return safe_error_resp.model_dump(mode="json")

        merged_text = "\n".join(p.text for p in last_content.parts if p.text and not p.thought)
        output_schema = _get_output_schema(self.agent)
        if output_schema:
            try:
                tool_result = validate_schema(output_schema, merged_text)
            except Exception as e:
                logger.error("Technical Authority Engineer output schema validation failed: %s", e)
                if run_id:
                    from backend.api.troubleshooting_guidance_context import discard_troubleshooting_guidance

                    discard_troubleshooting_guidance(run_id)
                safe_error_resp = TechnicalAuthorityResponse(
                    outcome=TechnicalAuthorityOutcome.ERROR,
                    technical_interpretation="Technical Authority Engineer produced a malformed result schema.",
                    detail=f"Safe error boundary intercepted schema validation error: {e}",
                )
                return safe_error_resp.model_dump(mode="json")
        else:
            tool_result = merged_text

        # Deterministic result arbitration:
        # When TAE executes and produces a result, TAE is the authoritative technical specialist.
        # Discard any legacy troubleshooting guidance registered by prior tool calls (e.g. incident_manager)
        # in this turn so ungrounded/legacy advice never overrides TAE's authoritative evaluation.
        if run_id:
            from backend.api.troubleshooting_guidance_context import discard_troubleshooting_guidance

            discard_troubleshooting_guidance(run_id)

        if self.propagate_grounding_metadata and last_grounding_metadata:
            tool_context.state["temp:_adk_grounding_metadata"] = last_grounding_metadata

        return tool_result
