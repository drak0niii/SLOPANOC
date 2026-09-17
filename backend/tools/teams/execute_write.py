"""ADK tools: `teams_create_chat` / `teams_send_message`.

These are the approval-gated Teams WRITE EXECUTION tools (instruction
milestone 3B sections 3/10/17). Every call follows the same fixed order,
and nothing about it is prompt-controlled:

  1. normalize the exact execution payload (write_validation.py -- the
     SAME functions `propose_write.py` used, so a legitimately unchanged
     request always re-normalizes to the exact approved payload).
  2. `backend.approval.policy_gate.authorize_write(operation, payload,
     tool_context.state)`.
  3. if not authorized: STOP. No Power Automate call is made. Return a
     safe deterministic denial.
  4. call `PowerAutomateClient` with that exact normalized payload.
  5. only if Power Automate reports success: `consume_proposal(...)` --
     never before, never merely because step 2 passed.
  6. return a safe, structured result.

This ordering is enforced by the function body itself, not by an LLM
instruction -- no prompt wording can skip step 2 or reorder step 5 ahead
of step 4, because the model never sees or controls the code path inside
this tool; it only supplies the arguments.

STATE, ACROSS THE AgentTool BOUNDARY: see propose_write.py's module
docstring for the verified mechanism by which this tool's
`tool_context.state` both contains team_manager's real
`pending_action_proposal` (read by `authorize_write`) and forwards this
tool's `consume_proposal` write back into team_manager's real session
when the call returns.
"""
from __future__ import annotations

from datetime import datetime, timezone

from typing import Any, Optional

from google.adk.tools import ToolContext

from backend.approval.policy_gate import authorize_write
from backend.approval.schemas import ApprovalDenialReason, WriteOperation
from backend.approval.execution_identity import (
    EXECUTION_RECORD_STATE_KEY,
    ExecutionRecord,
    ExecutionStatus,
    build_execution_record_delta,
    derive_operation_id,
    parse_execution_record,
    payload_hash,
    retry_permitted,
)
from backend.approval.service import consume_proposal
from backend.gateway.power_automate_client import GatewayAmbiguousOutcomeError
from backend.gateway.write_envelope import (
    WriteEnvelopeMode,
    WriteEnvelopeOutcome,
    validate_write_envelope,
)
from backend.gateway.power_automate_client import PowerAutomateClient
from backend.gateway.safe_error import SafeError, SafeErrorException
from backend.tools.teams.write_validation import (
    normalize_create_chat_payload,
    normalize_send_message_payload,
)

_DENIAL_MESSAGES: dict[ApprovalDenialReason, str] = {
    ApprovalDenialReason.NO_PENDING_PROPOSAL: "This action has not been proposed yet.",
    ApprovalDenialReason.PROPOSAL_NOT_APPROVED: "This action has not been approved yet.",
    ApprovalDenialReason.PROPOSAL_EXPIRED: "The approval for this action has expired. Please propose it again.",
    ApprovalDenialReason.PROPOSAL_REJECTED: "This action was rejected and cannot be executed.",
    ApprovalDenialReason.PROPOSAL_CONSUMED: "This action was already executed and cannot run again.",
    ApprovalDenialReason.OPERATION_MISMATCH: (
        "The approved action does not match what was requested. Please propose it again."
    ),
    ApprovalDenialReason.PAYLOAD_MISMATCH: (
        "This action has changed since it was approved. Please propose it again."
    ),
}


def _denial_result(reason: Optional[ApprovalDenialReason]) -> dict[str, Any]:
    message = _DENIAL_MESSAGES.get(
        reason, "This action is not currently authorized to run."
    )
    return {"error": SafeError(error_code="authorization_error", user_message=message).to_dict()}


def _envelope_mode() -> "WriteEnvelopeMode":
    from backend.config.settings import get_settings

    return get_settings().power_automate_write_envelope_mode


