"""Phase 6A.3 corrective addendum: the Knowledge Asset Metadata ->
6A.2 Applicability bridge
(`backend/knowledge/domain/asset_metadata_applicability_bridge.py`).

Proves the critical invariant (§9/§23): missing metadata NEVER becomes
ANY -- a dimension with no populated asset-metadata values is simply
ABSENT from the derived dimensions dict, which 6A.2's own
`dimension_scope` (unmodified by this addendum) correctly resolves to
`ApplicabilityScopeKind.UNSPECIFIED`, the safe fail-closed default.
`ApplicabilityScopeKind.EXPLICIT_ANY` is never derived by this bridge --
it remains exactly what 6A.2 already defined it as: an explicit,
separate, trusted-caller-supplied declaration.
"""
from __future__ import annotations

from backend.knowledge.domain.asset_metadata import KnowledgeAssetMetadata, MultiValueMetadataField
from backend.knowledge.domain.asset_metadata_applicability_bridge import (
    ASSET_METADATA_APPLICABILITY_DIMENSIONS,
    derive_applicability_dimensions_from_asset_metadata,
)
from backend.knowledge.domain.models import Applicability
from backend.knowledge.domain.telco_applicability import ApplicabilityScopeKind, dimension_scope, from_knowledge_object


def test_no_asset_metadata_produces_empty_dimensions() -> None:
    dims = derive_applicability_dimensions_from_asset_metadata(KnowledgeAssetMetadata())
    assert dims == {}


def test_vendor_with_normalized_values_becomes_constrained_dimension() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Ericsson"], normalized_values=["ericsson"])
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    assert dims == {"vendor": ["ericsson"]}

    profile = from_knowledge_object(knowledge_id="K1", version_label="1.0", applicability=Applicability(dimensions=dims))
    assert dimension_scope(profile, "vendor") == ApplicabilityScopeKind.CONSTRAINED


def test_vendor_missing_resolves_to_unspecified_never_any() -> None:
    """THE critical proof (§9/§23): missing metadata != ANY."""
    metadata = KnowledgeAssetMetadata()  # vendor never populated
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    assert "vendor" not in dims

    profile = from_knowledge_object(knowledge_id="K1", version_label="1.0", applicability=Applicability(dimensions=dims))
    scope = dimension_scope(profile, "vendor")
    assert scope == ApplicabilityScopeKind.UNSPECIFIED
    assert scope != ApplicabilityScopeKind.EXPLICIT_ANY


def test_vendor_explicit_any_requires_a_separate_trusted_declaration_never_inferred() -> None:
    """Even if a caller writes a raw value that LOOKS like a wildcard
    declaration (e.g. "Any", "All vendors"), the bridge does NOT infer
    EXPLICIT_ANY from that text -- it becomes an ordinary CONSTRAINED
    dimension with that literal string as its one allowed value, exactly
    like any other text. EXPLICIT_ANY can only be produced by a trusted
    caller passing `explicit_any_dimensions` explicitly to
    `KnowledgeApplicabilityProfile`/`from_knowledge_object`, per 6A.2's
    own unmodified contract.
    """
    metadata = KnowledgeAssetMetadata()
    metadata.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Any"])
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    assert dims == {"vendor": ["Any"]}  # NOT treated as a wildcard

    # The bridge itself produces only CONSTRAINED signals -- to get
    # EXPLICIT_ANY, a caller must pass it explicitly, unrelated to any
    # asset-metadata field value:
    profile_constrained = from_knowledge_object(
        knowledge_id="K1", version_label="1.0", applicability=Applicability(dimensions=dims)
    )
    assert dimension_scope(profile_constrained, "vendor") == ApplicabilityScopeKind.CONSTRAINED

    profile_explicit_any = from_knowledge_object(
        knowledge_id="K2", version_label="1.0", applicability=Applicability(), explicit_any_dimensions=frozenset({"vendor"})
    )
    assert dimension_scope(profile_explicit_any, "vendor") == ApplicabilityScopeKind.EXPLICIT_ANY


