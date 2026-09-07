"""Deterministic construction of safe, structured governed-knowledge
source/provenance data for the frontend's source drawer (Phase 5.1J
correction pass, Part C) -- the KM counterpart to
`backend/api/source_reference.py`'s Teams-derived `SourceReferenceDTO`.

SOURCE OWNERSHIP (non-negotiable): input here is EXCLUSIVELY the trusted,
backend-side list of `KnowledgeEvidenceItem`s
`backend.tools.knowledge.runtime.snapshot_selected_knowledge_evidence`
returns for one run -- i.e. only what Incident Manager explicitly SELECTED
via `knowledge_select_evidence` this turn, never every item
`knowledge_search` merely returned (AVAILABLE EVIDENCE != SELECTED
EVIDENCE, backend/knowledge/tools/__init__.py). Every field on the
resulting DTO is copied from that trusted item -- never from the model's
own `agent_payload`, never from the model's final answer text, and never
re-derived from the repository after the fact.

`source_uri` is deliberately never read here at all -- see
docs/KNOWLEDGE_CONTRACT.md's Phase 5.1J section, "DO NOT EXPOSE source_uri
YET".
"""
from __future__ import annotations

import uuid

from backend.api.schemas import KnowledgeSourceReferenceDTO
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem

KNOWLEDGE_SOURCE_LABEL = "Governed knowledge"


def _identity(item: KnowledgeEvidenceItem) -> tuple[str, str, "str | None"]:
    return (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)


def build_knowledge_source_references(
    selected_items: list[KnowledgeEvidenceItem],
) -> list[KnowledgeSourceReferenceDTO]:
    """One `KnowledgeSourceReferenceDTO` per DISTINCT selected evidence
    identity (`knowledge_id`/`version_label`/`section_id`) -- deduplicated
    (a model selecting the same item via multiple `knowledge_select_evidence`
    calls, or the same item surviving several searches, must never produce
    two chips for one governed section), preserving the selection order
    `snapshot_selected_knowledge_evidence` itself already returns (first-
    selected order -- see backend/tools/knowledge/runtime.py's own
    `select_evidence`). An empty input list -- no selection this turn --
    returns an empty list, never a fabricated placeholder.
    """
    references: list[KnowledgeSourceReferenceDTO] = []
    seen: set[tuple[str, str, "str | None"]] = set()
    for item in selected_items:
        identity = _identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        references.append(
            KnowledgeSourceReferenceDTO(
                source_id=str(uuid.uuid4()),
                source_type="knowledge",
                label=KNOWLEDGE_SOURCE_LABEL,
                knowledge_id=item.reference.knowledge_id,
                version_label=item.reference.version_label,
                section_id=item.reference.section_id,
                title=item.title,
                document_type=item.document_type.value,
                source_system=item.source.source_system,
                evidence_source_id=item.source.source_id,
                source_display_name=item.source.display_name,
                section_heading=item.section.heading,
                source_locator=item.reference.source_locator,
                content=item.section.content,
            )
        )
    return references
