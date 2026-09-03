"""The trusted choose/skip orchestration for the API layer (interaction-
capability extension), mirroring approval_service.py's exact shape:
lock-then-re-fetch-then-persist, `user_id` always resolved from
`UserContext` upstream, never client input.

SECURITY CONTRACT: this module is the only caller of
`backend.selection.service.resolve_selection`/`skip_selection` in this
codebase -- neither is ever registered as an ADK tool (see
backend/selection/__init__.py). This module never calls the ADK
`Runner`, never talks to Gemini, and never calls `PowerAutomateClient` or
any `teams_create_chat`/`teams_send_message` tool.

RESUME SEMANTICS (instruction sections 13-15, 22): choosing an option for
a WRITE-pending selection (`pending_write_message` set at creation time --
see selection/schemas.py) deterministically creates the `ActionProposal`
directly here, via the SAME `create_action_proposal` Python function
`teams_propose_send_message` itself calls -- no further model/Runner
involvement, since the message text and the resolved chat_id are both
already fully known; there is no judgment call left for Gemini to make.
This never re-executes anything -- it only ever creates a new PENDING
proposal, subject to the exact same frozen approve/execute flow as any
other proposal (instruction section 33: write security is unchanged).

For a READ-pending selection (`pending_write_message` is `None`), this
module does NOT attempt to answer the read itself -- there is no
deterministic "what the answer is" for a summary/question. It persists
`selected_teams_chat_id`/`selected_teams_chat_topic` (the SAME
authoritative state keys `state_sync.py` already writes), marks the
selection resolved, and computes `resume_message` (see
`selection/read_resume.py`) DETERMINISTICALLY from the selection's own
stored `pending_read_intent` -- never from the user's original raw
request text. The frontend then drives one ordinary new backend turn
with that exact text (see AppState.tsx's `chooseSelectionOption`), so
`incident_manager` retrieves and summarizes for real, through the
existing, unmodified topology -- this module itself never calls
`incident_manager`, never talks to Gemini, and never fabricates an
answer.

HARDENING PASS: the previous design instead had the frontend resend the
user's own original request text verbatim. That text still named the
OLD, unresolved destination, so `incident_manager` -- whose prompt
correctly gives an explicitly-named chat priority over the
currently-selected one -- resolved that old name again and reopened the
exact same ambiguity. `resume_message` fixes this at the source: it is
never derived from, and never contains, the original request text or the
old destination name.

PRODUCTION HARDENING PASS (`ResolvedReadContinuation`): `resume_message`
is still computed and returned -- the frontend still drives one ordinary
new backend turn with it -- but it is now DESCRIPTIVE CONTEXT ONLY, never
the mechanism establishing correctness. This function ALSO builds a
`ResolvedReadContinuation` (see selection/schemas.py) from the exact same
`chat_id`/`topic`/`pending_read_intent` already in hand here, and stores
it (single-use) via `store_read_continuation`. `chat_service.py` consumes
it deterministically at the start of the next turn, so team_manager's
model is never the thing deciding destination/operation/focus/time-range
for a resumed read -- see `backend.agents.team_manager
.read_continuation_enforcement`'s own module docstring for exactly how.
"""
from __future__ import annotations

from typing import Optional

from backend.api.pending_action import map_pending_action
from backend.api.schemas import ChooseSelectionResponse, PendingActionDTO, SkipSelectionResponse
from backend.api.session_service import DEFAULT_USER_ID, ApiSessionService
from backend.approval.schemas import WriteOperation
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY, create_action_proposal
from backend.gateway.safe_error import SafeError, SafeErrorException
from backend.selection.read_resume import build_read_resume_message
from backend.selection.schemas import PendingReadIntent, PendingSelection, ResolvedReadContinuation
from backend.selection.service import (
    PENDING_SELECTION_STATE_KEY,
    SelectionDenialReason,
    SelectionResult,
    resolve_selection,
    skip_selection,
    store_read_continuation,
)
from backend.tools.teams.state_keys import SELECTED_TEAMS_CHAT_ID_STATE_KEY, SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY

_DENIAL_MESSAGES: dict[SelectionDenialReason, str] = {
    SelectionDenialReason.NO_PENDING_SELECTION: "There is no active chat selection in this session.",
    SelectionDenialReason.SELECTION_ID_MISMATCH: (
        "This selection is no longer the active one for this session -- it may have been replaced."
    ),
    SelectionDenialReason.SELECTION_NOT_PENDING: "This selection has already been resolved.",
    SelectionDenialReason.OPTION_NOT_FOUND: "That option does not belong to this selection.",
}


