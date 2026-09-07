"""The smallest structured current-turn concept for "does THIS request
require a current, freshly-verified source" (FOURTH pre-4H correction
pass -- PAST ASSISTANT OUTPUT != GOVERNED KNOWLEDGE).

THE BUG THIS FIXES: `IncidentManagerRequest.requires_governed_knowledge`
(third correction pass) only ever exists once team_manager has already
decided to delegate -- it protects the shape of a delegation that
happens, but nothing previously forced team_manager to delegate in the
first place. A live adversarial test showed team_manager satisfying a
"use governed knowledge" request by reciting facts from an EARLIER turn's
already-validated (but now stale, different-run) answer, with NO current
delegation, NO current `knowledge_search`, and NO current `knowledge_
select_evidence` -- content that happened to be accurate, but with zero
current provenance. `chat_service.py`'s own turn-completion boundary
(governed_knowledge_completion.py) is the enforcement point; THIS module
only produces the structured signal that boundary reads.

WHY A TOOL, NOT A SECOND AGENT OR A SECOND LLM CALL -- exactly
`conversation_target.py`'s own established pattern (see that module's own
docstring for the full rationale, unchanged here): a same-turn, same-
reasoning-pass structured declaration, model-decided (never Python
inspecting the user's message), tool-validated only for shape. Kept as a
SEPARATE tool from `record_conversation_target` rather than folded into
it -- conversation targeting (which conversation the user means) and
source requirements (which CURRENT sources this request needs) are two
independent semantic questions; conflating them into one tool/schema
would make either harder to reason about and harder to test in isolation.

EPHEMERAL, PER-TURN ONLY -- same discipline as `ConversationTarget`:
deliberately NOT written to persisted session state. A CURRENT-turn
requirement must control THIS turn's own completion check, never leak
into or get satisfied by a different turn's declaration. The declared
value is observed directly off this turn's own event stream by
`backend.api.source_requirements_capture.SourceRequirementsCapture`
(mirrors `ConversationTargetCapture`'s own pattern) -- but unlike that
class (developer-diagnostic only), THIS capture is load-bearing:
`chat_service.py` uses it to decide whether the completion-boundary
governed-knowledge gate applies to this turn at all.

DEFAULT IS "NOT REQUIRED", NOT FAIL-CLOSED: if team_manager never calls
this tool for a given turn, the capture defaults `requires_governed_
knowledge`/`requires_teams` to `False` (see source_requirements_capture.py)
-- an ordinary turn unrelated to Teams/governed content (e.g. "hello", "what
can you do") must never be blocked by a missing declaration. The prompt
(prompts.py, "CURRENT-TURN SOURCE DECLARATION") makes the call mandatory
for every non-history-recall request; `IncidentManagerRequest.requires_
governed_knowledge`'s OWN independent, structural enforcement inside
incident_manager (evidence.py/provenance_compliance.py, third correction
pass) remains the backstop whenever a delegation DOES happen regardless of
whether this declaration was also made.
"""
from __future__ import annotations

from typing import Any, Optional

from google.adk.tools import ToolContext


def record_source_requirements(
    requires_teams: bool, requires_governed_knowledge: bool, tool_context: Optional[ToolContext] = None
) -> dict[str, Any]:
    """Declare, for THIS request only, whether a current Teams conversation
    and/or current governed knowledge are REQUIRED sources -- your own
    semantic judgment, never a fixed phrase or keyword match. Call this
    once per turn for any request that is not pure recall of this
    SLOPANOC conversation's own history (skip it for `current_thread`
    requests, exactly like `record_conversation_target`).

    Args:
      requires_teams: True when a Teams conversation's current content is
        a required source for this request -- mirrors whether you are
        about to set `chat_topic` on an `incident_manager` delegation.
      requires_governed_knowledge: True when current, freshly-verified
        governed knowledge is a required source for this request -- set
        this whenever that is true even if you end up not delegating to
        `incident_manager` at all, and even if you already discussed the
        same topic on an earlier turn or the user asks you to skip
        citations: an earlier turn's answer is never current verification,
        and a citation-wording preference never changes this declaration.
      tool_context: ADK-injected.

    Returns:
      `{"requires_teams": ..., "requires_governed_knowledge": ...}` -- a
      plain acknowledgement; this call has no other side effect and does
      not by itself change what you do next.
    """
    return {"requires_teams": bool(requires_teams), "requires_governed_knowledge": bool(requires_governed_knowledge)}
