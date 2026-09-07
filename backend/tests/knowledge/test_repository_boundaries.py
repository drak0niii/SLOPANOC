"""Phase 5.1F: the repository's boundaries -- no delete, no currentness
resolution, no search/ranking, no vector index, no applicability
evaluation, and a storage-agnostic `contracts.py`. Structural (AST/
Protocol-introspection) checks, not naming-only string matches.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from backend.knowledge.repository import contracts as repository_contracts
from backend.knowledge.repository.contracts import KnowledgeRepository
from backend.knowledge.repository.sqlite import SQLiteKnowledgeRepository

_REPOSITORY_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "repository"


def _protocol_method_names() -> set[str]:
    return {name for name, _ in inspect.getmembers(KnowledgeRepository) if not name.startswith("_")}


def _sqlite_public_method_names() -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(SQLiteKnowledgeRepository)
        if not name.startswith("_") and inspect.iscoroutinefunction(member)
    }


# --- no delete -------------------------------------------------------------


def test_repository_protocol_exposes_no_delete_operation() -> None:
    forbidden = {"delete", "purge", "remove", "remove_version", "delete_version", "hard_delete"}
    assert _protocol_method_names().isdisjoint(forbidden)


def test_sqlite_implementation_exposes_no_delete_operation() -> None:
    forbidden = {"delete", "purge", "remove", "remove_version", "delete_version", "hard_delete"}
    assert _sqlite_public_method_names().isdisjoint(forbidden)


# --- no currentness shortcuts -----------------------------------------


def test_repository_protocol_exposes_no_currentness_shortcut() -> None:
    forbidden = {"get_current_version", "resolve_current", "latest_version", "current_version", "resolve_current_version"}
    assert _protocol_method_names().isdisjoint(forbidden)


def test_sqlite_implementation_exposes_no_currentness_shortcut() -> None:
    forbidden = {"get_current_version", "resolve_current", "latest_version", "current_version", "resolve_current_version"}
    assert _sqlite_public_method_names().isdisjoint(forbidden)


def test_no_lifecycle_transition_methods_exist() -> None:
    """§9: no approve()/archive()/publish()/make_current() -- the
    repository must not duplicate governance/service.py.
    """
    forbidden = {"approve", "archive", "publish", "make_current", "approve_version", "archive_version"}
    assert _protocol_method_names().isdisjoint(forbidden)
    assert _sqlite_public_method_names().isdisjoint(forbidden)


# --- no search / ranking ------------------------------------------------


def test_repository_protocol_exposes_no_search_or_ranking() -> None:
    forbidden = {"search", "semantic_search", "keyword_search", "find_relevant", "rank", "top_k", "query_by_text", "knowledge_search"}
    assert _protocol_method_names().isdisjoint(forbidden)


def test_sqlite_implementation_exposes_no_search_or_ranking() -> None:
    forbidden = {"search", "semantic_search", "keyword_search", "find_relevant", "rank", "top_k", "query_by_text", "knowledge_search"}
    assert _sqlite_public_method_names().isdisjoint(forbidden)


def test_repository_protocol_has_exactly_the_five_intended_operations() -> None:
    """add/get/replace/list_versions (5.1F) plus list_all (correction
    pass -- source-of-truth corpus enumeration, see
    test_repository_list_all.py) -- never a sixth, search-shaped
    operation.
    """
    assert _protocol_method_names() == {"add", "get", "replace", "list_versions", "list_all"}


# --- no vector/embedding index -------------------------------------------


def test_no_vector_or_embedding_dependency_in_repository_package() -> None:
    forbidden_import_prefixes = ("faiss", "chromadb", "chroma", "pgvector", "pinecone", "weaviate")
    violations: list[tuple[str, str]] = []
    for path in sorted(_REPOSITORY_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in forbidden_import_prefixes:
                    if name == forbidden or name.startswith(forbidden + "."):
                        violations.append((path.name, name))
    assert violations == [], f"vector/embedding dependency found: {violations}"


def test_no_embedding_generation_function_defined() -> None:
    forbidden_name_fragments = ("embed", "vector_search", "generate_embedding")
    violations: list[tuple[str, str]] = []
    for path in sorted(_REPOSITORY_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                lowered = node.name.lower()
                for fragment in forbidden_name_fragments:
                    if fragment in lowered:
                        violations.append((path.name, node.name))
    assert violations == [], f"embedding-shaped declaration found: {violations}"


# --- no applicability evaluation inside repository -----------------------


def test_repository_never_calls_evaluate_applicability() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(_REPOSITORY_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
                if name == "evaluate_applicability":
                    violations.append((path.name, "evaluate_applicability call"))
    assert violations == [], f"repository must never evaluate applicability: {violations}"


# --- contract is storage-agnostic ------------------------------------------


def test_contracts_module_imports_no_storage_technology() -> None:
    forbidden_prefixes = ("sqlite3", "sqlalchemy", "aiosqlite", "asyncpg", "psycopg", "google.cloud")
    tree = ast.parse((_REPOSITORY_DIR / "contracts.py").read_text(encoding="utf-8"), filename="contracts.py")
    violations: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            for forbidden in forbidden_prefixes:
                if name == forbidden or name.startswith(forbidden + "."):
                    violations.append(name)
    assert violations == [], f"contracts.py must remain storage-agnostic: {violations}"


def test_contracts_module_source_has_no_sql_keywords() -> None:
    """A lightweight extra check: the contract file's own source should
    never contain a raw SQL statement fragment.
    """
    text = (_REPOSITORY_DIR / "contracts.py").read_text(encoding="utf-8")
    for keyword in ("SELECT ", "INSERT INTO", "CREATE TABLE", "PRIMARY KEY"):
        assert keyword not in text.upper()


def test_sqlite_implementation_satisfies_the_protocol_structurally() -> None:
    """SQLiteKnowledgeRepository is never required to subclass
    KnowledgeRepository (Protocol is structural) -- confirm it exposes
    every required method with a matching async shape.
    """
    for method_name in ("add", "get", "replace", "list_versions", "list_all"):
        assert hasattr(SQLiteKnowledgeRepository, method_name)
        assert inspect.iscoroutinefunction(getattr(SQLiteKnowledgeRepository, method_name))
