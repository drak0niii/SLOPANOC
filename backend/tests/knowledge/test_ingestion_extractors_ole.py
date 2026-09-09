"""A5 corrective pass, Correction 1: focused tests for
backend/knowledge/ingestion/extractors/ole.py (legacy OLE2 Compound-
File-Binary "Insert Object > Create from File" embed support) and its
wiring into extractors/dispatch.py -- synthetic fixtures only (A5
instruction section 48; never the real Rogers corpus content), built
via backend/tests/knowledge/_synthetic_docs.py's hand-crafted CFB
builder (`olefile` is read-only, so no write-capable OLE library exists
to build fixtures with).
"""
from __future__ import annotations

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus
from backend.knowledge.ingestion.extraction import ExtractionBudget, ExtractionLimits, hash_bytes
from backend.knowledge.ingestion.extractors.dispatch import extract_embedded_artifact
from backend.knowledge.ingestion.extractors.ole import extract_ole_package_payload, is_ole_compound_file

from ._synthetic_docs import make_minimal_cfb_with_stream, make_ole_package_object

_SYNTHETIC_LOG = "2026-09-07 12:00:00 INFO node status GREEN\n2026-09-07 12:00:05 INFO checksum 9421\n"


def _limits(**overrides: int) -> ExtractionLimits:
    defaults = dict(max_recursion_depth=6, max_artifacts_per_root=500, max_artifact_bytes=5 * 1024 * 1024, max_total_expanded_bytes=20 * 1024 * 1024)
    defaults.update(overrides)
    return ExtractionLimits(**defaults)  # type: ignore[arg-type]


def _budget(**overrides: int) -> ExtractionBudget:
    return ExtractionBudget(limits=_limits(**overrides))


# --- 1. safe OLE payload discovery ------------------------------------


def test_is_ole_compound_file_detects_real_magic_bytes() -> None:
    data = make_ole_package_object("notes.txt", b"content")
    assert is_ole_compound_file(data) is True


def test_is_ole_compound_file_rejects_non_ole_bytes() -> None:
    assert is_ole_compound_file(b"not an ole file at all") is False


def test_extract_ole_package_payload_discovers_embedded_file() -> None:
    data = make_ole_package_object("EnodeB_HC.txt", _SYNTHETIC_LOG.encode("latin-1"))
    result = extract_ole_package_payload(data)
    assert result is not None
    filename, payload = result
    assert filename == "EnodeB_HC.txt"


# --- 2/3. embedded TXT extraction + line preservation -----------------


def test_extracted_txt_content_matches_exactly() -> None:
    data = make_ole_package_object("health.txt", _SYNTHETIC_LOG.encode("latin-1"))
    _, payload = extract_ole_package_payload(data)
    assert payload.decode("latin-1") == _SYNTHETIC_LOG


def test_extracted_txt_line_structure_preserved() -> None:
    data = make_ole_package_object("health.txt", _SYNTHETIC_LOG.encode("latin-1"))
    _, payload = extract_ole_package_payload(data)
    lines = payload.decode("latin-1").splitlines()
    assert lines[0] == "2026-09-07 12:00:00 INFO node status GREEN"
    assert lines[1] == "2026-09-07 12:00:05 INFO checksum 9421"


# --- 4/5. parent lineage + correct artifact role via dispatch ----------


def test_dispatch_produces_ole_container_and_txt_log_child() -> None:
    ole_bytes = make_ole_package_object("EnodeB_HC.txt", _SYNTHETIC_LOG.encode("latin-1"))
    budget = _budget()
    artifacts = extract_embedded_artifact(
        ole_bytes, parent_artifact_id=None, depth=0, budget=budget, display_name="oleObject1.bin", position=0, relationship_kind="embedded_object"
    )

    assert len(artifacts) == 2
    container = next(a for a in artifacts if a.kind == "ole_package")
    child = next(a for a in artifacts if a.kind == "txt_log")

    assert container.parent_artifact_id is None
    assert container.display_name == "oleObject1.bin"
    assert container.extraction_status == ArtifactExtractionStatus.COMPLETE

    assert child.parent_artifact_id == container.artifact_id
    assert child.depth == container.depth + 1
    assert child.display_name == "EnodeB_HC.txt"
    assert child.extracted_text == _SYNTHETIC_LOG
    assert child.extraction_status == ArtifactExtractionStatus.COMPLETE


