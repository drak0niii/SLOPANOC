"""Phase 6A.10 prompt-contract tests -- mirrors `backend/tests/
test_followup_routing_contract.py`'s own established style: natural-
language ROUTING QUALITY is not unit-testable (no keyword/regex router
exists to test), but the actual instruction text sent to the model, and
the absence of any hardcoded routing implementation, are.
"""
from __future__ import annotations

import inspect

from backend.agents.team_manager import troubleshooting_tool as troubleshooting_tool_module
from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION


def _normalized() -> str:
    return " ".join(TEAM_MANAGER_INSTRUCTION.split()).lower()


def test_prompt_distinguishes_the_two_specialist_questions() -> None:
    text = _normalized()
    assert "what should i check or do next" in text
    assert "what happened" in text


def test_prompt_never_dumps_troubleshooting_methodology() -> None:
    """instruction section 33: Team Manager must know WHO to ask, never
    HOW to troubleshoot -- no vendor procedure/command/methodology text
    belongs in this prompt."""
    text = TEAM_MANAGER_INSTRUCTION
    for forbidden in ("VSWR", "restart the radio", "AMOS", "alarm procedure", "diagnostic workflow"):
        assert forbidden not in text


def test_prompt_states_troubleshooting_is_advisory_only() -> None:
    text = _normalized()
    assert "advisory only" in text
    assert "never call a tool" in text or "never execute" in text


def test_prompt_requires_surfacing_needs_information_honestly() -> None:
    text = _normalized()
    assert "needs_information" in text
    assert "never invent the assessment" in text


def test_prompt_requires_surfacing_blocked_honestly() -> None:
    text = _normalized()
    assert "blocked" in text
    assert "never substitute general knowledge or call" in text


def test_prompt_forbids_treating_incident_manager_prose_as_authoritative_troubleshooting_input() -> None:
    text = _normalized()
    assert "never treat" in text
    assert "authoritative input to a troubleshooting question" in text


def test_prompt_states_specialists_never_call_each_other() -> None:
    text = _normalized()
    assert "neither specialist ever calls the other" in text


def test_prompt_requires_the_question_field_to_be_self_contained() -> None:
    text = _normalized()
    assert "troubleshooting_question" in text
    assert "self-contained statement" in text


def test_troubleshooting_tool_contains_no_keyword_or_regex_routing() -> None:
    """No Python code anywhere makes the specialist-selection decision --
    it is entirely team_manager's own model reasoning (docs/AGENT_
    CONTRACT.md #5's "agent vs. tool" principle, restated for 6A.10)."""
    source = inspect.getsource(troubleshooting_tool_module)
    for forbidden in ("import re", "re.compile(", "re.match(", "re.search(", "'troubleshoot' in", '"troubleshoot" in'):
        assert forbidden not in source
