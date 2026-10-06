"""Technical Authority Engineer structured-output recovery: ONE bounded, tool-free regeneration.

Live defect (Prompt 4 acceptance, session 84f6074b, turn 3):
    turn 2 presented the governed `alt` step; turn 3 the operator pasted its output
    -> routing result_provided, the server invoked the specialist exactly once
    -> the specialist searched governed knowledge, then returned an EMPTY final response
    -> `validate_schema` raised `Invalid JSON: EOF ... input_value=''`; no validated execution record;
       the forced turn failed closed on a serialization failure, not on evidence or authority.

Invariant under test:
    reasoning / tool phase runs once -> final structured answer attempt 1 -> structurally unusable?
    -> ONE regeneration in the SAME specialist session with NO tools -> the standard validation chain.
    A valid answer (whatever its outcome) is never regenerated; a second structural failure fails
    closed; a regenerated answer has no authority of its own.

The specialist is the PRODUCTION agent (tools and callbacks, including the integrity callback) with
only its model scripted; the Team Manager honours request-level function-calling constraints.
"""
from __future__ import annotations

import json
from typing import Any, AsyncGenerator, Optional

import pytest
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

import backend.tests.test_clarification_continuity as tcc
from backend.agents.team_manager.operational_routing import OPERATIONAL_ROUTE_UNAVAILABLE_TEXT, OperationalTurnKind
from backend.agents.technical_authority_engineer import agent_tool as tae_agent_tool
from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer import structured_output as so
from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityResponse
from backend.agents.technical_authority_engineer.structured_output import (
    MALFORMED_JSON_FALLBACK_DETAIL,
    StructuredOutputFailureReason as Reason,
    StructuredOutputRecovery,
    classify_structured_output,
)
from backend.cases.troubleshooting_progression import StepStatus
from backend.tests.test_applicability_blocked_governed_action import CATALOG, DOC, SEARCH, _governed, _legacy_alt, use_production_specialist
from backend.tests.test_clarification_continuity import _fc
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_operational_continuation_routing import (
    ALT_ID,
    ALT_RESULT,
    HISTORY_ANSWER,
    PLUGIN_ID,
    _alt_presented,
    _assert_forced,
    _authorized,
    _events,
    _insufficient,
    _RoutedConversation,
    _trace,
)

FABRICATED_ID = "pa-00000000000000000000"
REGENERATION_MARKER = "SERVER STRUCTURED-OUTPUT RECOVERY"


def _text(text: str) -> list[types.Part]:
    return [types.Part.from_text(text=text)]


EMPTY = _text("")
WHITESPACE = _text("   \n\t ")
TRUNCATED = _text('{"outcome": "recommended", "technical_interpretation": "Alarm list shows a link fail')
MALFORMED = _text("Here is my analysis: {outcome = recommended}")
SCHEMA_INVALID = _text(json.dumps({"diagnostic_step": None, "missing_information": "not-a-list"}))
PLUGIN_STEP = _governed(PLUGIN_ID, "Check plug-in unit states")


def _escalation() -> list[types.Part]:
    return _text(json.dumps({
        "outcome": "escalation_required", "technical_interpretation": "No governed procedure covers the observed fault.",
        "verified_evidence_citations": [], "escalation_reason": "Outside the governed procedure scope.",
    }))


class _RecordingLlm(tcc._ScriptedLlm):
    """Scripted specialist model that also records every request it receives."""

    instances: list["_RecordingLlm"] = []

    def __init__(self, model: str, parts_by_call: list[list[types.Part]], **kwargs: Any) -> None:
        super().__init__(model=model, parts_by_call=parts_by_call, **kwargs)
        self._requests: list[LlmRequest] = []
        _RecordingLlm.instances.append(self)

    async def generate_content_async(self, llm_request: LlmRequest, stream: bool = False) -> AsyncGenerator[LlmResponse, None]:
        self._requests.append(llm_request)
        async for response in super().generate_content_async(llm_request, stream):
            yield response


@pytest.fixture
def conversation(isolated_km_repo, monkeypatch):  # noqa: F811
    use_production_specialist(monkeypatch)
    monkeypatch.setattr(tcc, "_ScriptedLlm", _RecordingLlm)
    _RecordingLlm.instances = []
    so.reset_structured_output_metrics()
    return isolated_km_repo, _RoutedConversation(monkeypatch)


