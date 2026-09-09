"""A5 final corrective pass: focused tests for
backend/agents/incident_manager/evidence.py's new troubleshooting-
guidance capture-and-render step, wired into
`enforce_incident_manager_response_integrity`.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from backend.agents.incident_manager.evidence import (
    _capture_and_render_troubleshooting_guidance,
    enforce_incident_manager_response_integrity,
)
from backend.api.troubleshooting_guidance_context import pop_troubleshooting_guidance


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.parts = parts


class _FakeEvent:
    def __init__(self, author: str, text: Optional[str] = None) -> None:
        self.author = author
        self.content = _FakeContent([_FakePart(text)]) if text is not None else None


class _FakeSession:
    def __init__(self, events: list[_FakeEvent]) -> None:
        self.events = events


class _FakeCallbackContext:
    def __init__(self, events: list[_FakeEvent], state: Optional[dict[str, Any]] = None) -> None:
        self.session = _FakeSession(events)
        self.state = dict(state or {})


def _next_step_response(**guidance_overrides: object) -> str:
    guidance = {
        "interaction_mode": "next_step",
        "interpretation": "The alarm is active.",
        "next_action": "Check the alarm status.",
        "command": "alt",
        "evidence_requested": "Paste the output here.",
        "full_procedure_steps": [],
    }
    guidance.update(guidance_overrides)
    return json.dumps(
        {
            "outcome": "ok",
            "summary": "1. Check alarm.\n2. Restart if active.\n3. Raise a ticket.",
            "evidence": [],
            "candidate_titles": [],
            "detail": None,
            "troubleshooting_guidance": guidance,
        }
    )


def _ordinary_response() -> str:
    return json.dumps(
        {
            "outcome": "ok",
            "summary": "Here is a plain answer with no troubleshooting guidance.",
            "evidence": [],
            "candidate_titles": [],
            "detail": None,
        }
    )


# --- pure function: _capture_and_render_troubleshooting_guidance --------


def test_no_change_when_no_guidance_present() -> None:
    assert _capture_and_render_troubleshooting_guidance(_ordinary_response()) is None


def test_no_change_for_unparseable_text() -> None:
    assert _capture_and_render_troubleshooting_guidance("not json at all") is None


def test_rewrites_summary_deterministically_when_guidance_present() -> None:
    corrected = _capture_and_render_troubleshooting_guidance(_next_step_response())
    assert corrected is not None
    payload = json.loads(corrected)
    assert payload["summary"] == "The alarm is active.\n\nCheck the alarm status.\n\nRun:\n\nalt\n\nPaste the output here."
    # The original multi-step summary is gone -- never a trace of it.
    assert "Restart if active" not in payload["summary"]
    assert "Raise a ticket" not in payload["summary"]


def test_malformed_guidance_treated_as_absent() -> None:
    text = json.dumps({"outcome": "ok", "summary": "x", "troubleshooting_guidance": {"interaction_mode": "not-a-real-mode"}})
    assert _capture_and_render_troubleshooting_guidance(text) is None


# --- full callback wiring -------------------------------------------------


@pytest.mark.asyncio
async def test_callback_overrides_summary_when_guidance_present() -> None:
    ctx = _FakeCallbackContext(events=[_FakeEvent("incident_manager", _next_step_response())])
    result = await enforce_incident_manager_response_integrity(ctx)
    assert result is not None
    corrected = json.loads(result.parts[0].text)
    assert corrected["summary"] == "The alarm is active.\n\nCheck the alarm status.\n\nRun:\n\nalt\n\nPaste the output here."


@pytest.mark.asyncio
async def test_callback_no_override_for_ordinary_response() -> None:
    ctx = _FakeCallbackContext(events=[_FakeEvent("incident_manager", _ordinary_response())])
    assert await enforce_incident_manager_response_integrity(ctx) is None


@pytest.mark.asyncio
async def test_callback_registers_guidance_for_later_pop(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.agents.incident_manager.evidence as evidence_module

    monkeypatch.setattr(evidence_module, "current_run_id", lambda: "test-run-xyz")
    ctx = _FakeCallbackContext(events=[_FakeEvent("incident_manager", _next_step_response())])
    await enforce_incident_manager_response_integrity(ctx)

    captured = pop_troubleshooting_guidance("test-run-xyz")
    assert captured is not None
    assert captured.next_action == "Check the alarm status."
    assert captured.command == "alt"


@pytest.mark.asyncio
async def test_full_procedure_mode_also_rewritten_deterministically() -> None:
    text = json.dumps(
        {
            "outcome": "ok",
            "summary": "some free text the model wrote",
            "evidence": [],
            "candidate_titles": [],
            "detail": None,
            "troubleshooting_guidance": {
                "interaction_mode": "full_procedure",
                "full_procedure_steps": [
                    {"action": "Log in.", "command": "amos NODE-1"},
                    {"action": "Check status.", "command": "alt"},
                ],
            },
        }
    )
    ctx = _FakeCallbackContext(events=[_FakeEvent("incident_manager", text)])
    result = await enforce_incident_manager_response_integrity(ctx)
    assert result is not None
    corrected = json.loads(result.parts[0].text)
    assert "Log in." in corrected["summary"]
    assert "amos NODE-1" in corrected["summary"]
    assert "Check status." in corrected["summary"]
