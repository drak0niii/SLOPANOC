"""A5 Layer I: focused tests proving artifact/lineage/section_roles wire
correctly through governed materialization
(backend/knowledge/governance/service.py's `materialize_candidate`) and
provenance revalidation
(backend/knowledge/provenance/service.py's `build_evidence_set`) --
reusing the existing governance/provenance architecture, never a
parallel one.
"""
from __future__ import annotations

import pytest

from backend.knowledge.domain.applicability import ApplicabilityOutcome
from backend.knowledge.domain.artifacts import KnowledgeArtifact
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSource, KnowledgeVersion
from backend.knowledge.governance.service import materialize_candidate
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.processing.compound import process_compound_document
from backend.knowledge.provenance.contracts import KnowledgeEvidenceNotFoundError
from backend.knowledge.provenance.service import KnowledgeProvenanceService
from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalItem, KnowledgeRetrievalResult


def _compound_document() -> IngestedKnowledgeDocument:
    artifact = KnowledgeArtifact(artifact_id="a1", kind="xlsx_sheet", depth=0, extracted_text="Headers: Node | Status\nN1 | OK")
    return IngestedKnowledgeDocument(
        source=KnowledgeSource(source_system="local_file", source_id="doc-1"),
        title="Compound Doc",
        content="# Root Heading\nRoot body text.",
        artifacts=[artifact],
    )


def test_materialize_candidate_carries_artifacts_through() -> None:
    structured = process_compound_document(_compound_document())
    ko = materialize_candidate(structured, knowledge_id="k1", document_type=KnowledgeDocumentType.MOP, version=KnowledgeVersion(label="1.0"))
    assert len(ko.artifacts) == 1
    assert ko.artifacts[0].artifact_id == "a1"


def test_materialize_candidate_carries_section_artifact_id_through() -> None:
    structured = process_compound_document(_compound_document())
    ko = materialize_candidate(structured, knowledge_id="k1", document_type=KnowledgeDocumentType.MOP, version=KnowledgeVersion(label="1.0"))
    artifact_sections = [s for s in ko.sections if s.artifact_id == "a1"]
    root_sections = [s for s in ko.sections if s.artifact_id is None]
    assert artifact_sections and root_sections


def test_materialize_candidate_never_infers_section_type_without_explicit_roles() -> None:
    structured = process_compound_document(_compound_document())
    ko = materialize_candidate(structured, knowledge_id="k1", document_type=KnowledgeDocumentType.MOP, version=KnowledgeVersion(label="1.0"))
    assert all(section.section_type is None for section in ko.sections)


def test_materialize_candidate_applies_explicit_trusted_section_roles() -> None:
    structured = process_compound_document(_compound_document())
    # section_key "section-0000" is the root document's own first (and
    # only) section, per HeadingStructureProcessor's deterministic keying.
    ko = materialize_candidate(
        structured,
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.MOP,
        version=KnowledgeVersion(label="1.0"),
        section_roles={"section-0000": "PROCEDURE"},
    )
    root_section = next(s for s in ko.sections if s.artifact_id is None)
    assert root_section.section_type == "PROCEDURE"


@pytest.mark.asyncio
async def test_build_evidence_set_resolves_matching_artifact() -> None:
    structured = process_compound_document(_compound_document())
    ko = materialize_candidate(structured, knowledge_id="k1", document_type=KnowledgeDocumentType.MOP, version=KnowledgeVersion(label="1.0"))
    ko = ko.model_copy(update={"lifecycle_status": LifecycleStatus.APPROVED})

    repo = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(ko)
    provenance = KnowledgeProvenanceService(repo)

    artifact_section = next(s for s in ko.sections if s.artifact_id == "a1")
    retrieval_item = KnowledgeRetrievalItem(
        knowledge_id="k1",
        version_label="1.0",
        document_type=KnowledgeDocumentType.MOP,
        title="Compound Doc",
        lifecycle_status=LifecycleStatus.APPROVED,
        source=ko.source,
        section=artifact_section,
        relevance_score=1.0,
        applicability_outcome=ApplicabilityOutcome.MATCH,
    )
    evidence_set = await provenance.build_evidence_set(KnowledgeRetrievalResult(items=[retrieval_item]))

    assert len(evidence_set.items) == 1
    item = evidence_set.items[0]
    assert item.artifact is not None
    assert item.artifact.artifact_id == "a1"
    assert item.reference.artifact_id == "a1"
    await repo.close()


@pytest.mark.asyncio
async def test_build_evidence_set_artifact_none_for_root_section() -> None:
    structured = process_compound_document(_compound_document())
    ko = materialize_candidate(structured, knowledge_id="k1", document_type=KnowledgeDocumentType.MOP, version=KnowledgeVersion(label="1.0"))
    ko = ko.model_copy(update={"lifecycle_status": LifecycleStatus.APPROVED})

    repo = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.add(ko)
    provenance = KnowledgeProvenanceService(repo)

    root_section = next(s for s in ko.sections if s.artifact_id is None)
    retrieval_item = KnowledgeRetrievalItem(
        knowledge_id="k1",
        version_label="1.0",
        document_type=KnowledgeDocumentType.MOP,
        title="Compound Doc",
        lifecycle_status=LifecycleStatus.APPROVED,
        source=ko.source,
        section=root_section,
        relevance_score=1.0,
        applicability_outcome=ApplicabilityOutcome.MATCH,
    )
    evidence_set = await provenance.build_evidence_set(KnowledgeRetrievalResult(items=[retrieval_item]))

    assert evidence_set.items[0].artifact is None
    assert evidence_set.items[0].reference.artifact_id is None
    await repo.close()
