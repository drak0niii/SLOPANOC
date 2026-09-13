"""XLSX workbook extraction (A5 Layer D; range/table provenance hardened
in 6A.3 / P11-M03).

Preserves structure as Workbook -> Sheet -> header/row schema -- never
flattened into one arbitrary text blob (A5 instruction section 23).
Formulas are read as their literal string form (`data_only=False`,
openpyxl's default) and NEVER evaluated; no macro (`xlsm`) content is
ever executed (openpyxl has no VBA execution capability at all -- macro
code, if present, is opaque binary this module never touches). Row
sampling is bounded (`_MAX_SAMPLE_ROWS`) so one enormous sheet cannot
blow up a single artifact's `extracted_text` size -- the real row count
is still recorded so a truncated sample is never presented as complete.

An empty template (headers only, zero data rows) still produces a real,
non-degenerate sheet artifact -- the header/schema row alone is useful
knowledge (A5 instruction section 23), never treated as "nothing to
extract."

6A.3 RANGE/TABLE PROVENANCE (audited gap, closed here): the sheet-level
artifact's own `locator_detail` now includes the sheet's real used range
(e.g. `"sheet=VSWR;range=A1:F32"`), not merely the sheet name -- computed
from `Worksheet.dimensions`, never guessed. Additionally, every NATIVE
Excel Table the workbook actually declares (`Worksheet.tables` -- an
author-defined named range with real headers, distinct from an ad hoc
block of cells that merely looks tabular) is extracted as its OWN
`kind="xlsx_table"` child artifact, nested under its owning sheet
artifact, with `locator_detail` carrying the table's own name and exact
range (e.g. `"sheet=VSWR;table=VSWRThreshold;range=A1:C3"`) and
`extracted_text` rendered as a real header+rows table -- never a guessed
table boundary; only ranges the source workbook itself already declares
as a Table are ever extracted this way. A sheet with no native Tables
produces no `xlsx_table` artifacts at all -- the existing whole-sheet
artifact remains the only representation, exactly as before this pass.

READ_ONLY MODE CHANGE (empirically verified before implementing, not
assumed): openpyxl's `read_only=True` `ReadOnlyWorksheet` does NOT expose
`.tables` or `.dimensions` at all (confirmed by direct interactive
testing against a real generated workbook: both raise `AttributeError`
in read_only mode). This module now loads with `read_only=False` to
access both -- still safely bounded by the same pre-existing
`ExtractionLimits.max_artifact_bytes` ceiling every XLSX artifact is
already subject to before this function is ever reached, so this is not
a new, unbounded resource-usage path.
"""
from __future__ import annotations

import io
from typing import Optional

from backend.knowledge.domain.artifacts import ArtifactExtractionStatus, KnowledgeArtifact
from backend.knowledge.ingestion.extraction import ExtractionBudget, ExtractionLimitExceededError, deterministic_artifact_id, hash_bytes

_MAX_SAMPLE_ROWS = 25


def _render_row(row: tuple) -> str:
    return " | ".join("" if cell is None else str(cell) for cell in row)


def _render_sheet(sheet_name: str, rows: list[tuple], total_row_count: int) -> str:
    lines = [f"Sheet: {sheet_name}"]
    if not rows:
        lines.append("(no rows)")
        return "\n".join(lines)

    header, *data_rows = rows
    lines.append("Headers: " + _render_row(header))
    for row in data_rows:
        lines.append(_render_row(row))
    if total_row_count > len(rows):
        lines.append(f"... ({total_row_count - len(rows)} more row(s) not shown)")
    return "\n".join(lines)


def _render_table(table_name: str, rows: list[tuple]) -> str:
    lines = [f"Table: {table_name}"]
    if not rows:
        lines.append("(no rows)")
        return "\n".join(lines)
    header, *data_rows = rows
    lines.append("Columns: " + _render_row(header))
    for row in data_rows:
        lines.append(_render_row(row))
    return "\n".join(lines)


