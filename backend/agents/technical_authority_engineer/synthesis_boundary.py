"""Deterministic post-synthesis validation boundary for Technical Authority Engineer (Step 3).

Enforces that Team Manager synthesis does not introduce operational instructions,
mutating operations (restart/reboot/reset/reload), configuration changes,
additional troubleshooting steps, or unauthorized commands beyond the
server-validated Technical Authority Engineer output.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from backend.agents.technical_authority_engineer.validation import is_command_authorized

logger = logging.getLogger(__name__)

SAFE_SYNTHESIS_FALLBACK = (
    "No operational commands are authorized for this fault based on current validated evidence."
)

GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT = (
    "The governed procedure is available, but no command for this diagnostic step has passed the "
    "current grounding and authorization checks."
)

_MUTATING_PATTERNS = [
    re.compile(
        r"\b(?:(?:run|execute|issue|apply|perform|trigger|initiate|launch|do)\s+)?"
        r"(?:\b(?:please|kindly|you\s+(?:must|should|can|need\s+to|have\s+to))\s+)?"
        r"\b(?:restart|reboot|reset|reload|poweroff|power\s+off|shutdown|kill|bounce|halt|format|restarting|rebooting|resetting|reloading)\b"
        r"(?:\s+(?:the|a|an|all))?(?:\s+(?!(?:to|for|on|and|or|in|at|with|then|next|if|before|after)\b)[A-Za-z0-9_\-=\.:/]+){1,3}",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:perform|trigger|execute|initiate|do)\s+(?:a\s+|an\s+)?(?:restart|reboot|reset|reload)\b(?:\s+(?:of\s+)?(?:the\s+)?[A-Za-z0-9_\-=\.:/]+)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bacc\s+[A-Za-z0-9_\-=\.:/]+\s+(?:manualrestart|cvrestart|restart|reset|lock|unlock|stop|start)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:manualrestart|cvrestart)\b", re.IGNORECASE),
]

_CONFIG_PATTERNS = [
    re.compile(
        r"\b(?:set|cr|del)\s+[A-Za-z0-9_\-=\.:/]+\s+[A-Za-z0-9_\-=\.:/]+",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bacc\s+[A-Za-z0-9_\-=\.:/]+\s+(?:set|cr|del|modify|configure)\b",
        re.IGNORECASE,
    ),
]

_CLI_PATTERNS = [
    re.compile(
        r"\bst\s+(?!(?:to|for|on|and|or|in|at|with|then|next|if|before|after)\b)[A-Za-z0-9_\-=\.:/]+",
        re.IGNORECASE,
    ),
    re.compile(r"\balt(?:\s+-[A-Za-z0-9]+|\s+cm)?\b", re.IGNORECASE),
    re.compile(r"\bacc\s+[A-Za-z0-9_\-=\.:/]+(?:\s+[A-Za-z0-9_\-=\.:/]+){1,3}\b", re.IGNORECASE),
    re.compile(r"\b(?:bl|deb|cvls|inv|lh|cab|pr|lg)\s+[A-Za-z0-9_\-=\.:/]+\b", re.IGNORECASE),
    re.compile(r"\b(?:ping|traceroute)\s+[A-Za-z0-9_\-=\.:/]+\b", re.IGNORECASE),
    re.compile(r"\bsystemctl\s+(?:restart|reload|stop|start|status)\s+[A-Za-z0-9_\-=\.:/]+\b", re.IGNORECASE),
]

_DANGLING_COMMAND_REFERENCE = re.compile(
    r"\b(?:this|that|these|those|the\s+above|the\s+following|the\s+same|said)\s+commands?\b"
    r"|\b(?:output|result|results)\s+of\s+(?:the|this|that)\s+commands?\b"
    r"|\b(?:the|this|that)\s+command(?:'s)?\s+(?:output|result)s?\b"
    r"|\b(?:run|execute|issue|enter|type)\s+(?:the|this|that)\s+commands?\b"
    r"|\b(?:run|execute)\s+(?:it|them)\b",
    re.IGNORECASE,
)
"""A sentence that refers to a specific command ("provide the output of this command", "run it").
Kept only when the authoritative diagnostic step still carries an authorized command."""


_MECHANISM = r"(?:commands?|cmds?|procedures?|runbooks?|mops?|sops?|tools?|scripts?|methods?)"
_POLITE_REQUEST = re.compile(
    r"(?:\b(?:please|kindly)\b|\b(?:could|can|would|will)\s+you\b|\blet\s+me\s+know\b|^\s*(?:provide|share|send|give|tell\s+me|specify))"
    rf"[^.?!\n]{{0,80}}?\b{_MECHANISM}\b",
    re.IGNORECASE,
)
_ASK_WHICH = re.compile(rf"\b(?:which|what)\s+{_MECHANISM}\s+(?:do|would|should|can|could|did)\s+you\b", re.IGNORECASE)
_YOU_USE = re.compile(rf"\b{_MECHANISM}\s+(?:that\s+)?you\s+(?:normally|typically|usually|would|use)\b", re.IGNORECASE)
_OUTPUT_OF = re.compile(r"\b(?:output|outputs|result|results)\s+(?:of|from)\b", re.IGNORECASE)
_EXECUTE = re.compile(r"\b(?:run|execute|issue|enter)\b", re.IGNORECASE)
"""An acquisition method (command, procedure, tool) is the SYSTEM's to establish from governed
authority -- the operator is never asked to supply one ("could you please provide the command ...",
"which command do you normally use?"). Asking the operator to RUN a presented command, or for the
OUTPUT of one, is a different request and is kept; so is the system's own statement about commands."""


