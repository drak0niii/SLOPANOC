"""User identity resolution for the API.

`UserContext` is the one abstraction every session-bound route depends on
for "who is making this request" -- never a raw header, never a request
body field. Every session-bound operation (create session, send a
message, approve, reject) resolves this FIRST, from a trusted request
boundary/dependency, and uses `UserContext.user_id` as the ADK `user_id`
for every session lookup -- this is what makes session ownership
enforcement automatic rather than a separately-implemented check (see
session_service.py's module docstring): ADK's own session storage is
already keyed by `(app_name, user_id, session_id)`, so a request
resolved to the wrong `user_id` simply cannot find another user's
session at all.

THIS IS DEVELOPMENT-ONLY IDENTITY, NOT PRODUCTION AUTHENTICATION.
`resolve_user_context` below trusts a plain request header
(`X-SLOPANOC-DEV-USER`) with no verification whatsoever -- anyone who can
reach this API can claim to be any user id simply by setting that header.
This is acceptable ONLY because this phase explicitly has no production
authentication yet (instruction section 7/14). It exists so that:

  1. session ownership enforcement, multi-user isolation, and the
     `UserContext` abstraction itself can be built and tested NOW, against
     a real (if trivial) identity boundary, rather than against the
     single hardcoded `user_id` constant Phase 4A/4B used.
  2. a future production identity provider (validating a real session
     cookie / OIDC token / etc.) is a drop-in replacement for
     `resolve_user_context` alone -- same `UserContext` return type, same
     FastAPI dependency-injection point (`Depends(resolve_user_context)`
     in app.py) -- with zero change required to `ApiSessionService`,
     `ChatService`, `approval_service`, or any agent/tool code, all of
     which only ever see the already-resolved `user_id`.

NEVER accepted from a JSON request body anywhere in this API (instruction
section 8) -- `SendMessageRequest`/`ApprovalRequest` (schemas.py) have no
`user_id` field, and nothing in app.py reads one from a request body.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from fastapi import Header

from backend.api.session_service import DEFAULT_USER_ID

DEV_USER_HEADER_NAME = "X-SLOPANOC-DEV-USER"

# The identity assumed when the (optional, development-only) header is
# absent -- keeps local/manual testing (curl, the manual dev CLI's future
# HTTP equivalent, etc.) usable without requiring every request to set a
# header, while still resolving to ONE consistent, real `UserContext`
# rather than silently meaning "no identity". Deliberately reuses
# `session_service.DEFAULT_USER_ID` (rather than a second, differently-
# named constant) so a session/turn created directly against
# `ApiSessionService` with no explicit `user_id` (as most of this
# backend's pre-4C tests still do) and a request made through the HTTP API
# with no dev header resolve to the exact same identity -- one default,
# not two that happen to need to be kept in sync.
_DEFAULT_DEV_USER_ID = DEFAULT_USER_ID


@dataclass(frozen=True)
class UserContext:
    """The resolved identity for one request. Deliberately minimal --
    just `user_id` (instruction: "Do not over-engineer roles/permissions
    yet.").
    """

    user_id: str


async def resolve_user_context(
    x_slopanoc_dev_user: Optional[str] = Header(default=None, alias=DEV_USER_HEADER_NAME),
) -> UserContext:
    """FastAPI dependency: the ONE place identity is resolved for the
    whole API (instruction: "identity logic is centralized"). See module
    docstring -- this is a development-only mechanism with no
    verification.
    """
    user_id = (x_slopanoc_dev_user or "").strip() or _DEFAULT_DEV_USER_ID
    return UserContext(user_id=user_id)
