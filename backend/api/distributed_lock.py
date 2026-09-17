"""POST-6A -- PostgreSQL session advisory locks for multi-worker safety.

THE GAP THIS CLOSES: `SessionExecutionCoordinator` holds one
`asyncio.Lock` per session, scoped to ONE Python process. With more than
one worker, two requests for the same session land on two processes with
two independent lock maps and serialize against nothing. Everything that
depends on a whole turn or a whole approve/dispatch being atomic --
turns, cancellation, approval/rejection, dispatch -- is unprotected the
moment the deployment scales past one process.

WHY ADVISORY LOCKS, NOT LEASES: a lease needs expiry, renewal and
fencing, and every one of those is a new way to be subtly wrong (a
renewal that misses its window silently hands the lock to a second
holder while the first is still working). A PostgreSQL SESSION advisory
lock is released by the database itself the instant the holding
connection goes away -- crash, network drop, process kill -- so the
failure mode that matters most needs no timer at all.

THE TWO THINGS THAT MUST BE HANDLED EXPLICITLY, and are:

  CONNECTION OWNERSHIP -- a session advisory lock belongs to a
  CONNECTION, not a transaction or a pool. So one dedicated connection is
  held for the lifetime of the lock and released with it. Taking the lock
  on a pooled connection and returning it to the pool would leave the
  lock held by whatever used that connection next.

  LOSS OF THE CONNECTION -- if the connection dies, the lock is gone and
  this process no longer owns anything, even though its Python code is
  still running. `still_holds()` re-asserts ownership against the
  database, and every critical section re-checks it before publishing or
  persisting. A holder that cannot prove it still holds the lock must not
  act.

NO TRANSACTION IS HELD OPEN ACROSS A MODEL OR GATEWAY CALL. The lock is
acquired with `pg_advisory_lock` in autocommit; the connection sits idle
(not idle-in-transaction) while the turn runs. Holding a transaction open
across a Gemini or Power Automate call would pin a database connection
for the length of a network round trip and block vacuum.

DEGRADES HONESTLY. Against a non-PostgreSQL URL (SQLite in tests/local
dev) this reports itself unavailable and the caller keeps the existing
process-local lock -- which is correct for a single process and is
exactly what those environments are. It never pretends to provide
distributed mutual exclusion it cannot.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Optional

_logger = logging.getLogger(__name__)

__all__ = ["AdvisoryLock", "AdvisoryLockManager", "advisory_key"]


def advisory_key(namespace: str, identity: str) -> tuple[int, int]:
    """A stable `(classid, objid)` pair for PostgreSQL's two-int advisory
    lock space.

    Derived from a hash so any string identity maps deterministically to
    the same pair in every process and across restarts. Signed 32-bit
    range, because that is what `pg_advisory_lock(int, int)` takes.
    """
    digest = hashlib.sha256(f"{namespace}\x1f{identity}".encode("utf-8")).digest()
    classid = int.from_bytes(digest[:4], "big", signed=True)
    objid = int.from_bytes(digest[4:8], "big", signed=True)
    return classid, objid


class AdvisoryLock:
    """One held lock, bound to one dedicated connection."""

    def __init__(self, connection, key: tuple[int, int], manager: "AdvisoryLockManager") -> None:
        self._connection = connection
        self._key = key
        self._manager = manager
        self._released = False

    async def still_holds(self) -> bool:
        """Re-assert ownership against the database.

        THE FENCING CHECK. A process whose connection dropped is no
        longer the holder even though its own code is still running --
        `pg_locks` is the authority, not this object's existence. Any
        error answering the question is treated as "no": a holder that
        cannot prove it holds must not act.
        """
        if self._released:
            return False
        try:
            from sqlalchemy.sql import text

            result = await self._connection.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND classid = :classid AND objid = :objid AND pid = pg_backend_pid() AND granted"
                ),
                {"classid": self._key[0], "objid": self._key[1]},
            )
            return bool(result.scalar())
        except Exception:
            _logger.warning("advisory lock: ownership could not be confirmed -- treating as lost", exc_info=True)
            return False

    async def release(self) -> None:
        """POST-6A -- SHIELDED, so cancellation cannot leak a connection.

        This is almost always awaited from a `finally`, and a `finally`
        reached by cancellation is itself running inside a cancelled
        task: the very next `await` would raise `CancelledError` again
        and abandon the connection mid-unlock. A connection abandoned
        while holding a session advisory lock keeps that lock until the
        database eventually reaps the backend, which blocks every other
        worker from the same operation for no reason at all.

        `asyncio.shield` lets the unlock-and-close finish even as the
        surrounding task is torn down. The `CancelledError` is then
        re-raised as normal -- shielding the cleanup does not swallow the
        cancellation, it only refuses to abandon the resource.
        """
        if self._released:
            return
        self._released = True
        await asyncio.shield(self._manager._release(self._connection, self._key))


class AdvisoryLockManager:
    """Creates and releases advisory locks on dedicated connections."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._engine = None

    @property
    def available(self) -> bool:
        """`True` only for PostgreSQL. Everything else keeps the existing
        process-local lock, which is genuinely correct there."""
        return self._database_url.startswith("postgresql")

    def _get_engine(self):
        if self._engine is None:
            from sqlalchemy.ext.asyncio import create_async_engine

            # AUTOCOMMIT: the lock is a session-level object and must not
            # sit inside an open transaction while a model/gateway call
            # runs -- see this module's docstring.
            self._engine = create_async_engine(
                self._database_url, future=True, isolation_level="AUTOCOMMIT", pool_pre_ping=True
            )
        return self._engine

    async def acquire(self, namespace: str, identity: str, *, wait: bool = True) -> Optional[AdvisoryLock]:
        """Take the lock, or return `None`.

        `wait=False` uses `pg_try_advisory_lock`, which is what dispatch
        needs: a second worker that cannot take the lock must NOT queue
        behind the first and then send the same message again -- it must
        find out it lost and stop.
        """
        if not self.available:
            return None
        from sqlalchemy.sql import text

        key = advisory_key(namespace, identity)
        connection = await self._get_engine().connect()
        try:
            function = "pg_advisory_lock" if wait else "pg_try_advisory_lock"
            result = await connection.execute(
                text(f"SELECT {function}(:classid, :objid)"),
                {"classid": key[0], "objid": key[1]},
            )
            acquired = True if wait else bool(result.scalar())
            if not acquired:
                await asyncio.shield(connection.close())
                return None
            return AdvisoryLock(connection, key, self)
        except BaseException:
            # POST-6A -- `BaseException`, NOT `Exception`. Since Python
            # 3.8 `asyncio.CancelledError` derives from `BaseException`,
            # so an `except Exception` here would let a cancellation
            # during `pg_advisory_lock` walk out of this function leaving
            # a checked-out connection that may ALREADY hold the lock --
            # the exact "returned a connection holding an advisory lock"
            # failure this module exists to prevent. Shielded for the
            # same reason `release` is.
            await asyncio.shield(connection.close())
            raise

    async def _release(self, connection, key: tuple[int, int]) -> None:
        from sqlalchemy.sql import text

        try:
            await connection.execute(
                text("SELECT pg_advisory_unlock(:classid, :objid)"),
                {"classid": key[0], "objid": key[1]},
            )
        except Exception:
            # The connection is already gone, which means PostgreSQL has
            # already released the lock for us. Nothing to repair.
            _logger.info("advisory lock: unlock failed; the connection is gone so the lock is already released")
        finally:
            # Never conditional and never skipped: a connection that is
            # not closed is a connection still holding the lock.
            await connection.close()

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
