"""Teams Rich Content milestone (single-image scope) -- integration tests
for `teams_get_messages`'s own `hosted_content_ids` population and its
non-regression guarantees (test matrix cases M, N, O, P, R).
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse, message
from backend.tools.teams.get_messages import KNOWN_HOSTED_CONTENT_IDS_STATE_KEY, teams_get_messages

_HOSTED_URL = (
    "https://graph.microsoft.com/beta/chats/19:abc@thread.v2/messages/{mid}/hostedContents/{cid}/$value"
)


class _FakeToolContext:
    def __init__(self, state: Optional[dict[str, Any]] = None) -> None:
        self.state = dict(state or {})


def _image_message(msg_id: str, content_id: str = "ABC123", author: str = "Alex") -> dict[str, Any]:
    return {
        "id": msg_id,
        "createdDateTime": "2026-09-01T09:00:00Z",
        "senderName": author,
        "contentType": "html",
        "content": f'<p><img src="{_HOSTED_URL.format(mid=msg_id, cid=content_id)}"></p>',
    }


# --- Basic population ---------------------------------------------------------


def test_hosted_content_ids_populated_for_an_image_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m1")]))

    result = teams_get_messages(chat_id="c1")

    assert result["messages"][0]["hosted_content_ids"] == ["ABC123"]


def test_hosted_content_ids_empty_for_a_text_only_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [message("m1", "Alex", "just text", "2026-09-01T09:00:00Z")]),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["messages"][0]["hosted_content_ids"] == []


# --- M: existing shape/fields must remain unchanged ---------------------------


def test_m_existing_message_shape_unaffected_by_the_schema_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [message("m1", "Alex", "Gateway is back up.", "2026-08-30T09:00:00Z")]),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["messages"] == [
        {
            "id": "m1",
            "author": "Alex",
            "text": "Gateway is back up.",
            "sent_at": "2026-08-30T09:00:00Z",
            "raw_content": "Gateway is back up.",
            "content_type": "text",
            "message_references": [],
            "hosted_content_ids": [],
            "hosted_content_truncated": False,
        }
    ]


# --- N: messageReference non-regression ----------------------------------------


def test_n_message_reference_attachment_never_becomes_hosted_content(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    reference_payload = json.dumps({"messageId": "ref-1", "messagePreview": "quoted text"})
    entry = {
        "id": "m2",
        "createdDateTime": "2026-08-31T13:31:00Z",
        "senderName": "Current User",
        "contentType": "html",
        "content": '<attachment id="ref-1"></attachment><p>Is this it?</p>',
        "attachments": [{"id": "ref-1", "contentType": "messageReference", "content": reference_payload}],
    }
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [entry]))

    result = teams_get_messages(chat_id="c1")

    msg = result["messages"][0]
    assert msg["hosted_content_ids"] == []
    assert msg["message_references"][0]["message_id"] == "ref-1"


def test_n_image_and_message_reference_coexist_correctly_in_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """A message may legitimately carry BOTH a reply AND an inline image --
    the two mechanisms must never interfere with each other.
    """
    import json

    reference_payload = json.dumps({"messageId": "ref-1", "messagePreview": "quoted text"})
    entry = {
        "id": "m3",
        "createdDateTime": "2026-08-31T13:31:00Z",
        "senderName": "Current User",
        "contentType": "html",
        "content": (
            f'<attachment id="ref-1"></attachment><p>Look at this:</p>'
            f'<img src="{_HOSTED_URL.format(mid="m3", cid="XYZ")}">'
        ),
        "attachments": [{"id": "ref-1", "contentType": "messageReference", "content": reference_payload}],
    }
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [entry]))

    result = teams_get_messages(chat_id="c1")

    msg = result["messages"][0]
    assert msg["hosted_content_ids"] == ["XYZ"]
    assert msg["message_references"][0]["message_id"] == "ref-1"


# --- O: system-event non-regression --------------------------------------------


def test_o_system_event_content_still_excluded_even_with_a_stray_img_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = {
        "id": "m4",
        "createdDateTime": "2026-08-31T13:31:00Z",
        "senderName": None,
        "contentType": "html",
        "content": '<systemEventMessage/>',
    }
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [entry]))

    result = teams_get_messages(chat_id="c1")

    assert result["messages"] == []
    assert result["filtered_system_event_count"] == 1


def test_o_image_only_message_is_not_treated_as_empty_or_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """An image-only message (no accompanying text) must still survive
    system/event filtering -- normalized text is the non-empty
    "[Attachment]" marker, never truly empty (see html_text.py).
    """
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m5")]))

    result = teams_get_messages(chat_id="c1")

    assert result["filtered_system_event_count"] == 0
    assert len(result["messages"]) == 1
    assert result["messages"][0]["hosted_content_ids"] == ["ABC123"]


# --- P: pagination/time-range/coverage non-regression --------------------------


def test_p_pagination_still_works_with_hosted_content_present_on_some_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    page1 = [_image_message(f"p1-{i}") for i in range(50)]  # full page -- triggers another fetch
    page2 = [message("older-1", "Alex", "older text", "2026-08-29T09:00:00Z")]
    calls: list[Optional[str]] = []

    def fake_post(url, json, timeout):
        calls.append(json.get("before"))
        if json.get("before") is None:
            return FakeResponse(200, page1)
        return FakeResponse(200, page2)

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    result = teams_get_messages(chat_id="c1", max_messages=200)

    assert len(calls) == 2  # paginated exactly as before -- unaffected by hosted-content parsing
    assert result["retrieved_count"] == 51
    assert result["messages"][-1]["hosted_content_ids"] == ["ABC123"]  # newest (image) message, chronologically last
    assert result["messages"][0]["hosted_content_ids"] == []  # oldest (plain text) message


def test_p_coverage_status_unaffected_by_hosted_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m1")]))

    result = teams_get_messages(chat_id="c1")

    assert result["coverage"]["status"] == "complete"


# --- Known-hosted-content-ids state (provenance registry) ----------------------


def test_known_hosted_content_ids_state_populated_for_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m1")]))
    ctx = _FakeToolContext()

    teams_get_messages(chat_id="c1", tool_context=ctx)

    # FULL PROVENANCE BINDING corrective pass: keyed by chat_id first, then
    # message_id -- see get_messages.py's own KNOWN_HOSTED_CONTENT_IDS_
    # STATE_KEY docstring.
    assert ctx.state[KNOWN_HOSTED_CONTENT_IDS_STATE_KEY] == {"c1": {"m1": ["ABC123"]}}


def test_known_hosted_content_ids_accumulates_across_calls_within_a_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _FakeToolContext()

    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m1")]))
    teams_get_messages(chat_id="c1", tool_context=ctx)

    monkeypatch.setattr(
        pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m2", content_id="DEF456")])
    )
    teams_get_messages(chat_id="c1", tool_context=ctx)

    assert ctx.state[KNOWN_HOSTED_CONTENT_IDS_STATE_KEY] == {"c1": {"m1": ["ABC123"], "m2": ["DEF456"]}}


def test_known_hosted_content_ids_scoped_independently_per_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """FULL PROVENANCE BINDING corrective pass: a second chat's own
    retrieval must never merge into, or be shadowed by, a different chat's
    entry -- each chat_id gets its own independent inner message map, even
    when message ids happen to collide across chats.
    """
    ctx = _FakeToolContext()

    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m1")]))
    teams_get_messages(chat_id="c1", tool_context=ctx)

    monkeypatch.setattr(
        pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m1", content_id="DEF456")])
    )
    teams_get_messages(chat_id="c2", tool_context=ctx)

    assert ctx.state[KNOWN_HOSTED_CONTENT_IDS_STATE_KEY] == {
        "c1": {"m1": ["ABC123"]},
        "c2": {"m1": ["DEF456"]},
    }


def test_rewind_cleared_known_hosted_content_ids_does_not_crash_next_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    """D1-class hazard, hardened proactively for this new key too: a
    rewind leaves the key present with value `None`.
    """
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m9")]))
    ctx = _FakeToolContext({KNOWN_HOSTED_CONTENT_IDS_STATE_KEY: None})

    result = teams_get_messages(chat_id="c1", tool_context=ctx)

    assert "error" not in result
    assert ctx.state[KNOWN_HOSTED_CONTENT_IDS_STATE_KEY] == {"c1": {"m9": ["ABC123"]}}


# --- R: no Power-Automate transport fields leak into TeamsMessage --------------


def test_r_no_power_automate_fields_in_the_message_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_image_message("m1")]))

    result = teams_get_messages(chat_id="c1")

    msg_keys = set(result["messages"][0].keys())
    assert msg_keys == {
        "id",
        "author",
        "text",
        "sent_at",
        "raw_content",
        "content_type",
        "message_references",
        "hosted_content_ids",
        "hosted_content_truncated",
    }
    assert "contentBase64" not in result["messages"][0]
    assert "hostedContents" not in result["messages"][0]


# ============================================================================
# Multiple Teams Hosted Images milestone -- image-count limit (section 9/
# 12.L) and canonical-order recording (section 3/7).
# ============================================================================


def _multi_image_message(msg_id: str, content_ids: list[str], author: str = "Alex") -> dict[str, Any]:
    imgs = "".join(f'<p><img src="{_HOSTED_URL.format(mid=msg_id, cid=cid)}"></p>' for cid in content_ids)
    return {
        "id": msg_id,
        "createdDateTime": "2026-09-01T09:00:00Z",
        "senderName": author,
        "contentType": "html",
        "content": imgs,
    }


def test_multiple_images_extracted_in_true_source_order(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_multi_image_message("m1", ["ID_A", "ID_B", "ID_C"])]),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["messages"][0]["hosted_content_ids"] == ["ID_A", "ID_B", "ID_C"]
    assert result["messages"][0]["hosted_content_truncated"] is False


def test_l_message_with_more_images_than_the_limit_is_truncated_deterministically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Section 9's own requirement: process only the deterministic allowed
    prefix, and truthfully expose that more existed -- never silently
    process an unbounded number, never claim all were reviewed.
    """
    from backend.api.hosted_content_vision_context import MAX_HOSTED_IMAGES_PER_MESSAGE

    all_ids = [f"ID_{i}" for i in range(MAX_HOSTED_IMAGES_PER_MESSAGE + 3)]
    monkeypatch.setattr(
        pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_multi_image_message("m1", all_ids)])
    )

    result = teams_get_messages(chat_id="c1")

    msg = result["messages"][0]
    assert msg["hosted_content_ids"] == all_ids[:MAX_HOSTED_IMAGES_PER_MESSAGE]
    assert msg["hosted_content_truncated"] is True
    assert len(msg["hosted_content_ids"]) == MAX_HOSTED_IMAGES_PER_MESSAGE


