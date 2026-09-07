"""POST-5.1 A2: tests for `resolve_knowledge_database_url()`
(backend/config/settings.py) -- the Generic KM database URL/secret
resolution added to bring it to parity with `resolve_database_url()`.

Mirrors two existing test files' own patterns rather than inventing new
ones:
  - test_api_session_backend_factory.py's plain-string / postgresql-URL-
    without-connecting assertions.
  - test_settings_secret_caching.py's fake `SecretManagerServiceClient`
    (never touches the real GCP Secret Manager).

None of these tests depend on gcloud, the Cloud SQL Auth Proxy, network
access, or Application Default Credentials -- `resolve_knowledge_
database_url()` only ever returns a string; nothing here opens a real
database or Secret Manager connection.
"""
from __future__ import annotations

import pytest

from backend.config import settings as settings_module
from backend.config.settings import Settings


# --- Local default / explicit env var -------------------------------------


def test_knowledge_database_url_defaults_to_local_sqlite() -> None:
    settings = Settings(env={})
    url = settings.resolve_knowledge_database_url()
    assert url.startswith("sqlite+aiosqlite:///")


def test_knowledge_database_url_uses_the_explicit_env_var_when_set() -> None:
    settings = Settings(env={"SLOPANOC_KNOWLEDGE_DATABASE_URL": "postgresql+asyncpg://user:pw@host/slopanoc"})
    assert settings.resolve_knowledge_database_url() == "postgresql+asyncpg://user:pw@host/slopanoc"


def test_knowledge_database_url_accepts_postgresql_asyncpg_scheme() -> None:
    """Configuration acceptance only -- no connection is attempted or
    required; `resolve_knowledge_database_url()` is plain string
    resolution.
    """
    settings = Settings(env={"SLOPANOC_KNOWLEDGE_DATABASE_URL": "postgresql+asyncpg://costin%40example.com@127.0.0.1:5432/slopanoc"})
    url = settings.resolve_knowledge_database_url()
    assert url.startswith("postgresql+asyncpg://")


def test_knowledge_database_url_never_reuses_the_session_database_url() -> None:
    """The two configuration domains stay explicit and independent
    (instruction: "Do NOT automatically make Knowledge silently reuse the
    session DB setting") -- setting only `SLOPANOC_DATABASE_URL` must
    never change what `resolve_knowledge_database_url()` returns.
    """
    settings = Settings(env={"SLOPANOC_DATABASE_URL": "postgresql+asyncpg://user:pw@host/slopanoc"})
    url = settings.resolve_knowledge_database_url()
    assert url.startswith("sqlite+aiosqlite:///")
    assert "postgresql" not in url


def test_session_database_url_never_reuses_the_knowledge_database_url() -> None:
    """The reverse direction of the same independence guarantee."""
    settings = Settings(env={"SLOPANOC_KNOWLEDGE_DATABASE_URL": "postgresql+asyncpg://user:pw@host/slopanoc"})
    url = settings.resolve_database_url()
    assert url.startswith("sqlite+aiosqlite:///")
    assert "postgresql" not in url


def test_knowledge_database_url_and_session_database_url_can_both_target_the_same_database() -> None:
    """A real deployment MAY point both at the same Cloud SQL database --
    this is a configuration choice, not something the code forces or
    forbids.
    """
    settings = Settings(
        env={
            "SLOPANOC_DATABASE_URL": "postgresql+asyncpg://user:pw@127.0.0.1:5432/slopanoc",
            "SLOPANOC_KNOWLEDGE_DATABASE_URL": "postgresql+asyncpg://user:pw@127.0.0.1:5432/slopanoc",
        }
    )
    assert settings.resolve_database_url() == settings.resolve_knowledge_database_url()


# --- Repository construction (engine is lazy -- no live Postgres needed) --


def test_sqlalchemy_knowledge_repository_accepts_a_postgresql_url_without_connecting() -> None:
    """Mirrors test_api_session_backend_factory.py's
    `test_factory_accepts_a_postgresql_url_without_connecting` -- engine
    construction is lazy (verified there against the installed ADK/
    SQLAlchemy stack), so this is safe without a live PostgreSQL server.
    """
    from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository

    repository = SqlAlchemyKnowledgeRepository("postgresql+asyncpg://user:pw@localhost:5432/slopanoc")
    assert repository._engine.dialect.name == "postgresql"


