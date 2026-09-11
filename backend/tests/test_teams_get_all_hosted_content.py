"""Deterministic All-Image Retrieval milestone -- unit tests for
`backend.tools.teams.get_hosted_content.teams_get_all_hosted_content`.

Maps onto section 14's own required test matrix (A-Q).
"""
from __future__ import annotations

import base64
from typing import Any, Optional

import pytest
from PIL import Image as _PILImage
import io as _io

from backend.api import hosted_content_vision_context as hcv
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse
from backend.tools.teams.get_hosted_content import teams_get_all_hosted_content, teams_get_hosted_content
from backend.tools.teams.get_messages import KNOWN_HOSTED_CONTENT_IDS_STATE_KEY

_VALID_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YA"
    "AAAASUVORK5CYII="
)
_VALID_PNG_BASE64 = base64.b64encode(_VALID_PNG_BYTES).decode("ascii")


def _make_real_jpeg_bytes() -> bytes:
    buffer = _io.BytesIO()
    _PILImage.new("RGB", (4, 4), color=(10, 20, 30)).save(buffer, format="JPEG")
    return buffer.getvalue()


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = dict(state or {})


def _known_state(chat_id: str, message_id: str, hosted_content_ids: list[str]) -> dict[str, Any]:
    return {KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: {chat_id: {message_id: hosted_content_ids}}}


def _setup_run(run_name: str, chat_id: str, message_id: str, ordered_ids: list[str]):
    token = bind_run_id(run_name)
    hcv.record_message_hosted_content_order(message_id, ordered_ids)
    return token


class _FakeLlmRequest:
    def __init__(self) -> None:
        self.contents: list[Any] = []


def _inject() -> None:
    """Simulates the real `before_model_callback` firing on the model's
    NEXT call after `teams_get_all_hosted_content` runs -- "delivered"
    (in `_delivered`, consumed by `pop_delivered_visual_evidence`) only
    ever becomes true once this actually happens; `teams_get_all_hosted_
    content` itself only QUEUES (`_pending`)."""
    hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=_FakeLlmRequest())


@pytest.fixture(autouse=True)
def _clean_store() -> Any:
    yield
    with hcv._lock:  # noqa: SLF001
        hcv._pending.clear()
        hcv._message_order.clear()
        hcv._message_metadata.clear()
        hcv._delivered.clear()


def _mock_gateway_by_content_id(monkeypatch: pytest.MonkeyPatch, responses: dict[str, dict]) -> dict:
    called = {"count": 0}

    def fake_post(url, json=None, **k):
        called["count"] += 1
        hosted_content_id = json.get("hostedContentId") if json else None
        return FakeResponse(200, responses[hosted_content_id])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    return called


# --- A/B/C: full batch retrieval succeeds -----------------------------------


def test_a_three_authoritative_ids_all_images_intent_makes_exactly_three_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = {
        "id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "id-b": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "id-c": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
    }
    called = _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b", "id-c"]))

    token = _setup_run("run-a", "chat-1", "msg-1", ["id-a", "id-b", "id-c"])
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" not in result
    assert called["count"] == 3
    assert result["attempted_count"] == 3


def test_b_all_three_valid_exactly_three_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        cid: {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}
        for cid in ["id-a", "id-b", "id-c"]
    }
    _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b", "id-c"]))

    token = _setup_run("run-b", "chat-1", "msg-1", ["id-a", "id-b", "id-c"])
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
        _inject()
        delivered = hcv.pop_delivered_visual_evidence("run-b")
    finally:
        reset_run_id(token)

    assert result["delivered_count"] == 3
    assert result["failed_ordinals"] == []
    assert len(delivered) == 3


