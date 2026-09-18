"""DEF-0048 regression tests: "Resolved Teams selection is not authoritative
across resumed read turn".

REAL LIVE UI FAILURE THIS CLOSES: "give me the last picture posted in chat
room: SLOPANOC Gateway. Describe it for me" -> ambiguous candidates
correctly presented -> user picks "SLOPANOC Gateway Group Test" -> UI
confirms the selection -> the resumed turn incorrectly re-asked the user to
pick a chat again, instead of deterministically executing the already-
resolved read.

AUDIT FINDING (see DEFECT_REGISTER.md's DEF-0048 entry for the full
writeup): the suspected root cause -- POST-6A turn-lifecycle bookkeeping
now running BEFORE `pop_read_continuation(session.state)` -- was
investigated and could NOT be reproduced as a data-loss mechanism: ADK's
own `append_event` (verified against the installed 1.33.0
`DatabaseSessionService`/`BaseSessionService` source) MERGES a delta into
the same in-memory `session.state` dict rather than replacing it, so an
unrelated `persist_state_delta` call between session load and the
continuation pop does not remove or hide the continuation. Ordering is
still hardened below (chat_service.py now claims the continuation
immediately after session load, before any other state mutation) as
defense-in-depth against a future ADK/backend change, and
`test_pending_read_continuation_survives_post6a_lifecycle_writes` below
proves the invariant directly against a real `DatabaseSessionService`.

The CONFIRMED, fixable gap was a design one: `ReadOperation`'s closed
vocabulary had no slot for "find the latest posted image and describe it"
-- the intent had nowhere to live except free-text `question`, which
`list_chats.py`'s `_safe_pending_question` must discard whenever the
ambiguous chat's own name appears inside it (a near-certainty for exactly
this phrasing). `ReadOperation.GET_LATEST_HOSTED_IMAGE` closes that gap
structurally; `execute_read_continuation`'s dispatch is also corrected so
this operation always retains real Teams tool access (the deterministic
"prefetch + tools=[] synthesis" fast path cannot ever describe an image at
all -- see read_continuation_execution.py's own DEF-0048 docstring).

Real deterministic functions are exercised throughout (`teams_list_chats`,
`selection_service.choose`, `read_resume.build_read_resume_message`,
`teams_get_messages`, `teams_get_hosted_content`, `evidence.
validate_evidence`) -- only the ADK `Runner`/model reasoning is faked
(`FakeRunner`, and a `read_continuation_executor` that still performs real
retrieval), mirroring test_read_continuation_regression.py's own
established approach.
"""
from __future__ import annotations

import base64
import inspect
import tempfile
from pathlib import Path
from typing import Any

import pytest
from google.adk.sessions import DatabaseSessionService

from backend.agents.incident_manager.evidence import validate_evidence
from backend.api import chat_service as chat_service_module
from backend.api import selection_service
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.api.turn_lifecycle import (
    TURN_LIFECYCLE_STATE_KEY,
    TurnStatus,
    build_turn_lifecycle_delta,
    reconcile_interrupted_turns,
)
from backend.gateway import power_automate_client as pac_module
from backend.selection.read_resume import GENERIC_GET_LATEST_HOSTED_IMAGE_RESUME_TEXT
from backend.selection.schemas import PendingReadIntent, ReadOperation
from backend.selection.service import (
    PENDING_READ_CONTINUATION_STATE_KEY,
    load_active_selection,
    pop_read_continuation,
)
from backend.tests._api_fakes import FakeRunner
from backend.tests._fakes import FakeResponse, chat, message
from backend.tools.teams.get_hosted_content import teams_get_hosted_content
from backend.tools.teams.get_messages import teams_get_messages
from backend.tools.teams.list_chats import teams_list_chats

_VALID_PNG_BASE64 = base64.b64encode(
    base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YA"
        "AAAASUVORK5CYII="
    )
).decode("ascii")

_HOSTED_URL = "https://graph.microsoft.com/v1.0/chats/{chat_id}/messages/{mid}/hostedContents/{cid}/$value"

_USER_REQUEST_TEXT = "give me the last picture posted in chat room: SLOPANOC Gateway. Describe it for me"


