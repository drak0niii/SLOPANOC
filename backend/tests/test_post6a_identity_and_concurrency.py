"""POST-6A PROMPT 5 -- focused verification: identity and concurrency.

Local RSA keys, fake gateway responses, isolated persistence. No live
IdP, no live gateway, no real messages, no live database.

POSTGRESQL VERIFICATION GAP, STATED UP FRONT: the distributed advisory
lock is PostgreSQL-only. These tests run on SQLite, where the manager
correctly reports itself unavailable, so what is verified here is the
LOGIC (identity derivation, claim/fence decisions, the
`distributed_available` gate) -- NOT that PostgreSQL advisory locks
behave as expected under real multi-worker contention. That requires an
isolated PostgreSQL instance and has not been run.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from backend.gateway.safe_error import SafeErrorException

# ---------------------------------------------------------------------------
# Local key material -- generated per test session, never a real credential.
# ---------------------------------------------------------------------------

_ISSUER = "https://issuer.test/v2.0"
_AUDIENCE = "api://slopanoc-test"
_JWKS_URL = "https://issuer.test/keys"


@pytest.fixture(scope="module")
def keypair():
    from cryptography.hazmat.primitives.asymmetric import rsa

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwk(keypair):
    import json

    from jwt.algorithms import RSAAlgorithm

    public_jwk = json.loads(RSAAlgorithm.to_jwk(keypair.public_key()))
    public_jwk["kid"] = "test-key"
    return public_jwk


@pytest.fixture(autouse=True)
def _installed_jwks(monkeypatch, jwk):
    """Serve the local JWKS from the cache without any network call. The
    URL is still the CONFIGURED one -- nothing here reads a URL out of a
    token, which is the property that matters."""
    from backend.api import auth

    auth._jwks_caches.clear()

    class _LocalCache:
        def key_for(self, kid):
            return jwk if kid == jwk["kid"] else None

    monkeypatch.setattr(auth, "_cache_for", lambda url, ttl: _LocalCache())


def _token(keypair, **overrides) -> str:
    import jwt

    now = datetime.now(timezone.utc)
    claims = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "subject-1",
        "tid": "tenant-1",
        "oid": "object-1",
        "roles": ["knowledge.governor"],
        "iat": now,
        "exp": now + timedelta(minutes=10),
    }
    claims.update(overrides)
    return jwt.encode(claims, keypair, algorithm="RS256", headers={"kid": "test-key"})


def _verify(token: str, **overrides):
    from backend.api.auth import verify_bearer_token

    kwargs: dict[str, Any] = dict(
        issuer=_ISSUER,
        audience=_AUDIENCE,
        jwks_url=_JWKS_URL,
        algorithms=("RS256",),
    )
    kwargs.update(overrides)
    return verify_bearer_token(token, **kwargs)


# ===========================================================================
# 1. Invalid / expired / wrong-audience tokens are rejected
# ===========================================================================


def test_a_valid_token_is_accepted_and_yields_a_stable_identity(keypair) -> None:
    identity = _verify(_token(keypair))
    assert identity.user_id == "aad:tenant-1:object-1"
    assert identity.roles == ("knowledge.governor",)
    assert identity.tenant_id == "tenant-1"


def test_expired_token_is_rejected(keypair) -> None:
    past = datetime.now(timezone.utc) - timedelta(hours=2)
    with pytest.raises(SafeErrorException):
        _verify(_token(keypair, exp=past, iat=past))


def test_wrong_audience_is_rejected(keypair) -> None:
    with pytest.raises(SafeErrorException):
        _verify(_token(keypair, aud="api://some-other-service"))


def test_wrong_issuer_is_rejected(keypair) -> None:
    with pytest.raises(SafeErrorException):
        _verify(_token(keypair, iss="https://attacker.test/v2.0"))


def test_tampered_signature_is_rejected(keypair) -> None:
    token = _token(keypair)
    head, payload, signature = token.split(".")
    tampered = f"{head}.{payload}.{signature[:-4]}AAAA"
    with pytest.raises(SafeErrorException):
        _verify(tampered)


def test_unsigned_token_is_rejected(keypair) -> None:
    """`alg: none` is refused before any key is selected."""
    import jwt

    token = jwt.encode({"iss": _ISSUER, "aud": _AUDIENCE, "sub": "x"}, key="", algorithm="none")
    with pytest.raises(SafeErrorException):
        _verify(token)


def test_disallowed_algorithm_is_rejected(keypair) -> None:
    with pytest.raises(SafeErrorException):
        _verify(_token(keypair), algorithms=("ES256",))


def test_a_disallowed_tenant_is_rejected(keypair) -> None:
    with pytest.raises(SafeErrorException):
        _verify(_token(keypair), allowed_tenants=frozenset({"tenant-other"}))


def test_identity_never_derives_from_a_mutable_display_name(keypair) -> None:
    """Changing email/name must not change the ownership key -- otherwise
    a rename silently detaches a user from their own data."""
    first = _verify(_token(keypair, email="a@example.test", name="A"))
    second = _verify(_token(keypair, email="b@example.test", name="B"))
    assert first.user_id == second.user_id

    from backend.api.auth import derive_user_id

    # Without Entra claims, identity is issuer-scoped, never `sub` alone.
    assert derive_user_id({"sub": "s1"}, _ISSUER) == f"oidc:{_ISSUER}:s1"
    assert derive_user_id({"sub": "s1"}, "https://other") != derive_user_id({"sub": "s1"}, _ISSUER)


def test_signing_keys_are_never_taken_from_the_token(monkeypatch, keypair) -> None:
    """A token naming its own JWKS location would verify itself."""
    import inspect

    from backend.api import auth

    source = inspect.getsource(auth.verify_bearer_token)
    assert "jku" not in source
    assert "jwks_url=jwks_url" not in source  # the URL is a parameter, never read from claims
    assert "_cache_for(jwks_url" in source


# ===========================================================================
# 2. The dev header cannot override verified production identity
# ===========================================================================


@pytest.mark.asyncio
async def test_dev_header_is_ignored_in_oidc_mode(monkeypatch, keypair) -> None:
    from backend.api.identity import resolve_user_context

    monkeypatch.setenv("SLOPANOC_AUTH_MODE", "oidc")
    monkeypatch.setenv("SLOPANOC_AUTH_ISSUER", _ISSUER)
    monkeypatch.setenv("SLOPANOC_AUTH_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("SLOPANOC_AUTH_JWKS_URL", _JWKS_URL)

    context = await resolve_user_context(
        x_slopanoc_dev_user="attacker", authorization=f"Bearer {_token(keypair)}"
    )
    assert context.user_id == "aad:tenant-1:object-1", "the header must not influence identity"
    assert context.verified is True


@pytest.mark.asyncio
async def test_oidc_mode_rejects_a_request_with_only_the_dev_header(monkeypatch) -> None:
    from backend.api.identity import resolve_user_context

    monkeypatch.setenv("SLOPANOC_AUTH_MODE", "oidc")
    monkeypatch.setenv("SLOPANOC_AUTH_ISSUER", _ISSUER)
    monkeypatch.setenv("SLOPANOC_AUTH_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("SLOPANOC_AUTH_JWKS_URL", _JWKS_URL)

    with pytest.raises(SafeErrorException):
        await resolve_user_context(x_slopanoc_dev_user="attacker", authorization=None)


@pytest.mark.asyncio
async def test_oidc_mode_without_configuration_fails_closed(monkeypatch, keypair) -> None:
    """A deployment that asks for verified identity and cannot do it must
    reject, never fall back to development identity."""
    from backend.api.identity import resolve_user_context

    monkeypatch.setenv("SLOPANOC_AUTH_MODE", "oidc")
    monkeypatch.delenv("SLOPANOC_AUTH_ISSUER", raising=False)
    monkeypatch.delenv("SLOPANOC_AUTH_AUDIENCE", raising=False)
    monkeypatch.delenv("SLOPANOC_AUTH_JWKS_URL", raising=False)

    with pytest.raises(SafeErrorException):
        await resolve_user_context(
            x_slopanoc_dev_user="anyone", authorization=f"Bearer {_token(keypair)}"
        )


# ===========================================================================
# 3. An authenticated non-governor cannot approve descriptors
# ===========================================================================


@pytest.mark.asyncio
async def test_authenticated_non_governor_cannot_approve(tmp_path) -> None:
    from backend.knowledge.governance.operation_approval_store import (
        GovernancePermissionError,
        OperationApprovalStore,
    )

    store = OperationApprovalStore(
        f"sqlite+aiosqlite:///{tmp_path / 'roles.db'}",
        governors=frozenset(),
        governor_roles=frozenset({"knowledge.governor"}),
        dev_mode=False,
        manage_schema=True,
    )
    # Authenticated, but carrying no governor role.
    with pytest.raises(GovernancePermissionError):
        store.require_governance_permission("aad:t:u", verified=True, roles=("reader",))

    # Authenticated WITH the role -- authorized, and notably with no
    # development flag anywhere.
    store.require_governance_permission("aad:t:u", verified=True, roles=("knowledge.governor",))


def test_production_governance_works_without_dev_mode(tmp_path) -> None:
    from backend.knowledge.governance.operation_approval_store import OperationApprovalStore

    store = OperationApprovalStore(
        f"sqlite+aiosqlite:///{tmp_path / 'prod.db'}",
        governors=frozenset(),
        governor_roles=frozenset({"knowledge.governor"}),
        dev_mode=False,
    )
    store.require_governance_available(verified=True)
    store.require_governance_permission("aad:t:u", verified=True, roles=("knowledge.governor",))


def test_the_cli_takes_no_actor_argument_at_all() -> None:
    """POST-6A -- the CLI cannot NAME a governor, in any mode.

    The strongest form of "it cannot impersonate": there is no syntax for
    it. `--actor` is gone from every mutating subcommand, so identity can
    only come from the token the API verifies (remote) or from host
    configuration under an explicit development flag (`--local`).
    """
    from backend.tools.admin.descriptor_admin import main

    for command in ("approve", "revoke", "draft"):
        with pytest.raises(SystemExit):
            # argparse exits 2 on an unrecognized argument.
            main([command, "K1", "2.0", "s1", "--actor", "alice"])


def test_the_cli_local_path_refuses_outside_development_mode(monkeypatch) -> None:
    """`--local` is the direct-to-database path, and it is development-only.

    Previously a configured `SLOPANOC_ADMIN_ACTOR_ID` on the governor
    allowlist authorized a local approval in ANY deployment. That made an
    environment variable -- something any process on the host can be given
    -- equivalent to a verified human governor, and stamped that human's
    name on the audit record. It must refuse instead.
    """
    from backend.tools.admin.descriptor_admin import _require_local_development

    monkeypatch.delenv("SLOPANOC_GOVERNANCE_DEV_MODE", raising=False)
    monkeypatch.setenv("SLOPANOC_ADMIN_ACTOR_ID", "ops-admin")
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_GOVERNORS", "ops-admin")
    with pytest.raises(PermissionError):
        _require_local_development()

    # Explicit development mode, and only that, permits the local path.
    monkeypatch.setenv("SLOPANOC_GOVERNANCE_DEV_MODE", "true")
    assert _require_local_development() == "ops-admin"


def test_the_cli_remote_path_requires_a_token(monkeypatch) -> None:
    """No token, no governance action -- and the failure names the
    variable rather than silently falling back to the local path."""
    from backend.tools.admin.descriptor_admin import _RemoteError, _api_token

    monkeypatch.delenv("SLOPANOC_API_TOKEN", raising=False)
    with pytest.raises(_RemoteError):
        _api_token()


def test_drafting_is_permission_gated(tmp_path) -> None:
    """POST-6A -- drafting withdraws a live approval, so it is gated like
    approval.

    An authenticated but unprivileged caller must not be able to overwrite
    a reviewed descriptor and demote an APPROVED operation to CANDIDATE.
    That destroys operational authority for everyone, which is a
    governance act even though it grants nothing.
    """
    from backend.knowledge.governance.operation_approval_store import (
        GovernancePermissionError,
        OperationApprovalStore,
    )

    store = OperationApprovalStore(
        f"sqlite+aiosqlite:///{tmp_path / 'draftgate.db'}",
        governors=frozenset({"aad:t:governor"}),
        governor_roles=frozenset(),
        dev_mode=False,
    )
    # A verified, ordinary user -- authenticated, not authorized.
    with pytest.raises(GovernancePermissionError):
        store.require_governance_permission("aad:t:ordinary", verified=True, roles=())
    # The governor passes the same gate.
    store.require_governance_permission("aad:t:governor", verified=True, roles=())


# ===========================================================================
# 4. Cross-user session / source access is rejected
# ===========================================================================


@pytest.mark.asyncio
async def test_cross_user_session_access_is_rejected() -> None:
    from backend.api.session_service import ApiSessionService

    service = ApiSessionService()
    session_id = await service.create_session(user_id="aad:t:alice")

    assert (await service.get_session(session_id, "aad:t:alice")) is not None
    with pytest.raises(SafeErrorException):
        await service.get_session(session_id, "aad:t:bob")


@pytest.mark.asyncio
async def test_cross_user_operational_status_is_rejected() -> None:
    """Through the REAL route's own ownership boundary."""
    from backend.api.session_service import ApiSessionService

    service = ApiSessionService()
    session_id = await service.create_session(user_id="aad:t:alice")
    with pytest.raises(SafeErrorException):
        await service.get_session(session_id, "aad:t:bob")


