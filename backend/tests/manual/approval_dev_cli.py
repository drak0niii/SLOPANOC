"""Manual, local-only trusted approval boundary for live CLI verification
of the Teams write-approval flow (milestone 3B).

NOT collected by pytest -- same convention as live_gateway_check.py in
this same package: named outside `test_*.py`, in a package pytest is
never pointed at, so the automated suite can never accidentally run a
live conversation or call the real Power Automate gateway.

WHAT THIS IS: the smallest usable stand-in for the future SLOPANOC
approval card (instruction section 9/16). It runs team_manager through a
REAL ADK `Runner` + `InMemorySessionService`, talking to the REAL
configured Gemini model (and, if a gateway URL is configured, the REAL
Power Automate gateway) -- but the actual APPROVE/REJECT step is done by
this script pausing and asking the developer running it directly, then
calling `backend.approval.service.approve_proposal`/`reject_proposal`
against the session's own real state -- never by asking Gemini, and never
by parsing the developer's `y`/`n` keystroke as if it were natural
language. It is a fixed two-choice menu mapped 1:1 to a function call,
structurally the same thing a future UI "Approve"/"Reject" button does:
neither this menu prompt nor a future button's `onClick` handler is
"conversational confirmation parsing" -- that phrase (instruction section
9's prohibition) refers specifically to inferring approval from Gemini's
own conversation with the user (e.g. treating the user's chat message
"yes" as authorization), which never happens here or anywhere in this
codebase.

HOW THE FUTURE UI WILL DO THE SAME THING: a SLOPANOC approval card's
"Approve"/"Reject" button will call a trusted backend endpoint (not built
in this milestone) that does exactly what this script's `_handle_pending_proposal`
does -- look up the user's real ADK session, then call
`approve_proposal(proposal_id, session.state)` / `reject_proposal(...)`
directly, then persist the resulting state back through whatever
`SessionService` the deployed backend uses. Nothing about that path
differs conceptually from this CLI script; only the "ask the developer"
step is replaced by "the button was clicked."

RUN IT:

    PYTHONPATH=. SLOPANOC_POWER_AUTOMATE_GATEWAY_URL="<real gateway url>" \\
        python -m backend.tests.manual.approval_dev_cli

Without a configured Gemini API key/gateway URL, team_manager's own
`Runner.run_async` calls will fail -- that is expected; this script is
for live, manual verification only, exactly like live_gateway_check.py.

Type a message to team_manager at each prompt. When a write action has
just been proposed, this script will pause and ask you, directly (not
through Gemini), whether to approve or reject it before your next message
reaches team_manager again.

OUTPUT: each tool call/result team_manager's turn made along the way is
printed as one compact `[tool]` line (useful for a developer following
what actually happened -- e.g. seeing that `incident_manager` was called,
or that `teams_propose_create_chat` ran) before team_manager's own
final text reply. The one thing suppressed here, and only here, is the
`google_genai.types` SDK's own `logger.warning(...)` about a response
having non-text parts alongside text (a routine, expected shape for any
turn that also calls a tool) -- that line is pure log noise for this
harness's purpose and carries no information the `[tool]` lines above
don't already show more usefully. Nothing about agent behavior, tool
execution, or error surfacing changes: a genuine tool-level `error` still
prints in full as part of the `[tool]` line for that call.
"""
from __future__ import annotations

import asyncio
import logging

from google.genai import types

from backend.approval.schemas import ProposalStatus
from backend.approval.service import approve_proposal, load_active_proposal, reject_proposal
from backend.tools.teams.expiry_presentation import (
    compute_expires_in_minutes,
    compute_expires_in_seconds,
)

# Dev-CLI-only presentation cleanup (see module docstring's "OUTPUT"
# section) -- does not touch any agent/runtime code or behavior.
logging.getLogger("google_genai.types").setLevel(logging.ERROR)


async def _run() -> None:
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService

    from backend.agents.team_manager.agent import team_manager

    session_service = InMemorySessionService()
    session = await session_service.create_session(
        app_name="slopanoc-approval-dev-cli", user_id="dev", state={}
    )
    runner = Runner(
        app_name="slopanoc-approval-dev-cli",
        agent=team_manager,
        session_service=session_service,
    )

    print("SLOPANOC approval dev CLI. Type a message, or 'quit' to exit.")
    while True:
        user_text = input("\nyou> ").strip()
        if user_text.lower() in ("quit", "exit"):
            return

        content = types.Content(role="user", parts=[types.Part.from_text(text=user_text)])
        async for event in runner.run_async(
            user_id=session.user_id, session_id=session.id, new_message=content
        ):
            _print_tool_activity(event)
            if event.content and event.content.parts:
                text = "\n".join(p.text for p in event.content.parts if p.text)
                if text:
                    print(f"team_manager> {text}")

        # Re-fetch: `create_session`'s returned object does not track
        # deltas the Runner appended to the persisted session.
        session = await session_service.get_session(
            app_name="slopanoc-approval-dev-cli", user_id="dev", session_id=session.id
        )
        _maybe_handle_pending_proposal(session)


def _print_tool_activity(event) -> None:
    """Print one compact `[tool]` line per function call/response in this
    event, if any -- a readable stand-in for the parts the SDK's own
    non-text-parts warning was otherwise complaining about (see module
    docstring). Purely cosmetic/dev-side: does not alter `event`, and
    never swallows a tool's own `error` payload.
    """
    for call in event.get_function_calls():
        print(f"[tool] {call.name}({call.args})")
    for response in event.get_function_responses():
        print(f"[tool] {response.name} -> {response.response}")


def _maybe_handle_pending_proposal(session) -> None:
    """This is the trusted approval boundary: a direct call to
    `approve_proposal`/`reject_proposal`, made by this script after asking
    the developer -- never by Gemini, never by parsing `user_text` above.
    """
    proposal = load_active_proposal(session.state)
    if proposal is None or proposal.status != ProposalStatus.PENDING:
        return

    print(f"\n--- Pending Teams write action: {proposal.operation.value} ---")
    if proposal.summary:
        print(proposal.summary)
    print(f"proposal_id:   {proposal.proposal_id}")
    print(f"expires_at:    {proposal.expires_at.isoformat()}  (raw, dev-only)")
    expires_in_seconds = compute_expires_in_seconds(proposal.expires_at)
    print(
        f"expires_in:    ~{compute_expires_in_minutes(expires_in_seconds)} min "
        f"({expires_in_seconds}s) -- dev-only; independently computed here from "
        "the proposal's real expires_at (Phase 4G: the model itself no longer "
        "receives this value at all -- see propose_write.py's _proposal_info)"
    )

    choice = input("Approve this action? [y/N/skip]: ").strip().lower()
    if choice == "y":
        result = approve_proposal(proposal.proposal_id, session.state)
        print(f"approved: {result.success}" if result.success else f"approve failed: {result.reason}")
    elif choice == "skip":
        print("Leaving the proposal pending.")
    else:
        result = reject_proposal(proposal.proposal_id, session.state)
        print(f"rejected: {result.success}" if result.success else f"reject failed: {result.reason}")


def main() -> int:
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
