"""POST-A5 refinement (Track A, final corrective pass): tests for
`backend.api.runtime_database_policy.validate_runtime_database_configuration`
-- the Cloud SQL-only runtime startup gate wired into `backend.api.app
._lifespan`.

Two groups of tests:
  1. Pure-function tests against the real, unpatched
     `validate_runtime_database_configuration` -- never touch the real
     FastAPI app, a live Postgres server, the Cloud SQL Auth Proxy, or
     Application Default Credentials. Unaffected by conftest.py's autouse
     `bypass_runtime_database_policy_for_tests` fixture, which only
     patches the reference `backend.api.app` itself calls.
  2. Real `_lifespan` integration tests, proving the policy is genuinely
     enforced on a real app boot -- each explicitly restores the real
     function via its own `monkeypatch.setattr`, overriding the autouse
     bypass for that one test only (the same override pattern
     `test_model_warmup.py` already uses against `disable_model_warmup_
     by_default`).
"""
from __future__ import annotations

import pytest

from backend.api.runtime_database_policy import validate_runtime_database_configuration
from backend.config.settings import ConfigurationError, Settings

_POSTGRES_SESSION_URL = "postgresql+asyncpg://user:pw@127.0.0.1:5432/slopanoc"
_POSTGRES_KNOWLEDGE_URL = "postgresql+asyncpg://user:pw@127.0.0.1:5432/slopanoc"


# =============================================================================
# 1. Pure-function tests
# =============================================================================


