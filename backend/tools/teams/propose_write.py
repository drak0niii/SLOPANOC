"""ADK tools: `teams_propose_create_chat` / `teams_propose_send_message`.

These are the "deterministic proposal-creation capability" (instruction
milestone 3B section 5) incident_manager may call. They validate and
normalize input (write_validation.py), then call
`backend.approval.service.create_action_proposal` -- generating the
proposal id/payload hash/timestamps deterministically in Python, never
from the model -- and return only safe, user-facing proposal information.

THEY NEVER APPROVE. There is no code path here, or anywhere reachable
from these functions, that can move a proposal's status to `approved`
(see backend/approval/service.py's module docstring for the security
contract that keeps `approve_proposal`/`reject_proposal` off every
agent's tool list entirely).

STATE, ACROSS THE AgentTool BOUNDARY: `tool_context.state` here is
incident_manager's own nested-session state for this call -- but per
`google.adk.tools.agent_tool.AgentTool.run_async` (verified against the
installed ADK 1.33.0 source), that nested session's initial state is a
direct copy of team_manager's own session state at call time, and any
state delta produced during the call (including this tool's
`create_action_proposal` write) is forwarded back into team_manager's
real `tool_context.state` when the call returns. So a write here
genuinely ends up as "`pending_action_proposal` in Team Manager's session
state" (instruction requirement), even though this function itself never
touches team_manager's `ToolContext` directly -- no second/parallel
memory system is introduced; this is the same session, relayed by ADK's
own tool-calling machinery.
"""
from __future__ import annotations

from typing import Any, Optional

from google.adk.tools import ToolContext

from backend.approval.schemas import WriteOperation
from backend.approval.service import create_action_proposal
from backend.gateway.safe_error import SafeErrorException
from backend.tools.teams.message_formatting import format_teams_message
from backend.tools.teams.state_keys import (
    SELECTED_TEAMS_CHAT_ID_STATE_KEY,
    SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY,
)
from backend.tools.teams.write_validation import (
    normalize_create_chat_payload,
    normalize_send_message_payload,
)


def _proposal_info(proposal: Any, extra: dict[str, Any]) -> dict[str, Any]:
    """The safe, model-facing subset of an `ActionProposal` -- deliberately
    excludes `payload_hash` (instruction: "Do not expose payload hash
    unless technically required" -- it is not required here) and anything
    gateway/credential-related.

    HARDENING PASS (Phase 4G): deliberately excludes `expires_at`/
    `expires_in_seconds`/`expires_in_minutes` too. The model does not need
    expiry information to describe the proposed action or to state that
    approval is required -- exposing a countdown/duration here is exactly
    what caused the model to narrate "expires in approximately 10
    minutes" in normal conversation, which violates the locked "no visible
    timer" UX requirement. This is the actual SOURCE fix (removing the
    data the model would otherwise echo), not a prompt-only request not to
    use it, and not post-hoc string stripping of generated text.
    Expiry remains fully enforced server-side regardless -- `authorize_write`
    (backend/approval/policy_gate.py) always re-derives it from the stored
    `ActionProposal.expires_at` directly, completely independent of
    anything returned here; nothing in this function is ever consulted for
    authorization. If a proposal has already expired by the time a write
    is attempted, `teams_create_chat`/`teams_send_message` (execute_write.py)
    still denies it and returns a safe, authoritative "This proposal has
    expired" message the model may relay plainly (see incident_manager's/
    team_manager's existing generic tool-error-relay instructions) --
    that is a genuine backend fact being reported after the fact, not a
    conversational countdown estimate.
    """
    return {
        "proposal_id": proposal.proposal_id,
        "operation": proposal.operation.value,
        "status": proposal.status.value,
        **extra,
    }


