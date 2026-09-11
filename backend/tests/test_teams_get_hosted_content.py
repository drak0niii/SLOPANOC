"""Teams Rich Content milestone (single-image scope) -- unit tests for
`backend.tools.teams.get_hosted_content.teams_get_hosted_content`.

Maps onto this milestone's own required test matrix (cases G [gateway
payload -- see test_power_automate_client.py], H-L, Q, R), PLUS the Teams
Image Vision + Full Provenance Binding corrective milestone's own required
matrix: provenance is now bound to the FULL `(chat_id, message_id,
hosted_content_id)` triple (section 8's own "A-E" cases, below), and a
successful retrieval stashes the validated bytes for real Gemini
multimodal delivery (section 6's own multimodal test requirements).
"""
from __future__ import annotations

import base64
from typing import Any, Optional

import pytest

from backend.api import hosted_content_vision_context
from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse
from backend.tools.teams.get_hosted_content import teams_get_hosted_content
from backend.tools.teams.get_messages import KNOWN_HOSTED_CONTENT_IDS_STATE_KEY

# A real, minimal, valid 1x1 PNG -- small enough to embed directly, real
# enough that Pillow genuinely decodes it (never a fabricated/oversized
# fixture, and never a real production screenshot).
_VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YA"
    "AAAASUVORK5CYII="
)
_VALID_PNG_BASE64 = base64.b64encode(_VALID_PNG_BYTES).decode("ascii")


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = dict(state or {})


def _known_state(chat_id: str, message_id: str, hosted_content_ids: list[str]) -> dict[str, Any]:
    """Builds the CURRENT, nested `dict[chat_id, dict[message_id,
    list[hosted_content_id]]]` shape -- see get_messages.py's own
    `KNOWN_HOSTED_CONTENT_IDS_STATE_KEY` docstring for the full corrective-
    pass rationale (the full provenance binding fix this milestone made).
    """
    return {KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {chat_id: {message_id: hosted_content_ids}}}


def _mock_gateway(monkeypatch: pytest.MonkeyPatch, response_body: Any, status_code: int = 200) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(status_code, response_body))


@pytest.fixture(autouse=True)
def _clean_pending_image_store() -> Any:
    """Every test in this file calls `teams_get_hosted_content` without a
    bound `current_run_id()` (no `chat_service.py`-driven turn), so
    `stash_pending_hosted_content_image` is always a safe no-op here --
    this fixture only guards against any accidental cross-test leakage if
    that assumption is ever violated.
    """
    yield
    with hosted_content_vision_context._lock:  # noqa: SLF001 -- test-only direct access to assert isolation.
        hosted_content_vision_context._pending.clear()


# --- H: successful response parsing --------------------------------------


