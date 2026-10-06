"""Deterministic response validation and integrity enforcement for the Technical Authority Engineer.

This module guarantees:
1. Citation Provenance: Citations in `verified_evidence_citations` must strictly exist
   in the request's `verified_evidence`. Unknown or fabricated citations are stripped.
2. Command Grounding: Operational commands in `diagnostic_step` must be grounded
   either in `approved_commands_catalog` or verbatim within verified evidence content.
   Ungrounded commands are stripped.
3. Single-Step Discipline: At most one diagnostic step is permitted. Non-recommended
   outcomes cannot have diagnostic steps. Recommended outcomes without steps fail-closed
   to `insufficient_evidence`.
4. Fail-closed safety: Malformed or unparseable payloads return a safe structured fallback.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Optional

from google.genai import types

from backend.agents.technical_authority_engineer.schemas import (
    TechnicalAuthorityOutcome,
    TechnicalAuthorityResponse,
)
from backend.agents.technical_authority_engineer.structured_output import (
    MALFORMED_JSON_FALLBACK_DETAIL,
    NON_OBJECT_JSON_FALLBACK_DETAIL,
)

logger = logging.getLogger(__name__)

_TECHNICAL_AUTHORITY_AUTHOR = "technical_authority_engineer"


def _find_incoming_request_text(callback_context: Any) -> Optional[str]:
    """Extracts JSON text of the incoming TechnicalAuthorityRequest from user_content."""
    user_content = getattr(callback_context, "user_content", None)
    parts = getattr(user_content, "parts", None) if user_content else None
    if not parts:
        return None
    text = "".join(part.text for part in parts if getattr(part, "text", None))
    return text or None


def _find_last_response_text(session: Any) -> Optional[str]:
    """Finds the last response text authored by technical_authority_engineer in the session."""
    for event in reversed(getattr(session, "events", [])):
        author = getattr(event, "author", None)
        if author not in (_TECHNICAL_AUTHORITY_AUTHOR, "troubleshooting_manager"):
            continue
        content = getattr(event, "content", None)
        parts = getattr(content, "parts", None) if content else None
        if not parts:
            continue
        text = "".join(part.text for part in parts if getattr(part, "text", None))
        if text:
            return text
    return None


def _template_to_regex(template: str) -> Optional[re.Pattern]:
    """Converts a command template with placeholders (e.g. <board_slot>, {cell_id}) to a regex."""
    clean = template.strip()
    placeholder_pattern = re.compile(r"<[A-Za-z0-9_\-]+>|\{[A-Za-z0-9_\-]+\}")
    if not placeholder_pattern.search(clean):
        return None
    replaced = placeholder_pattern.sub("__PARAM_PLACEHOLDER__", clean)
    escaped = re.escape(replaced)
    regex_str = "^" + escaped.replace("__PARAM_PLACEHOLDER__", r"[A-Za-z0-9_\-\.:/]+") + "$"
    return re.compile(regex_str, re.IGNORECASE)


_PROHIBITION_PATTERNS = [
    re.compile(r"(?:do\s+not|never|prohibited|must\s+not|avoid)\s+(?:execute|run|issue)?\s*(?:`|\*\*)?{cmd}(?:`|\*\*)?", re.IGNORECASE),
    re.compile(r"warning:\s*(?:do\s+not\s+(?:execute|run|issue)?\s*)?(?:`|\*\*)?{cmd}(?:`|\*\*)?", re.IGNORECASE),
]


def is_command_prohibited_in_snippet(cmd: str, snippet: str) -> bool:
    """Checks if a command is mentioned exclusively in a warning or prohibition context."""
    cmd_clean = re.sub(r"\*\*|`", "", cmd).strip()
    if not cmd_clean:
        return False
    escaped = re.escape(cmd_clean)
    for p_temp in _PROHIBITION_PATTERNS:
        pattern = re.compile(p_temp.pattern.format(cmd=escaped), re.IGNORECASE)
        if pattern.search(snippet):
            return True
    return False


def _is_command_matching_snippet(cmd_clean: str, cmd_stripped: str, snippet: str) -> bool:
    """Checks if cmd matches snippet via backtick template, word boundary, or template regex.

    Arbitrary substring matches (e.g. 'alt' inside 'fault') and prohibited commands are rejected.
    """
    if is_command_prohibited_in_snippet(cmd_clean, snippet):
        return False

    # 1. Check backtick-enclosed candidate commands and templates, e.g. `alt`, `restart board <board_slot>`
    for match in re.finditer(r"`([^`\n]+)`", snippet):
        candidate = match.group(1).strip()
        if cmd_clean == candidate or cmd_stripped == candidate:
            return True
        template_regex = _template_to_regex(candidate)
        if template_regex and (template_regex.match(cmd_clean) or template_regex.match(cmd_stripped)):
            return True

    # 2. Word-boundary exact match (prevents substring hijacking e.g. 'alt' in 'fault')
    for target in (cmd_clean, cmd_stripped):
        if not target:
            continue
        escaped = re.escape(target)
        if re.search(rf"(?<![A-Za-z0-9_\-]){escaped}(?![A-Za-z0-9_\-])", snippet, re.IGNORECASE):
            return True

    # 3. Whole snippet template regex
    template_regex = _template_to_regex(snippet)
    if template_regex and (template_regex.search(cmd_clean) or template_regex.search(cmd_stripped)):
        return True

    return False


def _evidence_field(ev: Any, name: str) -> Any:
    return getattr(ev, name, None) if not isinstance(ev, dict) else ev.get(name)


def _normalize_source_alias(value: Any) -> str:
    """Case-insensitive, whitespace-collapsed alias form with parenthetical annotations
    (e.g. "(section: HC Commands)", "(line 81)") removed."""
    if not isinstance(value, str):
        return ""
    base = re.sub(r"\s*\([^)]*\)", "", value)
    return re.sub(r"\s+", " ", base).strip().casefold()


def match_source_to_evidence(
    raw_source: Optional[str],
    evidence_references: list[dict[str, Any] | Any],
) -> list[str]:
    """Returns the distinct canonical source_ids in `evidence_references` that `raw_source` names.

    An exact canonical source_id always identifies exactly one item. Otherwise the raw value is
    compared (exact, normalized) against server-trusted aliases of governed knowledge items only:
    the governed document's source_id (e.g. its original filename), its title, its knowledge_id and
    its section_id.
    Callers pass only the current run's SELECTED evidence (see build_server_validated_evidence), so
    AVAILABLE/unselected evidence can never be matched here.
    """
    if not raw_source or not isinstance(raw_source, str) or not raw_source.strip():
        return []
    src_clean = raw_source.strip()

    for ev in evidence_references:
        canonical_id = _evidence_field(ev, "source_id")
        if isinstance(canonical_id, str) and canonical_id.strip() and canonical_id.strip() == src_clean:
            return [canonical_id.strip()]

    wanted = _normalize_source_alias(src_clean)
    if not wanted:
        return []

    matches: list[str] = []
    for ev in evidence_references:
        canonical_id = _evidence_field(ev, "source_id")
        if not isinstance(canonical_id, str) or not canonical_id.strip():
            continue
        canonical_id = canonical_id.strip()
        if _normalize_source_alias(canonical_id) == wanted:
            if canonical_id not in matches:
                matches.append(canonical_id)
            continue
        if _evidence_field(ev, "source_type") != "governed_knowledge":
            continue
        meta = _evidence_field(ev, "metadata")
        meta = meta if isinstance(meta, dict) else {}
        aliases = (
            _evidence_field(ev, "title"),
            meta.get("title"),
            meta.get("source_id"),
            meta.get("knowledge_id"),
            meta.get("section_id"),
        )
        if any(_normalize_source_alias(alias) == wanted for alias in aliases if alias):
            if canonical_id not in matches:
                matches.append(canonical_id)
    return matches


def resolve_canonical_source_id(
    raw_source: Optional[str],
    evidence_references: list[dict[str, Any] | Any],
) -> Optional[str]:
    """Resolves a model- or caller-supplied source identifier to a canonical server-owned source_id.

    Recognizes the canonical id (knowledge_id:version_label:section_id) and, for governed knowledge,
    the document filename/source_id, title, knowledge_id and section_id. Fails closed (returns None) when the
    value matches no evidence item or is ambiguous (matches more than one).
    """
    matches = match_source_to_evidence(raw_source, evidence_references)
    return matches[0] if len(matches) == 1 else None


def _section_grounds_command(command: str, ev: Any) -> bool:
    """The exact command appears as an instruction in this governed section's INSTRUCTION-ONLY text
    (sample output, transcripts and examples never count) and is not prohibited there."""
    from backend.agents.technical_authority_engineer.procedure_actions import instruction_view

    full = str(_evidence_field(ev, "content_snippet") or "")
    cmd_clean = command.strip()
    cmd_stripped = re.sub(r"\*\*|`", "", cmd_clean).strip()
    if not cmd_clean or not full or is_command_prohibited_in_snippet(cmd_clean, full):
        return False
    meta = _evidence_field(ev, "metadata")
    meta = meta if isinstance(meta, dict) else {}
    return _is_command_matching_snippet(cmd_clean, cmd_stripped, instruction_view(full, artifact_derived=bool(meta.get("artifact_derived"))))


def resolve_command_source(
    command: Optional[str],
    raw_source: Optional[str],
    evidence_references: list[dict[str, Any] | Any],
) -> Optional[str]:
    """The ONE current-run governed section (display id) a model-written command source denotes.

    An exact display id or a unique alias wins (resolve_canonical_source_id). When an alias -- e.g. a
    document filename -- matches SEVERAL of the run's selected sections, the exact command is
    grounded against each candidate section's instruction-only governed text: exactly one grounds it
    -> that section; none or several -> None (fail closed). Never the first match, never a filename
    alone; callers pass only the run's SELECTED evidence."""
    matches = match_source_to_evidence(raw_source, evidence_references)
    if len(matches) == 1:
        return matches[0]
    if len(matches) < 2 or not command or not str(command).strip():
        return None
    candidates = [
        ev for ev in evidence_references
        if _evidence_field(ev, "source_type") == "governed_knowledge" and _evidence_field(ev, "source_id") in matches
    ]
    grounded = [str(_evidence_field(ev, "source_id")) for ev in candidates if _section_grounds_command(str(command), ev)]
    return grounded[0] if len(set(grounded)) == 1 else None


