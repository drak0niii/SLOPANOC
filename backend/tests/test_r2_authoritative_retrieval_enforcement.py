"""R2 -- correctness-regression pass: a `ResolvedReadContinuation` must
never produce a trusted "ok"/"no_result" result unless authoritative Teams
retrieval (`teams_get_messages`, targeting the resolved chat) genuinely
happened this run.

Root cause this pass found (see read_continuation_execution.py's own
`_authoritative_retrieval_verified` docstring): `strip_unverified_evidence`
(evidence.py) strips individual FAKE evidence entries, but never
downgrades `outcome` itself -- so a model that never called `teams_get_
messages` at all could still return `outcome="ok"` with a fabricated
`summary`, and it would survive evidence stripping (down to an empty,
unnoticed `evidence` list) all the way to a `TrustedSpecialistResult`.

Exercises the REAL `execute_read_continuation` machinery end to end (only
the innermost ADK `Runner` is faked, mirroring test_p0_p1_live_incident_
regression.py's own `_RealRetrievalFakeIncidentManagerRunner` pattern) --
these fakes simulate a MISBEHAVING model, never a correctness shortcut.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest
from google.adk.events import Event, EventActions
from google.genai import types

from backend.agents.team_manager import read_continuation_execution as execution_module
from backend.agents.team_manager.read_continuation_execution import execute_read_continuation
from backend.selection.schemas import ResolvedReadContinuation
from backend.tests._fakes import FakeResponse, chat, message
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


class _FakeEvent:
    def __init__(self, content: Any) -> None:
        self.content = content


def _hallucinated_ok_runner(summary: str):
    """Simulates a model that returns `outcome="ok"` WITHOUT ever calling
    `teams_get_messages` at all -- exactly the R2 root-cause scenario.
    """

    class _Runner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._app_name = app_name
            self._session_service = session_service

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            request = json.loads(new_message.parts[0].text)
            session = await self._session_service.get_session(
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )
            resolved_chat_id = session.state.get("resolved_chat_id")
            payload = {
                "outcome": "ok",
                "chat_id": resolved_chat_id,
                "chat_title": request.get("chat_topic"),
                "summary": summary,
                "evidence": [
                    {"message_id": "fabricated-1", "author": "Nobody", "sent_at": "2026-08-20T09:00:00Z"}
                ],
            }
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

        async def close(self) -> None:
            return None

    return _Runner


def _real_retrieval_runner(get_messages_by_chat_id: dict):
    """A CORRECTLY-behaving simulated model -- calls the real, enforced
    `teams_get_messages` for the authoritative `resolved_chat_id` and
    reports "ok" only from what it actually retrieved. Used as the
    positive-case control proving the enforcement does not reject
    legitimate results.
    """
    from backend.agents.incident_manager.evidence import validate_evidence

    class _Runner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            self._app_name = app_name
            self._session_service = session_service

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            request = json.loads(new_message.parts[0].text)
            session = await self._session_service.get_session(
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )
            resolved_chat_id = session.state.get("resolved_chat_id")
            ctx = _Ctx(dict(session.state))
            retrieval = execution_module.get_resolved_chat_messages(tool_context=ctx)
            assert "error" not in retrieval
            candidate_evidence = [
                {"message_id": m["id"], "author": m["author"], "sent_at": m["sent_at"]} for m in retrieval["messages"]
            ]
            known_ids = set(ctx.state.get(KNOWN_MESSAGE_IDS_STATE_KEY, []))
            validated = validate_evidence(candidate_evidence, known_ids)

            # A real ADK tool call's state write reaches the session via
            # the real Runner's own event/state-delta machinery -- this
            # fake bypasses the real Runner, so it must persist that ONE
            # side effect explicitly (mirrors test_p0_p1_live_incident_
            # regression.py's own `_RealRetrievalFakeIncidentManagerRunner`
            # fix for the identical reason).
            state_delta_event = Event(
                author="incident_manager",
                invocation_id="test-invocation",
                actions=EventActions(state_delta={KNOWN_MESSAGE_IDS_STATE_KEY: ctx.state.get(KNOWN_MESSAGE_IDS_STATE_KEY, [])}),
            )
            await self._session_service.append_event(session, state_delta_event)

            payload = {
                "outcome": "ok",
                "chat_id": resolved_chat_id,
                "chat_title": request.get("chat_topic"),
                "summary": "Real, grounded summary.",
                "evidence": validated,
            }
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

        async def close(self) -> None:
            return None

    return _Runner


def _resolved_continuation(
    chat_id: str = "chat-b2-id", chat_topic: str = "Chat B2", requested_time_range: Optional[str] = None
) -> ResolvedReadContinuation:
    return ResolvedReadContinuation(
        selected_chat_id=chat_id, selected_chat_topic=chat_topic, requested_time_range=requested_time_range
    )


# --- TEST 3 (R2): retrieval missing entirely -------------------------------


@pytest.mark.asyncio
async def test_hallucinated_ok_without_any_retrieval_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api.session_service import ApiSessionService

    monkeypatch.setattr(execution_module, "Runner", _hallucinated_ok_runner("Fabricated summary, never grounded."))

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-hallucinated",
        parent_state=dict(session.state),
        continuation=_resolved_continuation(),
    )

    assert result is not None  # a safe, explicit failure -- never None/silently dropped
    assert result["outcome"] == "error"
    assert result.get("summary") is None  # the fabricated summary never survives
    assert result.get("evidence") in (None, [])
    assert "Fabricated" not in json.dumps(result)


# TEST 4 (R2, original "retrieval targets the wrong destination") is now
# SUPERSEDED, not merely moved: the urgent destination-binding fix removed
# `chat_id` from the continuation-specific retrieval tool's schema
# entirely, so "the model supplies a wrong chat_id" is no longer a
# reachable code path to test here at all -- see test_r2_urgent_
# destination_binding_fix.py's own TEST 3 ("model cannot override
# destination"), which proves the STRONGER property directly: the schema
# itself has no such parameter, verified against the real ADK function
# declaration.


# --- Positive control: legitimate retrieval is still accepted --------------


@pytest.mark.asyncio
async def test_legitimate_retrieval_for_the_authoritative_chat_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api.session_service import ApiSessionService
    from backend.gateway import power_automate_client as pac_module

    call_counts: dict[str, int] = {}

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.getMessages":
            return FakeResponse(
                200, [message("m1", "Alex", "We decided to proceed next sprint.", "2026-08-20T09:00:00Z")]
            )
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    monkeypatch.setattr(execution_module, "Runner", _real_retrieval_runner({}))

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-legit",
        parent_state=dict(session.state),
        # P4B: `_real_retrieval_runner` exercises `get_resolved_chat_
        # messages` directly (the model-driven retrieval path) -- a
        # non-empty `requested_time_range` keeps this continuation on
        # that path rather than the newer no-time-range deterministic one
        # (covered separately in test_p4b_incident_manager_call_graph.py).
        continuation=_resolved_continuation(chat_id="chat-b2-id", requested_time_range="the last 7 days"),
    )

    assert result is not None
    assert result["outcome"] == "ok"
    assert result["summary"] == "Real, grounded summary."
    assert len(result["evidence"]) == 1
    assert result["evidence"][0]["message_id"] == "m1"
    assert call_counts.get("teams.getMessages", 0) == 1
    assert call_counts.get("teams.listChats", 0) == 0


# --- No stale state leaks into a later run ----------------------------------


@pytest.mark.asyncio
async def test_rejected_result_leaves_no_leaked_internal_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api.session_service import ApiSessionService

    monkeypatch.setattr(execution_module, "Runner", _hallucinated_ok_runner("Fabricated."))

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-cleanup-check",
        parent_state=dict(session.state),
        continuation=_resolved_continuation(),
    )

    internal_session_id = execution_module._internal_session_id(session_id, "run-cleanup-check")
    leaked = await service.adk_session_service.get_session(
        app_name=execution_module._INTERNAL_SPECIALIST_APP_NAME, user_id="api-user", session_id=internal_session_id
    )
    assert leaked is None
