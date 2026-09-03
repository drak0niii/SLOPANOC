"""Prompt-contract tests for the supporting-evidence snippet-authenticity
fix -- pins that incident_manager is no longer asked to produce a
`snippet` at all (that responsibility moved entirely to deterministic
backend code, see source_reference.py), so evidence display never again
depends on optional model output. Mirrors the other prompt-contract tests
in this suite: pin instruction text, never an exact model sentence.
"""
from __future__ import annotations

from backend.agents.incident_manager.prompts import INCIDENT_MANAGER_INSTRUCTION

_IM = " ".join(INCIDENT_MANAGER_INSTRUCTION.split())


def test_prompt_no_longer_asks_the_model_to_produce_a_snippet() -> None:
    assert "snippet" not in _IM.lower()


def test_prompt_explains_evidence_is_selection_only_not_excerpting() -> None:
    assert "a separate, deterministic backend step independently builds any excerpt" in _IM
    assert "do not include a quote/excerpt of the message yourself here" in _IM


def test_prompt_still_requires_message_id_author_sent_at_per_evidence_entry() -> None:
    assert "one entry (`message_id`/`author`/`sent_at`," in _IM


def test_prompt_still_forbids_inventing_evidence() -> None:
    assert "copied exactly from the retrieved message -- never invented" in _IM
