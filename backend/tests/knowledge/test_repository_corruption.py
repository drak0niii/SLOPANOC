"""Phase 5.1F: corrupted stored data must fail closed -- never a
partial object, never dropped fields, never a fabricated default, never
a raw dict.
"""
from __future__ import annotations

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.repository.contracts import KnowledgeRepositoryCorruptionError
from backend.knowledge.repository.sqlite import KnowledgeObjectRecord, SQLiteKnowledgeRepository


def _ko(knowledge_id: str = "k1", label: str = "1.0") -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title="Some document",
        version=KnowledgeVersion(label=label),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
    )


async def _corrupt_payload(repo: SQLiteKnowledgeRepository, knowledge_id: str, version_label: str, payload: str) -> None:
    async with repo._session_factory() as session:
        record = await session.get(KnowledgeObjectRecord, (knowledge_id, version_label))
        record.payload = payload
        await session.commit()


@pytest.fixture
def repo_url() -> str:
    return "sqlite+aiosqlite:///:memory:"


@pytest.mark.asyncio
async def test_invalid_json_payload_fails_closed() -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko())
    await _corrupt_payload(repo, "k1", "1.0", "not valid json {{{")

    with pytest.raises(KnowledgeRepositoryCorruptionError):
        await repo.get("k1", "1.0")

    await repo.close()


@pytest.mark.asyncio
async def test_valid_json_but_invalid_knowledge_object_fails_closed() -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko())
    await _corrupt_payload(repo, "k1", "1.0", '{"this": "is valid json but not a KnowledgeObject"}')

    with pytest.raises(KnowledgeRepositoryCorruptionError):
        await repo.get("k1", "1.0")

    await repo.close()


@pytest.mark.asyncio
async def test_payload_knowledge_id_mismatch_vs_storage_key_fails_closed() -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko(knowledge_id="k1"))
    mismatched = _ko(knowledge_id="SOME-OTHER-ID")
    await _corrupt_payload(repo, "k1", "1.0", mismatched.model_dump_json())

    with pytest.raises(KnowledgeRepositoryCorruptionError):
        await repo.get("k1", "1.0")

    await repo.close()


@pytest.mark.asyncio
async def test_payload_version_label_mismatch_vs_storage_key_fails_closed() -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko(label="1.0"))
    mismatched = _ko(label="SOME-OTHER-LABEL")
    await _corrupt_payload(repo, "k1", "1.0", mismatched.model_dump_json())

    with pytest.raises(KnowledgeRepositoryCorruptionError):
        await repo.get("k1", "1.0")

    await repo.close()


@pytest.mark.asyncio
async def test_corruption_never_returns_a_partial_object_or_raw_dict() -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko())
    await _corrupt_payload(repo, "k1", "1.0", "{}")

    try:
        result = await repo.get("k1", "1.0")
        pytest.fail(f"expected KnowledgeRepositoryCorruptionError, got a return value: {result!r}")
    except KnowledgeRepositoryCorruptionError:
        pass

    await repo.close()


@pytest.mark.asyncio
async def test_corruption_in_list_versions_also_fails_closed() -> None:
    """The same fail-closed behavior applies to list_versions, not just
    get -- a corrupted family member must not be silently skipped or
    returned as a partial/raw value.
    """
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko(label="1.0"))
    await repo.add(_ko(label="2.0"))
    await _corrupt_payload(repo, "k1", "2.0", "not valid json {{{")

    with pytest.raises(KnowledgeRepositoryCorruptionError):
        await repo.list_versions("k1")

    await repo.close()
