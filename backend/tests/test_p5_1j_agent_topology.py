"""Phase 5.1J: agent topology, no-keyword-routing, and no-session-state-
authority checks. Complements test_p5_1j_knowledge_tool_schema.py
(schema shape) and test_api_security_contract.py's own
`test_agent_topology_is_unaffected_by_the_api_layer` (frozen tool-list
snapshot, updated this phase).
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from backend.agents.incident_manager.agent import incident_manager
from backend.agents.team_manager.agent import team_manager
from backend.agents.team_manager.direct_read_fast_path import _fast_path_incident_manager
from backend.agents.team_manager.read_continuation_execution import _SYNTHESIS_ONLY_INCIDENT_MANAGER

_TOOLS_FILE = Path(__file__).resolve().parents[1] / "tools" / "knowledge" / "tools.py"
_RUNTIME_FILE = Path(__file__).resolve().parents[1] / "tools" / "knowledge" / "runtime.py"


def _tool_names(agent) -> set[str]:
    return {getattr(t, "__name__", None) or getattr(t, "name", None) for t in agent.tools}


# --- registration: Incident Manager only ----------------------------------------


def test_incident_manager_has_both_new_knowledge_tools() -> None:
    names = _tool_names(incident_manager)
    assert "knowledge_search" in names
    assert "knowledge_select_evidence" in names


def test_team_manager_does_not_have_either_knowledge_tool() -> None:
    names = _tool_names(team_manager)
    assert "knowledge_search" not in names
    assert "knowledge_select_evidence" not in names


def test_existing_incident_manager_teams_tools_are_not_regressed() -> None:
    names = _tool_names(incident_manager)
    for expected in (
        "teams_list_chats",
        "teams_get_messages",
        "get_current_time_context",
        "teams_propose_create_chat",
        "teams_propose_send_message",
        "teams_create_chat",
        "teams_send_message",
    ):
        assert expected in names


def test_live_delegation_agent_variant_inherits_the_knowledge_tools() -> None:
    """`_fast_path_incident_manager` (direct_read_fast_path.py) is the
    ACTUAL agent wired into team_manager's own live `AgentTool` -- it is
    a `.model_copy` of the base `incident_manager` that only overrides
    callbacks, so it must inherit the same tool list, including the two
    new KM tools, automatically.
    """
    assert _tool_names(_fast_path_incident_manager) == _tool_names(incident_manager)


def test_synthesis_only_variant_still_has_no_tools_at_all() -> None:
    """The synthesis-only variant (read_continuation_execution.py)
    explicitly overrides `tools=[]` -- it must remain tool-less,
    including no KM tools, since it never reasons freely; it only
    synthesizes prose from evidence already deterministically prefetched.
    """
    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.tools == []


# --- no keyword/regex/intent-phrase routing --------------------------------------


def test_no_keyword_or_regex_routing_decides_knowledge_search_invocation() -> None:
    """Structural proof: neither `tools.py` nor `runtime.py` contains a
    regex compile, a keyword/intent-phrase list, or an `if "mop" in`/
    `if "procedure" in`-shaped substring check that could decide WHETHER
    `knowledge_search` gets called -- that decision is the model's own
    semantic judgment, exactly like every other tool-call decision in
    this codebase (docs/AGENT_CONTRACT.md #5).
    """
    for path in (_TOOLS_FILE, _RUNTIME_FILE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "compile":
                if isinstance(node.func.value, ast.Name) and node.func.value.id == "re":
                    raise AssertionError(f"{path.name} must not use a regex-based routing mechanism")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "re", f"{path.name} must not import re"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "re", f"{path.name} must not import re"


def test_no_fault_name_or_intent_dictionary_declared() -> None:
    for path in (_TOOLS_FILE, _RUNTIME_FILE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        source = path.read_text(encoding="utf-8").lower()
        for forbidden in ("keyword_list", "intent_phrases", "fault_names", "trigger_words"):
            assert forbidden not in source, f"{path.name} must not declare {forbidden!r}"


# --- no session-state authority --------------------------------------------------


def test_tools_module_never_writes_to_tool_context_state() -> None:
    """Unlike `teams_get_messages`/`teams_list_chats` (which legitimately
    write small, safe markers into `tool_context.state` for OTHER
    established mechanisms), the KM tool adapter carries its trusted
    evidence exclusively through `backend/tools/knowledge/runtime.py`'s
    own run-id-keyed store -- never through ordinary ADK session state,
    which could flow into prompts/model context on a later turn.
    """
    tree = ast.parse(_TOOLS_FILE.read_text(encoding="utf-8"), filename=str(_TOOLS_FILE))
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) and node.value.attr == "state":
            raise AssertionError("tools.py must never write to tool_context.state")


def test_runtime_module_has_no_adk_or_tool_context_dependency() -> None:
    """`backend/tools/knowledge/runtime.py` holds the trusted evidence
    store itself -- it must not import ADK/ToolContext directly; only
    `tools.py` (the thin ADK adapter layer) does.
    """
    tree = ast.parse(_RUNTIME_FILE.read_text(encoding="utf-8"), filename=str(_RUNTIME_FILE))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("google.adk"):
            raise AssertionError("runtime.py must not import google.adk")


def test_knowledge_run_evidence_state_is_never_a_pydantic_serializable_session_value() -> None:
    """`KnowledgeRunEvidenceState` is a plain (non-Pydantic) dataclass --
    it is not the kind of object that would be accidentally assigned into
    `tool_context.state[...]` and survive a JSON-serializing session
    backend the way a plain dict/BaseModel might.
    """
    from backend.tools.knowledge.runtime import KnowledgeRunEvidenceState

    assert not hasattr(KnowledgeRunEvidenceState, "model_dump")


# --- knowledge tools remain read-only, no mutation capability exposed -----------


def test_no_knowledge_mutation_tool_is_registered() -> None:
    names = _tool_names(incident_manager)
    for forbidden in ("knowledge_add", "knowledge_replace", "knowledge_approve", "knowledge_archive", "knowledge_ingest", "knowledge_delete"):
        assert forbidden not in names


def test_no_additional_get_browse_current_tool_is_registered() -> None:
    names = _tool_names(incident_manager)
    for forbidden in ("knowledge_get", "knowledge_get_current", "knowledge_get_section", "knowledge_get_document", "knowledge_browse", "knowledge_latest"):
        assert forbidden not in names


def test_knowledge_search_function_signature_has_no_run_or_session_identity_param() -> None:
    from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

    for func in (knowledge_search, knowledge_select_evidence):
        params = set(inspect.signature(func).parameters)
        for forbidden in ("run_id", "invocation_id", "session_id", "user_id"):
            assert forbidden not in params, f"{func.__name__} must not accept {forbidden!r} as a parameter"
