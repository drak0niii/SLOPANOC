"""Phase 5.1A: closed vocabularies -- KnowledgeDocumentType/LifecycleStatus."""
from __future__ import annotations

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus


def test_all_required_document_types_exist() -> None:
    required = {
        "MOP",
        "SOP",
        "RCA",
        "KB_ARTICLE",
        "TROUBLESHOOTING_GUIDE",
        "OPERATIONAL_PROCEDURE",
        "TECHNICAL_INSTRUCTION",
        "OTHER",
    }
    assert required <= set(KnowledgeDocumentType.__members__)


def test_mop_sop_rca_kb_are_values_of_the_same_generic_type() -> None:
    """MOP is a document TYPE, not a separate architecture -- all four
    are plain members of the one KnowledgeDocumentType enum.
    """
    assert KnowledgeDocumentType.MOP.value == "mop"
    assert KnowledgeDocumentType.SOP.value == "sop"
    assert KnowledgeDocumentType.RCA.value == "rca"
    assert KnowledgeDocumentType.KB_ARTICLE.value == "kb_article"
    assert isinstance(KnowledgeDocumentType.MOP, KnowledgeDocumentType)
    assert isinstance(KnowledgeDocumentType.KB_ARTICLE, KnowledgeDocumentType)


def test_document_type_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        KnowledgeDocumentType("not_a_real_type")


def test_lifecycle_status_has_exactly_the_current_three_values() -> None:
    assert {member.value for member in LifecycleStatus} == {"candidate", "approved", "archive"}


def test_lifecycle_status_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        LifecycleStatus("published")
