"""A5 Layer H: bridges a compound `IngestedKnowledgeDocument` (root text
+ `artifacts[]`, each optionally carrying its own `extracted_text`) into
one coherent `StructuredKnowledgeDocument` -- WITHOUT a new,
artifact-specific processor. The existing, unmodified 5.1D
`HeadingStructureProcessor` (or any `KnowledgeContentProcessor`) is
reused verbatim for the root text AND, separately, for each artifact's
own extracted text -- this is deliberate reuse, not a parallel
segmentation implementation (A5 instruction section 21/23: "no new
processor was needed").

Every resulting section keeps `source_locator` in the SAME `"lines:
<start>-<end>"` shape 5.1D already produces (now scoped to whichever
text -- root or one artifact's own -- it was actually cut from) and
gains `artifact_id` (`None` for a root-text section, an
`artifacts[].artifact_id` for an artifact-derived one) -- completing
hierarchical provenance (A5 instruction section 35/36: "section ->
artifact -> parent artifact -> ... -> root") without inventing a second
locator scheme.

An artifact with no `extracted_text` (e.g. an image not yet
multimodally interpreted, or one that failed/was skipped during
extraction) simply contributes no section of its own -- it still exists
in `KnowledgeObject.artifacts` for lineage/provenance, it just has no
retrievable text yet (A5 instruction section 26: "retrieval must not
pretend image-derived knowledge exists if extraction failed").
"""
from __future__ import annotations

from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.processing.contracts import StructuredKnowledgeDocument, StructuredKnowledgeSection
from backend.knowledge.processing.processor import HeadingStructureProcessor, KnowledgeContentProcessor

_DEFAULT_PROCESSOR = HeadingStructureProcessor()


def process_compound_document(
    document: IngestedKnowledgeDocument,
    *,
    processor: KnowledgeContentProcessor = _DEFAULT_PROCESSOR,
) -> StructuredKnowledgeDocument:
    """Returns one `StructuredKnowledgeDocument` covering the root
    document's own text AND every artifact's own `extracted_text`, all
    segmented by the SAME `processor`. `section_key`/`sequence` are
    renumbered globally (still deterministic and document-order-based:
    root sections first, then each artifact's sections in
    `document.artifacts` order) so the combined result satisfies
    `StructuredKnowledgeDocument`'s own uniqueness invariants unchanged.

    For a plain-text document with no artifacts (pre-A5 shape), this is
    IDENTICAL to calling `processor.process(document)` directly --
    `artifacts=[]` contributes no additional sections, and every root
    section's `artifact_id` stays `None`.
    """
    root_structured = processor.process(document)

    section_list: list[StructuredKnowledgeSection] = []
    sequence = 0
    for section in root_structured.sections:
        section_list.append(
            section.model_copy(update={"section_key": f"section-{sequence:04d}", "sequence": sequence, "artifact_id": None})
        )
        sequence += 1

    for artifact in document.artifacts:
        if not artifact.extracted_text:
            continue
        artifact_document = IngestedKnowledgeDocument(
            source=document.source,
            title=artifact.display_name or artifact.artifact_id,
            content=artifact.extracted_text,
        )
        artifact_structured = processor.process(artifact_document)
        for section in artifact_structured.sections:
            section_list.append(
                section.model_copy(update={"section_key": f"section-{sequence:04d}", "sequence": sequence, "artifact_id": artifact.artifact_id})
            )
            sequence += 1

    return StructuredKnowledgeDocument(source_document=document, sections=section_list)
