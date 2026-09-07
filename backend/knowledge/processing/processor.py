"""The generic processing contract and its one reference implementation
(Phase 5.1D).

`KnowledgeContentProcessor` is a minimal structural `typing.Protocol`
(same pattern as `backend/knowledge/ingestion/adapters.py`'s
`KnowledgeSourceAdapter` and `backend/api/chat_service.py`'s `_Runner`).
`HeadingStructureProcessor` is the ONE reference implementation this
phase builds: a deterministic, synchronous, local text processor that
recognizes explicit Markdown-style ATX heading SYNTAX (`#`, `##`, ...,
up to `######`) already present in normalized text. It is document-type-
and source-agnostic by construction -- see the module docstring in
`backend/knowledge/processing/__init__.py` and
docs/KNOWLEDGE_CONTRACT.md's Phase 5.1D section for the full rationale.

DESIGN NOTE on empty headings (documented here since it resolves an
apparent tension in the phase instructions): a heading immediately
followed by another heading (or by end of document), with no body text
between them, produces NO section for that heading. `StructuredKnowledgeSection.content`
must always be non-blank (a hard structural invariant shared with
`KnowledgeSection` in domain/models.py), so a heading with nothing to
attach to is structural punctuation with no content to represent --
dropping it is the only way to keep "content is always non-blank"
exceptionless while also avoiding meaningless empty sections for
adjacent headings. A REAL nested-heading document (each level followed
by at least some body text) is unaffected by this and preserves every
level -- see test_processing_nested_headings in
backend/tests/knowledge/test_processing_reference_processor.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Protocol

from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.processing.contracts import StructuredKnowledgeDocument, StructuredKnowledgeSection

_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(\S.*)$")
_FENCE_MARKER = "```"


class KnowledgeContentProcessor(Protocol):
    """Structural contract: transform one `IngestedKnowledgeDocument`
    into one `StructuredKnowledgeDocument`. Deterministic, local,
    synchronous -- no network, no database, no Gemini/ADK call, no
    global state.
    """

    def process(self, document: IngestedKnowledgeDocument) -> StructuredKnowledgeDocument: ...


@dataclass
class _PendingSpan:
    """Internal accumulator for one heading's (or the document's leading
    content's) body lines, before it is either emitted as a
    `StructuredKnowledgeSection` or dropped for having no body content.
    Never exposed outside this module.
    """

    heading: Optional[str]
    heading_level: Optional[int]
    body_line_numbers: list[int] = field(default_factory=list)


class HeadingStructureProcessor:
    """The Phase 5.1D reference generic processor.

    Recognizes ATX-style Markdown headings (`# text` through
    `###### text`) as structural boundaries -- SYNTAX detection only,
    never semantic interpretation of the heading text itself. A line
    inside an explicit fenced block (a line whose stripped content
    starts with ``` , with or without a following language tag) is never
    treated as a heading, even if it looks like one. If no explicit
    heading is detected anywhere in the document, the entire content
    becomes exactly one section -- never chunked by size. Operational
    body text (including anything inside a fenced block) is preserved
    verbatim; only the heading marker lines themselves are separated out
    into `heading`/`heading_level`.
    """

    def process(self, document: IngestedKnowledgeDocument) -> StructuredKnowledgeDocument:
        lines = document.content.split("\n")
        spans = self._segment_into_spans(lines)
        sections = self._spans_to_sections(spans, lines)

        if not sections:
            # Every detected span turned out to have no body content
            # (e.g. a document that is nothing but adjacent headings) --
            # rather than emit nothing, fall back to the whole,
            # unmodified document as a single section. This is the same
            # "unknown/degenerate structure remains intact" principle
            # applied at the document level, not just when zero headings
            # exist at all.
            sections = [
                StructuredKnowledgeSection(
                    section_key="section-0000",
                    sequence=0,
                    heading=None,
                    heading_level=None,
                    content=document.content,
                    source_locator=f"lines:1-{len(lines)}" if lines else None,
                )
            ]

        return StructuredKnowledgeDocument(source_document=document, sections=sections)

    @staticmethod
    def _segment_into_spans(lines: list[str]) -> list[_PendingSpan]:
        spans: list[_PendingSpan] = []
        current = _PendingSpan(heading=None, heading_level=None)
        in_fence = False

        for line_number, line in enumerate(lines, start=1):
            stripped = line.strip()

            if stripped.startswith(_FENCE_MARKER):
                in_fence = not in_fence
                current.body_line_numbers.append(line_number)
                continue

            if not in_fence:
                match = _HEADING_PATTERN.match(line)
                if match:
                    spans.append(current)
                    level = len(match.group(1))
                    heading_text = match.group(2)
                    current = _PendingSpan(heading=heading_text, heading_level=level)
                    continue

            current.body_line_numbers.append(line_number)

        spans.append(current)
        return spans

    @staticmethod
    def _spans_to_sections(spans: list[_PendingSpan], lines: list[str]) -> list[StructuredKnowledgeSection]:
        sections: list[StructuredKnowledgeSection] = []
        for span in spans:
            if not span.body_line_numbers:
                continue
            body_text = "\n".join(lines[line_number - 1] for line_number in span.body_line_numbers)
            if not body_text.strip():
                continue

            index = len(sections)
            sections.append(
                StructuredKnowledgeSection(
                    section_key=f"section-{index:04d}",
                    sequence=index,
                    heading=span.heading,
                    heading_level=span.heading_level,
                    content=body_text,
                    source_locator=f"lines:{span.body_line_numbers[0]}-{span.body_line_numbers[-1]}",
                )
            )
        return sections
