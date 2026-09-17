"""Phase 6A.5: index synchronization (§24).

Minimal, reliable rebuild/upsert behavior -- NOT a full event-driven
ingestion platform (§24's own explicit "do not build" boundary). Supports
exactly: insert new evidence, idempotent re-run (skip-unchanged via
content hash), update changed source content, re-embed when the
embedding model/version changes. This module NEVER calls the concrete
`VertexTextEmbeddingProvider` directly -- it only depends on the generic
`EmbeddingProvider` Protocol (`embedding.py`), so it stays free of
`google.genai` (enforced by this milestone's own dependency-boundary
extension).

EMBEDDING FAILURE HANDLING (§42): if the embedding provider raises, this
module does NOT corrupt the Knowledge repository (it never touches it --
read-only against `KnowledgeObject`), does NOT delete any existing
exact/lexical-capable row, and records the failure by simply leaving
`embedding=None` for the affected evidence units -- exact/lexical
indexing for those units still succeeds via the SAME `upsert` call.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.knowledge.domain.models import KnowledgeObject
from backend.knowledge.hybrid_retrieval.contracts import EvidenceIndexRecord
from backend.knowledge.hybrid_retrieval.embedding import EmbeddingProvider
from backend.knowledge.hybrid_retrieval.indexable_text import build_indexable_text, content_hash, resolve_is_derived
from backend.knowledge.hybrid_retrieval.repository import EvidenceIndexRepository

__all__ = ["evidence_id_for_section", "index_knowledge_object"]

_logger = logging.getLogger(__name__)


def evidence_id_for_section(knowledge_id: str, version_label: str, section_id: str) -> str:
    """Deterministic identity (§8: never a random UUID) -- re-indexing
    the identical section always produces the identical `evidence_id`,
    making re-runs naturally idempotent at the identity level (content-
    hash comparison then handles the "did the TEXT change" question
    separately, in `repository.upsert`)."""
    import hashlib

    basis = f"{knowledge_id}\x1f{version_label}\x1f{section_id}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


async def index_knowledge_object(
    knowledge_object: KnowledgeObject,
    repository: EvidenceIndexRepository,
    embedding_provider: EmbeddingProvider,
) -> dict[str, int]:
    """Indexes every section of `knowledge_object`, one evidence unit per
    section (§7: SECTION granularity, never whole-document). Batches all
    of this object's texts into ONE embedding call (§45 cost control --
    never one network round trip per section). Returns a small, plain
    counters dict (`inserted_or_updated`/`skipped_unchanged`/
    `embedding_failed`) -- deterministic, safe to log.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    records: list[EvidenceIndexRecord] = []
    for section in knowledge_object.sections:
        text_value = build_indexable_text(knowledge_object, section)
        records.append(
            EvidenceIndexRecord(
                evidence_id=evidence_id_for_section(knowledge_object.knowledge_id, knowledge_object.version.label, section.section_id),
                knowledge_id=knowledge_object.knowledge_id,
                version_label=knowledge_object.version.label,
                section_id=section.section_id,
                artifact_id=section.artifact_id,
                is_derived=resolve_is_derived(knowledge_object, section),
                indexable_text=text_value,
                content_hash=content_hash(text_value),
            )
        )

    if not records:
        # An object with no sections at all still needs its stale rows
        # removed -- otherwise emptying a document leaves its whole index
        # footprint searchable.
        removed = await repository.delete_missing_for_version(
            knowledge_object.knowledge_id, knowledge_object.version.label, keep_evidence_ids=[]
        )
        return {
            "inserted_or_updated": 0,
            "skipped_unchanged": 0,
            "embedding_failed": 0,
            "removed_sections": removed,
            "reembedded": 0,
        }

    existing_by_id: dict[str, EvidenceIndexRecord] = {}
    for record in records:
        existing = await repository.get(record.evidence_id)
        if existing is not None:
            existing_by_id[record.evidence_id] = existing

    # POST-6A -- INDEX RECONCILIATION. Content hash answers exactly one
    # question ("did the indexable TEXT change?"), and the original
    # `to_embed` rule treated that as the only reason to re-embed. Three
    # real reconciliation needs were therefore unreachable, all of them
    # leaving an evidence unit permanently unsearchable by the semantic
    # channel while looking perfectly healthy:
    #
    #   MISSING VECTOR -- a prior run's embedding call failed, so the row
    #     exists with `embedding_generated_at IS NULL`. Its hash is
    #     unchanged, so it was never retried. Ever.
    #   CHANGED EMBEDDING MODEL / VERSION / DIMENSIONS -- the stored
    #     vector was produced by a different model or at a different
    #     dimensionality, so it is not comparable with query vectors any
    #     more. Again, unchanged hash, never retried.
    #   CHANGED CONTENT -- the pre-existing case, unchanged.
    #
    # An unchanged content hash must not PREVENT a required retry; it only
    # means the text itself is the same.
    expected_model = getattr(embedding_provider, "model", None)
    expected_model_version = getattr(embedding_provider, "model_version", None)
    expected_dimensions = getattr(embedding_provider, "dimensions", None)

    def _needs_embedding(record: EvidenceIndexRecord) -> bool:
        existing = existing_by_id.get(record.evidence_id)
        if existing is None:
            return True
        if existing.content_hash != record.content_hash:
            return True
        if existing.embedding_generated_at is None:
            return True  # missing vector -- a previous attempt failed
        if expected_model is not None and existing.embedding_model != expected_model:
            return True
        if expected_model_version is not None and existing.embedding_model_version != expected_model_version:
            return True
        if expected_dimensions is not None and existing.embedding_dimensions != expected_dimensions:
            return True
        return False

    to_embed = [r for r in records if _needs_embedding(r)]

    embeddings: dict[str, list[float]] = {}
    embedding_failed = 0
    if to_embed:
        try:
            vectors = await embedding_provider.embed([r.indexable_text for r in to_embed])
            for record, vector in zip(to_embed, vectors):
                embeddings[record.evidence_id] = list(vector.values)
                record.embedding_model = vector.model
                record.embedding_model_version = vector.model_version
                record.embedding_dimensions = vector.dimensions
                record.embedding_generated_at = now
        except Exception:
            _logger.warning("embedding generation failed for knowledge_id=%s -- exact/lexical indexing continues", knowledge_object.knowledge_id, exc_info=True)
            embedding_failed = len(to_embed)

    inserted_or_updated = 0
    skipped_unchanged = 0
    for record in records:
        changed = await repository.upsert(record, embeddings.get(record.evidence_id), now=now)
        if changed:
            inserted_or_updated += 1
        else:
            skipped_unchanged += 1

    # POST-6A -- REMOVED SECTIONS. A section deleted from the governed
    # object previously left its index row behind forever: it stayed
    # exact/lexically searchable and could still be returned as a
    # candidate for content that no longer exists in the source. Every
    # row for this (knowledge_id, version_label) that is not among the
    # sections just indexed is deleted. Scoped strictly to THIS version
    # -- another version's rows are a different, still-valid index.
    removed = await repository.delete_missing_for_version(
        knowledge_object.knowledge_id,
        knowledge_object.version.label,
        keep_evidence_ids=[r.evidence_id for r in records],
    )

    return {
        "inserted_or_updated": inserted_or_updated,
        "skipped_unchanged": skipped_unchanged,
        "embedding_failed": embedding_failed,
        "removed_sections": removed,
        "reembedded": len(to_embed),
    }
