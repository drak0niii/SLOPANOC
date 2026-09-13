"""CORE ARCHITECTURAL INVARIANT (6A.2 instruction, mirroring
`backend/tests/knowledge/test_dependency_boundary.py`'s own established
pattern): the TELCO Context domain (`backend/context/domain/`) must
remain independent of every individual agent, of ADK/Gemini, of Generic
Knowledge (`backend.knowledge`), of Case (`backend.cases`), of any
storage/SQLAlchemy package, and of the frontend -- TELCO Context and
Knowledge Context are PEER context domains
(docs/INTELLIGENCE_ARCHITECTURE.md #4), never coupled to each other.
Enforced here as an automated `ast`-based check against real import
statements, not merely a module docstring's promise -- the exact same
mechanism Knowledge's own dependency-boundary test already uses.
"""
from __future__ import annotations

import ast
from pathlib import Path

_CONTEXT_DIR = Path(__file__).resolve().parents[2] / "context"
_DOMAIN_DIR = _CONTEXT_DIR / "domain"
_SQLALCHEMY_DIR = _CONTEXT_DIR / "sqlalchemy"

_FORBIDDEN_DOMAIN_IMPORT_PREFIXES = (
    "google.adk",
    "google.genai",
    "backend.agents",
    "backend.tools.teams",
    "backend.knowledge",
    "backend.cases",
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


def test_context_domain_directory_exists_and_is_non_empty() -> None:
    assert _DOMAIN_DIR.is_dir()
    assert sorted(_DOMAIN_DIR.glob("*.py")), f"expected {_DOMAIN_DIR}/*.py to exist"


def test_context_domain_has_no_forbidden_imports() -> None:
    """`backend/context/domain/` must not import ADK/Gemini, any
    individual agent, Generic Knowledge, Case, or any storage technology
    -- it is a pure, deterministic, peer-independent domain model, exactly
    like `backend/knowledge/domain/` is for Knowledge.
    """
    violations: list[tuple[str, str]] = []
    for path in sorted(_DOMAIN_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if _matches_any(module_name, _FORBIDDEN_DOMAIN_IMPORT_PREFIXES):
                violations.append((path.name, module_name))

    assert violations == [], f"backend/context/domain must stay dependency-free -- forbidden imports found: {violations}"


def test_no_context_package_imports_the_frontend() -> None:
    violations: list[tuple[str, str]] = []
    for directory in (_DOMAIN_DIR, _SQLALCHEMY_DIR):
        for path in sorted(directory.glob("*.py")):
            for module_name in _imported_module_names(path):
                if module_name == "src" or module_name.startswith("src."):
                    violations.append((path.name, module_name))

    assert violations == [], f"backend/context must not import the frontend: {violations}"


def test_context_domain_actually_importable_standalone() -> None:
    """Real (not merely static) check that `backend.context.domain`
    loads, in a FRESH interpreter, without pulling google.adk/
    google.genai/backend.agents/backend.tools.teams/backend.knowledge/
    backend.cases into sys.modules -- complements the static AST check
    above by also catching an indirect/transitive import. Mirrors
    `backend/tests/knowledge/test_dependency_boundary.py`'s own
    `test_all_km_packages_actually_importable_standalone` exactly,
    including its reason for using a fresh subprocess.
    """
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import backend.context.domain.enums\n"
        "import backend.context.domain.models\n"
        "forbidden_roots = ('google.adk', 'google.genai', 'backend.agents', "
        "'backend.tools.teams', 'backend.knowledge', 'backend.cases')\n"
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
    assert found == [], f"importing backend.context.domain pulled in forbidden modules: {found}"
