"""Phase 5.1J correction pass (Part C): `backend/api/knowledge_source_reference.py`
-- KM Source references are built ONLY from trusted, SELECTED
`KnowledgeEvidenceItem`s, never from `agent_payload`/model text, never
expose `source_uri`, deduplicate by identity while preserving selection
order, and correctly produce nothing when nothing was selected.
"""
from __future__ import annotations

from backend.api.knowledge_source_reference import KNOWLEDGE_SOURCE_LABEL, build_knowledge_source_references
from backend.api.schemas import KnowledgeSourceReferenceDTO
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceReference


def _item(
    knowledge_id: str = "aurora-relay-verification",
    section_id: str = "aurora-relay-verification:v1:s0",
    version_label: str = "v1",
    content: str = "Confirm the checksum is 7319 and the status is GREEN.",
    source_uri: str = "https://internal.example/SECRET-URL",
) -> KnowledgeEvidenceItem:
    section = KnowledgeSection(
        section_id=section_id, knowledge_id=knowledge_id, heading="Verification", sequence=0, content=content, source_locator="test-fixture:verification"
    )
    source = KnowledgeSource(
        source_system="manual_e2e_fixture", source_id="doc-1", source_uri=source_uri, display_name="Aurora Relay Governed Test Procedure"
    )
    reference = KnowledgeEvidenceReference(
        knowledge_id=knowledge_id, version_label=version_label, section_id=section_id,
        source_system="manual_e2e_fixture", source_id="doc-1", source_locator="test-fixture:verification",
    )
    return KnowledgeEvidenceItem(
        reference=reference, title="Aurora Relay Verification Procedure", document_type=KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
        lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section,
    )


# --- basic construction -----------------------------------------------------------


def test_build_from_one_selected_item() -> None:
    refs = build_knowledge_source_references([_item()])
    assert len(refs) == 1
    ref = refs[0]
    assert isinstance(ref, KnowledgeSourceReferenceDTO)
    assert ref.source_type == "knowledge"
    assert ref.label == KNOWLEDGE_SOURCE_LABEL
    assert ref.knowledge_id == "aurora-relay-verification"
    assert ref.version_label == "v1"
    assert ref.section_id == "aurora-relay-verification:v1:s0"
    assert ref.title == "Aurora Relay Verification Procedure"
    assert ref.document_type == "technical_instruction"
    assert ref.source_system == "manual_e2e_fixture"
    assert ref.evidence_source_id == "doc-1"
    assert ref.source_display_name == "Aurora Relay Governed Test Procedure"
    assert ref.section_heading == "Verification"
    assert ref.source_locator == "test-fixture:verification"
    assert ref.content == "Confirm the checksum is 7319 and the status is GREEN."


def test_empty_selection_produces_no_references() -> None:
    assert build_knowledge_source_references([]) == []


# --- source_uri never exposed --------------------------------------------------------


def test_source_uri_never_appears_on_the_dto() -> None:
    assert "source_uri" not in KnowledgeSourceReferenceDTO.model_fields
    ref = build_knowledge_source_references([_item()])[0]
    dumped = ref.model_dump(mode="json")
    assert "source_uri" not in dumped
    assert "SECRET-URL" not in str(dumped)


# --- multiple selections: dedup + order preservation ----------------------------------


def test_multiple_distinct_items_all_produce_references_preserving_order() -> None:
    item_a = _item(knowledge_id="k1", section_id="k1:v1:sA")
    item_b = _item(knowledge_id="k1", section_id="k1:v1:sB")
    refs = build_knowledge_source_references([item_b, item_a])
    assert [r.section_id for r in refs] == ["k1:v1:sB", "k1:v1:sA"]


def test_duplicate_identity_is_deduplicated() -> None:
    item = _item()
    duplicate = item.model_copy(deep=True)
    refs = build_knowledge_source_references([item, duplicate])
    assert len(refs) == 1


def test_each_reference_gets_a_distinct_synthetic_source_id() -> None:
    item_a = _item(knowledge_id="k1", section_id="k1:v1:sA")
    item_b = _item(knowledge_id="k1", section_id="k1:v1:sB")
    refs = build_knowledge_source_references([item_a, item_b])
    assert refs[0].source_id != refs[1].source_id


# --- content fidelity -----------------------------------------------------------------


def test_content_and_locator_match_the_trusted_item_exactly() -> None:
    tricky_content = "Run: `systemctl restart relay`\nChecksum: 7319\nStatus: GREEN"
    item = _item(content=tricky_content)
    ref = build_knowledge_source_references([item])[0]
    assert ref.content == tricky_content
    assert ref.source_locator == item.reference.source_locator


def test_arbitrary_document_type_and_source_system_pass_through_identically() -> None:
    section = KnowledgeSection(section_id="k2:v1:s0", knowledge_id="k2", sequence=0, content="text")
    source = KnowledgeSource(source_system="FutureSystemX", source_id="doc-2")
    reference = KnowledgeEvidenceReference(knowledge_id="k2", version_label="v1", section_id="k2:v1:s0", source_system="FutureSystemX", source_id="doc-2")
    item = KnowledgeEvidenceItem(
        reference=reference, title="Other Guide", document_type=KnowledgeDocumentType.OTHER,
        lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section,
    )
    ref = build_knowledge_source_references([item])[0]
    assert ref.document_type == "other"
    assert ref.source_system == "FutureSystemX"
