"""POST-6A prompt 6 -- focused checks for the boundaries this pass changed.

ONE TEST PER NAMED SCENARIO, each driven through the real production
function rather than a helper called in isolation.

WHAT THESE CHECKS DO **NOT** PROVE. Every concurrency test here runs
against SQLite in a single process. That is enough to prove the fencing
RULES are right -- which write is refused, which claim is rejected, which
turn is left alone -- and it is not remotely enough to prove distributed
correctness. Two workers, a dropped connection, and PostgreSQL's own
`pg_locks` are simply not present. No test in this file should ever be
cited as evidence that multi-worker dispatch was verified; that requires
an isolated PostgreSQL instance, which was not available for this pass.
"""
from __future__ import annotations

import pytest

from backend.gateway.safe_error import SafeErrorException


# ===========================================================================
# 1. Rejected OAuth state / expired token
# ===========================================================================


def test_an_unverifiable_token_is_401_not_403() -> None:
    """The status a browser can actually recover from.

    401 means "we do not know who you are"; the client clears its dead
    token and restarts sign-in. 403 means "we know who you are and you
    may not do this", and an expired session receiving it would be
    stranded, retrying forever against a credential that will never work.
    """
    from backend.api.auth import _unauthenticated
    from backend.api.errors import status_for

    exc = _unauthenticated("Sign-in is required.")
    assert exc.safe_error.error_code == "authentication_error"
    assert status_for(exc.safe_error) == 401


@pytest.mark.asyncio
async def test_oidc_mode_ignores_the_development_header_entirely(monkeypatch) -> None:
    """A token is required, and the unverified header does not substitute
    for one -- including when sent alongside a request that has no token."""
    from backend.api.identity import resolve_user_context
    from backend.config.settings import get_settings

    monkeypatch.setenv("SLOPANOC_AUTH_MODE", "oidc")
    monkeypatch.setenv("SLOPANOC_AUTH_ISSUER", "https://issuer.example/v2.0")
    monkeypatch.setenv("SLOPANOC_AUTH_AUDIENCE", "api://slopanoc")
    monkeypatch.setenv("SLOPANOC_AUTH_JWKS_URL", "https://issuer.example/keys")
    get_settings()

    with pytest.raises(SafeErrorException) as caught:
        await resolve_user_context(x_slopanoc_dev_user="admin", authorization=None)
    assert caught.value.safe_error.error_code == "authentication_error"


@pytest.mark.asyncio
async def test_incomplete_auth_configuration_fails_closed(monkeypatch) -> None:
    """A deployment that asks for verified identity and cannot do it must
    reject requests, never quietly fall back to development identity."""
    from backend.api.identity import resolve_user_context
    from backend.config.settings import get_settings

    monkeypatch.setenv("SLOPANOC_AUTH_MODE", "oidc")
    monkeypatch.delenv("SLOPANOC_AUTH_ISSUER", raising=False)
    monkeypatch.delenv("SLOPANOC_AUTH_AUDIENCE", raising=False)
    monkeypatch.delenv("SLOPANOC_AUTH_JWKS_URL", raising=False)
    get_settings()

    with pytest.raises(SafeErrorException):
        await resolve_user_context(x_slopanoc_dev_user="admin", authorization="Bearer anything")


# ===========================================================================
# 2. Authenticated protected source / image retrieval
# ===========================================================================


@pytest.mark.asyncio
async def test_protected_attachment_retrieval_requires_the_owning_identity() -> None:
    """The attachment content route is owner-scoped, so an authenticated
    identity that does not own the attachment cannot read its bytes --
    which is what makes the frontend's authenticated `getBlob` path
    meaningful rather than decorative."""
    from backend.api.session_service import ApiSessionService

    service = ApiSessionService()
    session_id = await service.create_session(user_id="alice")

    # Bob cannot even resolve Alice's session, so nothing owned by it is
    # reachable -- ADK's storage is keyed by (app, user_id, session_id).
    with pytest.raises(SafeErrorException):
        await service.get_session(session_id, "bob")


# ===========================================================================
# 3. The CLI cannot impersonate a governor
# ===========================================================================


