"""Technical Authority Engineer structured-output recovery: ONE bounded regeneration.

Live defect (Prompt 4 acceptance, session 84f6074b, turn 3): the operator supplied the result of the
presented step, the server forced the governed route and invoked the specialist exactly once; the
specialist searched governed knowledge and then returned an EMPTY final response. `validate_schema`
raised `Invalid JSON: EOF ... input_value=''`, no validated execution record was produced, and the
forced turn failed closed -- a serialization failure, not an operational outcome.

Invariant:
    the specialist's reasoning / tool phase runs ONCE; when its final structured answer is
    structurally unusable (empty, whitespace-only, no payload, invalid or truncated JSON, rejected by
    the response schema) the server asks for that answer ONCE more, in the SAME specialist session
    (same run, request contract, tool results, selection and issued catalog), with NO tools declared.
    max structured-output attempts per TAE invocation = MAX_STRUCTURED_OUTPUT_ATTEMPTS (2).

A VALID structured response is never regenerated, whatever its outcome (insufficient_evidence,
escalation_required, error, a recommendation that authority later rejects): those are operational
outcomes. A regenerated response has zero authority of its own -- it passes the same integrity
callback, ProcedureAction resolution, Command Authority, progression and egress as a first answer.
A second structural failure fails closed through the existing error boundary.
"""
from __future__ import annotations

import json
import logging
import threading
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)

MAX_STRUCTURED_OUTPUT_ATTEMPTS = 2
"""Attempt 1 plus at most ONE regeneration per specialist invocation. Never a loop."""

STRUCTURED_OUTPUT_RECORD_KEY = "structured_output"
"""Execution-record key: attempts / regeneration / outcome of the final structured answer (server-owned)."""

# Server-owned markers of the integrity callback's structural fallbacks (validation.py): the model's
# text was not a JSON object, so the callback replaced it with a safe ERROR response. Classified as
# the structural failure they stand for, never as a model-chosen ERROR outcome.
MALFORMED_JSON_FALLBACK_DETAIL = "Deterministic safety boundary: malformed JSON response intercepted."
NON_OBJECT_JSON_FALLBACK_DETAIL = "Deterministic safety boundary: non-dict JSON response intercepted."


class StructuredOutputFailureReason(str, Enum):
    EMPTY_RESPONSE = "empty_response"
    NO_STRUCTURED_PAYLOAD = "no_structured_payload"
    MALFORMED_JSON = "malformed_json"
    TRUNCATED_JSON = "truncated_json"
    SCHEMA_INVALID = "schema_invalid"


@dataclass(frozen=True)
class StructuredOutputFailure:
    reason: StructuredOutputFailureReason
    detail: str
    """Bounded diagnostic (error class / position); never model text."""


def final_output_text(content: Any) -> str:
    """The text the server validates: every non-thought text part, joined exactly as the validator does."""
    parts = getattr(content, "parts", None) or []
    return "\n".join(p.text for p in parts if getattr(p, "text", None) and not getattr(p, "thought", False))


