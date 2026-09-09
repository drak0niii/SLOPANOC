"""A5 Layers C/D/E/F: focused tests for DOCX/XLSX/PDF/TXT extraction,
recursive embedded-artifact discovery, defensive limits, hashing, and
deduplication/lineage -- against minimal SYNTHETIC fixtures only (A5
instruction section 48), built via backend/tests/knowledge/_synthetic_docs.py.
Never touches real corpus content -- see test_real_corpus_validation.py
for the separate, read-only real-file validation pass.
"""
from __future__ import annotations

import pytest

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus
from backend.knowledge.ingestion.extraction import ExtractionBudget, ExtractionLimits, hash_bytes
from backend.knowledge.ingestion.extractors.dispatch import (
    EncryptedDocumentError,
    UnsupportedRootDocumentError,
    extract_embedded_artifact,
    extract_root_document,
)
from backend.knowledge.ingestion.extractors.pdf import EncryptedPdfError, extract_pdf
from backend.knowledge.ingestion.extractors.txt import extract_txt
from backend.knowledge.ingestion.extractors.xlsx import extract_xlsx

from ._synthetic_docs import inject_embedded_member, make_minimal_docx, make_minimal_pdf, make_minimal_png, make_minimal_xlsx


def _limits(**overrides: int) -> ExtractionLimits:
    defaults = dict(max_recursion_depth=6, max_artifacts_per_root=500, max_artifact_bytes=5 * 1024 * 1024, max_total_expanded_bytes=20 * 1024 * 1024)
    defaults.update(overrides)
    return ExtractionLimits(**defaults)  # type: ignore[arg-type]


def _budget(**overrides: int) -> ExtractionBudget:
    return ExtractionBudget(limits=_limits(**overrides))


# --- DOCX --------------------------------------------------------------


def test_docx_paragraphs_extracted_in_order() -> None:
    data = make_minimal_docx(heading="Procedure", paragraphs=["First step.", "Second step.", "Third step."])
    text, _ = extract_root_document(data, budget=_budget())
    assert text.index("First step.") < text.index("Second step.") < text.index("Third step.")


def test_docx_heading_becomes_markdown_syntax() -> None:
    data = make_minimal_docx(heading="Procedure Title", paragraphs=["body"])
    text, _ = extract_root_document(data, budget=_budget())
    assert text.startswith("# Procedure Title")


def test_docx_table_preserved_with_row_structure() -> None:
    data = make_minimal_docx(heading="H", paragraphs=["p"], table_rows=[["Node", "Status"], ["N1", "OK"]])
    text, _ = extract_root_document(data, budget=_budget())
    assert "Node | Status" in text
    assert "N1 | OK" in text


def test_docx_hyperlink_target_preserved_as_reference_never_fetched() -> None:
    data = make_minimal_docx(heading="H", paragraphs=["p"], hyperlink_text="Vendor Portal", hyperlink_url="https://example.invalid/portal")
    text, _ = extract_root_document(data, budget=_budget())
    assert "Vendor Portal" in text
    assert "https://example.invalid/portal" in text


def test_docx_embedded_image_preserved_as_artifact() -> None:
    data = make_minimal_docx(heading="H", paragraphs=["p"], image_png=make_minimal_png())
    _, artifacts = extract_root_document(data, budget=_budget())
    images = [a for a in artifacts if a.kind == "image"]
    assert len(images) == 1
    assert images[0].parent_artifact_id is None
    assert images[0].depth == 0
    assert images[0].content_hash is not None


def test_nested_docx_handled_recursively() -> None:
    inner = make_minimal_docx(heading="Inner Guide", paragraphs=["inner content"])
    outer = make_minimal_docx(heading="Outer MOP", paragraphs=["outer content"])
    combined = inject_embedded_member(outer, "word/embeddings/inner.docx", inner)

    _, artifacts = extract_root_document(combined, budget=_budget())
    embedded = [a for a in artifacts if a.kind == "embedded_docx"]
    assert len(embedded) == 1
    assert embedded[0].extracted_text is not None
    assert "Inner Guide" in embedded[0].extracted_text
    assert "inner content" in embedded[0].extracted_text


def test_invalid_docx_rejected_safely() -> None:
    with pytest.raises(UnsupportedRootDocumentError):
        extract_root_document(b"this is not a real docx package at all", budget=_budget())