def _citation_identity(citation: Any) -> Optional[Any]:
    """A citation written as a STRUCTURED identity (a JSON object with exactly knowledge_id,
    version_label and section_id) -> EvidenceIdentity. Never parsed from a display string."""
    from backend.cases.evidence_identity import stored_identity

    if not isinstance(citation, str) or not citation.strip().startswith("{"):
        return None
    try:
        return stored_identity(json.loads(citation))
    except (ValueError, TypeError):
        return None


def _resolve_citation(citation: Any, governed: list[Any]) -> tuple[str, Optional[str]]:
    """(resolution kind, the selected section's display id) of one model-written citation:
    'selected_display_id' | 'structured_identity' | 'alias' | 'structured_mismatch' | 'unresolved'.
    A structured citation is compared ONLY structurally (never through aliases)."""
    from backend.cases.evidence_identity import identity_of

    if not isinstance(citation, str) or not citation.strip():
        return "unresolved", None
    text = citation.strip()
    identity = _citation_identity(text)
    if identity is not None:
        hit = next((ev for ev in governed if identity_of(ev) == identity), None)
        return ("structured_identity", str(_evidence_field(hit, "source_id"))) if hit is not None else ("structured_mismatch", None)
    if any(_evidence_field(ev, "source_id") == text for ev in governed):
        return "selected_display_id", text
    matches = match_source_to_evidence(text, governed)
    return ("alias", matches[0] if len(matches) == 1 else None) if matches else ("unresolved", None)