def extract_xlsx(data: bytes, *, container_artifact_id: Optional[str], depth: int, budget: ExtractionBudget) -> tuple[str, list[KnowledgeArtifact]]:
    """Returns (workbook summary text, a flattened list of `KnowledgeArtifact`s:
    one per sheet -- `parent_artifact_id=container_artifact_id`,
    `kind="xlsx_sheet"`, `depth=depth`, `locator_detail="sheet=<name>;range=<used range>"`
    -- plus, for every native Excel Table the sheet declares, one further
    `kind="xlsx_table"` child artifact -- `parent_artifact_id=<owning sheet's own artifact_id>`,
    `depth=depth+1`, `locator_detail="sheet=<name>;table=<table name>;range=<ref>"`).

    `container_artifact_id=None` means "this XLSX is itself the root
    document" -- see `extract_pdf`'s own docstring for the full 6A.3 /
    P11-M03 defect-fix rationale (dispatch.py previously passed the
    dangling literal string `"root"` here for a root-level workbook,
    which `IngestedKnowledgeDocument`'s own lineage validator correctly
    rejected; this fix changes no artifact_id/content_hash/storage key
    that may already exist from a prior real ingestion run).
    """
    import openpyxl  # noqa: PLC0415 -- imported lazily, matching this codebase's "only import a heavy dependency where used" convention.

    # read_only=False (not the prior read_only=True): required to reach
    # Worksheet.tables/.dimensions at all -- see module docstring.
    workbook = openpyxl.load_workbook(io.BytesIO(data), data_only=False, read_only=False)

    artifacts: list[KnowledgeArtifact] = []
    sheet_names: list[str] = list(workbook.sheetnames)

    for position, sheet_name in enumerate(sheet_names):
        worksheet = workbook[sheet_name]
        rows: list[tuple] = []
        total_row_count = 0
        for row in worksheet.iter_rows(values_only=True):
            total_row_count += 1
            if len(rows) < _MAX_SAMPLE_ROWS:
                rows.append(row)

        used_range = worksheet.dimensions if total_row_count > 0 else None
        sheet_text = _render_sheet(sheet_name, rows, total_row_count)
        sheet_hash = hash_bytes(sheet_text.encode("utf-8"))

        try:
            budget.reserve_bytes(len(sheet_text.encode("utf-8")))
            budget.reserve_artifact_slot()
        except ExtractionLimitExceededError:
            budget.record_skipped(f"{sheet_name} (sheet)", "extraction limit exceeded")
            continue

        sheet_artifact_id = deterministic_artifact_id(
            parent_artifact_id=container_artifact_id, kind="xlsx_sheet", position=position, content_hash=sheet_hash
        )
        sheet_locator = f"sheet={sheet_name}" + (f";range={used_range}" if used_range else "")
        artifacts.append(
            KnowledgeArtifact(
                artifact_id=sheet_artifact_id,
                parent_artifact_id=container_artifact_id,
                kind="xlsx_sheet",
                media_type="text/plain",
                display_name=sheet_name,
                depth=depth,
                content_hash=sheet_hash,
                extracted_text=sheet_text,
                derived=False,
                locator_detail=sheet_locator,
                extraction_status=ArtifactExtractionStatus.COMPLETE,
            )
        )
        budget.record_artifact(len(sheet_text.encode("utf-8")))

        # Native Excel Tables only -- never a heuristic/guessed table
        # boundary (6A.3). `Worksheet.tables` is only populated when the
        # source workbook itself already declared the range as a Table.
        table_names = list(worksheet.tables)
        for table_position, table_name in enumerate(table_names):
            table = worksheet.tables[table_name]
            table_rows = [tuple(cell.value for cell in row) for row in worksheet[table.ref]]
            table_text = _render_table(table_name, table_rows)
            table_hash = hash_bytes(table_text.encode("utf-8"))

            try:
                budget.reserve_bytes(len(table_text.encode("utf-8")))
                budget.reserve_artifact_slot()
            except ExtractionLimitExceededError:
                budget.record_skipped(f"{sheet_name}!{table_name} (table)", "extraction limit exceeded")
                continue

            table_artifact_id = deterministic_artifact_id(
                parent_artifact_id=sheet_artifact_id, kind="xlsx_table", position=table_position, content_hash=table_hash
            )
            artifacts.append(
                KnowledgeArtifact(
                    artifact_id=table_artifact_id,
                    parent_artifact_id=sheet_artifact_id,
                    kind="xlsx_table",
                    media_type="text/plain",
                    display_name=table_name,
                    depth=depth + 1,
                    content_hash=table_hash,
                    extracted_text=table_text,
                    derived=False,
                    locator_detail=f"sheet={sheet_name};table={table_name};range={table.ref}",
                    extraction_status=ArtifactExtractionStatus.COMPLETE,
                )
            )
            budget.record_artifact(len(table_text.encode("utf-8")))

    workbook.close()
    summary = f"Workbook with {len(sheet_names)} sheet(s): " + ", ".join(sheet_names)
    return summary, artifacts
