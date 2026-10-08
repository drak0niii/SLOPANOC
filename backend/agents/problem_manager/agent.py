"""`ProblemManager` -- ITIL Problem Management & Root Cause Analysis specialist.

Responsible for:
- Post-incident root cause analysis (RCA)
- Identifying underlying structural defects across telecommunications systems
- Known error database (KEDB) alignment
- Outlook briefing generation and SharePoint RCA documentation
"""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field

from backend.observability.adk_adapter import ObservedAgent as Agent
from backend.config.settings import get_settings, get_shared_llm
from backend.observability.model_adapter import instrument_model
from backend.api.perf_timing import before_model_call, after_model_call

_settings = get_settings()

PROBLEM_MANAGER_INSTRUCTION = """You are the Problem Manager for SLOPANOC, operating under ITIL Problem Management standards for telecommunications networks (RAN, Core, Transport, Cloud).

MISSION:
- You own post-incident investigation, Root Cause Analysis (RCA), and preventative resolution.
- You analyze completed incident timelines, technical authority diagnostic conclusions, and verified evidence.
- You determine the fundamental root cause (technical, procedural, environmental) rather than merely recording symptoms.
- You formulate permanent workarounds, permanent fixes, and post-incident documentation for SharePoint and Outlook distribution.

PRINCIPLES:
1. Dynamic, grounded analysis: Never use canned or hardcoded vendor scenarios. Reason from actual case context.
2. Fact-based RCA: Trace the causal chain from trigger to failure to impact using verified evidence.
"""


class ProblemManagerRequest(BaseModel):
    case_id: Optional[str] = Field(default=None, description="Case or incident ID for RCA")
    incident_summary: str = Field(description="Summary of the resolved or active incident")
    incident_timeline: Optional[list[dict[str, Any]]] = Field(default=None, description="Timeline of observed events")
    technical_diagnostic_findings: Optional[dict[str, Any]] = Field(default=None, description="Findings from Technical Authority Engineer")


class ProblemManagerResponse(BaseModel):
    problem_id: Optional[str] = None
    root_cause_analysis: str = Field(description="Detailed causal explanation of the root cause")
    contributing_factors: list[str] = Field(default_factory=list, description="Secondary or environmental factors")
    preventative_actions: list[str] = Field(default_factory=list, description="Recommended permanent fixes or procedural updates")
    sharepoint_rca_draft: Optional[str] = Field(default=None, description="Draft document text for SharePoint publishing")
    outlook_briefing_draft: Optional[str] = Field(default=None, description="Draft stakeholder email briefing")


problem_manager = Agent(
    name="problem_manager",
    model=instrument_model(get_shared_llm(_settings.gemini_model), "problem_manager", "specialist_reasoning"),
    description=(
        "ITIL Problem Management specialist for root cause analysis (RCA), "
        "known error documentation, and post-incident reporting."
    ),
    instruction=PROBLEM_MANAGER_INSTRUCTION,
    tools=[],  # Future: outlook_search_emails, outlook_send_mail, sharepoint_get_document, sharepoint_upload_rca
    input_schema=ProblemManagerRequest,
    output_schema=ProblemManagerResponse,
    before_model_callback=before_model_call("problem_manager"),
    after_model_callback=after_model_call("problem_manager"),
)
