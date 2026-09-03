"""Deterministic Teams chat membership retrieval --
docs/TEAMS_TOOL_CONTRACT.md #5 (`teams_get_members`).

Never performs LLM reasoning, mirrors `get_messages.py`'s tolerance for
malformed entries (skipped, never fatal, never fabricated).

NOT an ADK tool bound to `incident_manager` in this milestone -- this
milestone's only caller is `backend/api/source_reference.py`'s
`resolve_authoritative_contributors`, a deterministic post-processing
step `chat_service.py` runs after a turn already produced a structured
Teams source reference, so a Source drawer's `contributors` reflects the
chat's REAL, full membership rather than only the authors who happened to
send one of the (at most 5, display-capped) retrieved evidence examples.
Exposing this as a model-callable tool for direct "who is in this chat"
questions is a separate, later change -- deliberately out of scope here
(no new agent surface, no model-output parsing involved in building
`contributors`).

BUGFIX (contributors-missing investigation): this endpoint's shape was
implemented purely from docs/TEAMS_TOOL_CONTRACT.md #5's documented
contract (`{id, displayName}[]`), never independently verified against a
live gateway response the way `teams_get_messages`/`teams_list_chats`
were (see get_messages.py's own module docstring on exactly this class of
live-shape surprise -- a bare array instead of a wrapper, `senderName`
instead of an assumed key, etc.). Two defensive changes here, so a live
shape variance degrades to "still shows names" rather than "silently
drops every member":
  1. `member_id` is looked up across a small set of plausible key names
     (`id`/`memberId`/`userId`) instead of requiring exactly `"id"`.
  2. A missing/malformed member id NO LONGER discards the entry --
     `TeamsMember.id` is `Optional`, and this milestone's one consumer
     (`resolve_authoritative_contributors`) only ever reads
     `display_name`. Only a missing/empty/malformed DISPLAY NAME (tried
     across `displayName`/`display_name`/`name`) causes an entry to be
     skipped, since that is the one thing actually needed here.
Also see `_logger` below -- membership resolution failures/anomalies are
now logged with safe, structured detail (never chat_id, never a raw
payload) instead of silently degrading to an empty list with no trace.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from backend.gateway.power_automate_client import GatewayPayload, PowerAutomateClient, extract_items
from backend.gateway.safe_error import SafeErrorException, validation_error
from backend.tools.teams.schemas import TeamsGetMembersResult, TeamsMember

_logger = logging.getLogger(__name__)

# Tried in order for each raw member entry -- see the module docstring's
# "BUGFIX" note. Not a confirmed live shape, just tolerance for the most
# plausible variants until one is actually proven live.
_MEMBER_ID_KEYS = ("id", "memberId", "userId")
_DISPLAY_NAME_KEYS = ("displayName", "display_name", "name")


def _first_string(entry: dict[str, Any], keys: tuple[str, ...]) -> Optional[str]:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _parse_members(raw: GatewayPayload) -> list[TeamsMember]:
    items = extract_items(raw, wrapper_key="members")

    members: list[TeamsMember] = []
    skipped_no_display_name = 0
    for entry in items:
        if not isinstance(entry, dict):
            skipped_no_display_name += 1
            continue
        display_name = _first_string(entry, _DISPLAY_NAME_KEYS)
        if display_name is None:
            skipped_no_display_name += 1
            continue
        members.append(TeamsMember(id=_first_string(entry, _MEMBER_ID_KEYS), display_name=display_name))

    if skipped_no_display_name and not members:
        # Safe, structured diagnostic only -- counts, never any member
        # name/id/chat_id/raw payload. A non-zero raw item count with zero
        # usable members is the strongest live signal of a field-name
        # mismatch against this module's `_DISPLAY_NAME_KEYS` assumption.
        _logger.warning(
            "teams_get_members: %d raw member entries were retrieved but none "
            "had a usable display name (tried keys: %s) -- the gateway's "
            "member shape may not match this parser's assumptions",
            skipped_no_display_name,
            _DISPLAY_NAME_KEYS,
        )
    return members


def teams_get_members(chat_id: str) -> dict[str, Any]:
    """Retrieve the authoritative, full member list of one already-
    resolved Teams chat.

    Args:
      chat_id: A chat id previously returned by `teams_list_chats` (or, in
        this milestone's one caller, the `chat_id` a successful
        `incident_manager` "ok" response already carried for the same
        turn) -- never a free-text chat name.

    Returns:
      On success, `{"chat_id", "members": [{"id", "display_name"}, ...]}`
      (`TeamsGetMembersResult`, dumped to a plain dict). An empty
      `members` list is a valid, non-error result. On failure -- an empty/
      invalid `chat_id`, or any gateway failure -- a dict with a single
      `error` key holding a SafeError; callers must treat this as
      "membership unavailable," never invent participants to fill the gap
      (see source_reference.py's `resolve_authoritative_contributors`).
    """
    if not chat_id or not chat_id.strip():
        return {"error": validation_error("A Teams chat id is required.").safe_error.to_dict()}

    client = PowerAutomateClient()
    try:
        raw = client.get_members(chat_id)
        members = _parse_members(raw)
    except SafeErrorException as exc:
        # Safe by construction: `exc.safe_error` never carries the gateway
        # URL/secret/raw response (see safe_error.py) -- logging its
        # error_code/retryable is the same class of detail already safe to
        # return to a caller.
        _logger.warning(
            "teams_get_members: gateway call failed (error_code=%s, retryable=%s)",
            exc.safe_error.error_code,
            exc.safe_error.retryable,
        )
        return {"error": exc.safe_error.to_dict()}

    result = TeamsGetMembersResult(chat_id=chat_id, members=members)
    return result.model_dump(mode="json")
