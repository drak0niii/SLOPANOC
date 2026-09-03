"""Unit tests for the teams_get_messages tool.

The mocked gateway response defaults to a bare top-level JSON array (the
live gateway's proven shape, see gateway/power_automate_client.py). See
the shape-specific tests below for the still-supported wrapped shapes and
malformed responses.
"""
from __future__ import annotations

import pytest

from backend.api.turn_context import bind_run_id, pop_message_texts, reset_run_id
from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse, message
from backend.tools.teams.get_messages import teams_get_messages


def test_get_messages_receives_the_resolved_chat_id(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def fake_post(url, json, timeout):
        captured["chat_id"] = json.get("chatId")
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)

    teams_get_messages(chat_id="resolved-chat-id-123")

    assert captured["chat_id"] == "resolved-chat-id-123"


def test_messages_are_normalized_with_provenance_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [message("m1", "Alex", "Gateway is back up.", "2026-08-30T09:00:00Z")],
        ),
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
        }
    ]


def test_html_content_is_normalized_while_raw_content_is_preserved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The requirement this fixes: the live gateway returns raw Teams HTML
    (e.g. `<p>text&nbsp;</p>`, `<emoji>`, `<attachment>`) in `content` --
    `text` must be clean/readable, while `raw_content`/`content_type`
    retain exactly what the gateway sent, and no other field is lost.
    """
    raw_html = (
        '<p>Gateway is back up&nbsp;<emoji alt="✅"></emoji></p>'
        '<p>See details: <attachment id="a1"></attachment></p>'
    )
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                {
                    "id": "m1",
                    "createdDateTime": "2026-08-30T09:00:00Z",
                    "lastModifiedDateTime": "2026-08-30T09:00:00Z",
                    "senderName": "Alex",
                    "senderId": "user-alex",
                    "contentType": "html",
                    "content": raw_html,
                    "webUrl": "https://teams.microsoft.com/l/message/m1",
                }
            ],
        ),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["messages"] == [
        {
            "id": "m1",
            "author": "Alex",
            "text": "Gateway is back up ✅\nSee details: [Attachment]",
            "sent_at": "2026-08-30T09:00:00Z",
            "raw_content": raw_html,
            "content_type": "html",
            "message_references": [],
        }
    ]


def test_no_loss_of_id_author_timestamp_or_raw_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleaning `text` must never come at the cost of any provenance
    field -- id, author, sent_at, and the untouched raw_content must all
    still be present and correct.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                {
                    "id": "msg-provenance-1",
                    "createdDateTime": "2026-08-30T09:15:00Z",
                    "senderName": "Priya",
                    "contentType": "html",
                    "content": "<p>All good&nbsp;here</p>",
                }
            ],
        ),
    )

    result = teams_get_messages(chat_id="c1")
    msg = result["messages"][0]

    assert msg["id"] == "msg-provenance-1"
    assert msg["author"] == "Priya"
    assert msg["sent_at"] == "2026-08-30T09:15:00Z"
    assert msg["raw_content"] == "<p>All good&nbsp;here</p>"
    assert msg["text"] == "All good here"


def test_empty_chat_returns_empty_messages_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, []))

    result = teams_get_messages(chat_id="c1")

    assert result["messages"] == []
    assert "error" not in result


def test_gateway_error_is_reported_as_safe_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(500))

    result = teams_get_messages(chat_id="c1")

    assert result["error"]["errorCode"] == "run_failure"


def test_blank_chat_id_is_a_validation_error() -> None:
    result = teams_get_messages(chat_id="  ")

    assert result["error"]["errorCode"] == "validation_error"


def test_summary_cannot_be_produced_without_retrieved_messages() -> None:
    """Structural half of the grounding guarantee (docs/AGENT_CONTRACT.md
    #10, docs/TEAMS_TOOL_CONTRACT.md #9). The automated suite cannot
    invoke the real LLM, so it cannot directly prove incident_manager
    never hallucinates a summary -- that half is enforced by prompt
    instructions (backend/agents/incident_manager/prompts.py) and the
    `output_schema` contract, and must be validated by manual/live testing
    once model credentials are available.

    What *is* provable here, deterministically: the tool layer -- the
    only place Teams content enters the system -- has no `summary`/`text`
    field of its own to leak fabricated content through, and an empty
    `messages` list is returned as an honest, unambiguous empty result,
    never substituted with placeholder text.
    """
    from backend.tools.teams.coverage import build_coverage
    from backend.tools.teams.schemas import TeamsGetMessagesResult

    assert "summary" not in TeamsGetMessagesResult.model_fields
    assert "text" not in TeamsGetMessagesResult.model_fields

    empty_result = TeamsGetMessagesResult(
        chat_id="c1",
        messages=[],
        coverage=build_coverage(
            requested_from=None,
            requested_to=None,
            range_fully_covered=True,
            truncated=False,
            retrieved_count=0,
            oldest_retrieved_at=None,
            newest_retrieved_at=None,
        ),
    )
    assert empty_result.messages == []


def test_raw_top_level_array_is_the_accepted_success_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This is the live gateway's actual, proven response shape for
    `teams.getMessages`: a bare JSON array, each item using `id`/
    `senderName`/`content`/`createdDateTime` (plus fields this tool
    doesn't model: `lastModifiedDateTime`, `senderId`, `contentType`,
    `webUrl`) -- not a `{"messages": [...]}` envelope.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                {
                    "id": "m1",
                    "createdDateTime": "2026-08-30T09:00:00Z",
                    "lastModifiedDateTime": "2026-08-30T09:00:00Z",
                    "senderName": "Alex",
                    "senderId": "user-alex",
                    "contentType": "text",
                    "content": "Gateway is back up.",
                    "webUrl": "https://teams.microsoft.com/l/message/m1",
                }
            ],
        ),
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
        }
    ]


def test_legacy_wrapped_messages_envelope_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward/backward compatibility: a `{"messages": [...]}` envelope
    (the shape an earlier, unverified draft of this tool assumed) is
    still accepted if a gateway ever sends it.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, {"messages": [message("m1", "Alex", "hi", "2026-08-30T09:00:00Z")]}
        ),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["messages"][0]["id"] == "m1"


def test_success_data_envelope_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward compatibility: a generic `{"success": true, "data": [...]}`
    envelope is accepted if a gateway ever sends one.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            {"success": True, "data": [message("m1", "Alex", "hi", "2026-08-30T09:00:00Z")]},
        ),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["messages"][0]["id"] == "m1"


def test_success_false_envelope_is_a_run_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"success": False, "data": []}),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["error"]["errorCode"] == "run_failure"


def test_malformed_response_shape_is_an_internal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither a bare array nor a recognized envelope -- must fail closed
    with a SafeError, never be silently treated as zero messages.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, {"unexpected": "shape"}),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["error"]["errorCode"] == "internal_error"


# --- Snippet-authenticity fix: forwarding retrieved text via turn_context --


def test_retrieved_message_text_is_forwarded_to_turn_context(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [message("m1", "Alex", "Gateway is back up.", "2026-08-30T09:00:00Z")]),
    )

    token = bind_run_id("run-forward-1")
    try:
        teams_get_messages(chat_id="c1")
    finally:
        reset_run_id(token)

    assert pop_message_texts("run-forward-1") == {"m1": "Gateway is back up."}


def test_nothing_is_forwarded_outside_a_bound_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A safe no-op when called outside chat_service.py's own turn
    lifecycle (e.g. `adk run`, or a test that never bound a run_id) --
    never raises."""
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [message("m1", "Alex", "hi", "2026-08-30T09:00:00Z")]),
    )

    result = teams_get_messages(chat_id="c1")  # no bind_run_id at all

    assert "error" not in result  # the actual retrieval is unaffected


