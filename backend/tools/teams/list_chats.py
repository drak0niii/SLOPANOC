"""ADK tool: `teams_list_chats`.

Deterministic. Never performs LLM reasoning
(docs/TEAMS_TOOL_CONTRACT.md #3). Chat-name matching (the "CHAT DISCOVERY"
rules) is implemented here as plain, deterministic string comparison --
incident_manager never computes or invents a chat id itself; it only ever
reads back what this function already decided.

DEVIATION NOTE from the original TEAMS_TOOL_CONTRACT.md draft: that
document described `query` as an optional free-text hint, with
disambiguation left to incident_manager's judgment. The concrete gateway
contract for this slice (`teams.listChats` takes no filter parameters at
all) and the explicit "CHAT DISCOVERY" algorithm given for this
implementation pass make the matching itself deterministic and mandatory,
so this tool takes a required `topic` and performs the exact/
case-insensitive matching internally, returning a classified outcome
rather than raw candidates alone.

WIRE FORMAT: the live gateway's proven response for `teams.listChats` is a
bare JSON array of `{"id", "topic", "createdDateTime",
"lastUpdatedDateTime"}` objects (not the `{"chats": [...]}` envelope an
earlier, unverified draft of this module assumed). Parsing goes through
`gateway.power_automate_client.extract_items`, which also still accepts a
`{"chats": [...]}` wrapper or a `{"success", "data": [...]}` envelope, so
this keeps working unchanged if the gateway's response shape changes
later. `entry["id"]` is the only field required to be present per chat;
`topic` is optional (Teams allows unnamed chats) and `participantCount`
is not provided by the current gateway at all.

LIMITATION: the current Power Automate `teams.listChats` operation takes
no paging parameters and returns whatever single page of chats the
gateway provides. No pagination is implemented in this slice -- if the
gateway ever returns a partial chat list, chat discovery in this slice can
only match against that partial list. See docs/TEAMS_TOOL_CONTRACT.md #10.
"""
from __future__ import annotations

from typing import Any, Optional

from google.adk.tools import ToolContext

from backend.gateway.power_automate_client import (
    GatewayPayload,
    PowerAutomateClient,
    extract_items,
)
from backend.gateway.safe_error import SafeErrorException, internal_error, validation_error
from backend.selection.schemas import PendingReadIntent, ReadOperation, SelectionKind
from backend.selection.service import create_pending_selection, supersede_active_selection
from backend.tools.teams.chat_resolution import ChatResolutionOutcome, resolve_chat
from backend.tools.teams.schemas import ChatMatchOutcome, ChatSummary, TeamsListChatsResult


def _parse_chats(raw: GatewayPayload) -> list[ChatSummary]:
    items = extract_items(raw, wrapper_key="chats")

    chats: list[ChatSummary] = []
    for entry in items:
        try:
            chats.append(
                ChatSummary(
                    chat_id=entry["id"],
                    title=entry.get("topic") or "",
                    participant_count=entry.get("participantCount"),
                    last_activity_at=entry.get("lastUpdatedDateTime"),
                )
            )
        except (KeyError, TypeError):
            raise internal_error(
                "The Teams connector returned an incomplete chat record."
            ) from None
    return chats


def _safe_pending_question(topic: str, pending_question: Optional[str]) -> Optional[str]:
    """Deterministic safety net for a live-traced bug: `IncidentManagerRequest
    .question` is documented as "a complete, self-contained... statement,"
    but team_manager's own instruction has historically allowed passing
    "the user's original request" verbatim for a FIRST question about a
    chat -- which, for a request like "summarize this chat room Ops
    Bridge", still names the very `topic` that turns out to be ambiguous
    here. If that tainted `question` were later replayed verbatim as the
    resume turn's driving text (read_resume.py), team_manager's own
    "an explicitly-named chat always takes priority, even over a chat
    that is already selected" rule (a real, wanted behavior for genuine
    corrections) would re-trigger resolution of the STALE, already-
    superseded name instead of trusting the freshly-selected candidate --
    reopening the exact ambiguity the user just resolved.

    This is a narrow, dynamic (never hardcoded) discard, not an intent/
    keyword classifier: it never inspects `pending_question` for what
    operation is being requested, only whether it happens to restate the
    ALREADY-KNOWN `topic` string this exact call is resolving. Deliberately
    kept OUT of read_resume.py/`build_read_resume_message` (see that
    module's own "never a destination placeholder" structural guarantee,
    still enforced by test) -- the sanitization belongs here, at the one
    point in the codebase where both values are already legitimately
    in hand, so the resume-text builder itself stays destination-free by
    construction.
    """
    if not pending_question or not pending_question.strip():
        return None
    normalized_topic = topic.strip().casefold()
    if normalized_topic and normalized_topic in pending_question.casefold():
        return None
    return pending_question.strip()


