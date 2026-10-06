"""Concrete runtime composition + trusted, run-scoped evidence state for
the Generic KM tool adapter. See this package's own `__init__.py` for the
full "why a run-id-keyed dict, not a ContextVar" rationale.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable, Optional

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
    explicit_empty_selection: bool = False
    applicability_by_identity: dict[tuple[str, str, "str | None"], str] = field(default_factory=dict)
    unresolved_dimensions_by_identity: dict[tuple[str, str, "str | None"], list[str]] = field(default_factory=dict)
    search_log: list[dict[str, Any]] = field(default_factory=list)
    """Every governed search of this run: query, status (ok | error), result count (discovery audit)."""
    annotations_by_identity: dict[tuple[str, str, "str | None"], dict[str, Any]] = field(default_factory=dict)
    """Opaque, server-derived annotations of SELECTED evidence (e.g. a consumer's derived view of the
    same governed version). Never model input; this module does not interpret them."""


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

    Applicability context starts EMPTY by default (§11) -- never inferred
    from free text, a title, or model reasoning -- UNLESS incident_
    manager's own `before_agent_callback` (A5 final corrective pass,
    Correction D: `evidence.capture_known_applicability_context`) already
    registered a TRUSTED context for this run_id from `IncidentManager
    Request.known_applicability_facts` (facts the user explicitly,
    literally stated this turn -- never a model guess). Popped exactly
    once here, so it applies for the whole run without a second
    "did I already use it" flag.
    """
    from backend.api.applicability_context_capture import pop_known_applicability_context

    with _lock:
        state = _run_states.get(run_id)
        if state is None:
            known_context = pop_known_applicability_context(run_id)
            state = KnowledgeRunEvidenceState(
                run_id=run_id,
                execution_context=KnowledgeToolExecutionContext(
                    as_of=clock(), applicability_context=known_context if known_context is not None else ApplicabilityContext()
                ),
            )
            _run_states[run_id] = state
        elif not state.execution_context.applicability_context.dimensions:
            known_context = pop_known_applicability_context(run_id)
            if known_context is not None and known_context.dimensions:
                state.execution_context.applicability_context = known_context
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

        # Record server-evaluated applicability outcomes by item identity
        for payload_item in execution.agent_payload.items:
            key = payload_item.selection_key
            ident = (key.knowledge_id, key.version_label, key.section_id)
            outcome = getattr(payload_item, "applicability_outcome", None)
            if outcome is not None:
                val = outcome.value.lower() if hasattr(outcome, "value") else str(outcome).lower()
                state.applicability_by_identity[ident] = val
                state.unresolved_dimensions_by_identity[ident] = list(
                    getattr(payload_item, "unresolved_applicability_dimensions", None) or []
                )


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

        if state is not None:
            if not selections:
                state.explicit_empty_selection = True
            elif validated:
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


def record_evidence_annotation(run_id: str, identity: tuple[str, str, "str | None"], name: str, value: Any) -> None:
    """Trusted, backend-only: attach a server-derived annotation to one SELECTED evidence identity
    of this run (no-op for an unknown run or an unselected identity)."""
    with _lock:
        state = _run_states.get(run_id)
        if state is None or identity not in {_identity(item) for item in state.selected_evidence}:
            return
        state.annotations_by_identity.setdefault(identity, {})[name] = value


def get_evidence_annotation(run_id: str, identity: tuple[str, str, "str | None"], name: str) -> Any:
    with _lock:
        state = _run_states.get(run_id)
        return (state.annotations_by_identity.get(identity) or {}).get(name) if state is not None else None


def get_evidence_applicability_outcome(
    run_id: str, identity: tuple[str, str, "str | None"]
) -> "str | None":
    """Trusted, backend-only accessor -- returns server-evaluated applicability outcome
    ('match', 'partial_match', 'unknown', etc.) recorded during retrieval for this evidence identity.
    """
    with _lock:
        state = _run_states.get(run_id)
        if state is None:
            return None
        return state.applicability_by_identity.get(identity)


