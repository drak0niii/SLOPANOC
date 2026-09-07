"""Phase 5.1H: structural/AST genericity checks for
`backend/knowledge/provenance/` -- complementing test_dependency_boundary.py
(forbidden imports) and test_provenance_service.py (behavioral mismatch
tests). Proves absence of hardcoded business/document-type/source
vocabulary, source-system/document-type independence, no persistence, and
no wall-clock calls -- `KnowledgeEvidenceReference` construction is
already timestamp-free by its own frozen 5.1A contract.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.knowledge.domain.applicability import ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.provenance.service import KnowledgeProvenanceService
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalItem, KnowledgeRetrievalResult

_PROVENANCE_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "provenance"
_SERVICE_FILE = _PROVENANCE_DIR / "service.py"
_CONTRACTS_FILE = _PROVENANCE_DIR / "contracts.py"


class _FakeRepository:
    def __init__(self, objects: list[KnowledgeObject]) -> None:
        self._objects = {(o.knowledge_id, o.version.label): o for o in objects}

    async def add(self, knowledge_object): raise AssertionError
    async def get(self, knowledge_id: str, version_label: str):
        return self._objects.get((knowledge_id, version_label))
    async def replace(self, knowledge_object): raise AssertionError
    async def list_versions(self, knowledge_id: str): raise AssertionError
    async def list_all(self): raise AssertionError


def _governed(knowledge_id: str, document_type: KnowledgeDocumentType, source_system: str, source_id: str) -> KnowledgeObject:
    section = KnowledgeSection(section_id=f"{knowledge_id}:v1:s0", knowledge_id=knowledge_id, sequence=0, content="router outage recovery")
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=document_type,
        title=f"{knowledge_id} guide",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system=source_system, source_id=source_id),
        sections=[section],
    )


def _retrieval_item(governed: KnowledgeObject) -> KnowledgeRetrievalItem:
    return KnowledgeRetrievalItem(
        knowledge_id=governed.knowledge_id, document_type=governed.document_type, title=governed.title,
        version_label=governed.version.label, lifecycle_status=governed.lifecycle_status,
        section=governed.sections[0], source=governed.source,
        applicability_outcome=ApplicabilityOutcome.MATCH, relevance_score=0.9,
    )


# --- no hardcoded business/domain vocabulary -----------------------------------


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


_FORBIDDEN_HARDCODED_STRINGS = ("mop", "sop", "rca", "kb_article", "outage", "sharepoint", "ericsson", "runbook")


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


def test_no_specific_document_type_member_is_referenced() -> None:
    """`service.py` must never reference a specific
    `KnowledgeDocumentType` MEMBER (`.MOP`/`.SOP`/`.RCA`/...) -- it only
    ever carries the caller-supplied `document_type` value through
    unchanged, never branches on which one it is.
    """
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    violations: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "KnowledgeDocumentType"
        ):
            violations.append(node.attr)
    assert violations == [], f"service.py must not reference a specific KnowledgeDocumentType member: {violations}"


# --- source-system openness / document-type independence ----------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("source_system", ["sharepoint", "confluence-internal-2030", "future_system_x", "arbitrary-made-up-system"])
async def test_arbitrary_source_systems_behave_identically(source_system: str) -> None:
    governed = _governed("k1", KnowledgeDocumentType.OTHER, source_system, "doc-1")
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    evidence_set = await service.build_evidence_set(KnowledgeRetrievalResult(items=[_retrieval_item(governed)]))
    assert evidence_set.items[0].source.source_system == source_system


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document_type", [KnowledgeDocumentType.MOP, KnowledgeDocumentType.SOP, KnowledgeDocumentType.RCA, KnowledgeDocumentType.KB_ARTICLE, KnowledgeDocumentType.OTHER]
)
async def test_arbitrary_document_types_behave_identically(document_type: KnowledgeDocumentType) -> None:
    governed = _governed(f"k-{document_type.value}", document_type, "test", "doc-1")
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    evidence_set = await service.build_evidence_set(KnowledgeRetrievalResult(items=[_retrieval_item(governed)]))
    assert evidence_set.items[0].document_type is document_type


# --- no persistence / no wall-clock ---------------------------------------------


def test_service_module_has_no_persistence_or_caching_calls() -> None:
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    forbidden_names = {"open", "pickle", "shelve", "sqlite3"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in forbidden_names:
            violations.append(node.id)
        if isinstance(node, ast.ImportFrom) and node.module in forbidden_names:
            violations.append(node.module)
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in forbidden_names:
                    violations.append(alias.name)
    assert violations == [], f"service.py must not persist evidence: {violations}"


def test_no_provenance_module_calls_the_wall_clock() -> None:
    for path in (_SERVICE_FILE, _CONTRACTS_FILE):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert not (
                    node.func.attr in ("now", "utcnow")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "datetime"
                ), f"{path.name} must never call the wall clock"


def test_no_global_or_module_level_mutable_registry() -> None:
    """No module-level dict/list assignment that would act as a hidden
    global evidence registry -- constants (`_SECTION_FIELDS` tuples) are
    fine; a mutable module-level collection intended to accumulate state
    across calls is not.
    """
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, (ast.List, ast.Dict)) and not isinstance(node.value, ast.Tuple):
                    if isinstance(node.value, ast.List) and len(node.value.elts) == 0:
                        pytest.fail(f"module-level mutable empty list {target.id!r} looks like a hidden registry")
                    if isinstance(node.value, ast.Dict) and len(node.value.keys) == 0:
                        pytest.fail(f"module-level mutable empty dict {target.id!r} looks like a hidden registry")
