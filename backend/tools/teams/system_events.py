"""Deterministic detection of Teams system/event messages.

Membership changes, call events, and other non-user-authored chat entries
must never be handed to incident_manager's reasoning context as if they
were something a person said. Detection here is entirely content-based --
never sender-based: a message is never excluded merely because
`senderName` was missing. A legitimate user message can also arrive with
no reported sender, and requirement (docs/TEAMS_TOOL_CONTRACT.md #4c)
is explicit that only a clear system/event marker or genuinely empty
content justifies exclusion, not an absent sender on its own.
"""
from __future__ import annotations

import re

# Known Teams system-event content markers. Extend this set if further
# system-event tag names are observed in the live gateway's output; do
# not widen detection to include sender/author signals instead.
_SYSTEM_EVENT_TAG_PATTERN = re.compile(r"<\s*systemEventMessage\b", re.IGNORECASE)


def is_system_event_content(raw_content: str) -> bool:
    """True if `raw_content` is a known Teams system/event marker (e.g.
    `<systemEventMessage/>`, with or without attributes), regardless of
    what the sender was reported as.
    """
    if not raw_content:
        return False
    return bool(_SYSTEM_EVENT_TAG_PATTERN.search(raw_content))


def is_excludable_from_reasoning(
    raw_content: str, normalized_text: str, has_hosted_content: bool = False
) -> bool:
    """True if this message must not be handed to incident_manager for
    reasoning: either it is a known system/event marker, or it carries no
    meaningful content at all once normalized (truly empty content, or
    markup that normalizes to nothing) AND has no hosted content either.

    Never based on sender/author -- a message with no `senderName` but
    real, meaningful text is still included; see the module docstring.

    `has_hosted_content` (Teams Rich Content milestone, single-image
    scope, default `False` -- every pre-existing caller/behavior is
    byte-for-byte unchanged): an inline/pasted Teams image is represented
    as a bare `<img>` tag, which `html_text.normalize_teams_content`
    produces NO text for (unlike `<attachment>`, which becomes the
    `"[Attachment]"` marker) -- a genuine image-only message (real,
    meaningful content) would otherwise normalize to empty text and be
    wrongly excluded as content-free. `has_hosted_content=True` exempts
    ONLY the empty-text exclusion -- a genuine system/event marker is
    STILL always excluded regardless (checked first, unconditionally),
    so rich content can never cause a system/event entry to become
    evidence (instruction: system-event non-regression is absolute).
    """
    if is_system_event_content(raw_content):
        return True
    if has_hosted_content:
        return False
    return not normalized_text.strip()
