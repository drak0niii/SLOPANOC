"""In-process, never-persisted, run-id-keyed store of THIS turn's
trusted `ApplicabilityContext` (A5 final corrective pass, Correction D)
-- mirrors `troubleshooting_guidance_context.py`/`multimodal_turn_
context.py`'s exact pattern.

WHY THIS EXISTS: A5's own live validation found `KnowledgeToolExecution
Context.applicability_context` was ALWAYS empty at runtime (`backend/
tools/knowledge/runtime.py`'s `get_or_init_run_state` unconditionally
constructed `ApplicabilityContext()`), so deterministic applicability
evaluation never actually participated in real retrieval -- discrimination
came entirely from lexical ranking + model reasoning. This module is the
SMALLEST bounded fix: it lets a TRUSTED fact -- one team_manager's own
model extracted from an EXPLICIT, LITERAL statement in the CURRENT user
message (`IncidentManagerRequest.known_applicability_facts`, never
inferred/assumed) -- reach the SAME, unmodified `ApplicabilityContext`/
`evaluate_applicability` machinery Phase 5.1B already built.

This is NOT Phase 6 Context Engineering: no new generic context
architecture, no cross-turn persistence, no operational-context source
beyond what the current message and the existing IncidentManagerRequest
delegation contract already carry.

LIFECYCLE, mirroring `troubleshooting_guidance_context.py` exactly:
`register_known_applicability_context` is called from incident_manager's
own `before_agent_callback` (`evidence.py`'s `capture_known_
applicability_context`), the instant its incoming structured request is
parsed -- BEFORE any tool call happens, so `knowledge_search`'s first
call already sees the trusted context. `pop_known_applicability_context`
is read exactly once, by `backend.tools.knowledge.runtime.get_or_init_
run_state`, when a run's evidence state is first initialized.
`discard_known_applicability_context` is the same-shape backstop cleanup
from chat_service.py's own `finally` block.
"""
from __future__ import annotations

import threading
from typing import Optional

from backend.knowledge.domain.applicability import ApplicabilityContext

_lock = threading.Lock()
_store: dict[str, ApplicabilityContext] = {}


def register_known_applicability_context(run_id: Optional[str], context: Optional[ApplicabilityContext]) -> None:
    """Called once, from incident_manager's own `before_agent_callback`.
    A no-op for a missing `run_id` or a `None`/empty-dimensions context
    -- never raises. An empty-dimensions context is deliberately NOT
    registered (leaving the store untouched for this run_id) so `pop_
    known_applicability_context` returning `None` and "nothing was ever
    registered" stay indistinguishable -- both correctly mean "start
    from an empty ApplicabilityContext," the pre-existing behavior.
    """
    if not run_id or context is None or not context.dimensions:
        return
    with _lock:
        _store[run_id] = context


def pop_known_applicability_context(run_id: Optional[str]) -> Optional[ApplicabilityContext]:
    """Read exactly once, by `get_or_init_run_state` when a run's
    evidence state is first initialized -- removes the entry as it
    reads it (this run's `ApplicabilityContext` is then fixed for the
    rest of the run, exactly like the pre-existing `as_of` capture-once
    discipline). Returns `None` for a missing `run_id` or a turn that
    registered no known facts -- both safe, ordinary "start from an
    empty ApplicabilityContext" outcomes, never an error.
    """
    if not run_id:
        return None
    with _lock:
        return _store.pop(run_id, None)


def discard_known_applicability_context(run_id: str) -> None:
    """Backstop/normal cleanup, called from chat_service.py's own
    `finally` block. Safe to call whether or not an entry exists (e.g.
    `pop_known_applicability_context` already consumed it via a real
    `knowledge_search` call, or none was ever registered).
    """
    with _lock:
        _store.pop(run_id, None)
