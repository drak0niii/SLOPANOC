"""Phase 6A.13 -- Request Contract Foundation.

Proves `backend/agents/team_manager/request_contract.py`'s two validation
layers (structural, at the tool boundary; provenance/continuity, in the
`after_tool_callback`) close the exact live defect that motivated this
milestone: "how do i handle HW Partial Fault?" -> "it's an RRU" must
NEVER surface `unit_id=RRU-9` merely because RRU-9 is a real, Approved,
governed EXAMPLE identifier -- the user never supplied it.

Covers, per the milestone's own required examples (section 17) and
adversarial tests (section 18):
  A/B/E.  structural round-trips for INFORMATION/PROCEDURE/
          KNOWLEDGE_INVENTORY-shaped contracts
  F.      ACTION always forces approval_required=true, deterministically
  G.      THE live defect -- a Knowledge-example unit_id is dropped
  H.      a value the user ACTUALLY typed this turn is accepted
  I.      SupportUnit is never confused with RRU/AAS
  J.      an ambiguous request with no prior context stays ambiguous
  --      model cannot inject an unknown intent/requested_output
  --      model cannot fabricate a USER-provenance parameter
  --      prior session state never leaks into a fresh session
  --      malformed contract fails closed (state left untouched)
  --      explicit topic change replaces the prior subject
  --      continuation does not permanently pin an old subject
"""
from __future__ import annotations

import pytest

from backend.agents.team_manager.request_contract import (
    REQUEST_CONTRACT_TOOL_NAME,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    _VALID_INTENTS,
    _VALID_REQUESTED_OUTPUTS,
    record_request_contract,
    safe_request_contract_observability_fields,
    validate_and_persist_request_contract,
)


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [_FakePart(text)]


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeToolContext:
    def __init__(self, user_text: str, state: dict | None = None) -> None:
        self.user_content = _FakeContent(user_text)
        self.state = dict(state or {})


def _observe(user_text: str, state: dict, tool_response, tool_name: str = REQUEST_CONTRACT_TOOL_NAME) -> _FakeToolContext:
    ctx = _FakeToolContext(user_text, state=state)
    validate_and_persist_request_contract(_FakeTool(tool_name), {}, ctx, tool_response)
    return ctx


def _stored(ctx: _FakeToolContext) -> dict | None:
    return ctx.state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY)


# --- structural validation (RequestContract / RequestParameter) -----------


@pytest.mark.parametrize("intent", sorted(_VALID_INTENTS))
def test_all_valid_intents_accepted(intent: str) -> None:
    contract = RequestContract(intent=intent, requested_output=RequestedOutput.FACT)
    assert contract.intent == intent


def test_invalid_intent_rejected() -> None:
    with pytest.raises(Exception):
        RequestContract(intent="not_a_real_intent", requested_output=RequestedOutput.FACT)


@pytest.mark.parametrize("requested_output", sorted(_VALID_REQUESTED_OUTPUTS))
def test_all_valid_requested_outputs_accepted(requested_output: str) -> None:
    contract = RequestContract(intent=RequestIntent.INFORMATION, requested_output=requested_output)
    assert contract.requested_output == requested_output


def test_invalid_requested_output_rejected() -> None:
    with pytest.raises(Exception):
        RequestContract(intent=RequestIntent.INFORMATION, requested_output="not_a_real_shape")


def test_invalid_parameter_provenance_rejected() -> None:
    with pytest.raises(Exception):
        RequestParameter(name="unit_type", value="RRU", provenance="knowledge")


def test_blank_parameter_name_rejected() -> None:
    with pytest.raises(Exception):
        RequestParameter(name="  ", value="RRU", provenance=ParameterProvenance.USER)


def test_blank_parameter_value_rejected() -> None:
    with pytest.raises(Exception):
        RequestParameter(name="unit_type", value="", provenance=ParameterProvenance.USER)


def test_blank_missing_context_entry_rejected() -> None:
    with pytest.raises(Exception):
        RequestContract(intent=RequestIntent.COMMAND, requested_output=RequestedOutput.EXACT_COMMAND, missing_context=[""])


