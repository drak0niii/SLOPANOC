"""Governed procedure text semantics: what a command-looking line in an approved section IS.

Deterministic, extraction-time, EXPLICIT evidence only (no model, no vendor / unit / fault
vocabulary). Every command-looking candidate gets exactly one class:

    PARAMETERIZED_TEMPLATE    the source writes an explicit placeholder (`<name>`, `{name}`, `Key=xxxx`)
    FIXED_INSTANCE            a literal command, valid only exactly as written. A literal `Key=Value`
                              in it is a FIXED target: the state-change target gate decides whether
                              THIS case has exactly that instance (the text alone proves nothing).
    EXPLICIT_EXAMPLE          the source explicitly marks it illustrative: an example / sample label,
                              an inline "e.g." / "for example" / "such as" / "sample" / "illustrative"
                              marker, or a "replace <literal> with ..." instruction naming its literal
    SAMPLE_OUTPUT             printed output or a CLI transcript: an output-labelled block, a
                              prompt-prefixed transcript, or an output-typed fenced block
    SCREENSHOT_TRANSCRIPTION  text transcribed from an image: a screenshot / figure label, or a
                              section whose text is a model interpretation of an artifact
    UNKNOWN                   the line cannot be classified as an operation

Only PARAMETERIZED_TEMPLATE and FIXED_INSTANCE can become ProcedureActions. Nothing is ever
re-interpreted into another class: a literal never becomes a slot, and generic prose ("restart the
identified unit") never turns an instance into an example.

Document STRUCTURE (a numbered outline, section headings) supplies two further explicit relations,
never inferred from wording alone:
    governed conditions   top-level precondition / post-action outline blocks of the governed
                          version, and the verbatim text of the condition-specific block an action
                          sits in -- bound to the action so confirmation/approval cover them
    condition scope       an action inside a "<qualifier>-specific" outline block applies only to
                          that block's named condition (dimension = qualifier, value = its heading)
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional


class InstanceSemantics(str, Enum):
    PARAMETERIZED_TEMPLATE = "parameterized_template"
    FIXED_INSTANCE = "fixed_instance"
    EXPLICIT_EXAMPLE = "explicit_example"
    SAMPLE_OUTPUT = "sample_output"
    SCREENSHOT_TRANSCRIPTION = "screenshot_transcription"
    UNKNOWN = "unknown"


EXECUTABLE_SEMANTICS = frozenset({InstanceSemantics.PARAMETERIZED_TEMPLATE, InstanceSemantics.FIXED_INSTANCE})


class BlockKind(str, Enum):
    """The structural region a line sits in."""

    INSTRUCTION = "instruction"
    COMMAND = "command"
    EXAMPLE = "example"
    OUTPUT = "output"
    SCREENSHOT = "screenshot"


NON_INSTRUCTION_SEMANTICS = {
    BlockKind.EXAMPLE: InstanceSemantics.EXPLICIT_EXAMPLE,
    BlockKind.OUTPUT: InstanceSemantics.SAMPLE_OUTPUT,
    BlockKind.SCREENSHOT: InstanceSemantics.SCREENSHOT_TRANSCRIPTION,
}

# ---- labels (a line ending with ':' introduces a block) -----------------------------------------
LABEL_LINE = re.compile(r"^\s*(?:[•▪◦\-–—*]\s*)?([^:\n]{1,100}):\s*$")
_OUTPUT_LABEL = re.compile(r"\b(?:outputs?|print-?outs?|printouts?|transcripts?|expected\s+results?)\b", re.IGNORECASE)
"""Only unambiguous output wording: "logs" / "results" / "response" also name collection or check
steps in procedures, so they never mark a block as output on their own."""
_SCREENSHOT_LABEL = re.compile(
    r"\b(?:screen-?\s?shots?|screen\s+captures?|snapshots?|images?|figures?|pictures?|photos?)\b", re.IGNORECASE
)
_EXAMPLE_MARKER = r"(?:\be\.g\.|\b(?:examples?|for instance|samples?|illustrat\w*|such as)\b)"
_EXAMPLE_LABEL = re.compile(_EXAMPLE_MARKER, re.IGNORECASE)
_COMMAND_LABEL = re.compile(r"\bcommands?\b", re.IGNORECASE)


def label_kind(label: str) -> Optional[BlockKind]:
    """Block kind a label introduces. Output beats screenshot beats example beats command, so
    "Example output:" is output and "Example command:" is an example (never an instruction)."""
    for pattern, kind in (
        (_OUTPUT_LABEL, BlockKind.OUTPUT),
        (_SCREENSHOT_LABEL, BlockKind.SCREENSHOT),
        (_EXAMPLE_LABEL, BlockKind.EXAMPLE),
        (_COMMAND_LABEL, BlockKind.COMMAND),
    ):
        if pattern.search(label or ""):
            return kind
    return None


# ---- inline markers ------------------------------------------------------------------------------
_INLINE_EXAMPLE_BEFORE = re.compile(_EXAMPLE_MARKER + r"[^.;`]*$", re.IGNORECASE)
"""An example marker in the same clause, immediately before the code span ("e.g. `...`")."""
_INLINE_EXAMPLE_AFTER = re.compile(r"^\s*[(\[,]?\s*" + _EXAMPLE_MARKER, re.IGNORECASE)
"""An example marker right after the code span ("`...` (example)")."""
REPLACE_INSTRUCTION = re.compile(
    r"\b(?:replace|substitute)\s+[`'\"]?([A-Za-z0-9_\-\.:/=]+?)[`'\"]?\s+(?:with|by|for)\b", re.IGNORECASE
)
""""replace <literal> with ...": the named literal is explicitly illustrative (never a slot)."""

BULLET = re.compile(r"^\s*(?:[•▪◦\-–—*]|\d+[.)])\s+")
_TRANSCRIPT_HOST_PROMPT = re.compile(r"^\s*[A-Za-z0-9_\-\.@:~/]{2,64}[>#$%]\s+[a-z]")
"""`NODE> cmd` / `user@host$ cmd`: a recorded session line, not an instruction."""
_TRANSCRIPT_BARE_PROMPT = re.compile(r"^\s*[$>]\s+[a-z]")
_OUTPUT_FENCE_INFO = frozenset({"output", "console", "log", "logs", "terminal", "transcript", "stdout", "shell-session"})


def inline_example(prefix: str, suffix: str) -> bool:
    return bool(_INLINE_EXAMPLE_BEFORE.search(prefix or "") or _INLINE_EXAMPLE_AFTER.match(suffix or ""))


def transcript_line(line: str, *, in_fence: bool = False) -> bool:
    return bool(_TRANSCRIPT_HOST_PROMPT.match(line) or (in_fence and _TRANSCRIPT_BARE_PROMPT.match(line)))


def fence_kind(info: str, body: list[str], enclosing: Optional[BlockKind]) -> tuple[BlockKind, str]:
    first = (info or "").strip().split()[0].lower() if (info or "").strip() else ""
    if first in _OUTPUT_FENCE_INFO:
        return BlockKind.OUTPUT, f"fenced block typed {first!r}"
    if any(transcript_line(line, in_fence=True) for line in body):
        return BlockKind.OUTPUT, "fenced block is a prompt-prefixed CLI transcript"
    if enclosing in NON_INSTRUCTION_SEMANTICS:
        return enclosing, f"fenced block under a {enclosing.value} label"
    return BlockKind.COMMAND, ""


def replaced_literals(text: str) -> list[str]:
    return [m.group(1).strip("`'\".,;:") for m in REPLACE_INSTRUCTION.finditer(text or "")]


# ---- outline structure -----------------------------------------------------------------------------
_LEVEL1 = re.compile(r"^\s*(\d+)\.\s+(\S.*?)\s*:?\s*$")
"""`3. Some Heading` -- a top-level outline entry (number + '.')."""
_LEVEL2 = re.compile(r"^\s*(?:\d+\)|\d+\.\d+\.?|[A-Za-z]\))\s+(\S.*?)\s*:?\s*$")
"""`1) Heading` / `3.1 Heading` / `a) Heading` -- a child entry inside a condition-specific block."""
_PRECONDITION_LABEL = re.compile(r"\bpre-?\s?(?:conditions?|requisites?|checks?)\b", re.IGNORECASE)
_POST_ACTION_LABEL = re.compile(r"(?:\bpost[- ]\w+|^\s*after\b)", re.IGNORECASE)
_SCOPED_LABEL = re.compile(r"\b([A-Za-z]+)[- ]specific\b", re.IGNORECASE)
_NUMBERING = re.compile(r"^\s*(?:\d+(?:\.\d+)*[.)]?|[A-Za-z]\))\s+")
_MAX_CONDITION_LINES = 40


class ConditionKind(str, Enum):
    PRECONDITION = "precondition"
    POST_ACTION = "post_action"
    BLOCK_CONDITION = "block_condition"


@dataclass
class OutlineChild:
    heading: str
    line: int
    body: list[int] = field(default_factory=list)


@dataclass
class OutlineBlock:
    kind: str  # "precondition" | "post_action" | "scoped"
    label: str
    line: int
    number: int
    body: list[int] = field(default_factory=list)
    dimension: Optional[str] = None
    children: list[OutlineChild] = field(default_factory=list)


@dataclass
class Outline:
    blocks: list[OutlineBlock]
    first_level1_number: Optional[int]
    """Number of the top-level entry the section STARTS with (None: it starts with body text)."""

    @property
    def trailing_scope(self) -> Optional[OutlineBlock]:
        """A condition-specific block that is the section's last entry and has no body: its
        children are the following sections of the same governed version."""
        last = self.blocks[-1] if self.blocks else None
        return last if last is not None and last.kind == "scoped" and not last.body else None

    def block_at(self, line: int) -> Optional[OutlineBlock]:
        return next((b for b in self.blocks if b.line == line or line in b.body), None)

    def child_at(self, line: int) -> tuple[Optional[OutlineBlock], Optional[OutlineChild]]:
        block = self.block_at(line)
        if block is None or block.kind != "scoped":
            return None, None
        return block, next((c for c in block.children if c.line == line or line in c.body), None)


def _block_kind(text: str) -> Optional[str]:
    if _PRECONDITION_LABEL.search(text):
        return "precondition"
    if _POST_ACTION_LABEL.search(text):
        return "post_action"
    if _SCOPED_LABEL.search(text):
        return "scoped"
    return None


def parse_outline(content: str) -> Outline:
    """Top-level numbered entries that DECLARE a precondition, post-action or condition-specific
    block, with their bodies; children of condition-specific blocks. A top-level entry closes the
    open block only when it is a later sibling (a higher number), so numbered steps inside a block
    never end it early."""
    lines = (content or "").splitlines()
    blocks: list[OutlineBlock] = []
    current: Optional[OutlineBlock] = None
    first_level1: Optional[int] = None
    seen_text = False
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        level1 = _LEVEL1.match(line)
        if not seen_text:
            first_level1 = int(level1.group(1)) if level1 else None
            seen_text = True
        if level1 and (current is None or int(level1.group(1)) > current.number):
            kind = _block_kind(level1.group(2))
            current = None
            if kind:
                scoped = _SCOPED_LABEL.search(level1.group(2)) if kind == "scoped" else None
                current = OutlineBlock(kind=kind, label=level1.group(2).strip(), line=index, number=int(level1.group(1)),
                                       dimension=scoped.group(1).lower() if scoped else None)
                blocks.append(current)
            continue
        if current is None:
            continue
        current.body.append(index)
        if current.kind == "scoped":
            level2 = _LEVEL2.match(line)
            if level2:
                current.children.append(OutlineChild(heading=level2.group(1).strip(), line=index))
            elif current.children:
                current.children[-1].body.append(index)
    return Outline(blocks=blocks, first_level1_number=first_level1)


def heading_value(heading: Optional[str]) -> Optional[str]:
    text = _NUMBERING.sub("", (heading or "").strip()).strip().rstrip(":").strip()
    return text or None


def _condition(kind: ConditionKind, scope: str, text: str, source: str, line: int) -> dict[str, Any]:
    return {"kind": kind.value, "scope": scope, "text": text.strip()[:300], "source": source, "line": line + 1}


def outline_document_conditions(outline: Outline, lines: list[str], source: str) -> list[dict[str, Any]]:
    """Verbatim lines of the top-level precondition / post-action blocks."""
    out: list[dict[str, Any]] = []
    for block in outline.blocks:
        if block.kind == "scoped":
            continue
        kind = ConditionKind.PRECONDITION if block.kind == "precondition" else ConditionKind.POST_ACTION
        for index in block.body:
            if lines[index].strip():
                out.append(_condition(kind, "document", lines[index], source, index))
    return out[: _MAX_CONDITION_LINES]


def block_conditions(lines: list[str], indices: Iterable[int], exclude: set[int], source: str) -> list[dict[str, Any]]:
    """Verbatim text of an action's own condition-specific block (commands, command labels and
    non-instruction lines excluded) -- shown to and bound into confirmation/approval, never
    interpreted."""
    out = []
    for index in indices:
        text = lines[index].strip()
        if not text or index in exclude:
            continue
        label = LABEL_LINE.match(lines[index])
        if label and label_kind(label.group(1)) is not None:
            continue
        out.append(_condition(ConditionKind.BLOCK_CONDITION, "block", text, source, index))
    return out[: _MAX_CONDITION_LINES]


def governed_document_scope(knowledge_object: Any, section_id: str) -> dict[str, Any]:
    """Document-level governed structure for ONE section of ONE governed version (pure):

    document_conditions  verbatim lines of top-level precondition / post-action blocks of the
                         version's OTHER sections, and of sections whose heading declares one
    condition_scope      when the section is a child of a condition-specific block left open by an
                         earlier section: {dimension, value (the section heading), label, source}
    artifact_derived     the section's text is a model interpretation of an artifact (e.g. an image)
    """
    kid = str(getattr(knowledge_object, "knowledge_id", ""))
    version = str(getattr(getattr(knowledge_object, "version", None), "label", ""))
    sections = sorted(getattr(knowledge_object, "sections", []) or [], key=lambda s: s.sequence)
    artifacts = {a.artifact_id: a for a in getattr(knowledge_object, "artifacts", []) or []}
    conditions: list[dict[str, Any]] = []
    condition_scope: Optional[dict[str, Any]] = None
    artifact_derived = False
    open_scope: Optional[tuple[OutlineBlock, str]] = None
    for section in sections:
        source = f"{kid}:{version}:{section.section_id}"
        lines = section.content.splitlines()
        outline = parse_outline(section.content)
        if open_scope is not None and outline.first_level1_number is not None and outline.first_level1_number > open_scope[0].number:
            open_scope = None
        child_scope = None
        if open_scope is not None:
            block, block_source = open_scope
            child_scope = {
                "dimension": block.dimension, "value": heading_value(section.heading), "label": block.label,
                "source": block_source, "line": block.line + 1,
            }
        if section.section_id == section_id:
            condition_scope = child_scope
            artifact = artifacts.get(section.artifact_id) if section.artifact_id else None
            artifact_derived = bool(artifact is not None and getattr(artifact, "derived", False))
        elif child_scope is None:
            heading_kind = _block_kind(section.heading or "")
            if heading_kind in ("precondition", "post_action"):
                kind = ConditionKind.PRECONDITION if heading_kind == "precondition" else ConditionKind.POST_ACTION
                conditions += [_condition(kind, "document", line, source, i) for i, line in enumerate(lines) if line.strip()]
            else:
                conditions += outline_document_conditions(outline, lines, source)
        if outline.trailing_scope is not None:
            open_scope = (outline.trailing_scope, source)
    scope = {
        "status": "resolved",
        "document_conditions": conditions[: _MAX_CONDITION_LINES],
        "condition_scope": condition_scope,
        "artifact_derived": artifact_derived,
    }
    scope["digest"] = scope_digest(scope)
    return scope


def scope_digest(scope: Mapping[str, Any]) -> str:
    body = {k: scope.get(k) for k in ("document_conditions", "condition_scope", "artifact_derived")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def unavailable_scope(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


# ---- condition establishment ------------------------------------------------------------------------
def _phrase_pattern(value: str) -> Optional[re.Pattern[str]]:
    words = (value or "").split()
    if not words:
        return None
    return re.compile(r"(?<![A-Za-z0-9])" + r"\s+".join(re.escape(w) for w in words) + r"(?![A-Za-z0-9])", re.IGNORECASE)


def condition_evidence(condition_scope: Optional[Mapping[str, Any]], case_results: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    """The trusted case results (same fault, validated, non-stale) that literally state the scoped
    condition's value as a phrase. Exact phrase only -- no synonyms, no partial names."""
    pattern = _phrase_pattern(str((condition_scope or {}).get("value") or ""))
    if pattern is None:
        return []
    return [
        {"step_id": str(r.get("step_id")), "result_id": str(r.get("result_id"))}
        for r in case_results or []
        if pattern.search(str(r.get("text") or ""))
    ]


