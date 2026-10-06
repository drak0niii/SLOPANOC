"""Universal operational-command egress boundary (server-owned, producer-independent).

    NO operational command may appear in user-visible text unless that exact command is backed by
    a valid CURRENT-TURN command authorization record.

Live defect (session c70e3d84, run 1ed56d64): Team Manager declared governed knowledge required,
refused safely and did not consult the Technical Authority Engineer; the forced governed-knowledge
completion ran Incident Manager, whose free-text summary copied a literal state-changing command
from a governed section ("The command to restart an RRU ... is `<command>`"). No ProcedureAction,
Command Authority or synthesis boundary participated -- the TAE synthesis boundary only ran when
the TAE had run. This module applies to EVERY final answer regardless of producer (TAE, Team
Manager, Incident Manager, forced governed completion, troubleshooting-guidance rendering, trusted
presentation, fallback text, a future specialist).

The ONLY authorization record is the current run's Technical Authority Engineer execution record
(the same one the TAE synthesis boundary uses): its `diagnostic_step.command` -- set only when
Command Authority authorized it in THIS run -- that also appears, authorized, in the run's approved
catalog. A record from another run, an earlier turn, another fault, an approval-required action, a
governed document's own text, or a model/operator/Incident Manager claim never authorizes anything.

Detection is structural and generic (no vendor, command, target or fault vocabulary): code-formatted
invocations (inline code, fenced blocks, shell-prompt lines), argument-syntax lines, spans introduced
by command wording ("the command is", "syntax:", "run", "for example"), and command strings the
governed procedure extractor itself recognises in this turn's selected evidence. It reuses the
extractor's command-shape rules and the server's operation classifier. Narrative technical prose
("the procedure contains a restart step") is not a command and is kept. Removal is by sentence /
list item / code line; nothing is ever added except a fixed server sentence saying no command can
be provided.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional

_logger = logging.getLogger(__name__)

NO_AUTHORIZED_COMMAND_TEXT = (
    "No operational command can be provided for this request: no command has passed the current "
    "authorization checks."
)

_NON_COMMAND_WORDS = frozenset(
    """a an the this that these those it its they them their there here you your we our us i my me he she his her
    on in at to for of from with by into onto over under about after before between during within without
    and or but nor so then than if else when while where which what who whom whose how why
    is are was were be been being am do does did done has have had can could shall should will would may might must
    not no yes as all any some each every both either neither other another same such only also just
    available following above below next previous current same one""".split()
)
"""Generic English function words: a span starting with one is prose, never a command invocation."""

_FENCED = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_INLINE = re.compile(r"`([^`\n]+)`")
_PROMPT_LINE = re.compile(r"^\s*[$#>]\s+(\S.*)$")
_LIST_PREFIX = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
_EMPHASIS = re.compile(r"\*\*|__|`")
_INTRO = re.compile(
    r"(?:"
    r"\b(?:commands?|cmds?|syntax|cli|command\s+line)\b[^.!?\n`]{0,120}?(?:\b(?:is|are|would\s+be|looks\s+like|reads)\b|:)"
    r"|\b(?:run|execute|issue|enter|type|use|try|invoke)\b(?:\s+(?:the|this|that)\s+command)?"
    r"|\b(?:e\.g\.|i\.e\.|for\s+example|for\s+instance|such\s+as)"
    r"|\bexample(?:\s+(?:syntax|command))?\s*:"
    r")\s*,?\s*",
    re.IGNORECASE,
)
_SPAN_END = re.compile(r"[.;!?](?:\s|$)|\n|`|,\s|\s[-–—]\s|\s\(")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_CLAUSE_SPLIT = re.compile(r",\s*(?:and\s+then|then|and|also)\s+|;\s*|,\s*|\s+(?:and\s+then|then)\s+", re.IGNORECASE)
_SECRET_VALUE = re.compile(r"(?i)\b(pass(?:word|wd)?|secret|token|api[_-]?key|key)\s*=\s*\S+")
_ORPHAN_LABEL = re.compile(r"^\W*(?:commands?|cmds?|syntax|example(?:\s+syntax)?|run)\W*:?\W*$", re.IGNORECASE)
_MAX_CANDIDATE_CHARS = 160


def _clean(span: str) -> str:
    text = _EMPHASIS.sub("", span or "").strip()
    text = text.strip("\"'“”‘’")
    return text.rstrip(" .,;:!?").strip()


def normalize_command(text: str) -> str:
    return re.sub(r"\s+", " ", _clean(text)).casefold()


def _command_parts():
    from backend.agents.technical_authority_engineer.agent_tool import classify_command_operation
    from backend.agents.technical_authority_engineer.procedure_actions import (
        _COMMAND_VERB,
        _has_argument_syntax,
        _is_command_shaped,
    )
    from backend.agents.technical_authority_engineer.schemas import CommandOperationType

    return classify_command_operation, _COMMAND_VERB, _has_argument_syntax, _is_command_shaped, CommandOperationType


def is_command_like(span: str, *, code: bool = False) -> bool:
    """A single command invocation, by the server's own structural rules: a lowercase command word
    (never an English function word) that the operation classifier recognises, or that carries
    command-argument syntax, or -- when code-formatted -- that is followed by arguments."""
    classify, command_verb, has_argument_syntax, is_command_shaped, op_types = _command_parts()
    text = _clean(span)
    if not text or not is_command_shaped(text):
        return False
    tokens = text.split()
    first = tokens[0]
    if not command_verb.match(first) or first in _NON_COMMAND_WORDS:
        return False
    if classify(text) is not op_types.UNKNOWN:
        return True
    if has_argument_syntax(text):
        return True
    return code and len(tokens) >= 2


# ---------------------------------------------------------------------------------------------
# Current-turn authorization record
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CommandAuthorization:
    """A command Command Authority authorized in THIS run and that the run's validated technical
    step presents. Never persisted as authority; rebuilt from the current run's record only."""

    command: str
    normalized: str
    run_id: str
    procedure_action_id: Optional[str]
    source_id: Optional[str]
    fault_id: Optional[str]

    def view(self) -> dict[str, Any]:
        return {"command": _redact(self.command), "run_id": self.run_id, "procedure_action_id": self.procedure_action_id,
                "source_id": self.source_id, "fault_id": self.fault_id}


