"""Server-owned operational continuation routing.

    ACTIVE OPERATIONAL FAULT + UNFINISHED INVESTIGATION + OPERATIONAL CONTINUATION
        = the SERVER routes the turn through the governed operational pipeline (Technical Authority)

Before this boundary existed, whether an operator's "what next?" on an active investigation reached
the Technical Authority Engineer was the Team Manager model's decision: it could answer from
conversation history instead, repeating an earlier command that the universal egress then (rightly)
removed, leaving a dead end. Historical conversation text is context, never current authority.

Decision (deterministic, before the Team Manager runs; no model call):
  * active operational investigation -- from the AUTHORITATIVE progression only (never chat text):
    the fault in focus exists, is not resolved / escalated, and holds unfinished operational work
    (a pending step, an open evidence requirement or acquisition gap, an open clarification, or a
    mid-investigation phase such as a validated result awaiting the next step)
  * operational turn kind -- from the operator's exact message against that state (the same
    classifiers the progression controller and clarification continuity use):
        RESULT_PROVIDED          output for the pending step (validated or candidate)
        CLARIFICATION_ANSWER     binds values to the fault's open applicability clarification
        COMMAND_FOLLOW_UP        asks for an operational method ("what command?")
        OPERATION_REQUEST        asks for an operational action on the investigation's own subject
                                 ("restart the affected unit", or a target observed in its trusted results)
        GENERIC_CONTINUATION     asks to go on, adding nothing of its own ("what next?", "continue")
        CLARIFICATION_FOLLOW_UP  asks which information is still requested (served from state)
        NEW_OBJECTIVE            explicitly redirects to another subject
        SUBJECT_REQUEST          carries its own subject (a question or request about something)
        NON_OPERATIONAL          social / meta ("thanks", "what can you do?")
  * route -- GOVERNED_OPERATIONAL for the first four kinds on an active investigation (and only when
    the governed specialist is available); TEAM_MANAGER (normal model-coordinated routing) otherwise.

Enforcement: a forced route is applied at the MODEL REQUEST level, not by prompt wording. The Team
Manager's `before_model_callback` (`enforce_operational_route`) restricts its function calling to
the governed specialist (`ANY` + allowed function = the specialist) until the specialist has been
invoked in this run, then disables function calling so the Team Manager can only present the
validated result. The chat service additionally fails closed when a forced turn ends without a
specialist record. Nothing here selects evidence, issues actions, grants authority or reuses an
earlier run's authorization: the specialist's own pipeline (discovery, explicit selection,
applicability, ProcedureAction, Command Authority) and the universal egress decide everything.
No vendor, technology, fault, procedure or command vocabulary.
"""
from __future__ import annotations

from backend.observability.turn_trace import observe_phase
from backend.observability.tracing import Operation
from backend.observability.stages import Stage as TelemetryStage

import logging
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional

logger = logging.getLogger(__name__)

TECHNICAL_AUTHORITY_TOOL_NAME = "technical_authority_engineer"

OPERATIONAL_ROUTE_UNAVAILABLE_TEXT = (
    "The governed troubleshooting specialist could not evaluate this step of the active investigation, so no "
    "operational guidance is provided. Please try again; if this persists, escalate."
)


class OperationalTurnKind(str, Enum):
    NEW_OBJECTIVE = "new_objective"
    RESULT_PROVIDED = "result_provided"
    CLARIFICATION_ANSWER = "clarification_answer"
    CLARIFICATION_FOLLOW_UP = "clarification_follow_up"
    COMMAND_FOLLOW_UP = "command_follow_up"
    OPERATION_REQUEST = "operation_request"
    GENERIC_CONTINUATION = "generic_continuation"
    SUBJECT_REQUEST = "subject_request"
    NON_OPERATIONAL = "non_operational"


class OperationalRoute(str, Enum):
    GOVERNED_OPERATIONAL = "governed_operational"
    """Server-forced: the governed operational specialist runs before anything is presented."""
    TEAM_MANAGER = "team_manager"
    """Normal model-coordinated routing (the Team Manager decides whether to delegate)."""


ROUTED_KINDS = frozenset({
    OperationalTurnKind.RESULT_PROVIDED,
    OperationalTurnKind.CLARIFICATION_ANSWER,
    OperationalTurnKind.COMMAND_FOLLOW_UP,
    OperationalTurnKind.OPERATION_REQUEST,
    OperationalTurnKind.GENERIC_CONTINUATION,
})
"""Turn kinds that continue the ACTIVE investigation and therefore need current governed progression."""


