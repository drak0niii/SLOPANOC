"""Phase 5.1C: the generic IngestedKnowledgeDocument contract -- validity,
structural validation, optional hints, and reuse of domain/'s own value
objects (never a forked/duplicate equivalent).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.enums import KnowledgeDocumentType
from backend.knowledge.domain.models import Applicability, KnowledgeMetadata, KnowledgeSource, KnowledgeVersion
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument


def _document(**overrides: object) -> IngestedKnowledgeDocument:
    fields: dict[str, object] = {
        "source": KnowledgeSource(source_system="source_a", source_id="doc-1"),
        "title": "Upgrade MOP",
        "content": "Step 1: verify process status.",
    }
    fields.update(overrides)
    return IngestedKnowledgeDocument(**fields)


# --- valid document ------------------------------------------------------


def test_arbitrary_source_system_accepted() -> None:
    doc = _document(source=KnowledgeSource(source_system="enterprise_repo_x", source_id="123"))
    assert doc.source.source_system == "enterprise_repo_x"


def test_arbitrary_source_id_accepted() -> None:
    doc = _document(source=KnowledgeSource(source_system="source_a", source_id="anything-goes-here"))
    assert doc.source.source_id == "anything-goes-here"


def test_valid_title_and_content_accepted() -> None:
    doc = _document(title="Rollback SOP", content="Rollback procedure text.")
    assert doc.title == "Rollback SOP"
    assert doc.content == "Rollback procedure text."


def test_metadata_carried_through() -> None:
    metadata = KnowledgeMetadata(owner="RAN Engineering", tags=["upgrade"])
    doc = _document(metadata=metadata)
    assert doc.metadata == metadata


def test_applicability_carried_through() -> None:
    applicability = Applicability(dimensions={"vendor": ["Ericsson"]})
    doc = _document(applicability=applicability)
    assert doc.applicability == applicability


def test_defaults_when_metadata_and_applicability_omitted() -> None:
    doc = _document()
    assert doc.metadata == KnowledgeMetadata()
    assert doc.applicability == Applicability()


# --- validation ------------------------------------------------------------


def test_blank_title_rejected() -> None:
    with pytest.raises(ValidationError):
        _document(title="")
    with pytest.raises(ValidationError):
        _document(title="   ")


def test_blank_content_rejected() -> None:
    with pytest.raises(ValidationError):
        _document(content="")


def test_blank_knowledge_id_hint_rejected_when_present() -> None:
    with pytest.raises(ValidationError):
        _document(knowledge_id_hint="")
    with pytest.raises(ValidationError):
        _document(knowledge_id_hint="   ")


def test_blank_source_revision_rejected_when_present() -> None:
    with pytest.raises(ValidationError):
        _document(source_revision="")


def test_blank_media_type_rejected_when_present() -> None:
    with pytest.raises(ValidationError):
        _document(media_type="   ")


def test_source_itself_must_be_valid() -> None:
    with pytest.raises(ValidationError):
        _document(source=KnowledgeSource(source_system="", source_id="doc-1"))


# --- optional hints --------------------------------------------------------


def test_document_type_hint_may_be_absent() -> None:
    doc = _document()
    assert doc.document_type_hint is None


@pytest.mark.parametrize(
    "document_type",
    [
        KnowledgeDocumentType.MOP,
        KnowledgeDocumentType.SOP,
        KnowledgeDocumentType.RCA,
        KnowledgeDocumentType.KB_ARTICLE,
        KnowledgeDocumentType.OTHER,
    ],
)
def test_arbitrary_supported_document_type_hint_may_be_supplied(document_type: KnowledgeDocumentType) -> None:
    doc = _document(document_type_hint=document_type)
    assert doc.document_type_hint is document_type


def test_version_hint_may_be_absent() -> None:
    doc = _document()
    assert doc.version_hint is None


@pytest.mark.parametrize("label", ["1.0", "Rev-A", "2026-08"])
def test_arbitrary_valid_version_hint_label_preserved(label: str) -> None:
    doc = _document(version_hint=KnowledgeVersion(label=label))
    assert doc.version_hint is not None
    assert doc.version_hint.label == label


def test_invalid_version_hint_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeVersion(label="")  # sanity: the reused model still validates
    with pytest.raises(ValidationError):
        _document(version_hint={"label": ""})


def test_knowledge_id_hint_may_be_absent() -> None:
    doc = _document()
    assert doc.knowledge_id_hint is None


def test_knowledge_id_hint_when_supplied_is_preserved_as_given() -> None:
    doc = _document(knowledge_id_hint="source-native-doc-42")
    assert doc.knowledge_id_hint == "source-native-doc-42"


def test_observed_at_and_media_type_and_source_revision_are_optional() -> None:
    doc = _document()
    assert doc.observed_at is None
    assert doc.media_type is None
    assert doc.source_revision is None
