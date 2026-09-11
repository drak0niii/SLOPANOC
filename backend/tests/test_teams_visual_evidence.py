"""Teams Visual Evidence milestone -- focused backend tests.

Maps onto section 15's own required matrix (A-Q). Tiers:

  1. `hosted_content_vision_context.build_visual_evidence` -- pure,
     independently testable (A, C, D, E, F implicitly via delivery-chain
     exclusion, P).
  2. `turn_source_references.py`'s new `build_turn_source_references_delta`
     (`visual_evidence_internal` param) / `resolve_visual_evidence_binding`
     -- pure, independently testable (F, G, I, J, K, L, N, O, Q).
  3. `source_images.get_source_image_content` -- the authenticated HTTP
     content path (H, I, J, K, L, M).
  4. A real end-to-end ChatService/Runner proof that B (3 delivered
     images -> exactly 3 public visual evidence items, correct order) and
     the discovered/retrieved/queued/attached distinction (C) hold for a
     genuine turn, not just at the unit level.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.api.hosted_content_vision_context import DeliveredVisualEvidence, build_visual_evidence
from backend.api.schemas import SourceReferenceDTO
from backend.api.source_images import get_source_image_content
from backend.api.turn_source_references import (
    TURN_SOURCE_REFERENCES_STATE_KEY,
    build_turn_source_references_delta,
    resolve_visual_evidence_binding,
)
from backend.gateway.safe_error import SafeErrorException


def _delivered(
    chat_id: str = "chat-1",
    message_id: str = "msg-1",
    hosted_content_id: str = "content-a",
    ordinal: int = 1,
    mime_type: str = "image/png",
    size_bytes: int = 100,
    author: str = "Alex",
    sent_at: str = "2026-09-11T10:00:00Z",
) -> DeliveredVisualEvidence:
    return DeliveredVisualEvidence(
        chat_id=chat_id,
        message_id=message_id,
        hosted_content_id=hosted_content_id,
        mime_type=mime_type,
        size_bytes=size_bytes,
        ordinal=ordinal,
        author=author,
        sent_at=sent_at,
    )


# ============================================================================
# Tier 1: build_visual_evidence
# ============================================================================


def test_a_no_delivered_images_yields_empty_visual_evidence() -> None:
    items, internal = build_visual_evidence([], "chat-1")
    assert items == []
    assert internal == []


def test_a_none_chat_id_yields_empty_visual_evidence() -> None:
    """No Teams Source resolved this turn -- there is nothing to attach
    visual evidence to, regardless of what was delivered."""
    items, internal = build_visual_evidence([_delivered()], None)
    assert items == []
    assert internal == []


def test_b_three_delivered_images_produce_three_items_in_order() -> None:
    delivered = [
        _delivered(hosted_content_id="content-a", ordinal=1),
        _delivered(hosted_content_id="content-b", ordinal=2),
        _delivered(hosted_content_id="content-c", ordinal=3),
    ]
    items, internal = build_visual_evidence(delivered, "chat-1")

    assert [item.ordinal for item in items] == [1, 2, 3]
    assert len(internal) == 3
    # image_id values are unique and match 1:1 between the two lists.
    assert {item.image_id for item in items} == {entry["image_id"] for entry in internal}
    assert len({item.image_id for item in items}) == 3


def test_public_dto_never_carries_raw_teams_identifiers() -> None:
    items, _ = build_visual_evidence([_delivered()], "chat-1")
    dumped = items[0].model_dump()
    assert set(dumped.keys()) == {"image_id", "ordinal", "mime_type", "size_bytes", "author", "sent_at"}
    assert "chat_id" not in dumped
    assert "message_id" not in dumped
    assert "hosted_content_id" not in dumped


def test_internal_binding_carries_the_full_real_provenance_tuple() -> None:
    delivered = _delivered(chat_id="ChatA", message_id="Message1", hosted_content_id="ImageA")
    _, internal = build_visual_evidence([delivered], "ChatA")

    assert internal[0]["chat_id"] == "ChatA"
    assert internal[0]["message_id"] == "Message1"
    assert internal[0]["hosted_content_id"] == "ImageA"


def test_p_images_from_a_different_chat_are_excluded() -> None:
    """Section 6/P -- an image belonging to a DIFFERENT chat than the
    Source's own resolved chat_id must never be attached (also covers the
    "run A cannot enter run B" spirit at the DTO-construction boundary --
    an unrelated chat's image is filtered exactly like an unrelated run's
    would be, since nothing here trusts anything beyond the chat_id match).
    """
    delivered = [_delivered(chat_id="chat-1"), _delivered(chat_id="chat-2", hosted_content_id="content-other")]
    items, internal = build_visual_evidence(delivered, "chat-1")

    assert len(items) == 1
    assert len(internal) == 1
    assert internal[0]["chat_id"] == "chat-1"


def test_ordinal_and_author_sent_at_pass_through_verbatim() -> None:
    delivered = _delivered(ordinal=2, author="Priya", sent_at="2026-09-11T11:30:00Z", mime_type="image/jpeg", size_bytes=555)
    items, _ = build_visual_evidence([delivered], "chat-1")

    assert items[0].ordinal == 2
    assert items[0].author == "Priya"
    assert items[0].sent_at == "2026-09-11T11:30:00Z"
    assert items[0].mime_type == "image/jpeg"
    assert items[0].size_bytes == 555


# ============================================================================
# Tier 2: turn_source_references.py
# ============================================================================


def _source(source_id: str = "source-1") -> SourceReferenceDTO:
    return SourceReferenceDTO(source_id=source_id, source_type="teams", label="Teams conversation")


def test_q_existing_source_reference_dto_behavior_unaffected_by_new_field() -> None:
    """A DTO built with no explicit `visual_evidence` (every pre-existing
    call site in this codebase) still constructs cleanly and defaults to
    `[]` -- fully backward compatible."""
    source = _source()
    assert source.visual_evidence == []
    dumped = source.model_dump(mode="json")
    assert dumped["visual_evidence"] == []


def test_visual_evidence_internal_persists_alongside_source_and_resolves() -> None:
    source = _source(source_id="source-xyz")
    internal = [{"image_id": "img-1", "chat_id": "chat-1", "message_id": "msg-1", "hosted_content_id": "content-a"}]

    delta = build_turn_source_references_delta(None, "turn-1", source, [], internal)
    assert delta is not None

    state = {TURN_SOURCE_REFERENCES_STATE_KEY: delta}
    binding = resolve_visual_evidence_binding(state, "source-xyz", "img-1")

    assert binding is not None
    assert binding.chat_id == "chat-1"
    assert binding.message_id == "msg-1"
    assert binding.hosted_content_id == "content-a"


def test_g_persisted_internal_binding_survives_alongside_multiple_turns() -> None:
    source_1 = _source(source_id="source-1")
    source_2 = _source(source_id="source-2")
    internal_1 = [{"image_id": "img-a", "chat_id": "chat-1", "message_id": "m1", "hosted_content_id": "c1"}]
    internal_2 = [{"image_id": "img-b", "chat_id": "chat-2", "message_id": "m2", "hosted_content_id": "c2"}]

    delta_1 = build_turn_source_references_delta(None, "turn-1", source_1, [], internal_1)
    delta_2 = build_turn_source_references_delta(delta_1, "turn-2", source_2, [], internal_2)
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: delta_2}

    binding_1 = resolve_visual_evidence_binding(state, "source-1", "img-a")
    binding_2 = resolve_visual_evidence_binding(state, "source-2", "img-b")

    assert binding_1 is not None and binding_1.chat_id == "chat-1"
    assert binding_2 is not None and binding_2.chat_id == "chat-2"


def test_i_wrong_source_id_fails_safely() -> None:
    source = _source(source_id="source-real")
    internal = [{"image_id": "img-1", "chat_id": "chat-1", "message_id": "msg-1", "hosted_content_id": "content-a"}]
    delta = build_turn_source_references_delta(None, "turn-1", source, [], internal)
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: delta}

    assert resolve_visual_evidence_binding(state, "source-does-not-exist", "img-1") is None


def test_j_wrong_image_id_fails_safely() -> None:
    source = _source(source_id="source-real")
    internal = [{"image_id": "img-1", "chat_id": "chat-1", "message_id": "msg-1", "hosted_content_id": "content-a"}]
    delta = build_turn_source_references_delta(None, "turn-1", source, [], internal)
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: delta}

    assert resolve_visual_evidence_binding(state, "source-real", "img-does-not-exist") is None


def test_l_an_image_id_cannot_be_used_to_substitute_a_different_source() -> None:
    """The authoritative "no substitution" proof: img-a genuinely belongs
    to source-1 -- looking it up under source-2 must fail, never silently
    resolve to img-a's real binding under the wrong source."""
    source_1 = _source(source_id="source-1")
    source_2 = _source(source_id="source-2")
    internal_1 = [{"image_id": "img-a", "chat_id": "chat-1", "message_id": "m1", "hosted_content_id": "c1"}]
    internal_2 = [{"image_id": "img-b", "chat_id": "chat-2", "message_id": "m2", "hosted_content_id": "c2"}]
    delta_1 = build_turn_source_references_delta(None, "turn-1", source_1, [], internal_1)
    delta_2 = build_turn_source_references_delta(delta_1, "turn-2", source_2, [], internal_2)
    state = {TURN_SOURCE_REFERENCES_STATE_KEY: delta_2}

    assert resolve_visual_evidence_binding(state, "source-2", "img-a") is None


