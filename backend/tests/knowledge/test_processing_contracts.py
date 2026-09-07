"""Phase 5.1D: structural validation of StructuredKnowledgeSection /
StructuredKnowledgeDocument.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.models import KnowledgeSource
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.processing.contracts import StructuredKnowledgeDocument, StructuredKnowledgeSection


def _ingested(**overrides: object) -> IngestedKnowledgeDocument:
    fields: dict[str, object] = {
        "source": KnowledgeSource(source_system="source_a", source_id="doc-1"),
        "title": "Some document",
        "content": "# Heading\nBody",
    }
    fields.update(overrides)
    return IngestedKnowledgeDocument(**fields)


def _section(**overrides: object) -> StructuredKnowledgeSection:
    fields: dict[str, object] = {
        "section_key": "section-0000",
        "sequence": 0,
        "content": "Body text",
    }
    fields.update(overrides)
    return StructuredKnowledgeSection(**fields)


# --- StructuredKnowledgeSection ---------------------------------------


def test_valid_section_accepted() -> None:
    section = _section(heading="Purpose", heading_level=2, source_locator="lines:1-3")
    assert section.heading == "Purpose"
    assert section.heading_level == 2
    assert section.source_locator == "lines:1-3"


def test_section_key_blank_rejected() -> None:
    with pytest.raises(ValidationError):
        _section(section_key="")


def test_content_blank_rejected() -> None:
    with pytest.raises(ValidationError):
        _section(content="")
    with pytest.raises(ValidationError):
        _section(content="   ")


def test_sequence_negative_rejected() -> None:
    with pytest.raises(ValidationError):
        _section(sequence=-1)


def test_heading_blank_rejected_when_present() -> None:
    with pytest.raises(ValidationError):
        _section(heading="", heading_level=1)


@pytest.mark.parametrize("level", [0, 7, -1])
def test_invalid_heading_level_rejected(level: int) -> None:
    with pytest.raises(ValidationError):
        _section(heading="A heading", heading_level=level)


@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
def test_valid_heading_levels_accepted(level: int) -> None:
    section = _section(heading="A heading", heading_level=level)
    assert section.heading_level == level


def test_heading_without_heading_level_rejected() -> None:
    with pytest.raises(ValidationError):
        _section(heading="A heading", heading_level=None)


def test_heading_level_without_heading_rejected() -> None:
    with pytest.raises(ValidationError):
        _section(heading=None, heading_level=1)


def test_no_heading_and_no_level_is_valid() -> None:
    section = _section(heading=None, heading_level=None)
    assert section.heading is None
    assert section.heading_level is None


@pytest.mark.parametrize("locator", ["lines:1-3", "lines:5-5", "lines:1-1000"])
def test_valid_source_locator_accepted(locator: str) -> None:
    assert _section(source_locator=locator).source_locator == locator


@pytest.mark.parametrize("locator", ["", "lines:0-3", "lines:5-2", "not-a-locator", "lines:abc-def", "page:1"])
def test_invalid_source_locator_rejected(locator: str) -> None:
    with pytest.raises(ValidationError):
        _section(source_locator=locator)


def test_source_locator_may_be_absent() -> None:
    assert _section().source_locator is None


# --- StructuredKnowledgeDocument -----------------------------------------


def test_valid_structured_document_accepted() -> None:
    doc = StructuredKnowledgeDocument(source_document=_ingested(), sections=[_section()])
    assert len(doc.sections) == 1


def test_sections_must_be_non_empty() -> None:
    with pytest.raises(ValidationError):
        StructuredKnowledgeDocument(source_document=_ingested(), sections=[])


def test_duplicate_section_keys_rejected() -> None:
    with pytest.raises(ValidationError):
        StructuredKnowledgeDocument(
            source_document=_ingested(),
            sections=[_section(section_key="section-0000", sequence=0), _section(section_key="section-0000", sequence=1)],
        )


def test_duplicate_sequence_rejected() -> None:
    with pytest.raises(ValidationError):
        StructuredKnowledgeDocument(
            source_document=_ingested(),
            sections=[_section(section_key="section-0000", sequence=0), _section(section_key="section-0001", sequence=0)],
        )


def test_source_document_must_be_valid() -> None:
    with pytest.raises(ValidationError):
        StructuredKnowledgeDocument(source_document={"title": ""}, sections=[_section()])


def test_multiple_valid_sections_preserve_order() -> None:
    doc = StructuredKnowledgeDocument(
        source_document=_ingested(),
        sections=[
            _section(section_key="section-0000", sequence=0, heading="First", heading_level=1, content="A"),
            _section(section_key="section-0001", sequence=1, heading="Second", heading_level=1, content="B"),
        ],
    )
    assert [s.heading for s in doc.sections] == ["First", "Second"]
