"""Alembic environment for SLOPANOC-owned schemas.

SCOPE (POST-5.1 A2 instruction section 9, extended POST-5.1 B1): Alembic
manages ONLY the SLOPANOC-owned SQLAlchemy metadata collections --

  - backend.cases.models.Base.metadata       (Case/Fault Context)
  - backend.knowledge.repository.sqlalchemy.Base.metadata  (Governed Knowledge)
  - backend.attachments.models.Base.metadata  (Chat Attachments, POST-5.1 B1)

ADK's own `DatabaseSessionService` schema (sessions/events/app_states/
user_states) is deliberately NEVER imported or referenced here. ADK
manages that schema internally, automatically, and lazily
(`_prepare_tables()`, verified in backend/api/session_service.py's own
docstring) -- this file must not know its table names, must not
autogenerate against it, and must never emit a DROP/CREATE for it.

`target_metadata` is a LIST of both SLOPANOC-owned `MetaData` objects
(Alembic autogenerate supports a sequence of `MetaData` since 1.11+,
confirmed against the installed 1.18.4) -- Cases and Knowledge keep their
own separate `DeclarativeBase`/engine at RUNTIME (backend/cases/db.py,
backend/knowledge/repository/sqlalchemy.py each own their own engine);
this file only needs read-only visibility into both table sets for
migration authoring, never a shared runtime Base.

DATABASE URL: resolved via `backend.config.settings.get_settings()
.resolve_database_url()` -- the EXACT SAME resolver the FastAPI app
itself uses for the session/Case database (SLOPANOC_DATABASE_URL /
SLOPANOC_DATABASE_SECRET_RESOURCE / local SQLite default). Never
hardcoded here, never logged. This is deliberate: Cases already share
that database by default (backend/cases/db.py's own docstring), and the
POST-5.1 A target design puts Governed Knowledge in the SAME single
Cloud SQL `slopanoc` database too (POST-5.1 A2 instruction section 3) --
so one Alembic environment, targeting one resolved URL, migrating both
SLOPANOC-owned table sets, is the correct shape. This file never touches
`resolve_knowledge_database_url()` -- see this repo's `docs/` for the
production cutover note once Knowledge is actually pointed at the same
database.

ASYNC ENGINE: SLOPANOC's configured database URLs are async SQLAlchemy
URLs (`sqlite+aiosqlite://...`, `postgresql+asyncpg://...`) -- this
follows SQLAlchemy/Alembic's own documented "Using asyncio with Alembic"
pattern (`async_engine_from_config` + `connection.run_sync(...)`), not a
second, separately-maintained sync URL/driver.
"""
from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from backend.attachments.models import Base as AttachmentBase
from backend.cases.models import Base as CaseBase
from backend.config.settings import get_settings
from backend.knowledge.repository.sqlalchemy import Base as KnowledgeBase

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# All SLOPANOC-owned metadata collections, never ADK's own session schema.
# POST-5.1 B1 adds AttachmentBase (backend/attachments/models.py) alongside
# the two established since POST-5.1 A2.
target_metadata = [CaseBase.metadata, KnowledgeBase.metadata, AttachmentBase.metadata]

# POST-5.1 A4: derived (never hand-duplicated) from target_metadata itself
# -- the exact set of table names Alembic is actually allowed to compare
# against/manage. Without `include_object` filtering autogenerate by this
# set, `alembic revision --autogenerate` against a real database that also
# has ADK's own session tables (created independently by
# `DatabaseSessionService`, never part of target_metadata) proposes
# DROPping every one of them -- confirmed live (A4): a first autogenerate
# attempt proposed dropping `sessions`/`events`/`app_states`/`user_states`/
# `adk_internal_metadata`. Those statements were removed by hand from that
# migration; this filter stops autogenerate from ever proposing it again,
# for any table ADK happens to own, without this file needing to know
# their names.
_SLOPANOC_OWNED_TABLE_NAMES = frozenset(
    table.name for metadata in target_metadata for table in metadata.tables.values()
)


def _include_object(object_, name, type_, reflected, compare_to):
    if type_ == "table":
        return name in _SLOPANOC_OWNED_TABLE_NAMES
    return True


def _resolved_database_url() -> str:
    """Same resolver the FastAPI app itself uses -- see module docstring.
    Never logged, never printed; only ever handed straight to the engine.
    """
    return get_settings().resolve_database_url()


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection (`--sql`) -- still
    resolves the real URL (for correct dialect-specific SQL generation)
    but never connects.
    """
    context.configure(
        url=_resolved_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, include_object=_include_object)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = _resolved_database_url()
    connectable = async_engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
