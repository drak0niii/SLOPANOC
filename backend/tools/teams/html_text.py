"""Deterministic Teams message content normalization.

Teams message `content` is HTML for `contentType: "html"` messages (the
common case, confirmed against the live gateway), e.g.:

    <p>text&nbsp;</p>
    <emoji alt="(smile)" ...></emoji>
    <attachment id="..."></attachment>

This module turns that into clean, readable plain text using only the
Python standard library `html.parser.HTMLParser` -- no LLM, no
third-party HTML library, fully deterministic. It is used exclusively by
`teams_get_messages` (backend/tools/teams/get_messages.py); the raw,
unmodified content is always preserved separately as
`TeamsMessage.raw_content`, so normalization here is never lossy for the
caller even though it discards markup.

Some `<attachment id="...">` tags are not file/image attachments at all --
they are Teams "message reference" placeholders (a quote/reply to another
message), whose real content lives in the message's separate `attachments`
array, parsed by `message_references.py`. Those must never render as the
generic `"[Attachment]"` marker (that would misrepresent a reply as an
unrelated file). `normalize_teams_content`'s `suppressed_attachment_ids`
parameter tells this module which `<attachment>` ids to omit entirely from
the rendered text instead -- get_messages.py passes the ids it already
classified as message references. An attachment id not in that set still
renders as `"[Attachment]"`, unchanged from the original behavior, for
genuine/unclassified attachments (file/image support is not implemented
yet).

LIMITATION: this is a minimal, purpose-built extractor for the tag
vocabulary Teams messages actually use (`p`, `br`, `div`, `emoji`,
`attachment`, plus generic inline tags whose text content passes through
unchanged) -- it is not a general HTML sanitizer. Malformed/unclosed
`<emoji>` or `<attachment>` tags could suppress trailing content; Teams-
generated message HTML is system-generated and well-formed in practice,
so this tradeoff is accepted rather than adding a general HTML-repair
step.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import AbstractSet, Optional

_BLOCK_TAGS = frozenset({"p", "div"})
_BREAK_TAGS = frozenset({"br"})
_REPLACED_TAGS = frozenset({"emoji", "attachment"})

_WHITESPACE_RUN = re.compile(r"[ \t]+")
_BLANK_LINE_RUN = re.compile(r"\n{3,}")


class _TeamsHtmlTextExtractor(HTMLParser):
    """Walks Teams message HTML and emits clean text.

    `convert_charrefs=True` (the `HTMLParser` default) already decodes
    HTML entities (`&nbsp;`, `&amp;`, ...) into their Unicode characters
    before `handle_data` sees them -- no separate entity-decoding step is
    needed.

    `<emoji>`/`<attachment>` are replaced with a fixed marker on their
    start tag (an `<attachment>` whose id is in `suppressed_attachment_ids`
    is instead omitted entirely -- see the module docstring); any text
    nested inside them (between the start and matching end tag) is
    suppressed via `_skip_stack`, so their marker is never duplicated or
    polluted by raw inner content.
    """

    def __init__(self, suppressed_attachment_ids: AbstractSet[str] = frozenset()) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_stack: list[str] = []
        self._suppressed_attachment_ids = suppressed_attachment_ids

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in _BREAK_TAGS:
            self._parts.append("\n")
            return

        if tag in _REPLACED_TAGS:
            if tag == "emoji":
                alt = dict(attrs).get("alt")
                self._parts.append(alt if alt else "[emoji]")
            else:  # attachment
                attachment_id = dict(attrs).get("id")
                if attachment_id not in self._suppressed_attachment_ids:
                    self._parts.append("[Attachment]")
            self._skip_stack.append(tag)
            return

        # Any other tag (span, a, strong, ul, li, ...) contributes no text
        # of its own -- its contained text arrives via handle_data.

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack and self._skip_stack[-1] == tag:
            self._skip_stack.pop()
            return
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def normalize_teams_content(
    raw_content: Optional[str],
    suppressed_attachment_ids: AbstractSet[str] = frozenset(),
) -> str:
    """Convert raw Teams message content (HTML or plain text) into clean,
    readable text. Deterministic -- never invokes a model.

    Handles, at minimum:
      - line/block tags: `<p>`, `<br>`, `<div>` -> line breaks
      - HTML entities (`&nbsp;`, `&amp;`, ...) -> their Unicode characters
      - `<emoji ...>` -> its `alt` attribute value if present, else
        `"[emoji]"`
      - `<attachment ...>` -> `"[Attachment]"` (never silently dropped),
        UNLESS its `id` is in `suppressed_attachment_ids`, in which case it
        is omitted entirely (used for Teams message-reference placeholders
        -- see the module docstring; not for genuine file/image
        attachments, which always keep the `"[Attachment]"` marker)
      - any other/unknown tag -> stripped, its inner text kept

    Returns `""` for empty/whitespace-only/`None` input. Safe to call on
    plain text (`contentType: "text"`) as well as HTML -- text with no
    tags passes through with only whitespace cleanup applied.
    """
    if not raw_content or not raw_content.strip():
        return ""

    parser = _TeamsHtmlTextExtractor(suppressed_attachment_ids)
    parser.feed(raw_content)
    parser.close()
    text = parser.get_text()

    # A decoded &nbsp; reads as a regular space in plain text.
    text = text.replace("\xa0", " ")
    text = _WHITESPACE_RUN.sub(" ", text)
    text = _BLANK_LINE_RUN.sub("\n\n", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines).strip()

    return text
