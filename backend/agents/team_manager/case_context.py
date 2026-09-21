"""Dynamic, per-invocation Case-context injection for team_manager's
instruction (Phase 4D, instruction section 22).

MECHANISM SELECTED, AND WHY (verified against the installed ADK 1.33.0
source before choosing, per instruction -- "Do not rely on undocumented/
private ADK internals"): `google.adk.agents.llm_agent.LlmAgent.instruction`
accepts `Union[str, InstructionProvider]`, where `InstructionProvider =
Callable[[ReadonlyContext], Union[str, Awaitable[str]]]`
(`llm_agent.py`). `LlmAgent.canonical_instruction` awaits this callable
once per invocation when `instruction` is not a plain string
(`instruction = self.instruction(ctx); if inspect.isawaitable(instruction):
instruction = await instruction`) -- i.e. option 1 in the instruction's
preferred-order list ("supported invocation/transient state") turned out,
on inspection, to require piggybacking on ADK's internal `temp:`-prefixed
state-delta trimming machinery (`_apply_temp_state`/
`_trim_temp_delta_state`, private methods with no documented public
contract for pre-seeding); option 2 (this file) is fully public API,
documented in `llm_agent.py` itself, and gives complete control with no
risk of the snapshot ever leaking into persisted state. `ReadonlyContext`
(`readonly_context.py`) exposes exactly what's needed: `.user_id`,
`.session` (so `.session.id` for the session id), and `.state` (a
read-only view of the CURRENT session state, used only for the
non-authoritative `active_case_id` hint -- see below).

FRESH EVERY TURN (instruction section 21): ADK calls this function anew
for every single invocation -- nothing here is cached across turns, so a
Case context update from one user is visible on another user's very next
turn without any special invalidation logic.

NEVER PERSISTED (instruction section 22): this function only builds and
RETURNS a string that becomes part of the prompt sent to Gemini for this
one turn. It never writes anything to `ctx.state`, never calls
`append_event`, and is never invoked anywhere near
`session_service.persist_state_delta`. The full `CaseContextSnapshot` is
never added to `session.state` and never appended to conversation
history -- only the already-existing final response text becomes a
persisted `Event`, exactly as before this milestone.

ACTIVE-CASE HINT IS NOT AUTHORIZATION (instruction section 23):
`ctx.state.get("active_case_id")` is read only to know WHICH case to look
up -- `CaseService.get_case`/`get_context_items` independently
re-verify that `ctx.user_id` is still a member before anything is
included. If that hint is stale (case unlinked/membership revoked through
another path since the hint was written), the lookup raises `not_found`,
which this function treats as "no case context available" -- never an
error that aborts the turn.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.utils.instructions_utils import inject_session_state

from backend.agents.team_manager.prompts import (
    CASE_CONTEXT_TEAM_MANAGER_ADDENDUM,
    TEAM_MANAGER_INSTRUCTION,
    TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION,
)
from backend.agents.team_manager.read_continuation_presentation import PENDING_SPECIALIST_RESULT_STATE_KEY
from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY
from backend.cases.schemas import CaseContextSnapshot
from backend.cases.service import get_case_service
from backend.cases.snapshot import build_case_context_snapshot
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.config.settings import get_settings
from backend.context.assembly import ContextDomain, ContextEngineeringBroker, ContextItem
from backend.gateway.safe_error import SafeErrorException

TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM = """
TECHNICAL AUTHORITY ENGINEER DELEGATION (Phase 6A):
When the user asks for technical troubleshooting, diagnosis of a fault or issue, or next steps to resolve a problem:
- Delegate technical fault interpretation and diagnostic step recommendations EXCLUSIVELY to the `technical_authority_engineer` specialist tool.
- Use `incident_manager` strictly for operational retrieval (Teams messages, incident chat history, visual evidence) and Teams write actions. Do NOT delegate technical diagnostic recommendations, troubleshooting next steps, or procedural command next steps to `incident_manager`.
- Never set `chat_topic` or delegate to `incident_manager` for purely local node diagnostics or alarm dumps unless the user explicitly requests Teams communication or collaboration.
- If incident context or symptoms must be retrieved from Teams, first delegate to `incident_manager` to gather operational facts, then pass the verified symptoms, problem statement, and known applicability facts to `technical_authority_engineer`.
- Provide the problem statement, verified symptoms (including raw alarm lines, node prompts, and observed parameters), and any known applicability facts to `technical_authority_engineer`.
- The specialist evaluates technical evidence and recommends at most ONE evidence-grounded next check, or identifies missing diagnostic information.
- Relay the specialist's technical interpretation, the single next check, and its justification clearly to the user.
- Presenting the specialist's diagnostic recommendation:
  * When `diagnostic_step.command` is provided and authorized, display the exact command syntax, any parameter prerequisites, and its supporting governed citation clearly to the user.
  * When `diagnostic_step.command` is null or not authorized, do NOT instruct the user to execute or run an unspecified command. Explain plainly that operational command execution is not authorized and state precisely what authorization or approved procedure is missing.
