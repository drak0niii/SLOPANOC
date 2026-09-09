"""In-process, never-persisted, run-id-keyed store of THIS turn's
captured `TroubleshootingGuidance` (A5 final corrective pass), plus the
deterministic Python renderer that turns it into user-visible text.

WHY THIS EXISTS (see docs/AGENT_CONTRACT.md's general "prompt
instructions alone are not a sufficient control" principle, already
applied identically to Teams evidence stripping and governed-knowledge
selection): real live-Gemini testing during A5's own live validation
proved that a prompt paragraph alone -- however emphatic -- does not
reliably keep incident_manager's free-text `summary` to one diagnostic
action. The fix is NOT a stronger paragraph; it is a TYPED field
(`IncidentManagerResponse.troubleshooting_guidance`,
`backend/agents/incident_manager/schemas.py`) the model populates via
Gemini's own constrained/schema-guided structured output (far more
reliable than free-prose self-restraint), plus a Python renderer that
constructs the FINAL text directly from that field's typed sub-fields --
never by parsing/truncating the model's own prose after the fact.

LIFECYCLE, mirroring `multimodal_turn_context.py`'s exact pattern:
`register_troubleshooting_guidance` is called from `incident_manager`'s
own `after_agent_callback` (`evidence.py`'s
`enforce_incident_manager_response_integrity`) the instant its
structured response is parsed -- deep inside the nested `AgentTool`/
remediation call chain, correlated via `current_run_id()`
(`turn_context.py`'s own ContextVar, already proven safe for this exact
correlation problem). `pop_troubleshooting_guidance` is read exactly
once, at `chat_service.py`'s own turn-completion boundary (the same
call site `governed_knowledge_completion.py`'s remediation already
uses), and REMOVES the entry as it reads it -- so a captured guidance
can only ever override `final_text` once, and a turn that never
populated the field leaves this store untouched. `discard_troubleshooting_guidance`
is the same-shape backstop cleanup from chat_service.py's own `finally`
block, safe to call whether or not an entry exists.

NOT TROUBLESHOOTING STATE: nothing here is persisted (never ADK session
state, never Cloud SQL, never the frontend, never a log line) or reused
across turns -- a fresh capture happens every turn that populates the
field, and this store never accumulates history. This is purely an
in-process, single-turn correlation aid, exactly like `multimodal_turn_
context.py`'s own image-attachment-id store.
"""
from __future__ import annotations

import threading
from typing import Optional

from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
from backend.api.turn_context import current_run_id

_lock = threading.Lock()
_store: dict[str, TroubleshootingGuidance] = {}


def register_troubleshooting_guidance(run_id: Optional[str], guidance: Optional[TroubleshootingGuidance]) -> None:
    """Called once, from `incident_manager`'s own `after_agent_callback`,
    the instant its structured response is parsed. A no-op for a missing
    `run_id` or `guidance` -- never raises, mirroring `multimodal_turn_
    context.register_run_images`'s own "never fail the turn over a side
    channel" discipline. Overwrites any prior entry for the same
    `run_id` (the LATEST parse of this turn's own response is always the
    authoritative one -- e.g. after a provenance-compliance retry).
    """
    if not run_id or guidance is None:
        return
    with _lock:
        _store[run_id] = guidance


def pop_troubleshooting_guidance(run_id: Optional[str]) -> Optional[TroubleshootingGuidance]:
    """Read exactly once, at `chat_service.py`'s own turn-completion
    boundary -- removes the entry as it reads it, so a captured guidance
    can only ever be applied once. Returns `None` for a missing `run_id`
    or a turn that registered no guidance -- both safe, ordinary "not a
    troubleshooting-guidance turn" outcomes, never an error.
    """
    if not run_id:
        return None
    with _lock:
        return _store.pop(run_id, None)


def discard_troubleshooting_guidance(run_id: str) -> None:
    """Backstop/normal cleanup, called from chat_service.py's own
    `finally` block alongside its sibling context stores. Safe to call
    whether or not an entry exists (e.g. `pop_troubleshooting_guidance`
    already consumed it, or none was ever registered).
    """
    with _lock:
        _store.pop(run_id, None)


def render_troubleshooting_guidance(guidance: TroubleshootingGuidance) -> str:
    """The deterministic Python renderer -- the ONLY place
    `TroubleshootingGuidance` becomes user-visible text. Reads ONLY the
    fields appropriate to `interaction_mode`:

    - `NEXT_STEP`: `interpretation` (if any), `next_action`, `command`
      (if any, at most one), `evidence_requested`. `full_procedure_steps`
      is NEVER read in this branch -- structurally impossible for a
      later procedural step the model may have also populated there to
      leak into the rendered text, regardless of what free-text
      `summary` the model separately wrote.
    - `FULL_PROCEDURE`: `interpretation` (if any) followed by every
      `full_procedure_steps` entry in order, each as one numbered
      action (+ its own command, if any) -- `next_action`/`command`/
      `evidence_requested` are NOT separately re-rendered here (the
      steps list is authoritative for this mode).

    Never fabricates, paraphrases, or reorders a command -- every
    `command`/`step.command` value is emitted byte-for-byte exactly as
    the model supplied it (which is itself required, by
    `TroubleshootingGuidance`'s own field docs and incident_manager's
    existing COMMAND TRUST discipline, to be reproduced verbatim from
    Approved, selected governed knowledge).
    """
    if guidance.interaction_mode == TroubleshootingInteractionMode.FULL_PROCEDURE:
        blocks: list[str] = []
        if guidance.interpretation:
            blocks.append(guidance.interpretation)
        for index, step in enumerate(guidance.full_procedure_steps, start=1):
            step_block = f"{index}. {step.action}"
            if step.command:
                step_block += f"\n\nRun:\n\n{step.command}"
            blocks.append(step_block)
        return "\n\n".join(blocks)

    blocks = []
    if guidance.interpretation:
        blocks.append(guidance.interpretation)
    if guidance.next_action:
        blocks.append(guidance.next_action)
    if guidance.command:
        blocks.append(f"Run:\n\n{guidance.command}")
    if guidance.evidence_requested:
        blocks.append(guidance.evidence_requested)
    return "\n\n".join(blocks)