class _Ctx:
    def __init__(self, state: dict) -> None:
        self.state = state


def _image_message(msg_id: str, sender: str, sent_at: str, chat_id: str, content_id: str = "IMG1") -> dict[str, Any]:
    return {
        "id": msg_id,
        "createdDateTime": sent_at,
        "senderName": sender,
        "contentType": "html",
        "content": f'<p><img src="{_HOSTED_URL.format(chat_id=chat_id, mid=msg_id, cid=content_id)}"></p>',
    }


def _routed_gateway(
    list_chats_payload: list,
    get_messages_by_chat_id: dict,
    hosted_content_by_id: dict,
    call_counts: dict,
):
    def fake_post(url, json, timeout):
        operation = json.get("operation")
        call_counts[operation] = call_counts.get(operation, 0) + 1
        if operation == "teams.listChats":
            return FakeResponse(200, list_chats_payload)
        if operation == "teams.getMessages":
            chat_id = json.get("chatId")
            if chat_id not in get_messages_by_chat_id:
                return FakeResponse(404, {"error": "not found"})
            return FakeResponse(200, get_messages_by_chat_id[chat_id])
        if operation == "teams.getMembers":
            return FakeResponse(200, [])
        if operation == "teams.getHostedContent":
            cid = json.get("hostedContentId")
            return FakeResponse(200, hosted_content_by_id.get(cid, {"success": False}))
        raise AssertionError(f"unexpected operation: {operation}")

    return fake_post


def _latest_hosted_image_executor(captured: dict[str, Any], call_log: list):
    """Fake `read_continuation_executor` (matches `execute_read_
    continuation`'s own `(*, user_id, continuation)` signature) that still
    performs the REAL `teams_get_messages`/`teams_get_hosted_content`/
    `evidence.validate_evidence` work with the continuation's OWN
    authoritative `selected_chat_id` -- never a `teams_list_chats` lookup.
    Only incident_manager's own model reasoning (finding "the latest
    image" among retrieved messages, and describing it) is replaced --
    deterministically, by the SAME latest-first scan the "LATEST IMAGE
    REQUESTS" prompt instruction (prompts.py) tells the real model to
    perform -- since no live model is available in this offline suite.
    """

    async def executor(*, user_id: str, continuation: Any, **kwargs: Any) -> dict[str, Any]:
        call_log.append(continuation)
        captured["operation"] = continuation.operation
        captured["chat_topic_used"] = continuation.selected_chat_topic
        captured["question_used"] = continuation.question

        inner_ctx = _Ctx({})
        retrieval = teams_get_messages(chat_id=continuation.selected_chat_id, tool_context=inner_ctx)
        captured["retrieval"] = retrieval
        if "error" in retrieval:
            return {"outcome": "error", "detail": retrieval["error"].get("userMessage")}

        image_messages = [m for m in retrieval["messages"] if m["hosted_content_ids"]]
        if not image_messages:
            return {"outcome": "no_result", "chat_id": continuation.selected_chat_id, "chat_title": continuation.selected_chat_topic}

        latest = image_messages[-1]  # messages are chronological oldest -> newest
        captured["latest_image_message_id"] = latest["id"]
        hosted_result = teams_get_hosted_content(
            chat_id=continuation.selected_chat_id,
            message_id=latest["id"],
            hosted_content_id=latest["hosted_content_ids"][-1],
            tool_context=inner_ctx,
        )
        captured["hosted_result"] = hosted_result
        if "error" in hosted_result:
            return {"outcome": "error", "detail": hosted_result["error"].get("userMessage")}

        evidence = validate_evidence(
            [{"message_id": latest["id"], "author": latest["author"], "sent_at": latest["sent_at"]}],
            set(inner_ctx.state.get("known_message_ids", [])) | {latest["id"]},
        )
        return {
            "outcome": "ok",
            "chat_id": continuation.selected_chat_id,
            "chat_title": continuation.selected_chat_topic,
            "summary": "The most recently posted image shows a status dashboard.",
            "evidence": evidence,
        }

    return executor


