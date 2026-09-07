"""Phase 5.1D: the reference HeadingStructureProcessor -- structural
heading detection, no-structure fallback, fence safety, content fidelity,
document-type independence, source locators, deterministic local
identity, input preservation, and the pre-governance boundary.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType
from backend.knowledge.domain.models import Applicability, KnowledgeMetadata, KnowledgeSource, KnowledgeVersion
from backend.knowledge.ingestion.contracts import IngestedKnowledgeDocument
from backend.knowledge.processing.processor import HeadingStructureProcessor

_PROCESSING_DIR = Path(__file__).resolve().parents[2] / "knowledge" / "processing"

_processor = HeadingStructureProcessor()


def _document(content: str, **overrides: object) -> IngestedKnowledgeDocument:
    fields: dict[str, object] = {
        "source": KnowledgeSource(source_system="source_a", source_id="doc-1"),
        "title": "Some document",
        "content": content,
    }
    fields.update(overrides)
    return IngestedKnowledgeDocument(**fields)


# --- A-D: basic structure ------------------------------------------------


def test_a_one_explicit_heading_produces_one_headed_section() -> None:
    result = _processor.process(_document("# Heading\nBody"))
    assert len(result.sections) == 1
    assert result.sections[0].heading == "Heading"
    assert result.sections[0].heading_level == 1
    assert result.sections[0].content == "Body"


def test_b_multiple_headings_produce_ordered_sections() -> None:
    result = _processor.process(_document("# First\nBody A\n# Second\nBody B"))
    assert [s.heading for s in result.sections] == ["First", "Second"]
    assert [s.content for s in result.sections] == ["Body A", "Body B"]
    assert [s.sequence for s in result.sections] == [0, 1]


def test_c_nested_headings_preserve_explicit_levels() -> None:
    """A REAL nested-heading document (each level carries body text) --
    see processor.py's module docstring for why a heading with genuinely
    NO body content (the literal degenerate "# Main\n## Child\n### Grandchild"
    with nothing else) is a different, deliberately-documented case,
    covered separately by test_adjacent_headings_with_no_body_are_dropped.
    """
    content = "# Main\nMain intro.\n## Child\nChild text.\n### Grandchild\nGrandchild text."
    result = _processor.process(_document(content))
    assert [(s.heading, s.heading_level) for s in result.sections] == [
        ("Main", 1),
        ("Child", 2),
        ("Grandchild", 3),
    ]
    assert [s.content for s in result.sections] == ["Main intro.", "Child text.", "Grandchild text."]


def test_d_introductory_content_before_first_heading_is_preserved() -> None:
    result = _processor.process(_document("Intro text\n\n# Heading\nBody"))
    assert len(result.sections) == 2
    assert result.sections[0].heading is None
    assert result.sections[0].heading_level is None
    assert result.sections[0].content == "Intro text\n"
    assert result.sections[1].heading == "Heading"
    assert result.sections[1].content == "Body"


# --- no-structure fallback ----------------------------------------------


def test_plain_content_with_no_headings_produces_exactly_one_section() -> None:
    content = "This is a document.\nIt contains several paragraphs.\nThere are no explicit headings."
    result = _processor.process(_document(content))
    assert len(result.sections) == 1
    assert result.sections[0].heading is None
    assert result.sections[0].content == content


def test_large_document_with_no_headings_is_never_chunked() -> None:
    """Regression: protects the architecture from a future 'just chunk
    every N characters' shortcut (instruction section 46).
    """
    content = "\n".join(f"Paragraph {i} with some operational detail." for i in range(5000))
    result = _processor.process(_document(content))
    assert len(result.sections) == 1
    assert result.sections[0].content == content


def test_adjacent_headings_with_no_body_are_dropped() -> None:
    """The literal '# A\n## B\nContent' example from the phase
    instructions: 'A' has no body (immediately followed by '## B') and
    is dropped; only 'B' (which has real content) is emitted. See
    processor.py's module docstring for the full rationale.
    """
    result = _processor.process(_document("# A\n## B\nContent"))
    assert len(result.sections) == 1
    assert result.sections[0].heading == "B"
    assert result.sections[0].heading_level == 2
    assert result.sections[0].content == "Content"


def test_document_that_is_only_adjacent_empty_headings_falls_back_to_one_section() -> None:
    """Every heading has empty body -> nothing emittable -> the whole,
    unmodified document becomes one section rather than producing zero
    sections (which would violate the 'sections non-empty' invariant).
    """
    content = "# Alpha\n# Root Cause\n# Completely Random Heading"
    result = _processor.process(_document(content))
    assert len(result.sections) == 1
    assert result.sections[0].heading is None
    assert result.sections[0].content == content


# --- no hard-coded semantics -----------------------------------------------


@pytest.mark.parametrize(
    "heading_text",
    ["Alpha", "Root Cause", "Completely Random Heading", "Something Invented In 2035", "Bananas", "Purpose", "Rollback"],
)
def test_arbitrary_heading_text_handled_identically(heading_text: str) -> None:
    result = _processor.process(_document(f"# {heading_text}\nSome body text."))
    assert len(result.sections) == 1
    assert result.sections[0].heading == heading_text
    assert result.sections[0].content == "Some body text."


def test_arbitrary_headings_with_bodies_all_preserved_in_order() -> None:
    content = "\n".join(
        [
            "# Alpha",
            "Alpha body.",
            "# Root Cause",
            "Root cause body.",
            "# Completely Random Heading",
            "Random body.",
            "# Something Invented In 2035",
            "Invented body.",
        ]
    )
    result = _processor.process(_document(content))
    assert [s.heading for s in result.sections] == ["Alpha", "Root Cause", "Completely Random Heading", "Something Invented In 2035"]
    assert [s.content for s in result.sections] == ["Alpha body.", "Root cause body.", "Random body.", "Invented body."]


def test_no_hardcoded_operational_heading_vocabulary_in_processing_package() -> None:
    """Static proof: no declared class/constant name in
    backend/knowledge/processing/ suggests a fixed operational heading
    dictionary (MOP_SECTIONS, RCA_SECTIONS, SOP_SECTIONS, a section-type
    registry, ...). Checked via real AST declarations, not raw text, so
    this package's own docstrings explaining the absence are never a
    false positive.
    """
    forbidden_substrings = ("_SECTIONS", "SECTIONTYPEENUM", "HEADING_REGISTRY", "KNOWN_HEADING")
    violations: list[tuple[str, str]] = []
    for path in sorted(_PROCESSING_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        declared_names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                declared_names.append(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                declared_names.extend(t.id for t in targets if isinstance(t, ast.Name))
        for name in declared_names:
            upper = name.upper()
            for forbidden in forbidden_substrings:
                if forbidden in upper:
                    violations.append((path.name, name))
    assert violations == [], f"hardcoded operational heading vocabulary found: {violations}"


def test_no_branching_on_heading_text_identifiers() -> None:
    """No production code compares a heading string against a known
    operational label (e.g. `if heading == "Rollback"`). Checked
    structurally: no string constant among the forbidden operational
    labels appears as an ast.Compare operand anywhere in the package.
    """
    forbidden_labels = {"Rollback", "Root Cause", "Preconditions", "Purpose", "Validation", "Escalation", "Corrective Action"}
    violations: list[tuple[str, str]] = []
    for path in sorted(_PROCESSING_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for operand in [node.left, *node.comparators]:
                    if isinstance(operand, ast.Constant) and operand.value in forbidden_labels:
                        violations.append((path.name, str(operand.value)))
    assert violations == [], f"production code branches on a known operational heading label: {violations}"


# --- document-type independence -------------------------------------------


@pytest.mark.parametrize("document_type_hint", [KnowledgeDocumentType.MOP, KnowledgeDocumentType.RCA, KnowledgeDocumentType.OTHER, None])
def test_document_type_hint_does_not_change_segmentation(document_type_hint) -> None:
    content = "# Diagnostic Steps\nshow process-status --detail\r\nkey=value"
    baseline = _processor.process(_document(content))
    result = _processor.process(_document(content, document_type_hint=document_type_hint))
    assert [(s.heading, s.heading_level, s.content) for s in result.sections] == [
        (s.heading, s.heading_level, s.content) for s in baseline.sections
    ]


def test_processor_never_reads_document_type_hint_at_all() -> None:
    """Static proof, complementing the runtime parametrized check above:
    the processing package never references `document_type_hint`
    anywhere in its own code.
    """
    for path in sorted(_PROCESSING_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "document_type_hint":
                pytest.fail(f"{path.name} references document_type_hint -- segmentation must stay type-independent")


# --- code fences ---------------------------------------------------------


def test_heading_like_line_inside_labeled_fence_is_not_a_heading() -> None:
    content = "# Real Heading\n```text\n# not a heading\nshow something\n```\n# Next Heading\nStuff"
    result = _processor.process(_document(content))
    assert [s.heading for s in result.sections] == ["Real Heading", "Next Heading"]
    assert "# not a heading" in result.sections[0].content
    assert "show something" in result.sections[0].content


def test_heading_like_line_inside_unlabeled_fence_is_not_a_heading() -> None:
    content = "# Real\nintro\n```\n# still not a heading\n```\nmore"
    result = _processor.process(_document(content))
    assert len(result.sections) == 1
    assert result.sections[0].heading == "Real"
    assert "# still not a heading" in result.sections[0].content


def test_unlabeled_fence_only_document_stays_one_section() -> None:
    content = "```\n# still not a heading\n```"
    result = _processor.process(_document(content))
    assert len(result.sections) == 1
    assert result.sections[0].heading is None
    assert result.sections[0].content == content


# --- content fidelity -----------------------------------------------------


def test_commands_and_config_syntax_preserved_verbatim() -> None:
    body_lines = [
        "show process-status --detail",
        r"path\to\file",
        "key=value",
        "special !@#$%^&*() punctuation",
    ]
    content = "# Diagnostic Steps\n" + "\n".join(body_lines)
    result = _processor.process(_document(content))
    assert result.sections[0].content == "\n".join(body_lines)


def test_no_whitespace_case_or_punctuation_rewriting() -> None:
    content = "# Heading\n  Leading spaces preserved.  \nUPPER and lower CaSe.\ttab\tcharacters"
    result = _processor.process(_document(content))
    assert result.sections[0].content == "  Leading spaces preserved.  \nUPPER and lower CaSe.\ttab\tcharacters"


# --- source locators -------------------------------------------------------


def test_locators_are_deterministic_one_based_and_repeatable() -> None:
    content = "Intro\n# Heading\nLine A\nLine B"
    first = _processor.process(_document(content))
    second = _processor.process(_document(content))
    assert [s.source_locator for s in first.sections] == [s.source_locator for s in second.sections]
    assert first.sections[0].source_locator == "lines:1-1"  # intro
    assert first.sections[1].source_locator == "lines:3-4"  # headed section body


def test_final_section_locator_reaches_end_of_document() -> None:
    content = "# Heading\nLine 2\nLine 3\nLine 4"
    result = _processor.process(_document(content))
    assert result.sections[0].source_locator == "lines:2-4"


# --- local section identity -------------------------------------------------


def test_section_keys_are_deterministic_and_unique() -> None:
    result = _processor.process(_document("# First\nA\n# Second\nB\n# Third\nC"))
    keys = [s.section_key for s in result.sections]
    assert keys == ["section-0000", "section-0001", "section-0002"]
    assert len(set(keys)) == len(keys)


def test_repeated_processing_gives_identical_section_keys() -> None:
    content = "# First\nA\n# Second\nB"
    first = _processor.process(_document(content))
    second = _processor.process(_document(content))
    assert [s.section_key for s in first.sections] == [s.section_key for s in second.sections]


def test_no_uuid_or_random_identity() -> None:
    import re

    result = _processor.process(_document("# Heading\nBody"))
    assert re.fullmatch(r"section-\d{4}", result.sections[0].section_key)


# --- input preservation ---------------------------------------------------


def test_structured_document_preserves_original_ingested_document_unmodified() -> None:
    source = KnowledgeSource(source_system="source_a", source_id="doc-1", source_uri="https://example.invalid/1", display_name="Doc")
    metadata = KnowledgeMetadata(owner="RAN Engineering", tags=["upgrade"])
    applicability = Applicability(dimensions={"vendor": ["Ericsson"]})
    original = IngestedKnowledgeDocument(
        source=source,
        title="Upgrade MOP",
        content="# Heading\nBody",
        knowledge_id_hint="native-42",
        document_type_hint=KnowledgeDocumentType.MOP,
        version_hint=KnowledgeVersion(label="4.2"),
        metadata=metadata,
        applicability=applicability,
        media_type="text/plain",
        source_revision="etag-1",
    )

    result = _processor.process(original)

    assert result.source_document == original
    assert result.source_document.source == source
    assert result.source_document.title == "Upgrade MOP"
    assert result.source_document.content == "# Heading\nBody"
    assert result.source_document.knowledge_id_hint == "native-42"
    assert result.source_document.document_type_hint is KnowledgeDocumentType.MOP
    assert result.source_document.version_hint == KnowledgeVersion(label="4.2")
    assert result.source_document.metadata == metadata
    assert result.source_document.applicability == applicability
    assert result.source_document.media_type == "text/plain"
    assert result.source_document.source_revision == "etag-1"


# --- pre-governance boundary ------------------------------------------------


def test_structured_document_requires_no_lifecycle_status() -> None:
    result = _processor.process(_document("# Heading\nBody"))
    assert "lifecycle_status" not in type(result).model_fields
    assert "lifecycle_status" not in type(result.sections[0]).model_fields


def test_structured_document_fabricates_no_final_knowledge_object() -> None:
    from backend.knowledge.domain.models import KnowledgeObject
    from backend.knowledge.processing.contracts import StructuredKnowledgeDocument

    result = _processor.process(_document("# Heading\nBody"))
    assert not isinstance(result, KnowledgeObject)
    assert type(result) is StructuredKnowledgeDocument
    assert "knowledge_id" not in type(result).model_fields


def test_structured_section_has_no_final_knowledge_section_identity() -> None:
    from backend.knowledge.processing.contracts import StructuredKnowledgeSection

    result = _processor.process(_document("# Heading\nBody"))
    assert "knowledge_id" not in type(result.sections[0]).model_fields
    assert "section_id" not in type(result.sections[0]).model_fields
    assert type(result.sections[0]) is StructuredKnowledgeSection


def test_structured_document_requires_no_agent_or_retrieval_fields() -> None:
    from backend.knowledge.processing.contracts import StructuredKnowledgeDocument

    assert set(StructuredKnowledgeDocument.model_fields) == {"source_document", "sections"}
