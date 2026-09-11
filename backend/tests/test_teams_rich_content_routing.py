"""Teams Rich Content Routing corrective milestone.

Fixes the live defect: a request needing Teams-posted visual content
(e.g. "what is shown in the latest image") was intercepted by the
text-only exact-read fast path (`direct_read_fast_path.py`) exactly like
an ordinary text read, because nothing structurally distinguished the
two at fast-path-eligibility time. Once intercepted, the shortcut hands
off to `read_continuation_execution.py`'s synthesis-only agent
(`tools=[]`), which is structurally incapable of a second tool call
(`teams_get_hosted_content`) after `teams_get_messages` -- so retrieval
genuinely succeeded live, but the hosted-content tool was never
reachable.

FIX: a new `IncidentManagerRequest.requires_rich_content` field (set by
`team_manager`'s own semantic judgment, never inferred from keywords),
read back by a new `_requires_rich_content` gate in
`direct_read_fast_path.py` -- the IDENTICAL mechanism/shape already
proven twice for `requires_governed_knowledge` and (structurally, not by
JSON) for image evidence. When true, the fast path is skipped and
`incident_manager`'s own normal, full tool-calling turn runs instead,
where `teams_get_hosted_content` is a real, callable tool.

These tests exercise the SAME `_capture_unique_match_for_fast_path`
domain-logic layer `test_p4b3_fast_path.py` already tests (Tier 2) --
never a second, competing test harness.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Optional

import pytest
from google.genai import types

from backend.agents.team_manager import direct_read_fast_path as fast_path_module
from backend.agents.team_manager.direct_read_fast_path import (
    _FAST_PATH_PENDING_STATE_KEY,
    _capture_unique_match_for_fast_path,
    _requires_rich_content,
)
from backend.selection.schemas import ReadOperation


def _user_content_json(payload: dict[str, Any]) -> Any:
    """Mirrors `AgentTool.run_async`'s own construction from
    `IncidentManagerRequest.model_dump_json(...)` -- see
    test_p4b3_fast_path.py's identical helper.
    """
    return types.Content(role="user", parts=[types.Part.from_text(text=json.dumps(payload))])


class _Ctx:
    def __init__(
        self,
        state: Optional[dict] = None,
        requires_governed_knowledge: bool = False,
        requires_rich_content: bool = False,
    ) -> None:
        self.state = state if state is not None else {}
        self.user_content = _user_content_json(
            {
                "requires_governed_knowledge": requires_governed_knowledge,
                "requires_rich_content": requires_rich_content,
            }
        )


def _matched_response(chat_id: str = "chat-rc-1", title: str = "SLOPANOC Gateway Group Test") -> dict[str, Any]:
    return {"chats": [], "match": "matched", "matched_chat": {"chat_id": chat_id, "title": title}}


def _teams_list_chats_tool() -> Any:
    return type("Tool", (), {"name": "teams_list_chats"})()


def _capture(ctx: _Ctx, args: Optional[dict[str, Any]] = None, response: Optional[dict[str, Any]] = None) -> None:
    _capture_unique_match_for_fast_path(
        _teams_list_chats_tool(),
        args if args is not None else {"topic": "SLOPANOC Gateway Group Test"},
        ctx,
        response if response is not None else _matched_response(),
    )


# --- 1: ordinary text read remains fast-path eligible -----------------------


def test_1_ordinary_text_read_is_fast_path_eligible() -> None:
    ctx = _Ctx(requires_rich_content=False)
    _capture(ctx)
    assert _FAST_PATH_PENDING_STATE_KEY in ctx.state


# --- 2: rich-content read is NOT fast-path eligible --------------------------


def test_2_rich_content_read_is_not_fast_path_eligible() -> None:
    ctx = _Ctx(requires_rich_content=True)
    _capture(ctx)
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


# --- 3: normal Incident Manager tool loop remains available ------------------
# (proven by tool-registration tests below -- the fast path merely defers to
# the SAME incident_manager agent object's own normal turn.)


def test_3_bypassing_the_fast_path_falls_back_to_the_normal_turn_not_an_error() -> None:
    """A skipped fast path is a silent no-op (returns `None`) -- never an
    error state, never a marker left in a partial/inconsistent shape.
    """
    ctx = _Ctx(requires_rich_content=True)
    result = _capture(ctx)
    assert result is None
    assert ctx.state == {}


# --- 4/5: tool registration -----------------------------------------------


def test_4_teams_get_hosted_content_is_registered_on_incident_manager() -> None:
    from backend.agents.incident_manager.agent import incident_manager

    names = [getattr(t, "name", None) or getattr(t, "__name__", None) for t in incident_manager.tools]
    assert "teams_get_hosted_content" in names


def test_4_fast_path_incident_manager_variant_also_has_the_tool() -> None:
    """The SAME variant `team_manager`'s own `incident_manager_tool`
    always delegates through (`_fast_path_incident_manager`) -- whether or
    not the shortcut actually engages, this is the agent whose normal turn
    must expose the tool.
    """
    from backend.agents.team_manager.direct_read_fast_path import _fast_path_incident_manager

    names = [getattr(t, "name", None) or getattr(t, "__name__", None) for t in _fast_path_incident_manager.tools]
    assert "teams_get_hosted_content" in names


def test_5_team_manager_never_receives_teams_get_hosted_content_directly() -> None:
    from backend.agents.team_manager.agent import team_manager

    names = [getattr(t, "name", None) or getattr(t, "__name__", None) for t in team_manager.tools]
    assert "teams_get_hosted_content" not in names


# --- 6/7: text vs. rich-content path divergence ------------------------------


def test_6_text_read_still_reaches_the_optimized_marker_shape() -> None:
    ctx = _Ctx(requires_rich_content=False)
    _capture(ctx, args={"topic": "SLOPANOC Gateway Group Test", "pending_question": None, "pending_time_range": None})
    pending = ctx.state[_FAST_PATH_PENDING_STATE_KEY]
    assert pending["chat_id"] == "chat-rc-1"
    assert pending["operation"] == ReadOperation.SUMMARIZE.value


def test_7_rich_content_read_never_reaches_the_synthesis_only_shortcut() -> None:
    """No `_FAST_PATH_PENDING_STATE_KEY` means `_fast_path_before_model_
    callback` will find nothing pending next turn and return `None` --
    `execute_read_continuation`'s synthesis-only path is never invoked
    for this request at all (structurally, not by a runtime check).
    """
    ctx = _Ctx(requires_rich_content=True)
    _capture(ctx)
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


# --- 8: no natural-language substring/regex routing --------------------------


def test_8_no_keyword_or_regex_routing_in_the_gate_implementation() -> None:
    """Structural proof, not just behavioral -- the routing decision reads
    a typed JSON field, never inspects the user's own wording. Mirrors
    test_p4a_orchestration_overhead_reduction.py's own "implementation_
    term not in source" pattern.
    """
    source = inspect.getsource(_requires_rich_content)
    # These tokens are the only genuinely suspicious "matching the user's
    # own wording" signatures -- deliberately NOT generic English words
    # like "image"/"screenshot", which legitimately appear in the
    # docstring's own explanation and must not be penalized for that.
    for forbidden in (".lower()", "re.search", "re.match", "re.compile", "startswith", "endswith", " in question", " in text"):
        assert forbidden not in source, f"found forbidden keyword-routing token {forbidden!r} in gate source"
    assert "user_content" in source  # reads the structured field, as expected
    assert "requires_rich_content" in source


# --- 9: rich-content structured intent survives chat resolution -------------


def test_9_rich_content_flag_read_correctly_regardless_of_match_outcome() -> None:
    ctx_matched = _Ctx(requires_rich_content=True)
    assert _requires_rich_content(ctx_matched) is True

    ctx_default = _Ctx(requires_rich_content=False)
    assert _requires_rich_content(ctx_default) is False


def test_9_well_formed_payload_missing_the_key_defaults_to_false() -> None:
    """Mirrors `_requires_governed_knowledge`'s own EXACT behavior for
    this case (verified by reading its source, not assumed): a
    STRUCTURALLY well-formed JSON object that simply omits this key
    (never produced by this codebase's own `IncidentManagerRequest
    .model_dump_json()`, which always serializes every field) falls back
    to `False` via `dict.get`'s own default -- fail-closed applies to
    STRUCTURAL problems (missing/malformed content), not to a
    well-formed-but-absent key."""
    ctx = type("Ctx", (), {"user_content": _user_content_json({"chat_topic": "x"})})()
    assert _requires_rich_content(ctx) is False


def test_9_malformed_user_content_fails_closed_true() -> None:
    ctx = type("Ctx", (), {"user_content": types.Content(role="user", parts=[types.Part.from_text(text="not json")])})()
    assert _requires_rich_content(ctx) is True


def test_9_missing_user_content_fails_closed_true() -> None:
    ctx = type("Ctx", (), {"user_content": None})()
    assert _requires_rich_content(ctx) is True


# --- 10/11: ambiguous / not_found rich-content requests -----------------------


def test_10_ambiguous_match_with_rich_content_never_sets_the_marker() -> None:
    """Ambiguous resolution already exits `_capture_unique_match_for_fast_
    path` BEFORE any requires_* gate is even reached -- proves the new
    gate does not interfere with, duplicate, or short-circuit the
    existing SelectionCard path."""
    ctx = _Ctx(requires_rich_content=True)
    _capture(ctx, response={"chats": [], "match": "ambiguous"})
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


def test_10_ambiguous_match_with_ordinary_text_read_also_never_sets_the_marker() -> None:
    """Non-regression: ambiguous resolution was already never fast-path
    eligible, with or without this milestone's change."""
    ctx = _Ctx(requires_rich_content=False)
    _capture(ctx, response={"chats": [], "match": "ambiguous"})
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


def test_11_not_found_match_with_rich_content_never_sets_the_marker() -> None:
    ctx = _Ctx(requires_rich_content=True)
    _capture(ctx, response={"chats": [], "match": "not_found"})
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


# --- 12: write requests unaffected -------------------------------------------


def test_12_write_resolution_unaffected_regardless_of_rich_content_flag() -> None:
    ctx = _Ctx(requires_rich_content=True)
    _capture(ctx, args={"topic": "SLOPANOC Gateway Group Test", "pending_write_message": "hello team"})
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


def test_12_write_resolution_unaffected_for_ordinary_text_flag_too() -> None:
    ctx = _Ctx(requires_rich_content=False)
    _capture(ctx, args={"topic": "SLOPANOC Gateway Group Test", "pending_write_message": "hello team"})
    assert _FAST_PATH_PENDING_STATE_KEY not in ctx.state


# --- Independence from the governed-knowledge gate ----------------------------


def test_governed_knowledge_and_rich_content_gates_are_independent() -> None:
    only_km = _Ctx(requires_governed_knowledge=True, requires_rich_content=False)
    _capture(only_km)
    assert _FAST_PATH_PENDING_STATE_KEY not in only_km.state

    only_rich = _Ctx(requires_governed_knowledge=False, requires_rich_content=True)
    _capture(only_rich)
    assert _FAST_PATH_PENDING_STATE_KEY not in only_rich.state

    neither = _Ctx(requires_governed_knowledge=False, requires_rich_content=False)
    _capture(neither)
    assert _FAST_PATH_PENDING_STATE_KEY in neither.state

    both = _Ctx(requires_governed_knowledge=True, requires_rich_content=True)
    _capture(both)
    assert _FAST_PATH_PENDING_STATE_KEY not in both.state


# --- perf log discriminability (developer-diagnostic only) -------------------


def test_perf_log_line_distinguishes_rich_content_skip_from_governed_knowledge_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[str] = []
    monkeypatch.setattr(fast_path_module._perf_logger, "info", lambda msg, *a, **k: logged.append(msg % a if a else msg))

    ctx = _Ctx(requires_rich_content=True)
    _capture(ctx)

    assert any("fast_path_skipped_requires_rich_content" in line for line in logged)