# ---------------------------------------------------------------------------------------------
# Integrity decisions (run-scoped; observability + the deferred ProcedureAction provenance check)
# ---------------------------------------------------------------------------------------------

INTEGRITY_UNRESOLVED_CITATION_TEXT = (
    "Recommendation relies on governed procedure but no approved, applicable governed evidence was selected in this run."
)
ACTION_RESOLUTION_DEFERRED = "action_resolution_deferred"

_integrity_lock = threading.Lock()
_integrity: dict[str, list[dict[str, Any]]] = {}


def _current_run_id() -> Optional[str]:
    try:
        from backend.api.turn_context import current_run_id

        return current_run_id()
    except Exception:
        return None


def record_integrity_decision(decision: dict[str, Any]) -> None:
    """Record one integrity decision for THIS run (diagnostic trace + run-scoped registry)."""
    run_id = _current_run_id()
    if run_id:
        with _integrity_lock:
            _integrity.setdefault(run_id, []).append(dict(decision))
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event({"stage": "integrity", **decision})
    except Exception:
        pass


def integrity_decisions(run_id: Optional[str]) -> list[dict[str, Any]]:
    if not run_id:
        return []
    with _integrity_lock:
        return [dict(d) for d in _integrity.get(run_id, [])]


def discard_integrity_decisions(run_id: str) -> None:
    with _integrity_lock:
        _integrity.pop(run_id, None)


def rollback_integrity_decisions(run_id: Optional[str], count: int) -> None:
    """Keep only this run's first `count` decisions: an answer the server discarded (never adopted)
    leaves no integrity decision behind. Its diagnostic-trace events are kept for audit."""
    if not run_id:
        return
    with _integrity_lock:
        if run_id in _integrity:
            del _integrity[run_id][count:]


