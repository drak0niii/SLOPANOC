"""`KnowledgeRetrievalService` -- the orchestration that composes
already-frozen 5.1E/5.1B/5.1F operations into one bounded, deterministic
retrieval result. Never duplicates currentness or applicability logic
itself; see docs/KNOWLEDGE_CONTRACT.md's Phase 5.1G section for the full
reference-flow rationale.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
import sys
import math
from typing import Optional

from backend.knowledge.domain.applicability import ApplicabilityEvaluation, ApplicabilityOutcome, evaluate_applicability
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection
from backend.knowledge.governance.contracts import CurrentVersionResolutionStatus, InvalidVersionFamilyError
from backend.knowledge.governance.versioning import resolve_current_version
from backend.knowledge.repository.contracts import KnowledgeRepository
from backend.knowledge.retrieval.contracts import (
    KnowledgeRetrievalDiagnostic,
    KnowledgeRetrievalDiagnosticReason,
    KnowledgeRetrievalItem,
    KnowledgeRetrievalMode,
    KnowledgeRetrievalQuery,
    KnowledgeRetrievalRankingDiagnostic,
    KnowledgeRetrievalResult,
)
from backend.knowledge.retrieval.reranking import RRF_K_CONSTANT, compute_reciprocal_rank_fusion
from backend.knowledge.retrieval.scoring import (
    DenseSimilarityProvider,
    KnowledgeRelevanceScorer,
    TokenOverlapRelevanceScorer,
)

DEFAULT_DENSE_MIN_SIMILARITY = 0.65
"""Generic semantic floor (raw cosine): a section with NO lexical overlap enters the
candidate set only when its dense similarity reaches this value. Not query-, vendor-
or document-tuned; configurable at composition time."""

DEFAULT_DENSE_TIMEOUT_SECONDS = 10.0

_RRF_MAX_SCORE = 2.0 / (RRF_K_CONSTANT + 1)
"""Best possible fused score (rank 1 in both signals); used to normalize RRF to [0, 1]."""

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


def _identity_sort_key(entry: tuple[KnowledgeObject, KnowledgeSection, ApplicabilityEvaluation]) -> tuple:
    obj, section, _ = entry
    return (obj.knowledge_id, obj.version.label, section.sequence, section.section_id)


def _ranks(scores: list[Optional[float]], order_keys: list[tuple]) -> list[Optional[int]]:
    """1-based rank per index among entries with a non-None score (higher first,
    deterministic identity tie-break); None for unranked entries."""
    ranked = sorted((i for i, sc in enumerate(scores) if sc is not None), key=lambda i: (-scores[i], order_keys[i]))
    out: list[Optional[int]] = [None] * len(scores)
    for position, index in enumerate(ranked, start=1):
        out[index] = position
    return out


class KnowledgeRetrievalService:
    """Depends only on `KnowledgeRepository` (never
    `SQLiteKnowledgeRepository` or any storage detail) and a
    `KnowledgeRelevanceScorer`. Read-only: never calls
    `repository.add`/`replace`, never mutates a `KnowledgeObject`, never
    persists retrieval state.

    HYBRID RELEVANCE (optional `dense_provider`): governance runs FIRST and is
    unchanged -- only sections of the resolved current version of a family that
    is not NOT_APPLICABLE are ever scored. Relevance is then computed twice
    (lexical `scorer`, semantic `dense_provider`), fused by reciprocal rank
    fusion, and ranked by the same deterministic `_item_sort_key`. Hybrid changes
    discovery/ranking ONLY: applicability outcomes, lifecycle status and version
    are copied from governance exactly as in lexical mode. If the dense stage
    fails or times out, this query falls back to lexical relevance (reported in
    `dense_status`); a synthetic/substitute similarity is never used.
    """

    def __init__(
        self,
        repository: KnowledgeRepository,
        scorer: Optional[KnowledgeRelevanceScorer] = None,
        dense_provider: Optional[DenseSimilarityProvider] = None,
        dense_min_similarity: float = DEFAULT_DENSE_MIN_SIMILARITY,
        dense_timeout_seconds: float = DEFAULT_DENSE_TIMEOUT_SECONDS,
        execution_guard=None,
        observer=None,
        operation_observer=None,
    ) -> None:
        self._observer = observer
        self._operation_observer = operation_observer
        self._repository = repository
        self._scorer: KnowledgeRelevanceScorer = scorer if scorer is not None else TokenOverlapRelevanceScorer()
        self._dense_provider = dense_provider
        self._dense_min_similarity = dense_min_similarity
        self._dense_timeout_seconds = dense_timeout_seconds
        self._execution_guard = execution_guard

    @contextmanager
    def _stage(self, operation, observer=None):
        # Optional observational seam; neither observer failure nor return value
        # changes retrieval. Default has no vendor/runtime dependency.
        manager = stage = None
        observer = self._observer if observer is None else observer
        if observer is not None:
            try:
                manager = observer(operation)
                stage = manager.__enter__()
            except Exception:
                manager = None
        try:
            yield stage
        finally:
            if manager is not None:
                try:
                    manager.__exit__(*sys.exc_info())
                except Exception:
                    pass

    @staticmethod
    def _observe(stage, method, value):
        if stage is not None:
            try:
                getattr(stage, method)(value)
            except Exception:
                pass

    async def _dense_similarities(self, query_text, eligible):
        with self._stage("dense") as stage:
            values, status = await self._dense_similarities_impl(query_text, eligible)
            self._observe(stage, "dense_result", status)
            return values, status

    async def _dense_similarities_impl(
        self, query_text: str, eligible: list[tuple[KnowledgeObject, KnowledgeSection, ApplicabilityEvaluation]]
    ) -> tuple[Optional[list[float]], str]:
        if self._dense_provider is None:
            return None, "not_configured"
        if not eligible:
            return None, "no_eligible_sections"
        try:
            values = await asyncio.wait_for(
                self._dense_provider.similarities(query_text, [(obj, sec) for obj, sec, _ in eligible]),
                timeout=self._dense_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return None, "unavailable:timeout"
        except Exception as exc:  # never propagate: lexical relevance remains available
            return None, f"unavailable:{type(exc).__name__}"
        if len(values) != len(eligible) or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            return None, "unavailable:invalid_response"
        return [max(-1.0, min(1.0, float(v))) for v in values], "ok"

    async def retrieve(self, query: KnowledgeRetrievalQuery) -> KnowledgeRetrievalResult:
        # The optional whole-operation observer counts one retrieval, independently
        # of metadata/ranking stages. It cannot alter governed retrieval results.
        from contextlib import nullcontext
        observer = self._stage("retrieval", self._operation_observer) if self._operation_observer is not None else nullcontext()
        with observer:
            if self._execution_guard is not None:
                async with self._execution_guard():
                    return await self._retrieve(query)
            return await self._retrieve(query)

    async def _retrieve(self, query: KnowledgeRetrievalQuery) -> KnowledgeRetrievalResult:
        with self._stage("metadata"):
            corpus = await self._repository.list_all()
        families = _group_by_knowledge_id(corpus)

        eligible: list[tuple[KnowledgeObject, KnowledgeSection, ApplicabilityEvaluation]] = []
        diagnostics: list[KnowledgeRetrievalDiagnostic] = []

        # Sorted purely for deterministic diagnostic ORDER -- the final
        # item ranking below is independently, fully re-sorted, so this
        # does not by itself make retrieval order-dependent.
        with self._stage("applicability"):
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
                    eligible.append((current, section, applicability_evaluation))

        order_keys = [_identity_sort_key(entry) for entry in eligible]
        with self._stage("sparse"):
            sparse = [self._scorer.score(query.query_text, obj, sec) for obj, sec, _ in eligible]
        dense, dense_status = await self._dense_similarities(query.query_text, eligible)

        with self._stage("fusion") as stage:
            sparse_for_rank: list[Optional[float]] = [sc if sc > 0.0 else None for sc in sparse]
            if dense is None:
                mode = KnowledgeRetrievalMode.LEXICAL
                dense_for_rank: list[Optional[float]] = [None] * len(eligible)
            else:
                mode = KnowledgeRetrievalMode.HYBRID
                dense_for_rank = [sim if sim >= self._dense_min_similarity else None for sim in dense]
            sparse_ranks = _ranks(sparse_for_rank, order_keys)
            dense_ranks = _ranks(dense_for_rank, order_keys)

            candidates = [i for i in range(len(eligible)) if sparse_for_rank[i] is not None or dense_for_rank[i] is not None]
            base_items: dict[tuple[str, str, str], KnowledgeRetrievalItem] = {}
            index_by_identity: dict[tuple[str, str, str], int] = {}
            for i in candidates:
                obj, section, evaluation = eligible[i]
                item = KnowledgeRetrievalItem(
                    knowledge_id=obj.knowledge_id,
                    document_type=obj.document_type,
                    title=obj.title,
                    version_label=obj.version.label,
                    lifecycle_status=obj.lifecycle_status,
                    section=section,
                    source=obj.source,
                    applicability_outcome=evaluation.outcome,
                    unresolved_applicability_dimensions=[
                        result.dimension
                        for result in evaluation.dimension_results
                        if result.outcome is ApplicabilityOutcome.UNKNOWN
                    ],
                    relevance_score=sparse[i],
                    is_derived=_resolve_is_derived(obj, section),
                )
                identity = (item.knowledge_id, item.version_label, section.section_id)
                base_items[identity] = item
                index_by_identity[identity] = i

            if mode is KnowledgeRetrievalMode.LEXICAL:
                items = list(base_items.values())
            else:
                def _ranked(ranks: list[Optional[int]]) -> list[KnowledgeRetrievalItem]:
                    keyed = [(ranks[index_by_identity[k]], k) for k in base_items if ranks[index_by_identity[k]] is not None]
                    return [base_items[k] for _, k in sorted(keyed)]

                fused = compute_reciprocal_rank_fusion(_ranked(sparse_ranks), _ranked(dense_ranks))
                items = [
                    item.model_copy(update={"relevance_score": min(1.0, item.relevance_score / _RRF_MAX_SCORE)})
                    for item in fused
                ]

            # Limit is applied ONLY here, after full eligibility + scoring +
            # ranking (instruction section 31/32) -- never earlier, and
            # never as a substitute for "send everything to the model".
            ranked = sorted(items, key=_item_sort_key)[: query.limit]
            ranking = []
            for item in ranked:
                i = index_by_identity[(item.knowledge_id, item.version_label, item.section.section_id)]
                ranking.append(
                    KnowledgeRetrievalRankingDiagnostic(
                        knowledge_id=item.knowledge_id,
                        version_label=item.version_label,
                        section_id=item.section.section_id,
                        sparse_score=round(sparse[i], 6),
                        sparse_rank=sparse_ranks[i],
                        dense_similarity=None if dense is None else round(dense[i], 6),
                        dense_rank=dense_ranks[i],
                        fused_score=round(item.relevance_score, 6),
                    )
                )
            self._observe(stage, "count", len(ranked))
            return KnowledgeRetrievalResult(
                items=ranked,
                excluded_families=diagnostics,
                mode=mode,
                dense_status=dense_status,
                eligible_section_count=len(eligible),
                candidate_count=len(candidates),
                ranking=ranking,
            )
