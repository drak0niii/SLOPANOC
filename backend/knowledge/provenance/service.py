"""`KnowledgeProvenanceService` -- exact repository revalidation of a
5.1G retrieval result into trusted `KnowledgeEvidenceSet`, plus the pure
`validate_evidence_selection` operation. Never re-runs relevance
scoring, ranking, applicability evaluation, or current-version
resolution -- see this package's own `__init__.py` for the full
rationale.
"""
from __future__ import annotations

from backend.knowledge.domain.contracts import KnowledgeEvidenceReference
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import (
    KnowledgeEvidenceItem,
    KnowledgeEvidenceMismatchError,
    KnowledgeEvidenceNotFoundError,
    KnowledgeEvidenceSelectionError,
    KnowledgeEvidenceSelectionKey,
    KnowledgeEvidenceSet,
)
from backend.knowledge.repository.contracts import KnowledgeRepository
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalItem, KnowledgeRetrievalResult

_SECTION_FIELDS = ("section_id", "knowledge_id", "sequence", "heading", "section_type", "content", "source_locator")
_SOURCE_FIELDS = ("source_system", "source_id", "source_uri", "display_name")


def _require_section_match(claimed: KnowledgeSection, governed: KnowledgeSection) -> None:
    """Exact, unnormalized field-by-field comparison -- no hashing, no
    "close enough", no comparing only `content`. Any single field
    disagreeing with the freshly-fetched governed section fails closed.
    """
    for field_name in _SECTION_FIELDS:
        if getattr(claimed, field_name) != getattr(governed, field_name):
            raise KnowledgeEvidenceMismatchError(
                f"retrieved section field {field_name!r} does not match governed repository "
                f"state for section_id={governed.section_id!r}"
            )


def _require_source_match(claimed: KnowledgeSource, governed: KnowledgeSource) -> None:
    for field_name in _SOURCE_FIELDS:
        if getattr(claimed, field_name) != getattr(governed, field_name):
            raise KnowledgeEvidenceMismatchError(
                f"retrieved source field {field_name!r} does not match governed repository "
                f"state for knowledge_id={governed.source_id!r}"
            )


def _require_document_metadata_match(retrieval_item: KnowledgeRetrievalItem, governed: KnowledgeObject) -> None:
    if retrieval_item.title != governed.title:
        raise KnowledgeEvidenceMismatchError(
            f"retrieved title does not match governed repository state for "
            f"knowledge_id={governed.knowledge_id!r} version_label={governed.version.label!r}"
        )
    if retrieval_item.document_type != governed.document_type:
        raise KnowledgeEvidenceMismatchError(
            f"retrieved document_type does not match governed repository state for "
            f"knowledge_id={governed.knowledge_id!r} version_label={governed.version.label!r}"
        )
    if retrieval_item.lifecycle_status != governed.lifecycle_status:
        raise KnowledgeEvidenceMismatchError(
            f"retrieved lifecycle_status does not match governed repository state for "
            f"knowledge_id={governed.knowledge_id!r} version_label={governed.version.label!r}"
        )


def _find_section(governed: KnowledgeObject, section_id: str) -> "KnowledgeSection | None":
    for section in governed.sections:
        if section.section_id == section_id:
            return section
    return None


class KnowledgeProvenanceService:
    """Depends only on the `KnowledgeRepository` Protocol (never
    `SQLiteKnowledgeRepository` or any storage detail). Read-only: never
    calls `repository.add`/`replace`.
    """

    def __init__(self, repository: KnowledgeRepository) -> None:
        self._repository = repository

    async def build_evidence_set(self, retrieval_result: KnowledgeRetrievalResult) -> KnowledgeEvidenceSet:
        """Validate every `KnowledgeRetrievalItem` in `retrieval_result`
        against an exact `repository.get(knowledge_id, version_label)`
        lookup -- never `list_all`, never a title/source search, never a
        fallback to another version. Order is preserved from
        `retrieval_result.items` (the 5.1G ranking order). Fails closed,
        raising on the FIRST invalid item rather than silently omitting
        it or returning a partial evidence set.
        """
        items: list[KnowledgeEvidenceItem] = []
        for retrieval_item in retrieval_result.items:
            governed = await self._repository.get(retrieval_item.knowledge_id, retrieval_item.version_label)
            if governed is None:
                raise KnowledgeEvidenceNotFoundError(
                    f"no governed object found for knowledge_id={retrieval_item.knowledge_id!r} "
                    f"version_label={retrieval_item.version_label!r}"
                )

            governed_section = _find_section(governed, retrieval_item.section.section_id)
            if governed_section is None:
                raise KnowledgeEvidenceNotFoundError(
                    f"no section {retrieval_item.section.section_id!r} found in governed version "
                    f"knowledge_id={retrieval_item.knowledge_id!r} version_label={retrieval_item.version_label!r}"
                )

            _require_section_match(retrieval_item.section, governed_section)
            _require_document_metadata_match(retrieval_item, governed)
            _require_source_match(retrieval_item.source, governed.source)

            reference = KnowledgeEvidenceReference(
                knowledge_id=governed.knowledge_id,
                version_label=governed.version.label,
                section_id=governed_section.section_id,
                source_system=governed.source.source_system,
                source_id=governed.source.source_id,
                source_locator=governed_section.source_locator,
            )
            items.append(
                KnowledgeEvidenceItem(
                    reference=reference,
                    title=governed.title,
                    document_type=governed.document_type,
                    lifecycle_status=governed.lifecycle_status,
                    source=governed.source,
                    section=governed_section,
                )
            )

        return KnowledgeEvidenceSet(items=items)


def validate_evidence_selection(
    evidence_set: KnowledgeEvidenceSet, selections: list[KnowledgeEvidenceSelectionKey]
) -> list[KnowledgeEvidenceItem]:
    """Pure, deterministic, repository-free: validates a requested
    selection against the already-bounded `evidence_set` ONLY -- a real
    repository section that was never part of this evidence set is
    indistinguishable, from this function's perspective, from a purely
    fabricated one. ALL requested identities must be present or the
    ENTIRE selection fails (no partial trust); duplicate requested
    identities are deduplicated, preserving first-occurrence order of
    `selections`; an empty `selections` list validly returns `[]`.
    """
    index: dict[tuple[str, str, "str | None"], KnowledgeEvidenceItem] = {
        (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id): item
        for item in evidence_set.items
    }

    requested_keys = [(key.knowledge_id, key.version_label, key.section_id) for key in selections]
    unknown_keys = [key for key in requested_keys if key not in index]
    if unknown_keys:
        raise KnowledgeEvidenceSelectionError(
            f"selection references evidence not present in the bounded evidence set: {unknown_keys}"
        )

    seen: set[tuple[str, str, "str | None"]] = set()
    result: list[KnowledgeEvidenceItem] = []
    for key in requested_keys:
        if key in seen:
            continue
        seen.add(key)
        result.append(index[key])
    return result