def test_system_event_and_content_free_messages_are_never_forwarded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only messages that survive `teams_get_messages`' own filtering
    (already-excluded system/event/content-free entries) are ever
    candidates for a displayed snippet -- confirms the forwarded map is
    built from the SAME final, filtered `messages` this tool returns, not
    the raw unfiltered gateway response."""
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                message("m1", "Alex", "A real message.", "2026-08-30T09:00:00Z"),
                {
                    "id": "m2",
                    "createdDateTime": "2026-08-30T09:01:00Z",
                    "senderName": "",
                    "contentType": "systemEventMessage",
                    "content": "",
                },
            ],
        ),
    )

    token = bind_run_id("run-forward-2")
    try:
        result = teams_get_messages(chat_id="c1")
    finally:
        reset_run_id(token)

    forwarded = pop_message_texts("run-forward-2")
    assert forwarded == {"m1": "A real message."}
    assert "m2" not in forwarded
    assert [m["id"] for m in result["messages"]] == ["m1"]


def test_message_text_accumulates_across_multiple_calls_in_the_same_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            FakeResponse(200, [message("m1", "Alex", "First page.", "2026-08-30T09:00:00Z")]),
            FakeResponse(200, [message("m2", "Priya", "Second page.", "2026-08-29T09:00:00Z")]),
        ]
    )
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: next(responses))

    token = bind_run_id("run-forward-3")
    try:
        teams_get_messages(chat_id="c1")
        teams_get_messages(chat_id="c1")
    finally:
        reset_run_id(token)

    assert pop_message_texts("run-forward-3") == {"m1": "First page.", "m2": "Second page."}


def test_malformed_item_within_a_valid_array_is_an_internal_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shape-level validation passing must not weaken per-item validation:
    an entry missing the required `createdDateTime` field still fails
    closed.
    """
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [{"id": "m1", "senderName": "Alex"}]),
    )

    result = teams_get_messages(chat_id="c1")

    assert result["error"]["errorCode"] == "internal_error"
