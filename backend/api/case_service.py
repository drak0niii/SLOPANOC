"""API-layer orchestration for Case operations that need BOTH ADK session
ownership (`backend.api.session_service`) and Case domain logic
(`backend.cases.service`) -- this is the one place those two are
combined; `backend/cases/` itself knows nothing about ADK sessions (see
its `__init__.py` docstring).

SESSION LINKING SECURITY (instruction section 31): `link_session` verifies,
in order: (1) the requester owns ADK session `session_id` --
`session_service.get_session(session_id, user_id)`, the exact same
Phase-4C ownership check every other session-bound operation uses, raising
the identical anti-enumeration `not_found` for a foreign/unknown session;
(2) the requester is a member of Case `case_id` -- `CaseService.link_session`
raises the identical `not_found` for a foreign/unknown case; (3) the
session is not already linked elsewhere -- `CaseService.link_session`
raises a `action_failure` conflict. No step trusts anything the client
supplied beyond the two ids themselves.

ACTIVE-CASE STATE HINT (instruction section 23): `active_case_id` is
written into ADK session state via the same `persist_state_delta`
mechanism Phase 4B's approval endpoints use -- but it is explicitly
documented, here and at every place that reads it
(`backend/agents/team_manager/case_context.py`,
`backend/agents/team_manager/case_tools.py`), as a non-authoritative
HINT only. The authoritative Session<->Case relationship is always
`CaseSessionLinkRecord`, re-checked through `CaseService` on every read.
"""
from __future__ import annotations

from typing import Optional

from backend.api.schemas import ActiveCaseDTO
from backend.api.session_service import ApiSessionService
from backend.cases.schemas import CaseSessionLinkDTO
from backend.cases.service import CaseService
from backend.gateway.safe_error import SafeErrorException

ACTIVE_CASE_ID_STATE_KEY = "active_case_id"


async def link_session(
    session_service: ApiSessionService,
    case_service: CaseService,
    user_id: str,
    case_id: str,
    session_id: str,
) -> CaseSessionLinkDTO:
    # (1) ADK session ownership.
    await session_service.get_session(session_id, user_id)
    # (2) Case membership + (3) not-already-linked-elsewhere.
    link = await case_service.link_session(user_id, case_id, session_id, session_owner_user_id=user_id)

    session = await session_service.get_session(session_id, user_id)
    await session_service.persist_state_delta(session, {ACTIVE_CASE_ID_STATE_KEY: case_id})
    return link


async def unlink_session(
    session_service: ApiSessionService,
    case_service: CaseService,
    user_id: str,
    case_id: str,
    session_id: str,
) -> None:
    await session_service.get_session(session_id, user_id)
    await case_service.unlink_session(user_id, case_id, session_id)

    session = await session_service.get_session(session_id, user_id)
    await session_service.persist_state_delta(session, {ACTIVE_CASE_ID_STATE_KEY: None})


async def get_active_case_for_session(
    case_service: CaseService, user_id: str, session_id: str
) -> Optional[ActiveCaseDTO]:
    """Used by `chat_service.py` to populate `ChatResponse.active_case`.
    Callers must have already established that `user_id` owns
    `session_id` (chat_service always has, via its own existing
    ownership check) -- this function re-derives the Case link and
    re-verifies Case membership independently; it never trusts anything
    beyond the two ids.
    """
    link = await case_service.get_link_for_session(session_id)
    if link is None or link.session_user_id != user_id:
        return None

    try:
        case = await case_service.get_case(user_id, link.case_id)
    except SafeErrorException:
        # The case was deleted/membership revoked through another path
        # since linking -- fail safe (no active case shown), never error
        # the whole chat turn over a stale link.
        return None

    return ActiveCaseDTO(case_id=case.case_id, title=case.title, status=case.status)
