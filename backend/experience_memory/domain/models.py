"""Phase 6A.8: the canonical Experience Memory contracts (§14).

DEPENDENCY BOUNDARY (enforced by `backend/tests/experience_memory/
test_dependency_boundary.py`, mirroring 6A.2/6A.4/6A.5/6A.6/6A.7's own
established pattern): this module imports ONLY `pydantic` and this
package's own `enums` -- never a service/repository/database module of
any domain, never `google.adk`/`google.genai`, never `backend.agents`/
`backend.tools`, never `backend.knowledge.*`, never `backend.cases.*`,
never `backend.skills.*`, never `backend.context_engineering.*`.
`ExperienceCandidate.context_fingerprint`/`skill_id`/`skill_version`/
`skill_fingerprint`/`evidence_references` are plain `str`/typed-leaf
fields -- deliberate STRING/ID references, never a typed import of
`ContextPackage`/`SkillDefinition`/`EvidenceSelectionResult` themselves
(§27/§28/§29: "prefer stable references... do not persist the entire
ContextPackage... do NOT copy governed Knowledge bodies").

EXPERIENCE != KNOWLEDGE, EXPERIENCE != CURRENT CONTEXT, EXPERIENCE !=
REASONING (§2-§5 of this milestone's own instruction): nothing in this
module reasons, executes, calls a model, searches Knowledge, or mutates
current TELCO Context/Case state -- it is a pure, typed, declarative
contract plus (in `admission.py`) one pure, deterministic function.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.experience_memory.domain.enums import ExperienceLifecycle, ExperienceSourceOrigin, ExperienceType

__all__ = [
    "EXPERIENCE_SCHEMA_VERSION",
    "ExperienceEvidenceReference",
    "ExperienceCandidate",
    "ExperienceRecord",
    "ExperienceQuery",
    "ExperienceMemoryResult",
]

EXPERIENCE_SCHEMA_VERSION = "1.0"
"""The Experience CONTRACT's own structure/serialization version --
distinct from `Skill`/`ContextPackage` schema versions, `skill_version`
(the METHODOLOGY revision), `source_event_at`/`recorded_at` (§15).
Bumped only on a genuine, intentional structural change to this
contract."""

_MAX_OBSERVED_FACTS = 20
_MAX_OBSERVED_FACT_LENGTH = 500
_MAX_OUTCOME_SUMMARY_LENGTH = 2000

_SOURCE_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]*$")
"""6A.8 final source-namespace corrective pass §5: a small, generic,
symbolic producer-namespace identifier (e.g. `"bmc"`, `"teams"`,
`"onefm"`, `"enm"` -- illustrative only, never a hardcoded product
taxonomy). Validates FORMAT only: lowercase, starts with a letter, no
whitespace, no blank/whitespace-only values, no free-form narrative.
Never derived automatically from `source_origin` or any other field --
the caller's own originating system must supply it explicitly."""


class ExperienceEvidenceReference(BaseModel):
    """§29: a STABLE REFERENCE into 6A.5's evidence identity space --
    never a copy of governed Knowledge content. At least one identifier
    must be present; every field is optional individually because not
    every reference is resolvable to every granularity (e.g. a
    section-level reference may have no `artifact_id`)."""

    evidence_id: Optional[str] = None
    knowledge_id: Optional[str] = None
    version_label: Optional[str] = None
    section_id: Optional[str] = None
    artifact_id: Optional[str] = None

    @model_validator(mode="after")
    def _at_least_one_identifier(self) -> "ExperienceEvidenceReference":
        if not any([self.evidence_id, self.knowledge_id, self.version_label, self.section_id, self.artifact_id]):
            raise ValueError("ExperienceEvidenceReference must carry at least one identifier")
        return self