async def _ambiguous_image_request_then_choose(
    service: ApiSessionService, session_id: str
):
    """Shared turn-1 setup: the real user request from the live bug report
    -- ambiguous "SLOPANOC Gateway" -> candidates -> user picks "SLOPANOC
    Gateway Group Test". Returns the `choose()` response.
    """
    session = await service.get_session(session_id)
    ctx = _Ctx(session.state)
    ambiguity_result = teams_list_chats(
        topic="SLOPANOC Gateway",
        pending_question=_USER_REQUEST_TEXT,
        pending_operation="get_latest_hosted_image",
        tool_context=ctx,
    )
    await service.persist_state_delta(session, dict(ctx.state))
    assert ambiguity_result["match"] == "not_found"
    assert ambiguity_result["selection_pending"] is True

    pending = load_active_selection((await service.get_session(session_id)).state)
    assert pending is not None
    # The intent survives structurally even though the free-text question
    # (which literally contains "SLOPANOC Gateway") had to be discarded.
    assert pending.pending_read_intent.operation == ReadOperation.GET_LATEST_HOSTED_IMAGE
    assert pending.pending_read_intent.question is None

    chosen = next(o for o in pending.options if o.label == "SLOPANOC Gateway Group Test")
    return await selection_service.choose(service, session_id, pending.selection_id, chosen.option_id), pending


# ============================================================================
# 1. The exact live user journey (task item 9) -- InMemorySessionService.
# ============================================================================


@pytest.mark.asyncio
async def test_exact_live_journey_latest_picture_resolves_without_a_second_clarification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages_payload = [
        message("m1", "Alex", "We should migrate the gateway this sprint.", "2026-08-20T09:00:00Z"),
        _image_message("m2", "Priya", "2026-08-20T09:05:00Z", chat_id="chat-real-1"),
    ]
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _routed_gateway(
            [chat("chat-real-1", "SLOPANOC Gateway Group Test"), chat("chat-real-2", "SLOPANOC Gateway Ops")],
            {"chat-real-1": messages_payload},
            {"IMG1": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}},
            call_counts,
        ),
    )

    service = ApiSessionService()
    session_id = await service.create_session()

    choose_response, pending = await _ambiguous_image_request_then_choose(service, session_id)
    assert choose_response.pending_action is None  # a read, not a write
    assert choose_response.resume_message == GENERIC_GET_LATEST_HOSTED_IMAGE_RESUME_TEXT
    assert "SLOPANOC" not in choose_response.resume_message  # never the destination name

    after_choose = await service.get_session(session_id)
    assert after_choose.state["selected_teams_chat_id"] == "chat-real-1"
    assert after_choose.state["selected_teams_chat_topic"] == "SLOPANOC Gateway Group Test"
    assert call_counts.get("teams.listChats") == 1  # the one legitimate discovery call

    captured: dict[str, Any] = {}
    call_log: list = []
    chat_service = ChatService(
        service,
        # FakeRunner cannot itself read `PENDING_SPECIALIST_RESULT_STATE_KEY`
        # the way the real `presentation_team_manager` does -- mirrors
        # test_read_continuation_regression.py's own established pattern
        # of matching `respond`'s text to the injected executor's own
        # canned summary, since the continuation path never actually
        # reaches this callable's logic for anything but its return text.
        runner=FakeRunner(service, respond=lambda t: "The most recently posted image shows a status dashboard."),
        read_continuation_executor=_latest_hosted_image_executor(captured, call_log),
    )
    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    # --- HARD acceptance criteria: no re-disambiguation of any kind. -------
    assert call_counts.get("teams.listChats") == 1  # unchanged -- never called again
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)

    # --- The authoritative chat_id was used directly for retrieval. --------
    assert captured["operation"] == ReadOperation.GET_LATEST_HOSTED_IMAGE
    assert captured["chat_topic_used"] == "SLOPANOC Gateway Group Test"
    assert captured["retrieval"]["chat_id"] == "chat-real-1"
    assert call_counts.get("teams.getMessages") == 1

    # --- The LATEST image-bearing message was found and retrieved. ---------
    assert captured["latest_image_message_id"] == "m2"
    assert captured["hosted_result"]["content_type"] == "image/png"
    assert call_counts.get("teams.getHostedContent") == 1

    # --- A real, non-empty description reached the user. --------------------
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "The most recently posted image shows a status dashboard."

    # --- Single-use: the continuation is gone after this turn. -------------
    final_session = await service.get_session(session_id, "api-user")
    assert pop_read_continuation(dict(final_session.state)) is None

    # --- The original PendingSelection stays resolved (an audit record). ---
    final_selection = load_active_selection((await service.get_session(session_id)).state)
    assert final_selection.selection_id == pending.selection_id
    assert final_selection.status == "resolved"


