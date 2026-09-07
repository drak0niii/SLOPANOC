"""Correction pass (post-5.1F): `KnowledgeRepository.list_all()` --
source-of-truth corpus enumeration, so a future derived retrieval/search
index (5.1G) can be built/rebuilt from the authoritative repository
without importing SQLiteKnowledgeRepository or knowing its schema. Not
search, not filtering, not ranking, not currentness resolution.
"""
from __future__ import annotations

import inspect

import pytest
import pytest_asyncio

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.repository.contracts import KnowledgeRepository, KnowledgeRepositoryCorruptionError
from backend.knowledge.repository.sqlite import KnowledgeObjectRecord, SQLiteKnowledgeRepository


def _ko(
    knowledge_id: str,
    label: str,
    status: LifecycleStatus,
    document_type: KnowledgeDocumentType = KnowledgeDocumentType.MOP,
    source_system: str = "source_a",
    applicability: Applicability | None = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=document_type,
        title="Some document",
        version=KnowledgeVersion(label=label),
        lifecycle_status=status,
        source=KnowledgeSource(source_system=source_system, source_id="doc-1"),
        applicability=applicability or Applicability(),
    )


@pytest_asyncio.fixture
async def repo():
    repository = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    yield repository
    await repository.close()


# --- A: multiple knowledge_ids and versions -------------------------------


@pytest.mark.asyncio
async def test_a_multiple_knowledge_ids_and_versions_all_returned(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko("knowledge-A", "Rev-A", LifecycleStatus.APPROVED))
    await repo.add(_ko("knowledge-A", "Rev-B", LifecycleStatus.CANDIDATE))
    await repo.add(_ko("knowledge-B", "2026-08", LifecycleStatus.ARCHIVE))
    await repo.add(_ko("knowledge-C", "arbitrary-version", LifecycleStatus.APPROVED))

    corpus = await repo.list_all()

    identities = {(obj.knowledge_id, obj.version.label) for obj in corpus}
    assert identities == {
        ("knowledge-A", "Rev-A"),
        ("knowledge-A", "Rev-B"),
        ("knowledge-B", "2026-08"),
        ("knowledge-C", "arbitrary-version"),
    }


@pytest.mark.asyncio
async def test_a_empty_repository_returns_empty_list(repo: SQLiteKnowledgeRepository) -> None:
    assert await repo.list_all() == []


# --- B: all lifecycle states retained --------------------------------------


@pytest.mark.asyncio
async def test_b_all_lifecycle_states_retained(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko("k1", "1.0", LifecycleStatus.CANDIDATE))
    await repo.add(_ko("k1", "2.0", LifecycleStatus.APPROVED))
    await repo.add(_ko("k1", "3.0", LifecycleStatus.ARCHIVE))

    corpus = await repo.list_all()

    statuses = {obj.lifecycle_status for obj in corpus}
    assert statuses == {LifecycleStatus.CANDIDATE, LifecycleStatus.APPROVED, LifecycleStatus.ARCHIVE}


@pytest.mark.asyncio
async def test_b_list_all_performs_no_lifecycle_filtering() -> None:
    """Static proof, complementing the runtime test above:
    list_all's own implementation never references LifecycleStatus.
    """
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "knowledge" / "repository" / "sqlite.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "list_all":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and inner.attr == "lifecycle_status":
                    pytest.fail("list_all references lifecycle_status -- it must perform no lifecycle filtering")
                if isinstance(inner, ast.Name) and inner.id == "LifecycleStatus":
                    pytest.fail("list_all references LifecycleStatus -- it must perform no lifecycle filtering")


# --- C: arbitrary document types --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document_type", [KnowledgeDocumentType.MOP, KnowledgeDocumentType.SOP, KnowledgeDocumentType.RCA, KnowledgeDocumentType.OTHER]
)
async def test_c_arbitrary_document_types_behave_identically(repo: SQLiteKnowledgeRepository, document_type: KnowledgeDocumentType) -> None:
    await repo.add(_ko(f"k-{document_type.value}", "1.0", LifecycleStatus.APPROVED, document_type=document_type))
    corpus = await repo.list_all()
    assert any(obj.document_type is document_type for obj in corpus)


# --- D: arbitrary source_system and applicability dimensions --------------


@pytest.mark.asyncio
async def test_d_arbitrary_source_system_and_applicability_round_trip(repo: SQLiteKnowledgeRepository) -> None:
    applicability = Applicability(dimensions={"hardware_family": ["AIR3268"], "some_future_dimension": ["x"]})
    await repo.add(_ko("k1", "1.0", LifecycleStatus.APPROVED, source_system="future_system_2030", applicability=applicability))

    corpus = await repo.list_all()

    assert len(corpus) == 1
    assert corpus[0].source.source_system == "future_system_2030"
    assert corpus[0].applicability == applicability


