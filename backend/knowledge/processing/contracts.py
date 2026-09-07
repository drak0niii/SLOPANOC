"""The structured, pre-governance processing output contract (Phase
5.1D).

Only STRUCTURAL validation belongs here -- non-blank identifiers/content,
internal consistency between a `StructuredKnowledgeDocument` and its own
`StructuredKnowledgeSection`s -- never document classification, lifecycle
authority, applicability evaluation, or version resolution.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.knowledge.domain.models import require_non_blank
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument

_SOURCE_LOCATOR_PATTERN = re.compile(r"^lines:(\d+)-(\d+)$")


class StructuredKnowledgeSection(BaseModel):
    """One deterministically-identified, structurally-segmented piece of
    a source document's content -- LOCAL / PRE-GOVERNANCE identity only.

    `section_key` is NOT `KnowledgeSection.section_id`, and this section
    has no `knowledge_id` at all: final governed section identity (5.1E/
    5.1F) may not exist yet at this point in the pipeline (the source
    document's `knowledge_id_hint` is only a hint -- see
    `IngestedKnowledgeDocument`). `section_key` only needs to be unique
    within the document it was produced from; it is not required to
    remain stable across a different revision of the same source.

    `heading`/`heading_level` reflect structural SYNTAX only (e.g. a
    Markdown-style `#`/`##`/... marker) -- never a semantic
    classification. `heading` is `None` for content that precedes the
    first detected heading (or for the single fallback section when no
    structure was detected at all); `heading_level` is `None` exactly
    when `heading` is `None`.

    `source_locator` (`"lines:<start>-<end>"`, 1-based, inclusive)
    covers exactly the original document lines this section's `content`
    was taken from -- never a source-system-specific locator (no
    SharePoint page id, PDF coordinate, or Drive block id belongs here;
    a future source-specific adapter/extractor may preserve those
    separately, outside this generic contract).
    """

    section_key: str
    sequence: int = Field(description="Deterministic, non-negative ordering position among a document's sections -- assigned in document order, never randomly.")
    heading: Optional[str] = Field(default=None, description="The heading text exactly as it appeared in the source, if this section followed an explicit structural heading.")
    heading_level: Optional[int] = Field(default=None, description="The structural heading depth (e.g. 1 for '#', 2 for '##', ...), if heading is set.")
    content: str
    source_locator: Optional[str] = Field(default=None, description='"lines:<start>-<end>", 1-based inclusive, or None if not determinable.')

    @field_validator("section_key", "content")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        return require_non_blank(value, info.field_name)

    @field_validator("heading")
    @classmethod
    def _heading_non_blank_if_present(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return require_non_blank(value, "heading")

    @field_validator("sequence")
    @classmethod
    def _sequence_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("sequence must not be negative")
        return value

    @field_validator("heading_level")
    @classmethod
    def _heading_level_valid_if_present(cls, value: Optional[int]) -> Optional[int]:
        if value is None:
            return value
        if not 1 <= value <= 6:
            raise ValueError("heading_level must be between 1 and 6")
        return value

    @field_validator("source_locator")
    @classmethod
    def _source_locator_valid_if_present(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        match = _SOURCE_LOCATOR_PATTERN.match(value)
        if not match:
            raise ValueError('source_locator must match "lines:<start>-<end>" (1-based, inclusive)')
        start, end = int(match.group(1)), int(match.group(2))
        if start < 1 or end < start:
            raise ValueError("source_locator start must be >= 1 and end must be >= start")
        return value

    @model_validator(mode="after")
    def _heading_and_level_are_both_set_or_both_unset(self) -> "StructuredKnowledgeSection":
        if (self.heading is None) != (self.heading_level is None):
            raise ValueError("heading and heading_level must both be set or both be unset")
        return self


class StructuredKnowledgeDocument(BaseModel):
    """The structural-processing output for one `IngestedKnowledgeDocument`
    -- STRICTLY PRE-GOVERNANCE: no `LifecycleStatus`, no final governed
    `knowledge_id`, no final `KnowledgeSection` identity, no repository
    identity, no retrieval/ranking score, no agent-specific field.

    `source_document` is the exact, unmodified `IngestedKnowledgeDocument`
    this was produced from -- its `source`/`title`/`content`/hints/
    `metadata`/`applicability` are never rewritten, enriched, inferred,
    or evaluated by structural processing (see the module docstring).
    """

    source_document: IngestedKnowledgeDocument
    sections: list[StructuredKnowledgeSection] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_sections(self) -> "StructuredKnowledgeDocument":
        seen_keys: set[str] = set()
        seen_sequences: set[int] = set()
        for section in self.sections:
            if section.section_key in seen_keys:
                raise ValueError(f"duplicate section_key {section.section_key!r}")
            seen_keys.add(section.section_key)
            # Mirrors KnowledgeObject's own section-sequence invariant
            # (backend/knowledge/domain/models.py): a duplicate sequence
            # value is REJECTED, never silently normalized, so ordering
            # never depends on an implicit construction-order accident.
            if section.sequence in seen_sequences:
                raise ValueError(f"duplicate section sequence {section.sequence!r}")
            seen_sequences.add(section.sequence)
        return self
