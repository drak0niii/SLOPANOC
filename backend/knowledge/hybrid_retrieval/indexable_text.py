"""Phase 6A.5: deterministic indexable-text construction (instruction
section 14).

Combines only structurally-meaningful fields (section heading, section
content, and -- for a table/sheet artifact -- its own locator identity)
-- never pollutes embedding input with arbitrary metadata (applicability
labels, internal ids, etc.). SOURCE vs DERIVED origin is preserved as a
separate boolean (`is_derived`), never folded into the text itself --
derived image interpretation is never allowed to masquerade as source
text (instruction section 14/16): the text itself is exactly what the
artifact/section already contains (an image's own DERIVED description,
if that's what the section's content came from), and `is_derived`
records that fact structurally for reranking (reranking.py) to use.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from backend.knowledge.domain.artifacts import KnowledgeArtifact
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection

__all__ = ["build_indexable_text", "resolve_is_derived", "content_hash"]


def _find_artifact(knowledge_object: KnowledgeObject, artifact_id: Optional[str]) -> Optional[KnowledgeArtifact]:
    if artifact_id is None:
        return None
    for artifact in knowledge_object.artifacts:
        if artifact.artifact_id == artifact_id:
            return artifact
    return None


def resolve_is_derived(knowledge_object: KnowledgeObject, section: KnowledgeSection) -> bool:
    """Byte-for-byte the same resolution 5.1G's own `retrieval/service.py`
    `_resolve_is_derived` already performs -- deliberately duplicated
    (not imported) so `hybrid_retrieval/` stays independent of the frozen
    `retrieval/` package (same architectural-layer independence 6A.4's
    own `narrowing/eligibility.py` already established for `retrieval/
    service.py`'s `_group_by_knowledge_id`), never a model claim.
    """
    if section.artifact_id is None:
        return False
    artifact = _find_artifact(knowledge_object, section.artifact_id)
    return artifact.derived if artifact is not None else False


def build_indexable_text(knowledge_object: KnowledgeObject, section: KnowledgeSection) -> str:
    """Deterministic text representation for one evidence unit. Always
    includes the section's own heading (if any) and content. For a
    table/sheet-shaped artifact (`kind` containing "table" or "sheet"),
    additionally prepends the artifact's own `display_name`/
    `locator_detail` so a table search can match its own sheet/range
    identity (instruction section 15) without requiring separate
    disconnected cell text.
    """
    parts: list[str] = []
    artifact = _find_artifact(knowledge_object, section.artifact_id)
    if artifact is not None and artifact.kind and ("table" in artifact.kind or "sheet" in artifact.kind):
        if artifact.display_name:
            parts.append(artifact.display_name)
        if artifact.locator_detail:
            parts.append(artifact.locator_detail)
    if section.heading:
        parts.append(section.heading)
    parts.append(section.content)
    return "\n".join(p for p in parts if p)


def content_hash(text: str) -> str:
    """SHA-256 of the exact indexable text -- the basis for skip-
    unchanged/re-embed-on-change idempotent indexing (§24)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
