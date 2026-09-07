"""Phase 5.1G: `KnowledgeRetrievalService` -- currentness reuse
(`governance.versioning.resolve_current_version`, 5.1E) and family-level
diagnostics. Retrieval never duplicates currentness logic itself; it only
reacts to RESOLVED / NOT_FOUND / AMBIGUOUS and to `InvalidVersionFamilyError`
subclasses.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.repository.contracts import KnowledgeRepository
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalDiagnosticReason, KnowledgeRetrievalQuery
from backend.knowledge.retrieval.service import KnowledgeRetrievalService

_AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)
_PAST = _AS_OF - timedelta(days=365)
_FUTURE = _AS_OF + timedelta(days=365)


class _FakeRepository:
    """A minimal in-memory stand-in satisfying `KnowledgeRepository`
    structurally -- retrieval only ever calls `list_all()`, so every
    other method deliberately raises if invoked (proving read-only,
    single-operation dependence, see test_retrieval_boundaries.py).
    """

    def __init__(self, objects: list[KnowledgeObject]) -> None:
        self._objects = objects

    async def add(self, knowledge_object: KnowledgeObject) -> None:
        raise AssertionError("retrieval must never call add()")

    async def get(self, knowledge_id: str, version_label: str) -> KnowledgeObject | None:
        raise AssertionError("retrieval must never call get()")

    async def replace(self, knowledge_object: KnowledgeObject) -> None:
        raise AssertionError("retrieval must never call replace()")

    async def list_versions(self, knowledge_id: str) -> list[KnowledgeObject]:
        raise AssertionError("retrieval must never call list_versions()")

    async def list_all(self) -> list[KnowledgeObject]:
        return list(self._objects)


def _obj(
    knowledge_id: str,
    label: str,
    status: LifecycleStatus,
    content: str = "router outage recovery steps",
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    supersedes: list[str] | None = None,
    superseded_by: list[str] | None = None,
    updated_at: datetime | None = None,
) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.SOP,
        title=f"{knowledge_id} guide",
        version=KnowledgeVersion(
            label=label,
            effective_from=effective_from,
            effective_to=effective_to,
            supersedes=supersedes or [],
            superseded_by=superseded_by or [],
        ),
        lifecycle_status=status,
        source=KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-{label}"),
        sections=[
            KnowledgeSection(section_id=f"{knowledge_id}:{label}:s0", knowledge_id=knowledge_id, sequence=0, content=content)
        ],
        updated_at=updated_at,
    )


async def _retrieve(repository: KnowledgeRepository, as_of: datetime = _AS_OF, limit: int = 50):
    service = KnowledgeRetrievalService(repository)
    query = KnowledgeRetrievalQuery(query_text="router outage", as_of=as_of, limit=limit)
    return await service.retrieve(query)


# --- family grouping is by knowledge_id only ---------------------------------


@pytest.mark.asyncio
async def test_families_are_grouped_by_knowledge_id_only() -> None:
    repo = _FakeRepository(
        [
            _obj("k1", "v1", LifecycleStatus.APPROVED, effective_from=_PAST),
            _obj("k2", "v1", LifecycleStatus.APPROVED, effective_from=_PAST),
        ]
    )
    result = await _retrieve(repo)
    assert {item.knowledge_id for item in result.items} == {"k1", "k2"}


# --- RESOLVED -- single unambiguous approved version -------------------------


@pytest.mark.asyncio
async def test_single_approved_effective_version_is_retrievable() -> None:
    repo = _FakeRepository([_obj("k1", "v1", LifecycleStatus.APPROVED, effective_from=_PAST)])
    result = await _retrieve(repo)
    assert len(result.items) == 1
    assert result.items[0].version_label == "v1"
    assert result.excluded_families == []


@pytest.mark.asyncio
async def test_candidate_only_family_is_not_found_and_produces_no_diagnostic() -> None:
    """A CANDIDATE-only family resolves NOT_FOUND (no current authoritative
    version yet) -- this is a normal, silent exclusion, never a
    diagnostic (NOT_FOUND is not an invalid-family error).
    """
    repo = _FakeRepository([_obj("k1", "v1", LifecycleStatus.CANDIDATE)])
    result = await _retrieve(repo)
    assert result.items == []
    assert result.excluded_families == []


@pytest.mark.asyncio
async def test_future_effective_approved_version_is_not_yet_current() -> None:
    repo = _FakeRepository([_obj("k1", "v1", LifecycleStatus.APPROVED, effective_from=_FUTURE)])
    result = await _retrieve(repo)
    assert result.items == []
    assert result.excluded_families == []


@pytest.mark.asyncio
async def test_archived_predecessor_is_never_eligible_regardless_of_supersession() -> None:
    predecessor = _obj(
        "k1", "v1", LifecycleStatus.ARCHIVE, effective_from=_PAST, superseded_by=["v2"], updated_at=_PAST + timedelta(days=1)
    )
    successor = _obj("k1", "v2", LifecycleStatus.APPROVED, effective_from=_PAST + timedelta(days=1), supersedes=["v1"])
    repo = _FakeRepository([predecessor, successor])
    result = await _retrieve(repo)
    assert [item.version_label for item in result.items] == ["v2"]


@pytest.mark.asyncio
async def test_approved_predecessor_is_permanently_excluded_once_successor_activates() -> None:
    """The PERMANENT-SUPERSESSION RULE (governance/versioning.py), reused
    unchanged: an otherwise-eligible APPROVED, effective predecessor is
    excluded once its declared successor has genuinely activated -- this
    is the scenario where currentness reuse actually matters, since
    without exclusion the predecessor would itself be an eligible,
    RESOLVED-worthy candidate.
    """
    predecessor = _obj("k1", "v1", LifecycleStatus.APPROVED, effective_from=_PAST, superseded_by=["v2"])
    successor = _obj("k1", "v2", LifecycleStatus.APPROVED, effective_from=_PAST + timedelta(days=1), supersedes=["v1"])
    repo = _FakeRepository([predecessor, successor])
    result = await _retrieve(repo)
    assert [item.version_label for item in result.items] == ["v2"]


@pytest.mark.asyncio
async def test_approved_predecessor_remains_current_while_successor_not_yet_effective() -> None:
    predecessor = _obj("k1", "v1", LifecycleStatus.APPROVED, effective_from=_PAST, superseded_by=["v2"])
    successor = _obj("k1", "v2", LifecycleStatus.APPROVED, effective_from=_FUTURE, supersedes=["v1"])
    repo = _FakeRepository([predecessor, successor])
    result = await _retrieve(repo)
    assert [item.version_label for item in result.items] == ["v1"]


# --- AMBIGUOUS -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_ambiguous_family_is_excluded_with_diagnostic() -> None:
    repo = _FakeRepository(
        [
            _obj("k1", "va", LifecycleStatus.APPROVED, effective_from=_PAST),
            _obj("k1", "vb", LifecycleStatus.APPROVED, effective_from=_PAST),
        ]
    )
    result = await _retrieve(repo)
    assert result.items == []
    assert len(result.excluded_families) == 1
    diagnostic = result.excluded_families[0]
    assert diagnostic.knowledge_id == "k1"
    assert diagnostic.reason is KnowledgeRetrievalDiagnosticReason.CURRENT_VERSION_AMBIGUOUS
    assert diagnostic.detail is not None and "va" in diagnostic.detail and "vb" in diagnostic.detail


@pytest.mark.asyncio
async def test_ambiguous_diagnostic_detail_contains_no_raw_document_content() -> None:
    repo = _FakeRepository(
        [
            _obj("k1", "va", LifecycleStatus.APPROVED, effective_from=_PAST, content="SECRET INTERNAL CONTENT"),
            _obj("k1", "vb", LifecycleStatus.APPROVED, effective_from=_PAST, content="SECRET INTERNAL CONTENT"),
        ]
    )
    result = await _retrieve(repo)
    diagnostic = result.excluded_families[0]
    assert "SECRET INTERNAL CONTENT" not in (diagnostic.detail or "")


# --- invalid version family (governance errors) -------------------------------


@pytest.mark.asyncio
async def test_mixed_knowledge_id_family_is_impossible_by_construction() -> None:
    """Grouping by `knowledge_id` (service.py's own `_group_by_knowledge_id`)
    makes a MixedKnowledgeIdError structurally unreachable through this
    service -- documented here as a structural guarantee, not tested via
    exception injection.
    """
    repo = _FakeRepository([_obj("k1", "v1", LifecycleStatus.APPROVED, effective_from=_PAST)])
    result = await _retrieve(repo)
    assert result.excluded_families == []


@pytest.mark.asyncio
async def test_duplicate_version_label_family_is_excluded_with_diagnostic() -> None:
    repo = _FakeRepository(
        [
            _obj("k1", "v1", LifecycleStatus.APPROVED, effective_from=_PAST),
            _obj("k1", "v1", LifecycleStatus.APPROVED, effective_from=_PAST),
        ]
    )
    result = await _retrieve(repo)
    assert result.items == []
    assert len(result.excluded_families) == 1
    diagnostic = result.excluded_families[0]
    assert diagnostic.reason is KnowledgeRetrievalDiagnosticReason.INVALID_VERSION_FAMILY
    assert diagnostic.detail == "DuplicateVersionLabelError"


@pytest.mark.asyncio
async def test_unknown_supersession_reference_family_is_excluded_with_diagnostic() -> None:
    repo = _FakeRepository(
        [_obj("k1", "v1", LifecycleStatus.APPROVED, effective_from=_PAST, supersedes=["does-not-exist"])]
    )
    result = await _retrieve(repo)
    assert result.items == []
    assert result.excluded_families[0].detail == "UnknownSupersessionReferenceError"


@pytest.mark.asyncio
async def test_supersession_cycle_family_is_excluded_with_diagnostic() -> None:
    a = _obj("k1", "va", LifecycleStatus.APPROVED, effective_from=_PAST, supersedes=["vb"])
    b = _obj("k1", "vb", LifecycleStatus.APPROVED, effective_from=_PAST, supersedes=["va"])
    repo = _FakeRepository([a, b])
    result = await _retrieve(repo)
    assert result.items == []
    assert result.excluded_families[0].detail == "SupersessionCycleError"


@pytest.mark.asyncio
async def test_one_invalid_family_does_not_exclude_a_healthy_sibling_family() -> None:
    healthy = _obj("k-healthy", "v1", LifecycleStatus.APPROVED, effective_from=_PAST)
    duplicate_a = _obj("k-broken", "v1", LifecycleStatus.APPROVED, effective_from=_PAST)
    duplicate_b = _obj("k-broken", "v1", LifecycleStatus.APPROVED, effective_from=_PAST)
    repo = _FakeRepository([healthy, duplicate_a, duplicate_b])
    result = await _retrieve(repo)
    assert [item.knowledge_id for item in result.items] == ["k-healthy"]
    assert [d.knowledge_id for d in result.excluded_families] == ["k-broken"]


@pytest.mark.asyncio
async def test_empty_corpus_returns_empty_result_with_no_diagnostics() -> None:
    repo = _FakeRepository([])
    result = await _retrieve(repo)
    assert result.items == []
    assert result.excluded_families == []
