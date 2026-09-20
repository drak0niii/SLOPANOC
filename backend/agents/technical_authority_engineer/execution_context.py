"""Run-scoped in-process tracking of Technical Authority Engineer executions.

Ensures that chat_service and completion boundaries are specialist-aware:
- Records when Technical Authority Engineer evaluates a fault in the current run
- Prevents Incident Manager from overwriting Technical Authority diagnoses
- Cleans up deterministically at turn-end in chat_service.py's finally block
"""
from __future__ import annotations

import threading
from typing import Any, Optional

_lock = threading.Lock()
_executions: dict[str, dict[str, Any]] = {}


def record_technical_authority_execution(run_id: str, execution_result: dict[str, Any]) -> None:
    """Records that Technical Authority Engineer produced an evaluation in this run."""
    if not run_id:
        return
    with _lock:
        _executions[run_id] = execution_result


def has_technical_authority_executed(run_id: str) -> bool:
    """Returns True if Technical Authority Engineer executed in this run."""
    if not run_id:
        return False
    with _lock:
        return run_id in _executions


def get_technical_authority_execution(run_id: str) -> Optional[dict[str, Any]]:
    """Retrieves the Technical Authority Engineer execution result for this run, if any."""
    if not run_id:
        return None
    with _lock:
        return _executions.get(run_id)


def discard_technical_authority_execution(run_id: str) -> None:
    """Discards run-scoped Technical Authority execution tracking at turn end."""
    if not run_id:
        return
    with _lock:
        _executions.pop(run_id, None)
