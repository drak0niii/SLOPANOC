"""CORE ARCHITECTURAL INVARIANT (6A.7 §59, mirroring `backend/tests/
context/`, `backend/tests/knowledge/`, and `backend/tests/context_
engineering/`'s own established pattern): the Skills Framework is a
deterministic, declarative, typed contract layer -- never an agent,
never an LLM caller, never a Tool executor, never a Knowledge/retrieval
consumer beyond the already-assembled `ContextPackage` it reads.

Enforced here as a real, automated `ast`-based check against actual
import statements, plus a real fresh-subprocess standalone-import probe.
"""
from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_SKILLS_DIR = _BACKEND_DIR / "skills"

_FORBIDDEN_IMPORT_PREFIXES = (
    "google.adk",
    "google.genai",
    "backend.agents",
    "backend.tools",
    # No Knowledge search/retrieval/applicability capability of any kind
    # (§31/§57) -- the Skills Framework never queries Knowledge itself.
    # Deliberately NOT a blanket "backend.knowledge" prefix: `backend.
    # context_engineering.contracts` (an approved dependency of Skills,
    # exactly as 6A.6 itself approved it) legitimately, transitively
    # imports `backend.knowledge.hybrid_retrieval.contracts` for typing
    # -- mirrors 6A.6's OWN dependency-boundary test's identical nuance.
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
    # No database/session-service access of any kind (§60: fully
    # offline) -- Case/TELCO Context/Context Engineering CONTRACT types
    # are fine; their persistence services are not.
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


def test_skills_directory_exists_and_is_non_empty() -> None:
    assert _SKILLS_DIR.is_dir()
    assert sorted(_SKILLS_DIR.glob("*.py")), f"expected {_SKILLS_DIR}/*.py to exist"


def test_skills_package_has_no_forbidden_imports() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(_SKILLS_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if _matches_any(module_name, _FORBIDDEN_IMPORT_PREFIXES):
                violations.append((path.name, module_name))

    assert violations == [], f"backend/skills must stay dependency-free -- forbidden imports found: {violations}"


def test_skills_only_imports_context_engineering_contracts_not_assembly() -> None:
    """A NARROWER, explicit proof: the only permitted `backend.context_
    engineering` import anywhere in `backend/skills/` is `.contracts`
    (the `ContextPackage` TYPE) -- never `.assembly` (which would let
    Skills construct/re-assemble context itself, never its intended
    role of pure CONSUMER)."""
    context_engineering_imports: set[str] = set()
    for path in sorted(_SKILLS_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name.startswith("backend.context_engineering"):
                context_engineering_imports.add(module_name)

    assert context_engineering_imports, "expected at least one backend.context_engineering import (contracts, for typing)"
    assert context_engineering_imports == {"backend.context_engineering.contracts"}, (
        f"backend/skills must import ONLY context_engineering.contracts -- found: {context_engineering_imports}"
    )


def test_skills_only_imports_context_domain_enums_not_sqlalchemy_service() -> None:
    context_imports: set[str] = set()
    for path in sorted(_SKILLS_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name.startswith("backend.context."):
                context_imports.add(module_name)

    assert context_imports, "expected at least one backend.context.domain.enums import"
    assert context_imports == {"backend.context.domain.enums"}, f"backend/skills must import ONLY backend.context.domain.enums -- found: {context_imports}"


def test_no_skills_module_imports_the_frontend() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(_SKILLS_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name == "src" or module_name.startswith("src."):
                violations.append((path.name, module_name))

    assert violations == [], f"backend/skills must not import the frontend: {violations}"


def test_skills_package_actually_importable_standalone() -> None:
    """Real (not merely static) check that `backend.skills` loads, in a
    FRESH interpreter, without pulling google.adk/google.genai/
    backend.agents/backend.tools/backend.knowledge/any SQL driver into
    sys.modules -- complements the static AST check above."""
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import backend.skills.contracts\n"
        "import backend.skills.versioning\n"
        "import backend.skills.fingerprint\n"
        "import backend.skills.loader\n"
        "import backend.skills.registry\n"
        "import backend.skills.readiness\n"
        "import backend.skills.applicability\n"
        "import backend.skills.rendering\n"
        "forbidden_roots = ('google.adk', 'google.genai', 'backend.agents', 'backend.tools', "
        "'backend.knowledge.narrowing', 'backend.knowledge.hybrid_retrieval.repository', "
        "'backend.knowledge.hybrid_retrieval.service', 'backend.knowledge.hybrid_retrieval.indexing', "
        "'backend.knowledge.hybrid_retrieval.embedding', 'backend.knowledge.repository', "
        "'backend.knowledge.retrieval', 'backend.knowledge.governance', 'backend.knowledge.provenance', "
        "'backend.knowledge.tools', 'backend.knowledge.ingestion', 'backend.knowledge_ingestion', "
        "'backend.knowledge_hybrid_retrieval', 'backend.context.sqlalchemy', 'backend.cases.service', "
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
    assert found == [], f"importing backend.skills pulled in forbidden modules: {found}"


def test_loader_uses_only_safe_yaml_load() -> None:
    """§13/§14/§48: the loader must use `yaml.safe_load` exclusively --
    never `yaml.load`, never a custom Loader capable of arbitrary object
    instantiation. Proven at the source level, not merely by convention."""
    loader_path = _SKILLS_DIR / "loader.py"
    source = loader_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    yaml_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in ("load", "safe_load", "unsafe_load", "full_load"):
            yaml_calls.append(node.func.attr)
    assert yaml_calls == ["safe_load"], f"loader.py must call ONLY yaml.safe_load -- found calls: {yaml_calls}"
