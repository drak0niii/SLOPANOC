"""Phase 5.1B metadata hardening (instruction section 18/43) --
KnowledgeMetadata's small structural improvements. Applicability
matching must never be driven by metadata; see
test_applicability_separation.py for the Applicability-side guarantees.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.models import KnowledgeMetadata


def test_valid_metadata_accepted() -> None:
    metadata = KnowledgeMetadata(
        owner="RAN Engineering", classification="Internal", tags=["upgrade", "software"], attributes={"authoring_team": "NOC"}
    )
    assert metadata.tags == ["upgrade", "software"]
    assert metadata.attributes["authoring_team"] == "NOC"


def test_blank_tag_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeMetadata(tags=["upgrade", ""])
    with pytest.raises(ValidationError):
        KnowledgeMetadata(tags=["   "])


def test_duplicate_tags_deduped_deterministically_preserving_first_casing() -> None:
    metadata = KnowledgeMetadata(tags=["Upgrade", "upgrade", "UPGRADE", "software"])
    assert metadata.tags == ["Upgrade", "software"]


def test_tags_are_stripped_of_surrounding_whitespace() -> None:
    metadata = KnowledgeMetadata(tags=[" upgrade ", "software"])
    assert metadata.tags == ["upgrade", "software"]


def test_blank_attribute_key_rejected() -> None:
    with pytest.raises(ValidationError):
        KnowledgeMetadata(attributes={"": "value"})
    with pytest.raises(ValidationError):
        KnowledgeMetadata(attributes={"   ": "value"})


def test_independent_mutable_defaults() -> None:
    a = KnowledgeMetadata()
    b = KnowledgeMetadata()
    a.tags.append("only-on-a")
    a.attributes["only-on-a"] = True
    assert b.tags == []
    assert b.attributes == {}


def test_arbitrary_custom_metadata_attribute_accepted() -> None:
    metadata = KnowledgeMetadata(attributes={"vendor": "ericsson", "release": "24.3", "anything_future": {"nested": True}})
    assert metadata.attributes["anything_future"] == {"nested": True}


def test_metadata_attributes_do_not_drive_applicability() -> None:
    """KnowledgeMetadata and Applicability are separate concerns --
    metadata carries no applicability-evaluation behavior at all.
    """
    metadata = KnowledgeMetadata(attributes={"vendor": "ericsson"})
    assert not hasattr(metadata, "evaluate_applicability")
    assert not hasattr(metadata, "dimensions")