def is_command_authorized(
    command: str,
    command_source: Optional[str],
    approved_commands_catalog: list[dict[str, Any]],
    verified_evidence: list[dict[str, Any]],
) -> bool:
    """Checks if a command is authorized in the server-validated approved commands catalog.

    Operational commands exposed in diagnostic_step.command or operational prose must be
    grounded AND authorized by server-side policy. Textual presence in evidence snippets
    alone does not confer authorization.
    """
    cmd_clean = command.strip()
    cmd_stripped = re.sub(r"\*\*|`", "", cmd_clean).strip()
    src_clean = (command_source or "").strip()

    canonical_src = resolve_command_source(cmd_clean, src_clean, verified_evidence) or src_clean

    for approved in approved_commands_catalog:
        if not isinstance(approved, dict):
            continue
        app_cmd = approved.get("command", "").strip()
        app_src = approved.get("source_id", "").strip()
        if not src_clean or canonical_src == app_src or src_clean == app_src:
            if cmd_clean == app_cmd or cmd_stripped == app_cmd:
                return True
            template_regex = _template_to_regex(app_cmd)
            if template_regex and (template_regex.match(cmd_clean) or template_regex.match(cmd_stripped)):
                return True

    return False


def is_command_grounded(
    command: str,
    command_source: Optional[str],
    approved_commands_catalog: list[dict[str, Any]],
    verified_evidence: list[dict[str, Any]],
) -> bool:
    """Checks if a command is strictly grounded in the approved catalog or verified governed evidence.

    Supports exact commands and parameterized template commands with parameter substitution.
    Non-governed evidence (Teams conversations, case context) CANNOT authorize operational commands.
    """
    cmd_clean = command.strip()
    cmd_stripped = re.sub(r"\*\*|`", "", cmd_clean).strip()
    src_clean = (command_source or "").strip()

    canonical_src = resolve_command_source(cmd_clean, src_clean, verified_evidence) or src_clean

    # 1. Check approved commands catalog (server-validated)
    for approved in approved_commands_catalog:
        if not isinstance(approved, dict):
            continue
        app_cmd = approved.get("command", "").strip()
        app_src = approved.get("source_id", "").strip()
        if not src_clean or canonical_src == app_src or src_clean == app_src:
            if cmd_clean == app_cmd or cmd_stripped == app_cmd:
                return True
            template_regex = _template_to_regex(app_cmd)
            if template_regex and (template_regex.match(cmd_clean) or template_regex.match(cmd_stripped)):
                return True

    # 2. Check verified evidence ONLY if source is governed knowledge / approved procedure
    _AUTHORIZED_SOURCE_TYPES = {"governed_knowledge", "approved_procedure", "governed_command_registry"}
    for ev in verified_evidence:
        if not isinstance(ev, dict):
            continue
        ev_type = ev.get("source_type", "")
        ev_src = ev.get("source_id", "").strip()
        # Non-governed sources (Teams chats, raw case notes, user observations) CANNOT authorize operational commands
        if ev_type in ("teams_conversation", "case_context", "user_evidence", "observed_metric") or ev_src.startswith("teams:"):
            continue
        if ev_type and ev_type not in _AUTHORIZED_SOURCE_TYPES:
            continue
        if ev_type == "governed_knowledge":
            meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
            l_status = str(meta.get("lifecycle_status", "")).lower()
            app_outcome = str(meta.get("applicability_outcome", "")).lower()
            if l_status != "approved":
                continue
            if app_outcome != "match":
                continue
        ev_snippet = ev.get("content_snippet", "")
        if (canonical_src == ev_src or src_clean == ev_src) and ev_snippet:
            if _is_command_matching_snippet(cmd_clean, cmd_stripped, ev_snippet):
                return True

    return False


_OPERATIONAL_COMMAND_PATTERNS = [
    re.compile(r"\b(acc\s+[A-Za-z0-9_\-=\.:/]+(?:\s+[A-Za-z0-9_\-=\.:/]+)*)", re.IGNORECASE),
    re.compile(r"\b((?:restart|reboot|reload|format|delete|rm|kill|systemctl|inv)\s+[A-Za-z0-9_\-=\.:/]+(?:\s+[A-Za-z0-9_\-=\.:/]+)*)", re.IGNORECASE),
    re.compile(r"\b(set\s+[A-Za-z0-9_\-=\.:/]+\s+(?:locked|unlocked|state|enabled|disabled))", re.IGNORECASE),
]


_EMPTY_DELIMITERS = re.compile(r"''|\"\"|``|‘’|“”|\*\*\*\*")
_COMMAND_NOUN = re.compile(r"\b(the|this|that)\s+command\b", re.IGNORECASE)


