"""Phase 5.1J: end-to-end `knowledge_search`/`knowledge_select_evidence`
tool functions over a REAL, isolated `SQLiteKnowledgeRepository` --
basic composition, source_uri exclusion, authoritative-field origin,
read-only behavior, generic document-type/source-system behavior, safe
tool errors, bounded result count, and content fidelity.
"""
from __future__ import annotations

import contextvars
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from backend.api.turn_context import bind_run_id, reset_run_id
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.provenance.contracts import KnowledgeEvidenceSelectionKey
from backend.tools.knowledge import runtime as rt
from backend.tools.knowledge.tools import knowledge_search, knowledge_select_evidence

_EFFECTIVE_FROM = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _governed(
    knowledge_id: str = "k1",
    title: str = "QuantumBanana Interface Guide",
    content: str = "QuantumBanana interface verification steps.",
    document_type: KnowledgeDocumentType = KnowledgeDocumentType.SOP,
    source_system: str = "test",
) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=document_type,
        title=title,
        version=KnowledgeVersion(label="v1", effective_from=_EFFECTIVE_FROM),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system=source_system, source_id=f"{knowledge_id}-doc", source_uri=f"https://internal/{knowledge_id}", display_name=f"{knowledge_id} doc"),
        sections=[KnowledgeSection(section_id=f"{knowledge_id}:v1:s0", knowledge_id=knowledge_id, heading="Overview", sequence=0, content=content, source_locator="p1")],
    )


@pytest_asyncio.fixture
async def isolated_repo(monkeypatch):
    """An ISOLATED, in-memory repository -- never the process-wide
    singleton/normal runtime database (instruction section 79: "Do NOT
    seed or modify the normal user/runtime KM database"). Forces the
    real `get_knowledge_repository()`/`get_knowledge_tool_service()`
    singletons (as actually called from `tools.py`'s own already-bound
    imports) to rebuild against an in-memory database for the duration
    of one test, via the env var they read plus an `lru_cache` clear --
    patching the module attribute alone would not affect `tools.py`'s
    own already-imported function reference.
    """
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()
    repository = rt.get_knowledge_repository()
    yield repository
    await repository.close()
    rt.get_knowledge_repository.cache_clear()
    rt.get_knowledge_tool_service.cache_clear()


@pytest.fixture(autouse=True)
def _clean_runtime_state():
    yield
    for run_id in ("tool-run-a", "tool-run-b"):
        rt.discard_knowledge_run_evidence_state(run_id)


def _bind(run_id: str) -> contextvars.Token:
    return bind_run_id(run_id)


# --- basic composition ------------------------------------------------------------


@pytest.mark.asyncio
async def test_basic_search_returns_safe_payload_and_records_trusted_evidence(isolated_repo) -> None:
    await isolated_repo.add(_governed())
    token = _bind("tool-run-a")
    try:
        result = await knowledge_search(query_text="QuantumBanana interface verification")
    finally:
        reset_run_id(token)

    assert "error" not in result
    assert len(result["items"]) == 1
    assert result["items"][0]["content"] == "QuantumBanana interface verification steps."
    assert len(rt.get_available_knowledge_evidence("tool-run-a").items) == 1


@pytest.mark.asyncio
async def test_result_bounded_to_requested_limit(isolated_repo) -> None:
    for i in range(6):
        await isolated_repo.add(_governed(knowledge_id=f"k{i}", title=f"guide {i}", content="QuantumBanana interface verification"))
    token = _bind("tool-run-a")
    try:
        result = await knowledge_search(query_text="QuantumBanana interface verification", limit=3)
    finally:
        reset_run_id(token)

    assert len(result["items"]) == 3


# --- source_uri exclusion ----------------------------------------------------------


@pytest.mark.asyncio
async def test_source_uri_absent_from_model_payload_but_present_in_trusted_evidence(isolated_repo) -> None:
    await isolated_repo.add(_governed())
    token = _bind("tool-run-a")
    try:
        result = await knowledge_search(query_text="QuantumBanana interface verification")
    finally:
        reset_run_id(token)

    assert "source_uri" not in str(result)
    available = rt.get_available_knowledge_evidence("tool-run-a")
    assert available.items[0].source.source_uri == "https://internal/k1"


# --- authoritative field origin -----------------------------------------------------


@pytest.mark.asyncio
async def test_authoritative_fields_come_from_the_real_governed_object(isolated_repo) -> None:
    await isolated_repo.add(_governed(title="The Real Title", content="The real QuantumBanana content."))
    token = _bind("tool-run-a")
    try:
        result = await knowledge_search(query_text="real QuantumBanana content")
    finally:
        reset_run_id(token)

    item = result["items"][0]
    assert item["title"] == "The Real Title"
    assert item["content"] == "The real QuantumBanana content."
    assert item["source_system"] == "test"
    assert item["source_id"] == "k1-doc"


# --- selection end-to-end ------------------------------------------------------------


