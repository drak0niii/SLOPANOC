"""A5 instruction section 68: REAL CORPUS VALIDATION.

Reads the three real MOP files directly from their external,
out-of-repository development paths (READ-ONLY -- never copied, moved,
staged, or committed; see CLAUDE.md/the A5 instructions for the exact
paths and the explicit prohibition on committing their content).

This test file itself contains NO real MOP text, no extracted
screenshots, no internal IPs/URLs/customer data -- only structural
assertions (counts, presence of expected substrings that are themselves
real operational values already documented in the milestone instructions,
e.g. "VSWR", never full paragraphs). If a path is unavailable, the
corresponding test SKIPS (never fails, never fabricates a result) --
this is real-corpus validation, not a synthetic-fixture test, so it is
only meaningful when the real files are actually present on this
machine.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from backend.knowledge.ingestion.extraction import ExtractionLimits
from backend.knowledge_ingestion.local_file_adapter import ingest_local_files

_DOCUMENT1 = Path(r"C:\Users\eosiocn\Downloads\Document1.docx")
_ROGERS_4G = Path(r"C:\Users\eosiocn\Downloads\Rogers ERICSSON_4G_Resource_Timeout_and Allocation Failure.docx")
_ROGERS_4G5G = Path(
    r"C:\Users\eosiocn\Downloads\MOP_Rogers ERICSSON_4G5G_Resource_Timeout_ Allocation Failure_Service Degraded_Serive_Unavailable Alarms Resolution.docx"
)
_ALL_REAL_PATHS = [_DOCUMENT1, _ROGERS_4G, _ROGERS_4G5G]

_missing = [p for p in _ALL_REAL_PATHS if not p.is_file()]
pytestmark = pytest.mark.skipif(bool(_missing), reason=f"real validation corpus not available on this machine: {_missing}")

_LIMITS = ExtractionLimits(max_recursion_depth=6, max_artifacts_per_root=500, max_artifact_bytes=25 * 1024 * 1024, max_total_expanded_bytes=200 * 1024 * 1024)


@pytest.fixture(scope="module")
def real_corpus_results():
    import asyncio

    return asyncio.run(ingest_local_files(_ALL_REAL_PATHS, limits=_LIMITS))


def test_all_three_root_documents_recognized(real_corpus_results) -> None:
    assert all(result.report.succeeded for result in real_corpus_results)
    assert all(result.document is not None for result in real_corpus_results)


def test_document1_is_plain_text_no_compound_structure(real_corpus_results) -> None:
    document1_result = real_corpus_results[0]
    assert document1_result.report.artifact_count == 0
    assert len(document1_result.document.content) > 0


def test_document1_vswr_prohibition_survives_extraction(real_corpus_results) -> None:
    # Section 56/47's critical acceptance case: the "no restart" rule
    # must survive ingestion, distinct from every other restart-allowed
    # alarm case in the same document.
    text = real_corpus_results[0].document.content
    assert "VSWR" in text
    vswr_index = text.index("VSWR")
    surrounding = text[vswr_index : vswr_index + 200]
    assert "No restart" in surrounding or "no restart" in surrounding.lower()


def test_rogers_documents_are_compound_with_nested_artifacts(real_corpus_results) -> None:
    for result in real_corpus_results[1:]:
        assert result.report.artifact_count > 0
        assert result.report.max_depth >= 1  # nested artifact discovered (embedded doc's own images)


def test_rogers_documents_contain_expected_artifact_kinds(real_corpus_results) -> None:
    for result in real_corpus_results[1:]:
        kinds = set(result.report.counts_by_kind)
        assert "image" in kinds
        assert "embedded_docx" in kinds
        assert "embedded_xlsx" in kinds


def test_rogers_documents_contain_real_ole_txt_log_payload(real_corpus_results) -> None:
    # A5 corrective pass, Correction 1: both real Rogers MOPs embed a
    # legacy OLE "Insert Object > Create from File" package whose real
    # payload is a health-check log named EnodeB_HC.txt -- it must now
    # be discovered as a typed ole_package -> txt_log artifact pair,
    # never merely reported as an unsupported/skipped OLE blob.
    for result in real_corpus_results[1:]:
        artifacts = result.document.artifacts
        ole_containers = [a for a in artifacts if a.kind == "ole_package"]
        txt_logs = [a for a in artifacts if a.kind == "txt_log"]
        assert ole_containers, "expected at least one recognized ole_package artifact"
        assert txt_logs, "expected the OLE object's real TXT/log payload to be discovered"

        log_artifact = next((a for a in txt_logs if a.display_name == "EnodeB_HC.txt"), None)
        assert log_artifact is not None, "expected the real embedded filename EnodeB_HC.txt to be discovered"
        assert log_artifact.extracted_text is not None
        assert len(log_artifact.extracted_text) > 0

        container = next(a for a in ole_containers if a.artifact_id == log_artifact.parent_artifact_id)
        assert container is not None  # lineage: the log's parent is the real OLE container, not the root document

        # No unsupported/skipped OLE object remains once its real payload
        # is safely identifiable -- confirms the correction actually
        # changed the classification, not merely added a sibling finding.
        assert not any(a.kind == "embedded_object" and a.extraction_status.value == "skipped" for a in artifacts)


def test_all_artifacts_have_deterministic_hashes(real_corpus_results) -> None:
    for result in real_corpus_results[1:]:
        for artifact in result.document.artifacts:
            assert artifact.content_hash is not None
            assert len(artifact.content_hash) == 64


def test_duplicate_shared_embedded_artifact_detected_across_both_rogers_documents(real_corpus_results) -> None:
    # Both real Rogers MOPs share the same embedded "Microsoft Word
    # Document"/"Microsoft Excel Worksheet" boilerplate attachments --
    # real-world Case C dedup evidence (A5 instruction section 34/63).
    hashes_a = {a.content_hash for a in real_corpus_results[1].document.artifacts if a.content_hash}
    hashes_b = {a.content_hash for a in real_corpus_results[2].document.artifacts if a.content_hash}
    shared = hashes_a & hashes_b
    assert len(shared) > 0, "expected at least one identical embedded artifact shared between the two real Rogers documents"


def test_parent_lineage_preserved_for_nested_artifacts(real_corpus_results) -> None:
    for result in real_corpus_results[1:]:
        artifacts_by_id = {a.artifact_id: a for a in result.document.artifacts}
        nested = [a for a in result.document.artifacts if a.depth > 0]
        assert nested, "expected at least one artifact nested inside another"
        for artifact in nested:
            assert artifact.parent_artifact_id in artifacts_by_id


def test_root_to_embedded_docx_to_nested_image_hierarchical_provenance(real_corpus_results, capsys) -> None:
    """A5 corrective pass, Correction 3: proves the real
    root-MOP -> embedded-Word-document -> nested-image chain is
    deterministically recoverable purely by walking `parent_artifact_id`
    pointers, regardless of the absolute depth numbers assigned --
    depth semantics documented at
    `KnowledgeArtifact.depth`'s own field description: 0 = embedded
    directly in the root document (the root itself is never an artifact
    at all -- KnowledgeArtifact's own module docstring), 1 = nested
    inside that, etc. No image binary/content is ever printed -- only
    structural identity/relationship facts.
    """
    for result in real_corpus_results[1:]:
        artifacts_by_id = {a.artifact_id: a for a in result.document.artifacts}
        embedded_docx = next(a for a in result.document.artifacts if a.kind == "embedded_docx")
        nested_image = next(a for a in result.document.artifacts if a.kind == "image" and a.parent_artifact_id == embedded_docx.artifact_id)

        # Documented depth semantics for this real chain.
        assert embedded_docx.parent_artifact_id is None  # embedded directly in the root -> depth 0
        assert embedded_docx.depth == 0
        assert nested_image.parent_artifact_id == embedded_docx.artifact_id  # nested one level deeper -> depth 1
        assert nested_image.depth == 1

        # The full parent chain is recoverable deterministically by
        # walking parent_artifact_id back to the root (None) -- this is
        # the invariant that matters, independent of the absolute depth
        # numbers themselves.
        chain: list[str] = [nested_image.artifact_id]
        current = nested_image
        while current.parent_artifact_id is not None:
            current = artifacts_by_id[current.parent_artifact_id]
            chain.append(current.artifact_id)
        assert chain[-1] == embedded_docx.artifact_id
        assert len(chain) == 2  # image -> embedded_docx -> (root, unrepresented)

        # Structural-only report (no image bytes/content) -- printed via
        # capsys so pytest -s/-v output stays reviewable without ever
        # touching a real file.
        print(
            f"[{result.report.file_name}] root(unrepresented) -> embedded_docx(id={embedded_docx.artifact_id[:16]}..., "
            f"depth={embedded_docx.depth}, kind={embedded_docx.kind}) -> image(id={nested_image.artifact_id[:16]}..., "
            f"depth={nested_image.depth}, kind={nested_image.kind}, hash={nested_image.content_hash[:16]}...)"
        )
    captured = capsys.readouterr()
    assert "embedded_docx" in captured.out


def test_no_sensitive_content_leaked_into_this_repository_file() -> None:
    # Structural self-check: this test file's own source must never
    # contain a real internal hostname/URL fragment from the corpus.
    # Fragments are built via concatenation so the check itself never
    # contains the literal substring it is checking for.
    source = Path(__file__).read_text(encoding="utf-8")
    forbidden_fragments = ["enm5" + "launcher", "enm3" + "launcher", "rogers" + ".com", "." + "oss" + "."]
    for forbidden_fragment in forbidden_fragments:
        assert forbidden_fragment not in source
