"""Run-scoped governed-knowledge DIAGNOSTIC trace: what every `knowledge_search`
searched for and returned (identity, rank, scores, applicability, lifecycle), what was
explicitly selected, and every command-authority decision -- enough to reconstruct a
troubleshooting turn after the fact.

OBSERVABILITY ONLY -- NEVER EVIDENCE. Nothing in this module is read by selection,
provenance, applicability, the TAE evidence envelope, or command authority. Trusted
evidence stays in `runtime.KnowledgeRunEvidenceState`; this module only mirrors
identities and decisions. No section body content, source URI, or credential is ever
recorded Only classified pseudonymous identities and finite decision codes survive projection.

Lifecycle mirrors the other run-scoped stores: keyed by the trusted run id
(`current_run_id()`), snapshotted then discarded in chat_service's turn-end `finally`.
The snapshot may be logged and persisted as per-turn diagnostic state; a persisted
snapshot is never re-read as evidence.
"""
from __future__ import annotations

import contextlib
import contextvars
import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from backend.api.turn_context import current_run_id
from backend.observability.diagnostic_privacy import project as safe_diagnostic

RETRIEVAL_DIAGNOSTICS_STATE_KEY = "turn_retrieval_diagnostics"
"""Session-state key for persisted per-turn snapshots (diagnostic only)."""

MAX_PERSISTED_TURNS = 20
_MAX_SEARCHES = 25
_MAX_SELECTIONS = 50
_MAX_COMMAND_DECISIONS = 50
_MAX_QUERY_CHARS = 500
_MAX_COMMAND_CHARS = 200


@dataclass
class _RunTrace:
    searches: list[dict[str, Any]] = field(default_factory=list)
    selections: list[dict[str, Any]] = field(default_factory=list)
    command_authority: list[dict[str, Any]] = field(default_factory=list)
    action_catalogs: list[dict[str, Any]] = field(default_factory=list)
    action_resolutions: list[dict[str, Any]] = field(default_factory=list)
    operational_events: list[dict[str, Any]] = field(default_factory=list)
    turn_requests: list[dict[str, Any]] = field(default_factory=list)
    dropped: int = 0


_lock = threading.Lock()
_traces: dict[str, _RunTrace] = {}


def _trace(run_id: str) -> _RunTrace:
    trace = _traces.get(run_id)
    if trace is None:
        trace = _traces[run_id] = _RunTrace()
    return trace