def test_blank_subject_treated_as_unresolved() -> None:
    contract = RequestContract(intent=RequestIntent.COMMAND, requested_output=RequestedOutput.EXACT_COMMAND, subject="   ")
    assert contract.subject is None


# --- tool-level structural validation (record_request_contract) -----------


@pytest.mark.asyncio
async def test_tool_rejects_unknown_intent() -> None:
    result = await record_request_contract(intent="bogus", requested_output=RequestedOutput.FACT)
    assert "error" in result


@pytest.mark.asyncio
async def test_tool_rejects_unknown_requested_output() -> None:
    result = await record_request_contract(intent=RequestIntent.INFORMATION, requested_output="bogus")
    assert "error" in result


@pytest.mark.asyncio
async def test_tool_success_roundtrip() -> None:
    result = await record_request_contract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        continuation=False,
    )
    assert "error" not in result
    assert result["intent"] == RequestIntent.TROUBLESHOOTING
    assert result["subject"] == "HW Partial Fault"


# --- Examples A/B/E: structural round-trips --------------------------------


@pytest.mark.asyncio
async def test_example_a_information_fact() -> None:
    """"what is VSWR?" -> INFORMATION / FACT."""
    result = await record_request_contract(
        intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, subject="VSWR", requires_governed_knowledge=True
    )
    ctx = _observe("what is VSWR?", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert stored["intent"] == RequestIntent.INFORMATION
    assert stored["requested_output"] == RequestedOutput.FACT
    assert stored["subject"] == "VSWR"
    assert stored["requires_governed_knowledge"] is True


@pytest.mark.asyncio
async def test_example_b_procedure_steps() -> None:
    """"give me the VSWR procedure" -> PROCEDURE / PROCEDURE_STEPS."""
    result = await record_request_contract(
        intent=RequestIntent.PROCEDURE, requested_output=RequestedOutput.PROCEDURE_STEPS, subject="VSWR Over Threshold"
    )
    ctx = _observe("give me the VSWR procedure", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert stored["intent"] == RequestIntent.PROCEDURE
    assert stored["requested_output"] == RequestedOutput.PROCEDURE_STEPS


@pytest.mark.asyncio
async def test_example_e_knowledge_inventory() -> None:
    """"what MOPs do you have?" -> KNOWLEDGE_INVENTORY / KNOWLEDGE_LIST."""
    result = await record_request_contract(intent=RequestIntent.KNOWLEDGE_INVENTORY, requested_output=RequestedOutput.KNOWLEDGE_LIST)
    ctx = _observe("what MOPs do you have?", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert stored["intent"] == RequestIntent.KNOWLEDGE_INVENTORY
    assert stored["requested_output"] == RequestedOutput.KNOWLEDGE_LIST


# --- Example F: ACTION always forces approval_required ---------------------


@pytest.mark.asyncio
async def test_example_f_action_forces_approval_required_even_if_model_omits_it() -> None:
    """"send this to Teams" -> ACTION / ACTION, approval_required=true --
    forced deterministically, never trusted from the model alone."""
    result = await record_request_contract(
        intent=RequestIntent.ACTION,
        requested_output=RequestedOutput.ACTION,
        action_requested=True,
        approval_required=False,  # the model UNDER-claims this -- must be corrected
    )
    ctx = _observe("send this to Teams", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert stored["approval_required"] is True


@pytest.mark.asyncio
async def test_action_requested_flag_alone_also_forces_approval_required() -> None:
    """Even a non-ACTION intent that nonetheless sets `action_requested`
    must still force approval_required=true."""
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        action_requested=True,
        approval_required=False,
    )
    ctx = _observe("do it", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert stored["approval_required"] is True


# --- Example G: THE live defect ---------------------------------------------


@pytest.mark.asyncio
async def test_example_g_knowledge_example_unit_id_is_dropped() -> None:
    """THE mandatory regression proof: "it's an RRU" must produce
    `unit_type=RRU` but NEVER `unit_id=RRU-9` -- RRU-9 was never supplied
    by the user, only known from governed Knowledge's own example
    content."""
    prior_state = {
        VALIDATED_REQUEST_CONTRACT_STATE_KEY: RequestContract(
            intent=RequestIntent.TROUBLESHOOTING, requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP, subject="HW Partial Fault"
        ).model_dump(mode="json")
    }
    result = await record_request_contract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        continuation=True,
        provided_context=[
            RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
            RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),  # HALLUCINATED
        ],
        missing_context=["unit_id"],
    )
    ctx = _observe("it's an RRU", prior_state, result)
    stored = _stored(ctx)
    assert stored is not None
    names = {p["name"]: p["value"] for p in stored["provided_context"]}
    assert names == {"unit_type": "RRU"}
    assert "unit_id" not in names


# --- Example H: an explicitly user-supplied value is accepted --------------


@pytest.mark.asyncio
async def test_example_h_explicitly_supplied_unit_id_is_accepted() -> None:
    """Following G -- the user now literally types "RRU-9" -- this
    SPECIFIC turn's real text makes it verifiable, so it is kept."""
    prior_state = {
        VALIDATED_REQUEST_CONTRACT_STATE_KEY: RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="HW Partial Fault",
            continuation=True,
            provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        ).model_dump(mode="json")
    }
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="HW Partial Fault",
        continuation=True,
        provided_context=[
            RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
            RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
        ],
    )
    ctx = _observe("RRU-9", prior_state, result)
    stored = _stored(ctx)
    assert stored is not None
    names = {p["name"]: p["value"] for p in stored["provided_context"]}
    assert names == {"unit_type": "RRU", "unit_id": "RRU-9"}


