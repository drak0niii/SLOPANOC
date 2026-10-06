"""Operator-requested controlled execution of ONE authorized diagnostic read (Tranche 3).

Reachable only through its HTTP route (never a model tool). The client supplies only the
`check_id`; everything else -- command, source, target, policy, adapter -- is re-derived and
re-validated server-side by `backend.operations.control_plane.execute_read_check`. State-changing
actions are refused here unconditionally. The next diagnostic step is never executed
automatically: the observed output is recorded on the check and the Technical Authority Engineer
decides the next step on the next turn.
"""
from __future__ import annotations

from backend.api.schemas import ReadExecutionDTO, ReadExecutionResponse
from backend.api.session_service import DEFAULT_USER_ID, ApiSessionService
from backend.gateway.safe_error import SafeError, SafeErrorException
from backend.operations.control_plane import PERSISTED_STATE_KEYS, OperationalDenial, execute_read_check


async def execute_read(
    session_service: ApiSessionService,
    session_id: str,
    check_id: str,
    user_id: str = DEFAULT_USER_ID,
) -> ReadExecutionResponse:
    await session_service.get_session(session_id, user_id)  # 404 before ever taking the lock

    async with session_service.lock_for(session_id, user_id):
        session = await session_service.get_session(session_id, user_id)
        try:
            result = await execute_read_check(session.state, check_id, user_id=user_id)
        except OperationalDenial as denial:
            await session_service.persist_state_delta(session, {k: session.state[k] for k in PERSISTED_STATE_KEYS if k in session.state})
            raise SafeErrorException(SafeError(error_code="action_failure", user_message=denial.message, reason=denial.reason)) from None
        await session_service.persist_state_delta(session, {k: session.state[k] for k in PERSISTED_STATE_KEYS if k in session.state})

    return ReadExecutionResponse(
        session_id=session_id,
        check_id=check_id,
        execution=ReadExecutionDTO(
            execution_id=result.execution_id,
            status=result.status.value,
            adapter_type=result.adapter_type,
            started_at=result.started_at.isoformat(),
            completed_at=result.completed_at.isoformat(),
            exit_code=result.exit_code,
            observed_output=result.observed_evidence,
            detail=result.detail,
        ),
    )
