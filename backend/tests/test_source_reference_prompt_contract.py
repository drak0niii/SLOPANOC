"""Prompt-contract tests for the structured Teams source/provenance UX
milestone (pre-4H) -- pins the deterministic instruction wording that
removes the old prose provenance footer in favor of the structured
Source reference, mirroring test_selection_prompt_contract.py's own
approach: pin instruction text, never an exact Gemini sentence, and
prove no hardcoded canned prose was introduced.
"""
from __future__ import annotations

from backend.agents.team_manager.prompts import TEAM_MANAGER_INSTRUCTION

_TM = " ".join(TEAM_MANAGER_INSTRUCTION.split())


def test_prompt_no_longer_sanctions_citing_evidence_authors_and_timestamps_in_prose() -> None:
    """The exact removed instruction that produced "This information is
    based on messages sent by X between Y and Z"-style footers."""
    assert "you may cite them naturally" not in _TM.lower()
    assert "User A -- 31 Aug" not in TEAM_MANAGER_INSTRUCTION


def test_prompt_forbids_a_provenance_footer_and_defers_to_the_source_reference() -> None:
    assert "do NOT append a provenance/citation footer" in _TM
    assert "do not enumerate `evidence`'s `author`/`sent_at` entries as a proof paragraph" in _TM
    assert "A separate, structured Source reference is attached to your answer automatically" in _TM
    assert "rely on it; do not restate what it already shows in prose" in _TM


def test_prompt_no_longer_ties_omitting_the_footer_to_being_concise() -> None:
    """UX/provenance refinement pass: the earlier wording tied "don't
    restate the Source reference in prose" to "keep your answer concise,"
    which was found to over-compress the substantive answer itself, not
    just the removed footer. That coupling is gone."""
    assert "keep your answer concise" not in _TM


def test_prompt_explicitly_forbids_letting_the_footer_removal_shorten_the_answer() -> None:
    assert "never a license to compress or truncate the substantive answer itself" in _TM
    assert "present the full extent of what incident_manager's structured result actually contains" in _TM


def test_prompt_does_not_hardcode_one_exact_replacement_sentence() -> None:
    """The fix must not simply swap one canned sentence for another --
    there is no single fixed phrase the model is instructed to reproduce
    verbatim for a grounded answer."""
    # A hardcoded replacement sentence would show up as a long quoted
    # string immediately following the new guidance; the actual
    # instruction is guidance about what NOT to do, not a template.
    assert '"This information is based on' not in TEAM_MANAGER_INSTRUCTION
    assert "e.g. \"User A" not in TEAM_MANAGER_INSTRUCTION


def test_prompt_still_forbids_internal_identifiers_in_the_answer() -> None:
    assert "never mention tool names, message ids, chat ids, or any other" in _TM


def test_prompt_explicitly_separates_the_evidence_display_cap_from_answer_depth() -> None:
    """Pins the exact separation the refinement pass requires: the Source
    drawer's Supporting Evidence cap is a display-only limit and must
    never be treated as a reason to say less."""
    assert "Supporting Evidence examples are a small, capped display sample" in _TM
    assert "have no bearing whatsoever on how much of `summary`/the structured fields you present" in _TM


def test_prompt_requires_comprehensive_coverage_for_a_general_summary() -> None:
    assert "present every populated category in full, since all" in _TM
