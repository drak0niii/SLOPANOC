"""Real persistence tests for Case data against a genuine temporary
SQLite database file (mirrors test_api_persistence.py's rigor -- never
faked). Also verifies Case/Session separation (instruction section 43):
a Case and its context ledger exist entirely independently of any ADK
session/conversation transcript.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.cases.db import CaseDatabase
from backend.cases.service import CaseService

ALICE = "alice"
BOB = "bob"


def _sqlite_url(tmp_path: Path) -> str:
    db_file = tmp_path / "slopanoc_test_cases.db"
    return f"sqlite+aiosqlite:///{db_file.as_posix()}"


@pytest.mark.asyncio
async def test_case_survives_service_recreation(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    db_a = CaseDatabase(url)
    service_a = CaseService(db_a)
    case = await service_a.create_case(ALICE, "Packet loss", "Users reporting packet loss.")
    await db_a.close()

    db_b = CaseDatabase(url)
    service_b = CaseService(db_b)
    try:
        reloaded = await service_b.get_case(ALICE, case.case_id)
        assert reloaded.case_id == case.case_id
        assert reloaded.title == "Packet loss"
    finally:
        await db_b.close()


@pytest.mark.asyncio
async def test_membership_survives_service_recreation(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    db_a = CaseDatabase(url)
    service_a = CaseService(db_a)
    case = await service_a.create_case(ALICE, "Title", "Problem")
    await service_a.add_member(ALICE, case.case_id, BOB)
    await db_a.close()

    db_b = CaseDatabase(url)
    service_b = CaseService(db_b)
    try:
        assert await service_b.is_member(BOB, case.case_id) is True
        bob_view = await service_b.get_case(BOB, case.case_id)
        assert bob_view.case_id == case.case_id
    finally:
        await db_b.close()


@pytest.mark.asyncio
async def test_session_link_survives_service_recreation(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    db_a = CaseDatabase(url)
    service_a = CaseService(db_a)
    case = await service_a.create_case(ALICE, "Title", "Problem")
    await service_a.link_session(ALICE, case.case_id, "session-xyz", session_owner_user_id=ALICE)
    await db_a.close()

    db_b = CaseDatabase(url)
    service_b = CaseService(db_b)
    try:
        link = await service_b.get_link_for_session("session-xyz")
        assert link is not None
        assert link.case_id == case.case_id
    finally:
        await db_b.close()


@pytest.mark.asyncio
async def test_full_context_ledger_survives_service_recreation(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    db_a = CaseDatabase(url)
    service_a = CaseService(db_a)
    case = await service_a.create_case(ALICE, "Title", "Problem")
    await service_a.add_user_context_item(ALICE, case.case_id, "observation", "Loss started 14:00 UTC.")
    await service_a.add_user_context_item(ALICE, case.case_id, "risk", "May affect SLA.")
    evidence = await service_a.add_user_context_item(ALICE, case.case_id, "evidence", "Log excerpt.")
    await service_a.record_case_analysis(
        ALICE, "team_manager", case.case_id, "hypothesis", "Config change suspected.",
        confidence=0.6, supporting_item_ids=[evidence.item_id],
    )
    await db_a.close()

    db_b = CaseDatabase(url)
    service_b = CaseService(db_b)
    try:
        items = await service_b.get_context_items(ALICE, case.case_id)
        assert len(items) == 4
        kinds = {i.kind.value for i in items}
        assert kinds == {"observation", "risk", "evidence", "hypothesis"}

        hypothesis = next(i for i in items if i.kind.value == "hypothesis")
        assert hypothesis.confidence == 0.6
        assert hypothesis.supporting_item_ids == [evidence.item_id]
        assert hypothesis.source_type.value == "agent"
        assert hypothesis.created_by_agent == "team_manager"
    finally:
        await db_b.close()


@pytest.mark.asyncio
async def test_case_status_update_survives_service_recreation(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    db_a = CaseDatabase(url)
    service_a = CaseService(db_a)
    case = await service_a.create_case(ALICE, "Title", "Problem")
    await service_a.update_case(ALICE, case.case_id, status="resolved")
    await db_a.close()

    db_b = CaseDatabase(url)
    service_b = CaseService(db_b)
    try:
        reloaded = await service_b.get_case(ALICE, case.case_id)
        assert reloaded.status.value == "resolved"
    finally:
        await db_b.close()


# --- Case / Session separation (instruction section 43) ---------------------


@pytest.mark.asyncio
async def test_case_context_exists_independently_of_any_adk_session() -> None:
    """This entire test never creates an ADK session at all -- the Case
    and its context ledger are fully usable through `backend.cases`
    alone, proving they are not implemented in terms of ADK session
    state.
    """
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    await service.add_user_context_item(ALICE, case.case_id, "observation", "Standalone context.")

    items = await service.get_context_items(ALICE, case.case_id)
    assert len(items) == 1


@pytest.mark.asyncio
async def test_one_case_may_have_multiple_sessions_with_separate_link_records() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    await service.add_member(ALICE, case.case_id, BOB)

    link_1 = await service.link_session(ALICE, case.case_id, "session-1", session_owner_user_id=ALICE)
    link_2 = await service.link_session(BOB, case.case_id, "session-2", session_owner_user_id=BOB)

    assert link_1.case_id == link_2.case_id == case.case_id
    assert link_1.session_id != link_2.session_id


@pytest.mark.asyncio
async def test_unlinking_a_session_does_not_delete_the_case_or_its_context() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    await service.add_user_context_item(ALICE, case.case_id, "observation", "Persistent context.")
    await service.link_session(ALICE, case.case_id, "session-1", session_owner_user_id=ALICE)

    await service.unlink_session(ALICE, case.case_id, "session-1")

    still_exists = await service.get_case(ALICE, case.case_id)
    assert still_exists.case_id == case.case_id
    items = await service.get_context_items(ALICE, case.case_id)
    assert len(items) == 1


@pytest.mark.asyncio
async def test_no_database_credentials_leak_through_case_dtos(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    db = CaseDatabase(url)
    service = CaseService(db)
    try:
        case = await service.create_case(ALICE, "Title", "Problem")
        dumped = case.model_dump_json()
        assert url not in dumped
        assert "sqlite" not in dumped.lower()
    finally:
        await db.close()
