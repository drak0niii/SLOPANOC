"""Contract tests for the synthetic semantic-classification fixtures
(_semantic_fixtures.py).

Per this task's test strategy, these check only that each fixture is
well-formed and survives the deterministic retrieval pipeline unchanged
-- never what a live model would conclude from it. Live-Gemini validation
of the actual classification quality is deferred to a not-yet-implemented
"final acceptance" step. A few light keyword-presence checks confirm each
fixture is internally consistent with the category it is meant to
illustrate (a check on what *we* wrote, not on model judgment).
"""
from __future__ import annotations

import pytest

from backend.gateway import power_automate_client as pac_module
from backend.tests._fakes import FakeResponse
from backend.tests._semantic_fixtures import (
    ACTION_WITH_OWNER,
    ACTION_WITHOUT_OWNER,
    ALL_FIXTURES,
    DECISION_CONFIRMED,
    MIXED_CONVERSATION,
    OPEN_QUESTION,
    PROPOSAL_ONLY,
    RISK_WITH_MITIGATION,
)
from backend.tools.teams.get_messages import teams_get_messages

_GENERIC_AUTHORS = {"User A", "User B", "User C"}


@pytest.mark.parametrize("name", sorted(ALL_FIXTURES))
def test_fixture_retrieves_cleanly(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = ALL_FIXTURES[name]
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(200, fixture))

    result = teams_get_messages(chat_id="c1")

    assert "error" not in result
    assert result["retrieved_count"] == len(fixture)
    assert [m["id"] for m in result["messages"]] == [m["id"] for m in fixture]
    assert result["coverage"]["status"] == "complete"


def test_all_fixtures_use_only_generic_placeholder_names() -> None:
    for name, fixture in ALL_FIXTURES.items():
        for msg in fixture:
            assert msg["senderName"] in _GENERIC_AUTHORS, (
                f"fixture {name!r} uses a non-generic sender name: "
                f"{msg['senderName']!r}"
            )


def test_decision_confirmed_fixture_contains_explicit_agreement_language() -> None:
    last_message_text = DECISION_CONFIRMED[-1]["content"].lower()
    assert "confirmed" in last_message_text or "agreed" in last_message_text


def test_proposal_only_fixture_contains_no_agreement_language() -> None:
    for msg in PROPOSAL_ONLY:
        text = msg["content"].lower()
        assert "confirmed" not in text
        assert "agreed" not in text
        assert "decided" not in text


def test_action_with_owner_fixture_names_the_owner_explicitly() -> None:
    assignment_text = ACTION_WITH_OWNER[0]["content"]
    assert "User B" in assignment_text


def test_action_without_owner_fixture_never_names_an_owner() -> None:
    for msg in ACTION_WITHOUT_OWNER:
        assert "User A" not in msg["content"]
        assert "User B" not in msg["content"]


def test_open_question_fixture_is_never_resolved() -> None:
    combined_text = " ".join(msg["content"].lower() for msg in OPEN_QUESTION)
    assert "?" in combined_text
    assert "confirmed" not in combined_text
    assert "decided" not in combined_text


def test_risk_and_mitigation_fixture_keeps_them_as_separate_messages() -> None:
    assert len(RISK_WITH_MITIGATION) == 2
    risk_text = RISK_WITH_MITIGATION[0]["content"].lower()
    mitigation_text = RISK_WITH_MITIGATION[1]["content"].lower()
    assert "delay" in risk_text
    assert "submit" in mitigation_text or "early" in mitigation_text


def test_mixed_conversation_fixture_has_enough_variety_for_all_categories() -> None:
    assert len(MIXED_CONVERSATION) == 6
    authors = {msg["senderName"] for msg in MIXED_CONVERSATION}
    assert authors == _GENERIC_AUTHORS
    combined_text = " ".join(msg["content"].lower() for msg in MIXED_CONVERSATION)
    assert "confirmed" in combined_text  # decision signal
    assert "?" in combined_text  # open question signal
    assert "delay" in combined_text  # risk signal
