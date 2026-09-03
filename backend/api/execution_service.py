"""The trusted execution-continuation for an APPROVED proposal (Phase 4G).

WHY THIS FILE EXISTS: `approval_service.py`'s `approve()`/`reject()` only
ever change a proposal's `status` -- confirmed by that module's own
docstring ("approving a proposal only ever changes its status; it never
executes anything"). The only code that can actually perform a Teams
write, `backend/tools/teams/execute_write.py`'s `teams_create_chat`/
`teams_send_message`, is registered ONLY as an ADK tool on
`incident_manager` -- meaning, before this file, the ONLY way an approved
proposal could ever actually execute was for Gemini to independently
decide, on some future chat turn, to call that tool again. There was no
deterministic backend path from "user clicked Approve" to "the write
actually happened."

THE SAFE CONTINUATION: `teams_create_chat`/`teams_send_message` are plain,
non-async Python functions with NO dependency on Gemini, the ADK Runner,
or any agent machinery -- they only need a `tool_context` argument
exposing one attribute, `.state` (confirmed by their own source:
`state = tool_context.state if tool_context is not None else {}`, and by
the existing test `test_teams_execute_write.py::
test_successful_create_chat_consumes_the_proposal`, which calls them
directly with a trivial state-only wrapper and zero LLM involvement).
This module calls those exact same functions directly, deterministically,
using ONLY the payload already stored server-side on the approved
proposal -- never text typed by the user in this request, never anything
the model supplies. `authorize_write` and `consume_proposal` (both inside
the unmodified `teams_create_chat`/`teams_send_message`) still
independently re-validate operation, payload hash, and status on every
single call -- this module gets no authorization shortcut; it just
supplies the same trusted inputs a legitimate execution already requires.

NEVER MODIFIED BY THIS FILE: backend/tools/teams/execute_write.py,
backend/approval/policy_gate.py, backend/approval/service.py's lifecycle
transitions, backend/api/approval_service.py, either agent's tool list.
No new ADK tool is registered anywhere -- this is reachable ONLY via the
new HTTP route, exactly like approve()/reject() already are.

STALE-PROPOSAL GUARD: `authorize_write` takes no `proposal_id` at all --
it only checks whatever proposal is CURRENTLY active against operation +
payload hash + status. That is sufficient for the tool's own internal
safety, but NOT sufficient, on its own, to guarantee a stale card (proposal
A, already superseded by proposal B) can never act against a newer
proposal that happens to look similar. This module therefore performs its
OWN explicit `proposal_id` + status pre-check first (mirroring
`approve_proposal`/`reject_proposal`'s own `PROPOSAL_ID_MISMATCH` check in
backend/approval/service.py) before ever calling the execute tool.

EXECUTION OUTCOME HONESTY: on a `PowerAutomateClient` failure, the
frozen gateway (`power_automate_client.py`) collapses both a definite
timeout and various response failures into the same `run_failure`/
`internal_error` codes -- there is no reliable signal here to prove the
write never happened (a timeout on OUR side does not mean Power Automate
never received/processed the request). This module never claims success
in that case, but it also never claims a definite failure it cannot
prove; the caller (the API route / frontend) is expected to treat
`run_failure`/`internal_error` as an unconfirmed outcome, not a proven
failure and not a success. Only `rate_limited` (429 -- the gateway
definitively responded and refused before any write could happen) and this
module's own pre-check denials count as definite ("nothing executed").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, MutableMapping, cast

from starlette.concurrency import run_in_threadpool

from backend.api.pending_action import map_pending_action
from backend.api.schemas import ExecuteActionResponse, ExecutedActionDTO
from backend.api.session_service import DEFAULT_USER_ID, ApiSessionService
from backend.approval.schemas import ApprovalDenialReason, ProposalStatus, WriteOperation
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY, effective_status, load_active_proposal
from backend.gateway.safe_error import ErrorCode, SafeError, SafeErrorException, internal_error
from backend.tools.teams.execute_write import teams_create_chat, teams_send_message

_DENIAL_MESSAGES: dict[ApprovalDenialReason, str] = {
    ApprovalDenialReason.NO_PENDING_PROPOSAL: "There is no active proposal in this session.",
    ApprovalDenialReason.PROPOSAL_ID_MISMATCH: (
        "This proposal is no longer the active one for this session -- it may have been "
        "replaced by a newer request."
    ),
    ApprovalDenialReason.PROPOSAL_NOT_APPROVED: "This action has not been approved yet.",
    ApprovalDenialReason.PROPOSAL_EXPIRED: "This proposal has expired and can no longer be executed.",
    ApprovalDenialReason.PROPOSAL_REJECTED: "This proposal was rejected and cannot be executed.",
    ApprovalDenialReason.PROPOSAL_CONSUMED: "This action was already executed and cannot run again.",
}


def _denial_exception(reason: ApprovalDenialReason) -> SafeErrorException:
    message = _DENIAL_MESSAGES.get(reason, "This action cannot be executed right now.")
    return SafeErrorException(SafeError(error_code="action_failure", user_message=message, reason=reason.value))


@dataclass
class _ExecutionToolContext:
    """The minimal `tool_context`-shaped object `teams_create_chat`/
    `teams_send_message` actually need -- only a `.state` attribute (see
    execute_write.py: `state = tool_context.state if tool_context is not
    None else {}`). `state` IS this call's own `session.state` -- the
    same trusted, session-scoped mapping `authorize_write`/
    `consume_proposal` already operate on inside the tool call, so nothing
    here changes what those functions see or can do.
    """

    state: MutableMapping[str, Any]


async def execute(
    session_service: ApiSessionService,
    session_id: str,
    proposal_id: str,
    user_id: str = DEFAULT_USER_ID,
) -> ExecuteActionResponse:
    await session_service.get_session(session_id, user_id)  # 404 before ever taking the lock

    async with session_service.lock_for(session_id, user_id):
        session = await session_service.get_session(session_id, user_id)  # authoritative, latest read

        proposal = load_active_proposal(session.state)
        if proposal is None:
            raise _denial_exception(ApprovalDenialReason.NO_PENDING_PROPOSAL)
        if proposal.proposal_id != proposal_id:
            raise _denial_exception(ApprovalDenialReason.PROPOSAL_ID_MISMATCH)

        status = effective_status(proposal)
        if status == ProposalStatus.CONSUMED:
            raise _denial_exception(ApprovalDenialReason.PROPOSAL_CONSUMED)
        if status == ProposalStatus.REJECTED:
            raise _denial_exception(ApprovalDenialReason.PROPOSAL_REJECTED)
        if status == ProposalStatus.EXPIRED:
            raise _denial_exception(ApprovalDenialReason.PROPOSAL_EXPIRED)
        if status == ProposalStatus.PENDING:
            raise _denial_exception(ApprovalDenialReason.PROPOSAL_NOT_APPROVED)
        # status == ProposalStatus.APPROVED from here on -- the only
        # remaining possibility given ProposalStatus's 5 closed values.

        tool_context = _ExecutionToolContext(state=session.state)

        # PowerAutomateClient uses the blocking `requests` library --
        # run_in_threadpool keeps this off the event loop, exactly the
        # same escape hatch FastAPI/Starlette itself uses for sync path
        # operations.
        if proposal.operation == WriteOperation.TEAMS_CREATE_CHAT:
            result = await run_in_threadpool(
                teams_create_chat,
                proposal.payload.get("title", ""),
                list(proposal.payload.get("members", [])),
                tool_context=tool_context,
            )
        elif proposal.operation == WriteOperation.TEAMS_SEND_MESSAGE:
            result = await run_in_threadpool(
                teams_send_message,
                proposal.payload.get("chatId", ""),
                proposal.payload.get("message", ""),
                tool_context=tool_context,
            )
        else:  # pragma: no cover -- unreachable given WriteOperation's closed 2-member enum
            raise internal_error("Unsupported action type.")

        if "error" in result:
            # Re-raises the tool layer's own SafeError shape unchanged --
            # same error_code/correlation_id it already generated. Never
            # populates `reason` here: this path only reflects
            # PowerAutomateClient/authorize_write failures from the
            # (frozen, unmodified) tool layer, not one of THIS module's
            # own closed pre-check denials above.
            error = result["error"]
            raise SafeErrorException(
                SafeError(
                    error_code=cast(ErrorCode, error["errorCode"]),
                    user_message=error["userMessage"],
                    correlation_id=error["correlationId"],
                )
            )

        # Success: consume_proposal already ran INSIDE teams_create_chat/
        # teams_send_message (the fixed step-5 of execute_write.py's own
        # order), mutating session.state in place. Persist it exactly the
        # way approve()/reject() persist their own transition.
        await session_service.persist_state_delta(
            session, {PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]}
        )
        refreshed = await session_service.get_session(session_id, user_id)
        pending_action = map_pending_action(refreshed.state)

        executed_action = ExecutedActionDTO(
            chat_id=result.get("chatId"),
            title=result.get("title"),
            web_url=result.get("webUrl"),
        )

    return ExecuteActionResponse(
        session_id=session_id,
        result="executed",
        pending_action=pending_action,
        executed_action=executed_action,
    )
