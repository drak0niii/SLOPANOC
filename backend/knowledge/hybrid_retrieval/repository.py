"""Phase 6A.5: the evidence-index persistence layer.

DELIBERATELY POSTGRESQL-ONLY (unlike `backend/knowledge/repository/
sqlalchemy.py`, which is dialect-neutral by design) -- this package's
entire value proposition is PostgreSQL-specific full-text search
(`tsvector`/`to_tsvector`/`plainto_tsquery`) and pgvector similarity
search (`<=>`), matching 6A.1's own architecture decision (`docs/
GCP_INTELLIGENCE_RUNTIME.md`: "Cloud SQL PostgreSQL + pgvector remains
the selected semantic-retrieval architecture"). No SQLite compatibility
path exists or is needed here.

DERIVED SEARCH INDEX, NEVER A SECOND KNOWLEDGE AUTHORITY (§23): every
row here is deterministically rebuildable from a real, already-governed
`KnowledgeObject`/`KnowledgeSection` (see `indexable_text.py`) -- this
table is never read as authoritative Knowledge content by anything
outside `hybrid_retrieval/`.

ALL CRUD GOES THROUGH RAW SQL (`sqlalchemy.text`), NEVER THE ORM CLASS
BELOW: `EvidenceIndexTable` exists ONLY so `Base.metadata.create_all`/
Alembic's `target_metadata` have a real declarative schema to work from
in FULL (vector-available) mode. Using the ORM for reads/writes would
bind every value through the declared `Vector(768)` column type even in
DEGRADED mode (see `ensure_schema` below), where the physical column is
a plain `TEXT` -- raw SQL sidesteps that entirely and keeps exactly one
code path correct in both modes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import text

from backend.knowledge.hybrid_retrieval.contracts import ChannelHit, EvidenceIndexRecord, RetrievalChannel

__all__ = ["Base", "EvidenceIndexTable", "EvidenceIndexRepository", "VECTOR_DIMENSIONS"]

VECTOR_DIMENSIONS = 768
"""Must match `backend.knowledge_hybrid_retrieval.vertex_embedding_
provider.EXPECTED_DIMENSIONS` -- the real, live-verified output
dimensionality of `text-embedding-005` (see that module's own
docstring)."""

_TABLE_NAME = "slopanoc_knowledge_evidence_index"


class Base(DeclarativeBase):
    pass


class EvidenceIndexTable(Base):
    """The FULL-mode declarative schema -- used by `Base.metadata
    .create_all`/Alembic only, never for direct CRUD (see module
    docstring)."""

    __tablename__ = _TABLE_NAME

    evidence_id: Mapped[str] = mapped_column(primary_key=True)
    knowledge_id: Mapped[str] = mapped_column(index=True)
    version_label: Mapped[str] = mapped_column(Text)
    section_id: Mapped[str] = mapped_column(Text)
    artifact_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_derived: Mapped[bool] = mapped_column(Boolean)
    indexable_text: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(Text, index=True)
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(VECTOR_DIMENSIONS), nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding_model_version: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    embedding_dimensions: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    embedding_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    text_search_vector: Mapped[Optional[Any]] = mapped_column(TSVECTOR, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


def _row_to_record(row: Any) -> EvidenceIndexRecord:
    return EvidenceIndexRecord(
        evidence_id=row.evidence_id,
        knowledge_id=row.knowledge_id,
        version_label=row.version_label,
        section_id=row.section_id,
        artifact_id=row.artifact_id,
        is_derived=row.is_derived,
        indexable_text=row.indexable_text,
        content_hash=row.content_hash,
        embedding_model=row.embedding_model,
        embedding_model_version=row.embedding_model_version,
        embedding_dimensions=row.embedding_dimensions,
        embedding_generated_at=row.embedding_generated_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class EvidenceIndexRepository:
    """Depends only on a real PostgreSQL `database_url`
    (`postgresql+asyncpg://...`)."""

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url)
        self._session_factory = async_sessionmaker(bind=self._engine, expire_on_commit=False)
        self._schema_ready = False
        self._vector_available: Optional[bool] = None

    @property
    def vector_available(self) -> Optional[bool]:
        """`None` until `ensure_schema()` has run once; `True`/`False`
        afterward. `semantic_search` consults this to fail EXPLICITLY
        (§43) rather than silently returning an empty result
        indistinguishable from "genuinely no semantic matches"."""
        return self._vector_available

    async def ensure_schema(self) -> None:
        """Idempotent. Real production schema creation goes through the
        Alembic migration (`a1f3c9e07b21`) for a fresh environment where
        the extension can genuinely be installed -- this method exists
        for the same test-convenience reason `backend/knowledge/
        repository/sqlalchemy.py`'s own `ensure_schema` exists.

        DEGRADED-MODE FALLBACK (§42/§43, a genuine, tested capability --
        not merely a test workaround): if `CREATE EXTENSION vector`
        fails with an insufficient-privilege error (the CONFIRMED, real
        blocker this milestone's own closure report records against the
        actual DEV database), this method does NOT raise and does NOT
        silently create a full schema it cannot support -- it creates a
        DEGRADED table (identical exact/lexical columns; `embedding`
        stored as a plain, never-populated nullable `TEXT` column --
        `text_search_vector`/FTS is completely unaffected, since
        `tsvector`/`to_tsvector` need no extension at all) and sets
        `vector_available = False`. `semantic_search()` checks this flag
        and returns an EXPLICIT `unavailable` signal rather than
        executing a query that would fail or silently match nothing.
        """
        if self._schema_ready:
            return
        async with self._engine.connect() as conn:
            try:
                async with conn.begin():
                    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                self._vector_available = True
            except Exception:
                self._vector_available = False

        async with self._engine.begin() as conn:
            if self._vector_available:
                await conn.run_sync(Base.metadata.create_all)
            else:
                await conn.execute(
                    text(
                        f"CREATE TABLE IF NOT EXISTS {_TABLE_NAME} ("
                        "evidence_id TEXT PRIMARY KEY, knowledge_id TEXT NOT NULL, "
                        "version_label TEXT NOT NULL, section_id TEXT NOT NULL, "
                        "artifact_id TEXT, is_derived BOOLEAN NOT NULL, "
                        "indexable_text TEXT NOT NULL, content_hash TEXT NOT NULL, "
                        "embedding TEXT, embedding_model TEXT, embedding_model_version TEXT, "
                        "embedding_dimensions INTEGER, embedding_generated_at TIMESTAMP, "
                        "text_search_vector TSVECTOR, created_at TIMESTAMP NOT NULL, "
                        "updated_at TIMESTAMP NOT NULL)"
                    )
                )
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_evidence_index_knowledge_id_degraded ON {_TABLE_NAME} (knowledge_id)"))
                await conn.execute(text(f"CREATE INDEX IF NOT EXISTS ix_evidence_index_text_search_vector_degraded ON {_TABLE_NAME} USING GIN (text_search_vector)"))
        self._schema_ready = True

    async def close(self) -> None:
        await self._engine.dispose()

    async def upsert(self, record: EvidenceIndexRecord, embedding: Optional[list[float]], *, now: datetime) -> bool:
        """Idempotent upsert with skip-unchanged semantics (§24): if a
        row with the same `evidence_id` already exists AND its stored
        `content_hash` matches `record.content_hash` AND no NEW embedding
        is supplied, this is a no-op (returns `False`). Otherwise
        inserts/updates the row (re-generating `text_search_vector` via a
        real `to_tsvector` call) and returns `True`. In DEGRADED mode,
        `embedding` is stored as its plain `repr(list)` text form (never
        used for search, only round-trip-preserved) -- `semantic_search`
        never reads this column in degraded mode regardless.
        """
        await self.ensure_schema()
        async with self._session_factory() as session:
            existing_row = (
                await session.execute(text(f"SELECT content_hash FROM {_TABLE_NAME} WHERE evidence_id = :id"), {"id": record.evidence_id})
            ).first()
            if existing_row is not None and existing_row.content_hash == record.content_hash and embedding is None:
                return False

            embedding_value: Any = None
            if embedding is not None:
                embedding_value = str(list(embedding)) if self._vector_available else str(list(embedding))

            params: dict[str, Any] = dict(
                evidence_id=record.evidence_id,
                knowledge_id=record.knowledge_id,
                version_label=record.version_label,
                section_id=record.section_id,
                artifact_id=record.artifact_id,
                is_derived=record.is_derived,
                indexable_text=record.indexable_text,
                content_hash=record.content_hash,
                embedding=embedding_value,
                embedding_model=record.embedding_model if embedding is not None else None,
                embedding_model_version=record.embedding_model_version if embedding is not None else None,
                embedding_dimensions=record.embedding_dimensions if embedding is not None else None,
                embedding_generated_at=record.embedding_generated_at if embedding is not None else None,
                created_at=now,
                updated_at=now,
            )

            if existing_row is None:
                embedding_cast = "CAST(:embedding AS vector)" if self._vector_available else ":embedding"
                await session.execute(
                    text(
                        f"INSERT INTO {_TABLE_NAME} (evidence_id, knowledge_id, version_label, section_id, artifact_id, "
                        "is_derived, indexable_text, content_hash, embedding, embedding_model, embedding_model_version, "
                        "embedding_dimensions, embedding_generated_at, created_at, updated_at) "
                        "VALUES (:evidence_id, :knowledge_id, :version_label, :section_id, :artifact_id, :is_derived, "
                        f":indexable_text, :content_hash, {embedding_cast if embedding is not None else 'NULL'}, "
                        ":embedding_model, :embedding_model_version, :embedding_dimensions, :embedding_generated_at, "
                        ":created_at, :updated_at)"
                    ),
                    params,
                )
            else:
                set_clauses = [
                    "knowledge_id = :knowledge_id",
                    "version_label = :version_label",
                    "section_id = :section_id",
                    "artifact_id = :artifact_id",
                    "is_derived = :is_derived",
                    "indexable_text = :indexable_text",
                    "content_hash = :content_hash",
                    "updated_at = :updated_at",
                ]
                if embedding is not None:
                    embedding_cast = "CAST(:embedding AS vector)" if self._vector_available else ":embedding"
                    set_clauses += [
                        f"embedding = {embedding_cast}",
                        "embedding_model = :embedding_model",
                        "embedding_model_version = :embedding_model_version",
                        "embedding_dimensions = :embedding_dimensions",
                        "embedding_generated_at = :embedding_generated_at",
                    ]
                await session.execute(
                    text(f"UPDATE {_TABLE_NAME} SET {', '.join(set_clauses)} WHERE evidence_id = :evidence_id"),
                    params,
                )

            await session.execute(
                text(f"UPDATE {_TABLE_NAME} SET text_search_vector = to_tsvector('english', indexable_text) WHERE evidence_id = :evidence_id"),
                {"evidence_id": record.evidence_id},
            )
            await session.commit()
            return True

    async def get(self, evidence_id: str) -> Optional[EvidenceIndexRecord]:
        await self.ensure_schema()
        async with self._session_factory() as session:
            row = (await session.execute(text(f"SELECT * FROM {_TABLE_NAME} WHERE evidence_id = :id"), {"id": evidence_id})).first()
        return _row_to_record(row) if row is not None else None

    async def exact_match(self, permitted_knowledge_ids: list[str], query_text: str) -> list[ChannelHit]:
        """Deterministic exact-substring match (case-insensitive `ILIKE`),
        constrained to `permitted_knowledge_ids` INSIDE the SQL query
        itself (never after retrieval -- §4/§65.A). An empty `permitted_
        knowledge_ids` short-circuits to `[]` without ever touching the
        database (§41).
        """
        if not permitted_knowledge_ids:
            return []
        await self.ensure_schema()
        async with self._session_factory() as session:
            result = await session.execute(
                text(f"SELECT evidence_id FROM {_TABLE_NAME} WHERE knowledge_id = ANY(:ids) AND indexable_text ILIKE :pattern"),
                {"ids": permitted_knowledge_ids, "pattern": f"%{query_text}%"},
            )
            return [ChannelHit(evidence_id=row.evidence_id, channel=RetrievalChannel.EXACT, raw_score=1.0) for row in result.all()]

    async def lexical_search(self, permitted_knowledge_ids: list[str], query_text: str, limit: int) -> list[ChannelHit]:
        """Real PostgreSQL full-text search (`plainto_tsquery`/`ts_rank`)
        constrained to `permitted_knowledge_ids` INSIDE the query itself.
        """
        if not permitted_knowledge_ids:
            return []
        await self.ensure_schema()
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    f"SELECT evidence_id, ts_rank(text_search_vector, plainto_tsquery('english', :q)) AS rank "
                    f"FROM {_TABLE_NAME} "
                    "WHERE knowledge_id = ANY(:ids) "
                    "AND text_search_vector @@ plainto_tsquery('english', :q) "
                    "ORDER BY rank DESC LIMIT :limit"
                ),
                {"q": query_text, "ids": permitted_knowledge_ids, "limit": limit},
            )
            return [ChannelHit(evidence_id=row.evidence_id, channel=RetrievalChannel.LEXICAL, raw_score=float(row.rank)) for row in result.all()]

    async def semantic_search(self, permitted_knowledge_ids: list[str], query_embedding: list[float], limit: int) -> list[ChannelHit]:
        """Real pgvector cosine-distance search (`<=>`) constrained to
        `permitted_knowledge_ids` INSIDE the query itself -- exact scan,
        no ANN index (§25: current corpus scale does not justify one).
        Raw score is `1 - cosine_distance` (higher = more similar).

        EXPLICIT DEGRADED-MODE BEHAVIOR (§42/§43): if `vector_available`
        is `False`, this returns `[]` WITHOUT executing any query --
        `service.py`'s telemetry records `semantic_channel_mode=
        "degraded_no_vector_extension"` so a caller can always tell
        "genuinely zero semantic matches" apart from "semantic search did
        not run at all."
        """
        if not permitted_knowledge_ids:
            return []
        await self.ensure_schema()
        if not self._vector_available:
            return []
        async with self._session_factory() as session:
            result = await session.execute(
                text(
                    f"SELECT evidence_id, 1 - (embedding <=> CAST(:qvec AS vector)) AS similarity "
                    f"FROM {_TABLE_NAME} "
                    "WHERE knowledge_id = ANY(:ids) AND embedding IS NOT NULL "
                    "ORDER BY embedding <=> CAST(:qvec AS vector) LIMIT :limit"
                ),
                {"qvec": str(list(query_embedding)), "ids": permitted_knowledge_ids, "limit": limit},
            )
            return [ChannelHit(evidence_id=row.evidence_id, channel=RetrievalChannel.SEMANTIC, raw_score=float(row.similarity)) for row in result.all()]

    async def get_many(self, evidence_ids: list[str]) -> dict[str, EvidenceIndexRecord]:
        if not evidence_ids:
            return {}
        await self.ensure_schema()
        async with self._session_factory() as session:
            result = await session.execute(text(f"SELECT * FROM {_TABLE_NAME} WHERE evidence_id = ANY(:ids)"), {"ids": evidence_ids})
            return {row.evidence_id: _row_to_record(row) for row in result.all()}
