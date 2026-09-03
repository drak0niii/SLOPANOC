"""Data contracts for the deterministic interactive-selection framework.

Nothing here executes anything -- these are plain data shapes shared by
service.py (lifecycle transitions) and the API layer (DTO mapping,
mirroring how backend/approval/schemas.py relates to
backend/api/pending_action.py).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SelectionKind(str, Enum):
    """Closed set of ambiguity kinds this framework can represent -- bound
    to a known, closed set, never an arbitrary caller-supplied string,
    mirroring `WriteOperation`'s own closed-enum rationale.
    """

    TEAMS_CHAT = "teams.chat"


class SelectionStatus(str, Enum):
    PENDING = "pending"
    RESOLVED = "resolved"
    SKIPPED = "skipped"
    SUPERSEDED = "superseded"


class SelectionOption(BaseModel):
    """Safe, user-facing option -- an opaque `option_id` and a
    human-readable `label` ONLY. Never the raw underlying identifier
    (e.g. a Teams chat id) -- see `PendingSelection`'s own internal-only
    mapping fields for where that lives.
    """

    option_id: str
    label: str


class ReadOperation(str, Enum):
    """Closed, deterministic vocabulary for "what kind of read is this" --
    pre-4H hardening pass. Exists so the RESUME fallback (read_resume.py's
    `build_read_resume_message`) can pick an accurate generic phrase even
    in the rare case `question` itself has to be discarded (see
    `PendingReadIntent.operation`'s own docstring for why that can still
    happen). Deliberately tiny and closed -- `incident_manager` classifies
    into one of these labels via its own reasoning, exactly the same kind
    of closed-enum judgment call it already makes for `outcome`
    (IncidentManagerOutcome) -- never a Python keyword/regex classifier
    over the user's own words.
    """

    SUMMARIZE = "summarize"
    GET_MESSAGES = "get_messages"


class PendingReadIntent(BaseModel):
    """The smallest structured model that resumes a READ Teams operation
    (summarize / answer-a-question / retrieve messages / a future read
    like "who's in this chat") once its destination is resolved -- the
    read-side analogue of `pending_write_message`.

    FIELD SEPARATION (pre-4H hardening pass -- item 1): this model keeps
    OPERATION (`operation`, a closed enum -- never free text, so it can
    NEVER accidentally carry a chat name) structurally separate from FOCUS
    (`question`, optional free text for a specific sub-question/detail
    beyond the base operation) and from TIME SCOPE (`requested_time_range`).
    The DESTINATION is not represented here at all, by design -- it lives
    entirely in the separately-resolved `selected_teams_chat_id`/`topic`
    session state (see read_resume.py's module docstring); resuming this
    intent only ever needs `operation`/`question`/`requested_time_range`,
    never a chat name.

    `question`/`requested_time_range` are captured before the destination
    was known to be ambiguous -- never re-derived from conversation text,
    never chain-of-thought/scratchpad (instruction section 5): they are
    exactly the same safe, already-structured values `incident_manager`'s
    prompt contract already treats as complete, self-contained,
    destination-free statements of what is needed (see
    `IncidentManagerRequest.question`'s own docstring: "a complete,
    self-contained statement... never forwarding the user's unresolved
    wording verbatim"). `operation` is a SEPARATE, always-present signal
    describing the base kind of read -- deliberately NOT derived by
    inspecting `question`'s text (that would be exactly the "if 'summarize'
    in message"-style routing this codebase forbids); `incident_manager`
    supplies it directly, the same way it already supplies `outcome`.

    KNOWN LIMITATION, stated plainly rather than worked around with text
    surgery: if `question` itself still names the ambiguous chat despite
    the prompt contract's explicit rule against it (list_chats.py's
    `_safe_pending_question` is the deterministic safety net for exactly
    this), the free-text FOCUS detail it carried is lost when that safety
    net discards it -- there is no safe way to algorithmically separate
    "the destination" from "the rest of the sentence" inside one
    model-generated string without regex/text-replacement surgery on
    arbitrary user wording, which this codebase deliberately never does
    (instruction: "Do NOT solve this with brittle regex replacement of
    arbitrary user text"). What is NEVER lost, even then: `operation`
    (a separate, always-safe field) and `requested_time_range` (also
    untouched by that safety net) -- see `build_read_resume_message`.

    `question` is `None` for a plain "summarize this chat" request with no
    specific sub-question -- a real, valid state, not a missing field.
    """

    operation: ReadOperation = ReadOperation.SUMMARIZE
    question: Optional[str] = None
    requested_time_range: Optional[str] = None


class ResolvedReadContinuation(BaseModel):
    """Production-hardening pass: the deterministic, single-use replacement
    for driving a resumed READ Teams operation via a natural-language
    "resume" turn that team_manager's model has to reinterpret.

    Once `selection_service.choose()` resolves which option the user
    picked, everything needed to execute the read is already known --
    scope (always the selected external Teams conversation, by
    construction: choosing a `SelectionCard` option is only ever offered
    for that), the base operation, the specific focus/question (if any),
    the requested time range (if any), and now the authoritative
    `chat_id`/`topic` the chosen option maps to. None of these fields is
    ever re-derived from user text, a resume message, or anything the
    model produces -- `conversation_target`/`operation`/`question`/
    `requested_time_range` are copied verbatim from the `PendingReadIntent`
    that was already captured before the ambiguity existed (see that
    model's own docstring); `selected_chat_id`/`selected_chat_topic` come
    only from the resolved option's `option_targets` entry (never from a
    fuzzy/exact re-resolution of the option's label).

    Storage/consumption: see `backend.selection.service`'s
    `store_read_continuation`/`pop_read_continuation` (server-side,
    session-scoped, single-use -- the pop is the consume) and
    `backend.agents.team_manager.read_continuation_enforcement` (how a
    popped continuation actually overrides the one `incident_manager` tool
    call it applies to, without any new agent or model call).
    """

    conversation_target: str = "selected_external_conversation"
    operation: ReadOperation = ReadOperation.SUMMARIZE
    selected_chat_id: str
    selected_chat_topic: str
    question: Optional[str] = None
    requested_time_range: Optional[str] = None


class PendingSelection(BaseModel):
    """A pending interactive disambiguation, awaiting (or holding) the
    user's choice -- the generic analogue of `ActionProposal` for
    "which real resource did you mean" rather than "should this write
    proceed."

    `options` is SAFE to expose to the frontend verbatim (see
    `SelectionOption`). `option_targets` is INTERNAL ONLY -- never
    serialized into any frontend-facing DTO -- and maps each
    `option_id` to the real, authoritative resource identifiers it
    represents (e.g. a Teams `chat_id`/`topic` pair); this is the
    server-side mapping instruction section 12/32 requires: the frontend
    can request "option_id 2", never a raw chat id directly.

    `pending_write_message`, when set, means this ambiguity arose while
    resolving the destination of a `teams.sendMessage` intent -- the
    EXACT message text already gathered from the user, preserved so
    choosing a candidate can resume that write immediately without
    re-asking "what should the message say?" (instruction section 13).

    `pending_read_intent`, when set (mutually exclusive with
    `pending_write_message` in practice -- a given ambiguity arises from
    either a read or a write, never both), means this ambiguity arose
    while resolving the destination of a READ intent -- resuming it
    (backend/api/selection_service.py's `choose`) needs only this
    structured intent plus the newly resolved destination; the frontend
    never replays the user's original raw request text (hardening pass:
    doing so previously re-surfaced the OLD, unresolved destination name
    and reopened the same ambiguity).
    """

    selection_id: str
    kind: SelectionKind
    status: SelectionStatus
    requested_value: str
    options: list[SelectionOption] = Field(default_factory=list)
    created_at: datetime
    option_targets: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pending_write_message: Optional[str] = None
    pending_read_intent: Optional[PendingReadIntent] = None
