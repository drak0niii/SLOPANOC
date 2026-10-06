"""Clarification continuity: information the server asked for survives across turns.

    OPERATIONAL PROGRESSION   pending TroubleshootingStep   -> awaits an observation / result
    CLARIFICATION PROGRESSION pending clarification         -> awaits requested information

The pending clarification is the fault's OPEN `OpenQuestion` with `requested_fields` on the
Case-owned TroubleshootingProgression (one server-owned record; `TroubleshootingState.
pending_clarification` is only its read projection). Its fields come from server evaluation
(unresolved applicability dimensions, unresolved procedure parameters) -- never from assistant prose.

Per operator turn, against the pending clarification of the fault the turn belongs to:
    CLARIFICATION_FOLLOW_UP  "what details should I provide?", "what is missing?" ...
                             -> rendered directly from the record: no new troubleshooting decision,
                                no governed retrieval, no specialist call, no state change
    CLARIFICATION_ANSWER     the message literally states values for requested fields
                             -> bound deterministically (applicability: governed vocabulary only);
                                still-unresolved fields are asked for again, nothing else
    OPERATIONAL              everything else: normal troubleshooting (Command Authority,
                             applicability, policy and HITL unchanged)

A clarification belongs to ONE fault: it is never read for, or answered by, another fault thread.
A clarification response is meta conversation built from trusted server state; it carries no
diagnostic step, command, governed evidence or procedure claim, which is what lets the final-answer
boundary present it without governed knowledge selection. Deterministic; no model call; no vendor,
technology or fault vocabulary.
"""
from __future__ import annotations

import logging
import re
from enum import Enum
from typing import Any, Mapping, Optional

from backend.agents.technical_authority_engineer.turn_request import _content_tokens, _tokens
from backend.cases.troubleshooting_progression import ClarificationReason, OpenQuestion, clarification_view
from backend.knowledge.domain.models import normalize_dimension_key

logger = logging.getLogger(__name__)

CLARIFICATION_CONTINUITY_KEY = "clarification_continuity"
"""Server-owned tool-result / execution-record key of a clarification meta response."""


class ClarificationTurnKind(str, Enum):
    OPERATIONAL = "operational"
    FOLLOW_UP = "clarification_follow_up"
    ANSWER = "clarification_answer"


NO_PENDING_CLARIFICATION_TEXT = (
    "No information request is currently outstanding for this investigation. "
    "Describe the fault, alarm or observation you want to analyse."
)

_PURPOSE = {
    ClarificationReason.APPLICABILITY: "confirm which governed procedure applies",
    ClarificationReason.TARGET_IDENTITY: "confirm the target",
    ClarificationReason.MISSING_PARAMETER: "complete the governed command",
    ClarificationReason.POLICY_CLARIFICATION: "apply the operational policy",
}

# A follow-up consists ONLY of meta vocabulary about the requested information (generic English;
# any other content word -- a component, operation, identifier, value -- makes it operational).
_META_CORE = frozenset(
    """detail details info information data field fields value values parameter parameters context input inputs
    missing need needed needs require required requires requirement requirements provide provided providing
    supply tell ask asked asking want wanted question questions clarification clarify""".split()
)
_META_OTHER = frozenset(
    """else exactly specifically still more other another kind sort give send share know mean meant again
    remind repeat""".split()
)
_INTERROGATIVE_START = re.compile(r"^\s*(?:and\s+|so\s+|ok(?:ay)?[,\s]+|then\s+)?(?:what|which|how|tell|remind)\b", re.IGNORECASE)
_MAX_FOLLOW_UP_CHARS = 200


def is_clarification_follow_up(text: str) -> bool:
    """True when the message only asks which information is still required (no operational content).
    "give me the command" / "what is missing on the board?" are NOT follow-ups."""
    stripped = (text or "").strip()
    if not stripped or len(stripped) > _MAX_FOLLOW_UP_CHARS:
        return False
    content = _content_tokens(stripped)
    if not content or not all(t in _META_CORE or t in _META_OTHER for t in content):
        return False
    if not any(t in _META_CORE for t in content):
        return False
    return stripped.endswith("?") or bool(_INTERROGATIVE_START.match(stripped)) or "you" in _tokens(stripped)


def _literal_spans(value: str, text: str) -> list[tuple[int, int, str]]:
    pattern = rf"(?<![0-9A-Za-z]){re.escape(value.strip())}(?![0-9A-Za-z])"
    return [(m.start(), m.end(), m.group(0)) for m in re.finditer(pattern, text, re.IGNORECASE)]


def bind_applicability_answer(
    requested_fields: list[str], text: str, vocabulary: Optional[Mapping[str, set[str]]]
) -> dict[str, list[str]]:
    """Values for requested applicability dimensions that the operator LITERALLY stated and that
    APPROVED governed metadata knows for that dimension (the governed vocabulary). A value known
    for several requested dimensions is ambiguous and binds nothing; a value overlapped by a longer
    stated value is ignored. Returns {requested field: [operator's literal values]}; never guesses
    (no vocabulary -> nothing is bound)."""
    if not vocabulary or not text or not requested_fields:
        return {}
    candidates: list[tuple[int, int, str, str]] = []  # (start, end, literal, field)
    for field in requested_fields:
        for value in vocabulary.get(normalize_dimension_key(field), ()) or ():
            if value:
                candidates.extend((s, e, lit, field) for s, e, lit in _literal_spans(value, text))
    by_span: dict[tuple[int, int], set[str]] = {}
    literal_by_span: dict[tuple[int, int], str] = {}
    for start, end, literal, field in candidates:
        by_span.setdefault((start, end), set()).add(field)
        literal_by_span[(start, end)] = literal
    bound: dict[str, list[str]] = {}
    for span, fields in sorted(by_span.items()):
        if len(fields) != 1:
            continue  # the same words name a value of several requested dimensions
        if any(o != span and o[0] <= span[0] and span[1] <= o[1] for o in by_span):
            continue  # part of a longer stated value
        field = next(iter(fields))
        literal = literal_by_span[span]
        if literal.casefold() not in {v.casefold() for v in bound.get(field, [])}:
            bound.setdefault(field, []).append(literal)
    return bound