async def _result_turn(conv: _RoutedConversation, tae_calls: list[list[types.Part]]) -> dict[str, Any]:
    """Turn 2: the operator supplies the presented step's result (server-forced governed route)."""
    turn = await conv.turn(ALT_RESULT, tae_calls, HISTORY_ANSWER, delegate=False)
    turn["tae_llm"] = _RecordingLlm.instances[-1]
    return turn


def _requests(turn: dict[str, Any]) -> list[LlmRequest]:
    return turn["tae_llm"]._requests


def _structured(turn: dict[str, Any]) -> list[tuple[Any, ...]]:
    return [(e["attempt"], e["phase"], e["status"], e["reason"]) for e in _events(turn, "structured_output")]


def _retries(turn: dict[str, Any]) -> list[dict[str, Any]]:
    return _events(turn, "structured_output_retry")


def _function_responses(request: LlmRequest) -> list[str]:
    return [p.function_response.name for c in request.contents or [] for p in c.parts or [] if p.function_response]


def _user_texts(request: LlmRequest) -> list[str]:
    return [p.text for c in request.contents or [] if c.role == "user" for p in c.parts or [] if p.text]


def _regeneration_context(request: LlmRequest) -> dict[str, Any]:
    text = next(t for t in reversed(_user_texts(request)) if REGENERATION_MARKER in t)
    return json.loads(text.split("re-validated by the server):\n", 1)[1])


def _tool_counts(turn: dict[str, Any]) -> tuple[int, int, int]:
    trace = _trace(turn)
    return len(trace.get("searches") or []), len(trace.get("selections") or []), len(trace.get("action_catalogs") or [])


def _assert_tool_free(request: LlmRequest) -> None:
    assert not request.tools_dict, "the regeneration request declares no tool"
    assert not (request.config and request.config.tools), "no function declaration reaches the model"


def _assert_alt_result_bound(turn: dict[str, Any]) -> None:
    alt = next(s for s in turn["progression"].steps if s.procedure_action_id == ALT_ID)
    assert alt.status in (StepStatus.OBSERVED, StepStatus.COMPLETED, StepStatus.VERIFIED) and alt.result is not None


# =============================================================================================
# Classification: structural failures vs valid structured outcomes
# =============================================================================================


def _content(*parts: types.Part) -> types.Content:
    return types.Content(role="model", parts=list(parts))


@pytest.mark.parametrize(
    "content, reason",
    [
        (None, Reason.NO_STRUCTURED_PAYLOAD),
        (types.Content(role="model", parts=[]), Reason.NO_STRUCTURED_PAYLOAD),
        (_content(types.Part.from_text(text="")), Reason.EMPTY_RESPONSE),
        (_content(types.Part.from_text(text="  \n\t")), Reason.EMPTY_RESPONSE),
        (_content(types.Part(function_response=types.FunctionResponse(name="knowledge_search", response={"r": 1}))), Reason.EMPTY_RESPONSE),
        (_content(types.Part(text='{"outcome": "x"}', thought=True)), Reason.EMPTY_RESPONSE),
        (_content(types.Part.from_text(text='{"outcome": "recommended", "technical_')), Reason.TRUNCATED_JSON),
        (_content(types.Part.from_text(text='{"outcome": "recommended",')), Reason.TRUNCATED_JSON),
        (_content(types.Part.from_text(text="not json at all")), Reason.MALFORMED_JSON),
        (_content(types.Part.from_text(text="[1, 2]")), Reason.SCHEMA_INVALID),
        (_content(types.Part.from_text(text='{"unexpected": true}')), Reason.SCHEMA_INVALID),
        (_content(types.Part.from_text(text='{"outcome": "bogus", "technical_interpretation": "x"}')), Reason.SCHEMA_INVALID),
        (_content(types.Part.from_text(text=json.dumps({
            "outcome": "error", "technical_interpretation": "Specialist output was not valid JSON.", "detail": MALFORMED_JSON_FALLBACK_DETAIL,
        }))), Reason.MALFORMED_JSON),
    ],
)
def test_structural_failures_are_classified(content: Any, reason: Reason) -> None:
    failure = classify_structured_output(content, TechnicalAuthorityResponse)
    assert failure is not None and failure.reason is reason


