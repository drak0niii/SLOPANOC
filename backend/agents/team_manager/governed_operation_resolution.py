"""POST-6A REPAIR 2/3/4 -- Resolving THIS turn's governed operation.

The single, deterministic bridge between "which governed section did this
turn actually select and revalidate" (already owned by `evidence.py` /
`governed_evidence_continuity.py`, both untouched) and "what does
governance positively say about the operation that section defines"
(`GovernedOperationDescriptor`, repair 3).

TWO INDEPENDENT APPROVALS ARE REQUIRED, AND BOTH ARE RE-CHECKED HERE:

  1. the owning knowledge must itself be `LifecycleStatus.APPROVED` -- a
     descriptor riding along on CANDIDATE knowledge has no more authority
     than the CANDIDATE knowledge it came from;
  2. the descriptor's own `authority` must be `APPROVED` -- ingestion and
     model extraction both produce `CANDIDATE`, and only the human-gated
     governance transition can change that.

Anything else resolves to `None`, which every consumer treats as "nothing
established" -- never as "safe". The descriptor is also re-bound to the
real source identity of the evidence item it was found on, so a
descriptor cannot assert a knowledge/version/section identity of its own.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from backend.knowledge.domain.enums import LifecycleStatus
from backend.knowledge.domain.operation_descriptor import (
    GovernedOperationDescriptor,
    OperationDescriptorAuthority,
)

_logger = logging.getLogger(__name__)


def _matches(item: Any, knowledge_id: str, version_label: Optional[str], section_id: str) -> bool:
    reference = getattr(item, "reference", None)
    section = getattr(item, "section", None)
    if reference is None or section is None:
        return False
    return (
        getattr(reference, "knowledge_id", None) == knowledge_id
        and getattr(reference, "version_label", None) == version_label
        and getattr(section, "section_id", None) == section_id
    )


def resolve_governed_operation_descriptor(
    selected_evidence: Sequence[Any],
    *,
    knowledge_id: Optional[str],
    version_label: Optional[str],
    section_id: Optional[str],
) -> Optional[GovernedOperationDescriptor]:
    """The APPROVED descriptor for the governed section this turn is
    actually acting on, or `None`.

    `selected_evidence` is this run's own trusted `KnowledgeEvidenceItem`
    list (backend-built, never model-supplied -- the SAME set every other
    grounding layer already consults). The three identity arguments come
    from the already-resolved active procedure anchor. All three must be
    present: a partially-identified section is not identified.
    """
    if not selected_evidence or not knowledge_id or not section_id:
        return None
    for item in selected_evidence:
        if not _matches(item, knowledge_id, version_label, section_id):
            continue
        descriptor = getattr(getattr(item, "section", None), "operation", None)
        if descriptor is None:
            return None
        if getattr(item, "lifecycle_status", None) != LifecycleStatus.APPROVED:
            _logger.info(
                "governed_operation: descriptor ignored -- owning knowledge is not APPROVED knowledge_id=%s section_id=%s",
                knowledge_id,
                section_id,
            )
            return None
        if descriptor.authority != OperationDescriptorAuthority.APPROVED:
            _logger.info(
                "governed_operation: descriptor ignored -- authority=%s (only APPROVED may authorize) "
                "knowledge_id=%s section_id=%s",
                descriptor.authority.value,
                knowledge_id,
                section_id,
            )
            return None
        # Identity is stamped from the evidence item, never trusted from
        # the descriptor's own stored fields.
        return descriptor.bind_to_source(
            knowledge_id=knowledge_id, version_label=version_label, section_id=section_id
        )
    return None
