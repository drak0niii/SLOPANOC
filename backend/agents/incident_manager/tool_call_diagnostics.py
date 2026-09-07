"""P4B AUDIT: safe, closed-vocabulary diagnostics for which tool
incident_manager actually calls, on each round trip -- added specifically
to answer this pass's own investigative question ("what are the pre-
retrieval model calls actually doing") without guessing, and left in place
afterward as ordinary low-noise observability (it costs nothing when nobody
is watching the logs, and answers the same question if the call graph ever
regresses).

SAFE BY CONSTRUCTION: logs only `tool.name` -- a `BaseTool`'s own name
attribute, always one of this module's own small, closed, non-secret set
of registered tool names (verified against `incident_manager.tools`/
`_CONTINUATION_INCIDENT_MANAGER.tools`, never open/attacker-influenced) --
checked against an explicit allowlist before logging, so a hypothetical
future tool with an unexpected name is silently skipped rather than logged
verbatim. Never logs `args`, `tool_context.state`, a tool's result, a chat
id, or any user/Teams content.
"""
from __future__ import annotations

import logging
from typing import Any

_logger = logging.getLogger("backend.perf")

_SAFE_TOOL_NAMES = frozenset(
    {
        "get_current_time_context",
        "get_resolved_chat_messages",
        "teams_list_chats",
        "teams_get_messages",
        "teams_get_members",
        "teams_propose_create_chat",
        "teams_propose_send_message",
        "teams_create_chat",
        "teams_send_message",
        # Phase 5.1J: same closed-vocabulary, name-only diagnostic
        # extended to the new Generic KM tools -- never logs query text,
        # selection keys, or evidence content.
        "knowledge_search",
        "knowledge_select_evidence",
    }
)


def log_incident_manager_tool_call(tool: Any, args: dict[str, Any], tool_context: Any) -> None:
    """`before_tool_callback` for incident_manager (wired in agent.py) --
    always returns `None` (a diagnostic side effect only, never a
    substitute tool result -- the real tool call always still happens).
    """
    name = getattr(tool, "name", None)
    if name in _SAFE_TOOL_NAMES:
        _logger.info("perf stage=incident_manager_function_call tool_name=%s", name)
    return None