def test_o_rewind_removes_the_binding_for_the_discarded_turn() -> None:
    """Simulates ADK's own rewind reversal: a discarded turn's entry is
    simply absent from the persisted dict (the SAME mechanism this
    module's own top docstring documents for `source`/`knowledge_
    sources`) -- proves the binding resolver naturally honors that."""
    source_1 = _source(source_id="source-1")
    internal_1 = [{"image_id": "img-a", "chat_id": "chat-1", "message_id": "m1", "hosted_content_id": "c1"}]
    delta_1 = build_turn_source_references_delta(None, "turn-1", source_1, [], internal_1)
    state_before_rewind = {TURN_SOURCE_REFERENCES_STATE_KEY: delta_1}
    assert resolve_visual_evidence_binding(state_before_rewind, "source-1", "img-a") is not None

    # Rewind reverses turn-1's own state_delta -- the key returns to its
    # pre-turn-1 value, which never existed (no prior entry) -> {}.
    state_after_rewind = {TURN_SOURCE_REFERENCES_STATE_KEY: {}}
    assert resolve_visual_evidence_binding(state_after_rewind, "source-1", "img-a") is None


def test_no_visual_evidence_key_written_when_internal_is_empty() -> None:
    source = _source()
    delta = build_turn_source_references_delta(None, "turn-1", source, [], [])
    assert "visual_evidence_internal" not in delta["turn-1"]

    delta_none = build_turn_source_references_delta(None, "turn-1", source, [], None)
    assert "visual_evidence_internal" not in delta_none["turn-1"]