# ============================================================================
# 2. The SAME journey, backed by a REAL DatabaseSessionService (task item 10)
#    -- the live failure occurred with Cloud SQL/PostgreSQL, so a purely
#    InMemorySessionService-backed pass proves nothing about it.
# ============================================================================


def _database_backed_service(tmp_path: Path) -> ApiSessionService:
    db_file = tmp_path / "def_0048.db"
    adk = DatabaseSessionService(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    return ApiSessionService(adk_session_service=adk)


@pytest.mark.asyncio
async def test_exact_live_journey_over_a_real_database_session_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    messages_payload = [
        message("m1", "Alex", "We should migrate the gateway this sprint.", "2026-08-20T09:00:00Z"),
        _image_message("m2", "Priya", "2026-08-20T09:05:00Z", chat_id="chat-real-1"),
    ]
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _routed_gateway(
            [chat("chat-real-1", "SLOPANOC Gateway Group Test"), chat("chat-real-2", "SLOPANOC Gateway Ops")],
            {"chat-real-1": messages_payload},
            {"IMG1": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}},
            call_counts,
        ),
    )

    service = _database_backed_service(tmp_path)
    session_id = await service.create_session()

    choose_response, _pending = await _ambiguous_image_request_then_choose(service, session_id)

    # The continuation is durably persisted through the REAL DB backend,
    # not merely held in an in-memory Session object.
    after_choose = await service.get_session(session_id)
    assert PENDING_READ_CONTINUATION_STATE_KEY in after_choose.state

    captured: dict[str, Any] = {}
    call_log: list = []
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "The most recently posted image shows a status dashboard."),
        read_continuation_executor=_latest_hosted_image_executor(captured, call_log),
    )
    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    assert call_counts.get("teams.listChats") == 1  # never re-queried
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    assert captured["retrieval"]["chat_id"] == "chat-real-1"
    assert captured["latest_image_message_id"] == "m2"
    completed = next(e for e in collected if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed.data["content"] == "The most recently posted image shows a status dashboard."

    # POST-6A lifecycle bookkeeping genuinely ran (proves this is not a
    # trivially-short-circuited turn) AND the continuation was still
    # correctly claimed -- see next section for a direct, isolated proof
    # of this same invariant.
    final_session = await service.get_session(session_id, "api-user")
    assert final_session.state.get(TURN_LIFECYCLE_STATE_KEY)
    assert pop_read_continuation(dict(final_session.state)) is None


# ============================================================================
# 3. Selection remains authoritative after POST-6A lifecycle writes
#    (task item 11, bullet 1) -- a direct, isolated proof against a real
#    DatabaseSessionService that unrelated turn-lifecycle bookkeeping
#    (reconcile_interrupted_turns / build_turn_lifecycle_delta / their own
#    persist_state_delta call) never removes or hides a pending read
#    continuation, regardless of how many such writes happen in between.
# ============================================================================


@pytest.mark.asyncio
async def test_pending_read_continuation_survives_post6a_lifecycle_writes(tmp_path: Path) -> None:
    service = _database_backed_service(tmp_path)
    session_id = await service.create_session()

    # Mirrors `choose()`'s own storage of a continuation directly (the
    # write path itself is proven correct/unchanged by the selection
    # suite) -- this test isolates the READ/CONSUME side only.
    from backend.selection.schemas import ResolvedReadContinuation
    from backend.selection.service import store_read_continuation

    session = await service.get_session(session_id)
    continuation = ResolvedReadContinuation(
        operation=ReadOperation.GET_LATEST_HOSTED_IMAGE,
        selected_chat_id="chat-real-1",
        selected_chat_topic="SLOPANOC Gateway Group Test",
    )
    delta: dict[str, Any] = {}
    store_read_continuation(delta, continuation)
    await service.persist_state_delta(session, delta)

    # Simulate exactly what chat_service.py's own POST-6A "ONE FAILURE
    # BOUNDARY" block does -- reconcile + record ACCEPTED -- run TWICE in a
    # row (a worse case than production ever hits in one turn) between the
    # continuation being stored and it being claimed.
    session = await service.get_session(session_id)
    for run_id in ("prior-turn-1", "prior-turn-2"):
        lifecycle_delta = await reconcile_interrupted_turns(
            session.state.get(TURN_LIFECYCLE_STATE_KEY), current_turn_key=run_id, single_worker=True
        )
        lifecycle_delta.update(
            build_turn_lifecycle_delta(
                lifecycle_delta.get(TURN_LIFECYCLE_STATE_KEY, session.state.get(TURN_LIFECYCLE_STATE_KEY)),
                run_id,
                TurnStatus.ACCEPTED,
            )
        )
        await service.persist_state_delta(session, lifecycle_delta)

    # The continuation must still be there, completely unaffected.
    assert PENDING_READ_CONTINUATION_STATE_KEY in session.state
    claimed = pop_read_continuation(session.state)
    assert claimed is not None
    assert claimed.selected_chat_id == "chat-real-1"
    assert claimed.operation == ReadOperation.GET_LATEST_HOSTED_IMAGE

    # And the pop's own removal, once persisted, is durable.
    await service.persist_state_delta(session, {PENDING_READ_CONTINUATION_STATE_KEY: None})
    reloaded = await service.get_session(session_id)
    assert pop_read_continuation(dict(reloaded.state)) is None


def test_chat_service_claims_the_read_continuation_before_post6a_lifecycle_bookkeeping() -> None:
    """Structural ordering contract (defense-in-depth, task item 4): the
    continuation must be claimed immediately after session load/canonical
    enforcement, textually BEFORE the POST-6A turn-lifecycle bookkeeping
    block -- so a future change to that bookkeeping (or a future ADK/
    session-service backend with different state-merge semantics) cannot
    silently reopen this defect by sitting between session load and the
    continuation claim.
    """
    source = inspect.getsource(chat_service_module.ChatService._run_turn_events)
    pop_index = source.index("pending_read_continuation = pop_read_continuation(session.state)")
    lifecycle_index = source.index("reconcile_interrupted_turns(")
    assert pop_index < lifecycle_index, (
        "pop_read_continuation must be claimed BEFORE the POST-6A turn-lifecycle "
        "bookkeeping block (DEF-0048) -- found lifecycle bookkeeping first"
    )


# ============================================================================
# 4. Continuation is consumed exactly once; a duplicate/replayed resume
#    cannot reuse a stale continuation (task item 11, bullets 2-4).
# ============================================================================


@pytest.mark.asyncio
async def test_duplicate_resume_and_an_unrelated_next_turn_never_reuse_the_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages_payload = [_image_message("m1", "Priya", "2026-08-20T09:05:00Z", chat_id="chat-real-1")]
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _routed_gateway(
            [chat("chat-real-1", "SLOPANOC Gateway Group Test"), chat("chat-real-2", "SLOPANOC Gateway Ops")],
            {"chat-real-1": messages_payload},
            {"IMG1": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}},
            call_counts,
        ),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    choose_response, _pending = await _ambiguous_image_request_then_choose(service, session_id)

    captured: dict[str, Any] = {}
    call_log: list = []
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "an ordinary reply"),
        read_continuation_executor=_latest_hosted_image_executor(captured, call_log),
    )

    # First resume: the continuation IS consumed.
    async for _ in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user"):
        pass
    assert len(call_log) == 1

    # Second call, replaying the EXACT SAME resume text on the SAME
    # session: the continuation was already consumed -- the executor must
    # NOT run again, and this must proceed as an ordinary team_manager
    # turn (FakeRunner's own plain reply), never a repeated image lookup.
    collected_replay = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]
    assert len(call_log) == 1  # unchanged -- executor never called a second time
    assert call_counts.get("teams.getHostedContent") == 1  # unchanged
    completed_replay = next(e for e in collected_replay if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed_replay.data["content"] == "an ordinary reply"

    # Third call, a genuinely unrelated later turn: must also never
    # inherit the (already long-gone) continuation.
    collected_unrelated = [
        event
        async for event in chat_service.execute_turn_events(session_id, "tell me about a different chat entirely", "api-user")
    ]
    assert len(call_log) == 1
    completed_unrelated = next(e for e in collected_unrelated if e.type == StreamEventType.MESSAGE_COMPLETED)
    assert completed_unrelated.data["content"] == "an ordinary reply"


# ============================================================================
# 5. A failed selected-chat lookup fails clearly rather than silently
#    re-disambiguating (task item 11, bullet 5).
# ============================================================================


@pytest.mark.asyncio
async def test_failed_selected_chat_retrieval_fails_closed_never_redisambiguates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_counts: dict[str, int] = {}
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        _routed_gateway(
            [chat("chat-real-1", "SLOPANOC Gateway Group Test"), chat("chat-real-2", "SLOPANOC Gateway Ops")],
            # No `teams.getMessages` entry for chat-real-1 -- the gateway
            # rejects it (e.g. the chat became inaccessible after selection).
            {},
            {},
            call_counts,
        ),
    )

    service = ApiSessionService()
    session_id = await service.create_session()
    choose_response, _pending = await _ambiguous_image_request_then_choose(service, session_id)

    captured: dict[str, Any] = {}
    call_log: list = []
    chat_service = ChatService(
        service,
        runner=FakeRunner(service, respond=lambda t: "irrelevant -- presentation-layer wording is out of scope here"),
        read_continuation_executor=_latest_hosted_image_executor(captured, call_log),
    )
    collected = [
        event
        async for event in chat_service.execute_turn_events(session_id, choose_response.resume_message, "api-user")
    ]

    # A clear, honest outcome -- never a silent re-disambiguation. Whether
    # the failure ultimately surfaces as an ERROR event or a faithfully-
    # presented "error" IncidentManagerResponse is read_continuation_
    # presentation.py's own concern (covered by its dedicated suite); what
    # this defect is specifically about is that NO fresh ambiguity/
    # re-disambiguation is ever introduced for an already-resolved
    # destination merely because retrieval against it failed.
    assert not any(e.type == StreamEventType.SELECTION_PENDING for e in collected)
    assert call_counts.get("teams.listChats") == 1  # never re-queried
    assert captured["retrieval"].get("error")  # the fake executor saw the real gateway failure

    # The continuation is still gone either way -- single-use regardless
    # of how execution turned out.
    final_session = await service.get_session(session_id, "api-user")
    assert pop_read_continuation(dict(final_session.state)) is None