@dataclass(frozen=True)
class OperationalRouteDecision:
    active_fault_id: Optional[str]
    active_operational_investigation: bool
    unfinished_work: tuple[str, ...]
    turn_kind: OperationalTurnKind
    route_required: bool
    selected_route: OperationalRoute
    reason: str
    pending_step: Optional[dict[str, Any]] = None
    specialist_available: bool = True
    flags: dict[str, bool] = field(default_factory=dict)

    def trace_view(self) -> dict[str, Any]:
        """Diagnostics (identifiers and decisions only)."""
        return {
            "stage": "routing",
            "active_fault": self.active_fault_id,
            "active_operational_investigation": self.active_operational_investigation,
            "unfinished_work": list(self.unfinished_work),
            "turn_kind": self.turn_kind.value,
            **self.flags,
            "route_required": self.route_required,
            "selected_route": self.selected_route.value,
            "specialist_available": self.specialist_available,
            "reason": self.reason,
            "pending_step": self.pending_step,
        }


# ---------------------------------------------------------------------------------------------
# Active investigation (authoritative progression only)
# ---------------------------------------------------------------------------------------------


def unfinished_operational_work(progression: Any, fault_id: Optional[str]) -> list[str]:
    """Why the fault's investigation is unfinished (empty: not an active operational investigation).
    Read from the server-owned progression only -- never from chat history."""
    from backend.agents.technical_authority_engineer.progression_controller import awaits_result
    from backend.cases.evidence_model import GapStatus
    from backend.cases.troubleshooting_progression import ProgressionPhase, ResolutionState

    fault = progression.faults.get(fault_id or "") if progression is not None else None
    if fault is None or fault.resolution in (ResolutionState.RESOLVED, ResolutionState.ESCALATED):
        return []
    reasons: list[str] = []
    pending = [s for s in progression.steps_for(fault.fault_id) if awaits_result(s)]
    if pending:
        reasons.append(f"pending_step:{pending[-1].status.value}")
    if progression.open_requirements(fault.fault_id):
        reasons.append("open_requirement")
    if any(g.fault_id == fault.fault_id and g.status is GapStatus.OPEN for g in progression.acquisition_gaps):
        reasons.append("open_gap")
    if progression.pending_clarification(fault.fault_id) is not None:
        reasons.append("open_clarification")
    if fault.phase not in (ProgressionPhase.NEW, ProgressionPhase.RESOLVED, ProgressionPhase.ESCALATION_REQUIRED) and (
        reasons or progression.steps_for(fault.fault_id)
    ):
        reasons.append(f"phase:{fault.phase.value}")
    return reasons


# ---------------------------------------------------------------------------------------------
# Turn classification (the operator's exact message against server state)
# ---------------------------------------------------------------------------------------------


_METHOD_WORDS = frozenset(
    """cmd cmds command commands syntax run type execute enter use check get obtain retrieve collect see list read
    acquire verify show give how which way method""".split()
)
"""Generic English words of a request for an acquisition method ("what command do I run?")."""


_OPERATION_VERBS = frozenset(
    """restart restarts restarting reboot rebooting reset resetting reload reloading power poweroff shutdown shut
    lock unlock block unblock deblock enable disable activate deactivate recover restore repair fix remediate
    replace bounce""".split()
)
"""Generic English verbs of an operational action request (no vendor, technology or command vocabulary)."""
_INVESTIGATION_REFERENTS = frozenset(
    """affected faulty failing failed broken impacted same unit units component components element elements module
    it them those these one ones""".split()
)
"""Words that refer back to the active investigation's own subject instead of naming a new one."""


def _fact_tokens(progression: Any, fault_id: Optional[str]) -> set[str]:
    """Tokens of the target identities observed in this fault's TRUSTED results (server state)."""
    from backend.agents.technical_authority_engineer.turn_request import _tokens
    from backend.cases.target_facts import case_target_facts

    return {t for fact in case_target_facts(progression, fault_id) for t in _tokens(fact.identity)}