def current_turn_command_authorizations(
    run_id: Optional[str],
    execution_record: Optional[Mapping[str, Any]],
    *,
    active_fault_id: Optional[str] = None,
) -> tuple[list[CommandAuthorization], list[dict[str, Any]]]:
    """(valid authorizations, rejected ones with the reason). Valid only when the record is THIS
    run's, its `diagnostic_step.command` is present, Command Authority authorized that exact command
    and source in this run (approved catalog), and it belongs to the fault now in focus."""
    if not run_id or not isinstance(execution_record, Mapping):
        return [], []
    step = execution_record.get("diagnostic_step")
    command = str(step.get("command") or "").strip() if isinstance(step, Mapping) else ""
    if not command:
        return [], []
    source = str(step.get("command_source") or "").strip() or None
    resolution = execution_record.get("procedure_action_resolution")
    action_id = (
        str(step.get("procedure_action_id") or "").strip()
        or (str(resolution.get("action_id") or "").strip() if isinstance(resolution, Mapping) else "")
        or None
    )
    authorization = CommandAuthorization(
        command=command, normalized=normalize_command(command), run_id=run_id, procedure_action_id=action_id,
        source_id=source, fault_id=execution_record.get("fault_id"),
    )
    record_run = execution_record.get("run_id")
    if record_run and record_run != run_id:
        return [], [{**authorization.view(), "reason": "different_run"}]
    authorized_in_catalog = any(
        isinstance(item, Mapping)
        and normalize_command(str(item.get("command") or "")) == authorization.normalized
        and (source is None or item.get("source_id") == source)
        and str(item.get("authorization_decision") or "authorized") == "authorized"
        for item in execution_record.get("approved_commands_catalog") or []
    )
    if not authorized_in_catalog:
        return [], [{**authorization.view(), "reason": "not_authorized_by_command_authority_this_run"}]
    if active_fault_id and authorization.fault_id and authorization.fault_id != active_fault_id:
        return [], [{**authorization.view(), "reason": "different_fault"}]
    return [authorization], []