def _prepare_execution(state: Any, authorization: Any, operation: str, destination: str, payload: Any):
    """POST-6A -- persist a durable operation identity BEFORE dispatch,
    and refuse a dispatch that is not permitted.

    Returns `(record, refusal)`. `refusal` is a ready-to-return error
    result when this operation must not be dispatched -- most importantly
    when a previous attempt reached `UNKNOWN_OUTCOME`, where a blind
    retry could double-send.
    """
    existing = parse_execution_record(state.get(EXECUTION_RECORD_STATE_KEY))
    operation_id = derive_operation_id(authorization.proposal.proposal_id, operation, destination, payload)

    # A record for a DIFFERENT operation is not this operation's history.
    if existing is not None and existing.operation_id != operation_id:
        existing = None

    if not retry_permitted(existing):
        if existing is not None and existing.status is ExecutionStatus.SUCCEEDED:
            message = "This action has already been executed."
        else:
            message = (
                "A previous attempt at this action may already have reached Microsoft Teams, and this "
                "connector cannot confirm whether it did. Please check Teams before trying again."
            )
        return None, {"error": SafeError(error_code="action_failure", user_message=message).to_dict()}

    record = ExecutionRecord(
        operation_id=operation_id,
        proposal_id=authorization.proposal.proposal_id,
        operation=operation,
        destination=destination,
        payload_hash=payload_hash(payload),
        status=ExecutionStatus.PREPARED,
        attempts=(existing.attempts if existing else 0) + 1,
        created_at=existing.created_at if existing else datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    state.update(build_execution_record_delta(record))
    return record, None


def _record_status(state: Any, record: Any, status: "ExecutionStatus", detail: str = "") -> None:
    if record is None:
        return
    state.update(
        build_execution_record_delta(
            record.model_copy(update={"status": status, "detail": detail, "updated_at": datetime.now(timezone.utc)})
        )
    )

def teams_create_chat(
    title: str,
    members: list[str],
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Execute an approved `teams.createChat` action. Only proceeds if a
    currently approved, unexpired, unconsumed proposal exists whose
    operation and exact (normalized) payload match `title`/`members`.

    Args:
      title: The exact chat title -- must match what was approved.
      members: The exact participant email addresses -- must match what
        was approved.
      tool_context: ADK-injected; required in practice (there is no
        session state to authorize against without it).

    Returns:
      On success: `{"status": "executed", "chatId", "title", "webUrl"}`
      (whichever fields Power Automate's response actually included --
      never fabricated). On denial or gateway failure: `{"error": ...}`
      (SafeError shape). Never calls Power Automate at all on denial.
    """
    try:
        payload = normalize_create_chat_payload(title, members)
    except SafeErrorException as exc:
        return {"error": exc.safe_error.to_dict()}

    state = tool_context.state if tool_context is not None else {}
    authorization = authorize_write(WriteOperation.TEAMS_CREATE_CHAT.value, payload, state)
    if not authorization.authorized:
        return _denial_result(authorization.reason)

    record, refusal = _prepare_execution(
        state, authorization, WriteOperation.TEAMS_CREATE_CHAT.value, payload["title"], payload
    )
    if refusal is not None:
        return refusal

    client = PowerAutomateClient()
    try:
        _record_status(state, record, ExecutionStatus.DISPATCHED)
        raw = client.create_chat(payload["title"], payload["members"])
    except GatewayAmbiguousOutcomeError as exc:
        # POST-6A -- the request LEFT this process and we never learned
        # what happened. It may have created the chat. Recorded as
        # UNKNOWN_OUTCOME, which blocks any further attempt: retrying
        # something that may already have succeeded is how one approved
        # action becomes two. The proposal is deliberately NOT consumed
        # (we cannot claim it executed) and NOT freed for retry either.
        _record_status(state, record, ExecutionStatus.UNKNOWN_OUTCOME, "gateway timeout or lost response")
        return {"error": exc.safe_error.to_dict()}
    except SafeErrorException as exc:
        # A DEFINITE gateway failure -- the request was refused or never
        # left. Safe to treat as not executed, so the proposal stays
        # approved and a legitimate retry remains possible.
        _record_status(state, record, ExecutionStatus.FAILED, "gateway refused the request")
        return {"error": exc.safe_error.to_dict()}

    # POST-6A -- HTTP 200 IS NOT SUCCESS. A flow that caught a Graph
    # failure returns 200 with `success: false` / an error payload; that
    # must never consume the proposal or be reported as executed.
    envelope = validate_write_envelope(WriteOperation.TEAMS_CREATE_CHAT.value, raw, mode=_envelope_mode())
    if envelope.outcome is WriteEnvelopeOutcome.UNCONFIRMED:
        # POST-6A -- DISPATCHED, OUTCOME UNKNOWN. The request reached the
        # flow and the flow answered without reporting a failure -- but
        # with no acknowledgement and no identifier, so we cannot say it
        # executed. Treating this as FAILED would be a lie in the
        # dangerous direction: the proposal would go back on the shelf and
        # a "retry" could double-send. UNKNOWN_OUTCOME records the truth,
        # blocks resend, and leaves the operation record intact for
        # reconciliation.
        _record_status(state, record, ExecutionStatus.UNKNOWN_OUTCOME, envelope.detail or "unconfirmed")
        return {
            "error": SafeError(
                error_code="action_failure",
                user_message=(
                    "Microsoft Teams did not confirm this action, so I cannot tell you whether it was "
                    "sent. Please check Teams before trying again."
                ),
                reason=envelope.outcome.value,
            ).to_dict()
        }
    if not envelope.executed:
        # A DEFINITE business failure -- the flow said it did not do it,
        # so the proposal stays approved and a genuine retry is safe.
        _record_status(state, record, ExecutionStatus.FAILED, envelope.outcome.value)
        return {
            "error": SafeError(
                error_code="action_failure",
                user_message="Microsoft Teams did not confirm this action, so it has not been recorded as sent.",
                reason=envelope.outcome.value,
            ).to_dict()
        }

    result = _parse_create_chat_result(raw)
    _record_status(state, record, ExecutionStatus.SUCCEEDED, "confirmed by the gateway")
    consume_proposal(authorization.proposal.proposal_id, state)
    return result


def teams_send_message(
    chat_id: str,
    message: str,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Execute an approved `teams.sendMessage` action. Only proceeds if a
    currently approved, unexpired, unconsumed proposal exists whose
    operation and exact payload match `chat_id`/`message`.

    Args:
      chat_id: The resolved Teams chat id -- must match what was approved.
      message: The exact message text -- must match what was approved,
        character for character.
      tool_context: ADK-injected; required in practice.

    Returns:
      On success: `{"status": "executed", "chatId"}`. On denial or
      gateway failure: `{"error": ...}` (SafeError shape). Never calls
      Power Automate at all on denial.
    """
    try:
        payload = normalize_send_message_payload(chat_id, message)
    except SafeErrorException as exc:
        return {"error": exc.safe_error.to_dict()}

    state = tool_context.state if tool_context is not None else {}
    authorization = authorize_write(WriteOperation.TEAMS_SEND_MESSAGE.value, payload, state)
    if not authorization.authorized:
        return _denial_result(authorization.reason)

    record, refusal = _prepare_execution(
        state, authorization, WriteOperation.TEAMS_SEND_MESSAGE.value, payload["chatId"], payload
    )
    if refusal is not None:
        return refusal

    client = PowerAutomateClient()
    try:
        _record_status(state, record, ExecutionStatus.DISPATCHED)
        raw = client.send_message(payload["chatId"], payload["message"])
    except GatewayAmbiguousOutcomeError as exc:
        # See `teams_create_chat` -- the message may already have been
        # delivered, so no retry is permitted from application code.
        _record_status(state, record, ExecutionStatus.UNKNOWN_OUTCOME, "gateway timeout or lost response")
        return {"error": exc.safe_error.to_dict()}
    except SafeErrorException as exc:
        _record_status(state, record, ExecutionStatus.FAILED, "gateway refused the request")
        return {"error": exc.safe_error.to_dict()}

    envelope = validate_write_envelope(WriteOperation.TEAMS_SEND_MESSAGE.value, raw, mode=_envelope_mode())
    if envelope.outcome is WriteEnvelopeOutcome.UNCONFIRMED:
        # POST-6A -- DISPATCHED, OUTCOME UNKNOWN. The request reached the
        # flow and the flow answered without reporting a failure -- but
        # with no acknowledgement and no identifier, so we cannot say it
        # executed. Treating this as FAILED would be a lie in the
        # dangerous direction: the proposal would go back on the shelf and
        # a "retry" could double-send. UNKNOWN_OUTCOME records the truth,
        # blocks resend, and leaves the operation record intact for
        # reconciliation.
        _record_status(state, record, ExecutionStatus.UNKNOWN_OUTCOME, envelope.detail or "unconfirmed")
        return {
            "error": SafeError(
                error_code="action_failure",
                user_message=(
                    "Microsoft Teams did not confirm this message, so I cannot tell you whether it was "
                    "sent. Please check Teams before trying again."
                ),
                reason=envelope.outcome.value,
            ).to_dict()
        }
    if not envelope.executed:
        # A DEFINITE business failure -- the flow said it did not do it,
        # so the proposal stays approved and a genuine retry is safe.
        _record_status(state, record, ExecutionStatus.FAILED, envelope.outcome.value)
        return {
            "error": SafeError(
                error_code="action_failure",
                user_message="Microsoft Teams did not confirm this message, so it has not been recorded as sent.",
                reason=envelope.outcome.value,
            ).to_dict()
        }

    _record_status(state, record, ExecutionStatus.SUCCEEDED, "confirmed by the gateway")
    consume_proposal(authorization.proposal.proposal_id, state)
    return {"status": "executed", "chatId": payload["chatId"], "messageId": envelope.identifier}


def _parse_create_chat_result(raw: Any) -> dict[str, Any]:
    """Best-effort extraction of the fields the instruction asks for
    (`chatId`/`title`/`webUrl`) from whatever shape Power Automate's
    `teams.createChat` response actually is. UNVERIFIED against a live
    gateway response as of this milestone (see the final report's "known
    limitations") -- accepts a single JSON object, or a one-item JSON
    array wrapping one (mirroring the tolerance `extract_items` already
    applies for the read operations), and never fabricates a field it did
    not actually receive.
    """
    entry = raw
    if isinstance(raw, list) and raw:
        entry = raw[0]
    if not isinstance(entry, dict):
        return {"status": "executed"}

    result: dict[str, Any] = {"status": "executed"}
    for source_key, out_key in (("id", "chatId"), ("chatId", "chatId"), ("topic", "title"), ("webUrl", "webUrl")):
        value = entry.get(source_key)
        if isinstance(value, str) and value:
            result[out_key] = value
    return result
