"""Concrete, ADK-compatible tools exposed to Incident Manager:
`knowledge_search` and `knowledge_select_evidence`. Both are deterministic
adapters over the frozen Generic KM stack (5.1F-5.1I) -- neither performs
any LLM reasoning, currentness/applicability/relevance logic, or
provenance construction itself; see backend/tools/knowledge/__init__.py
and backend/knowledge/tools/__init__.py for the full architecture this
composes.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from google.adk.tools import ToolContext
from pydantic import ValidationError

from backend.api.activity_queue import ActivityKind, report_activity
from backend.api.turn_context import current_run_id
from backend.gateway.safe_error import internal_error, validation_error
from backend.knowledge.provenance.contracts import KnowledgeEvidenceSelectionError, KnowledgeEvidenceSelectionKey
from backend.knowledge.repository.contracts import KnowledgeRepositoryCorruptionError
from backend.knowledge.tools.contracts import KnowledgeSearchToolRequest
from backend.tools.knowledge.runtime import (
    KnowledgeRuntimeError,
    get_knowledge_tool_service,
    get_or_init_run_state,
    record_search_result,
    select_evidence,
)

_perf_logger = logging.getLogger("backend.perf")
"""A5 live UI corrective pass -- observability gap closed after a real,
multi-pass diagnostic effort proved the existing logs could not
distinguish "knowledge_search genuinely found nothing" from "knowledge_
search silently ran against the wrong database" (both looked identical:
no `provenance_compliance: available governed evidence...` warning ever
fires when `item_count == 0`, indistinguishable from a completely
different-but-also-zero-result cause without this line). Identity/count
only -- never document bodies, never credentials, never a raw connection
string (this module never even sees one; `get_knowledge_tool_service()`'s
own repository owns that, and never logs it either -- see settings.py's
own `resolve_knowledge_database_url` docstring, "Never log the result").
"""

_UNBOUND_RUN_KEY = "knowledge-tools::unbound-run"
"""Fallback key used only OUTSIDE a `chat_service.py`-driven turn (e.g. a
standalone `adk run`/`adk web` local debugging session, or a direct test
call with no `tool_context`) -- `current_run_id()` is `None` there because
no real per-turn run identity was ever bound (see turn_context.py's own
docstring). Every real production/test turn through `chat_service.py`
binds a fresh, unique `run_id` before the Runner starts, so this constant
is never reached in that path and cross-run leakage between two real
turns remains structurally impossible either way.
"""


def _run_key() -> str:
    return current_run_id() or _UNBOUND_RUN_KEY


def _validation_error_result(message: str) -> dict[str, Any]:
    return {"error": validation_error(message).safe_error.to_dict()}


def _internal_error_result(message: str) -> dict[str, Any]:
    return {"error": internal_error(message).safe_error.to_dict()}


async def knowledge_search(
    query_text: str,
    limit: int = 5,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Search governed, provenance-validated knowledge (procedures,
    technical instructions, historical references, KB content, and
    similar) for content relevant to `query_text`. Returns current,
    applicability-evaluated, ranked evidence -- never fabricated, never
    unbounded.

    Use this when authoritative documented knowledge may materially help
    answer the incident/operational question at hand. It is valid to use
    this, Teams tools, both, or neither, depending on the problem -- there
    is no requirement to call this on every turn. If it returns no
    result, say so; never invent knowledge to fill the gap.

    `relevance_score` on each returned item is a lexical relevance signal
    only -- NOT a confidence, correctness, or authority score.
    `applicability_outcome` of `PARTIAL_MATCH` or `UNKNOWN` means
    applicability to the current situation is not fully proven; do not
    treat it as equivalent to `MATCH`.

    If your final answer materially relies on any returned item, call
    `knowledge_select_evidence` with the exact `selection_key` values of
    the items actually relied upon before producing that answer. Do not
    select an item merely because it was returned, and never invent a
    selection key that was not part of this tool's own result.

    Args:
      query_text: What to search for, in your own words. Never include
        operational facts you want applied as a filter -- this tool
        takes no such parameter.
      limit: Maximum number of evidence items to return (default 5,
        maximum 10). An out-of-range value is rejected, not clamped.
      tool_context: Auto-injected by ADK in real use (never supplied by
        the model). Used only to read the trusted run identity this
        search belongs to -- see `backend.api.turn_context.current_run_id`.

    Returns:
      On success, the safe, JSON-serializable fields of a
      `KnowledgeSearchAgentPayload` (`items`, `diagnostics`) -- never a
      trusted `KnowledgeEvidenceSet`, repository object, or `source_uri`.
      An empty `items` list is a normal, successful "nothing matched"
      result, never an error. On failure, a dict with a single `error`
      key holding a SafeError.
    """
    try:
        request = KnowledgeSearchToolRequest(query_text=query_text, limit=limit)
    except ValidationError as exc:
        return _validation_error_result(str(exc.errors()[0]["msg"]) if exc.errors() else "Invalid knowledge_search request.")

    run_id = _run_key()
    run_state = get_or_init_run_state(run_id)
    service = get_knowledge_tool_service()

    # Phase 2 (Runtime Activity Truthfulness): reported unconditionally --
    # this call genuinely begins retrieval work regardless of what it
    # returns. `report_activity` itself derives the run identity from
    # `current_run_id()` (never this function's own `_run_key()` fallback,
    # which may legitimately point at the unbound-run sentinel for a
    # standalone/test invocation -- `report_activity` safely no-ops in
    # that case, exactly as intended).
    report_activity(ActivityKind.KNOWLEDGE_SEARCH_STARTED)

    try:
        execution = await service.search(request, run_state.execution_context)
        record_search_result(run_id, execution)
    except KnowledgeRepositoryCorruptionError:
        _perf_logger.info("perf stage=knowledge_search_complete run_id=%s status=error error=repository_corruption", run_id)
        report_activity(ActivityKind.KNOWLEDGE_SEARCH_FAILED)
        return _internal_error_result("Governed knowledge could not be read right now. Please try again.")
    except KnowledgeRuntimeError:
        _perf_logger.info("perf stage=knowledge_search_complete run_id=%s status=error error=runtime_error", run_id)
        report_activity(ActivityKind.KNOWLEDGE_SEARCH_FAILED)
        return _internal_error_result("Governed knowledge could not be reconciled for this request. Please try again.")

    item_count = len(execution.agent_payload.items)
    _perf_logger.info(
        "perf stage=knowledge_search_complete run_id=%s status=success item_count=%d",
        run_id,
        item_count,
    )
    # `document_count` -- the SAME allowlisted metadata key `run_trace.py`
    # already reserves for this exact concept -- a safe, bounded count,
    # never the items themselves. Reported even when 0: a zero-result
    # search is still a genuine, successful completion of the search
    # operation (instruction section 12) -- the STATUS TRANSLATOR, not
    # this call site, is responsible for never phrasing a 0-count success
    # as "reviewing" anything.
    report_activity(ActivityKind.KNOWLEDGE_SEARCH_SUCCEEDED, {"document_count": item_count})
    return execution.agent_payload.model_dump(mode="json")