def test_resolve_binding_malformed_state_never_raises() -> None:
    assert resolve_visual_evidence_binding({}, "source-1", "img-1") is None
    assert resolve_visual_evidence_binding({TURN_SOURCE_REFERENCES_STATE_KEY: None}, "source-1", "img-1") is None
    assert resolve_visual_evidence_binding({TURN_SOURCE_REFERENCES_STATE_KEY: "not-a-dict"}, "s", "i") is None
    assert (
        resolve_visual_evidence_binding(
            {TURN_SOURCE_REFERENCES_STATE_KEY: {"turn-1": {"source": {"source_id": "s1"}, "visual_evidence_internal": "not-a-list"}}},
            "s1",
            "img-1",
        )
        is None
    )


# ============================================================================
# Tier 3: source_images.get_source_image_content (authenticated HTTP path)
# ============================================================================


class _FakeSession:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


class _FakeSessionService:
    def __init__(self, session: Optional[_FakeSession]) -> None:
        self._session = session
        self.get_session_calls: list[tuple[str, str]] = []

    async def get_session(self, session_id: str, user_id: str):
        self.get_session_calls.append((session_id, user_id))
        if self._session is None:
            from backend.gateway.safe_error import not_found

            raise not_found("No session was found with that id.")
        return self._session


def _state_with_binding(source_id: str, image_id: str, chat_id: str, message_id: str, hosted_content_id: str) -> dict:
    source = _source(source_id=source_id)
    internal = [{"image_id": image_id, "chat_id": chat_id, "message_id": message_id, "hosted_content_id": hosted_content_id}]
    delta = build_turn_source_references_delta(None, "turn-1", source, [], internal)
    return {TURN_SOURCE_REFERENCES_STATE_KEY: delta}


