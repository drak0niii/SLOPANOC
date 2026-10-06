"""Run-scoped governed-knowledge DIAGNOSTIC trace: what every `knowledge_search`
searched for and returned (identity, rank, scores, applicability, lifecycle), what was
explicitly selected, and every command-authority decision -- enough to reconstruct a
troubleshooting turn after the fact.

OBSERVABILITY ONLY -- NEVER EVIDENCE. Nothing in this module is read by selection,
provenance, applicability, the TAE evidence envelope, or command authority. Trusted
evidence stays in `runtime.KnowledgeRunEvidenceState`; this module only mirrors
identities and decisions. No section body content, source URI, or credential is ever
recorded (titles, headings and identities only; query text is bounded).

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
        "query_text": query_text[:_MAX_QUERY_CHARS],
        "limit": limit,
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else str(as_of),
        "applicability_context": {k: list(v) for k, v in (applicability_context or {}).items()},
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
        entry["user_request_text"] = str(contract.get("user_request_text") or "")[:_MAX_QUERY_CHARS]
        entry["diagnostic_objective"] = str(entry.get("diagnostic_objective") or "")[:_MAX_QUERY_CHARS]
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
    selected = {tuple(i) for i in selected_identities}
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
                        if (r["knowledge_id"], r["version_label"], r["section_id"]) in selected
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
    return snapshot


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
    base = dict(existing) if isinstance(existing, dict) else {}
    base.pop(turn_id, None)
    base[turn_id] = snapshot
    while len(base) > MAX_PERSISTED_TURNS:
        base.pop(next(iter(base)))
    return base


def _fmt(value: Any) -> str:
    return "-" if value is None else (f"{value:.3f}" if isinstance(value, float) else str(value))


def format_diagnostic_trace(snapshot: dict[str, Any]) -> str:
    """Human-readable rendering for logs (identities, scores and decisions only)."""
    lines = [f"RUN {snapshot.get('run_id')}"]
    for r in snapshot.get("turn_requests", []):
        lines.append(
            f"REQUEST focus={r.get('focus')} kind={r.get('request_kind') or 'operational'} "
            f"explicit={r.get('explicit_in_current_message')} text={r.get('user_request_text')!r}"
        )
        lines.append(f"  objective: {r.get('diagnostic_objective')!r}")
        thread = r.get("troubleshooting_thread") or {}
        if thread:
            lines.append(
                f"  THREAD {thread.get('decision')} active={thread.get('active_fault_id')}"
                f" previous={thread.get('previous_fault_id') or '-'} subject={thread.get('subject_component')!r}"
            )
        if r.get("active_investigation_objective"):
            lines.append(f"  background: {r.get('active_investigation_objective')!r}")
        for d in r.get("discarded_fields") or []:
            lines.append(f"  discarded {d.get('field')}={d.get('value')!r} ({d.get('reason')})")
    for s in snapshot.get("searches", []):
        lines.append(
            f"SEARCH {s.get('search_index')} status={s.get('status')} mode={s.get('retrieval_mode', '-')}"
            f" (configured={s.get('configured_mode', '-')}, dense={s.get('dense_status', '-')})"
            f" eligible={s.get('eligible_section_count', '-')} candidates={s.get('candidate_count', '-')}"
            f" limit={s.get('limit')}"
        )
        lines.append(f"  query: {s.get('query_text')!r}")
        if s.get("applicability_context"):
            lines.append(f"  applicability_context: {s['applicability_context']}")
        if s.get("error"):
            lines.append(f"  error: {s['error']}")
        for r in s.get("results", []):
            lines.append(
                f"  {r['rank']}. {r['knowledge_id']} / {r['version_label']} / {r['section_id']}"
                f" [{r.get('selection_state', 'AVAILABLE')}] title={r.get('title')!r} heading={r.get('section_heading')!r}"
            )
            lines.append(
                f"     relevance={_fmt(r.get('relevance_score'))} sparse={_fmt(r.get('sparse_score'))}"
                f"(#{_fmt(r.get('sparse_rank'))}) dense={_fmt(r.get('dense_similarity'))}(#{_fmt(r.get('dense_rank'))})"
                f" applicability={r.get('applicability_outcome')}"
                f"{' unresolved=' + ','.join(r['unresolved_applicability_dimensions']) if r.get('unresolved_applicability_dimensions') else ''}"
                f" lifecycle={r.get('lifecycle_status')} derived={r.get('is_derived')} source={r.get('source_id')!r}"
            )
        for d in s.get("excluded_families", []):
            lines.append(f"  excluded family {d['knowledge_id']}: {d['reason']}")
    for sel in snapshot.get("selections", []):
        lines.append(
            f"SELECTION status={sel['status']} requested={[_key(k) for k in sel['requested']]}"
            f" accepted={[_key(k) for k in sel['accepted']]}"
        )
    lines.append(f"SELECTED {[_key(k) for k in snapshot.get('selected', [])] or 'none'}")
    for catalog in snapshot.get("action_catalogs", []):
        lines.append(f"ACTION CATALOG {len(catalog.get('actions', []))} action(s)")
        for a in catalog.get("actions", []):
            lines.append(
                f"  {a.get('action_id')} intent={a.get('intent')!r} type={a.get('action_type')}"
                f" template={a.get('command_template')!r} params={a.get('parameters')} source={a.get('source')}"
            )
            if a.get("instance_semantics"):
                scope = (a.get("condition_scope") or {}).get("alternatives") or []
                lines.append(
                    f"    SEMANTICS {a.get('instance_semantics')} line={a.get('source_line')} class={a.get('state_change_class')}"
                    f" targets={[(t.get('key'), t.get('kind'), t.get('fixed_value')) for t in a.get('targets') or []]}"
                    f" scope={[(x.get('dimension'), x.get('value')) for x in scope]} conditions={len(a.get('conditions') or [])}"
                )
        for k in catalog.get("skipped_candidates", []):
            lines.append(f"  skipped: {k.get('template')!r} semantics={k.get('semantics')} reason={k.get('reason')}")
        for u in catalog.get("unavailable_sources", []):
            lines.append(f"  unavailable: {u.get('procedure')!r} / {u.get('section')!r} reason={u.get('reason')}")
    for r in snapshot.get("action_resolutions", []):
        lines.append(
            f"ACTION SELECTED id={r.get('action_id')} intent={r.get('intent')!r} source={r.get('source')}"
            f"{' (model command ignored: ' + repr(r['model_command_ignored']) + ')' if r.get('model_command_ignored') else ''}"
        )
        lines.append(f"  RESOLUTION status={r.get('status')} reason={r.get('reason')}")
        for prm in r.get("parameters") or []:
            lines.append(f"  PARAMETER {prm.get('name')}={str(prm.get('state')).upper()}({prm.get('value') or '-'}) {prm.get('detail') or ''}".rstrip())
        if r.get("rendered_command"):
            lines.append(f"  COMMAND RENDERED {r['rendered_command']!r}")
        else:
            lines.append("  COMMAND NOT RENDERED")
        lines.append(f"  AUTHORITY {str(r.get('command_authority') or 'not_evaluated').upper()}")
    for e in snapshot.get("operational_events", []):
        if e.get("stage") == "routing":
            lines.append(
                f"ROUTING active_fault={e.get('active_fault')} active_operational_investigation={e.get('active_operational_investigation')}"
                f" turn_kind={e.get('turn_kind')} new_objective={e.get('new_objective')} result_provided={e.get('result_provided')}"
                f" clarification_answer={e.get('clarification_answer')} generic_continuation={e.get('generic_continuation')}"
                f" route_required={e.get('route_required')} selected_route={e.get('selected_route')}"
            )
            lines.append(f"  unfinished={e.get('unfinished_work')} pending={e.get('pending_step')} reason={e.get('reason')}")
            continue
        if e.get("stage") == "specialist_invocation":
            lines.append(
                f"SPECIALIST INVOCATION specialist={e.get('specialist')} forced_by_server={e.get('forced_by_server')} reason={e.get('reason')}"
            )
            continue
        if e.get("stage") == "route_enforcement":
            lines.append(f"  ROUTE ENFORCEMENT function_calling={e.get('mode')} allowed={e.get('allowed')}")
            continue
        if e.get("stage") == "structured_output":
            reason = f" reason={e.get('reason')} detail={e.get('detail')}" if e.get("reason") else ""
            lines.append(
                f"STRUCTURED OUTPUT specialist={e.get('specialist')} attempt={e.get('attempt')} phase={e.get('phase')}"
                f" status={e.get('status')}{reason}"
            )
            continue
        if e.get("stage") == "structured_output_retry":
            lines.append(
                f"STRUCTURED OUTPUT RETRY specialist={e.get('specialist')} attempt={e.get('attempt')}"
                f" tools_enabled={str(e.get('tools_enabled')).lower()} context_reused={str(e.get('context_reused')).lower()}"
                f" reason={e.get('reason')}"
            )
            continue
        if e.get("stage") == "structured_output_recovery":
            lines.append(
                f"STRUCTURED OUTPUT RECOVERY specialist={e.get('specialist')} outcome={e.get('outcome')}"
                f" attempts={e.get('attempts')} reason={e.get('reason')}"
            )
            continue
        if e.get("stage") == "plan":
            lines.append(
                f"POLICY decision={e.get('policy_decision')} reasons={e.get('reason_codes')} mode={e.get('execution_mode')}"
                f" control={e.get('control_id')} check={e.get('check_id')}"
            )
            lines.append(
                f"  TARGET required={e.get('target_confirmation_required')} confirmed={bool(e.get('confirmation_id'))}"
                f" target={(e.get('target') or {}).get('canonical_identifier')!r}"
            )
            lines.append(f"  APPROVAL id={e.get('approval_id')} status={e.get('approval_status') or 'not_requested'}")
            lines.append(f"  STAGE {e.get('stage_value') or e.get('control_stage') or '-'}")
        elif e.get("stage") == "invariant":
            if e.get("normalized"):
                lines.append(
                    f"INVARIANT no SELECTED evidence + empty action catalog: {e.get('from')} -> {e.get('to')} claims={e.get('claims')}"
                )
            else:
                lines.append("INVARIANT no SELECTED evidence + empty action catalog: manual observation only (no command)")
        elif e.get("stage") == "execution":
            lines.append(
                f"EXECUTION id={e.get('execution_id')} status={e.get('status')} adapter={e.get('adapter')} context={e.get('execution_context')}"
            )
        elif e.get("stage") == "target_gate":
            lines.append(
                f"TARGET GATE action={e.get('procedure_action_id')} operation={e.get('operation')} fault={e.get('fault_id')}"
                f" passed={e.get('passed')} declared={[(t.get('key'), t.get('kind'), t.get('fixed_value')) for t in e.get('declared_targets') or []]}"
            )
            lines.append(f"  CASE FACTS {[f.get('identity') for f in e.get('case_target_facts') or []][:20]}")
            if e.get("instance_semantics"):
                lines.append(f"  SEMANTICS {e.get('instance_semantics')} governed_conditions={e.get('governed_conditions')} resolution={e.get('resolution')}")
            check = e.get("condition_check")
            if check:
                lines.append(
                    f"  CONDITION passed={check.get('passed')} required_any_of={[(a.get('dimension'), a.get('value')) for a in check.get('required_any_of') or []]}"
                    f" established={[(a.get('dimension'), a.get('value')) for a in check.get('established') or []]}"
                )
            for t in e.get("targets") or []:
                lines.append(
                    f"  TARGET {t.get('key')} {str(t.get('status')).upper()} value={t.get('value')!r} requested={t.get('requested')}"
                    f" facts={t.get('fact_ids')} detail={t.get('detail')}"
                )
        elif e.get("stage") == "integrity":
            lines.append(
                f"INTEGRITY phase={e.get('phase')} original={e.get('original_outcome')} final={e.get('final_outcome')}"
                f" action_id={e.get('procedure_action_id')} citations={e.get('citation_count')} resolution={e.get('citation_resolution')}"
                f" structured_match={e.get('structured_identity_match')} check={e.get('check_triggered')} decision={e.get('action')}"
                f" reason={e.get('reason')}"
            )
        elif e.get("stage") == "action_choice":
            lines.append(
                f"ACTION CHOICE offered={e.get('offered')} chosen={e.get('chosen')} declined={e.get('declined')}"
                f" acquisition={e.get('acquisition_outcome')} blocking={e.get('blocking_reason')}"
            )
        elif e.get("stage") == "continuation":
            pending = e.get("pending_step") or {}
            clarification = e.get("open_applicability_clarification") or {}
            lines.append(
                f"CONTINUATION kind={e.get('kind')} fault={e.get('fault_id')} rule={e.get('rule')} source={e.get('source')}"
                f" pending={pending.get('step_id')}/{pending.get('status')} known_method={e.get('known_procedure_action_id')}"
                f" clarification_open={e.get('known_clarification_open')} available_in_run={e.get('known_source_available_in_run')}"
                f" discovery={e.get('discovery')}"
            )
            if clarification:
                lines.append(
                    f"  APPLICABILITY CLARIFICATION {clarification.get('clarification_id')} status={clarification.get('status')}"
                    f" missing={clarification.get('missing_dimensions')} answered={clarification.get('answered_dimensions')}"
                )
        elif e.get("stage") == "applicability_clarification":
            lines.append(
                f"APPLICABILITY CLARIFICATION event={e.get('event')} id={e.get('clarification_id')} fault={e.get('fault_id')}"
                f" requirement={e.get('requirement_id')} status={e.get('status')} missing={e.get('missing_dimensions')}"
                f" unresolved={e.get('unresolved_dimensions')} answered={e.get('answered_dimensions')}"
            )
            lines.append(
                f"  sources={e.get('source_identities')} step={e.get('originating_step_id')} created_run={e.get('created_run_id')}"
                f" resulting_applicability={e.get('resulting_applicability')}"
                + (f" released={e.get('released_requirements')} stale_blockers={e.get('stale_blockers')}" if e.get("event") == "settled" else "")
            )
        elif e.get("stage") in ("retrieval_resumption", "selection_discovery", "gap_recovery_discovery") and "available" in e:
            lines.append(
                f"RESUMPTION stage={e.get('stage')} reason={e.get('reason')} clarification={e.get('clarification_id')} status={e.get('status')}"
                f" query={e.get('query_text')!r} available={[(a.get('section_id'), a.get('applicability_outcome')) for a in e.get('available') or []]}"
            )
        elif e.get("stage") == "resume_procedure_action":
            lines.append(f"RESUME ACTION legacy command re-expressed as ProcedureAction id={e.get('procedure_action_id')} source={e.get('source')}")
        elif e.get("stage") == "resume_completeness":
            lines.append(f"RESUME CHOICE offered={e.get('offered')} chosen={e.get('chosen')}")
        elif e.get("stage") == "response_completeness":
            lines.append(
                f"COMPLETENESS rule={e.get('rule')} forced_route={e.get('forced_route')} required={e.get('required')} satisfied={e.get('satisfied')}"
                f" elements={e.get('elements')} valid_actions={len(e.get('valid_actions') or [])}"
            )
            pending = e.get("pending_step") or {}
            lines.append(
                f"  PENDING RESULT CANDIDATE step={pending.get('step_id')} candidate_rejected={pending.get('candidate_rejected')}"
                f" presented_pending={e.get('presented_pending_step_id')} consumed_this_turn={e.get('consumed_step_ids')}"
            )
        elif e.get("stage") == "result_binding":
            lines.append(
                f"RESULT BINDING fault={e.get('fault_id')} step={e.get('step_id')} turn_kind={e.get('turn_kind')} binding={e.get('binding')}"
                f" result_source={e.get('result_source')} old_status={e.get('old_status')} new_status={e.get('new_status')}"
                f" consumed={e.get('consumed')} validation={e.get('validation')}"
            )
            lines.append(
                f"  pending_before={e.get('pending_before')} pending_after_binding={e.get('pending_after_binding')}"
                f" consumed_step_ids={e.get('consumed_step_ids')} known_result_step={e.get('known_result_step_id')}"
            )
        elif e.get("stage") == "continuation_state":
            lines.append(
                f"CONTINUATION STATE fault={e.get('fault_id')} consumed={e.get('consumed_step_ids')}"
                f" pending_after_binding={e.get('pending_after_binding')} next_step={e.get('next_step_id')}"
                f" pending_now={e.get('pending_now')} phase={e.get('progression_phase')}"
            )
        elif e.get("stage") == "progression":
            pending = e.get("pending_step") or {}
            lines.append(
                f"PROGRESSION fault={e.get('fault_id')} turn={e.get('turn_kind')} decision={e.get('decision')} phase={e.get('phase')}"
                f" pending={pending.get('step_id')}/{pending.get('status')}"
            )
            for r in e.get("requirements") or []:
                gap = r.get("gap") or {}
                lines.append(
                    f"  REQUIREMENT {r.get('requirement_id')} status={r.get('status')} blocking={r.get('blocking_reason')}"
                    f" gap={gap.get('gap_id')} gap_status={gap.get('status')} gap_reason={gap.get('reason')} attempts={gap.get('attempts')}"
                )
        elif e.get("stage") == "gap_recovery":
            lines.append(
                f"GAP RECOVERY requirement={e.get('requirement_id')} gap={e.get('gap_id')} reason={e.get('gap_reason')}"
                f" repeated={e.get('repeated')} discovery={e.get('discovery')} offered={e.get('offered')}"
                f" alternatives={[a.get('procedure_action_id') for a in e.get('alternatives') or []]} excluded={e.get('excluded')}"
            )
            lines.append(f"  NEXT chosen={e.get('chosen')} issued={e.get('chosen_issued')} terminal={e.get('terminal')}")
        elif e.get("stage") == "acquisition_discovery":
            lines.append(
                f"ACQUISITION DISCOVERY reason={e.get('reason')} status={e.get('status')} query={e.get('query_text')!r}"
                f" available={len(e.get('available') or [])}"
            )
        elif e.get("stage") == "acquisition_continuity":
            blocked = e.get("blocked_action") or {}
            lines.append(
                f"ACQUISITION rule={e.get('rule')} requirement={e.get('requirement_id')} candidate={e.get('acquisition_candidate_id')}"
                f" action={e.get('procedure_action_id')} blocked={blocked.get('procedure_action_id')}"
                f" search={e.get('governed_search_performed')} selected={e.get('selected_sources')}"
                f" applicability={e.get('known_source_applicability')} remediation={e.get('remediation')}"
            )
            lines.append(
                f"  OUTCOME {e.get('outcome')} reconstruction={e.get('reconstruction')} authority={e.get('authority_decision')}"
                f" decision={e.get('progression_decision')} presented={e.get('command_presented')}"
            )
    for c in snapshot.get("command_authority", []):
        lines.append(
            f"COMMAND {c['decision'].upper()} stage={c['stage']} command={c['command']!r} source={c['source_id']!r} reason={c['reason']}"
        )
    decision = snapshot.get("completeness_decision")
    if isinstance(decision, dict):
        lines.append(
            f"COMPLETENESS DECISION decision={decision.get('decision')} pending_result_candidate={decision.get('pending_result_candidate')}"
            f" candidate_step={decision.get('candidate_step_id')} consumed_this_turn={decision.get('consumed_this_turn')}"
            f" final_producer={decision.get('final_producer')}"
        )
    egress = snapshot.get("command_egress")
    if egress:
        lines.append(
            f"EGRESS producer={egress.get('producer')} decision={egress.get('decision')} removed_units={egress.get('removed_units')}"
            f" authorizations={[a.get('procedure_action_id') for a in egress.get('authorizations') or []]}"
        )
        for c in egress.get("candidates") or []:
            lines.append(
                f"  CANDIDATE {c.get('candidate')!r} {str(c.get('decision')).upper()} reason={c.get('reason')}"
                f" action={c.get('procedure_action_id')} run={c.get('authorization_run_id')}"
            )
        for r in egress.get("rejected_authorizations") or []:
            lines.append(f"  AUTHORIZATION REJECTED {r.get('command')!r} reason={r.get('reason')} run={r.get('run_id')} fault={r.get('fault_id')}")
    tae = snapshot.get("technical_authority")
    if tae:
        lines.append(
            f"TAE outcome={tae.get('outcome')} command={tae.get('command')!r} source={tae.get('command_source')!r}"
            f" selection_contract={tae.get('selection_contract')}"
        )
    if snapshot.get("dropped_entries"):
        lines.append(f"(dropped {snapshot['dropped_entries']} entries over caps)")
    return "\n".join(lines)


def _key(k: dict[str, Any]) -> str:
    return f"{k.get('knowledge_id')}/{k.get('version_label')}/{k.get('section_id')}"
