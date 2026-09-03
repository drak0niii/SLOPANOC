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

from typing import Any, Optional

from google.adk.tools import ToolContext

from backend.approval.policy_gate import authorize_write
from backend.approval.schemas import ApprovalDenialReason, WriteOperation
from backend.approval.service import consume_proposal
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

    client = PowerAutomateClient()
    try:
        raw = client.create_chat(payload["title"], payload["members"])
    except SafeErrorException as exc:
        # A definite gateway failure -- the proposal stays approved
        # (unconsumed) so a legitimate retry of the SAME approved action
        # remains possible; see this tool module's docstring / the final
        # report's "failure behavior" section for the current, un-extended
        # idempotency posture on ambiguous/timeout outcomes.
        return {"error": exc.safe_error.to_dict()}

    result = _parse_create_chat_result(raw)

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

    client = PowerAutomateClient()
    try:
        client.send_message(payload["chatId"], payload["message"])
    except SafeErrorException as exc:
        return {"error": exc.safe_error.to_dict()}

    consume_proposal(authorization.proposal.proposal_id, state)
    return {"status": "executed", "chatId": payload["chatId"]}


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
