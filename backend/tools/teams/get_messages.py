"""ADK tool: `teams_get_messages`.

Deterministic. Never performs LLM reasoning. This is the sole grounding
source for any Teams-content summary/answer incident_manager produces for
a chat in the current turn (docs/TEAMS_TOOL_CONTRACT.md #4).

WIRE FORMAT: the live gateway's proven response for `teams.getMessages` is
a bare JSON array of `{"id", "createdDateTime", "lastModifiedDateTime",
"senderName", "senderId", "contentType", "content", "webUrl"}` objects
(not the `{"messages": [...]}` envelope an earlier, unverified draft of
this module assumed). Parsing goes through
`gateway.power_automate_client.extract_items`, which also still accepts a
`{"messages": [...]}` wrapper or a `{"success", "data": [...]}` envelope,
so this keeps working unchanged if the gateway's response shape changes
later. `entry["id"]` and `entry["createdDateTime"]` are required per
message; `senderName`/`content` are tolerated as missing (mirroring the
same tolerance the original design had for `author`/`text`).

NORMALIZATION: `entry["content"]` is Teams' raw content -- HTML for
`contentType: "html"` messages (e.g. `<p>text&nbsp;</p>`,
`<emoji alt="...">`, `<attachment ...>`), observed as the live gateway's
actual output. `TeamsMessage.text` is that content run through
`html_text.normalize_teams_content` (deterministic, no LLM -- see that
module). `TeamsMessage.raw_content`/`content_type` retain the original,
unmodified `content`/`contentType`, so nothing is lost by cleaning `text`.

PAGINATION (deterministic, backend-only -- see docs/TEAMS_TOOL_CONTRACT.md
#4a):

The live Power Automate `teams.getMessages` flow is configured with
Top=50 / OrderBy=`createdDateTime desc` / its own paging toggle OFF, and
accepts an optional `before` (ISO-8601) cursor: omitted -> newest up to 50
messages; supplied -> `createdDateTime lt before`, i.e. the next older
page (proven live).

`teams_get_messages` walks that cursor itself, in a plain Python loop --
the LLM never sees or drives individual page requests
(docs/AGENT_CONTRACT.md #5's "agent vs. tool" principle; this is exactly
the kind of deterministic capability that must never become an
agent-driven loop). See `_PAGE_SIZE`/`DEFAULT_MAX_MESSAGES` below and the
algorithm in `teams_get_messages`'s docstring.

LIMITATION (documented, not fixed here): the pagination cursor is
`createdDateTime`, a timestamp, not an opaque server-issued token. If more
than one page's worth of messages ever shares the exact same
`createdDateTime` at a page boundary, the strict `lt` filter could in
principle skip same-timestamp messages beyond what a single page returned
at that instant. This is a v1 tradeoff of timestamp-based paging, not a
bug in the loop below -- a future gateway change to an opaque cursor would
remove it.

SYSTEM/EVENT FILTERING: Teams chats interleave real user messages with
system/event entries (membership changes, call events, ...) and, on the
live gateway, some entries with missing `senderName` and/or empty
`content` (e.g. raw content of `<systemEventMessage/>`). Those must never
reach incident_manager's reasoning context as if a person said them
(`system_events.is_excludable_from_reasoning`, deterministic, content-based
only -- never based on a missing sender alone; see that module's
docstring). Filtering is applied strictly *after* the pagination loop
above has already decided every page boundary/stop condition from the raw,
unfiltered pages -- it never changes which pages are fetched or how the
cursor advances (see `_PAGE_SIZE`/`truncated`/`next_before` above,
computed from raw page data only).

TIME-RANGE FILTERING (deterministic, backend-only -- see
docs/TEAMS_TOOL_CONTRACT.md #4c): `from_datetime`/`to_datetime` are
optional, already-normalized ISO-8601 UTC timestamps -- this tool never
interprets relative expressions ("today", "last 7 days"); that belongs to
incident_manager's reasoning (docs/AGENT_CONTRACT.md #5). When
`to_datetime` is given, the *first* Power Automate request is seeded with
`before=to_datetime`, so newer, unwanted pages are never fetched. When
`from_datetime` is given, pagination continues, exactly as it otherwise
would, until the oldest message on a just-fetched page reaches or passes
`from_datetime` -- at that point retrieval stops (this is not treated as
`truncated`, since the requested range was actually satisfied) and the
final message list is filtered to `requested_from <= sent_at <
requested_to`. See `TeamsGetMessagesResult.range_fully_covered` for how
callers learn whether `max_messages` cut retrieval short before the
requested lower boundary was actually reached.

COVERAGE CLASSIFICATION: `requested_from`/`requested_to`/`range_fully_covered`/
`truncated` above are the raw metadata; `coverage.py`'s `build_coverage`
deterministically classifies them into one `CoverageStatus` plus a
minimal set of supporting fields (`TeamsCoverage`, schemas.py) --
incident_manager is instructed to reason from `coverage.status`, never to
re-derive it from the raw booleans itself. This is a pure post-processing
step over fields the pagination loop above already produces; it never
changes what gets fetched.

MESSAGE REFERENCES: a Teams message that quotes/replies to another message
carries an `<attachment id="X">` placeholder in its `content` plus a
matching entry in `entry["attachments"]` with `contentType:
"messageReference"` (see `message_references.py`). Those are never file
attachments -- their `<attachment>` tag is suppressed from `text` entirely
(not rendered as `"[Attachment]"`; see `html_text.py`) and the parsed
reference (message id/preview/sender, never invented beyond what the
payload actually contained) becomes `TeamsMessage.message_references`
instead.

EVIDENCE-VALIDATION STATE: when `tool_context` is supplied (ADK
auto-injects it in real use), every message id actually returned in
`messages`, plus every `message_id` found inside any of their
`message_references`, is recorded into session state under
`KNOWN_MESSAGE_IDS_STATE_KEY`, accumulated across calls within the same
turn. `backend/agents/incident_manager/evidence.py` reads this set to
deterministically strip any `evidence` entry incident_manager's final
answer cites that does not correspond to real retrieved data -- "no fake
provenance may reach team_manager." This tool only ever *adds* to that
set; it never validates or trusts anything itself.

REWIND-CLEARED STATE (corrective pass): a rewind that discards a branch
which previously wrote `KNOWN_MESSAGE_IDS_STATE_KEY` leaves the key
PRESENT with value `None` (ADK's own rewind `state_delta` convention --
see `read_known_message_ids`'s own docstring for the verified mechanism),
never absent. Every reader of this key -- inside this module and in
`evidence.py`/`direct_read_fast_path.py` -- must go through
`read_known_message_ids` rather than `state.get(KNOWN_MESSAGE_IDS_STATE_
KEY, [])` directly, so a rewind-cleared key is treated exactly like an
absent one (an empty set) instead of raising `TypeError: 'NoneType'
object is not iterable`. A subsequent real `teams_get_messages` call
still accumulates onto that empty starting point normally -- no discarded
branch's ids reappear, and newly retrieved ids are recorded exactly as
before.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from google.adk.tools import ToolContext

from backend.api.activity_queue import ActivityKind, report_activity
from backend.api.turn_context import current_run_id, record_message_texts
from backend.gateway.power_automate_client import (
    GatewayPayload,
    PowerAutomateClient,
    extract_items,
)
from backend.gateway.safe_error import SafeErrorException, internal_error, validation_error
from backend.tools.teams.coverage import build_coverage
from backend.tools.teams.html_text import normalize_teams_content
from backend.tools.teams.message_references import (
    is_message_reference_attachment,
    parse_message_reference,
)
from backend.tools.teams.schemas import TeamsGetMessagesResult, TeamsMessage, TeamsMessageReference
from backend.tools.teams.system_events import is_excludable_from_reasoning
from backend.tools.teams.time_range import (
    InvalidTimestampError,
    cursor_advanced,
    format_utc,
    in_range,
    parse_utc_timestamp,
)

# Session-state key `teams_get_messages` accumulates known-good message ids
# into, for backend/agents/incident_manager/evidence.py's deterministic
# evidence validation to read. Not Power-Automate-facing; purely internal
# bookkeeping.
KNOWN_MESSAGE_IDS_STATE_KEY = "known_message_ids"


def read_known_message_ids(state: Any) -> set[str]:
    """Normalize any `KNOWN_MESSAGE_IDS_STATE_KEY` read into a `set[str]`.

    ADK's own rewind mechanism represents "this key's value must be
    reverted to before it existed" as an explicit `None` written into the
    rewind event's `state_delta` (verified against installed
    `google-adk==1.33.0`'s `Runner._compute_state_delta_for_rewind`, which
    deliberately sets a discarded key to `None` rather than omitting it) --
    and both session-service implementations this codebase uses
    (`DatabaseSessionService`/`InMemorySessionService`) persist that `None`
    as a literal dict value (`state.update({key: None})`), never by
    actually deleting the key. So after a rewind that discarded a branch
    which had written this key, the key is PRESENT with value `None`, not
    absent -- `state.get(KNOWN_MESSAGE_IDS_STATE_KEY, [])`'s own `[]`
    default therefore never fires (a `dict`/ADK `State.get` only falls
    back to its default when the key is missing entirely), and the caller
    gets `None` back instead. Every reader of this key must go through
    this function rather than repeating that `.get(..., [])` pattern
    directly, so "absent" and "explicitly rewind-cleared" are always
    treated identically -- an empty set, never a `TypeError` -- while a
    real, previously-accumulated list of ids survives unchanged.
    """
    raw = state.get(KNOWN_MESSAGE_IDS_STATE_KEY, [])
    return set(raw or [])


# The live Power Automate flow's configured page size (Top=50). Not sent
# by this client -- it's fixed on the flow side -- but needed here to
# decide whether a page might have more behind it ("full page" == 50).
_PAGE_SIZE = 50

# v1 safety ceiling (requirement: "the tool must never retrieve an
# unlimited chat history"). `teams_get_messages`'s `max_messages` defaults
# to this and is always clamped to `_HARD_MAX_MESSAGES`, regardless of
# what a caller passes.
DEFAULT_MAX_MESSAGES = 200
_HARD_MAX_MESSAGES = 1000


def _extract_message_references(
    entry: dict[str, Any]
) -> tuple[list[TeamsMessageReference], frozenset[str]]:
    """From one raw message entry's `attachments` array, return the
    parsed `TeamsMessageReference`s and the ids of every attachment that
    is structurally a message reference (used to suppress its
    `<attachment>` tag from normalized text, whether or not its content
    could actually be parsed -- see message_references.py's module
    docstring).
    """
    raw_attachments = entry.get("attachments")
    if not isinstance(raw_attachments, list):
        return [], frozenset()

    references: list[TeamsMessageReference] = []
    suppressed_ids: set[str] = set()
    for attachment in raw_attachments:
        if not is_message_reference_attachment(attachment):
            continue
        attachment_id = attachment.get("id")
        if isinstance(attachment_id, str):
            suppressed_ids.add(attachment_id)
        reference = parse_message_reference(attachment)
        if reference is not None:
            references.append(reference)
    return references, frozenset(suppressed_ids)


def _parse_messages(raw: GatewayPayload) -> list[TeamsMessage]:
    items = extract_items(raw, wrapper_key="messages")

    messages: list[TeamsMessage] = []
    for entry in items:
        try:
            raw_content = entry.get("content") or ""
            message_references, suppressed_attachment_ids = _extract_message_references(entry)
            messages.append(
                TeamsMessage(
                    id=entry["id"],
                    author=entry.get("senderName") or "Unknown",
                    text=normalize_teams_content(
                        raw_content, suppressed_attachment_ids=suppressed_attachment_ids
                    ),
                    sent_at=entry["createdDateTime"],
                    raw_content=raw_content,
                    content_type=entry.get("contentType"),
                    message_references=message_references,
                )
            )
        except (KeyError, TypeError):
            raise internal_error(
                "The Teams connector returned an incomplete message record."
            ) from None
    return messages


def _record_known_message_ids(
    tool_context: Optional[ToolContext], messages: list[TeamsMessage]
) -> None:
    """Accumulate ids of retrieved messages, plus any message ids found
    inside their `message_references`, into session state -- see
    `KNOWN_MESSAGE_IDS_STATE_KEY`'s docstring above. A referenced message
    id counts as "known" here precisely because its information (preview/
    sender) was actually present in retrieved data, even if the referenced
    message itself was not independently retrieved.
    """
    if tool_context is None:
        return

    known_ids: set[str] = read_known_message_ids(tool_context.state)
    for msg in messages:
        known_ids.add(msg.id)
        for reference in msg.message_references:
            known_ids.add(reference.message_id)
    tool_context.state[KNOWN_MESSAGE_IDS_STATE_KEY] = sorted(known_ids)


def _record_message_texts(messages: list[TeamsMessage]) -> None:
    """Forward `{message_id: text}` for this call's retrieved messages to
    `backend.api.turn_context` -- see that module's own docstring for the
    full investigation/rationale (a `tool_context.state` key, `temp:`-
    prefixed or not, was verified NOT to reliably reach chat_service.py
    across both supported session backends). Only messages with non-empty
    normalized `text` are recorded (an empty/system/event message was
    already excluded from `messages` before this is called -- see
    `teams_get_messages`'s own "SYSTEM/EVENT FILTERING" -- so this guard
    is defense in depth, not the primary filter). `record_message_texts`
    itself accumulates across multiple `teams_get_messages` calls within
    the same turn, and is a safe no-op outside of a `chat_service.py`-
    driven turn (e.g. `adk run`/a test with no bound `run_id`).
    """
    texts = {msg.id: msg.text for msg in messages if msg.text}
    record_message_texts(current_run_id(), texts)


def _validation_error_result(message: str) -> dict[str, Any]:
    return {"error": validation_error(message).safe_error.to_dict()}


def teams_get_messages(
    chat_id: str,
    max_messages: int = DEFAULT_MAX_MESSAGES,
    from_datetime: Optional[str] = None,
    to_datetime: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Retrieve the messages in one already-resolved Teams chat, paging
    automatically through as many 50-message Power Automate pages as
    needed (bounded by `max_messages`), optionally restricted to a time
    range.

    `chat_id` must be a chat id previously returned by `teams_list_chats`
    for an exact, unambiguous match -- never a free-text chat name, and
    never a value not obtained from a real tool result.

    `from_datetime`/`to_datetime`, when given, must already be normalized
    ISO-8601 timestamps with an explicit UTC offset (e.g.
    `"2026-08-25T00:00:00Z"`) -- this tool never interprets relative
    expressions like "today" or "last 7 days" itself; that conversion
    happens in incident_manager's reasoning before calling this tool.
    Returned messages satisfy `from_datetime <= sent_at < to_datetime`
    (lower bound inclusive, upper bound exclusive); either or both may be
    omitted for an unbounded side.

    Pagination algorithm, entirely deterministic (no LLM involvement):

    1. Fetch page 1. If `to_datetime` was given, seed the cursor with
       `before=to_datetime` so newer pages are never fetched
       unnecessarily; otherwise fetch with no cursor (newest first).
    2. For each page fetched: de-duplicate every message by `id` into the
       running result set, and track the oldest `sent_at` seen on that
       page.
    3. Stop, without fetching another page, as soon as any of these is
       true (checked in this order):
       - the page returned 0 messages, or fewer than 50 (natural end of
         history) -- `range_fully_covered: true`, `truncated: false`;
       - `from_datetime` was given and the oldest message on this page
         already reaches or passes it -- the requested lower boundary is
         satisfied; `range_fully_covered: true`, `truncated: false`;
       - the running result set has reached `max_messages` -- `truncated:
         true`, `range_fully_covered: false` (the requested/implied lower
         boundary may not have been reached);
       - the next cursor would not be strictly older than the current one
         (a misbehaving/stalled gateway response) -- stopped safely rather
         than looping forever; `range_fully_covered: false`.
    4. Otherwise, call again with `before` set to the oldest `sent_at`
       just seen, and repeat from step 2.
    5. After pagination: remove system/event and content-free messages
       (see the module docstring), then, if a time range was requested,
       remove anything outside `[from_datetime, to_datetime)`. Order the
       remainder chronologically oldest -> newest.

    Args:
      chat_id: The Teams chat id to retrieve messages for.
      max_messages: Safety ceiling on how many messages to retrieve
        across all pages. Defaults to `DEFAULT_MAX_MESSAGES` (200) and is
        always clamped to `_HARD_MAX_MESSAGES` (1000) regardless of what
        is passed -- this tool never retrieves unlimited chat history.
      from_datetime: Optional ISO-8601 UTC lower boundary (inclusive).
      to_datetime: Optional ISO-8601 UTC upper boundary (exclusive).
      tool_context: Auto-injected by ADK in real use (never supplied by
        the model). Used only to record which message ids were actually
        retrieved, for incident_manager's deterministic evidence
        validation -- see `KNOWN_MESSAGE_IDS_STATE_KEY` above. Safe to
        omit (e.g. in tests): retrieval behaves identically either way.

    Returns:
      On success, a dict with `chat_id`, `messages` (de-duplicated,
      system/event, content-free, and out-of-range entries removed,
      ordered chronologically oldest -> newest; each item has `id`,
      `author`, `text`, `sent_at`, `raw_content`, `content_type`,
      `message_references`), `retrieved_count_raw`, `filtered_system_event_count`,
      `filtered_out_of_range_count`, `retrieved_count`, `requested_from`,
      `requested_to`, `range_fully_covered`, `truncated`, `next_before`,
      `oldest_retrieved_at`, `newest_retrieved_at`, and `coverage` (a
      `status` of "full_range"/"partial_range"/"complete"/"latest_window"
      plus a few supporting fields -- see `coverage.py` and
      `TeamsGetMessagesResult`'s docstring for exact semantics). An empty
      `messages` list is a valid, non-error result -- never treated as a
      failure. On failure -- including an invalid `from_datetime`/
      `to_datetime`, or a gateway failure at any page -- a dict with a
      single `error` key holding a SafeError; no partial results are
      returned alongside an error.
    """
    if not chat_id or not chat_id.strip():
        return _validation_error_result("A Teams chat id is required.")

    try:
        max_messages = int(max_messages)
    except (TypeError, ValueError):
        return _validation_error_result("max_messages must be a positive integer.")
    if max_messages <= 0:
        return _validation_error_result("max_messages must be a positive integer.")
    max_messages = min(max_messages, _HARD_MAX_MESSAGES)

    from_dt: Optional[datetime] = None
    if from_datetime is not None:
        try:
            from_dt = parse_utc_timestamp(from_datetime)
        except InvalidTimestampError:
            return _validation_error_result(
                "from_datetime must be an ISO-8601 timestamp with an "
                "explicit UTC offset."
            )

    to_dt: Optional[datetime] = None
    if to_datetime is not None:
        try:
            to_dt = parse_utc_timestamp(to_datetime)
        except InvalidTimestampError:
            return _validation_error_result(
                "to_datetime must be an ISO-8601 timestamp with an "
                "explicit UTC offset."
            )

    # Phase 2 (Runtime Activity Truthfulness): reported once, right before
    # the first real, network-bound gateway page fetch below -- never per
    # page (this tool may page several times internally; that pagination
    # detail is never a user-visible activity boundary of its own).
    report_activity(ActivityKind.TEAMS_MESSAGES_RETRIEVAL_STARTED)
    client = PowerAutomateClient()
    messages_by_id: dict[str, TeamsMessage] = {}
    # Rule 1: seed the first request's cursor at to_datetime when given,
    # so newer, unwanted pages are never fetched.
    cursor: Optional[str] = to_datetime
    truncated = False
    range_fully_covered = True
    next_before: Optional[str] = None

    while True:
        try:
            raw = client.get_messages(chat_id, before=cursor)
            page = _parse_messages(raw)
        except SafeErrorException as exc:
            report_activity(ActivityKind.TEAMS_MESSAGES_RETRIEVAL_FAILED)
            return {"error": exc.safe_error.to_dict()}

        oldest_in_page: Optional[str] = None
        for msg in page:
            messages_by_id.setdefault(msg.id, msg)
            if oldest_in_page is None or msg.sent_at < oldest_in_page:
                oldest_in_page = msg.sent_at

        page_len = len(page)

        if page_len == 0 or page_len < _PAGE_SIZE:
            # Natural end of history for this chat -- nothing more to
            # fetch, nothing truncated, and the (implied or explicit)
            # lower boundary is necessarily covered.
            next_before = None
            range_fully_covered = True
            break

        # A full (_PAGE_SIZE) page came back: older messages may exist.

        # Rule 2: stop once we've reached/passed the requested lower
        # boundary -- no need to page back any further than that.
        if from_dt is not None and oldest_in_page is not None:
            try:
                oldest_dt = parse_utc_timestamp(oldest_in_page)
            except InvalidTimestampError:
                oldest_dt = None
            if oldest_dt is not None and oldest_dt <= from_dt:
                next_before = oldest_in_page
                range_fully_covered = True
                break

        if len(messages_by_id) >= max_messages:
            truncated = True
            range_fully_covered = False
            next_before = oldest_in_page
            break

        if oldest_in_page is None or (
            cursor is not None and not cursor_advanced(oldest_in_page, cursor)
        ):
            # Cursor failed to advance -- stop safely rather than risk an
            # infinite loop against a misbehaving gateway response.
            range_fully_covered = False
            next_before = oldest_in_page
            break

        cursor = oldest_in_page
        # Loop continues: fetch the next (older) page.

    ordered_raw = sorted(messages_by_id.values(), key=lambda m: m.sent_at)
    retrieved_count_raw = len(ordered_raw)

    # System/event and content-free filtering -- applied only here, after
    # every pagination decision above has already been made from the raw,
    # unfiltered pages (see the module docstring's "SYSTEM/EVENT
    # FILTERING" note).
    after_system_filter = [
        msg
        for msg in ordered_raw
        if not is_excludable_from_reasoning(msg.raw_content, msg.text)
    ]
    filtered_system_event_count = retrieved_count_raw - len(after_system_filter)

    # Time-range filtering -- the deterministic safety net for rule 3
    # ("return only from_datetime <= sent_at < to_datetime"), needed
    # because the last page fetched for a lower-bounded request typically
    # contains some messages older than from_datetime by design (that is
    # how the loop confirms the boundary was reached).
    if from_dt is not None or to_dt is not None:
        ordered = [msg for msg in after_system_filter if in_range(msg.sent_at, from_dt, to_dt)]
    else:
        ordered = after_system_filter
    filtered_out_of_range_count = len(after_system_filter) - len(ordered)

    _record_known_message_ids(tool_context, ordered)
    _record_message_texts(ordered)

    requested_from_utc = format_utc(from_dt) if from_dt is not None else None
    requested_to_utc = format_utc(to_dt) if to_dt is not None else None
    oldest_retrieved_at = ordered[0].sent_at if ordered else None
    newest_retrieved_at = ordered[-1].sent_at if ordered else None

    coverage = build_coverage(
        requested_from=requested_from_utc,
        requested_to=requested_to_utc,
        range_fully_covered=range_fully_covered,
        truncated=truncated,
        retrieved_count=len(ordered),
        oldest_retrieved_at=oldest_retrieved_at,
        newest_retrieved_at=newest_retrieved_at,
    )

    result = TeamsGetMessagesResult(
        chat_id=chat_id,
        messages=ordered,
        retrieved_count_raw=retrieved_count_raw,
        filtered_system_event_count=filtered_system_event_count,
        filtered_out_of_range_count=filtered_out_of_range_count,
        retrieved_count=len(ordered),
        requested_from=requested_from_utc,
        requested_to=requested_to_utc,
        range_fully_covered=range_fully_covered,
        truncated=truncated,
        next_before=next_before,
        oldest_retrieved_at=oldest_retrieved_at,
        newest_retrieved_at=newest_retrieved_at,
        coverage=coverage,
    )
    # `message_count` -- the SAME allowlisted metadata key `run_trace.py`
    # already reserves for this concept. Reported even when 0 (a valid,
    # non-error, empty-range result per this function's own docstring) --
    # the STATUS TRANSLATOR, not this call site, decides never to phrase
    # a 0-count success as "reviewing" anything.
    report_activity(ActivityKind.TEAMS_MESSAGES_RETRIEVAL_SUCCEEDED, {"message_count": len(ordered)})
    return result.model_dump(mode="json")
