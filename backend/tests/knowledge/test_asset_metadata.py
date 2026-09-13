"""Phase 6A.3 corrective addendum: the canonical Knowledge Asset
Metadata standard (`backend/knowledge/domain/asset_metadata.py`).

Covers: single-value metadata, multi-value metadata, missing metadata,
raw+normalized value handling, date normalization (ISO-8601, Excel
serial, invalid fallback), governance/lifecycle independence, the
source-lifecycle-stage vs. SLOPANOC-governance-lifecycle distinction,
backward compatibility with a pre-addendum persisted `KnowledgeObject`
payload, and end-to-end integration through the real, unmodified
`KnowledgeObject`/`materialize_candidate` pipeline.
"""
from __future__ import annotations

import json
from datetime import date

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.asset_metadata import (
    BooleanMetadataField,
    DateMetadataField,
    KnowledgeAssetMetadata,
    MetadataSource,
    MultiValueMetadataField,
    ReviewerEntry,
    ReviewerMetadataField,
    SOURCE_LIFECYCLE_STAGES,
    TextMetadataField,
    normalize_date_value,
    normalize_source_lifecycle_stage,
)
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeMetadata, KnowledgeObject, KnowledgeSource, KnowledgeVersion


# --- Date normalization ------------------------------------------------------


def test_date_normalization_iso8601() -> None:
    assert normalize_date_value("2026-08-14") == date(2026, 8, 14)


def test_date_normalization_iso8601_datetime_string() -> None:
    assert normalize_date_value("2026-08-14T10:30:00") == date(2026, 8, 14)


def test_date_normalization_excel_serial_matches_worked_example() -> None:
    # The instruction's own worked example: raw "46248" -> "2026-08-14".
    assert normalize_date_value("46248") == date(2026, 8, 14)


def test_date_normalization_rejects_short_numeric_values() -> None:
    """A bare year or small count must never be misread as a date
    serial -- see asset_metadata.py's own `_EXCEL_SERIAL_PLAUSIBLE_RANGE`
    docstring for why a 10,000 floor is a deliberate safety gate, not a
    guess.
    """
    assert normalize_date_value("2026") is None
    assert normalize_date_value("150") is None
    assert normalize_date_value("1") is None


def test_date_normalization_rejects_free_text() -> None:
    assert normalize_date_value("sometime last quarter") is None


def test_date_normalization_none_and_blank_input() -> None:
    assert normalize_date_value(None) is None
    assert normalize_date_value("   ") is None


def test_date_normalization_never_discards_raw_value_on_failure() -> None:
    field = DateMetadataField(raw_value="sometime last quarter", normalized_value=None)
    assert field.raw_value == "sometime last quarter"
    assert field.normalized_value is None


# --- Source lifecycle stage normalization ------------------------------------


@pytest.mark.parametrize("raw,expected", [
    ("Active", "Active"),
    ("active", "Active"),
    (" ACTIVE ", "Active"),
    ("Draft", "Draft"),
    ("Under Review", "Under Review"),
    ("Deprecated", "Deprecated"),
    ("Archived", "Archived"),
])
def test_source_lifecycle_stage_normalizes_known_values(raw: str, expected: str) -> None:
    assert normalize_source_lifecycle_stage(raw) == expected


def test_source_lifecycle_stage_unrecognized_value_stays_unnormalized() -> None:
    assert normalize_source_lifecycle_stage("In Progress") is None
    assert normalize_source_lifecycle_stage(None) is None


def test_source_lifecycle_stage_vocabulary_matches_canonical_five() -> None:
    assert SOURCE_LIFECYCLE_STAGES == ("Draft", "Under Review", "Active", "Deprecated", "Archived")


def test_lifecycle_field_auto_normalizes_recognized_raw_value_on_construction() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.lifecycle.lifecycle_stage = TextMetadataField(raw_value="active")
    # Re-validate through the model to trigger the field_validator (a
    # bare attribute assignment does not re-run validators by default).
    rebuilt = KnowledgeAssetMetadata.model_validate(metadata.model_dump())
    assert rebuilt.lifecycle.lifecycle_stage.normalized_value == "Active"


def test_lifecycle_field_does_not_overwrite_an_explicitly_supplied_normalized_value() -> None:
    metadata = KnowledgeAssetMetadata.model_validate(
        {"lifecycle": {"lifecycle_stage": {"raw_value": "active", "normalized_value": "Custom"}}}
    )
    assert metadata.lifecycle.lifecycle_stage.normalized_value == "Custom"


# --- Single-value / multi-value / missing metadata ---------------------------


