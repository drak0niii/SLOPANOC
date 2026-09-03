"""ADK session lifecycle for the API: user-scoped session
creation/lookup, persistence backend selection, and per-session
concurrency serialization.

SESSION MODEL: one client-facing `session_id` (server-generated, opaque,
a UUID4 string) IS the ADK session id directly -- there is no separate
mapping table between "API session id" and "ADK session id" to keep in
sync, and therefore no way for the two to ever drift apart. All API
sessions share one fixed `app_name` ("slopanoc-api"); `user_id` is now
resolved PER REQUEST from `backend.api.identity.UserContext` (Phase 4C)
-- never a runtime constant, and never client-supplied JSON.

OWNERSHIP, FOR FREE: every method below requires a `user_id`, and passes
it straight through to ADK's own session lookup, which is already keyed
by the full `(app_name, user_id, session_id)` triple. This means
ownership enforcement is not a separate check this module implements --
it falls directly out of always resolving `user_id` from `UserContext`
and never trusting a client-supplied one: a request resolved to the
"wrong" user simply cannot find another user's session at all, and
`get_session` raises the exact same `not_found` it would for a genuinely
unknown session id -- there is no way to distinguish "doesn't exist" from
"exists, but isn't yours" from the response, which is exactly the
anti-enumeration behavior required (instruction section 11).

PERSISTENCE BACKEND (Phase 4C): `create_session_service_backend()` is the
factory instruction section 5 asks for -- it reads
`Settings.session_backend`/`resolve_database_url()` and returns either
`InMemorySessionService` (explicit "memory" opt-out, non-persistent,
same-process only -- what this backend's own automated test suite uses
throughout, for speed/isolation) or `google.adk.sessions
.DatabaseSessionService` (the default -- persistent, restart-safe,
URL-driven so SQLite-for-local-dev vs PostgreSQL/Cloud-SQL-for-production
is a configuration change, never a code change). Verified against the
installed ADK 1.33.0 source (`database_session_service.py`) before use:

  - `DatabaseSessionService(db_url: str, **kwargs)` builds a SQLAlchemy
    ASYNC engine (`create_async_engine`) -- `db_url` must use an
    async-capable dialect+driver, e.g. `sqlite+aiosqlite:///...` or
    `postgresql+asyncpg://...` (both drivers confirmed installed in this
    environment), never the plain synchronous `sqlite:///`/`postgresql://`
    form SQLAlchemy's sync engine would accept.
  - Table creation is fully automatic and lazy (`_prepare_tables()`,
    called internally before every operation) -- no manual
    migration/schema-init step is needed from this codebase.
  - `get_session`/`create_session`/`append_event` share the exact same
    `BaseSessionService` async method signatures as `InMemorySessionService`
    (both are `BaseSessionService` subclasses) -- this module, and every
    caller of it (chat_service.py, approval_service.py), works unchanged
    regardless of which concrete backend is plugged in. No
    persistence-specific branching exists outside this factory function.
  - `get_session` returns `None` for an unknown `(app_name, user_id,
    session_id)` -- identical contract to `InMemorySessionService`, so
    `ApiSessionService.get_session`'s `not_found` translation below needs
    no backend-specific handling.
  - `append_event` on a session that was concurrently modified in storage
    since it was loaded raises a plain `ValueError` (ADK's own
    "the session has been modified in storage" staleness guard, backed by
    a storage revision marker, plus row-level locking -- `SELECT ... FOR
    UPDATE` -- on PostgreSQL/MySQL/MariaDB). This module does not need to
    special-case that: it always re-fetches the session immediately before
    mutating it while holding this session's execution lock (see
    `chat_service.py`/`approval_service.py`), so same-process staleness
    cannot occur; if it were ever raised anyway (e.g. a second backend
    process racing this one -- see execution_coordinator.py's "NOT
    SUFFICIENT FOR MULTIPLE PROCESSES/WORKERS"), it is just a plain
    exception that `backend/api/errors.py`'s existing catch-all handler
    already converts to a safe generic `internal_error`, never leaking the
    raw message, a SQL statement, or a connection string.
  - Real ADK conversation history (the actual `Event`s, including message
    content) persists as part of this same mechanism -- `StorageEvent
    .event_data` (a JSON column, confirmed in
    `google/adk/sessions/schemas/v1.py`) serializes the whole `Event`,
    not just custom state keys -- so a resumed session provides the model
    the same conversation history through ADK's normal mechanism, with no
    separate transcript table.

CONCURRENCY: `lock_for(user_id, session_id)` delegates to a
`SessionExecutionCoordinator` (see execution_coordinator.py for the
process-local-only scope and its documented limitation for multiple
backend workers/instances) -- this module never exposes a raw lock map to
its callers.

STATE PERSISTENCE (`persist_state_delta`, Phase 4B, unchanged in
principle): `get_session` returns a COPY of the stored session (true for
both `InMemorySessionService` and `DatabaseSessionService`, confirmed
against both implementations' source) -- mutating a fetched session's
`.state` dict directly is silently discarded, never persisted. The only
ADK-supported persistence path is `session_service.append_event(session,
event)` with the change captured in `event.actions.state_delta`, exactly
what the real `Runner`'s own tool-calling machinery does automatically
for a live agent turn (confirmed directly against `BaseSessionService`'s
installed 1.33.0 source: `append_event` is its entire mutation surface --
there is no separate "update state only" method). `persist_state_delta`
is the one place `backend/api/` constructs that event manually, for every
case where state changes without going through a live agent turn at all
-- a trusted approve/reject transition, a resolved read continuation's
trusted result, selected-Teams-chat/evidence sync, a Case link, and
similar deterministic, server-side transitions (see each caller's own
docstring for what it writes and why). See `persist_state_delta`'s own
docstring below for why every such event is authored `'user'`, not a
made-up application identity -- verified against ADK's own `Event.author`
contract and `Runner.rewind_async`'s own internal use of the identical
shape (P3, session/event-hygiene pass).
"""
from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any, Optional