def repair_removed_command_references(text: str, command_removed: bool = False) -> str:
    """Prose left behind after a command was removed from it: drop the empty quotes / backticks /
    emphasis the command occupied ("the '' command") and, since the text no longer names any
    command, refer to "the check" instead of "the command". Text without such a removal trace is
    returned unchanged (whitespace aside)."""
    if not text or not isinstance(text, str):
        return text
    repaired = _EMPTY_DELIMITERS.sub("", text)
    if command_removed or repaired != text:
        repaired = _COMMAND_NOUN.sub(lambda m: f"{m.group(1)} check", repaired)
        repaired = re.sub(r"[ \t]{2,}", " ", repaired)
        repaired = re.sub(r"\s+([,.;:!?])", r"\1", repaired).strip()
    return repaired


def sanitize_prose_operational_instructions(
    text: str,
    stripped_commands: list[str],
    approved_catalog: list[dict[str, Any]],
    verified_evidence: list[dict[str, Any]],
) -> tuple[str, bool]:
    """Validates operational instructions across prose fields.
    Scrubs occurrences of stripped commands and unapproved operational commands
    even when diagnostic_step.command is null.
    """
    if not text or not isinstance(text, str):
        return text, False

    modified = False
    result = text

    # 1. Scrub stripped commands (with or without backticks or markdown formatting)
    for cmd in stripped_commands:
        if not cmd:
            continue
        cmd_clean = re.sub(r"\*\*|`", "", cmd).strip()
        for pattern_str in [re.escape(cmd), re.escape(cmd_clean)]:
            if not pattern_str:
                continue
            # Whole command only: a command that is a prefix of an ordinary word is never cut out of it.
            regex = re.compile(rf"(?:`|\*\*)?(?<![\w-]){pattern_str}(?![\w-])(?:`|\*\*)?", re.IGNORECASE)
            if regex.search(result):
                result = repair_removed_command_references(regex.sub("", result), command_removed=True)
                modified = True

    # 2. Check backticked tokens for ungrounded operational commands
    def _replace_backticked(match: re.Match) -> str:
        nonlocal modified
        token = match.group(1).strip()
        token_clean = re.sub(r"\*\*|`", "", token).strip()
        is_op_cmd = any(p.search(token_clean) for p in _OPERATIONAL_COMMAND_PATTERNS)
        if is_op_cmd:
            if not is_command_authorized(token_clean, None, approved_catalog, verified_evidence):
                modified = True
                return ""
        return match.group(0)

    result = re.sub(r"`([^`]+)`", _replace_backticked, result)

    # 3. Check imperative phrases in prose directing execution of ungrounded commands
    for pattern in _OPERATIONAL_COMMAND_PATTERNS:
        for match in pattern.finditer(result):
            matched_cmd = match.group(1).strip()
            if not is_command_authorized(matched_cmd, None, approved_catalog, verified_evidence):
                result = result.replace(matched_cmd, "")
                modified = True

    # 4. Clean up any resulting double spaces or empty backticks/quotes
    result = re.sub(r"`\s*`", "", result)
    result = re.sub(r"\s{2,}", " ", result).strip()

    return result, modified


