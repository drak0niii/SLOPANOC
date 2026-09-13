"""Phase 6A.11 Pass 1 -- Integrated TELCO Validation: real-stack proof
that the architecture is GENERIC, not fixture-specific, and that
incorrect-vendor governed Knowledge is deterministically excluded by the
frozen 6A.4 applicability contract before it can ever reach retrieval.

Real Cloud SQL PostgreSQL DEV, real Vertex AI embeddings, real Gemini --
mirrors `test_troubleshooting_context_wiring_real_stack.py`'s own
established real-DEV-gated convention exactly (same env var, same
skip-if-unavailable discipline, same scoped-DELETE-only cleanup).

TWO materially different controlled fixtures are seeded:
  Fixture A: vendor=ERICSSON, technology=LTE, fault=VSWR-style
  Fixture B: vendor=NOKIA,    technology=5G,  fault=CELL-DOWN-style

Proves, against the real database:
  1. Querying with Fixture A's own context retrieves ONLY Fixture A's
     Knowledge -- Fixture B is excluded by 6A.4's own applicability gate
     before hybrid retrieval ever sees it (never merely "less relevant").
  2. Querying with Fixture B's own context is the symmetric case --
     proving genericity, not a VSWR/Ericsson special case.
  3. Querying with NEITHER fixture's facts stated (insufficient context)
     narrows to nothing permitted -- the real, integrated
     `troubleshooting_manager` wrapper produces NEEDS_INFORMATION, never
     a guessed vendor/technology.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio

_TEST_DB_URL = os.environ.get("SLOPANOC_TEST_POSTGRES_URL")

_FIXTURE_A_KID = "6A11-SCENARIO-A-ERICSSON-LTE-VSWR"
_FIXTURE_A_VENDOR = "ERICSSON-6A11-SCENARIO-A"
_FIXTURE_A_TECH = "LTE-6A11-SCENARIO-A"
_FIXTURE_A_FAULT = "VSWR-OVER-THRESHOLD-6A11-SCENARIO-A"

_FIXTURE_B_KID = "6A11-SCENARIO-B-NOKIA-5G-CELLDOWN"
_FIXTURE_B_VENDOR = "NOKIA-6A11-SCENARIO-B"
_FIXTURE_B_TECH = "5G-6A11-SCENARIO-B"
_FIXTURE_B_FAULT = "CELL-DOWN-6A11-SCENARIO-B"


@pytest.fixture(autouse=True)
def _isolated_experience_memory(monkeypatch):
    """Same DEF-0020 isolation already established -- the full-wrapper
    real-model calls in this file must never touch a shared/local
    Experience Memory database."""
    from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService

    import backend.agents.troubleshooting_manager.experience_support as experience_support_module

    isolated_service = ExperienceMemoryService()
    original = experience_support_module.get_experience_memory_service
    experience_support_module.get_experience_memory_service = lambda: isolated_service
    yield
    experience_support_module.get_experience_memory_service = original


class _FakeSession:
    def __init__(self, session_id: str) -> None:
        self.id = session_id


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeToolContext:
    def __init__(self, user_id: str, session_id: str = "sess-6a11-scenario", user_text: str = "") -> None:
        self.user_id = user_id
        self.session = _FakeSession(session_id)
        self.state: dict[str, Any] = {}
        self.user_content = _FakeContent(user_text) if user_text else None


@pytest.mark.skipif(not _TEST_DB_URL, reason="SLOPANOC_TEST_POSTGRES_URL not set")
class TestSecondTelcoScenarioRealStack:
    @staticmethod
    def _force_vertex_mode() -> None:
        os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"

    @staticmethod
    async def _ensure_schema_or_skip(repo) -> None:
        try:
            await repo.ensure_schema()
        except Exception as exc:  # pragma: no cover -- exercised only when DDL is genuinely denied
            await repo.close()
            pytest.skip(f"schema-level DDL unavailable on the real DEV database right now: {exc}")

    @pytest_asyncio.fixture
    async def two_fixture_stack(self):
        """Seeds TWO real, Approved, vendor-constrained `KnowledgeObject`s
        into the real Knowledge repository, indexes both with real Vertex
        embeddings, yields the shared repositories, and cleans up both
        real tables afterward via scoped `DELETE`s only (never DDL)."""
        self._force_vertex_mode()
        from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
        from backend.knowledge.domain.models import Applicability, KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
        from backend.knowledge.hybrid_retrieval.indexing import index_knowledge_object
        from backend.knowledge.hybrid_retrieval.repository import EvidenceIndexRepository
        from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository
        from backend.knowledge_hybrid_retrieval.vertex_embedding_provider import VertexTextEmbeddingProvider

        knowledge_repo = SqlAlchemyKnowledgeRepository(_TEST_DB_URL)
        evidence_repo = EvidenceIndexRepository(_TEST_DB_URL)
        await self._ensure_schema_or_skip(evidence_repo)
        embedding_provider = VertexTextEmbeddingProvider()

        fixture_a = KnowledgeObject(
            knowledge_id=_FIXTURE_A_KID,
            document_type=KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
            title="6A.11 Scenario A -- Ericsson LTE VSWR Controlled Procedure",
            version=KnowledgeVersion(label="1.0"),
            lifecycle_status=LifecycleStatus.APPROVED,
            source=KnowledgeSource(source_system="test", source_id=_FIXTURE_A_KID),
            applicability=Applicability(dimensions={"vendor": [_FIXTURE_A_VENDOR], "technology": [_FIXTURE_A_TECH], "fault": [_FIXTURE_A_FAULT]}),
            sections=[
                KnowledgeSection(
                    section_id=f"{_FIXTURE_A_KID}-S0",
                    knowledge_id=_FIXTURE_A_KID,
                    sequence=0,
                    content=(
                        "For the 6A.11 Scenario A controlled Ericsson LTE VSWR fault, "
                        "first verify the antenna feeder connector torque before escalating to the platform owner."
                    ),
                )
            ],
        )
        fixture_b = KnowledgeObject(
            knowledge_id=_FIXTURE_B_KID,
            document_type=KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
            title="6A.11 Scenario B -- Nokia 5G Cell Down Controlled Procedure",
            version=KnowledgeVersion(label="1.0"),
            lifecycle_status=LifecycleStatus.APPROVED,
            source=KnowledgeSource(source_system="test", source_id=_FIXTURE_B_KID),
            applicability=Applicability(dimensions={"vendor": [_FIXTURE_B_VENDOR], "technology": [_FIXTURE_B_TECH], "fault": [_FIXTURE_B_FAULT]}),
            sections=[
                KnowledgeSection(
                    section_id=f"{_FIXTURE_B_KID}-S0",
                    knowledge_id=_FIXTURE_B_KID,
                    sequence=0,
                    content=(
                        "For the 6A.11 Scenario B controlled Nokia 5G cell-down fault, "
                        "first verify gNB transport link status before escalating to the platform owner."
                    ),
                )
            ],
        )
        await knowledge_repo.add(fixture_a)
        await knowledge_repo.add(fixture_b)
        await index_knowledge_object(fixture_a, evidence_repo, embedding_provider)
        await index_knowledge_object(fixture_b, evidence_repo, embedding_provider)

        try:
            yield knowledge_repo, evidence_repo, embedding_provider
        finally:
            from sqlalchemy import text

            async with evidence_repo._engine.begin() as conn:  # noqa: SLF001
                await conn.execute(text("DELETE FROM slopanoc_knowledge_evidence_index WHERE knowledge_id = ANY(:kids)"), {"kids": [_FIXTURE_A_KID, _FIXTURE_B_KID]})
            async with knowledge_repo._engine.begin() as conn:  # noqa: SLF001
                await conn.execute(text("DELETE FROM slopanoc_knowledge_objects WHERE knowledge_id = ANY(:kids)"), {"kids": [_FIXTURE_A_KID, _FIXTURE_B_KID]})
            await evidence_repo.close()
            await knowledge_repo.close()

    def _patch_singletons(self, monkeypatch, knowledge_repo, evidence_repo, embedding_provider):
        import backend.agents.troubleshooting_manager.context_support as context_support

        monkeypatch.setattr(context_support, "get_knowledge_repository", lambda: knowledge_repo)
        monkeypatch.setattr(context_support, "get_evidence_index_repository", lambda: evidence_repo)
        monkeypatch.setattr(context_support, "get_embedding_provider", lambda: embedding_provider)

    @pytest.mark.asyncio
    async def test_scenario_a_ericsson_lte_vswr_excludes_nokia_fixture(self, two_fixture_stack, monkeypatch) -> None:
        knowledge_repo, evidence_repo, embedding_provider = two_fixture_stack
        self._patch_singletons(monkeypatch, knowledge_repo, evidence_repo, embedding_provider)
        import backend.agents.team_manager.troubleshooting_tool as tool_module

        user_text = f"We have {_FIXTURE_A_VENDOR} {_FIXTURE_A_TECH} equipment showing {_FIXTURE_A_FAULT}, what should be checked next?"
        ctx = _FakeToolContext(user_id="owner-6a11-scenario-a", user_text=user_text)
        result = await tool_module.troubleshooting_manager(
            troubleshooting_question=f"What should be checked next for {_FIXTURE_A_FAULT}?",
            known_context_facts={"vendor": _FIXTURE_A_VENDOR, "technology": _FIXTURE_A_TECH, "fault": _FIXTURE_A_FAULT},
            tool_context=ctx,
        )
        assert result["status"] == "advisory_ready", result
        assert result["assessment"]

        # Direct proof the wrong-vendor fixture was structurally excluded, not merely unranked.
        from backend.context.domain.enums import ContextDimension
        from backend.agents.troubleshooting_manager.context_support import build_context_state_from_known_facts, query_selected_evidence

        context_state = build_context_state_from_known_facts(
            {"vendor": _FIXTURE_A_VENDOR, "technology": _FIXTURE_A_TECH, "fault": _FIXTURE_A_FAULT}, user_text
        )
        selection = await query_selected_evidence(f"what should be checked next for {_FIXTURE_A_FAULT}?", context_state)
        selected_kids = {item.record.knowledge_id for item in selection.selected}
        assert _FIXTURE_A_KID in selected_kids
        assert _FIXTURE_B_KID not in selected_kids

    @pytest.mark.asyncio
    async def test_scenario_b_nokia_5g_celldown_excludes_ericsson_fixture(self, two_fixture_stack, monkeypatch) -> None:
        """The symmetric, materially different second TELCO scenario --
        proves the architecture is generic (domain, vendor, technology,
        and fault category all differ from Scenario A), never a VSWR/
        Ericsson-specific special case."""
        knowledge_repo, evidence_repo, embedding_provider = two_fixture_stack
        self._patch_singletons(monkeypatch, knowledge_repo, evidence_repo, embedding_provider)
        import backend.agents.team_manager.troubleshooting_tool as tool_module

        user_text = f"We have {_FIXTURE_B_VENDOR} {_FIXTURE_B_TECH} equipment showing {_FIXTURE_B_FAULT}, what should be checked next?"
        ctx = _FakeToolContext(user_id="owner-6a11-scenario-b", user_text=user_text)
        result = await tool_module.troubleshooting_manager(
            troubleshooting_question=f"What should be checked next for {_FIXTURE_B_FAULT}?",
            known_context_facts={"vendor": _FIXTURE_B_VENDOR, "technology": _FIXTURE_B_TECH, "fault": _FIXTURE_B_FAULT},
            tool_context=ctx,
        )
        assert result["status"] == "advisory_ready", result
        assert result["assessment"]

        from backend.agents.troubleshooting_manager.context_support import build_context_state_from_known_facts, query_selected_evidence

        context_state = build_context_state_from_known_facts(
            {"vendor": _FIXTURE_B_VENDOR, "technology": _FIXTURE_B_TECH, "fault": _FIXTURE_B_FAULT}, user_text
        )
        selection = await query_selected_evidence(f"what should be checked next for {_FIXTURE_B_FAULT}?", context_state)
        selected_kids = {item.record.knowledge_id for item in selection.selected}
        assert _FIXTURE_B_KID in selected_kids
        assert _FIXTURE_A_KID not in selected_kids

    @pytest.mark.asyncio
    async def test_insufficient_context_integrated_needs_information_no_vendor_guessed(self, two_fixture_stack, monkeypatch) -> None:
        """Neither fixture's facts are stated -- the real, integrated
        wrapper must produce NEEDS_INFORMATION, never guess a vendor/
        technology/fault merely because two real, applicable-looking
        Knowledge fixtures happen to exist in the corpus."""
        knowledge_repo, evidence_repo, embedding_provider = two_fixture_stack
        self._patch_singletons(monkeypatch, knowledge_repo, evidence_repo, embedding_provider)
        import backend.agents.team_manager.troubleshooting_tool as tool_module

        ctx = _FakeToolContext(user_id="owner-6a11-insufficient", user_text="Something seems wrong, what should I check next?")
        result = await tool_module.troubleshooting_manager(
            troubleshooting_question="Something seems wrong, what should I check next?",
            known_context_facts=None,
            tool_context=ctx,
        )
        assert result["status"] == "needs_information", result
        assert result["assessment"] is None