# --- Example I: SupportUnit is never confused with RRU/AAS -----------------


@pytest.mark.asyncio
async def test_example_i_supportunit_never_confused_with_rru_or_aas() -> None:
    prior_state = {
        VALIDATED_REQUEST_CONTRACT_STATE_KEY: RequestContract(
            intent=RequestIntent.TROUBLESHOOTING, requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP, subject="HW Partial Fault"
        ).model_dump(mode="json")
    }
    result = await record_request_contract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        continuation=True,
        provided_context=[RequestParameter(name="unit_type", value="SupportUnit", provenance=ParameterProvenance.USER)],
    )
    ctx = _observe("it's a SupportUnit", prior_state, result)
    stored = _stored(ctx)
    assert stored is not None
    names = {p["name"]: p["value"] for p in stored["provided_context"]}
    assert names == {"unit_type": "SupportUnit"}
    assert "RRU" not in names.values()
    assert "AAS" not in names.values()


# --- Example J: ambiguity with no prior context -----------------------------


@pytest.mark.asyncio
async def test_example_j_ambiguous_request_with_no_prior_context_stays_ambiguous() -> None:
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject=None,
        continuation=True,  # the model WRONGLY claims continuation with nothing to continue
        missing_context=["procedure"],
        ambiguity=True,
    )
    ctx = _observe("give me the first cmd", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert stored["ambiguity"] is True
    assert stored["subject"] is None
    assert stored["continuation"] is False  # deterministically cleared -- nothing valid to continue
    assert "procedure" in stored["missing_context"]


# --- adversarial tests (section 18) -----------------------------------------


@pytest.mark.asyncio
async def test_model_cannot_inject_unknown_intent_into_stored_state() -> None:
    # The tool itself already rejects this -- prove the callback ALSO
    # never persists a hand-crafted malformed dict that bypassed the tool.
    ctx = _observe("anything", {}, {"intent": "not_real", "requested_output": RequestedOutput.FACT})
    assert _stored(ctx) is None


@pytest.mark.asyncio
async def test_model_cannot_inject_unknown_requested_output_into_stored_state() -> None:
    ctx = _observe("anything", {}, {"intent": RequestIntent.INFORMATION, "requested_output": "not_real"})
    assert _stored(ctx) is None


@pytest.mark.asyncio
async def test_model_cannot_fabricate_user_provenance_parameter_with_no_textual_basis() -> None:
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="HW Partial Fault",
        provided_context=[RequestParameter(name="unit_id", value="AAS-1", provenance=ParameterProvenance.USER)],
    )
    ctx = _observe("how do i handle HW Partial Fault?", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert stored["provided_context"] == []


@pytest.mark.asyncio
async def test_knowledge_example_cannot_become_supplied_unit_id_full_pipeline() -> None:
    """Same as example G, phrased as the adversarial requirement itself:
    a governed EXAMPLE value can never become PROVIDED USER CONTEXT."""
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="HW Partial Fault",
        provided_context=[RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER)],
    )
    ctx = _observe("how do i handle HW Partial Fault?", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert stored["provided_context"] == []


def test_prior_session_state_never_leaks_into_a_fresh_session() -> None:
    """A genuinely separate session's `tool_context.state` starts empty --
    no code path here could ever read a DIFFERENT session's confirmed
    parameters, since each session owns its own real, isolated ADK
    session-state dict (the SAME structural guarantee every other
    session-state mechanism in this codebase already relies on)."""
    session_a_state = {
        VALIDATED_REQUEST_CONTRACT_STATE_KEY: RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="HW Partial Fault",
            provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        ).model_dump(mode="json")
    }
    session_b_state: dict = {}  # a genuinely different, fresh session
    ctx_b = _FakeToolContext("it's an RRU", state=session_b_state)
    assert ctx_b.state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY) is None
    validate_and_persist_request_contract(
        _FakeTool(REQUEST_CONTRACT_TOOL_NAME),
        {},
        ctx_b,
        {
            "intent": RequestIntent.TROUBLESHOOTING,
            "subject": "HW Partial Fault",
            "requested_output": RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            "requires_governed_knowledge": False,
            "requires_operational_context": False,
            "continuation": True,
            "provided_context": [{"name": "unit_type", "value": "RRU", "provenance": "user"}],
            "missing_context": [],
            "action_requested": False,
            "approval_required": False,
            "ambiguity": False,
        },
    )
    stored_b = _stored(ctx_b)
    assert stored_b is not None
    # session_a's own dict must be completely unaffected
    assert session_a_state[VALIDATED_REQUEST_CONTRACT_STATE_KEY]["provided_context"] == [
        {"name": "unit_type", "value": "RRU", "provenance": "user"}
    ]


