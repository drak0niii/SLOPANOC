"""Tests for the session-persistence backend factory
(backend/api/session_service.py's `create_session_service_backend`) and
its underlying settings (backend/config/settings.py).

Constructing a `DatabaseSessionService` does not itself connect to the
database (confirmed against the installed ADK source: `create_async_engine`
is lazy) -- so a PostgreSQL URL can be exercised here without a live
Postgres server. Real read/write persistence round-trips (which DO need
an actual database) are covered separately in test_api_persistence.py
against a real temporary SQLite file.
"""
from __future__ import annotations

import pytest

from backend.config.settings import ConfigurationError, Settings, get_settings


def test_session_backend_defaults_to_database() -> None:
    settings = Settings(env={})
    assert settings.session_backend == "database"


def test_session_backend_can_be_set_to_memory() -> None:
    settings = Settings(env={"SLOPANOC_SESSION_BACKEND": "memory"})
    assert settings.session_backend == "memory"


def test_session_backend_is_case_insensitive() -> None:
    settings = Settings(env={"SLOPANOC_SESSION_BACKEND": "MEMORY"})
    assert settings.session_backend == "memory"


def test_unsupported_session_backend_raises_configuration_error() -> None:
    settings = Settings(env={"SLOPANOC_SESSION_BACKEND": "mongodb"})
    with pytest.raises(ConfigurationError):
        _ = settings.session_backend


def test_database_url_defaults_to_local_sqlite() -> None:
    settings = Settings(env={})
    url = settings.resolve_database_url()
    assert url.startswith("sqlite+aiosqlite:///")


def test_database_url_uses_the_explicit_env_var_when_set() -> None:
    settings = Settings(env={"SLOPANOC_DATABASE_URL": "postgresql+asyncpg://user:pw@host/db"})
    assert settings.resolve_database_url() == "postgresql+asyncpg://user:pw@host/db"


def test_database_url_never_appears_in_the_configuration_error_message() -> None:
    """Mirrors the same guarantee `resolve_power_automate_gateway_url`
    already makes: no value is resolved (so nothing to leak) before a
    `ConfigurationError` -- but for the session backend, an unsupported
    backend name is the only failure mode, and its message must not echo
    any database URL either.
    """
    settings = Settings(env={"SLOPANOC_SESSION_BACKEND": "invalid", "SLOPANOC_DATABASE_URL": "postgresql+asyncpg://user:secret@host/db"})
    with pytest.raises(ConfigurationError) as exc_info:
        _ = settings.session_backend
    assert "secret" not in str(exc_info.value)
    assert "postgresql" not in str(exc_info.value)


# --- Factory -----------------------------------------------------------


def test_factory_returns_in_memory_service_for_memory_backend() -> None:
    from google.adk.sessions import InMemorySessionService

    from backend.api.session_service import create_session_service_backend

    settings = Settings(env={"SLOPANOC_SESSION_BACKEND": "memory"})
    service = create_session_service_backend(settings)
    assert isinstance(service, InMemorySessionService)


def test_factory_returns_database_session_service_for_database_backend_sqlite() -> None:
    from google.adk.sessions import DatabaseSessionService

    from backend.api.session_service import create_session_service_backend

    settings = Settings(env={"SLOPANOC_SESSION_BACKEND": "database", "SLOPANOC_DATABASE_URL": "sqlite+aiosqlite:///:memory:"})
    service = create_session_service_backend(settings)
    assert isinstance(service, DatabaseSessionService)


def test_factory_accepts_a_postgresql_url_without_connecting() -> None:
    """Confirms the exact production URL format
    (`postgresql+asyncpg://...`) is accepted by the installed ADK/
    SQLAlchemy stack -- engine construction is lazy (no connection
    attempted), so this is safe to run without a live PostgreSQL server.
    """
    from google.adk.sessions import DatabaseSessionService

    from backend.api.session_service import create_session_service_backend

    settings = Settings(
        env={
            "SLOPANOC_SESSION_BACKEND": "database",
            "SLOPANOC_DATABASE_URL": "postgresql+asyncpg://user:pw@localhost:5432/slopanoc",
        }
    )
    service = create_session_service_backend(settings)
    assert isinstance(service, DatabaseSessionService)
    assert service.db_engine.dialect.name == "postgresql"


def test_factory_never_constructs_two_authoritative_backends_for_one_call() -> None:
    """There is exactly one branch taken per call -- never both an
    in-memory AND a database service constructed for the same factory
    invocation.
    """
    import inspect

    from backend.api.session_service import create_session_service_backend

    source = inspect.getsource(create_session_service_backend)
    assert source.count("return InMemorySessionService()") == 1
    assert source.count("return DatabaseSessionService(") == 1


def test_get_session_service_singleton_uses_the_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.adk.sessions import InMemorySessionService

    from backend.api import session_service as session_service_module

    monkeypatch.setenv("SLOPANOC_SESSION_BACKEND", "memory")
    get_settings.cache_clear()
    session_service_module.get_session_service.cache_clear()
    try:
        service = session_service_module.get_session_service()
        assert isinstance(service.adk_session_service, InMemorySessionService)
    finally:
        session_service_module.get_session_service.cache_clear()
        get_settings.cache_clear()
        monkeypatch.delenv("SLOPANOC_SESSION_BACKEND", raising=False)
