"""The generic provenance/evidence contracts and error hierarchy.

Only STRUCTURAL validation belongs here -- internal consistency of one
evidence item, no duplicate identities within one evidence set, non-blank
selection-key fields -- never the actual repository revalidation logic
itself (service.py).
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from backend.knowledge.domain.contracts import KnowledgeEvidenceReference
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource, require_non_blank


class KnowledgeProvenanceError(Exception):
    """Base class for every error this package raises. Never constructed
    from raw document/payload content -- messages carry identity
    information only (knowledge_id/version_label/section_id/exception
    class names), never section text or a raw storage payload.
    """


class KnowledgeEvidenceNotFoundError(KnowledgeProvenanceError):
    """Raised when a `KnowledgeRetrievalItem` claims a governed object or
    section that does not actually exist in the repository at evidence-
    build time -- a retrieval result that cannot be grounded never
    becomes trusted evidence, and never falls back to another version or
    a partial evidence set.
    """


class KnowledgeEvidenceMismatchError(KnowledgeProvenanceError):
    """Raised when a `KnowledgeRetrievalItem`'s claimed section/document
    fields do not exactly match the freshly-fetched governed repository
    state (including because the repository has since changed under a
    stale retrieval result). No fuzzy/"close enough" comparison exists --
    an exact field mismatch always fails closed.
    """


class KnowledgeEvidenceSelectionError(KnowledgeProvenanceError):
    """Raised when a requested evidence selection references an identity
    absent from the bounded `KnowledgeEvidenceSet` -- whether because it
    is entirely fabricated or because it names a real repository section
    that was simply never part of this turn's retrieved evidence. A
    selection either validates completely or fails completely; no partial
    trust is ever returned.
    """


class KnowledgeEvidenceItem(BaseModel):
    """One actual, validated piece of governed knowledge -- reuses
    `KnowledgeSection`/`KnowledgeSource` directly (the same discipline
    `KnowledgeContextItem`/`KnowledgeRetrievalItem` already follow) rather
    than duplicating heading/content/source fields into new copies.
    `reference` is the stable, generic `KnowledgeEvidenceReference`
    (5.1A) -- constructed by `service.py` ONLY from validated
    authoritative repository objects, never from a caller/model claim.
    Contains no hidden/model reasoning of any kind.
    """

    reference: KnowledgeEvidenceReference
    title: str
    document_type: KnowledgeDocumentType
    lifecycle_status: LifecycleStatus
    source: KnowledgeSource
    section: KnowledgeSection

    @field_validator("title")
    @classmethod
    def _title_non_blank(cls, value: str) -> str:
        return require_non_blank(value, "title")

    @model_validator(mode="after")
    def _internally_consistent(self) -> "KnowledgeEvidenceItem":
        if self.reference.knowledge_id != self.section.knowledge_id:
            raise ValueError(
                f"reference.knowledge_id {self.reference.knowledge_id!r} does not match "
                f"section.knowledge_id {self.section.knowledge_id!r}"
            )
        if self.reference.section_id != self.section.section_id:
            raise ValueError(
                f"reference.section_id {self.reference.section_id!r} does not match "
                f"section.section_id {self.section.section_id!r}"
            )
        if self.reference.source_system != self.source.source_system or self.reference.source_id != self.source.source_id:
            raise ValueError(
                "reference source identity does not match this item's own KnowledgeSource "
                f"({self.reference.source_system!r}/{self.reference.source_id!r} vs. "
                f"{self.source.source_system!r}/{self.source.source_id!r})"
            )
        return self


def _identity(item: KnowledgeEvidenceItem) -> tuple[str, str, "str | None"]:
    return (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)


class KnowledgeEvidenceSet(BaseModel):
    """The bounded, validated snapshot of evidence derived from one
    5.1G `KnowledgeRetrievalResult`. Order preserves the retrieval
    ranking order this was built from (service.py never reorders).
    Deterministic, serializable (plain Pydantic `BaseModel`), no hidden
    state, no implicitly-generated timestamp, no global registry, and
    never persisted by this package.
    """

    items: list[KnowledgeEvidenceItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_identities(self) -> "KnowledgeEvidenceSet":
        seen: set[tuple[str, str, "str | None"]] = set()
        for item in self.items:
            identity = _identity(item)
            if identity in seen:
                raise ValueError(f"duplicate evidence identity in one KnowledgeEvidenceSet: {identity}")
            seen.add(identity)
        return self


class KnowledgeEvidenceSelectionKey(BaseModel):
    """The MINIMUM identity necessary for a future caller/model to SELECT
    an already-validated evidence item -- deliberately unable to carry
    section content, source identity, title, document type, lifecycle
    status, or a locator/snippet of any kind. Those authoritative values
    always come back from the backend's own `KnowledgeEvidenceSet`, never
    from the selector. This is the trust boundary 5.1I/5.1J will build
    on: a model may choose WHICH evidence to cite, but Python alone
    determines WHAT that evidence actually is.

    A CLOSED identity-only contract: `model_config = extra="forbid"`
    means any field other than `knowledge_id`/`version_label`/
    `section_id` is a deterministic validation FAILURE, never a silently
    dropped/ignored value -- a selector attempting to smuggle `content`,
    `source_system`, or any other authoritative-looking field alongside a
    real identity is rejected outright, strengthening MODEL SELECTION !=
    PROVENANCE beyond "the extra field simply has no effect".
    """

    model_config = ConfigDict(extra="forbid")

    knowledge_id: str
    version_label: str
    section_id: str

    @field_validator("knowledge_id", "version_label", "section_id")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        return require_non_blank(value, info.field_name)
