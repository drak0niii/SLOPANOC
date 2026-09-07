"""Phase 5.1F: basic add/get/replace CRUD semantics."""
from __future__ import annotations

import pytest
import pytest_asyncio

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.repository.contracts import KnowledgeVersionAlreadyExistsError, KnowledgeVersionNotFoundError
from backend.knowledge.repository.sqlite import SQLiteKnowledgeRepository



def _ko(knowledge_id: str = "k1", label: str = "1.0", status: LifecycleStatus = LifecycleStatus.CANDIDATE) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title="Some document",
        version=KnowledgeVersion(label=label),
        lifecycle_status=status,
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
    )


@pytest_asyncio.fixture
async def repo():
    repository = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    yield repository
    await repository.close()


# --- ADD -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_knowledge_object_can_be_added(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko())
    assert await repo.get("k1", "1.0") is not None


@pytest.mark.asyncio
async def test_same_exact_version_cannot_be_added_twice(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko())
    with pytest.raises(KnowledgeVersionAlreadyExistsError):
        await repo.add(_ko())


@pytest.mark.asyncio
async def test_another_version_of_same_knowledge_id_can_be_added(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko(label="1.0"))
    await repo.add(_ko(label="2.0"))
    assert await repo.get("k1", "1.0") is not None
    assert await repo.get("k1", "2.0") is not None


@pytest.mark.asyncio
async def test_same_version_label_under_another_knowledge_id_can_be_added(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko(knowledge_id="k1", label="1.0"))
    await repo.add(_ko(knowledge_id="k2", label="1.0"))
    assert await repo.get("k1", "1.0") is not None
    assert await repo.get("k2", "1.0") is not None


# --- GET -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_exact_version_returns_equal_object(repo: SQLiteKnowledgeRepository) -> None:
    original = _ko()
    await repo.add(original)
    result = await repo.get("k1", "1.0")
    assert result == original


@pytest.mark.asyncio
async def test_get_missing_version_returns_none(repo: SQLiteKnowledgeRepository) -> None:
    assert await repo.get("k1", "1.0") is None


@pytest.mark.asyncio
async def test_get_missing_knowledge_id_returns_none(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko(knowledge_id="k1"))
    assert await repo.get("does-not-exist", "1.0") is None


@pytest.mark.asyncio
async def test_get_never_falls_back_to_another_version(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko(label="1.0"))
    await repo.add(_ko(label="2.0"))
    assert await repo.get("k1", "9.9") is None  # not 1.0, not 2.0, not any fallback


# --- REPLACE ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_replace_existing_exact_version(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko(status=LifecycleStatus.CANDIDATE))
    approved = _ko(status=LifecycleStatus.APPROVED)
    await repo.replace(approved)
    result = await repo.get("k1", "1.0")
    assert result.lifecycle_status is LifecycleStatus.APPROVED


@pytest.mark.asyncio
async def test_replace_missing_exact_version_fails(repo: SQLiteKnowledgeRepository) -> None:
    with pytest.raises(KnowledgeVersionNotFoundError):
        await repo.replace(_ko())


@pytest.mark.asyncio
async def test_replace_does_not_create(repo: SQLiteKnowledgeRepository) -> None:
    with pytest.raises(KnowledgeVersionNotFoundError):
        await repo.replace(_ko())
    assert await repo.get("k1", "1.0") is None


@pytest.mark.asyncio
async def test_replacement_round_trip_returns_updated_object(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko(status=LifecycleStatus.CANDIDATE))
    approved = _ko(status=LifecycleStatus.APPROVED)
    await repo.replace(approved)
    result = await repo.get("k1", "1.0")
    assert result == approved