def test_customer_missing_resolves_to_unspecified() -> None:
    metadata = KnowledgeAssetMetadata()
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    profile = from_knowledge_object(knowledge_id="K1", version_label="1.0", applicability=Applicability(dimensions=dims))
    assert dimension_scope(profile, "customer") == ApplicabilityScopeKind.UNSPECIFIED


def test_customer_with_values_becomes_constrained() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.applicability_scope.customer_operator = MultiValueMetadataField(raw_values=["Vodafone"])
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    profile = from_knowledge_object(knowledge_id="K1", version_label="1.0", applicability=Applicability(dimensions=dims))
    assert dimension_scope(profile, "customer") == ApplicabilityScopeKind.CONSTRAINED


def test_technology_domain_missing_resolves_to_unspecified() -> None:
    metadata = KnowledgeAssetMetadata()
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    profile = from_knowledge_object(knowledge_id="K1", version_label="1.0", applicability=Applicability(dimensions=dims))
    assert dimension_scope(profile, "technology") == ApplicabilityScopeKind.UNSPECIFIED


def test_technology_domain_with_values_becomes_constrained() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.technical_scope.technology_domain = MultiValueMetadataField(raw_values=["RAN"])
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    profile = from_knowledge_object(knowledge_id="K1", version_label="1.0", applicability=Applicability(dimensions=dims))
    assert dimension_scope(profile, "technology") == ApplicabilityScopeKind.CONSTRAINED


def test_equipment_missing_resolves_to_unspecified() -> None:
    metadata = KnowledgeAssetMetadata()
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    profile = from_knowledge_object(knowledge_id="K1", version_label="1.0", applicability=Applicability(dimensions=dims))
    assert dimension_scope(profile, "equipment") == ApplicabilityScopeKind.UNSPECIFIED


def test_equipment_with_values_becomes_constrained() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.technical_scope.equipment_asset = MultiValueMetadataField(raw_values=["RBS6601"])
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    profile = from_knowledge_object(knowledge_id="K1", version_label="1.0", applicability=Applicability(dimensions=dims))
    assert dimension_scope(profile, "equipment") == ApplicabilityScopeKind.CONSTRAINED


def test_normalized_values_take_precedence_over_raw_values_when_both_present() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Ericsson"], normalized_values=["ericsson-normalized"])
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    assert dims["vendor"] == ["ericsson-normalized"]


def test_raw_values_used_as_fallback_when_normalized_values_absent() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Ericsson"])
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    assert dims["vendor"] == ["Ericsson"]


def test_all_six_bridged_dimensions_populated_simultaneously() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.applicability_scope.customer_operator = MultiValueMetadataField(raw_values=["Vodafone"])
    metadata.applicability_scope.territory_country = MultiValueMetadataField(raw_values=["UK"])
    metadata.technical_scope.equipment_asset = MultiValueMetadataField(raw_values=["RBS6601"])
    metadata.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Ericsson"])
    metadata.technical_scope.technology_domain = MultiValueMetadataField(raw_values=["RAN"])
    metadata.technical_scope.related_systems_tools = MultiValueMetadataField(raw_values=["Helix"])

    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    assert set(dims) == {"customer", "territory", "equipment", "vendor", "technology", "related_systems"}
    assert set(ASSET_METADATA_APPLICABILITY_DIMENSIONS) == set(dims)


def test_dimensions_dict_is_directly_usable_by_applicability_model() -> None:
    """Proves the bridge's output is not merely dict-shaped but actually
    ACCEPTED by the real, unmodified Applicability model (which rejects
    an empty value list, blank values, etc.) -- a real integration
    proof, not just a shape assertion.
    """
    metadata = KnowledgeAssetMetadata()
    metadata.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Ericsson", "Nokia"])
    dims = derive_applicability_dimensions_from_asset_metadata(metadata)
    applicability = Applicability(dimensions=dims)  # must not raise
    assert applicability.dimensions["vendor"] == ["Ericsson", "Nokia"]
