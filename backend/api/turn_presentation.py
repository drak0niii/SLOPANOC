"""Durable, per-turn presentation state management in `session.state`.

Keyed by stable ADK `turn_id` (`invocation_id`). Persists completed turn
presentation elements needed to reconstruct the rich conversational UI on
session history reload (GET /api/sessions/{id}/history):
- execution timing (worked duration)
- run trace activity steps and final outcome
- selection cards (along with phase and resolved option label)
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

TURN_PRESENTATION_STATE_KEY = "turn_presentation"


def build_turn_presentation_delta(
    existing: Any,
    turn_id: str,
    presentation_data: dict[str, Any],
) -> dict[str, Any]:
    """Merges a new per-turn presentation record into the state delta."""
    if not isinstance(turn_id, str) or not turn_id:
        return existing if isinstance(existing, dict) else {}
    base = dict(existing) if isinstance(existing, dict) else {}
    base[turn_id] = presentation_data
    return base


def resolve_turn_presentation(
    state: Any,
    turn_id: str,
) -> Optional[dict[str, Any]]:
    """Retrieves presentation data for a specific turn if stored."""
    if not hasattr(state, "get") or not isinstance(turn_id, str):
        return None
    all_pres = state.get(TURN_PRESENTATION_STATE_KEY)
    if isinstance(all_pres, dict):
        val = all_pres.get(turn_id)
        if isinstance(val, dict):
            return val
    return None


def update_turn_presentation_selection(
    existing: Any,
    selection_id: str,
    phase: str,
    selected_label: Optional[str] = None,
) -> dict[str, Any]:
    """Finds and updates the selection phase/label inside turn_presentation."""
    if not isinstance(existing, dict):
        return {}
    base = dict(existing)
    for turn_id, pres in list(base.items()):
        if isinstance(pres, dict):
            sel = pres.get("selection")
            if isinstance(sel, dict) and sel.get("selection_id") == selection_id:
                updated_pres = dict(pres)
                updated_sel = dict(sel)
                updated_sel["phase"] = phase
                if selected_label is not None:
                    updated_sel["selected_label"] = selected_label
                updated_pres["selection"] = updated_sel
                base[turn_id] = updated_pres
    return base