# ---------------------------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------------------------


def governed_command_strings(texts: Iterable[str]) -> list[str]:
    """Command strings the governed-procedure extractor itself recognises in this turn's selected
    evidence (including ones it could not classify): a verbatim copy of governed command text into
    prose is still command egress, however it is framed."""
    from backend.agents.technical_authority_engineer.procedure_actions import _candidates

    found: list[str] = []
    for text in texts:
        for template, _method, _description in _candidates(str(text or "")):
            if is_command_like(template, code=True) and template not in found:
                found.append(template)
    return found


def _corpus_pattern(command: str) -> re.Pattern[str]:
    from backend.agents.technical_authority_engineer.procedure_actions import template_placeholders

    template = _clean(command)
    parts, cursor = [], 0
    for _, start, end in template_placeholders(template):
        parts.append(r"\s+".join(re.escape(p) for p in template[cursor:start].split(" ")))
        parts.append(r"\S+")
        cursor = end
    parts.append(r"\s+".join(re.escape(p) for p in template[cursor:].split(" ")))
    return re.compile(rf"(?<![\w\-=]){''.join(parts)}(?![\w\-=])", re.IGNORECASE)


def find_command_candidates(text: str, corpus: Iterable[str] = ()) -> list[str]:
    """Every operational command-like span in `text` (original spelling, de-duplicated)."""
    found: list[str] = []

    def _add(span: str) -> None:
        value = _clean(span)
        if value and value not in found:
            found.append(value)

    for block in _FENCED.finditer(text or ""):
        for line in block.group(1).splitlines():
            prompt = _PROMPT_LINE.match(line)
            candidate = prompt.group(1) if prompt else line
            if is_command_like(candidate, code=True):
                _add(candidate)
    prose = _FENCED.sub(" ", text or "")
    for match in _INLINE.finditer(prose):
        if is_command_like(match.group(1), code=True):
            _add(match.group(1))
    for raw in prose.splitlines():
        prompt = _PROMPT_LINE.match(raw)
        if prompt and is_command_like(prompt.group(1), code=True):
            _add(prompt.group(1))
            continue
        line = _clean(_LIST_PREFIX.sub("", raw))
        _, _, has_argument_syntax, _, _ = _command_parts()
        if has_argument_syntax(line) and is_command_like(line):
            _add(line)
    without_code = _INLINE.sub(" ", prose)
    for match in _INTRO.finditer(without_code):
        rest = without_code[match.end():]
        end = _SPAN_END.search(rest)
        span = rest[: end.start()] if end else rest
        if is_command_like(span):
            _add(span)
    for command in corpus:
        for match in _corpus_pattern(command).finditer(text or ""):
            _add(match.group(0))
    return found


# ---------------------------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------------------------


def _redact(text: str) -> str:
    return _SECRET_VALUE.sub(lambda m: f"{m.group(1)}=<redacted>", str(text or ""))[:_MAX_CANDIDATE_CHARS]


def _remove_authorized(segment: str, authorizations: list[CommandAuthorization]) -> str:
    result = segment
    for auth in sorted(authorizations, key=lambda a: len(a.command), reverse=True):
        body = r"\s+".join(re.escape(p) for p in _clean(auth.command).split())
        result = re.sub(rf"`?(?<![\w\-=]){body}(?![\w\-=])`?", " ", result, flags=re.IGNORECASE)
    return result


def _contains_authorized(segment: str, authorizations: list[CommandAuthorization]) -> list[CommandAuthorization]:
    return [a for a in authorizations if _remove_authorized(segment, [a]) != segment]


