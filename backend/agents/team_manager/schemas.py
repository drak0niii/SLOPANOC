"""team_manager has no ADK `input_schema`/`output_schema` of its own in
this slice -- it is the root agent, so its input is the ongoing user
conversation and its output is free-form `Message.text`
(docs/AGENT_CONTRACT.md #12), not a schema-constrained reply.

This module exists (per the requested backend structure) to document, and
give a stable import path to, the structured shapes team_manager's own
reasoning depends on when delegating to incident_manager. The canonical
definitions live in backend/agents/incident_manager/schemas.py, co-located
with the agent whose ADK `input_schema`/`output_schema` fields actually
reference them (see that module's docstring) -- they are re-exported here
purely for discoverability/traceability against
docs/AGENT_CONTRACT.md #8's `IncidentManagerRequest`/`IncidentManagerResponse`
naming. Nothing in team_manager/agent.py needs to import from this module.
"""
from __future__ import annotations

from backend.agents.incident_manager.schemas import (
    ActionItem,
    DecisionItem,
    IncidentManagerOutcome,
    IncidentManagerRequest,
    IncidentManagerResponse,
    OpenQuestionItem,
    ProposalItem,
    RiskItem,
    TeamsEvidence,
)

__all__ = [
    "ActionItem",
    "DecisionItem",
    "IncidentManagerOutcome",
    "IncidentManagerRequest",
    "IncidentManagerResponse",
    "OpenQuestionItem",
    "ProposalItem",
    "RiskItem",
    "TeamsEvidence",
]
