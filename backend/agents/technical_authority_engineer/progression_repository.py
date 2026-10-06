"""Where the authoritative TroubleshootingProgression lives, and how session state is derived.

    session linked to a Case  -> CASE scope: `slopanoc_case_troubleshooting_progressions`, versioned
                                 (compare-and-swap; a stale writer gets ProgressionConflict)
    session not linked        -> SESSION scope: the session-state document (no Case exists to own it)

The link is resolved from the authoritative session->case link table; the session-state
`active_case_id` is only a hint that a lookup is worth doing.

Session state never holds a competing source of truth in case scope: `troubleshooting_state` /
`troubleshooting_threads` are READ projections re-derived from the progression on every save, and
`troubleshooting_progression_ref` is a non-authoritative {case_id, version} pointer.

One-way migration: on the first case-scope load of a session, its own session-scope progression
(or, for older sessions, its legacy fault-thread state) is imported into the Case progression --
only faults the Case does not already have -- and the session copy is removed. Projections are
never read back.
"""
from __future__ import annotations

from typing import Any, MutableMapping, Optional

from backend.agents.technical_authority_engineer.troubleshooting_threads import save_projections
from backend.cases.progression_store import CaseProgressionStore, ProgressionConflict, get_case_progression_store
from backend.cases.troubleshooting_progression import (
    PROGRESSION_STATE_KEY,
    ProgressionEvent,
    TroubleshootingProgression,
    load_or_adapt_progression,
    save_progression,
)

PROGRESSION_REF_KEY = "troubleshooting_progression_ref"
ACTIVE_CASE_HINT_KEY = "active_case_id"

__all__ = ["PROGRESSION_REF_KEY", "ProgressionConflict", "ProgressionRepository", "merge_missing_faults"]


def merge_missing_faults(target: TroubleshootingProgression, source: TroubleshootingProgression, origin: str) -> list[str]:
    """Import (one way) every fault of `source` that `target` does not have, with its steps,
    hypotheses and open questions. Existing faults in `target` are never modified."""
    imported = [fid for fid in source.faults if fid not in target.faults]
    if not imported:
        return []
    next_sequence = max((s.sequence for s in target.steps), default=0)
    for fid in imported:
        target.faults[fid] = source.faults[fid]
        for step in source.steps_for(fid):
            next_sequence += 1
            target.steps.append(step.model_copy(update={"sequence": next_sequence}))
        target.hypotheses.extend(h for h in source.hypotheses if h.fault_id == fid)
        target.open_questions.extend(q for q in source.open_questions if q.fault_id == fid)
    for session_id in source.session_ids:
        if session_id not in target.session_ids:
            target.session_ids.append(session_id)
    target.events.append(ProgressionEvent(event="faults_imported", details={"origin": origin, "faults": imported}))
    target.version += 1
    return imported


class ProgressionRepository:
    def __init__(self, state: MutableMapping[str, Any], *, session_id: Optional[str], store: Optional[CaseProgressionStore] = None) -> None:
        self.state = state
        self.session_id = session_id
        self._store = store
        self.case_id: Optional[str] = None
        self.version: Optional[int] = None
        self.imported: list[str] = []

    @property
    def case_scoped(self) -> bool:
        return self.case_id is not None

    def _get_store(self) -> CaseProgressionStore:
        if self._store is None:
            self._store = get_case_progression_store()
        return self._store

    async def load(self) -> TroubleshootingProgression:
        if self.state.get(ACTIVE_CASE_HINT_KEY) or self.state.get(PROGRESSION_REF_KEY):
            self.case_id = await self._get_store().linked_case_id(self.session_id)
        if not self.case_scoped:
            return load_or_adapt_progression(self.state, session_id=self.session_id) or TroubleshootingProgression()
        ref = self.state.get(PROGRESSION_REF_KEY)
        migrated = isinstance(ref, dict) and ref.get("case_id") == self.case_id
        # One-way: a session already migrated into this Case contributes nothing from its state.
        local = None if migrated else load_or_adapt_progression(self.state, session_id=self.session_id)
        stored = await self._get_store().load(self.case_id)
        if stored is None:
            progression = local or TroubleshootingProgression(case_id=self.case_id)
            progression.case_id = self.case_id
            if local is not None:
                progression.events.append(ProgressionEvent(event="migrated_to_case", details={"session_id": self.session_id}))
                self.imported = sorted(progression.faults)
            self.version = None  # created on first save
        else:
            progression, self.version = stored.progression, stored.version
            if local is not None:
                self.imported = merge_missing_faults(progression, local, origin=f"session:{self.session_id}")
        if self.session_id and self.session_id not in progression.session_ids:
            progression.session_ids.append(self.session_id)
        return progression

    async def save(self, progression: TroubleshootingProgression, active_fault_id: Optional[str] = None) -> None:
        """Persist to the authoritative store (compare-and-swap in case scope), then re-derive the
        session-state projections. Raises ProgressionConflict on a stale case-scope write."""
        if self.case_scoped:
            self.version = await self._get_store().save(self.case_id, progression, self.version, self.session_id)
            self.state[PROGRESSION_REF_KEY] = {"case_id": self.case_id, "version": self.version}
            if self.state.get(PROGRESSION_STATE_KEY) is not None:
                self.state[PROGRESSION_STATE_KEY] = None  # the session copy was migrated; never a second authority
        else:
            save_progression(self.state, progression)
        save_projections(self.state, progression, active_fault_id)
