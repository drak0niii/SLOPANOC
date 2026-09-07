"""Phase 5.1E: version governance -- opaque labels, explicit
supersession, current-version resolution (RESOLVED/NOT_FOUND/AMBIGUOUS),
future-effective/Candidate/archived successor behavior, invalid-family
rejection, supersession-chain resolution, and determinism.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.governance.contracts import (
    CurrentVersionResolutionStatus,
    DuplicateVersionLabelError,
    MixedKnowledgeIdError,
    SupersessionCycleError,
    UnknownSupersessionReferenceError,
)
from backend.knowledge.governance.versioning import resolve_current_version, resolve_supersession_chain

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NEXT_WEEK = _NOW + timedelta(days=7)
_LAST_MONTH = _NOW - timedelta(days=30)
_LAST_WEEK = _NOW - timedelta(days=10)


def _version_object(
    label: str,
    status: LifecycleStatus,
    *,
    knowledge_id: str = "k1",
    supersedes: list[str] | None = None,
    superseded_by: list[str] | None = None,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title="Some document",
        version=KnowledgeVersion(
            label=label, supersedes=supersedes or [], superseded_by=superseded_by or [], effective_from=effective_from, effective_to=effective_to
        ),
        lifecycle_status=status,
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
    )


# --- opaque version labels --------------------------------------------------


@pytest.mark.parametrize("labels", [("1.9", "1.10"), ("Rev-A", "Rev-Z"), ("2026-08", "alpha-build")])
def test_currentness_never_uses_label_sorting(labels: tuple[str, str]) -> None:
    """Neither of these pairs must resolve currentness by any lexical or
    numeric sort of the label -- with no explicit relationship between
    them, the correct result is AMBIGUOUS, never "whichever sorts higher".
    """
    a, b = labels
    family = [_version_object(a, LifecycleStatus.APPROVED), _version_object(b, LifecycleStatus.APPROVED)]
    result = resolve_current_version(family, _NOW)
    assert result.status is CurrentVersionResolutionStatus.AMBIGUOUS
    assert sorted(result.candidates) == sorted([a, b])


def test_no_max_shortcut_regression() -> None:
    """Regression: protects against a future `max(versions, key=label)`
    shortcut -- explicit supersession must be the only signal, even when
    the label with the "later" appearance is the OLDER version.
    """
    older = _version_object("Rev-Z", LifecycleStatus.APPROVED)
    newer = _version_object("Rev-A", LifecycleStatus.APPROVED, supersedes=["Rev-Z"])
    result = resolve_current_version([older, newer], _NOW)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "Rev-A"


def test_no_label_ordering_misleading_set() -> None:
    result = resolve_current_version(
        [_version_object("Rev-Z", LifecycleStatus.APPROVED), _version_object("Rev-A", LifecycleStatus.APPROVED, supersedes=["Rev-Z"])],
        _NOW,
    )
    assert result.current.version.label == "Rev-A"


# --- simple current version -------------------------------------------------


def test_single_approved_effective_not_superseded_is_resolved() -> None:
    result = resolve_current_version([_version_object("1.0", LifecycleStatus.APPROVED)], _NOW)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "1.0"


def test_single_candidate_is_not_found() -> None:
    result = resolve_current_version([_version_object("1.0", LifecycleStatus.CANDIDATE)], _NOW)
    assert result.status is CurrentVersionResolutionStatus.NOT_FOUND
    assert result.current is None


def test_single_archive_is_not_found() -> None:
    result = resolve_current_version([_version_object("1.0", LifecycleStatus.ARCHIVE)], _NOW)
    assert result.status is CurrentVersionResolutionStatus.NOT_FOUND


# --- supersession ------------------------------------------------------


def test_simple_supersession_via_supersedes() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED)
    v2 = _version_object("2.0", LifecycleStatus.APPROVED, supersedes=["1.0"])
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "2.0"


def test_simple_supersession_via_superseded_by() -> None:
    """The relationship declared through the inverse field (superseded_by
    on the OLDER version) must be recognized identically to supersedes
    declared on the newer version.
    """
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, superseded_by=["2.0"])
    v2 = _version_object("2.0", LifecycleStatus.APPROVED)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "2.0"


# --- future-effective successor ------------------------------------------


def test_future_effective_successor_today_predecessor_current() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED)
    v2 = _version_object("2.0", LifecycleStatus.APPROVED, supersedes=["1.0"], effective_from=_NEXT_WEEK)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "1.0"


def test_future_effective_successor_becomes_current_once_effective() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED)
    v2 = _version_object("2.0", LifecycleStatus.APPROVED, supersedes=["1.0"], effective_from=_NEXT_WEEK)
    result = resolve_current_version([v1, v2], _NEXT_WEEK)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "2.0"


# --- Candidate successor -------------------------------------------------


def test_candidate_successor_does_not_remove_predecessor_authority() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED)
    v2 = _version_object("2.0", LifecycleStatus.CANDIDATE, supersedes=["1.0"])
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "1.0"


# --- archived successor history ------------------------------------------


def test_archived_successor_does_not_resurrect_predecessor() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=_LAST_WEEK)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.NOT_FOUND
    assert result.current is None


def test_archived_successor_scenario_never_returns_the_archived_object_either() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=_LAST_WEEK)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.current is None  # never v2 either -- ARCHIVE is never current


# --- ambiguity -----------------------------------------------------------


def test_two_unrelated_approved_effective_versions_are_ambiguous() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED)
    v2 = _version_object("2.0", LifecycleStatus.APPROVED)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.AMBIGUOUS
    assert result.candidates == ["1.0", "2.0"]


def test_branching_supersession_is_ambiguous() -> None:
    a = _version_object("A", LifecycleStatus.APPROVED)
    b = _version_object("B", LifecycleStatus.APPROVED, supersedes=["A"])
    c = _version_object("C", LifecycleStatus.APPROVED, supersedes=["A"])
    result = resolve_current_version([a, b, c], _NOW)
    assert result.status is CurrentVersionResolutionStatus.AMBIGUOUS
    assert result.candidates == ["B", "C"]


def test_ambiguity_never_resolved_by_list_order() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED)
    v2 = _version_object("2.0", LifecycleStatus.APPROVED)
    forward = resolve_current_version([v1, v2], _NOW)
    backward = resolve_current_version([v2, v1], _NOW)
    assert forward.status is backward.status is CurrentVersionResolutionStatus.AMBIGUOUS
    assert forward.candidates == backward.candidates


# --- invalid version family ------------------------------------------------


def test_mixed_knowledge_id_rejected() -> None:
    with pytest.raises(MixedKnowledgeIdError):
        resolve_current_version(
            [_version_object("1.0", LifecycleStatus.APPROVED, knowledge_id="k1"), _version_object("1.0", LifecycleStatus.APPROVED, knowledge_id="k2")],
            _NOW,
        )


def test_duplicate_version_labels_rejected() -> None:
    with pytest.raises(DuplicateVersionLabelError):
        resolve_current_version(
            [_version_object("1.0", LifecycleStatus.APPROVED), _version_object("1.0", LifecycleStatus.CANDIDATE)], _NOW
        )


def test_unknown_supersession_reference_rejected() -> None:
    with pytest.raises(UnknownSupersessionReferenceError):
        resolve_current_version([_version_object("2.0", LifecycleStatus.APPROVED, supersedes=["9.9"])], _NOW)


def test_unknown_superseded_by_reference_rejected() -> None:
    with pytest.raises(UnknownSupersessionReferenceError):
        resolve_current_version([_version_object("1.0", LifecycleStatus.APPROVED, superseded_by=["9.9"])], _NOW)


def test_direct_cycle_rejected() -> None:
    a = _version_object("A", LifecycleStatus.APPROVED, supersedes=["B"])
    b = _version_object("B", LifecycleStatus.APPROVED, supersedes=["A"])
    with pytest.raises(SupersessionCycleError):
        resolve_current_version([a, b], _NOW)


def test_multi_node_cycle_rejected() -> None:
    a = _version_object("A", LifecycleStatus.APPROVED, supersedes=["C"])
    b = _version_object("B", LifecycleStatus.APPROVED, supersedes=["A"])
    c = _version_object("C", LifecycleStatus.APPROVED, supersedes=["B"])
    with pytest.raises(SupersessionCycleError):
        resolve_current_version([a, b, c], _NOW)


# --- supersession chain ------------------------------------------------------


def test_supersession_chain_is_deterministic_regardless_of_input_order() -> None:
    v1 = _version_object("1.0", LifecycleStatus.ARCHIVE)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"])
    v3 = _version_object("3.0", LifecycleStatus.APPROVED, supersedes=["2.0"])

    family = [v1, v2, v3]
    baseline = resolve_supersession_chain(family)
    assert baseline == ["1.0", "2.0", "3.0"]

    for _ in range(5):
        shuffled = list(family)
        random.shuffle(shuffled)
        assert resolve_supersession_chain(shuffled) == baseline


def test_supersession_chain_rejects_invalid_family() -> None:
    a = _version_object("A", LifecycleStatus.APPROVED, supersedes=["B"])
    b = _version_object("B", LifecycleStatus.APPROVED, supersedes=["A"])
    with pytest.raises(SupersessionCycleError):
        resolve_supersession_chain([a, b])


def test_supersession_chain_empty_family() -> None:
    assert resolve_supersession_chain([]) == []


# --- determinism ---------------------------------------------------------


def test_resolve_current_version_is_deterministic_across_repeated_calls() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED)
    v2 = _version_object("2.0", LifecycleStatus.APPROVED, supersedes=["1.0"])
    first = resolve_current_version([v1, v2], _NOW)
    second = resolve_current_version([v1, v2], _NOW)
    assert first == second


def test_resolve_current_version_does_not_call_the_wall_clock() -> None:
    """Static proof, complementing determinism: versioning.py never
    calls datetime.now/utcnow/time.time -- `as_of` is always an explicit
    caller-supplied instant.
    """
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "knowledge" / "governance" / "versioning.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("now", "utcnow", "time"):
                pytest.fail(f"versioning.py calls .{node.func.attr}() -- governance must never read the wall clock")
