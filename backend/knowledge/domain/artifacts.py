"""Generic compound-artifact representation (A5).

A real operational knowledge source (a MOP, SOP, technical instruction,
...) is frequently a COMPOUND document: native text plus embedded
objects (images, spreadsheets, other documents, logs) nested to some
bounded depth. `KnowledgeArtifact` is the single, generic node type used
to represent every one of those embedded objects -- there is
deliberately no `EmbeddedDocx`/`EmbeddedXlsx`/`EmbeddedImage` subclass
hierarchy; `kind` (a free-form string, exactly like
`KnowledgeSection.section_type`) is what distinguishes them.

An artifact tree always has a virtual root: the top-level document
itself is never one of these nodes (its own text remains
`IngestedKnowledgeDocument.content` / `KnowledgeObject`'s own sections
with `artifact_id=None`, exactly as before this module existed).
`KnowledgeArtifact.parent_artifact_id=None` means "embedded directly in
the root document"; a non-`None` parent means the artifact was found
nested inside another artifact (e.g. an image inside an embedded DOCX
inside the root MOP).

SOURCE vs DERIVED (A5 instruction section 16): `derived=False` means
`extracted_text` (if any) is a structural extraction of the artifact's
own real content (e.g. DOCX paragraph text, XLSX cell values).
`derived=True` means `extracted_text` is a MODEL INTERPRETATION of
source content that cannot itself be represented as text (e.g. a
screenshot's visual description) -- see docs/KNOWLEDGE_CONTRACT.md's A5
section. Derived content must never overwrite `storage_ref`'s underlying
binary, and a caller that needs the authoritative source must always be
able to reach the original artifact via `storage_ref`, never only the
derived text.

Only STRUCTURAL validation belongs here -- never extraction logic,
governance, hashing algorithm choice, or storage I/O (those are later
A5 layers: `backend/knowledge/ingestion/extractors/`,
`backend/knowledge/ingestion/storage.py`).
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.knowledge.domain._shared import require_non_blank

_SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArtifactExtractionStatus(str, Enum):
    """Whether extraction of one artifact's content succeeded.

    A genuinely bounded state machine (unlike `kind`/`section_type`,
    which are open content-taxonomy strings by design) -- exactly the
    same "small closed enum for a real state machine" convention
    `LifecycleStatus` already uses in this package.
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    SKIPPED = "skipped"


