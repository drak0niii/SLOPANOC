"""Phase 6A.6 §58/§59/§60: the mandatory controlled end-to-end proof,
chaining the REAL, UNMODIFIED 6A.2 -> 6A.4 -> 6A.5 -> 6A.6 pipeline:

    6A.2 compute_context_state
        -> 6A.4 narrow_corpus (real, unmodified)
        -> 6A.5 hybrid_retrieve (real, unmodified)
        -> 6A.5 select_evidence (real, unmodified)
        -> 6A.6 assemble_context_package

Uses the exact controlled TELCO scenario the milestone's own instruction
specifies (§58): Case CASE-6A6-001, Customer Vodafone, Domain RAN,
Vendor Ericsson, Technology LTE, Alarm VSWR -- plus one intentionally
UNKNOWN dimension (Release), one operational observation, one case
fact, one SOURCE evidence item, one DERIVED evidence item.

None of these tests generate a troubleshooting answer, a recommendation,
or an RCA -- they only prove correct, faithful, deterministic assembly.

Uses a FAKE in-memory evidence-index repository/embedding provider
(mirrors `test_hybrid_retrieval_narrowing_integration.py`'s own,
already-proven pattern exactly) -- proves the CHAINING logic, not a
second proof of real Postgres/pgvector (already proven in 6A.5's own
closure).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.cases.schemas import CaseContextSnapshot, CaseContextSnapshotItem, CaseStatus, ContextItemKind, SourceType
from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextProfileOwnerKind, ContextState
from backend.context.domain.models import ContextAssertion, compute_context_state
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackageInput, OperationalObservation, OperationalObservationKind, RequestContext
from backend.knowledge.domain.asset_metadata import KnowledgeAssetMetadata
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeMetadata, KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.hybrid_retrieval.contracts import ChannelHit, HybridRetrievalQuery, RetrievalChannel
from backend.knowledge.hybrid_retrieval.embedding import EmbeddingVector
from backend.knowledge.hybrid_retrieval.evidence_selection import select_evidence
from backend.knowledge.hybrid_retrieval.indexing import index_knowledge_object
from backend.knowledge.hybrid_retrieval.service import hybrid_retrieve
from backend.knowledge.narrowing.service import narrow_corpus

_AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
_EFF = datetime(2025, 1, 1, tzinfo=timezone.utc)
_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _knowledge_object(kid: str, *, vendor: str, technology: str, sections: list[str], explicit_any: bool = False) -> KnowledgeObject:
    dims = {}
    am = KnowledgeAssetMetadata()
    if explicit_any:
        am.applicability_scope.explicit_any_dimensions = ["customer"]
    else:
        dims["vendor"] = [vendor]
        dims["technology"] = [technology]
    return KnowledgeObject(
        knowledge_id=kid, document_type=KnowledgeDocumentType.MOP, title=kid,
        version=KnowledgeVersion(label="1.0", effective_from=_EFF), lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id=kid),
        applicability=Applicability(dimensions=dims), metadata=KnowledgeMetadata(asset_metadata=am),
        sections=[KnowledgeSection(section_id=f"{kid}-S{i}", knowledge_id=kid, sequence=i, content=c) for i, c in enumerate(sections)],
    )


class FakeIndexRepository:
    """Mirrors `test_hybrid_retrieval_narrowing_integration.py`'s own
    `FakeIndexRepository` exactly -- an in-process fake proving the
    CHAINING logic without requiring real PostgreSQL (real Postgres
    candidate-boundary proof already exists in 6A.5's own test suite)."""

    def __init__(self) -> None:
        self.rows: dict = {}
        self._vector_available = True

    @property
    def vector_available(self):
        return self._vector_available

    async def ensure_schema(self):
        return None

    async def get(self, evidence_id):
        return self.rows.get(evidence_id)

    async def upsert(self, record, embedding, *, now):
        self.rows[record.evidence_id] = record
        return True

    async def exact_match(self, permitted_knowledge_ids, query_text):
        if not permitted_knowledge_ids:
            return []
        return [
            ChannelHit(evidence_id=r.evidence_id, channel=RetrievalChannel.EXACT, raw_score=1.0)
            for r in self.rows.values()
            if r.knowledge_id in permitted_knowledge_ids and query_text.lower() in r.indexable_text.lower()
        ]

    async def lexical_search(self, permitted_knowledge_ids, query_text, limit):
        if not permitted_knowledge_ids:
            return []
        return [
            ChannelHit(evidence_id=r.evidence_id, channel=RetrievalChannel.LEXICAL, raw_score=0.5)
            for r in self.rows.values()
            if r.knowledge_id in permitted_knowledge_ids and any(w.lower() in r.indexable_text.lower() for w in query_text.split())
        ]

    async def semantic_search(self, permitted_knowledge_ids, query_embedding, limit):
        return []

    async def get_many(self, evidence_ids):
        return {eid: self.rows[eid] for eid in evidence_ids if eid in self.rows}


class FakeEmbeddingProvider:
    async def embed(self, texts):
        return [EmbeddingVector(values=tuple([0.1] * 768), model="fake", model_version="fake-v1", dimensions=768) for _ in texts]


def _context_state(*, vendor="Ericsson", customer="Vodafone", technology="LTE", conflicting_vendor: bool = False, include_release: bool = False) -> dict:
    assertions = [
        ContextAssertion(assertion_id="a-customer", dimension=ContextDimension.CUSTOMER, kind=AssertionKind.VALUE, raw_value=customer, canonical_value=customer.upper(), origin=ContextOrigin.USER, source_reference="case-CASE-6A6-001"),
        ContextAssertion(assertion_id="a-vendor", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value=vendor, canonical_value=vendor.upper(), origin=ContextOrigin.USER, source_reference="case-CASE-6A6-001"),
        ContextAssertion(assertion_id="a-technology", dimension=ContextDimension.TECHNOLOGY, kind=AssertionKind.VALUE, raw_value=technology, canonical_value=technology.upper(), origin=ContextOrigin.USER),
        ContextAssertion(assertion_id="a-alarm", dimension=ContextDimension.ALARM, kind=AssertionKind.VALUE, raw_value="VSWR Over Threshold", canonical_value="VSWR OVER THRESHOLD", origin=ContextOrigin.SYSTEM, source_reference="alarm-feed-1"),
    ]
    if conflicting_vendor:
        assertions.append(ContextAssertion(assertion_id="a-vendor-2", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Nokia", canonical_value="NOKIA", origin=ContextOrigin.CASE, source_reference="case-CASE-6A6-001"))
    # RELEASE is intentionally never asserted -- it stays absent/UNKNOWN.
    return compute_context_state(assertions)


async def _run_pipeline(corpus: list[KnowledgeObject], context_state: dict, query_text: str) -> tuple:
    narrowing_result = narrow_corpus(corpus, context_state, as_of=_AS_OF)
    repo = FakeIndexRepository()
    provider = FakeEmbeddingProvider()
    for obj in corpus:
        await index_knowledge_object(obj, repo, provider)
    retrieval_result = await hybrid_retrieve(HybridRetrievalQuery(query_text=query_text, permitted_knowledge_ids=narrowing_result.permitted_knowledge_ids, limit=10), repo, provider)
    selection_result = select_evidence(query_text, retrieval_result.candidates, max_evidence_units=5)
    return narrowing_result, retrieval_result, selection_result


@pytest.mark.asyncio
async def test_controlled_scenario_normal_known_context_with_one_unknown_dimension() -> None:
    """§58: the primary controlled scenario -- known context, one
    intentionally UNKNOWN dimension (Release), one operational
    observation, one case fact, real narrowing->retrieval->selection,
    assembled into a ContextPackage."""
    permitted_obj = _knowledge_object("VSWR-ERICSSON-LTE", vendor="Ericsson", technology="LTE", sections=[
        "VSWR Over Threshold alarm procedure: check antenna feeder connector.",
    ])
    excluded_obj = _knowledge_object("VSWR-NOKIA-LTE", vendor="Nokia", technology="LTE", sections=[
        "VSWR Over Threshold alarm procedure for Nokia equipment.",
    ])
    context_state = _context_state()
    narrowing_result, retrieval_result, selection_result = await _run_pipeline([permitted_obj, excluded_obj], context_state, "VSWR")

    assert narrowing_result.permitted_knowledge_ids == ["VSWR-ERICSSON-LTE"]
    assert "VSWR-NOKIA-LTE" in narrowing_result.excluded_knowledge_ids
    assert len(selection_result.selected) >= 1
    assert all(c.record.knowledge_id == "VSWR-ERICSSON-LTE" for c in selection_result.selected)

    case_context = CaseContextSnapshot(
        case_id="CASE-6A6-001", title="VSWR investigation", status=CaseStatus.INVESTIGATING,
        problem_statement="High VSWR alarm on an Ericsson LTE cell.",
        items=[CaseContextSnapshotItem(kind=ContextItemKind.OBSERVATION, content="Alarm first raised at 08:00 UTC.", source_type=SourceType.SYSTEM, created_at=_NOW)],
        total_item_count=1, included_item_count=1,
    )
    observation = OperationalObservation(kind=OperationalObservationKind.OBSERVED, content="VSWR reading measured at 2.1 (threshold 1.5).", source_reference="alarm-feed-1", observed_at=_NOW)

    input_ = ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.CASE, owner_id="CASE-6A6-001", case_id="CASE-6A6-001",
        request=RequestContext(question="What should I check next for this VSWR alarm?"),
        telco_context_state=context_state, case_context=case_context, operational_observations=[observation],
        evidence_selection=selection_result, retrieved_candidate_count=len(retrieval_result.candidates),
    )
    pkg = assemble_context_package(input_)

    # Correct TELCO context.
    vendor_view = next(v for v in pkg.telco_context if v.dimension == ContextDimension.VENDOR)
    assert vendor_view.state == ContextState.KNOWN and vendor_view.accepted[0].canonical_value == "ERICSSON"
    release_dims = {v.dimension for v in pkg.telco_context}
    assert ContextDimension.RELEASE not in release_dims, "Release must stay absent/UNKNOWN, never guessed"

    # Correct owner.
    assert pkg.owner_kind == ContextProfileOwnerKind.CASE and pkg.owner_id == "CASE-6A6-001"

    # Correct case context.
    assert pkg.case_context.case_id == "CASE-6A6-001"
    assert pkg.case_context.items[0].content == "Alarm first raised at 08:00 UTC."

    # Correct operational observation.
    assert pkg.operational_observations[0].content == "VSWR reading measured at 2.1 (threshold 1.5)."
    assert pkg.operational_observations[0].kind == OperationalObservationKind.OBSERVED

    # Only selected evidence -- never the excluded Nokia document.
    assert all(item.knowledge_id == "VSWR-ERICSSON-LTE" for item in pkg.evidence.items)
    assert "VSWR-NOKIA-LTE" not in {item.knowledge_id for item in pkg.evidence.items}

    # Deterministic package.
    assert pkg.content_fingerprint != ""


@pytest.mark.asyncio
async def test_controlled_scenario_source_and_derived_evidence_both_preserved() -> None:
    """§58's own "one SOURCE evidence item, one DERIVED evidence item
    where useful" requirement -- proven via a real `KnowledgeObject`
    whose second section is linked to a `derived=True` artifact, so the
    REAL `resolve_is_derived` resolution (indexable_text.py) is
    genuinely exercised end to end, not merely asserted by hand."""
    from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact

    derived_artifact = KnowledgeArtifact(
        artifact_id="ART-IMG-1", kind="image", depth=0, derived=True,
        extracted_text="VSWR alarm screenshot shows RRU status LED solid red.",
        extraction_status=ArtifactExtractionStatus.COMPLETE,
    )
    obj = KnowledgeObject(
        knowledge_id="VSWR-ERICSSON-LTE", document_type=KnowledgeDocumentType.MOP, title="VSWR-ERICSSON-LTE",
        version=KnowledgeVersion(label="1.0", effective_from=_EFF), lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="VSWR-ERICSSON-LTE"),
        applicability=Applicability(dimensions={"vendor": ["Ericsson"], "technology": ["LTE"]}),
        sections=[
            KnowledgeSection(section_id="S0", knowledge_id="VSWR-ERICSSON-LTE", sequence=0, content="VSWR Over Threshold alarm procedure: check antenna feeder."),
            KnowledgeSection(section_id="S1", knowledge_id="VSWR-ERICSSON-LTE", sequence=1, content="VSWR alarm screenshot shows RRU status LED solid red.", artifact_id="ART-IMG-1"),
        ],
        artifacts=[derived_artifact],
    )
    context_state = _context_state()
    narrowing_result, retrieval_result, selection_result = await _run_pipeline([obj], context_state, "VSWR")

    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1", telco_context_state=context_state, evidence_selection=selection_result)
    pkg = assemble_context_package(input_)

    is_derived_flags = {item.is_derived for item in pkg.evidence.items}
    assert is_derived_flags == {False, True}, f"expected both SOURCE (False) and DERIVED (True) evidence, got: {is_derived_flags}"
    assert pkg.evidence.source_evidence_count == 1
    assert pkg.evidence.derived_evidence_count == 1


@pytest.mark.asyncio
async def test_controlled_scenario_conflicting_vendor_never_resolved() -> None:
    """§59: a conflicting TELCO dimension (Vendor: Ericsson vs. Nokia)
    must be represented as CONFLICTING in the Context Package -- never
    collapsed to one vendor, even though one assertion is newer."""
    permitted_obj = _knowledge_object("GENERIC-VSWR", vendor="Ericsson", technology="LTE", sections=["generic VSWR guidance"], explicit_any=True)
    context_state = _context_state(conflicting_vendor=True)
    narrowing_result, retrieval_result, selection_result = await _run_pipeline([permitted_obj], context_state, "VSWR")

    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1", telco_context_state=context_state, evidence_selection=selection_result)
    pkg = assemble_context_package(input_)

    vendor_view = next(v for v in pkg.telco_context if v.dimension == ContextDimension.VENDOR)
    assert vendor_view.state == ContextState.CONFLICTING
    assert vendor_view.accepted == []
    canonical_values = {a.canonical_value for a in vendor_view.conflicting}
    assert canonical_values == {"ERICSSON", "NOKIA"}


@pytest.mark.asyncio
async def test_controlled_scenario_no_evidence_produces_valid_package_with_no_fallback() -> None:
    """§60: narrowing/retrieval legitimately yield no evidence (e.g. an
    unrelated query against the corpus) -- the package must still be
    valid, with knowledge evidence explicitly empty, and no fallback to
    a broader/excluded search."""
    permitted_obj = _knowledge_object("VSWR-ERICSSON-LTE", vendor="Ericsson", technology="LTE", sections=["VSWR Over Threshold alarm procedure."])
    context_state = _context_state()
    # A query that matches nothing in the (tiny, fake) corpus.
    narrowing_result, retrieval_result, selection_result = await _run_pipeline([permitted_obj], context_state, "completely unrelated billing question")

    assert selection_result.selected == []
    input_ = ContextPackageInput(owner_kind=ContextProfileOwnerKind.SESSION, owner_id="sess-1", telco_context_state=context_state, evidence_selection=selection_result, retrieved_candidate_count=len(retrieval_result.candidates))
    pkg = assemble_context_package(input_)

    assert pkg.evidence.items == []
    assert pkg.evidence.selected_evidence_count == 0
    assert pkg.assembly_trace.selected_evidence_count == 0
    assert pkg.content_fingerprint != ""
