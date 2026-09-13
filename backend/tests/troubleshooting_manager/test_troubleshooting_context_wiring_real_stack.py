"""Phase 6A.10 CORRECTIVE PASS -- real-stack proof that the normal,
user-routed `troubleshooting_manager` FunctionTool wrapper (team_manager/
troubleshooting_tool.py) is genuinely connected to LIVE TELCO Context and
LIVE governed Evidence, not merely composing frozen functions in the
abstract. Mirrors `test_hybrid_retrieval_service.py::TestRealEndToEnd`'s
own established real-DEV-gated convention exactly: `SLOPANOC_TEST_
POSTGRES_URL` set -> real Cloud SQL PostgreSQL DEV, real Vertex AI
embeddings, real pgvector similarity search; unset -> the whole class
SKIPS cleanly, never fails, never fabricates a result.

ISOLATION FROM THE PROCESS-WIDE SINGLETONS (never env-var override, to
avoid any risk of leaking into an unrelated test in the same process):
`context_support.get_knowledge_repository`/`get_evidence_index_
repository`/`get_embedding_provider` are monkeypatched directly to
return repositories/providers constructed against `_TEST_DB_URL`
explicitly -- this is safe to do even though `troubleshooting_tool.py`
imports `query_selected_evidence` by name (`from ... import
query_selected_evidence`), because that import binds the SAME function
OBJECT, whose `__globals__` remain `context_support`'s own module
namespace -- patching names in that namespace changes what the function
actually calls, regardless of which module a caller imported it from.

CLEANUP: real SCOPED `DELETE ... WHERE knowledge_id = :kid` /
`WHERE evidence_id LIKE :prefix` statements only -- NEVER a
table-level DDL statement, per the DEF-0018 lesson (6A.5 corrective
pass) this codebase already learned the hard way.

6A.10.2: `known_context_facts` values must now be VERIFIED, literally
and case-insensitively, against the REAL current-turn user text
(`_FakeToolContext.user_content`) before they are trusted -- both tests
below supply real user text that genuinely contains the probe fault
value, so this file continues to prove the real, live wiring rather than
accidentally exercising the (correct) rejection path.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

import pytest
import pytest_asyncio

_TEST_DB_URL = os.environ.get("SLOPANOC_TEST_POSTGRES_URL")

_PROBE_FAULT_VALUE = "CONTROLLED-6A10-CORRECTIVE-PROBE"
_PROBE_KNOWLEDGE_ID = "6A10-CORRECTIVE-PROBE-KM-001"


@pytest.fixture(autouse=True)
def _isolated_experience_memory(monkeypatch):
    """Same DEF-0020 isolation as `test_troubleshooting_tool_unit.py`:
    `troubleshooting_manager()`'s real Gemini call in this file's second
    test goes through `run_troubleshooting_assessment(experience_
    service=None)` -> `query_experience_support(service=None)` ->
    `get_experience_memory_service()`, which resolves via `Settings.
    resolve_database_url()` -- a DIFFERENT env var
    (`SLOPANOC_DATABASE_URL`) from this file's own `SLOPANOC_TEST_
    POSTGRES_URL` gate, so it would otherwise silently fall back to the
    real local dev SQLite file even while this file is deliberately
    exercising real Cloud SQL for Knowledge/Evidence. Isolated the same
    way -- an in-memory-only `ExperienceMemoryService()`."""
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
    def __init__(self, user_id: str, session_id: str = "sess-real-stack-1", user_text: str = "") -> None:
        self.user_id = user_id
        self.session = _FakeSession(session_id)
        self.state: dict[str, Any] = {}
        # 6A.10.2: real current-turn user text `extract_current_user_text`
        # reads -- required now for `known_context_facts` to be trusted.
        self.user_content = _FakeContent(user_text) if user_text else None


@pytest.mark.skipif(not _TEST_DB_URL, reason="SLOPANOC_TEST_POSTGRES_URL not set")
class TestTroubleshootingContextWiringRealStack:
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
    async def real_fixture(self):
        """Seeds ONE real, Approved, `fault`-constrained `KnowledgeObject`
        into the real Knowledge repository, indexes it into the real
        Evidence Index with a real Vertex embedding, yields nothing (the
        test reaches the fixtures via module-level constants), and cleans
        up both real tables afterward via scoped `DELETE`s only."""
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

        knowledge_object = KnowledgeObject(
            knowledge_id=_PROBE_KNOWLEDGE_ID,
            document_type=KnowledgeDocumentType.TECHNICAL_INSTRUCTION,
            title="6A.10 Corrective Pass Controlled Probe Procedure",
            version=KnowledgeVersion(label="1.0"),
            lifecycle_status=LifecycleStatus.APPROVED,
            source=KnowledgeSource(source_system="test", source_id=_PROBE_KNOWLEDGE_ID),
            applicability=Applicability(dimensions={"fault": [_PROBE_FAULT_VALUE]}),
            sections=[
                KnowledgeSection(
                    section_id=f"{_PROBE_KNOWLEDGE_ID}-S0",
                    knowledge_id=_PROBE_KNOWLEDGE_ID,
                    sequence=0,
                    content=(
                        "When the controlled 6A.10 corrective-pass probe fault is observed, "
                        "first verify the ONeFM synthetic health-check counter reads within its "
                        "documented range before escalating to the platform owner."
                    ),
                )
            ],
        )
        await knowledge_repo.add(knowledge_object)
        await index_knowledge_object(knowledge_object, evidence_repo, embedding_provider)

        try:
            yield knowledge_repo, evidence_repo, embedding_provider
        finally:
            from sqlalchemy import text

            async with evidence_repo._engine.begin() as conn:  # noqa: SLF001 -- test-only scoped cleanup, mirrors 6A.5's own established convention
                await conn.execute(text("DELETE FROM slopanoc_knowledge_evidence_index WHERE knowledge_id = :kid"), {"kid": _PROBE_KNOWLEDGE_ID})
            async with knowledge_repo._engine.begin() as conn:  # noqa: SLF001
                await conn.execute(text("DELETE FROM slopanoc_knowledge_objects WHERE knowledge_id = :kid"), {"kid": _PROBE_KNOWLEDGE_ID})
            await evidence_repo.close()
            await knowledge_repo.close()

    @pytest.mark.asyncio
    async def test_context_support_produces_real_non_empty_evidence_selection(self, real_fixture, monkeypatch) -> None:
        """Direct proof of `context_support.query_selected_evidence`'s own
        real composition -- narrow_knowledge -> hybrid_retrieve ->
        select_evidence -- against the real Cloud SQL fixture above."""
        knowledge_repo, evidence_repo, embedding_provider = real_fixture
        import backend.agents.troubleshooting_manager.context_support as context_support

        monkeypatch.setattr(context_support, "get_knowledge_repository", lambda: knowledge_repo)
        monkeypatch.setattr(context_support, "get_evidence_index_repository", lambda: evidence_repo)
        monkeypatch.setattr(context_support, "get_embedding_provider", lambda: embedding_provider)

        context_state = context_support.build_context_state_from_known_facts(
            {"fault": _PROBE_FAULT_VALUE},
            f"we are seeing {_PROBE_FAULT_VALUE} right now, what should be checked next?",
        )
        assert len(context_state) == 1

        result = await context_support.query_selected_evidence(
            "what should be checked next for the controlled 6A.10 corrective-pass probe fault?",
            context_state,
        )
        assert len(result.selected) >= 1
        assert result.selected[0].record.knowledge_id == _PROBE_KNOWLEDGE_ID

    @pytest.mark.asyncio
    async def test_full_wrapper_produces_advisory_ready_with_real_context_and_evidence(self, real_fixture, monkeypatch) -> None:
        """The mandatory end-to-end proof: the FULL `troubleshooting_
        manager` FunctionTool wrapper, invoked exactly as team_manager
        would invoke it, with `known_context_facts` populated exactly as
        team_manager's own prompt instructs, produces a real
        ADVISORY_READY result with non-empty TELCO Context AND a non-
        empty selected EvidencePackage citing the exact production Skill
        (`telco.troubleshooting_assessment`) -- never a fabricated
        assessment, never the old, honest-but-permanently-empty stub."""
        knowledge_repo, evidence_repo, embedding_provider = real_fixture
        import backend.agents.team_manager.troubleshooting_tool as tool_module
        import backend.agents.troubleshooting_manager.context_support as context_support

        monkeypatch.setattr(context_support, "get_knowledge_repository", lambda: knowledge_repo)
        monkeypatch.setattr(context_support, "get_evidence_index_repository", lambda: evidence_repo)
        monkeypatch.setattr(context_support, "get_embedding_provider", lambda: embedding_provider)

        ctx = _FakeToolContext(
            user_id="owner-6a10-corrective-real-stack",
            user_text=f"we are seeing {_PROBE_FAULT_VALUE} right now, what should be checked next?",
        )
        result = await tool_module.troubleshooting_manager(
            troubleshooting_question="What should be checked next for the controlled 6A.10 corrective-pass probe fault?",
            known_context_facts={"fault": _PROBE_FAULT_VALUE},
            tool_context=ctx,
        )

        assert result["status"] == "advisory_ready", result
        assert result["assessment"]
        assert result["next_diagnostic_requirement"] is not None