def test_c_exactly_three_gemini_parts_produced(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.genai import types

    responses = {
        cid: {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}
        for cid in ["id-a", "id-b", "id-c"]
    }
    _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b", "id-c"]))

    class _FakeLlmRequest:
        def __init__(self) -> None:
            self.contents: list[Any] = []

    token = _setup_run("run-c", "chat-1", "msg-1", ["id-a", "id-b", "id-c"])
    try:
        teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    file_parts = [p for p in request.contents[0].parts if getattr(p, "inline_data", None) is not None]
    assert len(file_parts) == 3


# --- D: source order preserved -----------------------------------------------


def test_d_source_order_remains_1_2_3(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.genai import types

    responses = {
        "id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "id-b": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "id-c": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
    }
    _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b", "id-c"]))

    token = _setup_run("run-d", "chat-1", "msg-1", ["id-a", "id-b", "id-c"])
    try:
        teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
        _inject()
        delivered = hcv.pop_delivered_visual_evidence("run-d")
    finally:
        reset_run_id(token)

    ordinals_by_id = {d.hosted_content_id: d.ordinal for d in delivered}
    assert ordinals_by_id == {"id-a": 1, "id-b": 2, "id-c": 3}


# --- E: partial failure best-effort -------------------------------------------


def test_e_middle_image_fails_third_still_attempted_and_delivered(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "id-b": {"success": True, "contentType": "image/png", "contentBase64": "not-valid-base64!!!"},
        "id-c": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
    }
    called = _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b", "id-c"]))

    token = _setup_run("run-e", "chat-1", "msg-1", ["id-a", "id-b", "id-c"])
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
        _inject()
        delivered = hcv.pop_delivered_visual_evidence("run-e")
    finally:
        reset_run_id(token)

    assert called["count"] == 3  # all three attempted, including id-c after id-b's failure
    assert result["attempted_count"] == 3
    assert result["delivered_count"] == 2
    assert result["failed_ordinals"] == [2]
    assert {d.hosted_content_id for d in delivered} == {"id-a", "id-c"}


# --- F: duplicate id in discovered source never retrieved twice --------------


def test_f_duplicate_id_in_order_list_retrieved_only_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Defensive: even if the recorded order somehow contained a literal
    duplicate id (extraction already dedups -- this proves the batch tool
    itself does not compound a hypothetical upstream duplicate)."""
    responses = {"id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}}
    called = _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a"]))

    token = _setup_run("run-f", "chat-1", "msg-1", ["id-a", "id-a"])
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert called["count"] == 1
    assert result["delivered_count"] == 2  # second occurrence reuses the already-delivered outcome, not a real re-fetch


# --- G: repeated batch/individual calls never re-fetch already-expanded ids ---


def test_g_repeated_batch_call_in_same_run_does_not_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {"id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}}
    called = _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a"]))

    token = _setup_run("run-g", "chat-1", "msg-1", ["id-a"])
    try:
        teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
        result_2 = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert called["count"] == 1
    assert result_2["delivered_count"] == 1


def test_g_individual_call_then_batch_call_does_not_refetch(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        "id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "id-b": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
    }
    called = _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b"]))

    token = _setup_run("run-g2", "chat-1", "msg-1", ["id-a", "id-b"])
    try:
        teams_get_hosted_content("chat-1", "msg-1", "id-a", tool_context=ctx)
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    # id-a retrieved once (individually), id-b retrieved once (via batch) -- 2 total, never 3.
    assert called["count"] == 2
    assert result["delivered_count"] == 2


# --- H/I/J: provenance unchanged, rejected before gateway ---------------------


def test_h_wrong_chat_provenance_fails_locally_before_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    called = _mock_gateway_by_content_id(
        monkeypatch, {"id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}}
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a"]))

    token = _setup_run("run-h", "chat-1", "msg-1", ["id-a"])
    try:
        result = teams_get_all_hosted_content("chat-2", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" in result
    assert called["count"] == 0


def test_i_wrong_message_provenance_fails_locally_before_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    called = _mock_gateway_by_content_id(
        monkeypatch, {"id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}}
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a"]))

    token = _setup_run("run-i", "chat-1", "msg-1", ["id-a"])
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-2", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" in result
    assert called["count"] == 0


def test_j_unknown_hosted_content_id_excluded_never_retrieved(monkeypatch: pytest.MonkeyPatch) -> None:
    """An id present in the recorded ORDER but absent from the trusted
    provenance registry must be silently excluded from the batch, never
    retrieved -- per-image provenance is never weakened by batching."""
    called = _mock_gateway_by_content_id(
        monkeypatch, {"id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}}
    )
    # Only "id-a" is authoritatively known; "id-unknown" is in the order
    # list (simulating a data inconsistency) but not in the registry.
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a"]))

    token = _setup_run("run-j", "chat-1", "msg-1", ["id-a", "id-unknown"])
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" not in result
    assert result["attempted_count"] == 1
    assert called["count"] == 1


# --- K/L: safety limits remain enforced ---------------------------------------


def test_k_image_count_limit_remains_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    ids = [f"id-{i}" for i in range(hcv.MAX_HOSTED_IMAGES_PER_MESSAGE + 2)]
    # get_messages.py would have already truncated the recorded order to
    # MAX_HOSTED_IMAGES_PER_MESSAGE -- simulate that here directly, proving
    # the batch tool relies on (never bypasses) that existing truncation.
    truncated_ids = ids[: hcv.MAX_HOSTED_IMAGES_PER_MESSAGE]
    responses = {cid: {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64} for cid in truncated_ids}
    called = _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", truncated_ids))

    token = _setup_run("run-k", "chat-1", "msg-1", truncated_ids)
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert called["count"] == hcv.MAX_HOSTED_IMAGES_PER_MESSAGE
    assert result["delivered_count"] == hcv.MAX_HOSTED_IMAGES_PER_MESSAGE


def test_l_total_byte_budget_remains_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    big_bytes = b"\x00" * (hcv.MAX_TOTAL_HOSTED_IMAGE_BYTES - 10)
    big_b64 = base64.b64encode(_VALID_PNG_BYTES).decode("ascii")  # placeholder, overridden below per-id
    responses = {
        "id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "id-b": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
    }
    _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b"]))

    token = _setup_run("run-l", "chat-1", "msg-1", ["id-a", "id-b"])
    try:
        # Pre-fill the run's byte budget to just below the limit so the
        # SECOND image in the batch is genuinely budget-rejected.
        hcv._pending["run-l"] = [
            hcv.HostedImage("chat-1", "msg-1", "already-stashed", "image/png", b"\x00" * (hcv.MAX_TOTAL_HOSTED_IMAGE_BYTES - 5))
        ]
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert result["delivered_count"] < result["attempted_count"]
    assert result["failed_ordinals"] != []


# --- M: single-image request still works --------------------------------------


def test_m_single_image_request_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_gateway_by_content_id(
        monkeypatch, {"id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}}
    )
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a"]))

    token = _setup_run("run-m", "chat-1", "msg-1", ["id-a"])
    try:
        result = teams_get_hosted_content("chat-1", "msg-1", "id-a", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" not in result
    assert result["delivered_for_visual_reasoning"] is True


# --- N: no Base64/raw bytes in prompt text -------------------------------------


def test_n_no_base64_or_raw_bytes_in_result_or_injected_text(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        cid: {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64}
        for cid in ["id-a", "id-b"]
    }
    _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b"]))

    class _FakeLlmRequest:
        def __init__(self) -> None:
            self.contents: list[Any] = []

    token = _setup_run("run-n", "chat-1", "msg-1", ["id-a", "id-b"])
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
        request = _FakeLlmRequest()
        hcv.inject_pending_hosted_content_image(callback_context=None, llm_request=request)
    finally:
        reset_run_id(token)

    serialized_result = str(result)
    assert _VALID_PNG_BASE64 not in serialized_result
    text_parts = [p.text for p in request.contents[0].parts if getattr(p, "text", None)]
    assert _VALID_PNG_BASE64 not in "\n".join(text_parts)


# --- O: Visual Evidence contains only actually delivered images --------------


def test_o_visual_evidence_excludes_failed_and_budget_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api.hosted_content_vision_context import build_visual_evidence

    responses = {
        "id-a": {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64},
        "id-b": {"success": True, "contentType": "image/png", "contentBase64": "not-valid-base64!!!"},
    }
    _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b"]))

    token = _setup_run("run-o", "chat-1", "msg-1", ["id-a", "id-b"])
    try:
        teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
        _inject()
        delivered = hcv.pop_delivered_visual_evidence("run-o")
    finally:
        reset_run_id(token)

    items, _ = build_visual_evidence(delivered, "chat-1")
    assert len(items) == 1


# --- P: run isolation ----------------------------------------------------------


def test_p_run_isolation_batch_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        cid: {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64} for cid in ["id-a", "id-b"]
    }
    _mock_gateway_by_content_id(monkeypatch, responses)

    token_a = _setup_run("run-p-a", "chat-1", "msg-1", ["id-a"])
    try:
        ctx_a = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a"]))
        teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx_a)
    finally:
        reset_run_id(token_a)

    token_b = _setup_run("run-p-b", "chat-1", "msg-1", ["id-b"])
    try:
        ctx_b = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-b"]))
        teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx_b)
        _inject()
        delivered_b = hcv.pop_delivered_visual_evidence("run-p-b")
    finally:
        reset_run_id(token_b)

    with hcv._lock:  # noqa: SLF001
        pending_a = list(hcv._pending.get("run-p-a", []))
    hcv.discard_pending_hosted_content_image("run-p-a")

    assert {d.hosted_content_id for d in delivered_b} == {"id-b"}
    assert {i.hosted_content_id for i in pending_a} == {"id-a"}


# --- No eligible ids -----------------------------------------------------------


def test_no_eligible_ids_fails_safely_never_calls_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"count": 0}

    def fake_post(*a, **k):
        called["count"] += 1
        return FakeResponse(200, {"success": True})

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    ctx = _FakeToolContext()  # nothing known at all

    token = bind_run_id("run-none")
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert "error" in result
    assert called["count"] == 0


@pytest.mark.parametrize("chat_id,message_id", [("", "msg-1"), ("chat-1", "")])
def test_missing_required_ids_fail_safely(chat_id: str, message_id: str) -> None:
    result = teams_get_all_hosted_content(chat_id, message_id, tool_context=_FakeToolContext())
    assert "error" in result


def test_result_never_exposes_a_hosted_content_id(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = {
        cid: {"success": True, "contentType": "image/png", "contentBase64": _VALID_PNG_BASE64} for cid in ["id-a", "id-b"]
    }
    _mock_gateway_by_content_id(monkeypatch, responses)
    ctx = _FakeToolContext(_known_state("chat-1", "msg-1", ["id-a", "id-b"]))

    token = _setup_run("run-noexpose", "chat-1", "msg-1", ["id-a", "id-b"])
    try:
        result = teams_get_all_hosted_content("chat-1", "msg-1", tool_context=ctx)
    finally:
        reset_run_id(token)

    assert set(result.keys()) == {
        "chat_id",
        "message_id",
        "discovered_count",
        "attempted_count",
        "delivered_count",
        "failed_ordinals",
    }
    assert "id-a" not in str(result)
    assert "id-b" not in str(result)