def test_malformed_contract_fails_closed_state_left_untouched() -> None:
    prior_state = {
        VALIDATED_REQUEST_CONTRACT_STATE_KEY: RequestContract(
            intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT, subject="VSWR"
        ).model_dump(mode="json")
    }
    ctx = _observe("anything", dict(prior_state), {"intent": "garbage", "requested_output": "garbage"})
    # the prior, still-valid contract must be left completely untouched
    assert _stored(ctx) == prior_state[VALIDATED_REQUEST_CONTRACT_STATE_KEY]


def test_error_tool_response_is_never_persisted() -> None:
    ctx = _observe("anything", {}, {"error": {"code": "validation_error", "message": "x"}})
    assert _stored(ctx) is None


def test_wrong_tool_name_is_ignored() -> None:
    ctx = _FakeToolContext("anything")
    result = validate_and_persist_request_contract(
        _FakeTool("some_other_tool"),
        {},
        ctx,
        {"intent": RequestIntent.INFORMATION, "requested_output": RequestedOutput.FACT},
    )
    assert result is None
    assert _stored(ctx) is None


@pytest.mark.asyncio
async def test_explicit_topic_change_replaces_prior_subject_and_drops_old_parameters() -> None:
    prior_state = {
        VALIDATED_REQUEST_CONTRACT_STATE_KEY: RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="HW Partial Fault",
            provided_context=[
                RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER),
                RequestParameter(name="unit_id", value="RRU-9", provenance=ParameterProvenance.USER),
            ],
        ).model_dump(mode="json")
    }
    result = await record_request_contract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="Resource Activation Timeout",
        continuation=False,
    )
    ctx = _observe("how do I handle Resource Activation Timeout?", prior_state, result)
    stored = _stored(ctx)
    assert stored is not None
    assert stored["subject"] == "Resource Activation Timeout"
    assert stored["provided_context"] == []