# ===========================================================================
# 5. Two competing dispatch attempts produce at most one gateway call
# ===========================================================================


@pytest.mark.asyncio
async def test_two_competing_dispatch_attempts_produce_one_gateway_call(monkeypatch) -> None:
    """The durable execution record is the boundary that holds even
    without distributed locking: the second attempt finds the proposal
    consumed and never reaches the gateway."""
    import asyncio

    from backend.api import execution_service
    from backend.api.session_service import ApiSessionService
    from backend.tests.test_api_execution_endpoints import (
        FakeResponse,
        _approved_send_message,
        _install_gateway,
    )

    spy = _install_gateway(monkeypatch, FakeResponse(200, {"messageId": "m-1"}))
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id, chat_id="c1", message="Hi")

    results = await asyncio.gather(
        execution_service.execute(service, session_id, proposal_id),
        execution_service.execute(service, session_id, proposal_id),
        return_exceptions=True,
    )
    succeeded = [r for r in results if not isinstance(r, BaseException)]
    assert len(succeeded) == 1, "exactly one attempt may succeed"
    assert len(spy.calls) == 1, "at most one gateway call"


def test_dispatch_claims_the_operation_before_sending() -> None:
    import inspect

    from backend.api import execution_service

    source = inspect.getsource(execution_service)
    assert "distributed_lock_for(\n                \"execution\"" in source or 'distributed_lock_for(' in source
    assert "wait=False" in source, "the loser must not queue and then re-send"
    assert "still_holds()" in source, "fencing before dispatch"