def _safe_read_operation(pending_operation: Optional[str]) -> ReadOperation:
    """Validates `pending_operation` against the closed `ReadOperation`
    vocabulary, defaulting to `SUMMARIZE` for anything missing/invalid --
    never raises, mirroring every other closed-enum coercion in this
    codebase (e.g. `SelectionKind`). This is a validation/default step
    only, never a classifier: `incident_manager` decides the value via
    its own reasoning (see `PendingReadIntent.operation`'s docstring);
    this function does not inspect any free text to derive it.
    """
    try:
        return ReadOperation(pending_operation)
    except ValueError:
        return ReadOperation.SUMMARIZE


def _match(
    chats: list[ChatSummary],
    topic: str,
    tool_context: Optional[ToolContext],
    pending_write_message: Optional[str],
    pending_question: Optional[str],
    pending_time_range: Optional[str],
    pending_operation: Optional[str] = None,
) -> TeamsListChatsResult:
    """Deterministic resolution, delegated to chat_resolution.py's
    centralized `resolve_chat` (shared by every Teams capability that
    needs to resolve a chat by name -- instruction: "Do not implement one
    matching algorithm for sendMessage, another for summarize.").

    - Exact match (exactly one) -> "matched", unchanged from before.
    - Multiple chats sharing the exact same title -> "ambiguous",
      unchanged from before -- a real Teams edge case, distinct from
      "no exact match, but similar titles exist" below.
    - No exact match, but deterministic similarity scoring found real
      candidates -> still "not_found" (never silently treated as a
      match), PLUS a `PendingSelection` is created in session state
      (interaction-capability extension) and `selection_pending` is set
      -- the model never sees the candidate titles themselves, only this
      boolean, so it can explain that disambiguation is needed without
      ever fabricating/repeating options itself.
    - No exact match and no similar candidates either -> plain
      "not_found", exactly as before this extension existed.
    """
    normalized_topic = topic.strip().casefold()
    exact_matches = [
        chat for chat in chats if chat.title.strip().casefold() == normalized_topic
    ]

    if len(exact_matches) == 1:
        if tool_context is not None:
            # An exact resolution supersedes any earlier open
            # disambiguation for this session -- see
            # supersede_active_selection's docstring.
            supersede_active_selection(tool_context.state)
        return TeamsListChatsResult(
            chats=chats,
            match=ChatMatchOutcome.MATCHED,
            matched_chat=exact_matches[0],
        )
    if len(exact_matches) > 1:
        return TeamsListChatsResult(
            chats=chats,
            match=ChatMatchOutcome.AMBIGUOUS,
            candidates=exact_matches,
        )

    resolution = resolve_chat(topic, chats)
    if resolution.outcome == ChatResolutionOutcome.NOT_FOUND and resolution.similar_candidates:
        if tool_context is not None:
            # A given ambiguity arises from either a write or a read,
            # never both -- `pending_write_message` set means the write
            # path resumes deterministically (unchanged); otherwise this
            # was a read attempt, so its own structured intent is
            # captured for the same kind of deterministic resume (see
            # PendingReadIntent's docstring) -- even when both fields are
            # None (a plain "just summarize" request), recording an
            # (empty) PendingReadIntent still correctly marks "this was a
            # read," letting `selection_service.choose` tell reads and
            # writes apart without a separate boolean.
            create_pending_selection(
                kind=SelectionKind.TEAMS_CHAT,
                requested_value=topic,
                candidates=resolution.similar_candidates,
                session_state=tool_context.state,
                pending_write_message=pending_write_message,
                pending_read_intent=(
                    None
                    if pending_write_message is not None
                    else PendingReadIntent(
                        operation=_safe_read_operation(pending_operation),
                        question=_safe_pending_question(topic, pending_question),
                        requested_time_range=pending_time_range,
                    )
                ),
            )
        return TeamsListChatsResult(
            chats=chats,
            match=ChatMatchOutcome.NOT_FOUND,
            similar_candidates=resolution.similar_candidates,
            selection_pending=tool_context is not None,
        )
    return TeamsListChatsResult(chats=chats, match=ChatMatchOutcome.NOT_FOUND)


