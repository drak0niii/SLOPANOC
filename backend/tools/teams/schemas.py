"""Typed shapes for the two implemented read tools.

These are TOOL-level schemas (docs/TEAMS_TOOL_CONTRACT.md #3-#4) -- not to
be confused with the higher-level `IncidentManagerRequest`/`Response`
delegation schemas in backend/agents/incident_manager/schemas.py, which
describe the conceptual contract between team_manager and incident_manager
(docs/AGENT_CONTRACT.md #8), not the raw tool I/O.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ChatSummary(BaseModel):
    chat_id: str
    title: str
    participant_count: Optional[int] = None
    last_activity_at: Optional[str] = None


class ChatMatchOutcome(str, Enum):
    """Deterministic classification of a `topic` lookup against the chats
    Power Automate returned. Computed with plain string comparison inside
    `teams_list_chats` -- never by LLM judgment (docs/AGENT_CONTRACT.md
    #5's "agent vs. tool" principle; "CHAT DISCOVERY" rules).
    """

    MATCHED = "matched"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class TeamsListChatsResult(BaseModel):
    """Result of `teams_list_chats`.

    `chats` is always the full list the gateway returned for this call
    (see the pagination limitation documented in list_chats.py).
    `matched_chat`/`candidates` are populated by deterministic matching of
    `topic` against `chats` -- a caller may treat `matched_chat.chat_id`
    as the only legitimate source of a chat id for this turn; it is never
    a value invented by a model (docs/TEAMS_TOOL_CONTRACT.md #9).

    `similar_candidates` (interaction-capability extension) is populated
    only when `match` is "not_found" AND deterministic similarity scoring
    (chat_resolution.py) found at least one chat scoring at/above its
    documented threshold -- distinct from `candidates`, which is only for
    the (rarer) case of multiple chats sharing the EXACT same normalized
    title. When `similar_candidates` is non-empty, `selection_pending` is
    also true, meaning a `PendingSelection` was created in session state
    (see selection/service.py) -- the model itself never sees the titles
    in `similar_candidates`; only `selection_pending` (a plain boolean)
    reaches incident_manager's structured output, so it can explain that
    disambiguation is needed without ever fabricating/repeating options
    (the interactive card renders them from the deterministic
    `PendingSelection`, never from model output).
    """

    chats: list[ChatSummary] = Field(default_factory=list)
    match: Optional[ChatMatchOutcome] = None
    matched_chat: Optional[ChatSummary] = None
    candidates: list[ChatSummary] = Field(default_factory=list)
    similar_candidates: list[ChatSummary] = Field(default_factory=list)
    selection_pending: bool = False


class TeamsMember(BaseModel):
    """One chat member as authoritatively returned by `teams.getMembers`
    (docs/TEAMS_TOOL_CONTRACT.md #5). `id` is Teams' own internal member/
    user id -- kept here because it is a genuine part of the raw tool
    result, but it is NEVER forwarded past its one caller
    (`backend/api/source_reference.py`'s contributor resolution, which
    reads `display_name` only) into any frontend-facing shape; see
    `SourceReferenceDTO`'s own docstring for the same exclusion.

    `id` is deliberately `Optional` (bugfix, contributor-accuracy pass):
    the one thing this milestone actually needs from a member entry is
    `display_name` -- requiring a well-formed `id` too would silently drop
    a real, legitimately-named contributor whenever the live gateway's
    member-id field happens to be named, typed, or shaped differently than
    expected (exactly the class of live-shape surprise
    `get_messages.py`'s own module docstring already documents happening
    for other Teams endpoints). Never exposed regardless of whether it is
    present.
    """

    id: Optional[str] = None
    display_name: str


class TeamsGetMembersResult(BaseModel):
    """Result of `teams_get_members` -- the full, authoritative member
    list of one chat, as Power Automate returned it. Unlike
    `TeamsGetMessagesResult`, this is not paginated/bounded -- the gateway
    returns a chat's complete membership in one call.
    """

    chat_id: str
    members: list[TeamsMember] = Field(default_factory=list)


class TeamsMessageReference(BaseModel):
    """A Teams "message reference" -- the quoted/replied-to message a
    `TeamsMessage` responds to, distinct from the replying message itself.

    Parsed deterministically from a `messageReference`-typed attachment
    (see `message_references.py`), never invented: any field this module
    could not actually find in the retrieved payload stays `None` rather
    than being guessed. `message_id` is the one field guaranteed present
    -- a reference with no usable id is not constructed at all.
    """

    message_id: str
    preview: Optional[str] = None
    sender_name: Optional[str] = None
    sender_id: Optional[str] = None


class TeamsMessage(BaseModel):
    """One normalized Teams message.

    `text` is cleaned, readable text -- HTML tags stripped/translated and
    entities decoded by `html_text.normalize_teams_content` (deterministic,
    no LLM). `raw_content` and `content_type` retain the original,
    unmodified Teams content and its declared type, so provenance is never
    lost even though `text` is a derived, cleaned-up view of it.

    `message_references` holds any quoted/replied-to messages this message
    points to (see `TeamsMessageReference`) -- these describe a *different*
    message than the one `author`/`text`/`sent_at` describe, and must never
    be attributed to this message's own author.

    `hosted_content_ids` (Teams Rich Content milestone; Multiple Teams
    Hosted Images milestone extends this to more than one) -- the
    Teams-domain identity of every inline/pasted image this message
    carries, deterministically extracted from `raw_content` in TRUE
    SOURCE-HTML ORDER (see hosted_content.py) -- never Power Automate
    transport detail (no `contentBase64`, no gateway operation name, no
    provider-specific shape belongs here; see docs on the Microsoft
    integration/provider boundary). Empty for a message with no
    recognized inline image. Bounded to at most `get_messages.py`'s own
    `MAX_HOSTED_IMAGES_PER_MESSAGE` entries -- see `hosted_content_
    truncated` below for what happens beyond that. Retrieving the actual
    image bytes for one of these ids is a SEPARATE, explicit step
    (`teams_get_hosted_content`) -- populating this list never itself
    downloads anything.

    `hosted_content_truncated` -- `True` only when this message's raw
    content actually referenced MORE inline images than `MAX_HOSTED_
    IMAGES_PER_MESSAGE` allows; `hosted_content_ids` above then holds only
    the deterministic first-N-in-HTML-order prefix (never a random/
    reordered subset). Exists so `incident_manager` can truthfully tell
    the user that additional images existed but were not processed,
    rather than silently reviewing only some of them without saying so.
    """

    id: str
    author: str
    text: str
    sent_at: str
    raw_content: str
    content_type: Optional[str] = None
    message_references: list[TeamsMessageReference] = Field(default_factory=list)
    hosted_content_ids: list[str] = Field(default_factory=list)
    hosted_content_truncated: bool = False


class CoverageStatus(str, Enum):
    """Deterministic classification of what a `teams_get_messages` result
    actually proves about coverage -- computed in `coverage.py` from
    `requested_from`/`requested_to`/`range_fully_covered`/`truncated`,
    never left for incident_manager to re-derive from those raw fields
    itself (docs/AGENT_CONTRACT.md #5's "agent vs. tool" principle).

    FULL_RANGE: an explicit time range was requested and fully covered --
      answer normally, no caveat needed.
    PARTIAL_RANGE: an explicit time range was requested but only
      partially covered -- answer from what was retrieved, with a caveat.
    COMPLETE: no explicit time range was requested, and retrieval was not
      cut short by `max_messages` -- answer normally. Still not a
      guarantee that literally nothing precedes the chat's earliest
      retrievable message, only that this retrieval's own ceiling never
      bound it.
    LATEST_WINDOW: no explicit time range was requested, and retrieval
      *was* cut short by `max_messages` -- the answer is grounded only in
      the most recent messages retrieved; never imply "the entire chat".
    """

    FULL_RANGE = "full_range"
    PARTIAL_RANGE = "partial_range"
    COMPLETE = "complete"
    LATEST_WINDOW = "latest_window"


class TeamsCoverage(BaseModel):
    """A small, deliberately minimal view of retrieval coverage for
    incident_manager to reason about -- see `coverage.py`.

    Intentionally excludes `next_before` (a pagination-continuation
    implementation detail the model has no use for and must not reason
    about) and the raw `range_fully_covered`/`truncated` booleans this is
    classified from (the model reasons about `status`, not about
    reconstructing it from two booleans itself).
    """

    status: CoverageStatus
    requested_from: Optional[str] = None
    requested_to: Optional[str] = None
    retrieved_count: int = 0
    oldest_retrieved_at: Optional[str] = None
    newest_retrieved_at: Optional[str] = None


class TeamsGetMessagesResult(BaseModel):
    """Result of `teams_get_messages`.

    `messages` is the de-duplicated union of every page retrieved, with
    Teams system/event entries, content-free entries, and (when a time
    range was requested) out-of-range entries all removed, ordered
    chronologically oldest -> newest. This is still a bounded retrieval,
    never a claim of complete chat history or of the full requested time
    range: at most `max_messages` messages are ever fetched (default/
    ceiling documented in get_messages.py), and `truncated`/
    `range_fully_covered`/`next_before` tell the caller what was actually
    covered.

    Counting fields (all computed from the same underlying pipeline, in
    order, so `retrieved_count_raw - filtered_system_event_count -
    filtered_out_of_range_count == retrieved_count` always holds):
    - `retrieved_count_raw`: how many de-duplicated messages the gateway
      actually returned, before any filtering.
    - `filtered_system_event_count`: how many were excluded as system/
      event or content-free entries.
    - `filtered_out_of_range_count`: how many survived system/event
      filtering but fell outside `[requested_from, requested_to)`.
    - `retrieved_count`: `len(messages)` -- what's actually handed to
      incident_manager for reasoning, after all filtering.

    Time-range fields:
    - `requested_from` / `requested_to`: the validated, canonical-UTC echo
      of the `from_datetime`/`to_datetime` inputs (`None` when not
      supplied).
    - `range_fully_covered`: `True` if retrieval actually reached the
      requested lower boundary (`requested_from`, or the true start of the
      chat's history if no lower boundary was requested) before stopping.
      `False` when retrieval was cut short by `max_messages` or a stalled
      cursor before reaching that boundary -- i.e. some messages within
      the requested/implied range may not have been retrieved. This is
      distinct from `truncated`: reaching the requested lower boundary
      exactly at the `max_messages` ceiling is still `range_fully_covered:
      true` and `truncated: false`, because nothing within the requested
      scope was cut short.
    - `truncated`: `True` only when retrieval stopped specifically because
      `max_messages` was reached before the requested/implied lower
      boundary was reached -- i.e. older messages within scope may still
      exist. Never `True` for a natural end or for reaching the requested
      lower boundary. Computed from the raw page data, before filtering --
      filtering never changes pagination decisions.
    - `next_before`: the `before` cursor a future call could resume from
      to continue further back, or `None` when retrieval reached the true
      start of the chat's history (or the requested lower boundary, or the
      chat is empty).

    - `oldest_retrieved_at` / `newest_retrieved_at`: the `sent_at` of the
      first/last entries in the *final, filtered* `messages` (both `None`
      when `messages` is empty).
    - `coverage`: the same information, deterministically classified into
      one `CoverageStatus` plus the minimal supporting fields (see
      `coverage.py`) -- this is what incident_manager's prompt is
      instructed to reason from for coverage wording, rather than
      re-deriving it from `range_fully_covered`/`truncated` itself.

    Deliberately has no `summary` field: this tool only ever returns raw,
    retrieved data. Summarization is exclusively incident_manager's LLM
    reasoning, applied only to `messages` returned here -- the tool layer
    itself has no way to fabricate one, by construction.
    """

    chat_id: str
    messages: list[TeamsMessage] = Field(default_factory=list)
    retrieved_count_raw: int = 0
    filtered_system_event_count: int = 0
    filtered_out_of_range_count: int = 0
    retrieved_count: int = 0
    requested_from: Optional[str] = None
    requested_to: Optional[str] = None
    range_fully_covered: bool = True
    truncated: bool = False
    next_before: Optional[str] = None
    oldest_retrieved_at: Optional[str] = None
    newest_retrieved_at: Optional[str] = None
    coverage: TeamsCoverage


class TeamsHostedContentResult(BaseModel):
    """Result of `teams_get_hosted_content` (Teams Rich Content milestone,
    single-image scope) -- PROVIDER-NEUTRAL: describes Teams concepts only
    (conversation/message/hosted-content identity, content type, size),
    never a Power Automate transport shape. See get_hosted_content.py's
    own module docstring for the full integration-boundary rationale.

    Deliberately carries NO image bytes and no Base64 -- this is a safe,
    model-visible/frontend-safe summary proving retrieval and validation
    succeeded, never a vehicle for raw binary content. `chat_id`/
    `message_id`/`hosted_content_id` are echoed back so a caller can
    confirm this result answers the exact request it made -- never a
    different id than the one requested (see get_hosted_content.py's own
    provenance enforcement, which happens before this result is ever
    constructed).

    Successful construction of this object already implies the decoded
    bytes were validated as a genuine, supported, decodable image
    (`backend.attachments.validation.validate_image_bytes`) -- a retrieval
    or validation failure never reaches this type at all; it surfaces as
    the tool's `{"error": ...}` SafeError shape instead (see
    get_hosted_content.py), mirroring every other Teams tool's own
    success/error contract.

    `delivered_for_visual_reasoning` (Multiple Teams Hosted Images
    milestone) -- `True` if this image's validated bytes were actually
    queued for real Gemini multimodal delivery on the next model call
    (`backend.api.hosted_content_vision_context.stash_pending_hosted_
    content_image` succeeded); `False` if retrieval and validation BOTH
    succeeded but the image was NOT queued because this run already
    reached `MAX_HOSTED_IMAGES_PER_MESSAGE` or `MAX_TOTAL_HOSTED_IMAGE_
    BYTES` (see that module's own "BOUNDS" docstring). Exists so a caller
    can never mistake "retrieval succeeded" for "the model will actually
    see this image" -- `incident_manager`'s own prompt is instructed to
    say plainly when this is `False` rather than claim to have observed
    an image it never actually received as visual input.
    """

    chat_id: str
    message_id: str
    hosted_content_id: str
    content_type: str
    size_bytes: int
    delivered_for_visual_reasoning: bool


class TeamsGetAllHostedContentResult(BaseModel):
    """Result of `teams_get_all_hosted_content` (Deterministic All-Image
    Retrieval milestone) -- the deterministic BACKEND expansion over one
    message's ALREADY-DISCOVERED, ALREADY-ORDERED `hosted_content_ids`,
    replacing model-driven iteration (which real live validation proved
    unreliable -- see get_hosted_content.py's own module docstring for the
    full root-cause rationale) with one call.

    Deliberately NEVER carries a `hosted_content_id` (singular or list) at
    all -- unlike `TeamsHostedContentResult`, which echoes back the ONE id
    it was asked for, this is a pure COUNTS-AND-ORDINALS summary; exposing
    the underlying ids here would give the model (and, if this shape were
    ever reused elsewhere, a caller) a list it could misuse to attempt
    individual re-retrieval outside the deterministic expansion this tool
    exists to replace. `failed_ordinals` uses 1-based, TRUE-Teams-HTML-
    order positions ONLY (never a hosted_content_id) -- exactly the same
    identity space `SourceVisualEvidenceItemDTO.ordinal` already uses, so
    "ordinal 2 failed" means exactly the same thing here as it does in the
    Source drawer.

    `discovered_count`: how many hosted-content ids this run's own
    `teams_get_messages` call recorded for this message (already capped at
    `MAX_HOSTED_IMAGES_PER_MESSAGE` -- see `TeamsMessage.hosted_content_
    truncated` for whether the RAW message actually carried more than
    that).
    `attempted_count`: how many of those this call actually attempted
    retrieval for (equal to `discovered_count` unless `chat_id`/
    `message_id` provenance excluded some).
    `delivered_count`: how many were ACTUALLY validated AND queued for
    real Gemini multimodal delivery -- the authoritative "how many images
    will the model actually see" number.
    `failed_ordinals`: 1-based ordinals (true Teams order) that did NOT
    end up delivered, for ANY reason -- a genuine retrieval/validation
    failure, or a successful retrieval that the per-run image-count/
    total-byte budget excluded. Always `len(failed_ordinals) ==
    attempted_count - delivered_count`.
    """

    chat_id: str
    message_id: str
    discovered_count: int
    attempted_count: int
    delivered_count: int
    failed_ordinals: list[int] = Field(default_factory=list)