def _without_presented_commands(sentence: str, authorized_commands: set[str]) -> str:
    """`sentence` minus every exact presentation of a CURRENT-TURN authorized command: the command
    text (code-formatted or token-bounded) together with a mechanism noun naming it directly ("the
    command `X`", "`X` command"). Presenting the authorized command is not asking the operator to
    supply one; any other mechanism wording left in the sentence is still judged."""
    result = sentence
    for cmd in sorted((c for c in authorized_commands if c), key=len, reverse=True):
        text = rf"(?:`{re.escape(cmd)}`|\*\*{re.escape(cmd)}\*\*|(?<![A-Za-z0-9_\-]){re.escape(cmd)}(?![A-Za-z0-9_\-]))"
        result = re.sub(
            rf"(?:\b(?:the\s+)?{_MECHANISM}\s*:?\s*)?{text}(?:\s+{_MECHANISM}\b)?", " ", result, flags=re.IGNORECASE
        )
    return result


def _asks_operator_for_mechanism(sentence: str, authorized_commands: Optional[set[str]] = None) -> bool:
    if _OUTPUT_OF.search(sentence):
        return False
    sentence = _without_presented_commands(sentence, authorized_commands or set())
    if _ASK_WHICH.search(sentence) or _YOU_USE.search(sentence):
        return True
    return bool(_POLITE_REQUEST.search(sentence)) and not _EXECUTE.search(sentence)


_CLAUSE_SPLIT_REGEX = re.compile(
    r"(,\s*(?:and\s+then|then|also|next|afterwards|followed\s+by)\s+)"
    r"|(\s+(?:and\s+then|then|also|next|afterwards|followed\s+by)\s+)"
    r"|(,\s*and\s+)"
    r"|(;\s*)"
    r"|(,\s*)"
    r"|(\s+and\s+)",
    re.IGNORECASE,
)


