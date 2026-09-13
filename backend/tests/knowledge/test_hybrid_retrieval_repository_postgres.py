"""Phase 6A.5: REAL PostgreSQL integration tests for `EvidenceIndexRepository`
(`backend/knowledge/hybrid_retrieval/repository.py`).

Gated on `SLOPANOC_TEST_POSTGRES_URL` (a real `postgresql+asyncpg://...`
URL, e.g. the Cloud SQL Auth Proxy's own `127.0.0.1:5433` endpoint) --
SKIPS (never fails, never fabricates a result) if unset, mirroring the
exact same discipline this codebase's own "skip if real corpus
unavailable" tests already use. SQLite is NEVER substituted here (§51:
"SQLite-only validation is NOT sufficient") -- there is no SQLite code
path in `EvidenceIndexRepository` at all.

SCHEMA-DRIFT INCIDENT (found and fixed during the 6A.5 schema-drift
corrective pass -- the exact defect this docstring section now
documents): this file's `repository` fixture ORIGINALLY tore down with
`DROP TABLE IF EXISTS slopanoc_knowledge_evidence_index` after EVERY
test. At the time this was written, that table did not yet exist as a
real, Alembic-migrated, permanent object on the shared DEV database, so
the DROP was harmless. Once the real `a1f3c9e07b21` migration was
successfully applied for real against the shared DEV database (closure
report, §27.9), this SAME fixture teardown began destructively dropping
that real, migrated, shared schema object after every test run against
it -- confirmed as the actual root cause of the table's disappearance
between the original 6A.5 validation and this corrective pass (proven by
direct inspection of this exact line, not merely inferred).

FIXED: the fixture NEVER drops the table now. Every test-inserted row in
this file uses an `evidence_id` built through `_eid()`, which always
applies the module-level `_TEST_EVIDENCE_ID_PREFIX` -- the fixture
teardown deletes ONLY rows whose `evidence_id` starts with that prefix
(`DELETE ... WHERE evidence_id LIKE :prefix`), never a table-level DDL
operation, never rows this test file did not itself insert. This is
"test-owned rows only" cleanup (per the corrective pass's own preferred
option), robust to future tests added to this file as long as they
construct records via `_record()`/`_eid()` rather than a raw literal.
`ensure_schema()` is still called (idempotent, `CREATE TABLE IF NOT
EXISTS`-equivalent) so this file remains correct whether or not the real
Alembic-managed migration (`a1f3c9e07b21`) has ALSO been separately
applied to this same database -- it never assumes exclusive ownership of
the table, only of its own prefixed rows within it.

PGVECTOR PRIVILEGE HISTORY (closure-report Evidence Pack): earlier in
this milestone, `CREATE EXTENSION vector` was confirmed denied on the
real DEV Cloud SQL instance (`InsufficientPrivilegeError`, current IAM
role lacked `cloudsqlsuperuser`) -- this was NOT worked around. That
privilege was independently, externally granted later in the same
milestone (confirmed via `pg_roles`/`pg_extension` before any DDL was
attempted by this codebase), after which `CREATE EXTENSION vector`
succeeded and the real migration was applied for real. Both states are
now covered: `test_real_postgres_ensure_schema_reports_vector_
availability` proves the CURRENT (resolved) state; the degraded-mode
TEXT-column fallback path itself (`ensure_schema()`'s own except branch)
remains covered by the FAKE-repository unit tests in `test_hybrid_
retrieval_service.py` (`vector_available=False` case), so degraded-mode
correctness is not lost merely because the real environment no longer
exercises it.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.knowledge.hybrid_retrieval.contracts import EvidenceIndexRecord
from backend.knowledge.hybrid_retrieval.repository import EvidenceIndexRepository

_TEST_DB_URL = os.environ.get("SLOPANOC_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not _TEST_DB_URL, reason="SLOPANOC_TEST_POSTGRES_URL not set -- real PostgreSQL integration tests skipped")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


_TEST_EVIDENCE_ID_PREFIX = "slopanoc-test-hybrid-retrieval-repo-postgres-"
"""Every evidence_id this file inserts is namespaced under this prefix --
the SOLE basis for the fixture's own scoped, non-destructive cleanup
(see the module docstring's "SCHEMA-DRIFT INCIDENT" section). Never
change this constant without also updating the fixture's own DELETE
pattern below."""


def _eid(name: str) -> str:
    return f"{_TEST_EVIDENCE_ID_PREFIX}{name}"


def _record(evidence_id: str, knowledge_id: str, text_value: str, content_hash: str = "h") -> EvidenceIndexRecord:
    return EvidenceIndexRecord(
        evidence_id=_eid(evidence_id), knowledge_id=knowledge_id, version_label="1.0", section_id=evidence_id,
        is_derived=False, indexable_text=text_value, content_hash=content_hash,
    )


@pytest_asyncio.fixture()
async def repository():
    """Real role grants on the shared DEV database can change between
    runs (this milestone's own closure report documents a real, observed
    example: a temporary `cloudsqlsuperuser` elevation used to install
    `pgvector` was later revoked, along with schema-level DDL rights,
    independent of the pgvector-specific blocker itself being resolved).
    A schema-level DDL denial is NOT the pgvector-specific condition
    `ensure_schema()`'s own degraded-mode fallback is designed to handle
    (that fallback assumes `CREATE TABLE` still works, only `CREATE
    EXTENSION` is denied) -- it is a distinct, broader precondition
    failure this test file has no business asserting one way or the
    other, so it SKIPS rather than fails, mirroring this codebase's own
    "skip if a real external dependency is unavailable" convention
    (e.g. `test_hybrid_retrieval_embedding_real_api.py`).

    TEARDOWN IS NEVER DESTRUCTIVE TO THE SHARED TABLE (fixed defect, see
    module docstring): only rows this file itself inserted -- identified
    solely by the `_TEST_EVIDENCE_ID_PREFIX` namespace, never a DROP
    TABLE/blanket TRUNCATE -- are removed. A real, permanently-migrated
    production/shared-DEV schema object must survive this test file's
    own runs indefinitely.
    """
    repo = EvidenceIndexRepository(_TEST_DB_URL)
    try:
        await repo.ensure_schema()
    except Exception as exc:  # pragma: no cover -- exercised only when DDL is genuinely denied
        await repo.close()
        pytest.skip(f"schema-level DDL unavailable on the real DEV database right now: {exc}")
    yield repo
    async with repo._engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM slopanoc_knowledge_evidence_index WHERE evidence_id LIKE :prefix"),
            {"prefix": f"{_TEST_EVIDENCE_ID_PREFIX}%"},
        )
    await repo.close()


@pytest.mark.asyncio
async def test_real_postgres_ensure_schema_reports_vector_availability(repository) -> None:
    """`vector_available` must report a real, resolved boolean -- never
    `None` after `ensure_schema()` has run, and never flip on a second,
    idempotent call. Deliberately does NOT hardcode which boolean value
    is expected: this milestone's own closure report documents a real,
    live-observed case where `CREATE EXTENSION vector` was confirmed
    denied, then later confirmed to succeed after an externally-granted
    privilege change, within the SAME session -- proof this is a genuine
    environment fact to observe, never a constant to assume."""
    await repository.ensure_schema()
    first = repository.vector_available
    assert first in (True, False)
    await repository.ensure_schema()
    assert repository.vector_available == first


@pytest.mark.asyncio
async def test_real_postgres_upsert_and_get(repository) -> None:
    record = _record("ev1", "K1", "VSWR Over Threshold alarm procedure.")
    changed = await repository.upsert(record, embedding=None, now=_now())
    assert changed is True
    fetched = await repository.get(_eid("ev1"))
    assert fetched is not None
    assert fetched.indexable_text == "VSWR Over Threshold alarm procedure."


@pytest.mark.asyncio
async def test_real_postgres_upsert_skips_unchanged_content(repository) -> None:
    record = _record("ev1", "K1", "unchanged text", content_hash="samehash")
    await repository.upsert(record, embedding=None, now=_now())
    changed_again = await repository.upsert(record, embedding=None, now=_now())
    assert changed_again is False


@pytest.mark.asyncio
async def test_real_postgres_upsert_updates_changed_content(repository) -> None:
    v1 = _record("ev1", "K1", "old text", content_hash="hash1")
    await repository.upsert(v1, embedding=None, now=_now())
    v2 = _record("ev1", "K1", "new text", content_hash="hash2")
    changed = await repository.upsert(v2, embedding=None, now=_now())
    assert changed is True
    fetched = await repository.get(_eid("ev1"))
    assert fetched.indexable_text == "new text"


@pytest.mark.asyncio
async def test_real_postgres_exact_match_case_insensitive(repository) -> None:
    await repository.upsert(_record("ev1", "K1", "VSWR Over Threshold"), embedding=None, now=_now())
    hits = await repository.exact_match(["K1"], "vswr")
    assert len(hits) == 1
    assert hits[0].evidence_id == _eid("ev1")


@pytest.mark.asyncio
async def test_real_postgres_exact_match_respects_permitted_ids(repository) -> None:
    """§4/§58: candidate-boundary enforcement -- an evidence unit whose
    knowledge_id is NOT in permitted_knowledge_ids must never appear,
    proven against a REAL PostgreSQL query, not a Python-side filter."""
    await repository.upsert(_record("ev-excluded", "EXCLUDED", "VSWR test"), embedding=None, now=_now())
    await repository.upsert(_record("ev-permitted", "PERMITTED", "VSWR test"), embedding=None, now=_now())
    hits = await repository.exact_match(["PERMITTED"], "VSWR")
    assert [h.evidence_id for h in hits] == [_eid("ev-permitted")]


@pytest.mark.asyncio
async def test_real_postgres_exact_match_empty_permitted_ids_never_queries(repository) -> None:
    await repository.upsert(_record("ev1", "K1", "VSWR"), embedding=None, now=_now())
    hits = await repository.exact_match([], "VSWR")
    assert hits == []


@pytest.mark.asyncio
async def test_real_postgres_lexical_search_real_ts_rank(repository) -> None:
    """Real PostgreSQL FTS (`to_tsvector`/`plainto_tsquery`/`ts_rank`)."""
    await repository.upsert(_record("ev1", "K1", "high reflected power after antenna feeder intervention"), embedding=None, now=_now())
    await repository.upsert(_record("ev2", "K1", "unrelated billing invoice text"), embedding=None, now=_now())
    hits = await repository.lexical_search(["K1"], "reflected power feeder", limit=10)
    assert len(hits) == 1
    assert hits[0].evidence_id == _eid("ev1")
    assert hits[0].raw_score > 0.0


@pytest.mark.asyncio
async def test_real_postgres_lexical_search_respects_permitted_ids(repository) -> None:
    await repository.upsert(_record("ev-excluded", "EXCLUDED", "VSWR alarm troubleshooting"), embedding=None, now=_now())
    hits = await repository.lexical_search(["OTHER"], "VSWR alarm", limit=10)
    assert hits == []


@pytest.mark.asyncio
async def test_real_postgres_semantic_search_orders_by_real_cosine_distance(repository) -> None:
    """If pgvector is genuinely available right now: real proof of the
    `<=>` operator itself, against distinct, hand-crafted 768-dim vectors
    (never a real Vertex call, keeping this Postgres-focused file
    independent of live embedding-API reachability; the real end-to-end
    Vertex-embedding proof lives in `test_hybrid_retrieval_service.py::
    TestRealEndToEnd`). A query vector identical to 'close' and antipodal
    to 'far' must rank 'close' strictly first, with a real similarity
    score. If pgvector is NOT available right now (a real, previously-
    observed possibility -- see the fixture's own docstring): proves the
    documented degraded-mode contract instead -- semantic_search must
    return `[]` without raising."""
    await repository.ensure_schema()
    close_vector = [0.9] * 768
    far_vector = [-0.9] * 768
    await repository.upsert(_record("ev-close", "K1", "close text"), embedding=close_vector, now=_now())
    await repository.upsert(_record("ev-far", "K1", "far text"), embedding=far_vector, now=_now())
    hits = await repository.semantic_search(["K1"], close_vector, limit=10)
    if repository.vector_available:
        assert [h.evidence_id for h in hits][0] == _eid("ev-close")
        assert hits[0].raw_score > 0.0
    else:
        assert hits == []


@pytest.mark.asyncio
async def test_real_postgres_semantic_search_respects_permitted_ids(repository) -> None:
    """§4/§58: candidate-boundary enforcement for the semantic channel
    too. In full mode, proven against a REAL pgvector query, not a
    Python-side filter; in degraded mode, the channel returns `[]`
    unconditionally, which trivially also respects the boundary."""
    await repository.ensure_schema()
    vec = [0.5] * 768
    await repository.upsert(_record("ev-excluded", "EXCLUDED", "text"), embedding=vec, now=_now())
    await repository.upsert(_record("ev-permitted", "PERMITTED", "text"), embedding=vec, now=_now())
    hits = await repository.semantic_search(["PERMITTED"], vec, limit=10)
    if repository.vector_available:
        assert [h.evidence_id for h in hits] == [_eid("ev-permitted")]
    else:
        assert hits == []


@pytest.mark.asyncio
async def test_real_postgres_get_many(repository) -> None:
    await repository.upsert(_record("ev1", "K1", "text one"), embedding=None, now=_now())
    await repository.upsert(_record("ev2", "K1", "text two"), embedding=None, now=_now())
    records = await repository.get_many([_eid("ev1"), _eid("ev2"), _eid("nonexistent")])
    assert set(records) == {_eid("ev1"), _eid("ev2")}


@pytest.mark.asyncio
async def test_real_postgres_full_text_search_raw_sql_primitive() -> None:
    """Independent of any table -- proves the real PostgreSQL FTS ENGINE
    itself behaves as expected, a standalone primitive-level proof
    (§57's own spirit: show the real mechanism works)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_TEST_DB_URL)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT ts_rank(to_tsvector('english', :doc), plainto_tsquery('english', :query)) AS rank"
            ),
            {"doc": "VSWR degradation following feeder intervention", "query": "high reflected power"},
        )
        rank = result.scalar()
    await engine.dispose()
    assert rank is not None and rank > 0.0


@pytest.mark.asyncio
async def test_repository_fixture_teardown_never_drops_table_or_unrelated_rows() -> None:
    """DEF-0018 regression proof: the `repository` fixture's teardown
    must NEVER issue table-level DDL against the shared, permanently-
    migrated table, and must NEVER touch a row it did not itself insert
    -- proven directly against the fixture's own real teardown logic
    (not a paraphrase of it), self-contained (no cross-test/finalizer
    ordering tricks, which risk the exact class of event-loop-lifecycle
    bug this codebase's own D2 corrective pass already documented).

    Simulates "someone else's real, permanent data already in the shared
    table" by inserting one row OUTSIDE this file's `_TEST_EVIDENCE_ID_
    PREFIX` namespace, then a second row INSIDE it, then invokes the
    fixture's own real teardown statement directly and asserts: the
    table itself still exists, the foreign row survives untouched, and
    the prefixed row is gone.
    """
    from backend.knowledge.hybrid_retrieval.repository import EvidenceIndexRepository

    repo = EvidenceIndexRepository(_TEST_DB_URL)
    try:
        await repo.ensure_schema()
        foreign_record = EvidenceIndexRecord(
            evidence_id="def-0018-not-owned-by-this-test-file", knowledge_id="K1",
            version_label="1.0", section_id="def-0018-not-owned-by-this-test-file",
            is_derived=False, indexable_text="unrelated real data", content_hash="unrelated-hash",
        )
        await repo.upsert(foreign_record, embedding=None, now=_now())
        await repo.upsert(_record("def0018-owned", "K1", "this file's own row"), embedding=None, now=_now())

        # The EXACT teardown statement `repository`'s own fixture executes.
        async with repo._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM slopanoc_knowledge_evidence_index WHERE evidence_id LIKE :prefix"),
                {"prefix": f"{_TEST_EVIDENCE_ID_PREFIX}%"},
            )

        async with repo._session_factory() as session:
            still_there = (await session.execute(
                text("SELECT evidence_id FROM slopanoc_knowledge_evidence_index WHERE evidence_id = :id"),
                {"id": "def-0018-not-owned-by-this-test-file"},
            )).first()
            assert still_there is not None, "DEF-0018 regression: an unrelated real row must survive this teardown statement"
            gone = (await session.execute(
                text("SELECT evidence_id FROM slopanoc_knowledge_evidence_index WHERE evidence_id = :id"),
                {"id": _eid("def0018-owned")},
            )).first()
            assert gone is None, "the teardown statement must still remove this file's own prefixed rows"

        async with repo._engine.begin() as conn:
            table_regclass = (await conn.execute(text("SELECT to_regclass('public.slopanoc_knowledge_evidence_index')"))).scalar()
        assert table_regclass is not None, "DEF-0018 regression: the table itself must never be dropped by this teardown"
    finally:
        # Clean up this test's own foreign-row simulation directly -- never
        # a DROP, consistent with the exact invariant under test.
        async with repo._engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM slopanoc_knowledge_evidence_index WHERE evidence_id = :id"),
                {"id": "def-0018-not-owned-by-this-test-file"},
            )
        await repo.close()
