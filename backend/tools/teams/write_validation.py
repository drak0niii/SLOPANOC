"""Deterministic normalization/validation for Teams write payloads.

Used by BOTH the propose tools (backend/tools/teams/propose_write.py) and
the execute tools (backend/tools/teams/execute_write.py) -- calling the
exact same function at both proposal-creation time and execution time is
what guarantees "no model-side rewriting after approval" is actually safe
by construction: if execution-time input normalizes to anything other
than the exact payload that was approved, `authorize_write`'s hash
comparison (backend/approval/policy_gate.py) denies it deterministically.
This module does not know about proposals/approval at all -- it only ever
turns raw model-supplied arguments into the canonical payload shape the
Power Automate `teams.createChat`/`teams.sendMessage` operations expect,
or raises a `SafeErrorException` (`validation_error`) if the input cannot
be normalized safely.

No identity resolution happens here (instruction: "Do NOT implement name
-> email identity resolution."): a participant must already be a complete
email address; anything else is a validation error the caller (team_manager,
via incident_manager's prompt) is expected to turn into a clarifying
question, never a guess.
"""
from __future__ import annotations

import re
from typing import Any

from backend.gateway.safe_error import validation_error

# Deliberately simple/strict, not a full RFC 5322 implementation: this
# only needs to catch "not a plausible complete email address" (missing
# '@', missing domain, embedded whitespace) -- exactly the shape the
# instruction asks to validate ("validate complete email syntax
# deterministically"), not to be a general-purpose email validator.
_EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

_MIN_OTHER_PARTICIPANTS = 2


def _is_valid_email(candidate: str) -> bool:
    return bool(_EMAIL_PATTERN.match(candidate))


def normalize_create_chat_payload(title: str, members: list[str]) -> dict[str, Any]:
    """Validate and normalize `teams.createChat` input into the exact
    payload shape the gateway expects: `{"title": str, "members":
    [str, ...]}`.

    Rules (instruction section 7):
      - `title` must be non-empty after trimming.
      - at least 2 (other) participant email addresses.
      - every participant must be a complete, syntactically valid email
        address -- trimmed, never guessed/completed.
      - duplicate participants (case-insensitive) are rejected, not
        silently deduplicated -- a duplicate almost always signals a
        mistake in what the user asked for, and this framework never
        silently changes what will be sent.
      - the connection owner is never added here -- Power Automate/Graph
        adds it; `members` is exactly the other participants.

    Raises `SafeErrorException` (`validation_error`) for any violation.
    """
    trimmed_title = (title or "").strip()
    if not trimmed_title:
        raise validation_error("A chat title is required to create a Teams chat.")

    if not isinstance(members, list):
        raise validation_error("At least 2 participant email addresses are required.")

    trimmed_members = [m.strip() for m in members if isinstance(m, str) and m.strip()]

    if len(trimmed_members) < _MIN_OTHER_PARTICIPANTS:
        raise validation_error(
            f"At least {_MIN_OTHER_PARTICIPANTS} participant email addresses are required "
            "to create a Teams chat."
        )

    invalid = [m for m in trimmed_members if not _is_valid_email(m)]
    if invalid:
        raise validation_error(
            "One or more participant entries are not complete email addresses: "
            + ", ".join(invalid)
        )

    seen_casefolded: set[str] = set()
    duplicates: list[str] = []
    for m in trimmed_members:
        key = m.casefold()
        if key in seen_casefolded:
            duplicates.append(m)
        seen_casefolded.add(key)
    if duplicates:
        raise validation_error(
            "Duplicate participant email addresses were provided: " + ", ".join(duplicates)
        )

    return {"title": trimmed_title, "members": trimmed_members}


def normalize_send_message_payload(chat_id: str, message: str) -> dict[str, Any]:
    """Validate and normalize `teams.sendMessage` input into the exact
    payload shape the gateway expects: `{"chatId": str, "message": str}`.

    Rules (instruction section 8):
      - `chat_id` must be a non-empty string -- this function does not
        (and cannot, without another gateway round trip) verify the id is
        a real Teams chat; the guarantee that it is a genuinely resolved
        id, never invented, is the caller's responsibility (incident_manager
        always obtains it from `teams_list_chats`/the selected-chat state,
        per its instruction).
      - `message` must be non-empty after trimming. It is intentionally
        NOT further trimmed/rewritten beyond that -- the exact text the
        user sees during approval must be the exact text sent (instruction:
        "The exact message displayed for approval must be the exact
        message sent.").

    Raises `SafeErrorException` (`validation_error`) for any violation.
    """
    trimmed_chat_id = (chat_id or "").strip()
    if not trimmed_chat_id:
        raise validation_error("A resolved Teams chat id is required to send a message.")

    if not message or not message.strip():
        raise validation_error("A message is required to send a Teams message.")

    return {"chatId": trimmed_chat_id, "message": message}
