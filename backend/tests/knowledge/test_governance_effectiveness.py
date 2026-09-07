"""Phase 5.1E: deterministic effectiveness -- inclusive bounds, and
explicitly independent of LifecycleStatus.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.knowledge.domain.enums import LifecycleStatus
from backend.knowledge.domain.models import KnowledgeVersion
from backend.knowledge.governance.versioning import is_effective

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
_YESTERDAY = _NOW - timedelta(days=1)
_TOMORROW = _NOW + timedelta(days=1)


def test_no_bounds_is_always_effective() -> None:
    version = KnowledgeVersion(label="1.0")
    assert is_effective(version, _YESTERDAY) is True
    assert is_effective(version, _NOW) is True
    assert is_effective(version, _TOMORROW) is True


def test_only_effective_from_set() -> None:
    version = KnowledgeVersion(label="1.0", effective_from=_NOW)
    assert is_effective(version, _YESTERDAY) is False
    assert is_effective(version, _NOW) is True
    assert is_effective(version, _TOMORROW) is True


def test_only_effective_to_set() -> None:
    version = KnowledgeVersion(label="1.0", effective_to=_NOW)
    assert is_effective(version, _YESTERDAY) is True
    assert is_effective(version, _NOW) is True
    assert is_effective(version, _TOMORROW) is False


def test_both_bounds_set() -> None:
    version = KnowledgeVersion(label="1.0", effective_from=_YESTERDAY, effective_to=_TOMORROW)
    assert is_effective(version, _YESTERDAY - timedelta(days=1)) is False
    assert is_effective(version, _YESTERDAY) is True
    assert is_effective(version, _NOW) is True
    assert is_effective(version, _TOMORROW) is True
    assert is_effective(version, _TOMORROW + timedelta(days=1)) is False


def test_before_effective_from_is_not_effective() -> None:
    version = KnowledgeVersion(label="1.0", effective_from=_TOMORROW)
    assert is_effective(version, _NOW) is False


def test_exactly_effective_from_is_effective_inclusive() -> None:
    version = KnowledgeVersion(label="1.0", effective_from=_NOW)
    assert is_effective(version, _NOW) is True


def test_between_bounds_is_effective() -> None:
    version = KnowledgeVersion(label="1.0", effective_from=_YESTERDAY, effective_to=_TOMORROW)
    assert is_effective(version, _NOW) is True


def test_exactly_effective_to_is_effective_inclusive() -> None:
    version = KnowledgeVersion(label="1.0", effective_to=_NOW)
    assert is_effective(version, _NOW) is True


def test_after_effective_to_is_not_effective() -> None:
    version = KnowledgeVersion(label="1.0", effective_to=_YESTERDAY)
    assert is_effective(version, _NOW) is False


def test_effectiveness_is_independent_of_lifecycle_status() -> None:
    """is_effective takes only a KnowledgeVersion -- it structurally
    cannot see LifecycleStatus at all, so a CANDIDATE and an APPROVED
    object with the identical version are equally (in)effective. See
    resolve_current_version for where the two concepts are properly
    combined.
    """
    import inspect

    signature = inspect.signature(is_effective)
    assert list(signature.parameters) == ["version", "as_of"]
    assert "lifecycle_status" not in signature.parameters
    # sanity: LifecycleStatus itself is a real, distinct enum, unrelated
    # to this function's own parameters.
    assert LifecycleStatus.APPROVED.value == "approved"