# ---- server derivation per run (selected evidence -> same governed version) ----------------------------
GOVERNED_SCOPE_ANNOTATION = "governed_scope"


async def ensure_governed_scope(run_id: Optional[str]) -> None:
    """Derive, once per run and SELECTED identity, the document-level governed structure from the
    SAME governed version in the repository. A version that cannot be loaded, or whose section
    text differs from the selected evidence, is recorded as unavailable (state changes from it then
    fail closed)."""
    from backend.tools.knowledge.runtime import (
        get_evidence_annotation,
        get_knowledge_repository,
        record_evidence_annotation,
        snapshot_selected_knowledge_evidence,
    )

    if not run_id:
        return
    versions: dict[tuple[str, str], Any] = {}
    for item in snapshot_selected_knowledge_evidence(run_id):
        identity = (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)
        if get_evidence_annotation(run_id, identity, GOVERNED_SCOPE_ANNOTATION) is not None:
            continue
        key = identity[:2]
        if key not in versions:
            try:
                versions[key] = await get_knowledge_repository().get(*key)
            except Exception as exc:  # repository unavailable: fail closed for state changes
                versions[key] = exc
        knowledge_object = versions[key]
        if knowledge_object is None or isinstance(knowledge_object, Exception):
            scope = unavailable_scope("governed version could not be loaded")
        else:
            section = next((s for s in knowledge_object.sections if s.section_id == identity[2]), None)
            if section is None or section.content != item.section.content:
                scope = unavailable_scope("selected section does not match the governed version")
            else:
                scope = governed_document_scope(knowledge_object, identity[2])
        record_evidence_annotation(run_id, identity, GOVERNED_SCOPE_ANNOTATION, scope)
