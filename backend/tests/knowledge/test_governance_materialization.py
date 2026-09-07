"""Phase 5.1E: governed materialization -- StructuredKnowledgeDocument ->
KnowledgeObject in CANDIDATE state, with explicit governance identity
that ingestion hints must never silently override.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeMetadata, KnowledgeSource, KnowledgeVersion
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.processing.processor import HeadingStructureProcessor
from backend.knowledge.governance.service import final_section_id, materialize_candidate

_GOVERNANCE_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "governance"
_processor = HeadingStructureProcessor()


def _structured(content: str = "# Heading\nBody text.", **ingested_overrides: object):
    fields: dict[str, object] = {
        "source": KnowledgeSource(source_system="source_a", source_id="doc-1"),
        "title": "Some document",
        "content": content,
    }
    fields.update(ingested_overrides)
    return _processor.process(IngestedKnowledgeDocument(**fields))


def _materialize(structured=None, **overrides: object):
    fields: dict[str, object] = {
        "structured_document": structured or _structured(),
        "knowledge_id": "k1",
        "document_type": KnowledgeDocumentType.MOP,
        "version": KnowledgeVersion(label="1.0"),
    }
    fields.update(overrides)
    return materialize_candidate(**fields)


# --- basic materialization -------------------------------------------------


def test_structured_document_can_be_materialized() -> None:
    ko = _materialize()
    assert ko.knowledge_id == "k1"


def test_final_knowledge_id_is_explicitly_supplied() -> None:
    ko = _materialize(knowledge_id="explicit-id-42")
    assert ko.knowledge_id == "explicit-id-42"


def test_final_document_type_is_explicitly_supplied() -> None:
    ko = _materialize(document_type=KnowledgeDocumentType.RCA)
    assert ko.document_type is KnowledgeDocumentType.RCA


def test_final_version_is_explicitly_supplied() -> None:
    version = KnowledgeVersion(label="Rev-A")
    ko = _materialize(version=version)
    assert ko.version == version


def test_initial_lifecycle_is_always_candidate() -> None:
    ko = _materialize()
    assert ko.lifecycle_status is LifecycleStatus.CANDIDATE


def test_source_preserved() -> None:
    source = KnowledgeSource(source_system="source_a", source_id="doc-1", display_name="Doc")
    ko = _materialize(structured=_structured(source=source))
    assert ko.source == source


def test_title_preserved() -> None:
    ko = _materialize(structured=_structured(title="Rollback SOP"))
    assert ko.title == "Rollback SOP"


def test_metadata_preserved() -> None:
    metadata = KnowledgeMetadata(owner="RAN Engineering", tags=["upgrade"])
    ko = _materialize(structured=_structured(metadata=metadata))
    assert ko.metadata == metadata


def test_applicability_preserved() -> None:
    applicability = Applicability(dimensions={"vendor": ["Ericsson"]})
    ko = _materialize(structured=_structured(applicability=applicability))
    assert ko.applicability == applicability


def test_section_order_heading_content_locator_preserved() -> None:
    structured = _structured("# First\nBody A\n# Second\nBody B")
    ko = _materialize(structured=structured)
    assert [s.heading for s in ko.sections] == ["First", "Second"]
    assert [s.content for s in ko.sections] == ["Body A", "Body B"]
    assert [s.source_locator for s in ko.sections] == [s.source_locator for s in structured.sections]
    assert [s.sequence for s in ko.sections] == [0, 1]


def test_section_type_not_fabricated() -> None:
    ko = _materialize()
    assert all(section.section_type is None for section in ko.sections)


def test_deterministic_final_section_ids_assigned() -> None:
    structured = _structured("# First\nBody A\n# Second\nBody B")
    ko = _materialize(structured=structured)
    ids = [s.section_id for s in ko.sections]
    assert ids == [
        final_section_id("k1", "1.0", "section-0000"),
        final_section_id("k1", "1.0", "section-0001"),
    ]
    assert len(set(ids)) == len(ids)


def test_repeated_materialization_gives_identical_section_ids() -> None:
    structured = _structured("# First\nBody A\n# Second\nBody B")
    first = _materialize(structured=structured)
    second = _materialize(structured=structured)
    assert [s.section_id for s in first.sections] == [s.section_id for s in second.sections]


def test_different_version_produces_distinct_section_ids_for_same_local_keys() -> None:
    structured = _structured("# Heading\nBody")
    v1 = _materialize(structured=structured, version=KnowledgeVersion(label="1.0"))
    v2 = _materialize(structured=structured, version=KnowledgeVersion(label="2.0"))
    assert v1.sections[0].section_id != v2.sections[0].section_id
    assert v1.sections[0].section_id == final_section_id("k1", "1.0", "section-0000")
    assert v2.sections[0].section_id == final_section_id("k1", "2.0", "section-0000")


# --- collision safety (correction pass) -------------------------------------


def test_final_section_id_is_collision_safe_across_ambiguous_delimiter_split() -> None:
    """The exact collision example from the correction instruction: a
    naive "::".join(...) scheme would make these two DIFFERENT tuples
    serialize to the identical raw string "a::b::c::d". The
    length-prefixed encoding must keep them distinct.
    """
    id_a = final_section_id("a::b", "c", "d")
    id_b = final_section_id("a", "b::c", "d")
    assert id_a != id_b


def test_final_section_id_collision_safety_via_materialization() -> None:
    """Same collision proven end-to-end through materialize_candidate,
    not just the bare final_section_id helper.
    """
    structured = _structured("# Heading\nBody")
    ko_a = _materialize(structured=structured, knowledge_id="a::b", version=KnowledgeVersion(label="c"))
    ko_b = _materialize(structured=structured, knowledge_id="a", version=KnowledgeVersion(label="b::c"))
    assert ko_a.sections[0].section_id != ko_b.sections[0].section_id


def test_final_section_id_is_still_deterministic() -> None:
    assert final_section_id("k1", "1.0", "section-0000") == final_section_id("k1", "1.0", "section-0000")


def test_governed_at_sets_created_and_updated_timestamps() -> None:
    governed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ko = _materialize(governed_at=governed_at)
    assert ko.created_at == governed_at
    assert ko.updated_at == governed_at


def test_governed_at_defaults_to_none() -> None:
    ko = _materialize()
    assert ko.created_at is None
    assert ko.updated_at is None


def test_missing_required_governance_identity_rejected() -> None:
    with pytest.raises(TypeError):
        materialize_candidate(_structured(), document_type=KnowledgeDocumentType.MOP, version=KnowledgeVersion(label="1.0"))  # type: ignore[call-arg]


def test_invalid_explicit_knowledge_id_rejected() -> None:
    with pytest.raises(ValidationError):
        _materialize(knowledge_id="")


# --- hints are not authority -----------------------------------------------


def test_missing_hints_do_not_prevent_materialization() -> None:
    structured = _structured(knowledge_id_hint=None, document_type_hint=None, version_hint=None)
    ko = _materialize(structured=structured)
    assert ko.knowledge_id == "k1"


def test_conflicting_knowledge_id_hint_does_not_override_explicit_id() -> None:
    structured = _structured(knowledge_id_hint="source-native-99")
    ko = _materialize(structured=structured, knowledge_id="governed-k1")
    assert ko.knowledge_id == "governed-k1"
    assert ko.knowledge_id != "source-native-99"


def test_conflicting_document_type_hint_does_not_override_explicit_type() -> None:
    structured = _structured(document_type_hint=KnowledgeDocumentType.RCA)
    ko = _materialize(structured=structured, document_type=KnowledgeDocumentType.MOP)
    assert ko.document_type is KnowledgeDocumentType.MOP


def test_conflicting_version_hint_does_not_override_explicit_version() -> None:
    structured = _structured(version_hint=KnowledgeVersion(label="hint-version"))
    ko = _materialize(structured=structured, version=KnowledgeVersion(label="governed-version"))
    assert ko.version.label == "governed-version"


def test_original_hints_remain_preserved_in_structured_document_history() -> None:
    """The hint is not lost -- it simply never becomes authoritative."""
    structured = _structured(knowledge_id_hint="source-native-99", document_type_hint=KnowledgeDocumentType.RCA)
    _materialize(structured=structured, knowledge_id="governed-k1", document_type=KnowledgeDocumentType.MOP)
    assert structured.source_document.knowledge_id_hint == "source-native-99"
    assert structured.source_document.document_type_hint is KnowledgeDocumentType.RCA


def test_materialize_candidate_never_reads_ingestion_hints_at_all() -> None:
    """Static proof: materialize_candidate's own implementation never
    references knowledge_id_hint/document_type_hint/version_hint.
    """
    path = _GOVERNANCE_DIR / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_attrs = {"knowledge_id_hint", "document_type_hint", "version_hint"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
            pytest.fail(f"service.py references {node.attr} -- governance must never read ingestion hints")