class KnowledgeArtifact(BaseModel):
    """One node in a compound document's embedded-object tree.

    `kind` is intentionally a free-form, non-blank string (e.g.
    "root_document", "embedded_docx", "embedded_pdf", "embedded_xlsx",
    "xlsx_sheet", "image", "txt_log", "pdf_page") -- never a fixed enum,
    for the same reason `KnowledgeSection.section_type` is not one: new
    artifact kinds must never require a schema change.

    `locator_detail` is an opaque, kind-specific string carrying
    whatever addressing information is meaningful for this artifact's
    own kind (e.g. "sheet=Q3;range=B2:D10" for an XLSX range, "page=4"
    for a PDF page) -- deliberately never parsed generically by this
    domain (mirrors why `StructuredKnowledgeSection.source_locator`
    stays a plain line-range string: a source-system-specific locator
    belongs "separately, outside this generic contract").

    `processing_metadata` is an open, unvalidated bag (parser identity/
    version, extraction schema version, multimodal model id, processing
    timestamp) for reproducibility -- audit-only, never displayed to the
    user by default, and never interpreted here (same convention as
    `KnowledgeMetadata.attributes`).
    """

    artifact_id: str
    parent_artifact_id: Optional[str] = Field(
        default=None, description="Another artifact's artifact_id, or None if this artifact is embedded directly in the root document."
    )
    kind: str = Field(description='Free-form, extensible artifact kind label (e.g. "embedded_docx", "image", "xlsx_sheet") -- never a fixed enum.')
    media_type: Optional[str] = Field(default=None, description="Free-form content-type label if known (e.g. a MIME type) -- never branched on here.")
    display_name: Optional[str] = Field(default=None, description="Human-readable label (e.g. a filename, sheet name, or 'Page 4') -- for provenance display only.")
    depth: int = Field(description="Nesting depth: 0 for an artifact embedded directly in the root document, 1 for an artifact nested inside that, and so on.")
    content_hash: Optional[str] = Field(default=None, description="SHA-256 hex digest of this artifact's own raw content, if computed -- used for deduplication.")
    extracted_text: Optional[str] = Field(
        default=None, description="This artifact's own textual representation, if any -- structural extraction unless `derived=True` (see module docstring)."
    )
    derived: bool = Field(
        default=False,
        description="False: extracted_text (if any) is a structural extraction of real source content. True: extracted_text is a MODEL INTERPRETATION (e.g. an image description) -- source truth always remains storage_ref's binary, never overwritten by this text.",
    )
    locator_detail: Optional[str] = Field(default=None, description="Opaque, kind-specific addressing detail (e.g. a sheet/range or page number) -- never parsed generically.")
    storage_ref: Optional[str] = Field(default=None, description="Durable storage reference (e.g. a gs:// URI) for this artifact's own raw binary, once persisted -- None until the durable-artifact-storage layer runs.")
    extraction_status: ArtifactExtractionStatus = Field(default=ArtifactExtractionStatus.COMPLETE)
    extraction_error: Optional[str] = Field(default=None, description="Human-readable reason, required when extraction_status is not COMPLETE.")
    processing_metadata: dict[str, Any] = Field(default_factory=dict, description="Open reproducibility bag: parser version, extraction schema version, multimodal model id, timestamp, etc. Audit-only.")

    @field_validator("artifact_id", "kind")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        return require_non_blank(value, info.field_name)

    @field_validator("parent_artifact_id", "media_type", "display_name", "locator_detail", "storage_ref")
    @classmethod
    def _non_blank_if_present(cls, value: Optional[str], info: Any) -> Optional[str]:
        if value is None:
            return value
        return require_non_blank(value, info.field_name)

    @field_validator("extracted_text")
    @classmethod
    def _extracted_text_non_blank_if_present(cls, value: Optional[str]) -> Optional[str]:
        # Deliberately not `require_non_blank` verbatim: an artifact with
        # no textual representation at all (e.g. an unprocessed image
        # before multimodal interpretation ran) sets this to None, not
        # "". An explicitly-set empty/whitespace-only string is still
        # rejected as a caller error, exactly like every other optional
        # text field in this package.
        if value is None:
            return value
        return require_non_blank(value, "extracted_text")

    @field_validator("depth")
    @classmethod
    def _depth_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("depth must not be negative")
        return value

    @field_validator("content_hash")
    @classmethod
    def _content_hash_valid_if_present(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        if not _SHA256_HEX_PATTERN.match(value):
            raise ValueError("content_hash must be a 64-character lowercase hex SHA-256 digest")
        return value

    @model_validator(mode="after")
    def _validate_structural_consistency(self) -> "KnowledgeArtifact":
        if self.parent_artifact_id == self.artifact_id:
            raise ValueError("an artifact must not be its own parent")
        if self.extraction_status != ArtifactExtractionStatus.COMPLETE and not self.extraction_error:
            raise ValueError("extraction_error is required when extraction_status is not COMPLETE")
        if self.extraction_status == ArtifactExtractionStatus.COMPLETE and self.extraction_error:
            raise ValueError("extraction_error must not be set when extraction_status is COMPLETE")
        return self


def validate_artifact_lineage(artifacts: list[KnowledgeArtifact]) -> None:
    """Shared structural cross-check reused by every container that
    carries an `artifacts` list (`IngestedKnowledgeDocument`,
    `KnowledgeObject`) -- unique `artifact_id`, and every
    `parent_artifact_id` resolves to another artifact in the SAME list
    (a closed lineage graph: no dangling parent references). Raises
    `ValueError` on violation; callers wrap this in their own
    `model_validator`.
    """
    seen_ids: set[str] = set()
    for artifact in artifacts:
        if artifact.artifact_id in seen_ids:
            raise ValueError(f"duplicate artifact_id {artifact.artifact_id!r}")
        seen_ids.add(artifact.artifact_id)
    for artifact in artifacts:
        if artifact.parent_artifact_id is not None and artifact.parent_artifact_id not in seen_ids:
            raise ValueError(
                f"artifact {artifact.artifact_id!r} has parent_artifact_id "
                f"{artifact.parent_artifact_id!r}, which is not present in the same artifact list"
            )
