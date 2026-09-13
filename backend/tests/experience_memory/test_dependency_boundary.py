"""CORE ARCHITECTURAL INVARIANT (6A.8 §76-§79, mirroring `backend/tests/
context/`, `backend/tests/skills/`, `backend/tests/context_engineering/`'s
own established pattern): Experience Memory is a deterministic,
persisted, typed record store -- never an agent, never an LLM caller,
never a Skill/Tool executor, never a Knowledge search/retrieval
consumer, never a vector/embedding user.

TWO LAYERS, TWO DIFFERENT BOUNDARIES:
- `backend/experience_memory/domain/` is 100% PURE -- no database
  driver of any kind, exactly like 6A.2's/6A.7's own pure-domain
  packages.
- `backend/experience_memory/sqlalchemy/` legitimately uses SQLAlchemy/
  asyncpg/aiosqlite (it IS the persistence layer) -- but even there,
  no agent/LLM/Knowledge-search/Skill-execution/Tool-execution/vector
  import may appear.

Enforced via real `ast`-based checks against actual import statements,
plus a real fresh-subprocess standalone-import probe.
"""
from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PACKAGE_DIR = _BACKEND_DIR / "experience_memory"
_DOMAIN_DIR = _PACKAGE_DIR / "domain"
_SQLALCHEMY_DIR = _PACKAGE_DIR / "sqlalchemy"

_FORBIDDEN_ANYWHERE = (
    "google.adk",
    "google.genai",
    "backend.agents",
    "backend.tools",
    "backend.skills",
    # No Knowledge search/retrieval/applicability/narrowing/vector
    # capability of any kind (§78) -- Experience Memory never queries
    # Knowledge itself; it only ever stores caller-supplied REFERENCES
    # (evidence_id/knowledge_id/version_label/section_id/artifact_id --
    # plain strings, never a typed import of the Knowledge domain).
    "backend.knowledge",
    "backend.knowledge_ingestion",
    "backend.knowledge_hybrid_retrieval",
    # No Context Engineering ASSEMBLY (only a plain string
    # `context_fingerprint` field is ever stored -- never a typed
    # `ContextPackage` import at all, unlike 6A.7's own narrower
    # allowance for `.contracts`).
    "backend.context_engineering",
    "backend.cases",
    "backend.context.sqlalchemy",
    "backend.context.domain",
    # Frontend.
    "src",
)

_FORBIDDEN_ONLY_IN_DOMAIN = (
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


def test_experience_memory_directories_exist_and_are_non_empty() -> None:
    assert _DOMAIN_DIR.is_dir()
    assert sorted(_DOMAIN_DIR.glob("*.py"))
    assert _SQLALCHEMY_DIR.is_dir()
    assert sorted(_SQLALCHEMY_DIR.glob("*.py"))


def test_no_forbidden_imports_anywhere_in_package() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        for module_name in _imported_module_names(path):
            if _matches_any(module_name, _FORBIDDEN_ANYWHERE):
                violations.append((str(path.relative_to(_BACKEND_DIR)), module_name))
    assert violations == [], f"backend/experience_memory must never import agent/LLM/Knowledge/Skill/Case/ContextEngineering/frontend code -- found: {violations}"


def test_domain_layer_has_zero_database_imports() -> None:
    """The pure-domain boundary: `domain/` must never import a SQL
    driver of any kind -- persistence is exclusively `sqlalchemy/`'s
    concern."""
    violations: list[tuple[str, str]] = []
    for path in sorted(_DOMAIN_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if _matches_any(module_name, _FORBIDDEN_ONLY_IN_DOMAIN):
                violations.append((path.name, module_name))
    assert violations == [], f"backend/experience_memory/domain must have ZERO database imports -- found: {violations}"


def test_domain_layer_only_imports_pydantic_and_its_own_enums() -> None:
    """A narrower, explicit proof (mirrors 6A.7's own "only .contracts,
    never .assembly" precedent): every non-stdlib import inside
    `domain/` is either `pydantic` or another `backend.experience_
    memory.domain.*` module -- nothing else."""
    allowed_external_prefixes = ("pydantic",)
    for path in sorted(_DOMAIN_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name.startswith("backend.experience_memory.domain"):
                continue
            if module_name.startswith("backend.experience_memory"):
                raise AssertionError(f"{path.name} imports {module_name!r} -- domain/ must never import the sqlalchemy/ persistence layer")
            if _matches_any(module_name, allowed_external_prefixes):
                continue
            if "." not in module_name and module_name in ("hashlib", "json", "re", "enum", "typing", "datetime"):
                continue
            # stdlib submodule imports (e.g. `typing.Optional` via `from
            # typing import Optional` already resolves to module "typing"
            # above); anything else here is unexpected.
            assert module_name in ("__future__",), f"{path.name} has an unexpected external import: {module_name!r}"


def test_experience_memory_package_actually_importable_standalone() -> None:
    """Real (not merely static) check that the whole package loads, in a
    FRESH interpreter, without pulling google.adk/google.genai/
    backend.agents/backend.tools/backend.skills/backend.knowledge/
    backend.cases/backend.context_engineering into sys.modules."""
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import backend.experience_memory.domain.enums\n"
        "import backend.experience_memory.domain.models\n"
        "import backend.experience_memory.domain.admission\n"
        "import backend.experience_memory.domain.fingerprint\n"
        "import backend.experience_memory.sqlalchemy.models\n"
        "import backend.experience_memory.sqlalchemy.db\n"
        "import backend.experience_memory.sqlalchemy.service\n"
        "forbidden_roots = ('google.adk', 'google.genai', 'backend.agents', 'backend.tools', "
        "'backend.skills', 'backend.knowledge', 'backend.knowledge_ingestion', "
        "'backend.knowledge_hybrid_retrieval', 'backend.context_engineering', 'backend.cases')\n"
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
    assert found == [], f"importing backend.experience_memory pulled in forbidden modules: {found}"


def test_no_vector_or_embedding_reference_anywhere() -> None:
    """§37/§75: no Experience embeddings, vector column, HNSW/IVFFlat
    index, or Vertex embedding call anywhere in this package -- proven
    at the source-text level, not merely by absent imports (catches a
    stray raw-SQL string too)."""
    forbidden_fragments = ("pgvector", "Vector(", "HNSW", "IVFFlat", "embed_content", "embedding_model", "VECTOR_DIMENSIONS", "text-embedding")
    violations: list[tuple[str, str]] = []
    for path in sorted(_PACKAGE_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for fragment in forbidden_fragments:
            if fragment in source:
                violations.append((str(path.relative_to(_BACKEND_DIR)), fragment))
    assert violations == [], f"backend/experience_memory must contain no vector/embedding reference -- found: {violations}"
