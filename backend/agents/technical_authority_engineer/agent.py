"""Technical Authority Engineer specialist agent definition.

Historical alias: Troubleshooting Manager (Phase 6A).

This specialist evaluates verified diagnostic evidence and symptoms, distinguishes
facts from hypotheses, identifies missing diagnostic info, and recommends at most
one evidence-supported next check.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from backend.observability.adk_adapter import ObservedAgent as Agent
from google.genai import types

from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
from backend.agents.technical_authority_engineer.procedure_actions import procedure_action_catalog
from backend.agents.technical_authority_engineer.prompts import TECHNICAL_AUTHORITY_ENGINEER_INSTRUCTION
from backend.agents.technical_authority_engineer.schemas import (
    TechnicalAuthorityRequest,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.validation import (
    _find_incoming_request_text,
    enforce_technical_authority_response_integrity,
)
from backend.api.applicability_context_capture import register_known_applicability_context
from backend.api.perf_timing import after_model_call, before_model_call
from backend.api.turn_context import current_run_id
from backend.config.settings import get_settings, get_shared_llm
from backend.observability.model_adapter import instrument_model
from backend.knowledge.domain.applicability import ApplicabilityContext
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

_settings = get_settings()


async def capture_technical_authority_applicability_context(callback_context: Any) -> Optional[types.Content]:
    """ADK before_agent_callback for technical_authority_engineer.

    Registers trusted known_applicability_facts into the run-scoped store
    before knowledge tools are invoked.
    """
    text = _find_incoming_request_text(callback_context)
    if not text:
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    raw_facts = payload.get("known_applicability_facts")
    if not raw_facts or not isinstance(raw_facts, dict):
        return None

    try:
        context = ApplicabilityContext(dimensions=raw_facts)
        register_known_applicability_context(current_run_id(), context)
    except Exception:
        pass
    return None


technical_authority_engineer = Agent(
    name="technical_authority_engineer",
    model=instrument_model(get_shared_llm(_settings.gemini_model), "technical_authority_engineer", "specialist_reasoning"),
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
        # Server-issued governed ProcedureAction catalog for SELECTED evidence (Tranche 2)
        procedure_action_catalog,
    ],
    input_schema=TechnicalAuthorityRequest,
    output_schema=TechnicalAuthorityResponse,
    before_agent_callback=capture_technical_authority_applicability_context,
    after_agent_callback=enforce_technical_authority_response_integrity,
    before_model_callback=before_model_call("technical_authority_engineer"),
    after_model_callback=after_model_call("technical_authority_engineer"),
)

# Historical alias preserved for traceability (docs/AGENT_CONTRACT.md, docs/MASTER_ROADMAP.md)
troubleshooting_manager = technical_authority_engineer

technical_authority_engineer_tool = TechnicalAuthorityAgentTool(agent=technical_authority_engineer)
troubleshooting_manager_tool = technical_authority_engineer_tool
