"""Phase 5.1F: knowledge repository abstraction.

Answers: "how do governed KnowledgeObjects get persisted and retrieved
through one stable repository contract without coupling the Generic KM
domain to a database, vector engine, agent, document type, or deployment
platform?" See docs/KNOWLEDGE_CONTRACT.md's Phase 5.1F section for the
full architecture.

  - contracts.py -- `KnowledgeRepository` (a storage-agnostic
                     `typing.Protocol`, same pattern already used for
                     `KnowledgeSourceAdapter`/`KnowledgeContentProcessor`
                     in ingestion/processing) and the repository's own
                     error types. Imports only `KnowledgeObject` and
                     stdlib typing -- never `sqlite3`, SQLAlchemy, or any
                     other storage technology (verified by
                     backend/tests/knowledge/test_dependency_boundary.py).
  - sqlalchemy.py -- `SqlAlchemyKnowledgeRepository`, the one pragmatic
                     dialect-neutral implementation this phase builds,
                     backed by the same async-SQLAlchemy pattern already
                     established for Case persistence
                     (`backend/cases/db.py`) -- its own engine, its own
                     `DeclarativeBase`/table set (never shared with Case
                     or ADK session tables), and idempotent lazy schema
                     creation. Verified dialect-portable (POST-5.1 A) --
                     works unchanged against `sqlite+aiosqlite://...` or
                     `postgresql+asyncpg://...`.
  - sqlite.py    -- backward-compatibility shim only (POST-5.1 A2):
                     re-exports `SqlAlchemyKnowledgeRepository` as
                     `SQLiteKnowledgeRepository`, its original name/
                     location, so existing callers/tests are unaffected.
                     New code should import from sqlalchemy.py directly.

REPOSITORY vs. GOVERNANCE (§9/§35 of the phase instruction): the
repository PERSISTS already-governed state; it never DECIDES governance.
It never transitions lifecycle, never resolves currentness
(`resolve_current_version` stays exclusively in `governance/versioning.py`),
and never evaluates applicability -- see this package's own tests for the
explicit boundary proofs. `list_versions` returns the complete, unfiltered
version family (every `LifecycleStatus`) precisely so `governance/`'s own
resolution operations can be composed on top of it, deterministically, in
the caller's own code -- never inside this package.

REPOSITORY vs. RETRIEVAL ENGINE: this package answers "give me the exact
governed version I already know the identity of" or "give me every
version of this knowledge_id" -- never "find knowledge relevant to X".
No search, ranking, or embedding of any kind exists here (5.1G).

The governed repository IS the source of truth for `KnowledgeObject`
aggregates. Any future semantic/vector index is DERIVED, optional,
rebuildable, and non-authoritative -- if an index and this repository
ever disagree, the repository wins.

CORE ARCHITECTURAL INVARIANTS (same as domain/, ingestion/, processing/,
and governance/, extended to this package): no dependency on any
individual agent, ADK, Gemini, or concrete cloud/vendor SDK anywhere in
this package. `contracts.py` additionally never imports any storage
technology at all -- only `sqlalchemy.py` (and its `sqlite.py`
compatibility shim) may. `SqlAlchemyKnowledgeRepository` is already the
dialect-neutral implementation a Cloud SQL PostgreSQL deployment uses
(POST-5.1 A) -- a genuinely different storage technology (e.g. a
non-SQL/vector-native store) could still get its own dedicated
implementation later without ever touching `KnowledgeRepository` or any
of its callers.
"""
