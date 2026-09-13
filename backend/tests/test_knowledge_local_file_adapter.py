"""A5: focused tests for
backend/knowledge_ingestion/local_file_adapter.py -- the ADMIN/DEV
local-file ingestion entry point. Synthetic fixtures only (A5
instruction section 48); lives at this top level (not under
backend/tests/knowledge/) because it tests a concrete,
cloud-SDK-orchestrating module, mirroring the same convention already
used for backend/tools/knowledge/ and backend/knowledge_ingestion/
artifact_storage.py.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pytest

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact
from backend.knowledge.ingestion.image_interpretation import ImageInterpretationResult
from backend.knowledge_ingestion.artifact_storage import KnowledgeArtifactStorage
from backend.knowledge_ingestion.local_file_adapter import (
    ingest_and_structure_local_file,
    ingest_and_structure_local_files,
    ingest_local_file,
    ingest_local_files,
)

from backend.tests.knowledge._synthetic_docs import make_minimal_docx, make_minimal_pdf, make_minimal_png, make_minimal_xlsx


class _FakeGcsBlob:
    def __init__(self, store: dict[str, bytes], name: str) -> None:
        self._store = store
        self._name = name

    def exists(self) -> bool:
        return self._name in self._store

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self._store[self._name] = data


class _FakeBucket:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def blob(self, name: str) -> _FakeGcsBlob:
        return _FakeGcsBlob(self._store, name)


class _FakeGcsClient:
    def __init__(self, store: dict[str, bytes]) -> None:
        self._store = store

    def bucket(self, name: str) -> _FakeBucket:
        return _FakeBucket(self._store)


def _fake_storage() -> KnowledgeArtifactStorage:
    store: dict[str, bytes] = {}
    storage = KnowledgeArtifactStorage(bucket_name="fake-bucket")
    storage._client = _FakeGcsClient(store)  # bypass real google.cloud.storage.Client() construction
    return storage


class _FakeInterpreter:
    async def interpret(self, image_bytes: bytes, media_type: str, *, context: Optional[str] = None) -> ImageInterpretationResult:
        return ImageInterpretationResult(succeeded=True, description="A synthetic panel.", model_identifier="fake-model")


@pytest.mark.asyncio
async def test_ingest_missing_file_reported_not_raised(tmp_path: Path) -> None:
    result = await ingest_local_file(tmp_path / "does-not-exist.docx")
    assert result.document is None
    assert result.report.succeeded is False
    assert "could not read file" in result.report.error


@pytest.mark.asyncio
async def test_ingest_invalid_docx_reported_not_raised(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.docx"
    bad_file.write_bytes(b"not a real docx at all")
    result = await ingest_local_file(bad_file)
    assert result.document is None
    assert result.report.succeeded is False


@pytest.mark.asyncio
async def test_ingest_plain_docx_produces_document_and_report(tmp_path: Path) -> None:
    file_path = tmp_path / "procedure.docx"
    file_path.write_bytes(make_minimal_docx(heading="Synthetic Procedure", paragraphs=["Step one.", "Step two."]))

    result = await ingest_local_file(file_path)
    assert result.document is not None
    assert result.report.succeeded is True
    assert "Step one." in result.document.content
    assert result.document.source.source_system == "local_file"
    assert result.document.source.source_id == "procedure.docx"


@pytest.mark.asyncio
async def test_ingest_reports_artifact_counts_by_kind(tmp_path: Path) -> None:
    file_path = tmp_path / "with_image.docx"
    file_path.write_bytes(make_minimal_docx(heading="H", paragraphs=["p"], image_png=make_minimal_png()))

    result = await ingest_local_file(file_path)
    assert result.report.counts_by_kind.get("image") == 1
    assert result.report.artifact_count == 1


@pytest.mark.asyncio
async def test_ingest_never_mutates_source_file(tmp_path: Path) -> None:
    file_path = tmp_path / "readonly.docx"
    original_bytes = make_minimal_docx(heading="H", paragraphs=["p"])
    file_path.write_bytes(original_bytes)

    await ingest_local_file(file_path)
    assert file_path.read_bytes() == original_bytes


@pytest.mark.asyncio
async def test_ingest_uploads_artifacts_when_storage_configured(tmp_path: Path) -> None:
    file_path = tmp_path / "with_image.docx"
    file_path.write_bytes(make_minimal_docx(heading="H", paragraphs=["p"], image_png=make_minimal_png()))

    storage = _fake_storage()
    result = await ingest_local_file(file_path, storage=storage)
    assert result.report.storage_configured is True
    assert result.report.artifacts_uploaded == 1
    image_artifact = next(a for a in result.document.artifacts if a.kind == "image")
    assert image_artifact.storage_ref is not None
    assert image_artifact.storage_ref.startswith("gs://fake-bucket/knowledge-artifacts/")


@pytest.mark.asyncio
async def test_ingest_deduplicates_identical_artifact_across_two_files(tmp_path: Path) -> None:
    shared_image = make_minimal_png()
    file_a = tmp_path / "doc_a.docx"
    file_a.write_bytes(make_minimal_docx(heading="A", paragraphs=["a"], image_png=shared_image))
    file_b = tmp_path / "doc_b.docx"
    file_b.write_bytes(make_minimal_docx(heading="B", paragraphs=["b"], image_png=shared_image))

    storage = _fake_storage()
    results = await ingest_local_files([file_a, file_b], storage=storage)

    assert results[0].report.artifacts_uploaded == 1
    assert results[0].report.artifacts_deduplicated == 0
    assert results[1].report.artifacts_uploaded == 0
    assert results[1].report.artifacts_deduplicated == 1  # same content, already stored by file_a


@pytest.mark.asyncio
async def test_ingest_without_storage_configured_skips_upload_cleanly(tmp_path: Path) -> None:
    file_path = tmp_path / "with_image.docx"
    file_path.write_bytes(make_minimal_docx(heading="H", paragraphs=["p"], image_png=make_minimal_png()))

    unconfigured_storage = KnowledgeArtifactStorage(bucket_name=None)
    result = await ingest_local_file(file_path, storage=unconfigured_storage)
    assert result.report.succeeded is True
    assert result.report.storage_configured is False
    assert result.report.artifacts_uploaded == 0
    image_artifact = next(a for a in result.document.artifacts if a.kind == "image")
    assert image_artifact.storage_ref is None


@pytest.mark.asyncio
async def test_ingest_interprets_images_when_interpreter_supplied(tmp_path: Path) -> None:
    file_path = tmp_path / "with_image.docx"
    file_path.write_bytes(make_minimal_docx(heading="H", paragraphs=["p"], image_png=make_minimal_png()))

    result = await ingest_local_file(file_path, interpreter=_FakeInterpreter())
    assert result.report.images_interpreted == 1
    image_artifact = next(a for a in result.document.artifacts if a.kind == "image")
    assert image_artifact.extracted_text == "A synthetic panel."
    assert image_artifact.derived is True


@pytest.mark.asyncio
async def test_ingest_batch_one_failure_does_not_abort_others(tmp_path: Path) -> None:
    good_file = tmp_path / "good.docx"
    good_file.write_bytes(make_minimal_docx(heading="H", paragraphs=["p"]))
    bad_file = tmp_path / "bad.docx"
    bad_file.write_bytes(b"not a docx")

    results = await ingest_local_files([bad_file, good_file])
    assert results[0].report.succeeded is False
    assert results[1].report.succeeded is True


# --- ingest_and_structure_local_file(s) -- 6A.3 Layer H integration --------


@pytest.mark.asyncio
async def test_structure_extraction_failure_yields_no_structured_document(tmp_path: Path) -> None:
    bad_file = tmp_path / "bad.docx"
    bad_file.write_bytes(b"not a docx")
    outcome = await ingest_and_structure_local_file(bad_file)
    assert outcome.result.report.succeeded is False
    assert outcome.structured is None


@pytest.mark.asyncio
async def test_structure_plain_docx_root_text_becomes_a_section(tmp_path: Path) -> None:
    file_path = tmp_path / "procedure.docx"
    file_path.write_bytes(make_minimal_docx(heading="Synthetic Procedure", paragraphs=["Step one.", "Step two."]))

    outcome = await ingest_and_structure_local_file(file_path)
    assert outcome.structured is not None
    root_sections = [s for s in outcome.structured.sections if s.artifact_id is None]
    assert root_sections
    assert any("Step one." in s.content for s in root_sections)


@pytest.mark.asyncio
async def test_structure_pdf_root_document_page_content_becomes_retrievable_sections(tmp_path: Path) -> None:
    """THE gap this milestone closes, proven directly: a PDF root
    document's own `IngestedKnowledgeDocument.content` is only a short
    structural summary ("PDF document with 1 page(s)."), never the real
    page text -- without Layer H wired in, the real text
    ("A specific real sentence only on the PDF page.") would never reach
    any `StructuredKnowledgeSection`, only an inert `KnowledgeArtifact`.
    """
    file_path = tmp_path / "spec.pdf"
    file_path.write_bytes(make_minimal_pdf("A specific real sentence only on the PDF page."))

    outcome = await ingest_and_structure_local_file(file_path)
    assert outcome.structured is not None
    # The root section is indeed just the structural summary -- confirms
    # the gap was real, not merely theoretical.
    root_sections = [s for s in outcome.structured.sections if s.artifact_id is None]
    assert root_sections
    assert "page(s)" in root_sections[0].content
    assert "A specific real sentence" not in root_sections[0].content
    # But the real page text IS now reachable as its own, separately
    # tagged, artifact-linked section.
    artifact_sections = [s for s in outcome.structured.sections if s.artifact_id is not None]
    assert artifact_sections
    assert any("A specific real sentence only on the PDF page." in s.content for s in artifact_sections)


@pytest.mark.asyncio
async def test_structure_xlsx_root_document_sheet_content_becomes_retrievable_sections(tmp_path: Path) -> None:
    file_path = tmp_path / "workbook.xlsx"
    file_path.write_bytes(make_minimal_xlsx({"VSWR": [["Technology", "Warning"], ["LTE", 1.5]]}))

    outcome = await ingest_and_structure_local_file(file_path)
    assert outcome.structured is not None
    artifact_sections = [s for s in outcome.structured.sections if s.artifact_id is not None]
    assert artifact_sections
    assert any("LTE | 1.5" in s.content for s in artifact_sections)


@pytest.mark.asyncio
async def test_structure_section_artifact_ids_resolve_within_same_document(tmp_path: Path) -> None:
    """Every StructuredKnowledgeSection.artifact_id, when set, must
    resolve to a real artifact_id in the same document's own artifacts
    -- StructuredKnowledgeDocument's own model_validator already enforces
    this; this test proves it holds for a REAL extracted+structured
    document, not only for hand-built fixtures.
    """
    file_path = tmp_path / "workbook.xlsx"
    file_path.write_bytes(make_minimal_xlsx({"Sheet1": [["A", "B"], [1, 2]], "Sheet2": [["C"], [3]]}))

    outcome = await ingest_and_structure_local_file(file_path)
    assert outcome.structured is not None
    artifact_ids = {a.artifact_id for a in outcome.structured.source_document.artifacts}
    for section in outcome.structured.sections:
        if section.artifact_id is not None:
            assert section.artifact_id in artifact_ids


@pytest.mark.asyncio
async def test_structure_batch_one_failure_does_not_abort_others(tmp_path: Path) -> None:
    good_file = tmp_path / "good.docx"
    good_file.write_bytes(make_minimal_docx(heading="H", paragraphs=["p"]))
    bad_file = tmp_path / "bad.docx"
    bad_file.write_bytes(b"not a docx")

    outcomes = await ingest_and_structure_local_files([bad_file, good_file])
    assert outcomes[0].structured is None
    assert outcomes[1].structured is not None