@pytest.mark.asyncio
async def test_valid_selection_end_to_end(isolated_repo) -> None:
    await isolated_repo.add(_governed())
    token = _bind("tool-run-a")
    try:
        search_result = await knowledge_search(query_text="QuantumBanana interface verification")
        key = KnowledgeEvidenceSelectionKey(**search_result["items"][0]["selection_key"])
        selection_result = await knowledge_select_evidence(selections=[key])
    finally:
        reset_run_id(token)

    assert selection_result["status"] == "accepted"
    assert selection_result["selected"] == [{"knowledge_id": "k1", "version_label": "v1", "section_id": "k1:v1:s0"}]
    assert len(rt.snapshot_selected_knowledge_evidence("tool-run-a")) == 1


@pytest.mark.asyncio
async def test_fabricated_selection_returns_safe_error(isolated_repo) -> None:
    await isolated_repo.add(_governed())
    token = _bind("tool-run-a")
    try:
        await knowledge_search(query_text="QuantumBanana interface verification")
        bad = await knowledge_select_evidence(
            selections=[KnowledgeEvidenceSelectionKey(knowledge_id="k1", version_label="v1", section_id="fabricated")]
        )
    finally:
        reset_run_id(token)

    assert "error" in bad
    assert bad["error"]["errorCode"] == "validation_error"
    assert rt.snapshot_selected_knowledge_evidence("tool-run-a") == []


@pytest.mark.asyncio
async def test_model_payload_tampering_does_not_affect_backend_selected_evidence(isolated_repo) -> None:
    """Take a legitimate agent payload, tamper its content/source in the
    dict a model would have seen, then select using the legitimate
    selection key -- the backend-selected evidence must still come from
    the trusted `KnowledgeEvidenceSet`, never from the (irrelevant,
    already-discarded) tampered payload dict.
    """
    await isolated_repo.add(_governed(content="Original real content."))
    token = _bind("tool-run-a")
    try:
        search_result = await knowledge_search(query_text="QuantumBanana interface verification")
        tampered_payload = dict(search_result["items"][0])
        tampered_payload["content"] = "TAMPERED CONTENT"
        tampered_payload["source_system"] = "attacker-system"
        tampered_payload["source_id"] = "attacker-doc"

        key = KnowledgeEvidenceSelectionKey(**search_result["items"][0]["selection_key"])
        await knowledge_select_evidence(selections=[key])
    finally:
        reset_run_id(token)

    selected = rt.snapshot_selected_knowledge_evidence("tool-run-a")
    assert selected[0].section.content == "Original real content."
    assert selected[0].source.source_system == "test"


# --- read-only ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_knowledge_search_never_mutates_the_repository(isolated_repo) -> None:
    await isolated_repo.add(_governed())
    before = await isolated_repo.list_all()
    token = _bind("tool-run-a")
    try:
        await knowledge_search(query_text="QuantumBanana interface verification")
    finally:
        reset_run_id(token)
    after = await isolated_repo.list_all()
    assert before == after


# --- genericity: no document-type/source-system hardcoding ---------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "document_type", [KnowledgeDocumentType.MOP, KnowledgeDocumentType.SOP, KnowledgeDocumentType.RCA, KnowledgeDocumentType.OTHER]
)
async def test_arbitrary_document_types_behave_identically(isolated_repo, document_type: KnowledgeDocumentType) -> None:
    await isolated_repo.add(_governed(knowledge_id=f"k-{document_type.value}", document_type=document_type))
    token = _bind("tool-run-a")
    try:
        result = await knowledge_search(query_text="QuantumBanana interface verification")
    finally:
        reset_run_id(token)
    assert any(item["document_type"] == document_type.value for item in result["items"])


@pytest.mark.asyncio
async def test_arbitrary_source_system_behaves_identically(isolated_repo) -> None:
    await isolated_repo.add(_governed(source_system="FutureSystemX"))
    token = _bind("tool-run-a")
    try:
        result = await knowledge_search(query_text="QuantumBanana interface verification")
    finally:
        reset_run_id(token)
    assert result["items"][0]["source_system"] == "FutureSystemX"


# --- empty search --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_repository_search_succeeds_normally(isolated_repo) -> None:
    token = _bind("tool-run-a")
    try:
        result = await knowledge_search(query_text="anything")
    finally:
        reset_run_id(token)
    assert result["items"] == []
    assert "error" not in result


# --- unbound run (no chat_service.py orchestration) -----------------------------------


@pytest.mark.asyncio
async def test_search_works_without_a_bound_run_id(isolated_repo) -> None:
    """Local `adk run`/standalone-test scenario: no `current_run_id()`
    bound -- search still functions (graceful degradation), using the
    documented fallback key.
    """
    await isolated_repo.add(_governed())
    result = await knowledge_search(query_text="QuantumBanana interface verification")
    assert "error" not in result
    assert len(result["items"]) == 1


# --- validation errors are safe --------------------------------------------------------


@pytest.mark.asyncio
async def test_excessive_limit_returns_safe_validation_error(isolated_repo) -> None:
    token = _bind("tool-run-a")
    try:
        result = await knowledge_search(query_text="anything", limit=999)
    finally:
        reset_run_id(token)
    assert result["error"]["errorCode"] == "validation_error"
    assert "traceback" not in str(result).lower()
    assert "sql" not in str(result).lower()
