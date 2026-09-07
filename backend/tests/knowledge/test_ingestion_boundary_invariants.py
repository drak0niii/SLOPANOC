"""Phase 5.1C: cross-cutting boundary invariants -- source-identity
preservation, the ingestion/governance boundary, no automatic
segmentation, and no automatic lifecycle elevation (instruction sections
15/23/24/37/38/39/40).
"""
from __future__ import annotations

import ast
from pathlib import Path

from backend.knowledge.domain.models import KnowledgeSource
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument

_INGESTION_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "ingestion"


# --- source identity preservation ------------------------------------


def test_source_identity_survives_construction_unchanged() -> None:
    source = KnowledgeSource(
        source_system="source_a", source_id="doc-42", source_uri="https://example.invalid/doc-42", display_name="Upgrade MOP"
    )
    doc = IngestedKnowledgeDocument(source=source, title="Upgrade MOP", content="Step 1.")

    assert doc.source.source_system == "source_a"
    assert doc.source.source_id == "doc-42"
    assert doc.source.source_uri == "https://example.invalid/doc-42"
    assert doc.source.display_name == "Upgrade MOP"


def test_source_identity_survives_json_round_trip() -> None:
    source = KnowledgeSource(
        source_system="enterprise_repo_x", source_id="7", source_uri="https://example.invalid/7", display_name="Rollback SOP"
    )
    original = IngestedKnowledgeDocument(source=source, title="Rollback SOP", content="Rollback steps.")

    restored = IngestedKnowledgeDocument.model_validate_json(original.model_dump_json())

    assert restored.source == original.source
    assert restored.source.source_system == "enterprise_repo_x"
    assert restored.source.source_id == "7"
    assert restored.source.source_uri == "https://example.invalid/7"
    assert restored.source.display_name == "Rollback SOP"


def test_source_identity_survives_plain_dict_round_trip() -> None:
    source = KnowledgeSource(source_system="source_a", source_id="1")
    original = IngestedKnowledgeDocument(source=source, title="t", content="c")

    restored = IngestedKnowledgeDocument.model_validate(original.model_dump())

    assert restored.source == original.source


# --- pre-governance boundary -------------------------------------------


def test_ingested_document_has_no_lifecycle_status_field() -> None:
    """Ingestion is not governance -- INGESTED != APPROVED. There is no
    LifecycleStatus field on this contract at all.
    """
    assert "lifecycle_status" not in IngestedKnowledgeDocument.model_fields
    assert "status" not in IngestedKnowledgeDocument.model_fields


def test_ingested_document_has_no_sections_field() -> None:
    """5.1D owns segmentation -- there is no sections field here."""
    assert "sections" not in IngestedKnowledgeDocument.model_fields


def test_ingested_document_has_no_applicability_evaluation_field() -> None:
    """5.1B's evaluation output (ApplicabilityEvaluation) is not part of
    this contract -- only the raw, unevaluated Applicability
    representation is transported.
    """
    assert "applicability_evaluation" not in IngestedKnowledgeDocument.model_fields


def test_ingested_document_has_no_knowledge_context_item_field() -> None:
    assert "knowledge_context_item" not in IngestedKnowledgeDocument.model_fields


def test_ingested_document_requires_no_agent_or_retrieval_fields() -> None:
    """No agent-specific field, and no retrieval/ranking score field,
    exists on the generic ingestion contract.
    """
    fields = set(IngestedKnowledgeDocument.model_fields)
    assert fields == {
        "source",
        "title",
        "content",
        "knowledge_id_hint",
        "document_type_hint",
        "version_hint",
        "metadata",
        "applicability",
        "media_type",
        "source_revision",
        "observed_at",
    }


def test_ingested_document_is_a_distinct_type_from_knowledge_object() -> None:
    from backend.knowledge.domain.models import KnowledgeObject

    assert IngestedKnowledgeDocument is not KnowledgeObject
    assert not issubclass(IngestedKnowledgeDocument, KnowledgeObject)
    assert not issubclass(KnowledgeObject, IngestedKnowledgeDocument)


# --- no automatic segmentation -------------------------------------------


def test_no_segmentation_methods_exist_on_the_ingestion_contract() -> None:
    doc = IngestedKnowledgeDocument(source=KnowledgeSource(source_system="source_a", source_id="1"), title="t", content="c")
    for forbidden_method in ("segment", "chunk", "create_sections", "to_sections"):
        assert not hasattr(doc, forbidden_method)


def test_no_segmentation_function_defined_anywhere_in_the_ingestion_package() -> None:
    """Static proof: scan every declared function/method name under
    backend/knowledge/ingestion/ for a segmentation-shaped name.
    """
    forbidden_name_fragments = ("segment", "chunk", "create_section", "to_section")
    violations: list[tuple[str, str]] = []
    for path in sorted(_INGESTION_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                lowered = node.name.lower()
                for fragment in forbidden_name_fragments:
                    if fragment in lowered:
                        violations.append((path.name, node.name))
    assert violations == [], f"a segmentation-shaped function exists in the ingestion boundary: {violations}"


# --- no automatic approval elevation ------------------------------------


def test_no_code_path_assigns_approved_status_anywhere_in_ingestion() -> None:
    """A document entering ingestion must not gain authority
    automatically. Checked structurally (via `ast`, real imports only --
    not a raw text scan, which would also flag this package's own
    docstrings explaining the invariant): `LifecycleStatus` is never
    imported anywhere in backend/knowledge/ingestion/, so there is no
    code path through which this package could even reference
    `LifecycleStatus.APPROVED`.
    """
    violations: list[tuple[str, str]] = []
    for path in sorted(_INGESTION_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "enums" in node.module:
                for alias in node.names:
                    if alias.name == "LifecycleStatus":
                        violations.append((path.name, alias.name))
            if isinstance(node, ast.Attribute) and node.attr == "APPROVED":
                violations.append((path.name, "LifecycleStatus.APPROVED reference"))
    assert violations == [], f"ingestion must not import/reference LifecycleStatus.APPROVED: {violations}"
