"""FOURTH pre-4H correction pass -- the Team Manager completion-boundary
governed-knowledge gate.

WHY THIS EXISTS (see this package's `source_requirements.py` for the full
live-failure narrative): the third correction pass's enforcement
(`backend.agents.incident_manager.provenance_compliance`) lives INSIDE
incident_manager's own invocation -- it is a real, structural guarantee
for the turns where incident_manager actually runs, but it has no way to
act on a turn where team_manager's own model simply never delegates at
all (a live-reproduced failure: team_manager answered a "use governed
knowledge" request by reciting an EARLIER turn's already-stale governed
facts from its own conversation history, with zero current `knowledge_
search`/`knowledge_select_evidence`). `chat_service.py` is the one place
that (a) always runs, regardless of what team_manager's model chose to
do, (b) still has this turn's own `run_id`/trusted evidence state
available, and (c) is the actual place the final, user-facing `final_text`
is decided -- exactly the "closest reliable backend boundary" the
correction task asked for.

THIS MODULE'S OWN JOB IS NARROW: given a turn whose OWN structured
declaration (`record_source_requirements`, captured by `SourceRequirements
Capture`) said `requires_governed_knowledge=True`, but this turn's own
trusted, run-scoped SELECTED evidence is empty, deterministically run the
REAL, UNMODIFIED, full-toolset `incident_manager` agent -- not team_
manager's own (unreliable) choice -- via a throwaway internal session,
mirroring the SAME "fresh InMemorySessionService, one nested Runner call,
delete the session afterward" pattern already established three times in
this codebase (`chat_service._retry_trusted_presentation_once`,
`direct_read_fast_path._run_trusted_presentation`, `read_continuation_
execution._run_specialist_and_collect`) -- never a new mechanism.

Running the REAL `incident_manager` here means its OWN third-correction-
pass `after_agent_callback` (`enforce_incident_manager_response_
integrity`) is STILL the thing that actually enforces "available evidence
must be selected before completion" for this call -- this module adds NO
second, competing selection-enforcement mechanism; it only guarantees
incident_manager is genuinely invoked, with `requires_governed_
knowledge=True`, for a turn that structurally requires it.

A FRESH, DEDICATED `run_id` (never the original turn's own, already-
`reset`/discarded one) is bound for the DURATION of this one remediation
call only, so `knowledge_search`/`knowledge_select_evidence`'s own
run-scoped trusted state (`backend.tools.knowledge.runtime`) is genuinely
NEW for this attempt -- current-turn isolation cuts both ways: this
remediation must not (and structurally cannot) inherit or reuse ANY
evidence a prior turn, or even this same turn's own already-finished
Runner call, selected.

NO SEARCH/TEAMS REPEAT BEYOND WHAT THIS ONE CALL ITSELF PERFORMS: this
function is invoked from chat_service.py at most once per turn (see its
own call site) -- it is itself the "one bounded ... attempt" the
correction task describes, using the SAME real agent (with its own
internal one-retry compliance mechanism) rather than a second, separate
retry loop layered on top.

B7 LIVE-REGRESSION CORRECTIVE PASS -- TRUSTED CURRENT-TURN IMAGE EVIDENCE
NOW PROPAGATED: a real combined image + Teams + governed-KM live
validation proved this remediation's own `content` (below) was built
TEXT-ONLY, unconditionally -- this bounded remediation is a bare
`Runner.run_async` call, never routed through `AgentTool`/
`MultimodalAgentTool`, so B6's own image-propagation mechanism (which
only intercepts `AgentTool.run_async`, reading `tool_context.user_
content`) never had a way to reach it. The observable defect: a turn
whose FIRST incident_manager execution had real image evidence (via the
original delegation's `MultimodalAgentTool` path) but finished without
selected governed-KM evidence correctly triggered THIS remediation --
which then ran the real, unmodified `incident_manager` a SECOND time,
this time with NO image evidence at all, so the model had nothing to
ground its "observed" values in and invented them. `enforce_governed_
knowledge_at_completion` now accepts `image_parts` -- the SAME trusted
`file_data` Part(s) already sitting in chat_service.py's own turn-level
`Content` (extracted via `backend.api.multimodal_turn_context.trusted_
image_parts_from_content`, never re-derived from attachment ids, never a
fresh storage/DB lookup) -- and appends them to this remediation's own
Content, exactly mirroring `MultimodalAgentTool`'s own "text part first,
then trusted image parts, in order" construction. A text-only turn passes
an empty sequence and this remediation's `Content` is byte-identical to
before this pass -- zero behavior change for the non-multimodal case.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.agents.incident_manager.schemas import IncidentManagerOutcome, IncidentManagerResponse
from backend.api.session_service import APP_NAME
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem
from backend.tools.knowledge.runtime import discard_knowledge_run_evidence_state, snapshot_selected_knowledge_evidence

_logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("backend.perf")

_APP_NAME = f"{APP_NAME}::governed-knowledge-completion-remediation"
"""Distinct namespace -- never the user-facing app name, mirrors every
other throwaway-session helper in this codebase (`_INTERNAL_SPECIALIST_
APP_NAME`, `_FAST_PATH_APP_NAME_SUFFIX`, `_retry_trusted_presentation_
once`'s own app name)."""

_REMEDIATION_USER_ID = "governed-knowledge-completion-remediation"

SAFE_COMPLETION_FAILURE_TEXT = (
    "Governed knowledge could not be validated for this request. Please try again."
)
"""Deterministic, Python-authored safe-failure text for the case where the
remediation call itself fails/errors -- distinct wording from provenance_
compliance.py's own safe-failure text (that one is about a SPECIFIC
retrieved-but-unselected item; this one covers a broader "the deterministic
remediation attempt itself did not produce a trustworthy result" case),
never derived from team_manager's own discarded, unproven answer text.
"""


async def enforce_governed_knowledge_at_completion(
    *, question: str, chat_topic: Optional[str], run_id: str, image_parts: Sequence[types.Part] = ()
) -> tuple[str, list[KnowledgeEvidenceItem]]:
    """Runs the real `incident_manager` (full toolset, unmodified) exactly
    once, deterministically, with `requires_governed_knowledge=True` --
    see this module's own docstring for the full rationale. Returns
    `(final_text, selected_evidence)`:

      - `outcome in ("ok", "no_result")`: `final_text` is incident_
        manager's own validated `summary` (schema-checked prose,
        Section B/A of the correction task's own completion rule --
        "not found" and "ok" are both legitimate, non-fabricated
        outcomes here), `selected_evidence` is whatever this call's own
        run genuinely selected (may legitimately be empty for a
        zero-evidence "not found" case).
      - Anything else (a genuine gateway/validation failure, or
        incident_manager's OWN compliance retry exhausting itself and
        returning `outcome="error"`): `final_text` is `SAFE_COMPLETION_
        FAILURE_TEXT`, `selected_evidence` is `[]` -- deterministic safe
        failure (Section C), never team_manager's own discarded answer.

    `image_parts` -- B7 corrective pass: the SAME trusted `file_data`
    Part(s) this turn's original delegation already had (see this module's
    own docstring), passed by the caller (chat_service.py), never derived
    here. Appended AFTER the structured-request text part, in order,
    exactly mirroring `MultimodalAgentTool`'s own construction -- never a
    text part, never `inline_data`/bytes (the caller only ever passes what
    `trusted_image_parts_from_content` already filtered). Defaults to `()`
    for a text-only turn -- `content` is then byte-identical to before
    this pass.

    Never raises for an expected failure shape -- an unexpected exception
    from the nested Runner itself is allowed to propagate, exactly like
    every other nested-Runner call in this codebase (the caller,
    chat_service.py, already has its own top-level safe-error handling).
    """
    from backend.agents.incident_manager.agent import incident_manager
    from backend.agents.incident_manager.schemas import IncidentManagerRequest

    request = IncidentManagerRequest(chat_topic=chat_topic, question=question, requires_governed_knowledge=True)
    content = types.Content(
        role="user",
        parts=[
            types.Part.from_text(text=request.model_dump_json(exclude_none=True)),
            *image_parts,
        ],
    )

    session_service = InMemorySessionService()
    session_id = f"governed-completion::{run_id}"
    run_id_token = bind_run_id(run_id)
    _perf_logger.info("perf stage=governed_knowledge_completion_remediation_start run_id=%s", run_id)
    try:
        await session_service.create_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
        runner = Runner(
            app_name=_APP_NAME, agent=incident_manager, session_service=session_service, memory_service=InMemoryMemoryService()
        )
        last_content: Optional[types.Content] = None
        try:
            async for event in runner.run_async(user_id=_REMEDIATION_USER_ID, session_id=session_id, new_message=content):
                if event.content:
                    last_content = event.content
        finally:
            await runner.close()

        merged_text = None
        if last_content is not None and last_content.parts:
            merged_text = "\n".join(p.text for p in last_content.parts if p.text and not p.thought).strip() or None

        validated: Optional[dict[str, Any]] = None
        if merged_text is not None:
            try:
                validated = IncidentManagerResponse.model_validate_json(merged_text).model_dump(mode="json", exclude_none=True)
            except Exception:
                _logger.warning("governed_knowledge_completion: remediation response failed schema validation run_id=%s", run_id)
                validated = None

        selected_evidence = snapshot_selected_knowledge_evidence(run_id)

        if validated is not None and validated.get("outcome") in (IncidentManagerOutcome.OK.value, IncidentManagerOutcome.NO_RESULT.value):
            summary = validated.get("summary") or validated.get("detail")
            if summary:
                _perf_logger.info("perf stage=governed_knowledge_completion_remediation_ok run_id=%s", run_id)
                return summary, selected_evidence

        _logger.warning(
            "governed_knowledge_completion: remediation did not produce a usable governed answer run_id=%s", run_id
        )
        _perf_logger.info("perf stage=governed_knowledge_completion_remediation_failed run_id=%s", run_id)
        return SAFE_COMPLETION_FAILURE_TEXT, []
    finally:
        discard_knowledge_run_evidence_state(run_id)
        reset_run_id(run_id_token)
        try:
            existing = await session_service.get_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
            if existing is not None:
                await session_service.delete_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
        except Exception:
            _logger.warning("governed_knowledge_completion: failed to delete internal remediation session run_id=%s", run_id)
