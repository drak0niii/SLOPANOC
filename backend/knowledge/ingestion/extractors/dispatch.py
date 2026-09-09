"""Recursive compound-document orchestrator (A5 Layer E).

Two entry points:

- `extract_root_document`: the top-level document itself is NEVER
  represented as a `KnowledgeArtifact` (its own text becomes the
  returned root text, exactly like `IngestedKnowledgeDocument.content`
  always has); this returns that root text plus every artifact found
  embedded in it, flattened, at `depth=0` for direct children.
- `extract_embedded_artifact`: used both by the root call and
  recursively by `extract_docx` for anything found NESTED inside a
  compound document (an image, a spreadsheet, another document, an
  unsupported object) -- sniffs the embedded blob's real container
  structure (never trusts the internal package member name's
  extension), builds the `KnowledgeArtifact` node representing it, and
  -- only for a further-recursable kind (currently: DOCX) -- recurses
  into its own embedded objects. Returns a flat list: the node itself
  first, then every descendant.

Every recursive call shares the SAME `ExtractionBudget` instance (A5
instruction section 28) -- recursion depth, per-root artifact count,
individual artifact size, and total expanded size are all enforced
across the WHOLE root document's extraction, never reset per branch.
Hitting a limit converts to a recorded, skipped artifact
(`ExtractionBudget.record_skipped`) rather than raising out of the
whole extraction (A5 instruction section 28/46: partial failure must
never silently claim full extraction, but also must never abort
everything else that succeeded).
"""
from __future__ import annotations

from typing import Optional

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact
from backend.knowledge.ingestion.extraction import (
    ExtractionBudget,
    ExtractionLimitExceededError,
    deterministic_artifact_id,
    hash_bytes,
    sniff_media_type,
)
from backend.knowledge.ingestion.extractors.pdf import EncryptedPdfError, extract_pdf
from backend.knowledge.ingestion.extractors.txt import extract_txt
from backend.knowledge.ingestion.extractors.xlsx import extract_xlsx

_OLE2_COMPOUND_FILE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

_IMAGE_EXTENSION_MEDIA_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "bmp": "image/bmp",
    "emf": "image/x-emf",
    "wmf": "image/x-wmf",
    "tiff": "image/tiff",
    "tif": "image/tiff",
}


def _extension(display_name: Optional[str]) -> str:
    if not display_name or "." not in display_name:
        return ""
    return display_name.rsplit(".", 1)[-1].lower()


def extract_root_document(data: bytes, *, budget: ExtractionBudget, display_name: Optional[str] = None) -> tuple[str, list[KnowledgeArtifact]]:
    """Entry point for a ROOT document (never itself an artifact).
    Returns (root document's own readable text, flattened list of every
    artifact discovered inside it -- direct children at depth=0).
    Raises `UnsupportedRootDocumentError` if the root's own container
    format is not recognized, `EncryptedDocumentError` if it is
    password-protected -- both distinct, clean, non-guessed failures
    (A5 instruction section 30/31).
    """
    kind = sniff_media_type(data)
    if kind == "docx":
        from backend.knowledge.ingestion.extractors.docx import extract_docx  # noqa: PLC0415 -- avoids a docx<->dispatch import cycle.

        return extract_docx(data, container_artifact_id=None, depth=0, budget=budget)
    if kind == "xlsx":
        return extract_xlsx(data, container_artifact_id="root", depth=0, budget=budget)
    if kind == "pdf":
        try:
            return extract_pdf(data, container_artifact_id="root", depth=0, budget=budget)
        except EncryptedPdfError as exc:
            raise EncryptedDocumentError(str(exc)) from exc

    if data.startswith(_OLE2_COMPOUND_FILE_MAGIC):
        raise EncryptedDocumentError("root document appears to be a legacy/encrypted Office compound file -- never guessed, never force-opened.")

    raise UnsupportedRootDocumentError("root document format not recognized (not DOCX/XLSX/PDF by actual container structure).")


