"""A5 Layers C-F: focused tests for
backend/knowledge/ingestion/extraction.py's shared primitives --
hashing, deterministic artifact ids, defensive limits/budget, and
container-structure format sniffing (never trusting an extension).
"""
from __future__ import annotations

import io
import zipfile

import pytest

from backend.knowledge.ingestion.extraction import (
    ExtractionBudget,
    ExtractionLimitExceededError,
    ExtractionLimits,
    deterministic_artifact_id,
    hash_bytes,
    sniff_media_type,
)


def _limits(**overrides: int) -> ExtractionLimits:
    defaults = dict(max_recursion_depth=6, max_artifacts_per_root=500, max_artifact_bytes=1024, max_total_expanded_bytes=4096)
    defaults.update(overrides)
    return ExtractionLimits(**defaults)  # type: ignore[arg-type]


def _minimal_ooxml(main_content_type: str, main_part_name: str = "word/document.xml") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            f'<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            f'<Override PartName="/{main_part_name}" ContentType="{main_content_type}"/></Types>',
        )
        archive.writestr(main_part_name, "<root/>")
    return buffer.getvalue()


# --- hash_bytes / deterministic_artifact_id ---------------------------------


def test_hash_bytes_is_deterministic_sha256() -> None:
    assert hash_bytes(b"hello") == hash_bytes(b"hello")
    assert len(hash_bytes(b"hello")) == 64


def test_hash_bytes_differs_for_different_content() -> None:
    assert hash_bytes(b"a") != hash_bytes(b"b")


def test_deterministic_artifact_id_stable_for_same_inputs() -> None:
    a = deterministic_artifact_id(parent_artifact_id="p1", kind="image", position=0, content_hash="abc")
    b = deterministic_artifact_id(parent_artifact_id="p1", kind="image", position=0, content_hash="abc")
    assert a == b


def test_deterministic_artifact_id_changes_with_content_hash() -> None:
    a = deterministic_artifact_id(parent_artifact_id="p1", kind="image", position=0, content_hash="abc")
    b = deterministic_artifact_id(parent_artifact_id="p1", kind="image", position=0, content_hash="xyz")
    assert a != b


def test_deterministic_artifact_id_changes_with_position() -> None:
    a = deterministic_artifact_id(parent_artifact_id="p1", kind="image", position=0, content_hash="abc")
    b = deterministic_artifact_id(parent_artifact_id="p1", kind="image", position=1, content_hash="abc")
    assert a != b


# --- ExtractionBudget --------------------------------------------------------


def test_budget_allows_within_limits() -> None:
    budget = ExtractionBudget(limits=_limits())
    budget.check_depth(3)
    budget.reserve_bytes(100)
    budget.reserve_artifact_slot()
    budget.record_artifact(100)
    assert budget.artifacts_extracted == 1
    assert budget.total_bytes_expanded == 100


def test_budget_rejects_excess_depth() -> None:
    budget = ExtractionBudget(limits=_limits(max_recursion_depth=2))
    with pytest.raises(ExtractionLimitExceededError):
        budget.check_depth(3)


def test_budget_rejects_oversized_single_artifact() -> None:
    budget = ExtractionBudget(limits=_limits(max_artifact_bytes=10))
    with pytest.raises(ExtractionLimitExceededError):
        budget.reserve_bytes(11)


def test_budget_rejects_total_expanded_size_overrun() -> None:
    budget = ExtractionBudget(limits=_limits(max_artifact_bytes=1000, max_total_expanded_bytes=15))
    budget.reserve_bytes(10)
    budget.record_artifact(10)
    with pytest.raises(ExtractionLimitExceededError):
        budget.reserve_bytes(10)


def test_budget_rejects_artifact_count_overrun() -> None:
    budget = ExtractionBudget(limits=_limits(max_artifacts_per_root=1))
    budget.reserve_artifact_slot()
    budget.record_artifact(1)
    with pytest.raises(ExtractionLimitExceededError):
        budget.reserve_artifact_slot()


def test_budget_records_skipped_items() -> None:
    budget = ExtractionBudget(limits=_limits())
    budget.record_skipped("weird.bin", "unsupported format")
    assert budget.skipped == [("weird.bin", "unsupported format")]


# --- sniff_media_type ---------------------------------------------------


def test_sniff_recognizes_pdf_magic_bytes() -> None:
    assert sniff_media_type(b"%PDF-1.7\n...") == "pdf"


def test_sniff_recognizes_docx_by_content_types_declaration() -> None:
    data = _minimal_ooxml("application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml")
    assert sniff_media_type(data) == "docx"


def test_sniff_recognizes_xlsx_by_content_types_declaration() -> None:
    data = _minimal_ooxml("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml", "xl/workbook.xml")
    assert sniff_media_type(data) == "xlsx"


def test_sniff_never_trusts_extension_only_docx_named_zip_with_unrelated_content() -> None:
    # A ZIP file with a .docx-plausible name but NO OOXML content-type
    # declaration at all -- must NOT be misidentified as docx.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "just a plain zip, not an OOXML package")
    assert sniff_media_type(buffer.getvalue()) is None


def test_sniff_rejects_corrupt_zip_safely() -> None:
    assert sniff_media_type(b"PK\x03\x04not a real zip at all") is None


def test_sniff_rejects_plain_bytes() -> None:
    assert sniff_media_type(b"just some plain text, not a known container") is None