def _own_subject_tokens(text: str, known_values: Optional[list[str]] = None) -> list[str]:
    """Content words the message adds beyond generic conversation / method words and any values it
    answered a clarification with: a non-empty result means it names a subject of its own."""
    from backend.agents.technical_authority_engineer.turn_request import _CONVERSATIONAL, _content_tokens

    answered = {t for value in known_values or [] for t in _content_tokens(value)}
    return [t for t in _content_tokens(text) if t not in _CONVERSATIONAL and t not in _METHOD_WORDS and t not in answered]


def classify_operational_turn(
    text: str,
    progression: Any,
    fault_id: Optional[str],
    *,
    vocabulary: Optional[Mapping[str, set[str]]] = None,
) -> OperationalTurnKind:
    """Deterministic classification with the progression controller's and clarification
    continuity's own classifiers. No model call; no phrase list beyond those shared classifiers."""
    from backend.agents.technical_authority_engineer.clarification_continuity import (
        bind_applicability_answer,
        is_clarification_follow_up,
    )
    from backend.agents.technical_authority_engineer.progression_controller import (
        _MECHANISM_FOLLOW_UP,
        _NEW_OBJECTIVE,
        awaits_result,
        explicit_objective_change,
    )
    from backend.agents.technical_authority_engineer.result_validation import (
        UNVALIDATED_CANDIDATES,
        operator_intent,
        validate_operator_observation,
    )
    from backend.agents.technical_authority_engineer.turn_request import is_continuation_only, is_continuation_request
    from backend.cases.troubleshooting_progression import ClarificationReason

    stripped = (text or "").strip()
    pending = None
    clarification = None
    if progression is not None and fault_id:
        steps = progression.steps_for(fault_id)
        pending = next((s for s in reversed(steps) if awaits_result(s)), None)
        clarification = progression.pending_clarification(fault_id)
    # Intent decisions (redirect, operation request) read the operator's OWN words only: pasted output
    # is observation, never a request (structural; see result_validation.operator_intent).
    commands = [
        c for s in (progression.steps if progression is not None else [])
        for c in (s.command, s.identity.result_key if s.identity else None, s.identity.template if s.identity else None) if c
    ]
    intent = operator_intent(stripped, commands)
    if explicit_objective_change(intent):
        # An explicit redirect wins even when it also states values a clarification asked for: it is
        # a new objective (normal routing keeps the Team Manager's structured reading of the switch).
        return OperationalTurnKind.NEW_OBJECTIVE
    if clarification is not None and is_clarification_follow_up(stripped):
        return OperationalTurnKind.CLARIFICATION_FOLLOW_UP
    if clarification is not None and clarification.reason is ClarificationReason.APPLICABILITY and vocabulary:
        bound = bind_applicability_answer(clarification.unresolved_fields, stripped, vocabulary)
        if bound and not _own_subject_tokens(stripped, [v for values in bound.values() for v in values]):
            return OperationalTurnKind.CLARIFICATION_ANSWER  # the message only answers the clarification
    if pending is not None and stripped:
        known = [
            c for s in progression.steps if s.step_id != pending.step_id
            for c in (s.command, s.identity.result_key if s.identity else None) if c
        ]
        validation = validate_operator_observation(pending, stripped, known)
        if validation.bindable or validation.status in UNVALIDATED_CANDIDATES:
            return OperationalTurnKind.RESULT_PROVIDED
    own = _own_subject_tokens(intent)
    if own and any(t in _OPERATION_VERBS for t in own) and set(own) <= (
        _OPERATION_VERBS | _INVESTIGATION_REFERENTS | _fact_tokens(progression, fault_id)
    ):
        # An operational action on the investigation's own subject (its referents, or a target its
        # trusted results established) -- never a new subject: governed target / condition rules apply.
        return OperationalTurnKind.OPERATION_REQUEST
    if (_MECHANISM_FOLLOW_UP.search(stripped) and not own) or (
        pending is not None and pending.command and pending.command.lower() in stripped.lower()
    ):
        # Asks for the method of the work in progress, naming nothing else ("what command?").
        return OperationalTurnKind.COMMAND_FOLLOW_UP
    if is_continuation_request(stripped):
        return OperationalTurnKind.GENERIC_CONTINUATION
    if is_continuation_only(stripped):
        return OperationalTurnKind.NON_OPERATIONAL
    if _NEW_OBJECTIVE.search(stripped):
        return OperationalTurnKind.NEW_OBJECTIVE
    return OperationalTurnKind.SUBJECT_REQUEST


