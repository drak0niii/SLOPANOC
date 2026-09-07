"""Phase 5.1C: the generic ingestion boundary must have ZERO hardcoded
knowledge of named source systems -- instruction sections 3/9/35/41.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.knowledge.domain.models import KnowledgeSource
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument

_INGESTION_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "ingestion"


@pytest.mark.parametrize(
    "source_system",
    [
        "sharepoint",
        "gcs",
        "confluence",
        "source_a",
        "enterprise_repo_x",
        "future_system_2030",
        "yet-another-made-up-source",
    ],
)
def test_arbitrary_source_systems_behave_identically(source_system: str) -> None:
    doc = IngestedKnowledgeDocument(
        source=KnowledgeSource(source_system=source_system, source_id="doc-1"),
        title="Some document",
        content="Some content.",
    )
    assert doc.source.source_system == source_system


def test_a_brand_new_never_before_seen_source_system_requires_no_code_change() -> None:
    """The whole point: this string has never appeared anywhere in
    backend/knowledge/ingestion/ -- if it worked, nothing needed
    updating to support it.
    """
    doc = IngestedKnowledgeDocument(
        source=KnowledgeSource(source_system="zzz_never_seen_before_source_zzz", source_id="1"),
        title="t",
        content="c",
    )
    assert doc.source.source_system == "zzz_never_seen_before_source_zzz"


def test_no_hardcoded_source_system_vocabulary_exists_in_ingestion_package() -> None:
    """Static proof, not just a runtime sample: parse every .py file
    under backend/knowledge/ingestion/ and confirm no actual DECLARED
    identifier (a class name, or a module-level constant name) suggests
    a closed source-system vocabulary (SUPPORTED_SOURCES,
    SourceSystemEnum, ConnectorTypeEnum, a source-system registry, ...).
    This checks real declarations via `ast`, not raw text -- so a
    docstring merely explaining that no such thing exists (as this
    package's own __init__.py does) is correctly not a violation.
    """
    forbidden_substrings = (
        "SUPPORTED_SOURCE",
        "SOURCESYSTEMENUM",
        "CONNECTORTYPEENUM",
        "SOURCE_REGISTRY",
        "KNOWN_SOURCE",
    )
    violations: list[tuple[str, str]] = []
    for path in sorted(_INGESTION_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declared_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                declared_names.append(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                declared_names.extend(t.id for t in targets if isinstance(t, ast.Name))
        for name in declared_names:
            upper = name.upper()
            for forbidden in forbidden_substrings:
                if forbidden in upper:
                    violations.append((path.name, name))
    assert violations == [], f"hardcoded source-system vocabulary found: {violations}"


def test_no_concrete_source_system_names_appear_as_python_identifiers_in_production_code() -> None:
    """A stronger structural check than a plain substring search: parse
    every ingestion .py file and confirm no concrete source-system name
    (sharepoint/gcs/drive/confluence) is used as an identifier (class
    name, function name, or variable) anywhere -- these names are
    legitimate only as arbitrary string VALUES a caller supplies, never
    as something the generic boundary itself names or branches on.
    """
    forbidden_identifier_fragments = ("sharepoint", "gcs", "drive", "confluence", "onedrive")
    violations: list[tuple[str, str]] = []
    for path in sorted(_INGESTION_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            name = None
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
            elif isinstance(node, ast.Name):
                name = node.id
            if name is None:
                continue
            lowered = name.lower()
            for fragment in forbidden_identifier_fragments:
                if fragment in lowered:
                    violations.append((path.name, name))
    assert violations == [], f"a concrete source-system name is used as a Python identifier: {violations}"
