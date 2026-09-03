"""The ONE deterministic Case-analysis-recording capability reachable from
the model (instruction section 15) -- registered only on team_manager,
never a second/third near-duplicate tool.

WHAT THIS CANNOT DO: `kind` is restricted, by
`backend.cases.service.CaseService.record_case_analysis`, to
`AGENT_ANALYSIS_KINDS` (hypothesis / recommendation / open_question) --
attempting "evidence", "observation", "decision", "action", or
"resolution" here is rejected deterministically before anything is
written (see that method's docstring). There is no parameter for
`source_type` (always "agent"), no parameter to claim a different agent
name than "team_manager", and no way to write to a Case this session is
not linked to, or whose active-case hint is stale (see
`CaseService.record_case_analysis`'s independent membership
re-verification).

NO CHAIN-OF-THOUGHT (instruction section 16): this tool has no field for
private reasoning -- only `content` (the concise, shareable
conclusion itself) and, optionally, `confidence`/
`supporting_case_item_ids`. The model is instructed (prompts.py) to keep
`content` concise and externally shareable; nothing here enforces length,
but nothing here accepts a second, hidden-reasoning field either.
"""
from __future__ import annotations

from typing import Any, Optional

from google.adk.tools import ToolContext

from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY
from backend.cases.service import get_case_service
from backend.gateway.safe_error import SafeErrorException, not_found

_AGENT_NAME = "team_manager"


async def record_case_analysis(
    kind: str,
    content: str,
    confidence: Optional[float] = None,
    supporting_case_item_ids: Optional[list[str]] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Record durable Case analysis -- a hypothesis, a recommendation, or
    an open question -- for the Case this session is currently linked to.

    Args:
      kind: One of "hypothesis", "recommendation", "open_question" --
        anything else is rejected. Never "evidence", "observation",
        "decision", "action", or "resolution" -- those require a human
        Case member or a trusted deterministic source, never the model
        directly.
      content: A concise, externally-shareable statement (e.g. "Recent
        configuration change may be related to packet loss.") -- never
        private step-by-step reasoning.
      confidence: Optional 0-1 confidence, only if you actually have a
        basis for one.
      supporting_case_item_ids: Optional ids of existing Case context
        items (from what you were shown in "ACTIVE CASE CONTEXT") this
        analysis is based on -- every id is independently verified before
        anything is written; an unverifiable id fails the whole call.
      tool_context: ADK-injected.

    Returns:
      On success, `{"item_id", "kind", "content"}`. On failure (no Case
      linked, disallowed kind, unverifiable supporting id), a dict with a
      single `error` key (SafeError shape) -- nothing is written.
    """
    try:
        if tool_context is None:
            raise not_found("No case is currently linked to this session.")

        case_id = tool_context.state.get(ACTIVE_CASE_ID_STATE_KEY)
        if not case_id:
            raise not_found("No case is currently linked to this session.")

        case_service = get_case_service()
        item = await case_service.record_case_analysis(
            user_id=tool_context.user_id,
            agent_name=_AGENT_NAME,
            case_id=case_id,
            kind=kind,
            content=content,
            confidence=confidence,
            supporting_item_ids=supporting_case_item_ids,
        )
    except SafeErrorException as exc:
        return {"error": exc.safe_error.to_dict()}

    return {"item_id": item.item_id, "kind": item.kind.value, "content": item.content}
