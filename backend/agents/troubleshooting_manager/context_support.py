"""Phase 6A.10 CORRECTIVE PASS: real, live TELCO Context and selected-
Evidence support for the normal user-routed troubleshooting path.

ROOT CAUSE THIS MODULE FIXES: the original 6A.10 implementation
(`backend/agents/team_manager/troubleshooting_tool.py`) honestly built
an EMPTY TELCO Context and an EMPTY Evidence selection, on the grounds
that "no live producer is wired into any conversational turn yet." That
framing was correct about TELCO Context persistence (see below) but
WRONG about Evidence: 6A.4's deterministic narrowing engine
(`backend.knowledge.narrowing`) and 6A.5's hybrid retrieval
(`backend.knowledge.hybrid_retrieval`) are both real, COMPLETE,
live-validated-against-Cloud-SQL production capabilities — they were
simply never CALLED from any live conversational turn before this
corrective pass. This module is the coordinator that calls them for
real, mirroring `backend/tools/knowledge/runtime.py`'s own established
"impure coordinator over frozen, pure/generic packages" pattern — it
REUSES `backend.tools.knowledge.runtime.get_knowledge_repository`
directly (never a second Knowledge-repository singleton) and adds the
two singletons that did not exist yet for the Evidence Index/embedding
provider, mirroring that same module's own construction idiom exactly.
NONE of `backend/knowledge/narrowing/`, `backend/knowledge/hybrid_
retrieval/`, or `backend/tools/knowledge/` was modified to build this —
every function this module calls is byte-for-byte unmodified.

TELCO CONTEXT REMAINS HONEST, NEVER FABRICATED: no live
`TelcoContextProfile` persistence pipeline exists anywhere in this
codebase (`backend/context/sqlalchemy/` is still never called from any
live turn — deliberately; wiring a full live profile-persistence pipeline
remains a distinct, larger capability, correctly out of THIS corrective
pass's own bounded scope). Instead, this module mirrors the ALREADY-
PROVEN `known_applicability_facts` pattern (A5's own corrective pass,
`backend/agents/incident_manager/schemas.py`): `team_manager`'s own
model may state a `known_context_facts` dict populated ONLY from facts
the CURRENT user message EXPLICITLY, LITERALLY states — never inferred,
never assumed, never carried over from an earlier turn.
`build_context_state_from_known_facts` converts that trusted, literal
dict into real `backend.context.domain.models.ContextAssertion`s and
reduces them via the SAME pure, unmodified `compute_context_state` 6A.2
already defines — ephemeral, in-process, NEVER persisted to
`slopanoc_telco_context_profiles`/`_assertions` (no new DB write of any
kind, no `TelcoContextService` call anywhere in this module). An
unrecognized dimension name is silently ignored — never guessed, never a
crash, matching `known_applicability_facts`'s own "omitted means
UNKNOWN, never a guessed value" discipline exactly.

6A.10.2 CORRECTIVE PASS — DETERMINISTIC VERIFICATION AGAINST REAL USER
TEXT, NEVER PROMPT-ONLY TRUST: a `FunctionTool` argument is MODEL
OUTPUT, not automatically a user-stated fact — the ORIGINAL 6A.10.1 pass
labeled every `known_context_facts` entry `ContextOrigin.USER` on the
strength of the prompt instruction alone ("only literal facts"), which
this codebase's own governing trust principle explicitly rejects as
insufficient ("if the answer is 'the prompt asks the model nicely,' that
is not sufficient" — CLAUDE.md's own TRUST/CONTROL PRINCIPLES). Fixed
with a DETERMINISTIC, APPLICATION-ENFORCED gate, mirroring this
codebase's own established "retrieved != trusted until validated against
the real source" discipline (Teams evidence provenance; governed-
Knowledge SEARCH RESULT != EVIDENCE USED): `extract_current_user_text`
reads `tool_context.user_content` — the SAME `ReadonlyContext.user_
content` property B6's own audit already proved, for a call originating
directly from `team_manager`'s own turn (never a nested `AgentTool`
invocation), IS EXACTLY `team_manager`'s own real, top-level `Content`
for this turn — i.e. the user's REAL, VERBATIM current message text,
never the model's own restatement/paraphrase of it (`troubleshooting_
question` is NOT used for this — it is itself model-authored).
`build_context_state_from_known_facts` now REQUIRES this real text and
DROPS (never asserts) any `known_context_facts` entry whose raw value
does not appear, case-insensitively, as a literal substring of that real
text — only a fact that survives this check is ever labeled
`ContextOrigin.USER`, and that label is now TRUTHFULLY earned rather
than merely claimed. No new `ContextOrigin` member was added (per
explicit instruction: "do not invent a new provenance enum if an
existing canonical representation suffices") — `USER` already means
"understood to have been asserted by the user in this conversation," and
the verification step is what makes that label honest rather than a bare
model claim.

SELECTED EVIDENCE IS REAL, WHEN THE PIPELINE PRODUCES ANY: `query_
selected_evidence` composes, in order, the exact three UNMODIFIED 6A.4/
6A.5 production functions — `narrow_knowledge` (6A.4), `hybrid_retrieve`
(6A.5), `select_evidence` (6A.5) — exactly the same functions 6A.5's own
real-DEV-Cloud-SQL-validated tests already call. SEARCH RESULT != EVIDENCE
USED remains intact: only `hybrid_retrieve`'s own reranked, `select_
evidence`-selected top-K ever reaches the Intelligence Package — never
the raw candidate set, never a discarded item, never unfiltered corpus
content. Fails closed to an EMPTY selection (never raises, never
fabricates a result) on any downstream error — a real infra hiccup here
degrades to `NEEDS_INFORMATION` downstream (6A.9's own unmodified
behavior), never a crash of the whole troubleshooting turn.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.config.settings import get_settings
from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin
from backend.context.domain.models import ContextAssertion, ContextValue, compute_context_state, default_canonical_value
from backend.knowledge.hybrid_retrieval.contracts import EvidenceSelectionResult, HybridRetrievalQuery
from backend.knowledge.hybrid_retrieval.evidence_selection import select_evidence
from backend.knowledge.hybrid_retrieval.repository import EvidenceIndexRepository
from backend.knowledge.hybrid_retrieval.service import hybrid_retrieve
from backend.knowledge.narrowing.service import narrow_knowledge
from backend.knowledge_hybrid_retrieval.vertex_embedding_provider import VertexTextEmbeddingProvider
from backend.tools.knowledge.runtime import get_knowledge_repository

_logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_EVIDENCE_UNITS",
    "build_context_state_from_known_facts",
    "extract_current_user_text",
    "query_selected_evidence",
    "get_evidence_index_repository",
    "get_embedding_provider",
]

DEFAULT_MAX_EVIDENCE_UNITS = 5
"""Deliberately generic, matching this codebase's own established
'deliberately generic bound' convention (e.g. 6A.9's own
`DEFAULT_EXPERIENCE_LIMIT`) -- never query/corpus-tuned."""

_evidence_index_repository_cache: list[EvidenceIndexRepository] = []
_embedding_provider_cache: list[VertexTextEmbeddingProvider] = []


def get_evidence_index_repository() -> EvidenceIndexRepository:
    """Process-wide singleton, mirroring `backend.tools.knowledge.
    runtime.get_knowledge_repository`'s own established construction
    pattern. Deliberately uses `resolve_database_url()` -- the SAME
    database domain Alembic itself resolves to (confirmed by direct
    inspection of `alembic/env.py`'s own `_resolved_database_url`) for
    the `slopanoc_knowledge_evidence_index` migration (`a1f3c9e07b21`) --
    never `resolve_knowledge_database_url()`, which is `slopanoc_
    knowledge_objects`'s own, logically separate domain."""
    if not _evidence_index_repository_cache:
        _evidence_index_repository_cache.append(EvidenceIndexRepository(get_settings().resolve_database_url()))
    return _evidence_index_repository_cache[0]


def get_embedding_provider() -> VertexTextEmbeddingProvider:
    """Process-wide singleton -- the SAME concrete provider 6A.5's own
    live-validated indexing/retrieval pipeline already uses; no second
    embedding-client architecture."""
    if not _embedding_provider_cache:
        _embedding_provider_cache.append(VertexTextEmbeddingProvider())
    return _embedding_provider_cache[0]


def extract_current_user_text(tool_context: Any) -> str:
    """Reads the REAL, verbatim text of the current turn's user message
    from `tool_context.user_content` -- for a call originating directly
    from `team_manager`'s own turn (never a nested `AgentTool`
    invocation, which is the ONLY case `troubleshooting_manager`'s plain
    `FunctionTool` registration is ever used in), this is `ReadonlyContext
    .user_content`, proven by B6's own audit to be exactly `team_manager`
    's own top-level `Content` for this turn (`Runner.run_async`'s
    `new_message` becomes `invocation_context.user_content`) -- never a
    tool's own call arguments, never the model's own restatement. Mirrors
    `direct_read_fast_path.py`'s own established defensive `getattr`
    extraction pattern exactly. Returns `""` for missing/empty content --
    a safe, ordinary "nothing to verify against" outcome, never an error;
    every `known_context_facts` entry is then correctly dropped by
    `build_context_state_from_known_facts` rather than trusted blindly.
    """
    user_content = getattr(tool_context, "user_content", None)
    parts = getattr(user_content, "parts", None) if user_content else None
    if not parts:
        return ""
    return "".join(getattr(part, "text", None) or "" for part in parts)


def _fact_verified_in_text(raw_value: str, source_text: str) -> bool:
    """Deterministic, literal, case-insensitive substring check -- no
    alias/synonym resolution, matching `default_canonical_value`'s own
    established "unknown aliases remain unnormalized rather than guessed"
    discipline. A fact the model did not genuinely find in the user's own
    real text fails this check and is dropped -- never asserted as
    `ContextOrigin.USER` on the strength of the model's own claim alone.
    """
    if not source_text or not raw_value:
        return False
    return raw_value.strip().lower() in source_text.lower()


def build_context_state_from_known_facts(
    known_context_facts: Optional[dict[str, str]],
    current_user_text: str = "",
) -> dict[ContextDimension, ContextValue]:
    """Pure, no I/O. `known_context_facts` keys must match 6A.2's own
    closed `ContextDimension` vocabulary (e.g. `"fault"`, `"vendor"`) --
    an unrecognized key is silently dropped, never guessed and never a
    crash. A blank/whitespace-only value is dropped the same way.

    6A.10.2 CORRECTIVE PASS: `current_user_text` (the real, verbatim
    current-turn user message, from `extract_current_user_text`) is now
    REQUIRED for a fact to be trusted -- a `known_context_facts` entry
    whose `raw_value` is not a literal, case-insensitive substring of
    `current_user_text` is dropped (never asserted), regardless of what
    the model itself claimed. An empty `current_user_text` (missing/
    unavailable turn content) means EVERY fact fails verification and the
    result is always `{}` -- fail closed, never "trust anyway."

    Every fact that survives verification becomes a real `ContextAssertion`
    with `origin=USER` (now truthfully earned, not merely claimed),
    reduced via 6A.2's own unmodified `compute_context_state` -- the EXACT
    same reduction the (still-unwired) live `TelcoContextProfile` pipeline
    would eventually use, so this remains structurally compatible with
    that future wiring rather than a competing representation.
    """
    if not known_context_facts:
        return {}
    now = datetime.now(timezone.utc)
    assertions: list[ContextAssertion] = []
    for index, (raw_dimension, raw_value) in enumerate(known_context_facts.items()):
        if not raw_dimension or not isinstance(raw_dimension, str):
            continue
        try:
            dimension = ContextDimension(raw_dimension.strip().lower())
        except ValueError:
            continue  # unrecognized dimension name -- never guessed, never a crash
        if not raw_value or not isinstance(raw_value, str) or not raw_value.strip():
            continue
        if not _fact_verified_in_text(raw_value, current_user_text):
            continue  # model's claim not found in the real user text -- never trusted on its own say-so
        assertions.append(
            ContextAssertion(
                assertion_id=f"known-context-fact-{index}",
                dimension=dimension,
                kind=AssertionKind.VALUE,
                raw_value=raw_value,
                canonical_value=default_canonical_value(raw_value),
                origin=ContextOrigin.USER,
                source_reference=None,
                asserted_at=now,
            )
        )
    return compute_context_state(assertions)


async def query_selected_evidence(
    query_text: str,
    context_state: dict[ContextDimension, ContextValue],
    *,
    max_evidence_units: int = DEFAULT_MAX_EVIDENCE_UNITS,
) -> EvidenceSelectionResult:
    """Composes the exact three UNMODIFIED 6A.4/6A.5 production
    functions in order -- `narrow_knowledge` -> `hybrid_retrieve` ->
    `select_evidence` -- never re-implements narrowing/retrieval/
    selection. An empty `permitted_knowledge_ids` (no applicable
    Knowledge, or no TELCO context to narrow by) short-circuits to an
    empty, honest selection -- never "search everything" (6A.4's own
    frozen invariant, preserved here by construction: `narrow_knowledge`
    itself, unmodified, already enforces this)."""
    try:
        repository = get_knowledge_repository()
        narrowing_result = await narrow_knowledge(repository, context_state, as_of=datetime.now(timezone.utc))
        if not narrowing_result.permitted_knowledge_ids:
            return EvidenceSelectionResult(query_text=query_text, selected=[], selection_reason="no_permitted_knowledge")

        evidence_repository = get_evidence_index_repository()
        embedding_provider = get_embedding_provider()
        retrieval_query = HybridRetrievalQuery(
            query_text=query_text,
            permitted_knowledge_ids=narrowing_result.permitted_knowledge_ids,
            limit=max_evidence_units,
        )
        retrieval_result = await hybrid_retrieve(retrieval_query, evidence_repository, embedding_provider)
        return select_evidence(query_text, retrieval_result.candidates, max_evidence_units=max_evidence_units)
    except Exception:
        _logger.warning(
            "context_support: live evidence retrieval failed -- falling back to an empty, honest selection",
            exc_info=True,
        )
        return EvidenceSelectionResult(query_text=query_text, selected=[], selection_reason="live_retrieval_error")
