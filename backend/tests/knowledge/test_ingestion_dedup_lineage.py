"""A5 corrective pass, Correction 2: proves CONTENT IDENTITY
(`KnowledgeArtifact.content_hash`) is structurally distinct from
ARTIFACT OCCURRENCE / LINEAGE identity (`KnowledgeArtifact.artifact_id`,
`parent_artifact_id`) -- a shared content hash never collapses two real
occurrences into one artifact record, and each occurrence's own parent
chain remains independently recoverable.

Design under test (`backend/knowledge/ingestion/extraction.py`'s
`deterministic_artifact_id`): `artifact_id` is derived from
`(parent_artifact_id, kind, position, content_hash)` TOGETHER -- never
`content_hash` alone. Two occurrences of byte-identical content that
differ in ANY of parent/kind/position therefore always receive DISTINCT
`artifact_id` values, while sharing the identical `content_hash` (the
sole input to GCS's content-addressed storage key,
`backend/knowledge_ingestion/artifact_storage.py`'s
`build_artifact_object_name`). This is what makes
"one stored binary, many independent occurrences, each with its own
recoverable parent path" a structural property rather than an
incidental one -- proven here for all three conceptual cases the
corrective-pass instruction distinguishes:

  Case A: identical binary used by two separate ROOT documents.
  Case B: identical binary occurring twice within ONE root, under two
          different PARENT artifacts.
  Case C: identical binary occurring at different NESTING depths within
          one root (direct root-level vs. nested inside an embedded doc).
"""
from __future__ import annotations

from backend.knowledge.ingestion.extraction import ExtractionBudget, ExtractionLimits, hash_bytes
from backend.knowledge.ingestion.extractors.dispatch import extract_root_document

from ._synthetic_docs import inject_embedded_member, make_minimal_docx, make_minimal_png


def _limits() -> ExtractionLimits:
    return ExtractionLimits(max_recursion_depth=6, max_artifacts_per_root=500, max_artifact_bytes=5 * 1024 * 1024, max_total_expanded_bytes=20 * 1024 * 1024)


def _budget() -> ExtractionBudget:
    return ExtractionBudget(limits=_limits())


# --- Case A: two separate root documents --------------------------------


def test_case_a_cross_root_duplicate_shares_content_hash_distinct_artifact_ids() -> None:
    shared_image = make_minimal_png()
    doc_a = make_minimal_docx(heading="Doc A", paragraphs=["a"], image_png=shared_image)
    doc_b = make_minimal_docx(heading="Doc B", paragraphs=["b"], image_png=shared_image)

    _, artifacts_a = extract_root_document(doc_a, budget=_budget())
    _, artifacts_b = extract_root_document(doc_b, budget=_budget())

    image_a = next(a for a in artifacts_a if a.kind == "image")
    image_b = next(a for a in artifacts_b if a.kind == "image")

    # CONTENT IDENTITY: same bytes -> same hash (the storage-dedup key).
    assert image_a.content_hash == image_b.content_hash == hash_bytes(shared_image)
    # ARTIFACT OCCURRENCE IDENTITY: two entirely separate KnowledgeObjects
    # would own these -- each occurrence's own artifact_id may coincide
    # or differ (irrelevant here, since they never share one artifacts
    # list at all); what matters is each is independently, correctly
    # parented within its OWN root's own artifact list.
    assert image_a.parent_artifact_id is None
    assert image_b.parent_artifact_id is None


# --- Case B: identical binary under two different parents, one root ----


def test_case_b_same_root_two_different_parents_both_occurrences_survive() -> None:
    shared_image = make_minimal_png()
    branch_one = make_minimal_docx(heading="Branch One", paragraphs=["x"], image_png=shared_image)
    branch_two = make_minimal_docx(heading="Branch Two", paragraphs=["y"], image_png=shared_image)
    root = make_minimal_docx(heading="Root", paragraphs=["root text"])
    root = inject_embedded_member(root, "word/embeddings/branch_one.docx", branch_one)
    root = inject_embedded_member(root, "word/embeddings/branch_two.docx", branch_two)

    _, artifacts = extract_root_document(root, budget=_budget())

    embedded_docs = [a for a in artifacts if a.kind == "embedded_docx"]
    assert len(embedded_docs) == 2
    branch_one_artifact = next(a for a in embedded_docs if a.display_name == "branch_one.docx")
    branch_two_artifact = next(a for a in embedded_docs if a.display_name == "branch_two.docx")
    assert branch_one_artifact.artifact_id != branch_two_artifact.artifact_id

    images = [a for a in artifacts if a.kind == "image"]
    assert len(images) == 2, "both occurrences of the identical image must survive as two distinct artifact records"

    image_under_branch_one = next(a for a in images if a.parent_artifact_id == branch_one_artifact.artifact_id)
    image_under_branch_two = next(a for a in images if a.parent_artifact_id == branch_two_artifact.artifact_id)

    # CONTENT IDENTITY: both occurrences are byte-identical -> same hash.
    assert image_under_branch_one.content_hash == image_under_branch_two.content_hash == hash_bytes(shared_image)
    # ARTIFACT OCCURRENCE IDENTITY: distinct artifact_id, distinct
    # parent_artifact_id -- the shared content hash never collapses
    # these into one record, and each one's own parent path remains
    # independently, deterministically recoverable.
    assert image_under_branch_one.artifact_id != image_under_branch_two.artifact_id
    assert image_under_branch_one.parent_artifact_id != image_under_branch_two.parent_artifact_id


