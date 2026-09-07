"""Phase 5.1A: provenance/evidence identity primitives and the Knowledge
Context output contract -- domain primitives only, not the enrichment/
validation/ranking behavior later phases (5.1G/5.1H) build on top.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.contracts import KnowledgeContextItem, KnowledgeEvidenceReference
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection


def _evidence(**overrides: object) -> KnowledgeEvidenceReference:
    fields: dict[str, object] = {
        "knowledge_id": "k1",
        "version_label": "1.0",
        "source_system": "sharepoint",
        "source_id": "doc-123",
    }
    fields.update(overrides)
    return KnowledgeEvidenceReference(**fields)


# --- KnowledgeEvidenceReference ------------------------------------------


def test_valid_document_level_reference() -> None:
    reference = _evidence()
    assert reference.section_id is None
    assert reference.knowledge_id == "k1"


def test_valid_section_level_reference() -> None:
    reference = _evidence(section_id="s1")
    assert reference.section_id == "s1"


def test_reference_identity_version_source_are_retained() -> None:
    reference = _evidence(section_id="s1", source_locator="/mop/upgrade#step-3")
    assert reference.knowledge_id == "k1"
    assert reference.version_label == "1.0"
    assert reference.source_system == "sharepoint"
    assert reference.source_id == "doc-123"
    assert reference.source_locator == "/mop/upgrade#step-3"


@pytest.mark.parametrize("field", ["knowledge_id", "version_label", "source_system", "source_id"])
def test_reference_blank_identity_fields_rejected(field: str) -> None:
    with pytest.raises(ValidationError):
        _evidence(**{field: ""})


# --- KnowledgeContextItem --------------------------------------------------


def test_generic_context_item_can_be_constructed() -> None:
    section = KnowledgeSection(section_id="s1", knowledge_id="k1", sequence=0, content="Verify process status.")
    item = KnowledgeContextItem(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.MOP,
        title="Upgrade MOP",
        version_label="1.0",
        lifecycle_status=LifecycleStatus.APPROVED,
        sections=[section],
        evidence=[_evidence(section_id="s1")],
    )
    assert item.knowledge_id == "k1"
    assert item.sections[0].content == "Verify process status."
    assert item.evidence[0].section_id == "s1"


def test_context_item_requires_no_agent_specific_fields() -> None:
    """Constructing a KnowledgeContextItem needs nothing beyond generic
    KM identity/content -- no Team Manager/Incident Manager/ADK/Gemini
    concept is part of this contract.
    """
    item = KnowledgeContextItem(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title="Rollback SOP",
        version_label="Rev-A",
        lifecycle_status=LifecycleStatus.APPROVED,
    )
    assert item.sections == []
    assert item.evidence == []
    assert set(KnowledgeContextItem.model_fields) == {
        "knowledge_id",
        "document_type",
        "title",
        "version_label",
        "lifecycle_status",
        "applicability",
        "sections",
        "evidence",
    }


def test_context_item_section_knowledge_id_mismatch_rejected() -> None:
    mismatched_section = KnowledgeSection(section_id="s1", knowledge_id="SOME-OTHER-ID", sequence=0, content="text")
    with pytest.raises(ValidationError):
        KnowledgeContextItem(
            knowledge_id="k1",
            document_type=KnowledgeDocumentType.MOP,
            title="Upgrade MOP",
            version_label="1.0",
            lifecycle_status=LifecycleStatus.APPROVED,
            sections=[mismatched_section],
        )


def test_context_item_blank_identity_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeContextItem(
            knowledge_id="",
            document_type=KnowledgeDocumentType.MOP,
            title="Upgrade MOP",
            version_label="1.0",
            lifecycle_status=LifecycleStatus.APPROVED,
        )
