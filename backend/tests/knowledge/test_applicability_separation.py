"""Phase 5.1B: applicability must stay a separate concern from lifecycle
authority, document versioning, retrieval/ranking, and any agent-specific
type -- instruction sections 24/25/44.
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.knowledge.domain.applicability import (
    ApplicabilityContext,
    ApplicabilityEvaluation,
    ApplicabilityOutcome,
    evaluate_applicability,
)
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeObject, KnowledgeSource, KnowledgeVersion


def _object_with(lifecycle_status: LifecycleStatus, version_label: str) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.MOP,
        title="Upgrade MOP",
        version=KnowledgeVersion(label=version_label),
        lifecycle_status=lifecycle_status,
        source=KnowledgeSource(source_system="sharepoint", source_id="doc-123"),
        applicability=Applicability(dimensions={"vendor": ["Ericsson"], "release": ["24.Q2"]}),
    )


def test_lifecycle_status_does_not_influence_applicability_outcome() -> None:
    context = ApplicabilityContext(dimensions={"vendor": ["Ericsson"], "release": ["24.Q2"]})

    candidate = evaluate_applicability(_object_with(LifecycleStatus.CANDIDATE, "4.2").applicability, context)
    approved = evaluate_applicability(_object_with(LifecycleStatus.APPROVED, "4.2").applicability, context)
    archive = evaluate_applicability(_object_with(LifecycleStatus.ARCHIVE, "4.2").applicability, context)

    assert candidate.outcome is ApplicabilityOutcome.MATCH
    assert approved.outcome is ApplicabilityOutcome.MATCH
    assert archive.outcome is ApplicabilityOutcome.MATCH
    assert candidate == approved == archive


def test_knowledge_version_label_does_not_influence_operational_release_matching() -> None:
    """A KnowledgeObject's document revision (KnowledgeVersion.label,
    e.g. "4.2") is a completely different concept from an
    Applicability.release dimension (e.g. "24.Q2", the target
    operational software release) -- changing one must never change the
    other's evaluation.
    """
    context = ApplicabilityContext(dimensions={"vendor": ["Ericsson"], "release": ["24.Q2"]})

    for version_label in ("1.0", "4.2", "Rev-A", "2026-08"):
        obj = _object_with(LifecycleStatus.APPROVED, version_label)
        result = evaluate_applicability(obj.applicability, context)
        assert result.outcome is ApplicabilityOutcome.MATCH, version_label


def test_evaluator_never_reads_knowledge_version_at_all() -> None:
    """`evaluate_applicability` takes only `Applicability` + `ApplicabilityContext`
    -- it has no parameter through which a `KnowledgeVersion` could even
    be passed, structurally guaranteeing this separation.
    """
    import inspect
    from typing import get_type_hints

    signature = inspect.signature(evaluate_applicability)
    assert list(signature.parameters) == ["applicability", "context"]

    hints = get_type_hints(evaluate_applicability)
    assert hints["applicability"] is Applicability
    assert hints["context"] is ApplicabilityContext


def test_no_retrieval_or_ranking_fields_exist_on_the_evaluation_result() -> None:
    """ApplicabilityEvaluation must never grow a ranking score,
    similarity score, or retrieval-engine-internal field.
    """
    assert set(ApplicabilityEvaluation.model_fields) == {"outcome", "dimension_results"}


def test_applicability_evaluation_requires_no_agent_specific_type() -> None:
    result = evaluate_applicability(
        Applicability(dimensions={"vendor": ["Ericsson"]}),
        ApplicabilityContext(dimensions={"vendor": ["Ericsson"]}),
    )
    # A plain, agent-agnostic pydantic model -- no ADK/Gemini/agent import
    # anywhere in its type or its module (see test_dependency_boundary.py).
    assert result.__class__.__module__ == "backend.knowledge.domain.applicability"


def test_created_at_and_other_object_level_fields_do_not_affect_applicability() -> None:
    obj = _object_with(LifecycleStatus.APPROVED, "1.0").model_copy(
        update={"created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}
    )
    result = evaluate_applicability(obj.applicability, ApplicabilityContext(dimensions={"vendor": ["Ericsson"], "release": ["24.Q2"]}))
    assert result.outcome is ApplicabilityOutcome.MATCH