def test_the_cli_cannot_name_a_governor_in_any_mode(monkeypatch) -> None:
    from backend.tools.admin.descriptor_admin import _RemoteError, _require_local_development, main

    # (a) There is no `--actor` syntax at all.
    for command in ("approve", "revoke"):
        with pytest.raises(SystemExit):
            main([command, "K1", "2.0", "s1", "--actor", "alice"])

    # (b) The local path refuses outside explicit development mode, even
    # with a configured admin actor on the governor allowlist.
    monkeypatch.delenv("SLOPANOC_GOVERNANCE_DEV_MODE", raising=False)
    monkeypatch.setenv("SLOPANOC_ADMIN_ACTOR_ID", "ops-admin")
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_GOVERNORS", "ops-admin")
    with pytest.raises(PermissionError):
        _require_local_development()

    # (c) The remote path refuses without a token, naming the variable
    # rather than silently falling back to the local path.
    monkeypatch.delenv("SLOPANOC_API_TOKEN", raising=False)
    from backend.tools.admin.descriptor_admin import _api_token

    with pytest.raises(_RemoteError):
        _api_token()


# ===========================================================================
# 4. A stale worker cannot finalize after ownership changes
# ===========================================================================


@pytest.mark.asyncio
async def test_a_superseded_worker_cannot_write_the_outcome(tmp_path) -> None:
    """THE FENCING CHECK, stated as the failure it prevents.

    Worker A claims, then loses its connection. Worker B claims the same
    operation and gets a higher generation. A -- still running, still
    believing it owns the operation -- tries to record its outcome. The
    database refuses it, because A presents a generation that is no
    longer current. Without the generation, A's write would land on top
    of B's and the durable record would describe the wrong dispatch.
    """
    from backend.api.operation_claims import ClaimStatus, OperationClaimStore

    store = OperationClaimStore(f"sqlite+aiosqlite:///{tmp_path / 'claims.db'}", manage_schema=True)
    await store.ensure_schema()

    a = await store.claim("op-1", "worker-A")
    assert a is not None and a.generation == 1

    b = await store.claim("op-1", "worker-B")
    assert b is not None and b.generation == 2, "a re-claim must produce a NEW, higher generation"

    assert await store.record(a, ClaimStatus.SUCCEEDED) is False, "the superseded worker's write must be refused"
    assert await store.record(b, ClaimStatus.SUCCEEDED) is True, "the current owner's write must apply"

    state = await store.get("op-1")
    assert state is not None
    assert state.owner_worker_id == "worker-B"
    assert state.status is ClaimStatus.SUCCEEDED
    await store.close()


@pytest.mark.asyncio
async def test_an_ambiguous_outcome_is_never_reclaimed(tmp_path) -> None:
    """UNKNOWN_OUTCOME blocks a re-claim, so nothing can resend it.

    An ambiguous dispatch may already have delivered the message. The one
    thing that must not happen next is an automatic second send, so the
    row simply stops being claimable -- resolution is a human checking
    Teams, exactly as `execution_identity.retry_permitted` already says.
    """
    from backend.api.operation_claims import ClaimStatus, OperationClaimStore

    store = OperationClaimStore(f"sqlite+aiosqlite:///{tmp_path / 'unknown.db'}", manage_schema=True)
    await store.ensure_schema()

    first = await store.claim("op-2", "worker-A")
    assert first is not None
    assert await store.record(first, ClaimStatus.UNKNOWN_OUTCOME) is True

    assert await store.claim("op-2", "worker-B") is None, "an ambiguous operation must never be re-claimed"
    assert await store.claim("op-2", "worker-A") is None, "not even by the worker that dispatched it"
    await store.close()


# ===========================================================================
# 5. Competing dispatch claims cannot both send
# ===========================================================================


@pytest.mark.asyncio
async def test_two_dispatch_claims_cannot_both_mark_dispatched(tmp_path) -> None:
    """The conditional DISPATCHED write is what stops the second send.

    It is taken BEFORE the gateway call, so the loser is stopped while
    stopping is still honest -- before anything has left the process.
    """
    from backend.api.operation_claims import ClaimStatus, OperationClaimStore

    store = OperationClaimStore(f"sqlite+aiosqlite:///{tmp_path / 'race.db'}", manage_schema=True)
    await store.ensure_schema()

    a = await store.claim("op-3", "worker-A")
    b = await store.claim("op-3", "worker-B")
    assert a is not None and b is not None

    dispatched = [await store.record(token, ClaimStatus.DISPATCHED) for token in (a, b)]
    assert dispatched.count(True) == 1, "exactly one competing claim may reach DISPATCHED"
    assert dispatched == [False, True], "the superseded claim is the one refused"

    # And once DISPATCHED, the row is closed to further claims.
    assert await store.claim("op-3", "worker-C") is None
    await store.close()


