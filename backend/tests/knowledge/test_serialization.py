"""Phase 5.1A: round-trip serialization must preserve domain meaning --
no storage exists yet, but a stable, deterministic serialize/deserialize
shape is what a future repository (5.1F) and the future Knowledge Context
output (5.1G/5.1H) will both depend on.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.knowledge.domain.contracts import KnowledgeContextItem, KnowledgeEvidenceReference
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import (
    Applicability,
    KnowledgeMetadata,
    KnowledgeObject,
    KnowledgeSection,
    KnowledgeSource,
    KnowledgeVersion,
)


def test_knowledge_object_round_trips_through_json() -> None:
    original = KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.MOP,
        title="Upgrade MOP",
        version=KnowledgeVersion(label="1.0", effective_from=datetime(2026, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="sharepoint", source_id="doc-123"),
        metadata=KnowledgeMetadata(owner="noc-team", tags=["upgrade"], attributes={"vendor": "ericsson"}),
        applicability=Applicability(dimensions={"domain": ["RAN"]}),
        sections=[
            KnowledgeSection(section_id="s1", knowledge_id="k1", sequence=0, content="Purpose text"),
            KnowledgeSection(section_id="s2", knowledge_id="k1", sequence=1, content="Steps text"),
        ],
        created_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    restored = KnowledgeObject.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.sections[0].section_id == "s1"
    assert restored.metadata.attributes["vendor"] == "ericsson"
    assert restored.applicability.dimensions["domain"] == ["RAN"]


def test_knowledge_context_item_round_trips_through_json() -> None:
    section = KnowledgeSection(section_id="s1", knowledge_id="k1", sequence=0, content="Verify process status.")
    original = KnowledgeContextItem(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.MOP,
        title="Upgrade MOP",
        version_label="1.0",
        lifecycle_status=LifecycleStatus.APPROVED,
        sections=[section],
        evidence=[
            KnowledgeEvidenceReference(
                knowledge_id="k1", version_label="1.0", section_id="s1", source_system="sharepoint", source_id="doc-123"
            )
        ],
    )

    restored = KnowledgeContextItem.model_validate_json(original.model_dump_json())

    assert restored == original
    assert restored.evidence[0].source_id == "doc-123"


def test_round_trip_through_plain_dict_preserves_enum_values() -> None:
    original = KnowledgeSource(source_system="gcs", source_id="bucket/object.pdf")
    as_dict = original.model_dump()
    restored = KnowledgeSource.model_validate(as_dict)
    assert restored == original