async def knowledge_select_evidence(
    selections: list[KnowledgeEvidenceSelectionKey],
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Declare which specific governed-knowledge evidence items -- by
    their exact `selection_key` from a prior `knowledge_search` result --
    your final answer actually relied upon.

    Each entry accepts ONLY `knowledge_id`, `version_label`, and
    `section_id`, copied verbatim from a `selection_key` this run's own
    `knowledge_search` already returned -- never invented, never from a
    different conversation, never a value you construct yourself. An
    unknown, fabricated, or otherwise-unavailable-this-turn identity
    fails the ENTIRE call; nothing is partially accepted.

    Do not call this for evidence you did not end up relying on. If you
    never call this at all, that is fine -- it simply means no evidence
    is recorded as having been used.

    Args:
      selections: The exact selection keys of the evidence items relied
        upon. May be empty (a valid no-op).
      tool_context: Auto-injected by ADK in real use (never supplied by
        the model).

    Returns:
      On success, `{"status": "accepted", "selected": [...]}` where each
      entry is the plain `knowledge_id`/`version_label`/`section_id` of
      one newly-accepted selection -- never the full evidence content,
      source, or any other field. On failure (any unknown/fabricated/
      unavailable identity), a dict with a single `error` key holding a
      SafeError; the run's trusted selected-evidence state is left
      completely unchanged.
    """
    run_id = _run_key()
    report_activity(ActivityKind.KNOWLEDGE_EVIDENCE_SELECTION_STARTED)

    # Verified against a real live ADK+Gemini tool call (5.1J smoke test):
    # `google.adk.tools.function_tool.FunctionTool._preprocess_args` only
    # auto-converts a parameter whose OWN annotation is directly a
    # Pydantic `BaseModel` (or `Optional[BaseModel]`) -- it does not
    # descend into `list[BaseModel]`, so each entry of `selections`
    # arrives here as a plain dict from a real model-driven call, even
    # though the generated schema itself is correctly shaped. This
    # normalizes each entry through the SAME closed
    # `KnowledgeEvidenceSelectionKey` contract (`extra="forbid"`,
    # non-blank fields) -- it does not relax or bypass validation, it
    # only performs it explicitly where ADK does not do so for us. Real
    # `KnowledgeEvidenceSelectionKey` instances (e.g. from a direct
    # Python caller/test) pass through unchanged.
    try:
        selection_keys = [
            item if isinstance(item, KnowledgeEvidenceSelectionKey) else KnowledgeEvidenceSelectionKey.model_validate(item)
            for item in selections
        ]
    except ValidationError as exc:
        report_activity(ActivityKind.KNOWLEDGE_EVIDENCE_SELECTION_FAILED)
        return _validation_error_result(str(exc.errors()[0]["msg"]) if exc.errors() else "Invalid selection entry.")

    try:
        validated = select_evidence(run_id, selection_keys)
    except KnowledgeEvidenceSelectionError:
        report_activity(ActivityKind.KNOWLEDGE_EVIDENCE_SELECTION_FAILED)
        return _validation_error_result(
            "One or more selected evidence identities were not part of this run's own knowledge_search results."
        )

    report_activity(ActivityKind.KNOWLEDGE_EVIDENCE_SELECTION_SUCCEEDED, {"evidence_count": len(validated)})
    return {
        "status": "accepted",
        "selected": [
            {
                "knowledge_id": item.reference.knowledge_id,
                "version_label": item.reference.version_label,
                "section_id": item.reference.section_id,
            }
            for item in validated
        ],
    }
