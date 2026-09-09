"""Shared primitives for compound-document extraction (A5 Layers C-F):
deterministic hashing, defensive extraction limits/budget tracking,
container-structure format sniffing (never trust a file extension
alone), and deterministic artifact identity.

Nothing in this module performs any actual format-specific parsing --
see `backend/knowledge/ingestion/extractors/` for DOCX/PDF/XLSX/TXT/
image handling and `backend/knowledge/ingestion/extractors/dispatch.py`
for the recursive orchestrator that uses these primitives.

SAFETY (A5 instruction section 29/31): this module never executes
macros/VBA/formulas/scripts (it does no parsing at all), never follows
a path outside in-memory byte buffers (no filesystem extraction, so
zip/path traversal has no target to traverse to), and every extraction
limit here exists specifically to bound recursion depth, artifact
count, and expanded size against a hostile or malformed package.
"""
from __future__ import annotations

import hashlib
import zipfile
from dataclasses import dataclass, field
from typing import Optional


def hash_bytes(data: bytes) -> str:
    """Deterministic SHA-256 hex digest -- the sole hashing algorithm
    used anywhere in A5 ingestion (deduplication, `KnowledgeArtifact
    .content_hash`, durable storage object keys).
    """
    return hashlib.sha256(data).hexdigest()


def deterministic_artifact_id(*, parent_artifact_id: Optional[str], kind: str, position: int, content_hash: Optional[str]) -> str:
    """A stable artifact_id derived only from this node's own position
    in its parent's compound-document tree plus its content identity --
    NEVER a random UUID. Re-ingesting byte-identical source content
    reproduces the exact same artifact_id for the exact same node,
    which is what makes same-content re-ingestion idempotent (A5
    instruction section 45, Case A) without needing a separate
    "have I seen this document before" lookup during extraction itself
    -- the repository/governance layer is still the sole authority on
    whether a given ingestion actually becomes new governed knowledge.
    """
    basis = f"{parent_artifact_id or 'root'}:{kind}:{position}:{content_hash or ''}"
    return f"art-{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:24]}"


class ExtractionLimitExceededError(RuntimeError):
    """Raised internally when a defensive limit would be exceeded --
    always caught by the recursive orchestrator (dispatch.py), which
    converts it into a recorded, skipped artifact rather than letting it
    propagate and abort the whole root document's processing (A5
    instruction section 28: "preserve successfully processed content,
    record skipped item, record reason").
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ExtractionLimits:
    """Defensive bounds for one root document's whole extraction run.
    Values are read from `Settings.knowledge_ingestion_max_*` by the
    caller and passed in explicitly -- this module never imports
    `backend.config.settings` itself, keeping it a pure, settings-
    agnostic unit (consistent with the rest of `backend/knowledge/`'s
    "no hidden global config reads inside domain/processing logic"
    convention).
    """

    max_recursion_depth: int
    max_artifacts_per_root: int
    max_artifact_bytes: int
    max_total_expanded_bytes: int


@dataclass
class ExtractionBudget:
    """Mutable per-root-document counters, shared by reference across an
    entire recursive extraction run (every nested `extract_*` call
    receives the SAME instance). Never reset mid-run; a fresh
    `ExtractionBudget` is constructed once per root document ingested.
    """

    limits: ExtractionLimits
    artifacts_extracted: int = 0
    total_bytes_expanded: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (display_name, reason)
    binaries: dict[str, bytes] = field(
        default_factory=dict,
        # artifact_id -> raw bytes, populated once per artifact at the
        # single artifact-construction choke point
        # (extractors/dispatch.py's `extract_embedded_artifact`) --
        # transient, in-memory only for this one extraction run, never
        # itself persisted. The durable-artifact-storage boundary (Layer
        # B) and image-interpretation boundary (Layer G) both read from
        # here rather than needing extraction to return a second parallel
        # data structure.
    )

    def check_depth(self, depth: int) -> None:
        if depth > self.limits.max_recursion_depth:
            raise ExtractionLimitExceededError(f"recursion depth {depth} exceeds max_recursion_depth={self.limits.max_recursion_depth}")

    def reserve_artifact_slot(self) -> None:
        if self.artifacts_extracted >= self.limits.max_artifacts_per_root:
            raise ExtractionLimitExceededError(f"artifact count would exceed max_artifacts_per_root={self.limits.max_artifacts_per_root}")

    def reserve_bytes(self, size: int) -> None:
        if size > self.limits.max_artifact_bytes:
            raise ExtractionLimitExceededError(f"artifact size {size} exceeds max_artifact_bytes={self.limits.max_artifact_bytes}")
        if self.total_bytes_expanded + size > self.limits.max_total_expanded_bytes:
            raise ExtractionLimitExceededError(
                f"expanding {size} more bytes would exceed max_total_expanded_bytes={self.limits.max_total_expanded_bytes}"
            )

    def record_artifact(self, size: int) -> None:
        self.artifacts_extracted += 1
        self.total_bytes_expanded += size

    def record_skipped(self, display_name: str, reason: str) -> None:
        self.skipped.append((display_name, reason))


# --- Format sniffing (A5 instruction section 31: never trust extension alone) --

_PDF_MAGIC = b"%PDF-"
# OOXML content-type declarations that distinguish DOCX/XLSX/PPTX from
# each other and from an arbitrary ZIP -- read from the package's own
# [Content_Types].xml, never inferred from a filename suffix.
_OOXML_MAIN_PART_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": "xlsx",
}


def sniff_media_type(data: bytes) -> Optional[str]:
    """Inspects actual container/magic-byte structure and returns one of
    "docx" / "xlsx" / "pdf", or None if the content is not recognized as
    any of the compound formats this milestone supports (an unsupported
    artifact, per A5 instruction section 31 -- callers must report and
    skip it, never guess-interpret it as another type).
    """
    if data.startswith(_PDF_MAGIC):
        return "pdf"

    if data[:2] == b"PK":  # ZIP local-file-header signature -- OOXML packages are ZIPs.
        import io

        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                try:
                    content_types_xml = archive.read("[Content_Types].xml").decode("utf-8", errors="replace")
                except KeyError:
                    return None
                for content_type, media_type in _OOXML_MAIN_PART_CONTENT_TYPES.items():
                    if content_type in content_types_xml:
                        return media_type
        except zipfile.BadZipFile:
            return None
        return None

    return None
