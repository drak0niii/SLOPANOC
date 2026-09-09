"""Cloud SQL-only runtime hardening (POST-A5 refinement, Track A; final
corrective pass).

NORMAL SLOPANOC RUNTIME MUST NEVER USE SQLITE, AND MUST NEVER USE ADK'S
`InMemorySessionService` "memory" SESSION BACKEND -- both persistence
domains (ADK session/Case persistence via `Settings.resolve_database_url()`,
Generic Governed Knowledge persistence via `Settings.resolve_knowledge_
database_url()`) MUST resolve to a real PostgreSQL/Cloud SQL connection,
and session persistence must actually be database-backed
(`Settings.session_backend == "database"`). This is a POSITIVE
requirement -- the resolved SQLAlchemy dialect must be exactly
`"postgresql"` -- not merely "not sqlite" (a `mysql`/`mariadb`/`oracle`/
`mssql`/anything-else URL is rejected exactly like `sqlite` is).

THERE IS NO ENVIRONMENT VARIABLE THAT WEAKENS THIS POLICY. An earlier pass
of this refinement added `SLOPANOC_ALLOW_SQLITE_RUNTIME` as a config-based
test opt-out; that was a mistake -- ANY env var is, by construction,
something a real deployment's configuration could also set (accidentally
or otherwise), which defeats the entire point of a "normal runtime can
never use SQLite" guarantee. It has been removed from `Settings` entirely
(there is no such property to read). The ONLY way this module's own
`validate_runtime_database_configuration` is ever bypassed for a test is
`backend/tests/conftest.py`'s autouse fixture directly monkeypatching the
*function reference* `backend.api.app` imports and calls -- a pure
Python-level dependency substitution that no environment/configuration
value can reach or trigger, mirroring the exact pattern this codebase
already uses for `warmup_shared_model` (`test_model_warmup.py`). This
module itself never inspects pytest internals (`PYTEST_CURRENT_TEST`,
`sys.modules`, stack frames) and never will.

Deliberately NOT enforced inside `Settings.resolve_database_url()` /
`resolve_knowledge_database_url()` themselves -- those two methods stay
pure resolution logic (env var -> Secret Manager -> local-file default),
exactly as already relied upon by many existing tests that legitimately
construct an isolated SQLite repository/session service DIRECTLY (never
through `backend.api.app`'s `_lifespan` at all) via those same methods.
This module is instead wired into `_lifespan`, the application's one real
startup boundary (fail-fast, before any request is routed).
"""
from __future__ import annotations

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from backend.config.settings import ConfigurationError, Settings

_REQUIRED_DIALECT = "postgresql"

_SAFE_CONFIG_ERROR = (
    "Cloud SQL/PostgreSQL database configuration is required for {label} "
    "in normal runtime. Set {env_hint} to a PostgreSQL connection string "
    "(postgresql+asyncpg://...), or the matching *_SECRET_RESOURCE env var "
    "for deployment."
)

_SESSION_BACKEND_ERROR = (
    "SLOPANOC_SESSION_BACKEND=\"memory\" is not permitted for normal "
    "runtime -- session/Case persistence must be Cloud SQL PostgreSQL-"
    "backed. Set SLOPANOC_SESSION_BACKEND=\"database\" (the default) with "
    "SLOPANOC_DATABASE_URL pointing at PostgreSQL."
)


def _require_postgresql(database_url: str, *, label: str, env_hint: str) -> None:
    """Positively requires the resolved URL's SQLAlchemy dialect to be
    exactly `"postgresql"` -- sqlite/mysql/mariadb/oracle/mssql/an
    unparseable string are all rejected identically. Never includes the
    raw URL (which may embed credentials) in the raised error.
    """
    try:
        backend_name = make_url(database_url).get_backend_name()
    except ArgumentError:
        raise ConfigurationError(_SAFE_CONFIG_ERROR.format(label=label, env_hint=env_hint)) from None

    if backend_name != _REQUIRED_DIALECT:
        raise ConfigurationError(_SAFE_CONFIG_ERROR.format(label=label, env_hint=env_hint))


def validate_runtime_database_configuration(settings: Settings) -> tuple[str, str]:
    """Fails closed (raises `ConfigurationError`) unless BOTH persistence
    domains are genuinely PostgreSQL-backed:

      - `settings.session_backend` must be `"database"` (never `"memory"`
        -- that ADK mode never touches a database URL at all, so it can
        never be Cloud SQL-backed by definition) AND
        `settings.resolve_database_url()` must resolve to a `postgresql`
        dialect.
      - `settings.resolve_knowledge_database_url()` must resolve to a
        `postgresql` dialect (Governed Knowledge has no "memory"-
        equivalent mode to reject separately).

    Returns a sanitized `(session_database_backend, knowledge_database_
    backend)` pair -- always `("postgresql", "postgresql")` on success --
    safe for the caller's own startup observability line. Never returns
    on failure; raises instead.
    """
    if settings.session_backend != "database":
        raise ConfigurationError(_SESSION_BACKEND_ERROR)

    _require_postgresql(settings.resolve_database_url(), label="session/Case persistence", env_hint="SLOPANOC_DATABASE_URL")
    _require_postgresql(
        settings.resolve_knowledge_database_url(),
        label="Generic Governed Knowledge persistence",
        env_hint="SLOPANOC_KNOWLEDGE_DATABASE_URL",
    )

    return _REQUIRED_DIALECT, _REQUIRED_DIALECT