from google.adk.events import Event, EventActions
from google.adk.sessions import BaseSessionService, InMemorySessionService, Session

from backend.api.execution_coordinator import SessionExecutionCoordinator
from backend.config.settings import Settings, get_settings
from backend.gateway.safe_error import not_found

APP_NAME = "slopanoc-api"

# The identity used only when a caller does not resolve one via
# `backend.api.identity.UserContext` (i.e. existing/lower-level tests that
# predate per-request identity resolution) -- production route handlers in
# app.py always pass an explicit, resolved `user_id` and never rely on
# this default.
DEFAULT_USER_ID = "api-user"


def create_session_service_backend(settings: Optional[Settings] = None) -> BaseSessionService:
    """The persistence-backend factory (instruction section 5). Reads
    `Settings.session_backend`; "memory" returns a fresh
    `InMemorySessionService`, "database" (the default) returns ADK's own
    `DatabaseSessionService` bound to `Settings.resolve_database_url()`.
    There is never more than one authoritative session store at runtime --
    this is the single place that decision is made.
    """
    settings = settings or get_settings()
    backend = settings.session_backend
    if backend == "memory":
        return InMemorySessionService()

    # "database" -- lazy import: `DatabaseSessionService` needs
    # sqlalchemy>=2.0 (confirmed installed), and importing it eagerly
    # would be an unnecessary hard dependency for any caller that only
    # ever uses the in-memory backend (e.g. most of this backend's own
    # test suite).
    from google.adk.sessions import DatabaseSessionService

    return DatabaseSessionService(settings.resolve_database_url())


