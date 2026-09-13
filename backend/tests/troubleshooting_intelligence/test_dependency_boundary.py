"""CORE ARCHITECTURAL INVARIANT (6A.9): Troubleshooting Intelligence
Assembly is a deterministic PLATFORM CAPABILITY, never an agent, never
an LLM caller, and never has its own independent path to Skill
selection, Experience Memory retrieval, or Knowledge search/database
access of any kind. Mirrors `backend/tests/context_engineering/
test_dependency_boundary.py`'s own established pattern exactly.
"""
from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_PACKAGE_DIR = _BACKEND_DIR / "troubleshooting_intelligence"

_FORBIDDEN_IMPORT_PREFIXES = (
    "google.adk",
    "google.genai",
    "backend.agents",
    "backend.tools",
    # No Skill REGISTRY/LOADING capability of any kind -- contracts only.
    "backend.skills.registry",
    "backend.skills.loader",
    "backend.skills.readiness",
    "backend.skills.applicability",
    "backend.skills.versioning",
    "backend.skills.fingerprint",
    # No Experience Memory PERSISTENCE/QUERY capability of any kind --
    # domain contract types only.
    "backend.experience_memory.sqlalchemy",
    # No Knowledge search/discovery/persistence of any kind.
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
    # No database/session-service access of any kind.
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
    "yaml",
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


def test_package_exists_and_is_non_empty() -> None:
    assert _PACKAGE_DIR.is_dir()
    assert sorted(_PACKAGE_DIR.glob("*.py")), f"expected {_PACKAGE_DIR}/*.py to exist"


def test_no_forbidden_imports() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(_PACKAGE_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if _matches_any(module_name, _FORBIDDEN_IMPORT_PREFIXES):
                violations.append((path.name, module_name))
    assert violations == [], f"backend/troubleshooting_intelligence must stay dependency-free -- forbidden imports found: {violations}"


def test_only_permitted_skill_and_experience_contract_imports() -> None:
    """A NARROWER, positive proof: the only `backend.skills`/`backend.
    experience_memory` imports anywhere in this package are their pure
    `.contracts`/`.domain.models` type modules -- never a registry,
    loader, readiness/applicability evaluator, or a persistence service."""
    skills_imports: set[str] = set()
    experience_imports: set[str] = set()
    for path in sorted(_PACKAGE_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name.startswith("backend.skills"):
                skills_imports.add(module_name)
            if module_name.startswith("backend.experience_memory"):
                experience_imports.add(module_name)

    assert skills_imports, "expected at least one backend.skills import (contracts, for typing)"
    assert skills_imports == {"backend.skills.contracts", "backend.skills.rendering"}, (
        f"found unexpected backend.skills imports: {skills_imports} -- only the pure `.contracts` (types) and "
        "`.rendering` (a deterministic, non-reasoning text renderer, no registry/DB/LLM) modules are permitted"
    )

    assert experience_imports, "expected at least one backend.experience_memory import (domain.models, for typing)"
    assert experience_imports == {"backend.experience_memory.domain.models"}, f"found unexpected backend.experience_memory imports: {experience_imports}"


def test_actually_importable_standalone() -> None:
    """Real (not merely static) check that `backend.troubleshooting_
    intelligence` loads, in a FRESH interpreter, without pulling
    google.adk/google.genai/backend.agents/backend.tools/a Skill registry
    or loader/an Experience Memory persistence module/any SQL driver into
    sys.modules."""
    import subprocess
    import sys

    probe = (
        "import sys\n"
        "import backend.troubleshooting_intelligence.contracts\n"
        "import backend.troubleshooting_intelligence.fingerprint\n"
        "import backend.troubleshooting_intelligence.assembly\n"
        "import backend.troubleshooting_intelligence.rendering\n"
        "import backend.troubleshooting_intelligence.grounding\n"
        "forbidden_roots = ('google.adk', 'google.genai', 'backend.agents', 'backend.tools', "
        "'backend.skills.registry', 'backend.skills.loader', 'backend.skills.readiness', "
        "'backend.skills.applicability', 'backend.experience_memory.sqlalchemy', "
        "'backend.knowledge.narrowing', 'backend.knowledge.hybrid_retrieval.repository', "
        "'backend.knowledge.hybrid_retrieval.service', 'backend.context.sqlalchemy', "
        "'backend.cases.service', 'backend.cases.db', 'sqlalchemy', 'aiosqlite', 'asyncpg', "
        "'psycopg', 'psycopg2', 'yaml')\n"
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
    assert found == [], f"importing backend.troubleshooting_intelligence pulled in forbidden modules: {found}"