def decide_operational_route(
    text: str,
    *,
    progression: Any,
    fault_id: Optional[str],
    specialist_available: bool,
    vocabulary: Optional[Mapping[str, set[str]]] = None,
) -> OperationalRouteDecision:
    unfinished = unfinished_operational_work(progression, fault_id)
    active = bool(unfinished)
    kind = classify_operational_turn(text, progression, fault_id, vocabulary=vocabulary)
    required = active and specialist_available and kind in ROUTED_KINDS
    if required:
        reason = f"active operational investigation ({', '.join(unfinished)}) + {kind.value}: current governed progression required"
    elif not active:
        reason = "no active operational investigation in server state" if fault_id else "no active fault in server state"
    elif kind not in ROUTED_KINDS:
        reason = f"{kind.value} does not continue the active investigation: normal routing"
    else:
        reason = "governed operational specialist unavailable"
    pending = None
    if progression is not None and fault_id:
        from backend.agents.technical_authority_engineer.progression_controller import awaits_result

        step = next((s for s in reversed(progression.steps_for(fault_id)) if awaits_result(s)), None)
        if step is not None:
            pending = {"step_id": step.step_id, "status": step.status.value, "procedure_action_id": step.procedure_action_id}
    return OperationalRouteDecision(
        active_fault_id=fault_id,
        active_operational_investigation=active,
        unfinished_work=tuple(unfinished),
        turn_kind=kind,
        route_required=required,
        selected_route=OperationalRoute.GOVERNED_OPERATIONAL if required else OperationalRoute.TEAM_MANAGER,
        reason=reason,
        pending_step=pending,
        specialist_available=specialist_available,
        flags={
            "new_objective": kind is OperationalTurnKind.NEW_OBJECTIVE,
            "result_provided": kind is OperationalTurnKind.RESULT_PROVIDED,
            "clarification_answer": kind is OperationalTurnKind.CLARIFICATION_ANSWER,
            "generic_continuation": kind is OperationalTurnKind.GENERIC_CONTINUATION,
        },
    )


@observe_phase(Operation.PLANNING, TelemetryStage.PLANNING_STARTED, TelemetryStage.PLANNING_COMPLETED)
async def evaluate_operational_route(
    state: Mapping[str, Any], *, session_id: Optional[str], text: str, specialist_available: bool
) -> OperationalRouteDecision:
    """Load the AUTHORITATIVE progression (Case- or session-scoped) and decide. Read only."""
    from backend.agents.technical_authority_engineer.applicability_context import load_governed_vocabulary
    from backend.agents.technical_authority_engineer.progression_repository import ProgressionRepository
    from backend.agents.technical_authority_engineer.troubleshooting_threads import load_active_thread
    from backend.cases.troubleshooting_progression import ClarificationReason

    snapshot = dict(state)
    progression = await ProgressionRepository(snapshot, session_id=session_id).load()
    thread = load_active_thread(snapshot)
    fault_id = thread.fault_id if thread is not None and thread.fault_id in progression.faults else progression.active_fault_id
    vocabulary = None
    if fault_id and progression.pending_clarification(fault_id, ClarificationReason.APPLICABILITY) is not None:
        vocabulary = await load_governed_vocabulary()
    return decide_operational_route(
        text, progression=progression, fault_id=fault_id, specialist_available=specialist_available, vocabulary=vocabulary
    )


# ---------------------------------------------------------------------------------------------
# Run-scoped registry (in-process, keyed by the trusted run id; never session state)
# ---------------------------------------------------------------------------------------------

_lock = threading.Lock()
_routes: dict[str, OperationalRouteDecision] = {}
_invoked: set[str] = set()
"""Runs in which the governed specialist has been invoked (server-side fact, never request contents)."""


def record_operational_route(run_id: Optional[str], decision: Optional[OperationalRouteDecision]) -> None:
    if not run_id or decision is None:
        return
    with _lock:
        _routes[run_id] = decision
    from backend.observability.turn_trace import current_turn
    from backend.observability.slo_sources import observe
    turn = current_turn()
    if turn is not None and turn.run_id == run_id:
        observe(turn, "route", decision.selected_route == OperationalRoute.GOVERNED_OPERATIONAL)
    _trace(decision.trace_view())


