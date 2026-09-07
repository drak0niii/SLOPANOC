"""CORE ARCHITECTURAL INVARIANT (Phase 5.1A instruction section 3,
extended by Phase 5.1C instruction section 43, Phase 5.1D instruction
section 48, Phase 5.1E instruction section 63, Phase 5.1F instruction
section 61, Phase 5.1G instruction section 77, Phase 5.1H instruction
section 70, and Phase 5.1I instruction section 71): the Generic KM
domain, ingestion boundary, processing boundary, governance boundary,
repository boundary, retrieval boundary, provenance boundary, AND
agent-facing tools boundary must remain independent of every individual
agent, of ADK/Gemini, of any concrete cloud/vendor SDK
(ingestion/processing/governance/repository), of any storage/SQLAlchemy
package (processing/governance, and specifically
`repository/contracts.py` -- `repository/sqlite.py`, the one concrete
local implementation, is deliberately exempt), of any concrete
embedding/vector-index library (retrieval, provenance, tools), and of the
frontend (`src/`) -- a future source adapter, repository implementation,
or semantic retrieval scorer may depend on one of these in its OWN
module, but the generic
`backend/knowledge/{domain,ingestion,processing,governance,repository,retrieval,provenance,tools}/`
contracts/Protocols never do. This is enforced here as an automated check
against the actual `import`/`from ... import` statements in every file --
via `ast`, the standard library's own Python parser -- rather than
relying only on a module docstring's promise. No new dependency/linting
framework is introduced.
"""
from __future__ import annotations

import ast
from pathlib import Path

_KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge"
_DOMAIN_DIR = _KNOWLEDGE_DIR / "domain"
_INGESTION_DIR = _KNOWLEDGE_DIR / "ingestion"
_PROCESSING_DIR = _KNOWLEDGE_DIR / "processing"
_GOVERNANCE_DIR = _KNOWLEDGE_DIR / "governance"
_REPOSITORY_DIR = _KNOWLEDGE_DIR / "repository"
_RETRIEVAL_DIR = _KNOWLEDGE_DIR / "retrieval"
_PROVENANCE_DIR = _KNOWLEDGE_DIR / "provenance"
_TOOLS_DIR = _KNOWLEDGE_DIR / "tools"
_ALL_KM_DIRS = (_DOMAIN_DIR, _INGESTION_DIR, _PROCESSING_DIR, _GOVERNANCE_DIR, _REPOSITORY_DIR, _RETRIEVAL_DIR, _PROVENANCE_DIR, _TOOLS_DIR)
_STORAGE_FREE_DIRS = (_PROCESSING_DIR, _GOVERNANCE_DIR, _PROVENANCE_DIR, _TOOLS_DIR)
_CLOUD_SDK_FREE_DIRS = (_INGESTION_DIR, _REPOSITORY_DIR)
_REPOSITORY_CONTRACTS_FILE = _REPOSITORY_DIR / "contracts.py"
_EMBEDDING_VECTOR_FREE_DIRS = (_RETRIEVAL_DIR, _PROVENANCE_DIR, _TOOLS_DIR)
_SQLITE_REPOSITORY_FREE_DIRS = (_PROVENANCE_DIR, _TOOLS_DIR)

_FORBIDDEN_IMPORT_PREFIXES = (
    "google.adk",
    "google.genai",
    "backend.agents",
    "backend.tools.teams",
)

_FORBIDDEN_EMBEDDING_VECTOR_PREFIXES = (
    # Concrete embedding/vector-index libraries a future semantic scorer
    # might use -- never imported by the GENERIC retrieval contracts,
    # scoring Protocol, or orchestration service themselves (Phase 5.1G
    # instruction section 77). A future concrete `KnowledgeRelevanceScorer`
    # implementation is free to depend on one of these in its OWN module,
    # outside this package.
    "sentence_transformers",
    "faiss",
    "chromadb",
    "pinecone",
    "weaviate",
    "qdrant_client",
    "annoy",
    "numpy",
    "sklearn",
    "torch",
    "tensorflow",
)

