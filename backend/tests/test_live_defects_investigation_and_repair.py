"""Targeted regression tests for:
1. Defect 1: SQLAlchemy asyncpg PostgreSQL connection pooling and bounded single-retry on transient InterfaceError/DBAPIError.
2. Defect 2: Troubleshooting confirmation turn ('yes') preserving target MO binding and delegating to technical_authority_engineer without command execution authorization.
3. Defect 3: Durable per-turn presentation state persistence and rehydration.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import DBAPIError, InterfaceError

from backend.agents.team_manager.case_context import TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM
from backend.api.turn_presentation import (
    TURN_PRESENTATION_STATE_KEY,
    build_turn_presentation_delta,
    resolve_turn_presentation,
    update_turn_presentation_selection,
)
from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository, _engine_kwargs


# --- Defect 1: PostgreSQL connection handling and bounded retry ---


def test_postgres_engine_kwargs_pooling():
    kwargs = _engine_kwargs("postgresql+asyncpg://user:pass@localhost:5432/slopanoc")
    assert kwargs.get("pool_pre_ping") is True
    assert kwargs.get("pool_recycle") == 300

    sqlite_kwargs = _engine_kwargs("sqlite+aiosqlite:///:memory:")
    assert "pool_pre_ping" not in sqlite_kwargs


@pytest.mark.asyncio
async def test_knowledge_repo_bounded_retry_on_interface_error():
    repo = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.ensure_schema()

    call_count = 0
    original_session_factory = repo._session_factory

    class FlakySessionContext:
        async def __aenter__(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise InterfaceError("connection is closed", params=None, orig=Exception("closed"))
            real_ctx = original_session_factory()
            return await real_ctx.__aenter__()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    repo._session_factory = lambda: FlakySessionContext()

    # get() should catch InterfaceError, retry on a fresh session, and succeed (returning None for non-existent record)
    result = await repo.get("k-test", "v1")
    assert result is None
    assert call_count == 2


@pytest.mark.asyncio
async def test_knowledge_repo_exhaustion_on_repeated_error():
    repo = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.ensure_schema()

    class AlwaysFailingSessionContext:
        async def __aenter__(self):
            raise InterfaceError("connection is closed", params=None, orig=Exception("closed"))

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    repo._session_factory = lambda: AlwaysFailingSessionContext()

    with pytest.raises(InterfaceError):
        await repo.get("k-test", "v1")


# --- Defect 2: Troubleshooting confirmation safety & MO binding ---


def test_troubleshooting_delegation_prompt_safeguards():
    """Verify TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM includes:
    - Target confirmation handling (resolving target MO binding: Equipment=1,AuxPlugInUnit=RRU-2).
    - Delegation directly to technical_authority_engineer.
    - Safety boundary: Target confirmation confirms unit identity ONLY; never authorizes command execution, resets, or restarts.
    """
    assert "When the user confirms a target radio unit" in TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM
    assert "Equipment=1,AuxPlugInUnit=RRU-2" in TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM
    assert "technical_authority_engineer" in TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM
    assert "Target confirmation confirms unit identity ONLY" in TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM
    assert "does NOT authorize command execution, resets, or restarts" in TECHNICAL_AUTHORITY_DELEGATION_ADDENDUM


# --- Defect 3: Durable turn presentation state ---


def test_turn_presentation_lifecycle():
    turn_id = "inv-test-turn-1"
    initial_data = {
        "run_trace": {
            "server_run_id": "run-123",
            "steps": [{"step_id": "s1", "category": "search", "label": "Searching", "status": "completed"}],
            "final_duration_seconds": 3.45,
            "outcome": "ok",
        },
        "selection": {
            "selection_id": "sel-1",
            "pending_selection": {"selection_id": "sel-1", "options": []},
            "phase": "pending",
        },
    }

    state = {}
    delta = build_turn_presentation_delta(state.get(TURN_PRESENTATION_STATE_KEY), turn_id, initial_data)
    state[TURN_PRESENTATION_STATE_KEY] = delta

    resolved = resolve_turn_presentation(state, turn_id)
    assert resolved is not None
    assert resolved["run_trace"]["final_duration_seconds"] == 3.45
    assert resolved["selection"]["phase"] == "pending"

    # Resolve selection
    updated_pres = update_turn_presentation_selection(
        state[TURN_PRESENTATION_STATE_KEY],
        "sel-1",
        phase="resolved",
        selected_label="Option A",
    )
    state[TURN_PRESENTATION_STATE_KEY] = updated_pres

    resolved_after = resolve_turn_presentation(state, turn_id)
    assert resolved_after["selection"]["phase"] == "resolved"
    assert resolved_after["selection"]["selected_label"] == "Option A"


@pytest.mark.asyncio
async def test_knowledge_repo_bounded_retry_list_all_and_list_versions():
    repo = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.ensure_schema()

    original_session_factory = repo._session_factory

    # Test list_all retry
    call_count = 0

    class FlakySessionContextListAll:
        async def __aenter__(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise InterfaceError("connection is closed", params=None, orig=Exception("closed"))
            real_ctx = original_session_factory()
            return await real_ctx.__aenter__()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    repo._session_factory = lambda: FlakySessionContextListAll()
    results = await repo.list_all()
    assert results == []
    assert call_count == 2

    # Test list_versions retry
    call_count = 0

    class FlakySessionContextListVersions:
        async def __aenter__(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise InterfaceError("connection is closed", params=None, orig=Exception("closed"))
            real_ctx = original_session_factory()
            return await real_ctx.__aenter__()

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    repo._session_factory = lambda: FlakySessionContextListVersions()
    versions = await repo.list_versions("k-test")
    assert versions == []
    assert call_count == 2


@pytest.mark.asyncio
async def test_knowledge_repo_non_transient_error_not_retried():
    repo = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    await repo.ensure_schema()

    call_count = 0

    class NonTransientErrorSessionContext:
        async def __aenter__(self):
            nonlocal call_count
            call_count += 1
            raise ValueError("invalid sql parameters or value")

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    repo._session_factory = lambda: NonTransientErrorSessionContext()
    with pytest.raises(ValueError, match="invalid sql parameters"):
        await repo.get("k-test", "v1")
    # Must NOT retry on non-DBAPI/InterfaceError
    assert call_count == 1
