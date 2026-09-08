"""POST-5.1 B6 -- selection-continuation image preservation (instruction
sections 27-33, 60-63).

An image-bearing request that resolves to `selection_needed` must not
silently lose its image evidence once the user picks a candidate from the
`SelectionCard` on a LATER turn. Covers, end to end, using the REAL
deterministic functions at every stage (never mocked away, mirroring
test_ambiguous_selection_read_resume_regression.py's own established
discipline):

  1. `teams_list_chats`' ambiguous branch captures this turn's own trusted
     image attachment ids (via `multimodal_turn_context`) into the new
     `PendingSelection.pending_read_intent.attachment_ids` -- server-
     captured, never model/frontend-supplied.
  2. The safe, frontend-facing `PendingSelectionDTO` never exposes them.
  3. `selection_service.choose()` carries them forward, verbatim, into the
     `ResolvedReadContinuation` it stores.
  4. `read_continuation_execution.execute_read_continuation` re-resolves
     them against the REAL attachment repository (LINKED-only, ownership/
     session-scoped) and appends them to the nested Content.
  5. Every corrupted/foreign/wrong-status/unknown attachment reference
     fails the WHOLE continuation closed -- never a silent fallback to
     Teams-only reasoning.
  6. A rewind that discards the branch which created the PendingSelection
     also reverses the session state that held it -- no ghost images.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from backend.api import selection_service
from backend.api.chat_service import _active_events, _resolve_invocation_id_for_active_user_turn, ChatService
from backend.api.multimodal_turn_context import discard_run_images, register_run_images
from backend.api.pending_selection import map_pending_selection
from backend.api.session_service import ApiSessionService
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.attachments.models import ChatAttachmentRecord, ChatAttachmentStatus
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.attachments.storage import ChatAttachmentStorage
from backend.selection.schemas import PendingReadIntent, ReadOperation
from backend.selection.service import load_active_selection
from backend.tests._api_fakes import append_user_turn
from backend.tests._fakes import FakeResponse, chat
from backend.tools.teams.list_chats import teams_list_chats


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


def _attachment_storage() -> ChatAttachmentStorage:
    return ChatAttachmentStorage(None)


async def _linked_record(
    service: AttachmentService,
    *,
    attachment_id: str,
    owner_user_id: str,
    session_id: str,
    message_id: str = "some-earlier-turn",
) -> ChatAttachmentRecord:
    """Simulates a real, already-B5-sent-and-linked attachment -- created
    READY (the only status `AttachmentService.create` ever produces), then
    transitioned LINKED, exactly like a genuine earlier turn's own send
    would have left it.
    """
    record = await service.create(
        owner_user_id=owner_user_id,
        session_id=session_id,
        original_filename="screenshot.png",
        mime_type="image/png",
        size_bytes=1024,
        sha256="a" * 64,
        attachment_id=attachment_id,
    )
    return await service.link_to_message(attachment_id, owner_user_id, session_id, message_id)


# --- 1/2/3: capture -> DTO safety -> choose() propagation -------------------


@pytest.mark.asyncio
async def test_ambiguous_read_with_image_captures_attachment_ids_onto_the_pending_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.listChats":
            return FakeResponse(
                200, [chat("chat-a", "Ops Bridge Daily"), chat("chat-b", "Ops Bridge Weekly")]
            )
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)

    token = bind_run_id("run-image-ambiguous")
    try:
        register_run_images("run-image-ambiguous", ["att-1", "att-2"])
        result = teams_list_chats(topic="Ops Bridge", pending_operation="summarize", tool_context=ctx)
    finally:
        discard_run_images("run-image-ambiguous")
        reset_run_id(token)

    assert result["match"] == "not_found"
    assert result["selection_pending"] is True

    await service.persist_state_delta(session, dict(ctx.state))
    refreshed = await service.get_session(session_id)
    pending = load_active_selection(refreshed.state)
    assert pending is not None
    assert pending.pending_read_intent.attachment_ids == ["att-1", "att-2"]


@pytest.mark.asyncio
async def test_ambiguous_read_without_a_bound_run_id_captures_no_attachment_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A text-only (or otherwise unbound) request must never spuriously
    pick up a stale/unrelated run's images -- `current_run_image_
    attachment_ids()` returns `()` outside a bound run_id.
    """
    from backend.gateway import power_automate_client as pac_module

    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.listChats":
            return FakeResponse(200, [chat("chat-a", "Ops Bridge Daily"), chat("chat-b", "Ops Bridge Weekly")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)

    result = teams_list_chats(topic="Ops Bridge", pending_operation="summarize", tool_context=ctx)
    assert result["selection_pending"] is True

    pending = load_active_selection(ctx.state)
    assert pending.pending_read_intent.attachment_ids == []


def test_pending_selection_dto_never_exposes_attachment_ids() -> None:
    """Instruction section 29/61 -- frontend selection security: the safe
    DTO built for the SelectionCard must never carry `attachment_ids`,
    `option_targets`, or any other internal field, regardless of what the
    underlying `PendingSelection` holds.
    """
    from backend.selection.schemas import PendingSelection, SelectionKind, SelectionOption, SelectionStatus

    selection = PendingSelection(
        selection_id="sel-1",
        kind=SelectionKind.TEAMS_CHAT,
        status=SelectionStatus.PENDING,
        requested_value="Ops Bridge",
        options=[SelectionOption(option_id="opt-1", label="Ops Bridge Daily")],
        created_at=datetime.now(timezone.utc),
        option_targets={"opt-1": {"chat_id": "chat-a", "topic": "Ops Bridge Daily"}},
        pending_read_intent=PendingReadIntent(attachment_ids=["att-1", "att-2"]),
    )
    from backend.selection.service import PENDING_SELECTION_STATE_KEY

    state = {PENDING_SELECTION_STATE_KEY: selection.model_dump(mode="json")}
    dto = map_pending_selection(state)
    assert dto is not None

    dumped = dto.model_dump()
    assert "attachment_ids" not in dumped
    assert "pending_read_intent" not in dumped
    assert "option_targets" not in dumped
    assert "att-1" not in json.dumps(dumped)
    assert "att-2" not in json.dumps(dumped)


@pytest.mark.asyncio
async def test_choose_copies_attachment_ids_verbatim_into_resolved_continuation() -> None:
    from backend.selection.schemas import PendingSelection, SelectionKind, SelectionOption, SelectionStatus
    from backend.selection.service import PENDING_SELECTION_STATE_KEY, pop_read_continuation

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    selection = PendingSelection(
        selection_id="sel-2",
        kind=SelectionKind.TEAMS_CHAT,
        status=SelectionStatus.PENDING,
        requested_value="Ops Bridge",
        options=[SelectionOption(option_id="opt-1", label="Ops Bridge Daily")],
        created_at=datetime.now(timezone.utc),
        option_targets={"opt-1": {"chat_id": "chat-a", "topic": "Ops Bridge Daily"}},
        pending_read_intent=PendingReadIntent(
            operation=ReadOperation.SUMMARIZE, attachment_ids=["att-1", "att-2"]
        ),
    )
    await service.persist_state_delta(session, {PENDING_SELECTION_STATE_KEY: selection.model_dump(mode="json")})

    await selection_service.choose(service, session_id, "sel-2", "opt-1")

    refreshed = await service.get_session(session_id)
    continuation = pop_read_continuation(refreshed.state)
    assert continuation is not None
    assert continuation.attachment_ids == ["att-1", "att-2"]
    assert continuation.selected_chat_id == "chat-a"


# --- 4/5: real re-resolution at continuation-execution time ------------------


@pytest.mark.asyncio
async def test_execute_read_continuation_appends_the_resolved_linked_image(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.agents.team_manager import read_continuation_execution as execution_module
    from backend.agents.team_manager.read_continuation_execution import (
        _SYNTHESIS_ONLY_INCIDENT_MANAGER,
        execute_read_continuation,
    )
    from backend.gateway import power_automate_client as pac_module
    from backend.selection.schemas import ResolvedReadContinuation
    from backend.tests._fakes import message

    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.getMessages":
            return FakeResponse(200, [message("m1", "Alex", "status is green", "2026-08-20T09:00:00Z")])
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    session_service = ApiSessionService()
    session_id = await session_service.create_session()

    attachment_service = _attachment_service()
    await _linked_record(
        attachment_service, attachment_id="att-linked-1", owner_user_id="api-user", session_id=session_id
    )
    storage = ChatAttachmentStorage("test-bucket")

    captured_content: dict[str, Any] = {}

    class _CapturingSynthesisRunner:
        def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
            assert agent is _SYNTHESIS_ONLY_INCIDENT_MANAGER

        async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
            captured_content["content"] = new_message
            payload = {"outcome": "ok", "chat_id": "irrelevant", "chat_title": "irrelevant", "summary": "ok"}
            yield type("E", (), {"content": _text_content(json.dumps(payload))})()

        async def close(self) -> None:
            return None

    def _text_content(text: str):
        from google.genai import types

        return types.Content(role="model", parts=[types.Part.from_text(text=text)])

    monkeypatch.setattr(execution_module, "Runner", _CapturingSynthesisRunner)

    session = await session_service.get_session(session_id)
    continuation = ResolvedReadContinuation(
        selected_chat_id="chat-real",
        selected_chat_topic="Chat Real",
        attachment_ids=["att-linked-1"],
    )
    result = await execute_read_continuation(
        session_service=session_service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-continuation-image",
        parent_state=dict(session.state),
        continuation=continuation,
        attachment_service=attachment_service,
        attachment_storage=storage,
    )

    assert result is not None
    assert result["outcome"] == "ok"

    nested_content = captured_content["content"]
    file_parts = [p for p in nested_content.parts if getattr(p, "file_data", None) is not None]
    assert len(file_parts) == 1
    stored_record = await attachment_service.get_owned("att-linked-1", "api-user")
    assert file_parts[0].file_data.file_uri == storage.uri_for(stored_record.storage_object_name)
    assert file_parts[0].file_data.mime_type == "image/png"
    # No GCS URI ever appears in the structured-request TEXT part.
    text_parts = [p.text for p in nested_content.parts if getattr(p, "text", None)]
    assert all("gs://" not in t for t in text_parts)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "corruption",
    ["deleted", "foreign_owner", "wrong_session", "unknown", "still_ready"],
)
async def test_execute_read_continuation_fails_closed_and_never_falls_back_to_teams_only(
    monkeypatch: pytest.MonkeyPatch, corruption: str
) -> None:
    """Instruction section 32: every corrupted continuation attachment
    reference must abort the WHOLE continuation -- never silently proceed
    with Teams-only reasoning, and never even reach a real `teams.
    getMessages` gateway call (the failure is caught before retrieval).
    """
    from backend.selection.schemas import ResolvedReadContinuation
    from backend.gateway import power_automate_client as pac_module
    from backend.agents.team_manager.read_continuation_execution import execute_read_continuation

    gateway_calls: list[str] = []

    def fake_post(url, json, timeout):
        gateway_calls.append(json.get("operation"))
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    session_service = ApiSessionService()
    session_id = await session_service.create_session()

    attachment_service = _attachment_service()
    storage = ChatAttachmentStorage("test-bucket")
    attachment_id = "att-corrupt-1"

    if corruption == "deleted":
        await _linked_record(attachment_service, attachment_id=attachment_id, owner_user_id="api-user", session_id=session_id)
        await attachment_service.mark_deleted(attachment_id, "api-user", session_id)
    elif corruption == "foreign_owner":
        await _linked_record(attachment_service, attachment_id=attachment_id, owner_user_id="a-different-user", session_id=session_id)
    elif corruption == "wrong_session":
        await _linked_record(attachment_service, attachment_id=attachment_id, owner_user_id="api-user", session_id="a-different-session")
    elif corruption == "unknown":
        pass  # never created at all
    elif corruption == "still_ready":
        await attachment_service.create(
            owner_user_id="api-user",
            session_id=session_id,
            original_filename="never-sent.png",
            mime_type="image/png",
            size_bytes=1024,
            sha256="b" * 64,
            attachment_id=attachment_id,
        )

    session = await session_service.get_session(session_id)
    continuation = ResolvedReadContinuation(
        selected_chat_id="chat-real", selected_chat_topic="Chat Real", attachment_ids=[attachment_id]
    )
    result = await execute_read_continuation(
        session_service=session_service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id=f"run-corrupt-{corruption}",
        parent_state=dict(session.state),
        continuation=continuation,
        attachment_service=attachment_service,
        attachment_storage=storage,
    )

    assert result is not None
    assert result["outcome"] == "error"
    assert "image" in (result.get("detail") or "").lower()
    assert "teams.getMessages" not in gateway_calls  # aborted before any Teams retrieval was attempted


# --- 6: rewind discards a pending selection's own image references ----------


@pytest.mark.asyncio
async def test_rewind_discards_a_pending_selection_and_its_hidden_attachment_ids() -> None:
    """A rewind that discards the turn which created an image-bearing
    `PendingSelection` must also reverse the session state holding it --
    ADK's own `_compute_state_delta_for_rewind` (verified/relied on by the
    existing rewind test suite -- see test_chat_service_rewind.py) applies
    unchanged here; this proves it covers B6's own new field too.
    """
    from backend.selection.schemas import PendingSelection, SelectionKind, SelectionOption, SelectionStatus
    from backend.selection.service import PENDING_SELECTION_STATE_KEY, load_active_selection
    from google.adk.agents import Agent
    from google.adk.runners import Runner

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)
    session = await append_user_turn(service, session, "inv-1", "first message")
    session = await append_user_turn(service, session, "inv-2", "look at this image and check Ops Bridge")

    selection = PendingSelection(
        selection_id="sel-ghost",
        kind=SelectionKind.TEAMS_CHAT,
        status=SelectionStatus.PENDING,
        requested_value="Ops Bridge",
        options=[SelectionOption(option_id="opt-1", label="Ops Bridge Daily")],
        created_at=datetime.now(timezone.utc),
        option_targets={"opt-1": {"chat_id": "chat-a", "topic": "Ops Bridge Daily"}},
        pending_read_intent=PendingReadIntent(attachment_ids=["att-ghost-1"]),
    )
    await service.persist_state_delta(session, {PENDING_SELECTION_STATE_KEY: selection.model_dump(mode="json")})
    refreshed = await service.get_session(session_id)
    assert load_active_selection(refreshed.state) is not None

    agent = Agent(name="test_agent", model="gemini-2.0-flash")
    real_runner = Runner(app_name="slopanoc-api", agent=agent, session_service=service.adk_session_service)
    chat_service = ChatService(service, runner=real_runner)
    await chat_service.rewind_before_user_turn(session_id, 1)  # discard the turn that created the selection

    after_rewind = await service.get_session(session_id)
    assert load_active_selection(after_rewind.state) is None
    assert after_rewind.state.get(PENDING_SELECTION_STATE_KEY) is None