def validate_technical_authority_payload(
    response_payload: dict[str, Any],
    request_payload: dict[str, Any],
    *,
    phase: str = "validation",
) -> tuple[dict[str, Any], bool]:
    """Validates and enforces response integrity rules against request context.

    Authority order: SERVER-OWNED STRUCTURED PROVENANCE > MODEL-WRITTEN CITATION STRINGS. Citations
    are descriptive: a step that carries a `procedure_action_id` is never destroyed here because its
    citation strings do not resolve -- the decision is deferred to the server's ProcedureAction
    resolution (issued this run, re-derived from this run's SELECTED evidence, approved, MATCH),
    which fails closed exactly as before if it cannot prove the action. Every decision is recorded
    (`record_integrity_decision`; `phase` names the caller).

    Returns:
        (sanitized_payload, was_modified)
    """
    modified = False
    sanitized = dict(response_payload)
    original_outcome = response_payload.get("outcome") if isinstance(response_payload, dict) else None
    raw_step = response_payload.get("diagnostic_step") if isinstance(response_payload, dict) else None
    action_id = str(raw_step.get("procedure_action_id") or "").strip() if isinstance(raw_step, dict) else ""
    integrity: dict[str, Any] = {"check": None, "decision": "passed", "reason": None}

    # 1. Evidence Citation Provenance
    verified_evidence = request_payload.get("verified_evidence", [])
    valid_source_ids = {
        ev.get("source_id", "").strip()
        for ev in verified_evidence
        if isinstance(ev, dict) and ev.get("source_id")
    }
    governed_evidence = [ev for ev in verified_evidence if isinstance(ev, dict) and ev.get("source_type") == "governed_knowledge"]

    citations = sanitized.get("verified_evidence_citations")
    citation_resolution: list[tuple[str, Optional[str]]] = []
    if isinstance(citations, list):
        citation_resolution = [_resolve_citation(c, governed_evidence) for c in citations]
        # Kept: exact display ids, and structured identities of SELECTED sections normalized to their
        # display id (server-owned). Aliases and anything unresolved are dropped from the citations.
        filtered_citations = [
            c if isinstance(c, str) and c.strip() in valid_source_ids else display
            for c, (kind, display) in zip(citations, citation_resolution)
            if (isinstance(c, str) and c.strip() in valid_source_ids) or (kind == "structured_identity" and display)
        ]
        if filtered_citations != citations:
            sanitized["verified_evidence_citations"] = filtered_citations
            modified = True
    elif citations is not None:
        sanitized["verified_evidence_citations"] = []
        modified = True

    # 2. Outcome & Single Step Discipline
    outcome_raw = sanitized.get("outcome")
    try:
        outcome = TechnicalAuthorityOutcome(outcome_raw)
    except (ValueError, TypeError):
        outcome = TechnicalAuthorityOutcome.ERROR
        sanitized["outcome"] = outcome.value
        modified = True

    step = sanitized.get("diagnostic_step")
    approved_catalog = request_payload.get("approved_commands_catalog", [])
    stripped_commands: list[str] = []

    if outcome != TechnicalAuthorityOutcome.RECOMMENDED:
        if step is not None:
            sanitized["diagnostic_step"] = None
            modified = True
            # A non-operational outcome never carries an operational step: the outcome wins
            # deterministically (fail closed); the contradiction is recorded, never silent.
            integrity.update(check="payload_contradiction", decision="step_discarded",
                             reason=f"{outcome.value} outcome with a diagnostic_step: the step is discarded")
    else:
        # Outcome is RECOMMENDED
        if not isinstance(step, dict) or not step.get("action"):
            # Recommended outcome without a valid diagnostic step must fail closed to insufficient_evidence
            sanitized["outcome"] = TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value
            sanitized["diagnostic_step"] = None
            missing = sanitized.get("missing_information", [])
            if not isinstance(missing, list):
                missing = []
            missing.append("Diagnostic step action was missing or invalid.")
            sanitized["missing_information"] = missing
            modified = True
        else:
            step_copy = dict(step)
            # Check Command Authorization on the single step (requires presence in approved_commands_catalog)
            command = step_copy.get("command")
            if command:
                cmd_source = step_copy.get("command_source")
                if not is_command_authorized(command, cmd_source, approved_catalog, verified_evidence):
                    logger.warning(
                        "Technical Authority Engineer command stripped: unauthorized command %r (source %r)",
                        command,
                        cmd_source,
                    )
                    stripped_commands.append(str(command))
                    step_copy["command"] = None
                    step_copy["command_source"] = None
                    restrictions = list(step_copy.get("restrictions") or [])
                    restrictions.append("[Command stripped: unapproved operational command]")
                    governed_source_available = any(
                        isinstance(ev, dict)
                        and ev.get("source_type") == "governed_knowledge"
                        and str((ev.get("metadata") or {}).get("lifecycle_status", "")).lower() == "approved"
                        for ev in verified_evidence
                    )
                    if governed_source_available:
                        restrictions.append(
                            "[Governed procedure available: no command for this diagnostic step passed current grounding and authorization checks]"
                        )
                    else:
                        restrictions.append("[Operational command is not authorized: missing approved governed procedure]")
                    restrictions.append("[Observational check only: unapproved command stripped]")
                    step_copy["restrictions"] = restrictions

                    source_matches = match_source_to_evidence(cmd_source, verified_evidence)
                    if len(source_matches) > 1:
                        restrictions.append("[Command source ambiguous: matches more than one selected governed evidence item]")
                    elif not source_matches:
                        restrictions.append("[Command source does not resolve to selected governed evidence]")
                    step_copy["restrictions"] = restrictions

                    # If command was ungrounded due to non-MATCH applicability, record a targeted
                    # clarification for exactly the dimensions the server evaluation could not resolve.
                    canonical_src = source_matches[0] if len(source_matches) == 1 else None
                    for ev in verified_evidence:
                        if canonical_src and isinstance(ev, dict) and ev.get("source_id") == canonical_src:
                            meta = ev.get("metadata") if isinstance(ev.get("metadata"), dict) else {}
                            app_out = str(meta.get("applicability_outcome", "")).lower()
                            if app_out in ("partial_match", "unknown", "not_applicable"):
                                missing = list(sanitized.get("missing_information") or [])
                                if app_out == "not_applicable":
                                    gap_notes = [f"Procedure {canonical_src} is not applicable to the stated operational context."]
                                else:
                                    gap_notes = [f"Operational context for procedure {canonical_src} is {app_out}; exact applicability dimensions required."]
                                    dims = [str(d) for d in (meta.get("unresolved_applicability_dimensions") or []) if str(d).strip()]
                                    gap_notes.extend(f"Missing applicability context: {d}" for d in dims)
                                for note in gap_notes:
                                    if note not in missing:
                                        missing.append(note)
                                sanitized["missing_information"] = missing
                                modified = True

                    # Neutralize execution instruction so user is never told to execute an unspecified command
                    raw_action = step_copy.get("action", "")
                    action_text = raw_action
                    for sc in stripped_commands:
                        action_text = re.sub(rf"(?:`|\*\*)?{re.escape(sc)}(?:`|\*\*)?", "", action_text, flags=re.IGNORECASE)
                    action_text = re.sub(
                        r"\b(run|execute|issue|perform)\s+(the\s+command\s+)?(in|on|at|against)\b",
                        r"Perform observational check \3",
                        action_text,
                        flags=re.IGNORECASE,
                    )
                    action_text = re.sub(r"^\s*(run|execute|issue)\s+", "Inspect ", action_text, flags=re.IGNORECASE)
                    step_copy["action"] = re.sub(r"\s{2,}", " ", action_text).strip()
                    modified = True
                else:
                    # Grounded: bind canonical server-owned source ID to command_source
                    canonical_src = resolve_command_source(command, cmd_source, verified_evidence)
                    if canonical_src and canonical_src != cmd_source:
                        step_copy["command_source"] = canonical_src
                        modified = True

            # Validate operational instructions across all prose fields of the step
            for field in ("action", "reason", "expected_evidence"):
                val = step_copy.get(field)
                if val and isinstance(val, str):
                    new_val, field_mod = sanitize_prose_operational_instructions(
                        val, stripped_commands, approved_catalog, verified_evidence
                    )
                    if field_mod:
                        step_copy[field] = new_val
                        modified = True
                        # If command was null but unapproved operational commands were stripped from prose
                        if not command:
                            restrictions = list(step_copy.get("restrictions") or [])
                            if not any("[Observational check only" in r for r in restrictions):
                                restrictions.append("[Observational check only: unapproved operational command stripped from prose]")
                                step_copy["restrictions"] = restrictions

            # Invariant 5: Post-TAE Fail-Closed Integrity
            # If recommendation materially relies on claimed evidence citations that do not
            # resolve to trusted selected evidence in this run, TAE must fail closed
            # to outcome = 'insufficient_evidence'.
            valid_selected_source_ids = {
                ev.get("source_id", "").strip()
                for ev in verified_evidence
                if isinstance(ev, dict) and ev.get("source_id")
            }
            raw_citations = [
                str(c).strip()
                for c in (response_payload.get("verified_evidence_citations") or [])
                if isinstance(c, str) and str(c).strip()
            ]
            cites_unselected_evidence = bool(
                raw_citations
                and not any(
                    c in valid_selected_source_ids
                    or _resolve_citation(c, governed_evidence)[0] in ("selected_display_id", "structured_identity", "alias")
                    for c in raw_citations
                )
            )
            if cites_unselected_evidence and action_id and governed_evidence:
                # SERVER-OWNED PROVENANCE > CITATION STRINGS: the step names a ProcedureAction and
                # this run has SELECTED governed evidence. Whether that action was issued in this run
                # and re-derives from the SELECTED, approved, MATCH evidence is decided by the
                # server's resolver; if it cannot prove it, the step fails closed there.
                cites_unselected_evidence = False
                integrity.update(check="citation_unresolved_but_action_server_resolvable", decision=ACTION_RESOLUTION_DEFERRED,
                                 reason="no model citation resolves to SELECTED evidence; the ProcedureAction's own provenance decides")
            elif cites_unselected_evidence:
                integrity.update(
                    check="citation_unresolved_no_action_id" if governed_evidence else "citation_unresolved_no_selected_evidence",
                    decision="fail_closed_insufficient_evidence",
                    reason="the recommendation's citations resolve to no SELECTED governed evidence",
                )

            if cites_unselected_evidence:
                sanitized["outcome"] = TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value
                sanitized["diagnostic_step"] = None
                missing = list(sanitized.get("missing_information") or [])
                missing.append(INTEGRITY_UNRESOLVED_CITATION_TEXT)
                sanitized["missing_information"] = missing
                modified = True

            if sanitized["outcome"] == TechnicalAuthorityOutcome.RECOMMENDED.value:
                sanitized["diagnostic_step"] = step_copy

    # Validate prose operational instructions in technical_interpretation
    interp = sanitized.get("technical_interpretation")
    if interp and isinstance(interp, str):
        new_interp, interp_mod = sanitize_prose_operational_instructions(
            interp, stripped_commands, approved_catalog, verified_evidence
        )
        if interp_mod:
            sanitized["technical_interpretation"] = new_interp
            modified = True

    # 3. Escalation Reason Enforcement
    if outcome == TechnicalAuthorityOutcome.ESCALATION_REQUIRED:
        if not sanitized.get("escalation_reason"):
            sanitized["escalation_reason"] = "Escalation required: issue cannot be diagnosed or resolved safely."
            modified = True

    # 4. Fallback for Error outcome
    if outcome == TechnicalAuthorityOutcome.ERROR:
        if not sanitized.get("technical_interpretation"):
            sanitized["technical_interpretation"] = "Technical evaluation encountered an error."
            modified = True

    if integrity["check"] is None and citation_resolution and any(kind in ("selected_display_id", "structured_identity", "alias") for kind, _ in citation_resolution):
        integrity.update(check="citation_matches_selected_evidence")
    record_integrity_decision({
        "phase": phase,
        "original_outcome": getattr(original_outcome, "value", original_outcome),
        "final_outcome": getattr(sanitized.get("outcome"), "value", sanitized.get("outcome")),
        "procedure_action_id": action_id or None,
        "citation_count": len(citation_resolution),
        "citation_resolution": [kind for kind, _ in citation_resolution],
        "structured_identity_match": any(kind == "structured_identity" for kind, _ in citation_resolution),
        "selected_evidence": len(governed_evidence),
        "check_triggered": integrity["check"],
        "action": integrity["decision"],
        "reason": integrity["reason"],
    })
    return sanitized, modified