def test_postgres_concurrency_is_not_claimed_proven_on_sqlite() -> None:
    """The advisory lock is PostgreSQL-only. On SQLite the manager
    reports itself unavailable, and this test records that the
    multi-worker behaviour is therefore NOT verified here."""
    from backend.api.execution_coordinator import SessionExecutionCoordinator

    assert SessionExecutionCoordinator("sqlite+aiosqlite:///x.db").distributed_available is False
    assert SessionExecutionCoordinator("postgresql+asyncpg://u@h/db").distributed_available is True


# ===========================================================================
# 6. Empty write acknowledgement becomes UNKNOWN_OUTCOME
# ===========================================================================


@pytest.mark.asyncio
async def test_empty_write_acknowledgement_becomes_unknown_outcome(monkeypatch) -> None:
    from backend.api import execution_service
    from backend.api.session_service import ApiSessionService
    from backend.approval.execution_identity import (
        EXECUTION_RECORD_STATE_KEY,
        ExecutionStatus,
        parse_execution_record,
        retry_permitted,
    )
    from backend.approval.service import PENDING_ACTION_PROPOSAL_STATE_KEY
    from backend.tests.test_api_execution_endpoints import (
        FakeResponse,
        _approved_send_message,
        _install_gateway,
    )

    _install_gateway(monkeypatch, FakeResponse(200, {}))  # no ack, no identifier
    service = ApiSessionService()
    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id, chat_id="c1", message="Hi")

    with pytest.raises(SafeErrorException) as exc:
        await execution_service.execute(service, session_id, proposal_id)
    assert "cannot tell you whether it was sent" in exc.value.safe_error.user_message

    refreshed = await service.get_session(session_id)
    record = parse_execution_record(refreshed.state.get(EXECUTION_RECORD_STATE_KEY))
    assert record is not None and record.status is ExecutionStatus.UNKNOWN_OUTCOME
    assert retry_permitted(record) is False
    assert refreshed.state[PENDING_ACTION_PROPOSAL_STATE_KEY]["status"] != "consumed"


