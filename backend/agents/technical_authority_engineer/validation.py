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


def is_command_grounded(
    command: str,
    command_source: Optional[str],
    approved_commands_catalog: list[dict[str, Any]],
    verified_evidence: list[dict[str, Any]],
) -> bool:
    """Checks if a command is strictly grounded in the approved catalog or verified evidence.

    Supports exact commands and parameterized template commands with parameter substitution.
    """
    cmd_clean = command.strip()
    cmd_stripped = re.sub(r"\*\*|`", "", cmd_clean).strip()
    src_clean = (command_source or "").strip()

    # 1. Check approved commands catalog
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

    # 2. Check verified evidence content snippets
    for ev in verified_evidence:
        if not isinstance(ev, dict):
            continue
        ev_src = ev.get("source_id", "").strip()
        ev_snippet = ev.get("content_snippet", "")
        if src_clean and src_clean == ev_src and ev_snippet:
            if cmd_clean in ev_snippet or cmd_stripped in ev_snippet:
                return True
            template_regex = _template_to_regex(ev_snippet)
            if template_regex and (template_regex.search(cmd_clean) or template_regex.search(cmd_stripped)):
                return True

    return False


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
            # Check Command Grounding on the single step
            command = step.get("command")
            if command:
                approved_catalog = request_payload.get("approved_commands_catalog", [])
                cmd_source = step.get("command_source")
                if not is_command_grounded(command, cmd_source, approved_catalog, verified_evidence):
                    logger.warning(
                        "Technical Authority Engineer command stripped: ungrounded command %r (source %r)",
                        command,
                        cmd_source,
                    )
                    step_copy = dict(step)
                    step_copy["command"] = None
                    step_copy["command_source"] = None
                    restrictions = list(step_copy.get("restrictions") or [])
                    restrictions.append("[Command stripped: unapproved operational command]")
                    step_copy["restrictions"] = restrictions
                    sanitized["diagnostic_step"] = step_copy
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
