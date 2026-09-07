"""Phase 5.1A: domain value objects and the KnowledgeObject aggregate.

Only structural validation is exercised here -- no business/governance
logic exists yet to test (lifecycle transitions, "current version"
resolution, applicability matching are later 5.1 sub-phases).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import (
    Applicability,
    KnowledgeMetadata,
    KnowledgeObject,
    KnowledgeSection,
    KnowledgeSource,
    KnowledgeVersion,
)

_NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _source(**overrides: object) -> KnowledgeSource:
    fields = {"source_system": "sharepoint", "source_id": "doc-123"}
    fields.update(overrides)
    return KnowledgeSource(**fields)


def _version(**overrides: object) -> KnowledgeVersion:
    fields: dict[str, object] = {"label": "1.0"}
    fields.update(overrides)
    return KnowledgeVersion(**fields)


def _section(section_id: str, knowledge_id: str, sequence: int, **overrides: object) -> KnowledgeSection:
    fields: dict[str, object] = {
        "section_id": section_id,
        "knowledge_id": knowledge_id,
        "sequence": sequence,
        "content": "some section content",
    }
    fields.update(overrides)
    return KnowledgeSection(**fields)


def _knowledge_object(document_type: KnowledgeDocumentType, **overrides: object) -> KnowledgeObject:
    fields: dict[str, object] = {
        "knowledge_id": "k1",
        "document_type": document_type,
        "title": "Some governed document",
        "version": _version(),
        "lifecycle_status": LifecycleStatus.APPROVED,
        "source": _source(),
        "sections": [_section("s1", "k1", 0), _section("s2", "k1", 1)],
    }
    fields.update(overrides)
    return KnowledgeObject(**fields)


# --- KnowledgeSource ---------------------------------------------------


def test_valid_generic_source_accepted() -> None:
    source = _source(source_uri="https://example.invalid/doc-123", display_name="Upgrade MOP")
    assert source.source_system == "sharepoint"
    assert source.source_id == "doc-123"


def test_source_blank_source_system_rejected() -> None:
    with pytest.raises(ValidationError):
        _source(source_system="")


def test_source_blank_source_id_rejected() -> None:
    with pytest.raises(ValidationError):
        _source(source_id="   ")


def test_source_optional_locator_allowed() -> None:
    source = _source()
    assert source.source_uri is None
    assert source.display_name is None


# --- KnowledgeVersion ----------------------------------------------------


@pytest.mark.parametrize("label", ["1.0", "4.2", "Rev-A", "2026-08", "customer-x-rev-7"])
def test_arbitrary_operational_version_labels_accepted(label: str) -> None:
    assert _version(label=label).label == label


def test_version_blank_label_rejected() -> None:
    with pytest.raises(ValidationError):
        _version(label="")


def test_version_valid_effective_date_range_accepted() -> None:
    version = _version(effective_from=_NOW, effective_to=_NOW + timedelta(days=30))
    assert version.effective_to > version.effective_from


def test_version_effective_to_before_effective_from_rejected() -> None:
    with pytest.raises(ValidationError):
        _version(effective_from=_NOW, effective_to=_NOW - timedelta(days=1))


def test_version_self_supersession_rejected() -> None:
    with pytest.raises(ValidationError):
        _version(label="1.0", supersedes=["1.0"])

    with pytest.raises(ValidationError):
        _version(label="1.0", superseded_by=["1.0"])


def test_version_supersedes_a_different_label_is_fine() -> None:
    version = _version(label="2.0", supersedes=["1.0"])
    assert version.supersedes == ["1.0"]


# --- KnowledgeSection ------------------------------------------------------


def test_valid_section_accepted() -> None:
    section = _section("s1", "k1", 0, heading="Purpose", section_type="purpose")
    assert section.section_id == "s1"
    assert section.section_type == "purpose"


def test_section_empty_content_rejected() -> None:
    with pytest.raises(ValidationError):
        _section("s1", "k1", 0, content="")


def test_section_negative_sequence_rejected() -> None:
    with pytest.raises(ValidationError):
        _section("s1", "k1", -1)


def test_section_extensible_type_supported() -> None:
    """`section_type` is a free-form string -- any label is accepted,
    never validated against a fixed enum.
    """
    section = _section("s1", "k1", 0, section_type="a-brand-new-future-section-kind")
    assert section.section_type == "a-brand-new-future-section-kind"


# --- KnowledgeMetadata / Applicability --------------------------------------


def test_metadata_extensible_attributes_supported() -> None:
    metadata = KnowledgeMetadata(owner="noc-team", tags=["upgrade", "5g"], attributes={"vendor": "ericsson", "release": "24.3"})
    assert metadata.attributes["vendor"] == "ericsson"
    assert metadata.tags == ["upgrade", "5g"]


def test_metadata_defaults_are_not_shared_between_instances() -> None:
    a = KnowledgeMetadata()
    b = KnowledgeMetadata()
    a.tags.append("only-on-a")
    a.attributes["only-on-a"] = True
    assert b.tags == []
    assert b.attributes == {}


def test_applicability_extensible_dimensions_can_be_represented() -> None:
    applicability = Applicability(dimensions={"domain": ["RAN"], "technology": ["5G", "4G"], "vendor": ["Ericsson"]})
    assert applicability.dimensions["technology"] == ["5G", "4G"]


def test_applicability_has_no_matching_behavior_in_5_1a() -> None:
    """5.1A only represents applicability -- there is no evaluate/match
    method or MATCH/PARTIAL_MATCH/NOT_APPLICABLE/UNKNOWN outcome type
    anywhere on this model; that is 5.1B.
    """
    applicability = Applicability(dimensions={"domain": ["RAN"]})
    assert not hasattr(applicability, "evaluate")
    assert not hasattr(applicability, "evaluate_applicability")
    assert not hasattr(applicability, "match")


def test_applicability_defaults_are_not_shared_between_instances() -> None:
    a = Applicability()
    b = Applicability()
    a.dimensions["domain"] = ["RAN"]
    assert b.dimensions == {}


# --- KnowledgeObject ---------------------------------------------------------


def test_valid_mop() -> None:
    obj = _knowledge_object(KnowledgeDocumentType.MOP)
    assert obj.document_type is KnowledgeDocumentType.MOP


def test_valid_sop() -> None:
    obj = _knowledge_object(KnowledgeDocumentType.SOP)
    assert obj.document_type is KnowledgeDocumentType.SOP


def test_valid_rca() -> None:
    obj = _knowledge_object(KnowledgeDocumentType.RCA)
    assert obj.document_type is KnowledgeDocumentType.RCA


def test_valid_kb_article() -> None:
    obj = _knowledge_object(KnowledgeDocumentType.KB_ARTICLE)
    assert obj.document_type is KnowledgeDocumentType.KB_ARTICLE


def test_object_blank_knowledge_id_rejected() -> None:
    with pytest.raises(ValidationError):
        _knowledge_object(KnowledgeDocumentType.MOP, knowledge_id="", sections=[])


def test_object_blank_title_rejected() -> None:
    with pytest.raises(ValidationError):
        _knowledge_object(KnowledgeDocumentType.MOP, title="  ", sections=[])


def test_object_with_no_sections_is_valid() -> None:
    obj = _knowledge_object(KnowledgeDocumentType.MOP, sections=[])
    assert obj.sections == []


def test_object_section_knowledge_id_mismatch_rejected() -> None:
    with pytest.raises(ValidationError):
        _knowledge_object(
            KnowledgeDocumentType.MOP,
            sections=[_section("s1", "k1", 0), _section("s2", "SOME-OTHER-KNOWLEDGE-ID", 1)],
        )


def test_object_duplicate_section_ids_rejected() -> None:
    with pytest.raises(ValidationError):
        _knowledge_object(
            KnowledgeDocumentType.MOP,
            sections=[_section("s1", "k1", 0), _section("s1", "k1", 1)],
        )


def test_object_duplicate_sequence_rejected() -> None:
    with pytest.raises(ValidationError):
        _knowledge_object(
            KnowledgeDocumentType.MOP,
            sections=[_section("s1", "k1", 0), _section("s2", "k1", 0)],
        )


def test_object_section_order_is_deterministic() -> None:
    obj = _knowledge_object(
        KnowledgeDocumentType.MOP,
        sections=[_section("s2", "k1", 1), _section("s1", "k1", 0), _section("s3", "k1", 2)],
    )
    # Construction order is preserved as given -- ordering by `sequence`
    # for display/consumption is the caller's responsibility in this
    # phase (no automatic re-sort is performed, so behavior stays
    # obvious/deterministic rather than implicitly reordering input).
    assert [s.section_id for s in obj.sections] == ["s2", "s1", "s3"]
    assert sorted(obj.sections, key=lambda s: s.sequence)[0].section_id == "s1"
