"""The Experience Memory domain's own async SQLAlchemy engine --
mirrors `backend/context/sqlalchemy/db.py`'s `ContextDatabase` exactly
(same engine-per-database, lazy idempotent schema creation, SQLite-
in-memory `StaticPool` handling).

DATABASE CHOICE (§9's own instruction, matching 6A.1/6A.2's precedent):
this module defaults to `Settings.resolve_database_url()` -- the SAME
session/Case/TELCO-Context database domain, never
`resolve_knowledge_database_url()`. Experience Memory is durable
operational/historical state (what happened in a case), not governed
knowledge content -- it belongs with Case/TELCO Context, reusing the
identical Cloud SQL instance/database those already use, never a new
Cloud SQL instance/database/service (§9/§102.I).

Production schema provisioning goes through Alembic (see `alembic/
versions/` for this milestone's own migration) -- `ensure_schema()`'s
`create_all` remains the safe, idempotent local/test-only path.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.config.settings import Settings, get_settings
from backend.experience_memory.sqlalchemy.models import Base


def _engine_kwargs(database_url: str) -> dict:
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database in (None, ":memory:"):
        return {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    return {}


class ExperienceMemoryDatabase:
    """Owns one async engine + session factory, and lazy/idempotent
    schema creation -- constructible with an explicit URL (tests always
    do this, for isolation) or defaulting to the configured session/
    Case/TELCO-Context database URL (what the process-wide singleton
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
def get_experience_memory_database() -> ExperienceMemoryDatabase:
    """Process-wide singleton, mirroring `get_context_database()`'s own
    pattern."""
    return ExperienceMemoryDatabase()
