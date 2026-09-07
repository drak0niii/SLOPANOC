"""Phase 5.1J: the ACTUAL ADK-generated function-calling schema for
`knowledge_search`/`knowledge_select_evidence` -- proving the model-
visible surface, not merely Python intent. Uses `google.adk.tools.FunctionTool`
directly against the real tool functions, exactly as ADK does internally
when building `incident_manager`'s own function-calling declarations.
"""
from __future__ import annotations

from google.adk.tools import FunctionTool

from backend.agents.incident_manager.agent import incident_manager
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence


def _schema_dict(func) -> dict:
    tool = FunctionTool(func)
    declaration = tool._get_declaration()
    return declaration.model_dump(mode="json", exclude_none=True)


# --- knowledge_search schema ---------------------------------------------------


def test_knowledge_search_schema_exposes_only_query_text_and_limit() -> None:
    schema = _schema_dict(knowledge_search)
    properties = schema["parameters"]["properties"]
    assert set(properties.keys()) == {"query_text", "limit"}
    assert schema["parameters"]["required"] == ["query_text"]


def test_knowledge_search_schema_never_exposes_trusted_or_internal_fields() -> None:
    """Checks only the `parameters` sub-schema -- the actual structured
    fields a model must fill in -- never the free-text `description`
    (which legitimately explains `tool_context`/`source_uri` in prose for
    a human reader, but that prose is not itself a fillable schema
    field).
    """
    schema = _schema_dict(knowledge_search)
    parameters_text = str(schema["parameters"]).lower()
    for forbidden in (
        "tool_context",
        "as_of",
        "applicability_context",
        "run_id",
        "session_id",
        "evidence_set",
        "source_uri",
        "repository",
        "database",
    ):
        assert forbidden not in parameters_text, f"knowledge_search parameters must not mention {forbidden!r}: {schema['parameters']}"


def test_knowledge_search_tool_context_param_is_excluded_by_adk_itself() -> None:
    """Structural proof that ADK's own `FunctionTool` -- not merely this
    codebase's convention -- is what removes `tool_context` from the
    generated schema (`_ignore_params`), matching every other Teams tool
    in this codebase (`teams_get_messages`, `teams_list_chats`).
    """
    tool = FunctionTool(knowledge_search)
    assert "tool_context" in tool._ignore_params


# --- knowledge_select_evidence schema -------------------------------------------


def test_knowledge_select_evidence_schema_exposes_only_selections() -> None:
    schema = _schema_dict(knowledge_select_evidence)
    properties = schema["parameters"]["properties"]
    assert set(properties.keys()) == {"selections"}
    assert schema["parameters"]["required"] == ["selections"]


def test_knowledge_select_evidence_nested_item_schema_is_identity_only() -> None:
    schema = _schema_dict(knowledge_select_evidence)
    item_schema = schema["parameters"]["properties"]["selections"]["items"]
    assert set(item_schema["properties"].keys()) == {"knowledge_id", "version_label", "section_id"}
    assert set(item_schema["required"]) == {"knowledge_id", "version_label", "section_id"}


def test_knowledge_select_evidence_schema_never_exposes_authoritative_fields() -> None:
    """Checks only the `parameters` sub-schema -- see
    `test_knowledge_search_schema_never_exposes_trusted_or_internal_fields`'s
    own docstring for why the free-text `description` is excluded.
    `source_id`/`source_system` legitimately overlap with `section_id` at
    the substring level, so those two are checked as whole schema KEYS,
    not substrings.
    """
    schema = _schema_dict(knowledge_select_evidence)
    parameters = schema["parameters"]
    item_schema = parameters["properties"]["selections"]["items"]
    assert set(item_schema["properties"].keys()) == {"knowledge_id", "version_label", "section_id"}

    parameters_text = str(parameters).lower()
    for forbidden in (
        "content",
        "source_uri",
        "source_locator",
        "title",
        "document_type",
        "lifecycle_status",
        "relevance",
        "applicability",
        "run_id",
        "session_id",
        "user_id",
    ):
        assert forbidden not in parameters_text, f"knowledge_select_evidence parameters must not mention {forbidden!r}: {parameters}"


# --- registered on the real incident_manager agent object ---------------------


def test_incident_manager_agent_object_carries_both_tools_with_correct_schemas() -> None:
    """Exercises the SAME agent object wired into the live AgentTool
    delegation path (agent.py) -- not a re-declared stand-in function.
    """
    tool_names = {getattr(t, "__name__", None) for t in incident_manager.tools}
    assert "knowledge_search" in tool_names
    assert "knowledge_select_evidence" in tool_names

    search_tool = next(t for t in incident_manager.tools if getattr(t, "__name__", None) == "knowledge_search")
    select_tool = next(t for t in incident_manager.tools if getattr(t, "__name__", None) == "knowledge_select_evidence")

    assert set(_schema_dict(search_tool)["parameters"]["properties"].keys()) == {"query_text", "limit"}
    assert set(_schema_dict(select_tool)["parameters"]["properties"].keys()) == {"selections"}
