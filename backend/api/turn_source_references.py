"""B7 corrective pass -- durable, turn-owned Source/Knowledge-source
provenance persistence.

THE GAP THIS CLOSES: `chat_service.py` already builds a safe, structured
`SourceReferenceDTO` (Teams) and/or `KnowledgeSourceReferenceDTO` list
(governed KM) for a turn's own `message.completed` SSE event -- but
`session_history_service.py`'s `get_session_history` (`GET /api/sessions/
{id}/history`) never persisted or re-projected either one. A live turn's
answer therefore rendered its provenance correctly, but a hard refresh /
saved-chat reopen / backend restart lost it entirely, even though the
answer text itself survived (ADK's own durable event history).

WHY ADK SESSION STATE, NOT A NEW TABLE/MIGRATION: audited first (per
instruction) -- no new persistence subsystem is needed. `chat_service.py`
already writes several OTHER plain, cross-turn session-state keys this
exact same way (`selected_teams_chat_id`, `last_teams_evidence` --
state_sync.py), and ADK's own `DatabaseSessionService` already durably
persists `session.state` (proven, repeatedly, by this project's own Cloud
SQL restart validation). Reusing that mechanism for this data needs zero
schema/migration work.

WHY THIS SURVIVES REWIND FOR FREE (verified against the installed ADK
1.33.0 source, `runners.py`'s `Runner._compute_state_delta_for_rewind`,
not assumed): rewind's own state-delta reversal is computed by REPLAYING,
in event-list ORDER (not grouped by `invocation_id`), every state_delta
ever written to a given key up to the rewind point, and diffing that
against the CURRENT value -- for a key with NO entry before the rewind
point, the whole key is cleared; for a key that already had an EARLIER,
different value before the rewind point, it is restored to exactly that
earlier value. This is why `build_turn_source_references_delta` (below)
always writes the FULL, accumulated `{turn_id: {...}, ...}` dict on every
turn that has anything to persist -- never a partial/incremental patch --
mirroring the exact same "always overwrite the whole value" discipline
`state_sync.py`'s `compute_state_updates` already uses for the same
reason. No second branch-selection mechanism is implemented here; ADK's
own, already-proven rewind machinery is the only thing that decides which
turns' entries survive.

WHAT IS PERSISTED: the exact same safe `SourceReferenceDTO`/
`KnowledgeSourceReferenceDTO` payloads already sent to the frontend this
same turn (`.model_dump(mode="json")` of the SAME object instances
`chat_service.py` already built for the live SSE event) -- never a
separately-derived "safe subset", never re-queried from current KM/Teams
state. Both DTOs already exclude `source_uri`/`gs://`/any raw internal
identifier by construction (see `knowledge_source_reference.py`/
`source_reference.py`'s own docstrings) -- this module adds no new
exposure surface, it only durably stores what was already safe to show.

WHAT IS NEVER PERSISTED: anything not already part of the safe DTO
(`source_uri`, raw `knowledge_id`/database internals beyond what the DTO
already carries, model-authored citation text, anything from `agent_
payload`). This module never re-runs `knowledge_search`/`knowledge_
select_evidence` and never reads current KM/Teams state -- it only
durably stores what THIS turn's own already-trusted, already-validated
DTO construction produced.

B7 LIVE-REGRESSION CORRECTIVE PASS -- READ-TIME EXACT-IDENTITY
NORMALIZATION: `resolve_turn_source_references` now also applies
`knowledge_source_reference.dedupe_knowledge_source_references` to the
`knowledge_sources` list it reads back, keyed EXCLUSIVELY by the
canonical `(knowledge_id, version_label, section_id)` identity (never
`title`/`section_heading`/content). This is deliberately a READ-TIME
safety net for any turn's data that was already persisted with an exact
duplicate (whether from before this fix, or from a cause this pass could
not reproduce without live Cloud SQL/Gemini access) -- it never mutates
the stored session state itself, never re-runs KM retrieval, never
re-queries current KM state, and never merges/collapses two genuinely
DISTINCT evidence identities. `chat_service.py`'s own write path (`build_
turn_source_references_delta`'s caller) ALSO applies the same
normalization before persisting a NEW turn's provenance -- see that call
site's own comment -- so this read-time pass is redundant, not load-
bearing, for any turn completed after this fix; it exists purely to keep
already-persisted historical data from continuing to display a duplicate
after a hard refresh or backend restart, without a database migration.
"""
from __future__ import annotations