@dataclass
class EgressResult:
    text: str
    producer: str
    run_id: Optional[str]
    decisions: list[dict[str, Any]] = field(default_factory=list)
    authorizations: list[dict[str, Any]] = field(default_factory=list)
    rejected_authorizations: list[dict[str, Any]] = field(default_factory=list)
    removed_units: int = 0
    fallback: bool = False

    @property
    def blocked(self) -> bool:
        return self.removed_units > 0 or self.fallback

    def view(self) -> dict[str, Any]:
        return {
            "stage": "command_egress",
            "producer": self.producer,
            "run_id": self.run_id,
            "decision": "fallback" if self.fallback else ("sanitized" if self.removed_units else "unchanged"),
            "removed_units": self.removed_units,
            "candidates": list(self.decisions),
            "authorizations": list(self.authorizations),
            "rejected_authorizations": list(self.rejected_authorizations),
        }


def enforce_command_egress(
    text: Optional[str],
    *,
    authorizations: Iterable[CommandAuthorization] = (),
    producer: str,
    run_id: Optional[str],
    corpus: Iterable[str] = (),
    rejected_authorizations: Iterable[Mapping[str, Any]] = (),
) -> EgressResult:
    """Remove every sentence / list item / code line carrying an operational command that is not a
    valid current-turn authorization (a clause holding an authorized command keeps that clause).
    When anything was removed and no authorized command remains, the fixed server sentence is
    appended; when nothing remains, it is the whole answer."""
    auths = list(authorizations)
    corpus = [c for c in corpus if c]
    result = EgressResult(
        text=text or "", producer=producer, run_id=run_id,
        authorizations=[a.view() for a in auths], rejected_authorizations=[dict(r) for r in rejected_authorizations],
    )
    if not text or not text.strip():
        return result

    def _decide(segment: str) -> tuple[bool, list[str]]:
        residual = _remove_authorized(segment, auths)
        unauthorized = find_command_candidates(residual, corpus)
        for auth in _contains_authorized(segment, auths):
            entry = {"candidate": _redact(auth.command), "authorized": True, "procedure_action_id": auth.procedure_action_id,
                     "authorization_run_id": auth.run_id, "decision": "kept", "reason": "current_turn_authorized"}
            if entry not in result.decisions:
                result.decisions.append(entry)
        for candidate in unauthorized:
            entry = {"candidate": _redact(candidate), "authorized": False, "procedure_action_id": None,
                     "authorization_run_id": None, "decision": "removed", "reason": "no_current_turn_authorization"}
            if entry not in result.decisions:
                result.decisions.append(entry)
        return (not unauthorized), unauthorized

    def _sanitize_unit(unit: str) -> Optional[str]:
        ok, _ = _decide(unit)
        if ok:
            return unit
        if not _contains_authorized(unit, auths):
            result.removed_units += 1
            return None
        kept = [part.strip() for part in _CLAUSE_SPLIT.split(unit) if part and part.strip() and _decide(part)[0]]
        result.removed_units += 1
        if not kept or not any(_contains_authorized(part, auths) for part in kept):
            return None
        return ", ".join(kept).rstrip(".,;:") + "."

    def _sanitize_block(match: re.Match[str]) -> str:
        header = match.group(0).split("\n", 1)[0]
        kept = [line for line in match.group(1).splitlines() if line.strip() and _sanitize_unit(line) is not None]
        return f"{header}\n" + "\n".join(kept) + "\n```" if kept else ""

    sanitized = _FENCED.sub(_sanitize_block, text)
    lines_out: list[str] = []
    for line in sanitized.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("```"):
            lines_out.append(line)
            continue
        prefix_match = _LIST_PREFIX.match(line)
        if prefix_match:
            item = _sanitize_unit(line[prefix_match.end():])
            if item is not None:
                lines_out.append(line[: prefix_match.end()] + item)
            continue
        if _PROMPT_LINE.match(line) or "```" in line:
            if _sanitize_unit(line) is not None:
                lines_out.append(line)
            continue
        sentences = [s for s in (_sanitize_unit(s) for s in _SENTENCE_SPLIT.split(stripped)) if s]
        if sentences:
            indent = line[: len(line) - len(line.lstrip())]
            lines_out.append(indent + " ".join(sentences))
    out = "\n".join(lines_out)

    if result.removed_units:
        from backend.agents.technical_authority_engineer.synthesis_boundary import _DANGLING_COMMAND_REFERENCE

        authorized_left = bool(_contains_authorized(out, auths))
        if not authorized_left:
            # Nothing presentable remains: references to "this command" / "run it" and bare
            # command labels point at nothing.
            kept_lines = []
            for line in out.splitlines():
                if _ORPHAN_LABEL.match(line.strip()):
                    continue
                parts = [s for s in _SENTENCE_SPLIT.split(line.strip()) if s and not _DANGLING_COMMAND_REFERENCE.search(s)]
                if line.strip() and not parts:
                    continue
                kept_lines.append(line if not line.strip() else line[: len(line) - len(line.lstrip())] + " ".join(parts))
            out = "\n".join(kept_lines)
        out = re.sub(r"\n{3,}", "\n\n", out).strip()
        if not out:
            out = NO_AUTHORIZED_COMMAND_TEXT
            result.fallback = True
        elif not authorized_left and NO_AUTHORIZED_COMMAND_TEXT not in out:
            out = f"{out}\n\n{NO_AUTHORIZED_COMMAND_TEXT}"
    result.text = out if result.removed_units else text
    return result


