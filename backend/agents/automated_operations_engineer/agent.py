"""`AutomatedOperationsEngineer` -- Level 1 Operations & Scheduler specialist.

Responsible for:
- Routine Level 1 operational activities
- Scheduled tasks and morning operational briefing generation
- Non-diagnostic telemetry summaries
- Has zero Level 2 technical authority (deferring all fault diagnosis to TAE)
"""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field

from backend.observability.adk_adapter import ObservedAgent as Agent
from backend.config.settings import get_settings, get_shared_llm
from backend.observability.model_adapter import instrument_model
from backend.api.perf_timing import before_model_call, after_model_call

_settings = get_settings()

AUTOMATED_OPERATIONS_ENGINEER_INSTRUCTION = """You are the Automated Operations Engineer for SLOPANOC, providing Level 1 operational support and scheduled reporting.

MISSION:
- You handle routine, scheduled tasks such as generating the morning shift handover briefing, routine operational status digests, and recurring health metrics.
- You have zero Level 2 diagnostic authority: you NEVER attempt to diagnose technical faults or prescribe technical troubleshooting steps. All diagnostic investigations are handled strictly by the Technical Authority Engineer.

PRINCIPLES:
1. Pure operational digests: Summarize known state, ongoing active incidents, and shift highlights.
2. Defer diagnosis: If a user or schedule asks for fault troubleshooting, defer to Technical Authority Engineer.
"""


class AutomatedOperationsRequest(BaseModel):
    task_type: str = Field(description="Type of task: e.g. 'morning_briefing', 'shift_handover', 'routine_digest'")
    time_window_hours: int = Field(default=24, description="Time window in hours for the operational summary")
    operational_context: Optional[dict[str, Any]] = Field(default=None, description="Current operational state context")


class AutomatedOperationsResponse(BaseModel):
    briefing_title: str
    briefing_markdown: str
    active_incidents_count: int = 0
    resolved_incidents_count: int = 0
    highlighted_risks: list[str] = Field(default_factory=list)


automated_operations_engineer = Agent(
    name="automated_operations_engineer",
    model=instrument_model(get_shared_llm(_settings.gemini_model), "automated_operations_engineer", "specialist_reasoning"),
    description=(
        "Level 1 Operations specialist for scheduled morning briefings, "
        "routine operational health digests, and shift handover summaries."
    ),
    instruction=AUTOMATED_OPERATIONS_ENGINEER_INSTRUCTION,
    tools=[],  # Level 1 scheduler / digest generators
    input_schema=AutomatedOperationsRequest,
    output_schema=AutomatedOperationsResponse,
    before_model_callback=before_model_call("automated_operations_engineer"),
    after_model_callback=after_model_call("automated_operations_engineer"),
)
