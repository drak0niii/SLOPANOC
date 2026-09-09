"""Focused tests for backend/knowledge/domain/artifacts.py (A5 Layer A:
generic compound-artifact representation) and its wiring into
`KnowledgeSection`/`KnowledgeObject`/`KnowledgeEvidenceReference`.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.artifacts import (
    ArtifactExtractionStatus,
    KnowledgeArtifact,
    validate_artifact_lineage,
)
from backend.knowledge.domain.contracts import KnowledgeEvidenceReference
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion

_HASH = "a" * 64


def _artifact(**overrides: object) -> KnowledgeArtifact:
    defaults: dict[object, object] = dict(artifact_id="art-1", kind="embedded_docx", depth=0)
    defaults.update(overrides)
    return KnowledgeArtifact(**defaults)  # type: ignore[arg-type]


def _object(sections: list[KnowledgeSection] | None = None, artifacts: list[KnowledgeArtifact] | None = None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id="kn-1",
        document_type=KnowledgeDocumentType.MOP,
        title="Test MOP",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=KnowledgeSource(source_system="local_file", source_id="doc-1"),
        sections=sections or [],
        artifacts=artifacts or [],
    )


# --- KnowledgeArtifact structural validation --------------------------------


def test_minimal_artifact_accepted() -> None:
    artifact = _artifact()
    assert artifact.artifact_id == "art-1"
    assert artifact.derived is False
    assert artifact.extraction_status == ArtifactExtractionStatus.COMPLETE


@pytest.mark.parametrize("field", ["artifact_id", "kind"])
def test_required_string_fields_reject_blank(field: str) -> None:
    with pytest.raises(ValidationError):
        _artifact(**{field: "   "})


def test_negative_depth_rejected() -> None:
    with pytest.raises(ValidationError):
        _artifact(depth=-1)


def test_self_parent_rejected() -> None:
    with pytest.raises(ValidationError):
        _artifact(artifact_id="art-1", parent_artifact_id="art-1")


@pytest.mark.parametrize("bad_hash", ["", "abc", "g" * 64, "A" * 64, "a" * 63])
def test_invalid_content_hash_rejected(bad_hash: str) -> None:
    with pytest.raises(ValidationError):
        _artifact(content_hash=bad_hash)


def test_valid_content_hash_accepted() -> None:
    assert _artifact(content_hash=_HASH).content_hash == _HASH


def test_non_complete_status_requires_error() -> None:
    with pytest.raises(ValidationError):
        _artifact(extraction_status=ArtifactExtractionStatus.FAILED)


def test_complete_status_rejects_error() -> None:
    with pytest.raises(ValidationError):
        _artifact(extraction_status=ArtifactExtractionStatus.COMPLETE, extraction_error="should not be set")


def test_failed_status_with_error_accepted() -> None:
    artifact = _artifact(extraction_status=ArtifactExtractionStatus.FAILED, extraction_error="corrupt package")
    assert artifact.extraction_status == ArtifactExtractionStatus.FAILED


def test_derived_flag_marks_model_interpretation() -> None:
    artifact = _artifact(kind="image", extracted_text="A dashboard showing status GREEN.", derived=True)
    assert artifact.derived is True
    assert artifact.extracted_text is not None


def test_blank_extracted_text_rejected_if_explicitly_set() -> None:
    with pytest.raises(ValidationError):
        _artifact(extracted_text="   ")


# --- validate_artifact_lineage ----------------------------------------------


def test_lineage_accepts_valid_tree() -> None:
    root_child = _artifact(artifact_id="a1", depth=0)
    nested = _artifact(artifact_id="a2", parent_artifact_id="a1", depth=1)
    validate_artifact_lineage([root_child, nested])  # must not raise


def test_lineage_rejects_duplicate_artifact_id() -> None:
    with pytest.raises(ValueError, match="duplicate artifact_id"):
        validate_artifact_lineage([_artifact(artifact_id="a1"), _artifact(artifact_id="a1")])


def test_lineage_rejects_dangling_parent() -> None:
    with pytest.raises(ValueError, match="not present"):
        validate_artifact_lineage([_artifact(artifact_id="a1", parent_artifact_id="missing")])


# --- KnowledgeObject wiring ---------------------------------------------------


def test_knowledge_object_accepts_empty_artifacts_unchanged() -> None:
    obj = _object()
    assert obj.artifacts == []


def test_knowledge_object_accepts_section_referencing_valid_artifact() -> None:
    artifact = _artifact(artifact_id="a1")
    section = KnowledgeSection(
        section_id="s1", knowledge_id="kn-1", sequence=0, content="Sheet1 headers: Node, Status.", artifact_id="a1"
    )
    obj = _object(sections=[section], artifacts=[artifact])
    assert obj.sections[0].artifact_id == "a1"


def test_knowledge_object_rejects_section_with_unknown_artifact_id() -> None:
    section = KnowledgeSection(section_id="s1", knowledge_id="kn-1", sequence=0, content="text", artifact_id="missing")
    with pytest.raises(ValidationError, match="not present"):
        _object(sections=[section])


def test_knowledge_object_rejects_broken_artifact_lineage() -> None:
    artifact = _artifact(artifact_id="a1", parent_artifact_id="missing-parent")
    with pytest.raises(ValidationError, match="not present"):
        _object(artifacts=[artifact])


def test_knowledge_object_section_without_artifact_id_unaffected() -> None:
    # Pre-A5 behavior: a plain-text section with no artifact_id at all
    # must remain completely unaffected by the new field's existence.
    section = KnowledgeSection(section_id="s1", knowledge_id="kn-1", sequence=0, content="plain text")
    obj = _object(sections=[section])
    assert obj.sections[0].artifact_id is None


# --- KnowledgeEvidenceReference wiring --------------------------------------


def test_evidence_reference_artifact_id_optional() -> None:
    reference = KnowledgeEvidenceReference(knowledge_id="kn-1", version_label="1.0", source_system="local_file", source_id="doc-1")
    assert reference.artifact_id is None


def test_evidence_reference_artifact_id_rejects_blank_if_set() -> None:
    with pytest.raises(ValidationError):
        KnowledgeEvidenceReference(
            knowledge_id="kn-1", version_label="1.0", source_system="local_file", source_id="doc-1", artifact_id="   "
        )


def test_evidence_reference_accepts_artifact_id() -> None:
    reference = KnowledgeEvidenceReference(
        knowledge_id="kn-1", version_label="1.0", source_system="local_file", source_id="doc-1", artifact_id="a1"
    )
    assert reference.artifact_id == "a1"