def log_egress(result: EgressResult) -> None:
    """One structured record per final answer that carried command candidates (identifiers and
    redacted candidates only -- never message bodies or secrets)."""
    if not result.decisions and not result.rejected_authorizations:
        return
    view = result.view()
    level = logging.WARNING if result.blocked else logging.INFO
    _logger.log(
        level,
        "command_egress run_id=%s producer=%s decision=%s removed_units=%s candidates=%s rejected_authorizations=%s",
        result.run_id, result.producer, view["decision"], result.removed_units,
        [(d["candidate"], d["decision"], d["reason"], d["procedure_action_id"]) for d in result.decisions],
        [(r.get("command"), r.get("reason")) for r in result.rejected_authorizations],
    )


# ---------------------------------------------------------------------------------------------
# Live streaming gate (explicit non-governed turns stream Team Manager prose live)
# ---------------------------------------------------------------------------------------------


_TAIL_HOLD = re.compile(r"\b(?:commands?|cmds?|syntax|run|execute|issue|enter|type|use|example|e\.g)\b", re.IGNORECASE)
_TERMINATOR = re.compile(r"[.!?](?:\s|$)|\n")


class StreamingEgressGate:
    """Streams only text that already passed command detection. A trailing partial sentence that
    could still be completing a command (an open code span, or command wording) is held back; once
    any command candidate appears, live streaming stops for the rest of the turn -- the final
    `message.completed` (sanitized by `enforce_command_egress`) is authoritative and replaces it."""

    def __init__(self) -> None:
        self.emitted = ""
        self.pending = ""
        self.raw = ""
        """Everything offered, emitted or not (for the existing final-text recovery path, which is
        itself sanitized by `enforce_command_egress`)."""
        self.stopped = False

    def accept(self, delta: str) -> Optional[str]:
        self.raw += delta or ""
        if self.stopped:
            return None
        self.pending += delta or ""
        releasable, tail = self.pending, ""
        if releasable.count("`") % 2 == 1 or _TAIL_HOLD.search(self._last_partial(releasable)) or releasable.rstrip().endswith("```"):
            cut = self._last_terminator(releasable)
            releasable, tail = releasable[:cut], releasable[cut:]
            if releasable.count("`") % 2 == 1 or _FENCED_OPEN.search(releasable):
                releasable, tail = "", self.pending
        if not releasable:
            return None
        if find_command_candidates(self.emitted + releasable):
            self.stopped = True
            self.pending = ""
            return None
        self.emitted += releasable
        self.pending = tail
        return releasable

    @staticmethod
    def _last_terminator(text: str) -> int:
        cut = 0
        for match in _TERMINATOR.finditer(text):
            cut = match.end()
        return cut

    @classmethod
    def _last_partial(cls, text: str) -> str:
        return text[cls._last_terminator(text):]


_FENCED_OPEN = re.compile(r"```(?:(?!```).)*$", re.DOTALL)

