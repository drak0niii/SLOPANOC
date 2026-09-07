"""Deterministic, per-turn observation of team_manager's own `record_
source_requirements` tool call (fourth/fifth pre-4H correction passes).

BOUNDARY: independently re-observes the same top-level ADK event stream
`ConversationTargetCapture`/`TeamsSourceCapture`/`DelegationTimer` already
do, mirroring their own established "each helper inspects what it needs"
pattern -- rather than threading a new dependency through any of them.

UNLIKE `ConversationTargetCapture` (developer-diagnostic only), THIS
capture is LOAD-BEARING: `chat_service.py` reads it to decide whether this
turn's completion must satisfy the governed-knowledge completion gate
(governed_knowledge_completion.py) before its answer is accepted.

FIFTH CORRECTION PASS -- `declared` closes the remaining structural gap:
the fourth pass's own completion gate only ever fired when `requires_
governed_knowledge` was OBSERVED true, silently treating "the tool was
never called this turn" the same as "explicitly declared false" -- which
let team_manager skip the declaration entirely and answer straight from
conversation history with no gate ever noticing. `declared` distinguishes
those two cases explicitly: `chat_service.py` now requires `declared` to
be `True` before accepting ANY substantive completion, running a bounded
declaration-remediation attempt (source_requirements_completion.py)
whenever it is `False` -- absence of a declaration is no longer silently
read as "nothing required."
"""
from __future__ import annotations

from typing import Any

_SOURCE_REQUIREMENTS_TOOL_NAME = "record_source_requirements"


class SourceRequirementsCapture:
    """Captures the LAST `requires_teams`/`requires_governed_knowledge`
    values team_manager successfully declared via `record_source_
    requirements` during this turn (mirrors `ConversationTargetCapture`'s
    own "last successful call wins" semantics), and whether any such
    declaration happened at all this turn.
    """

    def __init__(self) -> None:
        self._declared: bool = False
        self._requires_teams: bool = False
        self._requires_governed_knowledge: bool = False

    def observe(self, event: Any) -> None:
        if getattr(event, "partial", False):
            return
        for response in event.get_function_responses():
            if getattr(response, "name", None) != _SOURCE_REQUIREMENTS_TOOL_NAME:
                continue
            result = getattr(response, "response", None)
            if not isinstance(result, dict):
                continue
            if isinstance(result.get("requires_teams"), bool) and isinstance(
                result.get("requires_governed_knowledge"), bool
            ):
                self._declared = True
                self._requires_teams = result["requires_teams"]
                self._requires_governed_knowledge = result["requires_governed_knowledge"]

    def record_external_declaration(self, requires_teams: bool, requires_governed_knowledge: bool) -> None:
        """Lets `chat_service.py` fold a declaration obtained from the
        FIFTH pass's own bounded remediation call (a SEPARATE nested
        Runner/event stream this capture never itself observes) into the
        same object the rest of the turn's completion logic already reads
        -- so callers only ever need to consult one capture, regardless of
        whether the declaration came from the main turn or the
        remediation attempt.
        """
        self._declared = True
        self._requires_teams = requires_teams
        self._requires_governed_knowledge = requires_governed_knowledge

    @property
    def declared(self) -> bool:
        return self._declared

    @property
    def requires_teams(self) -> bool:
        return self._requires_teams

    @property
    def requires_governed_knowledge(self) -> bool:
        return self._requires_governed_knowledge