def _resolve_target_display_name(state: Any, chat_id: str) -> Optional[str]:
    """PRESENTATION ONLY -- the human-readable topic of `chat_id`, if
    `state` (a copy of team_manager's own session state -- see this
    module's docstring) already authoritatively knows it, i.e. the
    resolved chat currently selected by team_manager's own deterministic
    `state_sync.py` matches this exact `chat_id`.

    Deliberately conservative: returns `None` (never a guess/invention,
    never parsed from conversation text) whenever the selected chat's id
    doesn't match -- e.g. the user is sending to a chat other than the
    one most recently discussed -- or nothing is selected yet. `None`
    here safely results in the frontend falling back to a neutral,
    non-identifying placeholder (see ApprovalCard.tsx); it has no effect
    on `payload`/`payload_hash`/execution.
    """
    if not isinstance(state, dict):
        return None
    if state.get(SELECTED_TEAMS_CHAT_ID_STATE_KEY) != chat_id:
        return None
    topic = state.get(SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY)
    return topic if isinstance(topic, str) and topic else None


def teams_propose_create_chat(
    title: str,
    members: list[str],
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Propose creating a new Teams chat. Does not create anything in
    Teams -- only records a pending `ActionProposal` for a trusted
    approval boundary (never this model) to approve or reject.

    Args:
      title: The exact chat title.
      members: The other participants' complete email addresses (not
        including the connection owner, which Power Automate adds
        automatically). Names are not accepted -- if the user gave names
        instead of email addresses, ask for complete email addresses
        rather than calling this tool.
      tool_context: ADK-injected; the proposal is stored in session state
        here (see module docstring for how this reaches team_manager's
        real session).

    Returns:
      On success, safe proposal info (`proposal_id`, `operation`,
      `status`, `title`, `members`) for team_manager to present to the
      user for approval -- deliberately no expiry field (see
      `_proposal_info`'s docstring). On a validation failure, a dict
      with a single `error` key (SafeError shape) -- no proposal is
      created.
    """
    try:
        payload = normalize_create_chat_payload(title, members)
    except SafeErrorException as exc:
        return {"error": exc.safe_error.to_dict()}

    state = tool_context.state if tool_context is not None else {}
    summary = (
        f"Create Teams chat \"{payload['title']}\" with participants: "
        + ", ".join(payload["members"])
    )
    proposal = create_action_proposal(
        WriteOperation.TEAMS_CREATE_CHAT.value, payload, state, summary=summary
    )
    return _proposal_info(proposal, {"title": payload["title"], "members": payload["members"]})


def teams_propose_send_message(
    chat_id: str,
    message: str,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Propose sending a message to an already-resolved Teams chat. Does
    not send anything -- only records a pending `ActionProposal`.

    Args:
      chat_id: The resolved Teams chat id (from `teams_list_chats` or the
        currently selected chat) -- never invented.
      message: The message text, in your own plain prose/paragraphs/lists.
        POST-B7 UI/UX refinement (Item 1, corrective pass): this is
        deterministically reformatted into clean, structured PLAIN TEXT
        (see message_formatting.py -- never HTML, per a real live test
        proving the current Power Automate/Teams write path does not
        render HTML as intended) BEFORE it becomes the approved payload --
        the FORMATTED text is what the user is asked to approve, and the
        only text that may later be sent (see write_validation.py) --
        never write your own markup here, and never re-propose the same
        content merely to "fix" its formatting.
      tool_context: ADK-injected; see module docstring.

    Returns:
      On success, safe proposal info (`proposal_id`, `operation`,
      `status`, `chat_id`, `message` (the formatted plain text that was
      approved), and `target_display_name` when the destination chat's
      human-readable topic is already authoritatively known -- see
      `_resolve_target_display_name`) -- deliberately no expiry field (see
      `_proposal_info`'s docstring). On a validation failure, a dict with
      a single `error` key -- no proposal is created.
    """
    try:
        payload = normalize_send_message_payload(chat_id, format_teams_message(message))
    except SafeErrorException as exc:
        return {"error": exc.safe_error.to_dict()}

    state = tool_context.state if tool_context is not None else {}
    target_display_name = _resolve_target_display_name(state, payload["chatId"])
    summary = f"Send message to chat {payload['chatId']}: {payload['message']}"
    proposal = create_action_proposal(
        WriteOperation.TEAMS_SEND_MESSAGE.value,
        payload,
        state,
        summary=summary,
        target_display_name=target_display_name,
    )
    extra: dict[str, Any] = {"chat_id": payload["chatId"], "message": payload["message"]}
    if target_display_name is not None:
        extra["target_display_name"] = target_display_name
    return _proposal_info(proposal, extra)
