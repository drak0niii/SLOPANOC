"""Fault-thread ownership for troubleshooting state (one session, several faults/components).

Each fault thread is a `TroubleshootingState` READ PROJECTION (its `fault_id`, symptom summary,
subject component and diagnostic history) re-derived from the authoritative
TroubleshootingProgression on every save (`save_projections`); nothing here is read back into it:

    session_state["troubleshooting_state"]    -- the session's ACTIVE thread projection
    session_state["troubleshooting_threads"]  -- every fault's projection by fault_id

Thread resolution below only decides WHICH fault a turn works on (a new fault identity is then
registered in the progression by the controller).

Resolution (deterministic, driven by the server-built current-turn request contract):

    focus "continue" / no new subject in the latest message -> keep the active thread
    focus "switch" + an existing thread matches the new subject -> reactivate that thread
    focus "switch" + explicit new subject/target, no thread matches -> create a new thread
    focus "switch" without an explicit subject/target, no match     -> keep the active thread
    no active thread                                            -> create the first thread

Threads are never deleted; switching only changes which one is active. History used for the
turn (prior steps, recorded observations, background) belongs to the resolved thread only.
"""
from __future__ import annotations

from backend.observability.turn_trace import observe_phase
from backend.observability.tracing import Operation
from backend.observability.stages import Stage as TelemetryStage

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, MutableMapping, Optional

from backend.agents.technical_authority_engineer.turn_request import _content_tokens, _token_matches, _tokens
from backend.cases.troubleshooting_state import TroubleshootingState

ACTIVE_THREAD_STATE_KEY = "troubleshooting_state"
THREADS_STATE_KEY = "troubleshooting_threads"


class ThreadDecision(str, Enum):
    CONTINUED = "continued"
    SWITCHED_TO_EXISTING = "switched_to_existing"
    CREATED = "created"


def _parse(raw: Any) -> Optional[TroubleshootingState]:
    if not isinstance(raw, dict):
        return None
    try:
        return TroubleshootingState.model_validate(raw)
    except Exception:
        return None


def load_active_thread(state: Any) -> Optional[TroubleshootingState]:
    return _parse(state.get(ACTIVE_THREAD_STATE_KEY)) if state is not None else None


def load_threads(state: Any) -> dict[str, TroubleshootingState]:
    """All threads by fault_id. The active-thread copy is authoritative for its own fault_id."""
    threads: dict[str, TroubleshootingState] = {}
    raw = state.get(THREADS_STATE_KEY) if state is not None else None
    if isinstance(raw, dict):
        for fault_id, value in raw.items():
            parsed = _parse(value)
            if parsed is not None:
                threads[str(fault_id)] = parsed
    active = load_active_thread(state)
    if active is not None:
        threads[active.fault_id] = active
    return threads


def save_threads(state: MutableMapping[str, Any], threads: dict[str, TroubleshootingState], active_fault_id: str) -> None:
    state[THREADS_STATE_KEY] = {fid: ts.model_dump(mode="json") for fid, ts in threads.items()}
    state[ACTIVE_THREAD_STATE_KEY] = threads[active_fault_id].model_dump(mode="json")


def save_projections(state: MutableMapping[str, Any], progression: Any, active_fault_id: Optional[str] = None) -> None:
    """Write the READ projections of the authoritative TroubleshootingProgression: every fault thread
    and the session's active thread. Control stages are overlaid from the operational control
    records (the authority for control stage). Nothing here is ever read back into the progression."""
    from backend.cases.troubleshooting_progression import project_threads

    threads = project_threads(progression)
    if not threads:
        return
    controls = state.get("operational_action_controls") or {}
    for ts in threads.values():
        for rec in ts.diagnostic_history:
            stage = ((controls.get(rec.control_id) or {}) if rec.control_id else {}).get("stage")
            if stage:
                rec.control_stage = stage
    current = load_active_thread(state)
    active = next(
        (fid for fid in (active_fault_id, current.fault_id if current else None, progression.active_fault_id) if fid in threads),
        next(iter(threads)),
    )
    save_threads(state, threads, active)


