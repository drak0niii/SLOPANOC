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
from backend.gateway.safe_error import SafeErrorException


def _render_case_context_block(snapshot: CaseContextSnapshot) -> str:
    lines = [
        "ACTIVE CASE CONTEXT:",
        f"Title: {snapshot.title}",
        f"Status: {snapshot.status.value}",
        f"Problem statement: {snapshot.problem_statement}",
    ]
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


async def team_manager_instruction_provider(ctx: ReadonlyContext) -> str:
    """The `InstructionProvider` ADK invokes once per team_manager turn.

    P4A -- MODE SELECTION: `chat_service.py` writes `PENDING_SPECIALIST_
    RESULT_STATE_KEY` into session state (if at all) strictly BEFORE
    calling team_manager's `Runner.run_async` for this turn (see read_
    continuation_presentation.py's own docstring) -- so by the time ADK
    invokes this provider, its presence or absence is already settled for
    the WHOLE turn, never something that could change mid-turn. A turn
    presenting a trusted, already-resolved specialist result never needs
    CONVERSATION TARGET/chat-discovery/TEAMS WRITE ACTIONS reasoning (see
    prompts.py's own "P4A" docstring section for the full rationale), so
    it gets the separate, narrower `TEAM_MANAGER_TRUSTED_RESULT_
    INSTRUCTION` instead of paying for the full orchestration instruction
    every time.
    """
    case_id = ctx.state.get(ACTIVE_CASE_ID_STATE_KEY)
    pending_specialist_result = ctx.state.get(PENDING_SPECIALIST_RESULT_STATE_KEY)
    base = TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION if pending_specialist_result else TEAM_MANAGER_INSTRUCTION
    base_instruction = await inject_session_state(base, ctx)

    if not case_id:
        return base_instruction

    try:
        case_service = get_case_service()
        case = await case_service.get_case(ctx.user_id, case_id)
        items = await case_service.get_context_items(ctx.user_id, case_id)
    except SafeErrorException:
        return base_instruction

    snapshot = build_case_context_snapshot(case, items)
    return f"{base_instruction}\n\n{CASE_CONTEXT_TEAM_MANAGER_ADDENDUM}\n\n{_render_case_context_block(snapshot)}"
