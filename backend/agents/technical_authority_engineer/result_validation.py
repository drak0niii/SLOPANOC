"""Trusted result validation (deterministic; no model call).

    operator message -> candidate observation -> RESULT VALIDATION -> trusted result -> completion

An operator's text is trusted as what the operator SAID, not automatically as evidence that
satisfies the pending diagnostic check. A candidate becomes a trusted result only when its
structure is compatible with what the server knows about the pending step (its command or rendered
ProcedureAction, target, objective / expected evidence, purpose):

    NOT_A_RESULT    questions ("is Unit=4 the one you mean?", "should I run st ru?") and prose with
                    no output structure
    VALIDATED       - the pending command is echoed (optionally after a prompt: "$ st ru",
                      "NODE> st ru", "st ru output:") and output content follows it; or the echo
                      follows a conversational lead-in on the same line ("this is the output:
                      $ st ru", "output of st ru:") AND output-structured lines follow it; or
                    - output-structured lines (key=value, status tokens, columns) that are
                      compatible with the step: its target is shown, or >= 2 distinctive words of the
                      step's objective / expected evidence / command appear in the output; or
                    - a remediation step: the operator reports the action executed, or echoes it; or
                    - a command-less (manual) step: a statement overlapping its objective
    COMMAND_FAILED  an explicit command error (unknown command, syntax error, permission denied...)
                    for the pending step
    FOREIGN         the output belongs to ANOTHER known step's command (any fault thread)
    AMBIGUOUS       output-like but not attributable to the pending step -> fail closed: the step
                    stays pending and its output is requested

Controlled execution-adapter output is bound only through structured metadata: a SUCCEEDED
execution recorded by the control plane for EXACTLY this check, whose output hash matches.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from backend.agents.technical_authority_engineer.step_identity import normalize_command, target_values
from backend.cases.troubleshooting_progression import StepPurpose, TroubleshootingStep


class ResultValidationStatus(str, Enum):
    VALIDATED = "validated"
    COMMAND_FAILED = "command_failed"
    AMBIGUOUS = "ambiguous"
    FOREIGN = "foreign"
    NOT_A_RESULT = "not_a_result"


BINDABLE = frozenset({ResultValidationStatus.VALIDATED, ResultValidationStatus.COMMAND_FAILED})
UNVALIDATED_CANDIDATES = frozenset({ResultValidationStatus.AMBIGUOUS, ResultValidationStatus.FOREIGN})


@dataclass
class ResultValidation:
    status: ResultValidationStatus
    reasons: list[str] = field(default_factory=list)
    signals: dict[str, Any] = field(default_factory=dict)

    @property
    def bindable(self) -> bool:
        return self.status in BINDABLE

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status.value, "reasons": list(self.reasons), "signals": dict(self.signals)}


COMMAND_FAILURE = re.compile(
    r"\b(?:unknown command|command not found|syntax error|invalid (?:command|syntax|argument)|permission denied)\b", re.IGNORECASE
)
EXECUTION_CONFIRMED = re.compile(r"\b(?:done|executed|performed|completed|finished|applied|ran it|did it)\b", re.IGNORECASE)
_QUESTION_START = re.compile(
    r"^\s*(?:is|are|was|were|what|what's|whats|which|who|why|how|where|when|should|shall|can|could|would|will|do|does|did|may|might|must)\b",
    re.IGNORECASE,
)
_PROMPT = re.compile(r"^\s*(?:[\w.\-@:]*[>$#%]\s*)")
_REPORT_PREFIX = re.compile(r"^\s*(?:i\s+(?:have\s+)?)?(?:ran|run|executed|tried|typed|entered)\s+", re.IGNORECASE)
_KEY_VALUE = re.compile(r"[A-Za-z][\w.\-]*\s*=\s*\S")
_WORDS = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
_GENERIC = frozenset({
    "the", "and", "for", "with", "check", "checks", "state", "status", "output", "result", "results", "command", "commands",
    "verify", "verification", "show", "shows", "run", "value", "values", "current", "confirm", "whether", "this", "that",
    "after", "before", "report", "reports", "observed", "expected", "perform", "governed", "step", "procedure",
})


_INSTRUCTION_REQUEST = re.compile(
    r"\b(?:what|which|how)\b[^.?!\n]{0,80}?\b(?:command|commands|cmd|run|type|enter|execute|check|get|obtain|retrieve|find|see|use|do)\b"
    r"|\b(?:tell|show|teach)\s+me\s+(?:how|what|which)\b"
    r"|\bhow\s+(?:do|can|should|would|could|to)\b"
    r"|\b(?:can|could|would)\s+you\s+(?:tell|show|give|explain|send)\b"
    r"|\bi\s+(?:need|want)\s+(?:to\s+know\s+)?(?:the|a|which|what)\s+(?:command|cmd)\b",
    re.IGNORECASE,
)
"""The operator asks HOW to obtain the evidence ("right, but what command do I run", "so how do I
check that", "tell me how to get that"): a conversational request, never an observation."""


def is_instruction_request(text: str) -> bool:
    """True when the message's opening asks how / with which command to obtain something."""
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return False
    opening = re.split(r"(?<=[.?!])\s+", lines[0], maxsplit=1)[0]
    return bool(_INSTRUCTION_REQUEST.search(opening)) and not _output_like(opening)


def _is_question(line: str) -> bool:
    text = line.strip()
    return text.endswith("?") or bool(_QUESTION_START.match(text) and not _output_like(text))


def _structured_token(token: str) -> bool:
    bare = token.strip(",.;:!")
    return bool(
        "=" in bare or re.search(r"\d", bare) or re.fullmatch(r"[A-Z][A-Z0-9_\-]+", bare)
        or re.search(r"[a-z][A-Z]", bare) or re.search(r"[()|\[\]_]", bare)
    )


def _output_like(line: str) -> bool:
    tokens = line.split()
    if not tokens:
        return False
    structured = sum(1 for t in tokens if _structured_token(t))
    return structured >= 1 and structured / len(tokens) >= 0.5


def _words(text: str) -> set[str]:
    return {w.casefold() for w in _WORDS.findall(text or "") if len(w) >= 3 and w.casefold() not in _GENERIC}


def _related(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def _overlap(output_words: set[str], descriptor: set[str]) -> list[str]:
    return sorted({d for d in descriptor if any(_related(d, w) for w in output_words)})


_ECHO_REST = re.compile(r"^\s*(?:[:\-=>|,`'\"]|\s|output|results?|returned|returns|shows|gives|got|cmd|command)*", re.IGNORECASE)
_LEAD_IN_PROMPT = re.compile(r"(?:^|[\s:;,(])[\w.\-@:]*[>$#%]\s*$")
"""A shell / node prompt immediately before the command, after a lead-in ("this is the output: $ ")."""
_LEAD_IN_ANNOUNCED = re.compile(
    r"\b(?:output|outputs|result|results|response|printout)\s+(?:of|for|from)\s+(?:the\s+|running\s+)?(?:(?:command|cmd)\s+)?$",
    re.IGNORECASE,
)
"""An output announcement naming the command next ("the output of st ru", "result from the command st ru")."""
_ANNOUNCED_AFTER = re.compile(r"\s*(?:(?:cmd|command)\s+)?(?:output|outputs|result|results|returned|returns|shows|gives)\b", re.IGNORECASE)
"""The command named as the subject of the output that follows ("here is the st ru output:")."""


@dataclass(frozen=True)
class _Echo:
    rest: str
    """Remainder (original case) of the line after the echoed command."""
    introduced: bool
    """The echo follows a conversational lead-in on the same line ("this is the output: $ st ru")
    rather than opening it: it counts only when output-structured lines follow it."""


def _find_echo(line: str, command: Optional[str]) -> Optional[_Echo]:
    """The echo of `command` in `line`: at its start ("$ st ru", "NODE> st ru", "st ru output: ...",
    "I ran st ru: ...") or after a lead-in that introduces it as executed / as the output's subject
    (a prompt right before it, "the output of st ru", "the st ru output"). None otherwise."""
    if not command:
        return None
    body = re.sub(r"\s+", " ", _REPORT_PREFIX.sub("", _PROMPT.sub("", line.replace("`", ""), count=1), count=1).strip())
    if body.casefold().startswith(command):
        rest = body[len(command):]
        if rest and rest[0].isalnum():
            return None  # "st ru" must not match "st rufoo"
        return _Echo(_ECHO_REST.sub("", rest).strip(), introduced=False)
    for match in re.finditer(rf"(?<![\w-]){re.escape(command)}(?![\w-])", body.casefold()):
        before, after = body[: match.start()], body[match.end():]
        if _LEAD_IN_PROMPT.search(before) or _LEAD_IN_ANNOUNCED.search(before) or _ANNOUNCED_AFTER.match(after):
            return _Echo(_ECHO_REST.sub("", after).strip(), introduced=True)
    return None


def _echo_remainder(line: str, command: Optional[str]) -> Optional[str]:
    """Remainder (original case) of `line` after an echo of `command`, or None when it does not echo it."""
    echo = _find_echo(line, command)
    return echo.rest if echo is not None else None


def _echo_with_output(lines: list[str], index: int, command: Optional[str]) -> Optional[_Echo]:
    """The echo of `command` on `lines[index]`; an introduced echo counts only when output follows it
    (structured tokens inline, or output-structured lines after it) -- a lead-in that merely names the
    command ("I don't have the output of st ru") is not an echo."""
    echo = _find_echo(lines[index], command)
    if echo is None or not echo.introduced:
        return echo
    inline = any(_structured_token(t) for t in echo.rest.split())
    return echo if inline or any(_output_like(line) for line in lines[index + 1:]) else None


def _target_shown(text: str, target: str) -> bool:
    folded = (text or "").casefold()
    for pair in (target or "").split(";"):
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        if re.search(rf"(?<![\w]){re.escape(key)}\s*=\s*{re.escape(value)}(?![\w])", folded):
            return True
        if not value.isdigit() and len(value) >= 3 and re.search(rf"(?<![\w-]){re.escape(value)}(?![\w-])", folded):
            return True
    return False


def operator_intent(text: str, commands: Iterable[Optional[str]] = (), carries_output: bool = False) -> str:
    """The operator's OWN words (requests, intent) -- never pasted operational output, which is
    observation. Structural only (no vocabulary): in a multi-line message everything from the first
    echoed known command that output follows ("st ru output:" + its table, "this is the output: $ st
    ru" ...) is output; output-structured lines are output when the server classified the message as
    carrying output (`carries_output`: a bound result, an unattributed candidate, a re-submitted
    result) or when a multi-line message holds a block of them (>= 2). A single typed line that is not
    classified as output ("restart Unit=4", "Unit=4") stays the operator's own words."""
    lines = (text or "").splitlines()
    multi_line = sum(1 for line in lines if line.strip()) > 1
    if multi_line:
        wanted = [c for c in (normalize_command(c) for c in commands) if c]
        for index, line in enumerate(lines):
            if any(_find_echo(line, command) is not None for command in wanted) and any(l.strip() for l in lines[index + 1:]):
                lines = lines[:index]
                break
    if carries_output or (multi_line and sum(1 for line in lines if _output_like(line)) >= 2):
        lines = [line for line in lines if not _output_like(line)]
    return "\n".join(lines).strip()


def _observation_lines(text: str) -> list[str]:
    return [" ".join(line.split()).casefold() for line in (text or "").splitlines() if _output_like(line)]


def same_observation(text: str, recorded: Optional[str]) -> bool:
    """`text` re-submits the already-recorded observation `recorded`: the same message, or the same
    output-structured lines (at least two, all present in the recorded observation, in order)."""
    if not text or not recorded:
        return False
    if " ".join(text.split()).casefold() == " ".join(recorded.split()).casefold():
        return True
    lines, known = _observation_lines(text), _observation_lines(recorded)
    if len(lines) < 2 or len(lines) > len(known):
        return False
    position = 0
    for line in lines:
        try:
            position = known.index(line, position) + 1
        except ValueError:
            return False
    return True


def expected_command(step: TroubleshootingStep) -> Optional[str]:
    """The command whose output completes this step: the presented command, or (for a state change
    never shown in-turn) the rendered ProcedureAction."""
    return normalize_command(step.command) or (step.identity.result_key if step.identity else None)


def validate_operator_observation(step: TroubleshootingStep, text: str, foreign_commands: Iterable[str] = ()) -> ResultValidation:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return ResultValidation(ResultValidationStatus.NOT_A_RESULT, ["empty message"])
    body = [line for line in lines if not _is_question(line)]
    if not body:
        return ResultValidation(ResultValidationStatus.NOT_A_RESULT, ["the message is a question, not an observation"])

    command = expected_command(step)
    if is_instruction_request(text) and not any(_echo_with_output(lines, i, command) is not None for i in range(len(lines))):
        # A request for HOW to obtain the result (even phrased as a statement and followed by the
        # list of what is needed) is conversation, not the result -- unless the pending command is
        # actually echoed with its output.
        return ResultValidation(ResultValidationStatus.NOT_A_RESULT, ["the message asks how to obtain the result; it is not an observation"])
    foreign = sorted({c for c in (normalize_command(f) for f in foreign_commands) if c and c != command}, key=len, reverse=True)
    echo_index, echo_rest = None, None
    for index in range(len(body)):
        echo = _echo_with_output(body, index, command)
        if echo is not None:
            echo_index, echo_rest = index, echo.rest
            break
        other = next((f for f in foreign if _echo_with_output(body, index, f) is not None), None)
        if other is not None:
            return ResultValidation(ResultValidationStatus.FOREIGN, [f"the output is for `{other}`, not for the pending step"], {"foreign_command": other})

    output_lines = body[echo_index + 1:] if echo_index is not None else body
    content = ([echo_rest] if echo_rest else []) + output_lines
    signals: dict[str, Any] = {"echo": echo_index is not None, "output_lines": sum(1 for line in content if _output_like(line))}

    if any(COMMAND_FAILURE.search(line) for line in content) and (echo_index is not None or len(body) == len(lines)):
        return ResultValidation(ResultValidationStatus.COMMAND_FAILED, ["the command reported an error"], signals)

    if step.purpose is StepPurpose.REMEDIATION:
        if echo_index is not None or any(EXECUTION_CONFIRMED.search(line) for line in body):
            return ResultValidation(ResultValidationStatus.VALIDATED, ["the operator reports the action as executed"], signals)
        return ResultValidation(ResultValidationStatus.NOT_A_RESULT, ["no execution report for the remediation action"], signals)

    if echo_index is not None:
        inline = bool(echo_rest) and any(_structured_token(t) for t in echo_rest.split())
        if inline or any(line.strip() for line in output_lines):
            return ResultValidation(ResultValidationStatus.VALIDATED, ["the pending command is echoed with its output"], signals)
        return ResultValidation(ResultValidationStatus.AMBIGUOUS, ["the command is mentioned without its output"], signals)

    descriptor = _words(" ".join(filter(None, [step.objective, step.expected_evidence, step.command, step.identity.template if step.identity else None])))
    output_text = "\n".join(line for line in content if _output_like(line))
    target = step.identity.target if step.identity else ""
    if not command:  # a manual (command-less) observation: the operator's statement about its objective
        overlap = _overlap(_words(" ".join(content)), descriptor)
        signals["overlap"] = overlap
        if overlap:
            return ResultValidation(ResultValidationStatus.VALIDATED, ["statement about the manual check's objective"], signals)
        return ResultValidation(ResultValidationStatus.NOT_A_RESULT, ["the statement does not concern the pending check"], signals)
    if not output_text:
        return ResultValidation(ResultValidationStatus.NOT_A_RESULT, ["no command output in the message"], signals)
    overlap = _overlap(_words(output_text), descriptor)
    signals.update(overlap=overlap, target_shown=_target_shown(output_text, target))
    if signals["target_shown"] or len(overlap) >= 2:
        return ResultValidation(ResultValidationStatus.VALIDATED, ["output structure compatible with the pending step"], signals)
    return ResultValidation(
        ResultValidationStatus.AMBIGUOUS, [f"the output cannot be attributed to the pending step; paste the output of `{step.command or 'the check'}`"], signals
    )


def validate_adapter_observation(
    step: TroubleshootingStep, execution_id: Optional[str], observed: Optional[str], state: Mapping[str, Any]
) -> ResultValidation:
    """Controlled execution output binds only to the exact check its SUCCEEDED execution was recorded for."""
    from backend.operations.signing import text_hash

    if not step.check_id:
        return ResultValidation(ResultValidationStatus.FOREIGN, ["the step has no check to bind an execution to"])
    if not execution_id or not observed:
        return ResultValidation(ResultValidationStatus.AMBIGUOUS, ["no execution output recorded"])
    for record in ((state or {}).get("operational_action_controls") or {}).values():
        context = (record or {}).get("context") or {}
        if context.get("check_id") != step.check_id:
            continue
        for execution in record.get("executions") or []:
            if execution.get("execution_id") != execution_id:
                continue
            if execution.get("check_id") not in (None, step.check_id):
                return ResultValidation(ResultValidationStatus.FOREIGN, ["execution belongs to another check"])
            if execution.get("status") != "succeeded":
                return ResultValidation(ResultValidationStatus.AMBIGUOUS, ["the controlled execution did not succeed"])
            if execution.get("output_hash") and execution["output_hash"] != text_hash(observed):
                return ResultValidation(ResultValidationStatus.AMBIGUOUS, ["recorded output does not match the executed output"])
            return ResultValidation(ResultValidationStatus.VALIDATED, ["controlled execution bound to this check"], {"execution_id": execution_id})
    return ResultValidation(ResultValidationStatus.AMBIGUOUS, ["no controlled execution of this check is on record"], {"execution_id": execution_id})
