"""POST-6A -- Verified API identity (OIDC/JWT).

THE GAP THIS CLOSES: `identity.py` resolved the caller from
`X-SLOPANOC-DEV-USER`, an unverified request header. Every ownership
boundary in this backend -- session isolation, case access, attachment
retrieval, approval, execution, knowledge governance -- rested on a value
the caller chose for themselves. That is not an identity boundary; it is
a convention.

WHAT THIS IS: bearer-token verification through the EXISTING
`UserContext` dependency, so nothing downstream changes shape. Built on
PyJWT (already a dependency of this environment) plus its own JWKS
client; no new auth framework, no new session mechanism.

WHAT IS VERIFIED, and why each matters:

    signature   -- against a key fetched from the CONFIGURED issuer's
                   JWKS endpoint. Never a URL taken from the token: a
                   token that names its own key source verifies itself.
    algorithms  -- an explicit allowlist. Without one, `alg: none` and
                   algorithm-confusion (HMAC-verifying against a public
                   key) are both live.
    issuer      -- exact match against configuration.
    audience    -- exact match. A token minted for a different API is a
                   valid token; it is not a token for US.
    expiry      -- `exp`/`nbf`, with a small configurable leeway.
    tenant      -- when configured, `tid` must be in the allowlist.

IDENTITY IS DERIVED FROM STABLE CLAIMS ONLY. `sub` (scoped by issuer),
or `tid`+`oid` for Entra ID. Never `email`, `preferred_username` or
`name`: those are mutable, and an ownership key that changes when someone
changes their display name silently detaches them from their own data.

PRODUCTION FAILS CLOSED. With `SLOPANOC_AUTH_MODE=oidc` and no issuer/
audience configured, every request is rejected -- a missing auth
configuration must never degrade to "trust everyone". In `oidc` mode
`X-SLOPANOC-DEV-USER` is ignored outright, including when sent alongside
a valid token.

NO CREDENTIALS ARE FABRICATED HERE. This module holds no tenant id, no
client id, no secret and no token. Everything is configuration the
operator supplies.

WHAT THIS DOES NOT GRANT: authenticating a user to SLOPANOC does not give
SLOPANOC that user's Microsoft permissions. Teams access still runs
through the Power Automate connection owner's own identity -- see
`docs/TEAMS_TOOL_CONTRACT.md`. Per-user delegated Microsoft access
remains unimplemented.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from backend.gateway.safe_error import SafeError, SafeErrorException

_logger = logging.getLogger(__name__)

__all__ = [
    "AuthConfigurationError",
    "AuthMode",
    "VerifiedIdentity",
    "JwksCache",
    "verify_bearer_token",
]


class AuthMode:
    DEVELOPMENT = "development"
    OIDC = "oidc"


class AuthConfigurationError(RuntimeError):
    """The deployment asks for verified identity but is not configured to
    provide it. Raised at request time and surfaced as a deployment
    failure -- never silently downgraded to development identity."""


@dataclass(frozen=True)
class VerifiedIdentity:
    """What a successfully verified token establishes."""

    user_id: str
    """The STABLE ownership key. Derived from issuer+subject, or
    tenant+object id. Never a display name or bare email."""

    issuer: str
    subject: str
    tenant_id: Optional[str] = None
    object_id: Optional[str] = None
    roles: tuple[str, ...] = ()
    """Roles/groups asserted by the token, for AUTHORIZATION decisions.
    Kept separate from identity on purpose: who you are and what you may
    do are different questions."""


def _unauthenticated(message: str) -> SafeErrorException:
    """POST-6A -- `authentication_error` (HTTP 401), not
    `authorization_error` (403).

    The distinction is not pedantic. 401 means "we do not know who you
    are -- present a credential", which is exactly what a missing,
    malformed, expired or unverifiable token means, and it is the
    status the browser client acts on to clear a dead token and
    restart sign-in. 403 means "we know who you are and you may not do
    this"; returning it here would tell an expired session it had been
    denied permission, and the client would never recover on its own.
    """
    return SafeErrorException(SafeError(error_code="authentication_error", user_message=message))


class JwksCache:
    """Bounded, rotation-aware JWKS cache.

    Keys are fetched ONLY from the configured issuer's JWKS URL. A cache
    miss on an unknown `kid` triggers at most one refresh per
    `min_refresh_interval` -- so a genuine key rotation is picked up
    promptly, while a stream of tokens with bogus `kid`s cannot be turned
    into a request amplifier against the issuer.
    """

    def __init__(self, jwks_url: str, *, ttl_seconds: float = 3600.0, min_refresh_interval: float = 60.0) -> None:
        self._jwks_url = jwks_url
        self._ttl = ttl_seconds
        self._min_refresh_interval = min_refresh_interval
        self._lock = threading.Lock()
        self._keys: dict[str, Any] = {}
        self._fetched_at: float = 0.0
        self._last_attempt: float = 0.0

    def _fetch(self) -> None:
        import json
        import urllib.request

        with urllib.request.urlopen(self._jwks_url, timeout=10) as response:  # noqa: S310 -- configured URL only
            document = json.loads(response.read().decode("utf-8"))
        keys = {}
        for entry in document.get("keys", []):
            kid = entry.get("kid")
            if kid:
                keys[kid] = entry
        self._keys = keys
        self._fetched_at = time.monotonic()

    def key_for(self, kid: Optional[str]) -> Optional[Any]:
        """The JWK for `kid`, refreshing if it is unknown or stale."""
        now = time.monotonic()
        with self._lock:
            stale = (now - self._fetched_at) > self._ttl
            unknown = kid is not None and kid not in self._keys
            may_retry = (now - self._last_attempt) > self._min_refresh_interval
            if (not self._keys or stale or unknown) and may_retry:
                self._last_attempt = now
                try:
                    self._fetch()
                except Exception:
                    _logger.warning("auth: could not refresh JWKS", exc_info=True)
            return self._keys.get(kid) if kid else None


_jwks_caches: dict[str, JwksCache] = {}
_jwks_lock = threading.Lock()


def _cache_for(jwks_url: str, ttl_seconds: float) -> JwksCache:
    with _jwks_lock:
        cache = _jwks_caches.get(jwks_url)
        if cache is None:
            cache = JwksCache(jwks_url, ttl_seconds=ttl_seconds)
            _jwks_caches[jwks_url] = cache
        return cache


def derive_user_id(claims: dict[str, Any], issuer: str) -> str:
    """The stable ownership key.

    Entra ID: `tid`+`oid` -- the object id is immutable for the life of
    the account, and the tenant scopes it. Otherwise: issuer+`sub`, the
    OIDC-standard stable pair. `sub` alone is NOT enough: it is only
    unique within an issuer, so two issuers could collide.

    Deliberately never `email`/`preferred_username`/`name`: all mutable,
    and an ownership key that changes when a display name changes
    silently detaches a user from their own sessions and cases.
    """
    tenant = claims.get("tid")
    object_id = claims.get("oid")
    if tenant and object_id:
        return f"aad:{tenant}:{object_id}"
    subject = claims.get("sub")
    if not subject:
        raise _unauthenticated("This token does not identify a user.")
    return f"oidc:{issuer}:{subject}"


def verify_bearer_token(
    token: str,
    *,
    issuer: str,
    audience: str,
    jwks_url: str,
    algorithms: tuple[str, ...],
    allowed_tenants: frozenset[str] = frozenset(),
    leeway_seconds: float = 30.0,
    roles_claim: str = "roles",
    jwks_ttl_seconds: float = 3600.0,
) -> VerifiedIdentity:
    """Verify one bearer token. Raises `SafeErrorException` for every
    rejection -- the caller never sees a library exception, and the
    response never explains WHICH check failed (that is reconnaissance)."""
    import jwt
    from jwt import algorithms as jwt_algorithms

    try:
        header = jwt.get_unverified_header(token)
    except Exception:
        raise _unauthenticated("Your sign-in could not be verified.") from None

    algorithm = header.get("alg")
    if algorithm not in algorithms:
        # Blocks `alg: none` and algorithm confusion outright, before any
        # key material is selected.
        raise _unauthenticated("Your sign-in could not be verified.")

    jwk = _cache_for(jwks_url, jwks_ttl_seconds).key_for(header.get("kid"))
    if jwk is None:
        raise _unauthenticated("Your sign-in could not be verified.")

    try:
        key = jwt_algorithms.RSAAlgorithm.from_jwk(jwk) if algorithm.startswith("RS") else (
            jwt_algorithms.ECAlgorithm.from_jwk(jwk)
        )
        claims = jwt.decode(
            token,
            key=key,
            algorithms=list(algorithms),
            audience=audience,
            issuer=issuer,
            leeway=leeway_seconds,
            options={"require": ["exp", "iss", "aud"]},
        )
    except Exception:
        # Signature, expiry, issuer, audience and nbf are all checked by
        # `jwt.decode`; every failure resolves to the same opaque message.
        raise _unauthenticated("Your sign-in has expired or is not valid for this application.") from None

    tenant = claims.get("tid")
    if allowed_tenants and (tenant or "") not in allowed_tenants:
        raise _unauthenticated("Your organization is not permitted to use this application.")

    raw_roles = claims.get(roles_claim) or ()
    roles = tuple(str(r) for r in raw_roles) if isinstance(raw_roles, (list, tuple)) else (str(raw_roles),)

    return VerifiedIdentity(
        user_id=derive_user_id(claims, issuer),
        issuer=str(claims.get("iss", issuer)),
        subject=str(claims.get("sub", "")),
        tenant_id=str(tenant) if tenant else None,
        object_id=str(claims["oid"]) if claims.get("oid") else None,
        roles=roles,
    )
