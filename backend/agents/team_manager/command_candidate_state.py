"""POST-6A REPAIR 5 -- Durable binding of the last authorized command.

One additive session-state key holding the `CommandCandidateBinding`
(command_construction.py) the most recent authorized command candidate
was bound to. Mirrors `PENDING_GOVERNED_REQUEST_STATE_KEY`'s own
established idiom exactly -- a single, always-refreshed, additive key
written only by `chat_service.py`'s own end-of-turn state delta, never by
a tool and never by the model.

WHAT IT IS FOR, AND ONLY FOR: comparing what a command was authorized FOR
last turn against what this turn establishes, so a candidate or an
approval bound to a superseded target/operation/version/payload is
actively invalidated rather than silently reused. It is NEVER an
authorization: storing a binding grants nothing, and a stored binding is
never a reason to re-emit the command it names. Re-authorization always
means a fresh construction through the full gate stack.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import ValidationError

from backend.agents.team_manager.command_construction import CommandCandidateBinding

COMMAND_CANDIDATE_BINDING_STATE_KEY = "last_command_candidate_binding"
"""Plain, OVERWRITABLE session-state value -- rewritten (or blanked) by
EVERY turn, exactly like `PENDING_GOVERNED_REQUEST_STATE_KEY`, so a
binding can never linger past the turn that established it."""


def parse_command_candidate_binding(raw: Any) -> Optional[CommandCandidateBinding]:
    """Tolerant, fail-closed parse -- mirrors `parse_pending_governed_
    request` exactly: malformed/absent/wrong-shaped data all resolve to
    `None`, never a raised exception. `None` means "nothing was bound",
    which correctly leaves nothing to invalidate."""
    if not isinstance(raw, dict):
        return None
    try:
        return CommandCandidateBinding.model_validate(raw)
    except ValidationError:
        return None
