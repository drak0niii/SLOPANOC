"""CORE ARCHITECTURAL INVARIANT (6A.6 §56/§57, mirroring `backend/tests/
context/test_dependency_boundary.py` and `backend/tests/knowledge/
test_dependency_boundary.py`'s own established pattern): Context
Engineering is a deterministic PLATFORM CAPABILITY, never an agent,
never an LLM caller, and never has its own independent path to discover/
search Knowledge, evaluate applicability, or query any database.

Enforced here as a real, automated `ast`-based check against actual
import statements -- never merely a module docstring's promise -- plus a
real fresh-subprocess standalone-import probe that also catches an
indirect/transitive import the static check could miss.
"""
from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_CONTEXT_ENGINEERING_DIR = _BACKEND_DIR / "context_engineering"

_FORBIDDEN_IMPORT_PREFIXES = (
    "google.adk",
    "google.genai",
    "backend.agents",
    "backend.tools",
    # No Knowledge SEARCH/DISCOVERY capability of any kind (§18/§24/§56/§57)
    # -- CONTRACT types (backend.knowledge.hybrid_retrieval.contracts,
    # imported for typing ONLY) are the sole exception, enforced by the
    # narrower check below rather than a blanket "backend.knowledge"
    # prefix (which would also forbid the one legitimate contracts import).
    "backend.knowledge.narrowing",
    "backend.knowledge.hybrid_retrieval.repository",
    "backend.knowledge.hybrid_retrieval.service",
    "backend.knowledge.hybrid_retrieval.indexing",
    "backend.knowledge.hybrid_retrieval.embedding",
    "backend.knowledge.repository",
    "backend.knowledge.retrieval",
    "backend.knowledge.governance",
    "backend.knowledge.provenance",
    "backend.knowledge.tools",
    "backend.knowledge.ingestion",
    "backend.knowledge_ingestion",
    "backend.knowledge_hybrid_retrieval",
    # No database/session-service access of any kind (§25: pure assembly
    # over already-fetched data only) -- Case/TELCO Context CONTRACT
    # types are fine; their persistence services are not.
    "backend.context.sqlalchemy",
    "backend.cases.service",
    "backend.cases.db",
    "backend.cases.models",
    "backend.cases.snapshot",
    "sqlalchemy",
    "aiosqlite",
    "asyncpg",
    "psycopg",
    "psycopg2",
    # Frontend.
    "src",
)


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _matches_any(module_name: str, prefixes: tuple[str, ...]) -> bool:
    return any(module_name == prefix or module_name.startswith(prefix + ".") for prefix in prefixes)


def test_context_engineering_directory_exists_and_is_non_empty() -> None:
    assert _CONTEXT_ENGINEERING_DIR.is_dir()
    assert sorted(_CONTEXT_ENGINEERING_DIR.glob("*.py")), f"expected {_CONTEXT_ENGINEERING_DIR}/*.py to exist"


def test_context_engineering_has_no_forbidden_imports() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(_CONTEXT_ENGINEERING_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if _matches_any(module_name, _FORBIDDEN_IMPORT_PREFIXES):
                violations.append((path.name, module_name))

    assert violations == [], f"backend/context_engineering must stay dependency-free -- forbidden imports found: {violations}"


def test_context_engineering_only_imports_hybrid_retrieval_contracts_not_the_service() -> None:
    """A NARROWER, explicit proof that the one permitted `backend.
    knowledge.hybrid_retrieval` import is ONLY `.contracts` (types) --
    never `.service`/`.repository`/`.indexing`/`.embedding` (the actual
    retrieval/search capability). Complements the broader forbidden-
    prefix check above with a positive assertion about what IS
    imported, not only what is not."""
    hybrid_retrieval_imports: set[str] = set()
    for path in sorted(_CONTEXT_ENGINEERING_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name.startswith("backend.knowledge.hybrid_retrieval"):
                hybrid_retrieval_imports.add(module_name)

    assert hybrid_retrieval_imports, "expected at least one backend.knowledge.hybrid_retrieval import (contracts, for typing)"
    assert hybrid_retrieval_imports == {"backend.knowledge.hybrid_retrieval.contracts"}, (
        f"backend/context_engineering must import ONLY hybrid_retrieval.contracts, never the retrieval service/repository/indexing/embedding modules -- found: {hybrid_retrieval_imports}"
    )


def test_no_context_engineering_module_imports_the_frontend() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(_CONTEXT_ENGINEERING_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name == "src" or module_name.startswith("src."):
                violations.append((path.name, module_name))

    assert violations == [], f"backend/context_engineering must not import the frontend: {violations}"


def test_context_engineering_actually_importable_standalone() -> None:
    """Real (not merely static) check that `backend.context_engineering`
    loads, in a FRESH interpreter, without pulling google.adk/
    google.genai/backend.agents/backend.tools/backend.knowledge.narrowing/
    backend.knowledge.hybrid_retrieval.{repository,service,indexing,
    embedding}/any SQL driver into sys.modules -- complements the static
    AST check above by also catching an indirect/transitive import.
    """
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import backend.context_engineering.contracts\n"
        "import backend.context_engineering.fingerprint\n"
        "import backend.context_engineering.assembly\n"
        "import backend.context_engineering.rendering\n"
        "forbidden_roots = ('google.adk', 'google.genai', 'backend.agents', 'backend.tools', "
        "'backend.knowledge.narrowing', 'backend.knowledge.hybrid_retrieval.repository', "
        "'backend.knowledge.hybrid_retrieval.service', 'backend.knowledge.hybrid_retrieval.indexing', "
        "'backend.knowledge.hybrid_retrieval.embedding', 'backend.knowledge.repository', "
        "'backend.knowledge.retrieval', 'backend.knowledge.governance', 'backend.knowledge.provenance', "
        "'backend.knowledge.tools', 'backend.context.sqlalchemy', 'backend.cases.service', "
        "'backend.cases.db', 'backend.cases.snapshot', 'sqlalchemy', 'aiosqlite', 'asyncpg', "
        "'psycopg', 'psycopg2')\n"
        "found = sorted(name for name in sys.modules if name.startswith(forbidden_roots))\n"
        "print('\\n'.join(found))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(_BACKEND_DIR.parent),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"probe subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    found = [line for line in result.stdout.splitlines() if line.strip()]
    assert found == [], f"importing backend.context_engineering pulled in forbidden modules: {found}"
