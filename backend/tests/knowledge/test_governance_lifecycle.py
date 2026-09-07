"""Phase 5.1E: lifecycle transitions -- the one authoritative
CANDIDATE -> APPROVED -> ARCHIVE state machine, document-type
independence, purity, and the "no automatic predecessor archiving"
invariant.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.governance.contracts import InvalidLifecycleTransitionError
from backend.knowledge.governance.service import approve_version, archive_version, transition_lifecycle


def _knowledge_object(status: LifecycleStatus, document_type: KnowledgeDocumentType = KnowledgeDocumentType.MOP, label: str = "1.0") -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id="k1",
        document_type=document_type,
        title="Some document",
        version=KnowledgeVersion(label=label),
        lifecycle_status=status,
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
    )


# --- allowed transitions --------------------------------------------------


def test_candidate_to_approved_allowed() -> None:
    result = transition_lifecycle(_knowledge_object(LifecycleStatus.CANDIDATE), LifecycleStatus.APPROVED)
    assert result.lifecycle_status is LifecycleStatus.APPROVED


def test_approved_to_archive_allowed() -> None:
    result = transition_lifecycle(_knowledge_object(LifecycleStatus.APPROVED), LifecycleStatus.ARCHIVE)
    assert result.lifecycle_status is LifecycleStatus.ARCHIVE


def test_approve_version_convenience() -> None:
    result = approve_version(_knowledge_object(LifecycleStatus.CANDIDATE))
    assert result.lifecycle_status is LifecycleStatus.APPROVED


def test_archive_version_convenience() -> None:
    result = archive_version(_knowledge_object(LifecycleStatus.APPROVED))
    assert result.lifecycle_status is LifecycleStatus.ARCHIVE


# --- rejected transitions ---------------------------------------------------


@pytest.mark.parametrize(
    "start,target",
    [
        (LifecycleStatus.CANDIDATE, LifecycleStatus.ARCHIVE),
        (LifecycleStatus.APPROVED, LifecycleStatus.CANDIDATE),
        (LifecycleStatus.ARCHIVE, LifecycleStatus.APPROVED),
        (LifecycleStatus.ARCHIVE, LifecycleStatus.CANDIDATE),
        (LifecycleStatus.CANDIDATE, LifecycleStatus.CANDIDATE),
        (LifecycleStatus.APPROVED, LifecycleStatus.APPROVED),
        (LifecycleStatus.ARCHIVE, LifecycleStatus.ARCHIVE),
    ],
)
def test_invalid_transitions_rejected(start: LifecycleStatus, target: LifecycleStatus) -> None:
    with pytest.raises(InvalidLifecycleTransitionError):
        transition_lifecycle(_knowledge_object(start), target)


def test_no_other_lifecycle_states_exist() -> None:
    assert {member.value for member in LifecycleStatus} == {"candidate", "approved", "archive"}


# --- purity ------------------------------------------------------------


def test_original_object_is_not_mutated() -> None:
    original = _knowledge_object(LifecycleStatus.CANDIDATE)
    transition_lifecycle(original, LifecycleStatus.APPROVED)
    assert original.lifecycle_status is LifecycleStatus.CANDIDATE


def test_transition_returns_a_new_object() -> None:
    original = _knowledge_object(LifecycleStatus.CANDIDATE)
    result = transition_lifecycle(original, LifecycleStatus.APPROVED)
    assert result is not original


def test_only_lifecycle_status_and_updated_at_change() -> None:
    original = _knowledge_object(LifecycleStatus.CANDIDATE)
    transitioned_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = transition_lifecycle(original, LifecycleStatus.APPROVED, transitioned_at=transitioned_at)
    assert result.model_dump(exclude={"lifecycle_status", "updated_at"}) == original.model_dump(
        exclude={"lifecycle_status", "updated_at"}
    )


# --- timestamp / determinism -------------------------------------------


def test_transitioned_at_is_deterministic_explicit_input() -> None:
    original = _knowledge_object(LifecycleStatus.CANDIDATE)
    transitioned_at = datetime(2026, 3, 15, tzinfo=timezone.utc)
    result = transition_lifecycle(original, LifecycleStatus.APPROVED, transitioned_at=transitioned_at)
    assert result.updated_at == transitioned_at


def test_transitioned_at_defaults_to_none_not_the_wall_clock() -> None:
    original = _knowledge_object(LifecycleStatus.CANDIDATE)
    result = transition_lifecycle(original, LifecycleStatus.APPROVED)
    assert result.updated_at is None


def test_repeated_transition_with_same_inputs_is_identical() -> None:
    original = _knowledge_object(LifecycleStatus.CANDIDATE)
    transitioned_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    first = transition_lifecycle(original, LifecycleStatus.APPROVED, transitioned_at=transitioned_at)
    second = transition_lifecycle(original, LifecycleStatus.APPROVED, transitioned_at=transitioned_at)
    assert first == second


def test_no_wall_clock_call_anywhere_in_service_module() -> None:
    """Static proof: service.py never calls datetime.now/utcnow/time.time."""
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "knowledge" / "governance" / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    forbidden_calls = {("now",), ("utcnow",), ("time",)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if (node.func.attr,) in forbidden_calls:
                pytest.fail(f"service.py calls .{node.func.attr}() -- governance must never read the wall clock")


# --- document-type independence -------------------------------------------


@pytest.mark.parametrize(
    "document_type",
    [KnowledgeDocumentType.MOP, KnowledgeDocumentType.SOP, KnowledgeDocumentType.RCA, KnowledgeDocumentType.KB_ARTICLE, KnowledgeDocumentType.OTHER],
)
def test_lifecycle_identical_across_arbitrary_document_types(document_type: KnowledgeDocumentType) -> None:
    candidate = _knowledge_object(LifecycleStatus.CANDIDATE, document_type=document_type)
    approved = transition_lifecycle(candidate, LifecycleStatus.APPROVED)
    archived = transition_lifecycle(approved, LifecycleStatus.ARCHIVE)
    assert approved.lifecycle_status is LifecycleStatus.APPROVED
    assert archived.lifecycle_status is LifecycleStatus.ARCHIVE


def test_no_document_type_branch_in_service_module() -> None:
    """Static proof: no `if document_type ==`-shaped comparison exists
    in service.py -- lifecycle semantics never vary by document type.
    """
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "knowledge" / "governance" / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for operand in [node.left, *node.comparators]:
                if isinstance(operand, ast.Attribute) and operand.attr == "document_type":
                    pytest.fail("service.py branches on document_type -- lifecycle must stay document-type-independent")


# --- no automatic predecessor archiving ------------------------------------


def test_approving_a_candidate_does_not_mutate_or_archive_another_object() -> None:
    predecessor = _knowledge_object(LifecycleStatus.APPROVED, label="1.0")
    successor_candidate = _knowledge_object(LifecycleStatus.CANDIDATE, label="2.0")

    approved_successor = approve_version(successor_candidate)

    # The predecessor object is untouched -- approve_version/transition_lifecycle
    # take exactly one KnowledgeObject and return exactly one new one; there
    # is no code path through which a second object could be mutated.
    assert predecessor.lifecycle_status is LifecycleStatus.APPROVED
    assert approved_successor.lifecycle_status is LifecycleStatus.APPROVED
    assert predecessor is not approved_successor
