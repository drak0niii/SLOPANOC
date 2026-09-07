"""Domain value objects and the central `KnowledgeObject` aggregate.

Only STRUCTURAL validation belongs here (non-empty identifiers, valid
date ranges, internal consistency between a `KnowledgeObject` and its own
`KnowledgeSection`s) -- never business/governance logic. In particular,
nothing here decides whether a lifecycle transition is legal, which
version is "current", or whether an object is applicable to a given
situation -- see docs/KNOWLEDGE_CONTRACT.md for exactly which later 5.1
sub-phase owns each of those.

Every model is a plain, mutable `pydantic.BaseModel` (the same convention
already used throughout this backend, e.g. `backend/approval/schemas.py`,
`backend/cases/schemas.py`) rather than a frozen model -- "immutable
after construction" is achieved the same way the rest of this codebase
already achieves it for domain records: never mutate an instance in
place, use `.model_copy(update={...})` to derive a changed copy. Every
mutable-typed field below uses `Field(default_factory=...)` so default
lists/dicts are never accidentally shared between instances.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus


def require_non_blank(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


# --- Phase 5.1B: applicability dimension normalization ---------------------
#
# These three functions live here (not in applicability.py, where the rest
# of 5.1B's applicability-evaluation code lives) so that `Applicability`
# below can use them directly in its own validator without creating a
# models.py <-> applicability.py import cycle (applicability.py needs to
# import the `Applicability` type itself). `ApplicabilityContext`
# (applicability.py) imports `normalize_applicability_dimensions` from
# here so both sides of an applicability comparison are normalized by the
# exact same code path.
#
# Normalization here is SYNTAX ONLY -- trimming, case-folding, and
# separator canonicalization -- never semantic aliasing (e.g. "tech" does
# NOT become "technology"). See docs/KNOWLEDGE_CONTRACT.md's 5.1B section.

_DIMENSION_KEY_SEPARATORS = re.compile(r"[\s\-]+")
_DIMENSION_KEY_REPEATED_UNDERSCORES = re.compile(r"_+")


def normalize_dimension_key(key: str) -> str:
    """Deterministic syntax normalization of an applicability dimension
    key: trim, casefold, spaces/hyphens -> underscore, collapse repeated
    underscores. E.g. "Node Type" / "node-type" / "node_type" all
    normalize to "node_type". Returns "" for a blank/whitespace-only or
    otherwise all-separator input -- callers must treat that as invalid.
    """
    lowered = key.strip().casefold()
    with_underscores = _DIMENSION_KEY_SEPARATORS.sub("_", lowered)
    collapsed = _DIMENSION_KEY_REPEATED_UNDERSCORES.sub("_", with_underscores)
    return collapsed.strip("_")


def normalize_dimension_value(value: str) -> str:
    """Deterministic COMPARISON form of an applicability value (trim +
    casefold) -- used only for equality checks during evaluation, never
    used to overwrite a stored value. E.g. "Ericsson" and "ericsson" and
    " ERICSSON " all normalize to the same comparison form, but the
    domain objects keep their original (trimmed) casing -- see
    `normalize_applicability_dimensions`.
    """
    return value.strip().casefold()


def normalize_applicability_dimensions(dimensions: dict[str, list[str]]) -> dict[str, list[str]]:
    """Normalize + structurally validate a raw dimensions mapping, shared
    by `Applicability` (below) and `ApplicabilityContext`
    (applicability.py) so both sides of a future comparison are always in
    the same canonical shape:

    - each key is normalized via `normalize_dimension_key`; a key that
      normalizes to "" is rejected (ValueError).
    - an empty allowed-value list is rejected (ValueError) -- REJECTED,
      never treated as an implicit wildcard (5.1B instruction section
      15): an applicability constraint naming a dimension but no allowed
      values is ambiguous, not "anything goes".
    - each value is stripped of surrounding whitespace and rejected if
      that leaves it blank (ValueError).
    - two raw keys that normalize to the same canonical key have their
      value lists merged.
    - duplicate values within one (merged) dimension are removed
      deterministically -- case-insensitive comparison
      (`normalize_dimension_value`), keeping the FIRST-seen original
      (trimmed) casing and encounter order. This is a dedupe, not a
      rejection, unlike a duplicate `section_id`/`knowledge_id` elsewhere
      in this package -- an applicability value list is a descriptive
      set of allowed values, not a strict per-item identity list.
    """
    normalized: dict[str, list[str]] = {}
    seen_per_key: dict[str, set[str]] = {}
    for raw_key, raw_values in dimensions.items():
        key = normalize_dimension_key(raw_key)
        if not key:
            raise ValueError(f"applicability dimension key {raw_key!r} is blank or invalid")
        if not raw_values:
            raise ValueError(f"applicability dimension {raw_key!r} has an empty allowed-value list")

        bucket = normalized.setdefault(key, [])
        seen = seen_per_key.setdefault(key, set())
        for raw_value in raw_values:
            if not raw_value or not raw_value.strip():
                raise ValueError(f"applicability dimension {raw_key!r} contains a blank value")
            value = raw_value.strip()
            folded = normalize_dimension_value(value)
            if folded in seen:
                continue
            seen.add(folded)
            bucket.append(value)
    return normalized


class KnowledgeSource(BaseModel):
    """Generic SOURCE IDENTITY for one knowledge object -- where the
    governed content originated, without coupling this domain to any one
    source system. This is identity only: no actual source connector
    (SharePoint/GCS/Drive/local-file/...) is implemented or assumed here
    -- that is 5.1C's job, against whatever shape `source_system` names.
    """

    source_system: str = Field(
        description='The originating system, e.g. "sharepoint", "gcs", "confluence" -- an opaque label, never validated against a fixed list.'
    )
    source_id: str = Field(description="The identifier of this content within source_system -- opaque to this domain.")
    source_uri: Optional[str] = Field(default=None, description="Optional locator (URL/path/URI) within source_system.")
    display_name: Optional[str] = Field(default=None, description="Optional human-readable label for this source.")

    @field_validator("source_system", "source_id")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        return require_non_blank(value, info.field_name)


class KnowledgeVersion(BaseModel):
    """Generic VERSION IDENTITY for one knowledge object.

    Deliberately does not force SemVer -- operational documents use all
    kinds of version schemes ("1.0", "4.2", "Rev-A", "2026-08", a
    customer-specific revision code); `label` accepts any non-blank
    string. `supersedes`/`superseded_by` are plain label references only
    (no traversal/graph logic, no "current version" resolution) -- that
    is 5.1E's job; this phase only lets a version RECORD that a
    relationship exists.
    """

    label: str = Field(description='The version label as the source names it, e.g. "1.0", "Rev-A", "2026-08".')
    revision: Optional[str] = Field(default=None, description="Optional additional revision identifier, if the source distinguishes one from label.")
    effective_from: Optional[datetime] = None
    effective_to: Optional[datetime] = None
    supersedes: list[str] = Field(
        default_factory=list, description="Version labels this version is understood to supersede, if known -- a record only, never traversed here."
    )
    superseded_by: list[str] = Field(
        default_factory=list, description="Version labels understood to supersede this one, if known -- a record only, never traversed here."
    )

    @field_validator("label")
    @classmethod
    def _label_non_blank(cls, value: str) -> str:
        return require_non_blank(value, "label")

    @model_validator(mode="after")
    def _validate_structural_consistency(self) -> "KnowledgeVersion":
        if self.effective_from is not None and self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be before effective_from")
        if self.label in self.supersedes or self.label in self.superseded_by:
            raise ValueError("a version must not supersede or be superseded by itself")
        return self


class KnowledgeMetadata(BaseModel):
    """Common metadata attached to a `KnowledgeObject`, kept deliberately
    small and extensible. Future dimensions (domain, technology, vendor,
    product, release, customer, market, node type, service, fault
    category, ...) live in `attributes` rather than as named fields on
    this model or on `KnowledgeObject` -- so the object model does not
    need to change shape every time a new metadata dimension is needed.
    Applicability MATCHING (as opposed to representation) is explicitly
    out of scope here -- see `Applicability` and 5.1B.
    """

    owner: Optional[str] = None
    classification: Optional[str] = None
    tags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(
        default_factory=dict,
        description="Extensible key/value bag for future metadata dimensions not yet promoted to a named field.",
    )

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, value: list[str]) -> list[str]:
        """Phase 5.1B metadata hardening: a blank tag is REJECTED (never
        silently dropped -- it signals a caller error worth surfacing).
        A duplicate tag (case-insensitive) is deterministically DEDUPED,
        keeping the first-seen original casing and order -- unlike a
        blank tag, an accidental repeat is not itself invalid input.
        """
        deduped: list[str] = []
        seen: set[str] = set()
        for tag in value:
            if not tag or not tag.strip():
                raise ValueError("tags must not contain a blank entry")
            stripped = tag.strip()
            folded = stripped.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            deduped.append(stripped)
        return deduped

    @field_validator("attributes")
    @classmethod
    def _validate_attribute_keys(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in value:
            if not key or not key.strip():
                raise ValueError("metadata attribute keys must not be blank")
        return value


class Applicability(BaseModel):
    """A generic, extensible REPRESENTATION of applicability information
    -- deliberately not an evaluator. `dimensions` is an open bag of
    named dimensions (e.g. "domain", "technology", "vendor", "release"),
    each holding the values this knowledge object is understood to apply
    to; no fixed RAN/CORE/vendor/customer schema is embedded here.

    Phase 5.1B evaluates applicability (see `applicability.py`'s
    `evaluate_applicability`, `ApplicabilityOutcome`); this model only
    represents it. Its `dimensions` are normalized/validated (key syntax,
    non-empty value lists, non-blank values, deterministic dedupe) via
    `normalize_applicability_dimensions` above -- the exact same function
    `ApplicabilityContext` (applicability.py) uses, so both sides of a
    future comparison are always in the same canonical shape.
    """

    dimensions: dict[str, list[str]] = Field(default_factory=dict)
    notes: Optional[str] = None

    @model_validator(mode="after")
    def _normalize_dimensions(self) -> "Applicability":
        self.dimensions = normalize_applicability_dimensions(self.dimensions)
        return self


class KnowledgeSection(BaseModel):
    """One structured section of a `KnowledgeObject`'s content -- a
    `KnowledgeObject` is a collection of these, never one opaque text
    blob. `section_type` is a plain, extensible string (never a fixed
    enum) precisely so future section kinds (Purpose, Preconditions,
    Warning, Diagnostic Step, Validation, Rollback, Escalation, Root
    Cause, Corrective Action, ...) never require a schema change.
    Automatic segmentation from a source document is 5.1D, not this
    model.
    """

    section_id: str
    knowledge_id: str = Field(description="The owning KnowledgeObject's knowledge_id -- see KnowledgeObject's own cross-check.")
    heading: Optional[str] = None
    section_type: Optional[str] = Field(default=None, description="Extensible, free-form section kind label -- never a fixed enum.")
    sequence: int = Field(description="Deterministic, non-negative ordering position among a KnowledgeObject's sections.")
    content: str
    source_locator: Optional[str] = Field(default=None, description="Optional locator of this section within the original source content.")

    @field_validator("section_id", "knowledge_id", "content")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        return require_non_blank(value, info.field_name)

    @field_validator("sequence")
    @classmethod
    def _sequence_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("sequence must not be negative")
        return value


class KnowledgeObject(BaseModel):
    """The central Generic Knowledge Management aggregate.

    One `KnowledgeObject` model represents every governed knowledge
    document type (MOP/SOP/RCA/KB article/...) -- `document_type` is
    what distinguishes them, never a different class. This model holds
    only structural invariants; it has no lifecycle-transition,
    persistence, or retrieval behavior (5.1E/5.1F/5.1G).
    """

    knowledge_id: str
    document_type: KnowledgeDocumentType
    title: str
    version: KnowledgeVersion
    lifecycle_status: LifecycleStatus
    source: KnowledgeSource
    metadata: KnowledgeMetadata = Field(default_factory=KnowledgeMetadata)
    applicability: Applicability = Field(default_factory=Applicability)
    sections: list[KnowledgeSection] = Field(default_factory=list)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @field_validator("knowledge_id", "title")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        return require_non_blank(value, info.field_name)

    @model_validator(mode="after")
    def _validate_sections(self) -> "KnowledgeObject":
        seen_section_ids: set[str] = set()
        seen_sequences: set[int] = set()
        for section in self.sections:
            if section.knowledge_id != self.knowledge_id:
                raise ValueError(
                    f"section {section.section_id!r} has knowledge_id "
                    f"{section.knowledge_id!r}, which does not match this "
                    f"KnowledgeObject's knowledge_id {self.knowledge_id!r}"
                )
            if section.section_id in seen_section_ids:
                raise ValueError(f"duplicate section_id {section.section_id!r}")
            seen_section_ids.add(section.section_id)
            # Sequence must unambiguously determine order -- a duplicate
            # sequence value is REJECTED rather than silently normalized
            # (e.g. by insertion order), since normalizing would make
            # section order depend on construction order, an implicit
            # and easily-broken invariant. Rejecting keeps ordering
            # deterministic and forces the caller (later, 5.1D's
            # segmentation step) to assign unambiguous sequence values.
            if section.sequence in seen_sequences:
                raise ValueError(f"duplicate section sequence {section.sequence!r}")
            seen_sequences.add(section.sequence)
        return self
