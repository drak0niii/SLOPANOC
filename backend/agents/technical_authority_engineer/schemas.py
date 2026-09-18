"""Structured input and output schemas for the Technical Authority Engineer.

Historical alias: Troubleshooting Manager (Phase 6A).

This specialist:
- Interprets verified technical problems.
- Distinguishes observed facts from hypotheses.
- Identifies missing diagnostic information.
- Evaluates applicable approved knowledge.
- Recommends at most ONE useful next diagnostic check.
- Explains why the check matters and what specific evidence is needed.
- Enforces strict command trust: operational commands must be grounded in an
  approved, verified source catalog.
- Operates in an advisory role only: no direct execution, no configuration
  changes, no approval authority, no Teams write capabilities.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class TechnicalAuthorityOutcome(str, Enum):
    """The high-level disposition of the technical evaluation."""

    RECOMMENDED = "recommended"
    """A single evidence-supported diagnostic step is recommended."""

    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    """Current evidence is insufficient to recommend a reliable next check.
    Missing diagnostic information must be gathered first."""

    ESCALATION_REQUIRED = "escalation_required"
    """The issue cannot be resolved or diagnosed safely with available
    procedures, or policy requires escalation to the system/platform owner."""

    ERROR = "error"
    """A technical evaluation failure occurred (e.g. malformed inputs or
    conflicting unresolvable state)."""


class EvidenceReference(BaseModel):
    """A server-validated evidence item available to the specialist.

    Populated from trusted application state (governed knowledge, Teams
    retrievals, Case context). The model cannot forge these items.
    """

    source_id: str = Field(
        ...,
        description="Unique identifier for the evidence item (e.g. 'doc123:v1:sec2', 'teams:msg987').",
    )
    source_type: str = Field(
        ...,
        description="Type of source: 'governed_knowledge', 'teams_conversation', 'case_context', etc.",
    )
    title: Optional[str] = Field(
        default=None,
        description="Human-readable title or heading of the evidence source.",
    )
    content_snippet: Optional[str] = Field(
        default=None,
        description="Relevant text or diagnostic extract from the verified evidence.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional structured metadata (e.g. section_id, author, timestamp).",
    )


class ApprovedCommand(BaseModel):
    """An approved diagnostic command authorized for the current context.

    Sourced strictly from approved governed knowledge procedures.
    """

    command: str = Field(
        ...,
        description="The exact command string authorized by an approved procedure.",
    )
    source_id: str = Field(
        ...,
        description="The source_id of the approved knowledge document/section containing this command.",
    )
    procedure_section: Optional[str] = Field(
        default=None,
        description="Specific section or procedure step where the command is authorized.",
    )
    restrictions: list[str] = Field(
        default_factory=list,
        description="Safety restrictions or prerequisites associated with this command.",
    )


class DiagnosticStep(BaseModel):
    """A single recommended diagnostic action.

    Strictly at most one step may be provided in a response.
    Operational commands must be grounded in an ApprovedCommand or verified evidence.
    """

    action: str = Field(
        ...,
        description="The single diagnostic action to perform, described clearly in plain language.",
    )
    reason: str = Field(
        ...,
        description="Technical justification for why this check is necessary and what it will isolate or confirm.",
    )
    command: Optional[str] = Field(
        default=None,
        description=(
            "Exact operational command to execute, if applicable. Must be drawn verbatim from an "
            "approved procedure or verified evidence. Must be None if no approved command exists."
        ),
    )
    command_source: Optional[str] = Field(
        default=None,
        description="Citation indicating the exact approved document/section authorizing the command.",
    )
    expected_evidence: str = Field(
        ...,
        description="The exact observation, diagnostic output, or symptom the engineer should report back.",
    )
    restrictions: list[str] = Field(
        default_factory=list,
        description="Operational precautions, warnings, or prohibitions (e.g. 'Do not reboot', 'Read-only check').",
    )


class TechnicalAuthorityRequest(BaseModel):
    """Input payload sent to the Technical Authority Engineer specialist.

    Constructed with server-validated context so the model cannot fabricate
    trusted evidence.
    """

    problem_statement: str = Field(
        ...,
        description="The technical problem or incident symptom under investigation.",
    )
    verified_symptoms: Optional[list[str]] = Field(
        default=None,
        description="Observed facts and symptoms verified from current evidence.",
    )
    missing_information: Optional[list[str]] = Field(
        default=None,
        description="Gaps in diagnostic evidence identified so far.",
    )
    verified_evidence: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="List of server-verified evidence items available for this evaluation.",
    )
    approved_commands_catalog: Optional[list[dict[str, Any]]] = Field(
        default=None,
        description="Catalog of verified operational commands approved for this operational domain.",
    )
    known_applicability_facts: Optional[dict[str, list[str]]] = Field(
        default=None,
        description="Operational context facts explicitly stated (e.g. {'vendor': ['ericsson'], 'technology': ['4g']}).",
    )
    prior_steps_taken: Optional[list[str]] = Field(
        default=None,
        description="Diagnostic or operational steps already completed in this session.",
    )


class TechnicalAuthorityResponse(BaseModel):
    """Output payload produced by the Technical Authority Engineer specialist.

    Validated deterministically by after_agent_callback to guarantee single-step
    discipline, citation provenance, and command grounding.
    """

    outcome: TechnicalAuthorityOutcome = Field(
        ...,
        description="Outcome of the evaluation: 'recommended', 'insufficient_evidence', 'escalation_required', 'error'.",
    )
    technical_interpretation: str = Field(
        ...,
        description=(
            "Current technical interpretation of the incident. Must distinguish observed facts "
            "from hypotheses and avoid speculative claims."
        ),
    )
    verified_evidence_citations: list[str] = Field(
        default_factory=list,
        description="List of source_ids from verified_evidence that support the interpretation and recommendations.",
    )
    missing_information: list[str] = Field(
        default_factory=list,
        description="Key diagnostic facts or observations that are still missing.",
    )
    diagnostic_step: Optional[DiagnosticStep] = Field(
        default=None,
        description="The single recommended next diagnostic step. Must be None unless outcome is 'recommended'.",
    )
    escalation_reason: Optional[str] = Field(
        default=None,
        description="Explanation of why escalation is required, populated when outcome is 'escalation_required'.",
    )
    detail: Optional[str] = Field(
        default=None,
        description="Optional technical context, boundary notes, or explanation.",
    )
