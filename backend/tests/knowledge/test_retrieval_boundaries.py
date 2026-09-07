"""Phase 5.1G: structural/AST-based genericity and boundary checks for
`backend/knowledge/retrieval/` -- complementing
test_dependency_boundary.py (which covers forbidden imports). These
checks prove absence of hardcoded business/domain vocabulary, absence of
wall-clock calls, absence of write access to the repository, and that
`KnowledgeRetrievalService` depends only on the `KnowledgeRepository`
Protocol (never `SQLiteKnowledgeRepository` or another storage detail).
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.repository.contracts import KnowledgeRepository
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery
from backend.knowledge.retrieval.service import KnowledgeRetrievalService

_RETRIEVAL_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "retrieval"
_SERVICE_FILE = _RETRIEVAL_DIR / "service.py"
_SCORING_FILE = _RETRIEVAL_DIR / "scoring.py"

_AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _module_ast(path: Path) -> ast.Module:
    return ast.parse(_module_source(path), filename=str(path))


def _non_docstring_string_constants(tree: ast.Module) -> list[str]:
    """Every string literal used as an actual runtime value -- excluding
    module/class/function DOCSTRINGS, which legitimately use illustrative
    business-domain examples (e.g. "Ericsson"/"5G") purely to explain
    behavior, never as a runtime constraint. Mirrors this test suite's
    established discipline (see test_ingestion_source_agnostic.py) of
    checking real AST structure rather than raw text, so explanatory
    prose is never mistaken for a hardcoded vocabulary.
    """
    docstring_node_ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docstring_node_ids.add(id(body[0].value))

    values: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_node_ids:
            values.append(node.value)
    return values


# --- no hardcoded business/domain vocabulary -----------------------------------


_FORBIDDEN_HARDCODED_STRINGS = (
    "incident",
    "outage",
    "5g",
    "ericsson",
    "sharepoint",
    "runbook",
    "troubleshoot",
)


def test_service_module_has_no_hardcoded_document_type_or_business_vocabulary() -> None:
    """AST-based (not raw text search, which would false-positive against
    explanatory docstrings/comments) -- checks only actual string
    LITERAL nodes used as runtime values, never docstrings.
    """
    violations: list[str] = []
    for value in _non_docstring_string_constants(_module_ast(_SERVICE_FILE)):
        lowered = value.lower()
        for forbidden in _FORBIDDEN_HARDCODED_STRINGS:
            if forbidden in lowered:
                violations.append(value)
    assert violations == [], f"service.py contains hardcoded business vocabulary: {violations}"


def test_scoring_module_has_no_synonym_table_or_domain_vocabulary() -> None:
    violations: list[str] = []
    for value in _non_docstring_string_constants(_module_ast(_SCORING_FILE)):
        lowered = value.lower()
        for forbidden in _FORBIDDEN_HARDCODED_STRINGS:
            if forbidden in lowered:
                violations.append(value)
    assert violations == [], f"scoring.py contains hardcoded business/domain vocabulary: {violations}"


# --- no wall-clock calls --------------------------------------------------------


def _calls_wall_clock(tree: ast.Module) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("now", "utcnow") and isinstance(node.func.value, ast.Name) and node.func.value.id == "datetime":
                violations.append(f"datetime.{node.func.attr}()")
            if node.func.attr == "time" and isinstance(node.func.value, ast.Name) and node.func.value.id == "time":
                violations.append("time.time()")
    return violations


def test_service_module_never_calls_the_wall_clock() -> None:
    violations = _calls_wall_clock(_module_ast(_SERVICE_FILE))
    assert violations == [], f"service.py must never call the wall clock -- as_of is always explicit: {violations}"


def test_scoring_module_never_calls_the_wall_clock() -> None:
    violations = _calls_wall_clock(_module_ast(_SCORING_FILE))
    assert violations == [], f"scoring.py must never call the wall clock: {violations}"


# --- read-only repository access ------------------------------------------------


def test_service_module_never_calls_repository_add_or_replace() -> None:
    tree = _module_ast(_SERVICE_FILE)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("add", "replace"):
            violations.append(node.attr)
    assert violations == [], f"service.py must be read-only -- found calls to: {violations}"


@pytest.mark.asyncio
async def test_service_only_ever_calls_list_all_at_runtime() -> None:
    """Runtime proof, complementing the static AST check: a fake
    repository that raises on every method except `list_all` must not
    raise when used through a full retrieve() call.
    """

    class _StrictRepository:
        async def add(self, knowledge_object: KnowledgeObject) -> None:
            raise AssertionError("must not be called")

        async def get(self, knowledge_id: str, version_label: str):
            raise AssertionError("must not be called")

        async def replace(self, knowledge_object: KnowledgeObject) -> None:
            raise AssertionError("must not be called")

        async def list_versions(self, knowledge_id: str):
            raise AssertionError("must not be called")

        async def list_all(self) -> list[KnowledgeObject]:
            return [
                KnowledgeObject(
                    knowledge_id="k1",
                    document_type=KnowledgeDocumentType.SOP,
                    title="router outage guide",
                    version=KnowledgeVersion(label="v1"),
                    lifecycle_status=LifecycleStatus.APPROVED,
                    source=KnowledgeSource(source_system="test", source_id="k1"),
                    sections=[KnowledgeSection(section_id="k1:v1:s0", knowledge_id="k1", sequence=0, content="router outage")],
                )
            ]

    service = KnowledgeRetrievalService(_StrictRepository())
    query = KnowledgeRetrievalQuery(query_text="router outage", as_of=_AS_OF, limit=10)
    result = await service.retrieve(query)
    assert len(result.items) == 1


# --- depends on the Protocol, never the concrete SQLite implementation --------


def test_service_module_never_imports_sqlite_repository() -> None:
    tree = _module_ast(_SERVICE_FILE)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "sqlite" not in node.module, f"service.py must not import a concrete repository: {node.module}"
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "sqlite" not in alias.name, f"service.py must not import a concrete repository: {alias.name}"


def test_service_constructor_accepts_the_repository_protocol_type() -> None:
    signature = inspect.signature(KnowledgeRetrievalService.__init__)
    annotation = signature.parameters["repository"].annotation
    assert annotation in (KnowledgeRepository, "KnowledgeRepository")


def test_service_never_calls_ensure_schema_or_other_sqlite_specific_methods() -> None:
    tree = _module_ast(_SERVICE_FILE)
    forbidden_attrs = {"ensure_schema", "close", "_session_factory", "_engine"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
            violations.append(node.attr)
    assert violations == [], f"service.py must depend only on the KnowledgeRepository Protocol surface: {violations}"


# --- retrieval never mutates a KnowledgeObject or a KnowledgeSection -----------


@pytest.mark.asyncio
async def test_retrieve_does_not_mutate_the_source_knowledge_objects() -> None:
    section = KnowledgeSection(section_id="k1:v1:s0", knowledge_id="k1", sequence=0, content="router outage")
    obj = KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title="router outage guide",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="k1"),
        sections=[section],
    )
    before = obj.model_copy(deep=True)

    class _Repo:
        async def add(self, knowledge_object): raise AssertionError
        async def get(self, knowledge_id, version_label): raise AssertionError
        async def replace(self, knowledge_object): raise AssertionError
        async def list_versions(self, knowledge_id): raise AssertionError
        async def list_all(self) -> list[KnowledgeObject]:
            return [obj]

    service = KnowledgeRetrievalService(_Repo())
    query = KnowledgeRetrievalQuery(query_text="router outage", as_of=_AS_OF, limit=10)
    await service.retrieve(query)

    assert obj == before


# --- KnowledgeRetrievalService structural shape --------------------------------


def test_retrieve_method_is_async() -> None:
    assert inspect.iscoroutinefunction(KnowledgeRetrievalService.retrieve)


def test_retrieve_accepts_exactly_one_query_argument() -> None:
    signature = inspect.signature(KnowledgeRetrievalService.retrieve)
    assert list(signature.parameters) == ["self", "query"]


def test_service_scorer_defaults_to_token_overlap_scorer() -> None:
    from backend.knowledge.repository.contracts import KnowledgeRepository as _Protocol
    from backend.knowledge.retrieval.scoring import TokenOverlapRelevanceScorer

    class _EmptyRepo:
        async def add(self, knowledge_object): raise AssertionError
        async def get(self, knowledge_id, version_label): raise AssertionError
        async def replace(self, knowledge_object): raise AssertionError
        async def list_versions(self, knowledge_id): raise AssertionError
        async def list_all(self):
            return []

    service = KnowledgeRetrievalService(_EmptyRepo())
    assert isinstance(service._scorer, TokenOverlapRelevanceScorer)