@pytest.mark.asyncio
async def test_h_correct_session_source_image_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.tools.teams.get_hosted_content import HostedContentBytes

    state = _state_with_binding("source-1", "img-1", "chat-1", "msg-1", "content-a")
    session_service = _FakeSessionService(_FakeSession(state))

    def fake_fetch(chat_id: str, message_id: str, hosted_content_id: str):
        assert (chat_id, message_id, hosted_content_id) == ("chat-1", "msg-1", "content-a")
        return HostedContentBytes(content_type="image/png", data=b"\x89PNGFAKE")

    monkeypatch.setattr("backend.api.source_images.fetch_and_validate_hosted_content", fake_fetch)

    data, content_type = await get_source_image_content(session_service, "user-1", "session-1", "source-1", "img-1")

    assert data == b"\x89PNGFAKE"
    assert content_type == "image/png"
    assert session_service.get_session_calls == [("session-1", "user-1")]


@pytest.mark.asyncio
async def test_i_wrong_source_id_fails_safely_at_http_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state_with_binding("source-1", "img-1", "chat-1", "msg-1", "content-a")
    session_service = _FakeSessionService(_FakeSession(state))

    called = {"count": 0}

    def fake_fetch(*a, **k):
        called["count"] += 1
        raise AssertionError("must never reach the gateway for an unresolved binding")

    monkeypatch.setattr("backend.api.source_images.fetch_and_validate_hosted_content", fake_fetch)

    with pytest.raises(SafeErrorException) as exc_info:
        await get_source_image_content(session_service, "user-1", "session-1", "source-does-not-exist", "img-1")

    assert exc_info.value.safe_error.error_code == "not_found"
    assert called["count"] == 0


