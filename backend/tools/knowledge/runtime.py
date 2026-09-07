"""Concrete runtime composition + trusted, run-scoped evidence state for
the Generic KM tool adapter. See this package's own `__init__.py` for the
full "why a run-id-keyed dict, not a ContextVar" rationale.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Callable

from backend.config.settings import get_settings
from backend.knowledge.domain.applicability import ApplicabilityContext
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceSelectionKey, KnowledgeEvidenceSet
from backend.knowledge.provenance.service import KnowledgeProvenanceService, validate_evidence_selection
from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.knowledge.tools.contracts import KnowledgeSearchExecutionResult, KnowledgeToolExecutionContext
from backend.knowledge.tools.service import KnowledgeToolService

Clock = Callable[[], datetime]


class KnowledgeRuntimeError(Exception):
    """Base class for this adapter's own runtime errors. Never constructed
    from raw document/payload content -- identity information only.
    """


class KnowledgeRuntimeConsistencyError(KnowledgeRuntimeError):
    """Raised when the exact same evidence identity
    (`knowledge_id`/`version_label`/`section_id`) is reported by two
    successful searches within the SAME run with different trusted
    `KnowledgeEvidenceItem` content/state. Fails closed -- never silently
    keeps the first, never silently replaces with the latest.
    """


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


def _identity(item: KnowledgeEvidenceItem) -> tuple[str, str, "str | None"]:
    return (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)


@dataclass
class KnowledgeRunEvidenceState:
    """One Incident Manager run's own trusted KM evidence universe --
    server-owned, keyed by trusted runtime run identity, process-local,
    non-persistent. `execution_context.as_of` is captured exactly ONCE,
    at first initialization, and reused unchanged for every subsequent
    `knowledge_search` call within this same run (§10/§12).
    """

    run_id: str
    execution_context: KnowledgeToolExecutionContext
    available_evidence: KnowledgeEvidenceSet = field(default_factory=KnowledgeEvidenceSet)
    selected_evidence: list[KnowledgeEvidenceItem] = field(default_factory=list)


_lock = threading.Lock()
_run_states: dict[str, KnowledgeRunEvidenceState] = {}


def get_or_init_run_state(run_id: str, clock: Clock = _default_clock) -> KnowledgeRunEvidenceState:
    """Lazily initializes this run's evidence state on first
    `knowledge_search` call (§17 -- initializing at Incident Manager run
    start is not currently a clean hook to reach from this adapter layer,
    so lazy-on-first-search is used instead; this is safe because run
    identity is already trusted, `as_of` is captured exactly once here,
    every subsequent call within the run reuses the SAME state object,
    and cleanup is unconditional in chat_service.py's own `finally`
    block regardless of how many searches actually happened).
    Applicability context starts empty (§11) -- never inferred from free
    text, a title, or model reasoning.
    """
    with _lock:
        state = _run_states.get(run_id)
        if state is None:
            state = KnowledgeRunEvidenceState(
                run_id=run_id,
                execution_context=KnowledgeToolExecutionContext(as_of=clock(), applicability_context=ApplicabilityContext()),
            )
            _run_states[run_id] = state
        return state


def record_search_result(run_id: str, execution: KnowledgeSearchExecutionResult) -> None:
    """Merge one successful search's trusted `evidence_set` into this
    run's AVAILABLE evidence universe (§18), unioned by
    `(knowledge_id, version_label, section_id)` identity, preserving
    deterministic first-seen order. An identical repeat of an already-
    available identity is a silent no-op (deduplication); a DIFFERENT
    `KnowledgeEvidenceItem` reported for the same identity within the
    same run raises `KnowledgeRuntimeConsistencyError` -- fails closed,
    never silently keeps one or the other.
    """
    with _lock:
        state = _run_states.get(run_id)
        if state is None:
            raise KnowledgeRuntimeError(f"no knowledge run state initialized for run_id={run_id!r}")

        merged_items = list(state.available_evidence.items)
        by_identity = {_identity(item): item for item in merged_items}
        for new_item in execution.evidence_set.items:
            identity = _identity(new_item)
            existing = by_identity.get(identity)
            if existing is None:
                merged_items.append(new_item)
                by_identity[identity] = new_item
            elif existing != new_item:
                raise KnowledgeRuntimeConsistencyError(
                    f"contradictory evidence reported for identity {identity} within run_id={run_id!r}"
                )
            # else: identical repeat -- deduplicated, no-op.

        state.available_evidence = KnowledgeEvidenceSet(items=merged_items)


def select_evidence(run_id: str, selections: list[KnowledgeEvidenceSelectionKey]) -> list[KnowledgeEvidenceItem]:
    """Validate `selections` against exactly this run's own AVAILABLE
    evidence (never the repository, never another run) by delegating to
    5.1H's own `validate_evidence_selection` -- no validation rule is
    reimplemented here. On success, merges the validated items into this
    run's SELECTED evidence (§19), deduplicating across repeated/multiple
    `knowledge_select_evidence` calls while preserving first-selected
    order (§26/§27). Raises `KnowledgeEvidenceSelectionError` (5.1H,
    unmodified) on any fabricated/real-but-unavailable/previous-run
    identity -- the WHOLE selection fails; the run's selected evidence is
    left completely unchanged on failure (the merge below only ever runs
    after `validate_evidence_selection` has already succeeded for every
    requested key).
    """
    with _lock:
        state = _run_states.get(run_id)
        available = state.available_evidence if state is not None else KnowledgeEvidenceSet()
        validated = validate_evidence_selection(available, selections)

        if validated and state is not None:
            existing_ids = {_identity(item) for item in state.selected_evidence}
            for item in validated:
                identity = _identity(item)
                if identity not in existing_ids:
                    state.selected_evidence.append(item)
                    existing_ids.add(identity)

        return validated


def get_available_knowledge_evidence(run_id: str) -> KnowledgeEvidenceSet:
    """Read-only accessor -- never ADK/model-facing."""
    with _lock:
        state = _run_states.get(run_id)
        return state.available_evidence if state is not None else KnowledgeEvidenceSet()


def snapshot_selected_knowledge_evidence(run_id: str) -> list[KnowledgeEvidenceItem]:
    """Trusted, backend-only accessor (§47) -- lets other trusted backend
    code inspect exactly what Incident Manager explicitly selected during
    `run_id`, before this run's state is cleaned up. Accepts only a
    trusted runtime run identity (never model-controllable), returns
    plain backend `KnowledgeEvidenceItem`s, and is never registered as an
    ADK tool or otherwise exposed to a model. Returns `[]` for an
    unknown/already-cleaned-up `run_id` -- never another run's evidence.
    """
    with _lock:
        state = _run_states.get(run_id)
        return list(state.selected_evidence) if state is not None else []


def discard_knowledge_run_evidence_state(run_id: str) -> None:
    """Called from chat_service.py's own existing turn-end `finally`
    block (mirroring `discard_model_call_tracking`/`discard_pending_trusted_result`'s
    own established pattern) -- guarantees no run-scoped KM evidence
    survives past the one turn/run that produced it, on every exit path.
    Safe to call even when nothing was ever recorded for `run_id`.
    """
    with _lock:
        _run_states.pop(run_id, None)


@lru_cache(maxsize=1)
def get_knowledge_repository() -> SqlAlchemyKnowledgeRepository:
    """Process-wide singleton, mirroring `backend/cases/db.py`'s
    `get_case_database()` pattern. `SqlAlchemyKnowledgeRepository` (POST-
    5.1 A2 -- formerly `SQLiteKnowledgeRepository`) is dialect-neutral --
    this constructs it with whatever `Settings.resolve_knowledge_
    database_url()` resolves to, `sqlite+aiosqlite://...` locally or
    `postgresql+asyncpg://...` in a Cloud SQL deployment, with no
    branching here on which dialect it is. Schema is never created here
    explicitly -- every `SqlAlchemyKnowledgeRepository` operation already
    lazily/idempotently calls its own `ensure_schema()` (5.1F); an empty/
    not-yet-created repository is a valid starting state, not an error.
    """
    return SqlAlchemyKnowledgeRepository(get_settings().resolve_knowledge_database_url())


@lru_cache(maxsize=1)
def get_knowledge_tool_service() -> KnowledgeToolService:
    """Process-wide singleton composing the frozen 5.1G/5.1H services
    over the shared repository -- constructed exactly once, reused for
    every `knowledge_search`/`knowledge_select_evidence` call in this
    process. No Generic KM constructor is modified to make this wiring
    easier.
    """
    repository = get_knowledge_repository()
    return KnowledgeToolService(KnowledgeRetrievalService(repository), KnowledgeProvenanceService(repository))
