"""THIRD pre-4H correction pass: SEARCH RESULT != EVIDENCE USED.

LIVE DEFECT THIS MODULE FIXES: a combined Teams + governed-knowledge
request (`requires_governed_knowledge=True`) could reach `knowledge_search`,
receive non-empty AVAILABLE evidence, use it in prose, and still complete
successfully WITHOUT ever calling `knowledge_select_evidence` -- so the
final answer relied on governed content the trusted backend never validated
as SELECTED, and the UI correctly (but confusingly) showed no "Source ·
Governed knowledge" chip for an answer that plainly used governed content.
MODEL SELECTION != PROVENANCE (backend/knowledge/provenance/contracts.py's
own `KnowledgeEvidenceSelectionKey` docstring) was being satisfied on the
happy path only, never enforced.

THE ONE INVARIANT THIS MODULE ENFORCES: when a delegated request
structurally REQUIRED governed knowledge and this run's own `knowledge_
search` produced at least one AVAILABLE evidence item, incident_manager
must not complete with SELECTED evidence still empty. See `enforce_
governed_knowledge_selection`, the single entry point evidence.py's
combined `after_agent_callback` calls.

WHY THIS BELONGS AT THE INCIDENT MANAGER BOUNDARY (`after_agent_callback`),
NOT team_manager or a tool: `after_agent_callback` fires exactly once,
after incident_manager's own nested Runner loop has already produced its
(would-be) final response -- verified against the installed ADK source,
`agents/base_agent.py` (`_handle_after_agent_callback`) and `flows/
llm_flows/base_llm_flow.py` (the SAME verification this codebase already
relied on for `evidence.py`'s own `strip_unverified_evidence`, unchanged).
This is the one point that can see the model's OWN final text AND this
run's trusted KM evidence state (`backend.tools.knowledge.runtime`) AND
still intervene before that text ever reaches team_manager.

WHY A NESTED, THROWAWAY RUNNER FOR THE RETRY -- NEVER A FABRICATED FUNCTION
CALL, NEVER A PRIVATE ADK LOOP-CONTINUATION HACK: this mirrors chat_
service.py's own already-established `_retry_trusted_presentation_once`
pattern exactly -- a fresh `InMemorySessionService`, one bounded extra
turn, the retry's own result captured from its final event text. The KEY
property that makes this safe and correct WITHOUT repeating retrieval:
`run_id` (`backend.api.turn_context.current_run_id`) is a `ContextVar`
already bound once at the very top of chat_service.py's own turn, BEFORE
this whole call chain (team_manager's Runner -> incident_manager's nested
AgentTool Runner -> this retry's own nested Runner) begins -- a value
already bound before any task-splitting IS correctly visible to code
running deeper in the same call chain (the exact asymmetry `direct_read_
fast_path.py`'s own module docstring already documents and relies on for
the identical reason: a WRITE performed inside a child task does not
propagate back out, but a value already bound before the split is copied
INTO every child task and reads of it succeed everywhere). So `knowledge_
select_evidence`, called for real by the model during this retry, resolves
the SAME `KnowledgeRunEvidenceState` (backend/tools/knowledge/runtime.py)
the original `knowledge_search` call already populated -- no second
search, no fabricated selection, just the SAME model given one more real,
tool-calling turn to declare what it relied on.

BOUNDED TO EXACTLY ONE RETRY, STRUCTURALLY: `_run_compliance_retry` is
called at most once per turn, from `enforce_governed_knowledge_selection`
below. The retry agent variant (`_compliance_retry_incident_manager`)
deliberately does NOT inherit `enforce_incident_manager_response_
integrity` as its own `after_agent_callback` (it is explicitly set to
`None`) -- so a non-compliant retry attempt can never recurse into
invoking a second retry of its own. This is a structural guarantee (the
retry agent object itself cannot re-enter this module), not merely a
counter that could be miscounted.

NO AUTOMATIC EVIDENCE SELECTION: this module never calls `knowledge_
select_evidence` (or `backend.tools.knowledge.runtime.select_evidence`)
itself, and never copies AVAILABLE evidence into SELECTED evidence
directly. The retry agent still has to call the real `knowledge_select_
evidence` tool, with the SAME closed, identity-only `KnowledgeEvidence
SelectionKey` contract and the SAME `validate_evidence_selection`
authority every other call to that tool already goes through -- this
module adds no alternate selection path.

NO ANSWER PARSING: this module never inspects the model's own answer text
for citations, keywords, or the presence/absence of specific facts -- the
ENTIRE decision of whether to intervene is made from trusted run state
(`get_available_knowledge_evidence`/`snapshot_selected_knowledge_
evidence`) and the structured `requires_governed_knowledge` request field,
never from parsing prose.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from google.adk.memory import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import BaseModel

from backend.agents.incident_manager.schemas import IncidentManagerOutcome, IncidentManagerResponse
from backend.api.turn_context import current_run_id
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceSelectionKey
from backend.tools.knowledge.runtime import get_available_knowledge_evidence, snapshot_selected_knowledge_evidence
from backend.tools.knowledge.tools import knowledge_select_evidence

_logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("backend.perf")

_RETRY_APP_NAME = "incident_manager::provenance-compliance-retry"
_RETRY_USER_ID = "provenance-compliance-retry"
"""Deliberately distinct from the real `APP_NAME` (mirrors read_
continuation_execution.py's `_INTERNAL_SPECIALIST_APP_NAME`/direct_read_
fast_path.py's own throwaway-session namespacing) -- this session is never
user-facing and never listed alongside a real SLOPANOC chat."""

_SAFE_FAILURE_TEXT = json.dumps(
    IncidentManagerResponse(
        outcome=IncidentManagerOutcome.ERROR,
        detail=(
            "Governed knowledge was retrieved, but the evidence used for "
            "the response could not be validated."
        ),
    ).model_dump(mode="json", exclude_none=True)
)
"""Deterministic, Python-authored safe-failure text -- reuses the SAME
`IncidentManagerOutcome.ERROR` shape `read_continuation_execution.py`'s
own `_fail_closed_result` already establishes for an analogous "cannot
trust this result" case (leaves `chat_id`/`chat_title`/`summary`/`evidence`
all unset, exactly like that precedent -- no partial/internal state is
exposed alongside the safe wording). Never derived from, or influenced
by, the model's own unproven answer text.
"""


def _requires_governed_knowledge(callback_context: Any) -> bool:
    """Reads `IncidentManagerRequest.requires_governed_knowledge` back
    from `callback_context.user_content` -- the SAME `types.Content` ADK's
    own `AgentTool.run_async` built for THIS incident_manager invocation
    (a public, documented `ReadonlyContext.user_content` property; see the
    second correction pass's own verification, unchanged here).

    Deliberately duplicated from (never imported from) `direct_read_fast_
    path._requires_governed_knowledge`: that module's own Teams/KM routing
    behavior is frozen and verified-correct as of this pass and must not
    be touched, even to add a cross-module import. Both copies share the
    identical fail-closed direction for the same reason: incorrectly
    treating a request as requiring governed knowledge, when unsure, is
    the SAFE direction in both call sites (here, it can only ever trigger
    the checks below, which are themselves no-ops whenever no AVAILABLE
    evidence exists or evidence is already selected -- see `enforce_
    governed_knowledge_selection`); incorrectly skipping enforcement when
    governed knowledge really was required is the exact provenance defect
    this module exists to close.
    """
    user_content = getattr(callback_context, "user_content", None)
    parts = getattr(user_content, "parts", None) if user_content else None
    if not parts:
        return True
    text = "".join(p.text for p in parts if getattr(p, "text", None))
    if not text:
        return True
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return True
    if not isinstance(payload, dict):
        return True
    return bool(payload.get("requires_governed_knowledge", False))


class _RetryEvidenceOption(BaseModel):
    """Model-facing projection of one AVAILABLE evidence item for the
    compliance retry's own reminder message -- mirrors `KnowledgeTool
    EvidenceItem` (backend/knowledge/tools/contracts.py), minus the
    scoring/applicability fields the ORIGINAL `knowledge_search` response
    carried but this run's trusted `KnowledgeEvidenceItem` no longer
    retains, and minus `source_uri` (never model-facing, the same
    boundary the original search response already enforces). This is
    real, already-retrieved governed content -- re-presenting it here is
    not a new search and not fabrication; the model already saw it once,
    in its own first attempt, earlier in this same run.
    """

    selection_key: KnowledgeEvidenceSelectionKey
    title: str
    section_heading: Optional[str] = None
    content: str
    source_display_name: Optional[str] = None


class _ProvenanceComplianceRetryRequest(BaseModel):
    """First-and-only turn content for the bounded compliance retry --
    deliberately its own shape, never `IncidentManagerRequest` (this is
    not a new user request, only a continuation of the SAME run's own
    already-completed reasoning that is missing one structural step).
    """

    prior_answer_text: str
    available_evidence: list[_RetryEvidenceOption]


def _to_retry_option(item: KnowledgeEvidenceItem) -> _RetryEvidenceOption:
    return _RetryEvidenceOption(
        selection_key=KnowledgeEvidenceSelectionKey(
            knowledge_id=item.reference.knowledge_id,
            version_label=item.reference.version_label,
            section_id=item.reference.section_id,
        ),
        title=item.title,
        section_heading=item.section.heading,
        content=item.section.content,
        source_display_name=item.source.display_name,
    )


_RETRY_INSTRUCTION = """You already completed an answer for this request, reproduced below as `prior_answer_text`. Governed knowledge evidence was retrieved for this request (`available_evidence`, below) but you did not declare which of it, if any, you actually relied on.

Call `knowledge_select_evidence` now with the exact `selection_key` of every item in `available_evidence` your prior answer actually relied on -- copy each `selection_key` verbatim, never invent one. If your prior answer did not actually rely on any item in `available_evidence`, call `knowledge_select_evidence` with an empty list instead.

After that one call, respond again with the SAME structured response your prior answer already gave -- do not change its content, only complete this one missing step. Do not call any other tool."""

_compliance_retry_agent_cache: list[Any] = []


def _compliance_retry_incident_manager() -> Any:
    """Lazily-built, memoized `.model_copy` of the base `incident_manager`
    (agent.py) -- same established lazy-import-inside-a-function pattern
    already used by `direct_read_fast_path.get_fast_path_team_manager`
    (that module cannot import `team_manager` at load time for the same
    reason this module cannot import `incident_manager` at load time:
    `agent.py` imports `evidence.py`, which imports THIS module, so this
    module importing `agent.py` back at module-load time would cycle).

    Deliberately reduced to `tools=[knowledge_select_evidence]` ONLY --
    structurally, not just by instruction, makes a repeated `teams_list_
    chats`/`teams_get_messages`/`knowledge_search` call impossible during
    this retry, the same "remove the capability from the schema, don't
    just police it" principle already established by `_CONTINUATION_
    INCIDENT_MANAGER`/`get_resolved_chat_messages` (read_continuation_
    execution.py).

    `after_agent_callback=None` is deliberate, not an oversight: this
    retry turn's own compliance outcome is checked in plain Python
    immediately after it returns (`_run_compliance_retry`'s caller,
    `enforce_governed_knowledge_selection`, below) -- if this variant
    instead inherited `enforce_incident_manager_response_integrity`
    (evidence.py), a non-compliant retry attempt could recurse into
    invoking a SECOND retry of its own. Leaving this `None` makes "at
    most one retry" a structural property of the agent object itself,
    not merely a counter that could be miscounted.
    """
    if not _compliance_retry_agent_cache:
        from backend.agents.incident_manager.agent import incident_manager

        _compliance_retry_agent_cache.append(
            incident_manager.model_copy(
                update={
                    "tools": [knowledge_select_evidence],
                    "instruction": _RETRY_INSTRUCTION,
                    "after_agent_callback": None,
                }
            )
        )
    return _compliance_retry_agent_cache[0]


def _merged_final_text(content: Optional[types.Content]) -> Optional[str]:
    """Mirrors `read_continuation_execution.py`'s own `_finalize` merge
    (`"\\n".join(p.text for p in last_content.parts if p.text and not
    p.thought)`) -- the same "final, non-thought text" extraction already
    established for a direct `Runner.run_async` result, reused unchanged
    rather than reimplemented.
    """
    if content is None or content.parts is None:
        return None
    merged = "\n".join(p.text for p in content.parts if p.text and not p.thought)
    return merged.strip() or None


async def _run_compliance_retry(
    *, run_id: str, prior_answer_text: str, available_items: list[KnowledgeEvidenceItem]
) -> Optional[str]:
    """Runs the ONE bounded compliance retry -- a fresh, throwaway
    `InMemorySessionService`/`Runner` turn (see this module's own
    docstring for the full rationale), never the real session. Returns
    the retry's own final response text, or `None` if it produced nothing
    usable (its caller treats that identically to "still non-compliant").
    """
    request = _ProvenanceComplianceRetryRequest(
        prior_answer_text=prior_answer_text,
        available_evidence=[_to_retry_option(item) for item in available_items],
    )
    content = types.Content(role="user", parts=[types.Part.from_text(text=request.model_dump_json())])

    session_service = InMemorySessionService()
    session_id = f"compliance-retry::{run_id}"
    runner = Runner(
        app_name=_RETRY_APP_NAME,
        agent=_compliance_retry_incident_manager(),
        session_service=session_service,
        memory_service=InMemoryMemoryService(),
    )
    try:
        await session_service.create_session(app_name=_RETRY_APP_NAME, user_id=_RETRY_USER_ID, session_id=session_id)
        last_content: Optional[types.Content] = None
        async for event in runner.run_async(user_id=_RETRY_USER_ID, session_id=session_id, new_message=content):
            if event.content:
                last_content = event.content
        return _merged_final_text(last_content)
    finally:
        await runner.close()
        try:
            existing = await session_service.get_session(
                app_name=_RETRY_APP_NAME, user_id=_RETRY_USER_ID, session_id=session_id
            )
            if existing is not None:
                await session_service.delete_session(
                    app_name=_RETRY_APP_NAME, user_id=_RETRY_USER_ID, session_id=session_id
                )
        except Exception:
            _logger.warning("provenance_compliance: failed to delete internal retry session")


async def enforce_governed_knowledge_selection(callback_context: Any, original_text: Optional[str]) -> Optional[str]:
    """The ONE compliance boundary this module adds -- called from
    evidence.py's combined `after_agent_callback` with incident_manager's
    own raw final text (`original_text`, possibly `None`). Returns
    corrected/replacement text if intervention was needed, or `None` to
    mean "use `original_text` unchanged, nothing to enforce here" --
    mirrors `strip_unverified_evidence`'s own `None`-means-no-override
    contract.

    Applies ONLY when ALL of: (a) `requires_governed_knowledge` was true
    for this request, (b) this run's `knowledge_search` produced at least
    one AVAILABLE evidence item, (c) nothing has been SELECTED via
    `knowledge_select_evidence` yet. The zero-evidence case and the
    KM-optional case are both satisfied by (a)/(b) alone -- neither ever
    reaches the retry, exactly as required.
    """
    run_id = current_run_id()
    if run_id is None:
        return None  # unbound turn (standalone `adk run`/`adk web`) -- nothing to enforce

    if not _requires_governed_knowledge(callback_context):
        return None  # KM-optional case -- never force a selection

    available = get_available_knowledge_evidence(run_id)
    if not available.items:
        return None  # zero-evidence case -- selection may legitimately stay empty

    if snapshot_selected_knowledge_evidence(run_id):
        return None  # already compliant -- nothing to enforce

    _logger.warning(
        "provenance_compliance: available governed evidence exists but none selected -- retrying once run_id=%s",
        run_id,
    )
    _perf_logger.info("perf stage=provenance_compliance_retry_start run_id=%s", run_id)
    try:
        retry_text = await _run_compliance_retry(
            run_id=run_id, prior_answer_text=original_text or "", available_items=list(available.items)
        )
    except Exception:
        _logger.warning("provenance_compliance: compliance retry raised -- failing closed run_id=%s", run_id)
        retry_text = None

    if snapshot_selected_knowledge_evidence(run_id):
        _perf_logger.info("perf stage=provenance_compliance_retry_succeeded run_id=%s", run_id)
        return retry_text or original_text

    _logger.warning(
        "provenance_compliance: retry did not select evidence -- deterministic safe failure run_id=%s", run_id
    )
    _perf_logger.info("perf stage=provenance_compliance_retry_failed run_id=%s", run_id)
    return _SAFE_FAILURE_TEXT