def _enum(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def record_search(
    *,
    query_text: str,
    limit: int,
    as_of: Any,
    applicability_context: dict[str, list[str]],
    configured_mode: str,
    execution: Any,
    diagnostics: Any = None,
) -> None:
    """Record one successful search from its `KnowledgeSearchExecutionResult` plus the
    observability-only `KnowledgeSearchRetrievalDiagnostics` (if delivered)."""
    run_id = current_run_id()
    if not run_id:
        return
    evidence_by_identity = {
        (e.reference.knowledge_id, e.reference.version_label, e.reference.section_id): e
        for e in execution.evidence_set.items
    }
    ranking = list(getattr(diagnostics, "ranking", ()) or ())
    results = []
    for rank, item in enumerate(execution.agent_payload.items, start=1):
        key = item.selection_key
        identity = (key.knowledge_id, key.version_label, key.section_id)
        evidence = evidence_by_identity.get(identity)
        diag = ranking[rank - 1] if rank - 1 < len(ranking) else None
        if diag is not None and (diag.knowledge_id, diag.version_label, diag.section_id) != identity:
            diag = None  # never misattribute scores
        results.append(
            {
                "rank": rank,
                "knowledge_id": key.knowledge_id,
                "version_label": key.version_label,
                "section_id": key.section_id,
                "title": item.title,
                "section_heading": item.section_heading,
                "section_type": evidence.section.section_type if evidence is not None else None,
                "document_type": _enum(item.document_type),
                "lifecycle_status": _enum(evidence.lifecycle_status) if evidence is not None else None,
                "applicability_outcome": _enum(item.applicability_outcome),
                "unresolved_applicability_dimensions": list(item.unresolved_applicability_dimensions or []),
                "source_system": item.source_system,
                "source_id": item.source_id,
                "is_derived": bool(evidence is not None and evidence.artifact is not None and evidence.artifact.derived),
                "relevance_score": round(item.relevance_score, 6),
                "sparse_score": diag.sparse_score if diag else None,
                "sparse_rank": diag.sparse_rank if diag else None,
                "dense_similarity": diag.dense_similarity if diag else None,
                "dense_rank": diag.dense_rank if diag else None,
                "fused_score": diag.fused_score if diag else None,
            }
        )
    entry = {
        "status": "success",
        "limit": limit,
        "applicability_dimension_count": len(applicability_context or {}),
        "configured_mode": configured_mode,
        "retrieval_mode": _enum(getattr(diagnostics, "retrieval_mode", None)),
        "dense_status": getattr(diagnostics, "dense_status", None),
        "eligible_section_count": getattr(diagnostics, "eligible_section_count", None),
        "candidate_count": getattr(diagnostics, "candidate_count", None),
        "results": results,
        "excluded_families": [
            {"knowledge_id": d.knowledge_id, "reason": _enum(d.reason)} for d in execution.agent_payload.diagnostics
        ],
    }
    _append(run_id, "searches", entry, _MAX_SEARCHES)


def record_search_failure(*, query_text: str, limit: int, error: str) -> None:
    run_id = current_run_id()
    if run_id:
        _append(
            run_id,
            "searches",
            {"status": "error", "error": error, "query_text": query_text[:_MAX_QUERY_CHARS], "limit": limit, "results": []},
            _MAX_SEARCHES,
        )


def record_selection(*, requested: Iterable[Any], status: str, accepted: Iterable[Any] = ()) -> None:
    """`status`: accepted | explicit_empty | rejected_unavailable | invalid."""
    run_id = current_run_id()
    if not run_id:
        return

    def _keys(values: Iterable[Any]) -> list[dict[str, Any]]:
        out = []
        for v in values:
            get = v.get if isinstance(v, dict) else (lambda name, _v=v: getattr(_v, name, None))
            out.append({"knowledge_id": get("knowledge_id"), "version_label": get("version_label"), "section_id": get("section_id")})
        return out

    _append(run_id, "selections", {"status": status, "requested": _keys(requested), "accepted": _keys(accepted)}, _MAX_SELECTIONS)


_authority_preview_active: contextvars.ContextVar[bool] = contextvars.ContextVar("authority_preview_active", default=False)


@contextlib.contextmanager
def authority_preview():
    """Marks Command Authority decisions made under a HYPOTHETICAL confirmed target (used only to
    decide whether to request a target confirmation) so the trace never shows them as real."""
    token = _authority_preview_active.set(True)
    try:
        yield
    finally:
        _authority_preview_active.reset(token)


def record_operational_event(entry: dict[str, Any]) -> None:
    """Policy / target-confirmation / approval / execution events (identities and codes only)."""
    run_id = current_run_id()
    if run_id:
        _append(run_id, "operational_events", dict(entry), _MAX_COMMAND_DECISIONS)


def record_command_authority(
    *, command: str, source_id: str, decision: str, reason: str, stage: str
) -> None:
    """`stage`: source | grounding | authorization. `decision`: authorized | rejected."""
    run_id = current_run_id()
    if not run_id:
        return
    if _authority_preview_active.get():
        stage = f"preview:{stage}"
        decision = f"preview_{decision}_if_target_confirmed"
    entry = {
        "stage": stage,
        "command": (command or "")[:_MAX_COMMAND_CHARS],
        "source_id": (source_id or "")[:_MAX_COMMAND_CHARS],
        "decision": decision,
        "reason": reason,
    }
    entry = safe_diagnostic(entry)
    with _lock:
        trace = _trace(run_id)
        if entry in trace.command_authority:  # the envelope is rebuilt more than once per TAE run
            return
    _append(run_id, "command_authority", entry, _MAX_COMMAND_DECISIONS)


def record_turn_request(contract: dict[str, Any]) -> None:
    """The server-built current-turn request contract (operator's latest request, focus,
    objective, discarded caller fields). Bounded text only."""
    run_id = current_run_id()
    if run_id:
        entry = {k: contract.get(k) for k in ("focus", "request_kind", "diagnostic_objective", "explicit_in_current_message", "active_investigation_objective", "discarded_fields", "troubleshooting_thread")}
        entry["user_request_text"] = contract.get("user_request_text")
        entry["diagnostic_objective"] = entry.get("diagnostic_objective")
        _append(run_id, "turn_requests", entry, _MAX_SELECTIONS)


def record_action_catalog(
    *, actions: list[dict[str, Any]], unavailable: list[dict[str, Any]], skipped: Optional[list[dict[str, Any]]] = None
) -> None:
    """One server-issued ProcedureAction catalog (identities, intents, templates, semantics; no
    section text). `skipped`: command-looking candidates that did not become actions, and why."""
    run_id = current_run_id()
    if run_id:
        entry: dict[str, Any] = {"actions": actions, "unavailable_sources": unavailable}
        if skipped:
            entry["skipped_candidates"] = skipped[:50]
        _append(run_id, "action_catalogs", entry, _MAX_SELECTIONS)


def record_action_resolution(entry: dict[str, Any]) -> None:
    """One resolution of a model-chosen action_id: selection, source gate, parameter binding
    states, rendered candidate and the Command Authority result."""
    run_id = current_run_id()
    if run_id:
        _append(run_id, "action_resolutions", dict(entry), _MAX_COMMAND_DECISIONS)


def _append(run_id: str, attr: str, entry: dict[str, Any], cap: int) -> None:
    entry = safe_diagnostic(entry)
    with _lock:
        trace = _trace(run_id)
        bucket = getattr(trace, attr)
        if len(bucket) >= cap:
            trace.dropped += 1
            return
        if attr == "searches":
            entry = {"search_index": len(bucket) + 1, **entry}
        bucket.append(entry)


def snapshot_diagnostic_trace(
    run_id: str,
    selected_identities: Iterable[tuple[str, str, Optional[str]]],
    technical_authority: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Copy of this run's trace with every search result annotated SELECTED / AVAILABLE
    from the trusted selected set (read, never modified). None when nothing was recorded."""
    selected = {tuple(safe_diagnostic({"knowledge_id":i[0],"version_label":i[1],"section_id":i[2]}).get(k) for k in ("knowledge_id","version_label","section_id")) for i in selected_identities}
    with _lock:
        trace = _traces.get(run_id)
        if trace is None and technical_authority is None:
            return None
        trace = trace or _RunTrace()
        searches = [
            {
                **s,
                "results": [
                    {
                        **r,
                        "selection_state": "SELECTED"
                        if (r.get("knowledge_id"), r.get("version_label"), r.get("section_id")) in selected
                        else "AVAILABLE",
                    }
                    for r in s.get("results", [])
                ],
            }
            for s in trace.searches
        ]
        snapshot = {
            "run_id": run_id,
            "searches": searches,
            "selections": [dict(s) for s in trace.selections],
            "selected": [
                {"knowledge_id": k, "version_label": v, "section_id": sid} for (k, v, sid) in sorted(selected, key=str)
            ],
            "command_authority": [dict(c) for c in trace.command_authority],
            "action_catalogs": [dict(c) for c in trace.action_catalogs],
            "action_resolutions": [dict(r) for r in trace.action_resolutions],
            "operational_events": [dict(e) for e in trace.operational_events],
            "turn_requests": [dict(r) for r in trace.turn_requests],
            "dropped_entries": trace.dropped,
        }
    if technical_authority is not None:
        snapshot["technical_authority"] = technical_authority
    return safe_diagnostic(snapshot)


def summarize_technical_authority(execution: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Identity/decision-only summary of the validated TAE record (no prose)."""
    if not isinstance(execution, dict):
        return None
    step = execution.get("diagnostic_step") if isinstance(execution.get("diagnostic_step"), dict) else {}
    return {
        "outcome": execution.get("outcome"),
        "command": step.get("command"),
        "command_source": step.get("command_source"),
        "approved_commands": [
            {"command": c.get("command"), "source_id": c.get("source_id"), "operation_type": c.get("operation_type")}
            for c in execution.get("approved_commands_catalog") or []
            if isinstance(c, dict)
        ],
        "selection_contract": execution.get("server_selection_contract"),
        "procedure_action_id": step.get("procedure_action_id"),
        "procedure_action_resolution": execution.get("procedure_action_resolution"),
        "applicability_clarification_dimensions": (execution.get("applicability_clarification") or {}).get("missing_dimensions")
        if isinstance(execution.get("applicability_clarification"), dict)
        else None,
        "clarification_continuity": {
            k: (execution.get("clarification_continuity") or {}).get(k) for k in ("kind", "fault_id")
        }
        if isinstance(execution.get("clarification_continuity"), dict)
        else None,
    }


def discard_diagnostic_trace(run_id: str) -> None:
    with _lock:
        _traces.pop(run_id, None)


def build_retrieval_diagnostics_delta(existing: Any, turn_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Per-turn persisted diagnostics, bounded to the most recent MAX_PERSISTED_TURNS."""
    base = {key: safe_diagnostic(value) for key,value in existing.items()} if isinstance(existing, dict) else {}
    base.pop(turn_id, None)
    base[turn_id] = safe_diagnostic(snapshot)
    while len(base) > MAX_PERSISTED_TURNS:
        base.pop(next(iter(base)))
    return base


def _fmt(value: Any) -> str:
    return "-" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))


def format_diagnostic_trace(snapshot: dict[str, Any]) -> str:
    """Counts and finite codes only on the logging surface."""
    import json
    return json.dumps(safe_diagnostic(snapshot, identities=False),sort_keys=True,separators=(',',':'))


def _key(k: dict[str, Any]) -> str:
    return f"{k.get('knowledge_id')}/{k.get('version_label')}/{k.get('section_id')}"
