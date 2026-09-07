"""Phase 5.1I: `KnowledgeToolService.search`/`validate_selection` --
end-to-end composition of REAL frozen 5.1F/5.1G/5.1H components (a real
`SQLiteKnowledgeRepository`, `KnowledgeRetrievalService`,
`KnowledgeProvenanceService`), plus the correlation algorithm's fail-
closed behavior using controlled doubles.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
import pytest_asyncio

from backend.knowledge.domain.applicability import ApplicabilityContext, ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.provenance.contracts import (
    KnowledgeEvidenceItem,
    KnowledgeEvidenceReference,
    KnowledgeEvidenceSelectionError,
    KnowledgeEvidenceSelectionKey,
    KnowledgeEvidenceSet,
)
from backend.knowledge.provenance.service import KnowledgeProvenanceService
from backend.knowledge.repository.sqlite import SQLiteKnowledgeRepository
from backend.knowledge.retrieval.contracts import (
    KnowledgeRetrievalDiagnostic,
    KnowledgeRetrievalDiagnosticReason,
    KnowledgeRetrievalItem,
    KnowledgeRetrievalResult,
)
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.knowledge.tools.contracts import KnowledgeSearchToolRequest, KnowledgeToolConsistencyError, KnowledgeToolExecutionContext
from backend.knowledge.tools.service import KnowledgeToolService, _correlate

_AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)
_EFFECTIVE_FROM = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _governed_object(
    knowledge_id: str = "k1",
    title: str = "Router Outage Guide",
    document_type: KnowledgeDocumentType = KnowledgeDocumentType.SOP,
    source_system: str = "test",
    content: str = "Router outage recovery steps.",
    dimensions: dict[str, list[str]] | None = None,
    section_id: str | None = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=document_type,
        title=title,
        version=KnowledgeVersion(label="v1", effective_from=_EFFECTIVE_FROM),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system=source_system, source_id=f"{knowledge_id}-doc", source_uri=f"https://internal/{knowledge_id}", display_name=f"{knowledge_id} display"),
        applicability=Applicability(dimensions=dimensions or {}),
        sections=[
            KnowledgeSection(
                section_id=section_id or f"{knowledge_id}:v1:s0", knowledge_id=knowledge_id, heading="Overview", sequence=0, content=content, source_locator="p3"
            )
        ],
    )


@pytest_asyncio.fixture
async def repo():
    repository = SQLiteKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    yield repository
    await repository.close()


def _service(repository: SQLiteKnowledgeRepository) -> KnowledgeToolService:
    return KnowledgeToolService(KnowledgeRetrievalService(repository), KnowledgeProvenanceService(repository))


# --- basic search composition ---------------------------------------------------


@pytest.mark.asyncio
async def test_basic_search_returns_validated_evidence(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_governed_object())
    service = _service(repo)

    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))

    assert len(execution.agent_payload.items) == 1
    assert len(execution.evidence_set.items) == 1
    item = execution.agent_payload.items[0]
    assert item.title == "Router Outage Guide"
    assert item.content == "Router outage recovery steps."


@pytest.mark.asyncio
async def test_search_trusted_evidence_set_available_after_execution(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_governed_object())
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))
    assert isinstance(execution.evidence_set, KnowledgeEvidenceSet)
    assert execution.evidence_set.items[0].reference.knowledge_id == "k1"


# --- agent payload vs internal evidence: the core trust split -----------------


@pytest.mark.asyncio
async def test_agent_payload_has_no_evidence_set_field(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_governed_object())
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))
    assert not hasattr(execution.agent_payload, "evidence_set")
    assert set(execution.agent_payload.model_dump().keys()) == {"items", "diagnostics"}


# --- source_uri: retained in evidence, excluded from agent payload -----------


@pytest.mark.asyncio
async def test_source_uri_retained_in_evidence_but_absent_from_agent_payload(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_governed_object())
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))

    assert execution.evidence_set.items[0].source.source_uri == "https://internal/k1"
    dumped_item = execution.agent_payload.items[0].model_dump()
    assert "source_uri" not in dumped_item


# --- authoritative field origin -------------------------------------------------


@pytest.mark.asyncio
async def test_authoritative_fields_come_from_evidence_not_pre_provenance_state(repo: SQLiteKnowledgeRepository) -> None:
    """The governed object's REAL title/content/source is distinct from
    anything a hypothetically-tampered retrieval item might have claimed
    -- this test proves the values in the agent payload are the real
    governed ones by round-tripping through the actual repository.
    """
    await repo.add(_governed_object(title="The Real Governed Title", content="The real governed content."))
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="real governed"), KnowledgeToolExecutionContext(as_of=_AS_OF))

    item = execution.agent_payload.items[0]
    assert item.title == "The Real Governed Title"
    assert item.content == "The real governed content."
    assert item.source_system == "test"
    assert item.source_id == "k1-doc"
    assert item.source_display_name == "k1 display"
    assert item.source_locator == "p3"


# --- correlation ------------------------------------------------------------------


def _make_retrieval_item(knowledge_id: str, version_label: str, section_id: str, content: str = "c") -> KnowledgeRetrievalItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    return KnowledgeRetrievalItem(
        knowledge_id=knowledge_id, document_type=KnowledgeDocumentType.SOP, title="t", version_label=version_label,
        lifecycle_status=LifecycleStatus.APPROVED, section=section, source=source,
        applicability_outcome=ApplicabilityOutcome.MATCH, relevance_score=0.5,
    )


def _make_evidence_item(knowledge_id: str, version_label: str, section_id: str, content: str = "c") -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(knowledge_id=knowledge_id, version_label=version_label, section_id=section_id, source_system="test", source_id=f"{knowledge_id}-doc")
    return KnowledgeEvidenceItem(reference=reference, title="t", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)


def test_correlate_normal_matching_succeeds() -> None:
    retrieval_items = [_make_retrieval_item("k1", "v1", "s1")]
    evidence_items = [_make_evidence_item("k1", "v1", "s1")]
    result = _correlate(retrieval_items, evidence_items)
    assert len(result) == 1
    assert result[0].selection_key.section_id == "s1"


def test_correlate_retrieval_identity_missing_from_evidence_fails() -> None:
    retrieval_items = [_make_retrieval_item("k1", "v1", "s1")]
    with pytest.raises(KnowledgeToolConsistencyError):
        _correlate(retrieval_items, [])


def test_correlate_unexpected_evidence_identity_fails() -> None:
    evidence_items = [_make_evidence_item("k1", "v1", "s1")]
    with pytest.raises(KnowledgeToolConsistencyError):
        _correlate([], evidence_items)


def test_correlate_duplicate_retrieval_identity_fails() -> None:
    retrieval_items = [_make_retrieval_item("k1", "v1", "s1"), _make_retrieval_item("k1", "v1", "s1")]
    evidence_items = [_make_evidence_item("k1", "v1", "s1")]
    with pytest.raises(KnowledgeToolConsistencyError):
        _correlate(retrieval_items, evidence_items)


def test_correlate_duplicate_evidence_identity_fails() -> None:
    retrieval_items = [_make_retrieval_item("k1", "v1", "s1")]
    evidence_items = [_make_evidence_item("k1", "v1", "s1"), _make_evidence_item("k1", "v1", "s1")]
    with pytest.raises(KnowledgeToolConsistencyError):
        _correlate(retrieval_items, evidence_items)


def test_correlate_does_not_rely_on_positional_zip() -> None:
    """Two items whose positions are swapped between the two lists still
    correlate correctly by identity, not position.
    """
    retrieval_items = [_make_retrieval_item("k1", "v1", "sA"), _make_retrieval_item("k1", "v1", "sB")]
    evidence_items = [_make_evidence_item("k1", "v1", "sB"), _make_evidence_item("k1", "v1", "sA")]
    result = _correlate(retrieval_items, evidence_items)
    assert [i.selection_key.section_id for i in result] == ["sA", "sB"]


# --- ordering: 5.1G ranking order preserved end to end -------------------------


@pytest.mark.asyncio
async def test_search_preserves_5_1g_ranking_order(repo: SQLiteKnowledgeRepository) -> None:
    high = _governed_object(knowledge_id="k-high", title="router outage", content="router outage")
    low = _governed_object(knowledge_id="k-low", title="router", content="router")
    await repo.add(low)
    await repo.add(high)
    service = _service(repo)

    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage", limit=10), KnowledgeToolExecutionContext(as_of=_AS_OF))
    assert [item.selection_key.knowledge_id for item in execution.agent_payload.items] == ["k-high", "k-low"]


# --- bounded result count -------------------------------------------------------


@pytest.mark.asyncio
async def test_search_result_count_never_exceeds_requested_limit(repo: SQLiteKnowledgeRepository) -> None:
    for i in range(8):
        await repo.add(_governed_object(knowledge_id=f"k{i}", title="router outage", content="router outage"))
    service = _service(repo)

    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage", limit=3), KnowledgeToolExecutionContext(as_of=_AS_OF))
    assert len(execution.agent_payload.items) == 3
    assert len(execution.evidence_set.items) == 3


# --- content fidelity ------------------------------------------------------------


@pytest.mark.asyncio
async def test_content_fidelity_operational_looking_text(repo: SQLiteKnowledgeRepository) -> None:
    tricky_content = (
        "Run: `systemctl restart router-agent`\n"
        "Config path: C:\\Program Files\\Router\\config.yaml\n"
        "Multiline:\n  - step 1\n  - step 2\n"
        "Symbols: #!/bin/bash && echo 'done' || exit 1; 100% -> [OK]"
    )
    await repo.add(_governed_object(content=tricky_content, title="router outage tricky"))
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage tricky"), KnowledgeToolExecutionContext(as_of=_AS_OF))
    assert execution.agent_payload.items[0].content == tricky_content


# --- applicability outcome preserved --------------------------------------------


@pytest.mark.asyncio
async def test_partial_match_and_unknown_are_preserved_not_normalized(repo: SQLiteKnowledgeRepository) -> None:
    partial = _governed_object(knowledge_id="k-partial", title="router outage partial", content="router outage", dimensions={"region": ["apac"], "vendor": ["acme"]})
    unknown = _governed_object(knowledge_id="k-unknown", title="router outage unknown", content="router outage", dimensions={"vendor": ["acme"]})
    await repo.add(partial)
    await repo.add(unknown)
    service = _service(repo)

    context = KnowledgeToolExecutionContext(as_of=_AS_OF, applicability_context=ApplicabilityContext(dimensions={"region": ["apac"]}))
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage", limit=10), context)

    outcomes = {item.selection_key.knowledge_id: item.applicability_outcome for item in execution.agent_payload.items}
    assert outcomes["k-partial"] is ApplicabilityOutcome.PARTIAL_MATCH
    assert outcomes["k-unknown"] is ApplicabilityOutcome.UNKNOWN


# --- relevance score survives, and is not renamed -------------------------------


@pytest.mark.asyncio
async def test_relevance_score_survives_into_agent_payload(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_governed_object(title="router outage", content="router outage"))
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))
    assert execution.agent_payload.items[0].relevance_score == 1.0


# --- safe diagnostics -------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_family_diagnostic_alongside_valid_evidence(repo: SQLiteKnowledgeRepository) -> None:
    healthy = _governed_object(knowledge_id="k-healthy", title="router outage", content="router outage")
    amb_a = _governed_object(knowledge_id="k-amb", title="router outage amb", content="router outage")
    amb_b = KnowledgeObject(
        knowledge_id="k-amb", document_type=KnowledgeDocumentType.SOP, title="router outage amb",
        version=KnowledgeVersion(label="v2", effective_from=_EFFECTIVE_FROM), lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="k-amb-doc-2"),
        sections=[KnowledgeSection(section_id="k-amb:v2:s0", knowledge_id="k-amb", sequence=0, content="router outage")],
    )
    await repo.add(healthy)
    await repo.add(amb_a)
    await repo.add(amb_b)
    service = _service(repo)

    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage", limit=10), KnowledgeToolExecutionContext(as_of=_AS_OF))

    assert [item.selection_key.knowledge_id for item in execution.agent_payload.items] == ["k-healthy"]
    assert len(execution.agent_payload.diagnostics) == 1
    diagnostic = execution.agent_payload.diagnostics[0]
    assert diagnostic.knowledge_id == "k-amb"
    assert diagnostic.reason is KnowledgeRetrievalDiagnosticReason.CURRENT_VERSION_AMBIGUOUS
    assert "router outage" not in (diagnostic.detail or "")


# --- empty search ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_repository_returns_normal_empty_success(repo: SQLiteKnowledgeRepository) -> None:
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))
    assert execution.agent_payload.items == []
    assert execution.evidence_set.items == []


@pytest.mark.asyncio
async def test_zero_relevance_returns_normal_empty_success(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_governed_object(title="unrelated", content="completely unrelated content"))
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))
    assert execution.agent_payload.items == []


# --- selection validation flow --------------------------------------------------


@pytest.mark.asyncio
async def test_validate_selection_returns_exact_trusted_item(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_governed_object())
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))

    key = execution.agent_payload.items[0].selection_key
    selected = service.validate_selection(execution, [key])
    assert len(selected) == 1
    assert selected[0].section.content == "Router outage recovery steps."


@pytest.mark.asyncio
async def test_validate_selection_rejects_fabricated_key(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_governed_object())
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))

    with pytest.raises(KnowledgeEvidenceSelectionError):
        service.validate_selection(execution, [KnowledgeEvidenceSelectionKey(knowledge_id="k1", version_label="v1", section_id="fabricated")])


@pytest.mark.asyncio
async def test_validate_selection_rejects_real_but_not_retrieved_section(repo: SQLiteKnowledgeRepository) -> None:
    """Repository contains section B (a sibling, unretrieved section);
    the search only retrieved section A. Selecting B must fail -- the
    validation universe is `execution.evidence_set`, never the
    repository.
    """
    governed = _governed_object(title="guide", content="router outage")
    sibling_section = KnowledgeSection(section_id="k1:v1:s1", knowledge_id="k1", sequence=1, content="completely unrelated sibling content")
    governed_with_sibling = governed.model_copy(update={"sections": [governed.sections[0], sibling_section]})
    await repo.add(governed_with_sibling)
    service = _service(repo)

    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))
    assert [item.selection_key.section_id for item in execution.agent_payload.items] == ["k1:v1:s0"]

    with pytest.raises(KnowledgeEvidenceSelectionError):
        service.validate_selection(execution, [KnowledgeEvidenceSelectionKey(knowledge_id="k1", version_label="v1", section_id="k1:v1:s1")])


@pytest.mark.asyncio
async def test_model_cannot_submit_an_evidence_set_as_authority(repo: SQLiteKnowledgeRepository) -> None:
    """`validate_selection` only accepts `KnowledgeEvidenceSelectionKey`
    identities and the trusted `execution_result` -- there is no
    parameter through which a caller could supply an alternate
    `KnowledgeEvidenceSet` to validate against.
    """
    import inspect

    signature = inspect.signature(KnowledgeToolService.validate_selection)
    assert list(signature.parameters) == ["self", "execution_result", "selections"]


# --- JSON serialization -----------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_payload_serializes_to_json_cleanly(repo: SQLiteKnowledgeRepository) -> None:
    await repo.add(_governed_object())
    service = _service(repo)
    execution = await service.search(KnowledgeSearchToolRequest(query_text="router outage"), KnowledgeToolExecutionContext(as_of=_AS_OF))

    dumped = execution.agent_payload.model_dump(mode="json")
    assert dumped["items"][0]["content"] == "Router outage recovery steps."
    assert dumped["items"][0]["applicability_outcome"] == "match"
    assert dumped["items"][0]["document_type"] == "sop"
    assert "source_uri" not in dumped["items"][0]
    assert "evidence_set" not in dumped
