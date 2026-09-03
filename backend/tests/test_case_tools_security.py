"""Security-contract and behavior tests for
backend/agents/team_manager/case_tools.py's `record_case_analysis` tool
-- the ONE Case-write capability reachable from the model.
"""
from __future__ import annotations

import inspect
from typing import Any, Optional

import pytest

from backend.agents.team_manager.agent import team_manager
from backend.agents.team_manager.case_tools import record_case_analysis
from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY
from backend.cases.service import CaseService

ALICE = "alice"


class _FakeToolContext:
    def __init__(self, user_id: str, state: Optional[dict[str, Any]] = None) -> None:
        self.user_id = user_id
        self.state = state if state is not None else {}


def test_record_case_analysis_is_registered_on_team_manager() -> None:
    names = [getattr(t, "name", None) or getattr(t, "__name__", str(t)) for t in team_manager.tools]
    assert "record_case_analysis" in names


def test_record_case_analysis_is_a_plain_function_not_a_direct_db_write() -> None:
    """Structural guarantee: the tool module never imports SQLAlchemy or
    the Case ORM models directly -- every write goes through
    `CaseService.record_case_analysis`'s validation.
    """
    import backend.agents.team_manager.case_tools as module

    source = inspect.getsource(module)
    assert "sqlalchemy" not in source.lower()
    assert "CaseContextItemRecord" not in source
    assert "CaseService" not in source or "get_case_service" in source  # only via the service, never raw ORM


@pytest.mark.asyncio
async def test_hypothesis_is_recorded_successfully() -> None:
    case_service = CaseService()
    case = await case_service.create_case(ALICE, "Title", "Problem")

    import backend.agents.team_manager.case_tools as module

    original = module.get_case_service
    module.get_case_service = lambda: case_service
    try:
        ctx = _FakeToolContext(ALICE, state={ACTIVE_CASE_ID_STATE_KEY: case.case_id})
        result = await record_case_analysis(kind="hypothesis", content="Maybe X.", tool_context=ctx)
    finally:
        module.get_case_service = original

    assert "error" not in result
    assert result["kind"] == "hypothesis"


@pytest.mark.asyncio
async def test_recommendation_is_recorded_successfully() -> None:
    case_service = CaseService()
    case = await case_service.create_case(ALICE, "Title", "Problem")

    import backend.agents.team_manager.case_tools as module

    original = module.get_case_service
    module.get_case_service = lambda: case_service
    try:
        ctx = _FakeToolContext(ALICE, state={ACTIVE_CASE_ID_STATE_KEY: case.case_id})
        result = await record_case_analysis(kind="recommendation", content="Check counters.", tool_context=ctx)
    finally:
        module.get_case_service = original

    assert "error" not in result
    assert result["kind"] == "recommendation"


@pytest.mark.asyncio
@pytest.mark.parametrize("forbidden_kind", ["evidence", "observation", "decision", "action", "resolution"])
async def test_disallowed_kinds_are_rejected(forbidden_kind: str) -> None:
    case_service = CaseService()
    case = await case_service.create_case(ALICE, "Title", "Problem")

    import backend.agents.team_manager.case_tools as module

    original = module.get_case_service
    module.get_case_service = lambda: case_service
    try:
        ctx = _FakeToolContext(ALICE, state={ACTIVE_CASE_ID_STATE_KEY: case.case_id})
        result = await record_case_analysis(kind=forbidden_kind, content="x", tool_context=ctx)
    finally:
        module.get_case_service = original

    assert "error" in result
    assert result["error"]["errorCode"] == "validation_error"


@pytest.mark.asyncio
async def test_no_active_case_is_a_safe_denial_not_a_crash() -> None:
    ctx = _FakeToolContext(ALICE, state={})
    result = await record_case_analysis(kind="hypothesis", content="x", tool_context=ctx)
    assert "error" in result
    assert result["error"]["errorCode"] == "not_found"


@pytest.mark.asyncio
async def test_missing_tool_context_is_a_safe_denial() -> None:
    result = await record_case_analysis(kind="hypothesis", content="x", tool_context=None)
    assert "error" in result


@pytest.mark.asyncio
async def test_fabricated_supporting_id_is_rejected_through_the_tool() -> None:
    case_service = CaseService()
    case = await case_service.create_case(ALICE, "Title", "Problem")

    import backend.agents.team_manager.case_tools as module

    original = module.get_case_service
    module.get_case_service = lambda: case_service
    try:
        ctx = _FakeToolContext(ALICE, state={ACTIVE_CASE_ID_STATE_KEY: case.case_id})
        result = await record_case_analysis(
            kind="hypothesis", content="x", supporting_case_item_ids=["fabricated"], tool_context=ctx
        )
    finally:
        module.get_case_service = original

    assert "error" in result
    assert result["error"]["errorCode"] == "validation_error"


def test_tool_signature_has_no_chain_of_thought_or_source_type_field() -> None:
    """No parameter through which the model could supply hidden reasoning
    or claim a different provenance than "agent".
    """
    params = set(inspect.signature(record_case_analysis).parameters)
    for forbidden in ("reasoning", "chain_of_thought", "source_type", "source_author", "agent_name"):
        assert forbidden not in params


def test_tool_signature_has_no_status_or_id_field() -> None:
    """The model cannot choose the item's id, nor any status/kind outside
    what `content`/`kind`/`confidence`/`supporting_case_item_ids` allow.
    """
    params = set(inspect.signature(record_case_analysis).parameters)
    assert params == {"kind", "content", "confidence", "supporting_case_item_ids", "tool_context"}


def test_case_tools_module_contains_no_regex_or_keyword_routing() -> None:
    import backend.agents.team_manager.case_tools as module

    source = inspect.getsource(module)
    for forbidden in ("import re", "re.compile(", "re.match(", "re.search("):
        assert forbidden not in source
