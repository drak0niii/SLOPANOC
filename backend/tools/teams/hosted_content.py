"""Deterministic extraction of Teams hosted-content (inline/pasted image)
identifiers from a message's own HTML content.

A pasted/inline Teams image is represented in the live `teams.getMessages`
`content` HTML as an `<img>` tag whose `src` attribute is a Graph-shaped
URL (proven live for the real corpus this milestone validated against):

    <p><img src="https://graph.microsoft.com/beta/chats/{chatId}/messages/
    {messageId}/hostedContents/{hostedContentId}/$value" ...></p>

This module extracts ONLY `hostedContentId` -- deterministically, via the
Python standard library `html.parser.HTMLParser`, no LLM involvement,
mirroring `html_text.py`/`message_references.py`'s own "no LLM, fails
safe, never invents a value" discipline exactly.

WHY NOT HOSTNAME-BOUND: the recognized structure is the URL PATH shape
`/messages/{messageId}/hostedContents/{hostedContentId}/$value` -- present
identically whether the URL prefix is `https://graph.microsoft.com/beta/...`
or `https://graph.microsoft.com/v1.0/...` (both observed API-version
prefixes for this same path shape). Matching on the stable path segment
rather than a hardcoded hostname means this extractor keeps working
unchanged if the API version prefix changes, without weakening what it
recognizes -- it still requires the exact `/messages/.../hostedContents/
.../$value` shape, never a bare "any image URL."

PROVENANCE SAFETY: `expected_message_id` (the id of the message this
`raw_content` actually belongs to, already known by the caller -- see
get_messages.py) is REQUIRED, and a URL whose OWN embedded `messageId`
does not match it is silently skipped, never returned. This is the same
"do not accept a value not obtained from a real, matching retrieval"
discipline `docs/TEAMS_TOOL_CONTRACT.md` #8 already applies to chat ids --
a hosted-content id embedded in a URL that names a DIFFERENT message is
either a data anomaly or a forwarded/quoted image and must never be
attributed to the message currently being parsed.

This module is a provider-neutral, Teams-domain concept: it knows nothing
about Power Automate, `teams.getHostedContent`, or any transport-specific
response shape -- see get_hosted_content.py for where those enter, at the
integration boundary only.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Optional

# Matches the stable Graph URL PATH shape, anywhere in the string -- never
# anchored to a specific hostname/API-version prefix (see module
# docstring). `[^"'>\s]+` for each id segment: a Teams/Graph id is opaque
# and may itself contain `=`/`-`/`_`/other URL-safe characters, so this
# accepts any run of characters that cannot itself be part of an HTML
# attribute delimiter or whitespace, rather than assuming a narrower id
# alphabet that might not hold for every real id.
_HOSTED_CONTENT_PATH_PATTERN = re.compile(
    r"/messages/(?P<message_id>[^/\"'>\s]+)/hostedContents/(?P<hosted_content_id>[^/\"'>\s]+)/\$value"
)


class _ImgSrcExtractor(HTMLParser):
    """Walks HTML and collects every `<img src="...">` attribute value, in
    document order. Deliberately minimal -- this is not a general HTML
    sanitizer/renderer (mirrors html_text.py's own scoping), it only ever
    needs `src` attribute values.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "img":
            return
        src = dict(attrs).get("src")
        if src:
            self.sources.append(src)


def extract_hosted_content_ids(raw_content: str, expected_message_id: str) -> list[str]:
    """Extract every hosted-content id referenced by an `<img src="...">`
    tag in `raw_content` whose URL's own embedded `messageId` matches
    `expected_message_id`.

    Never raises: malformed HTML, a missing/empty `src`, a `src` that does
    not match the recognized hosted-content path shape, or a `src` whose
    embedded `messageId` does not match `expected_message_id` are all
    silently skipped -- exactly the same "skip, never invent, never fail
    the whole message" tolerance `message_references.py`/`get_messages.py`
    already use throughout this package.

    Returns hosted-content ids in first-seen order, deduplicated (the same
    id referenced by two `<img>` tags in one message -- e.g. a forwarded
    duplicate -- yields exactly one entry). Returns `[]` for
    `None`/empty/whitespace-only content, content with no `<img>` tags, or
    content whose only `<img>` tags do not match the recognized hosted-
    content path shape (an ordinary external image URL, for example).
    """
    if not raw_content or not raw_content.strip():
        return []
    if not expected_message_id:
        return []

    parser = _ImgSrcExtractor()
    try:
        parser.feed(raw_content)
        parser.close()
    except Exception:  # noqa: BLE001 -- malformed HTML must never fail message parsing.
        return []

    seen: set[str] = set()
    ordered_ids: list[str] = []
    for src in parser.sources:
        match = _HOSTED_CONTENT_PATH_PATTERN.search(src)
        if not match:
            continue
        if match.group("message_id") != expected_message_id:
            continue
        hosted_content_id = match.group("hosted_content_id")
        if hosted_content_id in seen:
            continue
        seen.add(hosted_content_id)
        ordered_ids.append(hosted_content_id)

    return ordered_ids
