"""Schema tests for the `decisions`/`actions`/`proposals`/`open_questions`/
`risks` fields on `IncidentManagerResponse`.

Semantic classification quality itself is an LLM reasoning task and is
deliberately NOT unit-tested here (per this task's test strategy: "do not
attempt to unit-test Gemini's semantic judgment itself"). These tests
cover only the schema/structure guarantees: categories stay separate,
optional fields default to `None`/empty, and the existing (unchanged)
evidence mechanism remains fully compatible.
"""
from __future__ import annotations

from backend.agents.incident_manager.evidence import validate_evidence
from backend.agents.incident_manager.schemas import (
    ActionItem,
    DecisionItem,
    IncidentManagerOutcome,
    IncidentManagerResponse,
    OpenQuestionItem,
    ProposalItem,
    RiskItem,
    TeamsEvidence,
)


def test_schema_keeps_categories_as_separate_fields() -> None:
    response = IncidentManagerResponse(
        outcome=IncidentManagerOutcome.OK,
        chat_id="c1",
        chat_title="Ops",
        summary="Summary text.",
        decisions=[DecisionItem(decision="Move deployment to Friday.")],
        actions=[ActionItem(action="Update the script.")],
        proposals=[ProposalItem(proposal="Try caching.")],
        open_questions=[OpenQuestionItem(question="Which environment?")],
        risks=[RiskItem(risk="Approval may delay deployment.")],
    )

    assert len(response.decisions) == 1
    assert len(response.actions) == 1
    assert len(response.proposals) == 1
    assert len(response.open_questions) == 1
    assert len(response.risks) == 1
    assert response.decisions[0].decision == "Move deployment to Friday."
    assert response.actions[0].action == "Update the script."
    assert response.proposals[0].proposal == "Try caching."
    assert response.open_questions[0].question == "Which environment?"
    assert response.risks[0].risk == "Approval may delay deployment."


def test_action_item_missing_owner_stays_none() -> None:
    assert ActionItem(action="Update the documentation.").owner is None


def test_action_item_missing_due_date_stays_none() -> None:
    assert ActionItem(action="Update the documentation.").due_date is None


def test_action_item_missing_status_stays_none() -> None:
    assert ActionItem(action="Update the documentation.").status is None


def test_action_item_can_hold_all_explicit_fields() -> None:
    item = ActionItem(
        action="Update the deployment script.",
        owner="User B",
        due_date="2026-08-28",
        status="in progress",
    )
    assert item.owner == "User B"
    assert item.due_date == "2026-08-28"
    assert item.status == "in progress"


def test_open_question_missing_owner_stays_none() -> None:
    assert OpenQuestionItem(question="Which environment?").owner is None


def test_risk_item_missing_mitigation_stays_none() -> None:
    assert RiskItem(risk="Approval may delay deployment.").mitigation is None


def test_risk_and_mitigation_remain_separate_fields() -> None:
    item = RiskItem(
        risk="Required approval may delay deployment.",
        mitigation="Submit the approval request early.",
    )
    assert item.risk == "Required approval may delay deployment."
    assert item.mitigation == "Submit the approval request early."
    assert item.risk != item.mitigation


def test_all_categories_default_to_empty_lists() -> None:
    response = IncidentManagerResponse(outcome=IncidentManagerOutcome.OK)
    assert response.decisions == []
    assert response.actions == []
    assert response.proposals == []
    assert response.open_questions == []
    assert response.risks == []


def test_empty_categories_remain_a_valid_response() -> None:
    """An "ok" response that never populates any semantic category (e.g.
    a plain factual answer) is still perfectly valid -- these fields are
    never required.
    """
    response = IncidentManagerResponse(
        outcome=IncidentManagerOutcome.OK,
        chat_id="c1",
        chat_title="Ops",
        summary="A direct factual answer.",
    )
    assert response.decisions == []
    assert response.summary == "A direct factual answer."


def test_evidence_field_is_unaffected_by_new_categories() -> None:
    """The existing evidence mechanism is untouched -- semantic
    categories exist alongside it, not through a second evidence system.
    """
    response = IncidentManagerResponse(
        outcome=IncidentManagerOutcome.OK,
        evidence=[TeamsEvidence(message_id="m1", author="User A", sent_at="2026-08-25T09:00:00Z")],
        decisions=[DecisionItem(decision="Move deployment to Friday.")],
    )
    assert response.evidence == [
        TeamsEvidence(message_id="m1", author="User A", sent_at="2026-08-25T09:00:00Z")
    ]


def test_existing_evidence_validation_still_works_unchanged() -> None:
    """`validate_evidence` (evidence.py) operates only on the flat,
    top-level `evidence` list and required zero changes for the new
    categories to exist alongside it.
    """
    evidence = [
        {"message_id": "m1", "author": "User A", "sent_at": "2026-08-25T09:00:00Z"},
        {"message_id": "invented", "author": "Nobody", "sent_at": "x"},
    ]

    result = validate_evidence(evidence, known_message_ids={"m1"})

    assert result == [{"message_id": "m1", "author": "User A", "sent_at": "2026-08-25T09:00:00Z"}]


def test_extra_fields_on_a_semantic_item_degrade_safely() -> None:
    """Extra/unexpected keys in a semantic item's data must not raise --
    pydantic's default "ignore extra fields" behavior (unchanged from the
    rest of this codebase's schemas) keeps this degrading safely rather
    than crashing the whole response.
    """
    item = DecisionItem.model_validate(
        {"decision": "Move deployment to Friday.", "unexpected_field": "value"}
    )
    assert item.decision == "Move deployment to Friday."
    assert not hasattr(item, "unexpected_field")


def test_missing_optional_fields_in_raw_dicts_degrade_safely() -> None:
    """A semantic item dict that omits every optional field entirely
    (not even `null`) still parses cleanly.
    """
    action = ActionItem.model_validate({"action": "Update the documentation."})
    assert action.owner is None
    assert action.due_date is None
    assert action.status is None


def test_output_schema_still_wired_correctly_with_new_fields() -> None:
    from backend.agents.incident_manager.agent import incident_manager

    assert incident_manager.output_schema is IncidentManagerResponse