def _extract_authorized_commands(
    technical_authority_execution: dict[str, Any],
) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Extracts server-validated authorized commands, catalog, and verified evidence."""
    authorized_commands: set[str] = set()
    catalog = technical_authority_execution.get("approved_commands_catalog") or []
    verified_evidence = technical_authority_execution.get("verified_evidence") or []

    # 1. Authoritative command from diagnostic_step only for this turn
    step = technical_authority_execution.get("diagnostic_step")
    if isinstance(step, dict):
        cmd = step.get("command")
        if cmd and isinstance(cmd, str) and cmd.strip():
            clean = cmd.strip()
            clean_stripped = re.sub(r"\*\*|`", "", clean).strip()
            authorized_commands.add(clean.lower())
            authorized_commands.add(clean_stripped.lower())

    # Note: approved_commands_catalog records Step 2 case-level authorization,
    # but final-response operational authority for this turn is strictly limited
    # to diagnostic_step.command. The catalog must NOT expand the final response allow-list.
    return authorized_commands, catalog, verified_evidence


_INFLECTION_SUFFIXES = re.compile(r"(?:ing|ed|es|s)$")


def _state_changing_types() -> tuple[Any, ...]:
    from backend.agents.technical_authority_engineer.schemas import CommandOperationType

    return (CommandOperationType.MUTATING_OPERATIONAL, CommandOperationType.CONFIGURATION_CHANGE)


def _introduces_state_change(text: str) -> bool:
    """Classifier-driven: True when `text` introduces a MUTATING_OPERATIONAL or
    CONFIGURATION_CHANGE operation per the server's own `classify_command_operation`.

    The whole segment is classified first (imperative, noun and negated forms such as
    "a radio restart", "Do not restart the radio" all classify as mutating -- a negated
    mention is removed, never converted into an instruction). Inflected word forms
    ("restarting", "rebooted") are reduced to a base form and classified again with the SAME
    classifier; no operation vocabulary is maintained here. This only ever REMOVES content --
    it never grants authority; the current authorized diagnostic_step.command is excluded from
    `text` by the caller before classification.
    """
    from backend.agents.technical_authority_engineer.agent_tool import classify_command_operation
    from backend.agents.technical_authority_engineer.schemas import CommandOperationType

    if not text or not text.strip():
        return False
    if classify_command_operation(text) in _state_changing_types():
        return True
    for token in re.findall(r"[A-Za-z_]+", text):
        word = token.lower()
        base = _INFLECTION_SUFFIXES.sub("", word)
        if base == word or len(base) < 3:
            continue
        candidates = {base}
        if len(base) > 3 and base[-1] == base[-2]:
            candidates.add(base[:-1])  # doubled consonant, e.g. "resetting" -> "reset"
        if any(classify_command_operation(c) == CommandOperationType.MUTATING_OPERATIONAL for c in candidates):
            return True
    return False


# A blanket "cannot provide the command" claim. When governed evidence is present this is
# replaced by the accurate authorization-status statement (wording only; grants nothing).
_BLANKET_COMMAND_REFUSAL = re.compile(
    r"\b(?:cannot|can\s*not|can't|unable\s+to|won't\s+be\s+able\s+to|will\s+not\s+be\s+able\s+to|not\s+able\s+to)\s+"
    r"(?:provide|give|share|supply)\b[^.!?]*\bcommands?\b",
    re.IGNORECASE,
)


def _state_changing_evidence_lines(verified_evidence: list[dict[str, Any]]) -> list[str]:
    """Dynamic, evidence-derived operations: lines of governed evidence that the server classifier
    marks as state-changing (mutating or configuration). Such procedure text must not be projected
    into the final response unless it is the current authorized diagnostic_step.command."""
    from backend.agents.technical_authority_engineer.agent_tool import classify_command_operation

    lines: list[str] = []
    for ev in verified_evidence or []:
        if not isinstance(ev, dict) or ev.get("source_type") != "governed_knowledge":
            continue
        for raw in str(ev.get("content_snippet") or "").splitlines():
            for part in raw.split("|"):
                line = re.sub(r"\s+", " ", part).strip().lower().rstrip(".,;:")
                if len(line) < 6:
                    continue
                if classify_command_operation(line) in _state_changing_types():
                    if line not in lines:
                        lines.append(line)
    return lines


def _governed_template_patterns(verified_evidence: list[dict[str, Any]]) -> list[re.Pattern]:
    """Unanchored, token-bounded patterns for every command-formatted template in the selected
    governed evidence: the raw template text and, for parameterized templates, any value
    substitution in the command-parameter charset."""
    from backend.agents.technical_authority_engineer.procedure_actions import extract_procedure_actions, template_placeholders

    patterns: list[re.Pattern] = []
    for ev in verified_evidence or []:
        if not isinstance(ev, dict) or ev.get("source_type") != "governed_knowledge":
            continue
        meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
        actions, _ = extract_procedure_actions(
            knowledge_id=str(meta.get("knowledge_id") or ""),
            version_label=str(meta.get("version_label") or ""),
            section_id=str(meta.get("section_id") or ""),
            content=str(ev.get("content_snippet") or ""),
        )
        for action in actions:
            template = action.command_template
            parts, cursor = [], 0
            for _, start, end in template_placeholders(template):
                parts.append(re.escape(template[cursor:start]))
                parts.append(r"(?:[A-Za-z0-9_\-\.:/*]+|" + re.escape(template[start:end]) + ")")
                cursor = end
            parts.append(re.escape(template[cursor:]))
            body = "".join(parts)
            patterns.append(re.compile(rf"(?<![A-Za-z0-9_\-]){body}(?![A-Za-z0-9_\-])", re.IGNORECASE))
    return patterns


def _without_authorized_commands(segment: str, authorized_commands: set[str]) -> str:
    result = segment
    for cmd in sorted((c for c in authorized_commands if c), key=len, reverse=True):
        result = re.sub(rf"(?<![A-Za-z0-9_\-]){re.escape(cmd)}(?![A-Za-z0-9_\-])", " ", result, flags=re.IGNORECASE)
    return result


def _is_operation_authorized(
    candidate: str,
    authorized_commands: set[str],
    catalog: list[dict[str, Any]],
    verified_evidence: list[dict[str, Any]],
) -> bool:
    """Checks whether a candidate command string is authorized by the TAE server state."""
    clean = candidate.strip()
    clean_stripped = re.sub(r"\*\*|`", "", clean).strip()
    clean_stripped = clean_stripped.rstrip(".,;:!?").strip().lower()
    if not clean_stripped:
        return False
    if clean_stripped in authorized_commands or clean.lower() in authorized_commands:
        return True
    if clean.rstrip(".,;:!?").lower() in authorized_commands:
        return True
    return False


def _has_unauthorized_instruction(
    segment: str,
    authorized_commands: set[str],
    catalog: list[dict[str, Any]],
    verified_evidence: list[dict[str, Any]],
) -> bool:
    """Detects whether segment contains unauthorized operational instructions."""
    # -1. Narrative mention of a state-changing operation that is not the current authorized
    # command, or projection of state-changing procedure text from the governed evidence.
    residual = _without_authorized_commands(segment, authorized_commands)
    if _introduces_state_change(residual):
        return True
    residual_norm = re.sub(r"\s+", " ", residual).lower()
    for line in _state_changing_evidence_lines(verified_evidence):
        if line in residual_norm:
            return True
    # Governed command templates from the SELECTED evidence (the same deterministic extraction
    # that produces ProcedureActions): any rendering of one -- or the raw template -- that is not
    # the current authorized command is an unauthorized instruction. Removes content only.
    for pattern in _governed_template_patterns(verified_evidence):
        for match in pattern.finditer(residual):
            if match.group(0).strip().lower() not in authorized_commands:
                return True

    # 0. Check against known catalog commands dynamically
    # Any command in approved_commands_catalog is by definition an operational command candidate.
    # If it appears in the segment but is not in authorized_commands (i.e. not selected this turn),
    # it is an unauthorized instruction for this turn.
    if isinstance(catalog, list):
        seg_lower = segment.lower()
        for item in catalog:
            if isinstance(item, dict):
                c = item.get("command")
                if c and isinstance(c, str) and c.strip():
                    clean_c = re.sub(r"\*\*|`", "", c.strip()).strip().lower()
                    if clean_c and clean_c not in authorized_commands:
                        if re.search(rf"\b{re.escape(clean_c)}\b", seg_lower):
                            return True

    # 1. Backticked tokens or tokens preceded by operational verbs
    # Check backticks preceded by operational verbs (e.g. `Run `cmd``, `execute `cmd``)
    for match in re.finditer(
        r"(?:\b(?:run|execute|issue|apply|perform|trigger|initiate|launch|do)\s+)?`([^`]+)`",
        segment,
        re.IGNORECASE,
    ):
        token = match.group(1).strip()
        token_clean = re.sub(r"\*\*|`", "", token).strip()
        is_candidate = (
            bool(match.group(0).lower().startswith(("run ", "execute ", "issue ", "apply ", "perform ", "trigger ", "initiate ", "launch ", "do ")))
            or any(p.search(token_clean) for p in _MUTATING_PATTERNS)
            or any(p.search(token_clean) for p in _CONFIG_PATTERNS)
            or any(p.search(token_clean) for p in _CLI_PATTERNS)
            or bool(re.match(r"^(?:run|execute)\s+", token_clean, re.IGNORECASE))
        )
        if is_candidate:
            cmd_core = re.sub(r"^(?:run|execute)\s+", "", token_clean, flags=re.IGNORECASE).strip()
            if not _is_operation_authorized(cmd_core, authorized_commands, catalog, verified_evidence):
                return True

    # 2. Mutating instructions in prose
    for p in _MUTATING_PATTERNS:
        for match in p.finditer(segment):
            matched = match.group(0).strip()
            matched_core = re.sub(
                r"^(?:(?:run|execute|issue|apply|perform|trigger|initiate|launch|do)\s+)?(?:\b(?:please|kindly|you\s+(?:must|should|can|need\s+to|have\s+to))\s+)?",
                "",
                matched,
                flags=re.IGNORECASE,
            ).strip()
            if not _is_operation_authorized(matched_core, authorized_commands, catalog, verified_evidence):
                return True

    # 3. Config instructions in prose
    for p in _CONFIG_PATTERNS:
        for match in p.finditer(segment):
            matched = match.group(0).strip()
            if not _is_operation_authorized(matched, authorized_commands, catalog, verified_evidence):
                return True

    # 4. CLI commands in prose
    for p in _CLI_PATTERNS:
        for match in p.finditer(segment):
            matched = match.group(0).strip()
            core = re.sub(r"^(?:run|execute)\s+", "", matched, flags=re.IGNORECASE).strip()
            if not _is_operation_authorized(core, authorized_commands, catalog, verified_evidence):
                return True

    return False


def _has_authorized_command(
    segment: str,
    authorized_commands: set[str],
) -> bool:
    """Checks if segment references an authorized command."""
    if not authorized_commands:
        return False
    seg_lower = segment.lower()
    for cmd in authorized_commands:
        if not cmd:
            continue
        if re.search(rf"\b{re.escape(cmd)}\b", seg_lower):
            return True
    return False


def _sanitize_sentence(
    sentence: str,
    authorized_commands: set[str],
    catalog: list[dict[str, Any]],
    verified_evidence: list[dict[str, Any]],
) -> Optional[str]:
    """Sanitizes an individual sentence, splitting compound clauses if needed."""
    s = sentence.strip()
    if not s:
        return None

    unauth = _has_unauthorized_instruction(s, authorized_commands, catalog, verified_evidence)
    if not unauth:
        return s

    auth = _has_authorized_command(s, authorized_commands)
    if not auth:
        # No authorized command in sentence, but has unauthorized instruction -> drop entirely
        return None

    # Sentence has both authorized command and unauthorized instruction -> split clauses
    parts = _CLAUSE_SPLIT_REGEX.split(s)
    clean_parts = [p.strip() for p in parts if p and p.strip() and not _CLAUSE_SPLIT_REGEX.match(p)]

    surviving = []
    for part in clean_parts:
        if _has_unauthorized_instruction(part, authorized_commands, catalog, verified_evidence):
            continue
        surviving.append(part)

    if not surviving:
        return None

    res = surviving[0]
    for p in surviving[1:]:
        res += f", {p}"
    res = res.rstrip(".,;:") + "."
    return res


def enforce_technical_authority_synthesis_boundary(
    final_text: str,
    technical_authority_execution: Optional[dict[str, Any]],
) -> str:
    """Deterministic final-response boundary after Team Manager synthesis.

    Guarantees:
    - Team Manager may explain or summarize.
    - No additional commands, restart/reset/reload actions, configuration changes,
      or unauthorized operational instructions survive.
    - Authorized commands (e.g. alt) are preserved while appended unauthorized operations
      (e.g. restart radio, st pluginunit) are stripped.
    - Normal explanatory prose and evidence source references are preserved intact.
    - If synthesis contains only unauthorized operations, falls closed cleanly.
    """
    if not final_text or not isinstance(final_text, str):
        return final_text
    if not technical_authority_execution or not isinstance(technical_authority_execution, dict):
        return final_text

    authorized_commands, catalog, verified_evidence = _extract_authorized_commands(
        technical_authority_execution
    )
    has_governed_evidence = any(
        isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge" for ev in verified_evidence
    )

    # 1. Sanitize code blocks
    def _sanitize_code_block(match: re.Match) -> str:
        lang = match.group(1) or ""
        body = match.group(2)
        lines = body.splitlines()
        kept_lines = []
        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue
            if _has_unauthorized_instruction(line_str, authorized_commands, catalog, verified_evidence):
                continue
            kept_lines.append(line)
        if not kept_lines:
            return ""
        return f"```{lang}\n" + "\n".join(kept_lines) + "\n```"

    sanitized_text = re.sub(
        r"```([a-zA-Z0-9_\-]*)\n(.*?)```",
        _sanitize_code_block,
        final_text,
        flags=re.DOTALL,
    )

    # 2. Process line by line and sentence by sentence
    lines = sanitized_text.splitlines()
    sanitized_lines = []
    for line in lines:
        stripped_line = line.strip()
        if not stripped_line:
            sanitized_lines.append("")
            continue

        # Check if list item (- , * , 1. )
        list_match = re.match(r"^([-*]|\d+\.)\s+(.*)$", stripped_line)
        if list_match:
            prefix = list_match.group(1)
            item_text = list_match.group(2)
            if (not authorized_commands and _DANGLING_COMMAND_REFERENCE.search(item_text)) or _asks_operator_for_mechanism(item_text, authorized_commands):
                continue
            san_item = _sanitize_sentence(
                item_text, authorized_commands, catalog, verified_evidence
            )
            if san_item:
                sanitized_lines.append(f"{prefix} {san_item}")
            continue

        # Regular prose line / paragraph
        sentences = re.split(r"(?<=[.!?])\s+", stripped_line)
        san_sentences = []
        for s in sentences:
            if not authorized_commands and _DANGLING_COMMAND_REFERENCE.search(s):
                # The authoritative step carries no command: a reference to "this command" /
                # "its output" would point at nothing (or at a command that was removed).
                continue
            if has_governed_evidence and _BLANKET_COMMAND_REFUSAL.search(s):
                if GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT not in san_sentences:
                    san_sentences.append(GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT)
                continue
            if _asks_operator_for_mechanism(s, authorized_commands):
                continue
            san = _sanitize_sentence(
                s, authorized_commands, catalog, verified_evidence
            )
            if san:
                san_sentences.append(san)
        if san_sentences:
            sanitized_lines.append(" ".join(san_sentences))

    result = "\n".join(sanitized_lines).strip()
    result = re.sub(r"\n{3,}", "\n\n", result)

    # Check for orphaned command intros if no authorized commands exist
    if not authorized_commands:
        lines_clean = result.splitlines()
        filtered_lines = []
        for l in lines_clean:
            if re.match(
                r"^(?:run|execute|use)\s+(?:the\s+)?(?:following|below)\s+(?:command|step|procedure)s?:?$",
                l.strip(),
                re.IGNORECASE,
            ):
                continue
            filtered_lines.append(l)
        result = "\n".join(filtered_lines).strip()

    if not result:
        return _project_current_step(technical_authority_execution, verified_evidence)

    return result


def render_pending_target_confirmation(technical_authority_execution: Optional[dict[str, Any]]) -> Optional[str]:
    """The validated record's governed state change whose server-VALIDATED target awaits the
    operator's confirmation on the existing action card: rendered from server structure only (the
    operational control's validated target identity, the resolution's passed target gate). The
    command is never shown (it is withheld until confirmation and approval); nothing is created,
    regenerated or authorized here. None unless every condition holds."""
    from backend.agents.technical_authority_engineer.agent_tool import OPERATIONAL_CONTROL_KEY
    from backend.agents.technical_authority_engineer.procedure_actions import PROCEDURE_ACTION_RESOLUTION_KEY
    from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityOutcome

    execution = technical_authority_execution
    if not isinstance(execution, dict) or execution.get("outcome") != TechnicalAuthorityOutcome.RECOMMENDED.value:
        return None
    step = execution.get("diagnostic_step")
    control = execution.get(OPERATIONAL_CONTROL_KEY)
    resolution = execution.get(PROCEDURE_ACTION_RESOLUTION_KEY)
    if not isinstance(step, dict) or not isinstance(control, dict) or not isinstance(resolution, dict):
        return None
    if str(step.get("command") or "").strip():
        return None  # an in-turn command is not this state
    if control.get("target_confirmation_required") is not True or control.get("control_stage") != "awaiting_confirmation":
        return None
    if resolution.get("action_type") != "state_change" or not (resolution.get("target_validation") or {}).get("passed"):
        return None
    if not control.get("procedure_action_id") or control.get("procedure_action_id") != resolution.get("action_id"):
        return None
    target = str((control.get("target") or {}).get("display_value") or "").strip()
    if not target:
        return None
    return (
        f"A governed corrective action is available for the validated target {target}. "
        "It is a state-changing action and requires your confirmation before it can proceed; "
        "no command is provided or executed until then.\n\n"
        f"Please confirm {target} on the action card to continue."
    )


def _project_current_step(
    technical_authority_execution: dict[str, Any],
    verified_evidence: list[dict[str, Any]],
) -> str:
    """Fallback built only from the current validated TAE record: the selected step (and its
    command only if one survived authorization), never Team Manager prose. A state change whose
    validated target awaits confirmation is rendered as exactly that state (never as a grounding /
    authorization failure)."""
    confirmation = render_pending_target_confirmation(technical_authority_execution)
    if confirmation is not None:
        return confirmation
    step = technical_authority_execution.get("diagnostic_step")
    has_governed = any(
        isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge" for ev in verified_evidence or []
    )
    if isinstance(step, dict) and str(step.get("action") or "").strip():
        parts = [str(step["action"]).strip().rstrip(".") + "."]
        command = str(step.get("command") or "").strip()
        if command:
            parts.append(f"Command: `{command}`.")
        elif has_governed:
            parts.append(GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT)
        expected = str(step.get("expected_evidence") or "").strip()
        if expected:
            parts.append(f"Please report: {expected.rstrip('.')}.")
        projected = " ".join(parts)
        residual = _without_authorized_commands(projected, {command.lower()} if command else set())
        if not _introduces_state_change(residual) and not any(
            p.search(residual) for p in _governed_template_patterns(verified_evidence)
        ):
            return projected
    return GOVERNED_COMMAND_NOT_AUTHORIZED_TEXT if has_governed else SAFE_SYNTHESIS_FALLBACK


NO_GOVERNED_PROCEDURE_SELECTED_TEXT = (
    "No approved governed procedure was selected for this request, so no operational command is being provided."
)


def _sanitized_prose(text: Any) -> str:
    """Model-authored prose from the validated record, with every sentence that carries an
    operational instruction or command removed (nothing is authorized on this path)."""
    if not isinstance(text, str) or not text.strip():
        return ""
    kept = []
    for sentence in re.split(r"(?<=[.!?])\s+", " ".join(text.split())):
        san = _sanitize_sentence(sentence, set(), [], [])
        if san:
            kept.append(san)
    return " ".join(kept).strip()


def render_technical_authority_negative_selection_response(
    technical_authority_execution: Optional[dict[str, Any]],
) -> Optional[str]:
    """Deterministic response for a validated, non-operational TAE outcome whose governed
    evidence-selection contract was completed with an explicit empty selection.

    Returns None (the caller fails closed) unless EVERY condition holds: the outcome is
    escalation_required/insufficient_evidence; no diagnostic step, command, command source or
    approved command exists (including in any raw specialist payload, per the server-owned
    selection-contract record); no governed evidence was selected; no applicability
    clarification is pending; and the specialist itself searched governed knowledge and then
    explicitly selected nothing. Built only from the validated record's
    escalation_reason / technical_interpretation / missing_information -- never Team Manager
    prose, citations, or AVAILABLE-only evidence.
    """
    from backend.agents.technical_authority_engineer.agent_tool import (
        MANUAL_OBSERVATION_KEY,
        NON_OPERATIONAL_OUTCOMES,
        SELECTION_CONTRACT_RECORD_KEY,
    )
    from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityOutcome

    execution = technical_authority_execution
    if not isinstance(execution, dict):
        return None
    outcome = execution.get("outcome")
    step = execution.get("diagnostic_step")
    # A `recommended` outcome is renderable here ONLY as a server-flagged manual observation (see
    # agent_tool.enforce_selected_evidence_invariant): no command, source, action id or parameters.
    manual = (
        outcome == TechnicalAuthorityOutcome.RECOMMENDED.value
        and execution.get(MANUAL_OBSERVATION_KEY) is True
        and isinstance(step, dict)
        and not any(str(step.get(f) or "").strip() for f in ("command", "command_source", "procedure_action_id"))
        and not step.get("parameter_values")
    )
    if outcome not in NON_OPERATIONAL_OUTCOMES and not manual:
        return None
    if (step is not None and not manual) or execution.get("approved_commands_catalog"):
        return None
    if execution.get("applicability_clarification"):
        return None
    if any(
        isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge"
        for ev in execution.get("verified_evidence") or []
    ):
        return None
    contract = execution.get(SELECTION_CONTRACT_RECORD_KEY)
    if not isinstance(contract, dict):
        return None
    if (
        contract.get("governed_search_performed") is not True
        or contract.get("explicit_negative_selection") is not True
        or contract.get("operational_step_proposed") is not False
    ):
        return None

    sections = [NO_GOVERNED_PROCEDURE_SELECTED_TEXT]
    if manual:
        action = _sanitized_prose(step.get("action"))
        if not action:
            return None  # nothing safe left to present -> caller fails closed
        observation = f"Suggested manual observation (no command is provided): {action}"
        expected = _sanitized_prose(step.get("expected_evidence"))
        sections.append(observation + (f"\nPlease report: {expected}" if expected else ""))
    if outcome == TechnicalAuthorityOutcome.ESCALATION_REQUIRED.value:
        reason = _sanitized_prose(execution.get("escalation_reason"))
        if reason:
            sections.append(f"Escalation required: {reason}")
    interpretation = _sanitized_prose(execution.get("technical_interpretation"))
    if interpretation:
        sections.append(f"Technical assessment: {interpretation}")
    missing = [m for m in (_sanitized_prose(item) for item in execution.get("missing_information") or []) if m]
    if missing:
        sections.append("Missing information:\n" + "\n".join(f"- {m}" for m in missing))
    return "\n\n".join(sections)


def render_applicability_clarification_response(technical_authority_execution: Optional[dict[str, Any]]) -> Optional[str]:
    """The operator-facing request while governed applicability is unresolved, rendered ONLY from the
    validated TAE record's server-evaluated clarification (the dimensions the deterministic
    applicability evaluation reported missing). Synthesis prose is not used: it could request
    information the validated progression state does not need yet (e.g. a target identity before any
    target-specific action was selected). None when the record presents a command, escalates, or
    carries no clarification -- the normal synthesis path then applies."""
    from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityOutcome

    execution = technical_authority_execution
    if not isinstance(execution, dict):
        return None
    clarification = execution.get("applicability_clarification")
    if not isinstance(clarification, dict) or not clarification.get("missing_dimensions"):
        return None
    text = str(clarification.get("text") or "").strip()
    if not text or execution.get("outcome") == TechnicalAuthorityOutcome.ESCALATION_REQUIRED.value:
        return None
    step = execution.get("diagnostic_step")
    if isinstance(step, dict) and str(step.get("command") or "").strip():
        return None
    return text


RESPONSE_INCOMPLETE_TEXT = (
    "No next diagnostic step passed validation in this turn, so no operational guidance or command is provided. "
    "Ask to continue the investigation, or request escalation."
)

_MARKDOWN_EMPHASIS = re.compile(r"[`*_]+")


def _presents_command(text: str, command: str) -> bool:
    """Whether `text` presents `command` verbatim (whitespace, case and markdown emphasis ignored)."""
    def _norm(value: str) -> str:
        return " ".join(_MARKDOWN_EMPHASIS.sub("", value).split()).casefold()

    needle = _norm(command)
    return bool(needle) and re.search(rf"(?<![\w=-]){re.escape(needle)}(?![\w=-])", _norm(text)) is not None


def render_pending_result_request(
    pending: Optional[dict[str, Any]], consumed_step_ids: Any = ()
) -> Optional[str]:
    """The pending step's result request, rendered from the SERVER-recorded step (objective and
    expected evidence only). It never presents a command: an earlier run's authorization is not
    current authority, and nothing is assumed executed. A step whose result this turn bound
    (`consumed_step_ids`) is never rendered: once consumed, it cannot be awaiting its result."""
    if not isinstance(pending, dict):
        return None
    if pending.get("step_id") and pending.get("step_id") in set(consumed_step_ids or ()):
        logger.error("Pending-result request refused: step %s was consumed by this turn's result binding", pending.get("step_id"))
        return None
    objective = _sanitized_prose(pending.get("objective"))
    if not objective:
        return None
    expected = _sanitized_prose(pending.get("expected_evidence"))
    known = _sanitized_prose(pending.get("known_result_objective"))
    if pending.get("candidate_rejected"):
        # This message DID carry output, but it could not be attributed to the step: say so (it was
        # not recorded as the step's result) instead of claiming nothing was supplied.
        text = (
            f"The output in your message could not be matched to the current diagnostic step ({objective.rstrip('.')}), "
            "so it was not recorded as that step's result."
        )
        if expected:
            text += f" Please provide: {expected.rstrip('.')}, including the command line as it was entered."
        return text + " The investigation continues once that result is reported."
    text = ""
    if known:
        text = f"That output was already recorded as the result of an earlier step ({known.rstrip('.')}); it is not a new result. "
    text += f"The current diagnostic step is still awaiting its result: {objective.rstrip('.')}."
    if expected:
        text += f" Please provide: {expected.rstrip('.')}."
    return text + " The investigation continues once that result is reported."


def render_governed_exhaustion(completeness: dict[str, Any]) -> str:
    """A server-routed turn whose governed search found nothing applicable for the next step: the
    governed outcome rendered from server state (open evidence needs without an approved acquisition
    method and the escalation option) -- never a command, never synthesis prose."""
    sections = [NO_GOVERNED_PROCEDURE_SELECTED_TEXT]
    gaps = [g for g in (_sanitized_prose(item) for item in completeness.get("open_gaps") or []) if g]
    if gaps:
        sections.append("Evidence still required without an approved acquisition method:\n" + "\n".join(f"- {g}" for g in gaps))
    sections.append("Provide new evidence or observations to continue the investigation, or escalate to an SME.")
    return "\n\n".join(sections)


def _presents_step(text: str, step: dict[str, Any]) -> bool:
    """Whether `text` presents the validated step: its authorized command verbatim, or -- for a step
    without a command -- at least half of the content words of its stated action."""
    from backend.agents.technical_authority_engineer.turn_request import _content_tokens

    command = str(step.get("command") or "").strip()
    if command:
        return _presents_command(text, command)
    wanted = set(_content_tokens(str(step.get("action") or "")))
    if not wanted:
        return True
    present = wanted & set(_content_tokens(text))
    return len(present) * 2 >= len(wanted)


def _renderable_pending(completeness: dict[str, Any], decision: dict[str, Any]) -> Optional[dict[str, Any]]:
    """The completeness record's pending-result candidate, unless this turn consumed it."""
    pending = completeness.get("pending_step") if isinstance(completeness.get("pending_step"), dict) else None
    consumed = set(completeness.get("consumed_step_ids") or [])
    decision["pending_result_candidate"] = pending is not None
    decision["candidate_step_id"] = pending.get("step_id") if pending is not None else None
    decision["consumed_this_turn"] = sorted(consumed)
    if pending is not None and pending.get("step_id") in consumed:
        logger.error(
            "Final response completeness: pending-result candidate %s was consumed by this turn's result binding -- not renderable",
            pending.get("step_id"),
        )
        decision["consumed_step_rejected"] = pending.get("step_id")
        return None
    return pending