def _denial_exception(reason: Optional[SelectionDenialReason]) -> SafeErrorException:
    message = _DENIAL_MESSAGES.get(reason, "This selection cannot be resolved right now.")
    return SafeErrorException(
        SafeError(
            error_code="action_failure",
            user_message=message,
            reason=reason.value if reason is not None else None,
        )
    )


def _label_for(selection: PendingSelection, option_id: str) -> str:
    for option in selection.options:
        if option.option_id == option_id:
            return option.label
    return ""


async def choose(
    session_service: ApiSessionService,
    session_id: str,
    selection_id: str,
    option_id: str,
    user_id: str = DEFAULT_USER_ID,
) -> ChooseSelectionResponse:
    await session_service.get_session(session_id, user_id)  # 404 before ever taking the lock

    async with session_service.lock_for(session_id, user_id):
        session = await session_service.get_session(session_id, user_id)  # the authoritative, latest read
        result: SelectionResult = resolve_selection(selection_id, option_id, session.state)
        if not result.success:
            raise _denial_exception(result.reason)
        selection = result.selection
        assert selection is not None  # a successful result always carries the resolved selection

        target = result.target or {}
        chat_id = target.get("chat_id")
        topic = target.get("topic")
        delta: dict = {
            PENDING_SELECTION_STATE_KEY: session.state[PENDING_SELECTION_STATE_KEY],
            SELECTED_TEAMS_CHAT_ID_STATE_KEY: chat_id,
            SELECTED_TEAMS_CHAT_TOPIC_STATE_KEY: topic,
        }

        is_write_intent = selection.pending_write_message is not None and chat_id is not None
        if is_write_intent:
            summary = f"Send message to chat {chat_id}: {selection.pending_write_message}"
            create_action_proposal(
                WriteOperation.TEAMS_SEND_MESSAGE.value,
                {"chatId": chat_id, "message": selection.pending_write_message},
                session.state,
                summary=summary,
                target_display_name=topic,
            )
            delta[PENDING_ACTION_PROPOSAL_STATE_KEY] = session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]

        # Production-hardening pass: for a resolved READ selection, build
        # the deterministic, single-use `ResolvedReadContinuation` now --
        # while `chat_id`/`topic` are still the exact, just-validated
        # values from `option_targets` (never a re-resolution) and
        # `selection.pending_read_intent` is still the exact, untouched
        # value captured before the ambiguity existed. Included in the
        # SAME `persist_state_delta` call as the rest of this transition
        # (atomic -- never a separate write). `chat_service.py` is the
        # sole consumer (see `pop_read_continuation`); nothing here
        # invokes the model, `incident_manager`, or any Teams tool.
        if not is_write_intent and chat_id is not None and topic is not None:
            read_intent = selection.pending_read_intent or PendingReadIntent()
            continuation = ResolvedReadContinuation(
                operation=read_intent.operation,
                selected_chat_id=chat_id,
                selected_chat_topic=topic,
                question=read_intent.question,
                requested_time_range=read_intent.requested_time_range,
            )
            store_read_continuation(delta, continuation)

        await session_service.persist_state_delta(session, delta)

        pending_action: Optional[PendingActionDTO] = None
        if is_write_intent:
            refreshed = await session_service.get_session(session_id, user_id)
            pending_action = map_pending_action(refreshed.state)

        # A given selection is either a write or a read, never both --
        # `resume_message` is only ever computed for the read case, and
        # `build_read_resume_message` is destination-free by
        # construction (see its own module docstring), so this never
        # reintroduces the old, unresolved requested_value.
        resume_message: Optional[str] = None if is_write_intent else build_read_resume_message(selection.pending_read_intent)

    return ChooseSelectionResponse(
        session_id=session_id,
        selection_id=selection_id,
        status=selection.status.value,
        selected_label=_label_for(selection, option_id),
        pending_action=pending_action,
        resume_message=resume_message,
    )


async def skip(
    session_service: ApiSessionService,
    session_id: str,
    selection_id: str,
    user_id: str = DEFAULT_USER_ID,
) -> SkipSelectionResponse:
    await session_service.get_session(session_id, user_id)

    async with session_service.lock_for(session_id, user_id):
        session = await session_service.get_session(session_id, user_id)
        result: SelectionResult = skip_selection(selection_id, session.state)
        if not result.success:
            raise _denial_exception(result.reason)
        selection = result.selection
        assert selection is not None

        await session_service.persist_state_delta(
            session, {PENDING_SELECTION_STATE_KEY: session.state[PENDING_SELECTION_STATE_KEY]}
        )

    return SkipSelectionResponse(session_id=session_id, selection_id=selection_id, status=selection.status.value)
