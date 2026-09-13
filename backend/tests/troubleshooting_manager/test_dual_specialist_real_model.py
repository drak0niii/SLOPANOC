"""Phase 6A.10 §83-86/§131: the mandatory controlled REAL-MODEL
orchestration validation -- drives the REAL `team_manager` agent (the
exact object the normal SLOPANOC user-facing backend path uses, via a
real ADK `Runner`) through the actual configured Vertex/Gemini stack for
incident-only, troubleshooting-only, and dual-specialist requests, and
inspects the REAL event stream for which specialist(s) were actually
invoked -- never asserting routing correctness from final prose alone.

SKIPS (never fails, never fabricates a result) if a real Vertex AI model
call cannot be made in this environment -- mirrors this codebase's own
established "skip if real X unavailable" discipline (6A.5's embedding
test, 6A.9's own `test_real_model_validation.py`).

SAFETY: Power Automate is NOT configured in this environment (confirmed
by audit -- `resolve_power_automate_gateway_url()` raises
`ConfigurationError`, safely converted to a `SafeErrorException` by
`power_automate_client.py`) -- so even if the model chooses to call a
Teams tool, no real external network/Teams call is ever made; the tool
call safely fails closed. `get_experience_memory_service` is monkeypatched
to an isolated in-memory `ExperienceMemoryService()` for every test in
this file so no local SQLite file (`./slopanoc_sessions.db`) is ever
touched by Experience Memory's own default resolution -- `ApiSessionService`
already defaults to an in-memory ADK session service on its own.
"""
from __future__ import annotations

import asyncio

import pytest

import backend.agents.troubleshooting_manager.experience_support as experience_support_module
from backend.agents.team_manager.agent import team_manager
from backend.agents.troubleshooting_manager.experience_support import query_experience_support
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService
from backend.api.session_service import APP_NAME
from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types


def _real_model_reachable() -> bool:
    import os

    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
    try:
        async def _probe():
            session_service = InMemorySessionService()
            runner = Runner(app_name="probe", agent=team_manager, session_service=session_service, memory_service=InMemoryMemoryService())
            await session_service.create_session(app_name="probe", user_id="probe", session_id="probe")
            content = types.Content(role="user", parts=[types.Part.from_text(text="hello")])
            async for _ in runner.run_async(user_id="probe", session_id="probe", new_message=content):
                pass
            await runner.close()

        asyncio.run(_probe())
        return True
    except Exception:
        return False


_REACHABLE = _real_model_reachable()
pytestmark = pytest.mark.skipif(not _REACHABLE, reason="real Vertex AI model not reachable in this environment (no valid ADC, or model unavailable)")

_USER_ID = "6a10-real-model-user"


async def _run_turn(user_text: str, session_id: str, *, experience_service: ExperienceMemoryService) -> tuple[list[str], list]:
    """Returns `(function_call_names, events)` for one real team_manager
    turn -- the SAME `team_manager` object the normal API path uses."""

    original = experience_support_module.get_experience_memory_service
    experience_support_module.get_experience_memory_service = lambda: experience_service
    try:
        session_service = InMemorySessionService()
        runner = Runner(app_name=APP_NAME, agent=team_manager, session_service=session_service, memory_service=InMemoryMemoryService())
        try:
            await session_service.create_session(app_name=APP_NAME, user_id=_USER_ID, session_id=session_id)
            content = types.Content(role="user", parts=[types.Part.from_text(text=user_text)])
            events = []
            function_call_names: list[str] = []
            async for event in runner.run_async(user_id=_USER_ID, session_id=session_id, new_message=content):
                events.append(event)
                for call in event.get_function_calls():
                    function_call_names.append(call.name)
        finally:
            await runner.close()
    finally:
        experience_support_module.get_experience_memory_service = original

    return function_call_names, events


@pytest.mark.asyncio
async def test_real_troubleshooting_only_request_invokes_only_troubleshooting_manager() -> None:
    service = ExperienceMemoryService()
    calls, _events = await _run_turn(
        "What should I check next for a VSWR over threshold alarm? Do not summarize what happened -- I only want to know what to investigate next.",
        session_id="6a10-ts-only",
        experience_service=service,
    )
    assert "troubleshooting_manager" in calls, f"expected troubleshooting_manager to be invoked, got calls={calls}"
    assert "incident_manager" not in calls, f"incident_manager must not fire for a pure troubleshooting request, got calls={calls}"
    assert calls.count("troubleshooting_manager") == 1, f"troubleshooting_manager must be invoked at most once, got calls={calls}"


@pytest.mark.asyncio
async def test_real_incident_only_request_does_not_invoke_troubleshooting_manager() -> None:
    """Even if `incident_manager` cannot find anything useful (no Teams
    chat named, no case linked, no real gateway configured), the ROUTING
    decision itself must not spuriously also call `troubleshooting_
    manager` for a request that only asks what happened."""
    service = ExperienceMemoryService()
    calls, _events = await _run_turn(
        "What happened with this incident? Just give me a summary of what occurred -- I am not asking what to do next.",
        session_id="6a10-im-only",
        experience_service=service,
    )
    assert "troubleshooting_manager" not in calls, f"troubleshooting_manager must not fire for a pure incident-summary request, got calls={calls}"


@pytest.mark.asyncio
async def test_real_dual_specialist_request_invokes_both_at_most_once_each() -> None:
    service = ExperienceMemoryService()
    calls, _events = await _run_turn(
        "First, summarize what happened in the Teams chat named 'Production Bridge'. Second, and separately, tell me what I should investigate next for a VSWR over threshold alarm. Please answer both parts.",
        session_id="6a10-dual",
        experience_service=service,
    )
    assert "incident_manager" in calls, f"expected incident_manager to be invoked for the dual request, got calls={calls}"
    assert "troubleshooting_manager" in calls, f"expected troubleshooting_manager to be invoked for the dual request, got calls={calls}"
    assert calls.count("incident_manager") <= 1, f"incident_manager must be invoked at most once, got calls={calls}"
    assert calls.count("troubleshooting_manager") == 1, f"troubleshooting_manager must be invoked at most once (bounded, structurally enforced), got calls={calls}"


@pytest.mark.asyncio
async def test_real_troubleshooting_only_request_never_executes_a_tool() -> None:
    """§63/§130: even though the response will likely be needs_information
    (no live TELCO/Evidence source wired in 6A.10), the real model must
    never attempt to call a Teams/network/write tool to "help" satisfy
    the diagnostic requirement."""
    service = ExperienceMemoryService()
    calls, _events = await _run_turn(
        "What should I check next for a VSWR over threshold alarm?",
        session_id="6a10-no-execution",
        experience_service=service,
    )
    forbidden = {"teams_create_chat", "teams_send_message", "teams_propose_create_chat", "teams_propose_send_message"}
    assert not (set(calls) & forbidden), f"no write/execution tool may fire for an advisory troubleshooting request, got calls={calls}"


@pytest.mark.asyncio
async def test_real_troubleshooting_request_leaves_experience_read_only() -> None:
    """§127: after a normal user-routed troubleshooting request, the
    Experience row count must not increase -- no `record_experience` is
    ever called anywhere in this path."""
    service = ExperienceMemoryService()
    _before, before_meta = await query_experience_support(owner_id=_USER_ID, skill_id=None, skill_version=None, case_id=None, service=service)

    await _run_turn("What should I check next for a VSWR over threshold alarm?", session_id="6a10-ro-check", experience_service=service)

    _after, after_meta = await query_experience_support(owner_id=_USER_ID, skill_id=None, skill_version=None, case_id=None, service=service)
    assert after_meta.result_count == before_meta.result_count == 0
