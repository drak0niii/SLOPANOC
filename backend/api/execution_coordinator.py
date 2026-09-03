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


class SessionExecutionCoordinator:
    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

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