def enforce_response_completeness(
    final_text: str, technical_authority_execution: Optional[dict[str, Any]], decision_sink: Optional[dict[str, Any]] = None
) -> str:
    """Deterministic final-response completeness boundary (no authority of its own).

    - A turn the server continued while actionable investigation state existed (the TAE record's
      server-owned completeness record: open fault, SELECTED MATCH evidence offering valid governed
      actions) that ends with no next step, clarification, governed gap, escalation or resolution
      fails closed with a fixed text -- never a vacuous acknowledgement.
    - A validated next step whose authorized command the synthesis dropped is projected from the
      validated record itself (`_project_current_step`), never from synthesis prose.
    - A step whose result THIS turn bound (`consumed_step_ids`) is never rendered as awaiting its
      result: the post-binding progression wins over any pending-step snapshot.
    `decision_sink` (optional) receives the decision (diagnostics only).
    """
    from backend.agents.technical_authority_engineer.agent_tool import RESPONSE_COMPLETENESS_KEY
    from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityOutcome

    decision: dict[str, Any] = decision_sink if decision_sink is not None else {}
    decision["decision"] = "unchanged"
    execution = technical_authority_execution
    if not isinstance(execution, dict) or not isinstance(final_text, str):
        return final_text
    completeness = execution.get(RESPONSE_COMPLETENESS_KEY)
    if not isinstance(completeness, dict):
        completeness = None
    pending = _renderable_pending(completeness, decision) if completeness is not None else None
    consumed = (completeness or {}).get("consumed_step_ids") or []
    if completeness is not None and completeness.get("required") and not completeness.get("satisfied"):
        pending_request = render_pending_result_request(pending, consumed)
        logger.warning(
            "Final response completeness: active investigation but no next step, clarification, gap, escalation or "
            "resolution -- %s (valid actions=%d)",
            "requesting the pending step's result" if pending_request else "failing closed",
            len(completeness.get("valid_actions") or []),
        )
        decision["decision"] = (
            "pending_result_request" if pending_request
            else "consumed_step_not_renderable" if decision.get("consumed_step_rejected") else "fail_closed"
        )
        return pending_request or RESPONSE_INCOMPLETE_TEXT
    if (
        completeness is not None and completeness.get("forced_route")
        and completeness.get("elements") == ["no_applicable_governed_procedure"]
        and not final_text.startswith(NO_GOVERNED_PROCEDURE_SELECTED_TEXT)
    ):
        logger.warning("Final response completeness: server-routed turn found no applicable governed procedure -- rendering governed outcome")
        decision["decision"] = "governed_exhaustion"
        return render_governed_exhaustion(completeness)
    step = execution.get("diagnostic_step")
    if completeness is not None and completeness.get("presents_consumed_step") and isinstance(step, dict):
        # The validated record re-presents a step whose result THIS turn bound: stale state. Neither
        # it nor any synthesis built on it may reach the operator as the current step.
        logger.error("Final response completeness: the validated record re-presents a step consumed by this turn -- failing closed")
        decision["decision"] = "consumed_step_not_renderable"
        return RESPONSE_INCOMPLETE_TEXT
    if execution.get("outcome") == TechnicalAuthorityOutcome.RECOMMENDED.value and isinstance(step, dict):
        command = str(step.get("command") or "").strip()
        if command and not _presents_command(final_text, command):
            logger.warning("Final response completeness: synthesis dropped the validated step's command -- projecting the validated step")
            decision["decision"] = "project_validated_step"
            return _project_current_step(execution, execution.get("verified_evidence") or [])
        forced = completeness is not None and completeness.get("forced_route")
        if forced and not command and not _presents_step(final_text, step):
            # A server-routed turn whose validated step the synthesis did not present: rendered from
            # the record, never from synthesis prose. Only when the server decided that the final
            # proposal re-presents the (unconsumed) pending step is its result requested; any other
            # validated step -- e.g. the next step after a consumed result -- is projected itself.
            logger.warning("Final response completeness: synthesis did not present the validated step on a server-routed turn")
            presented_pending = completeness.get("presented_pending_step_id")
            if pending is not None and presented_pending and pending.get("step_id") == presented_pending:
                request = render_pending_result_request(pending, consumed)
                if request:
                    decision["decision"] = "pending_result_request"
                    return request
            decision["decision"] = (
                "consumed_step_not_renderable" if decision.get("consumed_step_rejected") else "project_validated_step"
            )
            return _project_current_step(execution, execution.get("verified_evidence") or [])
    return final_text
