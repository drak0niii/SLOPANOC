"""`KnowledgeRetrievalService` -- the orchestration that composes
already-frozen 5.1E/5.1B/5.1F operations into one bounded, deterministic
retrieval result. Never duplicates currentness or applicability logic
itself; see docs/KNOWLEDGE_CONTRACT.md's Phase 5.1G section for the full
reference-flow rationale.
"""
from __future__ import annotations

from typing import Optional

from backend.knowledge.domain.applicability import ApplicabilityOutcome, evaluate_applicability
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection
from backend.knowledge.governance.contracts import CurrentVersionResolutionStatus, InvalidVersionFamilyError
from backend.knowledge.governance.versioning import resolve_current_version
from backend.knowledge.repository.contracts import KnowledgeRepository
from backend.knowledge.retrieval.contracts import (
    KnowledgeRetrievalDiagnostic,
    KnowledgeRetrievalDiagnosticReason,
    KnowledgeRetrievalItem,
    KnowledgeRetrievalQuery,
    KnowledgeRetrievalResult,
)
from backend.knowledge.retrieval.scoring import KnowledgeRelevanceScorer, TokenOverlapRelevanceScorer

_APPLICABILITY_CERTAINTY_RANK: dict[ApplicabilityOutcome, int] = {
    ApplicabilityOutcome.MATCH: 0,
    ApplicabilityOutcome.PARTIAL_MATCH: 1,
    ApplicabilityOutcome.UNKNOWN: 2,
}
"""Deterministic tie-break ONLY (instruction section 29/66) -- never a
business weight. `ApplicabilityOutcome.NOT_APPLICABLE` never appears
here: such sections are excluded before an item is ever constructed.
"""

_RELEVANCE_TIE_BREAK_BUCKET_WIDTH = 0.15
"""A5 final corrective pass (Correction C): a deliberately generic,
NOT query/document/format-tuned tolerance -- two candidates whose
`relevance_score` falls within the same bucket of this width are
treated as "sufficiently similar" for ranking purposes, and
`is_derived` (source vs. derived evidence, A5's own existing
distinction -- never a new concept) becomes the tie-break BETWEEN them:
native/source content (`is_derived=False`) outranks a derived
interpretation (`is_derived=True`) of what is, at this coarse
granularity, comparably relevant content. A candidate in a DIFFERENT
bucket always wins or loses on relevance alone, regardless of
`is_derived` -- this is a tie-break among near-equal candidates, never a
blanket suppression of derived evidence (a highly relevant derived
image still outranks a barely-relevant native section, because they
land in different buckets)."""


def _relevance_bucket(relevance_score: float) -> int:
    return round(relevance_score / _RELEVANCE_TIE_BREAK_BUCKET_WIDTH)


def _resolve_is_derived(knowledge_object: KnowledgeObject, section: KnowledgeSection) -> bool:
    """A5 final corrective pass: resolves whether `section`'s content
    came from a derived (model-interpreted) artifact, purely from the
    SAME governed `KnowledgeObject.artifacts` the section itself belongs
    to -- never a model claim, never a separate lookup. A section with
    no `artifact_id` (root document text) is never derived.
    """
    if section.artifact_id is None:
        return False
    for artifact in knowledge_object.artifacts:
        if artifact.artifact_id == section.artifact_id:
            return artifact.derived
    return False


def _group_by_knowledge_id(corpus: list[KnowledgeObject]) -> dict[str, list[KnowledgeObject]]:
    """Group the repository's full corpus into logical version families
    by `knowledge_id` ONLY -- never by title, source, document type, or
    any label similarity (instruction section 12).
    """
    families: dict[str, list[KnowledgeObject]] = {}
    for knowledge_object in corpus:
        families.setdefault(knowledge_object.knowledge_id, []).append(knowledge_object)
    return families