def test_postgresql_accepted_for_both_domains() -> None:
    settings = Settings(
        env={"SLOPANOC_DATABASE_URL": _POSTGRES_SESSION_URL, "SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL}
    )
    assert validate_runtime_database_configuration(settings) == ("postgresql", "postgresql")


def test_postgresql_accepted_with_a_different_async_driver_still_reports_the_dialect_only() -> None:
    """`get_backend_name()` reports the dialect ("postgresql"), independent
    of the driver suffix -- psycopg/pg8000/asyncpg all resolve the same
    way at this check's level."""
    settings = Settings(
        env={
            "SLOPANOC_DATABASE_URL": "postgresql+psycopg://user:pw@127.0.0.1:5432/slopanoc",
            "SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL,
        }
    )
    assert validate_runtime_database_configuration(settings) == ("postgresql", "postgresql")


def test_sqlite_rejected_for_session_domain() -> None:
    settings = Settings(
        env={
            "SLOPANOC_DATABASE_URL": "sqlite+aiosqlite:///./explicit_sessions.db",
            "SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL,
        }
    )
    with pytest.raises(ConfigurationError):
        validate_runtime_database_configuration(settings)


def test_sqlite_rejected_for_knowledge_domain() -> None:
    settings = Settings(
        env={
            "SLOPANOC_DATABASE_URL": _POSTGRES_SESSION_URL,
            "SLOPANOC_KNOWLEDGE_DATABASE_URL": "sqlite+aiosqlite:///./explicit_knowledge.db",
        }
    )
    with pytest.raises(ConfigurationError):
        validate_runtime_database_configuration(settings)


@pytest.mark.parametrize(
    "url",
    [
        "mysql+pymysql://user:pw@127.0.0.1:3306/slopanoc",
        "mariadb+pymysql://user:pw@127.0.0.1:3306/slopanoc",
        "oracle+cx_oracle://user:pw@127.0.0.1:1521/slopanoc",
        "mssql+pyodbc://user:pw@127.0.0.1:1433/slopanoc",
    ],
    ids=["mysql", "mariadb", "oracle", "mssql"],
)
def test_non_postgresql_dialects_rejected_for_session_domain(url: str) -> None:
    settings = Settings(env={"SLOPANOC_DATABASE_URL": url, "SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL})
    with pytest.raises(ConfigurationError):
        validate_runtime_database_configuration(settings)


@pytest.mark.parametrize(
    "url",
    [
        "mysql+pymysql://user:pw@127.0.0.1:3306/slopanoc",
        "mariadb+pymysql://user:pw@127.0.0.1:3306/slopanoc",
        "oracle+cx_oracle://user:pw@127.0.0.1:1521/slopanoc",
        "mssql+pyodbc://user:pw@127.0.0.1:1433/slopanoc",
    ],
    ids=["mysql", "mariadb", "oracle", "mssql"],
)
def test_non_postgresql_dialects_rejected_for_knowledge_domain(url: str) -> None:
    settings = Settings(env={"SLOPANOC_DATABASE_URL": _POSTGRES_SESSION_URL, "SLOPANOC_KNOWLEDGE_DATABASE_URL": url})
    with pytest.raises(ConfigurationError):
        validate_runtime_database_configuration(settings)


def test_missing_session_database_config_fails() -> None:
    """Missing config resolves to the local-file SQLite default inside
    `resolve_database_url()` -- rejected by the SAME positive-postgresql
    check as an explicit sqlite URL, never a separate code path."""
    settings = Settings(env={"SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL})
    with pytest.raises(ConfigurationError) as exc:
        validate_runtime_database_configuration(settings)
    assert "SLOPANOC_DATABASE_URL" in str(exc.value)
    assert "slopanoc_sessions.db" not in str(exc.value)


def test_missing_knowledge_database_config_fails() -> None:
    settings = Settings(env={"SLOPANOC_DATABASE_URL": _POSTGRES_SESSION_URL})
    with pytest.raises(ConfigurationError) as exc:
        validate_runtime_database_configuration(settings)
    assert "SLOPANOC_KNOWLEDGE_DATABASE_URL" in str(exc.value)
    assert "slopanoc_knowledge.db" not in str(exc.value)


def test_malformed_session_url_fails_safely() -> None:
    """An unparseable URL string must still raise the SAME safe
    `ConfigurationError` -- never a raw `sqlalchemy.exc.ArgumentError`
    leaking to the caller."""
    settings = Settings(
        env={"SLOPANOC_DATABASE_URL": "not a valid url at all!!", "SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL}
    )
    with pytest.raises(ConfigurationError):
        validate_runtime_database_configuration(settings)


def test_malformed_knowledge_url_fails_safely() -> None:
    settings = Settings(
        env={"SLOPANOC_DATABASE_URL": _POSTGRES_SESSION_URL, "SLOPANOC_KNOWLEDGE_DATABASE_URL": "://not::a::url"}
    )
    with pytest.raises(ConfigurationError):
        validate_runtime_database_configuration(settings)


def test_memory_session_backend_rejected_even_with_valid_postgresql_knowledge_config() -> None:
    settings = Settings(
        env={"SLOPANOC_SESSION_BACKEND": "memory", "SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL}
    )
    with pytest.raises(ConfigurationError) as exc:
        validate_runtime_database_configuration(settings)
    assert "memory" in str(exc.value).lower()


def test_memory_session_backend_rejected_even_when_database_url_is_valid_postgresql() -> None:
    """The `session_backend` check runs FIRST and rejects unconditionally
    -- a valid `SLOPANOC_DATABASE_URL` does not "rescue" `memory` mode,
    since ADK's `InMemorySessionService` never reads that URL at all."""
    settings = Settings(
        env={
            "SLOPANOC_SESSION_BACKEND": "memory",
            "SLOPANOC_DATABASE_URL": _POSTGRES_SESSION_URL,
            "SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL,
        }
    )
    with pytest.raises(ConfigurationError):
        validate_runtime_database_configuration(settings)


def test_configuration_error_never_leaks_the_resolved_url_or_credentials() -> None:
    settings = Settings(
        env={
            "SLOPANOC_DATABASE_URL": "sqlite+aiosqlite:///./secret_looking_path_user_pw.db",
            "SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL,
        }
    )
    with pytest.raises(ConfigurationError) as exc:
        validate_runtime_database_configuration(settings)
    assert "secret_looking_path_user_pw" not in str(exc.value)


def test_no_allow_sqlite_runtime_escape_hatch_exists_anywhere() -> None:
    """Correction A: there must be NO environment variable, setting, or
    property that weakens this policy for normal runtime. Proves the
    removed `Settings.allow_sqlite_runtime` property no longer exists, and
    that a stray `SLOPANOC_ALLOW_SQLITE_RUNTIME=true` in the environment
    (e.g. left over from a prior local session) has ZERO effect on the
    outcome -- sqlite is still rejected regardless.
    """
    assert not hasattr(Settings, "allow_sqlite_runtime")

    settings = Settings(
        env={
            "SLOPANOC_ALLOW_SQLITE_RUNTIME": "true",
            "SLOPANOC_DATABASE_URL": "sqlite+aiosqlite:///./explicit_sessions.db",
            "SLOPANOC_KNOWLEDGE_DATABASE_URL": _POSTGRES_KNOWLEDGE_URL,
        }
    )
    with pytest.raises(ConfigurationError):
        validate_runtime_database_configuration(settings)


# =============================================================================
# 2. Real `_lifespan` integration tests (real app boot, real ASGI lifespan)
# =============================================================================


def _restore_real_policy_function(monkeypatch: pytest.MonkeyPatch) -> None:
    """Overrides conftest.py's autouse `bypass_runtime_database_policy_
    for_tests` fixture for the duration of one test, so `_lifespan` calls
    the REAL `validate_runtime_database_configuration` on a genuine app
    boot -- same override pattern `test_model_warmup.py` already uses.
    """
    monkeypatch.setattr(
        "backend.api.app.validate_runtime_database_configuration", validate_runtime_database_configuration
    )


def test_lifespan_accepts_valid_postgresql_configuration_on_real_app_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Engine construction is lazy (established elsewhere in this test
    suite), so a real Postgres server is not required for this to succeed
    -- only for the URL to correctly PARSE as `postgresql`.
    """
    from fastapi.testclient import TestClient

    from backend.api.app import app

    _restore_real_policy_function(monkeypatch)
    monkeypatch.setenv("SLOPANOC_DATABASE_URL", _POSTGRES_SESSION_URL)
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", _POSTGRES_KNOWLEDGE_URL)

    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200


def test_lifespan_rejects_missing_database_config_on_real_app_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import app

    _restore_real_policy_function(monkeypatch)
    monkeypatch.delenv("SLOPANOC_DATABASE_URL", raising=False)
    monkeypatch.delenv("SLOPANOC_DATABASE_SECRET_RESOURCE", raising=False)
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", _POSTGRES_KNOWLEDGE_URL)

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass


def test_lifespan_rejects_explicit_sqlite_on_real_app_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import app

    _restore_real_policy_function(monkeypatch)
    monkeypatch.setenv("SLOPANOC_DATABASE_URL", "sqlite+aiosqlite:///./real_boot_test.db")
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", _POSTGRES_KNOWLEDGE_URL)

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass


def test_lifespan_rejects_mysql_style_url_on_real_app_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import app

    _restore_real_policy_function(monkeypatch)
    monkeypatch.setenv("SLOPANOC_DATABASE_URL", _POSTGRES_SESSION_URL)
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", "mysql+pymysql://user:pw@127.0.0.1:3306/slopanoc")

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass


def test_lifespan_rejects_memory_session_backend_on_real_app_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The core Correction B proof: a normally-started app cannot bypass
    Cloud SQL session persistence merely by setting
    `SLOPANOC_SESSION_BACKEND=memory`, even with valid PostgreSQL URLs
    configured for both domains."""
    from fastapi.testclient import TestClient

    from backend.api.app import app

    _restore_real_policy_function(monkeypatch)
    monkeypatch.setenv("SLOPANOC_SESSION_BACKEND", "memory")
    monkeypatch.setenv("SLOPANOC_DATABASE_URL", _POSTGRES_SESSION_URL)
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", _POSTGRES_KNOWLEDGE_URL)

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass


def test_lifespan_ignores_a_stray_allow_sqlite_runtime_env_var_on_real_app_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Correction A's core proof at the real-boot level: a leftover/
    accidentally-set `SLOPANOC_ALLOW_SQLITE_RUNTIME=true` in a real
    deployment's environment has NO effect -- sqlite is still rejected."""
    from fastapi.testclient import TestClient

    from backend.api.app import app

    _restore_real_policy_function(monkeypatch)
    monkeypatch.setenv("SLOPANOC_ALLOW_SQLITE_RUNTIME", "true")
    monkeypatch.setenv("SLOPANOC_DATABASE_URL", "sqlite+aiosqlite:///./real_boot_test.db")
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_DATABASE_URL", _POSTGRES_KNOWLEDGE_URL)

    with pytest.raises(ConfigurationError):
        with TestClient(app):
            pass
