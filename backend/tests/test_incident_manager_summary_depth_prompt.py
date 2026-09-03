"""Prompt-contract tests for the "evidence-display cap must never control
answer depth" refinement pass -- pins that a plain Teams summary request
(pattern C) now comprehensively covers every materially-supported
category, exactly like the explicit broader-analysis request (pattern H),
and that the (frontend-only) Supporting Evidence display cap is
explicitly called out as irrelevant to that coverage. Mirrors
test_source_reference_prompt_contract.py's own "pin instruction text,
never an exact model sentence" approach.
"""
from __future__ import annotations

from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION

_IM = " ".join(INCIDENT_MANAGER_INSTRUCTION.split())


def test_default_summary_pattern_covers_every_material_category() -> None:
    assert "the same comprehensive coverage as pattern H" in _IM
    assert (
        "populate whichever of `decisions`/`actions`/`proposals`/`open_questions`/`risks` actually have material"
        in _IM
    )


def test_default_summary_pattern_no_longer_caps_to_a_fixed_point_count() -> None:
    assert "3-6 most important points" not in INCIDENT_MANAGER_INSTRUCTION


def test_prompt_explicitly_separates_evidence_citation_count_from_response_depth() -> None:
    assert (
        "has no bearing on and must never limit this coverage" in _IM
    )
    assert "it never gates how much of the response you may make" in _IM


def test_prompt_still_forbids_padding_incidental_content_into_structured_fields() -> None:
    # The comprehensiveness requirement must not be confused with padding
    # -- MATERIALITY GATE / "do not force enterprise structure onto casual
    # content" still applies.
    assert "never populate a field, or narrate a section in `summary`, just" in _IM
    assert "do not force enterprise structure onto casual content" in _IM


def test_general_rule_treats_comprehensiveness_and_padding_as_independent_axes() -> None:
    assert "never shorten a comprehensive answer (C/H) merely to be brief" in _IM
    assert "these are independent axes" in _IM