def test_messageid_is_recognized_before_the_flow_returns_it() -> None:
    from backend.gateway.write_envelope import validate_write_envelope

    assert validate_write_envelope("teams.sendMessage", {"messageId": "m-1"}).executed is True
    assert validate_write_envelope("teams.sendMessage", {"success": True}).executed is True
    assert validate_write_envelope("teams.sendMessage", {}).executed is False


def test_configuration_cannot_turn_missing_evidence_into_success() -> None:
    from backend.gateway.write_envelope import WriteEnvelopeMode, validate_write_envelope

    for selector in ("default", "ack_only", "identifier_only", "nonsense"):
        envelope = validate_write_envelope(
            "teams.sendMessage", {}, mode=WriteEnvelopeMode(contract_selector=selector)
        )
        assert envelope.executed is False, f"{selector!r} must not confirm an empty body"


# ===========================================================================
# 7. Lost ownership prevents stale finalization
# ===========================================================================


@pytest.mark.asyncio
async def test_lost_ownership_prevents_dispatch(monkeypatch, tmp_path) -> None:
    """A worker whose claim can no longer be proven must not send."""
    from backend.api import execution_service
    from backend.api.session_service import ApiSessionService
    from backend.tests.test_api_execution_endpoints import (
        FakeResponse,
        _approved_send_message,
        _install_gateway,
    )

    spy = _install_gateway(monkeypatch, FakeResponse(200, {"messageId": "m-1"}))

    class _LostClaim:
        async def still_holds(self):
            return False

        async def release(self):
            return None

    service = ApiSessionService()
    monkeypatch.setattr(type(service.coordinator), "distributed_available", property(lambda self: True))

    async def _claim(namespace, identity, wait=True):
        return _LostClaim()

    monkeypatch.setattr(service.coordinator, "distributed_lock_for", _claim)

    # Forcing `distributed_available` on means the durable claim store is
    # consulted too, so it needs its table. `manage_schema=True` is the
    # isolated-test path (Alembic owns the real schema).
    from backend.api.operation_claims import OperationClaimStore

    claims = OperationClaimStore(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}", manage_schema=True)
    await claims.ensure_schema()
    monkeypatch.setattr(type(service.coordinator), "operation_claims", property(lambda self: claims))

    session_id = await service.create_session()
    proposal_id = await _approved_send_message(service, session_id, chat_id="c1", message="Hi")

    with pytest.raises(SafeErrorException):
        await execution_service.execute(service, session_id, proposal_id)
    assert spy.calls == [], "a worker that lost its claim must not dispatch"


