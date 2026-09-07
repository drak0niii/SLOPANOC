"""Internal ADK session-state keys and title derivation for POST-5.1 B4
(saved-conversation rehydration). A deliberate LEAF module -- imported by
both `chat_service.py` (writes the marker/title/activity at turn time)
and `session_history_service.py` (reads them for the saved-chat list),
but imports nothing from either, so there is no import cycle between the
two.

WHY PLAIN, UNPREFIXED KEYS (B4A correction pass, verified against the
installed ADK 1.33.0 source, `google/adk/sessions/state.py`): ADK reserves
three key prefixes with real, non-obvious semantics --

  - `State.APP_PREFIX` ("app:")  -- shared by EVERY session for this app,
    never per-session. Using it here would make one chat's title/
    visibility bleed into every other chat.
  - `State.USER_PREFIX` ("user:") -- shared by every session for this
    user. Same problem, scoped to the user instead of the app.
  - `State.TEMP_PREFIX` ("temp:") -- explicitly stripped before
    persistence (`BaseSessionService._trim_temp_delta_state`,
    `DatabaseSessionService.append_event`) -- never durable, wrong for
    something that must survive a restart.

A plain, unprefixed key is correctly session-scoped AND durable -- exactly
what these three keys need. This also matches this codebase's OWN existing
convention (`backend/approval/service.py`'s `PENDING_ACTION_PROPOSAL_STATE_KEY
= "pending_action_proposal"`, `backend/api/case_service.py`'s
`ACTIVE_CASE_ID_STATE_KEY = "active_case_id"`, `backend/tools/teams/
state_keys.py`'s `SELECTED_TEAMS_CHAT_ID_STATE_KEY`, etc.) -- none of
which use an app-wide prefix; the house style is a descriptive,
unprefixed snake_case value with a `_STATE_KEY`-suffixed constant name.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

_logger = logging.getLogger(__name__)

# --- State keys --------------------------------------------------------

# Tri-state semantics (B4A correction pass), never plain boolean-missing:
#   present, True  -> known real saved conversation (has a genuine user-
#                      visible message) -- list it.
#   present, False -> known EMPTY session (never sent, or B3 image-upload-
#                      only with no send) -- never list it, never re-check.
#   ABSENT entirely -> legacy (pre-B4B) or a narrow post-write-crash
#                      window -- one-time compatibility backfill, bounded
#                      per request (see session_history_service.py).
HAS_VISIBLE_MESSAGE_STATE_KEY = "has_visible_message"

# Set once, at the FIRST genuine user-visible turn (never rewritten by a
# later turn -- see `record_user_turn_activity`'s own docstring), or
# overwritten by an explicit manual rename (`rename_session`). Both write
# the SAME key, so "whichever wrote last wins" is the entire persistence
# model -- no separate "is this a manual rename" flag needed.
CHAT_TITLE_STATE_KEY = "chat_title"

# B4B CORRECTION PASS: the real, user-visible "last active" timestamp for
# sidebar ordering -- deliberately DIFFERENT from ADK's own
# `Session.last_update_time`, which also moves on every unrelated state-
# only write (a manual rename, a legacy-marker backfill, an approval/
# selection/Teams/Case state_delta -- none of which are a real chat turn).
# Set/advanced ONLY by `record_user_turn_activity`, to the REAL persisted
# user Event's own `.timestamp` -- never `datetime.now()`, never touched
# by `rename_session` or any other state write in this codebase.
CHAT_ACTIVITY_AT_STATE_KEY = "chat_activity_at"

# --- Title derivation ----------------------------------------------------

# Mirrors the frontend's own `deriveChatTitle` (src/data/mock.ts) exactly
# -- same truncation length, same whitespace-collapse, same ellipsis --
# so a server-derived title is never visually different from what the
# user already saw client-side for the same message, live, before any
# refresh. Do not invent a different rule here.
_AUTO_TITLE_MAX_LENGTH = 48
_WHITESPACE_RE = re.compile(r"\s+")

# A MANUAL rename (rename_session, POST-5.1 B4B) is a distinct, more
# generous bound -- the existing frontend `RENAME_CHAT` reducer case never
# truncates a user's own rename, so this backend must not silently
# truncate one either (B4A correction pass, explicit instruction). An
# over-limit manual title is REJECTED (`validation_error`), never cut
# down -- this bound exists only to stop unbounded abuse, not to shape
# normal use.
MAX_MANUAL_TITLE_LENGTH = 200


def derive_chat_title(text: str) -> str:
    """Deterministic, frontend-rule-matching title from a message's raw
    text. Never used for a manual rename (see `MAX_MANUAL_TITLE_LENGTH`
    above) -- only for the automatic first-turn title.
    """
    collapsed = _WHITESPACE_RE.sub(" ", text.strip())
    if not collapsed:
        return "New chat"
    if len(collapsed) > _AUTO_TITLE_MAX_LENGTH:
        return f"{collapsed[:_AUTO_TITLE_MAX_LENGTH]}…"
    return collapsed


# --- Genuine-user-content discriminator ---------------------------------

# THE one safe test for "this is a real, user-visible message" (B4A
# finding, verified against ADK's own `contents._contains_empty_content`)
# -- `author == "user"` ALONE is NOT sufficient: `persist_state_delta`-
# authored events (this module's own writes included) and ADK's own
# rewind-marker events also have `author == "user"` but `content is
# None`. A genuine turn always has `content.role == "user"` with real,
# non-empty `parts`. Canonical, single definition -- `session_history_
# service.py` imports this rather than keeping its own copy.
def is_genuine_user_content_event(event: Any) -> bool:
    return bool(
        event.author == "user"
        and event.content
        and event.content.role == "user"
        and event.content.parts
    )


def find_user_event_for_invocation(events: list[Any], invocation_id: str) -> Optional[Any]:
    """The genuine user-content event for ONE specific invocation -- used
    by `record_user_turn_activity` to read that event's real, already-
    persisted `.timestamp` (never `datetime.now()`). A real turn has
    exactly one such event (`Runner._append_new_message_to_session`
    appends it before the agent ever runs); this returns the first match
    found. No "active branch" filtering needed here -- the caller always
    supplies the CURRENT turn's own `invocation_id`, fresh from an event
    the Runner just yielded, which can never itself be something a rewind
    has excluded.
    """
    for event in events:
        if event.invocation_id == invocation_id and is_genuine_user_content_event(event):
            return event
    return None


async def record_user_turn_activity(
    session_service,
    session,
    message_text: str,
    invocation_id: str,
) -> None:
    """Called from `chat_service.py`'s `_run_turn_events`, at the exact
    point it observes the FIRST event yielded by `Runner.run_async` for a
    turn (`first_event_seen` in that function) -- on EVERY genuine user
    turn, not just the first. B4A's correction pass proved via direct
    source inspection that ADK's `Runner` always fully appends the real
    user-content event (a durably committed DB write for
    `DatabaseSessionService`) BEFORE `execute()`/the agent's own run ever
    starts, so no event can ever be yielded unless that append already
    succeeded. This makes "first yielded event observed" a PROVEN-safe
    trigger point: it fires exactly when a genuine user message is known
    to exist, regardless of whether the assistant's own turn later
    succeeds, fails, or is cancelled/abandoned.

    Writes, in ONE `persist_state_delta` call (never two separate
    events for one turn):

      - `HAS_VISIBLE_MESSAGE_STATE_KEY`/`CHAT_TITLE_STATE_KEY` -- ONLY on
        the session's first-ever genuine turn (checked via `session.state`
        -- never rewritten by a later turn; a manual rename overwrites
        `chat_title` afterward via the exact same key, and this function
        never touches it again once `has_visible_message` is already
        true).
      - `CHAT_ACTIVITY_AT_STATE_KEY` -- on EVERY genuine turn, set to the
        REAL persisted user `Event.timestamp` for THIS invocation (never
        `datetime.now()`, never the assistant's own response time, never
        touched by rename/approval/selection/Teams/Case/other unrelated
        state writes -- see `session_state_keys.py`'s own module
        docstring for why this must differ from ADK's generic
        `Session.last_update_time`).

    `session.state`/`session.events` are read directly from the passed
    `session` (never re-fetched here) -- the caller is responsible for
    passing an up-to-date session (see chat_service.py's own comment on
    why it re-fetches immediately before calling this, to avoid ADK's
    "session has been modified in storage" staleness guard).

    If the genuine user event for `invocation_id` cannot be found in
    `session.events` (should never happen given the proof above -- would
    indicate a genuinely unexpected ADK behavior change), this logs a
    warning and skips the activity-timestamp update for this turn rather
    than fabricating a wall-clock value; the visibility/title write (if
    this is the first turn) still proceeds using `message_text`, which
    the caller already has independent of any event lookup.
    """
    delta: dict[str, Any] = {}

    if not session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY):
        delta[HAS_VISIBLE_MESSAGE_STATE_KEY] = True
        delta[CHAT_TITLE_STATE_KEY] = derive_chat_title(message_text)

    user_event = find_user_event_for_invocation(session.events, invocation_id)
    if user_event is not None:
        delta[CHAT_ACTIVITY_AT_STATE_KEY] = user_event.timestamp
    else:
        # DEBUG, not WARNING (deliberate) -- this fires routinely under
        # test doubles (`FakeRunner` never appends a genuine matching
        # user-content event, by its own documented design) and, even in
        # the genuine-production case this should never reach per B4A's
        # proven happens-before guarantee, degrades gracefully (has_
        # visible_message/chat_title still get set correctly on the
        # session's first turn) rather than representing an operator-
        # actionable failure.
        _logger.debug(
            "record_user_turn_activity: no genuine user event found for invocation_id=%s -- "
            "chat_activity_at not updated this turn.",
            invocation_id,
        )

    if delta:
        await session_service.persist_state_delta(session, delta)