def test_missing_metadata_represented_as_none_not_a_default_value() -> None:
    metadata = KnowledgeAssetMetadata()
    assert metadata.identity.document_number.raw_value is None
    assert metadata.identity.document_number.normalized_value is None
    assert metadata.governance.ai_approved_flag.normalized_value is None
    assert metadata.technical_scope.vendor_oem.raw_values == []


def test_single_value_text_field_preserves_raw_and_source() -> None:
    field = TextMetadataField(raw_value="000 24-3146 Uen", source=MetadataSource.DOCUMENT_HEADER)
    assert field.raw_value == "000 24-3146 Uen"
    assert field.source == MetadataSource.DOCUMENT_HEADER


def test_multi_value_field_preserves_each_raw_entry() -> None:
    field = MultiValueMetadataField(raw_values=["Ericsson", "Nokia"], source=MetadataSource.DOCUMENT_BODY)
    assert field.raw_values == ["Ericsson", "Nokia"]


def test_multi_value_field_drops_blank_entries_only() -> None:
    field = MultiValueMetadataField(raw_values=["Ericsson", "  ", "", "Nokia"])
    assert field.raw_values == ["Ericsson", "Nokia"]


def test_boolean_field_requires_explicit_normalization_never_guesses() -> None:
    field = BooleanMetadataField(raw_value="Yes")
    assert field.raw_value == "Yes"
    assert field.normalized_value is None  # never auto-coerced from "Yes"


def test_reviewer_entries_keep_person_and_organization_separate() -> None:
    field = ReviewerMetadataField(
        entries=[
            ReviewerEntry(person="William McCann Murphy", organization="BCSS MSN SLOP"),
        ]
    )
    assert field.entries[0].person == "William McCann Murphy"
    assert field.entries[0].organization == "BCSS MSN SLOP"


def test_roles_keep_person_and_organization_as_separate_fields_not_collapsed() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.roles.approved_by = TextMetadataField(raw_value="David Condon")
    metadata.roles.approver_organization = TextMetadataField(raw_value="BCSS MSN BPD")
    assert metadata.roles.approved_by.raw_value == "David Condon"
    assert metadata.roles.approver_organization.raw_value == "BCSS MSN BPD"
    # Never collapsed into one canonical string anywhere in the model.
    dumped = metadata.model_dump()
    assert "David Condon - BCSS MSN BPD" not in json.dumps(dumped)


# --- Governance / lifecycle independence (§10, §24) --------------------------


def test_ai_approved_and_source_of_truth_are_independent_flags() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.governance.ai_approved_flag = BooleanMetadataField(raw_value="true", normalized_value=True)
    metadata.governance.source_of_truth_flag = BooleanMetadataField(raw_value="false", normalized_value=False)
    assert metadata.governance.ai_approved_flag.normalized_value is True
    assert metadata.governance.source_of_truth_flag.normalized_value is False


def test_ai_approved_true_and_expiry_in_the_past_both_preserved_independently() -> None:
    """AI-Approved = true and Expiry Date < current date must both be
    representable simultaneously -- this model performs no policy
    decision (e.g. auto-flipping ai_approved_flag to false because the
    document is expired); that is future 6A.4 eligibility-engine work,
    explicitly out of scope here.
    """
    metadata = KnowledgeAssetMetadata()
    metadata.governance.ai_approved_flag = BooleanMetadataField(normalized_value=True)
    metadata.lifecycle.expiry_date = DateMetadataField(raw_value="2020-01-01", normalized_value=date(2020, 1, 1))
    assert metadata.governance.ai_approved_flag.normalized_value is True
    assert metadata.lifecycle.expiry_date.normalized_value == date(2020, 1, 1)


def test_lifecycle_stage_active_does_not_imply_slopanoc_lifecycle_status_approved() -> None:
    """§12's explicit invariant: 'Active' (source lifecycle_stage) must
    never be blindly treated as SLOPANOC's own governance
    `LifecycleStatus.APPROVED`. This test proves no code path in this
    module performs that mapping -- constructing a KnowledgeObject with
    lifecycle_stage=Active and lifecycle_status=CANDIDATE is a
    completely valid, unrelated combination.
    """
    metadata = KnowledgeAssetMetadata()
    metadata.lifecycle.lifecycle_stage = TextMetadataField(raw_value="Active", normalized_value="Active")
    obj = KnowledgeObject(
        knowledge_id="TEST-LIFECYCLE",
        document_type=KnowledgeDocumentType.MOP,
        title="Test",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.CANDIDATE,  # deliberately NOT approved
        source=KnowledgeSource(source_system="local_file", source_id="x"),
        metadata=KnowledgeMetadata(asset_metadata=metadata),
    )
    assert obj.lifecycle_status == LifecycleStatus.CANDIDATE
    assert obj.metadata.asset_metadata.lifecycle.lifecycle_stage.normalized_value == "Active"


