"""Phase 6A.6: `assemble_context_package` -- the one deterministic
Context Package composition entry point (§25).

NO LLM CALL, NO DATABASE ACCESS, NO KNOWLEDGE SEARCH ANYWHERE IN THIS
MODULE (§56/§57, enforced by `backend/tests/context_engineering/
test_dependency_boundary.py`'s AST + standalone-import checks): every
input is already-computed data the caller supplies via
`ContextPackageInput`; this function is a pure, synchronous,
side-effect-free transformation from that input to a `ContextPackage`.
Given the SAME logical `ContextPackageInput`, calling this function
twice always produces two `ContextPackage`s whose `content_fingerprint`
is identical (§26/§53, proven by a dedicated repeated-assembly test) --
only `created_at` (excluded from the fingerprint) may legitimately
differ between the two calls.

DOES NOT MUTATE ANY UPSTREAM OBJECT (§23): every `ContextPackage` field
is built from NEW package-owned view objects
(`TelcoDimensionView`/`EvidenceItemView`/...), never a reference to a
mutable upstream object reused in place.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.context_engineering.contracts import (
    AssemblyTrace,
    ContextPackage,
    ContextPackageInput,
    EvidenceItemView,
    EvidencePackage,
    OperationalObservation,
    ProvenanceManifestEntry,
    TelcoAssertionView,
    TelcoDimensionView,
)
from backend.context_engineering.fingerprint import compute_content_fingerprint

__all__ = ["assemble_context_package"]


def _build_telco_context(input_: ContextPackageInput) -> list[TelcoDimensionView]:
    """§11/§12/§13: every dimension the caller's `telco_context_state`
    carries is included verbatim -- UNKNOWN/CONFLICTING/NOT_APPLICABLE
    are never dropped, collapsed, or resolved. Sorted by the dimension's
    own stable string value (never by dict iteration order, which
    `compute_context_state`'s own grouping does not guarantee) so
    repeated assembly from an equivalent input always yields the exact
    same list order (§26/§53)."""
    views: list[TelcoDimensionView] = []
    for dimension in sorted(input_.telco_context_state, key=lambda d: d.value):
        value = input_.telco_context_state[dimension]
        views.append(
            TelcoDimensionView(
                dimension=value.dimension,
                state=value.state,
                accepted=[_assertion_view(a) for a in value.accepted],
                conflicting=[_assertion_view(a) for a in value.conflicting],
            )
        )
    return views


def _assertion_view(assertion) -> TelcoAssertionView:
    return TelcoAssertionView(
        raw_value=assertion.raw_value,
        canonical_value=assertion.canonical_value,
        origin=assertion.origin,
        source_reference=assertion.source_reference,
        asserted_at=assertion.asserted_at,
    )


def _build_evidence_package(input_: ContextPackageInput) -> EvidencePackage:
    """§18/§19/§20: `input_.evidence_selection.selected` is ALREADY the
    fully-narrowed, fully-retrieved, fully-selected evidence -- this
    function does not re-rank, re-filter, or discover anything beyond
    it. `selected_rank` preserves 6A.5's own deterministic order
    (1-based position), never re-sorted here."""
    items: list[EvidenceItemView] = []
    source_count = 0
    derived_count = 0
    for rank, candidate in enumerate(input_.evidence_selection.selected, start=1):
        record = candidate.record
        if record.is_derived:
            derived_count += 1
        else:
            source_count += 1
        channel_scores = {hit.channel.value: hit.raw_score for hit in sorted(candidate.channel_hits, key=lambda h: h.channel.value)}
        items.append(
            EvidenceItemView(
                evidence_id=record.evidence_id,
                knowledge_id=record.knowledge_id,
                version_label=record.version_label,
                section_id=record.section_id,
                artifact_id=record.artifact_id,
                is_derived=record.is_derived,
                indexable_text=record.indexable_text,
                selected_rank=rank,
                channel_scores=channel_scores,
                fusion_score=candidate.fusion_score,
                rerank_score=candidate.rerank_score,
            )
        )
    return EvidencePackage(
        query_text=input_.evidence_selection.query_text,
        selection_reason=input_.evidence_selection.selection_reason,
        retrieved_candidate_count=input_.retrieved_candidate_count,
        selected_evidence_count=len(items),
        source_evidence_count=source_count,
        derived_evidence_count=derived_count,
        items=items,
    )


def _build_provenance_manifest(
    telco_context: list[TelcoDimensionView],
    input_: ContextPackageInput,
    evidence: EvidencePackage,
) -> list[ProvenanceManifestEntry]:
    """§38: one compact, auditable row per material piece of context --
    never a duplicate of the full source body."""
    manifest: list[ProvenanceManifestEntry] = []
    for view in telco_context:
        for assertion in (*view.accepted, *view.conflicting):
            if assertion.source_reference is not None:
                manifest.append(
                    ProvenanceManifestEntry(
                        category="telco_context",
                        identity=f"{view.dimension.value}:{assertion.source_reference}",
                        source_reference=assertion.source_reference,
                    )
                )
    if input_.case_context is not None:
        for item in input_.case_context.items:
            manifest.append(
                ProvenanceManifestEntry(
                    category="case_context",
                    identity=f"{input_.case_context.case_id}:{item.kind.value}:{item.created_at.isoformat()}",
                    source_reference=None,
                )
            )
    for observation in input_.operational_observations:
        manifest.append(
            ProvenanceManifestEntry(
                category="operational_observation",
                identity=observation.source_reference or observation.content[:64],
                source_reference=observation.source_reference,
            )
        )
    for item in evidence.items:
        manifest.append(
            ProvenanceManifestEntry(
                category="knowledge_evidence",
                identity=item.evidence_id,
                source_reference=f"{item.knowledge_id}:{item.version_label}:{item.section_id}",
            )
        )
    return manifest


def _build_assembly_trace(
    telco_context: list[TelcoDimensionView],
    input_: ContextPackageInput,
    evidence: EvidencePackage,
) -> AssemblyTrace:
    from backend.context.domain.enums import ContextState

    return AssemblyTrace(
        telco_dimensions_included=len(telco_context),
        telco_known_count=sum(1 for v in telco_context if v.state == ContextState.KNOWN),
        telco_unknown_count=sum(1 for v in telco_context if v.state == ContextState.UNKNOWN),
        telco_conflicting_count=sum(1 for v in telco_context if v.state == ContextState.CONFLICTING),
        telco_not_applicable_count=sum(1 for v in telco_context if v.state == ContextState.NOT_APPLICABLE),
        operational_observation_count=len(input_.operational_observations),
        retrieved_candidate_count=input_.retrieved_candidate_count,
        selected_evidence_count=evidence.selected_evidence_count,
        source_evidence_count=evidence.source_evidence_count,
        derived_evidence_count=evidence.derived_evidence_count,
    )


def assemble_context_package(input_: ContextPackageInput) -> ContextPackage:
    telco_context = _build_telco_context(input_)
    evidence = _build_evidence_package(input_)
    provenance_manifest = _build_provenance_manifest(telco_context, input_, evidence)
    assembly_trace = _build_assembly_trace(telco_context, input_, evidence)
    operational_observations = list(input_.operational_observations)

    package = ContextPackage(
        owner_kind=input_.owner_kind,
        owner_id=input_.owner_id,
        session_id=input_.session_id,
        case_id=input_.case_id,
        request=input_.request,
        telco_context=telco_context,
        case_context=input_.case_context,
        operational_observations=operational_observations,
        evidence=evidence,
        provenance_manifest=provenance_manifest,
        assembly_trace=assembly_trace,
        content_fingerprint="",
        created_at=datetime.now(timezone.utc),
    )
    fingerprint = compute_content_fingerprint(package)
    return package.model_copy(update={"content_fingerprint": fingerprint})
