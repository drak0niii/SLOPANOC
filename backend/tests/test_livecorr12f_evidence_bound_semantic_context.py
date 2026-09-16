"""LIVE-CORR-12F -- Evidence-Bound Semantic Context Verification.

THE GAP THIS CLOSES: `_verify_and_filter_provided_context`'s own three
existing paths (literal substring, session-confirmed, identifier-class)
could never verify a qualitative state fact whose canonical VALUE is not
itself a literal word the user said -- "the alarm is gone" never
contains the literal string "cleared", so a model candidate `alarm_
status=cleared` was unconditionally dropped. This module proves the new,
deliberately narrow FOURTH verification path (`_verify_semantic_context_
entry`, request_contract.py) closes this for exactly one parameter
(`alarm_status`, two closed values) via a small, closed, literal-phrase
allowlist -- never a general NLP classifier, never fuzzy/keyword
routing, and structurally (not by explicit negation detection) rejects
negated/contradictory phrasing.

`alarm_status` remains CANONICAL CONTEXT ONLY -- it is deliberately never
added to `CANONICAL_PARAMETER_ALIASES`/`authoritative_missing_context_
names` (LIVE-CORR-12B), so it can never become a REQUIRED field or grant
command/action authority by itself.

Section 20's own explicit requirement: at least one test here runs the
REAL `record_request_contract` -> `validate_and_persist_request_contract`
-> `derive_execution_decision` path, not merely the helper function in
isolation.

No real Gemini call anywhere in this file.
"""
from __future__ import annotations

import pytest

from backend.agents.team_manager.request_contract import (
    REQUEST_CONTRACT_TOOL_NAME,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    CANONICAL_PARAMETER_ALIASES,
    ParameterProvenance,
    RequestClass,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    SEMANTIC_CONTEXT_PARAMETER_NAMES,
    _verify_and_filter_provided_context,
    authoritative_missing_context_names,
    record_request_contract,
    validate_and_persist_request_contract,
)
from backend.agents.team_manager.request_execution_policy import RequestExecutionStatus, derive_execution_decision

_RUN_ID = "livecorr12f-run"


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


def _stored_contract(ctx: _FakeToolContext, run_id: str = _RUN_ID) -> RequestContract:
    raw = ctx.state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY)
    assert raw is not None
    contract = RequestContract.model_validate(raw)
    return contract.model_copy(update={"run_id": run_id})


def _param(name: str, value: str) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance=ParameterProvenance.USER)


# =============================================================================
# A -- positive semantic state
# =============================================================================


@pytest.mark.asyncio
async def test_a_alarm_is_gone_verifies_as_cleared() -> None:
    result = await record_request_contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU alarm",
        continuation=True,
        provided_context=[_param("alarm_status", "cleared")],
    )
    ctx = _observe("the alarm is gone", {}, result)
    contract = _stored_contract(ctx)
    names = {p.name: p.value for p in contract.provided_context}
    assert names == {"alarm_status": "cleared"}


# =============================================================================
# B -- alternative legitimate variation
# =============================================================================


@pytest.mark.asyncio
async def test_b_alarm_has_cleared_verifies_as_cleared() -> None:
    result = await record_request_contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU alarm",
        continuation=True,
        provided_context=[_param("alarm_status", "cleared")],
    )
    ctx = _observe("the alarm has cleared now", {}, result)
    contract = _stored_contract(ctx)
    names = {p.name: p.value for p in contract.provided_context}
    assert names == {"alarm_status": "cleared"}


# =============================================================================
# C -- active state
# =============================================================================


@pytest.mark.asyncio
async def test_c_alarm_still_active_verifies_as_active() -> None:
    result = await record_request_contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU alarm",
        continuation=True,
        provided_context=[_param("alarm_status", "active")],
    )
    ctx = _observe("the alarm is still active", {}, result)
    contract = _stored_contract(ctx)
    names = {p.name: p.value for p in contract.provided_context}
    assert names == {"alarm_status": "active"}


# =============================================================================
# D / E -- negation safety
# =============================================================================


@pytest.mark.asyncio
async def test_d_negated_cleared_is_not_verified() -> None:
    result = await record_request_contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU alarm",
        continuation=True,
        provided_context=[_param("alarm_status", "cleared")],
    )
    ctx = _observe("the alarm is not cleared", {}, result)
    contract = _stored_contract(ctx)
    assert contract.provided_context == []


@pytest.mark.asyncio
async def test_e_negated_active_is_not_verified() -> None:
    result = await record_request_contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU alarm",
        continuation=True,
        provided_context=[_param("alarm_status", "active")],
    )
    ctx = _observe("there are no active alarms", {}, result)
    contract = _stored_contract(ctx)
    assert contract.provided_context == []