def _non_blank(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


class ExperienceCandidate(BaseModel):
    """The typed input to `ExperienceMemoryService.record_experience`
    (§23). NOT yet durable -- admission (`admission.py`) decides whether
    a candidate becomes a persisted `ExperienceRecord` at all.

    IDENTITY INPUTS (§16/§17, corrected TWICE -- see the 6A.8 final
    source-namespace corrective pass, which supersedes the immediately
    prior corrective pass's own basis): `owner_id` + `experience_type` +
    `source_namespace` + `source_event_id` together are the
    deterministic identity basis (`fingerprint.compute_experience_id`)
    that makes admission idempotent -- a caller MUST supply a stable
    `source_event_id` from its own originating system (a Case
    context-item id, an execution id, etc.); this module never invents
    a random one, which would defeat idempotency entirely.

    `source_namespace` vs. `source_origin` -- TWO SEPARATE CONCEPTS,
    never overloaded into one field (this pass's own central
    correction):
    - `source_namespace` answers "which PRODUCER/SYSTEM namespace owns
      this `source_event_id`?" -- used for stable identity, dedup,
      Experience ID construction, historical replay, provenance. A
      small, explicit, caller-supplied symbolic identifier (e.g.
      `"bmc"`, `"teams"`, `"onefm"`) -- NEVER derived from
      `source_origin`.
    - `source_origin` answers "what TRUST/ADMISSION class does this
      candidate come from?" -- used ONLY by `admission.py`'s
      `evaluate_admission`. It remains available, independently, on
      every candidate/record, but is deliberately NOT part of the
      identity basis: two candidates sharing the same producer event
      (same `owner_id`+`experience_type`+`source_namespace`+
      `source_event_id`) but asserting a different `source_origin`
      resolve to the SAME `experience_id` -- identity follows the
      producer EVENT, not whatever trust classification a given
      submission happened to carry (audited, no proven reason found to
      do otherwise -- see this pass's own closure report §M).

    Two different upstream systems reusing the same literal
    `source_event_id` string (e.g. both using `"123"` for their own,
    unrelated first event) can never silently collide, because their
    `source_namespace` values differ.
    """

    model_config = {"extra": "forbid"}

    experience_schema_version: str = EXPERIENCE_SCHEMA_VERSION
    experience_type: ExperienceType
    source_origin: ExperienceSourceOrigin
    owner_id: str = Field(description="Deterministic customer/tenant scope -- e.g. the canonical TELCO CUSTOMER value. REQUIRED; never optional (§32/§45).")
    source_namespace: str = Field(description="The stable producer/system namespace that owns source_event_id (e.g. 'bmc', 'teams', 'onefm') -- REQUIRED, part of the identity basis. Never derived from source_origin.")
    source_event_id: str = Field(description="A stable identifier for the originating source event, supplied by the caller's own system -- the basis for idempotent admission (§16/§17).")
    source_reference: Optional[str] = Field(default=None, description="An opaque pointer to the originating event/record, mirroring Case's own `source_ref` shape.")
    source_event_at: Optional[datetime] = Field(default=None, description="The REAL-WORLD time the underlying event occurred -- distinct from `recorded_at` (persistence time), §42.")
    case_id: Optional[str] = Field(default=None, description="The canonical Case identifier (§25) -- never a duplicated Case snapshot.")
    session_id: Optional[str] = Field(default=None, description="Only when legitimately part of provenance (§26) -- never required.")
    context_fingerprint: Optional[str] = Field(default=None, description="A 6A.6 `ContextPackage.content_fingerprint` reference (§27) -- never the full ContextPackage.")
    skill_id: Optional[str] = None
    skill_version: Optional[str] = Field(default=None, description="The EXACT Skill version associated with the originating operation -- never 'latest' (§28).")
    skill_fingerprint: Optional[str] = None
    evidence_references: list[ExperienceEvidenceReference] = Field(default_factory=list)
    observed_facts: list[str] = Field(default_factory=list, description="Short, observed-fact statements (§30) -- never inferred correctness/confidence.")
    outcome_summary: str = Field(description="A short, plain-text, observed summary of what happened (§30).")
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("owner_id", "source_event_id", "outcome_summary")
    @classmethod
    def _non_blank_required(cls, value: str, info) -> str:
        return _non_blank(value, info.field_name)

    @field_validator("source_namespace")
    @classmethod
    def _valid_source_namespace(cls, value: str) -> str:
        # §5: required, non-empty, normalized/validated, non-executable,
        # stable, explicit -- never blank/whitespace-only, never a random
        # per-event UUID, never a timestamp, never free-form narrative.
        if not _SOURCE_NAMESPACE_PATTERN.match(value):
            raise ValueError(f"invalid source_namespace {value!r} -- must match {_SOURCE_NAMESPACE_PATTERN.pattern!r} (e.g. 'bmc', 'teams', 'onefm')")
        return value

    @field_validator("experience_schema_version")
    @classmethod
    def _known_schema_version(cls, value: str) -> str:
        if value != EXPERIENCE_SCHEMA_VERSION:
            raise ValueError(f"unknown experience_schema_version {value!r} -- only {EXPERIENCE_SCHEMA_VERSION!r} is supported")
        return value

    @field_validator("outcome_summary")
    @classmethod
    def _outcome_summary_bounded(cls, value: str) -> str:
        if len(value) > _MAX_OUTCOME_SUMMARY_LENGTH:
            raise ValueError(f"outcome_summary must not exceed {_MAX_OUTCOME_SUMMARY_LENGTH} characters")
        return value

    @field_validator("observed_facts")
    @classmethod
    def _observed_facts_bounded(cls, value: list[str]) -> list[str]:
        if len(value) > _MAX_OBSERVED_FACTS:
            raise ValueError(f"observed_facts must not exceed {_MAX_OBSERVED_FACTS} entries")
        for fact in value:
            if not fact or not fact.strip():
                raise ValueError("observed_facts entries must not be blank")
            if len(fact) > _MAX_OBSERVED_FACT_LENGTH:
                raise ValueError(f"each observed_facts entry must not exceed {_MAX_OBSERVED_FACT_LENGTH} characters")
        return value

    @model_validator(mode="after")
    def _skill_version_required_with_skill_id(self) -> "ExperienceCandidate":
        # §28: never store only "latest Skill" -- an experience either
        # names no Skill at all, or names an EXACT skill_id+skill_version
        # pair. A skill_id with no skill_version is fail-closed rejected
        # rather than silently treated as "any version"/"latest".
        if self.skill_id is not None and not self.skill_version:
            raise ValueError("skill_version is required whenever skill_id is set (never 'latest Skill')")
        return self


class ExperienceRecord(BaseModel):
    """The canonical, DURABLE, persisted Experience shape returned by
    the service. Every instance carries `source_class = "EXPERIENCE"`
    (§19) -- a fixed literal, never a second value in this milestone --
    so any downstream consumer can distinguish an Experience record from
    Governed Knowledge / current-observation representations purely by
    inspecting this one field, even out of context.

    IMMUTABLE CONTENT (§41): every field below except `lifecycle`/
    `invalidated_at`/`invalidation_reason` is set once, at admission,
    and never mutated afterward -- a correction is represented as
    invalidating this record and admitting a NEW one, never an in-place
    rewrite.
    """

    model_config = {"extra": "forbid"}

    source_class: Literal["EXPERIENCE"] = "EXPERIENCE"
    experience_id: str = Field(description="Deterministic SHA-256 hex digest of (owner_id, experience_type, source_namespace, source_event_id) -- see fingerprint.py.")
    experience_schema_version: str
    experience_type: ExperienceType
    lifecycle: ExperienceLifecycle
    owner_id: str
    source_origin: ExperienceSourceOrigin
    source_namespace: str
    source_reference: Optional[str] = None
    source_event_id: str
    source_event_at: Optional[datetime] = None
    case_id: Optional[str] = None
    session_id: Optional[str] = None
    context_fingerprint: Optional[str] = None
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    skill_fingerprint: Optional[str] = None
    evidence_references: list[ExperienceEvidenceReference] = Field(default_factory=list)
    observed_facts: list[str] = Field(default_factory=list)
    outcome_summary: str
    metadata: dict[str, str] = Field(default_factory=dict)
    content_fingerprint: str = Field(description="Deterministic fingerprint of the record's own logical (candidate) content -- audit/dedup-support only, never authority (§43).")
    recorded_at: datetime = Field(description="Persistence time -- NEVER used as proof the underlying event occurred at this time (§42).")
    invalidated_at: Optional[datetime] = None
    invalidation_reason: Optional[str] = None


class ExperienceQuery(BaseModel):
    """§45: `owner_id` is a REQUIRED field on the query contract itself
    -- there is no way to construct a valid, unscoped query. `limit` is
    always bounded (§46/§74); retrieval defaults to ACTIVE-only unless
    `include_invalidated` is explicitly set (§72)."""

    model_config = {"extra": "forbid"}

    owner_id: str
    case_id: Optional[str] = None
    experience_type: Optional[ExperienceType] = None
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    source_event_from: Optional[datetime] = None
    source_event_to: Optional[datetime] = None
    include_invalidated: bool = False
    limit: int = Field(default=50, ge=1, le=200)

    @field_validator("owner_id")
    @classmethod
    def _non_blank_owner(cls, value: str) -> str:
        return _non_blank(value, "owner_id")


class ExperienceMemoryResult(BaseModel):
    """§52: the typed retrieval result. `records` is always a real,
    already-scoped list -- an empty list is a completely valid result
    (§53), never an error, never a fallback trigger."""

    model_config = {"extra": "forbid"}

    owner_id: str
    records: list[ExperienceRecord] = Field(default_factory=list)
    count: int
    applied_filters: dict[str, str] = Field(default_factory=dict)
    limit: int