@pytest.mark.asyncio
async def test_continuation_does_not_permanently_pin_old_subject() -> None:
    """A THIRD turn, after an explicit topic change, must not somehow
    revert to or blend with the FIRST topic's own confirmed parameters --
    proves the store is a plain overwrite, never an ever-growing history."""
    turn1_state = {
        VALIDATED_REQUEST_CONTRACT_STATE_KEY: RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="HW Partial Fault",
            provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        ).model_dump(mode="json")
    }
    # Turn 2: explicit topic change
    result2 = await record_request_contract(
        intent=RequestIntent.TROUBLESHOOTING, requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP, subject="VSWR Over Threshold"
    )
    ctx2 = _observe("how do i troubleshoot vswr?", turn1_state, result2)
    turn2_state = {VALIDATED_REQUEST_CONTRACT_STATE_KEY: _stored(ctx2)}
    assert turn2_state[VALIDATED_REQUEST_CONTRACT_STATE_KEY]["provided_context"] == []

    # Turn 3: continuation of the NEW subject only
    result3 = await record_request_contract(
        intent=RequestIntent.COMMAND, requested_output=RequestedOutput.EXACT_COMMAND, subject="VSWR Over Threshold", continuation=True
    )
    ctx3 = _observe("give me the first cmd", turn2_state, result3)
    stored3 = _stored(ctx3)
    assert stored3 is not None
    assert stored3["subject"] == "VSWR Over Threshold"
    assert stored3["provided_context"] == []  # never re-inherits HW Partial Fault's own unit_type=RRU


def test_repeated_calls_same_turn_last_one_wins() -> None:
    ctx = _FakeToolContext("anything")
    validate_and_persist_request_contract(
        _FakeTool(REQUEST_CONTRACT_TOOL_NAME),
        {},
        ctx,
        {"intent": RequestIntent.INFORMATION, "requested_output": RequestedOutput.FACT, "subject": "First"},
    )
    validate_and_persist_request_contract(
        _FakeTool(REQUEST_CONTRACT_TOOL_NAME),
        {},
        ctx,
        {"intent": RequestIntent.PROCEDURE, "requested_output": RequestedOutput.PROCEDURE_STEPS, "subject": "Second"},
    )
    stored = _stored(ctx)
    assert stored is not None
    assert stored["subject"] == "Second"
    assert stored["intent"] == RequestIntent.PROCEDURE


def test_case_insensitive_value_matching() -> None:
    result = {
        "intent": RequestIntent.TROUBLESHOOTING,
        "requested_output": RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        "subject": "HW Partial Fault",
        "requires_governed_knowledge": False,
        "requires_operational_context": False,
        "continuation": False,
        "provided_context": [{"name": "unit_type", "value": "rru", "provenance": "user"}],
        "missing_context": [],
        "action_requested": False,
        "approval_required": False,
        "ambiguity": False,
    }
    ctx = _observe("It's an RRU.", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert {p["name"]: p["value"] for p in stored["provided_context"]} == {"unit_type": "rru"}


# --- Observability (section 16) --------------------------------------------


def test_safe_observability_fields_never_include_parameter_values() -> None:
    contract = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="HW Partial Fault",
        provided_context=[RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)],
        missing_context=["unit_id"],
    )
    fields = safe_request_contract_observability_fields(contract.model_dump(mode="json"))
    assert fields is not None
    assert fields["provided_context_keys"] == ["unit_type"]
    assert "RRU" not in str(fields)
    assert fields["missing_context_keys"] == ["unit_id"]


def test_safe_observability_fields_returns_none_for_invalid_input() -> None:
    assert safe_request_contract_observability_fields({"intent": "garbage"}) is None
    assert safe_request_contract_observability_fields(None) is None
    assert safe_request_contract_observability_fields("not a dict") is None
