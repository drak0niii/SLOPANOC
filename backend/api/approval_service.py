"""The trusted approve/reject orchestration for the API layer (Phase
4B; user-scoped since Phase 4C).

SECURITY CONTRACT: this module -- together with
`backend/tests/manual/approval_dev_cli.py` -- is one of only two callers
of `backend.approval.service.approve_proposal`/`reject_proposal` in this
entire codebase. Neither `team_manager` nor `incident_manager`, nor any
Gemini-reachable tool, ever calls either function (see
backend/approval/service.py's own "SECURITY CONTRACT" docstring,
unchanged by this milestone). This module never calls the ADK `Runner`,
never talks to Gemini, and never calls `PowerAutomateClient` or any
`teams_create_chat`/`teams_send_message` tool -- approving a proposal
only ever changes its `status`; it never executes anything (instruction
section 10).

TRUSTED BOUNDARY: `approve`/`reject` take a `proposal_id` and nothing
else that could influence the outcome -- the caller (`app.py`) supplies
it from the request body's one allowed field. Every other fact
(`operation`, `payload`, current `status`) is read from the session's own
trusted state, never accepted as client input, exactly as
`approve_proposal`/`reject_proposal` themselves already require. No
proposal-id matching logic is reimplemented here -- both functions
already perform that exact check (`ApprovalDenialReason.PROPOSAL_ID_MISMATCH`);
this module only translates their result into an HTTP-safe response.

TOCTOU / CONCURRENCY: both functions call
`session_service.get_session(session_id, user_id)` once, BEFORE acquiring
`session_service.lock_for(session_id, user_id)`, purely to raise a clean
404 for an unknown/foreign session without ever taking a lock for an id
that doesn't correspond to a real session owned by this user. Once the
lock is held, the session is RE-FETCHED (the authoritative read) before
the transition is applied -- this is the same lock a concurrent chat turn
for the same `(user_id, session_id)` would be holding (see
session_service.py's module docstring), so an approve/reject call can
never race a `chat_service.run_turn` call for the same session.

OWNERSHIP (Phase 4C): `user_id` is resolved once, upstream, from
`backend.api.identity.UserContext` (never from the request body -- see
identity.py) and passed straight through to every
`session_service` call below. Because ADK session lookup is keyed by
`(app_name, user_id, session_id)`, a `proposal_id` that is perfectly valid
in User A's session simply cannot be reached at all through User B's
`user_id` -- the session itself resolves to `not_found` before this
module ever inspects a proposal. No separate cross-user proposal check is
implemented here because none is needed.
"""
from __future__ import annotations

from typing import Optional

from backend.api.pending_action import map_pending_action
from backend.api.schemas import ApprovalResponse
from backend.api.session_service import DEFAULT_USER_ID, ApiSessionService
from backend.approval.schemas import ApprovalDenialReason
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY, approve_proposal, reject_proposal
from backend.gateway.safe_error import SafeError, SafeErrorException

# Reuses the existing, generic `action_failure` SafeError code (already
# mapped to HTTP 409 in errors.py, Phase 4A) for every "this proposal
# cannot be approved/rejected right now" case -- these are all instances
# of the same underlying situation (the request no longer matches the
# proposal's current state), not distinct error categories, so one code
# is the cleaner choice per instruction ("Exact mapping may differ if
# existing API conventions suggest something cleaner.").
_DENIAL_MESSAGES: dict[ApprovalDenialReason, str] = {
    ApprovalDenialReason.NO_PENDING_PROPOSAL: "There is no active proposal in this session.",
    ApprovalDenialReason.PROPOSAL_ID_MISMATCH: (
        "This proposal is no longer the active one for this session -- it may have been "
        "replaced by a newer request."
    ),
    ApprovalDenialReason.PROPOSAL_EXPIRED: "This proposal has expired and can no longer be approved or rejected.",
    ApprovalDenialReason.PROPOSAL_REJECTED: "This proposal was already rejected.",
    ApprovalDenialReason.PROPOSAL_CONSUMED: "This proposal was already used and can no longer be changed.",
}


def _denial_exception(reason: Optional[ApprovalDenialReason]) -> SafeErrorException:
    message = _DENIAL_MESSAGES.get(reason, "This proposal cannot be approved or rejected right now.")
    # `reason` (Phase 4G) exposes the exact closed ApprovalDenialReason value
    # on the wire so a frontend can classify expiry/staleness/already-used
    # deterministically, never by pattern-matching `user_message` prose --
    # see SafeError's own docstring on why this is optional/additive.
    return SafeErrorException(
        SafeError(
            error_code="action_failure",
            user_message=message,
            reason=reason.value if reason is not None else None,
        )
    )


async def approve(
    session_service: ApiSessionService,
    session_id: str,
    proposal_id: str,
    user_id: str = DEFAULT_USER_ID,
) -> ApprovalResponse:
    await session_service.get_session(session_id, user_id)  # 404 before ever taking the lock

    async with session_service.lock_for(session_id, user_id):
        session = await session_service.get_session(session_id, user_id)  # the authoritative, latest read
        result = approve_proposal(proposal_id, session.state)
        if not result.success:
            raise _denial_exception(result.reason)

        await session_service.persist_state_delta(
            session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
        )
        refreshed = await session_service.get_session(session_id, user_id)
        pending_action = map_pending_action(refreshed.state)

    return ApprovalResponse(session_id=session_id, result="approved", pending_action=pending_action)


async def reject(
    session_service: ApiSessionService,
    session_id: str,
    proposal_id: str,
    user_id: str = DEFAULT_USER_ID,
) -> ApprovalResponse:
    await session_service.get_session(session_id, user_id)

    async with session_service.lock_for(session_id, user_id):
        session = await session_service.get_session(session_id, user_id)
        result = reject_proposal(proposal_id, session.state)
        if not result.success:
            raise _denial_exception(result.reason)

        await session_service.persist_state_delta(
            session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
        )
        refreshed = await session_service.get_session(session_id, user_id)
        pending_action = map_pending_action(refreshed.state)

    return ApprovalResponse(session_id=session_id, result="rejected", pending_action=pending_action)