def test_message_with_exactly_the_limit_is_not_marked_truncated(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api.hosted_content_vision_context import MAX_HOSTED_IMAGES_PER_MESSAGE

    all_ids = [f"ID_{i}" for i in range(MAX_HOSTED_IMAGES_PER_MESSAGE)]
    monkeypatch.setattr(
        pac_module.requests, "post", lambda *a, **k: FakeResponse(200, [_multi_image_message("m1", all_ids)])
    )

    result = teams_get_messages(chat_id="c1")

    assert result["messages"][0]["hosted_content_ids"] == all_ids
    assert result["messages"][0]["hosted_content_truncated"] is False


def test_hosted_content_order_is_recorded_for_downstream_gemini_injection_ordering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Integration proof: `teams_get_messages` actually forwards each
    message's own true HTML order into `hosted_content_vision_context`'s
    run-scoped order map -- the mechanism `inject_pending_hosted_content_
    image` later relies on to sort Gemini `Part`s correctly regardless of
    tool-call completion order (see that module's own docstring).
    """
    from backend.api import hosted_content_vision_context as hcv
    from backend.api.turn_context import bind_run_id, reset_run_id

    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_multi_image_message("m1", ["ID_A", "ID_B", "ID_C"])]),
    )

    token = bind_run_id("run-order-integration")
    try:
        teams_get_messages(chat_id="c1", tool_context=_FakeToolContext())
        with hcv._lock:  # noqa: SLF001
            order_map = dict(hcv._message_order.get("run-order-integration", {}))
    finally:
        reset_run_id(token)
        hcv.discard_pending_hosted_content_image("run-order-integration")

    assert order_map == {"m1": ["ID_A", "ID_B", "ID_C"]}


def test_hosted_content_order_not_recorded_for_a_text_only_message(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.api import hosted_content_vision_context as hcv
    from backend.api.turn_context import bind_run_id, reset_run_id

    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [message("m1", "Alex", "just text", "2026-09-01T09:00:00Z")]),
    )

    token = bind_run_id("run-no-images")
    try:
        teams_get_messages(chat_id="c1", tool_context=_FakeToolContext())
        with hcv._lock:  # noqa: SLF001
            order_map = dict(hcv._message_order.get("run-no-images", {}))
    finally:
        reset_run_id(token)
        hcv.discard_pending_hosted_content_image("run-no-images")

    assert order_map == {}