def test_h_successful_response_yields_provider_neutral_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(
        monkeypatch,
        {
            "success": True,
            "chatId": "chat-1",
            "messageId": "msg-1",
            "hostedContentId": "content-abc",
            "contentType": "image/png",
            "contentBase64": _VALID_PNG_BASE64,
        },
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" not in result
    assert result == {
        "chat_id": "chat-1",
        "message_id": "msg-1",
        "hosted_content_id": "content-abc",
        "content_type": "image/png",
        "size_bytes": len(_VALID_PNG_BYTES),
        "delivered_for_visual_reasoning": False,  # no bound run_id in this test -- see its own contract
    }


def test_h_result_never_contains_base64_or_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(
        monkeypatch,
        {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "contentBase64" not in result
    serialized_values = "".join(str(v) for v in result.values())
    assert _VALID_PNG_BASE64 not in serialized_values


# --- I: invalid Base64 ------------------------------------------------------


def test_i_invalid_base64_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(
        monkeypatch,
        {"success": True, "contentType": "image/png", "contentBase64": "not-valid-base64!!!"},
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result


# --- J: empty Base64 ---------------------------------------------------------


def test_j_empty_base64_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": ""})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result


def test_j_missing_base64_field_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png"})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result


# --- K: mismatching hostedContentId echoed by the gateway -------------------


def test_k_mismatching_echoed_hosted_content_id_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(
        monkeypatch,
        {
            "success": True,
            "hostedContentId": "a-different-content-id",
            "contentType": "image/png",
            "contentBase64": _VALID_PNG_BASE64,
        },
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result


# --- L: mismatching message/chat ID echoed by the gateway -------------------


def test_l_mismatching_echoed_message_id_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(
        monkeypatch,
        {
            "success": True,
            "messageId": "a-different-message-id",
            "contentType": "image/png",
            "contentBase64": _VALID_PNG_BASE64,
        },
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result


def test_l_mismatching_echoed_chat_id_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(
        monkeypatch,
        {
            "success": True,
            "chatId": "a-different-chat-id",
            "contentType": "image/png",
            "contentBase64": _VALID_PNG_BASE64,
        },
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result


def test_gateway_reported_failure_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(monkeypatch, {"success": False})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result


def test_unsupported_content_type_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    """Image validation (instruction section 12) -- an unsupported MIME
    type (e.g. GIF, not in the currently supported PNG/JPEG/WebP set) must
    never reach a "success" result.
    """
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/gif", "contentBase64": _VALID_PNG_BASE64})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result


def test_declared_type_not_matching_actual_bytes_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never trust `contentType` merely because the provider declared it --
    real PNG bytes declared as JPEG must fail, mirroring B2's own
    declared-vs-actual discipline exactly.
    """
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/jpeg", "contentBase64": _VALID_PNG_BASE64})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result


def test_oversized_content_fails_safely(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.config.settings import get_settings

    oversized = b"\x00" * (get_settings().chat_attachment_max_bytes + 1)
    _mock_gateway(
        monkeypatch,
        {
            "success": True,
            "contentType": "image/png",
            "contentBase64": base64.b64encode(oversized).decode("ascii"),
        },
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result
    assert result["error"]["errorCode"] == "payload_too_large"


# --- Q: provenance (message-level, pre-existing) -----------------------------


def test_q_hosted_content_id_not_discovered_for_this_message_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    """The single most important provenance guarantee (instruction section
    8): a hosted_content_id never discovered for this exact message_id
    through a real `teams_get_messages` call this turn must never be
    retrievable -- the gateway must not even be called.
    """
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext()  # nothing known at all

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result
    assert called["count"] == 0


def test_q_hosted_content_id_discovered_for_a_different_message_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    # "content-abc" was discovered for a DIFFERENT message ("msg-other"),
    # never for "msg-1" -- must not authorize retrieval for "msg-1".
    ctx = _FakeToolContext(_known_state("chat-1", "msg-other", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result
    assert called["count"] == 0


def test_q_hosted_content_id_actually_discovered_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" not in result


def test_q_rewind_cleared_known_hosted_content_ids_normalizes_safely_never_crashes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same D1-class hazard, hardened proactively: a rewind that clears
    this key leaves it PRESENT with value `None` -- must normalize to "no
    known hosted content" (safe rejection), never raise.
    """
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext({KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: None})

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result
    assert called["count"] == 0


def test_q_no_tool_context_skips_provenance_enforcement_like_every_other_teams_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mirrors every other Teams tool's own `tool_context: Optional[...] =
    None` contract -- a test/debug caller that never supplies one gets
    identical retrieval behavior either way, exactly like
    `teams_get_messages` already documents for itself.
    """
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=None)

    assert "error" not in result


@pytest.mark.parametrize(
    "chat_id,message_id,hosted_content_id",
    [("", "msg-1", "content-abc"), ("chat-1", "", "content-abc"), ("chat-1", "msg-1", "")],
)
def test_missing_required_ids_fail_safely_without_calling_the_gateway(
    monkeypatch: pytest.MonkeyPatch, chat_id: str, message_id: str, hosted_content_id: str
) -> None:
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    result = teams_get_hosted_content(chat_id, message_id, hosted_content_id, tool_context=_FakeToolContext())

    assert "error" in result
    assert called["count"] == 0


# --- R: no Power-Automate-specific transport fields leak ---------------------


def test_r_no_power_automate_transport_fields_in_result(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(
        monkeypatch,
        {
            "success": True,
            "requestId": "some-pa-request-id",
            "version": "1.0",
            "contentType": "image/png",
            "contentBase64": _VALID_PNG_BASE64,
        },
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" not in result
    assert set(result.keys()) == {
        "chat_id",
        "message_id",
        "hosted_content_id",
        "content_type",
        "size_bytes",
        "delivered_for_visual_reasoning",
    }
    assert "requestId" not in result
    assert "version" not in result
    assert "success" not in result


# ============================================================================
# FULL PROVENANCE BINDING corrective milestone -- section 8's own required
# matrix (A-E): (chat_id, message_id, hosted_content_id) as one composite
# identity, never message_id/hosted_content_id alone.
# ============================================================================


def test_a_correct_chat_message_content_tuple_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" not in result
    assert result["chat_id"] == "chat-1"


def test_b_same_message_and_content_but_wrong_chat_is_rejected_before_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE authoritative provenance test (instruction section 8): a real
    chat-1 discovery for (msg-1, content-abc) must NEVER authorize
    retrieval under a DIFFERENT chat_id -- rejected by this backend's own
    deterministic check, BEFORE Power Automate is ever called. A
    downstream Graph/Power-Automate-side rejection is explicitly NOT
    sufficient (never reached here).
    """
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    # Discovered under "chat-1" only.
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-2", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result
    assert called["count"] == 0, "Power Automate must never be invoked for a locally-rejected provenance failure"


def test_c_same_chat_and_content_but_wrong_message_is_rejected_before_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-2", "content-abc", tool_context=ctx)

    assert "error" in result
    assert called["count"] == 0


def test_d_unknown_hosted_content_id_is_rejected_before_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-never-discovered", tool_context=ctx)

    assert "error" in result
    assert called["count"] == 0


def test_e_rejected_provenance_calls_never_invoke_power_automate_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit, direct proof (distinct from B/C/D's own `called["count"]`
    assertions) that `PowerAutomateClient.get_hosted_content` itself is
    never even constructed/reached on a provenance rejection -- patches
    the CLIENT METHOD directly rather than the transport `requests.post`.
    """
    from backend.gateway.power_automate_client import PowerAutomateClient

    called = {"count": 0}

    def fake_get_hosted_content(self, *a, **k):
        called["count"] += 1
        raise AssertionError("PowerAutomateClient.get_hosted_content must never be called for a rejected provenance request")

    monkeypatch.setattr(PowerAutomateClient, "get_hosted_content", fake_get_hosted_content)
    # Correct chat/content, WRONG message -- and, separately, wrong chat --
    # both must short-circuit before this method is ever reached.
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result_wrong_message = teams_get_hosted_content("chat-1", "msg-999", "content-abc", tool_context=ctx)
    result_wrong_chat = teams_get_hosted_content("chat-999", "msg-1", "content-abc", tool_context=ctx)

    assert "error" in result_wrong_message
    assert "error" in result_wrong_chat
    assert called["count"] == 0


def test_same_hosted_content_id_scoped_independently_per_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hosted_content_id discovered under TWO different chats (a real
    possibility -- Teams ids are not guaranteed globally unique across
    chats) is independently valid under EACH chat it was actually
    discovered for -- this is not the same as a cross-chat replay (which
    B/C/E above prove is rejected); it proves the fix scopes identity per
    chat rather than merely blocklisting one specific wrong chat_id.
    """
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})
    state = {
        KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {
            "chat-1": {"msg-1": ["content-abc"]},
            "chat-2": {"msg-1": ["content-abc"]},
        }
    }
    ctx = _FakeToolContext(state)

    result_chat_1 = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)
    result_chat_2 = teams_get_hosted_content("chat-2", "msg-1", "content-abc", tool_context=ctx)

    assert "error" not in result_chat_1
    assert "error" not in result_chat_2


# ============================================================================
# Teams Image Vision corrective milestone -- a successful retrieval stashes
# the validated bytes for real Gemini multimodal delivery (section 6).
# ============================================================================


def test_successful_retrieval_stashes_bytes_for_multimodal_delivery_when_run_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.turn_context import bind_run_id, reset_run_id

    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    token = bind_run_id("run-xyz")
    try:
        result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" not in result
    assert result["delivered_for_visual_reasoning"] is True
    with hosted_content_vision_context._lock:  # noqa: SLF001
        pending = list(hosted_content_vision_context._pending.get("run-xyz", []))
    hosted_content_vision_context.discard_pending_hosted_content_image("run-xyz")
    assert len(pending) == 1
    image = pending[0]
    assert image.message_id == "msg-1"
    assert image.hosted_content_id == "content-abc"
    assert image.mime_type == "image/png"
    assert image.data == _VALID_PNG_BYTES


def test_delivered_for_visual_reasoning_false_when_no_run_is_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    """No `current_run_id()` -- there is no run to deliver the image into,
    so the tool must truthfully report it as NOT delivered, never `True`.
    """
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)

    assert "error" not in result
    assert result["delivered_for_visual_reasoning"] is False


def test_two_images_from_the_same_message_both_stash_and_both_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api.turn_context import bind_run_id, reset_run_id

    ctx = _FakeToolContext(
        {
            KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {
                "chat-1": {"msg-1": ["content-a", "content-b"]},
            }
        }
    )

    token = bind_run_id("run-two-images")
    try:
        _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})
        result_a = teams_get_hosted_content("chat-1", "msg-1", "content-a", tool_context=ctx)
        result_b = teams_get_hosted_content("chat-1", "msg-1", "content-b", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert result_a["delivered_for_visual_reasoning"] is True
    assert result_b["delivered_for_visual_reasoning"] is True
    with hosted_content_vision_context._lock:  # noqa: SLF001
        pending = list(hosted_content_vision_context._pending.get("run-two-images", []))
    hosted_content_vision_context.discard_pending_hosted_content_image("run-two-images")
    assert {image.hosted_content_id for image in pending} == {"content-a", "content-b"}


def test_a_second_retrieval_of_the_same_image_is_idempotent_not_duplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api.turn_context import bind_run_id, reset_run_id

    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    token = bind_run_id("run-dup")
    try:
        first = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)
        second = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert first["delivered_for_visual_reasoning"] is True
    assert second["delivered_for_visual_reasoning"] is True
    with hosted_content_vision_context._lock:  # noqa: SLF001
        pending = list(hosted_content_vision_context._pending.get("run-dup", []))
    hosted_content_vision_context.discard_pending_hosted_content_image("run-dup")
    assert len(pending) == 1


def test_failed_retrieval_never_stashes_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api.turn_context import bind_run_id, reset_run_id

    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/gif", "contentBase64": _VALID_PNG_BASE64})
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    token = bind_run_id("run-fail")
    try:
        result = teams_get_hosted_content("chat-1", "msg-1", "content-abc", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" in result
    with hosted_content_vision_context._lock:  # noqa: SLF001
        assert "run-fail" not in hosted_content_vision_context._pending


def test_rejected_provenance_never_stashes_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api.turn_context import bind_run_id, reset_run_id

    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["content-abc"]))

    token = bind_run_id("run-rejected")
    try:
        result = teams_get_hosted_content("chat-2", "msg-1", "content-abc", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" in result
    assert called["count"] == 0
    with hosted_content_vision_context._lock:  # noqa: SLF001
        assert "run-rejected" not in hosted_content_vision_context._pending


# ============================================================================
# Multiple Teams Hosted Images milestone -- section 12's own required matrix
# (C, D/E re-proven with three images, F, N).
# ============================================================================

import io as _io  # noqa: E402 -- deliberately grouped with this section's own fixtures.

from PIL import Image as _PILImage  # noqa: E402


def _make_real_image_bytes(fmt: str) -> bytes:
    """A genuinely Pillow-encoded image (never a hand-typed byte string) --
    mirrors test_attachments_validation.py's own `_make_image_bytes`
    technique, needed here because `validate_image_bytes` decodes the
    ACTUAL bytes, not merely the declared content type.
    """
    buffer = _io.BytesIO()
    _PILImage.new("RGB", (4, 4), color=(10, 20, 30)).save(buffer, format=fmt)
    return buffer.getvalue()


_REAL_JPEG_BYTES = _make_real_image_bytes("JPEG")
_REAL_JPEG_BASE64 = base64.b64encode(_REAL_JPEG_BYTES).decode("ascii")


def test_c_three_correct_provenance_triples_all_succeed(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _FakeToolContext(
        {KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {"chat-1": {"msg-1": ["ID_A", "ID_B", "ID_C"]}}}
    )
    _mock_gateway(monkeypatch, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    result_a = teams_get_hosted_content("chat-1", "msg-1", "ID_A", tool_context=ctx)
    result_b = teams_get_hosted_content("chat-1", "msg-1", "ID_B", tool_context=ctx)
    result_c = teams_get_hosted_content("chat-1", "msg-1", "ID_C", tool_context=ctx)

    assert "error" not in result_a
    assert "error" not in result_b
    assert "error" not in result_c


def test_d_wrong_chat_for_one_of_three_images_rejected_before_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """(ChatA, Message1, ImageA) and (ChatA, Message1, ImageB) allowed;
    (ChatB, Message1, ImageA) rejected locally -- section 4's own matrix,
    proven with a real multi-image discovery state.
    """
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext(
        {KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {"ChatA": {"Message1": ["ImageA", "ImageB"]}}}
    )

    result_allowed_a = teams_get_hosted_content("ChatA", "Message1", "ImageA", tool_context=ctx)
    result_allowed_b = teams_get_hosted_content("ChatA", "Message1", "ImageB", tool_context=ctx)
    result_rejected = teams_get_hosted_content("ChatB", "Message1", "ImageA", tool_context=ctx)

    assert "error" not in result_allowed_a
    assert "error" not in result_allowed_b
    assert "error" in result_rejected
    assert called["count"] == 2  # only the two ALLOWED calls ever reached the gateway


def test_e_wrong_message_for_one_of_three_images_rejected_before_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """(ChatA, Message1, ImageA) rejected under Message2 -- section 4's
    own matrix, proven with a real multi-image discovery state."""
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext(
        {KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {"ChatA": {"Message1": ["ImageA", "ImageB"]}}}
    )

    result_allowed = teams_get_hosted_content("ChatA", "Message1", "ImageA", tool_context=ctx)
    result_rejected = teams_get_hosted_content("ChatA", "Message2", "ImageA", tool_context=ctx)

    assert "error" not in result_allowed
    assert "error" in result_rejected
    assert called["count"] == 1


def test_f_mixed_png_and_jpeg_both_succeed_and_both_stash_with_correct_mime_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.turn_context import bind_run_id, reset_run_id

    responses = {
        "content-png": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "content-jpeg": {"success": True, "contentType": "image/jpeg", "contentBase64": _REAL_JPEG_BASE64},
    }

    def fake_post(url, json=None, **k):
        hosted_content_id = json.get("hostedContentId") if json else None
        return FakeResponse(200, responses[hosted_content_id])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext(
        {KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {"chat-1": {"msg-1": ["content-png", "content-jpeg"]}}}
    )

    token = bind_run_id("run-mixed-mime")
    try:
        result_png = teams_get_hosted_content("chat-1", "msg-1", "content-png", tool_context=ctx)
        result_jpeg = teams_get_hosted_content("chat-1", "msg-1", "content-jpeg", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" not in result_png
    assert "error" not in result_jpeg
    assert result_png["content_type"] == "image/png"
    assert result_jpeg["content_type"] == "image/jpeg"
    assert result_png["delivered_for_visual_reasoning"] is True
    assert result_jpeg["delivered_for_visual_reasoning"] is True
    with hosted_content_vision_context._lock:  # noqa: SLF001
        pending = list(hosted_content_vision_context._pending.get("run-mixed-mime", []))
    hosted_content_vision_context.discard_pending_hosted_content_image("run-mixed-mime")
    mime_types = {image.hosted_content_id: image.mime_type for image in pending}
    assert mime_types == {"content-png": "image/png", "content-jpeg": "image/jpeg"}


def test_n_one_corrupt_image_among_valid_images_has_deterministic_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Image 1 valid, image 2 corrupt (declared PNG but actually
    malformed), image 3 valid -- section 10's own example. The corrupt
    image fails safely and independently; the other two remain fully
    available for visual reasoning, never silently treated as reviewed,
    and the corrupt image's failure never bypasses provenance or leaks a
    raw error for the OTHER images.
    """
    from backend.api.turn_context import bind_run_id, reset_run_id

    responses = {
        "content-1": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "content-2": {"success": True, "contentType": "image/png", "contentBase64": "not-valid-base64!!!"},
        "content-3": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
    }

    def fake_post(url, json=None, **k):
        hosted_content_id = json.get("hostedContentId") if json else None
        return FakeResponse(200, responses[hosted_content_id])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext(
        {KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {"chat-1": {"msg-1": ["content-1", "content-2", "content-3"]}}}
    )

    token = bind_run_id("run-partial-failure")
    try:
        result_1 = teams_get_hosted_content("chat-1", "msg-1", "content-1", tool_context=ctx)
        result_2 = teams_get_hosted_content("chat-1", "msg-1", "content-2", tool_context=ctx)
        result_3 = teams_get_hosted_content("chat-1", "msg-1", "content-3", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" not in result_1
    assert "error" in result_2  # corrupt -- deterministic, safe failure, own SafeError shape only
    assert "error" not in result_3
    assert result_1["delivered_for_visual_reasoning"] is True
    assert result_3["delivered_for_visual_reasoning"] is True
    with hosted_content_vision_context._lock:  # noqa: SLF001
        pending = list(hosted_content_vision_context._pending.get("run-partial-failure", []))
    hosted_content_vision_context.discard_pending_hosted_content_image("run-partial-failure")
    # Exactly the two VALID images were queued -- the corrupt one never
    # reached the stash at all (it fails before that call site).
    assert {image.hosted_content_id for image in pending} == {"content-1", "content-3"}
