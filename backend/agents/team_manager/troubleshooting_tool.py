"""Phase 6A.10: the ONE deterministic wrapper that lets `team_manager`
invoke the 6A.9 Troubleshooting Manager specialist -- a plain ADK
FunctionTool (mirroring `case_tools.py`'s/`conversation_target.py`'s own
established "plain deterministic function registered on team_manager"
shape), NEVER an `AgentTool`.

WHY NOT `AgentTool(agent=troubleshooting_manager)` (audited before
choosing): `AgentTool.run_async` builds the nested agent's own `Content`
directly from `input_schema.model_validate(args).model_dump_json()` --
it has no way to run the 6A.9 deterministic preparation pipeline (Skill
resolution, Experience query, Intelligence Assembly, grounding
validation) BEFORE the model is invoked. `troubleshooting_manager`
itself (agent.py) deliberately has no `input_schema` for exactly this
reason -- it expects a fully-RENDERED Intelligence Package as its input
Content, never raw tool arguments. So this module calls the canonical
6A.9 direct-invocation surface, `backend.agents.troubleshooting_manager
.runtime.run_troubleshooting_assessment`, directly -- reusing it exactly
as built, never duplicating Intelligence Assembly or building a second
troubleshooting runtime (instruction section 34).

CONTEXT PACKAGE ASSEMBLY, HONEST AND MINIMAL (instruction sections 37/38/
39/45/92): this module builds the SMALLEST truthful `ContextPackage` from
data ALREADY legitimately available at `team_manager` runtime --
`tool_context.user_id` (the same trusted, ADK-managed identity every
other session-bound operation in this codebase already uses, see
`backend/api/identity.py`), `tool_context.session.id` (the same accessor
`read_continuation_enforcement.py` already uses), and -- when this
session is linked to a Case -- the exact same `build_case_context_
snapshot`/`CaseService.get_case`/`get_context_items` call `case_context.py`
already makes for the Team Manager prompt itself (reused, never
reimplemented). It NEVER invents `owner_id`/`case_id`/`session_id` from
model text.

CORRECTIVE PASS (6A.10.1) -- LIVE EVIDENCE IS NOW REAL: the original
6A.10 pass left Evidence selection honestly empty, reasoning that "no
live producer is wired into any conversational turn yet." Audit for this
corrective pass found that framing was WRONG for Evidence specifically:
6A.4's narrowing engine and 6A.5's hybrid retrieval are both real,
COMPLETE, already-live-validated-against-Cloud-SQL production
capabilities (`backend/knowledge/narrowing/`, `backend/knowledge/
hybrid_retrieval/`) -- they were simply never CALLED from a live turn.
`backend/agents/troubleshooting_manager/context_support.py` (NEW, this
corrective pass) is the coordinator that calls them for real --
`narrow_knowledge` -> `hybrid_retrieve` -> `select_evidence`, all three
byte-for-byte UNMODIFIED -- so `evidence_selection` below is now a REAL
`EvidenceSelectionResult`, never a hardcoded empty stub, whenever the
governed corpus actually has something applicable. SEARCH RESULT !=
EVIDENCE USED remains intact -- only the already-narrowed, already-
reranked, already-selected top-K ever reaches the Intelligence Package.

TELCO CONTEXT REMAINS HONEST, STILL NEVER FABRICATED: no live
`TelcoContextProfile` PERSISTENCE pipeline exists anywhere in this
codebase (`backend/context/sqlalchemy/` is still never called from any
live turn -- deliberately; a full live profile-persistence wiring
remains a distinct, larger capability, correctly still out of THIS
corrective pass's own bounded scope, since it would require Team Manager
to durably write TELCO facts to Cloud SQL, a materially bigger and
riskier change than reusing two already-frozen retrieval functions).
Instead, `known_context_facts` -- an OPTIONAL dict Team Manager's own
model may populate, mirroring the ALREADY-PROVEN `known_applicability_
facts` pattern (A5's own corrective pass) EXACTLY: populated ONLY from
facts the CURRENT user message EXPLICITLY, LITERALLY states, using 6A.2's
own closed `ContextDimension` vocabulary as keys, never inferred, never
carried over from an earlier turn -- is converted into real, ephemeral,
NEVER-PERSISTED `ContextAssertion`s by `context_support.build_context_
state_from_known_facts` (a pure function, reducing via 6A.2's own
unmodified `compute_context_state`). An absent field, an unrecognized
dimension name, or a blank value all resolve to that dimension staying
UNKNOWN -- never a guess.

6A.10.2 CORRECTIVE PASS: a `FunctionTool` argument is model OUTPUT, not
automatically a verified user-stated fact merely because the prompt asks
for one -- this is enforced deterministically, never by prompt-following
alone. `_build_context_package` now also calls `context_support.extract_
current_user_text(tool_context)` to obtain the REAL, verbatim current
turn user message (never `troubleshooting_question`, which is itself
model-authored), and passes it into `build_context_state_from_known_
facts` -- a `known_context_facts` entry whose value does not literally
appear (case-insensitively) in that real text is DROPPED, never
asserted, regardless of what the model claimed. Only a fact that
survives this check is labeled `ContextOrigin.USER`.

Because the current production Skill (`telco.troubleshooting_assessment`)
still requires FAULT known AND at least one selected evidence item, a
live call through this wrapper legitimately still resolves to `NEEDS_
INFORMATION` whenever the user's own message states no concrete fault
and/or the governed corpus has nothing applicable -- 6A.9's own
unmodified, correct fail-closed behavior, never an invented assessment
either way. This is recorded here plainly, not hidden inside a closure
report only.

BOUNDED TO AT MOST ONE REAL INVOCATION PER TURN (instruction sections 28/
67/119): `block_repeated_troubleshooting_invocation`/`cache_
troubleshooting_result_this_turn` mirror `selection_delegation_guard.py`'s
own proven `tool_context.state["temp:..."]` mechanism EXACTLY (same
`temp:`-prefix rationale: team_manager's own outer Runner keeps ONE live
Session for the whole turn, so a `temp:`-prefixed write from an earlier
step is visible to a later step's `before_tool_callback` within the SAME
turn, and is guaranteed never to survive into a LATER turn via ADK's own
`_trim_temp_delta_state` -- no explicit cleanup needed). A second call to
this tool within the same turn is intercepted BEFORE the real pipeline
(and therefore before a second model call) and answered with the exact
same result the first real call already produced -- never a second real
invocation, never a fabricated "already answered" placeholder that loses
information.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from google.adk.tools import ToolContext

from backend.agents.troubleshooting_manager.context_support import build_context_state_from_known_facts, extract_current_user_text, query_selected_evidence
from backend.agents.troubleshooting_manager.runtime import run_troubleshooting_assessment
from backend.agents.troubleshooting_manager.schemas import TroubleshootingManagerRequest, TroubleshootingManagerResponse, TroubleshootingResponseStatus
from backend.api.case_service import ACTIVE_CASE_ID_STATE_KEY
from backend.cases.service import get_case_service
from backend.cases.snapshot import build_case_context_snapshot
from backend.context.domain.enums import ContextProfileOwnerKind
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackageInput, RequestContext
from backend.gateway.safe_error import SafeErrorException

__all__ = [
    "TOOL_NAME",
    "TROUBLESHOOTING_RESULT_THIS_TURN_STATE_KEY",
    "troubleshooting_manager",
    "block_repeated_troubleshooting_invocation",
    "cache_troubleshooting_result_this_turn",
]

_logger = logging.getLogger(__name__)

TOOL_NAME = "troubleshooting_manager"
"""Matches `FunctionTool.name` (== the wrapped function's own `__name__`,
verified against the installed ADK source) -- the identity the delegation
guard below compares against."""

TROUBLESHOOTING_RESULT_THIS_TURN_STATE_KEY = "temp:troubleshooting_result_this_turn"


async def _resolve_case_context(tool_context: ToolContext) -> tuple[Optional[str], Any, Any]:
    """Mirrors `case_context.py`'s own case-resolution exactly: an
    unlinked or stale `case_id` hint resolves to "no case," never an
    error that aborts the tool call."""
    case_id = tool_context.state.get(ACTIVE_CASE_ID_STATE_KEY)
    if not case_id:
        return None, None, ContextProfileOwnerKind.SESSION
    try:
        case_service = get_case_service()
        case = await case_service.get_case(tool_context.user_id, case_id)
        items = await case_service.get_context_items(tool_context.user_id, case_id)
    except SafeErrorException:
        return None, None, ContextProfileOwnerKind.SESSION
    snapshot = build_case_context_snapshot(case, items)
    return case_id, snapshot, ContextProfileOwnerKind.CASE


async def _build_context_package(
    tool_context: ToolContext,
    troubleshooting_question: str,
    known_context_facts: Optional[dict[str, str]] = None,
):
    owner_id = tool_context.user_id
    session_id = tool_context.session.id if tool_context.session is not None else None
    case_id, case_context, owner_kind = await _resolve_case_context(tool_context)

    current_user_text = extract_current_user_text(tool_context)
    telco_context_state = build_context_state_from_known_facts(known_context_facts, current_user_text)
    evidence_selection = await query_selected_evidence(troubleshooting_question, telco_context_state)

    input_ = ContextPackageInput(
        owner_kind=owner_kind,
        owner_id=owner_id,
        session_id=session_id,
        case_id=case_id,
        request=RequestContext(question=troubleshooting_question),
        telco_context_state=telco_context_state,
        case_context=case_context,
        evidence_selection=evidence_selection,
    )
    return assemble_context_package(input_)


def _response_to_safe_dict(response: TroubleshootingManagerResponse) -> dict[str, Any]:
    """Deliberately excludes `evidence_references_used`/`experience_
    references_used`/any Skill fingerprint from the model-visible tool
    result (instruction section 48: "do not expose internal
    implementation labels unnecessarily") -- `team_manager` needs the
    substantive advisory content to synthesize a reply, not internal
    provenance identifiers it has no established citation mechanism for
    today."""
    return {
        "status": response.status.value,
        "troubleshooting_objective": response.troubleshooting_objective.value if response.troubleshooting_objective else None,
        "assessment": response.assessment,
        "findings": list(response.findings),
        "information_gaps": [gap.description for gap in response.information_gaps],
        "next_diagnostic_requirement": (
            {
                "description": response.next_diagnostic_requirement.description,
                "required_capability": response.next_diagnostic_requirement.required_capability,
            }
            if response.next_diagnostic_requirement is not None
            else None
        ),
        "stop_or_escalation_condition": response.stop_or_escalation_condition,
        "detail": response.detail,
    }


_SAFE_FAILURE_DICT = {
    "status": TroubleshootingResponseStatus.BLOCKED.value,
    "troubleshooting_objective": None,
    "assessment": None,
    "findings": [],
    "information_gaps": [],
    "next_diagnostic_requirement": None,
    "stop_or_escalation_condition": None,
    "detail": "A troubleshooting assessment could not be completed for this request.",
}


async def troubleshooting_manager(
    troubleshooting_question: str,
    known_context_facts: Optional[dict[str, str]] = None,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Get a grounded troubleshooting assessment: current known/unknown
    operational context, authoritative selected governed evidence, the
    applicable methodology, and bounded historical experience, combined
    into a next diagnostic requirement or an explicit information gap.
    Advisory only -- this never executes any action, network command,
    ticket write, or Teams write; a "required capability" it names is a
    description of what would be needed, never something it has already
    done.

    Args:
      troubleshooting_question: A complete, self-contained statement of
        what should be investigated -- e.g. "what should be checked next
        for this VSWR alarm?". You are stateless here in the sense that
        this call does not see prior conversation turns -- resolve any
        pronoun/prior reference ("that alarm", "it") yourself before
        setting this, exactly like `incident_manager`'s own `question`
        field.
      known_context_facts: OPTIONAL. Populate ONLY with facts the
        CURRENT user message EXPLICITLY, LITERALLY states -- e.g.
        {"fault": "VSWR Over Threshold", "vendor": "Ericsson"}. Never
        infer, never guess, never carry a fact over from an earlier
        turn. Omit a fact entirely if the user did not literally state
        it -- an omitted fact stays unknown, which is always safe; a
        guessed fact is not. Mirrors `incident_manager`'s own `known_
        applicability_facts` field exactly.
      tool_context: ADK-injected.

    Returns:
      A dict with `status` ("advisory_ready" | "needs_information" |
      "blocked"), and, when applicable, `assessment`, `findings`,
      `information_gaps`, `next_diagnostic_requirement`, `stop_or_
      escalation_condition`, or `detail` explaining what is missing.
      `status != "advisory_ready"` means insufficient trusted information
      exists yet -- never improvise the missing assessment yourself.
    """
    if tool_context is None:
        return dict(_SAFE_FAILURE_DICT)

    try:
        context_package = await _build_context_package(tool_context, troubleshooting_question, known_context_facts)
        request = TroubleshootingManagerRequest(
            owner_id=tool_context.user_id,
            context_package=context_package,
            objective=troubleshooting_question,
        )
        result = await run_troubleshooting_assessment(request)
    except Exception:
        _logger.exception("troubleshooting_tool: run_troubleshooting_assessment raised")
        return dict(_SAFE_FAILURE_DICT)

    return _response_to_safe_dict(result.response)


def block_repeated_troubleshooting_invocation(tool: Any, args: dict[str, Any], tool_context: Any) -> Any:
    """`before_tool_callback` for `team_manager` (wired in agent.py,
    alongside the existing `enforce_read_continuation`/`block_repeated_
    delegation_after_selection_needed` list). Once this turn has already
    produced ONE real `troubleshooting_manager` result, any further call
    this same turn is intercepted here and answered with that SAME
    cached result -- never a second real pipeline run, never a second
    model call (instruction section 28's own "maximum 1 invocation"
    target, enforced structurally, mirroring `selection_delegation_
    guard.py`'s proven mechanism exactly)."""
    if getattr(tool, "name", None) != TOOL_NAME:
        return None
    cached = tool_context.state.get(TROUBLESHOOTING_RESULT_THIS_TURN_STATE_KEY)
    if cached is None:
        return None
    return dict(cached)


def cache_troubleshooting_result_this_turn(tool: Any, args: dict[str, Any], tool_context: Any, tool_response: Any) -> None:
    """`after_tool_callback` for `team_manager` (wired in agent.py,
    alongside the existing `sync_incident_manager_result_to_state`/
    `record_selection_needed` list). Always returns `None`: a
    deterministic side effect only, never a substitute tool result (same
    discipline `state_sync.py`/`selection_delegation_guard.py` already
    follow)."""
    if getattr(tool, "name", None) != TOOL_NAME:
        return None
    if not isinstance(tool_response, dict):
        return None
    tool_context.state[TROUBLESHOOTING_RESULT_THIS_TURN_STATE_KEY] = dict(tool_response)
    return None
