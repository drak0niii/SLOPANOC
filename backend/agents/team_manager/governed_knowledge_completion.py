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

6A.14 ACTIVE PROCEDURE CONTINUITY CORRECTION -- a real, live-observed
follow-up-question failure DIFFERENT from (and downstream of) DEF-0026's
own fix below: "how do i handle HW Partial Fault?" correctly selected
both the active "HW Partial Fault" section and a merely SUPPORTING
sibling "HW Fault" section; the follow-ups "it's an RRU" and, more
strikingly, the exact repeated heading "HW Partial Fault" both still
produced "Do you mean the HW Partial Fault or HW Fault procedure?" --
DEF-0026's own `len(effective_candidates) > 1` ambiguity short-circuit
below fired unconditionally, without ever checking whether the CURRENT
turn's own text, the CURRENT validated `RequestContract.subject`, or an
already-established prior active-procedure anchor could deterministically
narrow the SAME two candidates down to one. This function now accepts two
further parameters -- `active_anchor` (an unrevalidated, previously
persisted `KnowledgeEvidenceSelectionKey`, revalidated here exactly like
`prior_governed_evidence` already is) and `request_contract_subject` (the
CURRENT turn's own already-provenance-verified 6A.13 `RequestContract
.subject`, never a raw/unverified model claim) -- and, ONLY when more than
one candidate survives the pre-existing revalidation/override logic below,
attempts `governed_evidence_continuity.resolve_active_candidate_among_
ambiguous` BEFORE falling back to the ambiguous clarification. See that
function's own docstring, and `governed_evidence_continuity.py`'s own
module-level "6A.14 ACTIVE PROCEDURE CONTINUITY CORRECTION" section, for
the full precedence design. DEF-0026's own revalidation/override/
ambiguity-clarification machinery is otherwise completely unmodified.

DEF-0026 CORRECTIVE PASS -- GOVERNED KNOWLEDGE FOLLOW-UP IDENTITY
CONTINUITY: a real, live-observed follow-up-question failure
(`"give me the first cmd"` after a genuinely successful VSWR-scoped
turn) traced to exactly this function's own `question` argument, which
`chat_service.py`'s `_remediation_question` always built from the raw
current-turn text alone -- with no way to recover which governed
procedure the conversation had just been discussing. See `backend/api/
governed_evidence_continuity.py`'s own module docstring for the full
design (revalidation, deduplication/ambiguity, the structural "explicit
topic change must win" check). This function now accepts `prior_
governed_evidence` -- zero or more previously-selected `KnowledgeEvidence
SelectionKey`s the CALLER already read from durable session state (this
module never reads session state itself). When exactly one of them
survives real-time revalidation AND the current `question` does not
itself name a different, real sibling procedure, the underlying
incident_manager call is scoped with a deterministic, identity-derived
question augmentation instead of the bare original text -- still a REAL
`knowledge_search`/`knowledge_select_evidence` round trip, SEARCH RESULT
!= EVIDENCE USED unchanged. More than one surviving, DISTINCT prior
identity is genuinely ambiguous and short-circuits to a deterministic
clarification WITHOUT ever invoking incident_manager. Zero valid prior
identity (the pre-existing, still-supported case) runs the exact same
unscoped path this function already had -- the only difference is a
more useful, still 100%-deterministic failure clarification in place of
the flat `SAFE_COMPLETION_FAILURE_TEXT` whenever the underlying problem
may be a missing procedure identity, per instruction. `enforce_
procedure_scoped_command_grounding` (DEF-0024, incident_manager/
evidence.py) is completely unmodified and untouched by this pass -- the
two safeguards are complementary: this one keeps the CORRECT procedure
selected; that one keeps any COMMAND grounded in whatever procedure was
actually selected.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Optional, Sequence

from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types

from backend.agents.incident_manager.schemas import IncidentManagerOutcome, IncidentManagerResponse
from backend.api.governed_evidence_continuity import (
    GENERIC_MISSING_PROCEDURE_CLARIFICATION,
    build_ambiguous_procedure_clarification,
    build_scoped_failure_clarification,
    build_scoped_remediation_question,
    detect_explicit_sibling_topic_override,
    resolve_active_candidate_among_ambiguous,
    revalidate_prior_governed_evidence,
)
from backend.api.session_service import APP_NAME
from backend.api.turn_context import bind_run_id, reset_run_id
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceSelectionKey
from backend.tools.knowledge.runtime import (
    discard_knowledge_run_evidence_state,
    get_knowledge_repository,
    snapshot_selected_knowledge_evidence,
)

_logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("backend.perf")

_APP_NAME = f"{APP_NAME}::governed-knowledge-completion-remediation"
"""Distinct namespace -- never the user-facing app name, mirrors every
other throwaway-session helper in this codebase (`_INTERNAL_SPECIALIST_
APP_NAME`, `_FAST_PATH_APP_NAME_SUFFIX`, `_retry_trusted_presentation_
once`'s own app name)."""

_deterministic_fallback_lock = threading.Lock()
_deterministic_fallback_run_ids: set[str] = set()
"""LIVE REGRESSION CORRECTIVE PASS -- in-process, never-persisted,
run-id-keyed signal, mirroring `troubleshooting_guidance_context.py`'s
own exact register/pop/discard pattern one layer over (never a new
architectural mechanism).

THE GAP THIS CLOSES: this module's own `build_ambiguous_procedure_
clarification`/`build_scoped_failure_clarification`/`GENERIC_MISSING_
PROCEDURE_CLARIFICATION` returns are ALL deterministic, Python-authored,
never-model-generated text (see each one's own docstring) -- genuinely
SAFE by construction, unlike this SAME function's own "real specialist
`summary`" success return, which remains exactly as unvalidated as
before and must still be subject to `requires_unstructured_response_
backstop` (LIVE-CORR-3B's own protection). A real, live-reproduced
defect proved `chat_service.py` cannot currently tell these two return
shapes apart from the returned text alone: an `EXACT_COMMAND`-classified
turn whose CONTRACT-level target/parameters are already fully resolved
(e.g. "the RRU is RRU-3" after "give me a command to restart an RRU")
resolves `ALLOW`/`may_emit_command=True` -- correct, since `derive_
execution_decision` only ever answers "is a command ELIGIBLE," never "a
concrete candidate already exists" (see that module's own docstring).
With no `TroubleshootingGuidance` captured (this module never even
invoked `incident_manager` for the ambiguous-procedure shape --
confirmed by direct trace, not inferred), `requires_unstructured_
response_backstop` unconditionally fires for this `ALLOW`+operationally-
shaped decision and OVERWRITES the ALREADY-CORRECT, ALREADY-SAFE "which
procedure do you mean" clarification with its OWN, strictly more
generic, and here MISLEADING "please confirm the missing details" text
-- misleading because no target detail is actually missing; a DIFFERENT
governed-evidence question (which document) is what remains unresolved.

Marked by THIS module at each of its three deterministic-fallback
return sites (never at the real-summary success return); read exactly
once by `chat_service.py`, correlated by the SAME `governed_completion_
run_id` its own guidance re-pop already uses, to skip that backstop
overwrite for -- and ONLY for -- this specific, already-safe shape."""


def _mark_governed_completion_deterministic_fallback(run_id: Optional[str]) -> None:
    """Called only from this module's own three fixed-template return
    sites, immediately before returning. A no-op for a missing `run_id`,
    mirroring every other run-scoped store's "never fail the turn over a
    side channel" discipline."""
    if not run_id:
        return
    with _deterministic_fallback_lock:
        _deterministic_fallback_run_ids.add(run_id)


def pop_governed_completion_deterministic_fallback(run_id: Optional[str]) -> bool:
    """Read exactly once, at `chat_service.py`'s own turn-completion
    boundary, immediately alongside its existing `pop_troubleshooting_
    guidance` re-pop for the SAME `governed_completion_run_id` -- removes
    the entry as it reads it. `False` for a missing `run_id` or a turn
    that never marked one (both the ordinary, ovewhelmingly common
    case)."""
    if not run_id:
        return False
    with _deterministic_fallback_lock:
        if run_id in _deterministic_fallback_run_ids:
            _deterministic_fallback_run_ids.discard(run_id)
            return True
        return False


def discard_governed_completion_deterministic_fallback(run_id: Optional[str]) -> None:
    """Backstop/defensive cleanup, mirroring `discard_troubleshooting_
    guidance`'s own sibling shape -- safe to call whether or not an entry
    exists."""
    if not run_id:
        return
    with _deterministic_fallback_lock:
        _deterministic_fallback_run_ids.discard(run_id)

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
    *,
    question: str,
    chat_topic: Optional[str],
    run_id: str,
    image_parts: Sequence[types.Part] = (),
    prior_governed_evidence: Sequence[KnowledgeEvidenceSelectionKey] = (),
    active_anchor: Optional[KnowledgeEvidenceSelectionKey] = None,
    request_contract_subject: Optional[str] = None,
) -> tuple[str, list[KnowledgeEvidenceItem]]:
    """Runs the real `incident_manager` (full toolset, unmodified) exactly
    once, deterministically, with `requires_governed_knowledge=True` --
    see this module's own docstring for the full rationale, and `backend/
    api/governed_evidence_continuity.py`'s own docstring for the DEF-0026
    scoping/ambiguity/revalidation design this function now applies
    BEFORE that one real call. Returns `(final_text, selected_evidence)`:

      - `outcome in ("ok", "no_result")`: `final_text` is incident_
        manager's own validated `summary` (schema-checked prose,
        Section B/A of the correction task's own completion rule --
        "not found" and "ok" are both legitimate, non-fabricated
        outcomes here), `selected_evidence` is whatever this call's own
        run genuinely selected (may legitimately be empty for a
        zero-evidence "not found" case).
      - Genuinely ambiguous prior governed evidence (DEF-0026): a
        deterministic clarification listing only real, governed section
        headings/titles is returned WITHOUT incident_manager ever being
        invoked for this call -- `selected_evidence` is `[]`.
      - Anything else (a genuine gateway/validation failure, or
        incident_manager's OWN compliance retry exhausting itself and
        returning `outcome="error"`): `final_text` is a deterministic
        failure clarification (DEF-0026: naming the real procedure this
        attempt was scoped to, when one existed, else `GENERIC_MISSING_
        PROCEDURE_CLARIFICATION`), `selected_evidence` is `[]` --
        deterministic safe failure (Section C), never team_manager's own
        discarded answer.

    `image_parts` -- B7 corrective pass: the SAME trusted `file_data`
    Part(s) this turn's original delegation already had (see this module's
    own docstring), passed by the caller (chat_service.py), never derived
    here. Appended AFTER the structured-request text part, in order,
    exactly mirroring `MultimodalAgentTool`'s own construction -- never a
    text part, never `inline_data`/bytes (the caller only ever passes what
    `trusted_image_parts_from_content` already filtered). Defaults to `()`
    for a text-only turn -- `content` is then byte-identical to before
    this pass.

    `prior_governed_evidence` -- DEF-0026: zero or more previously-
    selected `KnowledgeEvidenceSelectionKey`s the caller already read from
    durable session state (`LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY`).
    This function revalidates them in real time before using any of them
    for anything -- see `revalidate_prior_governed_evidence`.

    `active_anchor` -- 6A.14: the single, previously-persisted
    `ACTIVE_GOVERNED_PROCEDURE_STATE_KEY` identity, if any, the caller
    already read from durable session state. Revalidated here exactly
    like `prior_governed_evidence` before being trusted for anything.
    Used ONLY (a) to narrow a genuinely ambiguous multi-candidate set down
    to one, as the lowest-precedence of three deterministic narrowing
    signals (see `governed_evidence_continuity.resolve_active_candidate_
    among_ambiguous`), and (b) as the sole scoping candidate when the
    ordinary `prior_governed_evidence` list revalidates to zero surviving
    candidates but this anchor is still independently valid.

    `request_contract_subject` -- 6A.14: the CURRENT turn's own already
    provenance-verified 6A.13 `RequestContract.subject` (never a raw,
    unverified model claim -- the caller is responsible for having already
    run it through `validate_and_persist_request_contract`'s own
    provenance re-verification and for confirming its `run_id` matches
    the CURRENT run before passing it here). Used only as the second of
    three deterministic narrowing signals, per the same function.

    Never raises for an expected failure shape -- an unexpected exception
    from the nested Runner itself is allowed to propagate, exactly like
    every other nested-Runner call in this codebase (the caller,
    chat_service.py, already has its own top-level safe-error handling).
    """
    revalidated = await revalidate_prior_governed_evidence(prior_governed_evidence, get_knowledge_repository())
    override_heading = detect_explicit_sibling_topic_override(question, revalidated)
    effective_candidates = [] if override_heading is not None else revalidated
    if override_heading is not None:
        _logger.info(
            "governed_knowledge_completion: explicit sibling topic override detected (%r) -- "
            "ignoring prior governed evidence identity for this remediation run_id=%s",
            override_heading,
            run_id,
        )

    # 6A.14 Active Procedure Continuity Correction: only consulted when an
    # explicit sibling-topic override did NOT already fire above (a real
    # topic change must still win outright, exactly as before this pass)
    # and the pre-existing candidate set itself needs help -- either it is
    # genuinely ambiguous (more than one candidate), or it revalidated to
    # nothing at all but a separately-tracked active anchor may still be
    # usable. Revalidating a single-key list reuses `revalidate_prior_
    # governed_evidence` verbatim -- no second revalidation algorithm.
    active_candidate = None
    if override_heading is None and len(effective_candidates) != 1 and active_anchor is not None:
        revalidated_active = await revalidate_prior_governed_evidence([active_anchor], get_knowledge_repository())
        active_candidate = revalidated_active[0] if revalidated_active else None

    if override_heading is None and len(effective_candidates) > 1:
        resolved = resolve_active_candidate_among_ambiguous(
            question=question,
            request_contract_subject=request_contract_subject,
            candidates=effective_candidates,
            active_anchor=active_candidate,
        )
        if resolved is not None:
            _logger.info(
                "governed_knowledge_completion: deterministically narrowed an ambiguous prior-evidence "
                "candidate set to one active procedure run_id=%s",
                run_id,
            )
            effective_candidates = [resolved]
        else:
            _perf_logger.info("perf stage=governed_knowledge_completion_remediation_ambiguous_prior_evidence run_id=%s", run_id)
            # LIVE REGRESSION CORRECTIVE PASS: `incident_manager` is never
            # invoked for this shape (see this function's own early
            # return, immediately below) -- this deterministic, fixed-
            # template text is genuinely safe as-is; see `_mark_governed_
            # completion_deterministic_fallback`'s own docstring.
            _mark_governed_completion_deterministic_fallback(run_id)
            return build_ambiguous_procedure_clarification(effective_candidates), []
    elif override_heading is None and len(effective_candidates) == 0 and active_candidate is not None:
        effective_candidates = [active_candidate]

    scoped_procedure = effective_candidates[0] if len(effective_candidates) == 1 else None
    effective_question = build_scoped_remediation_question(question, scoped_procedure) if scoped_procedure is not None else question

    validated, selected_evidence = await _run_incident_manager_remediation_once(
        question=effective_question, chat_topic=chat_topic, run_id=run_id, image_parts=image_parts
    )

    if validated is not None and validated.get("outcome") in (IncidentManagerOutcome.OK.value, IncidentManagerOutcome.NO_RESULT.value):
        summary = validated.get("summary") or validated.get("detail")
        if summary:
            _perf_logger.info("perf stage=governed_knowledge_completion_remediation_ok run_id=%s", run_id)
            return summary, selected_evidence

    _logger.warning(
        "governed_knowledge_completion: remediation did not produce a usable governed answer run_id=%s", run_id
    )
    _perf_logger.info("perf stage=governed_knowledge_completion_remediation_failed run_id=%s", run_id)
    # LIVE REGRESSION CORRECTIVE PASS: both remaining returns are the
    # SAME class of fixed, deterministic, never-model-generated text as
    # the ambiguous-prior-evidence return above -- `incident_manager` DID
    # run here (unlike that branch), but its own response was rejected as
    # unusable, so neither line below ever echoes anything it said.
    _mark_governed_completion_deterministic_fallback(run_id)
    if scoped_procedure is not None:
        return build_scoped_failure_clarification(scoped_procedure), []
    return GENERIC_MISSING_PROCEDURE_CLARIFICATION, []


async def _run_incident_manager_remediation_once(
    *, question: str, chat_topic: Optional[str], run_id: str, image_parts: Sequence[types.Part]
) -> tuple[Optional[dict[str, Any]], list[KnowledgeEvidenceItem]]:
    """The exact, unmodified-in-substance Runner-invocation mechanism this
    module has always used -- extracted verbatim (DEF-0026) so the outer
    function above can apply its scoping/ambiguity decision BEFORE
    deciding whether, and with what question text, to make this one real
    call. Returns `(validated_response_dict_or_None, selected_evidence)`
    -- never raises for an expected failure shape (schema-validation
    failure becomes `None`, exactly as before).
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
        return validated, selected_evidence
    finally:
        discard_knowledge_run_evidence_state(run_id)
        reset_run_id(run_id_token)
        try:
            existing = await session_service.get_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
            if existing is not None:
                await session_service.delete_session(app_name=_APP_NAME, user_id=_REMEDIATION_USER_ID, session_id=session_id)
        except Exception:
            _logger.warning("governed_knowledge_completion: failed to delete internal remediation session run_id=%s", run_id)