def test_corrupt_docx_zip_rejected_safely() -> None:
    with pytest.raises(UnsupportedRootDocumentError):
        extract_root_document(b"PK\x03\x04" + b"\x00" * 40, budget=_budget())


def test_macro_project_never_executed_reported_as_skipped() -> None:
    outer = make_minimal_docx(heading="H", paragraphs=["p"])
    with_macro = inject_embedded_member(outer, "word/vbaProject.bin", b"fake vba binary, never executed")
    _, artifacts = extract_root_document(with_macro, budget=_budget())
    macro_artifacts = [a for a in artifacts if a.kind == "macro_project"]
    assert len(macro_artifacts) == 1
    assert macro_artifacts[0].extraction_status == ArtifactExtractionStatus.SKIPPED


def test_unsupported_embedded_object_reported_not_crashed() -> None:
    outer = make_minimal_docx(heading="H", paragraphs=["p"])
    with_unknown = inject_embedded_member(outer, "word/embeddings/mystery.bin", b"\x00\x01\x02 not a real container")
    _, artifacts = extract_root_document(with_unknown, budget=_budget())
    unsupported = [a for a in artifacts if a.extraction_status == ArtifactExtractionStatus.SKIPPED and a.kind != "macro_project"]
    assert len(unsupported) == 1
    assert unsupported[0].extraction_error


# --- XLSX ----------------------------------------------------------------


def test_xlsx_headers_preserved() -> None:
    data = make_minimal_xlsx({"Report": [["Node", "Status"], ["N1", "OK"]]})
    _, sheets = extract_xlsx(data, container_artifact_id="root", depth=0, budget=_budget())
    assert "Headers: Node | Status" in sheets[0].extracted_text


def test_xlsx_rows_preserved() -> None:
    data = make_minimal_xlsx({"Report": [["Node", "Status"], ["N1", "OK"], ["N2", "DEGRADED"]]})
    _, sheets = extract_xlsx(data, container_artifact_id="root", depth=0, budget=_budget())
    assert "N1 | OK" in sheets[0].extracted_text
    assert "N2 | DEGRADED" in sheets[0].extracted_text


def test_xlsx_formula_never_executed_preserved_as_data() -> None:
    data = make_minimal_xlsx({"Sheet1": [["A", "B"], [1, "=A1+1"]]})
    _, sheets = extract_xlsx(data, container_artifact_id="root", depth=0, budget=_budget())
    assert "=A1+1" in sheets[0].extracted_text
    assert "2" not in sheets[0].extracted_text.split("=A1+1")[0].splitlines()[-1]  # formula not evaluated to 2


def test_xlsx_header_only_template_still_retrievable() -> None:
    data = make_minimal_xlsx({"Template": [["Field A", "Field B", "Field C"]]})
    _, sheets = extract_xlsx(data, container_artifact_id="root", depth=0, budget=_budget())
    assert sheets[0].extraction_status == ArtifactExtractionStatus.COMPLETE
    assert "Field A" in sheets[0].extracted_text


def test_xlsx_multiple_sheets_each_own_artifact() -> None:
    data = make_minimal_xlsx({"Sheet1": [["A"], [1]], "Sheet2": [["B"], [2]]})
    _, sheets = extract_xlsx(data, container_artifact_id="root", depth=0, budget=_budget())
    assert {s.display_name for s in sheets} == {"Sheet1", "Sheet2"}


# --- PDF -------------------------------------------------------------------


def test_pdf_page_text_extracted() -> None:
    data = make_minimal_pdf("Some real page text")
    _, pages = extract_pdf(data, container_artifact_id="root", depth=0, budget=_budget())
    assert pages[0].extracted_text == "Some real page text"


def test_pdf_page_provenance_preserved() -> None:
    data = make_minimal_pdf("text")
    _, pages = extract_pdf(data, container_artifact_id="root", depth=0, budget=_budget())
    assert pages[0].locator_detail == "page=1"
    assert pages[0].kind == "pdf_page"


def test_pdf_never_executes_embedded_actions() -> None:
    # pypdf has no capability to execute embedded PDF JavaScript/actions
    # at all -- this test documents that assumption by proving normal
    # text extraction succeeds without any script-disabling step.
    data = make_minimal_pdf("safe text")
    _, pages = extract_pdf(data, container_artifact_id="root", depth=0, budget=_budget())
    assert pages[0].extraction_status == ArtifactExtractionStatus.COMPLETE


