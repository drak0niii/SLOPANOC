"""POST-6A -- Durable ownership generations for dispatchable operations.

WHY AN ADVISORY LOCK IS NOT ENOUGH ON ITS OWN. A PostgreSQL session
advisory lock is excellent at LIVENESS: the database drops it the instant
the holding connection dies, with no lease, no timer and no renewal
window to get wrong. What it cannot do is stop a worker that has already
passed its own ownership check. The sequence that breaks a lock-only
design:

    worker A takes the lock, calls `still_holds()` -> True
    A's connection drops; PostgreSQL releases the lock
    worker B takes the lock, dispatches, records the outcome
    A -- still running, still believing it owns the operation -- writes
      its own outcome over B's

`still_holds()` narrows that window but cannot close it: any check is a
point in time, and the work happens after it. Closing it needs a FENCING
TOKEN -- a value that increases every time ownership changes hands, which
every subsequent write must present, so a write from a superseded owner
is rejected by the storage layer rather than by the owner's own honesty.
That is what `generation` is here.

THE PROTOCOL, and why each step is in this order:

  1. `claim()` -- ONE atomic statement takes ownership and bumps the
     generation, returning the new value. Atomic because it is a single
     `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`; two workers racing
     it produce two different generations, never the same one.
     It refuses outright when the row is not `PREPARED` -- an operation
     that is already DISPATCHED, SUCCEEDED, FAILED or UNKNOWN_OUTCOME is
     never re-claimed, so this mechanism never causes a resend.
  2. `record(..., generation=...)` -- a CONDITIONAL write: it applies only
     while the stored generation still matches the caller's. A superseded
     worker's write matches nothing, changes nothing, and returns `False`.
     That `False` is the signal to abort rather than publish.
  3. The caller marks DISPATCHED **before** the gateway call, conditional
     on its generation. So a fenced-out worker is stopped BEFORE anything
     leaves the process -- the only place it can be stopped honestly.

WHAT THIS DOES NOT CLAIM. It is not exactly-once delivery. Between the
conditional DISPATCHED write and the gateway's own acceptance there is
still a real window, and no amount of local bookkeeping closes it --
only the gateway deduplicating on an idempotency key would, and Power
Automate offers none. What this guarantees is narrower and true: an
operation is DISPATCHED at most once by a worker that could still prove
ownership, a superseded worker cannot overwrite the outcome, and an
ambiguous dispatch stays UNKNOWN_OUTCOME and is never automatically
retried.

PORTABILITY, HONESTLY STATED. The SQL here is the same on PostgreSQL and
SQLite (both support `ON CONFLICT ... DO UPDATE ... RETURNING`), so the
LOGIC is testable on SQLite. Distributed behaviour is not: a SQLite file
opened by one process proves nothing about two workers. A passing SQLite
test here means the fencing rules are right, never that concurrency was
proven.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

_logger = logging.getLogger(__name__)

__all__ = [
    "CLAIM_TABLE_NAME",
    "ClaimStatus",
    "ClaimStoreUnavailableError",
    "ClaimToken",
    "OperationClaimStore",
    "claim_metadata",
]


class ClaimStoreUnavailableError(RuntimeError):
    """The durable claim store could not be reached or does not exist.

    RAISED, NEVER SWALLOWED. Fencing is a safety boundary, and a safety
    boundary that quietly disappears when its storage is missing is worse
    than no boundary at all -- the deployment looks protected and is not.
    The most likely cause by far is a pending migration (`b7c4e1a95d60`),
    which is an operator action with an obvious fix, so the caller turns
    this into a refusal to dispatch rather than an unfenced dispatch.
    """


CLAIM_TABLE_NAME = "slopanoc_operation_claims"


class ClaimStatus(str, Enum):
    """Deliberately the same vocabulary as
    `backend.approval.execution_identity.ExecutionStatus`.

    Two vocabularies for one lifecycle would drift, and the durable row
    and the session-state record describe the same operation from two
    sides. UNKNOWN_OUTCOME is carried here for the same reason it exists
    there: an ambiguous dispatch is not a failure, and must never be
    silently turned into one (which would invite a resend).
    """

    PREPARED = "prepared"
    DISPATCHED = "dispatched"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN_OUTCOME = "unknown_outcome"


_CLAIMABLE = ClaimStatus.PREPARED.value
"""The ONLY status a claim may be taken from. Everything else means the
operation has already left this process at least once."""


claim_metadata = MetaData()

OperationClaimTable = Table(
    CLAIM_TABLE_NAME,
    claim_metadata,
    Column("operation_id", String(128), primary_key=True),
    Column("owner_worker_id", String(255), nullable=False),
    Column("generation", Integer, nullable=False),
    Column("status", String(32), nullable=False),
    Column("claimed_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


@dataclass(frozen=True)
class ClaimToken:
    """Proof of ownership at a point in time.

    Frozen on purpose: the generation is the whole value of this object.
    A mutable token could be "refreshed" in place by a worker that wanted
    its stale write to succeed, which is precisely the thing fencing
    exists to prevent.
    """

    operation_id: str
    owner_worker_id: str
    generation: int


@dataclass(frozen=True)
class ClaimState:
    operation_id: str
    owner_worker_id: str
    generation: int
    status: ClaimStatus
    updated_at: Optional[datetime] = None


class OperationClaimStore:
    """Durable claims for one database URL."""

    def __init__(self, database_url: str, *, manage_schema: bool = False) -> None:
        self._database_url = database_url
        self._engine: Optional[AsyncEngine] = None
        # NO LAZY DDL IN NORMAL RUNTIME -- schema ownership belongs to
        # Alembic (revision `b7c4e1a95d60`), matching
        # `OperationApprovalStore`. `manage_schema=True` is for isolated
        # tests and one-off local setup, where creating the table
        # in-process is the point.
        self._manage_schema = manage_schema
        self._schema_ready = not manage_schema

    def _get_engine(self) -> AsyncEngine:
        if self._engine is None:
            # AUTOCOMMIT: every statement below is individually atomic and
            # complete. No transaction is opened here, and therefore none
            # can be left open across a gateway or model call.
            self._engine = create_async_engine(
                self._database_url, future=True, isolation_level="AUTOCOMMIT", pool_pre_ping=True
            )
        return self._engine

    async def ensure_schema(self) -> None:
        if self._schema_ready or not self._manage_schema:
            return
        engine = create_async_engine(self._database_url, future=True)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(claim_metadata.create_all)
        finally:
            await engine.dispose()
        self._schema_ready = True

    async def claim(self, operation_id: str, worker_id: str) -> Optional[ClaimToken]:
        """Take ownership and return the new fencing generation, or
        `None` when this operation must not be dispatched by this worker.

        ONE STATEMENT. The insert-or-bump is a single
        `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`, so there is no
        read-then-write gap for a second worker to slip into. The
        `WHERE status = 'prepared'` guard on the conflict branch is what
        makes an already-dispatched operation unclaimable: the update
        matches no row, nothing is returned, and the caller stops.
        """
        now = datetime.now(timezone.utc)
        statement = text(
            f"INSERT INTO {CLAIM_TABLE_NAME} "
            "(operation_id, owner_worker_id, generation, status, claimed_at, updated_at) "
            "VALUES (:operation_id, :worker_id, 1, :prepared, :now, :now) "
            "ON CONFLICT (operation_id) DO UPDATE SET "
            "owner_worker_id = excluded.owner_worker_id, "
            f"generation = {CLAIM_TABLE_NAME}.generation + 1, "
            "updated_at = excluded.updated_at "
            f"WHERE {CLAIM_TABLE_NAME}.status = :prepared "
            "RETURNING generation"
        )
        try:
            async with self._get_engine().connect() as connection:
                result = await connection.execute(
                    statement,
                    {"operation_id": operation_id, "worker_id": worker_id, "prepared": _CLAIMABLE, "now": now},
                )
                row = result.first()
        except SQLAlchemyError as exc:
            raise ClaimStoreUnavailableError(str(exc)) from exc
        if row is None:
            return None
        return ClaimToken(operation_id=operation_id, owner_worker_id=worker_id, generation=int(row[0]))

    async def record(self, token: ClaimToken, status: ClaimStatus) -> bool:
        """THE CONDITIONAL WRITE. `True` only if this worker still owns
        the operation at the generation it was handed.

        A `False` return is not a storage error -- it is the fencing
        mechanism reporting that ownership moved on. The caller must treat
        it as "I am no longer the owner; do not publish, do not persist,
        do not dispatch", never as "retry the write".
        """
        now = datetime.now(timezone.utc)
        statement = text(
            f"UPDATE {CLAIM_TABLE_NAME} SET status = :status, updated_at = :now "
            "WHERE operation_id = :operation_id AND generation = :generation "
            "AND owner_worker_id = :worker_id"
        )
        try:
            async with self._get_engine().connect() as connection:
                result = await connection.execute(
                    statement,
                    {
                        "status": status.value,
                        "now": now,
                        "operation_id": token.operation_id,
                        "generation": token.generation,
                        "worker_id": token.owner_worker_id,
                    },
                )
        except SQLAlchemyError:
            # A write we could not perform is a write that did not happen.
            # Reporting `False` -- "you are no longer the owner, do not
            # publish" -- is the fail-closed reading, and the only one
            # that cannot cause a stale overwrite.
            _logger.warning(
                "operation_claims: conditional write failed for operation_id=%s", token.operation_id,
                exc_info=True,
            )
            return False
        return bool(result.rowcount == 1)

    async def get(self, operation_id: str) -> Optional[ClaimState]:
        statement = text(
            "SELECT operation_id, owner_worker_id, generation, status, updated_at "
            f"FROM {CLAIM_TABLE_NAME} WHERE operation_id = :operation_id"
        )
        async with self._get_engine().connect() as connection:
            row = (await connection.execute(statement, {"operation_id": operation_id})).first()
        if row is None:
            return None
        try:
            status = ClaimStatus(row[3])
        except ValueError:
            # An unrecognized stored status is treated as UNKNOWN_OUTCOME,
            # never as a success and never as claimable -- the fail-closed
            # reading of a value we cannot interpret.
            status = ClaimStatus.UNKNOWN_OUTCOME
        return ClaimState(
            operation_id=row[0],
            owner_worker_id=row[1],
            generation=int(row[2]),
            status=status,
            updated_at=row[4],
        )

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
