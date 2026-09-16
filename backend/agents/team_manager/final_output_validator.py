"""CONTROL-PLANE-SEQ-05 -- Final Deterministic Output Validator.

THE GAP THIS CLOSES (DEF-0045's own remaining shape, confirmed by direct
code audit before writing this module -- see the audit summary in the
milestone's own final report, section 7): `enforce_execution_decision_on_
guidance` (request_execution_policy.py, UNCHANGED by this module) strips
the STRUCTURED `command`/`step.command` field(s) when `may_emit_command`
is `False`, but for the `REFERENCE_DESCRIPTION`/`OBSERVATION`/`DIAGNOSTIC_
READ` classifications it deliberately leaves `interpretation`/`next_
action`/`evidence_requested` untouched (by design -- those fields carry no
operational authority claim of their own for those classifications). If
the model ALSO happened to restate the same exact command text inside one
of those free-text fields, `render_troubleshooting_guidance` renders it
verbatim into `final_text` regardless -- a real, structurally-open leak
path `evidence.py`'s own `_scrub_narrative_operational_leaks` (LIVE-CORR-4)
cannot close, because that scrub only ever runs for evidence.py's OWN
grounding-time rejections, never for this LATER, separate Phase-B `may_
emit_command=False` decision (chat_service.py, computed well after
incident_manager has already returned).

THE FIX: ONE deterministic, no-LLM check, run on the FINAL, already-
converged `final_text` value -- the single variable EVERY response path in
`chat_service.py` funnels into before `MESSAGE_COMPLETED` (deterministic
renders, deterministic fallbacks, and the one remaining ordinary-
conversational free-text path alike) -- against `AuthorizedResponseContext
.prohibited_commands` (authorized_response.py, UNCHANGED derivation logic
here). This is section 24's own "ONE FINAL AUTHORITY BOUNDARY": no
response path is special-cased or exempted, and no second, path-specific
validator is needed, because every path already converges into the same
`final_text` variable before this function ever runs.

DELIBERATELY NOT A COMMAND PARSER: per explicit instruction ("no generic
command regex routing... no LLM-based security classification... match
known commands conservatively"), this performs a single, exact (modulo
whitespace/line-ending normalization only -- see `_normalize_for_
comparison`) substring check against ALREADY-KNOWN, ALREADY-STRUCTURED
command values -- never semantic rewriting, never case-folding (command
syntax can be case-sensitive), never parameter normalization, never
near-match/fuzzy acceptance. A response containing no known command at all
(the overwhelming majority of turns -- ordinary conversation, informational
answers, or an already-fully-authorized command) is completely unaffected;
`prohibited_commands` is empty for those, so this function's own loop never
executes.
"""
from __future__ import annotations

from typing import Optional

from backend.agents.team_manager.authorized_response import AuthorizedResponseContext
from backend.agents.team_manager.request_execution_policy import RequestExecutionDecision, command_suppression_fallback_text


def _normalize_for_comparison(text: str) -> str:
    """Section 15's own explicit, narrow allowlist of normalization --
    trims surrounding whitespace and normalizes CRLF/CR line endings to LF
    -- nothing else. Never lowercases (command syntax/parameters can be
    case-sensitive), never collapses internal whitespace, never rewrites
    or infers equivalence. Applied identically to both the known command
    value and the candidate response text, so a purely presentational
    difference (e.g. the renderer's own line-ending convention) cannot
    cause a false negative, while a genuine textual difference still
    correctly fails to match.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def validate_final_output(
    final_text: Optional[str],
    decision: RequestExecutionDecision,
    context: AuthorizedResponseContext,
) -> tuple[Optional[str], bool]:
    """THE single final boundary (section 13) -- deterministic, no LLM
    call, no natural-language parsing. Returns `(text_to_actually_send,
    passed)`: `passed=True` returns `final_text` completely unchanged
    (including `None`, a complete no-op); `passed=False` returns a safe,
    deterministic replacement -- `final_text` itself is NEVER returned in
    that case, and the rejected content is never echoed into the
    replacement (section 16's own explicit "do not leak the rejected
    content inside the explanation").

    RULE (section 14A/B/C/D, all enforced by this ONE check): `final_text`
    must not contain, as an exact (whitespace/line-ending-normalized)
    substring, ANY value in `context.prohibited_commands` -- every
    structurally-known command this turn (`AuthorizedResponseContext.
    known_commands`) that is NOT on the closed `authorized_commands`
    allowlist. When `decision.may_emit_command` is `False`,
    `prohibited_commands` is every known command (section 14A); when
    `True`, it is every known command EXCEPT the one(s) actually
    authorized (section 14B) -- both cases are the SAME single check here,
    because `build_authorized_response_context` (authorized_response.py)
    already enforces that split at construction time; this function never
    re-derives it.

    FAIL-CLOSED REPLACEMENT TEXT (section 16): reuses the EXISTING,
    unmodified `command_suppression_fallback_text(decision)` (request_
    execution_policy.py) -- the SAME deterministic, Python-authored
    fallback this codebase already uses for every other "a command must be
    withheld" shape, never a new, parallel wording. Never model-generated,
    so never itself subject to this same validation.

    `final_text=None` is a complete no-op (`None, True`) -- there is
    nothing to validate; the caller's own existing "no final text at all"
    handling further down `chat_service.py`'s own method is unaffected.
    """
    if final_text is None:
        return None, True
    prohibited = context.prohibited_commands
    if not prohibited:
        return final_text, True
    normalized_text = _normalize_for_comparison(final_text)
    for command in prohibited:
        normalized_command = _normalize_for_comparison(command)
        if normalized_command and normalized_command in normalized_text:
            return command_suppression_fallback_text(decision), False
    return final_text, True
