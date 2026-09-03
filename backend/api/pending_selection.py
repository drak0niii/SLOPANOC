"""Deterministic mapper: ADK session state's active `PendingSelection` ->
the safe, frontend-facing `PendingSelectionDTO`.

Mirrors pending_action.py exactly: plain Python only, the model never
sees or decides any part of this mapping. Deliberately excludes
`option_targets` (the internal option_id -> real chat_id/topic map) and
`pending_write_message` -- neither ever reaches the frontend; only
`option_id`/`label` per option, matching the "never expose a raw Teams
chat id" requirement.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from backend.api.schemas import PendingSelectionDTO, SelectionOptionDTO
from backend.selection.schemas import SelectionStatus
from backend.selection.service import load_active_selection


def map_pending_selection(session_state: Mapping[str, Any]) -> Optional[PendingSelectionDTO]:
    """Returns `None` when there is no active selection in this session's
    state, OR when the active one is no longer PENDING (a resolved/
    skipped selection is presented to the frontend only via the direct
    `/choose`/`/skip` response at the moment it happens -- it is not
    re-emitted as a fresh `selection.pending` event on a later turn).
    """
    selection = load_active_selection(session_state)
    if selection is None or selection.status != SelectionStatus.PENDING:
        return None

    return PendingSelectionDTO(
        selection_id=selection.selection_id,
        kind=selection.kind.value,
        status=selection.status.value,
        requested_value=selection.requested_value,
        options=[SelectionOptionDTO(option_id=opt.option_id, label=opt.label) for opt in selection.options],
    )