# --- E: corrupt row fails closed --------------------------------------------


@pytest.mark.asyncio
async def test_e_corrupt_row_fails_closed(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko("k1", "1.0", LifecycleStatus.APPROVED))
    await repo.add(_ko("k2", "1.0", LifecycleStatus.APPROVED))

    async with repo._session_factory() as session:
        record = await session.get(KnowledgeObjectRecord, ("k2", "1.0"))
        record.payload = "not valid json {{{"
        await session.commit()

    with pytest.raises(KnowledgeRepositoryCorruptionError):
        await repo.list_all()


@pytest.mark.asyncio
async def test_e_corruption_never_returns_a_partial_corpus() -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko("k1", "1.0", LifecycleStatus.APPROVED))
    async with repo._session_factory() as session:
        record = await session.get(KnowledgeObjectRecord, ("k1", "1.0"))
        record.payload = "{}"
        await session.commit()

    try:
        result = await repo.list_all()
        pytest.fail(f"expected KnowledgeRepositoryCorruptionError, got a return value: {result!r}")
    except KnowledgeRepositoryCorruptionError:
        pass
    await repo.close()


# --- F: deterministic ordering, no governance meaning ------------------


@pytest.mark.asyncio
async def test_f_ordering_is_deterministic_and_repeatable(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_ko("knowledge-C", "1.0", LifecycleStatus.APPROVED))
    await repo.add(_ko("knowledge-A", "1.0", LifecycleStatus.APPROVED))
    await repo.add(_ko("knowledge-B", "1.0", LifecycleStatus.APPROVED))

    first = await repo.list_all()
    second = await repo.list_all()

    assert [(o.knowledge_id, o.version.label) for o in first] == [(o.knowledge_id, o.version.label) for o in second]


@pytest.mark.asyncio
async def test_f_ordering_does_not_reflect_currentness_or_precedence() -> None:
    """A CANDIDATE inserted with an alphabetically-earlier identity must
    not be mistaken for "more current" than an APPROVED one inserted
    later with a later identity -- ordering is storage presentation
    only.
    """
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(_ko("k-a-candidate", "1.0", LifecycleStatus.CANDIDATE))
    await repo.add(_ko("k-z-approved", "1.0", LifecycleStatus.APPROVED))

    corpus = await repo.list_all()

    # The CANDIDATE sorts first by identity, but that carries no
    # governance meaning whatsoever -- both entries are returned as
    # plain, unranked, unfiltered corpus members.
    assert corpus[0].lifecycle_status is LifecycleStatus.CANDIDATE
    assert corpus[1].lifecycle_status is LifecycleStatus.APPROVED
    await repo.close()


# --- G: no search/retrieval/ranking/currentness on the contract -----------


def test_g_protocol_still_exposes_no_search_ranking_or_currentness_operation() -> None:
    method_names = {name for name, _ in inspect.getmembers(KnowledgeRepository) if not name.startswith("_")}
    forbidden = {
        "search",
        "semantic_search",
        "keyword_search",
        "find_relevant",
        "rank",
        "top_k",
        "get_current_version",
        "resolve_current",
        "latest_version",
        "delete",
        "purge",
    }
    assert method_names.isdisjoint(forbidden)


def test_g_list_all_accepts_no_query_or_filter_arguments() -> None:
    signature = inspect.signature(KnowledgeRepository.list_all)
    assert list(signature.parameters) == ["self"]


def test_g_protocol_exposes_exactly_five_operations_now() -> None:
    method_names = {name for name, _ in inspect.getmembers(KnowledgeRepository) if not name.startswith("_")}
    assert method_names == {"add", "get", "replace", "list_versions", "list_all"}


# --- H: SQLiteKnowledgeRepository still structurally satisfies the Protocol -


def test_h_sqlite_implementation_exposes_list_all_with_matching_shape() -> None:
    assert hasattr(SQLiteKnowledgeRepository, "list_all")
    assert inspect.iscoroutinefunction(SQLiteKnowledgeRepository.list_all)
    signature = inspect.signature(SQLiteKnowledgeRepository.list_all)
    assert list(signature.parameters) == ["self"]


def test_h_sqlite_implementation_still_satisfies_every_protocol_method() -> None:
    for method_name in ("add", "get", "replace", "list_versions", "list_all"):
        assert hasattr(SQLiteKnowledgeRepository, method_name)
        assert inspect.iscoroutinefunction(getattr(SQLiteKnowledgeRepository, method_name))
