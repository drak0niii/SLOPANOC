"""DOCX extraction (A5 Layer C).

Preserves paragraphs, heading structure, table semantics, and document
ORDER (body elements are walked in the order the OOXML package actually
stores them, never `document.paragraphs` and `document.tables`
separately re-joined, which would lose interleaving) -- A5 instruction
section 21. Heading styles ("Heading 1".."Heading 6", "Title") become
Markdown ATX heading syntax (`#`.."######") in the rendered text so the
existing, unmodified `HeadingStructureProcessor`
(backend/knowledge/processing/processor.py) can segment this document's
text into sections exactly the way it already segments any other
document -- no new processor was needed for heading-aware DOCX
segmentation.

Embedded media/objects are discovered via the OOXML package's own
relationship structure (`word/_rels/document.xml.rels` + `word/media/`
+ `word/embeddings/`), never guessed from paragraph text -- A5
instruction section 21's explicit "inspect OOXML relationships/package
structure" requirement. Each is handed to
`backend.knowledge.ingestion.extractors.dispatch.extract_embedded_artifact`,
which sniffs its real format and (for a further-recursable kind)
recurses -- this is what makes "MOP.docx -> embedded AccessGuide.docx
-> screenshots" work (A5 instruction section 28) without any DOCX-
specific recursion code duplicated here.

Hyperlink TARGETS are preserved as reference text only -- never fetched
(A5 instruction section 32).

A `word/vbaProject.bin` member (macro-enabled document) is never
executed and never further decomposed -- reported as a single skipped
artifact (A5 instruction section 30); the REST of the document is still
processed normally, since python-docx has no VBA-execution capability
at all regardless.
"""
from __future__ import annotations

import io
import re
import zipfile
from typing import Optional
from xml.etree import ElementTree as ET

from backend.knowledge.domain.artifacts import KnowledgeArtifact
from backend.knowledge.ingestion.extraction import ExtractionBudget

