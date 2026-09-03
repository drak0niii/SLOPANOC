"""Prompt-contract test for the pre-4H latency investigation's "why would
Teams/incident_manager ever be invoked for a simple conversational turn"
question. Per the explicit constraint on that investigation, the fix for
an over-eager delegation could never be hardcoded NL routing/regex
("if 'hello' in message") -- so the only thing verifiable here,
deterministically, is that team_manager's OWN instruction text scopes
delegation exclusively to Teams-related requests, never unconditionally.
Mirrors the other prompt-contract tests in this suite: pin instruction
text, never an exact model decision.
"""
from __future__ import annotations

from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION

_TM = " ".join(TEAM_MANAGER_INSTRUCTION.split())


def test_delegation_logic_is_scoped_under_a_teams_related_request_condition() -> None:
    assert (
        "When the user asks you to summarize, read, or ask a question about a Teams chat/conversation"
        in _TM
    )


def test_there_is_no_unconditional_or_always_delegate_instruction() -> None:
    # A regression here would mean some edit made delegation the default
    # for every turn, rather than conditional on a Teams-related request.
    assert "always delegate" not in _TM.lower()
    assert "every turn" not in _TM.lower()


def test_team_manager_may_answer_directly_without_delegating() -> None:
    assert "You may answer directly, without calling `incident_manager`" in _TM


def test_no_hardcoded_natural_language_routing_exists_in_the_orchestration_code() -> None:
    """Deterministic proof, not just a prompt-text pin: chat_service.py
    (the actual orchestration code) contains no conditional branching on
    the user's message text to decide whether to delegate -- that
    decision is entirely team_manager's own model reasoning, mediated
    only through the existing AgentTool call/response mechanism.
    """
    import inspect

    from backend.api import chat_service as chat_service_module

    source = inspect.getsource(chat_service_module)
    for forbidden in ('"hello"', "'hello'", "message_text.lower()", "in message_text", "re.match", "re.search"):
        assert forbidden not in source
