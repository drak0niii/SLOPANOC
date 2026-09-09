"""A5 final corrective pass, Correction D: focused tests for
backend/api/applicability_context_capture.py (the run-scoped store) and
backend/agents/incident_manager/evidence.py's
`capture_known_applicability_context` (before_agent_callback) wiring,
plus backend/tools/knowledge/runtime.py's consumption of it.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from backend.agents.incident_manager.evidence import capture_known_applicability_context
from backend.api.applicability_context_capture import (
    discard_known_applicability_context,
    pop_known_applicability_context,
    register_known_applicability_context,
)
from backend.knowledge.domain.applicability import ApplicabilityContext


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, parts: list[_FakePart]) -> None:
        self.parts = parts


class _FakeCallbackContext:
    def __init__(self, user_content: Optional[_FakeContent]) -> None:
        self.user_content = user_content


def _request_text(**overrides: object) -> str:
    payload: dict[str, Any] = {"chat_topic": None, "question": "what should I check?", "requires_governed_knowledge": True}
    payload.update(overrides)
    return json.dumps(payload)


# --- store: register/pop/discard ----------------------------------------


def test_pop_returns_none_for_missing_run() -> None:
    assert pop_known_applicability_context("run-never-registered") is None


def test_register_then_pop_round_trips() -> None:
    context = ApplicabilityContext(dimensions={"vendor": ["ericsson"]})
    register_known_applicability_context("run-1", context)
    popped = pop_known_applicability_context("run-1")
    assert popped is not None
    assert popped.dimensions == {"vendor": ["ericsson"]}


def test_pop_consumes_the_entry_once() -> None:
    register_known_applicability_context("run-2", ApplicabilityContext(dimensions={"vendor": ["ericsson"]}))
    assert pop_known_applicability_context("run-2") is not None
    assert pop_known_applicability_context("run-2") is None


def test_register_noop_for_missing_run_id_or_context() -> None:
    register_known_applicability_context(None, ApplicabilityContext(dimensions={"vendor": ["ericsson"]}))
    register_known_applicability_context("run-3", None)
    assert pop_known_applicability_context("run-3") is None


def test_register_noop_for_empty_dimensions() -> None:
    # An empty-dimensions context is deliberately never stored -- "start
    # from empty" must be indistinguishable from "nothing registered".
    register_known_applicability_context("run-4", ApplicabilityContext(dimensions={}))
    assert pop_known_applicability_context("run-4") is None


def test_discard_is_safe_when_nothing_registered() -> None:
    discard_known_applicability_context("run-never-used")  # must not raise


# --- capture_known_applicability_context (before_agent_callback) --------


@pytest.mark.asyncio
async def test_callback_always_returns_none() -> None:
    ctx = _FakeCallbackContext(user_content=_FakeContent([_FakePart(_request_text(known_applicability_facts={"vendor": ["ericsson"]}))]))
    assert await capture_known_applicability_context(ctx) is None


@pytest.mark.asyncio
async def test_callback_registers_known_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.agents.incident_manager.evidence as evidence_module

    monkeypatch.setattr(evidence_module, "current_run_id", lambda: "run-known-facts")
    ctx = _FakeCallbackContext(
        user_content=_FakeContent([_FakePart(_request_text(known_applicability_facts={"vendor": ["ericsson"], "technology": ["4g"]}))])
    )
    await capture_known_applicability_context(ctx)

    captured = pop_known_applicability_context("run-known-facts")
    assert captured is not None
    assert captured.dimensions["vendor"] == ["ericsson"]
    assert captured.dimensions["technology"] == ["4g"]


@pytest.mark.asyncio
async def test_callback_noop_when_facts_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.agents.incident_manager.evidence as evidence_module

    monkeypatch.setattr(evidence_module, "current_run_id", lambda: "run-no-facts")
    ctx = _FakeCallbackContext(user_content=_FakeContent([_FakePart(_request_text())]))  # no known_applicability_facts key at all
    await capture_known_applicability_context(ctx)
    assert pop_known_applicability_context("run-no-facts") is None


@pytest.mark.asyncio
async def test_callback_noop_for_missing_user_content() -> None:
    ctx = _FakeCallbackContext(user_content=None)
    assert await capture_known_applicability_context(ctx) is None


@pytest.mark.asyncio
async def test_callback_noop_for_unparseable_text(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.agents.incident_manager.evidence as evidence_module

    monkeypatch.setattr(evidence_module, "current_run_id", lambda: "run-bad-json")
    ctx = _FakeCallbackContext(user_content=_FakeContent([_FakePart("not valid json")]))
    await capture_known_applicability_context(ctx)
    assert pop_known_applicability_context("run-bad-json") is None


@pytest.mark.asyncio
async def test_callback_noop_for_malformed_facts_never_crashes(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.agents.incident_manager.evidence as evidence_module

    monkeypatch.setattr(evidence_module, "current_run_id", lambda: "run-malformed")
    # A blank dimension key fails ApplicabilityContext's own normalization
    # validator -- must be treated as "nothing to register", never raise.
    ctx = _FakeCallbackContext(user_content=_FakeContent([_FakePart(_request_text(known_applicability_facts={"   ": ["x"]}))]))
    result = await capture_known_applicability_context(ctx)
    assert result is None
    assert pop_known_applicability_context("run-malformed") is None


# --- get_or_init_run_state consumption -----------------------------------


@pytest.mark.asyncio
async def test_run_state_uses_registered_applicability_context() -> None:
    from backend.tools.knowledge.runtime import get_or_init_run_state

    register_known_applicability_context("run-state-1", ApplicabilityContext(dimensions={"vendor": ["ericsson"]}))
    state = get_or_init_run_state("run-state-1")
    assert state.execution_context.applicability_context.dimensions == {"vendor": ["ericsson"]}


@pytest.mark.asyncio
async def test_run_state_defaults_to_empty_when_nothing_registered() -> None:
    from backend.tools.knowledge.runtime import get_or_init_run_state

    state = get_or_init_run_state("run-state-never-registered")
    assert state.execution_context.applicability_context.dimensions == {}
