"""Shared test fixtures for APPROVED governed operation descriptors.

Deliberately goes through the REAL governance path
(`author_section_operation` -> `approve_version` ->
`approve_section_operation`) rather than constructing an `APPROVED`
descriptor directly -- there is no longer any way to do the latter, which
is the point of POST-6A PROMPT 3's own approval binding. A test that
wants an approved descriptor must therefore exercise the same seam
production does.
"""
from __future__ import annotations

from typing import Optional

from backend.knowledge.domain.enums import KnowledgeDocumentType
from backend.knowledge.domain.models import (
    KnowledgeObject,
    KnowledgeSection,
    KnowledgeSource,
    KnowledgeVersion,
)
from backend.knowledge.domain.operation_descriptor import GovernedOperationDescriptor
from backend.knowledge.governance.operation_approval import (
    OperationApprovalRecord,
    approve_section_operation,
    author_section_operation,
    descriptor_fingerprint,
)
from backend.knowledge.governance.service import approve_version


def build_knowledge_object(
    *,
    knowledge_id: str = "k1",
    version_label: str = "v1",
    section_id: str = "s1",
    content: str = "restart procedure",
) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.OPERATIONAL_PROCEDURE,
        title="Restart",
        version=KnowledgeVersion(label=version_label),
        lifecycle_status="candidate",
        source=KnowledgeSource(source_system="local", source_id="x"),
        sections=[
            KnowledgeSection(
                section_id=section_id, knowledge_id=knowledge_id, sequence=0, content=content
            )
        ],
    )


def approved_object_with_operation(
    descriptor: GovernedOperationDescriptor,
    *,
    knowledge_id: str = "k1",
    version_label: str = "v1",
    section_id: str = "s1",
    content: str = "restart procedure",
) -> KnowledgeObject:
    """A fully APPROVED `KnowledgeObject` whose section carries an
    APPROVED descriptor, produced through the real governance seam."""
    obj = build_knowledge_object(
        knowledge_id=knowledge_id, version_label=version_label, section_id=section_id, content=content
    )
    obj = author_section_operation(obj, section_id, descriptor)
    obj = approve_version(obj)
    authored = _section(obj, section_id).operation
    assert authored is not None
    return approve_section_operation(
        obj,
        OperationApprovalRecord(
            knowledge_id=knowledge_id,
            version_label=version_label,
            section_id=section_id,
            descriptor_fingerprint=descriptor_fingerprint(authored),
            approved_by="governance-test",
        ),
    )


def approved_descriptor(
    descriptor: GovernedOperationDescriptor,
    *,
    knowledge_id: str = "k1",
    version_label: str = "v1",
    section_id: str = "s1",
) -> GovernedOperationDescriptor:
    """Just the APPROVED descriptor, for tests that only need it."""
    obj = approved_object_with_operation(
        descriptor, knowledge_id=knowledge_id, version_label=version_label, section_id=section_id
    )
    approved = _section(obj, section_id).operation
    assert approved is not None
    return approved


def _section(obj: KnowledgeObject, section_id: str) -> KnowledgeSection:
    for section in obj.sections:
        if section.section_id == section_id:
            return section
    raise AssertionError(f"no section {section_id!r}")


def candidate_descriptor(descriptor: GovernedOperationDescriptor) -> Optional[GovernedOperationDescriptor]:
    """The authored (CANDIDATE) form -- what ingestion produces."""
    obj = author_section_operation(build_knowledge_object(), "s1", descriptor)
    return _section(obj, "s1").operation