def test_d_e_direct_helper_negation_matrix() -> None:
    """Direct, unit-level confirmation of the same negation matrix,
    isolated from the full contract-persistence machinery."""
    contradictory_cases = [
        ("cleared", "the alarm is not cleared"),
        ("cleared", "the alarm has not cleared"),
        ("active", "there are no active alarms"),
        ("active", "the alarm is not active"),
    ]
    for value, text in contradictory_cases:
        verified = _verify_and_filter_provided_context([_param("alarm_status", value)], text, {})
        assert verified == [], f"{value!r} must not verify against {text!r}"


# =============================================================================
# F -- hallucinated candidate: no supporting evidence at all
# =============================================================================


@pytest.mark.asyncio
async def test_f_hallucinated_alarm_status_is_dropped() -> None:
    result = await record_request_contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU",
        continuation=True,
        provided_context=[_param("alarm_status", "cleared")],
    )
    ctx = _observe("RRU-3 is affected", {}, result)
    contract = _stored_contract(ctx)
    assert contract.provided_context == []


# =============================================================================
# G -- evidence from a prior turn only does not verify a fresh claim
# =============================================================================


def test_g_current_turn_with_no_evidence_and_no_carry_forward_fails() -> None:
    """Isolated from continuation/session-confirmed carry-forward
    entirely (that is a SEPARATE, already-proven-safe mechanism, not
    reopened by this milestone) -- a freshly-claimed `alarm_status`
    candidate with NOTHING in the current turn's own text to support it
    must fail, regardless of what a prior turn may have said."""
    verified = _verify_and_filter_provided_context(
        [_param("alarm_status", "cleared")], "what does alt command do?", {}
    )
    assert verified == []


# =============================================================================
# H -- correction: active -> cleared, no duplicate/conflicting state
# =============================================================================


@pytest.mark.asyncio
async def test_h_correction_from_active_to_cleared() -> None:
    prior_state = {
        VALIDATED_REQUEST_CONTRACT_STATE_KEY: RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject="RRU alarm",
            provided_context=[_param("alarm_status", "active")],
        ).model_dump(mode="json")
    }
    result = await record_request_contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU alarm",
        continuation=True,
        provided_context=[_param("alarm_status", "cleared")],
    )
    ctx = _observe("actually the alarm is gone now", prior_state, result)
    contract = _stored_contract(ctx)
    names = {p.name: p.value for p in contract.provided_context}
    assert names == {"alarm_status": "cleared"}  # no duplicate/conflicting entries


# =============================================================================
# I -- semantic context cannot grant command authority (REAL contract ->
# decision path, section 20's own explicit requirement)
# =============================================================================


@pytest.mark.asyncio
async def test_i_semantic_context_cannot_grant_command_authority() -> None:
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("alarm_status", "cleared")],
    )
    ctx = _observe("the alarm is gone, please restart the RRU", {}, result)
    contract = _stored_contract(ctx)

    names = {p.name: p.value for p in contract.provided_context}
    assert names == {"alarm_status": "cleared"}  # accepted as canonical context

    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False
    assert "unit_id" in decision.missing_context
    assert "alarm_status" not in decision.missing_context


# =============================================================================
# J -- semantic context cannot substitute for grounding (policy layer
# proof; the separate, unchanged evidence.py grounding layer is not
# re-tested here)
# =============================================================================


@pytest.mark.asyncio
async def test_j_fully_resolved_target_plus_semantic_context_still_requires_fresh_grounding() -> None:
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("unit_type", "RRU"), _param("unit_id", "RRU-3"), _param("alarm_status", "cleared")],
    )
    ctx = _observe("the alarm is gone, restart RRU-3", {}, result)
    contract = _stored_contract(ctx)

    decision = derive_execution_decision(contract, _RUN_ID)
    # Policy layer permits a command to be ELIGIBLE (all canonical target
    # context resolved) -- this is NEVER the same as a command actually
    # reaching the user; `evidence.py`'s own, separate, completely
    # unchanged grounding layer (DEF-0024/0026/0027) still independently
    # requires a real, verbatim-grounded candidate before any command
    # text is shown -- not re-tested here, already extensively covered
    # by its own existing suite (unaffected by this pass).
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is True


# =============================================================================
# K -- unknown semantic parameter is never accepted
# =============================================================================