# ============================================================================
# 6. GET_LATEST_HOSTED_IMAGE must never be routed to the deterministic
#    "prefetch + tools=[] synthesis" fast path -- that path structurally
#    cannot retrieve or describe an image (no hosted_content_ids in its
#    projection, no tools at all). See read_continuation_execution.py's
#    own DEF-0048 docstring.
# ============================================================================


@pytest.mark.asyncio
async def test_get_latest_hosted_image_continuation_always_uses_the_model_driven_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.agents.team_manager import read_continuation_execution as rce
    from backend.selection.schemas import ResolvedReadContinuation

    calls: list[str] = []

    async def fake_model_driven(**kwargs):
        calls.append("model_driven")
        return {"outcome": "ok"}

    async def fake_deterministic(**kwargs):
        calls.append("deterministic")
        return {"outcome": "ok"}

    monkeypatch.setattr(rce, "_execute_via_model_driven_retrieval", fake_model_driven)
    monkeypatch.setattr(rce, "_execute_via_deterministic_retrieval", fake_deterministic)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    continuation = ResolvedReadContinuation(
        operation=ReadOperation.GET_LATEST_HOSTED_IMAGE,
        selected_chat_id="chat-real-1",
        selected_chat_topic="SLOPANOC Gateway Group Test",
        requested_time_range=None,  # the common case -- no time range given
    )

    from backend.attachments.repository import AttachmentRepository
    from backend.attachments.service import AttachmentService
    from backend.attachments.storage import ChatAttachmentStorage

    await rce.execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-1",
        parent_state=dict(session.state),
        continuation=continuation,
        attachment_service=AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:")),
        attachment_storage=ChatAttachmentStorage(None),
    )

    assert calls == ["model_driven"]  # NEVER the tools=[] deterministic/synthesis-only path


