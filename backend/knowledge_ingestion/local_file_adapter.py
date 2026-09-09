"""Local-file ADMIN/DEVELOPER ingestion adapter (A5 instruction section
11/52).

This is NOT normal NOC-engineer behavior (A5 instruction section 12:
"Normal NOC engineers should NOT need to upload MOPs while
troubleshooting") -- it is the smallest appropriate ADMIN/DEVELOPER
ingestion entry point, used to onboard real Knowledge Islands (starting
with real local MOP files for this milestone's own validation corpus)
into the existing, unmodified Generic Governed Knowledge pipeline. It
accepts ONLY an explicit list of file paths -- it never recursively
scans an arbitrary filesystem location (section 52's own requirement).

Lives in `backend/knowledge_ingestion/` (not
`backend/knowledge/ingestion/`) because it orchestrates concrete,
cloud-SDK-dependent machinery: durable artifact storage
(`artifact_storage.py`) and, optionally, Gemini-backed image
interpretation (`gemini_image_interpreter.py`).

PIPELINE, per file:

    read bytes (read-only, never mutates the source file)
        -> extract_root_document (Layers C/D/E: DOCX/XLSX/PDF/TXT +
           recursive embedded-artifact discovery, hashing, defensive
           limits)
        -> upload each artifact's own raw binary to durable storage,
           content-hash-addressed (Layer B) -- best-effort: if no
           bucket is configured, this step is skipped and reported,
           never raised as a hard failure (mirrors B2's own "storage
           unavailable is a clean, reportable state" discipline)
        -> optionally interpret image artifacts (Layer G) -- best-
           effort in the same sense
        -> build the resulting `IngestedKnowledgeDocument`
           (source/title/content/artifacts), still PRE-GOVERNANCE --
           this module never calls `materialize_candidate`/
           `approve_version` itself; governance remains an explicit,
           separate, trusted step (A5 instruction section 38/40)

Produces a structural `IngestionReport` per file (section 53) --
counts only, never real document content, so this is safe to log.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact
from backend.knowledge.domain.models import KnowledgeSource
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.ingestion.extraction import ExtractionBudget, ExtractionLimits, hash_bytes
from backend.knowledge.ingestion.extractors.dispatch import EncryptedDocumentError, UnsupportedRootDocumentError, extract_root_document
from backend.knowledge.ingestion.image_interpretation import ImageInterpreter, apply_image_interpretation
from backend.knowledge_ingestion.artifact_storage import KnowledgeArtifactStorage, build_artifact_object_name

_logger = logging.getLogger(__name__)


@dataclass
class IngestionReport:
    """Structural-only summary (A5 instruction section 53) -- no real
    document content is ever recorded here.
    """

    file_name: str
    succeeded: bool
    error: Optional[str] = None
    artifact_count: int = 0
    counts_by_kind: dict[str, int] = field(default_factory=dict)
    max_depth: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    artifacts_uploaded: int = 0
    artifacts_deduplicated: int = 0
    images_interpreted: int = 0
    images_interpretation_failed: int = 0
    storage_configured: bool = False


@dataclass
class LocalFileIngestionResult:
    document: Optional[IngestedKnowledgeDocument]
    report: IngestionReport


def _default_extraction_limits() -> ExtractionLimits:
    from backend.config.settings import get_settings

    settings = get_settings()
    return ExtractionLimits(
        max_recursion_depth=settings.knowledge_ingestion_max_recursion_depth,
        max_artifacts_per_root=settings.knowledge_ingestion_max_artifacts_per_root,
        max_artifact_bytes=settings.knowledge_ingestion_max_artifact_bytes,
        max_total_expanded_bytes=settings.knowledge_ingestion_max_total_expanded_bytes,
    )


async def ingest_local_file(
    path: Path,
    *,
    source_system: str = "local_file",
    limits: Optional[ExtractionLimits] = None,
    storage: Optional[KnowledgeArtifactStorage] = None,
    interpreter: Optional[ImageInterpreter] = None,
) -> LocalFileIngestionResult:
    """Ingests exactly one explicit file path -- READ-ONLY (never
    writes to `path`, never moves/renames/deletes it). `storage`/
    `interpreter` are both optional and independently best-effort: pass
    `None` for either to skip that step entirely (e.g. for a structural-
    only validation run with no GCS bucket or model access configured).
    """
    file_name = path.name
    try:
        data = path.read_bytes()
    except OSError as exc:
        return LocalFileIngestionResult(document=None, report=IngestionReport(file_name=file_name, succeeded=False, error=f"could not read file: {exc}"))

    budget = ExtractionBudget(limits=limits or _default_extraction_limits())
    try:
        text, artifacts = extract_root_document(data, budget=budget)
    except (UnsupportedRootDocumentError, EncryptedDocumentError) as exc:
        return LocalFileIngestionResult(document=None, report=IngestionReport(file_name=file_name, succeeded=False, error=str(exc)))
    except Exception as exc:  # pragma: no cover -- defensive: a genuinely malformed package the extractors did not anticipate.
        _logger.warning("local_file_adapter: unexpected extraction failure for %s: %s", file_name, exc)
        return LocalFileIngestionResult(document=None, report=IngestionReport(file_name=file_name, succeeded=False, error=f"extraction failed: {exc}"))

    report = IngestionReport(
        file_name=file_name,
        succeeded=True,
        artifact_count=len(artifacts),
        max_depth=max((artifact.depth for artifact in artifacts), default=0),
        skipped=list(budget.skipped),
        storage_configured=bool(storage and storage.is_configured),
    )
    for artifact in artifacts:
        report.counts_by_kind[artifact.kind] = report.counts_by_kind.get(artifact.kind, 0) + 1

    if storage is not None and storage.is_configured:
        for artifact in artifacts:
            if artifact.content_hash is None or artifact.artifact_id not in budget.binaries:
                continue
            object_name = build_artifact_object_name(artifact.content_hash)
            try:
                # False means the object already existed at this
                # content-hash-addressed key -- i.e. genuinely
                # deduplicated, whether the earlier copy came from
                # elsewhere in this same batch or from a prior ingestion
                # run entirely (the object key strategy makes both cases
                # indistinguishable, and identically correct, by design).
                uploaded = storage.put_bytes_if_absent(object_name, budget.binaries[artifact.artifact_id], artifact.media_type or "application/octet-stream")
            except Exception as exc:  # pragma: no cover -- defensive: transient storage failure must not abort ingestion.
                _logger.warning("local_file_adapter: failed to upload artifact %s for %s: %s", artifact.artifact_id, file_name, exc)
                continue
            artifacts = [a.model_copy(update={"storage_ref": storage.uri_for(object_name)}) if a.artifact_id == artifact.artifact_id else a for a in artifacts]
            if uploaded:
                report.artifacts_uploaded += 1
            else:
                report.artifacts_deduplicated += 1

    if interpreter is not None:
        artifacts = await apply_image_interpretation(artifacts, budget.binaries, interpreter)
        for artifact in artifacts:
            if artifact.kind != "image":
                continue
            if artifact.derived and artifact.extracted_text:
                report.images_interpreted += 1
            elif artifact.extraction_status != ArtifactExtractionStatus.COMPLETE:
                report.images_interpretation_failed += 1

    document = IngestedKnowledgeDocument(
        source=KnowledgeSource(source_system=source_system, source_id=file_name, source_uri=str(path), display_name=file_name),
        title=path.stem,
        content=text,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        artifacts=artifacts,
    )
    return LocalFileIngestionResult(document=document, report=report)


async def ingest_local_files(
    paths: list[Path],
    *,
    source_system: str = "local_file",
    limits: Optional[ExtractionLimits] = None,
    storage: Optional[KnowledgeArtifactStorage] = None,
    interpreter: Optional[ImageInterpreter] = None,
) -> list[LocalFileIngestionResult]:
    """Ingests each of `paths` independently -- one file's failure never
    aborts the batch (each result records its own success/failure).
    """
    results = []
    for path in paths:
        results.append(
            await ingest_local_file(path, source_system=source_system, limits=limits, storage=storage, interpreter=interpreter)
        )
    return results
