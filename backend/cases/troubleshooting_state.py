"""Persistent Troubleshooting State domain model for SLOPANOC.

Persists the continuous technical diagnostic lifecycle across turns and sessions,
tracking:
- Active fault identity and observed symptoms
- Current working hypothesis and competing hypotheses
- Verified evidence references and chronological check history
- Applicable governed operational procedures
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from pydantic import BaseModel, Field


class TroubleshootingStatus(str, Enum):
    INVESTIGATING = "investigating"
    HYPOTHESIS_FORMULATED = "hypothesis_formulated"
    TESTING_NEXT_STEP = "testing_next_step"
    MITIGATION_RECOMMENDED = "mitigation_recommended"
    ESCALATION_REQUIRED = "escalation_required"
    RESOLVED = "resolved"


class DiagnosticCheckRecord(BaseModel):
    check_id: str
    action: str
    rationale: str
    grounded_command: Optional[str] = None
    command_source_id: Optional[str] = None
    expected_observation: str
    observed_result: Optional[str] = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TroubleshootingState(BaseModel):
    fault_id: str = Field(description="Unique identifier for the active fault/incident under investigation")
    status: TroubleshootingStatus = Field(default=TroubleshootingStatus.INVESTIGATING)
    symptom_summary: str = Field(description="Summary of initial observed symptoms or alarm triggers")
    working_hypothesis: Optional[str] = Field(default=None, description="Primary isolated hypothesis being evaluated")
    competing_hypotheses: list[str] = Field(default_factory=list, description="Alternative hypotheses not yet eliminated")
    verified_evidence_ids: list[str] = Field(default_factory=list, description="IDs of confirmed evidence items")
    diagnostic_history: list[DiagnosticCheckRecord] = Field(default_factory=list, description="History of checks performed")
    applicable_procedure_ids: list[str] = Field(default_factory=list, description="Applicable governed MOP/SOP IDs")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
