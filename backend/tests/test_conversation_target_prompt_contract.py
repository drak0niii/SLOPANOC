"""Prompt-contract and structural tests for the semantic scope /
conversation-target bug fix. Mirrors this suite's established approach:
pin instruction text, never an exact model decision; separately prove, by
source inspection, that no natural-language routing exists in the
deterministic code that touches this flow.
"""
from __future__ import annotations

import inspect

from backend.agents.team_manager import agent as team_manager_agent_module
from backend.agents.team_manager import conversation_target as conversation_target_module
from backend.agents.team_manager.conversation_target import record_conversation_target
from backend.agents.team_manager import prompts as team_manager_prompts_module
from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION
from backend.api import chat_service as chat_service_module
from backend.api import conversation_target_capture as capture_module

_TM = " ".join(TEAM_MANAGER_INSTRUCTION.split())


# --- Prompt contract ---------------------------------------------------


def test_prompt_defines_all_three_conversation_targets() -> None:
    assert "current_thread:" in _TM
    assert "selected_external_conversation:" in _TM
    assert "explicit_external_conversation:" in _TM


def test_prompt_requires_recording_the_target_before_anything_else() -> None:
    assert "call `record_conversation_target`" in _TM


def test_prompt_states_selected_chat_never_implies_current_conversation_means_teams() -> None:
    assert (
        "never, by itself, a reason to treat a vague reference" in _TM
    )


def test_prompt_states_current_request_target_controls_execution_not_prior_selection() -> None:
    assert "A structured external selection is context, never" in _TM
    assert "the target for the CURRENT request controls what" in _TM


def test_prompt_forbids_incident_manager_for_current_thread() -> None:
    assert "do NOT call `incident_manager` for this request -- not" in _TM


def test_prompt_forbids_leaking_internal_reasoning_in_a_current_thread_summary() -> None:
    assert "internal reasoning, tool call/response structures, system/developer" in _TM


def test_prompt_forbids_silently_enriching_current_thread_with_unrelated_case_context() -> None:
    assert "Do not silently pull in Case context or other" in _TM


def test_prompt_permits_a_brief_clarifying_question_only_when_genuinely_ambiguous() -> None:
    assert "If it is genuinely unclear which of the three is meant, ask one brief" in _TM
    assert "this should be rare" in _TM


def test_prompt_defines_the_two_external_targets_as_differing_only_in_chat_resolution() -> None:
    assert "differ only in which chat step 1 resolves to" in _TM


def test_module_docstring_explains_this_is_not_natural_language_classification() -> None:
    doc = " ".join((team_manager_prompts_module.__doc__ or "").split())
    assert "record_conversation_target" in doc
    assert "never inspects the user's wording itself" in doc


# --- Structural: no hardcoded language routing exists in code ----------


def test_conversation_target_module_contains_no_regex_or_keyword_routing() -> None:
    """The module's OWN docstrings legitimately quote the reported bug
    ("this chat window") and explain what this tool deliberately does
    NOT do (using the word "keyword" itself, mirroring prompts.py's own
    module docstring) -- so scanning comments for those words is not a
    useful signal here. The real, structural proof is the absence of any
    regex/string-matching machinery and any raw-user-text parameter in
    the actual function signature this tool exposes.
    """
    source = inspect.getsource(conversation_target_module)
    for forbidden in ("import re", "re.compile(", "re.match(", "re.search(", "re.findall("):
        assert forbidden not in source
    for forbidden_name in ("user_text", "utterance", "user_message"):
        assert forbidden_name not in source.lower()
    import inspect as _inspect

    signature = _inspect.signature(record_conversation_target)
    assert list(signature.parameters) == ["target", "tool_context"]


def test_conversation_target_capture_module_never_inspects_message_text() -> None:
    """The capture class only ever reads a tool's already-structured
    `response` dict (the `target` the model declared) -- never any raw
    text field."""
    source = inspect.getsource(capture_module)
    for forbidden in ("import re", "re.compile(", ".lower()", ".text", "message_text"):
        assert forbidden not in source


def test_chat_service_module_has_no_new_text_based_routing_for_conversation_target() -> None:
    """Extends the existing latency-investigation proof
    (test_team_manager_delegation_scope_prompt_contract.py) to the new
    conversation-target wiring specifically: chat_service.py only ever
    reads the ALREADY-STRUCTURED `ConversationTargetCapture.target`
    property, never the user's raw message text, to decide anything.
    """
    source = inspect.getsource(chat_service_module)
    for forbidden in (
        '"this chat"',
        "'this chat'",
        '"chat window"',
        "'chat window'",
        "message_text.lower()",
        "in message_text",
        "re.match",
        "re.search",
    ):
        assert forbidden not in source


def test_team_manager_agent_module_still_contains_no_regex_routing() -> None:
    import re as _re

    source = inspect.getsource(team_manager_agent_module)
    assert not _re.search(r"(?<!\w)import re(?!\w)", source)
    assert "re.compile(" not in source