@pytest.mark.parametrize(
    "payload",
    [
        {"outcome": "insufficient_evidence", "technical_interpretation": "Awaiting the alarm list.", "missing_information": ["alarm list"]},
        {"outcome": "escalation_required", "technical_interpretation": "Out of scope.", "escalation_reason": "No procedure."},
        {"outcome": "error", "technical_interpretation": "Inputs were malformed.", "detail": "model-chosen error outcome"},
        {"outcome": "recommended", "technical_interpretation": "Check plug-in units.", "diagnostic_step": {
            "action": "Check plug-in unit states", "reason": "r", "expected_evidence": "e", "procedure_action_id": FABRICATED_ID}},
    ],
)
def test_valid_structured_outcomes_are_never_retry_eligible(payload: dict[str, Any]) -> None:
    assert classify_structured_output(_content(types.Part.from_text(text=json.dumps(payload))), TechnicalAuthorityResponse) is None


def test_recovery_budget_allows_exactly_one_regeneration() -> None:
    recovery = StructuredOutputRecovery("technical_authority_engineer", "run-x")
    failure = classify_structured_output(None, TechnicalAuthorityResponse)
    assert recovery.can_regenerate and recovery.attempt == 1
    recovery.begin_regeneration(failure)
    assert not recovery.can_regenerate and recovery.attempt == 2
    with pytest.raises(RuntimeError):
        recovery.begin_regeneration(failure)


# =============================================================================================
# A. Normal valid response: one final generation, no retry, no extra model call
# =============================================================================================


@pytest.mark.asyncio
async def test_a_valid_first_answer_costs_no_extra_model_call(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, PLUGIN_STEP])
    _assert_forced(t2, OperationalTurnKind.RESULT_PROVIDED)
    assert len(_requests(t2)) == 4, "search, select, catalog, final answer -- nothing more"
    assert all(r.tools_dict for r in _requests(t2)), "no tool-free regeneration call happened"
    assert _structured(t2) == [(1, "initial", "valid", None)] and not _retries(t2)
    (record,) = t2["records"]
    assert record[so.STRUCTURED_OUTPUT_RECORD_KEY] == {"attempts": 1, "retry_count": 0, "retry_reason": None, "outcome": "valid"}
    assert _authorized(t2, "st pluginunit")


# =============================================================================================
# B-E. Structurally unusable first answer -> ONE tool-free regeneration in the same run
# =============================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "first, reason",
    [(EMPTY, "empty_response"), (WHITESPACE, "empty_response"), (TRUNCATED, "malformed_json"),
     (MALFORMED, "malformed_json"), (SCHEMA_INVALID, "schema_invalid")],
    ids=["B-empty", "C-whitespace", "D-truncated", "D-malformed", "E-schema-invalid"],
)
async def test_b_to_e_unusable_first_answer_is_regenerated_once_then_validated_normally(conversation, first, reason: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, first, PLUGIN_STEP])
    _assert_forced(t2, OperationalTurnKind.RESULT_PROVIDED)
    # Exactly one regeneration; the integrity callback replaces non-JSON text with its structural
    # fallback, which is classified as malformed JSON (never as a model-chosen ERROR outcome).
    assert _structured(t2) == [(1, "initial", "invalid", reason), (2, "regeneration", "valid", None)]
    (retry,) = _retries(t2)
    assert retry["tools_enabled"] is False and retry["context_reused"] is True and retry["reason"] == reason
    assert len(_requests(t2)) == 5
    _assert_tool_free(_requests(t2)[-1])
    # Normal validation continued on the regenerated answer: resolution, Command Authority, progression.
    (record,) = t2["records"]
    assert record[so.STRUCTURED_OUTPUT_RECORD_KEY] == {"attempts": 2, "retry_count": 1, "retry_reason": reason, "outcome": "recovered"}
    assert record[pa.PROCEDURE_ACTION_RESOLUTION_KEY]["action_id"] == PLUGIN_ID
    assert _authorized(t2, "st pluginunit") and "`st pluginunit`" in t2["final"]
    _assert_alt_result_bound(t2)


# =============================================================================================
# D (same run) / 18. No tool repetition / 19. No state duplication
# =============================================================================================


