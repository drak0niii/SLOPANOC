"""Phase 6A.3 corrective addendum: the canonical Knowledge Asset Metadata
standard.

Answers a DIFFERENT question from every other model in this package:

    STRUCTURAL PROVENANCE   (KnowledgeArtifact, artifacts.py, A5/6A.3)
        "Exactly where inside the source did this evidence originate?"

    DERIVATION PROVENANCE   (KnowledgeArtifact.derived, image_interpretation.py)
        "Is this content original source content, or derived through
        extraction/normalization/interpretation?"

    KNOWLEDGE ASSET METADATA   (this module)
        "What is this asset? Who owns it? What is its lifecycle? Where
        does it apply? Is it approved for AI use?"

These three concepts are deliberately NEVER collapsed into one generic
dictionary. This module owns only the third. It does not touch, wrap, or
reinterpret `KnowledgeArtifact`/artifact lineage (structural provenance)
or `KnowledgeArtifact.derived`/`image_interpretation.py` (derivation
provenance) -- both are byte-for-byte unmodified by this addendum.

WHY A NEW MODULE, NOT NEW FIELDS DIRECTLY ON `KnowledgeObject`
(audited first, per instruction): `KnowledgeMetadata` already exists
precisely as the extensibility point for "future metadata dimensions not
yet promoted to a named field" (see its own docstring in models.py) --
its `attributes: dict[str, Any]` bag is exactly where an ad-hoc value
would go. But the canonical catalogue this addendum implements needs
real structure per attribute (raw value, normalized value, source,
multi- vs. single-valued, deterministic validation) that a bare
`dict[str, Any]` cannot express safely. Rather than inventing a second,
competing metadata carrier, this module adds ONE new, purely additive,
optional-with-safe-defaults field to the EXISTING `KnowledgeMetadata`
model (`asset_metadata: KnowledgeAssetMetadata`) -- so every governed
`KnowledgeObject` already has access to it through the exact same
`metadata` field ingestion/processing/governance/persistence already
carry through unchanged (verified by audit: `IngestedKnowledgeDocument
.metadata` -> `StructuredKnowledgeDocument.source_document.metadata` ->
`materialize_candidate`'s `metadata=source_document.metadata` ->
`KnowledgeObject.metadata` -- not one of those call sites needed to
change for this field to flow through).

DELIBERATE NON-DUPLICATION: several canonical attributes already have an
existing, authoritative home elsewhere on `KnowledgeObject` --
Knowledge Object ID (`knowledge_id`), Document Title (`title`), Document
Type (`document_type`), and Revision (`version.label`/`version.revision`).
This module does NOT duplicate them as a second, potentially-divergent
value -- see docs/KNOWLEDGE_CONTRACT.md §25's gap matrix, which documents
each of those as SUPPORTED via its existing field rather than a new one
here. Duplicating identity fields would create two sources of truth for
the same fact; the existing field remains authoritative.

RAW + NORMALIZED, NEVER GUESSED: every leaf field below preserves the
original `raw_value`/`raw_values` string(s) alongside an OPTIONAL
`normalized_value`/`normalized_values`, populated ONLY when a conversion
is deterministic and authoritative (e.g. an ISO-8601 date string, or a
spreadsheet serial number under the well-defined Excel/1899-12-30 epoch
-- see `normalize_date_value`). If no deterministic conversion is
possible, `normalized_value` stays `None` and the raw value is still
preserved -- never discarded, never guessed at. No field here is ever
populated by LLM inference; `source` records where a value came from
(`MetadataSource`), and `MetadataSource.MODEL_INFERENCE` deliberately
does NOT exist in this enum -- there is no code path by which a model
suggestion could become canonical Knowledge Asset Metadata.

CARDINALITY: single-valued attributes use `TextMetadataField`/
`DateMetadataField`/`BooleanMetadataField`; genuinely multi-valued
attributes (Reviewer(s), Vendor/OEM, Technology/Domain, Related
Systems/Tools, Linked Policies/Standards, Equipment/Asset, Customer/
Operator, Territory/Country) use `MultiValueMetadataField` -- a typed
collection, never a comma-joined string. `ReviewerMetadataField` is its
own typed shape because a reviewer entry itself has TWO independent
sub-fields (person, organization) that must never be collapsed into one
string (see `ReviewerEntry`, mirroring the same person/organization
separation already used for prepared_by/approved_by/checked_by).

BACKWARD COMPATIBILITY: every field on every class in this module has a
safe default (`None`, `[]`, or a nested model's own all-empty default),
so `KnowledgeObject.model_validate_json(...)` against ANY pre-addendum
persisted payload (which has no `asset_metadata` key at all inside its
`metadata` object) succeeds unconditionally -- pydantic supplies the
`KnowledgeAssetMetadata` default in full, and every leaf field call
resolves to `None`/empty rather than raising. See
`backend/tests/knowledge/test_asset_metadata.py`'s dedicated backward-
compatibility test for the executed proof, not merely this claim.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "MetadataSource",
    "TextMetadataField",
    "DateMetadataField",
    "BooleanMetadataField",
    "MultiValueMetadataField",
    "ReviewerEntry",
    "ReviewerMetadataField",
    "KnowledgeAssetIdentityMetadata",
    "KnowledgeAssetClassificationMetadata",
    "KnowledgeAssetGovernanceMetadata",
    "KnowledgeAssetOwnershipMetadata",
    "KnowledgeAssetLifecycleMetadata",
    "KnowledgeAssetRolesMetadata",
    "KnowledgeAssetApplicabilityScopeMetadata",
    "KnowledgeAssetTechnicalScopeMetadata",
    "KnowledgeAssetAuditMetadata",
    "KnowledgeAssetMetadata",
    "normalize_date_value",
    "SOURCE_LIFECYCLE_STAGES",
    "normalize_source_lifecycle_stage",
]


class MetadataSource(str, Enum):
    """Where one metadata value came from -- §6's own enumerated list,
    closed because this is a genuinely bounded, structural classification
    of provenance CHANNEL (never a statement about the value's truth or
    freshness). Deliberately has NO `MODEL_INFERENCE`/`LLM_GENERATED`
    member -- see this module's own docstring ("NO METADATA GUESSING").
    """

    DOCUMENT_HEADER = "document_header"
    DOCUMENT_BODY = "document_body"
    FILE_PROPERTIES = "file_properties"
    FILENAME = "filename"
    REPOSITORY_METADATA = "repository_metadata"
    SHAREPOINT_METADATA = "sharepoint_metadata"
    INGESTION_CONFIGURATION = "ingestion_configuration"
    TRUSTED_USER_INPUT = "trusted_user_input"
    EXISTING_KNOWLEDGE_RECORD = "existing_knowledge_record"
    UNKNOWN = "unknown"


# --- Deterministic, non-guessing normalization helpers ---------------------

_EXCEL_SERIAL_EPOCH = date(1899, 12, 30)
_EXCEL_SERIAL_PLAUSIBLE_RANGE = (10_000, 100_000)  # ~1927-05-18 through
# ~2173-10-15. The 10,000 floor is deliberate, not arbitrary: it excludes
# every short (<=4-digit) purely-numeric raw value -- e.g. "2026" (a bare
# year) or "150" (a page/item count) -- from ever being MISread as a date
# serial. This is a safety gate against misinterpretation, not a guess
# about what a short number "really" means; a value below the floor
# simply stays unnormalized (raw value still preserved).


def normalize_date_value(raw_value: Optional[str]) -> Optional[date]:
    """Deterministic date normalization -- ISO-8601 first (unambiguous),
    then a bounded Excel/spreadsheet serial-number interpretation (the
    well-defined 1899-12-30 epoch every mainstream spreadsheet tool,
    including `openpyxl`, already uses) -- verified against this
    addendum's own worked example: serial 46248 -> 2026-08-14. Returns
    `None` for anything else (a free-text date, an ambiguous format, or a
    value outside the plausible serial range) -- NEVER guessed, per this
    module's own "no metadata guessing" discipline; the raw string
    remains available to the caller regardless.
    """
    if raw_value is None:
        return None
    text = raw_value.strip()
    if not text:
        return None

    try:
        return date.fromisoformat(text)
    except ValueError:
        pass

    try:
        parsed = datetime.fromisoformat(text)
        return parsed.date()
    except ValueError:
        pass

    try:
        serial = int(text)
    except ValueError:
        return None
    low, high = _EXCEL_SERIAL_PLAUSIBLE_RANGE
    if not (low <= serial <= high):
        return None
    return _EXCEL_SERIAL_EPOCH + timedelta(days=serial)


SOURCE_LIFECYCLE_STAGES: tuple[str, ...] = ("Draft", "Under Review", "Active", "Deprecated", "Archived")
"""The source/document lifecycle vocabulary (§4/§12) -- a DIFFERENT
dimension from SLOPANOC's own governance `LifecycleStatus`
(CANDIDATE/APPROVED/ARCHIVE, enums.py). See `KnowledgeAssetLifecycleMetadata`'s
own docstring for why these are never blindly mapped to one another.
"""

_SOURCE_LIFECYCLE_STAGE_LOOKUP = {stage.strip().casefold(): stage for stage in SOURCE_LIFECYCLE_STAGES}


def normalize_source_lifecycle_stage(raw_value: Optional[str]) -> Optional[str]:
    """Case-insensitive match against the closed `SOURCE_LIFECYCLE_STAGES`
    vocabulary; returns `None` (never a guess/best-fit) for anything that
    does not match one of the five canonical labels exactly, ignoring
    case/surrounding whitespace only -- e.g. "active"/"ACTIVE "/"Active"
    all normalize to "Active", but "In Progress" normalizes to `None`
    (the raw value is still preserved by the caller).
    """
    if raw_value is None:
        return None
    return _SOURCE_LIFECYCLE_STAGE_LOOKUP.get(raw_value.strip().casefold())


# --- Generic, typed raw+normalized field wrappers ---------------------------


class TextMetadataField(BaseModel):
    """A single-valued, free-text canonical metadata attribute. Type:
    scalar string. Cardinality: 0..1. `normalized_value` is populated
    only when a deterministic, non-guessed transformation applies (most
    text fields have none, and simply mirror `raw_value`'s trimmed form,
    or stay `None` if no deterministic transform is defined for that
    specific attribute -- see the owning category class's own docstring
    for which, if any, applies per field).
    """

    raw_value: Optional[str] = None
    normalized_value: Optional[str] = None
    source: Optional[MetadataSource] = None

    @field_validator("raw_value", "normalized_value")
    @classmethod
    def _blank_becomes_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class DateMetadataField(BaseModel):
    """A single-valued date canonical metadata attribute. Type: date.
    Cardinality: 0..1. `raw_value` preserves exactly what the source
    supplied (e.g. `"46248"` or `"2026-08-14"`); `normalized_value` is
    populated only by `normalize_date_value`'s deterministic ISO-8601/
    Excel-serial interpretation -- `None` if the raw value could not be
    deterministically interpreted (never guessed; the raw string remains
    available regardless).
    """

    raw_value: Optional[str] = None
    normalized_value: Optional[date] = None
    source: Optional[MetadataSource] = None

    @field_validator("raw_value")
    @classmethod
    def _blank_becomes_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class BooleanMetadataField(BaseModel):
    """A single-valued boolean flag canonical metadata attribute (e.g.
    Source-of-Truth Flag, AI-Approved Flag). Type: bool. Cardinality:
    0..1. `raw_value` preserves the source's own literal text (e.g.
    `"true"`, `"Yes"`) so a caller performing normalization can show its
    work; `normalized_value` is the resolved `bool` -- populated ONLY
    when a trusted caller (never this model itself) has performed a
    deterministic true/false interpretation. This model performs no
    truthy-string guessing on its own -- a caller must set
    `normalized_value` explicitly, exactly like `raw_value`.
    """

    raw_value: Optional[str] = None
    normalized_value: Optional[bool] = None
    source: Optional[MetadataSource] = None

    @field_validator("raw_value")
    @classmethod
    def _blank_becomes_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class MultiValueMetadataField(BaseModel):
    """A genuinely multi-valued canonical metadata attribute (§14) --
    Vendor/OEM, Technology/Domain, Related Systems/Tools, Linked
    Policies/Standards, Equipment/Asset, Customer/Operator, Territory/
    Country. Type: typed collection of strings, NEVER a comma-joined
    scalar. `raw_values` preserves each original source entry verbatim
    (trimmed only); `normalized_values` holds deterministically-derived
    forms (e.g. case-folded for comparison), populated only by an
    explicit caller -- this model performs no guessing of its own.
    """

    raw_values: list[str] = Field(default_factory=list)
    normalized_values: list[str] = Field(default_factory=list)
    source: Optional[MetadataSource] = None

    @field_validator("raw_values", "normalized_values")
    @classmethod
    def _drop_blank_entries(cls, values: list[str]) -> list[str]:
        return [v.strip() for v in values if v and v.strip()]


class ReviewerEntry(BaseModel):
    """One reviewer -- person and organization kept structurally separate
    (§15: never collapsed into `"David Condon - BCSS MSN BPD"` as the
    canonical representation; a presentation layer may format them
    together later). `raw_value` preserves the original source string
    this entry was parsed from, if applicable, for audit purposes.
    """

    person: Optional[str] = None
    organization: Optional[str] = None
    raw_value: Optional[str] = None

    @field_validator("person", "organization", "raw_value")
    @classmethod
    def _blank_becomes_none(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class ReviewerMetadataField(BaseModel):
    """The multi-valued Reviewer(s) attribute -- a typed list of
    `ReviewerEntry`, each with independently-preserved person/
    organization, never one flat multi-value string list (which could
    not represent the person/organization split per §15).
    """

    entries: list[ReviewerEntry] = Field(default_factory=list)
    source: Optional[MetadataSource] = None


# --- Category groupings (§4's own table categories) -------------------------


class KnowledgeAssetIdentityMetadata(BaseModel):
    """Identity category. `knowledge_object_id`/`document_title`/
    `revision`/`document_type` are DELIBERATELY ABSENT here -- they
    already have an authoritative home on `KnowledgeObject` itself
    (`knowledge_id`/`title`/`version.revision`/`document_type`); see this
    module's own docstring and docs/KNOWLEDGE_CONTRACT.md §25's gap
    matrix for the explicit mapping. `date` represents the document's OWN
    claimed "date of last update" (e.g. from a header/body field) --
    DELIBERATELY DISTINCT from `KnowledgeAssetAuditMetadata
    .last_modified_date`, which represents a repository/file-system-level
    audit fact; the two may legitimately disagree (a document's header
    may not have been updated even though the file was re-saved) and are
    never collapsed into one field.
    """

    document_number: TextMetadataField = Field(default_factory=TextMetadataField)
    date: DateMetadataField = Field(default_factory=DateMetadataField)
    file_format: TextMetadataField = Field(default_factory=TextMetadataField)
    language_code: TextMetadataField = Field(default_factory=TextMetadataField)


class KnowledgeAssetClassificationMetadata(BaseModel):
    """Classification category -- confidentiality, distinct from the
    pre-existing, dormant `KnowledgeMetadata.classification: Optional[str]`
    field (unused anywhere in this codebase as of this addendum, verified
    by grep; left completely untouched -- not repurposed, not migrated,
    not deprecated) to avoid silently redefining a field's meaning that a
    future caller might already rely on for something else.
    """

    confidentiality_class: TextMetadataField = Field(default_factory=TextMetadataField)
    external_confidentiality_label: TextMetadataField = Field(default_factory=TextMetadataField)


class KnowledgeAssetGovernanceMetadata(BaseModel):
    """Governance category -- deliberately INDEPENDENT dimensions (§10):
    `ai_approved_flag=true` never implies `source_of_truth_flag=true` or
    vice versa; neither implies anything about `KnowledgeAssetLifecycleMetadata
    .lifecycle_stage` or SLOPANOC's own `LifecycleStatus`. This class
    performs no cross-field inference of any kind -- see
    `backend/tests/knowledge/test_asset_metadata.py`'s dedicated
    independence tests.
    """

    source_of_truth_flag: BooleanMetadataField = Field(default_factory=BooleanMetadataField)
    ai_approved_flag: BooleanMetadataField = Field(default_factory=BooleanMetadataField)
    approval_constraints: TextMetadataField = Field(default_factory=TextMetadataField)
    linked_policies_standards: MultiValueMetadataField = Field(default_factory=MultiValueMetadataField)


class KnowledgeAssetOwnershipMetadata(BaseModel):
    """Ownership category. `business_owner` is the new canonical,
    structured field -- DISTINCT from the pre-existing, dormant
    `KnowledgeMetadata.owner: Optional[str]` field (unused anywhere in
    this codebase as of this addendum, verified by grep), which is left
    completely untouched for backward compatibility with anything that
    may already read it. A future ingestion adapter should prefer
    populating this structured field going forward.
    """

    business_owner: TextMetadataField = Field(default_factory=TextMetadataField)
    sme_team: TextMetadataField = Field(default_factory=TextMetadataField)
    domain_repository_of_origin: TextMetadataField = Field(default_factory=TextMetadataField)
    business_domain: TextMetadataField = Field(default_factory=TextMetadataField)


class KnowledgeAssetLifecycleMetadata(BaseModel):
    """Lifecycle category -- the SOURCE/document's own lifecycle stage
    (Draft/Under Review/Active/Deprecated/Archived, `SOURCE_LIFECYCLE_
    STAGES`), a GENUINELY DIFFERENT dimension from SLOPANOC's own
    governance `LifecycleStatus` (CANDIDATE/APPROVED/ARCHIVE,
    `backend/knowledge/domain/enums.py`), per §12's explicit instruction:
    "Do NOT blindly map Active = APPROVED... Document the relationship
    explicitly." The relationship: a `KnowledgeObject`'s
    `lifecycle_status` is SLOPANOC's OWN governance-transition state
    (who approved it for use inside this system, via `governance/
    service.py`'s transition table) -- it is set and changed ONLY through
    `transition_lifecycle`/`approve_version`/`archive_version`, never
    derived from this field. `lifecycle_stage` here is the SOURCE
    document's own self-reported state (e.g. a document header says
    "Active") -- informative context a human reviewer may consult before
    deciding whether to approve/archive the governed object, but it NEVER
    automatically drives a `LifecycleStatus` transition; no code in this
    addendum reads `lifecycle_stage` to change `lifecycle_status`. A
    document could legitimately be source-`Active` while still
    SLOPANOC-`CANDIDATE` (not yet reviewed), or source-`Deprecated` while
    still SLOPANOC-`APPROVED` (governance has not yet caught up) -- both
    are valid, simultaneously representable states.
    """

    lifecycle_stage: TextMetadataField = Field(default_factory=TextMetadataField)
    next_review_date: DateMetadataField = Field(default_factory=DateMetadataField)
    expiry_date: DateMetadataField = Field(default_factory=DateMetadataField)

    @field_validator("lifecycle_stage")
    @classmethod
    def _normalize_stage(cls, value: TextMetadataField) -> TextMetadataField:
        if value.normalized_value is None and value.raw_value is not None:
            resolved = normalize_source_lifecycle_stage(value.raw_value)
            if resolved is not None:
                return value.model_copy(update={"normalized_value": resolved})
        return value


class KnowledgeAssetRolesMetadata(BaseModel):
    """Process / Roles category -- person and organization kept
    structurally separate throughout (§15), never collapsed into one
    string as the canonical representation.
    """

    prepared_by: TextMetadataField = Field(default_factory=TextMetadataField)
    creator_organization: TextMetadataField = Field(default_factory=TextMetadataField)
    checked_by: TextMetadataField = Field(default_factory=TextMetadataField)
    checker_organization: TextMetadataField = Field(default_factory=TextMetadataField)
    approved_by: TextMetadataField = Field(default_factory=TextMetadataField)
    approver_organization: TextMetadataField = Field(default_factory=TextMetadataField)
    reviewers: ReviewerMetadataField = Field(default_factory=ReviewerMetadataField)


class KnowledgeAssetApplicabilityScopeMetadata(BaseModel):
    """Applicability category -- feeds
    `asset_metadata_applicability_bridge.py`'s deterministic mapping into
    the existing 6A.2 `KnowledgeApplicabilityProfile`/`Applicability`
    contract. Populating a field here does NOT by itself change
    `Applicability.dimensions` or applicability scope -- the bridge
    function must be explicitly invoked by a trusted caller; see that
    module's own docstring.

    `explicit_any_dimensions` (6A.4 addition, additive): the ONLY
    persisted representation of 6A.2's `ApplicabilityScopeKind
    .EXPLICIT_ANY` -- a list of dimension key NAMES (never values) this
    asset's governance has explicitly declared apply regardless of that
    dimension's value (e.g. `["customer"]` -- "this MOP applies to every
    customer"). Closes a real gap found during 6A.4's own audit: 6A.2's
    `KnowledgeApplicabilityProfile.explicit_any_dimensions` existed only
    as a live constructor argument with no persisted field anywhere on
    `KnowledgeObject`, so no real, already-governed object could ever
    actually BE `EXPLICIT_ANY` for a dimension -- every real object was
    structurally limited to CONSTRAINED/UNSPECIFIED only. Deliberately
    NEVER auto-populated from a value's own text (e.g. a raw value
    literally reading `"Any"` does NOT get added here automatically --
    see `asset_metadata_applicability_bridge.py`'s own "no metadata
    guessing applied to wildcard inference" discipline) -- only a
    trusted caller (ingestion configuration, governance/admin input) may
    set this, mirroring every other field in this module's own "raw
    values only from a real source, never inferred" discipline.
    """

    customer_operator: MultiValueMetadataField = Field(default_factory=MultiValueMetadataField)
    territory_country: MultiValueMetadataField = Field(default_factory=MultiValueMetadataField)
    explicit_any_dimensions: list[str] = Field(default_factory=list)

    @field_validator("explicit_any_dimensions")
    @classmethod
    def _drop_blank_dimension_names(cls, value: list[str]) -> list[str]:
        return [v.strip() for v in value if v and v.strip()]


class KnowledgeAssetTechnicalScopeMetadata(BaseModel):
    """Technical Scope category -- also feeds the applicability bridge
    (equipment/vendor/technology/related-systems dimensions). Same
    "populating this does not itself change applicability" note as
    `KnowledgeAssetApplicabilityScopeMetadata` applies here.
    """

    equipment_asset: MultiValueMetadataField = Field(default_factory=MultiValueMetadataField)
    vendor_oem: MultiValueMetadataField = Field(default_factory=MultiValueMetadataField)
    technology_domain: MultiValueMetadataField = Field(default_factory=MultiValueMetadataField)
    related_systems_tools: MultiValueMetadataField = Field(default_factory=MultiValueMetadataField)


class KnowledgeAssetAuditMetadata(BaseModel):
    """Audit category -- repository/file-system-level audit facts.
    `last_modified_date` is DELIBERATELY DISTINCT from
    `KnowledgeAssetIdentityMetadata.date` -- see that field's own
    docstring for why the two may legitimately disagree and are never
    collapsed.
    """

    last_modified_by: TextMetadataField = Field(default_factory=TextMetadataField)
    last_modified_date: DateMetadataField = Field(default_factory=DateMetadataField)


class KnowledgeAssetMetadata(BaseModel):
    """The canonical Knowledge Asset Metadata standard's top-level
    container -- one instance per `KnowledgeObject`, reached via
    `KnowledgeObject.metadata.asset_metadata` (see `models.py`'s
    `KnowledgeMetadata.asset_metadata`, the single new field this
    addendum adds to the existing, frozen `KnowledgeMetadata` model).
    Every category defaults to its own all-empty instance, so a
    freshly-constructed or backward-compatibly-deserialized
    `KnowledgeAssetMetadata` is always fully present and never `None` at
    the container level -- "missing metadata" is represented at the LEAF
    level (`raw_value=None`/`normalized_value=None`/`raw_values=[]`)
    rather than requiring a `None`-check on every category by every
    future caller.
    """

    identity: KnowledgeAssetIdentityMetadata = Field(default_factory=KnowledgeAssetIdentityMetadata)
    classification: KnowledgeAssetClassificationMetadata = Field(default_factory=KnowledgeAssetClassificationMetadata)
    governance: KnowledgeAssetGovernanceMetadata = Field(default_factory=KnowledgeAssetGovernanceMetadata)
    ownership: KnowledgeAssetOwnershipMetadata = Field(default_factory=KnowledgeAssetOwnershipMetadata)
    lifecycle: KnowledgeAssetLifecycleMetadata = Field(default_factory=KnowledgeAssetLifecycleMetadata)
    roles: KnowledgeAssetRolesMetadata = Field(default_factory=KnowledgeAssetRolesMetadata)
    applicability_scope: KnowledgeAssetApplicabilityScopeMetadata = Field(
        default_factory=KnowledgeAssetApplicabilityScopeMetadata
    )
    technical_scope: KnowledgeAssetTechnicalScopeMetadata = Field(default_factory=KnowledgeAssetTechnicalScopeMetadata)
    audit: KnowledgeAssetAuditMetadata = Field(default_factory=KnowledgeAssetAuditMetadata)