def teams_list_chats(
    topic: str,
    pending_write_message: Optional[str] = None,
    pending_question: Optional[str] = None,
    pending_time_range: Optional[str] = None,
    pending_operation: Optional[str] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Look up the Teams chat the user is referring to, by its exact title.

    Calls the Power Automate `teams.listChats` operation, then resolves
    `topic` against the returned chats using an exact, case-insensitive
    title match. Never guesses: if more than one chat shares the exact
    title, the result's `match` is "ambiguous"; if none do, it is
    "not_found" -- unless deterministic similarity scoring found real,
    similar candidates, in which case a `PendingSelection` is created
    (see `_match`'s docstring) and `selection_pending` is set. Only ever
    returns a `chat_id` (or, in `PendingSelection`, an `option_id`
    resolving to one) that Power Automate actually returned -- never an
    invented id, and never a raw chat id exposed as an "option" the
    frontend could pass back directly.

    Args:
      topic: The Teams chat title/name, exactly as the user stated it.
      pending_write_message: Set ONLY when this resolution is happening
        while preparing a `teams.sendMessage` write -- the exact message
        text already gathered from the user, so that if disambiguation
        turns out to be needed, choosing a candidate can resume the
        write immediately without asking for the message again. Leave
        unset for a read (summarize/get messages) request.
      pending_question/pending_time_range/pending_operation: For a READ
        request (i.e. `pending_write_message` is unset), pass the SAME
        `question`/`requested_time_range` values this call already
        received as part of its own request, unmodified -- if
        disambiguation turns out to be needed, choosing a candidate can
        resume the read immediately, using only this already-structured
        intent, never the user's original raw wording (which would still
        name the old, unresolved destination). `pending_operation` is
        your own closed classification of the base kind of read --
        `"summarize"` or `"get_messages"` (see `ReadOperation`) -- kept
        SEPARATE from `pending_question` specifically so the base intent
        survives even in the rare case `pending_question` has to be
        discarded for still naming the ambiguous chat (see
        `_safe_pending_question`). Leave all three unset for a write
        request.
      tool_context: ADK-injected; needed only to persist a
        `PendingSelection` when disambiguation is needed (see module
        docstring for how this reaches team_manager's real session).

    Returns:
      On success, a dict with `chats` (every chat the gateway returned),
      `match` ("matched" | "ambiguous" | "not_found"), `matched_chat`
      (set only when `match` is "matched"), `candidates` (set only when
      `match` is "ambiguous"), `similar_candidates` and
      `selection_pending` (set only when `match` is "not_found" and
      similar chats exist). On failure, a dict with a single `error` key
      holding a SafeError (`errorCode`, `userMessage`, `retryable`,
      `correlationId`).
    """
    if not topic or not topic.strip():
        return {
            "error": validation_error(
                "A chat topic/title is required."
            ).safe_error.to_dict()
        }

    client = PowerAutomateClient()
    try:
        raw = client.list_chats()
        chats = _parse_chats(raw)
    except SafeErrorException as exc:
        return {"error": exc.safe_error.to_dict()}

    result = _match(
        chats, topic, tool_context, pending_write_message, pending_question, pending_time_range, pending_operation
    )
    return result.model_dump(mode="json")
