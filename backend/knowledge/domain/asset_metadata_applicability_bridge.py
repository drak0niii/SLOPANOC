"""Phase 6A.3 corrective addendum: bridges `KnowledgeAssetMetadata`'s
applicability-relevant fields into the EXISTING, unmodified 6A.2
`KnowledgeApplicabilityProfile`/`Applicability` contract
(`backend/knowledge/domain/telco_applicability.py`, `models.py`).

Does NOT duplicate `KnowledgeApplicabilityProfile`/`ApplicabilityScopeKind`
-- neither is modified or re-implemented here. This module only answers:
"given this asset's structured Customer/Territory/Equipment/Vendor/
Technology/Related-Systems metadata, what `Applicability.dimensions`
dict would represent it?" -- a pure, deterministic mapping function, not
a second applicability engine.

CRITICAL INVARIANT (§9's own explicit requirement, preserved unchanged
from 6A.2): missing metadata never becomes ANY. This module produces
ONLY `CONSTRAINED`-shaped dimension entries (a dimension key appears in
the returned dict if and only if at least one normalized value exists
for it) -- it NEVER decides a dimension is `EXPLICIT_ANY`. That
decision remains exactly what 6A.2 already defined it as: an explicit,
separate, trusted-caller-supplied `explicit_any_dimensions` argument to
`KnowledgeApplicabilityProfile`/`from_knowledge_object` -- never inferred
from a metadata value's text (e.g. a raw value literally reading "Any"
or "All vendors" does NOT automatically become EXPLICIT_ANY here; that
would require guessing which free-text strings count as an explicit
wildcard declaration, which this addendum deliberately does not
attempt -- see docs/KNOWLEDGE_CONTRACT.md §25). A dimension with zero
normalized values is simply ABSENT from the returned dict, which
6A.2's own `dimension_scope` already correctly resolves to
`ApplicabilityScopeKind.UNSPECIFIED` -- the safe, fail-closed default.
"""
from __future__ import annotations

from backend.knowledge.domain.asset_metadata import KnowledgeAssetMetadata
from backend.knowledge.domain.models import normalize_dimension_key

__all__ = ["ASSET_METADATA_APPLICABILITY_DIMENSIONS", "derive_applicability_dimensions_from_asset_metadata"]


ASSET_METADATA_APPLICABILITY_DIMENSIONS: dict[str, str] = {
    "customer": "customer_operator",
    "territory": "territory_country",
    "equipment": "equipment_asset",
    "vendor": "vendor_oem",
    "technology": "technology_domain",
    "related_systems": "related_systems_tools",
}
"""Canonical dimension key -> the `KnowledgeAssetMetadata` field it is
derived from. Dimension keys are plain strings (never a closed enum),
matching `Applicability.dimensions`' own open-dimension philosophy
(§11.3) -- this mapping exists so both this module and any future
caller use the exact same dimension key spelling, normalized through the
same `normalize_dimension_key` 6A.2/5.1B already use.
"""


def derive_applicability_dimensions_from_asset_metadata(
    asset_metadata: KnowledgeAssetMetadata,
) -> dict[str, list[str]]:
    """Pure, deterministic mapping -- no database/model call, no mutation
    of `asset_metadata`. Returns a dict shaped exactly like
    `Applicability.dimensions` (dimension key -> non-empty list of
    values), suitable for passing to `Applicability(dimensions=...)` or
    for comparing directly against an existing object's own
    `applicability.dimensions`.

    A dimension is included ONLY if the corresponding asset-metadata
    field has at least one entry in EITHER `normalized_values` (preferred)
    OR, if no normalized values exist yet, `raw_values` (falls back so a
    caller who has populated raw source values but not yet run
    normalization still gets a usable CONSTRAINED dimension -- normalized
    values, when present, take precedence and are used exclusively over
    raw values for that field, never mixed together for one dimension).
    A field with zero entries in both contributes NO key at all -- never
    an empty-list key (which `Applicability`'s own validator rejects
    anyway, since `normalize_applicability_dimensions` treats an empty
    value list as invalid input, not an implicit wildcard).
    """
    scope = asset_metadata.applicability_scope
    technical = asset_metadata.technical_scope
    field_by_attribute = {
        "customer_operator": scope.customer_operator,
        "territory_country": scope.territory_country,
        "equipment_asset": technical.equipment_asset,
        "vendor_oem": technical.vendor_oem,
        "technology_domain": technical.technology_domain,
        "related_systems_tools": technical.related_systems_tools,
    }

    dimensions: dict[str, list[str]] = {}
    for dimension_key, attribute_name in ASSET_METADATA_APPLICABILITY_DIMENSIONS.items():
        field = field_by_attribute[attribute_name]
        values = field.normalized_values or field.raw_values
        if not values:
            continue
        dimensions[normalize_dimension_key(dimension_key)] = list(values)
    return dimensions