async def enforce_technical_authority_response_integrity(
    callback_context: Any,
) -> Optional[types.Content]:
    """ADK after_agent_callback for technical_authority_engineer.

    Intercepts the specialist's structured response, verifies citation provenance,
    enforces command grounding against approved catalog, and enforces single-step discipline.
    """
    req_text = _find_incoming_request_text(callback_context)
    resp_text = _find_last_response_text(getattr(callback_context, "session", None))

    if not resp_text or not resp_text.strip():
        # No structured payload at all (empty / whitespace-only): left to the tool's structured-output
        # recovery, which classifies it as an empty response.
        return None

    try:
        resp_payload = json.loads(resp_text)
    except (json.JSONDecodeError, TypeError):
        # Malformed model response: fail closed to safe error response
        safe_fallback = TechnicalAuthorityResponse(
            outcome=TechnicalAuthorityOutcome.ERROR,
            technical_interpretation="Specialist output was not valid JSON.",
            detail=MALFORMED_JSON_FALLBACK_DETAIL,
        ).model_dump(mode="json")
        return types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(safe_fallback))])

    if not isinstance(resp_payload, dict):
        safe_fallback = TechnicalAuthorityResponse(
            outcome=TechnicalAuthorityOutcome.ERROR,
            technical_interpretation="Specialist output was not a JSON object.",
            detail=NON_OBJECT_JSON_FALLBACK_DETAIL,
        ).model_dump(mode="json")
        return types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(safe_fallback))])

    # Parse request payload
    req_payload: dict[str, Any] = {}
    if req_text:
        try:
            parsed_req = json.loads(req_text)
            if isinstance(parsed_req, dict):
                req_payload = parsed_req
        except Exception:
            pass

    # Dynamic evidence refresh: specialist may have selected governed knowledge during execution.
    # Preserve approved procedure, applicability, parameter and source checks via build_server_validated_evidence.
    try:
        from backend.agents.technical_authority_engineer.agent_tool import (
            build_server_validated_commands,
            build_server_validated_evidence,
        )
        from backend.api.turn_context import current_run_id

        run_id = current_run_id()
        if run_id:
            caller_ev = req_payload.get("verified_evidence", [])
            caller_cmd = list(req_payload.get("approved_commands_catalog", []))
            resp_step = resp_payload.get("diagnostic_step")
            if isinstance(resp_step, dict) and resp_step.get("command"):
                caller_cmd.append({
                    "command": resp_step["command"],
                    "source_id": resp_step.get("command_source") or "",
                    "procedure_section": resp_step.get("action"),
                    "restrictions": resp_step.get("restrictions") or [],
                })
            refreshed_ev = build_server_validated_evidence(run_id, None, caller_ev)
            refreshed_cmd = build_server_validated_commands(refreshed_ev, caller_cmd)
            req_payload["verified_evidence"] = [e.model_dump(mode="json") for e in refreshed_ev]
            req_payload["approved_commands_catalog"] = [c.model_dump(mode="json") for c in refreshed_cmd]
    except Exception as e:
        logger.warning("Dynamic evidence refresh in callback encountered error: %s", e)

    sanitized, modified = validate_technical_authority_payload(resp_payload, req_payload, phase="integrity_callback")

    if not modified:
        return None

    return types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(sanitized))])
