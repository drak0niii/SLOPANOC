"""Security-contract tests for milestone 3B (approval-protected Teams
write execution) -- re-verifies, now that incident_manager actually has
write-capable tools, that the invariant from milestone 3A still holds:
approve/reject/consume remain unreachable from any agent, and no part of
the write flow is implemented as natural-language/phrase/keyword/regex
routing. Also confirms the "authorize_write before Power Automate"
ordering structurally, not just via the functional tests in
test_teams_execute_write.py.
"""
from __future__ import annotations

import inspect

from backend.agents.incident_manager.agent import incident_manager
from backend.agents.team_manager.agent import team_manager
from backend.approval import service
from backend.tools.teams import execute_write, propose_write, write_validation


def _tool_names(agent) -> list[str]:
    return [getattr(t, "name", None) or getattr(t, "__name__", str(t)) for t in agent.tools]


# --- approve/reject remain absent from every agent's tools -----------------


def test_incident_manager_tools_include_the_new_write_tools_but_not_approval() -> None:
    names = _tool_names(incident_manager)
    for expected in (
        "teams_propose_create_chat",
        "teams_propose_send_message",
        "teams_create_chat",
        "teams_send_message",
    ):
        assert expected in names
    for forbidden in ("approve_proposal", "reject_proposal", "consume_proposal"):
        assert forbidden not in names


def test_team_manager_tools_are_unchanged_by_this_milestone() -> None:
    # Phase 4D added `record_case_analysis` (see
    # backend/agents/team_manager/case_tools.py); the semantic-scope bug
    # fix added `record_conversation_target` (see
    # backend/agents/team_manager/conversation_target.py) -- the Teams
    # write-tooling itself is unchanged.
    assert _tool_names(team_manager) == [
        "incident_manager",
        "record_case_analysis",
        "record_conversation_target",
    ]


def _mentions_as_code(source: str, name: str) -> bool:
    """True if `name` appears as an actual import or call site -- not
    merely named in a docstring/comment explaining the security contract
    (several modules in this framework deliberately document, in prose,
    that `approve_proposal`/`reject_proposal` are intentionally absent;
    that prose mention is not itself a violation).
    """
    return f"import {name}" in source or f"{name}(" in source


def test_no_agent_module_imports_the_approval_service_transition_functions() -> None:
    import backend.agents.incident_manager.agent as incident_manager_agent_module
    import backend.agents.team_manager.agent as team_manager_agent_module

    for module in (team_manager_agent_module, incident_manager_agent_module):
        source = inspect.getsource(module)
        assert not _mentions_as_code(source, "approve_proposal")
        assert not _mentions_as_code(source, "reject_proposal")


def test_propose_and_execute_tool_modules_never_import_approve_or_reject() -> None:
    """The write tools may create proposals and authorize/consume them --
    never approve or reject one themselves.
    """
    for module in (propose_write, execute_write):
        source = inspect.getsource(module)
        assert not _mentions_as_code(source, "approve_proposal")
        assert not _mentions_as_code(source, "reject_proposal")


# --- no phrase/keyword/regex routing in the new write-tool layer -----------


def test_write_tool_modules_contain_no_regex_routing() -> None:
    for module in (propose_write, execute_write, write_validation):
        source = inspect.getsource(module)
        # write_validation.py legitimately uses `re` for EMAIL SYNTAX
        # validation (a fixed structural check, not intent/keyword
        # routing) -- everything else must stay regex-free.
        if module is write_validation:
            continue
        for forbidden in ("import re", "re.compile(", "re.match(", "re.search("):
            assert forbidden not in source


def test_write_validation_regex_is_scoped_to_email_syntax_only() -> None:
    """Confirms write_validation.py's one `re` usage is exactly the email
    pattern, not a general-purpose text/intent matcher.
    """
    source = inspect.getsource(write_validation)
    assert source.count("re.compile(") == 1
    assert "_EMAIL_PATTERN" in source


def test_no_hardcoded_confirmation_phrases_in_write_tool_modules() -> None:
    import re as _re

    forbidden_phrases = ("yes", "ok", "go ahead", "confirm", "approve it", "sounds good")
    for module in (propose_write, execute_write, service):
        source = inspect.getsource(module).lower()
        for phrase in forbidden_phrases:
            pattern = r"\b" + _re.escape(phrase) + r"\b"
            assert not _re.search(pattern, source), (
                f"{module.__name__} unexpectedly contains the confirmation phrase {phrase!r}"
            )


# --- authorize_write always precedes any Power Automate call ---------------


def test_execute_tools_call_authorize_write_before_constructing_a_gateway_client() -> None:
    """Structural check on source order: in both `teams_create_chat` and
    `teams_send_message`, `authorize_write(` appears before
    `PowerAutomateClient(` in the function body -- backing up the
    functional proof in test_teams_execute_write.py's spy-based tests with
    a check that the ordering is inherent to the code, not incidental to
    the specific test inputs used there.
    """
    source = inspect.getsource(execute_write)
    for function_name in ("def teams_create_chat", "def teams_send_message"):
        start = source.index(function_name)
        # Bound the search to this function's body (next top-level `def`).
        next_def = source.find("\ndef ", start + 1)
        body = source[start : next_def if next_def != -1 else len(source)]
        authorize_index = body.index("authorize_write(")
        gateway_index = body.index("PowerAutomateClient(")
        assert authorize_index < gateway_index


def test_execute_tools_call_consume_only_after_the_gateway_call() -> None:
    """Structural counterpart to test_teams_execute_write.py's functional
    "definite gateway failure does not consume" test: `consume_proposal(`
    must appear textually after the gateway call in both functions.
    """
    source = inspect.getsource(execute_write)
    for function_name, gateway_call in (
        ("def teams_create_chat", "client.create_chat("),
        ("def teams_send_message", "client.send_message("),
    ):
        start = source.index(function_name)
        next_def = source.find("\ndef ", start + 1)
        body = source[start : next_def if next_def != -1 else len(source)]
        gateway_index = body.index(gateway_call)
        consume_index = body.index("consume_proposal(")
        assert gateway_index < consume_index


def test_propose_tools_never_call_power_automate() -> None:
    source = inspect.getsource(propose_write)
    assert "PowerAutomateClient" not in source
    assert "requests" not in source
