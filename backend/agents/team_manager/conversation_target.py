"""The smallest structured semantic-target concept for team_manager's
"which conversation are you actually asking about" reasoning (semantic
scope bug fix, pre-4H).

THE BUG THIS FIXES: `selected_teams_chat_id`/`selected_teams_chat_topic`
(state_sync.py) are authoritative for "which Teams chat" once the user IS
asking about a Teams conversation -- but nothing previously forced
team_manager to first decide WHETHER the user is asking about a Teams
conversation at all, versus the current SLOPANOC conversation itself. A
vague follow-up like "summarize what was discussed in this chat window"
satisfied the OLD instruction's "no chat named, but one is selected ->
reuse it" rule and was incorrectly routed to the selected Teams chat
instead of a summary of the SLOPANOC session itself.

WHY A TOOL, NOT A SECOND AGENT OR A SECOND LLM CALL: this is a same-turn,
same-reasoning-pass structured declaration -- exactly the same shape as
`case_tools.py`'s `record_case_analysis` (a plain deterministic function
registered on `team_manager`, called when relevant, never itself
performing any classification). `ConversationTarget` is not computed by
this module or by any Python code inspecting the user's message -- the
model decides it, from its own semantic understanding of the request and
the conversation so far, and this function only VALIDATES the declared
value (closed set) and, for `selected_external_conversation`,
deterministically cross-checks that a Teams chat is actually selected in
session state -- mirroring `record_case_analysis`'s own "the tool
restricts what can be validly declared, the model supplies the judgment"
split. No natural-language parsing happens here, or anywhere else in this
fix -- see docs/AGENT_CONTRACT.md #5's "agent vs. tool" principle.

EPHEMERAL, PER-TURN ONLY: deliberately NOT written to persisted session
state. Requirement: "the semantic target selected for the CURRENT request
must control execution" -- caching/persisting a target across turns would
risk exactly the same class of bug this fixes (an old decision silently
outliving the request that produced it). The declared value is observed
directly off this turn's own event stream by
`backend.api.conversation_target_capture.ConversationTargetCapture`
(mirrors `TeamsSourceCapture`/`DelegationTimer`'s own established
"independently re-observe the event stream" pattern) purely for developer
diagnostics/testability -- it has no bearing on what team_manager does
next, since that already follows deterministically from team_manager's
own subsequent reasoning/tool calls in the SAME turn.
"""
from __future__ import annotations

from typing import Any, Optional

from google.adk.tools import ToolContext

from backend.gateway.safe_error import SafeErrorException, validation_error
from backend.tools.teams.state_keys import SELECTED_TEAMS_CHAT_ID_STATE_KEY


class ConversationTarget:
    """The closed set of semantic targets a "summarize/recap this
    conversation" style request can resolve to. Plain string constants
    (not a `pydantic`/`enum.Enum`) so ADK's automatic function-schema
    generation exposes `target` to the model as a simple string parameter,
    exactly like `record_case_analysis`'s own `kind: str` parameter.
    """

    CURRENT_THREAD = "current_thread"
    """The user is asking about THIS SLOPANOC conversation itself -- the
    back-and-forth between them and team_manager in this session."""

    SELECTED_EXTERNAL_CONVERSATION = "selected_external_conversation"
    """The user is asking about a Microsoft Teams conversation as an
    external resource, without naming a new one -- the currently selected
    Teams chat (`selected_teams_chat_id`/`selected_teams_chat_topic`)."""

    EXPLICIT_EXTERNAL_CONVERSATION = "explicit_external_conversation"
    """The user names a specific Microsoft Teams conversation in their
    current message."""


_VALID_TARGETS = frozenset(
    {
        ConversationTarget.CURRENT_THREAD,
        ConversationTarget.SELECTED_EXTERNAL_CONVERSATION,
        ConversationTarget.EXPLICIT_EXTERNAL_CONVERSATION,
    }
)


def record_conversation_target(target: str, tool_context: Optional[ToolContext] = None) -> dict[str, Any]:
    """Declare which conversation this specific request is actually about,
    before doing anything else for it -- see "CONVERSATION TARGET" in
    prompts.py for exactly when to call this and how to decide.

    Args:
      target: Exactly one of "current_thread",
        "selected_external_conversation", "explicit_external_conversation"
        -- anything else is rejected. Your own semantic judgment, never a
        keyword/phrase match.
      tool_context: ADK-injected.

    Returns:
      On success, `{"target": target}` -- a plain acknowledgement; this
      call has no other side effect and does not by itself change what
      you do next (your own subsequent reasoning/tool calls this turn
      do). On failure (an unrecognized `target`, or
      "selected_external_conversation" declared with no Teams chat
      actually selected), a dict with a single `error` key -- reconsider
      which target actually applies rather than retrying the same value.
    """
    try:
        if target not in _VALID_TARGETS:
            raise validation_error(
                "target must be one of: current_thread, selected_external_conversation, "
                "explicit_external_conversation."
            )
        if target == ConversationTarget.SELECTED_EXTERNAL_CONVERSATION:
            selected_chat_id = tool_context.state.get(SELECTED_TEAMS_CHAT_ID_STATE_KEY) if tool_context else None
            if not selected_chat_id:
                raise validation_error(
                    "No Teams chat is currently selected -- use explicit_external_conversation "
                    "with the named chat, or current_thread if this is about the SLOPANOC "
                    "conversation itself."
                )
    except SafeErrorException as exc:
        return {"error": exc.safe_error.to_dict()}

    return {"target": target}