def get_operational_route(run_id: Optional[str]) -> Optional[OperationalRouteDecision]:
    if not run_id:
        return None
    with _lock:
        return _routes.get(run_id)


def operational_route_forced(run_id: Optional[str]) -> bool:
    decision = get_operational_route(run_id)
    return decision is not None and decision.route_required


def discard_operational_route(run_id: Optional[str]) -> None:
    if not run_id:
        return
    with _lock:
        _routes.pop(run_id, None)
        _invoked.discard(run_id)


def specialist_invoked(run_id: Optional[str]) -> bool:
    if not run_id:
        return False
    with _lock:
        return run_id in _invoked


def _trace(entry: dict[str, Any]) -> None:
    try:
        from backend.tools.knowledge.diagnostic_trace import record_operational_event

        record_operational_event(entry)
    except Exception:
        pass


def record_specialist_invocation(specialist: str, run_id: Optional[str]) -> None:
    """The specialist started in this run (server-side fact the routing enforcement relies on), and
    diagnostics of whether the server forced it."""
    if run_id:
        with _lock:
            _invoked.add(run_id)
    decision = get_operational_route(run_id)
    if decision is not None and decision.route_required:
        from backend.observability.agent_instrumentation import observe_primary
        try:
            observe_primary(specialist)
        except Exception:
            pass  # Observation never affects routing or authority.
    _trace({
        "stage": "specialist_invocation",
        "specialist": specialist,
        "forced_by_server": bool(decision is not None and decision.route_required),
        "reason": decision.reason if decision is not None else "model-coordinated delegation (no routing decision)",
    })


# ---------------------------------------------------------------------------------------------
# Enforcement at the model-request level (Team Manager before_model_callback)
# ---------------------------------------------------------------------------------------------


DUPLICATE_SPECIALIST_CALL_RESPONSE = {
    "status": "duplicate_invocation_suppressed",
    "detail": "The governed specialist already evaluated this turn; present only its validated result.",
}


def guard_forced_specialist_call(tool: Any, args: dict[str, Any], tool_context: Any) -> Optional[dict[str, Any]]:
    """Team Manager `before_tool_callback`. On a server-forced operational route exactly ONE governed
    specialist evaluation runs per turn: the first call claims the run; any further call (a model may
    emit several parallel function calls under a forced function-calling mode) is suppressed without
    running the specialist. Check-and-claim is atomic within the event loop (no await in between)."""
    from backend.api.turn_context import current_run_id

    if getattr(tool, "name", None) != TECHNICAL_AUTHORITY_TOOL_NAME:
        return None
    run_id = current_run_id()
    decision = get_operational_route(run_id)
    if decision is None or not decision.route_required or not run_id:
        return None
    with _lock:
        claimed = run_id in _invoked
        _invoked.add(run_id)
    if claimed:
        _trace({"stage": "route_enforcement", "mode": "duplicate_call_suppressed", "allowed": None, "reason": decision.reason})
        return dict(DUPLICATE_SPECIALIST_CALL_RESPONSE)
    return None


def _operator_text(callback_context: Any) -> str:
    content = getattr(callback_context, "user_content", None)
    return " ".join(p.text for p in (getattr(content, "parts", None) or []) if getattr(p, "text", None)).strip()


_DECLARATION_TOOLS = frozenset({"record_source_requirements", "record_conversation_target"})
"""Declaration-only Team Manager tools: they carry no operational content and may precede the
specialist call."""


def _available_tool_names(callback_context: Any) -> set[str]:
    agent = getattr(getattr(callback_context, "_invocation_context", None), "agent", None)
    return {str(getattr(t, "name", None) or getattr(t, "__name__", "")) for t in (getattr(agent, "tools", None) or [])}


