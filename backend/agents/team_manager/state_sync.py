"""Deterministic session-state synchronization for team_manager.

Fixes the regression where structured Teams context (which chat is
selected, what evidence backed the last answer) was not being persisted
across turns even though the conversation text was: `incident_manager` is
stateless across calls, and nothing was writing the *resolved* chat/
evidence into team_manager's own session state -- team_manager's prompt
had no reliable way to know "which chat" a follow-up like "who is on
antibiotics?" meant.

This module owns *writing* `selected_teams_chat_id`,
`selected_teams_chat_topic`, and `last_teams_evidence` into team_manager's
session state -- never inferring them from raw conversation text. They
are read back into team_manager's own instruction via ADK's `{var?}`
session-state templating (see prompts.py); this module never reads them
for reasoning purposes, only writes.

WHY AN after_tool_callback ON team_manager, NOT SOMETHING INSIDE
incident_manager (verified against the installed ADK 1.33.0 source before
implementing, per instruction):

  - `google.adk.tools.agent_tool.AgentTool.run_async` creates a brand-new
    `InMemorySessionService()` and a brand-new session on *every single*
    call. incident_manager has no persistent nested session to accumulate
    cross-turn memory in, even if something tried to write to it.
  - `google.adk.flows.llm_flows.functions.py`'s tool-calling machinery
    (`__call_tool_async` -> `tool.run_async(args=args,
    tool_context=tool_context)`) is fully generic across `BaseTool`
    subclasses, including `AgentTool` -- so team_manager's own
    `after_tool_callback` fires for its `incident_manager` tool call
    exactly like it would for any other tool, receiving:
      - `tool`: the `AgentTool` instance (checked by `.name` below, so
        this callback only ever acts on the `incident_manager` call)
      - `tool_context`: team_manager's own, live `ToolContext` for this
        call -- never a copy, never a nested/forwarded one
      - `tool_response`: the dict `AgentTool.run_async` returned, which
        (since `output_schema=IncidentManagerResponse` is set) is already
        `validate_schema`-checked, and whose `evidence` has already been
        through incident_manager's own deterministic
        `strip_unverified_evidence` (evidence.py) before this callback
        ever sees it.

Writing directly into that `tool_context.state` is therefore unambiguous
and requires no reliance on nested-session state-delta forwarding (the
mechanism `KNOWN_MESSAGE_IDS_STATE_KEY`/`teams_get_messages` use
elsewhere in this codebase, which is real but is a different, narrower
case: a tool *inside* incident_manager's own nested run informing that
same run's later tool calls, not something intended to reach back across
turns).
"""
from __future__ import annotations

from typing import Any, Optional

from backend.tools.teams.state_keys import (
    SELECTED_TEAMS_CHAT_ID_STATE_KEY,
    SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY,
)

LAST_TEAMS_EVIDENCE_STATE_KEY = "last_teams_evidence"

_INCIDENT_MANAGER_TOOL_NAME = "incident_manager"


def _sanitize_evidence(evidence: Any) -> Optional[list[dict[str, str]]]:
    """Reshape an already-validated evidence list down to exactly the
    three allowed fields (`message_id`/`author`/`sent_at`), dropping
    anything malformed. Returns `None` if `evidence` isn't a list at all
    (nothing to sanitize/store) -- distinct from an empty list, which is a
    legitimate "this answer had no supporting messages" result.
    """
    if not isinstance(evidence, list):
        return None

    sanitized: list[dict[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        message_id = item.get("message_id")
        author = item.get("author")
        sent_at = item.get("sent_at")
        if isinstance(message_id, str) and isinstance(author, str) and isinstance(sent_at, str):
            sanitized.append(
                {"message_id": message_id, "author": author, "sent_at": sent_at}
            )
    return sanitized


def compute_state_updates(tool_response: Any) -> dict[str, Any]:
    """Pure function: given incident_manager's (already schema- and
    evidence-validated) response dict, return the state key/value pairs
    team_manager's session should be updated with this turn.

    An empty dict means "nothing to change" -- callers must never use an
    empty/missing key here to *blank out* existing state: a failed
    resolution (ambiguous/not_found/error) or a non-"ok" outcome must
    never destroy a previously selected chat or previously stored
    evidence (requirement: "A failed attempt to select a different chat
    must NOT destroy the previously valid selected chat").
    """
    if not isinstance(tool_response, dict):
        return {}

    updates: dict[str, Any] = {}

    chat_id = tool_response.get("chat_id")
    chat_title = tool_response.get("chat_title")
    # chat_id is populated by incident_manager only for a genuinely
    # resolved chat (outcome "ok" or "no_result" -- both mean a chat was
    # actually matched, see docs/AGENT_CONTRACT.md's CHAT DISCOVERY
    # rules) -- never for "ambiguous"/"not_found"/"error". This single
    # check is therefore already exactly the "store only after successful
    # resolution" gate; no separate outcome check is needed here.
    if isinstance(chat_id, str) and chat_id:
        updates[SELECTED_TEAMS_CHAT_ID_STATE_KEY] = chat_id
        updates[SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY] = (
            chat_title if isinstance(chat_title, str) and chat_title else chat_id
        )

    # Only a genuine "ok" answer is "the previous result" an evidence
    # follow-up should refer to -- not ambiguous/not_found/error/no_result
    # (which never carry evidence to begin with).
    if tool_response.get("outcome") == "ok":
        sanitized = _sanitize_evidence(tool_response.get("evidence"))
        if sanitized is not None:
            updates[LAST_TEAMS_EVIDENCE_STATE_KEY] = sanitized

    return updates


def sync_incident_manager_result_to_state(
    tool: Any, args: dict[str, Any], tool_context: Any, tool_response: Any
) -> None:
    """ADK `after_tool_callback` for team_manager (wired in agent.py).

    Writes `compute_state_updates`'s result into `tool_context.state` --
    team_manager's own, live session state for this call -- and always
    returns `None`: this is a deterministic side effect only, never a
    substitute tool result (returning non-`None` from an
    `after_tool_callback` replaces what the LLM sees as the tool's
    response, which this callback must never do).
    """
    if getattr(tool, "name", None) != _INCIDENT_MANAGER_TOOL_NAME:
        return None

    for key, value in compute_state_updates(tool_response).items():
        tool_context.state[key] = value

    return None
