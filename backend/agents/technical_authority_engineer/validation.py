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
from typing import Any, Optional

from google.genai import types

from backend.agents.technical_authority_engineer.schemas import (
    TechnicalAuthorityOutcome,
    TechnicalAuthorityResponse,
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


def _is_command_matching_snippet(cmd_clean: str, cmd_stripped: str, snippet: str) -> bool:
    """Checks if cmd matches snippet verbatim or matches any template extracted from snippet."""
    if cmd_clean in snippet or cmd_stripped in snippet:
        return True
    # Check backtick-enclosed candidate templates first, e.g. `restart board <board_slot>`
    for match in re.finditer(r"`([^`]+)`", snippet):
        candidate_template = match.group(1).strip()
        template_regex = _template_to_regex(candidate_template)
        if template_regex and (template_regex.match(cmd_clean) or template_regex.match(cmd_stripped)):
            return True
    # Also test whole snippet if it is a template itself
    template_regex = _template_to_regex(snippet)
    if template_regex and (template_regex.search(cmd_clean) or template_regex.search(cmd_stripped)):
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

    # 1. Check approved commands catalog (server-validated)
    for approved in approved_commands_catalog:
        if not isinstance(approved, dict):
            continue
        app_cmd = approved.get("command", "").strip()
        app_src = approved.get("source_id", "").strip()
        if not src_clean or src_clean == app_src:
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
        ev_snippet = ev.get("content_snippet", "")
        if src_clean and src_clean == ev_src and ev_snippet:
            if _is_command_matching_snippet(cmd_clean, cmd_stripped, ev_snippet):
                return True

    return False


_OPERATIONAL_COMMAND_PATTERNS = [
    re.compile(r"\b(acc\s+[A-Za-z0-9_\-=\.:/]+(?:\s+[A-Za-z0-9_\-=\.:/]+)*)", re.IGNORECASE),
    re.compile(r"\b((?:restart|reboot|reload|format|delete|rm|kill|systemctl|inv)\s+[A-Za-z0-9_\-=\.:/]+(?:\s+[A-Za-z0-9_\-=\.:/]+)*)", re.IGNORECASE),
    re.compile(r"\b(set\s+[A-Za-z0-9_\-=\.:/]+\s+(?:locked|unlocked|state|enabled|disabled))", re.IGNORECASE),
]


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
            regex = re.compile(rf"(?:`|\*\*)?{pattern_str}(?:`|\*\*)?", re.IGNORECASE)
            if regex.search(result):
                result = regex.sub("[unapproved command removed]", result)
                modified = True

    # 2. Check backticked tokens for ungrounded operational commands
    def _replace_backticked(match: re.Match) -> str:
        nonlocal modified
        token = match.group(1).strip()
        token_clean = re.sub(r"\*\*|`", "", token).strip()
        is_op_cmd = any(p.search(token_clean) for p in _OPERATIONAL_COMMAND_PATTERNS)
        if is_op_cmd:
            if not is_command_grounded(token_clean, None, approved_catalog, verified_evidence):
                modified = True
                return "[unapproved command removed]"
        return match.group(0)

    result = re.sub(r"`([^`]+)`", _replace_backticked, result)

    # 3. Check imperative phrases in prose directing execution of ungrounded commands
    for pattern in _OPERATIONAL_COMMAND_PATTERNS:
        for match in pattern.finditer(result):
            matched_cmd = match.group(1).strip()
            if not is_command_grounded(matched_cmd, None, approved_catalog, verified_evidence):
                if "[unapproved command" not in matched_cmd:
                    result = result.replace(matched_cmd, "[unapproved command removed]")
                    modified = True

    # 4. If imperative verbs were paired with stripped commands, neutralize to observational phrases
    imperative_sub_patterns = [
        (re.compile(r"\b(execute|run|issue|perform)\s+\[unapproved command removed\]", re.IGNORECASE), "check [unapproved operational command stripped - observational check only]"),
        (re.compile(r"\b(execute|run|issue|perform)\s+(the\s+command\s+)?\[unapproved command removed\]", re.IGNORECASE), "perform observational check [unapproved operational command stripped]"),
    ]
    for pattern, replacement in imperative_sub_patterns:
        if pattern.search(result):
            result = pattern.sub(replacement, result)
            modified = True

    return result, modified


def validate_technical_authority_payload(
    response_payload: dict[str, Any],
    request_payload: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Validates and enforces response integrity rules against request context.

    Returns:
        (sanitized_payload, was_modified)
    """
    modified = False
    sanitized = dict(response_payload)

    # 1. Evidence Citation Provenance
    verified_evidence = request_payload.get("verified_evidence", [])
    valid_source_ids = {
        ev.get("source_id", "").strip()
        for ev in verified_evidence
        if isinstance(ev, dict) and ev.get("source_id")
    }

    citations = sanitized.get("verified_evidence_citations")
    if isinstance(citations, list):
        filtered_citations = [c for c in citations if isinstance(c, str) and c.strip() in valid_source_ids]
        if len(filtered_citations) != len(citations):
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
            # Check Command Grounding on the single step
            command = step_copy.get("command")
            if command:
                cmd_source = step_copy.get("command_source")
                if not is_command_grounded(command, cmd_source, approved_catalog, verified_evidence):
                    logger.warning(
                        "Technical Authority Engineer command stripped: ungrounded command %r (source %r)",
                        command,
                        cmd_source,
                    )
                    stripped_commands.append(str(command))
                    step_copy["command"] = None
                    step_copy["command_source"] = None
                    restrictions = list(step_copy.get("restrictions") or [])
                    restrictions.append("[Command stripped: unapproved operational command]")
                    restrictions.append("[Observational check only: unapproved command stripped]")
                    step_copy["restrictions"] = restrictions
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

    if not resp_text:
        return None

    try:
        resp_payload = json.loads(resp_text)
    except (json.JSONDecodeError, TypeError):
        # Malformed model response: fail closed to safe error response
        safe_fallback = TechnicalAuthorityResponse(
            outcome=TechnicalAuthorityOutcome.ERROR,
            technical_interpretation="Specialist output was not valid JSON.",
            detail="Deterministic safety boundary: malformed JSON response intercepted.",
        ).model_dump(mode="json")
        return types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(safe_fallback))])

    if not isinstance(resp_payload, dict):
        safe_fallback = TechnicalAuthorityResponse(
            outcome=TechnicalAuthorityOutcome.ERROR,
            technical_interpretation="Specialist output was not a JSON object.",
            detail="Deterministic safety boundary: non-dict JSON response intercepted.",
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

    sanitized, modified = validate_technical_authority_payload(resp_payload, req_payload)

    if not modified:
        return None

    return types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(sanitized))])
