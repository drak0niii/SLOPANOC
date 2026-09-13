"""Phase 6A.9: the canonical `TroubleshootingIntelligencePackage` contract.

DEPENDENCY BOUNDARY (enforced by `backend/tests/troubleshooting_
intelligence/test_dependency_boundary.py`, mirroring 6A.2/6A.4/6A.5/
6A.6/6A.7/6A.8's own established pattern): this module imports ONLY
type/contract modules from peer domains -- `backend.context_engineering
.contracts` (6A.6 `ContextPackage`), `backend.skills.contracts` (6A.7
`SkillDefinition`/readiness/applicability result types), and
`backend.experience_memory.domain.models` (6A.8 `ExperienceRecord`, a
pure type) -- never a service/repository/database module of any domain,
never `google.adk`/`google.genai`, never `backend.agents`/`backend.
tools`. This package has NO code path to fetch its own inputs -- Skill
resolution and Experience Memory retrieval both already happened before
`assemble_troubleshooting_intelligence` is ever called.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from backend.context_engineering.contracts import ContextPackage
from backend.experience_memory.domain.models import ExperienceRecord
from backend.skills.contracts import SkillApplicabilityResult, SkillDefinition, SkillReadinessResult

__all__ = [
    "TROUBLESHOOTING_INTELLIGENCE_SCHEMA_VERSION",
    "SkillSelectionOutcome",
    "ExperienceSupportView",
    "ExperienceQueryMetadata",
    "TroubleshootingIntelligencePackage",
    "TroubleshootingIntelligenceInput",
]

TROUBLESHOOTING_INTELLIGENCE_SCHEMA_VERSION = "1.0"
"""The Troubleshooting Intelligence Package's own schema version --
distinct from `CONTEXT_SCHEMA_VERSION` (6A.6), `SKILL_SCHEMA_VERSION`
(6A.7), and `EXPERIENCE_SCHEMA_VERSION` (6A.8). Bumped only on a genuine,
intentional structural change to THIS contract; a new OPTIONAL field may
be added without bumping this."""


class SkillSelectionOutcome(str, Enum):
    """How the (at most one) Skill on this package was determined --
    never a claim that the MODEL made this choice. Skill selection is
    entirely deterministic, performed by the impure coordinator
    (`backend/agents/troubleshooting_manager/skill_resolution.py`)
    BEFORE assembly."""

    SELECTED = "selected"
    """Exactly one applicable Skill was found READY and was
    deterministically selected -- `skill` is populated."""

    NONE_READY = "none_ready"
    """One or more Skill candidates exist and are applicable, but none is
    READY (missing context/evidence/capability) -- `skill` is `None`;
    `skill_readiness`/`skill_applicability` describe the (deterministically
    chosen, e.g. lowest skill_id) candidate that was evaluated, for
    diagnostic/explanation purposes only -- never presented as selected."""

    NONE_APPLICABLE = "none_applicable"
    """One or more Skill candidates are registered, but none is applicable
    to the current TELCO Context -- `skill` is `None`."""

    NONE_REGISTERED = "none_registered"
    """No ACTIVE production Skill is registered at all -- `skill` is
    `None`, `skill_readiness`/`skill_applicability` are both `None`."""


class ExperienceSupportView(BaseModel):
    """One package-facing, bounded projection of an already-queried,
    already-owner-scoped `ExperienceRecord` (6A.8) -- historical,
    NON-AUTHORITATIVE supporting context only. `source_class` mirrors
    6A.8's own fixed trust tag verbatim so a renderer/consumer can label
    it correctly without re-deriving anything. Deliberately excludes
    `context_fingerprint`/`evidence_references`/`metadata`/
    `invalidated_at`/`invalidation_reason` -- an Experience record
    reaching this view is always ACTIVE (the coordinator's query never
    requests `include_invalidated`) and this package never needs to
    re-resolve a Context Package or evidence identity from history."""

    source_class: str = Field(default="EXPERIENCE", frozen=True)
    experience_id: str
    experience_type: str
    source_origin: str
    source_namespace: str
    outcome_summary: str
    observed_facts: list[str] = Field(default_factory=list)
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    recorded_at: datetime
    source_event_at: Optional[datetime] = None

    @classmethod
    def from_record(cls, record: ExperienceRecord) -> "ExperienceSupportView":
        return cls(
            experience_id=record.experience_id,
            experience_type=record.experience_type.value,
            source_origin=record.source_origin.value,
            source_namespace=record.source_namespace,
            outcome_summary=record.outcome_summary,
            observed_facts=list(record.observed_facts),
            skill_id=record.skill_id,
            skill_version=record.skill_version,
            recorded_at=record.recorded_at,
            source_event_at=record.source_event_at,
        )


class ExperienceQueryMetadata(BaseModel):
    """What structured filters were actually applied to retrieve
    `experience` -- never a claim of semantic/similarity relevance beyond
    those filters (§65/§101)."""

    owner_id: str
    applied_filters: dict[str, str] = Field(default_factory=dict)
    result_count: int
    limit: int


class TroubleshootingIntelligencePackage(BaseModel):
    """The canonical, versioned, deterministic Troubleshooting
    Intelligence Package (§15/§16/§17) -- the ONE typed input the
    Troubleshooting Manager reasoning boundary consumes.

    IMMUTABLE ONCE ASSEMBLED (mirrors 6A.6's own `ContextPackage`
    discipline): nothing in `assembly.py` mutates this object after
    construction; a differently-scoped package is always a NEW instance.

    `skill` is the FULL `SkillDefinition` ONLY when `skill_selection_
    outcome == SELECTED` -- otherwise `None` (§61/§62: a Skill that was
    evaluated but not selected is never silently presented as if it
    were).
    """

    troubleshooting_intelligence_schema_version: str = TROUBLESHOOTING_INTELLIGENCE_SCHEMA_VERSION
    owner_id: str
    objective: Optional[str] = None
    context_package: ContextPackage
    skill: Optional[SkillDefinition] = None
    skill_fingerprint: Optional[str] = None
    skill_selection_outcome: SkillSelectionOutcome
    skill_readiness: Optional[SkillReadinessResult] = None
    skill_applicability: Optional[SkillApplicabilityResult] = None
    experience: list[ExperienceSupportView] = Field(default_factory=list)
    experience_query: Optional[ExperienceQueryMetadata] = None
    content_fingerprint: str = Field(
        default="",
        description="SHA-256 hex digest of this package's own canonical, deterministic content -- see fingerprint.py. Empty string only transiently, during assembly, before the fingerprint is computed.",
    )
    created_at: datetime = Field(description="Real wall-clock assembly time -- deliberately EXCLUDED from content_fingerprint.")


class TroubleshootingIntelligenceInput(BaseModel):
    """The one input contract `assembly.assemble_troubleshooting_
    intelligence` accepts. Every field is ALREADY-COMPUTED data the
    caller supplies -- this contract itself performs no lookup, no
    database access, no Skill registry resolution, no Experience Memory
    query."""

    owner_id: str
    objective: Optional[str] = None
    context_package: ContextPackage
    skill: Optional[SkillDefinition] = None
    skill_fingerprint: Optional[str] = None
    skill_selection_outcome: SkillSelectionOutcome
    skill_readiness: Optional[SkillReadinessResult] = None
    skill_applicability: Optional[SkillApplicabilityResult] = None
    experience_records: list[ExperienceRecord] = Field(default_factory=list)
    experience_query_metadata: Optional[ExperienceQueryMetadata] = None