def render_clarification_request(
    question: OpenQuestion,
    newly_resolved: Optional[Mapping[str, list[str]]] = None,
    unconfirmed: Optional[list[Any]] = None,
) -> str:
    """The outstanding information request, rendered ONLY from the server record (plus, for a
    still-unresolved field, why a value proposed for it this turn was not accepted)."""
    lines: list[str] = []
    recorded = newly_resolved if newly_resolved else None
    if recorded:
        lines.append("Recorded: " + "; ".join(f"{k} = {', '.join(v)}" for k, v in recorded.items()) + ".")
    elif question.resolved_values:
        lines.append("Already provided: " + "; ".join(f"{k} = {', '.join(v)}" for k, v in question.resolved_values.items()) + ".")
    unresolved = question.unresolved_fields
    if not unresolved:
        lines.append("All requested information has been provided.")
        return "\n".join(lines)
    lines.append(f"To {_PURPOSE.get(question.reason, 'continue the investigation')}, I still need:")
    lines.extend(f"- {name}" for name in unresolved)
    for fact in unconfirmed or []:
        dimension, value, reason = getattr(fact, "dimension", None), getattr(fact, "value", None), getattr(fact, "reason", None)
        if dimension in unresolved and value:
            lines.append(f"'{value}' was not accepted as {dimension} ({reason}); please confirm the {dimension}.")
    return "\n".join(lines)


def clarification_meta_result(
    kind: ClarificationTurnKind,
    *,
    fault_id: Optional[str],
    question: Optional[OpenQuestion],
    text: str,
    confirmed_facts: Optional[Mapping[str, list[str]]] = None,
) -> dict[str, Any]:
    """Server-built, non-operational TAE result for a clarification turn: no diagnostic step, no
    command, no evidence. `CLARIFICATION_CONTINUITY_KEY` marks it as a meta response."""
    from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityOutcome

    unresolved = question.unresolved_fields if question is not None else []
    result: dict[str, Any] = {
        "outcome": TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value,
        "technical_interpretation": None,
        "diagnostic_step": None,
        "missing_information": list(unresolved),
        "verified_evidence_citations": [],
        CLARIFICATION_CONTINUITY_KEY: {
            "kind": kind.value,
            "fault_id": fault_id,
            "clarification": clarification_view(question) if question is not None else None,
            "text": text,
        },
    }
    if question is not None and question.reason is ClarificationReason.APPLICABILITY and unresolved:
        result["applicability_clarification"] = {
            "confirmed_facts": {k: list(v) for k, v in (confirmed_facts or {}).items()},
            "missing_dimensions": list(unresolved),
            "unconfirmed_facts": [],
            "text": text,
        }
    return result


def render_clarification_meta_response(execution: Optional[Mapping[str, Any]]) -> Optional[str]:
    """Text of a validated clarification meta record, or None. Only a record with NO diagnostic
    step, NO approved command, NO governed evidence and a non-operational outcome qualifies -- any
    operational content keeps the normal governed fail-closed path."""
    from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityOutcome

    if not isinstance(execution, Mapping):
        return None
    meta = execution.get(CLARIFICATION_CONTINUITY_KEY)
    if not isinstance(meta, Mapping):
        return None
    text = meta.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    if execution.get("outcome") != TechnicalAuthorityOutcome.INSUFFICIENT_EVIDENCE.value:
        return None
    if execution.get("diagnostic_step") is not None or execution.get("approved_commands_catalog"):
        return None
    if any(isinstance(ev, Mapping) and ev.get("source_type") == "governed_knowledge" for ev in execution.get("verified_evidence") or []):
        return None
    return text


async def pending_clarification_follow_up_text(state: Mapping[str, Any], session_id: Optional[str], message_text: str) -> Optional[str]:
    """Final-answer fallback when the specialist was not consulted this turn: a follow-up question
    about the session's ACTIVE fault's pending clarification is answered from the authoritative
    progression. None (normal handling) on any other message, no pending clarification, or any
    read failure."""
    if not is_clarification_follow_up(message_text):
        return None
    from backend.agents.technical_authority_engineer.progression_repository import ProgressionRepository
    from backend.agents.technical_authority_engineer.troubleshooting_threads import load_active_thread

    try:
        snapshot = dict(state)
        active = load_active_thread(snapshot)
        if active is None:
            return None
        progression = await ProgressionRepository(snapshot, session_id=session_id).load()
        question = progression.pending_clarification(active.fault_id)
    except Exception as exc:
        logger.warning("Pending clarification lookup failed: %s", type(exc).__name__)
        return None
    return render_clarification_request(question) if question is not None else None
