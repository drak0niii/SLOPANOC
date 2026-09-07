"""Backward-compatibility shim for `SQLiteKnowledgeRepository`.

POST-5.1 A2: the actual implementation moved to `sqlalchemy.py` as
`SqlAlchemyKnowledgeRepository` -- the Cloud SQL PostgreSQL audit (POST-5.1
A) confirmed the class was already dialect-portable (plain generic
SQLAlchemy Core/ORM, one generic `Text` column, no SQLite-specific SQL),
so its `sqlite`-branded name/location no longer matched what it actually
does. `SQLiteKnowledgeRepository` here is a plain alias -- the SAME class
object, not a subclass or a re-implementation -- so every existing
caller/test that imports `SQLiteKnowledgeRepository` (or
`KnowledgeObjectRecord`) from this module keeps working completely
unchanged, including passing it a `postgresql+asyncpg://...` URL.

New code should prefer importing `SqlAlchemyKnowledgeRepository` from
`backend.knowledge.repository.sqlalchemy` directly -- this module exists
purely for compatibility, not as the place to add new behavior.
"""
from __future__ import annotations

from backend.knowledge.repository.sqlalchemy import (
    Base,
    KnowledgeObjectRecord,
    SqlAlchemyKnowledgeRepository,
)

SQLiteKnowledgeRepository = SqlAlchemyKnowledgeRepository

__all__ = ["Base", "KnowledgeObjectRecord", "SQLiteKnowledgeRepository"]
