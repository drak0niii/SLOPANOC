"""Phase 6A.9: structured request/response contracts for
`troubleshooting_manager`.

`TroubleshootingManagerRequest` is the coordinator's own input (never an
ADK `input_schema` -- this specialist is not wired into `team_manager`
via `AgentTool` in this milestone, so there is no caller whose function-
call schema would need it; see `runtime.py`).

`TroubleshootingManagerResponse` IS wired as `troubleshooting_manager`'s
ADK `output_schema` (`agent.py`), so the model's final reply is
guaranteed to conform to this shape rather than free-form prose --
mirrors `IncidentManagerResponse`'s own role exactly.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from backend.context_engineering.contracts import ContextPackage


class TroubleshootingManagerRequest(BaseModel):
    """The coordinator's own input (`runtime.run_troubleshooting_
    assessment`) -- deliberately NOT an ADK `input_schema`; see this
    module's own docstring.

    OWNER CONSISTENCY (§44): `owner_id` must equal `context_package
    .owner_id` -- enforced by `backend.troubleshooting_intelligence
    .assembly.assemble_troubleshooting_intelligence`, fail-closed.
    """

    owner_id: str
    context_package: ContextPackage
    objective: Optional[str] = Field(
        default=None,
        description="A plain-language statement of what troubleshooting question/objective this turn is for -- e.g. 'what should I check next for this VSWR alarm?'. Never conversation history -- the bounded RequestContext already inside context_package.request carries chat_topic/question/requested_time_range.",
    )
    available_capabilities: Optional[list[str]] = Field(
        default=None,
        description="Symbolic capability identifiers known to be available (e.g. ['alarms.read']), or None if capability availability was never evaluated -- mirrors backend.skills.readiness.evaluate_skill_readiness's own None-means-NOT_EVALUATED contract exactly; never inferred, never guessed.",
    )
    invocation_metadata: dict[str, str] = Field(default_factory=dict, description="Safe-to-log, non-secret caller metadata (e.g. a correlation id) -- never a prompt, message body, or credential.")


class TroubleshootingResponseStatus(str, Enum):
    """§46: the small, deterministic status vocabulary. `ADVISORY_READY`
    means the model produced a grounded, validated assessment.
    `NEEDS_INFORMATION` means a required context/evidence gap blocks a
    grounded assessment (never a fabricated one instead). `BLOCKED` means
    either no applicable/ready methodology exists, or the model's own
    response failed grounding validation and was replaced with a safe,
    deterministic failure."""

    ADVISORY_READY = "advisory_ready"
    NEEDS_INFORMATION = "needs_information"
    BLOCKED = "blocked"


class TroubleshootingObjectiveKind(str, Enum):
    """§13/docs/INTELLIGENCE_ARCHITECTURE.md §13: the four troubleshooting
    objectives that must never collapse into one generic
    'recommendation'. `NEXT_CHECK` is the default per docs/
    TROUBLESHOOTING_STRATEGY.md's own next-best-diagnostic-action
    principle."""

    NEXT_CHECK = "next_check"
    MITIGATION = "mitigation"
    RESOLUTION = "resolution"
    RCA = "rca"


class EvidenceReferenceUsed(BaseModel):
    """A claim that the model's own assessment genuinely relied on this
    exact, already-selected Knowledge evidence item. Validated post-hoc
    against the Intelligence Package's own evidence ids
    (`backend.troubleshooting_intelligence.grounding
    .validate_evidence_references`) -- an id not present there fails the
    whole response closed."""

    evidence_id: str


class ExperienceReferenceUsed(BaseModel):
    """A claim that the model's own assessment cited this exact,
    already-queried Experience record as historical, NON-AUTHORITATIVE
    support. Validated post-hoc the same way as `EvidenceReferenceUsed`."""

    experience_id: str


class InformationGap(BaseModel):
    """One unresolved information gap blocking a fully grounded
    assessment -- e.g. a required TELCO Context dimension that is
    UNKNOWN/CONFLICTING, or missing evidence."""

    description: str


class NextDiagnosticRequirement(BaseModel):
    """§47/§49: a REQUEST for specific evidence/information, never an
    executable action. `required_capability`, when set, must be a
    read-only symbolic capability reference (e.g. 'alarms.read') -- never
    a command, payload, or tool invocation of any kind."""

    description: str = Field(description="Plain-language statement of the single next check/information to obtain -- never a command.")
    required_capability: Optional[str] = None


class TroubleshootingManagerResponse(BaseModel):
    """`troubleshooting_manager`'s structured reply.

    When `status` is `NEEDS_INFORMATION`/`BLOCKED`, `assessment`/
    `findings`/`next_diagnostic_requirement` are typically empty/unset
    and `detail` carries a safe, plain-language explanation instead --
    mirrors `IncidentManagerResponse`'s own non-"ok"-outcome convention.

    `skill_id`/`skill_version`, when set, must reference EXACTLY the
    Skill the Intelligence Package selected -- never 'latest', never a
    Skill outside the package's own closed candidate (enforced by
    `backend.troubleshooting_intelligence.grounding
    .validate_skill_reference`).

    Deliberately has NO numeric confidence field (§93) and NO chain-of-
    thought field (§96) -- only structured, grounded, auditable output.
    """

    status: TroubleshootingResponseStatus
    troubleshooting_objective: Optional[TroubleshootingObjectiveKind] = None
    assessment: Optional[str] = Field(default=None, description="A brief, grounded interpretation of current context/evidence -- never a list of options, never an executable plan.")
    findings: list[str] = Field(default_factory=list, description="Short, individually grounded factual statements -- each traceable to current context, selected evidence, or historical Experience (with Experience explicitly distinguished as non-authoritative).")
    information_gaps: list[InformationGap] = Field(default_factory=list)
    next_diagnostic_requirement: Optional[NextDiagnosticRequirement] = None
    stop_or_escalation_condition: Optional[str] = Field(default=None, description="Plain statement of whether/why to stop, escalate, or hand over -- grounded in selected evidence or current context, never invented.")
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    evidence_references_used: list[EvidenceReferenceUsed] = Field(default_factory=list)
    experience_references_used: list[ExperienceReferenceUsed] = Field(default_factory=list)
    detail: Optional[str] = Field(default=None, description="Safe, plain-language explanation for a NEEDS_INFORMATION/BLOCKED status -- never exposes internal state, prompts, or raw model text.")
