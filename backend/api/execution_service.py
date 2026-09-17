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

import logging
from dataclasses import dataclass
from typing import Any, MutableMapping, cast

from starlette.concurrency import run_in_threadpool

from backend.api.operation_claims import ClaimStatus, ClaimStoreUnavailableError
from backend.api.pending_action import map_pending_action
from backend.api.schemas import ExecuteActionResponse, ExecutedActionDTO
from backend.api.session_service import DEFAULT_USER_ID, ApiSessionService
from backend.api.turn_lifecycle import WORKER_ID
from backend.approval.execution_identity import EXECUTION_RECORD_STATE_KEY, derive_operation_id
from backend.approval.schemas import ApprovalDenialReason, ProposalStatus, WriteOperation
from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY, effective_status, load_active_proposal
from backend.gateway.safe_error import ErrorCode, SafeError, SafeErrorException, internal_error
from backend.tools.teams.execute_write import teams_create_chat, teams_send_message

_logger = logging.getLogger(__name__)

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

        # POST-6A -- REVALIDATE IMMEDIATELY BEFORE DISPATCH.
        #
        # The checks above ran against the session state as loaded. This
        # re-reads ownership, approval state, destination and payload from
        # the authoritative session one more time, right before anything
        # leaves this process, so a change that landed between the two
        # (a rejection, an expiry, a replaced proposal, a different
        # payload) cannot be dispatched on the strength of a stale read.
        # POST-6A -- ATOMICALLY CLAIM THIS OPERATION BEFORE SENDING IT.
        #
        # The per-session lock above is process-local. Two workers each
        # holding their own copy of it can both reach this point for the
        # same approved proposal and both dispatch -- one approved
        # message, sent twice. The claim below is keyed by the DURABLE
        # operation identity (proposal + destination + payload), taken
        # with `wait=False`, so the worker that loses does not queue
        # behind the winner and then send again; it learns it lost and
        # stops.
        #
        # Unavailable (SQLite / single-process development) means the
        # process-local lock is the whole boundary -- which is correct
        # there, and is reported rather than assumed.
        operation_claim = None
        claim_token = None
        claim_identity = derive_operation_id(
            proposal.proposal_id, proposal.operation.value, _destination_of(proposal), proposal.payload
        )
        if session_service.coordinator.distributed_available:
            operation_claim = await session_service.coordinator.distributed_lock_for(
                "execution", claim_identity, wait=False
            )
            if operation_claim is None:
                raise SafeErrorException(
                    SafeError(
                        error_code="action_failure",
                        user_message="This action is already being executed. Please wait for it to finish.",
                    )
                )
            # POST-6A -- DURABLE OWNERSHIP GENERATION, taken while the
            # advisory lock is held.
            #
            # The lock alone is liveness, not fencing: a worker that
            # passes `still_holds()` and then loses its connection is
            # still running, still believes it owns this operation, and
            # can still write. The generation returned here is the token
            # every later write must present, so a superseded worker's
            # write is refused by the database rather than by its own
            # honesty.
            #
            # `None` means the row is no longer claimable -- already
            # DISPATCHED, SUCCEEDED, FAILED or UNKNOWN_OUTCOME. That is
            # refused rather than retried: re-dispatching an operation
            # that may already have taken effect is exactly how one
            # approved message becomes two.
            try:
                claim_token = await session_service.coordinator.operation_claims.claim(claim_identity, WORKER_ID)
            except ClaimStoreUnavailableError:
                # Almost always the pending `b7c4e1a95d60` migration.
                # REFUSE rather than dispatch unfenced: a multi-worker
                # deployment without the claim table has no protection
                # against two workers sending the same approved message,
                # and silently proceeding would hide exactly that.
                await operation_claim.release()
                _logger.error(
                    "execution_service: durable operation claims are unavailable -- refusing to dispatch",
                    exc_info=True,
                )
                raise SafeErrorException(
                    SafeError(
                        error_code="action_failure",
                        user_message="This action cannot be executed safely right now. Please contact support.",
                    )
                )
            if claim_token is None:
                await operation_claim.release()
                raise SafeErrorException(
                    SafeError(
                        error_code="action_failure",
                        user_message=(
                            "This action has already been sent, or its outcome is still unknown. "
                            "Check Teams before trying again."
                        ),
                    )
                )

        try:
            return await _execute_claimed(
                session_service,
                session,
                session_id,
                proposal,
                proposal_id,
                user_id,
                operation_claim,
                claim_token,
            )
        finally:
            if operation_claim is not None:
                # `release` is itself shielded, so this still completes
                # when the surrounding request is cancelled -- a
                # connection abandoned while holding a session advisory
                # lock would block every other worker from this operation
                # until the database reaped the backend.
                await operation_claim.release()


def _claim_status_for(state) -> "ClaimStatus":
    """Map what the write path recorded onto the durable claim status.

    Reads the session's own `ExecutionRecord` rather than inferring from
    the error shape, so the durable row and the session state cannot
    disagree about whether an outcome was ambiguous. Anything
    unreadable is treated as UNKNOWN_OUTCOME -- the fail-closed reading,
    because it blocks a retry, whereas guessing FAILED would invite one.
    """
    from backend.approval.execution_identity import ExecutionStatus, parse_execution_record

    record = parse_execution_record(state.get(EXECUTION_RECORD_STATE_KEY))
    if record is None:
        return ClaimStatus.UNKNOWN_OUTCOME
    if record.status is ExecutionStatus.FAILED:
        return ClaimStatus.FAILED
    if record.status is ExecutionStatus.SUCCEEDED:
        return ClaimStatus.SUCCEEDED
    return ClaimStatus.UNKNOWN_OUTCOME