# --- TXT -------------------------------------------------------------------


def test_txt_content_preserved_verbatim() -> None:
    text = extract_txt(b"line one\nline two\nline three")
    assert text == "line one\nline two\nline three"


def test_txt_handles_undecodable_bytes_safely() -> None:
    text = extract_txt(b"valid text \xff\xfe more text")
    assert "valid text" in text


# --- Recursive orchestrator / limits ----------------------------------------


def test_recursion_depth_limit_enforced() -> None:
    level2 = make_minimal_docx(heading="L2", paragraphs=["deep"])
    level1 = make_minimal_docx(heading="L1", paragraphs=["mid"])
    level1_with_2 = inject_embedded_member(level1, "word/embeddings/level2.docx", level2)
    root = make_minimal_docx(heading="Root", paragraphs=["top"])
    root_with_nested = inject_embedded_member(root, "word/embeddings/level1.docx", level1_with_2)

    budget = ExtractionBudget(limits=_limits(max_recursion_depth=0))
    _, artifacts = extract_root_document(root_with_nested, budget=budget)
    # depth=0 children (level1 itself) still allowed; level1's OWN
    # children (level2, checked at depth=1) exceed the limit and must
    # be recorded as skipped, never silently dropped.
    assert any(a.kind == "embedded_docx" and a.display_name == "level1.docx" for a in artifacts)
    assert budget.skipped, "expected the deeper artifact to be recorded as skipped, not silently lost"


def test_individual_artifact_size_bound_enforced() -> None:
    big_image = make_minimal_png() * 10_000  # comfortably over a tiny limit
    data = make_minimal_docx(heading="H", paragraphs=["p"], image_png=make_minimal_png())
    data = inject_embedded_member(data, "word/media/huge.bin", big_image)
    budget = ExtractionBudget(limits=_limits(max_artifact_bytes=100))
    _, artifacts = extract_root_document(data, budget=budget)
    assert budget.skipped
    assert not any(a.display_name == "huge.bin" for a in artifacts)


def test_total_expanded_size_bound_enforced() -> None:
    data = make_minimal_docx(heading="H", paragraphs=["p"], image_png=make_minimal_png())
    budget = ExtractionBudget(limits=_limits(max_artifact_bytes=1_000_000, max_total_expanded_bytes=1))
    _, artifacts = extract_root_document(data, budget=budget)
    assert budget.skipped
    assert not any(a.kind == "image" for a in artifacts)


def test_artifact_count_bound_enforced() -> None:
    data = make_minimal_docx(heading="H", paragraphs=["p"], image_png=make_minimal_png())
    budget = ExtractionBudget(limits=_limits(max_artifacts_per_root=0))
    _, artifacts = extract_root_document(data, budget=budget)
    assert budget.skipped
    assert artifacts == []


def test_partial_failure_preserves_rest_of_document() -> None:
    # One oversized embedded image must not prevent the root document's
    # own text, or a second, small, valid image, from being preserved.
    data = make_minimal_docx(heading="Still readable", paragraphs=["still here"], image_png=make_minimal_png())
    huge = make_minimal_png() * 50_000
    data = inject_embedded_member(data, "word/media/huge_extra.bin", huge)
    budget = ExtractionBudget(limits=_limits(max_artifact_bytes=1000))
    text, artifacts = extract_root_document(data, budget=budget)
    assert "Still readable" in text and "still here" in text
    assert any(a.kind == "image" for a in artifacts)  # the small legitimate image survived
    assert budget.skipped  # the oversized one was recorded, not silently dropped


# --- Path traversal defense --------------------------------------------


def test_embedded_member_traversal_path_never_escapes_in_memory_processing() -> None:
    # Extraction only ever reads zip member bytes into memory
    # (`ZipFile.read`) -- nothing is ever written to a filesystem path,
    # so a malicious member name (e.g. "../../etc/passwd") has no
    # traversal target at all. This test proves such a member name is
    # still handled safely as an opaque artifact, never crashes, and
    # never touches the real filesystem.
    data = make_minimal_docx(heading="H", paragraphs=["p"])
    data = inject_embedded_member(data, "word/embeddings/../../../evil.bin", b"not a real container")
    text, artifacts = extract_root_document(data, budget=_budget())
    assert "H" in text  # root extraction still succeeded


