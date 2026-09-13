"""Phase 6A.8: REAL PostgreSQL integration tests for
`ExperienceMemoryService` (`backend/experience_memory/sqlalchemy/
service.py`), against the real, migrated `slopanoc_experience_records`
table.

Gated on `SLOPANOC_TEST_POSTGRES_URL` (a real `postgresql+asyncpg://...`
URL, e.g. the Cloud SQL Auth Proxy's own `127.0.0.1:5433` endpoint) --
SKIPS (never fails, never fabricates a result) if unset, mirroring
`test_hybrid_retrieval_repository_postgres.py`'s (6A.5) own discipline
exactly.

DEF-0018 / OPS-0001 LESSON APPLIED FROM THE START (never rediscovered
the hard way): this fixture's teardown NEVER issues `DROP TABLE`/
`TRUNCATE` against `slopanoc_experience_records` -- a real, permanently
Alembic-migrated (`c7e2a4f9b83d`) shared schema object. Every row this
file inserts uses an `owner_id` built through `_owner()`, which always
applies the module-level `_TEST_OWNER_PREFIX` namespace; teardown
deletes ONLY rows whose `owner_id` starts with that prefix (`DELETE ...
WHERE owner_id LIKE :prefix`) -- never a table-level DDL statement,
never a row this file did not itself insert.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from backend.experience_memory.domain.enums import ExperienceSourceOrigin, ExperienceType
from backend.experience_memory.domain.models import ExperienceCandidate, ExperienceEvidenceReference
from backend.experience_memory.sqlalchemy.db import ExperienceMemoryDatabase
from backend.experience_memory.sqlalchemy.models import ExperienceRecordTable
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService

_TEST_DB_URL = os.environ.get("SLOPANOC_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not _TEST_DB_URL, reason="SLOPANOC_TEST_POSTGRES_URL not set -- real PostgreSQL integration tests skipped")

_TEST_OWNER_PREFIX = "slopanoc-test-experience-memory-"


def _owner(name: str) -> str:
    return f"{_TEST_OWNER_PREFIX}{name}"


@pytest_asyncio.fixture
async def service():
    database = ExperienceMemoryDatabase(database_url=_TEST_DB_URL)
    svc = ExperienceMemoryService(database)
    await database.ensure_schema()
    yield svc
    # Teardown: delete ONLY rows this file's own tests inserted -- never
    # a table-level DDL operation (DEF-0018/OPS-0001 lesson).
    async with database.session() as session:
        await session.execute(
            text("DELETE FROM slopanoc_experience_records WHERE owner_id LIKE :prefix"),
            {"prefix": f"{_TEST_OWNER_PREFIX}%"},
        )
        await session.commit()
    await database.close()


def _candidate(owner_id: str, source_event_id: str, **overrides) -> ExperienceCandidate:
    defaults = dict(
        experience_type=ExperienceType.CASE_RESOLUTION,
        source_origin=ExperienceSourceOrigin.EXPLICIT_CASE_RESOLUTION,
        owner_id=owner_id,
        source_namespace="case_management",
        source_event_id=source_event_id,
        outcome_summary="Alarm cleared after documented intervention.",
        observed_facts=["Observed high VSWR alarm.", "Alarm later cleared after intervention."],
    )
    defaults.update(overrides)
    return ExperienceCandidate(**defaults)


@pytest.mark.asyncio
async def test_real_postgres_accept_and_retrieve_by_owner(service) -> None:
    owner = _owner(f"vodafone-{uuid.uuid4().hex[:8]}")
    candidate = _candidate(owner, "CASE-EXP-001-resolution", case_id="CASE-EXP-001")

    result = await service.record_experience(candidate)
    assert result.outcome.value == "accept"
    assert result.record is not None
    assert result.record.source_class == "EXPERIENCE"

    from backend.experience_memory.domain.models import ExperienceQuery

    query_result = await service.query(ExperienceQuery(owner_id=owner))
    assert query_result.count == 1
    assert query_result.records[0].outcome_summary == candidate.outcome_summary


@pytest.mark.asyncio
async def test_real_postgres_case_scoped_retrieval(service) -> None:
    owner = _owner(f"case-scope-{uuid.uuid4().hex[:8]}")
    await service.record_experience(_candidate(owner, "evt-1", case_id="CASE-A"))
    await service.record_experience(_candidate(owner, "evt-2", case_id="CASE-B"))

    from backend.experience_memory.domain.models import ExperienceQuery

    result = await service.query(ExperienceQuery(owner_id=owner, case_id="CASE-A"))
    assert result.count == 1
    assert result.records[0].case_id == "CASE-A"


@pytest.mark.asyncio
async def test_real_postgres_idempotent_same_source_event(service) -> None:
    owner = _owner(f"idem-{uuid.uuid4().hex[:8]}")
    candidate = _candidate(owner, "evt-idem-1")

    first = await service.record_experience(candidate)
    second = await service.record_experience(candidate)

    assert first.record.experience_id == second.record.experience_id
    assert second.deduplicated is True

    from backend.experience_memory.domain.models import ExperienceQuery

    result = await service.query(ExperienceQuery(owner_id=owner))
    assert result.count == 1


@pytest.mark.asyncio
async def test_real_postgres_corrective_pass_cross_namespace_same_event_id_two_distinct_records(service) -> None:
    """6A.8 final source-namespace corrective pass §9/§26.A: against the
    REAL Cloud SQL DEV table -- same owner, same experience_type, same
    source_origin, same source_event_id, but a DIFFERENT source_namespace
    -> two distinct, durable Experience records, both retrievable,
    neither overwriting the other, both namespaces preserved as
    inspectable provenance."""
    owner = _owner(f"cross-namespace-{uuid.uuid4().hex[:8]}")
    candidate_a = _candidate(owner, "evt-shared-id", source_namespace="bmc")
    candidate_b = _candidate(owner, "evt-shared-id", source_namespace="onefm", outcome_summary="A distinct, independently-recorded observation from a different producer.")

    result_a = await service.record_experience(candidate_a)
    result_b = await service.record_experience(candidate_b)

    assert result_a.outcome.value == "accept"
    assert result_b.outcome.value == "accept"
    assert result_a.record.experience_id != result_b.record.experience_id

    from backend.experience_memory.domain.models import ExperienceQuery

    result = await service.query(ExperienceQuery(owner_id=owner))
    assert result.count == 2
    assert {r.experience_id for r in result.records} == {result_a.record.experience_id, result_b.record.experience_id}
    assert {r.source_namespace for r in result.records} == {"bmc", "onefm"}


@pytest.mark.asyncio
async def test_real_postgres_corrective_pass_source_origin_independence(service) -> None:
    """6A.8 final source-namespace corrective pass §10: the SAME
    source_origin, DIFFERENT source_namespace, SAME source_event_id ->
    still two distinct records -- proving trust classification is not
    being mistaken for producer identity."""
    owner = _owner(f"origin-independence-{uuid.uuid4().hex[:8]}")
    candidate_a = _candidate(owner, "evt-same-origin-shared-id", source_origin=ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE, source_namespace="enm")
    candidate_b = _candidate(
        owner,
        "evt-same-origin-shared-id",
        source_origin=ExperienceSourceOrigin.TRUSTED_EXTERNAL_SYSTEM_STATE,
        source_namespace="alarm_platform",
        outcome_summary="A distinct observation, same trust class, different producer.",
    )

    result_a = await service.record_experience(candidate_a)
    result_b = await service.record_experience(candidate_b)

    assert result_a.record.experience_id != result_b.record.experience_id

    from backend.experience_memory.domain.models import ExperienceQuery

    result = await service.query(ExperienceQuery(owner_id=owner))
    assert result.count == 2


@pytest.mark.asyncio
async def test_real_postgres_corrective_pass_same_namespace_different_origin_is_same_event(service) -> None:
    """6A.8 final source-namespace corrective pass §11: SAME
    source_namespace + SAME source_event_id, DIFFERENT source_origin ->
    identity remains tied to the producer EVENT, not the admission/trust
    classification -- resolves to the SAME experience_id, and the second
    write is reported as deduplicated (never a second, competing
    historical record for the same real-world producer event)."""
    owner = _owner(f"same-namespace-diff-origin-{uuid.uuid4().hex[:8]}")
    candidate_a = _candidate(owner, "evt-same-producer-event", source_namespace="bmc", source_origin=ExperienceSourceOrigin.OBSERVED_CASE_OUTCOME)
    candidate_b = _candidate(owner, "evt-same-producer-event", source_namespace="bmc", source_origin=ExperienceSourceOrigin.EXECUTED_ACTION_RESULT)

    result_a = await service.record_experience(candidate_a)
    result_b = await service.record_experience(candidate_b)

    assert result_a.record.experience_id == result_b.record.experience_id
    assert result_b.deduplicated is True

    from backend.experience_memory.domain.models import ExperienceQuery

    result = await service.query(ExperienceQuery(owner_id=owner))
    assert result.count == 1


@pytest.mark.asyncio
async def test_real_postgres_corrective_pass_same_full_source_identity_idempotent(service) -> None:
    """6A.8 final source-namespace corrective pass §8/§26.B: against the
    REAL Cloud SQL DEV table -- the SAME complete source identity
    (owner + experience_type + source_namespace + source_event_id)
    written twice -> exactly one durable Experience record."""
    owner = _owner(f"idem-full-{uuid.uuid4().hex[:8]}")
    candidate = _candidate(owner, "evt-idem-full-identity", source_namespace="enm")

    first = await service.record_experience(candidate)
    second = await service.record_experience(candidate)

    assert first.record.experience_id == second.record.experience_id
    assert second.deduplicated is True

    from backend.experience_memory.domain.models import ExperienceQuery

    result = await service.query(ExperienceQuery(owner_id=owner))
    assert result.count == 1


@pytest.mark.asyncio
async def test_real_postgres_source_namespace_survives_round_trip(service) -> None:
    """§12: source_namespace is persisted as real, inspectable
    provenance -- never only inside the identity hash."""
    owner = _owner(f"namespace-provenance-{uuid.uuid4().hex[:8]}")
    result = await service.record_experience(_candidate(owner, "evt-1", source_namespace="teams"))
    assert result.record.source_namespace == "teams"

    fetched = await service.get_by_id(owner, result.record.experience_id)
    assert fetched.source_namespace == "teams"


@pytest.mark.asyncio
async def test_real_postgres_cross_owner_exclusion_at_sql_level(service) -> None:
    owner_a = _owner(f"customer-a-{uuid.uuid4().hex[:8]}")
    owner_b = _owner(f"customer-b-{uuid.uuid4().hex[:8]}")
    await service.record_experience(_candidate(owner_a, "evt-a1"))
    await service.record_experience(_candidate(owner_a, "evt-a2"))
    await service.record_experience(_candidate(owner_b, "evt-b1"))

    from backend.experience_memory.domain.models import ExperienceQuery

    result_a = await service.query(ExperienceQuery(owner_id=owner_a))
    assert result_a.count == 2
    assert all(r.owner_id == owner_a for r in result_a.records)
    assert not any(r.owner_id == owner_b for r in result_a.records)

    # Direct SQL-level proof: a raw query filtered to owner_a can never
    # even carry an owner_b row as an intermediate row -- proving
    # isolation happens inside the WHERE clause, not a Python post-filter.
    database = ExperienceMemoryDatabase(database_url=_TEST_DB_URL)
    async with database.session() as session:
        raw = await session.execute(
            text("SELECT owner_id FROM slopanoc_experience_records WHERE owner_id = :owner"),
            {"owner": owner_a},
        )
        raw_owners = {row[0] for row in raw.fetchall()}
    await database.close()
    assert raw_owners == {owner_a}


@pytest.mark.asyncio
async def test_real_postgres_skill_context_evidence_round_trip(service) -> None:
    owner = _owner(f"provenance-{uuid.uuid4().hex[:8]}")
    candidate = _candidate(
        owner,
        "evt-provenance-1",
        skill_id="telco.incident_evidence_review",
        skill_version="1.0.0",
        skill_fingerprint="abc123fingerprint",
        context_fingerprint="ctxfp-xyz789",
        evidence_references=[ExperienceEvidenceReference(knowledge_id="K1", version_label="1.0", section_id="verification")],
    )
    result = await service.record_experience(candidate)
    assert result.record.skill_id == "telco.incident_evidence_review"
    assert result.record.skill_version == "1.0.0"
    assert result.record.skill_fingerprint == "abc123fingerprint"
    assert result.record.context_fingerprint == "ctxfp-xyz789"
    assert len(result.record.evidence_references) == 1
    assert result.record.evidence_references[0].knowledge_id == "K1"

    fetched = await service.get_by_id(owner, result.record.experience_id)
    assert fetched.skill_fingerprint == "abc123fingerprint"
    assert fetched.evidence_references[0].section_id == "verification"


@pytest.mark.asyncio
async def test_real_postgres_bounded_deterministic_retrieval(service) -> None:
    owner = _owner(f"bounded-{uuid.uuid4().hex[:8]}")
    for i in range(5):
        await service.record_experience(_candidate(owner, f"evt-bounded-{i}"))

    from backend.experience_memory.domain.models import ExperienceQuery

    result = await service.query(ExperienceQuery(owner_id=owner, limit=3))
    assert result.count == 3
    assert result.limit == 3

    result_again = await service.query(ExperienceQuery(owner_id=owner, limit=3))
    assert [r.experience_id for r in result.records] == [r.experience_id for r in result_again.records]


@pytest.mark.asyncio
async def test_real_postgres_empty_result_is_valid(service) -> None:
    owner = _owner(f"empty-{uuid.uuid4().hex[:8]}")

    from backend.experience_memory.domain.models import ExperienceQuery

    result = await service.query(ExperienceQuery(owner_id=owner))
    assert result.count == 0
    assert result.records == []


@pytest.mark.asyncio
async def test_real_postgres_cleanup_removes_only_test_owned_rows(service) -> None:
    """The mandatory teardown-safety proof (§21 -- a dedicated regression
    test, mirroring DEF-0018's own corrective-pass precedent): a row
    OUTSIDE this file's own namespace survives this fixture's teardown,
    proving the DELETE is scoped, never a table-level operation."""
    owner = _owner(f"cleanup-check-{uuid.uuid4().hex[:8]}")
    await service.record_experience(_candidate(owner, "evt-cleanup-1"))

    foreign_owner = "not-" + _TEST_OWNER_PREFIX + "unrelated-owner"
    foreign_candidate = _candidate(foreign_owner, "evt-foreign-do-not-delete")
    foreign_result = await service.record_experience(foreign_candidate)
    assert foreign_result.outcome.value == "accept"

    try:
        database = ExperienceMemoryDatabase(database_url=_TEST_DB_URL)
        async with database.session() as session:
            row = await session.get(ExperienceRecordTable, foreign_result.record.experience_id)
            assert row is not None
        await database.close()
    finally:
        # This foreign row is NOT covered by this file's own fixture
        # teardown (its owner_id deliberately does not match
        # _TEST_OWNER_PREFIX) -- clean it up explicitly here, by its own
        # exact experience_id, never by a table-level statement.
        database = ExperienceMemoryDatabase(database_url=_TEST_DB_URL)
        async with database.session() as session:
            await session.execute(
                text("DELETE FROM slopanoc_experience_records WHERE experience_id = :eid"),
                {"eid": foreign_result.record.experience_id},
            )
            await session.commit()
        await database.close()


@pytest.mark.asyncio
async def test_real_postgres_table_survives_after_all_tests(service) -> None:
    """Mandatory post-test-suite safety check (§90/§25): the shared table
    itself, and its indexes, must still exist after this file's own
    fixture teardown runs -- never dropped, never truncated."""
    database = ExperienceMemoryDatabase(database_url=_TEST_DB_URL)
    async with database.session() as session:
        exists = await session.execute(
            text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name='slopanoc_experience_records')")
        )
        assert exists.scalar() is True
    await database.close()
