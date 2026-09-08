"""P4B -- Incident Manager latency/call-graph optimization.

Core architectural change (see read_continuation_execution.py's own
module/function docstrings for the full rationale): when a resolved
continuation has no `requested_time_range` (the common case), retrieval
now happens deterministically in the application layer, BEFORE incident_
manager's model ever runs -- collapsing the previous 3-pre-retrieval-call
+ 1-synthesis-call graph down to exactly ONE Incident Manager model call
(pure synthesis, evidence already provided). When `requested_time_range`
IS present, the model-driven path (get_current_time_context + the
resolved-chat retrieval tool) is preserved unchanged, since interpreting
an arbitrary natural-language time expression genuinely requires model
reasoning (never regex/keyword date parsing).

These tests exercise the REAL `execute_read_continuation`/`teams_get_
messages`/gateway-mock machinery end to end wherever practical (only the
innermost Gemini call is faked), never a re-derived approximation.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from google.adk.events import Event, EventActions
from google.genai import types

from backend.agents.team_manager import read_continuation_execution as execution_module
from backend.agents.team_manager.read_continuation_execution import (
    _SYNTHESIS_ONLY_INCIDENT_MANAGER,
    _build_prefetched_evidence,
    execute_read_continuation,
)
from backend.api.session_service import ApiSessionService
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.attachments.storage import ChatAttachmentStorage
from backend.selection.schemas import ResolvedReadContinuation
from backend.tests._fakes import FakeResponse, chat, message
from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY
from backend.tools.teams.schemas import TeamsMessage, TeamsMessageReference


def _attachment_service() -> AttachmentService:
    """POST-5.1 B6 -- every `_resolved_continuation()` in this file has an
    empty `attachment_ids` (unchanged by this pass), so `execute_read_
    continuation`'s new required params are never actually exercised here
    -- a fresh in-memory-SQLite service is enough to satisfy the
    signature."""
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


def _attachment_storage() -> ChatAttachmentStorage:
    return ChatAttachmentStorage(None)


class _FakeEvent:
    def __init__(self, content: Any) -> None:
        self.content = content


def _resolved_continuation(
    chat_id: str = "chat-b2-id", chat_topic: str = "Chat B2", requested_time_range: Any = None
) -> ResolvedReadContinuation:
    return ResolvedReadContinuation(
        selected_chat_id=chat_id, selected_chat_topic=chat_topic, requested_time_range=requested_time_range
    )


class _SynthesisOnlyFakeRunner:
    """Simulates the REAL synthesis-only model call: reads the pre-fetched
    evidence from the request payload it was actually given (proving the
    application, not the model, performed retrieval), and never calls any
    tool -- there is nothing registered on `_SYNTHESIS_ONLY_INCIDENT_
    MANAGER` to call.
    """

    def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
        assert agent is _SYNTHESIS_ONLY_INCIDENT_MANAGER  # proves the right agent variant was used
        self._app_name = app_name
        self._session_service = session_service

    async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
        request = json.loads(new_message.parts[0].text)
        assert "prefetched_evidence" in request
        assert "chat_id" not in request  # the model was never asked to supply a destination

        prefetched = request["prefetched_evidence"]
        evidence = [{"message_id": m["message_id"], "author": m["author"], "sent_at": m["sent_at"]} for m in prefetched]
        payload = {
            "outcome": "ok" if prefetched else "no_result",
            "chat_id": request.get("chat_topic"),  # deliberately NOT the real id -- proves override happens
            "chat_title": request.get("chat_topic"),
            "summary": "Synthesized directly from prefetched evidence." if prefetched else None,
            "evidence": evidence,
        }
        yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

    async def close(self) -> None:
        return None


def _gateway(monkeypatch: pytest.MonkeyPatch, messages_by_chat_id: dict[str, list], call_counts: dict[str, int]):
    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.getMessages":
            return FakeResponse(200, messages_by_chat_id.get(json.get("chatId"), []))
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)


# --- Section 21/30/31: resolved continuation reaches exactly one Incident
# --- Manager model call, retrieval happens before it ------------------------


@pytest.mark.asyncio
async def test_no_time_range_continuation_reaches_exactly_one_incident_manager_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_counts: dict[str, int] = {}
    _gateway(
        monkeypatch,
        {"chat-b2-id": [message("m1", "Alex", "We decided to proceed next sprint.", "2026-08-20T09:00:00Z")]},
        call_counts,
    )

    runner_construction_count = 0
    real_runner_cls = _SynthesisOnlyFakeRunner

    class _CountingRunner(real_runner_cls):
        def __init__(self, **kwargs: Any) -> None:
            nonlocal runner_construction_count
            runner_construction_count += 1
            super().__init__(**kwargs)

    monkeypatch.setattr(execution_module, "Runner", _CountingRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-one-call",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    assert result is not None
    assert result["outcome"] == "ok"
    assert result["chat_id"] == "chat-b2-id"  # authoritative override, never the model's own value
    assert result["chat_title"] == "Chat B2"
    assert call_counts.get("teams.listChats", 0) == 0
    assert call_counts.get("teams.getMessages", 0) == 1
    assert runner_construction_count == 1  # exactly one Incident Manager Runner/model call


@pytest.mark.asyncio
async def test_retrieval_happens_before_any_incident_manager_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Structural proof of section 9/31: `teams.getMessages` is called
    BEFORE the (fake, but real-shaped) model Runner is even constructed.
    """
    call_order: list[str] = []
    call_counts: dict[str, int] = {}

    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.getMessages":
            call_order.append("getMessages")
            call_counts["teams.getMessages"] = call_counts.get("teams.getMessages", 0) + 1
            return FakeResponse(200, [])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    class _OrderTrackingRunner(_SynthesisOnlyFakeRunner):
        def __init__(self, **kwargs: Any) -> None:
            call_order.append("runner_constructed")
            super().__init__(**kwargs)

    monkeypatch.setattr(execution_module, "Runner", _OrderTrackingRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-order",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    assert call_order == ["getMessages", "runner_constructed"]


@pytest.mark.asyncio
async def test_synthesis_only_agent_has_no_tools_for_the_model_to_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Structural proof (never merely prompt wording): the agent variant
    used for this path has an empty tool list -- there is no function-
    calling round trip the model COULD make even if it tried.
    """
    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.tools == []
    resolved_tools = await _SYNTHESIS_ONLY_INCIDENT_MANAGER.canonical_tools()
    assert resolved_tools == []


# --- Section 32: compact model-facing evidence view -------------------------


def test_compact_evidence_view_drops_transport_and_duplicate_fields() -> None:
    raw = TeamsMessage(
        id="m1",
        author="Alex",
        text="We decided to proceed next sprint.",
        sent_at="2026-08-20T09:00:00Z",
        raw_content="<p>We decided to proceed next sprint.&nbsp;</p>",
        content_type="html",
        message_references=[
            TeamsMessageReference(
                message_id="m0", preview="original message", sender_name="Priya", sender_id="internal-teams-id-123"
            )
        ],
    )

    projected = _build_prefetched_evidence([raw])
    assert len(projected) == 1
    view = projected[0].model_dump()

    # Allowlisted fields the model actually reasons from are present.
    assert view["message_id"] == "m1"
    assert view["author"] == "Alex"
    assert view["sent_at"] == "2026-08-20T09:00:00Z"
    assert view["text"] == "We decided to proceed next sprint."
    assert view["message_references"] == [{"message_id": "m0", "preview": "original message", "sender_name": "Priya"}]

    # Never present in the model-facing view.
    assert "raw_content" not in view
    assert "content_type" not in view
    assert "sender_id" not in view["message_references"][0]
    assert "<p>" not in json.dumps(view)  # no raw HTML leaks through anywhere


def test_compact_evidence_view_preserves_full_internal_record_separately() -> None:
    """The projection is additive/read-only -- the original `TeamsMessage`
    object (with `raw_content` etc.) is untouched, still available to
    whatever else needs the full record (evidence validation, source
    construction) -- never destructively replaced.
    """
    raw = TeamsMessage(
        id="m1", author="Alex", text="Hello", sent_at="2026-08-20T09:00:00Z", raw_content="<p>Hello</p>"
    )
    _build_prefetched_evidence([raw])
    assert raw.raw_content == "<p>Hello</p>"
    assert raw.content_type is None


# --- Section 34: large chat -- no message silently dropped ------------------


def test_large_chat_projection_keeps_every_retrieved_message() -> None:
    raw_messages = [
        TeamsMessage(
            id=f"m{i}",
            author=f"User{i % 5}",
            text=f"Message number {i}.",
            sent_at=f"2026-08-20T09:{i:02d}:00Z",
            raw_content=f"<p>Message number {i}.</p>",
        )
        for i in range(37)
    ]
    projected = _build_prefetched_evidence(raw_messages)
    assert len(projected) == 37
    assert [p.message_id for p in projected] == [f"m{i}" for i in range(37)]  # order preserved too


@pytest.mark.asyncio
async def test_large_chat_full_retrieval_reaches_synthesis_and_verifies(monkeypatch: pytest.MonkeyPatch) -> None:
    call_counts: dict[str, int] = {}
    many_messages = [message(f"m{i}", f"User{i % 5}", f"Message {i}.", f"2026-08-20T09:{i:02d}:00Z") for i in range(37)]
    _gateway(monkeypatch, {"chat-b2-id": many_messages}, call_counts)
    monkeypatch.setattr(execution_module, "Runner", _SynthesisOnlyFakeRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-large",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    assert result is not None
    assert result["outcome"] == "ok"
    assert len(result["evidence"]) == 37  # every valid retrieved message cited, none dropped by the projection


# --- Section 35: no_result / error via the deterministic path ---------------


@pytest.mark.asyncio
async def test_deterministic_path_no_usable_messages_yields_no_result(monkeypatch: pytest.MonkeyPatch) -> None:
    call_counts: dict[str, int] = {}
    _gateway(monkeypatch, {"chat-b2-id": []}, call_counts)
    monkeypatch.setattr(execution_module, "Runner", _SynthesisOnlyFakeRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-no-result",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    assert result is not None
    assert result["outcome"] == "no_result"
    assert not result.get("summary")
    assert call_counts.get("teams.getMessages", 0) == 1


@pytest.mark.asyncio
async def test_deterministic_path_gateway_error_yields_safe_error_with_zero_model_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 21's own further optimization: a pure retrieval failure now
    costs ZERO Incident Manager model calls, not one -- there is nothing
    useful for the model to do with a retrieval it never performed.
    """
    from backend.gateway import power_automate_client as pac_module
    from backend.gateway.safe_error import internal_error

    call_counts: dict[str, int] = {}

    def raising_post(url, json, timeout):
        call_counts[json.get("operation")] = call_counts.get(json.get("operation"), 0) + 1
        raise internal_error("The Teams connector is temporarily unavailable.")

    monkeypatch.setattr(pac_module.requests, "post", raising_post)

    runner_constructed = False

    class _NeverConstructedRunner:
        def __init__(self, **kwargs: Any) -> None:
            nonlocal runner_constructed
            runner_constructed = True

    monkeypatch.setattr(execution_module, "Runner", _NeverConstructedRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-gateway-fail",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    assert result is not None
    assert result["outcome"] == "error"
    assert result["chat_id"] == "chat-b2-id"  # destination still authoritative even on failure
    assert not result.get("summary")
    assert call_counts.get("teams.getMessages", 0) == 1
    assert runner_constructed is False  # zero Incident Manager model calls for a pure retrieval failure


@pytest.mark.asyncio
async def test_deterministic_path_gateway_failure_leaves_no_leaked_internal_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.gateway import power_automate_client as pac_module
    from backend.gateway.safe_error import internal_error

    def raising_post(url, json, timeout):
        raise internal_error("unavailable")

    monkeypatch.setattr(pac_module.requests, "post", raising_post)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-cleanup-check",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(),
    )

    internal_session_id = execution_module._internal_session_id(session_id, "run-cleanup-check")
    leaked = await service.adk_session_service.get_session(
        app_name=execution_module._INTERNAL_SPECIALIST_APP_NAME, user_id="api-user", session_id=internal_session_id
    )
    assert leaked is None


# --- Section 7/21: time-range-bearing continuations stay model-driven -------


@pytest.mark.asyncio
async def test_time_range_continuation_still_uses_the_model_driven_retrieval_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserves semantic time-range interpretation capability exactly:
    a continuation WITH `requested_time_range` still goes through
    `_CONTINUATION_INCIDENT_MANAGER` (`get_resolved_chat_messages`
    available, model reasons about `from_datetime`/`to_datetime`), never
    the synthesis-only shortcut.
    """
    from backend.agents.team_manager.read_continuation_execution import _CONTINUATION_INCIDENT_MANAGER

    call_counts: dict[str, int] = {}
    _gateway(monkeypatch, {"chat-b2-id": [message("m1", "Alex", "Hi.", "2026-08-20T09:00:00Z")]}, call_counts)

    used_agent = None

    class _RecordingRunner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            nonlocal used_agent
            used_agent = agent
            self._app_name = app_name
            self._session_service = session_service

        async def run_async(self, *, user_id, session_id, new_message, run_config=None):
            session = await self._session_service.get_session(
                app_name=self._app_name, user_id=user_id, session_id=session_id
            )
            resolved_chat_id = session.state.get("resolved_chat_id")
            ctx = type("Ctx", (), {"state": dict(session.state)})()
            retrieval = execution_module.get_resolved_chat_messages(tool_context=ctx)
            evidence = [{"message_id": m["id"], "author": m["author"], "sent_at": m["sent_at"]} for m in retrieval["messages"]]
            await self._session_service.append_event(
                session,
                Event(
                    author="incident_manager",
                    invocation_id="inv-1",
                    actions=EventActions(state_delta={KNOWN_MESSAGE_IDS_STATE_KEY: [e["message_id"] for e in evidence]}),
                ),
            )
            payload = {"outcome": "ok", "chat_id": resolved_chat_id, "chat_title": "Chat B2", "summary": "Hi.", "evidence": evidence}
            yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(execution_module, "Runner", _RecordingRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-time-range",
        parent_state=dict(session.state),
        attachment_service=_attachment_service(),
        attachment_storage=_attachment_storage(),
        continuation=_resolved_continuation(requested_time_range="the last 7 days"),
    )

    assert result is not None
    assert result["outcome"] == "ok"
    assert used_agent is _CONTINUATION_INCIDENT_MANAGER
    assert call_counts.get("teams.getMessages", 0) == 1