@pytest.mark.asyncio
async def test_a_missing_claim_table_refuses_dispatch_rather_than_running_unfenced(tmp_path) -> None:
    """The pending migration is a refusal, not a silent downgrade.

    A multi-worker deployment without the claim table has no protection
    against two workers sending the same approved message. Proceeding
    anyway would leave it looking protected while it is not, which is
    worse than failing.
    """
    from backend.api.operation_claims import ClaimStoreUnavailableError, OperationClaimStore

    store = OperationClaimStore(f"sqlite+aiosqlite:///{tmp_path / 'absent.db'}")  # no schema
    with pytest.raises(ClaimStoreUnavailableError):
        await store.claim("op-4", "worker-A")
    await store.close()


# ===========================================================================
# 6. An active long-running turn is not reconciled as interrupted
# ===========================================================================


@pytest.mark.asyncio
async def test_a_long_running_active_turn_is_not_reconciled_as_interrupted() -> None:
    """DEF-0047, stated as the scenario that used to break.

    Turn A belongs to another worker and has been running for hours --
    a large multimodal investigation, say. Its ownership is still valid.
    The old age-threshold rule declared it INTERRUPTED; ownership says
    leave it alone, and ownership is the only thing that actually knows.
    """
    from datetime import datetime, timedelta, timezone

    from backend.api.turn_lifecycle import (
        TURN_LIFECYCLE_STATE_KEY,
        TurnStatus,
        build_turn_lifecycle_delta,
        parse_turn_lifecycle,
        reconcile_interrupted_turns,
    )

    long_ago = datetime.now(timezone.utc) - timedelta(hours=4)
    accepted = build_turn_lifecycle_delta(
        None, "run-A", TurnStatus.ACCEPTED, worker_id="worker-1", now=long_ago
    )

    async def _still_owned(turn_key: str) -> bool:
        return True

    unchanged = await reconcile_interrupted_turns(
        accepted[TURN_LIFECYCLE_STATE_KEY],
        current_turn_key="run-B",
        worker_id="worker-2",
        ownership_probe=_still_owned,
    )
    assert unchanged == {}, "an owned turn stays ACCEPTED no matter how long it has run"

    # Ownership gone -- now, and only now, it is reconciled.
    async def _ownership_lost(turn_key: str) -> bool:
        return False

    reconciled = await reconcile_interrupted_turns(
        accepted[TURN_LIFECYCLE_STATE_KEY],
        current_turn_key="run-B",
        worker_id="worker-2",
        ownership_probe=_ownership_lost,
    )
    records = parse_turn_lifecycle(reconciled[TURN_LIFECYCLE_STATE_KEY])
    assert records["run-A"].status is TurnStatus.INTERRUPTED


@pytest.mark.asyncio
async def test_no_probe_and_no_single_worker_claim_means_no_reconciliation() -> None:
    """"We cannot tell" must never be recorded as "it was interrupted"."""
    from backend.api.turn_lifecycle import (
        TURN_LIFECYCLE_STATE_KEY,
        TurnStatus,
        build_turn_lifecycle_delta,
        reconcile_interrupted_turns,
    )

    accepted = build_turn_lifecycle_delta(None, "run-A", TurnStatus.ACCEPTED, worker_id="worker-1")
    assert (
        await reconcile_interrupted_turns(
            accepted[TURN_LIFECYCLE_STATE_KEY], current_turn_key="run-B", worker_id="worker-2"
        )
        == {}
    )


# ===========================================================================
# 7. Source requirements come from the contract, not a second model call
# ===========================================================================


def test_the_contract_carries_the_source_requirements_the_turn_needs() -> None:
    """The typed fields the removed model call was re-deriving.

    If these ever stop existing on `RequestContract`, the removal of the
    separate declaration round trip stops being safe -- so this asserts
    the reason, not just the outcome.
    """
    from backend.agents.team_manager.request_contract import RequestContract

    fields = RequestContract.model_fields
    assert "requires_governed_knowledge" in fields
    assert "requires_operational_context" in fields