def _item_sort_key(item: KnowledgeRetrievalItem) -> tuple:
    """Deterministic ranking (instruction section 29): higher relevance
    first (coarsely BUCKETED -- see `_RELEVANCE_TIE_BREAK_BUCKET_WIDTH`
    above -- so two candidates of comparable relevance are ranked as a
    group, not by float noise); WITHIN the same bucket, native/source
    content before derived interpretation (`is_derived`, Correction C);
    WITHIN that, exact relevance still discriminates; then stronger
    applicability certainty first (MATCH < PARTIAL_MATCH < UNKNOWN);
    then deterministic identity tie-breakers. `version_label`
    participating here carries ZERO governance/precedence meaning -- it
    is used only because SOME total order is required for deterministic
    output once every other key is already tied; `section.section_id`
    is the final, guaranteed-unique tie-breaker (unique per governed
    object, per 5.1A).
    """
    return (
        -_relevance_bucket(item.relevance_score),
        item.is_derived,  # False (native/source) sorts before True (derived) -- Python bool ordering, False < True.
        -item.relevance_score,
        _APPLICABILITY_CERTAINTY_RANK[item.applicability_outcome],
        item.knowledge_id,
        item.version_label,
        item.section.sequence,
        item.section.section_id,
    )


class KnowledgeRetrievalService:
    """Depends only on `KnowledgeRepository` (never
    `SQLiteKnowledgeRepository` or any storage detail) and a
    `KnowledgeRelevanceScorer`. Read-only: never calls
    `repository.add`/`replace`, never mutates a `KnowledgeObject`, never
    persists retrieval state.
    """

    def __init__(self, repository: KnowledgeRepository, scorer: Optional[KnowledgeRelevanceScorer] = None) -> None:
        self._repository = repository
        self._scorer: KnowledgeRelevanceScorer = scorer if scorer is not None else TokenOverlapRelevanceScorer()

    async def retrieve(self, query: KnowledgeRetrievalQuery) -> KnowledgeRetrievalResult:
        corpus = await self._repository.list_all()
        families = _group_by_knowledge_id(corpus)

        items: list[KnowledgeRetrievalItem] = []
        diagnostics: list[KnowledgeRetrievalDiagnostic] = []

        # Sorted purely for deterministic diagnostic ORDER -- the final
        # item ranking below is independently, fully re-sorted, so this
        # does not by itself make retrieval order-dependent.
        for knowledge_id in sorted(families):
            family = families[knowledge_id]

            try:
                resolution = resolve_current_version(family, query.as_of)
            except InvalidVersionFamilyError as exc:
                diagnostics.append(
                    KnowledgeRetrievalDiagnostic(
                        knowledge_id=knowledge_id,
                        reason=KnowledgeRetrievalDiagnosticReason.INVALID_VERSION_FAMILY,
                        detail=type(exc).__name__,
                    )
                )
                continue

            if resolution.status is CurrentVersionResolutionStatus.NOT_FOUND:
                continue
            if resolution.status is CurrentVersionResolutionStatus.AMBIGUOUS:
                diagnostics.append(
                    KnowledgeRetrievalDiagnostic(
                        knowledge_id=knowledge_id,
                        reason=KnowledgeRetrievalDiagnosticReason.CURRENT_VERSION_AMBIGUOUS,
                        detail=f"ambiguous among versions: {', '.join(resolution.candidates)}",
                    )
                )
                continue

            # RESOLVED -- the only outcome that ever contributes items.
            current = resolution.current
            assert current is not None  # RESOLVED always carries `current` (governance/contracts.py's own invariant)

            applicability_evaluation = evaluate_applicability(current.applicability, query.applicability_context)
            if applicability_evaluation.outcome is ApplicabilityOutcome.NOT_APPLICABLE:
                continue

            for section in current.sections:
                relevance = self._scorer.score(query.query_text, current, section)
                if relevance <= 0.0:
                    continue
                items.append(
                    KnowledgeRetrievalItem(
                        knowledge_id=current.knowledge_id,
                        document_type=current.document_type,
                        title=current.title,
                        version_label=current.version.label,
                        lifecycle_status=current.lifecycle_status,
                        section=section,
                        source=current.source,
                        applicability_outcome=applicability_evaluation.outcome,
                        relevance_score=relevance,
                        is_derived=_resolve_is_derived(current, section),
                    )
                )

        # Limit is applied ONLY here, after full eligibility + scoring +
        # ranking (instruction section 31/32) -- never earlier, and
        # never as a substitute for "send everything to the model".
        ranked = sorted(items, key=_item_sort_key)
        return KnowledgeRetrievalResult(items=ranked[: query.limit], excluded_families=diagnostics)