def extract_embedded_artifact(
    data: bytes,
    *,
    parent_artifact_id: Optional[str],
    depth: int,
    budget: ExtractionBudget,
    display_name: Optional[str],
    position: int,
    relationship_kind: str,
) -> list[KnowledgeArtifact]:
    """Builds the `KnowledgeArtifact` node for one embedded object found
    inside a parent document/artifact, and -- for a further-recursable
    kind -- recurses into it. Returns a flat list: [this artifact,
    *descendants]. Never raises: an unrecognized/encrypted/oversized
    embedded object becomes a single SKIPPED/FAILED artifact node
    (never aborts the parent's own processing), per A5 instruction
    section 46.
    """
    content_hash = hash_bytes(data)

    try:
        budget.check_depth(depth)
        budget.reserve_bytes(len(data))
        budget.reserve_artifact_slot()
    except ExtractionLimitExceededError as exc:
        budget.record_skipped(display_name or relationship_kind, str(exc))
        return []

    if relationship_kind == "image":
        kind = "image"
        media_type = _IMAGE_EXTENSION_MEDIA_TYPES.get(_extension(display_name), "application/octet-stream")
        artifact_id = deterministic_artifact_id(parent_artifact_id=parent_artifact_id, kind=kind, position=position, content_hash=content_hash)
        budget.record_artifact(len(data))
        budget.binaries[artifact_id] = data
        return [
            KnowledgeArtifact(
                artifact_id=artifact_id,
                parent_artifact_id=parent_artifact_id,
                kind=kind,
                media_type=media_type,
                display_name=display_name,
                depth=depth,
                content_hash=content_hash,
                extracted_text=None,
                derived=False,
                extraction_status=ArtifactExtractionStatus.PARTIAL,
                extraction_error="raw binary preserved; multimodal interpretation not yet run",
            )
        ]

    sniffed = sniff_media_type(data)
    if data.startswith(_OLE2_COMPOUND_FILE_MAGIC) and sniffed is None:
        from backend.knowledge.ingestion.extractors.ole import extract_ole_package_payload  # noqa: PLC0415

        package = extract_ole_package_payload(data)
        ole_artifact_id = deterministic_artifact_id(
            parent_artifact_id=parent_artifact_id, kind="ole_package", position=position, content_hash=content_hash
        )
        budget.record_artifact(len(data))
        budget.binaries[ole_artifact_id] = data

        if package is None:
            # Not a recognized "\x01Ole10Native" Package stream, or it
            # failed structural validation -- fail safely, exactly as
            # before this correction, never guessing at content.
            return [
                KnowledgeArtifact(
                    artifact_id=ole_artifact_id,
                    parent_artifact_id=parent_artifact_id,
                    kind="embedded_object",
                    media_type="application/x-ole-object" if _extension(display_name) == "bin" else None,
                    display_name=display_name,
                    depth=depth,
                    content_hash=content_hash,
                    extracted_text=None,
                    derived=False,
                    extraction_status=ArtifactExtractionStatus.SKIPPED,
                    extraction_error="legacy OLE/compound-file embedded object -- no recognized embedded payload, not further decomposed",
                )
            ]

        payload_filename, payload_bytes = package
        ole_container_artifact = KnowledgeArtifact(
            artifact_id=ole_artifact_id,
            parent_artifact_id=parent_artifact_id,
            kind="ole_package",
            media_type="application/x-ole-object",
            display_name=display_name,
            depth=depth,
            content_hash=content_hash,
            extracted_text=None,
            derived=False,
            extraction_status=ArtifactExtractionStatus.COMPLETE,
        )
        # The embedded payload becomes a CHILD of the OLE container
        # artifact (never a sibling), preserving the real container
        # relationship -- dispatched through the SAME generic recursive
        # pipeline (image/docx/xlsx/pdf/txt_log/unsupported) any other
        # embedded object uses, keyed by the payload's OWN original
        # filename (never the OLE container's own display_name).
        payload_extension = _extension(payload_filename)
        if payload_extension in _IMAGE_EXTENSION_MEDIA_TYPES:
            payload_relationship_kind = "image"
        elif payload_extension in {"txt", "log"}:
            payload_relationship_kind = "txt_log"
        else:
            # Any other extension (e.g. a nested .docx/.xlsx/.pdf) still
            # gets correctly classified below by extract_embedded_artifact's
            # OWN real container-structure sniffing -- this default is
            # only a fallback label, never authoritative.
            payload_relationship_kind = "embedded_object"
        child_artifacts = extract_embedded_artifact(
            payload_bytes,
            parent_artifact_id=ole_artifact_id,
            depth=depth + 1,
            budget=budget,
            display_name=payload_filename,
            position=0,
            relationship_kind=payload_relationship_kind,
        )
        return [ole_container_artifact, *child_artifacts]

    if sniffed == "docx":
        from backend.knowledge.ingestion.extractors.docx import extract_docx  # noqa: PLC0415

        artifact_id = deterministic_artifact_id(parent_artifact_id=parent_artifact_id, kind="embedded_docx", position=position, content_hash=content_hash)
        budget.record_artifact(len(data))
        budget.binaries[artifact_id] = data
        text, children = extract_docx(data, container_artifact_id=artifact_id, depth=depth + 1, budget=budget)
        self_artifact = KnowledgeArtifact(
            artifact_id=artifact_id,
            parent_artifact_id=parent_artifact_id,
            kind="embedded_docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            display_name=display_name,
            depth=depth,
            content_hash=content_hash,
            extracted_text=text if text.strip() else None,
            derived=False,
            extraction_status=ArtifactExtractionStatus.COMPLETE,
        )
        return [self_artifact, *children]

    if sniffed == "xlsx":
        artifact_id = deterministic_artifact_id(parent_artifact_id=parent_artifact_id, kind="embedded_xlsx", position=position, content_hash=content_hash)
        budget.record_artifact(len(data))
        budget.binaries[artifact_id] = data
        text, children = extract_xlsx(data, container_artifact_id=artifact_id, depth=depth + 1, budget=budget)
        self_artifact = KnowledgeArtifact(
            artifact_id=artifact_id,
            parent_artifact_id=parent_artifact_id,
            kind="embedded_xlsx",
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            display_name=display_name,
            depth=depth,
            content_hash=content_hash,
            extracted_text=text if text.strip() else None,
            derived=False,
            extraction_status=ArtifactExtractionStatus.COMPLETE,
        )
        return [self_artifact, *children]

    if sniffed == "pdf":
        artifact_id = deterministic_artifact_id(parent_artifact_id=parent_artifact_id, kind="embedded_pdf", position=position, content_hash=content_hash)
        try:
            budget.record_artifact(len(data))
            budget.binaries[artifact_id] = data
            text, children = extract_pdf(data, container_artifact_id=artifact_id, depth=depth + 1, budget=budget)
        except EncryptedPdfError as exc:
            return [
                KnowledgeArtifact(
                    artifact_id=artifact_id,
                    parent_artifact_id=parent_artifact_id,
                    kind="embedded_pdf",
                    display_name=display_name,
                    depth=depth,
                    content_hash=content_hash,
                    extraction_status=ArtifactExtractionStatus.SKIPPED,
                    extraction_error=f"encrypted PDF, not decoded: {exc}",
                )
            ]
        self_artifact = KnowledgeArtifact(
            artifact_id=artifact_id,
            parent_artifact_id=parent_artifact_id,
            kind="embedded_pdf",
            media_type="application/pdf",
            display_name=display_name,
            depth=depth,
            content_hash=content_hash,
            extracted_text=text if text.strip() else None,
            derived=False,
            extraction_status=ArtifactExtractionStatus.COMPLETE,
        )
        return [self_artifact, *children]

    if relationship_kind == "txt_log" or _extension(display_name) in {"txt", "log"}:
        text = extract_txt(data)
        artifact_id = deterministic_artifact_id(parent_artifact_id=parent_artifact_id, kind="txt_log", position=position, content_hash=content_hash)
        budget.record_artifact(len(data))
        budget.binaries[artifact_id] = data
        return [
            KnowledgeArtifact(
                artifact_id=artifact_id,
                parent_artifact_id=parent_artifact_id,
                kind="txt_log",
                media_type="text/plain",
                display_name=display_name,
                depth=depth,
                content_hash=content_hash,
                extracted_text=text if text.strip() else None,
                derived=False,
                extraction_status=ArtifactExtractionStatus.COMPLETE,
            )
        ]

    # Genuinely unrecognized/unsupported artifact type -- identified,
    # reported, skipped/quarantined safely (A5 instruction section 31),
    # never silently interpreted as another type.
    artifact_id = deterministic_artifact_id(parent_artifact_id=parent_artifact_id, kind="unsupported_artifact", position=position, content_hash=content_hash)
    budget.record_artifact(len(data))
    budget.binaries[artifact_id] = data
    return [
        KnowledgeArtifact(
            artifact_id=artifact_id,
            parent_artifact_id=parent_artifact_id,
            kind="unsupported_artifact",
            display_name=display_name,
            depth=depth,
            content_hash=content_hash,
            extraction_status=ArtifactExtractionStatus.SKIPPED,
            extraction_error="unrecognized artifact format -- not DOCX/XLSX/PDF/TXT/image by actual container structure",
        )
    ]


class UnsupportedRootDocumentError(RuntimeError):
    """The root document's own format could not be recognized (A5
    instruction section 31: never guess-interpret it as another type).
    """


class EncryptedDocumentError(RuntimeError):
    """The root document (or an embedded object recursed into as a root-
    like container) is password-protected/encrypted -- never guessed,
    never force-opened (A5 instruction section 30).
    """
