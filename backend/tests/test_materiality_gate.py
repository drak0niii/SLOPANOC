"""Prompt-contract and fixture-integrity tests for the semantic
materiality/relevance gate.

Per this task's test strategy, actual semantic/materiality judgment is a
Gemini reasoning task and is not unit-testable here. These tests instead
verify (a) the instruction text sent to the model establishes the
materiality gate and its consequences for each category, (b) no
deterministic phrase/keyword/regex classifier was introduced, and (c) the
new synthetic fixtures used for later live-model acceptance are
well-formed and internally consistent with the scenario they illustrate.
Whitespace is normalized (`" ".join(text.split())`) before substring
checks because the source instruction wraps prose across multiple
backslash-continued lines with varying indentation -- normalizing avoids
false negatives from that formatting, not from the actual wording.
"""
from __future__ import annotations

import inspect

from backend.agents.incident_manager import prompts as incident_manager_prompts
from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION
from backend.tests._semantic_fixtures import (
    ALL_FIXTURES,
    CASUAL_CONCERN_WITHOUT_MATERIAL_IMPACT,
    MIXED_SUBSTANTIVE_AND_SOCIAL,
    SOCIAL_INCIDENTAL_CONVERSATION,
)

_NORMALIZED_INSTRUCTION = " ".join(INCIDENT_MANAGER_INSTRUCTION.split())
_GENERIC_AUTHORS = {"User A", "User B", "User C"}


# --- Prompt contract: the materiality gate exists and is wired in --------


def test_prompt_establishes_a_materiality_gate_before_classification() -> None:
    assert "MATERIALITY GATE" in _NORMALIZED_INSTRUCTION
    assert (
        "retrieved message -> determine relevance/materiality -> if material, "
        "classify into the matching category" in _NORMALIZED_INSTRUCTION
    )


def test_prompt_defines_incidental_conversation_non_exhaustively() -> None:
    assert "Incidental conversation" in _NORMALIZED_INSTRUCTION
    assert "illustrative only" in _NORMALIZED_INSTRUCTION
    assert "never a list of words or phrasing to detect" in _NORMALIZED_INSTRUCTION


def test_prompt_allows_incidental_content_in_summary_but_not_structured_fields() -> None:
    assert (
        "never as an entry in a structured field" in _NORMALIZED_INSTRUCTION
        or "never as an entry in a structured field" in INCIDENT_MANAGER_INSTRUCTION
    )
    assert "prefer leaving it out of the structured fields" in _NORMALIZED_INSTRUCTION


def test_semantic_classification_bullets_are_gated_by_materiality() -> None:
    assert (
        "an item must both fit the category's definition AND be materially "
        "relevant to qualify" in _NORMALIZED_INSTRUCTION
    )


def test_prompt_requires_open_questions_to_be_materially_unresolved() -> None:
    assert "materially unresolved" in _NORMALIZED_INSTRUCTION
    assert "A conversational question that is merely part of normal dialogue" in _NORMALIZED_INSTRUCTION


def test_prompt_forbids_manufacturing_risk_from_incidental_mentions() -> None:
    assert "Do not manufacture a risk merely because a message mentions" in _NORMALIZED_INSTRUCTION
    assert "inconvenience, annoyance, humor" in _NORMALIZED_INSTRUCTION
    assert "When uncertain whether something is truly a risk, prefer leaving it out" in _NORMALIZED_INSTRUCTION


def test_prompt_requires_mitigation_tied_to_a_valid_material_risk() -> None:
    assert "never populate `mitigation` when there" in _NORMALIZED_INSTRUCTION
    assert "casual advice, jokes, or ordinary conversational behavior is never a mitigation" in _NORMALIZED_INSTRUCTION


def test_prompt_scopes_broad_proposal_discovery_to_materiality() -> None:
    assert "prioritize proposals that materially relate" in _NORMALIZED_INSTRUCTION
    assert (
        "intent changes how much is presented and at what depth, never the "
        "definition of what qualifies as a proposal" in _NORMALIZED_INSTRUCTION
    )


