"""The TELCO Context domain's own async SQLAlchemy engine -- mirrors
`backend/cases/db.py`'s `CaseDatabase` exactly (same engine-per-database,
lazy idempotent schema creation, SQLite-in-memory `StaticPool` handling),
including its own reasoning for each choice; not repeated here in full.

DATABASE CHOICE (6A.1's own decision, GCP_INTELLIGENCE_RUNTIME.md #4's
"TELCO structured context" row -- EXTEND the existing Cloud SQL/
PostgreSQL instance): this module defaults to `Settings
.resolve_database_url()` -- the SAME session/Case database domain, never
`resolve_knowledge_database_url()`. TELCO Context is operational/case-
like state (who/what/where the current investigation concerns), not
governed knowledge content -- it belongs with Case, not with the
Governed Knowledge repository, and reuses the identical database Case
already uses rather than requiring a third configured database URL.

Production schema provisioning goes through Alembic (see `alembic/
versions/` for this milestone's own migration) -- `ensure_schema()`'s
`create_all` remains the safe, idempotent local/test-only path, exactly
like `CaseDatabase.ensure_schema()`.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.config.settings import Settings, get_settings
from backend.context.sqlalchemy.models import Base


def _engine_kwargs(database_url: str) -> dict:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database in (None, ":memory:"):
        return {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    return {}


class ContextDatabase:
    """Owns one async engine + session factory, and lazy/idempotent
    schema creation -- constructible with an explicit URL (tests always
    do this, for isolation) or defaulting to the configured session/Case
    database URL (what the process-wide singleton below uses).
    """

    def __init__(self, database_url: Optional[str] = None, settings: Optional[Settings] = None) -> None:
        url = database_url if database_url is not None else (settings or get_settings()).resolve_database_url()
        self._engine: AsyncEngine = create_async_engine(url, **_engine_kwargs(url))
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine, expire_on_commit=False
        )
        self._schema_ready = False

    def session(self) -> AsyncSession:
        return self._session_factory()

    async def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._schema_ready = True

    async def close(self) -> None:
        await self._engine.dispose()


@lru_cache(maxsize=1)
def get_context_database() -> ContextDatabase:
    """Process-wide singleton, mirroring `get_case_database()`/
    `get_settings()`'s own pattern.
    """
    return ContextDatabase()