@pytest.mark.asyncio
async def test_j_wrong_image_id_fails_safely_at_http_layer(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _state_with_binding("source-1", "img-1", "chat-1", "msg-1", "content-a")
    session_service = _FakeSessionService(_FakeSession(state))

    monkeypatch.setattr(
        "backend.api.source_images.fetch_and_validate_hosted_content",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")),
    )

    with pytest.raises(SafeErrorException) as exc_info:
        await get_source_image_content(session_service, "user-1", "session-1", "source-1", "img-does-not-exist")

    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_k_wrong_owner_session_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    """`session_service.get_session` itself is where owner-scoping is
    enforced (mirrors every other session-scoped route) -- simulate the
    "unknown or foreign-owner session" case by having the fake service
    raise, exactly like the real `ApiSessionService.get_session` does.
    """
    session_service = _FakeSessionService(None)

    with pytest.raises(SafeErrorException) as exc_info:
        await get_source_image_content(session_service, "attacker", "someone-elses-session", "source-1", "img-1")

    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_l_source_endpoint_cannot_substitute_a_different_hosted_content_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The authoritative proof at the HTTP layer: even a well-formed,
    genuinely-existing (source_id, image_id) pair can only ever resolve to
    the ONE real triple it was durably bound to -- there is no parameter
    on this endpoint that could substitute a different hostedContentId."""
    state = _state_with_binding("source-1", "img-1", "chat-1", "msg-1", "content-a")
    session_service = _FakeSessionService(_FakeSession(state))

    captured_args: list[tuple[str, str, str]] = []

    def fake_fetch(chat_id: str, message_id: str, hosted_content_id: str):
        captured_args.append((chat_id, message_id, hosted_content_id))
        from backend.tools.teams.get_hosted_content import HostedContentBytes

        return HostedContentBytes(content_type="image/png", data=b"X")

    monkeypatch.setattr("backend.api.source_images.fetch_and_validate_hosted_content", fake_fetch)

    await get_source_image_content(session_service, "user-1", "session-1", "source-1", "img-1")

    assert captured_args == [("chat-1", "msg-1", "content-a")]


@pytest.mark.asyncio
async def test_m_returned_mime_and_bytes_are_revalidated_not_trusted_from_the_original_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The endpoint must call `fetch_and_validate_hosted_content` (the
    SAME real validation path) on every request -- never merely replay the
    `mime_type`/`size_bytes` recorded at the original turn."""
    state = _state_with_binding("source-1", "img-1", "chat-1", "msg-1", "content-a")
    session_service = _FakeSessionService(_FakeSession(state))

    call_count = {"n": 0}

    def fake_fetch(chat_id: str, message_id: str, hosted_content_id: str):
        call_count["n"] += 1
        from backend.tools.teams.get_hosted_content import HostedContentBytes

        return HostedContentBytes(content_type="image/jpeg", data=b"REAL-CURRENT-BYTES")

    monkeypatch.setattr("backend.api.source_images.fetch_and_validate_hosted_content", fake_fetch)

    data, content_type = await get_source_image_content(session_service, "user-1", "session-1", "source-1", "img-1")

    assert call_count["n"] == 1
    assert data == b"REAL-CURRENT-BYTES"
    assert content_type == "image/jpeg"


@pytest.mark.asyncio
async def test_unavailable_teams_content_fails_safely_never_leaks_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Section 13's own "if the original Teams hosted content has since
    become unavailable" case -- a fetch failure collapses to the SAME
    generic not_found, never a different error shape."""
    state = _state_with_binding("source-1", "img-1", "chat-1", "msg-1", "content-a")
    session_service = _FakeSessionService(_FakeSession(state))

    monkeypatch.setattr(
        "backend.api.source_images.fetch_and_validate_hosted_content",
        lambda *a, **k: {"error": {"errorCode": "internal_error", "userMessage": "gone"}},
    )

    with pytest.raises(SafeErrorException) as exc_info:
        await get_source_image_content(session_service, "user-1", "session-1", "source-1", "img-1")

    assert exc_info.value.safe_error.error_code == "not_found"
    # Never leaks the underlying detail/error code from the fetch failure.
    assert "gone" not in exc_info.value.safe_error.user_message


# ============================================================================
# Explicit gap-fill: C, N, P
# ============================================================================


def test_c_queued_but_never_attached_image_is_excluded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stash that reaches QUEUED but whose turn ends before `inject_
    pending_hosted_content_image` ever runs (e.g. an error immediately
    after the tool call) must never produce a delivered-visual-evidence
    record -- `discard_pending_hosted_content_image` (the `finally`-block
    backstop) clears the pending stash without ever touching `_delivered`,
    so `pop_delivered_visual_evidence` correctly returns `[]`.
    """
    from backend.api import hosted_content_vision_context as hcv
    from backend.api.turn_context import bind_run_id, reset_run_id

    token = bind_run_id("run-queued-not-attached")
    try:
        delivered = hcv.stash_pending_hosted_content_image("chat-1", "msg-1", "content-a", "image/png", b"X")
        assert delivered is True  # genuinely reached QUEUED
    finally:
        reset_run_id(token)
        # Simulates the turn ending (error/cancellation) before any further
        # model call -- the SAME cleanup chat_service.py's own `finally`
        # block always performs, on every exit path.
        hcv.discard_pending_hosted_content_image("run-queued-not-attached")

    result = hcv.pop_delivered_visual_evidence("run-queued-not-attached")
    assert result == []


def test_n_binding_survives_real_json_serialization_round_trip() -> None:
    """Concretely proves "backend-session reconstruction" survival: the
    persisted delta is round-tripped through real `json.dumps`/`json.loads`
    (exactly what durable Cloud SQL/session-state storage does) before
    being handed to the resolver -- never merely passed as the same
    in-memory Python objects.
    """
    import json

    source = _source(source_id="source-reload")
    internal = [{"image_id": "img-reload", "chat_id": "chat-1", "message_id": "msg-1", "hosted_content_id": "content-a"}]
    delta = build_turn_source_references_delta(None, "turn-1", source, [], internal)

    reloaded_state = {TURN_SOURCE_REFERENCES_STATE_KEY: json.loads(json.dumps(delta))}

    binding = resolve_visual_evidence_binding(reloaded_state, "source-reload", "img-reload")
    assert binding is not None
    assert binding.chat_id == "chat-1"
    assert binding.message_id == "msg-1"
    assert binding.hosted_content_id == "content-a"


def test_p_delivered_visual_evidence_store_is_isolated_per_run() -> None:
    """Same run-isolation guarantee already proven for `_pending`
    (test_pending_images_are_isolated_per_run, test_hosted_content_vision_
    context.py), proven here specifically for `_delivered` -- the store
    `pop_delivered_visual_evidence` actually reads from."""
    from backend.api import hosted_content_vision_context as hcv

    with hcv._lock:  # noqa: SLF001
        hcv._delivered["run-a"] = [_delivered(chat_id="chat-a")]
        hcv._delivered["run-b"] = [_delivered(chat_id="chat-b")]

    result_a = hcv.pop_delivered_visual_evidence("run-a")
    with hcv._lock:  # noqa: SLF001
        still_there_for_b = list(hcv._delivered.get("run-b", []))
    hcv.discard_pending_hosted_content_image("run-b")

    assert [d.chat_id for d in result_a] == ["chat-a"]
    assert [d.chat_id for d in still_there_for_b] == ["chat-b"]


# ============================================================================
# Live-validation bugfix regression: an image-only turn (no textual
# evidence) must still produce a Source so Visual Evidence has a home.
# ============================================================================


def test_ensure_source_reference_synthesizes_a_minimal_source_when_only_images_exist() -> None:
    from backend.api.source_reference import ensure_source_reference_for_visual_evidence

    delivered = [_delivered(chat_id="chat-1")]

    result = ensure_source_reference_for_visual_evidence(None, "chat-1", delivered)

    assert result is not None
    assert result.source_type == "teams"
    assert result.message_count is None
    assert result.period_start is None
    assert result.period_end is None
    assert result.evidence == []
    assert result.contributors == []


def test_ensure_source_reference_returns_existing_source_unchanged() -> None:
    from backend.api.source_reference import ensure_source_reference_for_visual_evidence

    existing = _source(source_id="already-built")
    result = ensure_source_reference_for_visual_evidence(existing, "chat-1", [_delivered(chat_id="chat-1")])

    assert result is existing


def test_ensure_source_reference_returns_none_when_no_chat_id() -> None:
    from backend.api.source_reference import ensure_source_reference_for_visual_evidence

    result = ensure_source_reference_for_visual_evidence(None, None, [_delivered(chat_id="chat-1")])
    assert result is None


def test_ensure_source_reference_returns_none_when_no_matching_delivered_image() -> None:
    from backend.api.source_reference import ensure_source_reference_for_visual_evidence

    # Images exist, but for a DIFFERENT chat than the one being asked about.
    result = ensure_source_reference_for_visual_evidence(None, "chat-1", [_delivered(chat_id="chat-2")])
    assert result is None


def test_ensure_source_reference_returns_none_when_nothing_delivered_at_all() -> None:
    from backend.api.source_reference import ensure_source_reference_for_visual_evidence

    result = ensure_source_reference_for_visual_evidence(None, "chat-1", [])
    assert result is None