- The specialist operates in an advisory role only: no direct execution, no configuration changes, no approval authority, no Teams write capabilities.
"""

PROBLEM_MANAGER_DELEGATION_ADDENDUM = """
PROBLEM MANAGER DELEGATION:
When the user asks for root cause analysis across multiple incidents, recurring fault trends, or known error investigation:
- Delegate root cause and problem lifecycle analysis to the `problem_manager` specialist tool.
- Provide the incident history, observed symptom patterns, and relevant fault identifiers.
- Relay the specialist's problem analysis and known error guidance clearly to the user.
"""

AUTOMATED_OPERATIONS_DELEGATION_ADDENDUM = """
AUTOMATED OPERATIONS ENGINEER DELEGATION:
When the user requests Level 1 operational checks or standard diagnostic triage:
- Delegate automated diagnostic triage to the `automated_operations_engineer` specialist tool.
- Relay findings and recommendations clearly to the user.
"""


def render_agent_roster_section(tools: list[Any]) -> str:
    """Renders the active agent roster based strictly on registered tool capabilities."""
    tool_names = {
        getattr(t, "name", getattr(t, "__name__", str(t)))
        for t in tools
    }

    lines = [
        "ACTIVE AGENT ROSTER & SPECIALISTS:",
        "You are team_manager, the orchestrator and sole user-facing contact. Your registered specialist capabilities for this session are:",
        "- `incident_manager`: Microsoft Teams operations, chat discovery, message retrieval, and write proposal preparation.",
    ]
    if "technical_authority_engineer" in tool_names or "troubleshooting_manager" in tool_names:
        lines.append(
            "- `technical_authority_engineer`: Technical fault diagnosis, diagnostic step recommendations, and command grounding."
        )
    if "problem_manager" in tool_names:
        lines.append(
            "- `problem_manager`: ITIL problem management, recurring incident patterns, and known error identification."
        )
    if "automated_operations_engineer" in tool_names:
        lines.append(
            "- `automated_operations_engineer`: Level 1 operations, automated diagnostic checks, and standard remediation actions."
        )
    lines.append(
        "Do not advertise, promise, or mention capabilities or specialists that are not listed in this active roster."
    )
    return "\n".join(lines)


def _render_troubleshooting_state_block(ts: TroubleshootingState) -> str:
    lines = [
        "ACTIVE TROUBLESHOOTING STATE:",
        f"Fault ID: {ts.fault_id} (Status: {ts.status.value})",
        f"Symptom summary: {ts.symptom_summary}",
    ]
    if ts.node_id:
        lines.append(f"Node: {ts.node_id}")
    if ts.working_hypothesis:
        lines.append(f"Working Hypothesis: {ts.working_hypothesis}")
    if ts.competing_hypotheses:
        lines.append(f"Competing Hypotheses: {', '.join(ts.competing_hypotheses)}")
    if ts.diagnostic_history:
        lines.append("Diagnostic history:")
        for rec in ts.diagnostic_history:
            cmd_info = f" [cmd: `{rec.grounded_command}`]" if rec.grounded_command else ""
            obs_info = f" -> observed: {rec.observed_result}" if rec.observed_result else ""
            lines.append(f"- [{rec.status.value}] {rec.action}{cmd_info}{obs_info}")
    return "\n".join(lines)


def _render_case_context_block(
    snapshot: CaseContextSnapshot,
    troubleshooting_state: Optional[TroubleshootingState] = None,
) -> str:
    lines = [
        "ACTIVE CASE CONTEXT:",
        f"Title: {snapshot.title}",
        f"Status: {snapshot.status.value}",
        f"Problem statement: {snapshot.problem_statement}",
    ]
    if troubleshooting_state:
        lines.append(f"Active Fault: {troubleshooting_state.fault_id} (Status: {troubleshooting_state.status.value})")
        if troubleshooting_state.working_hypothesis:
            lines.append(f"Working Hypothesis: {troubleshooting_state.working_hypothesis}")
        if troubleshooting_state.competing_hypotheses:
            lines.append(f"Competing Hypotheses: {', '.join(troubleshooting_state.competing_hypotheses)}")
        if troubleshooting_state.diagnostic_history:
            lines.append("Diagnostic history:")
            for rec in troubleshooting_state.diagnostic_history:
                cmd_info = f" [cmd: `{rec.grounded_command}`]" if rec.grounded_command else ""
                obs_info = f" -> observed: {rec.observed_result}" if rec.observed_result else ""
                lines.append(f"- [{rec.status.value}] {rec.action}{cmd_info}{obs_info}")
    if snapshot.external_reference:
        lines.append(f"External reference: {snapshot.external_reference}")
    if snapshot.items:
        lines.append("Recorded context (priority-selected, shown chronologically):")
        for item in snapshot.items:
            author = item.source_author or item.source_type.value
            lines.append(f"- [{item.kind.value} | source={item.source_type.value}:{author}] {item.content}")
    else:
        lines.append("No context items have been recorded for this case yet.")
    if snapshot.context_truncated:
        lines.append(
            f"(Showing {snapshot.included_item_count} of {snapshot.total_item_count} recorded case "
            "items -- this case has more history than fits here; say so if asked whether this is the complete history.)"
        )
    return "\n".join(lines)


def make_team_manager_instruction_provider(
    tools: Optional[list[Any]] = None,
) -> Callable[[ReadonlyContext], Awaitable[str]]:
    """Factory that builds an InstructionProvider capturing the agent's registered tools."""
    async def provider(ctx: ReadonlyContext) -> str:
        effective_tools = tools
        if effective_tools is None:
            from backend.agents.team_manager.agent import _build_team_manager_tools
            effective_tools = _build_team_manager_tools()

        tool_names = {
            getattr(t, "name", getattr(t, "__name__", str(t)))
            for t in effective_tools
        }

        case_id = ctx.state.get(ACTIVE_CASE_ID_STATE_KEY)
        pending_specialist_result = ctx.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY)
        base = TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION if pending_specialist_result else TEAM_MANAGER_INSTRUCTION
        base_instruction = await inject_session_state(base, ctx)

        if not pending_specialist_result:
            roster_section = render_agent_roster_section(effective_tools)
            base_instruction = f"{base_instruction}\n\n{roster_section}"

            if "technical_authority_engineer" in tool_names or "troubleshooting_manager" in tool_names:
                base_instruction = f"{base_instruction}\n\n{TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM}"
            if "problem_manager" in tool_names:
                base_instruction = f"{base_instruction}\n\n{PROBLEM_MANAGER_DELEGATION_ADDENDUM}"
            if "automated_operations_engineer" in tool_names:
                base_instruction = f"{base_instruction}\n\n{AUTOMATED_OPERATIONS_DELEGATION_ADDENDUM}"

        ts_raw = ctx.state.get("troubleshooting_state")
        troubleshooting_state: Optional[TroubleshootingState] = None
        if ts_raw and isinstance(ts_raw, dict):
            try:
                troubleshooting_state = TroubleshootingState.model_validate(ts_raw)
            except Exception:
                pass

        if not case_id:
            if troubleshooting_state:
                return f"{base_instruction}\n\n{_render_troubleshooting_state_block(troubleshooting_state)}"
            return base_instruction

        try:
            case_service = get_case_service()
            case = await case_service.get_case(ctx.user_id, case_id)
            items = await case_service.get_context_items(ctx.user_id, case_id)
        except SafeErrorException:
            if troubleshooting_state:
                return f"{base_instruction}\n\n{_render_troubleshooting_state_block(troubleshooting_state)}"
            return base_instruction

        snapshot = build_case_context_snapshot(case, items)

        # Context Engineering Layer: assemble multi-source bounded context
        broker = ContextEngineeringBroker()
        case_context_items = [
            ContextItem(
                domain=ContextDomain.CASE,
                source_id=f"case-item-{idx}",
                title=f"Case {item.kind.value} ({item.source_type.value})",
                content=item.content,
            )
            for idx, item in enumerate(snapshot.items)
        ]
        _ = broker.assemble(
            query=snapshot.problem_statement,
            operational_items=case_context_items,
            case_details={
                "case_id": snapshot.case_id,
                "title": snapshot.title,
                "status": snapshot.status.value,
                "problem_statement": snapshot.problem_statement,
            },
            troubleshooting_state=troubleshooting_state,
        )

        return f"{base_instruction}\n\n{CASE_CONTEXT_TEAM_MANAGER_ADDENDUM}\n\n{_render_case_context_block(snapshot, troubleshooting_state)}"

    return provider


team_manager_instruction_provider = make_team_manager_instruction_provider()
