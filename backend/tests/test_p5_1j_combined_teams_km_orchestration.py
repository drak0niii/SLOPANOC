"""Second pre-4H correction pass: the Teams exact-read fast path must
never silently drop a REQUIRED governed-knowledge source. Covers the
new `IncidentManagerRequest.requires_governed_knowledge` structured
signal, the fast-path eligibility guard that reads it back from
`tool_context.user_content` (the exact channel `AgentTool.run_async`
actually uses -- never a tool's own call arguments), and proves no
keyword/regex routing was introduced.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from google.genai import types

from backend.agents.incident_manager.schemas import IncidentManagerRequest
from backend.agents.team_manager.direct_read_fast_path import (
    _FAST_PATH_PENDING_STATE_KEY,
    _capture_unique_match_for_fast_path,
    _requires_governed_knowledge,
)

_FAST_PATH_FILE = Path(__file__).resolve().parents[1] / "agents" / "team_manager" / "direct_read_fast_path.py"


class _Ctx:
    def __init__(self, user_content: Any) -> None:
        self.state: dict[str, Any] = {}
        self.user_content = user_content


def _teams_list_chats_tool() -> Any:
    return type("Tool", (), {"name": "teams_list_chats"})()


def _matched_response(chat_id: str = "chat-1", title: str = "SLOPANOC Gateway Group Test") -> dict[str, Any]:
    return {"chats": [], "match": "matched", "matched_chat": {"chat_id": chat_id, "title": title}}


def _real_user_content(**request_fields: Any) -> types.Content:
    """Builds the EXACT `types.Content` `AgentTool.run_async` itself
    constructs -- `input_schema.model_validate(args)` then
    `model_dump_json(exclude_none=True)` -- so this test exercises the
    real request schema/serialization round trip, not a hand-rolled dict.
    """
    request = IncidentManagerRequest(**request_fields)
    return types.Content(role="user", parts=[types.Part.from_text(text=request.model_dump_json(exclude_none=True))])


# --- A/E: Teams-only (requires_governed_knowledge=False, the default) ---------


def test_a_teams_only_request_keeps_the_fast_path_eligible() -> None:
    ctx = _Ctx(_real_user_content(chat_topic="SLOPANOC Gateway Group Test"))
    _capture_unique_match_for_fast_path(_teams_list_chats_tool(), {"topic": "SLOPANOC Gateway Group Test"}, ctx, _matched_response())
    assert _FAST_PATH_PENDING_STATE_KEY in ctx.state


def test_e_explicit_false_does_not_disable_the_fast_path() -> None:
    ctx = _Ctx(_real_user_content(chat_topic="SLOPANOC Gateway Group Test", requires_governed_knowledge=False))
    _capture_unique_match_for_fast_path(_teams_list_chats_tool(), {"topic": "SLOPANOC Gateway Group Test"}, ctx, _matched_response())
    assert _FAST_PATH_PENDING_STATE_KEY in ctx.state


# --- B: KM-only request contract -----------------------------------------------


def test_b_km_only_request_has_no_chat_topic_and_requires_governed_knowledge() -> None:
    request = IncidentManagerRequest(
        question="Do we have any governed knowledge about Aurora Relay verification?",
        requires_governed_knowledge=True,
    )
    assert request.chat_topic is None
    assert request.requires_governed_knowledge is True


def test_b_km_only_serialized_request_correctly_reports_requires_governed_knowledge() -> None:
    """Proves the REAL serialization round trip (`model_dump_json` ->
    `types.Content` -> `_requires_governed_knowledge`), not a hand-built
    dict -- since `chat_topic` is absent, `teams_list_chats` is never
    called at all in real operation (see incident_manager's own "NO
    TEAMS CONVERSATION NEEDED" instruction branch), so there is no
    fast-path marker to set either way; this test protects the read-back
    mechanism itself.
    """
    content = _real_user_content(question="Do we have governed knowledge about Aurora Relay?", requires_governed_knowledge=True)
    ctx = _Ctx(content)
    assert _requires_governed_knowledge(ctx) is True


# --- C/D: combined Teams + KM request bypasses the fast path -------------------


def test_c_combined_request_does_not_enter_the_fast_path() -> None:
    ctx = _Ctx(_real_user_content(chat_topic="SLOPANOC Gateway Group Test", requires_governed_knowledge=True))
    _capture_unique_match_for_fast_path(_teams_list_chats_tool(), {"topic": "SLOPANOC Gateway Group Test"}, ctx, _matched_response())
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


def test_d_requires_governed_knowledge_true_is_read_back_correctly() -> None:
    content = _real_user_content(chat_topic="Ops Bridge", requires_governed_knowledge=True)
    assert _requires_governed_knowledge(_Ctx(content)) is True


def test_d_requires_governed_knowledge_false_is_read_back_correctly() -> None:
    content = _real_user_content(chat_topic="Ops Bridge", requires_governed_knowledge=False)
    assert _requires_governed_knowledge(_Ctx(content)) is False


def test_d_combined_guard_does_not_affect_an_ambiguous_or_not_found_match() -> None:
    """The guard only ever matters for a "matched" result -- an
    ambiguous/not-found result already takes a completely different path
    (selection/no-result), unaffected by `requires_governed_knowledge`.
    """
    ctx = _Ctx(_real_user_content(chat_topic="Ops Bridge", requires_governed_knowledge=True))
    _capture_unique_match_for_fast_path(
        _teams_list_chats_tool(), {"topic": "Ops Bridge"}, ctx, {"chats": [], "match": "ambiguous", "candidates": []}
    )
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


# --- fail-closed behavior of the read-back helper itself ------------------------


def test_requires_governed_knowledge_fails_closed_on_missing_user_content() -> None:
    assert _requires_governed_knowledge(_Ctx(None)) is True


def test_requires_governed_knowledge_fails_closed_on_malformed_json() -> None:
    content = types.Content(role="user", parts=[types.Part.from_text(text="not json")])
    assert _requires_governed_knowledge(_Ctx(content)) is True


def test_requires_governed_knowledge_fails_closed_on_empty_content() -> None:
    content = types.Content(role="user", parts=[])
    assert _requires_governed_knowledge(_Ctx(content)) is True


# --- G: no keyword/regex routing was introduced ---------------------------------


def test_g_no_regex_or_keyword_routing_in_the_fast_path_module() -> None:
    tree = ast.parse(_FAST_PATH_FILE.read_text(encoding="utf-8"), filename=str(_FAST_PATH_FILE))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "re", "direct_read_fast_path.py must not import re"
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re", "direct_read_fast_path.py must not import re"

    source_lower = _FAST_PATH_FILE.read_text(encoding="utf-8").lower()
    for forbidden in ("keyword_list", "intent_phrases", "trigger_words", 'in text', 'in question'):
        assert forbidden not in source_lower, f"direct_read_fast_path.py must not contain {forbidden!r}"


def test_g_requires_governed_knowledge_is_read_from_structured_content_not_parsed_text() -> None:
    """Structural proof that `_requires_governed_knowledge` reads a
    declared JSON field (`payload.get("requires_governed_knowledge")`)
    -- never scans free text for a phrase.
    """
    import inspect

    from backend.agents.team_manager import direct_read_fast_path as module

    source = inspect.getsource(module._requires_governed_knowledge)
    assert "json.loads" in source
    assert 'payload.get("requires_governed_knowledge"' in source
    assert " in text" not in source.replace('"".join', "")