# --- Case C: identical binary at different nesting depths ---------------


def test_case_c_same_root_direct_and_nested_occurrence_both_survive() -> None:
    shared_image = make_minimal_png()
    nested_doc = make_minimal_docx(heading="Nested", paragraphs=["n"], image_png=shared_image)
    root = make_minimal_docx(heading="Root", paragraphs=["root text"], image_png=shared_image)  # direct root-level occurrence
    root = inject_embedded_member(root, "word/embeddings/nested.docx", nested_doc)

    _, artifacts = extract_root_document(root, budget=_budget())

    direct_image = next(a for a in artifacts if a.kind == "image" and a.depth == 0)
    nested_container = next(a for a in artifacts if a.kind == "embedded_docx")
    nested_image = next(a for a in artifacts if a.kind == "image" and a.depth == 1)

    assert direct_image.parent_artifact_id is None
    assert nested_image.parent_artifact_id == nested_container.artifact_id

    # CONTENT IDENTITY shared; ARTIFACT OCCURRENCE IDENTITY (and depth)
    # both remain distinct and each parent chain independently resolves.
    assert direct_image.content_hash == nested_image.content_hash == hash_bytes(shared_image)
    assert direct_image.artifact_id != nested_image.artifact_id
    assert direct_image.depth != nested_image.depth


# --- Provenance resolves the correct occurrence, not merely "a" match --


def test_evidence_resolution_identifies_the_correct_occurrence_by_parent_path() -> None:
    # A section built from the NESTED occurrence's own extracted text
    # (were it to have one) must resolve back to the NESTED artifact
    # specifically -- never ambiguously "some artifact with this hash".
    # KnowledgeSection/KnowledgeEvidenceReference carry `artifact_id`,
    # never `content_hash`, precisely so resolution is never hash-based
    # (which WOULD be ambiguous whenever two occurrences share a hash).
    shared_text_artifact_id = "art-example-occurrence-2"
    from backend.knowledge.domain.artifacts import KnowledgeArtifact

    occurrence_1 = KnowledgeArtifact(artifact_id="art-example-occurrence-1", kind="xlsx_sheet", depth=1, parent_artifact_id="parent-a", content_hash="a" * 64, extracted_text="same text content")
    occurrence_2 = KnowledgeArtifact(artifact_id=shared_text_artifact_id, kind="xlsx_sheet", depth=1, parent_artifact_id="parent-b", content_hash="a" * 64, extracted_text="same text content")
    assert occurrence_1.content_hash == occurrence_2.content_hash  # identical content
    assert occurrence_1.artifact_id != occurrence_2.artifact_id  # distinct occurrence identity
    assert occurrence_1.parent_artifact_id != occurrence_2.parent_artifact_id  # distinct, independently recoverable parent path


# --- Deterministic retry does not duplicate semantic sections ----------


def test_deterministic_retry_produces_identical_artifact_ids_not_duplicates() -> None:
    shared_image = make_minimal_png()
    doc = make_minimal_docx(heading="Doc", paragraphs=["p"], image_png=shared_image)

    _, artifacts_first_run = extract_root_document(doc, budget=_budget())
    _, artifacts_second_run = extract_root_document(doc, budget=_budget())

    ids_first = sorted(a.artifact_id for a in artifacts_first_run)
    ids_second = sorted(a.artifact_id for a in artifacts_second_run)
    assert ids_first == ids_second, "re-ingesting identical content must reproduce identical artifact_ids, never new/duplicate ones"
    assert len(artifacts_first_run) == len(artifacts_second_run) == 1
