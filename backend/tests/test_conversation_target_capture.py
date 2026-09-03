"""Tests for backend/api/conversation_target_capture.py -- mirrors
test_source_reference.py's own TeamsSourceCapture tests in style.
"""
from __future__ import annotations

from backend.api.conversation_target_capture import ConversationTargetCapture
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse


def test_captures_a_successfully_declared_target() -> None:
    capture = ConversationTargetCapture()
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[FakeFunctionResponse("record_conversation_target", {"target": "current_thread"})],
        )
    )
    assert capture.target == "current_thread"


def test_ignores_a_rejected_declaration() -> None:
    capture = ConversationTargetCapture()
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "record_conversation_target", {"error": {"errorCode": "validation_error", "userMessage": "x"}}
                )
            ],
        )
    )
    assert capture.target is None


def test_ignores_partial_events() -> None:
    capture = ConversationTargetCapture()
    capture.observe(
        FakeEvent(
            final=False,
            partial=True,
            function_responses=[FakeFunctionResponse("record_conversation_target", {"target": "current_thread"})],
        )
    )
    assert capture.target is None


def test_ignores_other_tool_responses() -> None:
    capture = ConversationTargetCapture()
    capture.observe(
        FakeEvent(final=False, function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "ok"})])
    )
    assert capture.target is None


def test_last_successful_declaration_wins() -> None:
    capture = ConversationTargetCapture()
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("record_conversation_target", {"target": "selected_external_conversation"})
            ],
        )
    )
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[FakeFunctionResponse("record_conversation_target", {"target": "current_thread"})],
        )
    )
    assert capture.target == "current_thread"


def test_target_is_none_when_nothing_was_ever_declared() -> None:
    capture = ConversationTargetCapture()
    capture.observe(FakeEvent(text="hello", final=True))
    assert capture.target is None
