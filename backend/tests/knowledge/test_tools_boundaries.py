"""Phase 5.1I: structural/AST genericity and boundary checks for
`backend/knowledge/tools/` -- complementing test_dependency_boundary.py
(forbidden imports) and test_tools_service.py (behavioral tests). Proves
no hardcoded business/document-type vocabulary, no duplicated
currentness/applicability/scoring/provenance logic, no global evidence
registry, no ADK/Gemini/agent/Teams dependency, read-only repository
access, and source-system/document-type independence.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.knowledge.domain.applicability import ApplicabilityContext
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.provenance.service import KnowledgeProvenanceService
from backend.knowledge.repository.sqlite import SQLiteKnowledgeRepository
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.knowledge.tools.contracts import KnowledgeSearchToolRequest, KnowledgeToolExecutionContext
from backend.knowledge.tools.service import KnowledgeToolService

_TOOLS_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "tools"
_SERVICE_FILE = _TOOLS_DIR / "service.py"
_CONTRACTS_FILE = _TOOLS_DIR / "contracts.py"

_AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _governed(knowledge_id: str, document_type: KnowledgeDocumentType, source_system: str) -> KnowledgeObject:
    section = KnowledgeSection(section_id=f"{knowledge_id}:v1:s0", knowledge_id=knowledge_id, sequence=0, content="router outage recovery")
    return KnowledgeObject(
        knowledge_id=knowledge_id, document_type=document_type, title=f"{knowledge_id} guide",
        version=KnowledgeVersion(label="v1", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system=source_system, source_id="doc-1"),
        sections=[section],
    )


# --- no hardcoded business/document-type vocabulary ----------------------------


def _non_docstring_string_constants(tree: ast.Module) -> list[str]:
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


_FORBIDDEN_HARDCODED_STRINGS = ("mop", "sop", "rca", "kb_article", "outage", "sharepoint", "ericsson", "runbook", "quantumbanana")


@pytest.mark.parametrize("path", [_SERVICE_FILE, _CONTRACTS_FILE])
def test_no_hardcoded_business_or_document_type_vocabulary(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for value in _non_docstring_string_constants(tree):
        lowered = value.lower()
        for forbidden in _FORBIDDEN_HARDCODED_STRINGS:
            if forbidden in lowered:
                violations.append(value)
    assert violations == [], f"{path.name} contains hardcoded business/document-type vocabulary: {violations}"


def test_no_specific_document_type_member_referenced() -> None:
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "KnowledgeDocumentType":
            violations.append(node.attr)
    assert violations == [], f"service.py must not reference a specific KnowledgeDocumentType member: {violations}"


# --- no duplicated currentness/applicability/scoring/provenance logic --------


def test_service_module_never_imports_currentness_applicability_scoring_or_provenance_construction() -> None:
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)

    forbidden = {
        "backend.knowledge.governance.versioning",
        "resolve_current_version",
        "backend.knowledge.domain.applicability",
        "evaluate_applicability",
        "backend.knowledge.retrieval.scoring",
        "KnowledgeRelevanceScorer",
        "TokenOverlapRelevanceScorer",
        "KnowledgeEvidenceReference",
    }
    assert imported_names.isdisjoint(forbidden), f"service.py must not duplicate KM logic: {imported_names & forbidden}"


def test_service_module_never_constructs_evidence_types_directly() -> None:
    """5.1I transforms already-verified `KnowledgeEvidenceItem`s into a
    model-facing view -- it must never construct `KnowledgeEvidenceItem`/
    `KnowledgeEvidenceReference`/`KnowledgeEvidenceSet` itself (that is
    exclusively `KnowledgeProvenanceService.build_evidence_set`'s job).
    """
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    forbidden_calls = {"KnowledgeEvidenceItem", "KnowledgeEvidenceReference", "KnowledgeEvidenceSet"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
            violations.append(node.func.id)
    assert violations == [], f"service.py must not construct evidence types directly: {violations}"


# --- no wall-clock, no persistence, no global registry -------------------------


def test_no_tools_module_calls_the_wall_clock() -> None:
    for path in (_SERVICE_FILE, _CONTRACTS_FILE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not (
                    node.func.attr in ("now", "utcnow")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "datetime"
                ), f"{path.name} must never call the wall clock"


def test_no_global_evidence_registry_or_contextvar() -> None:
    for path in (_SERVICE_FILE, _CONTRACTS_FILE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "ContextVar":
                pytest.fail(f"{path.name} must not use ContextVar as an evidence mailbox")
            if isinstance(node, ast.ImportFrom) and node.module == "contextvars":
                pytest.fail(f"{path.name} must not import contextvars")

        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value, (ast.List, ast.Dict)):
                        if isinstance(node.value, ast.List) and len(node.value.elts) == 0:
                            pytest.fail(f"module-level mutable empty list {target.id!r} in {path.name} looks like a hidden registry")
                        if isinstance(node.value, ast.Dict) and len(node.value.keys) == 0:
                            pytest.fail(f"module-level mutable empty dict {target.id!r} in {path.name} looks like a hidden registry")


def test_no_persistence_calls_in_tools() -> None:
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    forbidden_names = {"open", "pickle", "shelve", "sqlite3"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden_names:
            violations.append(node.id)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden_names:
                    violations.append(alias.name)
    assert violations == [], f"service.py must not persist evidence: {violations}"


# --- read-only: never calls repository.add/replace ----------------------------


def test_service_module_never_calls_repository_add_or_replace_directly() -> None:
    """Specifically checks calls of the shape `<something with
    'repository' in its name>.add(...)`/`.replace(...)` -- not every
    `.add`/`.append` in the module (`seen_retrieval_identities.add(...)`,
    `correlated.append(...)` are unrelated local collection operations).
    `KnowledgeToolService` never holds a repository reference at all --
    it only composes `KnowledgeRetrievalService`/`KnowledgeProvenanceService`.
    """
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    violations: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in ("add", "replace")
            and isinstance(node.value, ast.Attribute)
            and "repository" in node.value.attr.lower()
        ):
            violations.append(node.attr)
    assert violations == [], f"service.py must be read-only: found {violations}"


def test_service_module_never_references_a_repository_attribute_at_all() -> None:
    """`KnowledgeToolService` composes `KnowledgeRetrievalService`/
    `KnowledgeProvenanceService` only -- it never holds or accesses a
    `KnowledgeRepository` reference of its own.
    """
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and "repository" in node.attr.lower():
            pytest.fail(f"service.py references a repository attribute directly: {node.attr}")


# --- source-system / document-type independence -------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("source_system", ["sharepoint", "confluence-internal-2030", "FutureSystemX", "arbitrary-made-up-system"])
async def test_arbitrary_source_systems_behave_identically(source_system: str) -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    try:
        await repo.add(_governed("k1", KnowledgeDocumentType.OTHER, source_system))
        service = KnowledgeToolService(KnowledgeRetrievalService(repo), KnowledgeProvenanceService(repo))
        execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))
        assert execution.agent_payload.items[0].source_system == source_system
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document_type", [KnowledgeDocumentType.MOP, KnowledgeDocumentType.SOP, KnowledgeDocumentType.RCA, KnowledgeDocumentType.KB_ARTICLE, KnowledgeDocumentType.OTHER]
)
async def test_arbitrary_document_types_behave_identically(document_type: KnowledgeDocumentType) -> None:
    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    try:
        await repo.add(_governed(f"k-{document_type.value}", document_type, "test"))
        service = KnowledgeToolService(KnowledgeRetrievalService(repo), KnowledgeProvenanceService(repo))
        execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))
        assert execution.agent_payload.items[0].document_type is document_type
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_arbitrary_applicability_dimensions_work() -> None:
    from backend.knowledge.domain.models import Applicability

    repo = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    try:
        governed = _governed("k1", KnowledgeDocumentType.OTHER, "test").model_copy(
            update={"applicability": Applicability(dimensions={"some_future_dimension": ["QuantumBanana"]})}
        )
        await repo.add(governed)
        service = KnowledgeToolService(KnowledgeRetrievalService(repo), KnowledgeProvenanceService(repo))
        context = KnowledgeToolExecutionContext(as_of=_AS_OF, applicability_context=ApplicabilityContext(dimensions={"some_future_dimension": ["QuantumBanana"]}))
        execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), context)
        assert len(execution.agent_payload.items) == 1
    finally:
        await repo.close()
