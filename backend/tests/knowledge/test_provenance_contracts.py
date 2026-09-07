"""Phase 5.1H: structural validation of `KnowledgeEvidenceItem`,
`KnowledgeEvidenceSet`, and `KnowledgeEvidenceSelectionKey` -- the
critical trust-boundary contract that a selector cannot carry content/
source/title/locator, plus internal-consistency and duplicate-identity
enforcement.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.contracts import KnowledgeEvidenceReference
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import (
    KnowledgeEvidenceItem,
    KnowledgeEvidenceSelectionKey,
    KnowledgeEvidenceSet,
)


def _section(section_id: str = "k1:v1:s0", knowledge_id: str = "k1", content: str = "router outage recovery") -> KnowledgeSection:
    return KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, heading="Overview", sequence=0, content=content)


def _source(source_system: str = "test", source_id: str = "doc-1") -> KnowledgeSource:
    return KnowledgeSource(source_system=source_system, source_id=source_id)


def _reference(**overrides: object) -> KnowledgeEvidenceReference:
    fields: dict[str, object] = dict(
        knowledge_id="k1", version_label="v1", section_id="k1:v1:s0", source_system="test", source_id="doc-1"
    )
    fields.update(overrides)
    return KnowledgeEvidenceReference(**fields)


def _item(**overrides: object) -> KnowledgeEvidenceItem:
    fields: dict[str, object] = dict(
        reference=_reference(),
        title="Router Outage Guide",
        document_type=KnowledgeDocumentType.SOP,
        lifecycle_status=LifecycleStatus.APPROVED,
        source=_source(),
        section=_section(),
    )
    fields.update(overrides)
    return KnowledgeEvidenceItem(**fields)


# --- KnowledgeEvidenceItem: valid construction --------------------------------


def test_valid_evidence_item_constructs() -> None:
    item = _item()
    assert item.reference.knowledge_id == "k1"
    assert item.section.content == "router outage recovery"


def test_evidence_item_reuses_knowledge_section_and_source_directly() -> None:
    section = _section()
    source = _source()
    item = _item(section=section, source=source)
    assert item.section == section
    assert item.source == source


def test_evidence_item_rejects_blank_title() -> None:
    with pytest.raises(ValidationError):
        _item(title="   ")


# --- KnowledgeEvidenceItem: internal consistency -------------------------------


def test_evidence_item_rejects_reference_section_id_not_matching_section() -> None:
    with pytest.raises(ValidationError):
        _item(reference=_reference(section_id="different-section"))


def test_evidence_item_rejects_reference_knowledge_id_not_matching_section() -> None:
    with pytest.raises(ValidationError):
        _item(reference=_reference(knowledge_id="different-knowledge-id"))


def test_evidence_item_rejects_reference_source_identity_not_matching_source() -> None:
    with pytest.raises(ValidationError):
        _item(reference=_reference(source_system="other-system"))
    with pytest.raises(ValidationError):
        _item(reference=_reference(source_id="other-doc"))


def test_evidence_item_contains_no_hidden_reasoning_field() -> None:
    field_names = set(KnowledgeEvidenceItem.model_fields.keys())
    assert field_names == {"reference", "title", "document_type", "lifecycle_status", "source", "section"}


# --- KnowledgeEvidenceSet: ordering, duplicates, serialization -----------------


def test_evidence_set_defaults_to_empty() -> None:
    assert KnowledgeEvidenceSet().items == []


def test_evidence_set_preserves_item_order() -> None:
    item_a = _item(reference=_reference(section_id="s-a"), section=_section(section_id="s-a"))
    item_b = _item(reference=_reference(section_id="s-b"), section=_section(section_id="s-b"))
    evidence_set = KnowledgeEvidenceSet(items=[item_b, item_a])
    assert [i.reference.section_id for i in evidence_set.items] == ["s-b", "s-a"]


def test_evidence_set_rejects_duplicate_identity() -> None:
    item = _item()
    with pytest.raises(ValidationError):
        KnowledgeEvidenceSet(items=[item, item.model_copy(deep=True)])


def test_evidence_set_allows_different_section_ids_within_same_knowledge_id() -> None:
    item_a = _item(reference=_reference(section_id="s-a"), section=_section(section_id="s-a"))
    item_b = _item(reference=_reference(section_id="s-b"), section=_section(section_id="s-b"))
    evidence_set = KnowledgeEvidenceSet(items=[item_a, item_b])
    assert len(evidence_set.items) == 2


def test_evidence_set_is_serializable_with_no_hidden_state() -> None:
    evidence_set = KnowledgeEvidenceSet(items=[_item()])
    payload = evidence_set.model_dump_json()
    restored = KnowledgeEvidenceSet.model_validate_json(payload)
    assert restored == evidence_set


def test_evidence_set_has_no_implicit_timestamp_field() -> None:
    field_names = set(KnowledgeEvidenceSet.model_fields.keys())
    assert field_names == {"items"}


# --- KnowledgeEvidenceSelectionKey: the critical trust-boundary contract -------


def test_selection_key_contains_only_the_minimal_identity_fields() -> None:
    field_names = set(KnowledgeEvidenceSelectionKey.model_fields.keys())
    assert field_names == {"knowledge_id", "version_label", "section_id"}


def test_selection_key_cannot_carry_content_source_title_or_locator() -> None:
    forbidden_fields = {"content", "source", "source_system", "source_id", "source_locator", "title", "document_type", "lifecycle_status", "snippet", "heading"}
    field_names = set(KnowledgeEvidenceSelectionKey.model_fields.keys())
    assert field_names.isdisjoint(forbidden_fields)


def test_selection_key_accepts_exactly_the_three_declared_fields() -> None:
    key = KnowledgeEvidenceSelectionKey(knowledge_id="k1", version_label="v1", section_id="s1")
    assert set(key.model_dump().keys()) == {"knowledge_id", "version_label", "section_id"}


@pytest.mark.parametrize(
    "extra_field",
    ["content", "source", "source_system", "source_id", "source_locator", "title", "document_type", "lifecycle_status", "snippet", "heading", "relevance_score", "some_future_field"],
)
def test_selection_key_rejects_any_extra_authoritative_looking_field(extra_field: str) -> None:
    """A selector attempting to smuggle an extra field (content, source,
    or any other name -- including one that does not exist anywhere else
    in this domain) alongside the three real identity fields is REJECTED
    outright, not silently ignored -- `model_config = extra="forbid"`
    means this is enforced generically, without hardcoding this specific
    list of field names into a validator.
    """
    with pytest.raises(ValidationError):
        KnowledgeEvidenceSelectionKey(
            knowledge_id="k1", version_label="v1", section_id="s1", **{extra_field: "fabricated"}
        )


def test_selection_key_rejects_multiple_extra_fields_at_once() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvidenceSelectionKey(
            knowledge_id="k1", version_label="v1", section_id="s1", content="fabricated", source_system="fabricated"
        )


@pytest.mark.parametrize("field", ["knowledge_id", "version_label", "section_id"])
def test_selection_key_rejects_blank_identity_fields(field: str) -> None:
    fields = {"knowledge_id": "k1", "version_label": "v1", "section_id": "s1"}
    fields[field] = "   "
    with pytest.raises(ValidationError):
        KnowledgeEvidenceSelectionKey(**fields)


def test_selection_key_requires_all_three_fields() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvidenceSelectionKey(knowledge_id="k1", version_label="v1")  # type: ignore[call-arg]