# --- Secret Manager resolution (fake client -- never touches real GCP) ----


class _FakeSecretPayload:
    def __init__(self, value: str) -> None:
        self.data = value.encode("utf-8")


class _FakeAccessSecretVersionResponse:
    def __init__(self, value: str) -> None:
        self.payload = _FakeSecretPayload(value)


class _FakeSecretManagerServiceClient:
    construct_count = 0
    access_calls: list = []

    def __init__(self) -> None:
        _FakeSecretManagerServiceClient.construct_count += 1

    def access_secret_version(self, name: str):
        _FakeSecretManagerServiceClient.access_calls.append(name)
        return _FakeAccessSecretVersionResponse(f"postgresql+asyncpg://secret-user:secret-pw@host/{name.split('/')[-1]}")


@pytest.fixture(autouse=True)
def _reset_fake_client_and_cache():
    _FakeSecretManagerServiceClient.construct_count = 0
    _FakeSecretManagerServiceClient.access_calls = []
    settings_module._cached_secret_value.cache_clear()
    yield
    settings_module._cached_secret_value.cache_clear()


def _install_fake_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from google.cloud import secretmanager

    monkeypatch.setattr(secretmanager, "SecretManagerServiceClient", _FakeSecretManagerServiceClient)


def test_knowledge_database_secret_resource_is_used_when_no_direct_url_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch)
    settings = Settings(env={"SLOPANOC_KNOWLEDGE_DATABASE_SECRET_RESOURCE": "projects/p/secrets/km-db-a2/versions/latest"})

    url = settings.resolve_knowledge_database_url()

    assert url == "postgresql+asyncpg://secret-user:secret-pw@host/latest"
    assert len(_FakeSecretManagerServiceClient.access_calls) == 1


def test_knowledge_database_direct_env_var_wins_over_secret_resource(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic precedence: the direct URL always wins, and the
    secret path is never even touched when it does.
    """
    _install_fake_client(monkeypatch)
    settings = Settings(
        env={
            "SLOPANOC_KNOWLEDGE_DATABASE_URL": "sqlite+aiosqlite:///./explicit.db",
            "SLOPANOC_KNOWLEDGE_DATABASE_SECRET_RESOURCE": "projects/p/secrets/km-db-a2/versions/latest",
        }
    )

    url = settings.resolve_knowledge_database_url()

    assert url == "sqlite+aiosqlite:///./explicit.db"
    assert _FakeSecretManagerServiceClient.construct_count == 0
    assert _FakeSecretManagerServiceClient.access_calls == []


def test_knowledge_database_local_default_remains_when_neither_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch)
    settings = Settings(env={})

    url = settings.resolve_knowledge_database_url()

    assert url == "sqlite+aiosqlite:///./slopanoc_knowledge.db"
    assert _FakeSecretManagerServiceClient.construct_count == 0


def test_knowledge_database_secret_resource_resolution_is_independent_of_session_secret_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setting only `SLOPANOC_DATABASE_SECRET_RESOURCE` must never be
    consulted by `resolve_knowledge_database_url()` -- the two secret
    settings are as independent as the two direct-URL settings.
    """
    _install_fake_client(monkeypatch)
    settings = Settings(env={"SLOPANOC_DATABASE_SECRET_RESOURCE": "projects/p/secrets/session-db/versions/latest"})

    url = settings.resolve_knowledge_database_url()

    assert url == "sqlite+aiosqlite:///./slopanoc_knowledge.db"
    assert _FakeSecretManagerServiceClient.construct_count == 0


def test_resolved_knowledge_secret_value_is_never_logged(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """The resolved connection string (which embeds credentials) must
    never appear in any log record produced while resolving it.
    """
    _install_fake_client(monkeypatch)
    settings = Settings(env={"SLOPANOC_KNOWLEDGE_DATABASE_SECRET_RESOURCE": "projects/p/secrets/km-db-a2/versions/latest"})

    with caplog.at_level("DEBUG"):
        url = settings.resolve_knowledge_database_url()

    assert "secret-user" not in caplog.text
    assert "secret-pw" not in caplog.text
    assert url not in caplog.text
