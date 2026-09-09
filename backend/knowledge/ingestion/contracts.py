"""The generic, source-agnostic ingestion document contract (Phase 5.1C).

`IngestedKnowledgeDocument` is what every future source adapter must
produce, regardless of source system -- normalized, but deliberately
UNSEGMENTED (5.1D owns turning this into `KnowledgeSection`s) and
deliberately PRE-GOVERNANCE (5.1E owns lifecycle/version authority; this
contract has no `LifecycleStatus` field at all -- see this module's
"INGESTED != APPROVED" note below).

Only STRUCTURAL validation belongs here -- non-blank text fields, and
reuse of `domain/`'s own validated value objects for source/metadata/
applicability/version -- never document classification, lifecycle
authority, applicability evaluation, version resolution, or duplicate
detection.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.knowledge.domain.artifacts import KnowledgeArtifact, validate_artifact_lineage
from backend.knowledge.domain.enums import KnowledgeDocumentType
from backend.knowledge.domain.models import Applicability, KnowledgeMetadata, KnowledgeSource, KnowledgeVersion, require_non_blank


class IngestedKnowledgeDocument(BaseModel):
    """The normalized output of any source adapter, BEFORE structural
    segmentation and BEFORE lifecycle governance.

    INGESTED != APPROVED: this model deliberately has no
    `LifecycleStatus` field. A document entering the pipeline has not
    thereby gained any operational authority -- see
    docs/KNOWLEDGE_CONTRACT.md's Phase 5.1C section for the full
    rationale. If source lifecycle information ever needs to be
    preserved, that is explicit future work, not an implicit
    default here.

    Reuses `domain/`'s own value objects rather than forking
    source-specific equivalents: `source` is a plain `KnowledgeSource`
    (never a `SharePointKnowledgeSource`/`GCSKnowledgeSource`/...),
    `metadata`/`applicability`/`version_hint` are the exact same
    `KnowledgeMetadata`/`Applicability`/`KnowledgeVersion` models
    `KnowledgeObject` itself uses.
    """

    source: KnowledgeSource = Field(description="Provenance identity, carried through unchanged -- this boundary never rewrites or manufactures source identity.")
    title: str
    content: str = Field(
        description=(
            "The source document's normalized textual payload, after whatever "
            "source-specific extraction a future adapter performs (PDF/DOCX/HTML/"
            "...) -- NOT yet KnowledgeSection[], not chunked, not summarized, not "
            "semantically classified. No maximum size is imposed at this boundary."
        )
    )

    knowledge_id_hint: Optional[str] = Field(
        default=None,
        description=(
            "A logical document/knowledge id the SOURCE already knows, if any -- a "
            "HINT only, never assumed to equal the eventual governed "
            "KnowledgeObject.knowledge_id, and never derived by hashing content or "
            "any other ID-generation strategy invented here. Identity governance is "
            "later work."
        ),
    )
    document_type_hint: Optional[KnowledgeDocumentType] = Field(
        default=None, description="The document type, if the source/adapter already knows it -- never inferred here (no model call, no classification logic)."
    )
    version_hint: Optional[KnowledgeVersion] = Field(
        default=None,
        description=(
            "The source's own document revision, if exposed, using the exact same "
            "KnowledgeVersion shape KnowledgeObject uses. This is the DOCUMENT's "
            "revision (e.g. \"4.2\") -- never conflated with an operational software "
            "release, which (if supplied at all) belongs in `applicability`'s own "
            "dimensions instead (the same 5.1B distinction, preserved here)."
        ),
    )

    metadata: KnowledgeMetadata = Field(default_factory=KnowledgeMetadata)
    applicability: Applicability = Field(
        default_factory=Applicability,
        description="Transported through unchanged if the source/adapter supplies it -- never evaluated, never inferred from content, never model-generated here. 5.1B remains the sole applicability evaluator.",
    )

    media_type: Optional[str] = Field(default=None, description='Free-form content-type label if useful (e.g. a MIME type) -- never a fixed enum, never branched on in this generic boundary.')
    artifacts: list[KnowledgeArtifact] = Field(
        default_factory=list,
        description=(
            "A5: the compound-artifact tree discovered by a source adapter's own extraction (embedded images, "
            "spreadsheets, nested documents, logs). Empty for a plain-text document -- exactly the same as before "
            "A5's compound-artifact support existed. See backend/knowledge/domain/artifacts.py."
        ),
    )
    source_revision: Optional[str] = Field(
        default=None,
        description="An opaque revision token/ETag/modification marker from the source, if exposed -- carried through uninterpreted, never compared or used for version resolution here.",
    )
    observed_at: Optional[datetime] = Field(default=None, description="When this document was retrieved/observed by the adapter, if useful -- carried through only, never interpreted here.")

    @field_validator("title", "content")
    @classmethod
    def _non_blank(cls, value: str, info: Any) -> str:
        return require_non_blank(value, info.field_name)

    @field_validator("knowledge_id_hint", "source_revision", "media_type")
    @classmethod
    def _non_blank_if_present(cls, value: Optional[str], info: Any) -> Optional[str]:
        if value is None:
            return value
        return require_non_blank(value, info.field_name)

    @model_validator(mode="after")
    def _validate_artifacts(self) -> "IngestedKnowledgeDocument":
        validate_artifact_lineage(self.artifacts)
        return self