_FORBIDDEN_CLOUD_SDK_PREFIXES = (
    # Concrete cloud/vendor SDKs a real future source adapter might use
    # (SharePoint/GCS/Drive/Confluence/...) -- never imported by the
    # GENERIC ingestion contracts/Protocol themselves (instruction
    # section 43's "avoid importing concrete cloud/vendor SDKs in
    # generic ingestion contracts"). A future concrete adapter is free
    # to depend on one of these in its OWN module, outside this package.
    "google.cloud",
    "azure",
    "boto3",
    "msal",
    "office365",
    "O365",
    "atlassian",
)

_FORBIDDEN_STORAGE_PREFIXES = (
    # Phase 5.1D instruction section 45/48: "No model or storage
    # dependency should enter processing." A future repository
    # implementation (5.1F) is free to depend on one of these, outside
    # this package.
    "sqlalchemy",
    "aiosqlite",
    "asyncpg",
    "psycopg",
    "psycopg2",
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


def test_all_km_package_directories_exist_and_are_non_empty() -> None:
    for directory in _ALL_KM_DIRS:
        assert directory.is_dir()
        assert sorted(directory.glob("*.py")), f"expected {directory}/*.py to exist"


def test_all_km_packages_have_no_forbidden_agent_adk_gemini_imports() -> None:
    violations: list[tuple[str, str]] = []
    for directory in _ALL_KM_DIRS:
        for path in sorted(directory.glob("*.py")):
            for module_name in _imported_module_names(path):
                if _matches_any(module_name, _FORBIDDEN_IMPORT_PREFIXES):
                    violations.append((path.name, module_name))

    assert violations == [], (
        "backend/knowledge/{domain,ingestion,processing,governance,repository} "
        f"must not import ADK/Gemini or any individual agent -- forbidden imports found: {violations}"
    )


def test_ingestion_and_repository_packages_have_no_concrete_cloud_vendor_sdk_imports() -> None:
    violations: list[tuple[str, str]] = []
    for directory in _CLOUD_SDK_FREE_DIRS:
        for path in sorted(directory.glob("*.py")):
            for module_name in _imported_module_names(path):
                if _matches_any(module_name, _FORBIDDEN_CLOUD_SDK_PREFIXES):
                    violations.append((path.name, module_name))

    assert violations == [], (
        "backend/knowledge/{ingestion,repository} must stay source/cloud-agnostic "
        f"-- neither may import a concrete cloud/vendor SDK: {violations}"
    )


def test_processing_and_governance_packages_have_no_storage_or_sqlalchemy_imports() -> None:
    violations: list[tuple[str, str]] = []
    for directory in _STORAGE_FREE_DIRS:
        for path in sorted(directory.glob("*.py")):
            for module_name in _imported_module_names(path):
                if _matches_any(module_name, _FORBIDDEN_STORAGE_PREFIXES):
                    violations.append((path.name, module_name))

    assert violations == [], (
        "backend/knowledge/{processing,governance} must stay storage-agnostic "
        f"-- neither may import SQLAlchemy or another storage package: {violations}"
    )


def test_retrieval_provenance_and_tools_packages_have_no_embedding_or_vector_index_imports() -> None:
    """`backend/knowledge/retrieval/`, `backend/knowledge/provenance/`,
    and `backend/knowledge/tools/` must stay purely deterministic, local
    reference implementations for this phase -- no embedding model,
    vector index, or numeric-computing library import, even though a
    FUTURE semantic scorer implementing the same
    `KnowledgeRelevanceScorer` Protocol may live outside these packages.
    """
    violations: list[tuple[str, str]] = []
    for directory in _EMBEDDING_VECTOR_FREE_DIRS:
        for path in sorted(directory.glob("*.py")):
            for module_name in _imported_module_names(path):
                if _matches_any(module_name, _FORBIDDEN_EMBEDDING_VECTOR_PREFIXES):
                    violations.append((path.name, module_name))

    assert violations == [], (
        "backend/knowledge/{retrieval,provenance,tools} must stay free of concrete embedding/vector-index "
        f"libraries -- forbidden imports found: {violations}"
    )


def test_provenance_and_tools_packages_never_import_the_concrete_sqlite_repository() -> None:
    """`backend/knowledge/provenance/` and `backend/knowledge/tools/`
    depend only on the `KnowledgeRepository` Protocol (5.1F) -- neither
    may import `SQLiteKnowledgeRepository` or `repository/sqlite.py`
    directly, so a future non-SQLite repository implementation needs no
    change in either package.
    """
    violations: list[tuple[str, str]] = []
    for directory in _SQLITE_REPOSITORY_FREE_DIRS:
        for path in sorted(directory.glob("*.py")):
            for module_name in _imported_module_names(path):
                if module_name.endswith("repository.sqlite") or module_name == "repository.sqlite":
                    violations.append((path.name, module_name))

    assert violations == [], f"backend/knowledge/{{provenance,tools}} must not import the concrete SQLite repository: {violations}"


def test_no_knowledge_package_imports_the_frontend() -> None:
    """No `backend/knowledge/` package may import anything under `src/`
    (the frontend) -- this is a backend-only domain capability.
    """
    violations: list[tuple[str, str]] = []
    for directory in _ALL_KM_DIRS:
        for path in sorted(directory.glob("*.py")):
            for module_name in _imported_module_names(path):
                if module_name == "src" or module_name.startswith("src."):
                    violations.append((path.name, module_name))

    assert violations == [], f"backend/knowledge must not import the frontend: {violations}"


def test_repository_contracts_module_has_no_storage_technology_imports() -> None:
    """`repository/contracts.py` specifically -- NOT `repository/sqlite.py`,
    which is the one concrete implementation and legitimately depends on
    SQLAlchemy/aiosqlite -- must remain storage-agnostic, so a future
    Postgres/Cloud SQL implementation can exist without ever touching
    `KnowledgeRepository` or its callers.
    """
    violations: list[tuple[str, str]] = []
    for module_name in _imported_module_names(_REPOSITORY_CONTRACTS_FILE):
        if _matches_any(module_name, _FORBIDDEN_STORAGE_PREFIXES) or module_name in ("sqlite3",):
            violations.append((_REPOSITORY_CONTRACTS_FILE.name, module_name))

    assert violations == [], f"repository/contracts.py must not import storage technology: {violations}"


def test_all_km_packages_actually_importable_standalone() -> None:
    """A real (not merely static) check that domain/ingestion/processing/
    governance/repository all load, in a FRESH interpreter, without pulling google.adk/
    google.genai/backend.agents/backend.tools.teams into sys.modules --
    complements the static AST check above (which only looks at direct
    imports) by also catching an indirect/transitive import. A fresh
    subprocess is used deliberately: by the time this test runs inside
    the full `backend/tests` suite, other tests will have already
    imported google.adk/backend.agents into this process's own
    sys.modules, which would make a same-process before/after
    sys.modules diff vacuously pass regardless of what backend.knowledge
    itself does.
    """
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import backend.knowledge.domain.contracts\n"
        "import backend.knowledge.domain.enums\n"
        "import backend.knowledge.domain.models\n"
        "import backend.knowledge.domain.applicability\n"
        "import backend.knowledge.ingestion.contracts\n"
        "import backend.knowledge.ingestion.adapters\n"
        "import backend.knowledge.processing.contracts\n"
        "import backend.knowledge.processing.processor\n"
        "import backend.knowledge.governance.contracts\n"
        "import backend.knowledge.governance.service\n"
        "import backend.knowledge.governance.versioning\n"
        "import backend.knowledge.repository.contracts\n"
        "import backend.knowledge.retrieval.contracts\n"
        "import backend.knowledge.retrieval.scoring\n"
        "import backend.knowledge.retrieval.service\n"
        "import backend.knowledge.provenance.contracts\n"
        "import backend.knowledge.provenance.service\n"
        "import backend.knowledge.tools.contracts\n"
        "import backend.knowledge.tools.service\n"
        "forbidden_roots = ('google.adk', 'google.genai', 'backend.agents', 'backend.tools.teams')\n"
        "found = sorted(name for name in sys.modules if name.startswith(forbidden_roots))\n"
        "print('\\n'.join(found))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(Path(__file__).resolve().parents[3]),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"probe subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    found = [line for line in result.stdout.splitlines() if line.strip()]
    assert found == [], f"importing backend.knowledge pulled in forbidden modules: {found}"