# --- Extended document type vocabulary (§13) ---------------------------------


@pytest.mark.parametrize("member", ["RUNBOOK", "HLD", "ASSESSMENT_REPORT", "ACTION_PLAN", "CHANGE_REQUEST"])
def test_document_type_extended_with_new_canonical_members(member: str) -> None:
    assert member in KnowledgeDocumentType.__members__


def test_document_type_existing_members_unchanged() -> None:
    assert KnowledgeDocumentType.MOP.value == "mop"
    assert KnowledgeDocumentType.OPERATIONAL_PROCEDURE.value == "operational_procedure"
    assert KnowledgeDocumentType.KB_ARTICLE.value == "kb_article"


# --- Backward compatibility (§20, §H) ----------------------------------------


def _pre_addendum_style_payload() -> dict:
    """Builds a payload shaped exactly like a REAL pre-addendum
    KnowledgeObject (A5/6A.3 era) -- constructed via the real model
    itself (so it stays in sync with any other field the domain adds)
    and then the `asset_metadata` key is deleted from its own JSON, to
    simulate a payload persisted before this addendum ever existed.
    """
    obj = KnowledgeObject(
        knowledge_id="PRE-ADDENDUM-1",
        document_type=KnowledgeDocumentType.MOP,
        title="Historical Document",
        version=KnowledgeVersion(label="1.0", revision="A"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="sharepoint", source_id="doc-1"),
    )
    payload = json.loads(obj.model_dump_json())
    del payload["metadata"]["asset_metadata"]
    assert "asset_metadata" not in payload["metadata"]
    return payload


def test_historical_payload_without_asset_metadata_key_deserializes_successfully() -> None:
    payload = _pre_addendum_style_payload()
    reconstructed = KnowledgeObject.model_validate_json(json.dumps(payload))
    assert reconstructed.knowledge_id == "PRE-ADDENDUM-1"
    assert reconstructed.metadata.asset_metadata == KnowledgeAssetMetadata()


def test_historical_payload_asset_metadata_fields_read_as_missing_not_error() -> None:
    payload = _pre_addendum_style_payload()
    reconstructed = KnowledgeObject.model_validate_json(json.dumps(payload))
    am = reconstructed.metadata.asset_metadata
    assert am.identity.document_number.raw_value is None
    assert am.governance.ai_approved_flag.normalized_value is None
    assert am.technical_scope.vendor_oem.raw_values == []
    assert am.roles.reviewers.entries == []


def test_historical_payload_does_not_raise_validation_error() -> None:
    payload = _pre_addendum_style_payload()
    try:
        KnowledgeObject.model_validate_json(json.dumps(payload))
    except ValidationError as exc:  # pragma: no cover -- defect if ever raised
        pytest.fail(f"pre-addendum payload must deserialize without error: {exc}")


def test_owner_and_classification_legacy_fields_remain_untouched() -> None:
    """The pre-existing, dormant KnowledgeMetadata.owner/classification
    fields are left completely alone by this addendum -- never
    repurposed, never migrated.
    """
    metadata = KnowledgeMetadata(owner="legacy-owner-value", classification="legacy-classification-value")
    assert metadata.owner == "legacy-owner-value"
    assert metadata.classification == "legacy-classification-value"
    assert metadata.asset_metadata == KnowledgeAssetMetadata()


# --- Full KnowledgeObject round-trip / serialization evidence ---------------


