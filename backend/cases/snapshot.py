"""Deterministic, budgeted `CaseContextSnapshot` construction (instruction
sections 18-20).

Never NLP/semantic relevance scoring -- selection is entirely a function
of explicit `kind`/`created_at` metadata, so the exact same set of
`CaseContextItemDTO`s always produces the exact same snapshot (tested
explicitly). The full ledger stays in the database; this function is the
only thing that ever decides what subset reaches a model call.
"""
from __future__ import annotations

from typing import Optional

from backend.cases.schemas import (
    CaseContextItemDTO,
    CaseContextSnapshot,
    CaseContextSnapshotItem,
    CaseDTO,
    ContextItemKind,
)
from backend.config.settings import Settings, get_settings

# Lower number = higher priority (instruction section 20's suggested
# category ordering: decisions/resolution, then observations/evidence,
# then risks/open questions, then hypotheses, then recommendations/
# actions). Ties within a tier are broken by recency (most recent first).
_PRIORITY_TIER: dict[ContextItemKind, int] = {
    ContextItemKind.DECISION: 0,
    ContextItemKind.RESOLUTION: 0,
    ContextItemKind.OBSERVATION: 1,
    ContextItemKind.EVIDENCE: 1,
    ContextItemKind.RISK: 2,
    ContextItemKind.OPEN_QUESTION: 2,
    ContextItemKind.HYPOTHESIS: 3,
    ContextItemKind.RECOMMENDATION: 4,
    ContextItemKind.ACTION: 4,
}


def build_case_context_snapshot(
    case: CaseDTO,
    items: list[CaseContextItemDTO],
    settings: Optional[Settings] = None,
) -> CaseContextSnapshot:
    """`items` should be the full ledger for `case` (as
    `CaseService.get_context_items` returns it, chronological). Selection
    here never mutates or reorders the authoritative ledger -- it only
    decides which items make it into this one snapshot.
    """
    settings = settings or get_settings()
    max_items = settings.case_context_max_items
    max_characters = settings.case_context_max_characters

    ranked = sorted(items, key=lambda item: (_PRIORITY_TIER.get(item.kind, 5), -item.created_at.timestamp()))

    selected: list[CaseContextItemDTO] = []
    total_characters = 0
    for item in ranked:
        if len(selected) >= max_items:
            break
        item_characters = len(item.content)
        if selected and total_characters + item_characters > max_characters:
            # Stop at the first item that would break the character
            # budget -- deterministic and simple: never reorders lower-
            # priority-but-smaller items ahead of a higher-priority one
            # just to fill remaining space. Always keep at least the
            # single highest-priority item even if it alone exceeds the
            # budget (an empty snapshot would be worse than one
            # over-budget item).
            break
        selected.append(item)
        total_characters += item_characters

    # Selection order is priority-based; presentation order is
    # chronological (most natural for a model reading a case's history).
    presentation_order = sorted(selected, key=lambda item: item.created_at)

    snapshot_items = [
        CaseContextSnapshotItem(
            kind=item.kind,
            content=item.content,
            source_type=item.source_type,
            source_author=item.source_author,
            created_at=item.created_at,
        )
        for item in presentation_order
    ]

    return CaseContextSnapshot(
        case_id=case.case_id,
        title=case.title,
        status=case.status,
        problem_statement=case.problem_statement,
        external_reference=case.external_reference,
        items=snapshot_items,
        context_truncated=len(selected) < len(items),
        total_item_count=len(items),
        included_item_count=len(selected),
    )