@pytest.mark.asyncio
async def test_k_unknown_semantic_parameter_never_gains_policy_authority() -> None:
    """IMPORTANT, honestly-documented pre-existing nuance (unrelated to
    this pass's own new semantic-context mechanism): `_verify_and_filter_
    provided_context`'s own PATH 1 (bare literal substring of `value`)
    has ALWAYS verified ANY parameter name -- known or not -- whenever
    its claimed value happens to appear literally in the user's text;
    name-gating has only ever governed whether a name can become
    AUTHORITATIVE REQUIRED context (`CANONICAL_PARAMETER_ALIASES`/
    `authoritative_missing_context_names`), never whether an arbitrary
    name can appear as VERIFIED provided_context at all. `magical_
    network_state=healthy` therefore DOES survive verification here
    (pre-existing behavior, not introduced by LIVE-CORR-12F) -- the real,
    provable safety boundary this test confirms is that it is not, and
    can never become, a recognized `SEMANTIC_CONTEXT_PARAMETER_NAMES`/
    `CANONICAL_PARAMETER_ALIASES` entry, so it can never affect `derive_
    execution_decision`'s own missing-context/command-authority
    computation."""
    assert "magical_network_state" not in SEMANTIC_CONTEXT_PARAMETER_NAMES
    assert "magical_network_state" not in CANONICAL_PARAMETER_ALIASES
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("magical_network_state", "healthy")],
    )
    ctx = _observe("the network is healthy, please restart the RRU", {}, result)
    contract = _stored_contract(ctx)

    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False
    assert "magical_network_state" not in decision.missing_context
    assert set(decision.missing_context) == {"unit_id", "unit_type"}


def test_k_alarm_status_never_becomes_an_authoritative_required_field() -> None:
    """Section 4/10's own core invariant: accepting `alarm_status` as
    canonical CONTEXT must never make it authoritative REQUIRED context."""
    assert "alarm_status" not in CANONICAL_PARAMETER_ALIASES
    assert authoritative_missing_context_names(["alarm_status"]) == []
    assert authoritative_missing_context_names(["alarm_status", "unit_id"]) == ["unit_id"]


# =============================================================================
# L -- LIVE-CORR-12B canonical alias regression
# =============================================================================


@pytest.mark.asyncio
async def test_l_canonical_alias_regression_unaffected() -> None:
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("RRU_ID", "RRU-3")],
    )
    ctx = _observe("the RRU is RRU-3", {}, result)
    contract = _stored_contract(ctx)
    names = {p.name for p in contract.provided_context}
    assert names == {"unit_id"}


# =============================================================================
# M -- LIVE-CORR-11 pending-request regression
# =============================================================================


@pytest.mark.asyncio
async def test_m_pending_request_continuity_unaffected_by_semantic_context() -> None:
    from backend.agents.team_manager.request_contract import PendingGovernedRequest, PendingGovernedRequestStatus
    from backend.agents.team_manager.request_execution_policy import resolve_effective_governed_contract

    pending = PendingGovernedRequest(
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        intent=RequestIntent.COMMAND,
        status=PendingGovernedRequestStatus.UNRESOLVED,
        subject="restart RRU",
        missing_context=["unit_id"],
        provided_context=[_param("unit_type", "RRU")],
    )
    result = await record_request_contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU ID",
        provided_context=[_param("unit_id", "RRU-3"), _param("alarm_status", "cleared")],
    )
    ctx = _observe("the alarm is gone, it is RRU-3", {}, result)
    contract = _stored_contract(ctx)

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is True

    effective = resolve_effective_governed_contract(contract, pending)
    effective_names = {p.name for p in effective.provided_context}
    assert {"unit_type", "unit_id"} <= effective_names  # alarm_status never displaces required target fields


# =============================================================================
# N -- LIVE-CORR-12D governed-evidence continuity regression
# =============================================================================


def test_n_governed_evidence_continuity_gate_unaffected() -> None:
    from backend.agents.team_manager.request_execution_policy import is_governed_evidence_continuity_permitted

    contract = RequestContract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="alt command",
        continuation=False,
        provided_context=[_param("alarm_status", "cleared")],
        request_class=RequestClass.OPERATIONAL_INFORMATION,
        run_id=_RUN_ID,
    )
    assert is_governed_evidence_continuity_permitted(contract, _RUN_ID) is False


# =============================================================================
# O -- LIVE-CORR-12E.2 clarification-rendering regression
# =============================================================================


def test_o_clarification_renderer_schema_still_structurally_closed() -> None:
    """CONTROL-PLANE-SEQ-06 -- reconciled with LIVE-CORR-12H (a later,
    deliberate reversal of the closed `{field_order, style}` design this
    test originally asserted): natural, model-authored `message` wording
    was explicitly restored, with `fields_asked` (validated by set-
    equality against `execution_decision.missing_context`, unchanged)
    remaining the ONE structural safety boundary. "Structurally closed"
    now means exactly these two fields, and no silent drift back toward a
    THIRD, unvalidated field -- never that `message` itself is
    template-only (that was the now-superseded 12E.1/12E.2 design)."""
    from backend.agents.team_manager.clarification_renderer import ClarificationRenderingPlan

    assert set(ClarificationRenderingPlan.model_fields.keys()) == {"message", "fields_asked"}
