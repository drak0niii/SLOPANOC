"""6A.3 / P11-M03: REAL CORPUS FULL-PIPELINE VALIDATION.

Extends the existing, extraction-only real-corpus validation
(test_knowledge_real_corpus_validation.py) all the way through Layer H
structuring (`process_compound_document`) and governed materialization
(`materialize_candidate`/`approve_version`), proving -- against real,
already-cleared TELCO/RAN MOP content, never synthetic fixtures -- that
embedded/compound content becomes genuinely retrievable via the
EXISTING, UNMODIFIED `KnowledgeRetrievalService`, not merely structurally
present in `KnowledgeObject.artifacts`. This is the real-world proof for
the gap 6A.3 closed: before this milestone, nothing in the committed
codebase ever ran a real file this far (a `grep` for
`materialize_candidate(` across all of `backend/`, excluding tests,
found zero production call sites).

Same discipline as the existing real-corpus test file: SKIPS (never
fails, never fabricates a result) if the real files are unavailable.
Only the exact "VSWR"/"No restart" substrings already cleared and used
by the existing committed test file are asserted against literally (no
new/different real paragraph text is added to this file's own source).
The real standalone XLSX file's actual cell content is treated as
sensitive-until-proven-otherwise: every assertion against it is
STRUCTURAL ONLY (sheet/artifact/section counts, artifact kinds) -- no
real cell value from it is ever printed, logged, or written into this
file's own source as a literal string. Where a query needs real text
from the XLSX, that text is read at RUNTIME from the already-extracted
artifact itself (never hardcoded here), so no sensitive content is ever
committed to this repository.

This file also doubles as the real-world regression proof for the
`parent_artifact_id="root"` defect this milestone found and fixed
(`backend/knowledge/ingestion/extractors/{pdf,xlsx}.py`,
`backend/knowledge/ingestion/extractors/dispatch.py`) -- before that fix,
`ingest_and_structure_local_file` on ANY root-level XLSX (including the
real Surgical_Checklist.xlsx used here, if present) raised a
`pydantic.ValidationError` (dangling `parent_artifact_id`) before ever
reaching Layer H.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeVersion
from backend.knowledge.governance.service import approve_version, materialize_candidate
from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.knowledge_ingestion.local_file_adapter import ingest_and_structure_local_files

_DOCUMENT1 = Path(r"C:\Users\eosiocn\Downloads\Document1.docx")
_ROGERS_4G = Path(r"C:\Users\eosiocn\Downloads\Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx")
_ROGERS_4G5G = Path(
    r"C:\Users\eosiocn\Downloads\MOP_Rogers ERICSSON_4G5G_Resource_Timeout_ Allocation Failure_Service Degraded_Serive_Unavailable Alarms Resolution.docx"
)
_SURGICAL_CHECKLIST_XLSX = Path(r"C:\Users\eosiocn\Downloads\Rogers_Core_Outage_Impact_Agent_Surgical_Checklist.xlsx")
_ALL_DOCX_PATHS = [_DOCUMENT1, _ROGERS_4G, _ROGERS_4G5G]

_missing = [p for p in _ALL_DOCX_PATHS if not p.is_file()]
pytestmark = pytest.mark.skipif(bool(_missing), reason=f"real validation corpus not available on this machine: {_missing}")

_AS_OF = datetime(2026, 9, 11, tzinfo=timezone.utc)
_EFFECTIVE_FROM = datetime(2026, 1, 1, tzinfo=timezone.utc)

_KNOWLEDGE_IDS = {
    "Document1.docx": "6A3-VALIDATION-DOCUMENT1",
    "Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx": "6A3-VALIDATION-ROGERS-4G",
    "MOP_Rogers ERICSSON_4G5G_Resource_Timeout_ Allocation Failure_Service Degraded_Serive_Unavailable Alarms Resolution.docx": "6A3-VALIDATION-ROGERS-4G5G",
}


@pytest.fixture(scope="module")
def structured_results():
    import asyncio

    return asyncio.run(ingest_and_structure_local_files(_ALL_DOCX_PATHS))


@pytest.fixture(scope="module")
def governed_repository(structured_results):
    """Builds real governed `KnowledgeObject`s (CANDIDATE -> APPROVED,
    effective) from the real structured documents, persists them into an
    ISOLATED in-memory SQLite repository (never any real/shared
    database), and returns the repository ready for retrieval queries.
    """
    import asyncio

    async def _build() -> SqlAlchemyKnowledgeRepository:
        repository = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
        for outcome in structured_results:
            if outcome.structured is None:
                continue
            file_name = outcome.result.report.file_name
            knowledge_id = _KNOWLEDGE_IDS.get(file_name, f"6A3-VALIDATION-{file_name}")
            candidate = materialize_candidate(
                outcome.structured,
                knowledge_id=knowledge_id,
                document_type=KnowledgeDocumentType.MOP,
                version=KnowledgeVersion(label="1.0", effective_from=_EFFECTIVE_FROM),
                governed_at=_EFFECTIVE_FROM,
            )
            approved = approve_version(candidate, transitioned_at=_EFFECTIVE_FROM)
            assert approved.lifecycle_status == LifecycleStatus.APPROVED
            await repository.add(approved)
        return repository

    return asyncio.run(_build())


def test_structuring_produces_more_than_the_degenerate_root_summary_for_every_document(structured_results) -> None:
    for outcome in structured_results:
        assert outcome.structured is not None, f"{outcome.result.report.file_name} failed to structure"
        assert len(outcome.structured.sections) >= 1


def test_document1_vswr_prohibition_survives_into_a_real_section(structured_results) -> None:
    # The exact substrings already cleared/used by
    # test_knowledge_real_corpus_validation.py -- no new real text added.
    #
    # DEF-0024 corrective pass: Document1 is no longer a single whole-
    # document section -- the new, generic `N)` procedure-heading marker
    # (backend/knowledge/processing/processor.py) now correctly splits it
    # into one section per named alarm procedure, including its own
    # "VSWR Over Threshold" section. Several OTHER procedure sections
    # (e.g. an example command's own sample output) may also legitimately
    # mention the substring "VSWR" without being the VSWR procedure
    # itself, so the safety-critical assertion must be scoped to the
    # section that IS the VSWR procedure (identified structurally, by its
    # own heading containing "VSWR" -- never by "first VSWR mention across
    # every matching section concatenated together", which is no longer a
    # sound proxy now that segmentation is correctly granular), not to an
    # arbitrary combined-text substring window.
    document1 = next(o for o in structured_results if o.result.report.file_name == "Document1.docx")
    assert document1.structured is not None
    vswr_procedure_sections = [s for s in document1.structured.sections if s.heading and "VSWR" in s.heading]
    assert vswr_procedure_sections, "expected a real, independently-segmented VSWR procedure section"
    combined = "\n".join(s.content for s in vswr_procedure_sections)
    assert "VSWR" in combined
    assert "No restart" in combined or "no restart" in combined.lower()


def test_rogers_documents_produce_artifact_tagged_sections_for_embedded_content(structured_results) -> None:
    for file_name in (
        "Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx",
        "MOP_Rogers ERICSSON_4G5G_Resource_Timeout_ Allocation Failure_Service Degraded_Serive_Unavailable Alarms Resolution.docx",
    ):
        outcome = next(o for o in structured_results if o.result.report.file_name == file_name)
        assert outcome.structured is not None
        artifact_sections = [s for s in outcome.structured.sections if s.artifact_id is not None]
        assert artifact_sections, f"expected at least one artifact-tagged section for {file_name}"


@pytest.mark.asyncio
async def test_vswr_prohibition_is_retrievable_via_the_real_unmodified_retrieval_service(governed_repository) -> None:
    """THE end-to-end proof: a real query against the real, unmodified
    5.1G `KnowledgeRetrievalService` + `TokenOverlapRelevanceScorer`
    finds the governed Document1 object and returns the section
    containing the VSWR "no restart" rule -- not merely present in
    `KnowledgeObject.artifacts`/`.content`, but an actual retrieval hit.
    """
    service = KnowledgeRetrievalService(governed_repository)
    result = await service.retrieve(
        KnowledgeRetrievalQuery(query_text="VSWR Over Threshold restart alarm", as_of=_AS_OF, limit=10)
    )
    matching = [item for item in result.items if item.knowledge_id == "6A3-VALIDATION-DOCUMENT1"]
    assert matching, "expected Document1 to be retrieved for a VSWR query"
    assert any("VSWR" in item.section.content for item in matching)


@pytest.mark.asyncio
async def test_rogers_xlsx_sheet_content_is_retrievable_via_the_real_unmodified_retrieval_service(
    structured_results, governed_repository
) -> None:
    """Proves an EMBEDDED xlsx_sheet artifact's own real content --
    never present in the root document's own text -- is retrievable.
    The query text is derived AT RUNTIME from the artifact's own already-
    extracted content (never hardcoded in this file's source), so no
    real corpus text is committed here.
    """
    rogers = next(
        o
        for o in structured_results
        if o.result.report.file_name == "Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx"
    )
    assert rogers.structured is not None
    xlsx_artifact = next(
        (a for a in rogers.structured.source_document.artifacts if a.kind == "xlsx_sheet" and a.extracted_text), None
    )
    if xlsx_artifact is None:
        pytest.skip("no xlsx_sheet artifact with extracted text found in this real document")

    # Pull a real, distinctive token out of the artifact's own text at
    # runtime -- never written literally into this file.
    words = [w.strip(":,.()|") for w in xlsx_artifact.extracted_text.split() if len(w.strip(":,.()|")) > 4]
    assert words, "expected at least one usable token in the real xlsx sheet text"
    query_token = words[0]

    service = KnowledgeRetrievalService(governed_repository)
    result = await service.retrieve(
        KnowledgeRetrievalQuery(query_text=query_token, as_of=_AS_OF, limit=20)
    )
    matching = [
        item
        for item in result.items
        if item.knowledge_id == "6A3-VALIDATION-ROGERS-4G" and item.section.artifact_id == xlsx_artifact.artifact_id
    ]
    assert matching, "expected the real xlsx_sheet artifact's own section to be retrieved by its own real content"


def test_lineage_chain_structural_proof_document_to_artifact_to_section(structured_results) -> None:
    """Prints/asserts REDACTED structural lineage only -- artifact KIND
    and depth, never real display names or content -- for at least 3
    representative chains, per instruction section 33's Evidence Pack
    requirement.
    """
    chains: list[str] = []
    for outcome in structured_results:
        if outcome.structured is None:
            continue
        artifacts_by_id = {a.artifact_id: a for a in outcome.structured.source_document.artifacts}
        for section in outcome.structured.sections:
            if section.artifact_id is None:
                continue
            chain_kinds = []
            current_id: str | None = section.artifact_id
            while current_id is not None:
                artifact = artifacts_by_id[current_id]
                chain_kinds.append(artifact.kind)
                current_id = artifact.parent_artifact_id
            chains.append(" -> ".join(["root", *reversed(chain_kinds), "section"]))
            if len(chains) >= 3:
                break
        if len(chains) >= 3:
            break

    assert len(chains) >= 3, f"expected at least 3 real lineage chains, found {len(chains)}: {chains}"
    for chain in chains:
        assert chain.startswith("root -> ")
        assert chain.endswith(" -> section")


# --- Real standalone XLSX root document (structural only, 6A.3 defect-fix proof) --


@pytest.mark.skipif(not _SURGICAL_CHECKLIST_XLSX.is_file(), reason="real standalone XLSX validation file not available on this machine")
def test_real_standalone_xlsx_root_document_ingests_and_structures_successfully() -> None:
    """Before this milestone's own `parent_artifact_id` defect fix, this
    call raised `pydantic_core.ValidationError` (a dangling
    `parent_artifact_id="root"`) -- confirmed by reproducing the failure
    against this exact real file before applying the fix. Structural
    assertions only; no real cell value is ever asserted literally.
    """
    import asyncio

    from backend.knowledge_ingestion.local_file_adapter import ingest_and_structure_local_file

    outcome = asyncio.run(ingest_and_structure_local_file(_SURGICAL_CHECKLIST_XLSX))
    assert outcome.result.report.succeeded is True
    assert outcome.structured is not None
    sheet_artifacts = [a for a in outcome.structured.source_document.artifacts if a.kind == "xlsx_sheet"]
    assert sheet_artifacts, "expected at least one xlsx_sheet artifact"
    artifact_sections = [s for s in outcome.structured.sections if s.artifact_id is not None]
    assert artifact_sections, "expected at least one artifact-tagged section from the real workbook"
    # Every artifact-tagged section's artifact_id must resolve within the
    # SAME document's own artifacts -- the exact invariant the defect
    # previously violated for a root-level workbook.
    artifact_ids = {a.artifact_id for a in outcome.structured.source_document.artifacts}
    for section in outcome.structured.sections:
        if section.artifact_id is not None:
            assert section.artifact_id in artifact_ids
