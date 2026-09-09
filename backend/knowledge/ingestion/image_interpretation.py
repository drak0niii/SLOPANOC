"""Generic image-interpretation boundary (A5 Layer G).

A `KnowledgeArtifact` of kind `"image"` is preserved as raw binary
during extraction (Layers C/D/E) with `extracted_text=None` -- pixels
are not text. Turning an image into retrievable text requires model-
backed (multimodal) interpretation, which this module deliberately
keeps behind a generic, ADK/Gemini-independent `Protocol` -- the same
architectural discipline as `KnowledgeSourceAdapter`
(backend/knowledge/ingestion/adapters.py) and
`KnowledgeContentProcessor` (backend/knowledge/processing/processor.py):
a Protocol here, a concrete implementation elsewhere.

A concrete Gemini-backed implementation lives in
`backend/knowledge_ingestion/gemini_image_interpreter.py` -- OUTSIDE
`backend/knowledge/` entirely, because `google.adk`/`google.genai`
imports are forbidden anywhere under `backend/knowledge/`
(test_dependency_boundary.py's `_FORBIDDEN_IMPORT_PREFIXES`), the exact
same reason `backend/knowledge_ingestion/artifact_storage.py` (a
concrete `google.cloud.storage` user) also lives there rather than
under `backend/knowledge/ingestion/`.

TRUST BOUNDARY (A5 instruction section 25): an `ImageInterpreter`
implementation may ONLY produce descriptive text (`ImageInterpretation
.description`) -- it has no way to execute a tool, approve knowledge, set
applicability, or choose a lifecycle state, because this Protocol's
return shape has no field for any of those. The caller (the ingestion
orchestrator, not this module) is responsible for marking any resulting
`KnowledgeArtifact.derived=True` (A5 instruction section 16: derived
content must never overwrite/be confused with source truth).

FAILURE (A5 instruction section 26): a failed interpretation must never
fail the whole root document -- `ImageInterpretationResult.succeeded`
is `False` with `error` set, and the caller preserves the artifact with
`extraction_status=PARTIAL`/`extracted_text=None` rather than
fabricating a description or raising.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional, Protocol

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact

_RASTERIZABLE_VECTOR_MEDIA_TYPES = {"image/x-emf", "image/x-wmf"}
_RASTERIZED_MEDIA_TYPE = "image/png"


def rasterize_vector_image(image_bytes: bytes, media_type: str) -> Optional[bytes]:
    """A5 final corrective pass (Correction B): safely rasterizes a
    legacy EMF/WMF vector image (Windows Enhanced/Windows Metafile --
    common in real Word documents pasted from screenshots/diagrams) into
    a real PNG bitmap, using Pillow's own already-bundled
    `WmfImagePlugin` -- Pillow is already this codebase's pinned image
    dependency (`backend/attachments/validation.py`); no new dependency
    was added for this. This is a pure, in-process, bounded decode +
    re-encode -- no external process, no shell, no COM/Office
    automation, no macro/script execution capability of any kind (Pillow
    has none). Input size is already bounded upstream by `ExtractionLimits
    .max_artifact_bytes` (this function is only ever reached for an
    artifact that already passed that check).

    Returns the rasterized PNG bytes, or `None` for anything not one of
    the two vector media types this handles, or that Pillow could not
    safely decode (a genuinely corrupt/unsupported vector file) --
    NEVER raises; the caller falls back to attempting interpretation of
    the ORIGINAL bytes directly (the pre-existing, unchanged failure
    path) in that case, so a rasterization failure never regresses
    behavior versus before this correction.

    The ORIGINAL EMF/WMF artifact remains the source of truth -- this
    function never mutates or replaces it; it only ever returns a NEW,
    separate bytes buffer for `apply_image_interpretation` to pass to
    the interpreter INSTEAD OF the original bytes for that one call.
    """
    if media_type not in _RASTERIZABLE_VECTOR_MEDIA_TYPES:
        return None
    try:
        from PIL import Image  # noqa: PLC0415 -- imported lazily, matching this codebase's convention.

        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            output = io.BytesIO()
            image.convert("RGB").save(output, format="PNG")
            return output.getvalue()
    except Exception:
        return None


@dataclass(frozen=True)
class ImageInterpretationResult:
    """The outcome of one interpretation attempt. `succeeded=False` means
    `description` is always `None` and `error` is always set -- the
    inverse never happens (enforced by `ImageInterpreter` implementations
    by convention; this is a plain data holder, not itself validated).
    """

    succeeded: bool
    description: Optional[str]
    model_identifier: Optional[str]
    error: Optional[str] = None


class ImageInterpreter(Protocol):
    """Structural contract: interpret one image's raw bytes into
    descriptive text, given optional bounded context (e.g. the parent
    section's own nearby text -- A5 instruction section 25's "image +
    bounded parent/adjacent section context -> structured visual
    interpretation" flow). Async because a real implementation performs
    a network call. Must never raise for an ordinary interpretation
    failure (a malformed image, a model error, a timeout) -- those
    become `ImageInterpretationResult(succeeded=False, ...)`; raising is
    reserved for a genuine programming error (e.g. invalid arguments).
    """

    async def interpret(self, image_bytes: bytes, media_type: str, *, context: Optional[str] = None) -> ImageInterpretationResult: ...


async def apply_image_interpretation(
    artifacts: list[KnowledgeArtifact],
    binaries: dict[str, bytes],
    interpreter: ImageInterpreter,
    *,
    context_by_artifact_id: Optional[dict[str, str]] = None,
) -> list[KnowledgeArtifact]:
    """Runs `interpreter.interpret` for every `kind="image"` artifact
    that has raw bytes available in `binaries` (artifact_id -> bytes,
    e.g. `ExtractionBudget.binaries` from the extraction run that
    produced `artifacts`), returning a NEW list with each image
    artifact's `extracted_text`/`derived`/`extraction_status`/
    `extraction_error`/`processing_metadata` updated from the result.

    Every non-image artifact, and every image artifact with no entry in
    `binaries` (raw bytes unavailable, e.g. already discarded), is
    returned completely unchanged. A failed interpretation
    (`succeeded=False`) leaves `extracted_text=None` and sets
    `extraction_status=PARTIAL`/`extraction_error` from the result's own
    `error` -- never fails this function as a whole, and never fails the
    artifacts around it (A5 instruction section 26).
    """
    context_by_artifact_id = context_by_artifact_id or {}
    updated: list[KnowledgeArtifact] = []
    for artifact in artifacts:
        if artifact.kind != "image" or artifact.artifact_id not in binaries:
            updated.append(artifact)
            continue

        original_bytes = binaries[artifact.artifact_id]
        original_media_type = artifact.media_type or "application/octet-stream"
        rasterized_bytes = rasterize_vector_image(original_bytes, original_media_type)
        if rasterized_bytes is not None:
            interpret_bytes, interpret_media_type = rasterized_bytes, _RASTERIZED_MEDIA_TYPE
        else:
            interpret_bytes, interpret_media_type = original_bytes, original_media_type

        result = await interpreter.interpret(
            interpret_bytes,
            interpret_media_type,
            context=context_by_artifact_id.get(artifact.artifact_id),
        )
        if result.succeeded and result.description:
            processing_metadata = {**artifact.processing_metadata, "multimodal_model_identifier": result.model_identifier}
            if rasterized_bytes is not None:
                # Correction B provenance requirement: the ORIGINAL EMF/
                # WMF artifact (artifact_id/content_hash/storage_ref
                # unchanged) remains the cited source; this metadata only
                # records that a rasterized intermediate was needed to
                # reach Gemini -- never a replacement of the source.
                processing_metadata["rasterized_from"] = original_media_type
                processing_metadata["rasterized_to"] = _RASTERIZED_MEDIA_TYPE
            updated.append(
                artifact.model_copy(
                    update={
                        "extracted_text": result.description,
                        "derived": True,
                        "extraction_status": ArtifactExtractionStatus.COMPLETE,
                        "extraction_error": None,
                        "processing_metadata": processing_metadata,
                    }
                )
            )
        else:
            updated.append(
                artifact.model_copy(
                    update={
                        "extracted_text": None,
                        "extraction_status": ArtifactExtractionStatus.PARTIAL,
                        "extraction_error": result.error or "image interpretation did not succeed",
                    }
                )
            )
    return updated
