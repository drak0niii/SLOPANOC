"""A5 final corrective pass, Correction C: focused tests for
`KnowledgeRetrievalService`'s native-source-vs-derived-interpretation
ranking tie-break (`is_derived`, `_relevance_bucket`,
`backend/knowledge/retrieval/service.py`). Mirrors
test_retrieval_ranking.py's own fixture conventions exactly.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.knowledge.domain.artifacts import KnowledgeArtifact
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery
from backend.knowledge.retrieval.service import KnowledgeRetrievalService

_AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)


class _FakeRepository:
    def __init__(self, objects: list[KnowledgeObject]) -> None:
        self._objects = objects

    async def add(self, knowledge_object: KnowledgeObject) -> None:
        raise AssertionError

    async def get(self, knowledge_id: str, version_label: str) -> KnowledgeObject | None:
        raise AssertionError

    async def replace(self, knowledge_object: KnowledgeObject) -> None:
        raise AssertionError

    async def list_versions(self, knowledge_id: str) -> list[KnowledgeObject]:
        raise AssertionError

    async def list_all(self) -> list[KnowledgeObject]:
        return list(self._objects)


async def _retrieve(objects: list[KnowledgeObject], query_text: str, limit: int = 10):
    service = KnowledgeRetrievalService(_FakeRepository(objects))
    return await service.retrieve(KnowledgeRetrievalQuery(query_text=query_text, as_of=_AS_OF, limit=limit))


def _object_with_native_and_derived_sections(
    *, native_content: str, derived_content: str, knowledge_id: str = "kn-1"
) -> KnowledgeObject:
    native_section = KnowledgeSection(section_id="s-native", knowledge_id=knowledge_id, sequence=0, content=native_content, artifact_id=None)
    derived_artifact = KnowledgeArtifact(artifact_id="art-derived", kind="image", depth=0, derived=True, extracted_text=derived_content)
    derived_section = KnowledgeSection(
        section_id="s-derived", knowledge_id=knowledge_id, sequence=1, content=derived_content, artifact_id="art-derived"
    )
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title="Doc",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id=knowledge_id),
        applicability=Applicability(dimensions={}),
        sections=[native_section, derived_section],
        artifacts=[derived_artifact],
    )


# --- 1. equivalent native XLSX-like text vs derived screenshot description -> native wins ---


@pytest.mark.asyncio
async def test_equivalent_relevance_native_outranks_derived() -> None:
    obj = _object_with_native_and_derived_sections(
        native_content="Headers: Alarm ID Site Name Technology Status",
        derived_content="The image displays a table with Alarm ID Site Name Technology columns",
    )
    result = await _retrieve([obj], "Alarm ID Site Name Technology")
    assert result.items[0].section.section_id == "s-native"
    assert result.items[0].is_derived is False


@pytest.mark.asyncio
async def test_equivalent_native_text_vs_derived_image_text_native_wins() -> None:
    obj = _object_with_native_and_derived_sections(
        native_content="Login procedure using Citrix Gateway with User name Password fields",
        derived_content="Citrix Gateway login page with User name Password fields shown",
    )
    result = await _retrieve([obj], "Citrix Gateway login User name Password")
    assert result.items[0].section.section_id == "s-native"


# --- 3. unique visual information -> derived image can still win -----------


@pytest.mark.asyncio
async def test_highly_relevant_derived_outranks_barely_relevant_native() -> None:
    obj = _object_with_native_and_derived_sections(
        native_content="General maintenance notes with no specific alarm terms at all",
        derived_content="dialog box titled Information approvers showing Name and Mail ID columns with a Close button",
    )
    result = await _retrieve([obj], "dialog box Information approvers Name Mail ID Close button")
    assert result.items[0].section.section_id == "s-derived"
    assert result.items[0].is_derived is True


# --- 4. unrelated native evidence never outranks highly relevant derived evidence merely by being native ---


@pytest.mark.asyncio
async def test_unrelated_native_does_not_outrank_relevant_derived_across_objects() -> None:
    derived_only = _object_with_native_and_derived_sections(
        knowledge_id="kn-derived",
        native_content="completely unrelated boilerplate text about scheduling",
        derived_content="alarm status output showing External Link Failure with timestamp and managed object",
    )
    result = await _retrieve([derived_only], "alarm status output External Link Failure timestamp managed object")
    assert result.items[0].section.section_id == "s-derived"


# --- 5. provenance remains correct after ranking ----------------------------


@pytest.mark.asyncio
async def test_is_derived_flag_matches_the_real_artifact_after_ranking() -> None:
    obj = _object_with_native_and_derived_sections(
        native_content="Alarm ID Site Name Technology",
        derived_content="Alarm ID Site Name Technology visible in screenshot",
    )
    result = await _retrieve([obj], "Alarm ID Site Name Technology")
    native_item = next(i for i in result.items if i.section.section_id == "s-native")
    derived_item = next(i for i in result.items if i.section.section_id == "s-derived")
    assert native_item.is_derived is False
    assert derived_item.is_derived is True


# --- root text (no artifact_id) is never derived ----------------------------


@pytest.mark.asyncio
async def test_root_text_section_is_never_derived() -> None:
    section = KnowledgeSection(section_id="s-root", knowledge_id="kn-root", sequence=0, content="plain root text about alarms")
    obj = KnowledgeObject(
        knowledge_id="kn-root",
        document_type=KnowledgeDocumentType.MOP,
        title="Doc",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="kn-root"),
        sections=[section],
    )
    result = await _retrieve([obj], "alarms")
    assert result.items[0].is_derived is False


# --- structural (non-derived) artifact extraction (e.g. native xlsx_sheet) is never derived ---


@pytest.mark.asyncio
async def test_structural_artifact_extraction_is_not_derived() -> None:
    artifact = KnowledgeArtifact(artifact_id="art-xlsx", kind="xlsx_sheet", depth=0, derived=False, extracted_text="Headers: A B C")
    section = KnowledgeSection(section_id="s-xlsx", knowledge_id="kn-1", sequence=0, content="Headers: A B C", artifact_id="art-xlsx")
    obj = KnowledgeObject(
        knowledge_id="kn-1",
        document_type=KnowledgeDocumentType.MOP,
        title="Doc",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="kn-1"),
        sections=[section],
        artifacts=[artifact],
    )
    result = await _retrieve([obj], "Headers A B C")
    assert result.items[0].is_derived is False
