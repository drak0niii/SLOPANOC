"""Correction pass (post-5.1E): distinguishing "was formally Approved"
from "actually reached an effective authoritative period before being
Archived" for an ARCHIVE successor -- see versioning.py's `_has_activated`
and its module docstring's "PERMANENT-SUPERSESSION RULE" for the full
rationale. Covers exactly scenarios A-D from the correction instruction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.governance.contracts import CurrentVersionResolutionStatus
from backend.knowledge.governance.versioning import resolve_current_version

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_NEXT_WEEK = _NOW + timedelta(days=7)
_LAST_MONTH = _NOW - timedelta(days=30)
_LAST_WEEK = _NOW - timedelta(days=10)


def _version_object(
    label: str,
    status: LifecycleStatus,
    *,
    supersedes: list[str] | None = None,
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    updated_at: datetime | None = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.MOP,
        title="Some document",
        version=KnowledgeVersion(label=label, supersedes=supersedes or [], effective_from=effective_from, effective_to=effective_to),
        lifecycle_status=status,
        source=KnowledgeSource(source_system="source_a", source_id="doc-1"),
        updated_at=updated_at,
    )


# --- A: future Approved successor ------------------------------------------


def test_a_before_effective_from_predecessor_current() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED)
    v2 = _version_object("2.0", LifecycleStatus.APPROVED, supersedes=["1.0"], effective_from=_NEXT_WEEK)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "1.0"


def test_a_at_effective_from_successor_current() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED)
    v2 = _version_object("2.0", LifecycleStatus.APPROVED, supersedes=["1.0"], effective_from=_NEXT_WEEK)
    result = resolve_current_version([v1, v2], _NEXT_WEEK)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "2.0"


# --- B: successor archived AFTER it became effective -----------------------


def test_b_predecessor_permanently_excluded() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    # v2 became effective at LAST_WEEK, then was archived at NOW -- i.e.
    # archived_at (NOW) is on/after effective_from (LAST_WEEK): it did
    # genuinely activate before being archived.
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=_LAST_WEEK, updated_at=_NOW)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.NOT_FOUND


def test_b_archived_successor_itself_never_current() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=_LAST_WEEK, updated_at=_NOW)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.current is None


def test_b_not_found_when_no_replacement_exists() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=_LAST_WEEK, updated_at=_NOW)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.NOT_FOUND
    assert result.candidates == []


# --- C: successor archived BEFORE it ever became effective -----------------


def test_c_predecessor_current_before_abandoned_successors_former_effective_date() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    # v2 was archived at NOW, strictly before its own effective_from
    # (NEXT_WEEK) -- it never activated at all.
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=_NEXT_WEEK, updated_at=_NOW)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "1.0"


def test_c_predecessor_remains_current_after_the_abandoned_effective_date_passes() -> None:
    """The key correction-pass regression: once the calendar passes v2's
    former effective_from, v2 must NOT suddenly begin suppressing v1 --
    v2 never activated, and archiving is permanent (ARCHIVE cannot
    transition back to APPROVED), so it can never activate later either.
    """
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=_NEXT_WEEK, updated_at=_NOW)

    as_of_well_after_former_effective_date = _NEXT_WEEK + timedelta(days=365)
    result = resolve_current_version([v1, v2], as_of_well_after_former_effective_date)

    assert result.status is CurrentVersionResolutionStatus.RESOLVED
    assert result.current.version.label == "1.0"


def test_c_archived_exactly_at_its_own_effective_from_counts_as_activated() -> None:
    """Boundary check: `archived_at == effective_from` is NOT strictly
    before effective_from, so it does not qualify for the "never
    activated" carve-out -- it falls into the conservative/activated
    branch, consistent with is_effective's own inclusive lower bound.
    """
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=_NOW, updated_at=_NOW)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.NOT_FOUND


# --- D: insufficient timestamps -> fail conservatively ----------------------


def test_d_missing_updated_at_fails_conservatively_not_found() -> None:
    """No `transitioned_at` was ever supplied when v2 was archived, so
    `updated_at` is None -- this module cannot prove v2 never activated,
    so it must NOT resurrect v1. The conservative result is NOT_FOUND,
    never a silently-resurrected predecessor.
    """
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=_LAST_WEEK, updated_at=None)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.NOT_FOUND
    assert result.current is None


def test_d_missing_updated_at_never_resurrects_predecessor_even_with_unbounded_effective_from() -> None:
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=None, updated_at=None)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.NOT_FOUND


def test_d_documented_behavior_is_conservative_not_permissive() -> None:
    """Sanity check on the documented default itself: when
    `updated_at` is present but `effective_from` is unset (unbounded),
    this module also cannot rule out activation and must remain
    conservative (still NOT_FOUND, never resurrecting v1).
    """
    v1 = _version_object("1.0", LifecycleStatus.APPROVED, effective_from=_LAST_MONTH)
    v2 = _version_object("2.0", LifecycleStatus.ARCHIVE, supersedes=["1.0"], effective_from=None, updated_at=_NOW)
    result = resolve_current_version([v1, v2], _NOW)
    assert result.status is CurrentVersionResolutionStatus.NOT_FOUND


# --- generic / document-type independence -----------------------------------


def test_activation_rule_is_document_type_independent() -> None:
    for document_type in (KnowledgeDocumentType.MOP, KnowledgeDocumentType.RCA, KnowledgeDocumentType.OTHER):
        v1 = KnowledgeObject(
            knowledge_id="k1",
            document_type=document_type,
            title="t",
            version=KnowledgeVersion(label="1.0", effective_from=_LAST_MONTH),
            lifecycle_status=LifecycleStatus.APPROVED,
            source=KnowledgeSource(source_system="s", source_id="1"),
        )
        v2 = KnowledgeObject(
            knowledge_id="k1",
            document_type=document_type,
            title="t",
            version=KnowledgeVersion(label="2.0", supersedes=["1.0"], effective_from=_NEXT_WEEK),
            lifecycle_status=LifecycleStatus.ARCHIVE,
            source=KnowledgeSource(source_system="s", source_id="1"),
            updated_at=_NOW,
        )
        result = resolve_current_version([v1, v2], _NOW)
        assert result.status is CurrentVersionResolutionStatus.RESOLVED
        assert result.current.version.label == "1.0"
