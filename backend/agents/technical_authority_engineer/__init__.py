"""Technical Authority Engineer specialist package (Phase 6A).

Historical alias: Troubleshooting Manager.
"""
from backend.agents.technical_authority_engineer.agent import (
    technical_authority_engineer,
    technical_authority_engineer_tool,
    troubleshooting_manager,
    troubleshooting_manager_tool,
)
from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
from backend.agents.technical_authority_engineer.prompts import TECHNICAL_AUTHORITY_ENGINEER_INSTRUCTION
from backend.agents.technical_authority_engineer.schemas import (
    ApprovedCommand,
    DiagnosticStep,
    EvidenceReference,
    TechnicalAuthorityOutcome,
    TechnicalAuthorityRequest,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.validation import (
    enforce_technical_authority_response_integrity,
    is_command_grounded,
    validate_technical_authority_payload,
)

__all__ = [
    "ApprovedCommand",
    "DiagnosticStep",
    "EvidenceReference",
    "TECHNICAL_AUTHORITY_ENGINEER_INSTRUCTION",
    "TechnicalAuthorityAgentTool",
    "TechnicalAuthorityOutcome",
    "TechnicalAuthorityRequest",
    "TechnicalAuthorityResponse",
    "enforce_technical_authority_response_integrity",
    "is_command_grounded",
    "technical_authority_engineer",
    "technical_authority_engineer_tool",
    "troubleshooting_manager",
    "troubleshooting_manager_tool",
    "validate_technical_authority_payload",
]
