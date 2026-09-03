"""Tests for backend/api/identity.py -- the development-only identity
provider and the `UserContext` abstraction it centralizes.
"""
from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from backend.api.app import app
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.identity import DEV_USER_HEADER_NAME, UserContext, resolve_user_context
from backend.api.session_service import ApiSessionService, get_session_service
from backend.tests._api_fakes import FakeRunner


@pytest.mark.asyncio
async def test_dev_header_resolves_to_the_given_user_id() -> None:
    context = await resolve_user_context(x_slopanoc_dev_user="alice")
    assert context.user_id == "alice"


@pytest.mark.asyncio
async def test_missing_dev_header_resolves_to_a_stable_default() -> None:
    context = await resolve_user_context(x_slopanoc_dev_user=None)
    assert context.user_id  # non-empty
    # Stable across repeated resolution with no header, so unauthenticated
    # local testing behaves consistently rather than getting a fresh
    # identity every call.
    context2 = await resolve_user_context(x_slopanoc_dev_user=None)
    assert context.user_id == context2.user_id


@pytest.mark.asyncio
async def test_whitespace_only_header_falls_back_to_the_default() -> None:
    default_context = await resolve_user_context(x_slopanoc_dev_user=None)
    whitespace_context = await resolve_user_context(x_slopanoc_dev_user="   ")
    assert whitespace_context.user_id == default_context.user_id


@pytest.mark.asyncio
async def test_distinct_dev_users_resolve_independently() -> None:
    alice = await resolve_user_context(x_slopanoc_dev_user="alice")
    bob = await resolve_user_context(x_slopanoc_dev_user="bob")
    assert alice.user_id != bob.user_id


@pytest.mark.asyncio
async def test_same_user_identity_is_stable_across_repeated_resolution() -> None:
    """Simulates "the same user makes several requests" -- each resolution
    call is independent (no session/cookie state in this dev provider),
    but the SAME header value always yields the SAME `user_id`.
    """
    first = await resolve_user_context(x_slopanoc_dev_user="alice")
    second = await resolve_user_context(x_slopanoc_dev_user="alice")
    third = await resolve_user_context(x_slopanoc_dev_user="alice")
    assert first.user_id == second.user_id == third.user_id == "alice"


def test_user_context_is_minimal() -> None:
    """Instruction: "Do not over-engineer roles/permissions yet." """
    import dataclasses

    fields = {f.name for f in dataclasses.fields(UserContext)}
    assert fields == {"user_id"}


def test_identity_resolution_is_centralized_in_one_dependency() -> None:
    """A future production identity provider only needs to replace
    `resolve_user_context` (same `UserContext` return type, same FastAPI
    dependency-injection point) -- confirmed here by checking that all
    session-bound routes use the exact same dependency callable.
    """
    from backend.api.app import app as fastapi_app

    session_bound_paths = {
        "/api/sessions",
        "/api/sessions/{session_id}/messages",
        "/api/sessions/{session_id}/approve",
        "/api/sessions/{session_id}/reject",
    }
    found_paths = set()
    for route in fastapi_app.routes:
        path = getattr(route, "path", None)
        if path not in session_bound_paths:
            continue
        dependant = route.dependant
        dependency_callables = {d.call for d in dependant.dependencies}
        assert resolve_user_context in dependency_callables, f"{path} does not resolve UserContext"
        found_paths.add(path)
    assert found_paths == session_bound_paths


def test_resolve_user_context_reads_only_the_dev_header_no_body_no_query() -> None:
    """Structural guarantee that identity cannot come from anywhere else
    -- the function's only parameter is the dev header.
    """
    params = inspect.signature(resolve_user_context).parameters
    assert list(params) == ["x_slopanoc_dev_user"]
    param = params["x_slopanoc_dev_user"]
    # FastAPI's `Header(default=None)` -- optional, falls back to the
    # default identity when the header is absent.
    assert param.default.default is None


# --- End-to-end: identity actually drives session ownership over HTTP ---


@pytest.fixture()
def session_service() -> ApiSessionService:
    return ApiSessionService()


@pytest.fixture()
def client(session_service: ApiSessionService) -> TestClient:
    app.dependency_overrides[get_session_service] = lambda: session_service
    app.dependency_overrides[get_chat_service] = lambda: ChatService(session_service, runner=FakeRunner(session_service))
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_two_dev_users_creating_sessions_get_independently_owned_sessions(client: TestClient) -> None:
    alice_session = client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: "alice"}).json()["session_id"]
    bob_session = client.post("/api/sessions", headers={DEV_USER_HEADER_NAME: "bob"}).json()["session_id"]

    assert alice_session != bob_session

    # Alice can use her own session.
    alice_chat = client.post(
        f"/api/sessions/{alice_session}/messages",
        json={"message": "hi"},
        headers={DEV_USER_HEADER_NAME: "alice"},
    )
    assert alice_chat.status_code == 200

    # Bob cannot use Alice's session id.
    bob_using_alice = client.post(
        f"/api/sessions/{alice_session}/messages",
        json={"message": "hi"},
        headers={DEV_USER_HEADER_NAME: "bob"},
    )
    assert bob_using_alice.status_code == 404


def test_requests_without_the_dev_header_consistently_use_the_default_identity(client: TestClient) -> None:
    session_id = client.post("/api/sessions").json()["session_id"]  # no header

    response = client.post(f"/api/sessions/{session_id}/messages", json={"message": "hi"})  # still no header

    assert response.status_code == 200
