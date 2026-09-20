"""Tests for backend/api/clarification_service.py -- Clarification Continuity state machine.
"""
from backend.api.clarification_service import (
    ClarificationType,
    PendingClarificationRequest,
    clear_pending_clarification,
    get_pending_clarification,
    set_pending_clarification,
)


def test_clarification_request_lifecycle() -> None:
    state: dict = {}

    # Initially empty
    assert get_pending_clarification(state) is None

    # Set pending clarification
    req = PendingClarificationRequest(
        request_id="clarify-001",
        originating_agent="technical_authority_engineer",
        clarification_type=ClarificationType.PARAMETER_VALUE,
        prompt_to_user="Which board slot is affected (e.g. SLOT-4-DUS)?",
        expected_field_name="board_slot",
        context_payload={"procedure_id": "MOP-ERICSSON-01", "node_id": "NODE-B71"},
    )
    set_pending_clarification(state, req)

    # Retrieve
    retrieved = get_pending_clarification(state)
    assert retrieved is not None
    assert retrieved.request_id == "clarify-001"
    assert retrieved.originating_agent == "technical_authority_engineer"
    assert retrieved.clarification_type == ClarificationType.PARAMETER_VALUE
    assert retrieved.expected_field_name == "board_slot"
    assert retrieved.context_payload["node_id"] == "NODE-B71"

    # Clear
    cleared = clear_pending_clarification(state)
    assert cleared is not None
    assert cleared.request_id == "clarify-001"
    assert get_pending_clarification(state) is None


def test_clarification_malformed_state_handling() -> None:
    state = {"pending_clarification_request": "not-a-dict"}
    assert get_pending_clarification(state) is None

    state = {"pending_clarification_request": {"incomplete": "payload"}}
    assert get_pending_clarification(state) is None