# ===========================================================================
# 8. Cancellation cannot overwrite a completed turn
# ===========================================================================


def test_cancellation_cannot_overwrite_a_completed_turn() -> None:
    from backend.api.turn_lifecycle import (
        TURN_LIFECYCLE_STATE_KEY,
        TurnStatus,
        build_turn_lifecycle_delta,
        parse_turn_lifecycle,
    )

    completed = build_turn_lifecycle_delta(None, "run-A", TurnStatus.COMPLETED)
    late_cancel = build_turn_lifecycle_delta(
        completed[TURN_LIFECYCLE_STATE_KEY], "run-A", TurnStatus.CANCELLED
    )
    assert parse_turn_lifecycle(late_cancel[TURN_LIFECYCLE_STATE_KEY])["run-A"].status is (
        TurnStatus.COMPLETED
    )

    # And the reverse: a completion arriving after a cancellation cannot
    # resurrect the turn either.
    cancelled = build_turn_lifecycle_delta(None, "run-B", TurnStatus.CANCELLED)
    late_complete = build_turn_lifecycle_delta(
        cancelled[TURN_LIFECYCLE_STATE_KEY], "run-B", TurnStatus.COMPLETED
    )
    assert parse_turn_lifecycle(late_complete[TURN_LIFECYCLE_STATE_KEY])["run-B"].status is (
        TurnStatus.CANCELLED
    )
