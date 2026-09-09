"""A5 Layer H: focused tests for
backend/knowledge/processing/compound.py -- reusing the existing,
unmodified `HeadingStructureProcessor` for both root text and each
artifact's own extracted text, with globally deterministic
section_key/sequence and correct artifact_id tagging.
"""
from __future__ import annotations

from backend.knowledge.domain.artifacts import KnowledgeArtifact
from backend.knowledge.domain.models import KnowledgeSource
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.processing.compound import process_compound_document


def _source() -> KnowledgeSource:
    return KnowledgeSource(source_system="local_file", source_id="doc-1")


def test_plain_text_document_unaffected_by_compound_bridge() -> None:
    # No artifacts at all -- must be byte-identical in behavior to
    # calling HeadingStructureProcessor.process() directly (pre-A5 shape).
    document = IngestedKnowledgeDocument(source=_source(), title="Doc", content="# Heading\nBody text.")
    structured = process_compound_document(document)
    assert len(structured.sections) == 1
    assert structured.sections[0].artifact_id is None
    assert structured.sections[0].heading == "Heading"


def test_artifact_with_no_extracted_text_contributes_no_section() -> None:
    artifact = KnowledgeArtifact(artifact_id="a1", kind="image", depth=0, extracted_text=None)
    document = IngestedKnowledgeDocument(source=_source(), title="Doc", content="root text", artifacts=[artifact])
    structured = process_compound_document(document)
    assert len(structured.sections) == 1
    assert structured.sections[0].artifact_id is None


def test_artifact_with_extracted_text_contributes_tagged_section() -> None:
    artifact = KnowledgeArtifact(artifact_id="a1", kind="xlsx_sheet", depth=0, extracted_text="Headers: Node | Status\nN1 | OK")
    document = IngestedKnowledgeDocument(source=_source(), title="Doc", content="root text", artifacts=[artifact])
    structured = process_compound_document(document)
    root_sections = [s for s in structured.sections if s.artifact_id is None]
    artifact_sections = [s for s in structured.sections if s.artifact_id == "a1"]
    assert len(root_sections) == 1 and "root text" in root_sections[0].content
    assert len(artifact_sections) == 1 and "N1 | OK" in artifact_sections[0].content


def test_multiple_artifacts_each_own_sections_globally_unique() -> None:
    artifact_a = KnowledgeArtifact(artifact_id="a1", kind="xlsx_sheet", depth=0, extracted_text="sheet one text")
    artifact_b = KnowledgeArtifact(artifact_id="a2", kind="embedded_docx", depth=0, extracted_text="# Sub Heading\nsub body")
    document = IngestedKnowledgeDocument(source=_source(), title="Doc", content="root", artifacts=[artifact_a, artifact_b])
    structured = process_compound_document(document)

    section_keys = [s.section_key for s in structured.sections]
    sequences = [s.sequence for s in structured.sections]
    assert len(section_keys) == len(set(section_keys))  # globally unique
    assert sequences == sorted(sequences)  # deterministic, document order

    a1_sections = [s for s in structured.sections if s.artifact_id == "a1"]
    a2_sections = [s for s in structured.sections if s.artifact_id == "a2"]
    assert a1_sections and "sheet one text" in a1_sections[0].content
    assert a2_sections and a2_sections[0].heading == "Sub Heading"


def test_source_locator_stays_lines_shape_scoped_to_own_text() -> None:
    artifact = KnowledgeArtifact(artifact_id="a1", kind="txt_log", depth=0, extracted_text="line one\nline two")
    document = IngestedKnowledgeDocument(source=_source(), title="Doc", content="root", artifacts=[artifact])
    structured = process_compound_document(document)
    artifact_section = next(s for s in structured.sections if s.artifact_id == "a1")
    assert artifact_section.source_locator == "lines:1-2"  # scoped to the artifact's own 2-line text, not the root document
