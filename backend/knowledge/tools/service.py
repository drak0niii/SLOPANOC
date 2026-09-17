"""`KnowledgeToolService` -- composes the frozen 5.1G retrieval and 5.1H
provenance services into the one generic `knowledge_search` capability,
correlating their outputs by deterministic identity and splitting the
result into a model-safe `agent_payload` plus trusted, Python-only
`evidence_set`. See this package's own `__init__.py` for the full
rationale.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceSelectionKey
from backend.knowledge.provenance.service import KnowledgeProvenanceService, validate_evidence_selection
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalItem, KnowledgeRetrievalQuery
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.knowledge.tools.contracts import (
    KnowledgeToolRetrievalUnavailableError,
    KnowledgeSearchAgentPayload,
    KnowledgeSearchDiagnostic,
    KnowledgeSearchExecutionResult,
    KnowledgeSearchToolRequest,
    KnowledgeToolConsistencyError,
    KnowledgeToolEvidenceItem,
    KnowledgeToolExecutionContext,
)

if TYPE_CHECKING:  # pragma: no cover -- typing only, avoids an import cycle at runtime
    from backend.knowledge.shared_evidence import SharedEvidenceService

_Identity = tuple[str, str, "str | None"]


def _identity_of_retrieval_item(item: KnowledgeRetrievalItem) -> _Identity:
    return (item.knowledge_id, item.version_label, item.section.section_id)


def _identity_of_evidence_item(item: KnowledgeEvidenceItem) -> _Identity:
    return (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)


def _build_tool_item(retrieval_item: KnowledgeRetrievalItem, evidence_item: KnowledgeEvidenceItem) -> KnowledgeToolEvidenceItem:
    """Authoritative fields come from `evidence_item` (validated by
    5.1H) ONLY -- `title`/`document_type`/`section_heading`/`content`/
    `source_system`/`source_id`/`source_display_name`/`source_locator`
    never read `retrieval_item`. `applicability_outcome` and
    `relevance_score` are the 5.1G retrieval decision, which provenance
    does not carry, so they come from `retrieval_item` alone.
    `source_uri` is deliberately never read here at all.
    """
    return KnowledgeToolEvidenceItem(
        selection_key=KnowledgeEvidenceSelectionKey(
            knowledge_id=evidence_item.reference.knowledge_id,
            version_label=evidence_item.reference.version_label,
            section_id=evidence_item.reference.section_id,
        ),
        title=evidence_item.title,
        document_type=evidence_item.document_type,
        section_heading=evidence_item.section.heading,
        content=evidence_item.section.content,
        source_system=evidence_item.source.source_system,
        source_id=evidence_item.source.source_id,
        source_display_name=evidence_item.source.display_name,
        source_locator=evidence_item.section.source_locator,
        applicability_outcome=retrieval_item.applicability_outcome,
        relevance_score=retrieval_item.relevance_score,
        section_sequence=evidence_item.section.sequence,
    )


def _correlate(
    retrieval_items: list[KnowledgeRetrievalItem], evidence_items: list[KnowledgeEvidenceItem]
) -> list[KnowledgeToolEvidenceItem]:
    """One-to-one identity correlation, never a positional zip. Order is
    preserved from `retrieval_items` (5.1G's own ranking order, which
    5.1H already preserves too) -- this function performs no re-ranking.
    Fails closed via `KnowledgeToolConsistencyError` on: a retrieval
    identity absent from the evidence set, an evidence identity absent
    from retrieval, or a duplicate identity on either side.
    """
    evidence_by_identity: dict[_Identity, KnowledgeEvidenceItem] = {}
    for evidence_item in evidence_items:
        identity = _identity_of_evidence_item(evidence_item)
        if identity in evidence_by_identity:
            raise KnowledgeToolConsistencyError(f"duplicate evidence identity encountered during correlation: {identity}")
        evidence_by_identity[identity] = evidence_item

    correlated: list[KnowledgeToolEvidenceItem] = []
    seen_retrieval_identities: set[_Identity] = set()
    for retrieval_item in retrieval_items:
        identity = _identity_of_retrieval_item(retrieval_item)
        if identity in seen_retrieval_identities:
            raise KnowledgeToolConsistencyError(f"duplicate retrieval identity encountered during correlation: {identity}")
        seen_retrieval_identities.add(identity)

        evidence_item = evidence_by_identity.pop(identity, None)
        if evidence_item is None:
            raise KnowledgeToolConsistencyError(f"retrieval item {identity} has no corresponding validated evidence")
        correlated.append(_build_tool_item(retrieval_item, evidence_item))

    if evidence_by_identity:
        raise KnowledgeToolConsistencyError(
            f"evidence set contains identities not present in the retrieval result: {sorted(evidence_by_identity)}"
        )

    return correlated


class KnowledgeToolService:
    """Depends only on already-constructed `KnowledgeRetrievalService`
    (5.1G) and `KnowledgeProvenanceService` (5.1H) instances -- never
    `SQLiteKnowledgeRepository`, never a database URL, never
    `resolve_current_version`/`evaluate_applicability`/a relevance
    scorer directly.
    """

    def __init__(
        self,
        retrieval_service: KnowledgeRetrievalService,
        provenance_service: KnowledgeProvenanceService,
        shared_evidence_service: "SharedEvidenceService | None" = None,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._provenance_service = provenance_service
        self._shared_evidence_service = shared_evidence_service

    async def search(
        self,
        request: KnowledgeSearchToolRequest,
        context: KnowledgeToolExecutionContext,
        *,
        run_id: str = "",
        user_id: str = "",
        context_state: "dict | None" = None,
    ) -> KnowledgeSearchExecutionResult:
        """The one generic `knowledge_search` capability.

        POST-6A -- THIS IS NOW THE SHARED EVIDENCE PATH. When a
        `SharedEvidenceService` is composed in (production wiring, see
        `backend/tools/knowledge/runtime.py`), retrieval goes through it:
        narrowing -> version-constrained retrieval -> provenance
        revalidation, with the per-turn cache that stops the second
        specialist re-running the same retrieval, and with a TYPED
        availability outcome instead of an ambiguous empty list.

        Nothing about the model-facing contract changes: the returned
        `agent_payload` is built by the SAME `_correlate` over the SAME
        retrieval/provenance pairs, so `applicability_outcome`/
        `relevance_score` still come from retrieval and content still
        comes from validated provenance.

        `shared_evidence_service=None` keeps the original direct path
        verbatim -- used by the existing unit tests that construct this
        service with only the two 5.1G/5.1H collaborators.
        """
        if self._shared_evidence_service is None:
            return await self._search_direct(request, context)
        return await self._search_shared(
            request, context, run_id=run_id, user_id=user_id, context_state=context_state or {}
        )

    async def _search_direct(
        self, request: KnowledgeSearchToolRequest, context: KnowledgeToolExecutionContext
    ) -> KnowledgeSearchExecutionResult:
        query = KnowledgeRetrievalQuery(
            query_text=request.query_text,
            applicability_context=context.applicability_context,
            as_of=context.as_of,
            limit=request.limit,
        )
        retrieval_result = await self._retrieval_service.retrieve(query)
        evidence_set = await self._provenance_service.build_evidence_set(retrieval_result)

        items = _correlate(retrieval_result.items, evidence_set.items)
        diagnostics = [
            KnowledgeSearchDiagnostic(knowledge_id=d.knowledge_id, reason=d.reason, detail=d.detail)
            for d in retrieval_result.excluded_families
        ]

        agent_payload = KnowledgeSearchAgentPayload(items=items, diagnostics=diagnostics)
        return KnowledgeSearchExecutionResult(agent_payload=agent_payload, evidence_set=evidence_set)

    async def _search_shared(
        self,
        request: KnowledgeSearchToolRequest,
        context: KnowledgeToolExecutionContext,
        *,
        run_id: str,
        user_id: str,
        context_state: dict,
    ) -> KnowledgeSearchExecutionResult:
        from backend.knowledge.shared_evidence import (
            EvidenceAvailability,
            EvidenceRequest,
            applicability_fingerprint,
        )

        result = await self._shared_evidence_service.acquire(
            EvidenceRequest(
                run_id=run_id,
                query_text=request.query_text,
                as_of=context.as_of,
                limit=request.limit,
                requested_by="incident_manager",
                user_id=user_id,
                applicability_fingerprint=applicability_fingerprint(context.applicability_context, context_state),
            ),
            context_state,
            applicability_context=context.applicability_context,
        )

        if result.availability is EvidenceAvailability.RETRIEVAL_UNAVAILABLE:
            # Raised, never returned as an empty result: an empty result
            # is indistinguishable from "nothing applicable", and the
            # caller's own error branch is what keeps an infrastructure
            # outage from being reported to the user as a knowledge gap.
            raise KnowledgeToolRetrievalUnavailableError(result.detail or "governed knowledge retrieval unavailable")

        items = _correlate(list(result.retrieval_items), list(result.items))
        agent_payload = KnowledgeSearchAgentPayload(items=items, diagnostics=[])
        # `result.evidence_set` is the object `KnowledgeProvenanceService`
        # itself built -- passed through, never reconstructed here. This
        # module constructs no evidence type of its own (enforced by
        # `test_tools_boundaries.py`'s own AST check), because a second
        # construction would be trusted state assembled outside the one
        # service allowed to validate it.
        return KnowledgeSearchExecutionResult(
            agent_payload=agent_payload,
            evidence_set=result.evidence_set,
            availability=result.availability.value,
            degradation=result.degradation.value,
        )

    def validate_selection(
        self, execution_result: KnowledgeSearchExecutionResult, selections: list[KnowledgeEvidenceSelectionKey]
    ) -> list[KnowledgeEvidenceItem]:
        """Thin pass-through to 5.1H's own `validate_evidence_selection`,
        validating strictly against `execution_result.evidence_set` --
        never a repository, never a different execution's evidence, and
        never an `EvidenceSet` supplied by the caller/model. Duplicates
        this package's identity discipline nowhere: all selection rules
        (fail-whole-batch-on-any-unknown-key, dedupe-preserving-first-
        order, empty-selection-succeeds) are 5.1H's, unchanged.
        """
        return validate_evidence_selection(execution_result.evidence_set, selections)