_HEADING_LEVEL_PATTERN = re.compile(r"^Heading (\d)$")
_RELS_NS = {"r": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _heading_prefix(style_name: Optional[str]) -> str:
    if not style_name:
        return ""
    if style_name == "Title":
        return "# "
    match = _HEADING_LEVEL_PATTERN.match(style_name)
    if not match:
        return ""
    level = min(int(match.group(1)), 6)
    return "#" * level + " "


def _iter_block_items(document):
    """Standard python-docx idiom: walk `document.element.body`'s direct
    children in document order, yielding a `Paragraph` or `Table` for
    each -- the only way to preserve interleaved paragraph/table
    ordering (python-docx's own `.paragraphs`/`.tables` properties are
    each flattened separately and lose interleaving).
    """
    from docx.oxml.ns import qn  # noqa: PLC0415
    from docx.table import Table  # noqa: PLC0415
    from docx.text.paragraph import Paragraph  # noqa: PLC0415

    body = document.element.body
    for child in body.iterchildren():
        if child.tag == qn("w:p"):
            yield Paragraph(child, document)
        elif child.tag == qn("w:tbl"):
            yield Table(child, document)


def _render_table(table) -> str:
    lines = ["Table:"]
    for row in table.rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def _render_paragraph(paragraph) -> str:
    text = paragraph.text
    # python-docx (>=1.2) exposes hyperlink runs separately from plain
    # runs -- `.text` already includes their visible text, but the
    # target itself is only reachable via `.hyperlinks`. Append targets
    # as a plain reference note, never fetched (section 32).
    try:
        links = paragraph.hyperlinks
    except Exception:  # pragma: no cover -- defensive: older/odd paragraph shapes.
        links = []
    references = [f"[{link.text}]({link.address})" for link in links if getattr(link, "address", None)]
    if references:
        text = f"{text} ({'; '.join(references)})" if text.strip() else "; ".join(references)
    return text


def _discover_embedded_members(archive: zipfile.ZipFile) -> list[tuple[str, str, str]]:
    """Returns (member_path, display_name, relationship_kind) for every
    media/embedding member the document's own relationships declare --
    `relationship_kind` is "image" or "embedded_object" (dispatch sniffs
    the real container format for the latter). Never inferred from a
    bare filename glob alone -- driven by the actual `document.xml.rels`
    relationship graph, falling back to a raw `word/media/`+
    `word/embeddings/` member scan only if the rels part itself is
    missing/unreadable (a still-safe, still-inspected fallback, never a
    guess about content).
    """
    discovered: list[tuple[str, str, str]] = []
    seen_paths: set[str] = set()

    try:
        rels_xml = archive.read("word/_rels/document.xml.rels")
        root = ET.fromstring(rels_xml)
        for relationship in root.findall("r:Relationship", _RELS_NS):
            rel_type = relationship.get("Type", "")
            target = relationship.get("Target", "")
            target_mode = relationship.get("TargetMode", "Internal")
            if target_mode == "External":
                continue  # hyperlinks handled separately in paragraph text; never fetched here.
            member_path = f"word/{target}" if not target.startswith("word/") else target
            member_path = member_path.replace("word/../", "")
            if member_path not in archive.namelist() or member_path in seen_paths:
                continue
            display_name = target.rsplit("/", 1)[-1]
            if rel_type.endswith("/image"):
                discovered.append((member_path, display_name, "image"))
                seen_paths.add(member_path)
            elif rel_type.endswith("/package") or rel_type.endswith("/oleObject"):
                discovered.append((member_path, display_name, "embedded_object"))
                seen_paths.add(member_path)
    except (KeyError, ET.ParseError):
        pass

    for member_path in archive.namelist():
        if member_path in seen_paths:
            continue
        if member_path.startswith("word/media/"):
            discovered.append((member_path, member_path.rsplit("/", 1)[-1], "image"))
            seen_paths.add(member_path)
        elif member_path.startswith("word/embeddings/"):
            discovered.append((member_path, member_path.rsplit("/", 1)[-1], "embedded_object"))
            seen_paths.add(member_path)

    return discovered


def extract_docx(data: bytes, *, container_artifact_id: Optional[str], depth: int, budget: ExtractionBudget) -> tuple[str, list[KnowledgeArtifact]]:
    """Returns (this DOCX's own readable text -- paragraphs/headings/
    tables in document order, with hyperlink targets appended as
    reference notes -- and the flattened list of every artifact
    discovered embedded inside it, direct children at `depth`, deeper
    descendants at `depth+1` and beyond). `container_artifact_id` is
    this DOCX's own artifact_id if it is itself an embedded document
    (children get `parent_artifact_id=container_artifact_id`), or
    `None` if this call is extracting the ROOT document (children get
    `parent_artifact_id=None`, i.e. depth=0 -- see dispatch.py's module
    docstring for the root-vs-embedded distinction).
    """
    from backend.knowledge.ingestion.extractors.dispatch import extract_embedded_artifact  # noqa: PLC0415 -- see dispatch.py's own import-cycle note.
    from docx import Document  # noqa: PLC0415

    document = Document(io.BytesIO(data))

    lines: list[str] = []
    for block in _iter_block_items(document):
        block_type = type(block).__name__
        if block_type == "Table":
            rendered = _render_table(block)
            if rendered.strip():
                lines.append(rendered)
        else:
            prefix = _heading_prefix(block.style.name if block.style else None)
            text = _render_paragraph(block)
            if text.strip():
                lines.append(f"{prefix}{text}" if prefix else text)

    own_text = "\n".join(lines)

    child_artifacts: list[KnowledgeArtifact] = []
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        has_macros = "word/vbaProject.bin" in archive.namelist()
        members = _discover_embedded_members(archive)
        for position, (member_path, display_name, relationship_kind) in enumerate(members):
            member_bytes = archive.read(member_path)
            child_artifacts.extend(
                extract_embedded_artifact(
                    member_bytes,
                    parent_artifact_id=container_artifact_id,
                    depth=depth,
                    budget=budget,
                    display_name=display_name,
                    position=position,
                    relationship_kind=relationship_kind,
                )
            )
        if has_macros:
            from backend.knowledge.domain.artifacts import ArtifactExtractionStatus
            from backend.knowledge.ingestion.extraction import deterministic_artifact_id, hash_bytes

            macro_bytes = archive.read("word/vbaProject.bin")
            macro_hash = hash_bytes(macro_bytes)
            child_artifacts.append(
                KnowledgeArtifact(
                    artifact_id=deterministic_artifact_id(
                        parent_artifact_id=container_artifact_id, kind="macro_project", position=len(members), content_hash=macro_hash
                    ),
                    parent_artifact_id=container_artifact_id,
                    kind="macro_project",
                    display_name="vbaProject.bin",
                    depth=depth,
                    content_hash=macro_hash,
                    extraction_status=ArtifactExtractionStatus.SKIPPED,
                    extraction_error="macro-enabled document (VBA project) -- never executed, never decomposed",
                )
            )

    return own_text, child_artifacts
