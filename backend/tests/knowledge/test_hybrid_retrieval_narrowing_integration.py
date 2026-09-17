"""Phase 6A.5 §37/§58: the full 6A.4 -> 6A.5 chained pipeline, proven
together -- NOT 6A.4 and 6A.5 each tested in isolation. Uses the exact
controlled corpus shape §37 specifies (A-F), built with explicit TELCO
applicability so 6A.4's own real, unmodified `narrow_corpus` produces a
real `permitted_knowledge_ids` list that is then fed DIRECTLY into 6A.5's
`hybrid_retrieve` -- proving the excluded/indeterminate documents never
appear even as an intermediate hit from ANY channel.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin
from backend.context.domain.models import ContextAssertion, compute_context_state
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeMetadata, KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.domain.asset_metadata import KnowledgeAssetMetadata
from backend.knowledge.hybrid_retrieval.contracts import HybridRetrievalQuery
from backend.knowledge.hybrid_retrieval.embedding import EmbeddingVector
from backend.knowledge.hybrid_retrieval.indexing import index_knowledge_object
from backend.knowledge.hybrid_retrieval.service import hybrid_retrieve
from backend.knowledge.narrowing.service import narrow_corpus

_AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
_EFF = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _obj(kid, vendor=None, technology=None, customer=None, explicit_any_customer=False, sections=("content",)):
    dims = {}
    if vendor:
        dims["vendor"] = [vendor]
    if technology:
        dims["technology"] = [technology]
    if customer and not explicit_any_customer:
        dims["customer"] = [customer]
    am = KnowledgeAssetMetadata()
    if explicit_any_customer:
        am.applicability_scope.explicit_any_dimensions = ["customer"]
    return KnowledgeObject(
        knowledge_id=kid, document_type=KnowledgeDocumentType.MOP, title=kid,
        version=KnowledgeVersion(label="1.0", effective_from=_EFF), lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="x", source_id=kid),
        applicability=Applicability(dimensions=dims), metadata=KnowledgeMetadata(asset_metadata=am),
        sections=[KnowledgeSection(section_id=f"{kid}-S{i}", knowledge_id=kid, sequence=i, content=c) for i, c in enumerate(sections)],
    )


class FakeIndexRepository:
    """In-process fake -- proves the CHAINING logic (6A.4 -> 6A.5)
    without requiring real PostgreSQL for this specific integration
    point (real Postgres candidate-boundary proof already exists in
    `test_hybrid_retrieval_repository_postgres.py`/`test_hybrid_
    retrieval_service.py::TestRealEndToEnd`)."""

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

    async def delete_missing_for_version(self, knowledge_id, version_label, *, keep_evidence_ids):
        """POST-6A: mirrors the real repository's own reconciliation
        delete -- scoped to ONE (knowledge_id, version_label), returning
        how many stale rows were removed."""
        keep = set(keep_evidence_ids)
        stale = [
            evidence_id
            for evidence_id, record in self.rows.items()
            if record.knowledge_id == knowledge_id
            and record.version_label == version_label
            and evidence_id not in keep
        ]
        for evidence_id in stale:
            self.rows.pop(evidence_id, None)
        return len(stale)

    async def upsert(self, record, embedding, *, now):
        self.rows[record.evidence_id] = record
        return True

    async def exact_match(self, permitted_knowledge_ids, query_text, permitted_version_keys=None):
        from backend.knowledge.hybrid_retrieval.contracts import ChannelHit, RetrievalChannel

        if not permitted_knowledge_ids:
            return []
        return [
            ChannelHit(evidence_id=r.evidence_id, channel=RetrievalChannel.EXACT, raw_score=1.0)
            for r in self.rows.values()
            if r.knowledge_id in permitted_knowledge_ids and query_text.lower() in r.indexable_text.lower()
        ]

    async def lexical_search(self, permitted_knowledge_ids, query_text, limit, permitted_version_keys=None):
        from backend.knowledge.hybrid_retrieval.contracts import ChannelHit, RetrievalChannel

        if not permitted_knowledge_ids:
            return []
        return [
            ChannelHit(evidence_id=r.evidence_id, channel=RetrievalChannel.LEXICAL, raw_score=0.5)
            for r in self.rows.values()
            if r.knowledge_id in permitted_knowledge_ids and any(w.lower() in r.indexable_text.lower() for w in query_text.split())
        ]

    async def semantic_search(self, permitted_knowledge_ids, query_embedding, limit, permitted_version_keys=None):
        return []

    async def get_many(self, evidence_ids):
        return {eid: self.rows[eid] for eid in evidence_ids if eid in self.rows}


class FakeEmbeddingProvider:
    async def embed(self, texts):
        return [EmbeddingVector(values=tuple([0.1] * 768), model="fake", model_version="fake-v1", dimensions=768) for _ in texts]


def _context_state():
    assertions = [
        ContextAssertion(assertion_id="a1", dimension=ContextDimension.CUSTOMER, kind=AssertionKind.VALUE, raw_value="Vodafone", canonical_value="VODAFONE", origin=ContextOrigin.USER),
        ContextAssertion(assertion_id="a2", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER),
        ContextAssertion(assertion_id="a3", dimension=ContextDimension.TECHNOLOGY, kind=AssertionKind.VALUE, raw_value="LTE", canonical_value="LTE", origin=ContextOrigin.USER),
    ]
    return compute_context_state(assertions)


@pytest.mark.asyncio
async def test_full_6a4_to_6a5_pipeline_excluded_and_indeterminate_never_appear() -> None:
    """§37's exact corpus shape:
    A - exact VSWR procedure, applicable -> permitted
    B - Nokia VSWR document -> excluded by 6A.4 (vendor mismatch)
    D - Ericsson 5G document -> excluded by 6A.4 (technology mismatch)
    E - Ericsson LTE generic explicitly applicable -> permitted
    F - unspecified applicability -> indeterminate by 6A.4 (post-corrective-pass)
    """
    a = _obj("A", vendor="Ericsson", technology="LTE", customer="Vodafone", sections=["VSWR Over Threshold alarm procedure: check antenna feeder."])
    b = _obj("B", vendor="Nokia", technology="LTE", customer="Vodafone", sections=["VSWR Over Threshold alarm procedure for Nokia equipment."])
    d = _obj("D", vendor="Ericsson", technology="5G", customer="Vodafone", sections=["VSWR Over Threshold alarm procedure for 5G equipment."])
    e = _obj("E", vendor="Ericsson", technology="LTE", explicit_any_customer=True, sections=["Ericsson LTE generic troubleshooting guide, applies to all customers."])
    f = _obj("F", sections=["VSWR Over Threshold alarm procedure with no declared applicability."])

    corpus = [a, b, d, e, f]
    narrowing_result = narrow_corpus(corpus, _context_state(), as_of=_AS_OF)

    assert sorted(narrowing_result.permitted_knowledge_ids) == ["A", "E"]
    assert "B" in narrowing_result.excluded_knowledge_ids
    assert "D" in narrowing_result.excluded_knowledge_ids
    assert "F" in narrowing_result.indeterminate_knowledge_ids

    # --- 6A.5 receives ONLY 6A.4's permitted_knowledge_ids ---
    repo = FakeIndexRepository()
    provider = FakeEmbeddingProvider()
    for obj in corpus:  # index the FULL corpus -- the boundary must be enforced at QUERY time, not by withholding indexing
        await index_knowledge_object(obj, repo, provider)

    query = HybridRetrievalQuery(query_text="VSWR", permitted_knowledge_ids=narrowing_result.permitted_knowledge_ids, limit=10)
    result = await hybrid_retrieve(query, repo, provider)

    result_knowledge_ids = {c.record.knowledge_id for c in result.candidates}
    assert result_knowledge_ids <= {"A", "E"}
    assert "B" not in result_knowledge_ids  # excluded by 6A.4 -- never an intermediate hit
    assert "D" not in result_knowledge_ids  # excluded by 6A.4 -- never an intermediate hit
    assert "F" not in result_knowledge_ids  # indeterminate by 6A.4 -- never an intermediate hit
    assert "A" in result_knowledge_ids  # A genuinely matches "VSWR" and is permitted


@pytest.mark.asyncio
async def test_6a5_never_rediscovers_applicability_itself() -> None:
    """6A.5 has no code path that queries Applicability/TelcoContext at
    all -- proven structurally: hybrid_retrieve's own source never
    imports backend.knowledge.narrowing or backend.context."""
    import ast
    import inspect

    from backend.knowledge.hybrid_retrieval import service as module

    tree = ast.parse(inspect.getsource(module))
    imported_modules = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    assert not any(m.startswith("backend.knowledge.narrowing") for m in imported_modules)
    assert not any(m.startswith("backend.context") for m in imported_modules)
