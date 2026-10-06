"""Technical Authority Engineer ProcedureAction id recovery: ONE bounded, tool-free re-selection.

Live defect (Prompt 4 acceptance, session 0233596d, turn 3): the server issued the current-run
ProcedureAction `pa-19ceca4905357d33fb50`; the specialist returned `pa-19ceca4905353d33fb50`. The
resolver correctly rejected it as UNKNOWN_ACTION and the turn failed closed -- the model mistyped an
opaque, server-owned identifier.

Invariant:
    the model chooses WHICH governed action (semantics); the server owns its canonical identity.
    A returned id is accepted only when it EXACTLY matches an action issued in THIS run. There is no
    approximate, prefix, edit-distance or historical matching, and the server never substitutes one
    action for another.

Recovery boundary: the specialist's final answer recommends a step whose procedure_action_id is not
an identity this run issued or offered, and this run HAS an issued catalog -> the server asks ONCE,
in the SAME specialist session with NO tools declared, to select exactly one identifier from that
catalog. Only an exact match of the issued catalog is adopted; anything else (another unknown id,
no id, a structurally unusable answer) is discarded and the ORIGINAL answer fails closed through the
existing UNKNOWN_ACTION path. A known id that later fails a semantic gate (selection, applicability,
target, progression, Command Authority, HITL, egress) is never re-selected. An adopted answer has no
authority of its own: it passes the same resolver and downstream chain as a first answer.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)

MAX_ACTION_ID_RESELECTIONS = 1
"""Initial action selection plus at most ONE re-selection per specialist invocation. Never a loop."""

RESELECTION_INSTRUCTION = (
    "SERVER PROCEDUREACTION ID RECOVERY: the procedure_action_id in your previous response ({invalid}) was not "
    "issued in this run.\n"
    "Select exactly one ProcedureAction from the current-run catalog below -- the action your previous response "
    "intended -- and return its action_id in diagnostic_step.procedure_action_id exactly as supplied.\n"
    "Do not modify an identifier. Do not invent an identifier. Do not create a new action. Do not search for new "
    "evidence. Do not call tools.\n"
    "If none of the supplied actions is the action you intended, set diagnostic_step.procedure_action_id to null.\n"
    "Return one complete TechnicalAuthorityResponse conforming exactly to the required schema.\n"
    "CURRENT-RUN PROCEDUREACTION CATALOG (identities only; it confers no authority -- every field is re-validated "
    "by the server):\n{catalog}"
)


def proposed_action_id(payload: Optional[Mapping[str, Any]]) -> Optional[str]:
    """The procedure_action_id of a RECOMMENDED step (the resolver's own entry condition), else None."""
    if not isinstance(payload, Mapping) or payload.get("outcome") != "recommended":
        return None
    step = payload.get("diagnostic_step")
    if not isinstance(step, Mapping):
        return None
    return str(step.get("procedure_action_id") or "").strip() or None


def unknown_action_id(
    payload: Optional[Mapping[str, Any]], issued_ids: Iterable[str], offered_ids: Iterable[str] = ()
) -> Optional[str]:
    """The recommended action id when it is NOT an identity this run issued (`issued_ids`) or the server
    offered to the specialist for exact issuance after the run (`offered_ids`: the known governed
    acquisition, offered gap-recovery alternatives). Exact set membership only."""
    action_id = proposed_action_id(payload)
    if action_id is None or action_id in set(issued_ids) or action_id in set(offered_ids):
        return None
    return action_id


def reselection_instruction(invalid_action_id: str, catalog: Iterable[Any]) -> str:
    """Narrow re-selection request over THIS run's issued catalog (the view the catalog tool returns)."""
    entries = [a.catalog_entry() for a in catalog]
    return RESELECTION_INSTRUCTION.format(invalid=json.dumps(invalid_action_id), catalog=json.dumps(entries, sort_keys=True))


def _trace(entry: dict[str, Any]) -> None:
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event(entry)
    except Exception:
        pass


def trace_reselection_request(specialist: str, run_id: Optional[str], initial_action_id: str, catalog_size: int) -> None:
    _trace({"stage": "action_id_recovery", "event": "request", "initial_action_id": initial_action_id,
            "reason": "unknown_action", "retry": 1, "tools_enabled": False, "catalog_size": catalog_size})
    logger.warning(
        "action_id_recovery specialist=%s run_id=%s initial_action_id=%s reason=unknown_action retry=1 tools_enabled=false catalog_size=%d",
        specialist, run_id, initial_action_id, catalog_size,
    )


def trace_reselection_result(specialist: str, run_id: Optional[str], returned_action_id: Optional[str], exact_match: bool) -> None:
    _trace({"stage": "action_id_recovery", "event": "result", "returned_action_id": returned_action_id, "exact_match": exact_match})
    logger.warning(
        "action_id_recovery specialist=%s run_id=%s returned_action_id=%s exact_match=%s",
        specialist, run_id, returned_action_id, str(exact_match).lower(),
    )
