"""Real persistence tests for TELCO Context data against a genuine
temporary SQLite database file -- mirrors
`backend/tests/test_case_persistence.py`'s own rigor exactly (never
faked): round-trip through service recreation, profile identity
uniqueness per owner, append-only assertion history, and deterministic
state recomputation surviving a reload.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin, ContextProfileOwnerKind
from backend.context.domain.models import compute_context_state
from backend.context.sqlalchemy.db import ContextDatabase
from backend.context.sqlalchemy.service import TelcoContextService

SESSION_A = "session-alice-1"
CASE_A = "case-alice-1"


def _sqlite_url(tmp_path: Path) -> str:
    db_file = tmp_path / "slopanoc_test_context.db"
    return f"sqlite+aiosqlite:///{db_file.as_posix()}"


@pytest.mark.asyncio
async def test_profile_survives_service_recreation(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    db_a = ContextDatabase(url)
    service_a = TelcoContextService(db_a)
    profile = await service_a.get_or_create_profile(ContextProfileOwnerKind.SESSION, SESSION_A, created_by_user_id="alice")
    await db_a.close()

    db_b = ContextDatabase(url)
    service_b = TelcoContextService(db_b)
    try:
        reloaded = await service_b.get_profile(ContextProfileOwnerKind.SESSION, SESSION_A)
        assert reloaded is not None
        assert reloaded.profile_id == profile.profile_id
        assert reloaded.created_by_user_id == "alice"
    finally:
        await db_b.close()


@pytest.mark.asyncio
async def test_get_or_create_profile_is_idempotent_per_owner(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    db = ContextDatabase(url)
    service = TelcoContextService(db)
    try:
        first = await service.get_or_create_profile(ContextProfileOwnerKind.SESSION, SESSION_A)
        second = await service.get_or_create_profile(ContextProfileOwnerKind.SESSION, SESSION_A)
        assert first.profile_id == second.profile_id
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_session_owned_and_case_owned_profiles_are_independent(tmp_path: Path) -> None:
    """A session-scoped profile and a Case-scoped profile with the same
    string id value never collide -- ownership is `(owner_kind,
    owner_id)`, not `owner_id` alone.
    """
    url = _sqlite_url(tmp_path)
    db = ContextDatabase(url)
    service = TelcoContextService(db)
    try:
        session_profile = await service.get_or_create_profile(ContextProfileOwnerKind.SESSION, "shared-id")
        case_profile = await service.get_or_create_profile(ContextProfileOwnerKind.CASE, "shared-id")
        assert session_profile.profile_id != case_profile.profile_id
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_assertion_history_and_computed_state_survive_service_recreation(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)

    db_a = ContextDatabase(url)
    service_a = TelcoContextService(db_a)
    profile = await service_a.get_or_create_profile(ContextProfileOwnerKind.CASE, CASE_A)
    await service_a.record_assertion(
        profile.profile_id,
        ContextDimension.VENDOR,
        AssertionKind.VALUE,
        ContextOrigin.USER,
        raw_value="Ericsson",
        canonical_value="ERICSSON",
        source_reference="case-item-1",
    )
    await service_a.record_assertion(
        profile.profile_id,
        ContextDimension.ALARM,
        AssertionKind.VALUE,
        ContextOrigin.TEAMS,
        raw_value="VSWR",
        canonical_value="VSWR",
        source_reference="msg-42",
    )
    await db_a.close()

    db_b = ContextDatabase(url)
    service_b = TelcoContextService(db_b)
    try:
        assertions = await service_b.get_assertions(profile.profile_id)
        assert len(assertions) == 2
        assert {a.source_reference for a in assertions} == {"case-item-1", "msg-42"}

        state = await service_b.get_context_state(profile.profile_id)
        assert state[ContextDimension.VENDOR].accepted[0].canonical_value == "ERICSSON"
        assert state[ContextDimension.ALARM].accepted[0].canonical_value == "VSWR"

        # Re-deriving state from the raw assertions directly (bypassing
        # the service) produces the identical result -- proving state is
        # genuinely computed, not a separately-stored, driftable column.
        recomputed = compute_context_state(assertions)
        assert recomputed[ContextDimension.VENDOR].state == state[ContextDimension.VENDOR].state
    finally:
        await db_b.close()


@pytest.mark.asyncio
async def test_recording_assertion_for_unknown_profile_fails_closed(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path)
    db = ContextDatabase(url)
    service = TelcoContextService(db)
    try:
        with pytest.raises(LookupError):
            await service.record_assertion(
                "no-such-profile",
                ContextDimension.VENDOR,
                AssertionKind.VALUE,
                ContextOrigin.USER,
                raw_value="Ericsson",
                canonical_value="ERICSSON",
            )
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_conflicting_state_persists_and_reloads_correctly(tmp_path: Path) -> None:
    """A real conflict (two sources disagreeing on a SINGULAR dimension)
    must survive a reload as CONFLICTING, never silently resolved to
    whichever assertion happened to be written last.
    """
    url = _sqlite_url(tmp_path)
    db_a = ContextDatabase(url)
    service_a = TelcoContextService(db_a)
    profile = await service_a.get_or_create_profile(ContextProfileOwnerKind.SESSION, SESSION_A)
    await service_a.record_assertion(
        profile.profile_id,
        ContextDimension.VENDOR,
        AssertionKind.VALUE,
        ContextOrigin.TEAMS,
        raw_value="Ericsson",
        canonical_value="ERICSSON",
    )
    await service_a.record_assertion(
        profile.profile_id,
        ContextDimension.VENDOR,
        AssertionKind.VALUE,
        ContextOrigin.CASE,
        raw_value="Nokia",
        canonical_value="NOKIA",
    )
    await db_a.close()

    db_b = ContextDatabase(url)
    service_b = TelcoContextService(db_b)
    try:
        from backend.context.domain.enums import ContextState

        state = await service_b.get_context_state(profile.profile_id)
        assert state[ContextDimension.VENDOR].state == ContextState.CONFLICTING
        assert len(state[ContextDimension.VENDOR].conflicting) == 2
    finally:
        await db_b.close()