def refresh_run_applicability_context(run_id: str, context: ApplicabilityContext) -> None:
    """Trusted, backend-only: install the server-reconciled `ApplicabilityContext` for this run.

    When the run's evidence state already exists with a DIFFERENT context, the context is replaced
    and every previously recorded applicability outcome is discarded, so a stale UNKNOWN /
    PARTIAL_MATCH is never reused after enrichment. Outcomes are only re-established by a new
    `knowledge_search`, i.e. by deterministic `evaluate_applicability` -- never set here.
    Selected evidence is untouched; without a fresh outcome it stays non-authoritative.
    """
    from backend.api.applicability_context_capture import register_known_applicability_context

    with _lock:
        state = _run_states.get(run_id)
        if state is None:
            register_known_applicability_context(run_id, context)
            return
        if state.execution_context.applicability_context.dimensions == context.dimensions:
            return
        state.execution_context.applicability_context = context
        state.applicability_by_identity.clear()
        state.unresolved_dimensions_by_identity.clear()


def get_available_unresolved_applicability_dimensions(run_id: str) -> list[str]:
    """Trusted, backend-only: unresolved applicability dimension names of the highest-ranked
    AVAILABLE governed document whose outcome is not MATCH. Used only to phrase a clarification;
    never confers authority."""
    with _lock:
        state = _run_states.get(run_id)
        if state is None:
            return []
        target_kid = None
        dims: list[str] = []
        for item in state.available_evidence.items:
            ident = _identity(item)
            outcome = state.applicability_by_identity.get(ident)
            if outcome in (None, "match"):
                continue
            if target_kid is None:
                target_kid = ident[0]
            if ident[0] != target_kid:
                continue
            for dim in state.unresolved_dimensions_by_identity.get(ident, []):
                if dim not in dims:
                    dims.append(dim)
        return dims


def get_evidence_unresolved_applicability_dimensions(
    run_id: str, identity: tuple[str, str, "str | None"]
) -> list[str]:
    """Trusted, backend-only accessor -- returns the applicability dimension names the
    server-side evaluation could not resolve (per-dimension UNKNOWN) for this evidence identity.
    """
    with _lock:
        state = _run_states.get(run_id)
        if state is None:
            return []
        return list(state.unresolved_dimensions_by_identity.get(identity, []))


def has_explicit_empty_knowledge_selection(run_id: str) -> bool:
    """Trusted, backend-only accessor -- returns True if `knowledge_select_evidence`
    was explicitly called with an empty list (`selections=[]`), indicating negative
    selection (the model reviewed available evidence and determined none applied).
    """
    with _lock:
        state = _run_states.get(run_id)
        return state.explicit_empty_selection if state is not None else False


def has_knowledge_run_state(run_id: str) -> bool:
    """Trusted, backend-only accessor -- True once any `knowledge_search` initialized this
    run's evidence state (i.e. governed knowledge was actually searched in this run).
    """
    with _lock:
        return run_id in _run_states


def note_search_outcome(run_id: str, query_text: str, status: str, result_count: int = 0, error: Optional[str] = None) -> None:
    """Trusted, backend-only: record one governed search of this run (success or failure) so the
    server can tell a completed discovery from a failed one. Bounded."""
    with _lock:
        state = _run_states.get(run_id)
        if state is None:
            return
        entry = {"query_text": query_text[:300], "status": status, "result_count": result_count}
        if error:
            entry["error"] = error
        state.search_log = [*state.search_log[-19:], entry]


def run_search_log(run_id: Optional[str]) -> list[dict[str, Any]]:
    if not run_id:
        return []
    with _lock:
        state = _run_states.get(run_id)
        return [dict(e) for e in state.search_log] if state is not None else []


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
    settings = get_settings()
    dense_provider = None
    if settings.knowledge_retrieval_mode == "hybrid" and settings.vertex_ai_enabled:
        from backend.tools.knowledge.dense_similarity import VertexEmbeddingSimilarityProvider

        dense_provider = VertexEmbeddingSimilarityProvider(model_name=settings.knowledge_embedding_model)
    retrieval_service = KnowledgeRetrievalService(
        repository,
        dense_provider=dense_provider,
        dense_min_similarity=settings.knowledge_dense_min_similarity,
        dense_timeout_seconds=settings.knowledge_dense_timeout_seconds,
    )
    return KnowledgeToolService(retrieval_service, KnowledgeProvenanceService(repository))
