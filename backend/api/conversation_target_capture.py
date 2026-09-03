"""Deterministic, per-turn observation of team_manager's own
`record_conversation_target` tool call (semantic-scope bug fix).

BOUNDARY: independently re-observes the same top-level ADK event stream
`StatusTranslator`/`RunTraceTranslator`/`TeamsSourceCapture`/
`DelegationTimer` already do, mirroring their own established "each
helper inspects what it needs" pattern (see source_reference.py's module
docstring) -- rather than threading a new dependency through any of them.

DEVELOPER DIAGNOSTIC ONLY: the captured `target` is used by
`chat_service.py` purely to emit one safe, structured log line (`stage`/
`run_id`/`target` -- never user content, mirroring `perf_timing.py`'s own
safety contract) for testability/observability. It has NO bearing on what
happens in the turn -- team_manager's own subsequent reasoning/tool calls
in the SAME turn already deterministically decide that (calling
`incident_manager` or not, which chat, etc.); this class never feeds back
into or gates anything.
"""
from __future__ import annotations

from typing import Any, Optional

_CONVERSATION_TARGET_TOOL_NAME = "record_conversation_target"


class ConversationTargetCapture:
    """Captures the LAST `target` value team_manager successfully declared
    via `record_conversation_target` during this turn (mirrors
    `TeamsSourceCapture`'s own "last successful call wins" semantics --
    the most recent declaration is the one actually governing this turn's
    response, in the rare case the model calls it more than once).
    """

    def __init__(self) -> None:
        self._target: Optional[str] = None

    def observe(self, event: Any) -> None:
        if getattr(event, "partial", False):
            return
        for response in event.get_function_responses():
            if getattr(response, "name", None) != _CONVERSATION_TARGET_TOOL_NAME:
                continue
            result = getattr(response, "response", None)
            if isinstance(result, dict) and isinstance(result.get("target"), str):
                self._target = result["target"]

    @property
    def target(self) -> Optional[str]:
        return self._target
