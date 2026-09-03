"""Security-contract tests for the deterministic approval framework.

Per instruction sections 2, 9, 10, and 18: the model must never be able to
self-approve a write action, and no part of this framework may be
implemented as natural-language/phrase/keyword/regex parsing of user
text. These tests verify that structurally, not by exercising a live
model (which is out of scope for this milestone -- no write execution is
wired up at all yet).
"""
from __future__ import annotations

import inspect

from backend.agents.incident_manager.agent import incident_manager
from backend.agents.team_manager.agent import team_manager
from backend.approval import canonical, policy_gate, schemas, service


def _tool_names(agent) -> list[str]:
    return [getattr(t, "name", None) or getattr(t, "__name__", str(t)) for t in agent.tools]


# --- approve/reject are not LLM tools --------------------------------------


def test_team_manager_has_no_approval_tools() -> None:
    # Phase 4D added `record_case_analysis` (a restricted Case-analysis
    # write, see backend/agents/team_manager/case_tools.py); the semantic-
    # scope bug fix added `record_conversation_target` (a deterministic
    # declaration tool, see conversation_target.py) -- still no
    # approval-mutation tool of any kind.
    names = _tool_names(team_manager)
    assert names == ["incident_manager", "record_case_analysis", "record_conversation_target"]
    for forbidden in ("approve_proposal", "reject_proposal", "consume_proposal", "create_action_proposal"):
        assert forbidden not in names


def test_incident_manager_has_no_approval_tools() -> None:
    names = _tool_names(incident_manager)
    for forbidden in ("approve_proposal", "reject_proposal", "consume_proposal", "create_action_proposal"):
        assert forbidden not in names


def test_approval_service_functions_are_plain_functions_not_adk_tools() -> None:
    """`approve_proposal`/`reject_proposal` must be ordinary Python
    callables -- never wrapped as (or registered via) an ADK `BaseTool`/
    `FunctionTool`, which is what would make them model-callable.
    """
    from google.adk.tools import BaseTool

    for fn in (service.approve_proposal, service.reject_proposal, service.consume_proposal, service.create_action_proposal):
        assert inspect.isfunction(fn)
        assert not isinstance(fn, BaseTool)


def test_no_agent_module_imports_the_approval_service() -> None:
    """A stronger structural guarantee than just checking `tools=[...]`:
    neither agent's own source even references the approval service
    module, so there is no code path -- tool or otherwise -- by which
    either agent could reach an approval transition.
    """
    import backend.agents.incident_manager.agent as incident_manager_agent_module
    import backend.agents.team_manager.agent as team_manager_agent_module

    for module in (team_manager_agent_module, incident_manager_agent_module):
        source = inspect.getsource(module)
        assert "backend.approval" not in source
        assert "approval.service" not in source


# --- no phrase/keyword/regex approval routing exists -----------------------


def test_no_raw_user_text_confirmation_parser_exists_in_approval_modules() -> None:
    for module in (schemas, canonical, service, policy_gate):
        source = inspect.getsource(module)
        for forbidden in ("import re", "re.compile(", "re.match(", "re.search(", "re.findall("):
            assert forbidden not in source, f"{module.__name__} unexpectedly contains {forbidden!r}"


def test_no_hardcoded_confirmation_phrases_in_approval_modules() -> None:
    """Checks with word boundaries (not raw substring) so this stays
    meaningful without being fragile against unrelated words that happen
    to contain "ok"/"yes" as a substring (e.g. "token", "broken").
    """
    import re

    forbidden_phrases = ("yes", "ok", "go ahead", "confirm", "approve it", "sounds good")
    for module in (schemas, canonical, service, policy_gate):
        source = inspect.getsource(module).lower()
        for phrase in forbidden_phrases:
            pattern = r"\b" + re.escape(phrase) + r"\b"
            assert not re.search(pattern, source), (
                f"{module.__name__} unexpectedly contains the confirmation phrase {phrase!r}"
            )


def test_approval_transition_functions_never_take_free_text_input() -> None:
    """`approve_proposal`/`reject_proposal` are only ever invoked with a
    `proposal_id` and `session_state` -- there is no parameter through
    which raw user utterance text could flow into an approval decision.
    """
    for fn in (service.approve_proposal, service.reject_proposal, service.consume_proposal):
        params = list(inspect.signature(fn).parameters)
        assert params[:2] == ["proposal_id", "session_state"]
        for forbidden_param in ("user_text", "message_text", "utterance", "user_message"):
            assert forbidden_param not in params


def test_authorize_write_decision_depends_only_on_structured_arguments() -> None:
    """`authorize_write` takes `operation`/`payload`/`session_state` --
    never conversation text -- so the model has no channel through which
    to argue its way into an authorization.
    """
    params = list(inspect.signature(policy_gate.authorize_write).parameters)
    assert params[:3] == ["operation", "payload", "session_state"]
    for forbidden_param in ("user_text", "message_text", "utterance", "confirmation_text"):
        assert forbidden_param not in params


# --- no parallel/second memory system ---------------------------------------


def test_approval_state_uses_exactly_one_state_key() -> None:
    """Only one state key is ever written by this framework -- confirmed
    by inspecting the actual persistence call sites, not just trusting the
    constant's name.
    """
    source = inspect.getsource(service)
    # Every session_state[...] write in service.py must use the same key.
    assert source.count("session_state[PENDING_ACTION_PROPOSAL_STATE_KEY]") == source.count(
        "session_state["
    )
