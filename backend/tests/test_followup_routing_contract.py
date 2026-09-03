"""Prompt-contract and structural tests for the semantic follow-up
routing fix.

team_manager resolves referents and decides when to re-delegate purely
through its own LLM reasoning; no Python code anywhere performs
intent/keyword/regex routing on natural language. Per this task's test
strategy, natural-language reasoning quality itself is not unit-testable
here -- these tests instead verify (a) the actual instruction text sent
to the model establishes the required rules, and (b) no hardcoded
language-routing implementation exists in the deterministic code that
touches this flow.
"""
from __future__ import annotations

import inspect

from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION
from backend.agents.team_manager import agent as team_manager_agent_module
from backend.agents.team_manager import prompts as team_manager_prompts
from backend.agents.team_manager import state_sync
from backend.agents.team_manager.agent import team_manager
from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION

# --- Prompt contract: team_manager --------------------------------------


def test_prompt_forbids_treating_state_absence_as_teams_capability_absence() -> None:
    assert "absence from your own state is not" in TEAM_MANAGER_INSTRUCTION
    assert "the same as absence from Teams" in TEAM_MANAGER_INSTRUCTION


def test_prompt_requires_referent_resolution_from_conversation_context() -> None:
    assert "pronoun" in TEAM_MANAGER_INSTRUCTION
    assert "ellipsis" in TEAM_MANAGER_INSTRUCTION


def test_prompt_states_evidence_state_never_holds_message_content() -> None:
    assert "never message bodies" in TEAM_MANAGER_INSTRUCTION
    assert "never message content" in TEAM_MANAGER_INSTRUCTION


def test_prompt_requires_ambiguity_to_be_clarified_not_guessed() -> None:
    assert "clarifying question" in TEAM_MANAGER_INSTRUCTION
    assert "rather than guessing" in TEAM_MANAGER_INSTRUCTION


def test_prompt_instructs_delegation_uses_a_self_contained_question() -> None:
    assert "self-contained" in TEAM_MANAGER_INSTRUCTION
    assert "never sees this conversation" in TEAM_MANAGER_INSTRUCTION


def test_prompt_only_permits_direct_answers_from_state_or_prior_turns() -> None:
    assert "You may answer directly" in TEAM_MANAGER_INSTRUCTION
    assert "already fully and reliably" in TEAM_MANAGER_INSTRUCTION


def test_prompt_forbids_a_delegated_question_from_restating_the_chat_name() -> None:
    """Live-bug regression (pre-4H refinement, item 3): `question` must
    never restate the chat's own name/topic -- `chat_topic` already
    carries the destination separately, and a `question` that repeats it
    would reopen an already-resolved chat-selection ambiguity if this
    exact request later needs to resume against a newly-selected chat
    (see `read_resume.py`/`list_chats.py`'s `_safe_pending_question` for
    the deterministic backend safety net this prompt language backs up).
    """
    normalized = " ".join(TEAM_MANAGER_INSTRUCTION.split())
    assert "must NEVER repeat or restate the chat's own name/topic" in normalized
    assert "leave `question` unset entirely" in normalized


def test_module_docstring_states_no_hardcoded_routing_design_principle() -> None:
    doc = " ".join((team_manager_prompts.__doc__ or "").split())
    assert "never as literal phrases, keywords, or patterns to match" in doc
    assert "no Python code anywhere" in doc


# --- Structural: no hardcoded language routing exists in code -----------


def test_state_sync_module_contains_no_regex_or_keyword_routing() -> None:
    source = inspect.getsource(state_sync)
    for forbidden in ("import re", "re.compile(", "re.match(", "re.search(", "re.findall("):
        assert forbidden not in source
    # state_sync only ever inspects the structured tool_response dict --
    # never raw user/agent natural-language text.
    for forbidden_name in ("user_text", "utterance", "user_message", "keyword"):
        assert forbidden_name not in source.lower()


def test_team_manager_agent_module_contains_no_regex_routing() -> None:
    """Word-boundary-checked: a naive `"import re" in source` substring
    check false-positives on `"import record_case_analysis"` (Phase 4D) --
    "import re" is a literal prefix of "import record_case_analysis".
    """
    import re as _re

    source = inspect.getsource(team_manager_agent_module)
    assert not _re.search(r"(?<!\w)import re(?!\w)", source)
    assert "re.compile(" not in source


def test_team_manager_has_no_tools_besides_incident_manager_and_case_analysis() -> None:
    """The Teams-vs-conversation routing decision has nowhere to live
    except team_manager's own LLM reasoning -- there is no other AGENT it
    could delegate a "classify this request" step to, and no second LLM
    call was introduced. `record_case_analysis` (Phase 4D) is a
    restricted Case-analysis write; `record_conversation_target`
    (semantic-scope bug fix) is a restricted, closed-set declaration tool
    -- see conversation_target.py's own module docstring for why this is
    a tool (deterministic validation only), never a classifier itself.
    Neither performs any natural-language routing -- see
    test_conversation_target_prompt_contract.py for that structural
    proof specifically.
    """
    names = [getattr(t, "name", None) or getattr(t, "__name__", str(t)) for t in team_manager.tools]
    assert names == ["incident_manager", "record_case_analysis", "record_conversation_target"]


# --- Prompt contract: incident_manager (semantic consistency) -----------


def test_semantic_definitions_are_centralized_not_duplicated() -> None:
    """Patterns D-H reference the single shared "SEMANTIC CLASSIFICATION"
    section rather than each restating their own definition -- there is
    only one definition per category, not a per-pattern copy that could
    drift out of consistency with another.
    """
    assert INCIDENT_MANAGER_INSTRUCTION.count("SEMANTIC CLASSIFICATION") >= 2
    assert INCIDENT_MANAGER_INSTRUCTION.count("Decision confidence rule:") == 1
    assert INCIDENT_MANAGER_INSTRUCTION.count("Consistency across framings:") == 1


def test_prompt_states_the_conservative_decision_vs_proposal_rule() -> None:
    assert "prefer proposal" in INCIDENT_MANAGER_INSTRUCTION
    assert "prefer open question" in INCIDENT_MANAGER_INSTRUCTION


def test_prompt_states_consistency_between_focused_and_broad_requests() -> None:
    assert "same rigor" in INCIDENT_MANAGER_INSTRUCTION
    assert "must not disagree" in INCIDENT_MANAGER_INSTRUCTION
    assert "not an inconsistency" in INCIDENT_MANAGER_INSTRUCTION


def test_prompt_requires_genuine_inspection_before_absence_claims() -> None:
    assert "Never conclude a category is empty without" in INCIDENT_MANAGER_INSTRUCTION


def test_prompt_documents_latest_earliest_message_handling() -> None:
    assert "MESSAGE CHRONOLOGY" in INCIDENT_MANAGER_INSTRUCTION
    assert "LAST entry in `messages`" in INCIDENT_MANAGER_INSTRUCTION
    assert "FIRST entry in `messages`" in INCIDENT_MANAGER_INSTRUCTION
    assert "never derived from `evidence`" in INCIDENT_MANAGER_INSTRUCTION
