"""PDF page extraction (A5 Layer D).

Preserves page identity/order/text/provenance (A5 instruction section
22) -- one `KnowledgeArtifact` per page (`kind="pdf_page"`), never one
flattened document blob. Uses `pypdf`, a mature, bounded parser that has
no capability to execute embedded PDF actions/JavaScript at all (it
never interprets the interactive/action parts of the PDF object graph,
only the page/content-stream text layer) -- no explicit "disable
scripts" step is needed or possible because none ever runs.

OCR is deliberately NOT performed here (A5 instruction section 22: "do
not implement OCR as the default architecture") -- a page with no
extractable text layer (e.g. a scanned image) simply yields an empty
`extracted_text`, left for a future multimodal-interpretation pass
(Layer G) rather than an OCR engine.

Encrypted PDFs are detected and reported cleanly (never guessed).
"""
from __future__ import annotations

import io

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact
from backend.knowledge.ingestion.extraction import ExtractionBudget, deterministic_artifact_id, hash_bytes


class EncryptedPdfError(RuntimeError):
    """Raised when the PDF is password-protected/encrypted -- callers
    must report this cleanly (A5 instruction section 30) rather than
    attempt to guess a password or silently skip the document.
    """


def extract_pdf(data: bytes, *, container_artifact_id: str, depth: int, budget: ExtractionBudget) -> tuple[str, list[KnowledgeArtifact]]:
    """Returns (document summary text, one `KnowledgeArtifact` per page,
    each with `parent_artifact_id=container_artifact_id`,
    `kind="pdf_page"`, `depth=depth`, `locator_detail="page=<n>"`, 1-based).
    Raises `EncryptedPdfError` if the PDF is password-protected.
    """
    import pypdf  # noqa: PLC0415 -- imported lazily, matching this codebase's convention.

    reader = pypdf.PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise EncryptedPdfError("PDF is password-protected/encrypted -- never guessed, never force-opened.")

    page_artifacts: list[KnowledgeArtifact] = []
    for position, page in enumerate(reader.pages):
        page_number = position + 1
        try:
            page_text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover -- pypdf can raise on a malformed page; keep going.
            budget.record_skipped(f"page {page_number}", f"page text extraction failed: {exc}")
            continue

        extraction_status = ArtifactExtractionStatus.COMPLETE
        extraction_error = None
        stored_text = page_text if page_text.strip() else None
        if stored_text is None:
            extraction_status = ArtifactExtractionStatus.PARTIAL
            extraction_error = "no extractable text layer on this page (e.g. a scanned image) -- OCR is out of scope"

        page_hash = hash_bytes(page_text.encode("utf-8")) if page_text else None

        try:
            budget.reserve_bytes(len(page_text.encode("utf-8")))
            budget.reserve_artifact_slot()
        except Exception:
            budget.record_skipped(f"page {page_number}", "extraction limit exceeded")
            continue

        artifact_id = deterministic_artifact_id(
            parent_artifact_id=container_artifact_id, kind="pdf_page", position=position, content_hash=page_hash
        )
        page_artifacts.append(
            KnowledgeArtifact(
                artifact_id=artifact_id,
                parent_artifact_id=container_artifact_id,
                kind="pdf_page",
                media_type="text/plain",
                display_name=f"Page {page_number}",
                depth=depth,
                content_hash=page_hash,
                extracted_text=stored_text,
                derived=False,
                locator_detail=f"page={page_number}",
                extraction_status=extraction_status,
                extraction_error=extraction_error,
            )
        )
        budget.record_artifact(len(page_text.encode("utf-8")))

    summary = f"PDF document with {len(reader.pages)} page(s)."
    return summary, page_artifacts