class ApiSessionService:
    def __init__(
        self,
        adk_session_service: Optional[BaseSessionService] = None,
        coordinator: Optional[SessionExecutionCoordinator] = None,
    ) -> None:
        # Defaults to `InMemorySessionService` when not given -- keeps
        # every existing/ad hoc `ApiSessionService()` construction (used
        # throughout this backend's test suite) fast, isolated, and free
        # of any file/database I/O; the actual runtime default
        # (persistent "database" backend) is selected by
        # `create_session_service_backend()` above, wired in only by
        # `get_session_service()`'s singleton below.
        self._adk = adk_session_service if adk_session_service is not None else InMemorySessionService()
        self._coordinator = coordinator if coordinator is not None else SessionExecutionCoordinator()

    @property
    def adk_session_service(self) -> BaseSessionService:
        return self._adk

    async def create_session(self, user_id: str = DEFAULT_USER_ID) -> str:
        """Generates the session id server-side -- callers cannot supply
        one, and there is no parameter here through which a client could
        seed initial ADK state (instruction: "Do not allow clients to
        inject arbitrary ADK state during creation.").
        """
        session_id = str(uuid.uuid4())
        await self._adk.create_session(
            app_name=APP_NAME, user_id=user_id, session_id=session_id, state={}
        )
        return session_id

    async def get_session(self, session_id: str, user_id: str = DEFAULT_USER_ID) -> Session:
        """Raises a `SafeErrorException` (`not_found`) for an unknown
        session id, OR a session that exists but belongs to a different
        `user_id` -- both look identical from the caller's perspective, by
        design (see module docstring's "OWNERSHIP, FOR FREE").
        """
        session = await self._adk.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        if session is None:
            raise not_found("No session was found with that id.")
        return session

    async def session_exists(self, session_id: str, user_id: str = DEFAULT_USER_ID) -> bool:
        session = await self._adk.get_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        return session is not None

    async def persist_state_delta(self, session: Session, delta: dict[str, Any]) -> None:
        """Persist `delta` into `session`'s real, stored state via ADK's
        own event/state-delta mechanism (see module docstring's "STATE
        PERSISTENCE"). `session` must be a session object obtained from
        this same service (its `app_name`/`user_id`/`id` identify which
        stored session `append_event` updates) -- the delta itself is
        computed by the caller (e.g. `approval_service.py`, from a
        trusted, deterministic transition), never by this method.

        P3 FIX (session/event-hygiene pass) -- `author='user'`, NEVER a
        made-up application identity: `google.adk.events.event.Event
        .author`'s own field docstring is definitive and exhaustive --
        "'user' OR the name of the agent, indicating who appended the
        event to the session" -- ADK's `Event` model recognizes exactly
        two author categories, with no third "system/application" one.
        Using anything else here (the previous `"slopanoc-api"`) put
        every event this method ever created outside that documented
        contract, which is exactly why `Runner._find_agent_to_run`
        (installed ADK 1.33.0 source, `runners.py`) logged "Event from an
        unknown agent: slopanoc-api" on every later turn: it walks
        `session.events` in reverse looking for the agent that should
        continue the session, and treats any `author` that is neither
        `root_agent.name` nor a real sub-agent name (found via
        `root_agent.find_sub_agent`) as unrecognized.

        `author='user'` is not a workaround -- it is the SAME pattern
        ADK's OWN `Runner.rewind_async` uses internally for its own
        rewind-marker events (verified directly: `runners.py`'s
        `rewind_async` builds `Event(invocation_id=..., author='user',
        actions=EventActions(rewind_before_invocation_id=..., state_delta=
        ...))` -- action-only, no `content`, exactly this method's own
        shape). `_find_agent_to_run`'s own `_event_filter` already skips
        every `author == 'user'` event before it would ever reach the
        unknown-agent check, by design -- this is a documented,
        ADK-native exclusion path, not an internal/workflow-only field
        (`EventActions.agent_state`/`end_of_agent`, both explicitly
        documented as "should only be set by ADK workflow", were
        considered and rejected for that reason).

        NEVER MODEL-VISIBLE, before or after this change: this method
        never sets `Event.content` (only `actions.state_delta`) --
        `google.adk.flows.llm_flows.contents._get_contents` (verified
        directly) only appends an event's `content` to the model-facing
        `contents` list when `content` is truthy; a state-delta-only
        event was already invisible to every model call regardless of
        its author, so this change alters ONLY which agent-resolution
        code path recognizes the event, never what the model sees.
        """
        event = Event(
            author="user",
            invocation_id=str(uuid.uuid4()),
            actions=EventActions(state_delta=delta),
        )
        await self._adk.append_event(session, event)

    def lock_for(self, session_id: str, user_id: str = DEFAULT_USER_ID):
        return self._coordinator.lock_for(user_id, session_id)


@lru_cache(maxsize=1)
def get_session_service() -> ApiSessionService:
    """Process-wide singleton, mirroring `config/settings.py`'s
    `get_settings()` pattern -- all API requests must share the same
    underlying session-service instance, or sessions would be
    inconsistent (and, for the in-memory backend, vanish) between
    requests. Uses `create_session_service_backend()` -- the configured
    persistence backend, "database" by default.
    """
    return ApiSessionService(create_session_service_backend())
