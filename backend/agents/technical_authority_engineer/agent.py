"""Technical Authority Engineer specialist agent definition.

Historical alias: Troubleshooting Manager (Phase 6A).

This specialist evaluates verified diagnostic evidence and symptoms, distinguishes
facts from hypotheses, identifies missing diagnostic info, and recommends at most
one evidence-supported next check.
"""
from __future__ import annotations

from google.adk.agents import Agent

from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
from backend.agents.technical_authority_engineer.prompts import TECHNICAL_AUTHORITY_ENGINEER_INSTRUCTION
from backend.agents.technical_authority_engineer.schemas import (
    TechnicalAuthorityRequest,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.validation import (
    enforce_technical_authority_response_integrity,
)
from backend.api.perf_timing import after_model_call, before_model_call
from backend.config.settings import get_settings, get_shared_llm
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

_settings = get_settings()

technical_authority_engineer = Agent(
    name="technical_authority_engineer",
    model=get_shared_llm(_settings.gemini_model),
    description=(
        "Level 2 Technical Authority advisory specialist for technical problem "
        "interpretation, diagnostic strategy, governed procedure evaluation, "
        "and evidence-grounded next-step recommendations."
    ),
    instruction=TECHNICAL_AUTHORITY_ENGINEER_INSTRUCTION,
    tools=[
        # Consumes the shared Generic Knowledge Management context service
        knowledge_search,
        knowledge_select_evidence,
    ],
    input_schema=TechnicalAuthorityRequest,
    output_schema=TechnicalAuthorityResponse,
    after_agent_callback=enforce_technical_authority_response_integrity,
    before_model_callback=before_model_call("technical_authority_engineer"),
    after_model_callback=after_model_call("technical_authority_engineer"),
)

# Historical alias preserved for traceability (docs/AGENT_CONTRACT.md, docs/MASTER_ROADMAP.md)
troubleshooting_manager = technical_authority_engineer

technical_authority_engineer_tool = TechnicalAuthorityAgentTool(agent=technical_authority_engineer)
troubleshooting_manager_tool = technical_authority_engineer_tool
