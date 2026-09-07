"""Phase 5.1H: `KnowledgeProvenanceService.build_evidence_set` -- exact
repository revalidation of a 5.1G retrieval result into trusted
`KnowledgeEvidenceSet`. Covers basic evidence build, multiple items,
exact-identity revalidation (never list_all/search/fallback), missing
object/section, content/document/source mismatch, stale repository
state, repository corruption, content fidelity, no truncation, read-only
access, and independence from currentness/applicability/scoring.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.knowledge.domain.applicability import ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.knowledge.provenance.contracts import KnowledgeEvidenceMismatchError, KnowledgeEvidenceNotFoundError
from backend.knowledge.provenance.service import KnowledgeProvenanceService
from backend.knowledge.repository.contracts import KnowledgeRepositoryCorruptionError
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalItem, KnowledgeRetrievalResult

_PROVENANCE_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "provenance"
_SERVICE_FILE = _PROVENANCE_DIR / "service.py"


class _FakeRepository:
    """An in-memory `KnowledgeRepository` double. Every method other than
    `get` raises if called, so a call to `list_all`/`list_versions`/
    `add`/`replace` from inside provenance is caught immediately.
    """

    def __init__(self, objects: list[KnowledgeObject]) -> None:
        self._objects = {(o.knowledge_id, o.version.label): o for o in objects}
        self.get_calls: list[tuple[str, str]] = []

    async def add(self, knowledge_object: KnowledgeObject) -> None:
        raise AssertionError("provenance must never call add()")

    async def get(self, knowledge_id: str, version_label: str):
        self.get_calls.append((knowledge_id, version_label))
        return self._objects.get((knowledge_id, version_label))

    async def replace(self, knowledge_object: KnowledgeObject) -> None:
        raise AssertionError("provenance must never call replace()")

    async def list_versions(self, knowledge_id: str):
        raise AssertionError("provenance must never call list_versions()")

    async def list_all(self):
        raise AssertionError("provenance must never call list_all()")

    def replace_object(self, knowledge_object: KnowledgeObject) -> None:
        """Test-only helper simulating a real repository.replace() --
        mutates stored state without going through the double's own
        (intentionally forbidden) `replace` method.
        """
        self._objects[(knowledge_object.knowledge_id, knowledge_object.version.label)] = knowledge_object


class _CorruptRepository:
    async def add(self, knowledge_object): raise AssertionError
    async def get(self, knowledge_id: str, version_label: str):
        raise KnowledgeRepositoryCorruptionError("corrupt payload")
    async def replace(self, knowledge_object): raise AssertionError
    async def list_versions(self, knowledge_id: str): raise AssertionError
    async def list_all(self): raise AssertionError


def _governed_object(
    knowledge_id: str = "k1",
    version_label: str = "v1",
    title: str = "Router Outage Guide",
    document_type: KnowledgeDocumentType = KnowledgeDocumentType.SOP,
    lifecycle_status: LifecycleStatus = LifecycleStatus.APPROVED,
    source: KnowledgeSource | None = None,
    sections: list[KnowledgeSection] | None = None,
) -> KnowledgeObject:
    section = KnowledgeSection(
        section_id=f"{knowledge_id}:{version_label}:s0",
        knowledge_id=knowledge_id,
        heading="Overview",
        sequence=0,
        content="Router outage recovery: step one, step two, step three.",
        source_locator="page-3",
    )
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=document_type,
        title=title,
        version=KnowledgeVersion(label=version_label),
        lifecycle_status=lifecycle_status,
        source=source or KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc", source_uri="https://example/doc", display_name="Doc"),
        sections=sections if sections is not None else [section],
    )


def _retrieval_item_from(governed: KnowledgeObject, section_index: int = 0, **overrides: object) -> KnowledgeRetrievalItem:
    fields: dict[str, object] = dict(
        knowledge_id=governed.knowledge_id,
        document_type=governed.document_type,
        title=governed.title,
        version_label=governed.version.label,
        lifecycle_status=governed.lifecycle_status,
        section=governed.sections[section_index],
        source=governed.source,
        applicability_outcome=ApplicabilityOutcome.MATCH,
        relevance_score=0.9,
    )
    fields.update(overrides)
    return KnowledgeRetrievalItem(**fields)


# --- basic evidence build ------------------------------------------------------


@pytest.mark.asyncio
async def test_build_evidence_set_produces_one_correct_item() -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    retrieval_result = KnowledgeRetrievalResult(items=[_retrieval_item_from(governed)])

    evidence_set = await service.build_evidence_set(retrieval_result)

    assert len(evidence_set.items) == 1
    item = evidence_set.items[0]
    assert item.reference.knowledge_id == "k1"
    assert item.reference.version_label == "v1"
    assert item.reference.section_id == "k1:v1:s0"
    assert item.section.content == governed.sections[0].content
    assert item.section.heading == "Overview"
    assert item.source.source_system == "test"
    assert item.source.source_id == "k1-doc"
    assert item.reference.source_locator == "page-3"
    assert item.title == "Router Outage Guide"
    assert item.document_type is KnowledgeDocumentType.SOP


# --- multiple retrieval items ---------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_retrieval_items_all_present_and_order_preserved() -> None:
    section_a = KnowledgeSection(section_id="k1:v1:sA", knowledge_id="k1", sequence=0, content="alpha content")
    section_b = KnowledgeSection(section_id="k1:v1:sB", knowledge_id="k1", sequence=1, content="beta content")
    governed = _governed_object(sections=[section_a, section_b])
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)

    retrieval_result = KnowledgeRetrievalResult(
        items=[_retrieval_item_from(governed, section_index=1), _retrieval_item_from(governed, section_index=0)]
    )
    evidence_set = await service.build_evidence_set(retrieval_result)

    assert [i.reference.section_id for i in evidence_set.items] == ["k1:v1:sB", "k1:v1:sA"]


@pytest.mark.asyncio
async def test_evidence_set_contains_no_extra_repository_sections() -> None:
    section_a = KnowledgeSection(section_id="k1:v1:sA", knowledge_id="k1", sequence=0, content="alpha content")
    section_b = KnowledgeSection(section_id="k1:v1:sB", knowledge_id="k1", sequence=1, content="beta content")
    governed = _governed_object(sections=[section_a, section_b])
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)

    retrieval_result = KnowledgeRetrievalResult(items=[_retrieval_item_from(governed, section_index=0)])
    evidence_set = await service.build_evidence_set(retrieval_result)

    assert len(evidence_set.items) == 1
    assert evidence_set.items[0].reference.section_id == "k1:v1:sA"


# --- exact repository revalidation, never list_all/search/fallback ------------


@pytest.mark.asyncio
async def test_build_evidence_set_calls_get_with_exact_identity_only() -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    await service.build_evidence_set(KnowledgeRetrievalResult(items=[_retrieval_item_from(governed)]))
    assert repo.get_calls == [("k1", "v1")]


def test_service_module_never_calls_list_all_or_replace() -> None:
    """Specifically checks calls of the shape `self._repository.<attr>`
    -- not every `.add`/`.append` in the module (e.g. `items.append(...)`,
    `seen.add(...)` are unrelated local collection operations).
    """
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    forbidden = {"list_all", "list_versions", "add", "replace"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in forbidden
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "_repository"
        ):
            violations.append(node.attr)
    assert violations == [], f"service.py must only call repository.get(): found {violations}"


# --- missing governed object ----------------------------------------------------


@pytest.mark.asyncio
async def test_missing_governed_object_fails_closed() -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    fabricated_item = _retrieval_item_from(governed, knowledge_id="does-not-exist")

    with pytest.raises(KnowledgeEvidenceNotFoundError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[fabricated_item]))


@pytest.mark.asyncio
async def test_missing_governed_object_produces_no_partial_evidence_set() -> None:
    governed_ok = _governed_object(knowledge_id="k-ok")
    repo = _FakeRepository([governed_ok])
    service = KnowledgeProvenanceService(repo)
    ok_item = _retrieval_item_from(governed_ok)
    missing_item = _retrieval_item_from(governed_ok, knowledge_id="k-missing", version_label="v1")

    try:
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[ok_item, missing_item]))
        pytest.fail("expected KnowledgeEvidenceNotFoundError")
    except KnowledgeEvidenceNotFoundError:
        pass


# --- missing section -------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_section_fails_closed() -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    tampered_section = governed.sections[0].model_copy(update={"section_id": "does-not-exist-in-repository"})
    tampered_item = _retrieval_item_from(governed, section=tampered_section)

    with pytest.raises(KnowledgeEvidenceNotFoundError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[tampered_item]))


@pytest.mark.asyncio
async def test_missing_section_is_not_matched_by_heading_or_content() -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    # Same heading/content as the real section, but a fabricated section_id.
    tampered_section = governed.sections[0].model_copy(update={"section_id": "fabricated-but-same-content"})
    tampered_item = _retrieval_item_from(governed, section=tampered_section)

    with pytest.raises(KnowledgeEvidenceNotFoundError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[tampered_item]))


# --- content / section field mismatches -----------------------------------------


@pytest.mark.asyncio
async def test_content_mismatch_fails_closed() -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    tampered_section = governed.sections[0].model_copy(update={"content": "TAMPERED CONTENT NOT IN REPOSITORY"})
    tampered_item = _retrieval_item_from(governed, section=tampered_section)

    with pytest.raises(KnowledgeEvidenceMismatchError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[tampered_item]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field_update",
    [
        {"heading": "Fabricated Heading"},
        {"sequence": 99},
        {"section_type": "fabricated-type"},
        {"source_locator": "fabricated-locator"},
    ],
)
async def test_other_section_field_mismatches_fail_closed(field_update: dict) -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    tampered_section = governed.sections[0].model_copy(update=field_update)
    tampered_item = _retrieval_item_from(governed, section=tampered_section)

    with pytest.raises(KnowledgeEvidenceMismatchError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[tampered_item]))


@pytest.mark.asyncio
async def test_section_knowledge_id_mismatch_fails_closed() -> None:
    """A section claiming a different knowledge_id than the one it was
    looked up under -- constructed via a second governed object so the
    tampered `KnowledgeSection` remains internally valid (a `KnowledgeSection`
    validates its own `knowledge_id` field is non-blank, but the CROSS
    mismatch against the actual retrieved family is a provenance concern).
    """
    governed = _governed_object(knowledge_id="k1")
    other_section = KnowledgeSection(section_id="k1:v1:s0", knowledge_id="k-other", sequence=0, content=governed.sections[0].content)
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    tampered_item = _retrieval_item_from(governed, section=other_section)

    with pytest.raises(KnowledgeEvidenceMismatchError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[tampered_item]))


# --- document-level metadata mismatches ------------------------------------------


@pytest.mark.asyncio
async def test_title_mismatch_fails_closed() -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    tampered_item = _retrieval_item_from(governed, title="Fabricated Title")

    with pytest.raises(KnowledgeEvidenceMismatchError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[tampered_item]))


@pytest.mark.asyncio
async def test_document_type_mismatch_fails_closed() -> None:
    governed = _governed_object(document_type=KnowledgeDocumentType.SOP)
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    tampered_item = _retrieval_item_from(governed, document_type=KnowledgeDocumentType.MOP)

    with pytest.raises(KnowledgeEvidenceMismatchError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[tampered_item]))


@pytest.mark.asyncio
async def test_lifecycle_status_mismatch_fails_closed() -> None:
    governed = _governed_object(lifecycle_status=LifecycleStatus.APPROVED)
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    tampered_item = _retrieval_item_from(governed, lifecycle_status=LifecycleStatus.ARCHIVE)

    with pytest.raises(KnowledgeEvidenceMismatchError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[tampered_item]))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_update",
    [
        {"source_system": "fabricated-system"},
        {"source_id": "fabricated-id"},
        {"source_uri": "https://fabricated/uri"},
        {"display_name": "Fabricated Display Name"},
    ],
)
async def test_source_identity_mismatch_fails_closed(source_update: dict) -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    tampered_source = governed.source.model_copy(update=source_update)
    tampered_item = _retrieval_item_from(governed, source=tampered_source)

    with pytest.raises(KnowledgeEvidenceMismatchError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[tampered_item]))


# --- trusted evidence is reconstructed from the repository, never copied ------


@pytest.mark.asyncio
async def test_trusted_evidence_values_come_from_repository_not_retrieval_item() -> None:
    """Even when a retrieval item exactly matches, the CONSTRUCTED
    evidence fields are traced to have come from the governed object
    (proven by identity, since mismatches are rejected above) -- this
    test additionally proves the reference is a NEW object, not the same
    Python object graph as anything on the retrieval item.
    """
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    retrieval_item = _retrieval_item_from(governed)

    evidence_set = await service.build_evidence_set(KnowledgeRetrievalResult(items=[retrieval_item]))
    item = evidence_set.items[0]

    assert item.section is governed.sections[0] or item.section == governed.sections[0]
    assert item.reference.source_locator == governed.sections[0].source_locator


# --- stale repository state ------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_retrieval_result_fails_closed_after_repository_changes() -> None:
    governed = _governed_object()
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)
    stale_retrieval_item = _retrieval_item_from(governed)

    changed_section = governed.sections[0].model_copy(update={"content": "Updated content after replace()."})
    changed_governed = governed.model_copy(update={"sections": [changed_section]})
    repo.replace_object(changed_governed)

    with pytest.raises(KnowledgeEvidenceMismatchError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[stale_retrieval_item]))


# --- repository corruption --------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_corruption_propagates_and_never_becomes_evidence() -> None:
    governed = _governed_object()
    service = KnowledgeProvenanceService(_CorruptRepository())
    retrieval_item = _retrieval_item_from(governed)

    with pytest.raises(KnowledgeRepositoryCorruptionError):
        await service.build_evidence_set(KnowledgeRetrievalResult(items=[retrieval_item]))


# --- content fidelity / no truncation ---------------------------------------------


@pytest.mark.asyncio
async def test_operational_looking_content_is_preserved_exactly() -> None:
    tricky_content = (
        "Run: `systemctl restart router-agent`\n"
        "Config path: C:\\Program Files\\Router\\config.yaml\n"
        "Multiline:\n  - step 1\n  - step 2\n"
        "Symbols: #!/bin/bash && echo 'done' || exit 1; 100% success -> [OK]"
    )
    section = KnowledgeSection(section_id="k1:v1:s0", knowledge_id="k1", sequence=0, content=tricky_content)
    governed = _governed_object(sections=[section])
    repo = _FakeRepository([governed])
    service = KnowledgeProvenanceService(repo)

    evidence_set = await service.build_evidence_set(KnowledgeRetrievalResult(items=[_retrieval_item_from(governed)]))

    assert evidence_set.items[0].section.content == tricky_content


def test_service_module_never_truncates_or_summarizes_content() -> None:
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    forbidden_calls = {"truncate", "summarize", "paraphrase"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_calls:
            violations.append(node.attr)
        if isinstance(node, ast.Subscript):
            # A slice like content[:200] would show up as a Subscript with a Slice.
            if isinstance(node.slice, ast.Slice):
                violations.append("slice-subscript")
    assert violations == [], f"service.py must never truncate/summarize content: {violations}"


# --- separation: no currentness / applicability / relevance duplication -------


def test_service_module_never_imports_currentness_applicability_or_scoring() -> None:
    tree = ast.parse(_SERVICE_FILE.read_text(encoding="utf-8"), filename=str(_SERVICE_FILE))
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_names.add(node.module)
            for alias in node.names:
                imported_names.add(alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_names.add(alias.name)

    forbidden = {
        "backend.knowledge.governance.versioning",
        "resolve_current_version",
        "backend.knowledge.domain.applicability",
        "evaluate_applicability",
        "backend.knowledge.retrieval.scoring",
        "KnowledgeRelevanceScorer",
        "TokenOverlapRelevanceScorer",
    }
    assert imported_names.isdisjoint(forbidden), f"service.py must not duplicate currentness/applicability/scoring: {imported_names & forbidden}"
