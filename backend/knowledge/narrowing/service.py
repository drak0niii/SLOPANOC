"""Phase 6A.4: `narrow_knowledge` -- the single composition entry point.

Depends only on `KnowledgeRepository` (never a storage detail), mirroring
`KnowledgeRetrievalService`'s own dependency shape. Read-only: never
calls `repository.add`/`replace`, never mutates a `KnowledgeObject`,
never persists narrowing state (§35 -- no new table, no JSONB index; the
existing `list_all()` in-process filter is used at this milestone's own
measured scale -- see the closure report's §L for the real corpus
timing).

PERFORMANCE (§34): no model call anywhere in this module or anything it
calls. `narrow_knowledge` is `N Knowledge objects × 1 deterministic
Python pass` -- no `N × M model calls` of any kind.

NOT WIRED INTO THE LIVE `knowledge_search` TOOL (§47, a deliberate
choice, mirroring 6A.2's own `dimension_scope` and 6A.3's own
`ingest_and_structure_local_file`, neither of which are wired into their
respective live paths either): this module is a new, additive, internal
service/repository-level API. `KnowledgeRetrievalService.retrieve()`
(5.1G) is BYTE-FOR-BYTE UNMODIFIED by this milestone -- verified by an
empty scoped `git diff`. A future milestone (6A.5 or later) may choose to
consume this module's output to bound `retrieve()`'s own corpus; that
wiring decision is explicitly deferred, not made here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.context.domain.enums import ContextDimension
from backend.context.domain.models import ContextValue
from backend.knowledge.domain.models import KnowledgeObject
from backend.knowledge.narrowing.applicability_gate import evaluate_telco_applicability
from backend.knowledge.narrowing.contracts import (
    EligibilityDecision,
    KnowledgeNarrowingItem,
    KnowledgeNarrowingResult,
    NarrowingPolicy,
    NarrowingReasonCode,
)
from backend.knowledge.narrowing.eligibility import evaluate_eligibility_for_corpus
from backend.knowledge.repository.contracts import KnowledgeRepository

__all__ = ["narrow_knowledge", "narrow_corpus"]


def _apply_policy(decision: EligibilityDecision, policy: NarrowingPolicy) -> EligibilityDecision:
    """The ONE call site where `NarrowingPolicy`'s OBSERVE/ENFORCE flags
    are actually consulted (`eligibility.py` itself never reads
    `NarrowingPolicy` at all -- see that module's own docstring). A
    version-authority exclusion (already `eligible=False`) is never
    "un-excluded" by policy -- policy can only ADD an exclusion reason to
    an object that was otherwise version-eligible, never remove one.
    """
    if not decision.eligible:
        return decision

    extra_reasons: list[NarrowingReasonCode] = []
    if policy.enforce_ai_approved and decision.ai_approved is not True:
        extra_reasons.append(
            NarrowingReasonCode.AI_APPROVAL_MISSING if decision.ai_approved is None else NarrowingReasonCode.AI_APPROVAL_FALSE
        )
    if policy.enforce_not_expired and decision.expired is True:
        extra_reasons.append(NarrowingReasonCode.EXPIRED)

    if not extra_reasons:
        return decision
    return decision.model_copy(update={"eligible": False, "reason_codes": decision.reason_codes + extra_reasons})


def narrow_corpus(
    corpus: list[KnowledgeObject],
    context_state: dict[ContextDimension, ContextValue],
    *,
    as_of: datetime,
    policy: Optional[NarrowingPolicy] = None,
) -> KnowledgeNarrowingResult:
    """The pure, synchronous, corpus-in/result-out narrowing pipeline --
    no repository dependency, so this function alone is trivially
    testable with a hand-built corpus (used throughout this milestone's
    own integration tests). `narrow_knowledge` (below) is the thin,
    repository-backed async wrapper real callers use.
    """
    resolved_policy = policy or NarrowingPolicy()

    eligibility_by_identity: dict[tuple[str, str], EligibilityDecision] = {}
    for decision in evaluate_eligibility_for_corpus(corpus, as_of):
        applied = _apply_policy(decision, resolved_policy)
        eligibility_by_identity[(applied.knowledge_id, applied.version_label)] = applied

    items: list[KnowledgeNarrowingItem] = []
    permitted: list[str] = []
    # POST-6A: the per-VERSION permitted set, kept alongside the (lossy)
    # id-only projection so downstream retrieval can constrain on real
    # version identity rather than on a bare knowledge_id.
    permitted_version_keys: list[tuple[str, str]] = []
    indeterminate_version_keys: list[tuple[str, str]] = []
    excluded: list[str] = []
    indeterminate: list[str] = []
    exclusion_reason_counts: dict[str, int] = {}

    for knowledge_object in corpus:
        identity = (knowledge_object.knowledge_id, knowledge_object.version.label)
        eligibility = eligibility_by_identity[identity]

        if not eligibility.eligible:
            items.append(
                KnowledgeNarrowingItem(
                    knowledge_id=knowledge_object.knowledge_id,
                    version_label=knowledge_object.version.label,
                    eligibility=eligibility,
                    applicability=None,
                    final_bucket="excluded",
                )
            )
            excluded.append(knowledge_object.knowledge_id)
            for reason in eligibility.reason_codes:
                exclusion_reason_counts[reason.value] = exclusion_reason_counts.get(reason.value, 0) + 1
            continue

        applicability = evaluate_telco_applicability(knowledge_object, context_state)
        if applicability.outcome == "mismatch":
            bucket = "excluded"
            excluded.append(knowledge_object.knowledge_id)
            for reason in applicability.reason_codes:
                exclusion_reason_counts[reason.value] = exclusion_reason_counts.get(reason.value, 0) + 1
        elif applicability.outcome == "indeterminate":
            bucket = "indeterminate"
            indeterminate.append(knowledge_object.knowledge_id)
            indeterminate_version_keys.append((knowledge_object.knowledge_id, knowledge_object.version.label))
        else:
            bucket = "permitted"
            permitted.append(knowledge_object.knowledge_id)
            permitted_version_keys.append((knowledge_object.knowledge_id, knowledge_object.version.label))

        items.append(
            KnowledgeNarrowingItem(
                knowledge_id=knowledge_object.knowledge_id,
                version_label=knowledge_object.version.label,
                eligibility=eligibility,
                applicability=applicability,
                final_bucket=bucket,
            )
        )

    return KnowledgeNarrowingResult(
        input_count=len(corpus),
        permitted_knowledge_ids=permitted,
        permitted_version_keys=permitted_version_keys,
        indeterminate_version_keys=indeterminate_version_keys,
        excluded_knowledge_ids=excluded,
        indeterminate_knowledge_ids=indeterminate,
        items=items,
        exclusion_reason_counts=exclusion_reason_counts,
    )


async def narrow_knowledge(
    repository: KnowledgeRepository,
    context_state: dict[ContextDimension, ContextValue],
    *,
    as_of: datetime,
    policy: Optional[NarrowingPolicy] = None,
) -> KnowledgeNarrowingResult:
    """The real, repository-backed entry point -- reads the FULL corpus
    via `repository.list_all()` (the same source-of-truth enumeration
    `KnowledgeRetrievalService.retrieve()` already uses, never a second,
    competing corpus-loading path), then delegates to `narrow_corpus`.
    """
    corpus = await repository.list_all()
    return narrow_corpus(corpus, context_state, as_of=as_of, policy=policy)
