"""Deterministic construction of the driving text for a resumed READ
Teams operation, once its destination has been resolved via
`selection_service.choose()`.

Hardening pass: the previous design let the FRONTEND resume a read by
re-sending the user's own original raw request text as a brand-new turn.
That text still literally contained the OLD, unresolved destination name
(e.g. "gimme a summary of this chat room SLOPANOC Gateway"), so
`incident_manager` -- whose prompt correctly gives an explicitly-named
chat priority over the currently-selected one (instruction section 6/16,
a real, wanted behavior for genuine explicit corrections) -- resolved
that old name again from scratch and reopened the exact same ambiguity.

This module never touches, parses, or replays the user's original text.
It builds resume text ONLY from `PendingReadIntent`'s own structured
fields (see selection/schemas.py) -- all destination-free by construction
(see `PendingReadIntent`'s own docstring) -- falling back to one of a
small, fixed set of generic phrases, chosen by `intent.operation` (a
closed enum, never inspected/derived from text), for a request with no
specific `question`. The destination itself is never part of this text;
team_manager resolves it from `selected_teams_chat_topic` session state
instead (see team_manager/prompts.py's "authoritative state" paragraph),
exactly the same way it already would for any other destination-free
follow-up.

FIELD-SEPARATION HARDENING PASS (item 1): `operation` and `question` are
deliberately independent inputs here -- `operation` alone drives which
generic fallback phrase is used; it is NEVER combined with a discarded
`question` to try to reconstruct "the parts of the original sentence that
were safe." This function does no text surgery of any kind (see
`test_never_contains_a_destination_placeholder`'s structural guarantee,
unchanged) -- it only ever selects between whole, pre-written, safe
phrases and appends already-safe structured values verbatim.

SCOPE-DISAMBIGUATION HARDENING PASS (ambiguous-selection-resume bugfix):
the generic fallback phrases used to say only "the currently selected
chat" -- correct and unambiguous under the PRE-existing architecture,
where "chat"/"conversation" could only ever mean a Microsoft Teams
conversation. Once team_manager's `ConversationTarget` semantics
(conversation_target.py) introduced a SECOND legitimate meaning -- "this
SLOPANOC conversation itself" (`current_thread`) -- that same generic
phrasing became genuinely ambiguous: read as a fresh, standalone turn (it
IS one; it drives a brand-new backend turn -- see
`selection_service.py`'s own docstring), "the currently selected chat"
no longer unambiguously signals an EXTERNAL Teams resource the way it
used to, risking a `current_thread` misclassification for a request that
has nothing yet in this conversation to summarize. `GENERIC_
SUMMARY_RESUME_TEXT`/`GENERIC_GET_MESSAGES_RESUME_TEXT` now say "Teams
conversation" explicitly -- still fully generic and destination-free (no
chat NAME is added, only the domain word "Teams"), so this is a
clarification of this module's own pre-written, system-authored
boilerplate, never new text derived from the user's original wording.
"""
from __future__ import annotations

from typing import Optional

from backend.selection.schemas import PendingReadIntent, ReadOperation

GENERIC_SUMMARY_RESUME_TEXT = "Please provide a summary of the currently selected Teams conversation."
GENERIC_GET_MESSAGES_RESUME_TEXT = (
    "Please retrieve the latest messages from the currently selected Teams conversation."
)

_GENERIC_RESUME_TEXT_BY_OPERATION: dict[ReadOperation, str] = {
    ReadOperation.SUMMARIZE: GENERIC_SUMMARY_RESUME_TEXT,
    ReadOperation.GET_MESSAGES: GENERIC_GET_MESSAGES_RESUME_TEXT,
}


def build_read_resume_message(intent: Optional[PendingReadIntent]) -> str:
    """Returns the deterministic text to drive the resumed turn.

    - A stored `question` (already a complete, self-contained,
      destination-free statement -- see `PendingReadIntent`'s docstring)
      is used verbatim; this covers "who said X"/"what are the action
      items"/a future "who's in this chat" the same way, without any
      operation-specific branching -- `question`, when present, always
      takes priority over the operation-based generic phrase below, since
      it is strictly more specific.
    - No stored `question` falls back to a generic phrase chosen by
      `intent.operation` (defaults to the summarize phrasing when `intent`
      itself is `None`, matching the prior, pre-hardening-pass behavior)
      -- never invented per-call, never derived from the user's original
      wording, and never blended with any part of a discarded `question`.
    - A stored `requested_time_range` is appended as a short, clearly
      labeled clause, exactly as given (already the user's own verbatim
      time expression from the original turn, e.g. "the last 7 days" --
      never re-parsed or re-derived here), regardless of which of the
      above two branches produced the base text.
    """
    question = intent.question if intent is not None else None
    time_range = intent.requested_time_range if intent is not None else None
    operation = intent.operation if intent is not None else ReadOperation.SUMMARIZE

    if question and question.strip():
        text = question.strip()
    else:
        text = _GENERIC_RESUME_TEXT_BY_OPERATION.get(operation, GENERIC_SUMMARY_RESUME_TEXT)

    if time_range and time_range.strip():
        text = f"{text} (time range: {time_range.strip()})"
    return text