def test_prompt_forbids_forcing_structure_onto_casual_content() -> None:
    assert (
        "A mostly informal or test conversation may legitimately produce only "
        "a short summary with few or no populated structured fields"
        in _NORMALIZED_INSTRUCTION
    )
    assert "do not force enterprise structure onto casual content" in _NORMALIZED_INSTRUCTION


def test_prompt_states_materiality_is_invariant_across_framings() -> None:
    assert "Materiality is likewise invariant" in _NORMALIZED_INSTRUCTION
    assert (
        "must not become material in one response and incidental in another"
        in _NORMALIZED_INSTRUCTION
    )


def test_prompt_distinguishes_summary_from_structured_classification() -> None:
    assert "SUMMARY VS CLASSIFICATION" in _NORMALIZED_INSTRUCTION
    assert "the structured fields stay stricter than the narrative summary" in _NORMALIZED_INSTRUCTION


# --- Structural: no hardcoded materiality/keyword classifier introduced --


def test_incident_manager_prompts_module_contains_no_regex_or_keyword_lists() -> None:
    source = inspect.getsource(incident_manager_prompts)
    for forbidden in ("import re", "re.compile(", "re.match(", "re.search(", "re.findall("):
        assert forbidden not in source
    for forbidden_name in ("keyword_list", "social_words", "incidental_phrases", "SOCIAL_KEYWORDS"):
        assert forbidden_name not in source


def test_incident_manager_prompts_module_defines_no_classifier_functions() -> None:
    """The materiality gate is prose in `INCIDENT_MANAGER_INSTRUCTION`
    only -- this module must not gain any Python function that inspects
    message text to decide materiality.
    """
    members = inspect.getmembers(incident_manager_prompts, inspect.isfunction)
    assert members == []


# --- Fixture integrity: new materiality scenarios are well-formed --------


def test_social_incidental_fixture_has_no_substantive_project_content() -> None:
    combined_text = " ".join(msg["content"].lower() for msg in SOCIAL_INCIDENTAL_CONVERSATION)
    for substantive_marker in ("deploy", "confirmed", "agreed", "decided", "action item", "risk"):
        assert substantive_marker not in combined_text


def test_social_incidental_fixture_uses_only_generic_authors() -> None:
    for msg in SOCIAL_INCIDENTAL_CONVERSATION:
        assert msg["senderName"] in _GENERIC_AUTHORS


def test_mixed_substantive_and_social_fixture_contains_both_kinds_of_content() -> None:
    combined_text = " ".join(msg["content"].lower() for msg in MIXED_SUBSTANTIVE_AND_SOCIAL)
    # Substantive signal: a confirmed decision and an assigned action.
    assert "confirmed" in combined_text
    assert "update the deployment script" in combined_text
    # Incidental signal: greetings/banter interspersed between them.
    assert "good morning" in combined_text
    assert "coffee" in combined_text or "lunch" in combined_text


def test_mixed_substantive_and_social_fixture_interleaves_rather_than_blocks() -> None:
    """Confirms the fixture actually interleaves incidental and substantive
    messages (not two separate blocks) -- otherwise it would not exercise
    per-message materiality judgment the way live testing found the
    failure.
    """
    is_incidental = [
        any(marker in msg["content"].lower() for marker in ("morning", "coffee", "haha", "lunch"))
        for msg in MIXED_SUBSTANTIVE_AND_SOCIAL
    ]
    # At least one incidental message appears strictly between two
    # non-incidental (substantive) messages.
    assert any(
        is_incidental[i] and not is_incidental[i - 1] and not is_incidental[i + 1]
        for i in range(1, len(is_incidental) - 1)
    )


def test_casual_concern_fixture_names_no_material_business_impact() -> None:
    combined_text = " ".join(msg["content"].lower() for msg in CASUAL_CONCERN_WITHOUT_MATERIAL_IMPACT)
    for material_marker in (
        "delay",
        "deadline",
        "schedule",
        "security",
        "compliance",
        "cost",
        "deployment",
        "release",
        "outage",
    ):
        assert material_marker not in combined_text


def test_new_fixtures_are_registered_in_all_fixtures() -> None:
    for name in (
        "social_incidental_conversation",
        "mixed_substantive_and_social",
        "casual_concern_without_material_impact",
    ):
        assert name in ALL_FIXTURES