@pytest.mark.asyncio
async def test_regeneration_reuses_the_same_run_context_and_repeats_no_tool(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, EMPTY, PLUGIN_STEP])
    first_request, regeneration = _requests(t2)[0], _requests(t2)[-1]
    # Tool calls of attempt 1 are NOT repeated: one search, one selection, one catalog issuance.
    assert _tool_counts(t2) == (1, 1, 1)
    _assert_tool_free(regeneration)
    # SAME specialist session: the original server-built request and every tool result of this run.
    assert _user_texts(regeneration)[0] == _user_texts(first_request)[0]
    assert _function_responses(regeneration) == ["knowledge_search", "knowledge_select_evidence", "procedure_action_catalog"]
    # Server-owned current-run context (identities only): exactly what attempt 1 selected and issued.
    context = _regeneration_context(regeneration)
    trace = _trace(t2)
    assert context["selected_evidence"] == trace["selected"] == [{"knowledge_id": DOC.kid, "version_label": DOC.ver, "section_id": DOC.section}]
    issued = [a["action_id"] for catalog in trace["action_catalogs"] for a in catalog["actions"]]
    assert sorted(a["procedure_action_id"] for a in context["issued_procedure_actions"]) == sorted(issued)
    assert context["governed_search_performed"] is True
    # SAME run and fault: one specialist invocation, one execution record bound to this run's fault.
    (invocation,) = _events(t2, "specialist_invocation")
    assert invocation["specialist"] == "technical_authority_engineer"
    (record,) = t2["records"]
    assert record["fault_id"] == t2["progression"].active_fault_id
    assert {e.get("run_id") for e in [record]} == {record["run_id"]}
    assert all(e["specialist"] == "technical_authority_engineer" for e in _events(t2, "structured_output"))


@pytest.mark.asyncio
async def test_regeneration_writes_progression_state_exactly_once(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, EMPTY, PLUGIN_STEP])
    progression = t2["progression"]
    fault_id = progression.active_fault_id
    assert len(t2["records"]) == 1, "one execution record"
    steps = progression.steps_for(fault_id)
    assert [s.procedure_action_id for s in steps] == [ALT_ID, PLUGIN_ID], "no duplicated step"
    assert sum(1 for s in steps if s.status is StepStatus.PRESENTED) == 1
    assert steps[0].result is not None and steps[1].result is None
    requirement_ids = [r.requirement_id for r in progression.requirements_for(fault_id)]
    assert len(requirement_ids) == len(set(requirement_ids)), "no duplicated evidence requirement"
    gap_ids = [g.gap_id for g in progression.gaps_for(fault_id)] if hasattr(progression, "gaps_for") else []
    assert len(gap_ids) == len(set(gap_ids))
    assert len(_events(t2, "progression")) == 1, "one progression decision"


# =============================================================================================
# F / G. Unusable twice -> fail closed, never a third attempt
# =============================================================================================


def _assert_failed_closed_after_two_attempts(turn: dict[str, Any], reason: str) -> None:
    assert _structured(turn) == [(1, "initial", "invalid", reason), (2, "regeneration", "invalid", reason)]
    assert len(_retries(turn)) == 1
    (recovery,) = _events(turn, "structured_output_recovery")
    assert recovery["outcome"] == "fail_closed" and recovery["attempts"] == 2
    assert len(_requests(turn)) == 5, "attempt 1 + one regeneration; the scripted third answer is never requested"
    if reason == "empty_response":
        # No structured payload at all: the existing error boundary, no validated record.
        assert turn["records"] == [] and turn["final"] == OPERATIONAL_ROUTE_UNAVAILABLE_TEXT
    else:
        # Malformed text: the integrity callback's existing safe ERROR response (no step, no command).
        (record,) = turn["records"]
        assert record["outcome"] == "error" and record.get("diagnostic_step") is None
        assert record[so.STRUCTURED_OUTPUT_RECORD_KEY] == {"attempts": 2, "retry_count": 1, "retry_reason": reason, "outcome": "fail_closed"}
    assert "st pluginunit" not in turn["final"] and not _authorized(turn, "st pluginunit")
    _assert_alt_result_bound(turn)  # the result binding survives a specialist failure (persisted first)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad, reason", [(EMPTY, "empty_response"), (MALFORMED, "malformed_json")], ids=["F-empty-twice", "G-malformed-twice"])
async def test_f_g_unusable_twice_fails_closed_with_no_third_attempt(conversation, bad, reason: str) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, bad, bad, PLUGIN_STEP])
    _assert_failed_closed_after_two_attempts(t2, reason)
    metrics = so.structured_output_metrics()
    assert metrics["tae_structured_output_retry_count"] == 1 and metrics["tae_structured_output_retry_failure"] == 1
    assert metrics["tae_structured_output_retry_success"] == 0 and metrics["failure_reason"] == {reason: 2}


