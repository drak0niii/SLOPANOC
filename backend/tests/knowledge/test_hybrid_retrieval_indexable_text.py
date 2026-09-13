"""Phase 6A.5: indexable-text construction
(`backend/knowledge/hybrid_retrieval/indexable_text.py`)."""
from __future__ import annotations

import hashlib

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.hybrid_retrieval.indexable_text import build_indexable_text, content_hash, resolve_is_derived


def _artifact(artifact_id: str, kind: str, derived: bool = False, display_name=None, locator_detail=None) -> KnowledgeArtifact:
    return KnowledgeArtifact(
        artifact_id=artifact_id, kind=kind, derived=derived, display_name=display_name,
        locator_detail=locator_detail, extraction_status=ArtifactExtractionStatus.COMPLETE,
        content_hash=hashlib.sha256(artifact_id.encode()).hexdigest(), depth=0,
    )


def _obj(artifacts=None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id="K", document_type=KnowledgeDocumentType.MOP, title="T",
        version=KnowledgeVersion(label="1.0"), lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="x", source_id="y"), artifacts=artifacts or [],
    )


def test_plain_section_uses_heading_and_content() -> None:
    section = KnowledgeSection(section_id="S1", knowledge_id="K", heading="Procedure", sequence=0, content="Do the thing.")
    text = build_indexable_text(_obj(), section)
    assert "Procedure" in text
    assert "Do the thing." in text


def test_section_without_heading_uses_content_only() -> None:
    section = KnowledgeSection(section_id="S1", knowledge_id="K", sequence=0, content="Just content.")
    text = build_indexable_text(_obj(), section)
    assert text == "Just content."


def test_table_artifact_prepends_sheet_range_identity() -> None:
    artifact = _artifact("A1", kind="xlsx_table", display_name="VSWR Sheet", locator_detail="sheet=VSWR;range=B7:F14")
    section = KnowledgeSection(section_id="S1", knowledge_id="K", sequence=0, content="header1 header2", artifact_id="A1")
    text = build_indexable_text(_obj(artifacts=[artifact]), section)
    assert "VSWR Sheet" in text
    assert "sheet=VSWR;range=B7:F14" in text
    assert "header1 header2" in text


def test_non_table_artifact_does_not_prepend_locator() -> None:
    artifact = _artifact("A1", kind="image", display_name="Figure 1", locator_detail="page=4")
    section = KnowledgeSection(section_id="S1", knowledge_id="K", sequence=0, content="derived description", artifact_id="A1")
    text = build_indexable_text(_obj(artifacts=[artifact]), section)
    assert "Figure 1" not in text
    assert text == "derived description"


def test_resolve_is_derived_true_for_derived_artifact() -> None:
    artifact = _artifact("A1", kind="image", derived=True)
    section = KnowledgeSection(section_id="S1", knowledge_id="K", sequence=0, content="c", artifact_id="A1")
    assert resolve_is_derived(_obj(artifacts=[artifact]), section) is True


def test_resolve_is_derived_false_for_source_artifact() -> None:
    artifact = _artifact("A1", kind="embedded_docx", derived=False)
    section = KnowledgeSection(section_id="S1", knowledge_id="K", sequence=0, content="c", artifact_id="A1")
    assert resolve_is_derived(_obj(artifacts=[artifact]), section) is False


def test_resolve_is_derived_false_for_root_text_no_artifact() -> None:
    section = KnowledgeSection(section_id="S1", knowledge_id="K", sequence=0, content="c")
    assert resolve_is_derived(_obj(), section) is False


def test_content_hash_deterministic() -> None:
    assert content_hash("same text") == content_hash("same text")


def test_content_hash_changes_when_text_changes() -> None:
    assert content_hash("text a") != content_hash("text b")


def test_derived_interpretation_never_masquerades_as_source() -> None:
    """§14/§16: the text itself is exactly what the section contains --
    a derived image's own description text is indexed as-is, but
    resolve_is_derived correctly flags it, never silently presented as
    equivalent to source content."""
    artifact = _artifact("A1", kind="image", derived=True)
    section = KnowledgeSection(section_id="S1", knowledge_id="K", sequence=0, content="A screenshot showing status GREEN.", artifact_id="A1")
    obj = _obj(artifacts=[artifact])
    text = build_indexable_text(obj, section)
    assert text == "A screenshot showing status GREEN."
    assert resolve_is_derived(obj, section) is True
