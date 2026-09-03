"""The application's own SQLAlchemy async engine for Case data --
completely separate from ADK's `DatabaseSessionService` engine, bound to
the SAME configured `SLOPANOC_DATABASE_URL` by default (instruction
section 3: "Use the existing configured database URL unless there is a
strong technical reason not to... Do not create a second production
database requirement unnecessarily.").

This module never imports anything from `google.adk.sessions
.database_session_service` and never touches its private `db_engine` --
per instruction ("Do not reach into private internals of
DatabaseSessionService to reuse its engine"), the Case store builds and
owns its own engine/session factory. The two engines point at the same
physical database (in production) but manage entirely separate table
sets (`slopanoc_*` vs. ADK's own schema) -- "logically separate" per
instruction section 2, achieved simply by never sharing a
`MetaData`/`Base` between the two.

SCHEMA CREATION (instruction section 4): `ensure_schema()` runs
`Base.metadata.create_all` -- perfectly fine for local SQLite and tests,
explicitly NOT presented as a production migration strategy. No
migration framework (e.g. Alembic) exists in this repository yet, and
none is introduced here (out of scope: "Do not introduce a large
migration framework solely for this milestone unless clearly
necessary"). Production PostgreSQL schema provisioning/migration is
documented as a hardening item in the final report -- `ensure_schema` is
still safe to call there (it is idempotent, and SQLAlchemy's `create_all`
does not touch tables that already exist), but a real deployment should
manage schema evolution deliberately once this ships past prototype
stage.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.cases.models import Base
from backend.config.settings import Settings, get_settings


def _engine_kwargs(database_url: str) -> dict:
    """Mirrors the exact SQLite-in-memory handling the installed ADK
    `DatabaseSessionService.__init__` uses (verified in Phase 4C) --
    without it, an in-memory SQLite database (used by this backend's own
    test suite) would get a fresh, empty database on every new pooled
    connection, since SQLite's `:memory:` database only exists for the
    lifetime of a single connection.
    """
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database in (None, ":memory:"):
        return {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    return {}


class CaseDatabase:
    """Owns one async engine + session factory, and lazy/idempotent
    schema creation -- constructible with an explicit URL (tests always
    do this, for isolation) or defaulting to the configured
    `Settings.resolve_database_url()` (what the process-wide singleton
    below uses).
    """

    def __init__(self, database_url: Optional[str] = None, settings: Optional[Settings] = None) -> None:
        url = database_url if database_url is not None else (settings or get_settings()).resolve_database_url()
        self._engine: AsyncEngine = create_async_engine(url, **_engine_kwargs(url))
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine, expire_on_commit=False
        )
        self._schema_ready = False

    def session(self) -> AsyncSession:
        """A new `AsyncSession` bound to this engine -- callers use it as
        an `async with` context manager for one unit of work.
        """
        return self._session_factory()

    async def ensure_schema(self) -> None:
        """Idempotent, lazy `create_all` -- called before any operation
        needs the schema (mirrors ADK's own `_prepare_tables` pattern,
        Phase 4C), so nothing else in this codebase needs its own
        migration/init step.
        """
        if self._schema_ready:
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._schema_ready = True

    async def close(self) -> None:
        await self._engine.dispose()


@lru_cache(maxsize=1)
def get_case_database() -> CaseDatabase:
    """Process-wide singleton, mirroring `get_settings()`/
    `get_session_service()`'s pattern.
    """
    return CaseDatabase()