def classify_structured_output(content: Any, output_schema: Any) -> Optional[StructuredOutputFailure]:
    """Deterministic structural check of a final specialist response. None when it is a valid
    structured response (whatever its outcome); otherwise the structural failure."""
    from google.adk.utils._schema_utils import validate_schema

    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return StructuredOutputFailure(StructuredOutputFailureReason.NO_STRUCTURED_PAYLOAD, "no content parts")
    text = final_output_text(content)
    if not text.strip():
        tool_parts = any(getattr(p, "function_call", None) or getattr(p, "function_response", None) for p in parts)
        detail = "whitespace-only text" if text else "no text after tool activity" if tool_parts else "empty text"
        return StructuredOutputFailure(StructuredOutputFailureReason.EMPTY_RESPONSE, detail)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        truncated = exc.pos >= len(text.rstrip()) or exc.msg.startswith("Unterminated")
        reason = StructuredOutputFailureReason.TRUNCATED_JSON if truncated else StructuredOutputFailureReason.MALFORMED_JSON
        return StructuredOutputFailure(reason, f"{exc.msg} at char {exc.pos}")
    if not isinstance(payload, dict):
        return StructuredOutputFailure(StructuredOutputFailureReason.SCHEMA_INVALID, f"JSON {type(payload).__name__}, not an object")
    if payload.get("outcome") == "error" and payload.get("detail") == MALFORMED_JSON_FALLBACK_DETAIL:
        return StructuredOutputFailure(StructuredOutputFailureReason.MALFORMED_JSON, "integrity boundary intercepted malformed JSON")
    if payload.get("outcome") == "error" and payload.get("detail") == NON_OBJECT_JSON_FALLBACK_DETAIL:
        return StructuredOutputFailure(StructuredOutputFailureReason.SCHEMA_INVALID, "integrity boundary intercepted a non-object JSON")
    if output_schema is not None:
        try:
            validate_schema(output_schema, text)
        except Exception as exc:  # pydantic ValidationError or equivalent
            count = exc.error_count() if hasattr(exc, "error_count") else 1
            return StructuredOutputFailure(StructuredOutputFailureReason.SCHEMA_INVALID, f"{type(exc).__name__} ({count} error(s))")
    return None


REGENERATION_INSTRUCTION = (
    "SERVER STRUCTURED-OUTPUT RECOVERY: your previous final structured response could not be parsed ({reason}).\n"
    "Do not perform new investigation. Do not call tools. Do not introduce new evidence. Do not introduce new actions.\n"
    "Using only the validated context already supplied in this evaluation -- the request above, the tool results "
    "already returned in this run, and the server context below -- return one complete TechnicalAuthorityResponse "
    "conforming exactly to the required schema.\n"
    "SERVER CONTEXT (identities only; it confers no authority -- every field is re-validated by the server):\n{context}"
)


def regeneration_context(run_id: Optional[str]) -> dict[str, Any]:
    """Server-owned, current-run context for the regeneration: what THIS run already searched,
    selected and issued (identities only). Never model prose, never reasoning."""
    from backend.agents.technical_authority_engineer.procedure_actions import issued_actions
    from backend.cases.evidence_identity import identity_of
    from backend.tools.knowledge.runtime import (
        has_explicit_empty_knowledge_selection,
        has_knowledge_run_state,
        snapshot_selected_knowledge_evidence,
    )

    if not run_id:
        return {"governed_search_performed": False, "selected_evidence": [], "issued_procedure_actions": []}
    selected = [i.selection_key() for i in (identity_of(item) for item in snapshot_selected_knowledge_evidence(run_id)) if i]
    actions = [
        {
            "procedure_action_id": a.action_id,
            "intent": a.intent,
            "action_type": getattr(a.action_type, "value", a.action_type),
            "source": {"knowledge_id": a.source.knowledge_id, "version_label": a.source.version_label, "section_id": a.source.section_id},
        }
        for a in issued_actions(run_id)
    ]
    return {
        "governed_search_performed": has_knowledge_run_state(run_id),
        "selected_evidence": selected,
        "explicit_empty_selection": has_explicit_empty_knowledge_selection(run_id) and not selected,
        "issued_procedure_actions": actions,
    }


def regeneration_instruction(failure: StructuredOutputFailure, run_id: Optional[str]) -> str:
    return REGENERATION_INSTRUCTION.format(
        reason=failure.reason.value, context=json.dumps(regeneration_context(run_id), sort_keys=True)
    )


# ---------------------------------------------------------------------------------------------
# Process-lifetime counters (log / diagnostics only; no telemetry stack)
# ---------------------------------------------------------------------------------------------

_metrics_lock = threading.Lock()
_metrics: Counter[str] = Counter()
_failure_reasons: Counter[str] = Counter()


