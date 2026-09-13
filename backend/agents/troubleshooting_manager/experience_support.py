"""Phase 6A.9: bounded, structured, owner-scoped Experience Memory
retrieval for a troubleshooting turn -- the IMPURE coordinator half of
Experience consumption.

RESOLVES SKILL FIRST (§64): callers pass the ALREADY-resolved
`skill_id`/`skill_version` (from `skill_resolution.py`), never re-derive
it here. QUERY-TIME OWNER ISOLATION is entirely `ExperienceMemoryService
.query`'s own responsibility (6A.8, unmodified) -- this module adds only
a small, generic, deterministic bound (`_DEFAULT_EXPERIENCE_LIMIT`,
never query/corpus-tuned) and the metadata projection the Intelligence
Package needs (§101). NO SEMANTIC/SIMILARITY RANKING OF ANY KIND is
performed anywhere in this module (§65/§67) -- only 6A.8's own
already-deterministic `recorded_at DESC, experience_id ASC` order.
"""
from __future__ import annotations

from typing import Optional

from backend.experience_memory.domain.models import ExperienceMemoryResult, ExperienceQuery, ExperienceRecord
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService, get_experience_memory_service
from backend.troubleshooting_intelligence.contracts import ExperienceQueryMetadata

__all__ = ["DEFAULT_EXPERIENCE_LIMIT", "query_experience_support"]

DEFAULT_EXPERIENCE_LIMIT = 20
"""Deliberately generic, not query/corpus-tuned -- matches this
codebase's own "deliberately generic bound" convention (e.g. 6A.5's
`RRF_K`, 6A.6's `_DEFAULT_MAX_EVIDENCE_ITEM_CHARACTERS`)."""


async def query_experience_support(
    *,
    owner_id: str,
    skill_id: Optional[str],
    skill_version: Optional[str],
    case_id: Optional[str],
    service: Optional[ExperienceMemoryService] = None,
    limit: int = DEFAULT_EXPERIENCE_LIMIT,
) -> tuple[list[ExperienceRecord], ExperienceQueryMetadata]:
    """Returns `(records, query_metadata)` -- `records` is ALWAYS a real,
    already owner-scoped list; an empty list is a completely valid result
    (§79), never an error, never a trigger to broaden scope or query
    another owner."""
    memory_service = service if service is not None else get_experience_memory_service()
    query = ExperienceQuery(owner_id=owner_id, case_id=case_id, skill_id=skill_id, skill_version=skill_version, limit=limit)
    result: ExperienceMemoryResult = await memory_service.query(query)
    metadata = ExperienceQueryMetadata(
        owner_id=result.owner_id,
        applied_filters=result.applied_filters,
        result_count=result.count,
        limit=result.limit,
    )
    return result.records, metadata