@pytest.mark.asyncio
async def test_summarize_continuation_with_no_time_range_still_uses_the_deterministic_fast_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-regression: the P4B fast path itself is untouched for the
    operations it was always correct for.
    """
    from backend.agents.team_manager import read_continuation_execution as rce
    from backend.selection.schemas import ResolvedReadContinuation

    calls: list[str] = []

    async def fake_model_driven(**kwargs):
        calls.append("model_driven")
        return {"outcome": "ok"}

    async def fake_deterministic(**kwargs):
        calls.append("deterministic")
        return {"outcome": "ok"}

    monkeypatch.setattr(rce, "_execute_via_model_driven_retrieval", fake_model_driven)
    monkeypatch.setattr(rce, "_execute_via_deterministic_retrieval", fake_deterministic)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    continuation = ResolvedReadContinuation(
        operation=ReadOperation.SUMMARIZE,
        selected_chat_id="chat-real-1",
        selected_chat_topic="SLOPANOC Gateway Group Test",
        requested_time_range=None,
    )

    from backend.attachments.repository import AttachmentRepository
    from backend.attachments.service import AttachmentService
    from backend.attachments.storage import ChatAttachmentStorage

    await rce.execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-1",
        parent_state=dict(session.state),
        continuation=continuation,
        attachment_service=AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:")),
        attachment_storage=ChatAttachmentStorage(None),
    )

    assert calls == ["deterministic"]


# ============================================================================
# 7. The structural fix itself: the ambiguous-request intent survives
#    disambiguation via `operation`, never via free-text `question`
#    (task items 2, 7, 8).
# ============================================================================


def test_teams_list_chats_classifies_latest_image_intent_structurally_despite_sanitized_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact live scenario at the `teams_list_chats` layer: the user's
    own question literally names the ambiguous topic ("chat room: SLOPANOC
    Gateway"), so `_safe_pending_question` correctly discards it (unchanged,
    documented, deliberate behavior) -- but the base "find the latest image
    and describe it" intent must survive via the closed `operation` field,
    which that sanitizer never touches.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, [chat("chat-real-1", "SLOPANOC Gateway Group Test"), chat("chat-real-2", "SLOPANOC Gateway Ops")]
        ),
    )
    ctx = _Ctx({})
    result = teams_list_chats(
        topic="SLOPANOC Gateway",
        pending_question=_USER_REQUEST_TEXT,
        pending_operation="get_latest_hosted_image",
        tool_context=ctx,
    )
    assert result["match"] == "not_found"

    pending = load_active_selection(ctx.state)
    assert pending is not None
    assert pending.pending_read_intent.operation == ReadOperation.GET_LATEST_HOSTED_IMAGE
    # The known, documented limitation: free-text focus naming the
    # ambiguous topic is still discarded -- but that is no longer where
    # the base intent lives, so nothing important is actually lost.
    assert pending.pending_read_intent.question is None


def test_get_latest_hosted_image_is_never_reduced_to_get_messages_by_sanitization() -> None:
    """Task item 2's explicit requirement: sanitization of pending_question
    must never silently reduce the request to a generic GET_MESSAGES
    operation. `_safe_read_operation` only ever touches an INVALID/missing
    `pending_operation` string -- a valid "get_latest_hosted_image" must
    pass through completely unchanged.
    """
    from backend.tools.teams.list_chats import _safe_read_operation

    assert _safe_read_operation("get_latest_hosted_image") == ReadOperation.GET_LATEST_HOSTED_IMAGE


def test_read_operation_is_a_closed_three_value_vocabulary() -> None:
    assert {op.value for op in ReadOperation} == {"summarize", "get_messages", "get_latest_hosted_image"}
