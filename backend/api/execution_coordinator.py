"""Per-session execution serialization, isolated behind a dedicated
abstraction so `chat_service.py`/`approval_service.py` never depend on a
raw `asyncio.Lock` map directly (Phase 4C instruction section 20).

CURRENT SCOPE -- PROCESS-LOCAL ONLY: `SessionExecutionCoordinator` holds
one `asyncio.Lock` per `(user_id, session_id)` pair, created lazily,
scoped to THIS Python process's event loop. This is sufficient for
correctness with a single backend process/worker (the deployment shape
this milestone targets), and prevents the exact race this exists to
prevent: an agent turn and a trusted approve/reject call for the SAME
session interleaving their reads/writes of that session's ADK state.

NOT SUFFICIENT FOR MULTIPLE PROCESSES/WORKERS: if the API is later
horizontally scaled (multiple Cloud Run instances, multiple Gunicorn/
Uvicorn workers, etc.), two requests for the same session could land on
two different processes, each with its own independent lock map -- this
coordinator would NOT serialize them. `google.adk.sessions
.database_session_service.DatabaseSessionService.append_event` (used once
persistent storage is configured, see session_service.py) does provide
its own additional protection at the individual-write level: it detects a
storage revision mismatch and raises rather than silently overwriting a
concurrent write from another process, and PostgreSQL's row-level locking
(`SELECT ... FOR UPDATE`, confirmed used for the `mariadb`/`mysql`/
`postgresql` dialects in the installed ADK source) further protects a
single `append_event` call under true multi-process contention. But this
coordinator's own per-session lock -- which is what keeps an ENTIRE agent
turn or an ENTIRE approve/reject transition atomic, not just one
individual database write -- is explicitly out of scope for distributed
correctness in this milestone (instruction: "Do NOT invent a distributed
lock now."). A future phase can harden this with a distributed lock
(e.g. a Postgres advisory lock, keyed the same way) without changing this
class's public shape.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from backend.api.distributed_lock import AdvisoryLockManager
    from backend.api.operation_claims import OperationClaimStore

_logger = logging.getLogger(__name__)


class SessionExecutionCoordinator:
    """POST-6A -- process-local lock PLUS a PostgreSQL session advisory
    lock when one is available.

    `lock_for` is unchanged and remains the in-process serialization every
    existing caller uses. `distributed_lock_for` is the additional,
    cross-process boundary: held for the same critical section, released
    with it, and answerable (`still_holds`) so a worker that lost its
    connection can be stopped before it publishes anything.

    Against SQLite/local development the manager reports itself
    unavailable and this degrades to exactly the previous behaviour --
    which is correct for a single process, and never pretends otherwise.
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._manager: Optional["AdvisoryLockManager"] = None
        self._claims: Optional["OperationClaimStore"] = None
        self._database_url = database_url

    def _resolved_database_url(self) -> str:
        if self._database_url:
            return self._database_url
        from backend.config.settings import get_settings

        return get_settings().resolve_database_url()

    @property
    def operation_claims(self) -> "OperationClaimStore":
        """POST-6A -- the DURABLE ownership generation store.

        Deliberately a sibling of the advisory lock rather than a
        replacement for it, because the two answer different questions.
        The lock answers "is the holder still alive?" -- which the
        database can decide instantly and without a lease, by noticing
        the connection is gone. The claim answers "is this writer still
        the owner?" -- which the lock cannot decide, because ownership
        can change AFTER a worker's last check and before its next write.
        Liveness plus fencing; neither one alone is sufficient.
        """
        if self._claims is None:
            from backend.api.operation_claims import OperationClaimStore

            self._claims = OperationClaimStore(self._resolved_database_url())
        return self._claims

    def _distributed_manager(self):
        if self._manager is None:
            from backend.api.distributed_lock import AdvisoryLockManager

            self._manager = AdvisoryLockManager(self._resolved_database_url())
        return self._manager

    @property
    def distributed_available(self) -> bool:
        """Whether cross-process mutual exclusion is genuinely in force.
        Callers that need to know (and tests that must not claim
        PostgreSQL behaviour was proven on SQLite) read this rather than
        assuming."""
        try:
            return self._distributed_manager().available
        except Exception:
            return False

    async def distributed_lock_for(self, namespace: str, identity: str, *, wait: bool = True):
        """The cross-process lock, or `None` when unavailable.

        `wait=False` is what dispatch uses: a worker that cannot take the
        lock must learn it lost and stop, never queue and then re-send.
        """
        try:
            return await self._distributed_manager().acquire(namespace, identity, wait=wait)
        except Exception:
            _logger.warning(
                "coordinator: could not acquire a distributed lock for %s/%s", namespace, identity, exc_info=True
            )
            return None

    def lock_for(self, user_id: str, session_id: str) -> asyncio.Lock:
        """Returns the one lock for this `(user_id, session_id)` pair,
        creating it on first use. Safe without an additional guard lock:
        this method contains no `await` between the check and the set, and
        asyncio only switches coroutines at an `await` point.
        """
        key = (user_id, session_id)
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
