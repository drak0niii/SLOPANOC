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

from backend.gateway.safe_error import SafeError, SafeErrorException

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
    """The resolved identity for one request.

    POST-6A: `user_id` remains the single ownership key every downstream
    layer already uses, so nothing about session/case/attachment
    isolation changes shape. What changed is where it comes from: in
    `oidc` mode it is DERIVED from verified, stable token claims
    (issuer+subject, or tenant+object id), not asserted by the caller.

    `roles` and `verified` are additive. Roles carry AUTHORIZATION
    (deliberately separate from authentication -- who you are and what
    you may do are different questions); `verified` lets a privileged
    operation require a genuinely authenticated caller rather than merely
    a named one.
    """

    user_id: str
    roles: tuple[str, ...] = ()
    verified: bool = False
    tenant_id: Optional[str] = None


async def resolve_user_context(
    x_slopanoc_dev_user: Optional[str] = Header(default=None, alias=DEV_USER_HEADER_NAME),
    authorization: Optional[str] = Header(default=None),
) -> UserContext:
    """FastAPI dependency: the ONE place identity is resolved for the
    whole API.

    POST-6A -- TWO MODES, AND ONLY ONE IS EVER ACTIVE:

    `SLOPANOC_AUTH_MODE=oidc` (production): a verified bearer token is
    REQUIRED. `X-SLOPANOC-DEV-USER` is ignored completely -- including
    when sent alongside a valid token, which is exactly the case a
    "prefer the token" implementation would still get wrong if it left
    the header readable anywhere downstream. Missing issuer/audience/JWKS
    configuration REJECTS the request; it never degrades to development
    identity, because a deployment that asks for verified identity and
    cannot do it must fail closed.

    `SLOPANOC_AUTH_MODE=development` (default): the pre-existing
    unverified header, unchanged, so local work and existing tests are
    unaffected. The resulting context is marked `verified=False`, which
    is what privileged operations check.
    """
    from backend.config.settings import get_settings

    settings = get_settings()
    if settings.auth_mode != "oidc":
        user_id = (x_slopanoc_dev_user or "").strip() or _DEFAULT_DEV_USER_ID
        return UserContext(user_id=user_id, verified=False)

    from backend.api.auth import verify_bearer_token

    if not (settings.auth_issuer and settings.auth_audience and settings.auth_jwks_url):
        raise SafeErrorException(
            SafeError(
                error_code="internal_error",
                user_message="This deployment is not configured for authentication.",
            )
        )

    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        # 401, not 403 -- see `backend.api.auth._unauthenticated`. The
        # browser client clears its token and restarts sign-in on a
        # 401; a 403 here would strand an expired session.
        raise SafeErrorException(
            SafeError(error_code="authentication_error", user_message="Sign-in is required.")
        )

    identity = verify_bearer_token(
        token.strip(),
        issuer=settings.auth_issuer,
        audience=settings.auth_audience,
        jwks_url=settings.auth_jwks_url,
        algorithms=settings.auth_algorithms,
        allowed_tenants=settings.auth_allowed_tenants,
        roles_claim=settings.auth_roles_claim,
    )
    return UserContext(
        user_id=identity.user_id,
        roles=identity.roles,
        verified=True,
        tenant_id=identity.tenant_id,
    )