def test_ole_extracted_image_payload_routed_as_image() -> None:
    from ._synthetic_docs import make_minimal_png

    ole_bytes = make_ole_package_object("panel.png", make_minimal_png())
    artifacts = extract_embedded_artifact(
        ole_bytes, parent_artifact_id=None, depth=0, budget=_budget(), display_name="oleObject1.bin", position=0, relationship_kind="embedded_object"
    )
    image_child = next(a for a in artifacts if a.kind == "image")
    assert image_child.display_name == "panel.png"


# --- 6. deterministic content hash -------------------------------------


def test_ole_container_hash_deterministic_across_runs() -> None:
    ole_bytes = make_ole_package_object("EnodeB_HC.txt", _SYNTHETIC_LOG.encode("latin-1"))
    artifacts_a = extract_embedded_artifact(
        ole_bytes, parent_artifact_id=None, depth=0, budget=_budget(), display_name="o.bin", position=0, relationship_kind="embedded_object"
    )
    artifacts_b = extract_embedded_artifact(
        ole_bytes, parent_artifact_id=None, depth=0, budget=_budget(), display_name="o.bin", position=0, relationship_kind="embedded_object"
    )
    container_a = next(a for a in artifacts_a if a.kind == "ole_package")
    container_b = next(a for a in artifacts_b if a.kind == "ole_package")
    assert container_a.content_hash == container_b.content_hash == hash_bytes(ole_bytes)


def test_identical_ole_object_in_two_parents_shares_content_hash() -> None:
    # Mirrors the real corpus finding: the same OLE object embedded in
    # two different root documents must resolve to the same content_hash
    # (the structural basis for storage-layer deduplication).
    ole_bytes = make_ole_package_object("EnodeB_HC.txt", _SYNTHETIC_LOG.encode("latin-1"))
    artifacts_doc_a = extract_embedded_artifact(
        ole_bytes, parent_artifact_id="root-a", depth=0, budget=_budget(), display_name="o.bin", position=0, relationship_kind="embedded_object"
    )
    artifacts_doc_b = extract_embedded_artifact(
        ole_bytes, parent_artifact_id="root-b", depth=0, budget=_budget(), display_name="o.bin", position=0, relationship_kind="embedded_object"
    )
    hash_a = next(a for a in artifacts_doc_a if a.kind == "ole_package").content_hash
    hash_b = next(a for a in artifacts_doc_b if a.kind == "ole_package").content_hash
    assert hash_a == hash_b


# --- 7. no execution -----------------------------------------------------


def test_ole_module_has_no_execution_capability_imports() -> None:
    # Structural proof, not behavioral mocking: the module's own import
    # statements never reference subprocess/COM/win32com/os.system/eval/
    # exec -- it can only ever read bytes via olefile + struct.
    import ast
    from pathlib import Path

    source_path = Path(__file__).resolve().parents[2] / "knowledge" / "ingestion" / "extractors" / "ole.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    forbidden_modules = {"subprocess", "win32com", "comtypes", "os"}
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])
    assert not (imported_modules & forbidden_modules), f"unexpected execution-capable import(s): {imported_modules & forbidden_modules}"

    forbidden_calls = {"eval", "exec", "system", "popen", "call", "run"}
    called_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
            if name:
                called_names.add(name)
    assert not (called_names & forbidden_calls), f"unexpected execution-capable call(s): {called_names & forbidden_calls}"


# --- 8. malformed OLE fails safely --------------------------------------


def test_malformed_ole_magic_but_truncated_fails_safely() -> None:
    truncated = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 4
    assert extract_ole_package_payload(truncated) is None


