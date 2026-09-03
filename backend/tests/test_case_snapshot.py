"""Tests for backend/cases/snapshot.py -- the deterministic, budgeted
`CaseContextSnapshot` builder (instruction section 41).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.cases.schemas import CaseContextItemDTO, CaseDTO, CaseStatus, ContextItemKind, SourceType
from backend.cases.snapshot import build_case_context_snapshot
from backend.config.settings import Settings

_NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)


def _case(**overrides) -> CaseDTO:
    defaults = dict(
        case_id="case-1",
        title="Packet loss",
        problem_statement="Users reporting packet loss.",
        external_reference=None,
        status=CaseStatus.INVESTIGATING,
        created_by_user_id="alice",
        created_at=_NOW,
        updated_at=_NOW,
    )
    defaults.update(overrides)
    return CaseDTO(**defaults)


def _item(item_id: str, kind: ContextItemKind, content: str, minutes_ago: int, **overrides) -> CaseContextItemDTO:
    defaults = dict(
        item_id=item_id,
        case_id="case-1",
        kind=kind,
        content=content,
        source_type=SourceType.USER,
        source_author="alice",
        created_at=_NOW - timedelta(minutes=minutes_ago),
    )
    defaults.update(overrides)
    return CaseContextItemDTO(**defaults)


def _settings(max_items: int = 12, max_characters: int = 4000) -> Settings:
    return Settings(
        env={
            "SLOPANOC_CASE_CONTEXT_MAX_ITEMS": str(max_items),
            "SLOPANOC_CASE_CONTEXT_MAX_CHARACTERS": str(max_characters),
        }
    )


def test_deterministic_snapshot_for_the_same_input() -> None:
    case = _case()
    items = [_item("1", ContextItemKind.OBSERVATION, "A", 10), _item("2", ContextItemKind.RISK, "B", 5)]

    first = build_case_context_snapshot(case, items, settings=_settings())
    second = build_case_context_snapshot(case, items, settings=_settings())

    assert first.model_dump() == second.model_dump()


def test_case_metadata_is_included() -> None:
    case = _case(external_reference="TICKET-123")
    snapshot = build_case_context_snapshot(case, [], settings=_settings())

    assert snapshot.case_id == case.case_id
    assert snapshot.title == case.title
    assert snapshot.status == case.status
    assert snapshot.problem_statement == case.problem_statement
    assert snapshot.external_reference == "TICKET-123"


def test_context_items_are_included() -> None:
    case = _case()
    items = [_item("1", ContextItemKind.OBSERVATION, "Loss observed", 10)]
    snapshot = build_case_context_snapshot(case, items, settings=_settings())

    assert len(snapshot.items) == 1
    assert snapshot.items[0].content == "Loss observed"
    assert snapshot.items[0].kind == ContextItemKind.OBSERVATION


def test_item_limit_is_enforced() -> None:
    case = _case()
    items = [_item(str(i), ContextItemKind.OBSERVATION, f"item {i}", minutes_ago=i) for i in range(20)]

    snapshot = build_case_context_snapshot(case, items, settings=_settings(max_items=5))

    assert snapshot.included_item_count == 5
    assert len(snapshot.items) == 5
    assert snapshot.total_item_count == 20
    assert snapshot.context_truncated is True


def test_character_budget_is_enforced() -> None:
    case = _case()
    items = [_item(str(i), ContextItemKind.OBSERVATION, "x" * 100, minutes_ago=i) for i in range(10)]

    snapshot = build_case_context_snapshot(case, items, settings=_settings(max_items=100, max_characters=250))

    total_chars = sum(len(i.content) for i in snapshot.items)
    assert total_chars <= 250
    assert snapshot.context_truncated is True


def test_context_truncated_is_false_when_everything_fits() -> None:
    case = _case()
    items = [_item("1", ContextItemKind.OBSERVATION, "short", 1)]

    snapshot = build_case_context_snapshot(case, items, settings=_settings())

    assert snapshot.context_truncated is False
    assert snapshot.included_item_count == snapshot.total_item_count == 1


def test_at_least_one_item_is_kept_even_if_it_alone_exceeds_the_character_budget() -> None:
    case = _case()
    items = [_item("1", ContextItemKind.DECISION, "x" * 500, 1)]

    snapshot = build_case_context_snapshot(case, items, settings=_settings(max_characters=100))

    assert snapshot.included_item_count == 1
    assert snapshot.context_truncated is False  # the one and only item was included


def test_provenance_labels_are_preserved() -> None:
    case = _case()
    items = [
        _item("1", ContextItemKind.HYPOTHESIS, "Maybe X", 1, source_type=SourceType.AGENT, source_author="team_manager")
    ]

    snapshot = build_case_context_snapshot(case, items, settings=_settings())

    assert snapshot.items[0].source_type == SourceType.AGENT
    assert snapshot.items[0].source_author == "team_manager"


def test_important_deterministic_categories_are_prioritized_over_recency() -> None:
    """A much older decision must still outrank a much newer, low-priority
    recommendation when the item budget is tight -- priority tier beats
    recency across tiers (recency only breaks ties WITHIN a tier).
    """
    case = _case()
    old_decision = _item("1", ContextItemKind.DECISION, "Old decision", minutes_ago=1000)
    new_recommendation = _item("2", ContextItemKind.RECOMMENDATION, "New recommendation", minutes_ago=1)

    snapshot = build_case_context_snapshot(
        case, [new_recommendation, old_decision], settings=_settings(max_items=1)
    )

    assert snapshot.included_item_count == 1
    assert snapshot.items[0].kind == ContextItemKind.DECISION


def test_recency_breaks_ties_within_the_same_priority_tier() -> None:
    case = _case()
    older_observation = _item("1", ContextItemKind.OBSERVATION, "Older", minutes_ago=100)
    newer_observation = _item("2", ContextItemKind.OBSERVATION, "Newer", minutes_ago=1)

    snapshot = build_case_context_snapshot(
        case, [older_observation, newer_observation], settings=_settings(max_items=1)
    )

    assert snapshot.items[0].content == "Newer"


def test_no_database_or_internal_fields_leak_into_the_snapshot() -> None:
    case = _case()
    items = [_item("1", ContextItemKind.EVIDENCE, "Something", 1, created_by_user_id="alice")]

    snapshot = build_case_context_snapshot(case, items, settings=_settings())

    dumped = snapshot.model_dump()
    item_dump = dumped["items"][0]
    assert "created_by_user_id" not in item_dump
    assert "item_id" not in item_dump
    assert "case_id" not in item_dump
    assert "supporting_item_ids" not in item_dump


def test_snapshot_never_includes_a_confidence_or_reasoning_field() -> None:
    from backend.cases.schemas import CaseContextSnapshotItem

    fields = set(CaseContextSnapshotItem.model_fields)
    for forbidden in ("confidence", "reasoning", "chain_of_thought"):
        assert forbidden not in fields


def test_snapshot_items_are_presented_chronologically() -> None:
    case = _case()
    items = [
        _item("1", ContextItemKind.DECISION, "First (decision, old)", minutes_ago=100),
        _item("2", ContextItemKind.OBSERVATION, "Second (observation, newer)", minutes_ago=50),
    ]

    snapshot = build_case_context_snapshot(case, items, settings=_settings())

    assert [i.content for i in snapshot.items] == ["First (decision, old)", "Second (observation, newer)"]


def test_settings_drive_the_limits_not_hardcoded_values() -> None:
    """Confirms the budget genuinely comes from `Settings`, not a
    module-level constant baked into snapshot.py.
    """
    case = _case()
    items = [_item(str(i), ContextItemKind.OBSERVATION, f"item {i}", minutes_ago=i) for i in range(3)]

    tight = build_case_context_snapshot(case, items, settings=_settings(max_items=1))
    loose = build_case_context_snapshot(case, items, settings=_settings(max_items=3))

    assert tight.included_item_count == 1
    assert loose.included_item_count == 3
