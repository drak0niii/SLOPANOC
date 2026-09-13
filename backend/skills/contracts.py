"""Phase 6A.7: the canonical `SkillDefinition` contract and its
readiness/applicability result types.

DEPENDENCY BOUNDARY (enforced by `backend/tests/skills/test_dependency_
boundary.py`, mirroring 6A.2/6A.4/6A.5/6A.6's own established pattern):
this module imports ONLY `backend.context.domain.enums` (the canonical
`ContextDimension` -- a type) -- never a service/repository/database
module of any domain, never `google.adk`/`google.genai`, never
`backend.agents`/`backend.tools`, never `backend.knowledge.*`, never
`backend.cases.*`. `readiness.py`/`applicability.py` separately import
`backend.context_engineering.contracts.ContextPackage` (a type only) for
their own function signatures.

SKILL != AGENT, SKILL != KNOWLEDGE, SKILL != TOOL, SKILL != MEMORY
(§2-§7 of this milestone's own instruction, restating `docs/AGENT_
CONTRACT.md` §3a / `docs/INTELLIGENCE_ARCHITECTURE.md` §11): nothing in
this module reasons, executes, calls a model, or stores historical
outcomes -- it is a pure, typed, declarative contract.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.context.domain.enums import ContextDimension
from backend.skills.versioning import InvalidSkillVersion, parse_skill_version

__all__ = [
    "SKILL_SCHEMA_VERSION",
    "SkillLifecycle",
    "ContextRequirement",
    "EvidenceRequirement",
    "MethodologyStep",
    "ApplicabilityCondition",
    "SkillApplicability",
    "SkillDefinition",
    "RequirementStatus",
    "ContextRequirementResult",
    "EvidenceRequirementResult",
    "CapabilityRequirementResult",
    "SkillReadinessStatus",
    "SkillReadinessResult",
    "ApplicabilityOutcome",
    "ApplicabilityConditionResult",
    "SkillApplicabilityResult",
]

SKILL_SCHEMA_VERSION = "1.0"
"""The Skill CONTRACT's own structure/serialization version -- distinct
from `SkillDefinition.version` (the METHODOLOGY revision, §15). Bumped
only on a genuine, intentional structural change to this contract; a new
OPTIONAL field may be added without bumping this."""

_CAPABILITY_IDENTIFIER_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
"""A minimal, deliberately small symbolic-capability-identifier shape
(e.g. `alarms.read`, `tickets.read`) -- §33/§36's own explicit "define
minimal symbolic capability identifier validation... do not build a
full tool registry solely for 6A.7." Validates FORMAT only, never
existence against any registry (none exists -- confirmed by this
milestone's own audit)."""


class SkillLifecycle(str, Enum):
    """§22: the minimum useful lifecycle -- deliberately NOT Knowledge's
    CANDIDATE/APPROVED/ARCHIVE (a different governed-artifact type, no
    approval workflow implemented in this milestone)."""

    ACTIVE = "active"
    DEPRECATED = "deprecated"


class ContextRequirement(BaseModel):
    """§28/§29: a declarative requirement that a specific canonical 6A.2
    `ContextDimension` be KNOWN. Uses the CANONICAL enum directly --
    pydantic rejects any value outside it, satisfying §29's "invalid
    fields must fail validation" requirement structurally, not via
    hand-written checks."""

    dimension: ContextDimension


class EvidenceRequirement(BaseModel):
    """§31: a declarative requirement over 6A.5's own selected-evidence
    counts (via the caller-supplied `ContextPackage.evidence`) -- never a
    requirement that triggers retrieval itself."""

    minimum_selected_items: int = Field(default=0, ge=0)
    requires_source_evidence: bool = Field(default=False, description="At least one SELECTED evidence item must have is_derived=False.")


class MethodologyStep(BaseModel):
    """§39: a minimal, non-executable methodology step. No executable
    behavior, no generic workflow DSL (§41)."""

    step_id: str
    sequence: int = Field(ge=0)
    title: str
    purpose: Optional[str] = None
    required_capability: Optional[str] = None

    @field_validator("step_id", "title")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("required_capability")
    @classmethod
    def _capability_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not _CAPABILITY_IDENTIFIER_PATTERN.match(value):
            raise ValueError(f"invalid capability identifier format: {value!r} (expected e.g. 'alarms.read')")
        return value


class ApplicabilityCondition(BaseModel):
    """§23/§29: a single typed-dimension applicability condition --
    "this methodology's scope is limited to these canonical values for
    this dimension". Never free text, never a ranking weight."""

    dimension: ContextDimension
    allowed_values: list[str] = Field(min_length=1, description="Canonical values (matched against the accepted ContextAssertion.canonical_value of a KNOWN dimension).")


class SkillApplicability(BaseModel):
    """§23: zero or more typed conditions. An empty condition list means
    the Skill declares no scope restriction -- trivially APPLICABLE,
    never a guess."""

    conditions: list[ApplicabilityCondition] = Field(default_factory=list)


class SkillDefinition(BaseModel):
    """The canonical, typed Skill contract (§16). Deliberately the
    SMALLEST complete contract this milestone's own audit justified --
    no vague metadata dumping ground, no executable content anywhere.

    IDENTITY (§17): `skill_id` + `version` together are the authoritative
    identity for audit/replay -- enforced by `registry.py`, never by this
    model alone (a single `SkillDefinition` has no notion of "the other
    versions of itself").
    """

    model_config = {"extra": "forbid"}

    skill_schema_version: str = SKILL_SCHEMA_VERSION
    skill_id: str
    version: str = Field(description="A strict, stable MAJOR.MINOR.PATCH version string, e.g. '1.0.0' or '1.10.0' -- no pre-release/build metadata/epoch/v-prefix; validated by versioning.py, never compared lexically.")
    lifecycle: SkillLifecycle
    name: str
    description: str
    objective: str
    applicability: SkillApplicability = Field(default_factory=SkillApplicability)
    context_requirements: list[ContextRequirement] = Field(default_factory=list)
    requires_case_context: bool = False
    evidence_requirement: EvidenceRequirement = Field(default_factory=EvidenceRequirement)
    capability_requirements: list[str] = Field(default_factory=list)
    methodology: list[MethodologyStep] = Field(default_factory=list)
    expected_output: Optional[str] = None
    guardrails: list[str] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("skill_id", "name", "description", "objective")
    @classmethod
    def _non_blank(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("version")
    @classmethod
    def _valid_semantic_version(cls, value: str) -> str:
        # 6A.7 corrective pass §1: a strict, stable MAJOR.MINOR.PATCH
        # string only -- any PEP-440-only form (two-component, v-prefix,
        # leading zero, prerelease, postrelease, dev, epoch, local) fails
        # closed here, at construction time, never silently coerced and
        # never compared lexically later.
        try:
            parse_skill_version(value)
        except InvalidSkillVersion as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("skill_schema_version")
    @classmethod
    def _known_schema_version(cls, value: str) -> str:
        # §15/§48: unknown schema versions fail closed -- no migration
        # machinery is implemented in this milestone (deliberately
        # deferred, per instruction section 15's own explicit guidance).
        if value != SKILL_SCHEMA_VERSION:
            raise ValueError(f"unknown skill_schema_version {value!r} -- only {SKILL_SCHEMA_VERSION!r} is supported")
        return value

    @field_validator("capability_requirements")
    @classmethod
    def _capability_requirements_format(cls, value: list[str]) -> list[str]:
        for identifier in value:
            if not _CAPABILITY_IDENTIFIER_PATTERN.match(identifier):
                raise ValueError(f"invalid capability identifier format: {identifier!r} (expected e.g. 'alarms.read')")
        return value

    @model_validator(mode="after")
    def _validate_methodology_ordering(self) -> "SkillDefinition":
        # §40/§48: duplicate step IDs, duplicate sequence numbers, and
        # invalid ordering must all fail closed.
        step_ids = [step.step_id for step in self.methodology]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("duplicate methodology step_id")
        sequences = [step.sequence for step in self.methodology]
        if len(sequences) != len(set(sequences)):
            raise ValueError("duplicate methodology step sequence")
        return self


# --- Readiness result types (§50) -------------------------------------


class RequirementStatus(str, Enum):
    """§35/§50: a single requirement's own evaluated status. `NOT_
    EVALUATED` exists ONLY for capability requirements whose availability
    the caller did not supply -- it must never be silently produced for
    context/evidence requirements, which `ContextPackage` always answers
    in full (§30's own "do not guess" invariant applies structurally:
    there is no code path that returns NOT_EVALUATED for a context/
    evidence requirement)."""

    SATISFIED = "satisfied"
    MISSING = "missing"
    NOT_EVALUATED = "not_evaluated"


class ContextRequirementResult(BaseModel):
    dimension: ContextDimension
    status: RequirementStatus
    reason: Optional[str] = None


class EvidenceRequirementResult(BaseModel):
    status: RequirementStatus
    reason: Optional[str] = None


class CapabilityRequirementResult(BaseModel):
    capability: str
    status: RequirementStatus


class SkillReadinessStatus(str, Enum):
    """§50/§51: readiness's own OVERALL outcome -- deliberately a THIRD
    state (`INDETERMINATE`) distinct from `READY`/`NOT_READY` for the
    case where every context/evidence requirement is satisfied but a
    capability requirement's availability was never supplied by the
    caller -- neither falsely READY nor falsely NOT_READY (§35's own
    "must NOT automatically become SATISFIED... must NOT automatically
    become MISSING" applied at the whole-result level, not only the
    per-capability level)."""

    READY = "ready"
    NOT_READY = "not_ready"
    INDETERMINATE = "indeterminate"


class SkillReadinessResult(BaseModel):
    skill_id: str
    version: str
    status: SkillReadinessStatus
    context_results: list[ContextRequirementResult] = Field(default_factory=list)
    case_context_result: Optional[RequirementStatus] = None
    evidence_result: EvidenceRequirementResult
    capability_results: list[CapabilityRequirementResult] = Field(default_factory=list)


# --- Applicability result types (§23-§26) ------------------------------


class ApplicabilityOutcome(str, Enum):
    """§26: `INDETERMINATE` is returned whenever the required typed
    context is UNKNOWN/CONFLICTING/absent -- never guessed, never
    inferred from free text."""

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not_applicable"
    INDETERMINATE = "indeterminate"


class ApplicabilityConditionResult(BaseModel):
    dimension: ContextDimension
    outcome: ApplicabilityOutcome


class SkillApplicabilityResult(BaseModel):
    skill_id: str
    version: str
    outcome: ApplicabilityOutcome
    condition_results: list[ApplicabilityConditionResult] = Field(default_factory=list)
