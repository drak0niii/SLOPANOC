"""Phase 5.1F: complete version-family retrieval, and composition with
5.1E governance (materialize_candidate/approve_version/
resolve_current_version) WITHOUT moving any governance logic into the
repository.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.governance.contracts import CurrentVersionResolutionStatus
from backend.knowledge.governance.service import approve_version, materialize_candidate
from backend.knowledge.governance.versioning import resolve_current_version
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.processing.processor import HeadingStructureProcessor
from backend.knowledge.repository.sqlite import SQLiteKnowledgeRepository



def _ko(knowledge_id: str = "k1", label: str = "1.0", status: LifecycleStatus = LifecycleStatus.CANDIDATE, supersedes: list[str] | None = None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title="Some document",
        version=KnowledgeVersion(label=label, supersedes=supersedes or []),
        lifecycle_status=status,
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
    )


@pytest_asyncio.fixture
async def repo():
    repository = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    yield repository
    await repository.close()


# --- complete version family ------------------------------------------


@pytest.mark.asyncio
async def test_list_versions_returns_all_lifecycle_states(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko(label="1.0", status=LifecycleStatus.CANDIDATE))
    await repo.add(_ko(label="2.0", status=LifecycleStatus.APPROVED))
    await repo.add(_ko(label="3.0", status=LifecycleStatus.ARCHIVE))

    family = await repo.list_versions("k1")

    labels_and_statuses = {(v.version.label, v.lifecycle_status) for v in family}
    assert labels_and_statuses == {
        ("1.0", LifecycleStatus.CANDIDATE),
        ("2.0", LifecycleStatus.APPROVED),
        ("3.0", LifecycleStatus.ARCHIVE),
    }


@pytest.mark.asyncio
async def test_list_versions_empty_for_unknown_knowledge_id(repo: SQLiteKnowledgeRepository) -> None:
    assert await repo.list_versions("does-not-exist") == []


@pytest.mark.asyncio
async def test_list_versions_only_returns_the_requested_knowledge_id(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko(knowledge_id="k1", label="1.0"))
    await repo.add(_ko(knowledge_id="k2", label="1.0"))
    family = await repo.list_versions("k1")
    assert {v.knowledge_id for v in family} == {"k1"}


@pytest.mark.asyncio
async def test_list_versions_family_composes_with_resolve_current_version(repo: SQLiteKnowledgeRepository) -> None:
    """Proves composition works without any repository-specific
    currentness logic -- the repository only ever returns the raw
    family; 5.1E alone decides what's current.
    """
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    await repo.add(_ko(label="1.0", status=LifecycleStatus.CANDIDATE))
    await repo.add(_ko(label="2.0", status=LifecycleStatus.APPROVED, supersedes=["1.0"]))
    await repo.add(_ko(label="3.0", status=LifecycleStatus.ARCHIVE))

    family = await repo.list_versions("k1")
    resolution = resolve_current_version(family, now)

    assert resolution.status is CurrentVersionResolutionStatus.RESOLVED
    assert resolution.current.version.label == "2.0"


@pytest.mark.asyncio
async def test_repository_never_moves_the_resolver_inside_itself() -> None:
    """Static proof, complementing the composition test above: neither
    contracts.py nor sqlite.py ever imports or calls
    resolve_current_version/resolve_supersession_chain.
    """
    import ast
    from pathlib import Path

    repository_dir = Path(__file__).resolve().parents[2] / "knowledge" / "repository"
    forbidden_names = {"resolve_current_version", "resolve_supersession_chain"}
    for path in sorted(repository_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in forbidden_names:
                pytest.fail(f"{path.name} references {node.id} -- currentness must stay in governance/")
            if isinstance(node, ast.Attribute) and node.attr in forbidden_names:
                pytest.fail(f"{path.name} references {node.attr} -- currentness must stay in governance/")


# --- end-to-end governance composition -----------------------------------


@pytest.mark.asyncio
async def test_end_to_end_ingestion_through_repository_approval(repo: SQLiteKnowledgeRepository) -> None:
    """StructuredKnowledgeDocument -> materialize_candidate() ->
    repository.add() -> repository.get() -> approve_version() ->
    repository.replace() -> repository.get() -> APPROVED.
    """
    ingested = IngestedKnowledgeDocument(
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
        title="Upgrade MOP",
        content="# Heading\nBody text.",
    )
    structured = HeadingStructureProcessor().process(ingested)
    candidate = materialize_candidate(
        structured, knowledge_id="k1", document_type=KnowledgeDocumentType.MOP, version=KnowledgeVersion(label="1.0")
    )
    assert candidate.lifecycle_status is LifecycleStatus.CANDIDATE

    await repo.add(candidate)
    stored_candidate = await repo.get("k1", "1.0")
    assert stored_candidate.lifecycle_status is LifecycleStatus.CANDIDATE

    approved = approve_version(stored_candidate)
    await repo.replace(approved)

    final = await repo.get("k1", "1.0")
    assert final.lifecycle_status is LifecycleStatus.APPROVED
    assert final.sections[0].content == "Body text."
