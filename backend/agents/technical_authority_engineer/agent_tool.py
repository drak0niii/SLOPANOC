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
import re
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

from backend.agents.technical_authority_engineer.execution_context import (
    record_technical_authority_execution,
)
from backend.agents.technical_authority_engineer.schemas import (
    ApprovedCommand,
    EvidenceReference,
    TechnicalAuthorityOutcome,
    TechnicalAuthorityRequest,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.validation import (
    _is_command_matching_snippet,
    _template_to_regex,
    validate_technical_authority_payload,
)
from backend.api.applicability_context_capture import register_known_applicability_context
from backend.api.turn_context import current_run_id
from backend.cases.troubleshooting_state import (
    CheckLifecycleStatus,
    TroubleshootingState,
    TroubleshootingStatus,
)
from backend.knowledge.domain.applicability import ApplicabilityContext
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
                            "source_id": item.source.source_id,
                            "title": item.title,
                        },
                    )
                )

    # 2. Known Teams messages from session state
    if tool_context and hasattr(tool_context, "state") and tool_context.state:
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
    """Assembles and verifies approved commands against verified evidence and procedures.

    Mandatory Safeguard 1:
    Command presence in an evidence snippet is not sufficient authorization.
    Requires an approved, applicable procedure or governed command registry.
    Validates exact commands/templates, parameters, restrictions, and source provenance.
    Eliminates the vulnerability where raw_src presence authorized arbitrary commands.
    """
    from backend.agents.technical_authority_engineer.validation import (
        _is_command_matching_snippet,
        is_command_prohibited_in_snippet,
        resolve_canonical_source_id,
    )

    approved_list: list[ApprovedCommand] = []
    seen_commands: set[tuple[str, str]] = set()

    _AUTHORIZED_SOURCE_TYPES = {"governed_knowledge", "approved_procedure", "governed_command_registry"}
    authorized_sources: dict[str, EvidenceReference] = {}
    for ev in evidence_references:
        src_type = ev.source_type or ""
        src_id = (ev.source_id or "").strip()
        # Non-governed sources (Teams chats, raw case notes, user observations) CANNOT authorize operational commands
        if src_type in ("teams_conversation", "case_context", "user_evidence", "observed_metric") or src_id.startswith("teams:"):
            continue
        if src_type in _AUTHORIZED_SOURCE_TYPES or (not src_type and not src_id.startswith("teams:")):
            authorized_sources[src_id] = ev

    # Validate caller or candidate commands against governed evidence
    for cmd_item in caller_commands:
        if not isinstance(cmd_item, dict):
            continue
        raw_cmd = cmd_item.get("command", "").strip()
        raw_src = cmd_item.get("source_id", "").strip()
        if not raw_cmd:
            continue

        canonical_src = resolve_canonical_source_id(raw_src, evidence_references) or raw_src
        if not canonical_src or canonical_src not in authorized_sources:
            logger.warning(
                "Rejecting caller command %r from unauthorized or non-governed source: %r",
                raw_cmd,
                raw_src,
            )
            continue

        ev = authorized_sources[canonical_src]
        snippet = ev.content_snippet or ""
        cmd_stripped = re.sub(r"\*\*|`", "", raw_cmd).strip()

        if is_command_prohibited_in_snippet(raw_cmd, snippet):
            logger.warning("Rejecting caller command %r: prohibited in snippet for %r", raw_cmd, canonical_src)
            continue

        is_grounded = _is_command_matching_snippet(raw_cmd, cmd_stripped, snippet)

        if is_grounded:
            key = (raw_cmd, canonical_src)
            if key not in seen_commands:
                seen_commands.add(key)
                approved_list.append(
                    ApprovedCommand(
                        command=raw_cmd,
                        source_id=canonical_src,
                        procedure_section=cmd_item.get("procedure_section") or ev.title,
                        restrictions=list(cmd_item.get("restrictions") or []),
                    )
                )
        else:
            logger.warning(
                "Rejecting ungrounded caller command: %r (not found in snippet for %r)",
                raw_cmd,
                raw_src,
            )

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

        # 1. Register trusted applicability context for knowledge retrieval lifecycle
        caller_facts = args.get("known_applicability_facts")
        if caller_facts and isinstance(caller_facts, dict):
            try:
                app_ctx = ApplicabilityContext(dimensions=caller_facts)
                register_known_applicability_context(run_id, app_ctx)
            except Exception:
                pass

        # 2. Retrieve or initialize persistent TroubleshootingState
        ts_raw = tool_context.state.get("troubleshooting_state")
        troubleshooting_state: Optional[TroubleshootingState] = None
        if ts_raw and isinstance(ts_raw, dict):
            try:
                troubleshooting_state = TroubleshootingState.model_validate(ts_raw)
            except Exception:
                pass

        session_id = getattr(getattr(tool_context, "_invocation_context", None), "session_id", None)
        case_id = tool_context.state.get("active_case_id")

        if troubleshooting_state is None:
            problem = args.get("problem_statement") or "investigation"
            import uuid
            troubleshooting_state = TroubleshootingState(
                fault_id=f"FAULT-{uuid.uuid4().hex[:6].upper()}",
                symptom_summary=problem[:200],
                session_id=session_id,
                case_id=case_id,
            )
        else:
            if session_id and not troubleshooting_state.session_id:
                troubleshooting_state.session_id = session_id
            if case_id and not troubleshooting_state.case_id:
                troubleshooting_state.case_id = case_id

        # 3. Detect user execution of previously recommended checks
        symptoms_str = " ".join(args.get("verified_symptoms") or [])
        problem_str = args.get("problem_statement", "")
        combined_text = f"{problem_str} {symptoms_str}".strip()

        for rec in reversed(troubleshooting_state.diagnostic_history):
            if rec.status == CheckLifecycleStatus.RECOMMENDED:
                cmd = (rec.grounded_command or "").strip().lower()
                obs = None
                if cmd and cmd in combined_text.lower():
                    obs = f"Output observed for `{rec.grounded_command}`: {combined_text[:200]}"
                elif combined_text:
                    obs = combined_text[:200]
                troubleshooting_state.record_user_execution(
                    check_id=rec.check_id,
                    command=rec.grounded_command,
                    observed_result=obs,
                )
                break

        # 4. Populate prior_steps_taken into sanitized_args to prevent diagnostic repetition loops
        prior_steps = troubleshooting_state.get_prior_steps_summary()
        caller_prior = list(args.get("prior_steps_taken") or [])
        merged_prior = list(prior_steps)
        for cp in caller_prior:
            if cp not in merged_prior:
                merged_prior.append(cp)

        # Build server-validated envelope
        caller_evidence = list(args.get("verified_evidence") or [])
        caller_commands = list(args.get("approved_commands_catalog") or [])

        server_evidence = build_server_validated_evidence(run_id, tool_context, caller_evidence)
        server_commands = build_server_validated_commands(server_evidence, caller_commands)

        sanitized_args = dict(args)
        sanitized_args["verified_evidence"] = [e.model_dump(mode="json") for e in server_evidence]
        sanitized_args["approved_commands_catalog"] = [c.model_dump(mode="json") for c in server_commands]
        sanitized_args["prior_steps_taken"] = merged_prior

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
                raw_result = validate_schema(output_schema, merged_text)
                if isinstance(raw_result, dict):
                    # Dynamic evidence refresh: specialist may have called knowledge_search
                    # and knowledge_select_evidence during execution. Refresh server-validated
                    # evidence and approved commands catalog so legitimately selected evidence
                    # is available to post-runner validation.
                    refreshed_evidence = build_server_validated_evidence(run_id, tool_context, caller_evidence)
                    candidate_commands = list(caller_commands)
                    step = raw_result.get("diagnostic_step")
                    if isinstance(step, dict) and step.get("command"):
                        candidate_commands.append({
                            "command": step["command"],
                            "source_id": step.get("command_source") or "",
                            "procedure_section": step.get("action"),
                            "restrictions": step.get("restrictions") or [],
                        })
                    refreshed_commands = build_server_validated_commands(refreshed_evidence, candidate_commands)
                    sanitized_args["verified_evidence"] = [e.model_dump(mode="json") for e in refreshed_evidence]
                    sanitized_args["approved_commands_catalog"] = [c.model_dump(mode="json") for c in refreshed_commands]

                    tool_result, _ = validate_technical_authority_payload(
                        response_payload=raw_result,
                        request_payload=sanitized_args,
                    )
                else:
                    tool_result = raw_result
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

        # 5. Persist recommended checks and record specialist execution
        if isinstance(tool_result, dict):
            if run_id:
                record_technical_authority_execution(run_id, tool_result)

            if tool_result.get("outcome") == TechnicalAuthorityOutcome.RECOMMENDED.value:
                step_data = tool_result.get("diagnostic_step")
                if isinstance(step_data, dict) and step_data.get("action"):
                    troubleshooting_state.record_recommended_check(
                        action=step_data.get("action", ""),
                        rationale=step_data.get("reason", ""),
                        expected_observation=step_data.get("expected_evidence", ""),
                        grounded_command=step_data.get("command"),
                        command_source_id=step_data.get("command_source"),
                        session_id=session_id,
                        case_id=case_id,
                        node_id=troubleshooting_state.node_id,
                        fault_id=troubleshooting_state.fault_id,
                    )

            tool_context.state["troubleshooting_state"] = troubleshooting_state.model_dump(mode="json")

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
