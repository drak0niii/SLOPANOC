"""Generic Clarification Continuity state machine for SLOPANOC.

Manages pending clarification requests across conversational turns:
- Preserves context when an agent requires user disambiguation (e.g., target chat, missing node ID, parameter value)
- Combines semantic understanding with deterministic validation
- Ensures continuity without resetting the investigation workflow
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class ClarificationType(str, Enum):
    TARGET_SELECTION = "target_selection"
    PARAMETER_VALUE = "parameter_value"
    EVIDENCE_CONFIRMATION = "evidence_confirmation"
    GENERAL = "general"


class PendingClarificationRequest(BaseModel):
    request_id: str = Field(description="Unique request ID")
    originating_agent: str = Field(description="Agent requiring clarification: team_manager, incident_manager, technical_authority_engineer")
    clarification_type: ClarificationType = Field(default=ClarificationType.GENERAL)
    prompt_to_user: str = Field(description="Question or prompt presented to the user")
    expected_field_name: str = Field(description="Field name expected in the reply, e.g. 'board_slot' or 'node_id'")
    context_payload: dict[str, Any] = Field(default_factory=dict, description="Preserved caller state needed to resume execution")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


_PENDING_CLARIFICATION_KEY = "pending_clarification_request"


def set_pending_clarification(state: dict[str, Any], request: PendingClarificationRequest) -> None:
    """Stores a pending clarification request into session state."""
    state[_PENDING_CLARIFICATION_KEY] = request.model_dump(mode="json")


def get_pending_clarification(state: dict[str, Any]) -> Optional[PendingClarificationRequest]:
    """Retrieves the active pending clarification request from session state if any."""
    raw = state.get(_PENDING_CLARIFICATION_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        return PendingClarificationRequest.model_validate(raw)
    except Exception:
        return None


def clear_pending_clarification(state: dict[str, Any]) -> Optional[PendingClarificationRequest]:
    """Clears and returns the pending clarification request from session state."""
    req = get_pending_clarification(state)
    state[_PENDING_CLARIFICATION_KEY] = None
    return req
