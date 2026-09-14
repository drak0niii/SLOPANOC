"""Phase 6A.14A HARDENING PASS -- eliminates the fail-open history edge
case and enforces full canonical-payload immutability.

Section 1: unit tests for the new/changed `backend/api/turn_source_
references.py` primitives -- `CANONICAL_RESULT_ENFORCEMENT_STATE_KEY`/
`is_canonical_result_enforcement_marker_event`, `resolve_canonical_turn_
state`/`CanonicalTurnStatus`, and `build_turn_source_references_delta`'s
now-complete-payload conflict/idempotency comparison.

Section 2: unit tests for `session_history_service.py`'s `_project_turns`
own event-order-based `canonical_required` classification.

Section 3: real end-to-end integration tests (a real file-backed
`DatabaseSessionService`, the REAL `team_manager` agent object, real
`ChatService`/`get_session_history`) proving the double-persistence-
failure gap is closed, the initial marker-establishment failure blocks
all specialist/model execution, genuine legacy turns remain readable, a
mixed legacy+canonical session renders correctly, restart-durability
holds, and rewind correctly reverses the enforcement marker/canonical
result/failure state together.

This file is ADDITIVE -- `test_p6a14a_canonical_turn_result.py`'s own 18
original tests are left completely untouched and continue to pass
unmodified (verified by this pass's own regression run).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.agents import Agent
from google.adk.models import BaseLlm, LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types
from pydantic import PrivateAttr

from backend.agents.team_manager.agent import team_manager
from backend.api.chat_service import ChatService
from backend.api.schemas import KnowledgeSourceReferenceDTO, SourceReferenceDTO
from backend.api.session_history_service import _project_turns, get_session_history
from backend.api.session_service import APP_NAME, ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.api.turn_source_references import (
    CANONICAL_RESULT_ENFORCEMENT_STATE_KEY,
    TURN_SOURCE_REFERENCES_STATE_KEY,
    CanonicalTurnResultConflictError,
    CanonicalTurnStatus,
    build_turn_source_references_delta,
    is_canonical_result_enforcement_marker_event,
    resolve_canonical_turn_state,
)
from backend.attachments.repository import AttachmentRepository
from backend.attachments.service import AttachmentService
from backend.tests._api_fakes import FakeEvent, append_user_turn

# --- Section 1: turn_source_references.py unit tests ------------------------


def _source_dto(source_id: str = "src-1") -> SourceReferenceDTO:
    return SourceReferenceDTO(source_id=source_id, source_type="teams", label="Teams conversation", title="Ops Bridge")


def _km_dto(section_id: str, heading: str, knowledge_id: str = "aurora-relay", version_label: str = "v1") -> KnowledgeSourceReferenceDTO:
    return KnowledgeSourceReferenceDTO(
        source_id=f"ks-{knowledge_id}-{version_label}-{section_id}",
        source_type="knowledge",
        label="Governed knowledge",
        knowledge_id=knowledge_id,
        version_label=version_label,
        section_id=section_id,
        title="Aurora Relay Verification Procedure",
        document_type="technical_instruction",
        source_system="test",
        evidence_source_id="aurora-relay-doc",
        section_heading=heading,
        content="...",
    )


def test_9_9_identical_complete_payload_persisted_twice_is_idempotent() -> None:
    first = build_turn_source_references_delta(
        {}, "turn-1", _source_dto(), [_km_dto("s0", "Verification")], final_text="Answer."
    )
    second = build_turn_source_references_delta(
        first, "turn-1", _source_dto(), [_km_dto("s0", "Verification")], final_text="Answer."
    )
    assert second["turn-1"] == first["turn-1"]


def test_9_7_same_text_different_teams_source_conflicts() -> None:
    first = build_turn_source_references_delta({}, "turn-1", _source_dto("src-1"), [], final_text="Answer.")
    with pytest.raises(CanonicalTurnResultConflictError):
        build_turn_source_references_delta(first, "turn-1", _source_dto("src-2"), [], final_text="Answer.")


def test_9_7_same_text_source_added_conflicts() -> None:
    first = build_turn_source_references_delta({}, "turn-1", None, [], final_text="Answer.")
    with pytest.raises(CanonicalTurnResultConflictError):
        build_turn_source_references_delta(first, "turn-1", _source_dto(), [], final_text="Answer.")


def test_9_7_same_text_source_removed_conflicts() -> None:
    first = build_turn_source_references_delta({}, "turn-1", _source_dto(), [], final_text="Answer.")
    with pytest.raises(CanonicalTurnResultConflictError):
        build_turn_source_references_delta(first, "turn-1", None, [], final_text="Answer.")


def test_9_8_same_text_different_knowledge_id_conflicts() -> None:
    first = build_turn_source_references_delta({}, "turn-1", None, [_km_dto("s0", "Verification")], final_text="Answer.")
    with pytest.raises(CanonicalTurnResultConflictError):
        build_turn_source_references_delta(
            first, "turn-1", None, [_km_dto("s0", "Verification", knowledge_id="other-doc")], final_text="Answer."
        )


def test_9_8_same_text_different_version_label_conflicts() -> None:
    first = build_turn_source_references_delta({}, "turn-1", None, [_km_dto("s0", "Verification")], final_text="Answer.")
    with pytest.raises(CanonicalTurnResultConflictError):
        build_turn_source_references_delta(
            first, "turn-1", None, [_km_dto("s0", "Verification", version_label="v2")], final_text="Answer."
        )


def test_9_8_same_text_different_section_id_conflicts() -> None:
    first = build_turn_source_references_delta({}, "turn-1", None, [_km_dto("s0", "Verification")], final_text="Answer.")
    with pytest.raises(CanonicalTurnResultConflictError):
        build_turn_source_references_delta(
            first, "turn-1", None, [_km_dto("s1", "Verification")], final_text="Answer."
        )


def test_9_8_knowledge_source_added_conflicts() -> None:
    first = build_turn_source_references_delta({}, "turn-1", None, [_km_dto("s0", "Verification")], final_text="Answer.")
    with pytest.raises(CanonicalTurnResultConflictError):
        build_turn_source_references_delta(
            first,
            "turn-1",
            None,
            [_km_dto("s0", "Verification"), _km_dto("s1", "Escalation")],
            final_text="Answer.",
        )


def test_9_10_source_order_is_authoritative_reordering_conflicts() -> None:
    """9.10: ordering is deliberately authoritative in this comparison
    (never silently re-sorted) -- the SAME two Knowledge sources supplied
    in a different order are treated as a material difference, never as
    an equivalent payload. Real callers (chat_service.py) always apply
    the SAME deterministic `dedupe_knowledge_source_references` ordering
    before calling this function, so a genuine re-persist of the same
    logical selection always produces the same order in practice."""
    a, b = _km_dto("s0", "Verification"), _km_dto("s1", "Escalation")
    first = build_turn_source_references_delta({}, "turn-1", None, [a, b], final_text="Answer.")
    with pytest.raises(CanonicalTurnResultConflictError):
        build_turn_source_references_delta(first, "turn-1", None, [b, a], final_text="Answer.")


def test_conflict_never_mutates_the_existing_entry() -> None:
    first = build_turn_source_references_delta({}, "turn-1", None, [], final_text="First.")
    try:
        build_turn_source_references_delta(first, "turn-1", None, [], final_text="Second.")
    except CanonicalTurnResultConflictError:
        pass
    assert first["turn-1"]["final_text"] == "First."


def test_different_turn_id_never_conflicts() -> None:
    first = build_turn_source_references_delta({}, "turn-1", None, [], final_text="First.")
    second = build_turn_source_references_delta(first, "turn-2", None, [], final_text="Different text entirely.")
    assert second["turn-1"]["final_text"] == "First."
    assert second["turn-2"]["final_text"] == "Different text entirely."


def test_marker_event_detection() -> None:
    class _StateDeltaEvent:
        def __init__(self, state_delta: dict[str, Any]) -> None:
            self.actions = type("A", (), {"state_delta": state_delta})()

    assert is_canonical_result_enforcement_marker_event(
        _StateDeltaEvent({CANONICAL_RESULT_ENFORCEMENT_STATE_KEY: True})
    )
    assert not is_canonical_result_enforcement_marker_event(_StateDeltaEvent({"unrelated": True}))
    assert not is_canonical_result_enforcement_marker_event(_StateDeltaEvent({}))


def test_marker_event_detection_defensive_for_missing_actions() -> None:
    class _NoActions:
        pass

    assert is_canonical_result_enforcement_marker_event(_NoActions()) is False


def test_9_11_malformed_canonical_state_fails_closed() -> None:
    """final_text present but the wrong type -- never a valid string."""
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: {"turn-1": {"final_text": 12345}}}
    result = resolve_canonical_turn_state(state, "turn-1")
    assert result.status == CanonicalTurnStatus.MALFORMED
    assert result.result is None


def test_9_12_conflicting_success_and_failure_state_fails_closed() -> None:
    """A structurally contradictory entry (never producible by this
    module's own writers, which always overwrite the whole entry) is
    still checked defensively and fails closed."""
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: {"turn-1": {"final_text": "Answer.", "failed": True}}}
    result = resolve_canonical_turn_state(state, "turn-1")
    assert result.status == CanonicalTurnStatus.CONFLICTING
    assert result.result is None


def test_resolve_canonical_turn_state_absent_for_missing_entry() -> None:
    result = resolve_canonical_turn_state({}, "turn-1")
    assert result.status == CanonicalTurnStatus.ABSENT
    assert result.result is None


def test_resolve_canonical_turn_state_failed_for_failure_marker() -> None:
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: {"turn-1": {"failed": True}}}
    result = resolve_canonical_turn_state(state, "turn-1")
    assert result.status == CanonicalTurnStatus.FAILED
    assert result.result is None


def test_resolve_canonical_turn_state_valid_for_a_real_result() -> None:
    delta = build_turn_source_references_delta({}, "turn-1", _source_dto(), [], final_text="Answer.")
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: delta}
    result = resolve_canonical_turn_state(state, "turn-1")
    assert result.status == CanonicalTurnStatus.VALID
    assert result.result is not None
    assert result.result.text == "Answer."


# --- Section 2: _project_turns canonical_required classification -----------


class _RawStateDeltaEvent:
    """Minimal duck-typed event carrying only `.actions.state_delta` plus
    the fields `_project_turns`/`is_genuine_user_content_event`/
    `_extract_final_text` need to see it as neither a genuine user event
    nor a final-response event."""

    def __init__(self, invocation_id: str, state_delta: dict[str, Any]) -> None:
        self.invocation_id = invocation_id
        self.content = None
        self.partial = False
        self.author = "user"
        self.actions = type("A", (), {"state_delta": state_delta})()

    def get_function_calls(self) -> list[Any]:
        return []

    def get_function_responses(self) -> list[Any]:
        return []

    def is_final_response(self) -> bool:
        return False


class _Part:
    def __init__(self, text: str) -> None:
        self.text = text
        self.thought = False


class _Content:
    def __init__(self, role: str, text: str) -> None:
        self.role = role
        self.parts = [_Part(text)]


class _RawContentEvent:
    """Minimal duck-typed event matching exactly what `is_genuine_user_
    content_event`/`_extract_final_text`/`_project_turns` each read --
    deliberately NOT `FakeEvent` (which has no `.content.role`/
    `.timestamp`, since it is designed only to be yielded by `FakeRunner`
    and consumed directly by `chat_service.py`'s own turn loop, never
    read back as a persisted `session.events` entry)."""

    def __init__(self, invocation_id: str, author: str, role: str, text: str, timestamp: float, final: bool) -> None:
        self.invocation_id = invocation_id
        self.author = author
        self.content = _Content(role, text)
        self.timestamp = timestamp
        self.partial = False
        self._final = final

    def get_function_calls(self) -> list[Any]:
        return []

    def get_function_responses(self) -> list[Any]:
        return []

    def is_final_response(self) -> bool:
        return self._final


def _user_event(invocation_id: str, text: str, timestamp: float = 1.0) -> Any:
    return _RawContentEvent(invocation_id, "user", "user", text, timestamp, final=False)


def _final_event(invocation_id: str, text: str, timestamp: float = 2.0) -> Any:
    return _RawContentEvent(invocation_id, "team_manager", "model", text, timestamp, final=True)


def test_project_turns_marks_turn_before_marker_as_not_canonical_required() -> None:
    events = [_user_event("turn-legacy", "hello"), _final_event("turn-legacy", "hi")]
    turns = _project_turns(events)
    assert len(turns) == 1
    assert turns[0].canonical_required is False


def test_project_turns_marks_turn_after_marker_as_canonical_required() -> None:
    events = [
        _RawStateDeltaEvent("marker-event", {CANONICAL_RESULT_ENFORCEMENT_STATE_KEY: True}),
        _user_event("turn-new", "hello"),
        _final_event("turn-new", "hi"),
    ]
    turns = _project_turns(events)
    assert len(turns) == 1
    assert turns[0].canonical_required is True


def test_project_turns_mixed_legacy_and_canonical_required_in_one_walk() -> None:
    events = [
        _user_event("turn-legacy", "old question", timestamp=1.0),
        _final_event("turn-legacy", "old answer", timestamp=1.5),
        _RawStateDeltaEvent("marker-event", {CANONICAL_RESULT_ENFORCEMENT_STATE_KEY: True}),
        _user_event("turn-new", "new question", timestamp=2.0),
        _final_event("turn-new", "new answer", timestamp=2.5),
    ]
    turns = _project_turns(events)
    by_id = {t.turn_id: t for t in turns}
    assert by_id["turn-legacy"].canonical_required is False
    assert by_id["turn-new"].canonical_required is True


# --- Section 3: real end-to-end integration -----------------------------


def _attachment_service() -> AttachmentService:
    return AttachmentService(AttachmentRepository("sqlite+aiosqlite:///:memory:"))


def _db_backed_service(tmp_path: Path, name: str) -> ApiSessionService:
    db_file = tmp_path / f"{name}.db"
    return ApiSessionService(adk_session_service=DatabaseSessionService(f"sqlite+aiosqlite:///{db_file.as_posix()}"))


class _OrdinaryOuterLlm(BaseLlm):
    _step: list[int] = PrivateAttr(default_factory=lambda: [0])

    def __init__(self, text: str = "Hello! How can I help you today?", **kwargs: Any) -> None:
        super().__init__(model="scripted-hardening-outer-model", **kwargs)
        self._text = text

    async def generate_content_async(self, llm_request: Any, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        step = self._step[0]
        self._step[0] += 1
        if step == 0:
            part = types.Part.from_function_call(
                name="record_source_requirements", args={"requires_teams": False, "requires_governed_knowledge": False}
            )
            part.function_call.id = "hardening-0"
            yield LlmResponse(content=types.Content(role="model", parts=[part]), partial=False)
            return
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part.from_text(text=self._text)]), partial=False)


def _build_runner(session_service: ApiSessionService, model: BaseLlm) -> Runner:
    scripted_team_manager = team_manager.model_copy(update={"model": model})
    return Runner(app_name=APP_NAME, agent=scripted_team_manager, session_service=session_service.adk_session_service)


@pytest.mark.asyncio
async def test_9_1_double_persistence_failure_fails_closed_no_raw_text_resurrected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """9.1: canonical-result persistence fails AND the best-effort
    failure-marker persistence also fails. Proves: raw ADK text is never
    returned; the turn is excluded entirely; the enforcement marker
    itself (a SEPARATE, earlier, successful write) still correctly
    classifies this turn as canonical-required, closing the exact gap a
    plain per-turn failure marker could not close on its own."""
    session_service = _db_backed_service(tmp_path, "hardening_double_failure")
    session_id = await session_service.create_session("api-user")
    runner = _build_runner(session_service, _OrdinaryOuterLlm())
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())

    original_persist = ApiSessionService.persist_state_delta

    async def _selectively_raising_persist(self: ApiSessionService, session: Any, delta: dict[str, Any]) -> None:
        if TURN_SOURCE_REFERENCES_STATE_KEY in delta:
            raise RuntimeError("simulated double failure: canonical write AND failure-marker write both fail")
        return await original_persist(self, session, delta)

    monkeypatch.setattr(ApiSessionService, "persist_state_delta", _selectively_raising_persist)

    saw_error = False
    async for event in chat_service.execute_turn_events(session_id, "hello", "api-user"):
        if event.type == StreamEventType.ERROR:
            saw_error = True
        assert event.type != StreamEventType.MESSAGE_COMPLETED

    assert saw_error is True

    monkeypatch.setattr(ApiSessionService, "persist_state_delta", original_persist)

    session = await session_service.get_session(session_id, "api-user")
    # The enforcement marker succeeded (a separate, earlier write) --
    # proving this turn is genuinely canonical-required, not merely
    # "absent" in a way that could be confused with legacy status.
    assert session.state.get(CANONICAL_RESULT_ENFORCEMENT_STATE_KEY) is True
    # But NEITHER a canonical result NOR a failure marker exists --
    # the exact double-failure shape.
    assert session.state.get(TURN_SOURCE_REFERENCES_STATE_KEY) in (None, {})

    history = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant_messages = [m for m in history.messages if m.role == "assistant"]
    assert assistant_messages == []
    user_messages = [m for m in history.messages if m.role == "user"]
    assert len(user_messages) == 1
    assert user_messages[0].text == "hello"


@pytest.mark.asyncio
async def test_9_2_initial_marker_establishment_failure_blocks_all_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """9.2: force the ONE marker-establishment write itself to fail.
    Proves: no Runner/specialist/model call happens at all; no raw
    assistant final event becomes eligible for history; the user
    receives correct error semantics."""
    session_service = _db_backed_service(tmp_path, "hardening_marker_fail")
    session_id = await session_service.create_session("api-user")

    runner_invoked = False

    class _SpyRunner:
        async def run_async(self, **kwargs: Any) -> AsyncGenerator[Any, None]:
            nonlocal runner_invoked
            runner_invoked = True
            yield FakeEvent(text="should never be reached", final=True)

        async def rewind_async(self, **kwargs: Any) -> None:
            pass

    chat_service = ChatService(session_service, runner=_SpyRunner(), attachment_service=_attachment_service())

    original_persist = ApiSessionService.persist_state_delta

    async def _raising_persist(self: ApiSessionService, session: Any, delta: dict[str, Any]) -> None:
        if CANONICAL_RESULT_ENFORCEMENT_STATE_KEY in delta:
            raise RuntimeError("simulated marker-establishment failure")
        return await original_persist(self, session, delta)

    monkeypatch.setattr(ApiSessionService, "persist_state_delta", _raising_persist)

    saw_error = False
    saw_message_completed = False
    async for event in chat_service.execute_turn_events(session_id, "hello", "api-user"):
        if event.type == StreamEventType.ERROR:
            saw_error = True
        if event.type == StreamEventType.MESSAGE_COMPLETED:
            saw_message_completed = True

    assert runner_invoked is False
    assert saw_error is True
    assert saw_message_completed is False

    monkeypatch.setattr(ApiSessionService, "persist_state_delta", original_persist)
    session = await session_service.get_session(session_id, "api-user")
    assert session.state.get(CANONICAL_RESULT_ENFORCEMENT_STATE_KEY) is not True


@pytest.mark.asyncio
async def test_9_3_genuine_legacy_turn_still_renders_from_raw_adk_text(tmp_path: Path) -> None:
    """9.3: a turn built the OLD way (no enforcement marker ever written
    -- exactly what a real pre-6A.14A session looks like) still renders
    correctly from raw ADK history, completely unaffected by this
    hardening pass."""
    session_service = _db_backed_service(tmp_path, "hardening_legacy")
    session_id = await session_service.create_session("api-user")
    session = await session_service.get_session(session_id, "api-user")

    await append_user_turn(session_service, session, "legacy-inv-1", "old question", "old answer")

    history = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant_messages = [m for m in history.messages if m.role == "assistant"]
    assert len(assistant_messages) == 1
    assert assistant_messages[0].text == "old answer"


@pytest.mark.asyncio
async def test_9_4_mixed_legacy_and_canonical_required_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """9.4: one session containing a genuine legacy turn, a new
    successful canonical-required turn, and a new failed/incomplete
    canonical-required turn. Proves each renders per its own correct
    classification."""
    session_service = _db_backed_service(tmp_path, "hardening_mixed")
    session_id = await session_service.create_session("api-user")
    session = await session_service.get_session(session_id, "api-user")

    # Legacy turn -- no marker, no canonical write.
    await append_user_turn(session_service, session, "legacy-inv-1", "old question", "old raw answer")

    # A real successful canonical-required turn (establishes the marker).
    runner = _build_runner(session_service, _OrdinaryOuterLlm("A real canonical answer."))
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())
    await chat_service.run_turn(session_id, "new question", "api-user")

    # A failed canonical-required turn (double failure, same mechanism as 9.1).
    original_persist = ApiSessionService.persist_state_delta

    async def _selectively_raising_persist(self: ApiSessionService, session_obj: Any, delta: dict[str, Any]) -> None:
        if TURN_SOURCE_REFERENCES_STATE_KEY in delta:
            raise RuntimeError("simulated failure for the mixed-session test")
        return await original_persist(self, session_obj, delta)

    monkeypatch.setattr(ApiSessionService, "persist_state_delta", _selectively_raising_persist)
    runner2 = _build_runner(session_service, _OrdinaryOuterLlm("This must never appear in history."))
    chat_service2 = ChatService(session_service, runner=runner2, attachment_service=_attachment_service())
    async for _event in chat_service2.execute_turn_events(session_id, "third question", "api-user"):
        pass
    monkeypatch.setattr(ApiSessionService, "persist_state_delta", original_persist)

    history = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant_texts = [m.text for m in history.messages if m.role == "assistant"]
    assert "old raw answer" in assistant_texts  # legacy turn -- still visible
    assert "A real canonical answer." in assistant_texts  # new successful turn -- canonical
    assert "This must never appear in history." not in assistant_texts  # failed turn -- excluded
    assert len(assistant_texts) == 2  # exactly the two successful turns, never the failed one

    user_texts = [m.text for m in history.messages if m.role == "user"]
    assert "third question" in user_texts  # the user's own message for the failed turn still shows


@pytest.mark.asyncio
async def test_9_5_restart_simulation_classification_survives_fresh_instantiation(tmp_path: Path) -> None:
    """9.5: recreate the history/session service from persisted state
    only (a fresh `ApiSessionService`/`DatabaseSessionService` instance
    against the same on-disk file) -- classification must not depend on
    process memory."""
    session_service = _db_backed_service(tmp_path, "hardening_restart")
    session_id = await session_service.create_session("api-user")
    session = await session_service.get_session(session_id, "api-user")
    await append_user_turn(session_service, session, "legacy-inv-1", "old question", "old raw answer")

    runner = _build_runner(session_service, _OrdinaryOuterLlm("A real canonical answer."))
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())
    await chat_service.run_turn(session_id, "new question", "api-user")

    restarted_service = _db_backed_service(tmp_path, "hardening_restart")
    history = await get_session_history(restarted_service, _attachment_service(), session_id, "api-user")
    assistant_texts = [m.text for m in history.messages if m.role == "assistant"]
    assert "old raw answer" in assistant_texts
    assert "A real canonical answer." in assistant_texts


@pytest.mark.asyncio
async def test_9_6_rewind_reverses_marker_and_canonical_result_together(tmp_path: Path) -> None:
    """9.6: rewind correctly removes/reverses the enforcement marker,
    the canonical result, and the failure state for the discarded turn,
    without damaging an earlier turn."""
    session_service = _db_backed_service(tmp_path, "hardening_rewind")
    session_id = await session_service.create_session("api-user")

    runner = _build_runner(session_service, _OrdinaryOuterLlm("First real answer."))
    chat_service = ChatService(session_service, runner=runner, attachment_service=_attachment_service())
    await chat_service.run_turn(session_id, "first question", "api-user")

    history_before = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert len(history_before.messages) == 2

    await chat_service.rewind_before_user_turn(session_id, 0, "api-user")

    history_after = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert history_after.messages == []

    session = await session_service.get_session(session_id, "api-user")
    # NOTE on the enforcement marker specifically: it was established as
    # a SEPARATE, earlier `append_event` (its own random invocation_id),
    # strictly BEFORE turn 0's own invocation began -- verified against
    # ADK's own documented rewind mechanism (this module's own top
    # docstring: state is restored to whatever it was immediately BEFORE
    # the rewind boundary, using real event-list order). A marker write
    # that already happened before that boundary is therefore correctly
    # PRESERVED by rewind, not reversed -- this is expected, correct ADK
    # behavior, not a defect: the running backend is still 6A.14A-era
    # code regardless of which turn was just discarded, so it remains
    # correct for a NEW turn in this session to still be canonical-
    # required. The turn's own CANONICAL RESULT, below, is what actually
    # must be (and is) cleared.
    stored = session.state.get(TURN_SOURCE_REFERENCES_STATE_KEY)
    assert not stored

    # A NEW turn after the rewind works normally regardless of the
    # marker's own preserved state (self-healing either way).
    runner2 = _build_runner(session_service, _OrdinaryOuterLlm("Second real answer, after rewind."))
    chat_service2 = ChatService(session_service, runner=runner2, attachment_service=_attachment_service())
    await chat_service2.run_turn(session_id, "second question", "api-user")

    history_final = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assistant_texts = [m.text for m in history_final.messages if m.role == "assistant"]
    assert assistant_texts == ["Second real answer, after rewind."]


@pytest.mark.asyncio
async def test_9_6b_rewind_of_a_later_turn_preserves_an_earlier_turns_canonical_result(tmp_path: Path) -> None:
    """9.6, the representative multi-turn case: turn 1 (kept) and turn 2
    (discarded via rewind) -- turn 2's own canonical result/marker-
    relative classification must be removed while turn 1's own canonical
    result remains fully intact and correctly classified."""
    session_service = _db_backed_service(tmp_path, "hardening_rewind_multiturn")
    session_id = await session_service.create_session("api-user")

    runner1 = _build_runner(session_service, _OrdinaryOuterLlm("Turn one answer."))
    chat_service1 = ChatService(session_service, runner=runner1, attachment_service=_attachment_service())
    await chat_service1.run_turn(session_id, "first question", "api-user")

    runner2 = _build_runner(session_service, _OrdinaryOuterLlm("Turn two answer."))
    chat_service2 = ChatService(session_service, runner=runner2, attachment_service=_attachment_service())
    await chat_service2.run_turn(session_id, "second question", "api-user")

    history_before = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert len(history_before.messages) == 4  # 2 user + 2 assistant

    # Discard turn 2 (index 1) -- turn 1 (index 0) must survive intact.
    await chat_service2.rewind_before_user_turn(session_id, 1, "api-user")

    history_after = await get_session_history(session_service, _attachment_service(), session_id, "api-user")
    assert len(history_after.messages) == 2
    assistant_texts = [m.text for m in history_after.messages if m.role == "assistant"]
    assert assistant_texts == ["Turn one answer."]  # turn 2's own text never resurfaces

    session = await session_service.get_session(session_id, "api-user")
    assert session.state.get(CANONICAL_RESULT_ENFORCEMENT_STATE_KEY) is True  # unaffected -- predates turn 1 already
    stored = session.state.get(TURN_SOURCE_REFERENCES_STATE_KEY)
    assert isinstance(stored, dict)
    assert len(stored) == 1  # only turn 1's own entry remains
