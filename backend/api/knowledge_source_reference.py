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

B7 LIVE-REGRESSION CORRECTIVE PASS -- EXACT-DUPLICATE PROVENANCE: a real
live-stack combined image+Teams+governed-KM run produced an EXACT duplicate
"Verification" source chip that survived a hard refresh (i.e. was durably
persisted/reprojected, not a transient frontend artifact). Audited first:
`build_knowledge_source_references` below was ALREADY correct -- it has
deduplicated by the canonical `(knowledge_id, version_label, section_id)`
identity since the original Phase 5.1J pass, and `KnowledgeEvidenceSet`'s
own model validator (backend/knowledge/provenance/contracts.py) already
structurally forbids a duplicate identity within one set. No reproducible
in-process defect was found in either mechanism. What WAS missing: neither
`turn_source_references.py`'s persistence write nor its read-time history
projection had any exact-identity normalization of their own -- so IF a
duplicate DTO list ever reached either boundary (from any cause), it would
be faithfully persisted/reprojected forever. `dedupe_knowledge_source_
references`, below, closes that gap generically: exact-identity-only
(`knowledge_id`/`version_label`/`section_id` -- the SAME canonical
identity `build_knowledge_source_references` already uses, never `title`/
`section_heading`/`source_display_name`/content, which can legitimately
collide for two genuinely DISTINCT references), first-seen order
preserved, applied both at the SINGLE point `chat_service.py` builds the
turn's `knowledge_sources` (so the live SSE event and the persisted record
are always identical and already-safe) and, separately, at read-time
projection (for any already-persisted historical turn, without a
migration or KM re-query -- see turn_source_references.py's own updated
docstring).
"""
from __future__ import annotations

import uuid

from backend.api.schemas import KnowledgeSourceReferenceDTO
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem

KNOWLEDGE_SOURCE_LABEL = "Governed knowledge"


def _identity(item: KnowledgeEvidenceItem) -> tuple[str, str, "str | None"]:
    return (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)


def _dto_identity(dto: KnowledgeSourceReferenceDTO) -> tuple[str, str, "str | None"]:
    return (dto.knowledge_id, dto.version_label, dto.section_id)


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


def dedupe_knowledge_source_references(
    references: list[KnowledgeSourceReferenceDTO],
) -> list[KnowledgeSourceReferenceDTO]:
    """B7 live-regression corrective pass -- defensive, generic, identity-
    only normalization for an already-built `list[KnowledgeSourceReference
    DTO]`, keyed EXCLUSIVELY by the canonical `(knowledge_id, version_
    label, section_id)` identity (never `source_id`, which this module's
    own `build_knowledge_source_references` documents as a synthetic
    per-construction `uuid4`, not an identity field -- see `schemas.py`'s
    own `KnowledgeSourceReferenceDTO.source_id` docstring). First-seen
    order is preserved; a later duplicate of an already-seen identity is
    dropped, never the earlier one (so a caller passing an already-correct
    list gets it back byte-for-byte, order included).

    Deliberately NEVER dedupes by `title`/`section_heading`/`source_
    display_name`/`content` -- two references sharing the same document
    (even the same title) but a DIFFERENT `section_id` are two distinct,
    legitimately-selected evidence identities and must both survive (e.g.
    "Verification" and "Escalation" sections of the same approved
    procedure). Two references sharing the same `section_id` but a
    DIFFERENT `version_label` are likewise distinct (a version boundary
    genuinely changes what was actually cited) and must both survive.

    Reusable at any boundary that already has a `KnowledgeSourceReference
    DTO` list in hand (construction-time in chat_service.py, read-time
    projection in turn_source_references.py) -- never re-derives anything
    from `KnowledgeEvidenceItem`/the repository, so it is safe to call on
    data that was serialized and later deserialized (e.g. from persisted
    session state) just as safely as on freshly-built DTOs.
    """
    result: list[KnowledgeSourceReferenceDTO] = []
    seen: set[tuple[str, str, "str | None"]] = set()
    for reference in references:
        identity = _dto_identity(reference)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(reference)
    return result
