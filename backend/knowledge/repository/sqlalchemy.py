"""The dialect-neutral async-SQLAlchemy implementation of
`KnowledgeRepository`.

STORAGE TECHNOLOGY CHOICE: async SQLAlchemy, mirroring
`backend/cases/db.py`'s already-established pattern for local
persistence in this backend, rather than stdlib `sqlite3` directly. This
backend already has two other async-SQLAlchemy-backed persistence
layers (ADK's own `DatabaseSessionService` and `backend/cases/`), so
following the same pattern here is the smallest-surprise choice, keeps
this repository consistent with the rest of the codebase's transaction/
session-management idioms, and (per instruction) is a genuinely
established dependency that strongly fits -- not a new one introduced
for this phase. This module owns its OWN engine, session factory, and
`DeclarativeBase`/table set -- it never shares a `MetaData`/`Base` with
`backend/cases/models.py` or ADK's own session tables, the same
domain-isolation discipline `backend/cases/db.py` already established.

SYNC VS. ASYNC: async, matching that same existing pattern -- this is
"follow the existing async repository pattern already used in this
backend", not "make it async merely because production storage might be
async someday" (which the instruction explicitly warns against).

SCHEMA: one small, generic table -- `slopanoc_knowledge_objects`, keyed
by `(knowledge_id, version_label)`, storing the ENTIRE governed
`KnowledgeObject` aggregate as one JSON payload column (via
`KnowledgeObject.model_dump_json()`/`model_validate_json()` -- the
established Pydantic serialization API, never a hand-built dict, never
`pickle`). This is deliberately not normalized into a table per nested
model (`KnowledgeSection`, `KnowledgeVersion`, ...) -- there is no
concrete need for one yet, and the instruction explicitly asks for the
smallest correct solution; the JSON payload keeps the whole aggregate's
round-trip fidelity trivially exact (pydantic's own validation
reconstructs it) without hand-maintaining dozens of columns.

DIALECT PORTABILITY (POST-5.1 A2): this class was originally named
`SQLiteKnowledgeRepository` and lived in this package's `sqlite.py`. The
Cloud SQL PostgreSQL audit (POST-5.1 A) confirmed it was already
dialect-portable by construction -- nothing here does anything but
generic SQLAlchemy Core/ORM calls (`create_async_engine`,
`async_sessionmaker`, `select`, `session.get`, `IntegrityError`), backed
by a single generic `Text` column, so it works unchanged against
`postgresql+asyncpg://...` as well as `sqlite+aiosqlite://...`. It was
renamed/moved here so its identity stops implying a SQLite-only
implementation; `sqlite.py` now re-exports `SQLiteKnowledgeRepository` as
a compatibility alias for this exact class, so no existing caller/test
needs to change. See that module's own docstring for the alias.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from pydantic import ValidationError
from sqlalchemy import Text, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool

from backend.knowledge.domain.models import KnowledgeObject
from backend.knowledge.repository.contracts import (
    KnowledgeRepositoryCorruptionError,
    KnowledgeVersionAlreadyExistsError,
    KnowledgeVersionNotFoundError,
)


class Base(DeclarativeBase):
    pass


class KnowledgeObjectRecord(Base):
    """One governed version, keyed by the SAME logical identity the
    `KnowledgeRepository` contract itself uses -- `(knowledge_id,
    version_label)` as a composite primary key, DB-enforced (this is
    what makes duplicate detection a storage-level guarantee, not merely
    a Python "check then insert" -- see `SqlAlchemyKnowledgeRepository.add`).
    `payload` is the complete `KnowledgeObject`, JSON-serialized -- no
    document-type-specific table or column exists, and none should.
    """

    __tablename__ = "slopanoc_knowledge_objects"

    knowledge_id: Mapped[str] = mapped_column(primary_key=True)
    version_label: Mapped[str] = mapped_column(primary_key=True)
    payload: Mapped[str] = mapped_column(Text)


def _engine_kwargs(database_url: str) -> dict[str, Any]:
    """Mirrors `backend/cases/db.py`'s own `_engine_kwargs` exactly
    (deliberately duplicated, not imported -- this package stays
    independent of `backend.cases`, the same domain-isolation discipline
    `backend/cases/db.py` itself already applies to ADK's session
    engine). Without this, an in-memory SQLite URL would get a fresh,
    empty database on every new pooled connection, since `:memory:` only
    exists for the lifetime of a single connection.

    This is a SQLite-ONLY special case -- `url.get_backend_name() ==
    "sqlite"` gates it -- and must never fire for any other dialect. A
    `postgresql+asyncpg://...` URL always falls through to `{}`, i.e.
    plain SQLAlchemy engine defaults (POST-5.1 A2 instruction section 7:
    "Use SQLAlchemy defaults for this milestone unless a concrete
    existing test requires otherwise").
    """
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite" and url.database in (None, ":memory:"):
        return {"poolclass": StaticPool, "connect_args": {"check_same_thread": False}}
    return {}


class SqlAlchemyKnowledgeRepository:
    """The generic, dialect-neutral `KnowledgeRepository` implementation.

    `database_url` is always explicit -- this class never defaults to,
    or hardcodes, any environment-specific or developer-machine path;
    callers supply a real file path, a temporary path, an in-memory
    SQLite URL (e.g. `"sqlite+aiosqlite:///:memory:"`), or a
    `postgresql+asyncpg://...` URL. Runtime configuration wiring (a
    process-wide singleton bound to `Settings.resolve_knowledge_database_url()`,
    mirroring `backend/cases/db.py`'s `get_case_database()`) lives in
    `backend/tools/knowledge/runtime.py`.
    """

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, **_engine_kwargs(database_url))
        self._session_factory = async_sessionmaker(bind=self._engine, expire_on_commit=False)
        self._schema_ready = False

    async def ensure_schema(self) -> None:
        """Idempotent, lazy `create_all` -- mirrors
        `backend/cases/db.py`'s own `ensure_schema`. Opening/initializing
        the same repository twice, or calling this more than once, never
        fails merely because the table already exists; every public
        method below calls this itself, so a caller never needs a
        separate explicit init step.
        """
        if self._schema_ready:
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self._schema_ready = True

    async def close(self) -> None:
        await self._engine.dispose()

    async def add(self, knowledge_object: KnowledgeObject) -> None:
        await self.ensure_schema()
        record = KnowledgeObjectRecord(
            knowledge_id=knowledge_object.knowledge_id,
            version_label=knowledge_object.version.label,
            payload=knowledge_object.model_dump_json(),
        )
        async with self._session_factory() as session:
            session.add(record)
            try:
                await session.commit()
            except IntegrityError as exc:
                # The composite primary key is what actually enforces
                # uniqueness (never only a pre-insert Python existence
                # check, per instruction) -- this converts the low-level
                # storage integrity violation into the repository's own
                # explicit domain error, so callers never see a raw
                # SQLAlchemy/DBAPI exception.
                await session.rollback()
                raise KnowledgeVersionAlreadyExistsError(
                    f"version {knowledge_object.version.label!r} of knowledge_id "
                    f"{knowledge_object.knowledge_id!r} already exists"
                ) from exc

    async def get(self, knowledge_id: str, version_label: str) -> Optional[KnowledgeObject]:
        await self.ensure_schema()
        async with self._session_factory() as session:
            record = await session.get(KnowledgeObjectRecord, (knowledge_id, version_label))
        if record is None:
            return None
        return self._reconstruct(record)

    async def replace(self, knowledge_object: KnowledgeObject) -> None:
        await self.ensure_schema()
        async with self._session_factory() as session:
            record = await session.get(
                KnowledgeObjectRecord, (knowledge_object.knowledge_id, knowledge_object.version.label)
            )
            if record is None:
                raise KnowledgeVersionNotFoundError(
                    f"version {knowledge_object.version.label!r} of knowledge_id "
                    f"{knowledge_object.knowledge_id!r} does not exist -- replace() never creates"
                )
            # Identity (the primary key columns) is never touched here --
            # replace() persists updated governed STATE for the exact
            # same logical version; changing identity means add()-ing a
            # different version, never mutating a storage key.
            record.payload = knowledge_object.model_dump_json()
            await session.commit()

    async def list_versions(self, knowledge_id: str) -> list[KnowledgeObject]:
        await self.ensure_schema()
        async with self._session_factory() as session:
            # Ordered by version_label purely for deterministic,
            # reproducible test/inspection output -- PRESENTATION/STORAGE
            # DETERMINISM ONLY. Version labels are opaque identifiers
            # (docs/KNOWLEDGE_CONTRACT.md §14.4); this ordering carries
            # ZERO governance/precedence meaning, and no caller may infer
            # currentness from list position. Every persisted lifecycle
            # state (CANDIDATE/APPROVED/ARCHIVE) is returned -- no
            # filtering of any kind.
            result = await session.execute(
                select(KnowledgeObjectRecord)
                .where(KnowledgeObjectRecord.knowledge_id == knowledge_id)
                .order_by(KnowledgeObjectRecord.version_label)
            )
            records = result.scalars().all()
        return [self._reconstruct(record) for record in records]

    async def list_all(self) -> list[KnowledgeObject]:
        """Source-of-truth corpus enumeration -- every governed
        `KnowledgeObject` persisted in this repository, across every
        `knowledge_id`, version, and `LifecycleStatus`. No query,
        `top_k`, document-type filter, lifecycle filter, or applicability
        filter is accepted; this performs no ranking, filtering,
        currentness resolution, or applicability evaluation -- see
        `KnowledgeRepository.list_all`'s own docstring for the full
        source-of-truth rationale. Reconstructs each row through the
        exact same `_reconstruct` corruption-safe path `get`/
        `list_versions` already use, so a corrupt row still fails closed
        with `KnowledgeRepositoryCorruptionError` here too.
        """
        await self.ensure_schema()
        async with self._session_factory() as session:
            # Ordered by (knowledge_id, version_label) purely for
            # deterministic, reproducible output -- PRESENTATION/STORAGE
            # DETERMINISM ONLY, exactly like list_versions's own ordering.
            # This carries ZERO governance or relevance meaning; it is
            # not a ranking and must never be read as one.
            result = await session.execute(
                select(KnowledgeObjectRecord).order_by(
                    KnowledgeObjectRecord.knowledge_id, KnowledgeObjectRecord.version_label
                )
            )
            records = result.scalars().all()
        return [self._reconstruct(record) for record in records]

    @staticmethod
    def _reconstruct(record: KnowledgeObjectRecord) -> KnowledgeObject:
        """Reconstruct and validate a stored payload -- fails closed
        (raises `KnowledgeRepositoryCorruptionError`, never returns a
        partial object, never drops/fabricates a field) if the payload
        is not valid JSON, does not validate as a `KnowledgeObject`, or
        validates but disagrees with the storage key it was read under.
        """
        try:
            knowledge_object = KnowledgeObject.model_validate_json(record.payload)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            raise KnowledgeRepositoryCorruptionError(
                f"stored payload for ({record.knowledge_id!r}, {record.version_label!r}) "
                "could not be reconstructed as a valid KnowledgeObject"
            ) from exc

        if knowledge_object.knowledge_id != record.knowledge_id or knowledge_object.version.label != record.version_label:
            raise KnowledgeRepositoryCorruptionError(
                f"stored payload identity (knowledge_id={knowledge_object.knowledge_id!r}, "
                f"version.label={knowledge_object.version.label!r}) does not match its storage key "
                f"({record.knowledge_id!r}, {record.version_label!r})"
            )
        return knowledge_object