def structured_output_metrics() -> dict[str, Any]:
    with _metrics_lock:
        return {
            "tae_structured_output_attempts": _metrics["attempts"],
            "tae_structured_output_retry_count": _metrics["retry_count"],
            "tae_structured_output_retry_success": _metrics["retry_success"],
            "tae_structured_output_retry_failure": _metrics["retry_failure"],
            "failure_reason": dict(_failure_reasons),
        }


def reset_structured_output_metrics() -> None:
    with _metrics_lock:
        _metrics.clear()
        _failure_reasons.clear()


def _bump(name: str, reason: Optional[str] = None) -> None:
    with _metrics_lock:
        _metrics[name] += 1
        if reason:
            _failure_reasons[reason] += 1


def _trace(entry: dict[str, Any]) -> None:
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event(entry)
    except Exception:
        pass


class StructuredOutputRecovery:
    """Per-invocation budget and record of the specialist's final structured output. The budget is
    shared by every check of this invocation: at most MAX_STRUCTURED_OUTPUT_ATTEMPTS - 1 regenerations."""

    def __init__(self, specialist: str, run_id: Optional[str]) -> None:
        self.specialist = specialist
        self.run_id = run_id
        self.regenerations = 0
        self.first_failure: Optional[StructuredOutputFailure] = None
        self.outcome: Optional[str] = None

    @property
    def attempt(self) -> int:
        return 1 + self.regenerations

    @property
    def can_regenerate(self) -> bool:
        return self.regenerations < MAX_STRUCTURED_OUTPUT_ATTEMPTS - 1

    def observe(self, failure: Optional[StructuredOutputFailure], *, phase: str) -> None:
        """Records one evaluated final structured output. `phase`: initial (the first final answer),
        final (a later remediation message's answer, recorded only when invalid) or regeneration."""
        _bump("attempts", failure.reason.value if failure else None)
        _trace({
            "stage": "structured_output", "specialist": self.specialist, "attempt": self.attempt, "phase": phase,
            "status": "valid" if failure is None else "invalid",
            "reason": failure.reason.value if failure else None, "detail": failure.detail if failure else None,
        })
        if failure is not None:
            self.first_failure = self.first_failure or failure
            logger.warning(
                "structured_output specialist=%s run_id=%s attempt=%d phase=%s status=invalid reason=%s detail=%s",
                self.specialist, self.run_id, self.attempt, phase, failure.reason.value, failure.detail,
            )
        if phase == "regeneration":
            if failure is None:
                self.outcome = "recovered"
                _bump("retry_success")
            else:
                _bump("retry_failure")

    def begin_regeneration(self, failure: StructuredOutputFailure) -> None:
        if not self.can_regenerate:  # defensive: callers check first; the budget is never exceeded
            raise RuntimeError("structured-output regeneration budget exhausted")
        self.regenerations += 1
        _bump("retry_count")
        _trace({"stage": "structured_output_retry", "specialist": self.specialist, "attempt": self.attempt,
                "tools_enabled": False, "context_reused": True, "reason": failure.reason.value})
        logger.warning(
            "structured_output_retry specialist=%s run_id=%s attempt=%d tools_enabled=false context_reused=true reason=%s",
            self.specialist, self.run_id, self.attempt, failure.reason.value,
        )

    def fail_closed(self, failure: StructuredOutputFailure) -> None:
        """The final output is still structurally invalid and no regeneration remains."""
        if self.outcome == "fail_closed":
            return
        self.outcome = "fail_closed"
        _trace({"stage": "structured_output_recovery", "specialist": self.specialist, "outcome": "fail_closed",
                "attempts": self.attempt, "reason": failure.reason.value})
        logger.warning("structured_output_recovery specialist=%s run_id=%s outcome=fail_closed attempts=%d reason=%s",
                       self.specialist, self.run_id, self.attempt, failure.reason.value)

    def summary(self) -> dict[str, Any]:
        """Server-owned execution-record view (no authority)."""
        return {
            "attempts": self.attempt,
            "retry_count": self.regenerations,
            "retry_reason": self.first_failure.reason.value if self.first_failure and self.regenerations else None,
            "outcome": self.outcome or "valid",
        }
