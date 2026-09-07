"""Phase 5.1C: the generic KnowledgeSourceAdapter Protocol -- proven only
with TEST-ONLY fake adapters (instruction section 18/36). No concrete
production adapter exists anywhere in this package.
"""
from __future__ import annotations

import pytest

from backend.knowledge.domain.models import KnowledgeSource
from backend.knowledge.ingestion.adapters import KnowledgeSourceAdapter
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument


def _document(source_system: str, source_id: str, title: str) -> IngestedKnowledgeDocument:
    return IngestedKnowledgeDocument(
        source=KnowledgeSource(source_system=source_system, source_id=source_id),
        title=title,
        content=f"Content for {title}.",
    )


class _SingleDocumentFakeAdapter:
    """A minimal test double satisfying KnowledgeSourceAdapter -- proves
    the Protocol requires nothing beyond `fetch_documents`.
    """

    async def fetch_documents(self):
        yield _document("source_a", "doc-1", "Only Document")


class _MultiDocumentFakeAdapter:
    def __init__(self, source_system: str, count: int) -> None:
        self._source_system = source_system
        self._count = count

    async def fetch_documents(self):
        for i in range(self._count):
            yield _document(self._source_system, f"doc-{i}", f"Document {i}")


@pytest.mark.asyncio
async def test_one_adapter_can_produce_one_document() -> None:
    adapter: KnowledgeSourceAdapter = _SingleDocumentFakeAdapter()
    documents = [doc async for doc in adapter.fetch_documents()]
    assert len(documents) == 1
    assert documents[0].title == "Only Document"


@pytest.mark.asyncio
async def test_one_adapter_can_produce_multiple_documents() -> None:
    adapter: KnowledgeSourceAdapter = _MultiDocumentFakeAdapter("source_a", 3)
    documents = [doc async for doc in adapter.fetch_documents()]
    assert len(documents) == 3
    assert [doc.title for doc in documents] == ["Document 0", "Document 1", "Document 2"]


@pytest.mark.asyncio
async def test_documents_from_different_arbitrary_source_systems_use_the_same_contract() -> None:
    adapter_a: KnowledgeSourceAdapter = _MultiDocumentFakeAdapter("enterprise_repo_x", 1)
    adapter_b: KnowledgeSourceAdapter = _MultiDocumentFakeAdapter("future_system_2030", 1)

    docs_a = [doc async for doc in adapter_a.fetch_documents()]
    docs_b = [doc async for doc in adapter_b.fetch_documents()]

    assert type(docs_a[0]) is type(docs_b[0]) is IngestedKnowledgeDocument
    assert docs_a[0].source.source_system == "enterprise_repo_x"
    assert docs_b[0].source.source_system == "future_system_2030"


@pytest.mark.asyncio
async def test_adapter_protocol_requires_no_agent_gemini_or_source_specific_production_code() -> None:
    """The fake adapters above are ordinary Python classes with a single
    async-generator method -- no ADK Agent, no Gemini call, no
    source-specific base class, no registration step.
    """
    adapter: KnowledgeSourceAdapter = _SingleDocumentFakeAdapter()
    assert hasattr(adapter, "fetch_documents")
    assert not hasattr(adapter, "tools")
    assert not hasattr(adapter, "model")
