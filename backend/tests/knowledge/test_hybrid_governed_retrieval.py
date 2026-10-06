"""Tranche 1: hybrid (lexical + semantic) Generic KM retrieval under unchanged governance,
plus retrieval/selection/command-authority observability.

No network: semantic similarity comes from a TEST-ONLY concept-vector provider that
implements the production `DenseSimilarityProvider` contract. The concept table below
is test data, never production retrieval behavior.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

import pytest

from backend.agents.technical_authority_engineer.agent_tool import (
    build_server_validated_commands,
    build_server_validated_evidence,
)
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.knowledge.domain.applicability import Applicability, ApplicabilityContext, ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.provenance.service import KnowledgeProvenanceService
from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalMode, KnowledgeRetrievalQuery
from backend.knowledge.retrieval.scoring import section_retrieval_text
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.knowledge.tools.service import KnowledgeToolService
from backend.tools.knowledge import diagnostic_trace as dt
from backend.tools.knowledge import runtime as rt
from backend.tools.knowledge.dense_similarity import DenseEmbeddingError, VertexEmbeddingSimilarityProvider
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

_AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)

# --- test-only semantic model ------------------------------------------------------------
_CONCEPTS = {
    "optics": {"sfp", "transceiver", "optical", "light", "fibre", "fiber", "module"},
    "degrade": {"loss", "degradation", "weak", "low", "dropped", "attenuation"},
    "link": {"rilink", "interface", "link", "cpri"},
    "power": {"rectifier", "battery", "mains", "voltage"},
    "check": {"inspect", "check", "diagnostics", "diagnose", "levels", "verify"},
}


def _concept_vector(text: str) -> list[float]:
    words = {w.strip(".,:;()`").lower() for w in text.split()}
    vec = [float(len(words & members)) for members in _CONCEPTS.values()]
    norm = math.sqrt(sum(v * v for v in vec))
    return [v / norm for v in vec] if norm else vec


class ConceptSimilarityProvider:
    """Implements `DenseSimilarityProvider`; records which candidates it was shown."""

    model_name = "test-concepts"

    def __init__(self) -> None:
        self.seen_identities: list[tuple[str, str]] = []

    async def similarities(self, query_text: str, candidates: Sequence[tuple[KnowledgeObject, KnowledgeSection]]) -> list[float]:
        q = _concept_vector(query_text)
        out = []
        for obj, section in candidates:
            self.seen_identities.append((obj.knowledge_id, section.section_id))
            c = _concept_vector(section_retrieval_text(obj, section))
            out.append(sum(a * b for a, b in zip(q, c)))
        return out


class FailingProvider:
    model_name = "failing"

    async def similarities(self, query_text: str, candidates: Sequence[Any]) -> list[float]:
        raise DenseEmbeddingError("boom")


class SlowProvider:
    model_name = "slow"

    async def similarities(self, query_text: str, candidates: Sequence[Any]) -> list[float]:
        await asyncio.sleep(5)
        return [1.0] * len(candidates)


def _doc(
    kid: str,
    title: str,
    content: str,
    dims: Optional[dict[str, list[str]]] = None,
    status: LifecycleStatus = LifecycleStatus.APPROVED,
    label: str = "v1",
    effective: datetime = datetime(2020, 1, 1, tzinfo=timezone.utc),
) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=kid,
        document_type=KnowledgeDocumentType.MOP,
        title=title,
        version=KnowledgeVersion(label=label, effective_from=effective),
        lifecycle_status=status,
        applicability=Applicability(dimensions=dims or {}),
        source=KnowledgeSource(source_system="test", source_id=f"{kid}.docx", display_name=title),
        sections=[
            KnowledgeSection(
                section_id=f"{kid}:{label}:s0",
                knowledge_id=kid,
                heading="Procedure",
                sequence=0,
                content=content,
                source_locator="lines:1-3",
            )
        ],
    )


# Semantic target: NO token shared with the query "SFP light loss".
_TRANSCEIVER = _doc(
    "MOP-TRX",
    "Transceiver Optical Degradation",
    "Inspect optical module receive levels.\nHC Commands:\nst pluginunit\n",
)
_UNRELATED = _doc("MOP-PWR", "Rectifier Alarm", "Verify rectifier mains voltage.\nHC Commands:\nalt\n")
_QUERY = "SFP light loss"


@pytest.fixture
def repo():
    repository = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    yield repository
    asyncio.run(repository.close())


def _query(text: str = _QUERY, dims: Optional[dict[str, list[str]]] = None, limit: int = 5) -> KnowledgeRetrievalQuery:
    return KnowledgeRetrievalQuery(
        query_text=text, applicability_context=ApplicabilityContext(dimensions=dims or {}), as_of=_AS_OF, limit=limit
    )


def _ids(result: Any) -> list[str]:
    return [i.knowledge_id for i in result.items]


# --- A: lexical miss / semantic recovery, governance intact ------------------------------
@pytest.mark.asyncio
async def test_a_lexical_miss_semantic_recovery_keeps_governance(repo) -> None:
    superseded = _doc(
        "MOP-TRX", "Transceiver Optical Degradation (old)", "Inspect optical module levels (obsolete).",
        label="v0", effective=datetime(2010, 1, 1, tzinfo=timezone.utc), status=LifecycleStatus.ARCHIVE,
    )
    candidate = _doc("MOP-CAND", "Transceiver optical candidate", "Inspect optical module.", status=LifecycleStatus.CANDIDATE)
    for obj in (superseded, _TRANSCEIVER, _UNRELATED, candidate):
        await repo.add(obj)

    lexical = await KnowledgeRetrievalService(repo).retrieve(_query())
    assert lexical.items == [] and lexical.mode is KnowledgeRetrievalMode.LEXICAL

    hybrid = await KnowledgeRetrievalService(repo, dense_provider=ConceptSimilarityProvider()).retrieve(_query())
    assert hybrid.mode is KnowledgeRetrievalMode.HYBRID and hybrid.dense_status == "ok"
    assert _ids(hybrid)[0] == "MOP-TRX"
    top = hybrid.items[0]
    assert top.version_label == "v1", "only the governed-current version is retrievable"
    assert top.lifecycle_status is LifecycleStatus.APPROVED
    assert top.applicability_outcome is ApplicabilityOutcome.MATCH
    assert "MOP-PWR" not in _ids(hybrid), "semantically unrelated section stays below the dense floor"
    diag = hybrid.ranking[0]
    assert diag.sparse_score == 0.0 and diag.sparse_rank is None
    assert diag.dense_rank == 1 and diag.dense_similarity is not None
    assert "MOP-CAND" not in _ids(hybrid), "a non-approved candidate is never governed-current, however relevant"
    assert all(i.lifecycle_status is LifecycleStatus.APPROVED for i in hybrid.items)


# --- B: NOT_APPLICABLE excluded before relevance, however semantically close ---------------
@pytest.mark.asyncio
async def test_b_not_applicable_excluded_even_with_top_semantic_relevance(repo) -> None:
    other_vendor = _doc(
        "MOP-TRX-OTHER", "Transceiver optical light loss", "SFP light loss: inspect optical module levels.",
        dims={"vendor": ["VendorB"]},
    )
    matching = _doc("MOP-TRX-A", "Transceiver checks", "Inspect optical module levels.", dims={"vendor": ["VendorA"]})
    await repo.add(other_vendor)
    await repo.add(matching)
    provider = ConceptSimilarityProvider()
    result = await KnowledgeRetrievalService(repo, dense_provider=provider).retrieve(_query(dims={"vendor": ["VendorA"]}))
    assert _ids(result) == ["MOP-TRX-A"]
    assert result.items[0].applicability_outcome is ApplicabilityOutcome.MATCH
    assert all(kid != "MOP-TRX-OTHER" for kid, _ in provider.seen_identities), "never even scored"


# --- C: UNKNOWN stays UNKNOWN --------------------------------------------------------------
@pytest.mark.asyncio
async def test_c_semantic_relevance_never_converts_unknown_to_match(repo) -> None:
    unknown = _doc(
        "MOP-UNK", "Transceiver optical light loss", "SFP light loss: inspect optical module levels.",
        dims={"technology": ["5G"]},
    )
    match = _doc("MOP-MATCH", "Transceiver checks", "Inspect optical module levels.")
    await repo.add(unknown)
    await repo.add(match)
    result = await KnowledgeRetrievalService(repo, dense_provider=ConceptSimilarityProvider()).retrieve(_query())
    by_id = {i.knowledge_id: i for i in result.items}
    assert by_id["MOP-UNK"].applicability_outcome is ApplicabilityOutcome.UNKNOWN
    assert by_id["MOP-UNK"].unresolved_applicability_dimensions == ["technology"]
    assert by_id["MOP-MATCH"].applicability_outcome is ApplicabilityOutcome.MATCH
    # Higher relevance may rank UNKNOWN first; ranking is visible, authority is not implied.
    assert _ids(result)[0] == "MOP-UNK"


# --- fallback: dense failure never substitutes a synthetic signal ------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider,status",
    [(FailingProvider(), "unavailable:DenseEmbeddingError"), (SlowProvider(), "unavailable:timeout")],
)
async def test_dense_failure_falls_back_to_exact_lexical_result(repo, provider: Any, status: str) -> None:
    await repo.add(_TRANSCEIVER)
    await repo.add(_UNRELATED)
    q = _query("optical module levels")
    lexical = await KnowledgeRetrievalService(repo).retrieve(q)
    degraded = await KnowledgeRetrievalService(repo, dense_provider=provider, dense_timeout_seconds=0.05).retrieve(q)
    assert degraded.mode is KnowledgeRetrievalMode.LEXICAL
    assert degraded.dense_status == status
    assert degraded.items == lexical.items


# --- D / E / F / G through the real ADK tool adapter ---------------------------------------
@pytest.fixture
def hybrid_runtime(repo, monkeypatch):
    service = KnowledgeToolService(
        KnowledgeRetrievalService(repo, dense_provider=ConceptSimilarityProvider()), KnowledgeProvenanceService(repo)
    )
    monkeypatch.setattr("backend.tools.knowledge.tools.get_knowledge_tool_service", lambda: service)
    return repo


def _bound(run_id: str):
    class _Ctx:
        def __enter__(self):
            self.token = bind_run_id(run_id)
            return run_id

        def __exit__(self, *exc):
            reset_run_id(self.token)
            rt.discard_knowledge_run_evidence_state(run_id)
            dt.discard_diagnostic_trace(run_id)

    return _Ctx()


@pytest.mark.asyncio
async def test_d_hybrid_available_is_never_selected(hybrid_runtime) -> None:
    await hybrid_runtime.add(_TRANSCEIVER)
    with _bound("t1-d") as run_id:
        payload = await knowledge_search(_QUERY)
        assert [i["selection_key"]["knowledge_id"] for i in payload["items"]] == ["MOP-TRX"]
        assert len(rt.get_available_knowledge_evidence(run_id).items) == 1
        assert rt.snapshot_selected_knowledge_evidence(run_id) == []
        assert build_server_validated_evidence(run_id, None, []) == []


async def _search_and_select(run_id: str) -> list[Any]:
    payload = await knowledge_search(_QUERY)
    key = payload["items"][0]["selection_key"]
    assert (await knowledge_select_evidence([key]))["status"] == "accepted"
    return build_server_validated_evidence(run_id, None, [])


@pytest.mark.asyncio
async def test_e_hybrid_evidence_does_not_authorize_model_command(hybrid_runtime) -> None:
    await hybrid_runtime.add(_TRANSCEIVER)
    with _bound("t1-e") as run_id:
        evidence = await _search_and_select(run_id)
        assert evidence and evidence[0].metadata["applicability_outcome"] == "match"
        catalog = build_server_validated_commands(evidence, [{"command": "st rilink", "source_id": evidence[0].source_id}])
        assert catalog == []
        snap = dt.snapshot_diagnostic_trace(run_id, [])
        rejected = [c for c in snap["command_authority"] if c["command"] == "st rilink"]
        assert rejected and rejected[0]["decision"] == "rejected" and rejected[0]["stage"] == "grounding"
        assert "not found" in rejected[0]["reason"]


@pytest.mark.asyncio
async def test_f_grounded_read_only_command_in_selected_section_is_authorized(hybrid_runtime) -> None:
    await hybrid_runtime.add(_TRANSCEIVER)
    with _bound("t1-f") as run_id:
        evidence = await _search_and_select(run_id)
        catalog = build_server_validated_commands(evidence, [{"command": "st pluginunit", "source_id": evidence[0].source_id}])
        assert [c.command for c in catalog] == ["st pluginunit"]
        snap = dt.snapshot_diagnostic_trace(run_id, [])
        assert any(c["decision"] == "authorized" and c["command"] == "st pluginunit" for c in snap["command_authority"])


@pytest.mark.asyncio
async def test_g_trace_captures_query_ranking_scores_applicability_and_selection(hybrid_runtime) -> None:
    unknown = _doc(
        "MOP-UNK", "Transceiver optical light loss", "SFP light loss: inspect optical module levels.",
        dims={"technology": ["5G"]},
    )
    await hybrid_runtime.add(unknown)
    await hybrid_runtime.add(_TRANSCEIVER)
    with _bound("t1-g") as run_id:
        payload = await knowledge_search(_QUERY, limit=5)
        await knowledge_select_evidence([payload["items"][1]["selection_key"]])
        selected = [
            (e.reference.knowledge_id, e.reference.version_label, e.reference.section_id)
            for e in rt.snapshot_selected_knowledge_evidence(run_id)
        ]
        snap = dt.snapshot_diagnostic_trace(run_id, selected)

    search = snap["searches"][0]
    assert search["query_text"] == _QUERY and search["limit"] == 5
    assert search["retrieval_mode"] == "hybrid" and search["dense_status"] == "ok"
    assert search["eligible_section_count"] == 2 and search["candidate_count"] == 2
    ranks = [(r["rank"], r["knowledge_id"], r["applicability_outcome"], r["selection_state"]) for r in search["results"]]
    assert ranks == [(1, "MOP-UNK", "unknown", "AVAILABLE"), (2, "MOP-TRX", "match", "SELECTED")]
    first = search["results"][0]
    for field in ("sparse_score", "sparse_rank", "dense_similarity", "dense_rank", "fused_score", "relevance_score",
                  "lifecycle_status", "section_id", "version_label", "title", "section_heading", "source_id", "is_derived"):
        assert field in first
    assert first["unresolved_applicability_dimensions"] == ["technology"]
    assert first["lifecycle_status"] == "approved"
    assert snap["selections"] == [
        {"status": "accepted", "requested": [snap["selected"][0]], "accepted": [snap["selected"][0]]}
    ]
    dumped = json.dumps(snap)
    assert "inspect optical module levels" not in dumped.lower(), "no section body content"
    text = dt.format_diagnostic_trace(snap)
    assert "SEARCH 1" in text and "query: 'SFP light loss'" in text
    assert "MOP-TRX" in text and "[SELECTED]" in text and "applicability=unknown" in text


@pytest.mark.asyncio
async def test_trace_records_explicit_empty_and_rejected_selection(hybrid_runtime) -> None:
    await hybrid_runtime.add(_TRANSCEIVER)
    with _bound("t1-sel") as run_id:
        await knowledge_search(_QUERY)
        await knowledge_select_evidence([])
        await knowledge_select_evidence([{"knowledge_id": "FAKE", "version_label": "v1", "section_id": "x"}])
        snap = dt.snapshot_diagnostic_trace(run_id, [])
    assert [s["status"] for s in snap["selections"]] == ["explicit_empty", "rejected_unavailable"]
    assert snap["searches"][0]["results"][0]["selection_state"] == "AVAILABLE"


def test_trace_is_run_scoped_and_discarded() -> None:
    token = bind_run_id("t1-scope")
    try:
        dt.record_command_authority(command="alt", source_id="S", decision="rejected", reason="r", stage="grounding")
    finally:
        reset_run_id(token)
    assert dt.snapshot_diagnostic_trace("other-run", []) is None
    assert dt.snapshot_diagnostic_trace("t1-scope", [])["command_authority"]
    dt.discard_diagnostic_trace("t1-scope")
    assert dt.snapshot_diagnostic_trace("t1-scope", []) is None


def test_persisted_diagnostics_are_bounded_per_turn() -> None:
    state: dict[str, Any] = {}
    for i in range(dt.MAX_PERSISTED_TURNS + 5):
        state = dt.build_retrieval_diagnostics_delta(state, f"turn-{i}", {"run_id": str(i)})
    assert len(state) == dt.MAX_PERSISTED_TURNS
    assert "turn-0" not in state and f"turn-{dt.MAX_PERSISTED_TURNS + 4}" in state


# --- concrete Vertex provider (fake client; no network) -------------------------------------
class _FakeEmbeddings:
    def __init__(self, dim: int, fail: bool = False, bad_dim: bool = False) -> None:
        self.calls: list[tuple[int, str]] = []
        self._dim, self._fail, self._bad_dim = dim, fail, bad_dim

    async def embed_content(self, *, model: str, contents: list[str], config: Any) -> Any:
        self.calls.append((len(contents), config.task_type))
        if self._fail:
            raise RuntimeError("vertex down")
        dim = self._dim - 1 if self._bad_dim else self._dim
        vectors = [type("E", (), {"values": _concept_vector(t) + [0.0] * (dim - len(_CONCEPTS))})() for t in contents]
        return type("R", (), {"embeddings": vectors})()


def _fake_client(embeddings: _FakeEmbeddings) -> Any:
    return type("C", (), {"aio": type("A", (), {"models": embeddings})()})()


@pytest.mark.asyncio
async def test_vertex_provider_caches_section_vectors_and_uses_retrieval_task_types() -> None:
    fake = _FakeEmbeddings(dim=8)
    provider = VertexEmbeddingSimilarityProvider(dimension=8, batch_size=1, client_factory=lambda: _fake_client(fake))
    cands = [(_TRANSCEIVER, _TRANSCEIVER.sections[0]), (_UNRELATED, _UNRELATED.sections[0])]
    first = await provider.similarities(_QUERY, cands)
    assert first[0] > first[1]
    assert sorted(fake.calls) == [(1, "RETRIEVAL_DOCUMENT"), (1, "RETRIEVAL_DOCUMENT"), (1, "RETRIEVAL_QUERY")]
    fake.calls.clear()
    assert await provider.similarities(_QUERY, cands) == first
    assert fake.calls == [(1, "RETRIEVAL_QUERY")], "section vectors served from cache"


@pytest.mark.asyncio
@pytest.mark.parametrize("kwargs", [{"fail": True}, {"bad_dim": True}])
async def test_vertex_provider_raises_instead_of_synthetic_fallback(kwargs: dict[str, bool]) -> None:
    provider = VertexEmbeddingSimilarityProvider(dimension=8, client_factory=lambda: _fake_client(_FakeEmbeddings(8, **kwargs)))
    with pytest.raises(Exception):
        await provider.similarities(_QUERY, [(_TRANSCEIVER, _TRANSCEIVER.sections[0])])


# --- runtime composition / settings -----------------------------------------------------------
@pytest.mark.parametrize(
    "mode,vertex,expect_dense",
    [(None, "true", True), ("hybrid", "true", True), ("lexical", "true", False), ("hybrid", None, False)],
)
def test_runtime_composes_dense_provider_only_when_hybrid_and_vertex(monkeypatch, mode, vertex, expect_dense) -> None:
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    for var, val in (("SLOPANOC_KNOWLEDGE_RETRIEVAL_MODE", mode), ("GOOGLE_GENAI_USE_VERTEXAI", vertex)):
        if val is None:
            monkeypatch.delenv(var, raising=False)
        else:
            monkeypatch.setenv(var, val)
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()
    try:
        service = rt.get_knowledge_tool_service()
        dense = service._retrieval_service._dense_provider
        assert (dense is not None) is expect_dense
        if expect_dense:
            assert isinstance(dense, VertexEmbeddingSimilarityProvider)
    finally:
        rt.get_knowledge_repository.cache_clear()
        rt.get_knowledge_tool_service.cache_clear()


def test_invalid_retrieval_settings_rejected(monkeypatch) -> None:
    from backend.config.settings import ConfigurationError, Settings

    with pytest.raises(ConfigurationError):
        Settings({"SLOPANOC_KNOWLEDGE_RETRIEVAL_MODE": "semantic-only"}).knowledge_retrieval_mode
    with pytest.raises(ConfigurationError):
        Settings({"SLOPANOC_KNOWLEDGE_DENSE_MIN_SIMILARITY": "2"}).knowledge_dense_min_similarity
    assert Settings({}).knowledge_retrieval_mode == "hybrid"


def test_retrieval_trace_logger_is_warning_on_fail_closed(caplog) -> None:
    from backend.api.chat_service import _emit_retrieval_diagnostics

    with caplog.at_level(logging.INFO, logger="backend.knowledge.retrieval_trace"):
        _emit_retrieval_diagnostics({"run_id": "r", "searches": [], "selected": []}, "governed_fail_closed", logging.WARNING)
    assert caplog.records and caplog.records[0].levelno == logging.WARNING
    assert "retrieval_trace run_id=r final=governed_fail_closed" in caplog.records[0].getMessage()
