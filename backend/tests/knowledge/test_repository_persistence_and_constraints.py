"""Phase 5.1F: persistence across repository instances, idempotent
schema initialization, and the storage-level (not merely
check-then-insert) duplicate-version constraint.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.repository.contracts import KnowledgeVersionAlreadyExistsError
from backend.knowledge.repository.sqlite import SQLiteKnowledgeRepository


def _ko(knowledge_id: str = "k1", label: str = "1.0") -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title="Some document",
        version=KnowledgeVersion(label=label),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
    )


def _file_url(tmp_path: Path) -> str:
    db_path = (tmp_path / "knowledge.db").as_posix()
    return f"sqlite+aiosqlite:///{db_path}"


# --- persistence across instances --------------------------------------


@pytest.mark.asyncio
async def test_persistence_survives_repository_reinstantiation(tmp_path: Path) -> None:
    url = _file_url(tmp_path)
    original = _ko()

    repo1 = SQLiteKnowledgeRepository(url)
    await repo1.add(original)
    await repo1.close()

    repo2 = SQLiteKnowledgeRepository(url)
    restored = await repo2.get("k1", "1.0")
    await repo2.close()

    assert restored == original


@pytest.mark.asyncio
async def test_persistence_is_real_local_durability_not_an_in_memory_fake(tmp_path: Path) -> None:
    """Distinguishing this from an in-memory fake: a THIRD, completely
    independent repository instance, opened after the first two are both
    closed, must still see the data.
    """
    url = _file_url(tmp_path)
    repo1 = SQLiteKnowledgeRepository(url)
    await repo1.add(_ko(label="1.0"))
    await repo1.close()

    repo2 = SQLiteKnowledgeRepository(url)
    await repo2.add(_ko(label="2.0"))
    await repo2.close()

    repo3 = SQLiteKnowledgeRepository(url)
    family = await repo3.list_versions("k1")
    await repo3.close()

    assert {v.version.label for v in family} == {"1.0", "2.0"}


# --- schema initialization ------------------------------------------------


@pytest.mark.asyncio
async def test_schema_initialization_is_idempotent(tmp_path: Path) -> None:
    url = _file_url(tmp_path)
    repo = SQLiteKnowledgeRepository(url)
    await repo.ensure_schema()
    await repo.ensure_schema()  # must not fail merely because the table already exists
    await repo.ensure_schema()
    await repo.add(_ko())  # repository still fully functional afterward
    assert await repo.get("k1", "1.0") is not None
    await repo.close()


@pytest.mark.asyncio
async def test_opening_the_same_database_twice_does_not_fail(tmp_path: Path) -> None:
    url = _file_url(tmp_path)
    repo1 = SQLiteKnowledgeRepository(url)
    await repo1.ensure_schema()
    await repo1.close()

    repo2 = SQLiteKnowledgeRepository(url)
    await repo2.ensure_schema()
    await repo2.add(_ko())
    await repo2.close()


@pytest.mark.asyncio
async def test_schema_initialization_is_not_destructive(tmp_path: Path) -> None:
    """Re-initializing schema on a database that already has data must
    never reset/drop it.
    """
    url = _file_url(tmp_path)
    repo = SQLiteKnowledgeRepository(url)
    await repo.add(_ko())
    await repo.ensure_schema()
    await repo.ensure_schema()
    assert await repo.get("k1", "1.0") is not None
    await repo.close()


# --- duplicate constraint enforced by storage, not just Python -----------


@pytest.mark.asyncio
async def test_duplicate_constraint_is_enforced_by_storage_itself() -> None:
    """Bypasses the repository's own add() entirely and inserts directly
    at the SQLAlchemy layer to prove the composite PRIMARY KEY -- not a
    Python-side pre-check -- is what actually rejects a duplicate.
    """
    from backend.knowledge.repository.sqlite import KnowledgeObjectRecord

    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.ensure_schema()

    async with repo._session_factory() as session:
        session.add(KnowledgeObjectRecord(knowledge_id="k1", version_label="1.0", payload="{}"))
        await session.commit()

    async with repo._session_factory() as session:
        session.add(KnowledgeObjectRecord(knowledge_id="k1", version_label="1.0", payload="{}"))
        with pytest.raises(Exception) as exc_info:
            await session.commit()
        # A raw SQLAlchemy/sqlite3 integrity violation -- proves storage
        # itself rejects it; repo.add() (tested separately) is what
        # translates this into KnowledgeVersionAlreadyExistsError.
        assert "UNIQUE" in str(exc_info.value).upper() or "CONSTRAINT" in str(exc_info.value).upper()

    await repo.close()


@pytest.mark.asyncio
async def test_repository_add_translates_storage_violation_into_domain_error() -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko())
    with pytest.raises(KnowledgeVersionAlreadyExistsError):
        await repo.add(_ko())
    # The repository must remain usable after a caught integrity error --
    # the failed transaction was rolled back cleanly.
    assert await repo.get("k1", "1.0") is not None
    await repo.close()


# --- transactional behavior ------------------------------------------------


@pytest.mark.asyncio
async def test_failed_add_leaves_no_partial_row() -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko())
    try:
        await repo.add(_ko())
    except KnowledgeVersionAlreadyExistsError:
        pass
    # Exactly one row for this identity -- the failed second add() did
    # not leave a partially-written or duplicated row behind.
    async with repo._session_factory() as session:
        result = await session.execute(
            text("SELECT COUNT(*) FROM slopanoc_knowledge_objects WHERE knowledge_id = :k AND version_label = :v"),
            {"k": "k1", "v": "1.0"},
        )
        count = result.scalar_one()
    assert count == 1
    await repo.close()