def save_thread(state: MutableMapping[str, Any], ts: TroubleshootingState) -> None:
    """Persist one thread without changing which thread is active."""
    active = load_active_thread(state)
    threads = load_threads(state)
    threads[ts.fault_id] = ts
    active_fault_id = active.fault_id if active is not None else ts.fault_id
    save_threads(state, threads, active_fault_id)


def find_thread_for_check(state: Any, check_id: str) -> Optional[TroubleshootingState]:
    for ts in load_threads(state).values():
        if any(rec.check_id == check_id for rec in ts.diagnostic_history):
            return ts
    return None


def _thread_tokens(ts: TroubleshootingState) -> list[str]:
    parts = [ts.symptom_summary or "", ts.subject_component or "", ts.working_hypothesis or ""]
    parts += [rec.action for rec in ts.diagnostic_history]
    return _tokens(" ".join(parts))


def _focus_signal(contract: Any) -> list[str]:
    """Tokens that identify what the latest message is about: its explicit subject/target when
    stated, otherwise its content words."""
    explicit = [f.value for f in (getattr(contract, "subject_component", None), getattr(contract, "explicit_target", None)) if f is not None]
    if explicit:
        return _content_tokens(" ".join(explicit))
    return _content_tokens(getattr(contract, "user_request_text", "") or "")


def _match_existing(threads: list[TroubleshootingState], signal: list[str]) -> Optional[TroubleshootingState]:
    best: Optional[tuple[int, datetime, TroubleshootingState]] = None
    for ts in threads:
        tokens = _thread_tokens(ts)
        score = sum(1 for t in signal if _token_matches(t, tokens))
        if score and (best is None or (score, ts.updated_at) > (best[0], best[1])):
            best = (score, ts.updated_at, ts)
    return best[2] if best else None


def _new_thread(summary: str, subject: Optional[str], session_id: Optional[str], case_id: Optional[str]) -> TroubleshootingState:
    return TroubleshootingState(
        fault_id=f"FAULT-{uuid.uuid4().hex[:6].upper()}",
        symptom_summary=(summary or "investigation")[:200],
        subject_component=subject,
        session_id=session_id,
        case_id=case_id,
    )


@observe_phase(Operation.THREAD, TelemetryStage.THREAD_RESOLVE_STARTED, TelemetryStage.THREAD_RESOLVE_COMPLETED)
def resolve_active_thread(
    state: MutableMapping[str, Any],
    contract: Any,
    *,
    problem_statement: str = "",
    session_id: Optional[str] = None,
    case_id: Optional[str] = None,
) -> tuple[TroubleshootingState, dict[str, TroubleshootingState], ThreadDecision, Optional[str]]:
    """Returns (active thread for this turn, all threads, decision, previously active fault_id).
    Does not persist; the caller saves with `save_threads` once the turn's updates are applied."""
    threads = load_threads(state)
    active = load_active_thread(state)
    subject = getattr(getattr(contract, "subject_component", None), "value", None)

    if active is None:
        ts = _new_thread(problem_statement or getattr(contract, "user_request_text", ""), subject, session_id, case_id)
        threads[ts.fault_id] = ts
        return ts, threads, ThreadDecision.CREATED, None

    signal = _focus_signal(contract)
    if getattr(contract, "focus", "continue") != "switch" or not signal:
        return active, threads, ThreadDecision.CONTINUED, None

    others = [ts for fid, ts in threads.items() if fid != active.fault_id]
    match = _match_existing(others, signal)
    now = datetime.now(timezone.utc)
    if match is not None:
        match.updated_at = now
        return match, threads, ThreadDecision.SWITCHED_TO_EXISTING, active.fault_id
    explicit = any(getattr(contract, name, None) is not None for name in ("subject_component", "explicit_target"))
    if not explicit:
        # Opening a new fault thread needs an explicit subject/target stated in the latest message;
        # a wording-only change of focus (e.g. a clarification answer or "what does it mean?")
        # stays in the active thread.
        return active, threads, ThreadDecision.CONTINUED, None
    ts = _new_thread(getattr(contract, "user_request_text", "") or problem_statement, subject, session_id or active.session_id, case_id or active.case_id)
    threads[ts.fault_id] = ts
    return ts, threads, ThreadDecision.CREATED, active.fault_id