def restrict_forced_route_response(callback_context: Any, llm_response: Any) -> Any:
    """Team Manager `after_model_callback`: the response-level half of the forced route. Under a forced
    function-calling mode a model response may still carry several parallel specialist calls, calls
    to tools that do not exist, or no specialist call at all. Function calls execute only from the
    final (non-partial) response, which is what this governs:
      * calls to tools the agent does not have are always dropped;
      * before the specialist ran, the response carries exactly ONE specialist call (the model's own
        first one), or only declaration-only calls; with nothing valid left, ONE specialist call built
        by the server from the operator's exact message (the specialist rebuilds its request from that
        text and server state anyway);
      * after it ran, further specialist calls are suppressed at execution (`guard_forced_specialist_call`)."""
    from google.genai import types

    from backend.api.turn_context import current_run_id

    run_id = current_run_id()
    decision = get_operational_route(run_id)
    if decision is None or not decision.route_required:
        return None
    content = getattr(llm_response, "content", None)
    parts = list(getattr(content, "parts", None) or [])
    available = _available_tool_names(callback_context)
    is_call = lambda p: getattr(p, "function_call", None) is not None  # noqa: E731
    kept = [p for p in parts if not is_call(p) or p.function_call.name in available]
    reason = "calls to non-existent tools dropped"
    if not specialist_invoked(run_id):
        specialist = next((p for p in kept if is_call(p) and p.function_call.name == TECHNICAL_AUTHORITY_TOOL_NAME), None)
        if specialist is not None:
            kept = [p for p in kept if not is_call(p) or p is specialist]
            reason = "model response reduced to one specialist call"
        else:
            kept = [p for p in kept if not is_call(p) or p.function_call.name in _DECLARATION_TOOLS]
            if not any(is_call(p) for p in kept) and not getattr(llm_response, "partial", False):
                kept = [types.Part(function_call=types.FunctionCall(
                    name=TECHNICAL_AUTHORITY_TOOL_NAME, args={"problem_statement": _operator_text(callback_context) or decision.reason},
                ))]
                reason = "no specialist call in the model response: server-built call"
            else:
                reason = "non-declaration calls dropped before the specialist ran"
    if kept == parts:
        return None
    _trace({"stage": "route_enforcement", "mode": "response_restricted", "allowed": [TECHNICAL_AUTHORITY_TOOL_NAME],
            "dropped": [p.function_call.name for p in parts if is_call(p) and all(p is not k for k in kept)], "reason": reason})
    return llm_response.model_copy(update={"content": types.Content(role="model", parts=kept)})


def _specialist_responded(llm_request: Any) -> bool:
    """The governed specialist already returned a response in THIS invocation (after the latest
    operator message): its function response is present in the request contents."""
    for content in reversed(list(getattr(llm_request, "contents", None) or [])):
        parts = list(getattr(content, "parts", None) or [])
        if any(getattr(getattr(p, "function_response", None), "name", None) == TECHNICAL_AUTHORITY_TOOL_NAME for p in parts):
            return True
        if getattr(content, "role", None) == "user" and any(getattr(p, "text", None) for p in parts):
            return False
    return False


def enforce_operational_route(callback_context: Any, llm_request: Any) -> Any:
    """Team Manager `before_model_callback`. On a server-forced operational route the Team Manager
    model does not decide whether -- or answer before -- the governed specialist runs: until the
    specialist has been invoked in this run, the model call is SKIPPED and replaced by a server-built
    response carrying exactly ONE specialist call (arguments from the operator's exact message; the
    specialist rebuilds its request from that text and server state anyway). Once the specialist ran,
    function calling is disabled (`NONE`) so the Team Manager can only present the validated result.
    Any other turn is left untouched."""
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from backend.api.turn_context import current_run_id

    run_id = current_run_id()
    decision = get_operational_route(run_id)
    if decision is None or not decision.route_required:
        return None
    if TECHNICAL_AUTHORITY_TOOL_NAME not in (getattr(llm_request, "tools_dict", None) or {}):
        return None  # e.g. the tool-less presentation agent: nothing to route
    llm_request.config = llm_request.config or types.GenerateContentConfig()
    # Server-side fact first: request contents may be re-projected by other callbacks (authoritative
    # earlier answers), so they are only a secondary signal.
    if specialist_invoked(run_id) or _specialist_responded(llm_request):
        llm_request.config.tool_config = types.ToolConfig(
            function_calling_config=types.FunctionCallingConfig(mode=types.FunctionCallingConfigMode.NONE)
        )
        _trace({"stage": "route_enforcement", "mode": "NONE", "allowed": None, "reason": decision.reason})
        return None
    _trace({"stage": "route_enforcement", "mode": "server_invoked", "allowed": [TECHNICAL_AUTHORITY_TOOL_NAME], "reason": decision.reason})
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(function_call=types.FunctionCall(
            name=TECHNICAL_AUTHORITY_TOOL_NAME,
            args={"problem_statement": _operator_text(callback_context) or decision.reason},
        ))]),
        partial=False,
    )
