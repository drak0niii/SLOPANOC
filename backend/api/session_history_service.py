"""Safe saved-conversation list + transcript rehydration (POST-5.1 B4B).

THREE OPERATIONS, mirroring the B4A/B4B-correction-pass design exactly:

  - `list_saved_sessions` -- `GET /api/sessions`. Cheap: reads ADK's own
    `list_sessions` (no events, but a real merged `.state`/
    `.last_update_time`), applies the tri-state `has_visible_message`
    marker, and performs a bounded, per-request legacy-marker/activity
    repair for sessions that need one (pre-B4B session, or a known-
    visible session created before the `chat_activity_at` correction) --
    never a per-row event read for a session that already has everything
    it needs.
  - `get_session_history` -- `GET /api/sessions/{id}/history`. Reuses
    `chat_service.py`'s ALREADY-TESTED `_active_events`/`_extract_final_text`/
    `_non_thought_text` verbatim (imported, never reimplemented) to
    reconstruct only the active-branch, user-visible transcript, then
    joins in LINKED attachments with exactly ONE ownership-scoped query
    (never one query per message -- B4B instruction section 18).
  - `rename_session` -- `PATCH /api/sessions/{id}`. Durable, explicit-
    validation rename via the SAME `CHAT_TITLE_STATE_KEY` the automatic
    first-turn title write uses -- last-write-wins, no new table. Never
    touches `CHAT_ACTIVITY_AT_STATE_KEY` (B4B correction pass) -- a
    rename must never reorder the saved-chat sidebar.

TRANSCRIPT AUTHORITY (locked, B4A/B4B): text comes from ADK's active
event history; attachment OWNERSHIP comes from `slopanoc_chat_attachments`
(never derived by parsing a `gs://` URI out of an ADK event); attachment
BINARY is never touched here at all (the frontend fetches content
separately, by `attachment_id`, from the existing B2 endpoint).

SIDEBAR ORDERING (B4B correction pass): `SessionSummaryDTO.updated_at` is
`CHAT_ACTIVITY_AT_STATE_KEY` -- the real timestamp of the latest GENUINE
user-visible chat turn -- NEVER ADK's own generic `Session.last_update_time`,
which also moves on every unrelated state-only write this codebase already
performs (approval, selection, Teams, Case, a manual rename, or this very
module's own legacy-marker repair). Sorting/display by the generic
timestamp would incorrectly bump an old, untouched conversation to the
top of the sidebar merely because some unrelated background state write
happened to touch its session row.

NEVER RAW ADK EVENTS: nothing in this module returns `Session.state`,
`Session.events`, `Event.actions`, a function call/response, a thought
part, or any internal identifier beyond the two identities this module
itself defines (`turn_id`/`message_id`). See `test_session_history_*`
for the deliberate "plant a secret in a synthetic event, prove it never
serializes" tests this safety boundary depends on.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.api.chat_service import _active_events, _extract_final_text, _non_thought_text
from backend.api.schemas import (
    AttachmentHistoryDTO,
    SessionHistoryMessageDTO,
    SessionHistoryResponse,
    SessionSummaryDTO,
)
from backend.api.session_service import ApiSessionService
from backend.api.session_state_keys import (
    CHAT_ACTIVITY_AT_STATE_KEY,
    CHAT_TITLE_STATE_KEY,
    HAS_VISIBLE_MESSAGE_STATE_KEY,
    MAX_MANUAL_TITLE_LENGTH,
    derive_chat_title,
    is_genuine_user_content_event,
)
from backend.api.turn_source_references import resolve_turn_source_references
from backend.attachments.models import ChatAttachmentStatus
from backend.attachments.service import AttachmentService
from backend.gateway.safe_error import validation_error

_logger = logging.getLogger(__name__)


def _iso(timestamp: float) -> str:
    """Every timestamp this module emits comes from a REAL field -- either
    `CHAT_ACTIVITY_AT_STATE_KEY` (itself always a real, previously-
    persisted `Event.timestamp` -- see `session_state_keys.py`'s
    `record_user_turn_activity`) or, only as a documented TEMPORARY
    fallback for a known-visible session that has not yet been repaired
    this request (see `list_saved_sessions`), `Session.last_update_time`
    -- never invented (B4A instruction section 17). Both are epoch-
    seconds floats (`google.adk.utils.platform_time.get_time()` / SQL
    `update_time` mapped through `to_session()`).
    """
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _user_text(event: Any) -> Optional[str]:
    """Same safe-text-join discipline as `_extract_final_text` (skip
    `thought` parts, join non-empty text) -- just without the
    `is_final_response()` gate, which is an assistant-only concept.
    """
    texts = [t for t in (_non_thought_text(p) for p in event.content.parts) if t]
    text = "\n".join(texts)
    return text or None


class _Turn:
    """In-memory-only accumulator while walking one session's active
    events -- never serialized itself; `SessionHistoryMessageDTO`s are
    built from it once the walk is done.
    """

    __slots__ = ("turn_id", "user_text", "user_timestamp", "final_text", "final_timestamp")

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        self.user_text: Optional[str] = None
        self.user_timestamp: Optional[float] = None
        self.final_text: Optional[str] = None
        self.final_timestamp: Optional[float] = None


def _project_turns(events: list[Any]) -> list[_Turn]:
    """Walks the ALREADY active-branch-filtered event list once, grouping
    by `invocation_id` (B4A: reliable for events ADK itself appended
    during one `run_async` call). For each invocation, keeps the genuine
    user message (there is exactly one per real turn -- ADK's own
    `Runner._append_new_message_to_session` appends it before the agent
    ever runs) and the LAST `is_final_response()`-true text seen for that
    same invocation (mirrors `chat_service._run_turn_events`'s own
    `final_text = text` overwrite-as-you-go loop, scoped per turn instead
    of per whole call). A turn with a user message but no final event
    (model failure, cancellation, or still in flight) keeps
    `final_text=None` -- the caller renders the user message alone,
    never a fabricated assistant reply (B4A instruction section 12).

    Turns with NO genuine user event at all (a rewind marker would have
    already been stripped by `_active_events` before this function ever
    sees it; a `persist_state_delta`-only event has `content=None` and
    satisfies neither branch below) are silently absent from the result
    -- there is nothing user-visible to project.
    """
    order: list[str] = []
    turns: dict[str, _Turn] = {}

    def _get(turn_id: str) -> _Turn:
        if turn_id not in turns:
            turns[turn_id] = _Turn(turn_id)
            order.append(turn_id)
        return turns[turn_id]

    for event in events:
        if is_genuine_user_content_event(event):
            # POST-5.1 B5 fix: an image-only user turn (Content with only
            # file_data/URI parts, no text part at all) has `_user_text(
            # event) is None` -- a turn must still be created for it
            # (instruction section 10: "do NOT drop the turn merely
            # because it has no text part"). `user_timestamp is None` is
            # the correct "not yet set" sentinel here (an event's own
            # `.timestamp` is never None), NOT `user_text is None` --
            # `user_text` is now always a real string (possibly `""`)
            # once a turn has been observed at all.
            turn = _get(event.invocation_id)
            if turn.user_timestamp is None:
                turn.user_text = _user_text(event) or ""
                turn.user_timestamp = event.timestamp
            continue

        final_text = _extract_final_text(event)
        if final_text is not None:
            turn = _get(event.invocation_id)
            turn.final_text = final_text
            turn.final_timestamp = event.timestamp

    return [turns[turn_id] for turn_id in order if turns[turn_id].user_timestamp is not None]


def _genuine_user_events_in_order(events: list[Any]) -> list[Any]:
    """Every genuine user event (text-bearing OR image-only -- POST-5.1
    B5 fix, instruction section 10: an image-only genuine user turn must
    count as a visible chat turn, not be silently excluded from legacy-
    repair consideration), in chronological order, from an ALREADY
    active-branch-filtered event list. The FIRST entry is what
    `chat_title` should be derived from (an image-only first turn
    correctly falls back to "New chat" -- see `derive_chat_title`'s own
    empty-string handling, unchanged); the LAST entry's `.timestamp` is
    what `chat_activity_at` should be repaired to (the most recent
    genuine turn still on the active branch -- never a rewound-away one,
    since `_active_events` has already excluded those before this
    function ever sees the list).
    """
    return [e for e in events if is_genuine_user_content_event(e)]


async def get_session_history(
    session_service: ApiSessionService,
    attachment_service: AttachmentService,
    session_id: str,
    user_id: str,
) -> SessionHistoryResponse:
    """Ownership enforced identically to every other session-scoped route
    (`session_service.get_session` raises the same anti-enumeration
    `not_found` for an unknown OR foreign session id) BEFORE anything
    else happens.
    """
    session = await session_service.get_session(session_id, user_id)
    active = _active_events(session.events)
    projected_turns = _project_turns(active)

    # Instruction section 18: exactly ONE ownership-scoped query for the
    # whole history request, never one per message. `get_for_owner_session`
    # returns every status (READY/LINKED/DELETED) -- filtered here to
    # genuinely LINKED, non-null-message_id rows only, so a still-
    # unlinked draft upload or a deleted attachment can never appear in
    # a rendered transcript.
    all_attachments = await attachment_service.get_for_owner_session(user_id, session_id)
    attachments_by_turn: dict[str, list[Any]] = {}
    for record in all_attachments:
        if record.status != ChatAttachmentStatus.LINKED.value or not record.message_id:
            continue
        attachments_by_turn.setdefault(record.message_id, []).append(record)

    messages: list[SessionHistoryMessageDTO] = []
    for turn in projected_turns:
        turn_attachments = [
            AttachmentHistoryDTO(
                attachment_id=r.attachment_id,
                filename=r.original_filename,
                mime_type=r.mime_type,
                size_bytes=r.size_bytes,
            )
            for r in attachments_by_turn.get(turn.turn_id, [])
        ]
        messages.append(
            SessionHistoryMessageDTO(
                message_id=f"{turn.turn_id}:user",
                turn_id=turn.turn_id,
                role="user",
                text=turn.user_text or "",
                created_at=_iso(turn.user_timestamp or 0.0),
                attachments=turn_attachments,
            )
        )
        if turn.final_text is not None:
            # B7 corrective pass -- re-projects this turn's own durably
            # persisted Teams/governed-KM provenance (backend/api/turn_
            # source_references.py), keyed by the SAME `turn.turn_id`
            # (ADK invocation_id) this message already carries. `session
            # .state` here is ALREADY the active-branch-consistent value
            # ADK's own rewind mechanism maintains -- a discarded branch's
            # own entry was already removed from state by rewind itself
            # (see that module's own docstring); no separate filtering is
            # needed here beyond what `_active_events`/`projected_turns`
            # already do for the message list itself.
            source, knowledge_sources = resolve_turn_source_references(session.state, turn.turn_id)
            messages.append(
                SessionHistoryMessageDTO(
                    message_id=f"{turn.turn_id}:assistant",
                    turn_id=turn.turn_id,
                    role="assistant",
                    text=turn.final_text,
                    created_at=_iso(turn.final_timestamp or 0.0),
                    attachments=[],
                    source=source,
                    knowledge_sources=knowledge_sources,
                )
            )

    return SessionHistoryResponse(session_id=session_id, messages=messages)


async def _backfill_unknown_marker(session_service: ApiSessionService, session: Any, user_id: str) -> Optional[bool]:
    """ONE-TIME compatibility repair for a session whose `has_visible_
    message` key is entirely ABSENT (pre-B4B legacy session, or the
    narrow post-write-crash window `session_state_keys.py`'s own
    docstring describes). Re-fetches the FULL session (with events -- the
    cheap `list_sessions` result never has them), applies the exact same
    active-branch + genuine-user-content projection the history endpoint
    itself uses, and persists whichever outcome it finds so this session
    is NEVER re-checked this way again.

    B4B CORRECTION: derives `chat_title` from the FIRST genuine user turn
    (unchanged) but `chat_activity_at` from the LAST/most-recent genuine
    active-branch user turn -- a legacy conversation with many turns must
    sort by when it was last genuinely active, not by its opening message.
    Returns the now-known `has_visible_message` value.
    """
    full_session = await session_service.get_session(session.id, user_id)
    active = _active_events(full_session.events)
    genuine_turns = _genuine_user_events_in_order(active)

    if genuine_turns:
        await session_service.persist_state_delta(
            full_session,
            {
                HAS_VISIBLE_MESSAGE_STATE_KEY: True,
                CHAT_TITLE_STATE_KEY: derive_chat_title(_user_text(genuine_turns[0]) or ""),
                CHAT_ACTIVITY_AT_STATE_KEY: genuine_turns[-1].timestamp,
            },
        )
        return True

    await session_service.persist_state_delta(full_session, {HAS_VISIBLE_MESSAGE_STATE_KEY: False})
    return False


async def _repair_missing_activity(session_service: ApiSessionService, session: Any, user_id: str) -> Optional[float]:
    """ONE-TIME repair for a session that is already KNOWN-visible
    (`has_visible_message=True`) but is missing `chat_activity_at` -- the
    "legacy edge case" (B4B correction pass): a session marked visible by
    an earlier, uncommitted revision of this same B4B work, or a
    partially-repaired fixture, before `chat_activity_at` existed at all.

    Derives it from the LAST genuine active-branch user turn, exactly
    like `_backfill_unknown_marker` does for a brand-new legacy repair --
    never re-derives `chat_title` (already correct, untouched).

    Returns the repaired timestamp, or `None` if the session's active
    transcript genuinely contains no reconstructable genuine user turn at
    all (a real data inconsistency, not an expected case -- logged so it
    can be investigated rather than silently masked with a fabricated
    value).
    """
    full_session = await session_service.get_session(session.id, user_id)
    active = _active_events(full_session.events)
    genuine_turns = _genuine_user_events_in_order(active)

    if not genuine_turns:
        _logger.warning(
            "session %s is marked has_visible_message=True but its active transcript has no "
            "reconstructable genuine user turn -- chat_activity_at could not be repaired.",
            session.id,
        )
        return None

    timestamp = genuine_turns[-1].timestamp
    await session_service.persist_state_delta(full_session, {CHAT_ACTIVITY_AT_STATE_KEY: timestamp})
    return timestamp


async def list_saved_sessions(
    session_service: ApiSessionService,
    user_id: str,
    limit: int,
) -> list[SessionSummaryDTO]:
    """`GET /api/sessions`.

    B4B CORRECTION -- `limit` no longer caps the RAW session list before
    visibility is even evaluated (that would let enough empty/legacy
    sessions hide an older genuine saved conversation entirely). Instead:

      1. Every session ADK returns is classified for free from its
         already-loaded `.state` (no event read): known-TRUE (with a
         real `chat_activity_at`) go straight into the visible set;
         known-TRUE but missing `chat_activity_at` (the rare "legacy edge
         case") ALSO go into the visible set immediately, using
         `Session.last_update_time` as a documented, temporary sort
         fallback, and are additionally queued for a real repair; known-
         FALSE are dropped; marker-ABSENT sessions are queued for a full
         legacy backfill, never added to the visible set until that
         backfill actually confirms a real turn exists.
      2. At most `limit` of the queued repairs are actually performed
         this request (an event read each) -- self-terminating: whatever
         didn't fit this request is repaired on a later `GET /api/sessions`
         call, exactly like B4A's original design, extended to cover the
         "known-true, missing activity" case too.
      3. The full visible set (never pre-truncated) is sorted by
         `chat_activity_at` (or its temporary fallback) descending, and
         only THEN sliced to `limit` for the actual response.

    This guarantees a known-real saved conversation is never hidden by
    however many empty/unknown sessions happen to also exist -- the
    return-count cap only ever discards ALREADY-classified-as-visible,
    lowest-priority sessions, never an unevaluated raw session.
    """
    sessions = await session_service.list_sessions(user_id)

    # (session_id, sort_timestamp, title)
    visible: list[tuple[str, float, str]] = []
    repair_queue: list[Any] = []

    for session in sessions:
        marker = session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY)
        if marker is True:
            title = session.state.get(CHAT_TITLE_STATE_KEY, "New chat")
            activity = session.state.get(CHAT_ACTIVITY_AT_STATE_KEY)
            if activity is not None:
                visible.append((session.id, activity, title))
            else:
                # Known-visible, missing only the activity timestamp --
                # MUST still be listed (never excluded merely because the
                # repair budget below might not reach it this request).
                visible.append((session.id, session.last_update_time, title))
                repair_queue.append(session)
        elif marker is None:
            repair_queue.append(session)
        # marker is False -> known empty, never listed, never queued.

    # Prefer repairing the most-recently-touched candidates first (best
    # effort ordering only -- correctness never depends on this order,
    # since every candidate eventually gets repaired across enough
    # requests either way).
    repair_queue.sort(key=lambda s: s.last_update_time, reverse=True)

    for session in repair_queue[:limit]:
        marker = session.state.get(HAS_VISIBLE_MESSAGE_STATE_KEY)
        if marker is True:
            repaired_activity = await _repair_missing_activity(session_service, session, user_id)
            if repaired_activity is not None:
                for i, (session_id, _activity, title) in enumerate(visible):
                    if session_id == session.id:
                        visible[i] = (session_id, repaired_activity, title)
                        break
            # else: logged inside _repair_missing_activity; the fallback
            # entry already in `visible` (sorted by last_update_time)
            # stands as-is rather than disappearing.
        else:
            became_visible = await _backfill_unknown_marker(session_service, session, user_id)
            if became_visible:
                refreshed = await session_service.get_session(session.id, user_id)
                visible.append(
                    (
                        session.id,
                        refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY, refreshed.last_update_time),
                        refreshed.state.get(CHAT_TITLE_STATE_KEY, "New chat"),
                    )
                )
            # else False (or None from a genuinely corrupted session) ->
            # correctly stays excluded -- it was never added above.

    visible.sort(key=lambda item: item[1], reverse=True)
    return [
        SessionSummaryDTO(session_id=session_id, title=title, updated_at=_iso(activity))
        for session_id, activity, title in visible[:limit]
    ]


async def rename_session(
    session_service: ApiSessionService,
    session_id: str,
    user_id: str,
    new_title: str,
) -> SessionSummaryDTO:
    """`PATCH /api/sessions/{id}`. Explicit validation, never silent
    truncation (B4A correction pass, matching the existing frontend
    `RENAME_CHAT` reducer's own never-truncates behavior) -- an
    over-limit title is REJECTED, not cut down.

    B4B CORRECTION: writes ONLY `CHAT_TITLE_STATE_KEY` -- never
    `CHAT_ACTIVITY_AT_STATE_KEY`. A rename must never reorder the saved-
    chat sidebar; ADK's own generic `Session.last_update_time` still
    moves (state was genuinely written), but the response/list ordering
    is governed by `chat_activity_at` alone, which this function leaves
    completely untouched.
    """
    session = await session_service.get_session(session_id, user_id)

    trimmed = new_title.strip()
    if not trimmed:
        raise validation_error("Title cannot be empty.")
    if len(trimmed) > MAX_MANUAL_TITLE_LENGTH:
        raise validation_error(f"Title must be {MAX_MANUAL_TITLE_LENGTH} characters or fewer.")

    await session_service.persist_state_delta(session, {CHAT_TITLE_STATE_KEY: trimmed})

    # Re-fetched (rather than reusing the in-memory `session`) so the
    # response reflects the real, authoritative post-write state -- never
    # approximated. `updated_at` comes from `chat_activity_at`, which this
    # function never wrote, so it reports whatever it already was.
    refreshed = await session_service.get_session(session_id, user_id)
    activity = refreshed.state.get(CHAT_ACTIVITY_AT_STATE_KEY, refreshed.last_update_time)
    return SessionSummaryDTO(session_id=session_id, title=trimmed, updated_at=_iso(activity))
