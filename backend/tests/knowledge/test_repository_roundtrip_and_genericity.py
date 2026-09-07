"""Phase 5.1F: full domain round-trip fidelity, generic document-type
behavior, arbitrary/delimiter-containing identifiers, and source/
applicability openness -- the repository must contain zero hardcoded
business vocabulary.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import (
    Applicability,
    KnowledgeMetadata,
    KnowledgeObject,
    KnowledgeSection,
    KnowledgeSource,
    KnowledgeVersion,
)
from backend.knowledge.repository.sqlite import SQLiteKnowledgeRepository



@pytest_asyncio.fixture
async def repo():
    repository = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    yield repository
    await repository.close()


# --- full domain round-trip -----------------------------------------------


@pytest.mark.asyncio
async def test_full_aggregate_round_trip_preserves_every_field(repo: SQLiteKnowledgeRepository) -> None:
    created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    updated_at = datetime(2026, 2, 1, tzinfo=timezone.utc)
    effective_from = datetime(2026, 1, 15, tzinfo=timezone.utc)
    effective_to = datetime(2026, 12, 31, tzinfo=timezone.utc)

    original = KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.RCA,
        title="Root Cause Analysis: Packet Loss",
        version=KnowledgeVersion(
            label="Rev-A",
            revision="internal-7",
            effective_from=effective_from,
            effective_to=effective_to,
            supersedes=["Rev-Z"],
            superseded_by=[],
        ),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(
            source_system="source_a", source_id="doc-99", source_uri="https://example.invalid/99", display_name="Packet Loss RCA"
        ),
        metadata=KnowledgeMetadata(owner="RAN Engineering", classification="Internal", tags=["packet-loss", "rca"], attributes={"authoring_team": "NOC"}),
        applicability=Applicability(dimensions={"vendor": ["Ericsson"], "hardware_family": ["AIR3268"]}),
        sections=[
            KnowledgeSection(
                section_id="k1::Rev-A::s0", knowledge_id="k1", heading="Root Cause", section_type=None, sequence=0,
                content="Interface flapped due to a configuration mismatch.", source_locator="lines:1-3",
            ),
            KnowledgeSection(
                section_id="k1::Rev-A::s1", knowledge_id="k1", heading="Corrective Action", section_type=None, sequence=1,
                content="Apply configuration patch and monitor for 24 hours.", source_locator="lines:5-8",
            ),
        ],
        created_at=created_at,
        updated_at=updated_at,
    )

    await repo.add(original)
    restored = await repo.get("k1", "Rev-A")

    assert restored == original


# --- generic document types -----------------------------------------------


@pytest.mark.parametrize(
    "document_type",
    [
        KnowledgeDocumentType.MOP,
        KnowledgeDocumentType.SOP,
        KnowledgeDocumentType.RCA,
        KnowledgeDocumentType.KB_ARTICLE,
        KnowledgeDocumentType.TROUBLESHOOTING_GUIDE,
        KnowledgeDocumentType.OPERATIONAL_PROCEDURE,
        KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
        KnowledgeDocumentType.OTHER,
    ],
)
@pytest.mark.asyncio
async def test_every_document_type_persists_identically(repo: SQLiteKnowledgeRepository, document_type: KnowledgeDocumentType) -> None:
    obj = KnowledgeObject(
        knowledge_id=f"k-{document_type.value}",
        document_type=document_type,
        title="Some document",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
    )
    await repo.add(obj)
    restored = await repo.get(obj.knowledge_id, "1.0")
    assert restored.document_type is document_type


def test_no_document_type_specific_repository_classes_exist() -> None:
    import ast
    from pathlib import Path

    repository_dir = Path(__file__).resolve().parents[2] / "knowledge" / "repository"
    forbidden_fragments = ("MOP", "SOP", "RCA", "KB")
    violations: list[tuple[str, str]] = []
    for path in sorted(repository_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                upper = node.name.upper()
                for fragment in forbidden_fragments:
                    if fragment in upper:
                        violations.append((path.name, node.name))
    assert violations == [], f"document-type-specific repository class found: {violations}"


# --- arbitrary identifiers (delimiter-containing, non-SemVer) --------------


@pytest.mark.parametrize(
    "knowledge_id,version_label",
    [
        ("a::b", "c"),
        ("customer/item:x", "Rev-A"),
        ("k1", "b::c"),
        ("k1", "2026-08"),
        ("k1", "Rev-Z"),
        ("weird id with spaces", "v 1"),
    ],
)
@pytest.mark.asyncio
async def test_arbitrary_identifiers_round_trip(repo: SQLiteKnowledgeRepository, knowledge_id: str, version_label: str) -> None:
    obj = KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title="Some document",
        version=KnowledgeVersion(label=version_label),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
    )
    await repo.add(obj)
    restored = await repo.get(knowledge_id, version_label)
    assert restored == obj


@pytest.mark.asyncio
async def test_repository_schema_does_not_assume_semver_labels(repo: SQLiteKnowledgeRepository) -> None:
    for label in ("1.9", "1.10", "Rev-A", "Rev-Z", "2026-08", "alpha-build", "not.at.all.semver!!"):
        obj = KnowledgeObject(
            knowledge_id="k1",
            document_type=KnowledgeDocumentType.MOP,
            title="t",
            version=KnowledgeVersion(label=label),
            lifecycle_status=LifecycleStatus.CANDIDATE,
            source=KnowledgeSource(source_system="s", source_id="1"),
        )
        await repo.add(obj)
        assert (await repo.get("k1", label)).version.label == label


# --- source / applicability openness ---------------------------------------


@pytest.mark.parametrize("source_system", ["sharepoint", "gcs", "source_a", "future_system_2030", "yet-another-made-up-source"])
@pytest.mark.asyncio
async def test_arbitrary_source_system_round_trips(repo: SQLiteKnowledgeRepository, source_system: str) -> None:
    obj = KnowledgeObject(
        knowledge_id=f"k-{source_system}",
        document_type=KnowledgeDocumentType.MOP,
        title="t",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=KnowledgeSource(source_system=source_system, source_id="1"),
    )
    await repo.add(obj)
    restored = await repo.get(obj.knowledge_id, "1.0")
    assert restored.source.source_system == source_system


@pytest.mark.asyncio
async def test_arbitrary_applicability_dimensions_round_trip(repo: SQLiteKnowledgeRepository) -> None:
    applicability = Applicability(dimensions={"hardware_family": ["AIR3268"], "deployment_type": ["edge"], "some_future_dimension": ["x"]})
    obj = KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.MOP,
        title="t",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=KnowledgeSource(source_system="s", source_id="1"),
        applicability=applicability,
    )
    await repo.add(obj)
    restored = await repo.get("k1", "1.0")
    assert restored.applicability == applicability


def test_no_hardcoded_source_or_applicability_vocabulary_in_repository() -> None:
    import ast
    from pathlib import Path

    repository_dir = Path(__file__).resolve().parents[2] / "knowledge" / "repository"
    forbidden_substrings = ("SUPPORTED_SOURCE", "KNOWN_DIMENSION", "SOURCESYSTEMENUM")
    violations: list[tuple[str, str]] = []
    for path in sorted(repository_dir.glob("*.py")):
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
    assert violations == [], f"hardcoded vocabulary found: {violations}"