# =============================================================================================
# H-J. Valid operational outcomes are never regenerated
# =============================================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final",
    [_insufficient(["Next diagnostic decision"]), _escalation(), _governed(FABRICATED_ID, "Check plug-in unit states")],
    ids=["H-insufficient-evidence", "I-escalation-required", "J-recommendation-rejected-by-authority"],
)
async def test_h_to_j_valid_outcomes_are_not_retried(conversation, final) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, final])
    assert ("initial", "valid") in [(p, s) for _, p, s, _ in _structured(t2)]
    assert not _retries(t2) and not _events(t2, "structured_output_recovery")
    assert not any(REGENERATION_MARKER in t for r in _requests(t2) for t in _user_texts(r)), "no structured-output regeneration call"
    # An id the run never issued gets the separate, bounded ProcedureAction id re-selection (J only).
    assert all(r.tools_dict for r in _requests(t2)[:4])
    assert len(_requests(t2)) == (5 if FABRICATED_ID in final[0].text else 4)
    (record,) = t2["records"]
    assert record[so.STRUCTURED_OUTPUT_RECORD_KEY]["retry_count"] == 0
    assert not _authorized(t2, "st pluginunit")
    if final is not None and FABRICATED_ID in final[0].text:
        assert record[pa.PROCEDURE_ACTION_RESOLUTION_KEY]["status"] == "unknown_action"


# =============================================================================================
# 20. A regenerated answer has no authority of its own
# =============================================================================================


@pytest.mark.asyncio
async def test_regenerated_invented_action_id_fails_closed_as_normal(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, EMPTY, _governed(FABRICATED_ID, "Check plug-in unit states")])
    assert len(_retries(t2)) == 1
    (record,) = t2["records"]
    assert record[pa.PROCEDURE_ACTION_RESOLUTION_KEY]["status"] == "unknown_action"
    assert (record.get("diagnostic_step") or {}).get("command") is None
    assert not [c for c in _trace(t2).get("command_authority", []) if c["decision"] == "authorized"]


@pytest.mark.asyncio
async def test_regenerated_historical_action_id_is_not_current_authority(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)  # turn 1 issued the section's catalog (incl. PLUGIN_ID) in THAT run
    # This run selects but issues no catalog; the regenerated answer reuses the earlier run's id.
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), EMPTY, _governed(PLUGIN_ID, "Check plug-in unit states")])
    assert len(_retries(t2)) == 1
    (resolution,) = _trace(t2)["action_resolutions"]
    assert resolution["action_id"] == PLUGIN_ID and resolution["status"] == "unknown_action"
    assert "not issued in this turn" in resolution["reason"]
    assert not _authorized(t2, "st pluginunit") and "`st pluginunit`" not in t2["final"]


@pytest.mark.asyncio
async def test_regenerated_command_from_available_but_unselected_evidence_is_rejected(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    legacy = _legacy_alt(source=DOC.canonical, command="st pluginunit", action="Check plug-in unit states")
    # Nothing selected in this run; the selection remediation is declined (the same proposal again).
    t2 = await _result_turn(conv, [SEARCH, EMPTY, legacy, legacy])
    assert len(_retries(t2)) == 1 and _tool_counts(t2)[1] == 0
    assert not _authorized(t2, "st pluginunit") and "`st pluginunit`" not in t2["final"]
    for record in t2["records"]:
        assert (record.get("diagnostic_step") or {}).get("command") is None


@pytest.mark.asyncio
async def test_regenerated_command_not_grounded_in_the_selection_is_stripped(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    ungrounded = _legacy_alt(source=DOC.canonical, command="restart everything now", action="Restart the node")
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, EMPTY, ungrounded])
    assert len(_retries(t2)) == 1
    (record,) = t2["records"]
    assert (record.get("diagnostic_step") or {}).get("command") is None
    assert "restart everything now" not in t2["final"]


@pytest.mark.asyncio
async def test_a_tool_call_attempted_during_regeneration_never_executes(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    # The regeneration "answer" is a search call: no tool is declared, so nothing runs; it is a
    # failed regeneration and the turn fails closed (no third attempt).
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, EMPTY, SEARCH, PLUGIN_STEP])
    assert _tool_counts(t2) == (1, 1, 1)
    assert len(_retries(t2)) == 1 and _events(t2, "structured_output_recovery")
    assert t2["records"] == [] and t2["final"] == OPERATIONAL_ROUTE_UNAVAILABLE_TEXT


# =============================================================================================
# 21. Exact live failure shape (Prompt 4 turn 3)
# =============================================================================================