def test_ole_with_corrupt_directory_structure_fails_safely() -> None:
    real = make_ole_package_object("notes.txt", b"content padded" + b"x" * 4096)
    corrupted = bytearray(real)
    # Corrupt bytes inside the directory sector (sector index 1, right
    # after the header+FAT sectors) -- must not crash, must return None
    # or still-valid data; either is acceptable, but no exception may
    # ever escape.
    corrupted[512 + 200 : 512 + 220] = b"\xff" * 20
    try:
        result = extract_ole_package_payload(bytes(corrupted))
    except Exception as exc:  # pragma: no cover -- this is exactly what must never happen.
        raise AssertionError(f"extract_ole_package_payload must never raise, got {exc!r}") from exc
    assert result is None or isinstance(result, tuple)


def test_ole10native_stream_with_bad_length_field_fails_safely() -> None:
    from ._synthetic_docs import make_minimal_cfb_with_stream, make_ole10_native_stream

    stream = make_ole10_native_stream("notes.txt", b"short")
    # Corrupt the declared data-length field to an absurd value while
    # leaving everything else intact.
    import struct

    corrupted_stream = bytearray(stream)
    # The 4-byte length field sits right before the real payload -- find
    # it by re-deriving its offset the same way the parser does isn't
    # necessary here; instead corrupt the LAST 4 bytes before the known
    # short payload ("short" is 5 bytes at the very end).
    length_field_offset = len(corrupted_stream) - 5 - 4
    corrupted_stream[length_field_offset : length_field_offset + 4] = struct.pack("<I", 999_999_999)
    padded = bytes(corrupted_stream) + b"\x00" * max(0, 4096 - len(corrupted_stream))
    cfb = make_minimal_cfb_with_stream("\x01Ole10Native", padded)
    assert extract_ole_package_payload(cfb) is None


# --- 9. unsupported OLE reported safely (no Ole10Native stream at all) --


def test_ole_without_ole10native_stream_reports_unsupported() -> None:
    cfb = make_minimal_cfb_with_stream("SomeOtherStream", b"x" * 4096)
    assert extract_ole_package_payload(cfb) is None


def test_dispatch_reports_unsupported_ole_as_skipped_embedded_object() -> None:
    cfb = make_minimal_cfb_with_stream("SomeOtherStream", b"x" * 4096)
    artifacts = extract_embedded_artifact(
        cfb, parent_artifact_id=None, depth=0, budget=_budget(), display_name="mystery.bin", position=0, relationship_kind="embedded_object"
    )
    assert len(artifacts) == 1
    assert artifacts[0].kind == "embedded_object"
    assert artifacts[0].extraction_status == ArtifactExtractionStatus.SKIPPED
    assert artifacts[0].extraction_error


# --- 10. recursion/budget still enforced for OLE-derived artifacts -----


def test_ole_txt_log_child_counts_against_artifact_budget() -> None:
    ole_bytes = make_ole_package_object("EnodeB_HC.txt", _SYNTHETIC_LOG.encode("latin-1"))
    budget = ExtractionBudget(limits=_limits(max_artifacts_per_root=1))  # room for only the container itself
    artifacts = extract_embedded_artifact(
        ole_bytes, parent_artifact_id=None, depth=0, budget=budget, display_name="o.bin", position=0, relationship_kind="embedded_object"
    )
    assert len(artifacts) == 1
    assert artifacts[0].kind == "ole_package"
    assert budget.skipped  # the txt_log child was recorded as skipped, not silently dropped


def test_ole_txt_log_child_respects_recursion_depth_limit() -> None:
    ole_bytes = make_ole_package_object("EnodeB_HC.txt", _SYNTHETIC_LOG.encode("latin-1"))
    budget = ExtractionBudget(limits=_limits(max_recursion_depth=0))
    artifacts = extract_embedded_artifact(
        ole_bytes, parent_artifact_id=None, depth=0, budget=budget, display_name="o.bin", position=0, relationship_kind="embedded_object"
    )
    # The container itself is at depth 0 (allowed); its child would be
    # checked at depth 1, which exceeds max_recursion_depth=0.
    assert any(a.kind == "ole_package" for a in artifacts)
    assert not any(a.kind == "txt_log" for a in artifacts)
    assert budget.skipped
