"""Provenance/evidence identity primitives and the Knowledge Context
output contract.

Neither type here implements behavior (validation against real evidence,
ranking, snippet extraction, citation rendering) -- both are pure,
generic DATA SHAPES so that later phases (5.1G retrieval, 5.1H
provenance) have stable identifiers/output shapes to build against
without needing to change this domain's own model. See
docs/KNOWLEDGE_CONTRACT.md.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeSection, require_non_blank


class KnowledgeEvidenceReference(BaseModel):
    """A stable, generic reference to a real, identifiable piece of
    governed knowledge -- document-level (`section_id` unset) or
    section-level (`section_id` set). This is IDENTITY only: no
    enrichment, snippet extraction, ranking, or UI rendering is defined
    here -- 5.1H builds the full Knowledge Provenance capability using
    references shaped like this one as its stable anchor.
    """

    knowledge_id: str
    version_label: str = Field(description="The KnowledgeVersion.label this reference was resolved against.")
    section_id: Optional[str] = Field(default=None, description="Set for a section-level reference; unset for a document-level reference.")
    source_system: str
    source_id: str
    source_locator: Optional[str] = None

    @field_validator("knowledge_id", "version_label", "source_system", "source_id")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        return require_non_blank(value, info.field_name)


class KnowledgeContextItem(BaseModel):
    """The generic shape that leaves the KM domain once knowledge has
    been selected for consumption by a future context/reasoning layer.

    This is NOT retrieval logic, NOT an ADK tool result, and NOT the
    future Context Engineering Layer -- it only defines what a piece of
    selected, governed knowledge looks like once it crosses the KM
    domain boundary: generic, agent-agnostic, with no ranking score or
    retrieval-engine-internal field baked in. 5.1G/5.1H own how these are
    actually produced (retrieval, ranking, provenance enrichment); this
    phase only defines the shape.
    """

    knowledge_id: str
    document_type: KnowledgeDocumentType
    title: str
    version_label: str
    lifecycle_status: LifecycleStatus
    applicability: Applicability = Field(default_factory=Applicability)
    sections: list[KnowledgeSection] = Field(
        default_factory=list, description="The selected section(s) of this knowledge object relevant to the current selection."
    )
    evidence: list[KnowledgeEvidenceReference] = Field(default_factory=list)

    @field_validator("knowledge_id", "title", "version_label")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        return require_non_blank(value, info.field_name)

    @model_validator(mode="after")
    def _sections_belong_to_this_item(self) -> "KnowledgeContextItem":
        for section in self.sections:
            if section.knowledge_id != self.knowledge_id:
                raise ValueError(
                    f"section {section.section_id!r} has knowledge_id "
                    f"{section.knowledge_id!r}, which does not match this "
                    f"KnowledgeContextItem's knowledge_id {self.knowledge_id!r}"
                )
        return self