async def _prompt4_turns_1_2(conv: _RoutedConversation) -> None:
    await conv.turn("how can i troubleshoot ESS Service Unavailable ?", [SEARCH, DOC.select(), _insufficient(["technology", "vendor"])],
                    "Which technology and vendor?", tae_args={"problem_statement": "ESS Service Unavailable"})
    t2 = await conv.turn("4g, Ericsson", [DOC.select(), CATALOG, _governed(ALT_ID, "Check active alarms on the node")], "Okay.",
                         delegate=False)
    _assert_forced(t2, OperationalTurnKind.CLARIFICATION_ANSWER)
    assert _authorized(t2, "alt")


@pytest.mark.asyncio
async def test_live_prompt4_turn3_empty_first_answer_is_recovered(conversation) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _prompt4_turns_1_2(conv)
    # Live: search (AVAILABLE only), then an EMPTY final answer. The regenerated answer goes through
    # the normal chain, including the existing selection remediation (tools enabled there).
    t3 = await _result_turn(conv, [SEARCH, EMPTY, PLUGIN_STEP, DOC.select(), CATALOG, PLUGIN_STEP])
    _assert_forced(t3, OperationalTurnKind.RESULT_PROVIDED)
    assert len(_events(t3, "specialist_invocation")) == 1
    assert _structured(t3)[:2] == [(1, "initial", "invalid", "empty_response"), (2, "regeneration", "valid", None)]
    assert len(_retries(t3)) == 1
    (record,) = t3["records"]
    assert record[so.STRUCTURED_OUTPUT_RECORD_KEY]["outcome"] == "recovered"
    _assert_alt_result_bound(t3)
    assert _authorized(t3, "st pluginunit") and "`st pluginunit`" in t3["final"]
    assert t3["final"] != OPERATIONAL_ROUTE_UNAVAILABLE_TEXT


# =============================================================================================
# 24. Mutation tests: each protection is what makes the tests above pass
# =============================================================================================


@pytest.mark.asyncio
async def test_mutation_without_retry_eligibility_the_live_turn3_failure_reproduces(conversation, monkeypatch) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _prompt4_turns_1_2(conv)
    monkeypatch.setattr(tae_agent_tool, "classify_structured_output", lambda content, schema: None)
    t3 = await _result_turn(conv, [SEARCH, EMPTY, PLUGIN_STEP, DOC.select(), CATALOG, PLUGIN_STEP])
    # The live defect: the forced turn fails closed on the empty answer; no validated record.
    assert t3["records"] == [] and t3["final"] == OPERATIONAL_ROUTE_UNAVAILABLE_TEXT
    assert not _retries(t3)


@pytest.mark.asyncio
async def test_mutation_without_the_retry_maximum_a_third_attempt_happens(conversation, monkeypatch) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    monkeypatch.setattr(so, "MAX_STRUCTURED_OUTPUT_ATTEMPTS", 3)
    monkeypatch.setattr(tae_agent_tool, "MAX_STRUCTURED_OUTPUT_ATTEMPTS", 3)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, EMPTY, EMPTY, PLUGIN_STEP])
    with pytest.raises(AssertionError):
        _assert_failed_closed_after_two_attempts(t2, "empty_response")
    assert len(_requests(t2)) == 6


@pytest.mark.asyncio
async def test_mutation_without_tool_disable_the_regeneration_repeats_tools(conversation, monkeypatch) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    monkeypatch.setattr(tae_agent_tool, "_finalization_agent", lambda agent: agent)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, EMPTY, SEARCH, PLUGIN_STEP])
    with pytest.raises(AssertionError):
        _assert_tool_free(_requests(t2)[4])
    assert _tool_counts(t2)[0] == 2, "the search was executed again"


@pytest.mark.asyncio
async def test_mutation_without_same_session_reuse_the_run_context_is_lost(conversation, monkeypatch) -> None:
    repo, conv = conversation
    await repo.add(DOC.knowledge())
    await _alt_presented(conv)
    original = tae_agent_tool._regenerate_structured_output

    async def _fresh_session(runner: Any, session: Any, *args: Any, **kwargs: Any) -> Any:
        fresh = await runner.session_service.create_session(app_name=runner.app_name, user_id=session.user_id, state={})
        return await original(runner, fresh, *args, **kwargs)

    monkeypatch.setattr(tae_agent_tool, "_regenerate_structured_output", _fresh_session)
    t2 = await _result_turn(conv, [SEARCH, DOC.select(), CATALOG, EMPTY, PLUGIN_STEP])
    regeneration = _requests(t2)[-1]
    assert _function_responses(regeneration) == [], "tool results of the run are no longer in context"
    assert _user_texts(regeneration)[0] != _user_texts(_requests(t2)[0])[0], "the original request is no longer in context"
