"""CONTROL-PLANE-SEQ-04 -- Authoritative Current-Turn Request Context.

THE GAP THIS CLOSES: CONTROL-PLANE-SEQ-03 computed `effective_governed_
request` (`resolve_effective_governed_contract`) as part of its mandatory
preflight, but never made it visible to the downstream operational Team
Manager turn -- that turn's own model reasoning had no deterministic
summary of what governance already established, and had to re-derive its
own understanding of intent/subject/target parameters from raw
conversation history alone, same as before this milestone's own preflight
work ever ran. Section 17's own explicit "fix this" requirement.

WHAT THIS IS: the SMALLEST deterministic renderer necessary -- a plain
Python string built directly from an already-validated `RequestContract`
and its already-derived `WorkEnvelope`, no new schema, no new persisted
concept. `chat_service.py` calls `render_authoritative_current_turn_
request_context` once, per normal turn, immediately after preflight/
Work-Envelope derivation, and writes the result into session state under
`AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY` via the SAME `persist_state_
delta` mechanism every other end-of-preflight write already uses.

NEVER PERSISTED (section 17's own explicit requirement): the state key is
deliberately `temp:`-prefixed -- ADK's own documented convention (already
relied on elsewhere in this codebase, e.g. `read_continuation_enforcement
.py`'s own `temp:resolved_chat_id`) for a value that IS applied to the
live, in-memory session state for the REST of this turn (so `team_manager
_instruction_provider`'s own `ctx.state.get(...)` sees it when ADK invokes
the instruction provider for this turn's Runner call) but is unconditionally
stripped before anything is durably persisted -- verified against this
exact call path: `ApiSessionService.persist_state_delta` uses ADK's own
`append_event`/state-delta application (`_apply_temp_state`), the "apply,
then trim" path that correctly honors `temp:` keys, NOT `create_session`'s
own separate, documented-broken-for-`temp:` code path (see read_
continuation_enforcement.py's own docstring for that distinct, unrelated
limitation -- it concerns forwarding into a NESTED child session via
`create_session`, never this module's own top-level-session use).

DOWNSTREAM MODEL NEVER OVERWRITES IT (section 17's own explicit
requirement): this is a plain string appended to the OUTGOING prompt by
`case_context.py`'s own instruction provider -- never a tool, never a
session-state field the model itself can write to (no `record_*` tool
exposes it), and never re-derived or mutated once rendered.
"""
from __future__ import annotations

from typing import Optional

from backend.agents.team_manager.request_contract import RequestContract, authoritative_missing_context_names
from backend.agents.team_manager.request_execution_policy import WorkEnvelope

AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY = "temp:authoritative_current_turn_request_context"
"""`temp:`-prefixed (see this module's own docstring for why that prefix
is safe and correct here) -- visible to `team_manager_instruction_
provider`'s own `ctx.state.get(...)` for the remainder of THIS turn only,
never durably persisted, never visible to a later turn."""


def render_authoritative_current_turn_request_context(
    contract: Optional[RequestContract],
    work_envelope: WorkEnvelope,
    governed_specialist_result: Optional[str] = None,
) -> str:
    """Deterministic, Python-authored rendering only -- no model call, no
    natural-language generation. Returns an empty string (nothing to
    render/inject) when `contract` is `None` -- the caller must never write
    an empty-but-present block into session state (see call site).

    Includes ONLY (section 17's own explicit, closed list): `request_
    class`, `intent`, `requested_output`, `subject`, verified/canonical
    `provided_context`, authoritative `missing_context`, `continuation`,
    `run_id`, and the `WorkEnvelope` permissions relevant to a downstream
    operational reasoning turn. Never raw user text, never Knowledge
    content, never anything beyond what the CURRENT turn's own already-
    validated contract and already-derived envelope establish.

    `governed_specialist_result` -- CONTROL-PLANE-SEQ-04A section 9's own
    "supply the response-generating layer with... the governed specialist
    result/evidence already produced" requirement: the CURRENT turn's own
    real, already-verified governed answer text (`enforce_governed_
    knowledge_at_completion`'s own validated `summary`/`detail`), when the
    deterministic authorized-read stage genuinely produced one this turn --
    NEVER a deterministic fallback/failure/clarification text (the caller
    is responsible for that distinction; see chat_service.py's own call
    site). Appended as a clearly-labeled, separate block only when present
    -- omitted (not even an empty heading) otherwise, so an ordinary
    non-governed turn's own rendered context is byte-for-byte unchanged
    from before this addition.
    """
    if contract is None:
        return ""
    verified_context = ", ".join(f"{param.name}={param.value}" for param in contract.provided_context) or "(none)"
    authoritative_missing = ", ".join(authoritative_missing_context_names(contract.missing_context)) or "(none)"
    lines = [
        "AUTHORITATIVE CURRENT-TURN REQUEST CONTEXT (deterministic, already verified -- "
        "do not restate or contradict without new user-supplied information):",
        f"- request_class: {work_envelope.request_class or 'unresolved'}",
        f"- intent: {contract.intent}",
        f"- requested_output: {contract.requested_output}",
        f"- subject: {contract.subject or '(none resolved)'}",
        f"- continuation: {contract.continuation}",
        f"- run_id: {contract.run_id or '(none)'}",
        f"- verified provided context: {verified_context}",
        f"- authoritative missing context: {authoritative_missing}",
        (
            "- permitted this turn: "
            f"governed_knowledge={work_envelope.may_use_governed_knowledge}, "
            f"incident_manager={work_envelope.may_route_incident_manager}, "
            f"troubleshooting_manager={work_envelope.may_route_troubleshooting_manager}, "
            f"command_candidate={work_envelope.may_generate_command_candidate}, "
            f"action_candidate={work_envelope.may_prepare_action_candidate}"
        ),
    ]
    if governed_specialist_result:
        lines.append(
            "GOVERNED SPECIALIST RESULT (already retrieved and grounded this turn -- reuse it; "
            "do not claim you cannot access governed knowledge or ask the user to wait for a lookup):"
        )
        lines.append(governed_specialist_result)
    return "\n".join(lines)
