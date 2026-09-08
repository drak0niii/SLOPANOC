"""POST-B7 UI/UX refinement (Item 1) -- deterministic, generic markdown-lite
plain-text formatter for outbound Teams messages.

CORRECTIVE PASS -- HTML REPLACED WITH PLAIN TEXT: the first implementation
of this module formatted outbound messages as minimal-safe HTML, on the
(reasonable, but wrong) assumption that the Power Automate "Post message
in a chat" action's Message field renders rich text. A real live test
proved that assumption false: the current Power Automate/Teams write path
does not render the HTML as intended. This module now produces
DETERMINISTIC, PROFESSIONALLY STRUCTURED PLAIN TEXT instead -- no HTML, no
Markdown dependency, no Adaptive Cards, no new Power Automate flow
requirement. `message`/`ActionProposal.payload["message"]`/the Power
Automate `message` field all remain a plain Python `str`, exactly as
before this whole POST-B7 milestone started -- only ITS CONTENT changed
(from a raw, unstructured dump to a cleanly structured one), never its
type or the surrounding contract.

WHY THIS RUNS EXACTLY ONCE, BEFORE PROPOSAL HASHING (unchanged from the
HTML version -- this trust-boundary reasoning does not depend on the
output format): called from `propose_write.py`'s `teams_propose_send_
message`, on the model's raw `message` argument, BEFORE `normalize_send_
message_payload` ever sees it. The resulting plain-text string becomes
`payload["message"]` -- the exact value that is hashed, shown to the user
for approval (`ApprovalCard`), stored on the `ActionProposal`, and (via
`backend/api/execution_service.py`'s deterministic Phase-4G execution
path) replayed VERBATIM to `teams_send_message`/Power Automate. This
module is deliberately NEVER called from `write_validation.py` or
`execute_write.py` -- formatting an ALREADY-FORMATTED string a second
time is not guaranteed idempotent (see `test_teams_message_formatting.py`
for the explicit "formatter is not invoked again during execute" proof),
and formatting exactly once, before the payload is ever hashed, means
every downstream consumer (hash comparison, `ApprovalCard`, Power
Automate) sees the identical final string -- there is no second,
unapproved rewrite after approval.

SEMANTIC INTEGRITY -- REPRESENTATIONAL, NOT EDITORIAL: this module never
paraphrases, summarizes, reorders, adds, or removes semantic content. It
only normalizes PRESENTATION -- paragraph/list spacing, list markers,
blank-line collapsing, line-ending normalization. Every word, number,
identifier, URL, and command in the input survives byte-for-byte in the
output (list bullet/number MARKERS are the one deterministic mechanical
exception -- see `_format_block`'s own docstring). No HTML entity
escaping is applied (there is no HTML to escape into) -- literal text
such as `<script>` or `<b>` in the model's own text passes straight
through unchanged, because plain text is never parsed as markup by
anything downstream of this function.

SCOPE: recognizes plain paragraphs, bullet lists (`-`/`*`/`•`),
numbered lists (`1.`/`1)`), and a short single-line heading-like block
ending in `:` -- nothing else. Not a general-purpose Markdown/document
renderer.
"""
from __future__ import annotations

import re

_BULLET_LINE = re.compile(r"^[-*•]\s+(.+)$")
_NUMBERED_LINE = re.compile(r"^\d+[.)]\s+(.+)$")
_MAX_HEADING_LENGTH = 100
_BULLET_MARKER = "•"  # "•" -- the plain-text bullet character proven
# reliable through the current Power Automate/Teams integration (no HTML
# entity, no Markdown token -- a single printable Unicode character).

_BLOCK_SPLIT = re.compile(r"\n[ \t]*\n+")


def _format_block(block: str) -> str:
    """Formats ONE blank-line-delimited block. List markers are the only
    mechanical, deterministic exception to "byte-for-byte preserved" --
    `- item`/`* item`/`1. item`/`1) item` markers are replaced with a
    single canonical marker (`•` for every bullet list, sequential
    `N.` for every numbered list, renumbered from 1 regardless of the
    model's own source numbers) so the RENDERED list is always
    consistent -- this is a presentation mechanic, identical in spirit to
    how a browser/Teams client already renders `<ol>` sequentially
    regardless of source markup, never a change to the message's own
    semantic content (the item TEXT itself is never altered).
    """
    lines = [line.strip() for line in block.split("\n")]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    bullet_matches = [_BULLET_LINE.match(line) for line in lines]
    if all(bullet_matches):
        return "\n".join(f"{_BULLET_MARKER} {m.group(1)}" for m in bullet_matches if m)

    numbered_matches = [_NUMBERED_LINE.match(line) for line in lines]
    if all(numbered_matches):
        return "\n".join(f"{i}. {m.group(1)}" for i, m in enumerate(numbered_matches, start=1) if m)

    if len(lines) == 1 and lines[0].endswith(":") and len(lines[0]) <= _MAX_HEADING_LENGTH:
        return lines[0]

    return "\n".join(lines)


def format_teams_message(text: str) -> str:
    """Converts a model-authored plain-text answer into a deterministic,
    professionally structured plain-text message for the Teams "Post
    message" write path. Deterministic and pure -- no model call, no
    randomness, no I/O, no HTML.

    Returns `""` for blank/whitespace-only input (never a wrapper around
    nothing) so `normalize_send_message_payload`'s own existing non-empty
    check still correctly rejects an effectively-empty message.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return ""

    blocks = [b for b in _BLOCK_SPLIT.split(normalized) if b.strip()]
    formatted_blocks = [_format_block(block) for block in blocks]
    return "\n\n".join(b for b in formatted_blocks if b)
