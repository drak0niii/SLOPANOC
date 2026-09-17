"""POST-6A REPAIR 2 -- Bounded Canonical Context for Request Extraction.

THE GAP THIS CLOSES: `request_contract_completion.request_current_turn_
contract` runs the contract-only agent against a BRAND-NEW, EMPTY
`InMemorySessionService` session whose only content is the current user
message, and re-runs `validate_and_persist_request_contract` against a
`_CapturedToolContext` whose `state` is an EMPTY dict. Since CONTROL-
PLANE-SEQ-04 made that call the NORMAL preflight path for every ordinary
turn (not a rare reactive remediation), both emptinesses became systemic
continuity losses:

  - the model classifying the turn sees NO conversation at all, so a
    value-only reply ("rru-3"), a correction ("actually it is RRU-10"),
    or a topic-free follow-up ("and the next one?") is classified as if
    it were the first thing ever said -- no `subject`, no `continuation`,
    and no way to declare a relationship to a request the runtime is
    still waiting on;
  - `validate_and_persist_request_contract` reads `tool_context.state
    .get(VALIDATED_REQUEST_CONTRACT_STATE_KEY)` to build its `session_
    confirmed` carry-forward map. Against an empty dict that map is
    ALWAYS empty, so the same-subject continuation carry-forward it
    implements could never fire on this path at all -- a parameter the
    user confirmed on an earlier turn of the SAME subject was silently
    dropped every time.

WHAT THIS IS: a small, deterministic, Python-authored projection of
already-trusted state -- no model call, no new persistence, no new
schema. Exactly four things, and nothing else:

  1. the ACTIVE PENDING REQUEST (`PendingGovernedRequest`) -- what the
     runtime is still waiting on: its governance class, the original
     objective (`subject`), the outstanding clarification
     (`missing_context`), and its already-VERIFIED parameters;
  2. the LAST VALIDATED REQUEST CONTRACT -- the previous turn's own
     subject/intent and its already-VERIFIED `provided_context`;
  3. a BOUNDED number of RECENT CANONICAL TURNS -- the user's own message
     text and, for the assistant side, ONLY the 6A.14A canonical result
     (`resolve_canonical_turn_result`), never raw ADK event text;
  4. nothing else. No Knowledge content, no Teams content, no tool
     payloads, no session state beyond the two keys named above.

NOT UNRESTRICTED HISTORY: `CANONICAL_TURN_BRIEF_LIMIT` turns, each
truncated to `MAX_TURN_TEXT_CHARS`, taken from the ALREADY rewind-
filtered active branch the caller supplies. A turn with no persisted
canonical result contributes its user text only -- never a reconstructed
or guessed assistant reply.

PRIOR MODEL PROSE IS NEVER A VERIFIED FACT: everything rendered here is
labelled, explicitly, as background for CLASSIFICATION only. It cannot
become a verified parameter by being shown: `_verify_and_filter_provided_
context` (request_contract.py, completely unmodified) still verifies
every `provided_context` entry against the REAL current-turn user text or
the `session_confirmed` carry-forward derived from an ALREADY-VERIFIED
prior contract -- a value the model lifts out of this block and nothing
else establishes is dropped exactly as it always was. The only NEW
verification surface this module opens is item 2 feeding `session_
confirmed`, which is itself a set of values that already passed that same
verification when they were first recorded.
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from backend.agents.team_manager.request_contract import (
    PENDING_GOVERNED_REQUEST_STATE_KEY,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    PendingGovernedRequest,
    RequestContract,
    parse_pending_governed_request,
)
from backend.api.session_state_keys import is_genuine_user_content_event
from backend.api.turn_source_references import resolve_canonical_turn_result

CANONICAL_TURN_BRIEF_LIMIT = 3
"""How many of the most recent canonical turns are ever rendered. Small
and fixed -- this is continuity context for classifying ONE message, not
a transcript. Never configurable from a request."""

MAX_TURN_TEXT_CHARS = 600
"""Per-side truncation for a rendered turn. A long turn is cut with an
explicit marker rather than silently shortened."""


class CanonicalTurnBrief(BaseModel):
    """One already-completed turn, reduced to the two texts that actually
    inform classification. `assistant_text` is `None` for a turn with no
    persisted canonical result (a failed/cancelled/genuinely pre-6A.14A
    turn) -- never backfilled from raw ADK event text."""

    model_config = ConfigDict(frozen=True)

    user_text: str
    assistant_text: Optional[str] = None


class RequestExtractionContext(BaseModel):
    """The complete, bounded input this module renders -- see the module
    docstring for the closed list of what it may contain."""

    model_config = ConfigDict(frozen=True)

    pending_request: Optional[PendingGovernedRequest] = None
    prior_contract: Optional[RequestContract] = None
    recent_turns: list[CanonicalTurnBrief] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return self.pending_request is None and self.prior_contract is None and not self.recent_turns


def _truncate(text: str) -> str:
    collapsed = text.strip()
    if len(collapsed) <= MAX_TURN_TEXT_CHARS:
        return collapsed
    return f"{collapsed[:MAX_TURN_TEXT_CHARS]} [...truncated]"


def _event_user_text(event: Any) -> str:
    """Same safe-text-join discipline `session_history_service._user_text`
    uses -- non-empty, non-`thought` text parts only. An image-only turn
    correctly yields `""`."""
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return ""
    texts: list[str] = []
    for part in parts:
        if getattr(part, "thought", None):
            continue
        text = getattr(part, "text", None)
        if text:
            texts.append(text)
    return "\n".join(texts)


def build_canonical_turn_briefs(
    active_events: Sequence[Any], state: Any, limit: int = CANONICAL_TURN_BRIEF_LIMIT
) -> list[CanonicalTurnBrief]:
    """The most recent `limit` completed turns, oldest-first, from an
    ALREADY rewind-filtered active event list (the caller supplies
    `chat_service._active_events(session.events)` -- this module never
    reimplements ADK's own rewind algorithm).

    Turn identity is the ADK `invocation_id`, exactly as `session_history_
    service._project_turns` already establishes it. Assistant text comes
    EXCLUSIVELY from `resolve_canonical_turn_result` (6A.14A) -- the same
    already fully corrected text the user actually saw -- so this can
    never reintroduce raw, pre-correction model output that the canonical
    pipeline deliberately replaced.
    """
    order: list[str] = []
    user_text_by_turn: dict[str, str] = {}
    for event in active_events:
        if not is_genuine_user_content_event(event):
            continue
        turn_id = getattr(event, "invocation_id", None)
        if not turn_id or turn_id in user_text_by_turn:
            continue
        order.append(turn_id)
        user_text_by_turn[turn_id] = _event_user_text(event)

    briefs: list[CanonicalTurnBrief] = []
    for turn_id in order[-limit:] if limit > 0 else []:
        canonical = resolve_canonical_turn_result(state, turn_id)
        briefs.append(
            CanonicalTurnBrief(
                user_text=_truncate(user_text_by_turn[turn_id]),
                assistant_text=_truncate(canonical.text) if canonical is not None and canonical.text else None,
            )
        )
    return briefs


def build_request_extraction_context(
    state: Any, active_events: Sequence[Any] = (), limit: int = CANONICAL_TURN_BRIEF_LIMIT
) -> RequestExtractionContext:
    """Builds the bounded context from already-trusted session state.
    Fail-open-to-EMPTY, never fail-loud: every input is parsed with the
    SAME tolerant discipline every other session-state reader in this
    codebase uses, so a malformed/absent value simply contributes
    nothing. Losing continuity context degrades classification quality;
    it can never grant authority, so there is nothing here to fail closed
    ON.
    """
    raw_state = state if isinstance(state, dict) else {}
    pending_request = parse_pending_governed_request(raw_state.get(PENDING_GOVERNED_REQUEST_STATE_KEY))

    prior_contract: Optional[RequestContract] = None
    raw_prior = raw_state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY)
    if isinstance(raw_prior, dict):
        try:
            prior_contract = RequestContract.model_validate(raw_prior)
        except Exception:  # noqa: BLE001 -- tolerant parse, same discipline as load_current_turn_contract
            prior_contract = None

    return RequestExtractionContext(
        pending_request=pending_request,
        prior_contract=prior_contract,
        recent_turns=build_canonical_turn_briefs(active_events, raw_state, limit),
    )


def prior_validated_contract_state(context: Optional[RequestExtractionContext]) -> dict[str, Any]:
    """The MINIMAL state seed for `request_contract_completion`'s own
    `_CapturedToolContext` -- exactly one key, the last validated
    contract, and nothing else. This is what restores `validate_and_
    persist_request_contract`'s own `session_confirmed` same-subject
    carry-forward on the preflight path; deliberately NOT the real
    session state object (that shim's `state` is a throwaway the caller
    reads its result back out of, never a live session)."""
    if context is None or context.prior_contract is None:
        return {}
    return {VALIDATED_REQUEST_CONTRACT_STATE_KEY: context.prior_contract.model_dump(mode="json")}


def render_request_extraction_context(context: Optional[RequestExtractionContext]) -> str:
    """Deterministic, Python-authored rendering only -- no model call, no
    natural-language generation. Returns `""` when there is nothing to
    say, so an ordinary first turn's own extraction prompt is byte-for-
    byte what it was before this repair."""
    if context is None or context.is_empty():
        return ""

    lines: list[str] = [
        "CONVERSATION CONTEXT (supplied by the application, for classification only -- it is "
        "background, never a statement of verified fact, and never itself a request to act on):"
    ]

    if context.pending_request is not None:
        pending = context.pending_request
        provided = ", ".join(f"{p.name}={p.value}" for p in pending.provided_context) or "(none)"
        missing = ", ".join(pending.missing_context) or "(none)"
        lines.extend(
            [
                "",
                "OUTSTANDING REQUEST (the assistant asked the user for something and has not yet "
                "received it -- decide whether THIS message answers it, cancels it, or is a new request, "
                "and say which via `pending_request_relationship`):",
                f"- original objective: {pending.subject or '(none named)'}",
                f"- kind of answer it needs: {pending.requested_output}",
                f"- still waiting on: {missing}",
                f"- already confirmed by the user: {provided}",
            ]
        )

    if context.prior_contract is not None:
        prior = context.prior_contract
        prior_provided = ", ".join(f"{p.name}={p.value}" for p in prior.provided_context) or "(none)"
        lines.extend(
            [
                "",
                "PREVIOUS REQUEST RECORD (the last request recorded in this conversation):",
                f"- subject: {prior.subject or '(none resolved)'}",
                f"- intent/requested_output: {prior.intent}/{prior.requested_output}",
                f"- parameters the user had confirmed: {prior_provided}",
            ]
        )

    if context.recent_turns:
        lines.extend(["", "RECENT TURNS (most recent last):"])
        for brief in context.recent_turns:
            lines.append(f"- user: {brief.user_text or '(no text -- attachment only)'}")
            if brief.assistant_text:
                lines.append(f"  assistant: {brief.assistant_text}")

    return "\n".join(lines)
