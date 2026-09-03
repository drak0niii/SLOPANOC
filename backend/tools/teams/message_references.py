"""Deterministic parsing of Teams "messageReference" attachments -- a
quote/reply to another Teams message, not a file/image attachment.

Real Teams messages that quote or reply to another message carry an
`<attachment id="..."></attachment>` placeholder in `body.content` (HTML)
and a matching entry in the message's own `attachments` array with
`contentType: "messageReference"`, whose `content` field is itself a
JSON-*encoded string* (not a nested object) describing what was quoted:

    body.content:
      <attachment id="1788182857077"></attachment>
      <p>Is this it?</p>

    attachments:
      [
        {
          "id": "1788182857077",
          "contentType": "messageReference",
          "content": "{\\"messageId\\":\\"1788182857077\\",
                        \\"messagePreview\\":\\"Test message sent from SLOPANOC Gateway\\",
                        \\"messageSender\\":{\\"user\\":{\\"id\\":\\"...\\",
                                                          \\"displayName\\":\\"Referenced User\\"}}}"
        }
      ]

This is never a file/image attachment and must never be treated as one --
see get_messages.py, which uses `is_message_reference_attachment` to
decide which `<attachment>` ids to suppress from normalized text (rather
than rendering `"[Attachment]"` -- see html_text.py) and
`parse_message_reference` to build the structured `TeamsMessageReference`
the suppressed placeholder becomes instead.

Parsing is entirely deterministic (plain JSON parsing + dict lookups) and
fails safe: a malformed/unexpected shape never raises and never invents a
placeholder reference -- it is simply skipped, exactly like any other
message-shape tolerance already in this package (see get_messages.py's
per-item validation). A skipped/malformed reference still has its
`<attachment>` tag suppressed from `text` (it is still known,
structurally, to be a message reference, not a file) -- see
`is_message_reference_attachment`.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from backend.tools.teams.schemas import TeamsMessageReference

_MESSAGE_REFERENCE_CONTENT_TYPE = "messageReference"


def is_message_reference_attachment(attachment: Any) -> bool:
    """True if `attachment` (one entry of a raw message's `attachments`
    array) is a Teams message-reference (quote/reply) rather than a
    file/image attachment.
    """
    return (
        isinstance(attachment, dict)
        and attachment.get("contentType") == _MESSAGE_REFERENCE_CONTENT_TYPE
    )


def parse_message_reference(attachment: dict[str, Any]) -> Optional[TeamsMessageReference]:
    """Parse one `messageReference` attachment's JSON-encoded `content`
    into a `TeamsMessageReference`.

    Returns `None` -- never raises -- if `content` is missing, is not
    valid JSON, is not an object, or has no usable `messageId`. Never
    invents a value for any field: `preview`/`sender_name`/`sender_id` are
    included only when actually present in the parsed payload.
    """
    raw = attachment.get("content")
    if not isinstance(raw, str) or not raw.strip():
        return None

    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None

    if not isinstance(payload, dict):
        return None

    message_id = payload.get("messageId")
    if not isinstance(message_id, str) or not message_id.strip():
        return None

    preview = payload.get("messagePreview")
    if not isinstance(preview, str):
        preview = None

    sender_name: Optional[str] = None
    sender_id: Optional[str] = None
    sender = payload.get("messageSender")
    if isinstance(sender, dict):
        user = sender.get("user")
        if isinstance(user, dict):
            name = user.get("displayName")
            if isinstance(name, str):
                sender_name = name
            uid = user.get("id")
            if isinstance(uid, str):
                sender_id = uid

    return TeamsMessageReference(
        message_id=message_id,
        preview=preview,
        sender_name=sender_name,
        sender_id=sender_id,
    )
