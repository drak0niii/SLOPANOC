"""Tests for the Secret Manager fetch caching added in the pre-4H latency
investigation pass (backend/config/settings.py's `_cached_secret_value`).

Never exercises the real GCP Secret Manager -- `SecretManagerServiceClient`
is monkeypatched with a call-counting fake throughout.
"""
from __future__ import annotations

import pytest

from backend.config import settings as settings_module


class _FakeSecretPayload:
    def __init__(self, value: str) -> None:
        self.data = value.encode("utf-8")


class _FakeAccessSecretVersionResponse:
    def __init__(self, value: str) -> None:
        self.payload = _FakeSecretPayload(value)


class _FakeSecretManagerServiceClient:
    """Tracks how many times a real client would have been constructed
    and how many times a network call would have been made, per fake
    secret resource name.
    """

    construct_count = 0

    def __init__(self) -> None:
        _FakeSecretManagerServiceClient.construct_count += 1

    def access_secret_version(self, name: str):  # noqa: D401
        _FakeSecretManagerServiceClient.access_calls.append(name)
        return _FakeAccessSecretVersionResponse(f"secret-value-for-{name}")

    access_calls: list = []


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


def test_first_fetch_calls_the_real_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch)

    value = settings_module._fetch_secret_from_secret_manager("projects/p/secrets/s/versions/latest")

    assert value == "secret-value-for-projects/p/secrets/s/versions/latest"
    assert _FakeSecretManagerServiceClient.construct_count == 1
    assert len(_FakeSecretManagerServiceClient.access_calls) == 1


def test_repeated_fetches_for_the_same_resource_hit_the_cache_not_the_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_client(monkeypatch)
    resource = "projects/p/secrets/s/versions/latest"

    for _ in range(5):
        settings_module._fetch_secret_from_secret_manager(resource)

    # Exactly one real client construction and one real network call for
    # 5 logical fetches -- this is the fix for "every Teams operation
    # re-fetched the gateway URL from Secret Manager."
    assert _FakeSecretManagerServiceClient.construct_count == 1
    assert len(_FakeSecretManagerServiceClient.access_calls) == 1


def test_different_resource_names_are_cached_independently(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_client(monkeypatch)

    a = settings_module._fetch_secret_from_secret_manager("projects/p/secrets/a/versions/latest")
    b = settings_module._fetch_secret_from_secret_manager("projects/p/secrets/b/versions/latest")

    assert a != b
    assert _FakeSecretManagerServiceClient.construct_count == 2


def test_the_env_var_gateway_url_path_never_touches_the_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The direct-env-var path (what the automated test suite always
    uses -- see conftest.py's autouse fixture) is completely unaffected
    by this caching -- it never calls `_fetch_secret_from_secret_manager`
    at all, so `monkeypatch.setenv` changes are still observed live."""
    monkeypatch.setenv("SLOPANOC_POWER_AUTOMATE_GATEWAY_URL", "https://example.invalid/first")
    monkeypatch.delenv("SLOPANOC_POWER_AUTOMATE_SECRET_RESOURCE", raising=False)
    settings = settings_module.Settings()
    assert settings.resolve_power_automate_gateway_url() == "https://example.invalid/first"

    monkeypatch.setenv("SLOPANOC_POWER_AUTOMATE_GATEWAY_URL", "https://example.invalid/second")
    assert settings.resolve_power_automate_gateway_url() == "https://example.invalid/second"