from typing import Any, Optional

from backend.api.knowledge_source_reference import dedupe_knowledge_source_references
from backend.api.schemas import KnowledgeSourceReferenceDTO, SourceReferenceDTO

TURN_SOURCE_REFERENCES_STATE_KEY = "turn_source_references"
"""Plain (never `temp:`-prefixed) session-state key -- must survive past
the one turn that wrote it, unlike the `temp:` scratch values elsewhere in
this codebase. Value shape: `{turn_id: {"source"?: <SourceReferenceDTO
JSON>, "knowledge_sources"?: [<KnowledgeSourceReferenceDTO JSON>, ...]}}`.
`turn_id` is the ADK `invocation_id` -- the SAME identity `session_
history_service.py`'s `SessionHistoryMessageDTO.turn_id` already uses,
never a newly-invented one.
"""


def build_turn_source_references_delta(
    existing: Any,
    turn_id: str,
    source: Optional[SourceReferenceDTO],
    knowledge_sources: list[KnowledgeSourceReferenceDTO],
) -> Optional[dict[str, Any]]:
    """Returns the FULL updated value to persist for `TURN_SOURCE_
    REFERENCES_STATE_KEY`, or `None` when this turn has nothing worth
    persisting (no Teams source, no selected KM evidence) -- a turn with
    no evidence must never fabricate/write an empty placeholder entry.

    `existing` is the CURRENT raw value of the state key (already a
    `{turn_id: {...}}` dict in normal operation, but defensively accepted
    as `Any` and treated as empty if it is not actually a dict -- a
    single corrupted/foreign value must never crash a turn that otherwise
    completed successfully).
    """
    if source is None and not knowledge_sources:
        return None

    base = existing if isinstance(existing, dict) else {}
    entry: dict[str, Any] = {}
    if source is not None:
        entry["source"] = source.model_dump(mode="json")
    if knowledge_sources:
        entry["knowledge_sources"] = [item.model_dump(mode="json") for item in knowledge_sources]

    return {**base, turn_id: entry}


def resolve_turn_source_references(
    state: Any, turn_id: str
) -> tuple[Optional[SourceReferenceDTO], list[KnowledgeSourceReferenceDTO]]:
    """Reads back exactly one turn's own persisted provenance from a
    session's state, for `session_history_service.py`'s history
    projection. Safe/defensive throughout -- a missing key, a missing
    entry for this `turn_id`, or a malformed value at any level is
    treated as "nothing persisted for this turn" (returns `(None, [])`),
    never raised -- mirrors `backend.selection.service.load_active_
    selection`'s own tolerance for exactly the same class of "this is
    trusted backend data, but never trust its shape blindly on the way
    back out" defensiveness.
    """
    all_entries = state.get(TURN_SOURCE_REFERENCES_STATE_KEY) if hasattr(state, "get") else None
    if not isinstance(all_entries, dict):
        return None, []
    entry = all_entries.get(turn_id)
    if not isinstance(entry, dict):
        return None, []

    source: Optional[SourceReferenceDTO] = None
    raw_source = entry.get("source")
    if isinstance(raw_source, dict):
        try:
            source = SourceReferenceDTO.model_validate(raw_source)
        except Exception:
            source = None

    knowledge_sources: list[KnowledgeSourceReferenceDTO] = []
    raw_knowledge_sources = entry.get("knowledge_sources")
    if isinstance(raw_knowledge_sources, list):
        for raw_item in raw_knowledge_sources:
            if not isinstance(raw_item, dict):
                continue
            try:
                knowledge_sources.append(KnowledgeSourceReferenceDTO.model_validate(raw_item))
            except Exception:
                continue

    return source, dedupe_knowledge_source_references(knowledge_sources)