def _destination_of(proposal) -> str:
    """The destination bound into the operation identity: the chat for a
    send, the title for a create. Never a derived or defaulted value --
    a different destination is a different operation."""
    if proposal.operation == WriteOperation.TEAMS_SEND_MESSAGE:
        return str(proposal.payload.get("chatId", ""))
    return str(proposal.payload.get("title", ""))


async def _execute_claimed(
    session_service: ApiSessionService,
    session,
    session_id: str,
    proposal,
    proposal_id: str,
    user_id: str,
    operation_claim,
    claim_token=None,
) -> ExecuteActionResponse:
        revalidation = await session_service.get_session(session_id, user_id)
        revalidated = load_active_proposal(revalidation.state)
        if revalidated is None or revalidated.proposal_id != proposal_id:
            raise _denial_exception(ApprovalDenialReason.PROPOSAL_ID_MISMATCH)
        if effective_status(revalidated) != ProposalStatus.APPROVED:
            raise _denial_exception(ApprovalDenialReason.PROPOSAL_NOT_APPROVED)
        if revalidated.operation != proposal.operation or revalidated.payload != proposal.payload:
            # Destination/payload drifted between the pre-check and now --
            # the approval we validated is not the action about to be sent.
            raise _denial_exception(ApprovalDenialReason.PROPOSAL_ID_MISMATCH)
        proposal = revalidated

        tool_context = _ExecutionToolContext(state=session.state)

        # PowerAutomateClient uses the blocking `requests` library --
        # run_in_threadpool keeps this off the event loop, exactly the
        # same escape hatch FastAPI/Starlette itself uses for sync path
        # operations.
        # POST-6A -- FENCING. Between the claim and the dispatch we may
        # have lost the connection that holds it. A worker that can no
        # longer prove ownership must not send: the winner of a
        # re-election may already be sending the same thing.
        if operation_claim is not None and not await operation_claim.still_holds():
            raise SafeErrorException(
                SafeError(
                    error_code="action_failure",
                    user_message="This action could not be executed safely. Please try again.",
                )
            )

        # POST-6A -- THE FENCED WRITE THAT PRECEDES THE SEND.
        #
        # `still_holds()` above is a point-in-time liveness answer, and
        # the send happens after it; on its own it narrows the race
        # without closing it. This conditional write closes the part that
        # can be closed: marking DISPATCHED succeeds only while this
        # worker still holds the generation it was given, so a worker
        # that was superseded in the meantime is stopped HERE -- before
        # anything leaves the process, which is the only point at which
        # stopping is still honest.
        #
        # It also means the durable row says DISPATCHED before the
        # gateway is called. A crash mid-send therefore leaves DISPATCHED
        # behind, and `claim()` refuses to re-claim it: no automatic
        # resend, ever.
        if claim_token is not None:
            claims = session_service.coordinator.operation_claims
            if not await claims.record(claim_token, ClaimStatus.DISPATCHED):
                raise SafeErrorException(
                    SafeError(
                        error_code="action_failure",
                        user_message="This action is already being executed elsewhere. Please wait for it to finish.",
                    )
                )

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
            # POST-6A -- mirror the outcome into the DURABLE claim row
            # first, conditional on still owning the generation.
            #
            # UNKNOWN_OUTCOME is carried across verbatim and never
            # collapsed into FAILED: "the message may have been sent" and
            # "the message was not sent" are different facts, and only the
            # second one would make a retry safe.
            if claim_token is not None:
                await session_service.coordinator.operation_claims.record(
                    claim_token, _claim_status_for(session.state)
                )
            # POST-6A -- persist whatever the write path recorded about
            # this attempt (FAILED, or UNKNOWN_OUTCOME after an ambiguous
            # timeout) BEFORE surfacing the error. Without this the
            # ambiguity is lost on the next load and a later attempt would
            # look like a first attempt.
            if EXECUTION_RECORD_STATE_KEY in session.state:
                await session_service.persist_state_delta(
                    session, {EXECUTION_RECORD_STATE_KEY: session.state[EXECUTION_RECORD_STATE_KEY]}
                )
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
        # POST-6A -- proposal consumption and the durable execution record
        # are persisted in ONE delta. Writing them separately would leave a
        # window where the proposal reads as consumed with no record of
        # what consumed it, or vice versa.
        # POST-6A -- FENCED BEFORE PUBLISHING THE RESULT. If this write
        # fails, ownership moved on while we were in the gateway call:
        # another worker is the authority for this operation now, and
        # overwriting the session's canonical proposal/execution state
        # from here would clobber whatever it recorded. We still report
        # the send we actually made -- suppressing that would be a
        # different lie -- but we do not overwrite durable state we no
        # longer own.
        may_persist = True
        if claim_token is not None:
            may_persist = await session_service.coordinator.operation_claims.record(
                claim_token, ClaimStatus.SUCCEEDED
            )
            if not may_persist:
                _logger.warning(
                    "execution_service: ownership generation superseded during dispatch; "
                    "not overwriting session state for proposal_id=%s",
                    proposal_id,
                )
        if may_persist:
            await session_service.persist_state_delta(
                session,
                {
                    PENDING_ACTION_PROPOSAL_STATE_KEY: session.state[PENDING_ACTION_PROPOSAL_STATE_KEY],
                    EXECUTION_RECORD_STATE_KEY: session.state.get(EXECUTION_RECORD_STATE_KEY),
                },
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