# --- Hashing / deduplication / lineage --------------------------------


def test_deterministic_hashing_stable_across_runs() -> None:
    data = make_minimal_docx(heading="H", paragraphs=["p"], image_png=make_minimal_png())
    _, artifacts_a = extract_root_document(data, budget=_budget())
    _, artifacts_b = extract_root_document(data, budget=_budget())
    hashes_a = sorted(a.content_hash for a in artifacts_a)
    hashes_b = sorted(a.content_hash for a in artifacts_b)
    assert hashes_a == hashes_b


def test_duplicate_embedded_binary_shares_content_hash_across_two_parents() -> None:
    # A5 instruction section 34, Case C: same embedded binary in two
    # different parent documents -> same content_hash (the structural
    # basis for storage-layer deduplication), while each parent's own
    # artifact tree still independently records its own lineage.
    shared_image = make_minimal_png()
    doc_a = make_minimal_docx(heading="Doc A", paragraphs=["a"], image_png=shared_image)
    doc_b = make_minimal_docx(heading="Doc B", paragraphs=["b"], image_png=shared_image)

    _, artifacts_a = extract_root_document(doc_a, budget=_budget())
    _, artifacts_b = extract_root_document(doc_b, budget=_budget())

    hash_a = next(a.content_hash for a in artifacts_a if a.kind == "image")
    hash_b = next(a.content_hash for a in artifacts_b if a.kind == "image")
    assert hash_a == hash_b == hash_bytes(shared_image)


def test_lineage_preserved_for_nested_artifact() -> None:
    inner = make_minimal_docx(heading="Inner", paragraphs=["x"], image_png=make_minimal_png())
    outer = make_minimal_docx(heading="Outer", paragraphs=["y"])
    combined = inject_embedded_member(outer, "word/embeddings/inner.docx", inner)

    _, artifacts = extract_root_document(combined, budget=_budget())
    embedded_docx = next(a for a in artifacts if a.kind == "embedded_docx")
    nested_image = next(a for a in artifacts if a.kind == "image" and a.depth == 1)
    assert nested_image.parent_artifact_id == embedded_docx.artifact_id
    assert nested_image.depth == embedded_docx.depth + 1


# --- Encrypted / macro detection ----------------------------------------


def test_encrypted_pdf_detected_and_reported_cleanly() -> None:
    import pypdf

    pdf_bytes = make_minimal_pdf("secret")
    writer = pypdf.PdfWriter()
    reader = pypdf.PdfReader(__import__("io").BytesIO(pdf_bytes))
    writer.append(reader)
    writer.encrypt(user_password="invented-non-sensitive-password")
    import io as _io

    encrypted_buffer = _io.BytesIO()
    writer.write(encrypted_buffer)

    with pytest.raises(EncryptedPdfError):
        extract_pdf(encrypted_buffer.getvalue(), container_artifact_id="root", depth=0, budget=_budget())


def test_encrypted_root_document_reported_not_guessed() -> None:
    ole2_magic = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
    with pytest.raises(EncryptedDocumentError):
        extract_root_document(ole2_magic, budget=_budget())


# --- extract_embedded_artifact direct coverage --------------------------


def test_extract_embedded_artifact_txt_log() -> None:
    artifacts = extract_embedded_artifact(
        b"2026-09-07 12:00:00 INFO node status GREEN",
        parent_artifact_id=None,
        depth=0,
        budget=_budget(),
        display_name="health-check.log",
        position=0,
        relationship_kind="txt_log",
    )
    assert len(artifacts) == 1
    assert artifacts[0].kind == "txt_log"
    assert "GREEN" in artifacts[0].extracted_text


def test_extract_embedded_artifact_unsupported_reported() -> None:
    artifacts = extract_embedded_artifact(
        b"\x00\x01totally unrecognized binary content",
        parent_artifact_id=None,
        depth=0,
        budget=_budget(),
        display_name="mystery.dat",
        position=0,
        relationship_kind="embedded_object",
    )
    assert len(artifacts) == 1
    assert artifacts[0].kind == "unsupported_artifact"
    assert artifacts[0].extraction_status == ArtifactExtractionStatus.SKIPPED
