"""Phase 6A.5: index synchronization
(`backend/knowledge/hybrid_retrieval/indexing.py`)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.hybrid_retrieval.contracts import EvidenceIndexRecord
from backend.knowledge.hybrid_retrieval.embedding import EmbeddingVector
from backend.knowledge.hybrid_retrieval.indexing import evidence_id_for_section, index_knowledge_object


class FakeIndexRepository:
    """In-memory stand-in for `EvidenceIndexRepository` -- duck-typed,
    never a real database (mocks are acceptable for unit tests)."""

    def __init__(self) -> None:
        self.rows: dict[str, EvidenceIndexRecord] = {}
        self.upsert_calls: list[tuple[str, object]] = []

    async def get(self, evidence_id):
        return self.rows.get(evidence_id)

    async def delete_missing_for_version(self, knowledge_id, version_label, *, keep_evidence_ids):
        """POST-6A: mirrors the real repository's own reconciliation
        delete -- scoped to ONE (knowledge_id, version_label), returning
        how many stale rows were removed."""
        keep = set(keep_evidence_ids)
        stale = [
            evidence_id
            for evidence_id, record in self.rows.items()
            if record.knowledge_id == knowledge_id
            and record.version_label == version_label
            and evidence_id not in keep
        ]
        for evidence_id in stale:
            self.rows.pop(evidence_id, None)
        return len(stale)

    async def upsert(self, record, embedding, *, now):
        existing = self.rows.get(record.evidence_id)
        self.upsert_calls.append((record.evidence_id, embedding))
        if existing is not None and existing.content_hash == record.content_hash and embedding is None:
            return False
        self.rows[record.evidence_id] = record
        return True


class FakeEmbeddingProvider:
    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.embed_calls: list[list[str]] = []

    async def embed(self, texts):
        self.embed_calls.append(list(texts))
        if self.should_fail:
            raise RuntimeError("simulated embedding API failure")
        return [EmbeddingVector(values=tuple([0.1] * 768), model="fake", model_version="fake-v1", dimensions=768) for _ in texts]


def _obj(kid: str, contents: list[str]) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=kid, document_type=KnowledgeDocumentType.MOP, title=kid,
        version=KnowledgeVersion(label="1.0"), lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="x", source_id=kid),
        sections=[KnowledgeSection(section_id=f"{kid}-S{i}", knowledge_id=kid, sequence=i, content=c) for i, c in enumerate(contents)],
    )


def test_evidence_id_deterministic() -> None:
    assert evidence_id_for_section("K1", "1.0", "S1") == evidence_id_for_section("K1", "1.0", "S1")


def test_evidence_id_differs_for_different_sections() -> None:
    assert evidence_id_for_section("K1", "1.0", "S1") != evidence_id_for_section("K1", "1.0", "S2")


@pytest.mark.asyncio
async def test_new_object_inserts_all_sections() -> None:
    repo = FakeIndexRepository()
    provider = FakeEmbeddingProvider()
    stats = await index_knowledge_object(_obj("K1", ["first section", "second section"]), repo, provider)
    # POST-6A added reconciliation counters (/);
    # the pre-existing counters are asserted unchanged.
    assert {k: stats[k] for k in ("inserted_or_updated", "skipped_unchanged", "embedding_failed")} == {
        "inserted_or_updated": 2,
        "skipped_unchanged": 0,
        "embedding_failed": 0,
    }
    assert stats["removed_sections"] == 0
    assert len(repo.rows) == 2


@pytest.mark.asyncio
async def test_batches_all_sections_into_one_embedding_call() -> None:
    """§45 cost control: never one network round trip per section."""
    repo = FakeIndexRepository()
    provider = FakeEmbeddingProvider()
    await index_knowledge_object(_obj("K1", ["a", "b", "c"]), repo, provider)
    assert len(provider.embed_calls) == 1
    assert len(provider.embed_calls[0]) == 3


@pytest.mark.asyncio
async def test_idempotent_rerun_skips_unchanged_content() -> None:
    repo = FakeIndexRepository()
    provider = FakeEmbeddingProvider()
    obj = _obj("K1", ["stable content"])
    await index_knowledge_object(obj, repo, provider)
    stats = await index_knowledge_object(obj, repo, provider)
    assert stats["skipped_unchanged"] == 1
    assert stats["inserted_or_updated"] == 0


@pytest.mark.asyncio
async def test_changed_source_content_is_reindexed() -> None:
    repo = FakeIndexRepository()
    provider = FakeEmbeddingProvider()
    await index_knowledge_object(_obj("K1", ["original content"]), repo, provider)
    stats = await index_knowledge_object(_obj("K1", ["updated content"]), repo, provider)
    assert stats["inserted_or_updated"] == 1
    assert stats["skipped_unchanged"] == 0


@pytest.mark.asyncio
async def test_unchanged_sections_are_not_re_embedded() -> None:
    """Skip-unchanged is checked BEFORE the embedding call, not after --
    proven by asserting the embedding provider is never invoked for
    already-current content."""
    repo = FakeIndexRepository()
    provider = FakeEmbeddingProvider()
    obj = _obj("K1", ["stable content"])
    await index_knowledge_object(obj, repo, provider)
    provider.embed_calls.clear()
    await index_knowledge_object(obj, repo, provider)
    assert provider.embed_calls == []


@pytest.mark.asyncio
async def test_embedding_failure_does_not_corrupt_exact_lexical_indexing() -> None:
    """§42: embedding failure must never delete/block exact+lexical
    capability -- the row is still upserted, just with embedding=None."""
    repo = FakeIndexRepository()
    provider = FakeEmbeddingProvider(should_fail=True)
    stats = await index_knowledge_object(_obj("K1", ["content that fails to embed"]), repo, provider)
    assert stats["embedding_failed"] == 1
    assert stats["inserted_or_updated"] == 1  # still indexed for exact/lexical
    assert repo.upsert_calls[0][1] is None  # embedding=None was passed, never fabricated


@pytest.mark.asyncio
async def test_no_sections_produces_zero_counters_without_error() -> None:
    repo = FakeIndexRepository()
    provider = FakeEmbeddingProvider()
    empty_obj = KnowledgeObject(
        knowledge_id="K1", document_type=KnowledgeDocumentType.MOP, title="K1",
        version=KnowledgeVersion(label="1.0"), lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="x", source_id="K1"), sections=[],
    )
    stats = await index_knowledge_object(empty_obj, repo, provider)
    assert {k: stats[k] for k in ("inserted_or_updated", "skipped_unchanged", "embedding_failed")} == {
        "inserted_or_updated": 0,
        "skipped_unchanged": 0,
        "embedding_failed": 0,
    }
    assert stats["removed_sections"] == 0
    assert provider.embed_calls == []