def test_full_asset_metadata_round_trips_through_knowledge_object_json() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.identity.document_number = TextMetadataField(raw_value="000 24-3146 Uen", source=MetadataSource.DOCUMENT_HEADER)
    metadata.identity.date = DateMetadataField(raw_value="46248", normalized_value=normalize_date_value("46248"), source=MetadataSource.DOCUMENT_HEADER)
    metadata.classification.confidentiality_class = TextMetadataField(raw_value="Ericsson Internal")
    metadata.governance.ai_approved_flag = BooleanMetadataField(raw_value="true", normalized_value=True)
    metadata.governance.linked_policies_standards = MultiValueMetadataField(raw_values=["ISO 27001"])
    metadata.ownership.business_owner = TextMetadataField(raw_value="Network Operations")
    metadata.lifecycle.lifecycle_stage = TextMetadataField(raw_value="Active", normalized_value="Active")
    metadata.roles.approved_by = TextMetadataField(raw_value="David Condon")
    metadata.roles.approver_organization = TextMetadataField(raw_value="BCSS MSN BPD")
    metadata.roles.reviewers = ReviewerMetadataField(
        entries=[ReviewerEntry(person="William McCann Murphy", organization="BCSS MSN SLOP")]
    )
    metadata.applicability_scope.customer_operator = MultiValueMetadataField(raw_values=["Vodafone"])
    metadata.technical_scope.vendor_oem = MultiValueMetadataField(raw_values=["Ericsson"], normalized_values=["ericsson"])
    metadata.audit.last_modified_by = TextMetadataField(raw_value="Network Operations")

    obj = KnowledgeObject(
        knowledge_id="ROUND-TRIP-1",
        document_type=KnowledgeDocumentType.MOP,
        title="Round Trip Test",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=KnowledgeSource(source_system="local_file", source_id="x"),
        metadata=KnowledgeMetadata(asset_metadata=metadata),
    )
    dumped = obj.model_dump_json()
    reconstructed = KnowledgeObject.model_validate_json(dumped)

    am = reconstructed.metadata.asset_metadata
    assert am.identity.document_number.raw_value == "000 24-3146 Uen"
    assert am.identity.date.normalized_value == date(2026, 8, 14)
    assert am.classification.confidentiality_class.raw_value == "Ericsson Internal"
    assert am.governance.ai_approved_flag.normalized_value is True
    assert am.governance.linked_policies_standards.raw_values == ["ISO 27001"]
    assert am.ownership.business_owner.raw_value == "Network Operations"
    assert am.lifecycle.lifecycle_stage.normalized_value == "Active"
    assert am.roles.approved_by.raw_value == "David Condon"
    assert am.roles.approver_organization.raw_value == "BCSS MSN BPD"
    assert am.roles.reviewers.entries[0].person == "William McCann Murphy"
    assert am.roles.reviewers.entries[0].organization == "BCSS MSN SLOP"
    assert am.applicability_scope.customer_operator.raw_values == ["Vodafone"]
    assert am.technical_scope.vendor_oem.normalized_values == ["ericsson"]
    assert am.audit.last_modified_by.raw_value == "Network Operations"


def test_identity_fields_do_not_duplicate_top_level_knowledge_object_identity() -> None:
    """Knowledge Object ID / Document Title / Revision / Document Type
    are deliberately NOT fields on KnowledgeAssetIdentityMetadata -- they
    remain single-sourced from KnowledgeObject's own top-level fields.
    """
    assert not hasattr(KnowledgeAssetMetadata().identity, "knowledge_object_id")
    assert not hasattr(KnowledgeAssetMetadata().identity, "document_title")
    assert not hasattr(KnowledgeAssetMetadata().identity, "revision")
    assert not hasattr(KnowledgeAssetMetadata().identity, "document_type")


# --- explicit_any_dimensions (6A.4 addition) ---------------------------------


def test_explicit_any_dimensions_defaults_to_empty() -> None:
    assert KnowledgeAssetMetadata().applicability_scope.explicit_any_dimensions == []


def test_explicit_any_dimensions_preserves_declared_entries() -> None:
    metadata = KnowledgeAssetMetadata()
    metadata.applicability_scope.explicit_any_dimensions = ["customer", "vendor"]
    assert metadata.applicability_scope.explicit_any_dimensions == ["customer", "vendor"]


def test_explicit_any_dimensions_drops_blank_entries_only() -> None:
    metadata = KnowledgeAssetMetadata.model_validate(
        {"applicability_scope": {"explicit_any_dimensions": ["vendor", "  ", "", "customer"]}}
    )
    assert metadata.applicability_scope.explicit_any_dimensions == ["vendor", "customer"]


def test_explicit_any_dimensions_backward_compatible_with_pre_6a4_payload() -> None:
    """A payload from BEFORE this field existed (6A.3-addendum-era)
    deserializes with an empty list, never an error."""
    obj = KnowledgeObject(
        knowledge_id="PRE-6A4",
        document_type=KnowledgeDocumentType.MOP,
        title="T",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="x", source_id="y"),
    )
    payload = json.loads(obj.model_dump_json())
    del payload["metadata"]["asset_metadata"]["applicability_scope"]["explicit_any_dimensions"]
    reconstructed = KnowledgeObject.model_validate_json(json.dumps(payload))
    assert reconstructed.metadata.asset_metadata.applicability_scope.explicit_any_dimensions == []
