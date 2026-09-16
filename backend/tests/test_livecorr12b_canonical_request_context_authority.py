"""LIVE-CORR-12B -- Canonical Request-Context Parameter Authority.

THE GAP THIS CLOSES: the LIVE-CORR-12A architectural audit proved there was
no canonical registry or alias table for `RequestContract.provided_context`/
`missing_context` parameter NAMES -- only VALUES (`_canonicalize_if_
identifier`) were ever normalized. Live-reproduced: identical semantic input
("give me a command to restart an RRU" -> "the RRU is RRU-3") produced
`status=ALLOW` when the model happened to name the parameter `unit_id`, and
`status=AMBIGUOUS` (`missing_context=["unit_id", "unit_type"]`) when it
happened to name the SAME parameter `RRU_ID` instead -- a completely
model-controlled, unauditable parameter-name authority.

FIX: a small, explicit, closed alias table (`CANONICAL_PARAMETER_ALIASES`,
request_contract.py) plus two pure functions -- `canonical_parameter_name`
(one name -> canonical form, or `None`) and `authoritative_missing_context_
names` (filters a `missing_context` list down to ONLY names deterministic
policy actually understands). `provided_context` NAMES are canonicalized
(with fail-closed alias-conflict handling) before value verification, before
`missing_context` reconciliation, and before pending-request persistence.
`derive_execution_decision` (request_execution_policy.py) now bases
`NEEDS_INFORMATION` gating on the AUTHORITATIVE (canonical-only) subset of
`missing_context`, never the model's raw, unconstrained declaration.

This module does NOT touch governed-evidence continuity, semantic VALUE
verification ("the alarm is gone" -> alarm_status=cleared), or INVALID_
CONTRACT gating behavior -- those remain explicitly out of scope for this
pass (see the LIVE-CORR-12B milestone instruction).

No real Gemini call, no external network, no Cloud SQL requirement, no
Teams/Power Automate write.
"""
from __future__ import annotations

import pytest

from backend.agents.team_manager.request_contract import (
    PENDING_GOVERNED_REQUEST_STATE_KEY,
    REQUEST_CONTRACT_TOOL_NAME,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    PendingGovernedRequest,
    PendingGovernedRequestStatus,
    RequestClass,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    authoritative_missing_context_names,
    canonical_parameter_name,
    canonicalize_provided_context,
    parse_pending_governed_request,
    record_request_contract,
    required_target_parameter_gaps,
    safe_request_contract_observability_fields,
    validate_and_persist_request_contract,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionStatus,
    build_pending_governed_request_state_update,
    derive_execution_decision,
    resolve_effective_governed_contract,
)

_RUN_ID = "livecorr12b-run"
_ALARM_LISTING_COMMAND = "get alarms"


# =============================================================================
# Shared test scaffolding -- mirrors test_6a13_request_contract_foundation.py
# =============================================================================


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


def _stored_contract(ctx: _FakeToolContext, run_id: str = _RUN_ID) -> RequestContract:
    raw = _stored(ctx)
    assert raw is not None
    contract = RequestContract.model_validate(raw)
    return contract.model_copy(update={"run_id": run_id})


def _param(name: str, value: str) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance=ParameterProvenance.USER)


def _contract(**overrides) -> RequestContract:
    defaults = dict(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        provided_context=[],
        missing_context=[],
        run_id=_RUN_ID,
    )
    defaults.update(overrides)
    return RequestContract(**defaults)


# =============================================================================
# A -- alias normalization: RRU_ID=RRU-3 becomes authoritative unit_id=RRU-3
# =============================================================================


@pytest.mark.asyncio
async def test_a_rru_id_alias_normalizes_to_canonical_unit_id() -> None:
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("RRU_ID", "RRU-3")],
    )
    ctx = _observe("the RRU is RRU-3", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    names = {p["name"]: p["value"] for p in stored["provided_context"]}
    assert names == {"unit_id": "RRU-3"}
    assert "RRU_ID" not in names


# =============================================================================
# B -- case / known alias normalization
# =============================================================================


@pytest.mark.parametrize(
    "raw_name,expected",
    [
        ("unit_id", "unit_id"),
        ("RRU_ID", "unit_id"),
        ("rru_id", "unit_id"),
        ("Rru_Id", "unit_id"),
        ("unit_type", "unit_type"),
        ("UNIT_TYPE", "unit_type"),
        (" unit_id ", "unit_id"),
    ],
)
def test_b_known_aliases_normalize(raw_name: str, expected: str) -> None:
    assert canonical_parameter_name(raw_name) == expected


@pytest.mark.parametrize("raw_name", ["vendor", "technology", "software_version", "alarm_type", "alarm_status", "fault", "governed procedure"])
def test_b_unrecognized_names_are_not_canonicalized(raw_name: str) -> None:
    """Section 4's own explicit instruction: do NOT invent deterministic
    support for a parameter name merely because it looks plausible -- none
    of these names has any existing deterministic consumer in this
    codebase's request-context policy today."""
    assert canonical_parameter_name(raw_name) is None


# =============================================================================
# C -- same alias, duplicate value -> collapses to one canonical entry
# =============================================================================


def test_c_duplicate_alias_same_value_collapses_to_one_entry() -> None:
    merged = canonicalize_provided_context([_param("RRU_ID", "RRU-3"), _param("unit_id", "RRU-3")])
    assert len(merged) == 1
    assert merged[0].name == "unit_id"
    assert merged[0].value == "RRU-3"


# =============================================================================
# D -- conflicting aliases -> fail closed, no silent winner
# =============================================================================


def test_d_conflicting_aliases_drop_both_never_pick_a_winner() -> None:
    merged = canonicalize_provided_context([_param("RRU_ID", "RRU-3"), _param("unit_id", "RRU-10")])
    names = {p.name for p in merged}
    assert "unit_id" not in names
    assert "RRU-3" not in {p.value for p in merged}
    assert "RRU-10" not in {p.value for p in merged}


@pytest.mark.asyncio
async def test_d_conflicting_aliases_end_to_end_still_requires_confirmation() -> None:
    """The conflict must never silently authorize a command with EITHER
    candidate identifier -- the canonical parameter remains genuinely
    unresolved, exactly as if neither alias had been supplied."""
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("RRU_ID", "RRU-3"), _param("unit_id", "RRU-10")],
    )
    ctx = _observe("the RRU is RRU-3 or RRU-10", {}, result)
    contract = _stored_contract(ctx)
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert "unit_id" in decision.missing_context
    assert decision.may_emit_command is False


# =============================================================================
# E -- model-invented, unrecognized missing_context key must NOT force
# NEEDS_INFORMATION on its own
# =============================================================================


def test_e_unrecognized_missing_context_key_does_not_force_needs_information() -> None:
    contract = _contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="VSWR threshold",
        missing_context=["governed procedure"],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW


def test_e_authoritative_missing_context_names_excludes_unrecognized_keys() -> None:
    assert authoritative_missing_context_names(["governed procedure", "specific fault details"]) == []
    assert authoritative_missing_context_names(["unit_id", "governed procedure", "unit_type"]) == ["unit_id", "unit_type"]


def test_e_recognized_missing_context_key_still_forces_needs_information() -> None:
    """Non-regression for the ORIGINAL DEF-0037 fix: a CANONICAL
    model-declared gap must still force NEEDS_INFORMATION regardless of
    intent/requested_output classification."""
    contract = _contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="HW Partial Fault",
        missing_context=["unit_type", "unit_id"],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


# =============================================================================
# F -- model duplicate canonical + alias missing keys canonicalize without
# creating duplicate requirements
# =============================================================================


@pytest.mark.asyncio
async def test_f_duplicate_canonical_and_alias_missing_keys_deduplicate() -> None:
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        missing_context=["RRU_ID", "unit_id", "unit_type"],
    )
    ctx = _observe("give me a command to restart an RRU", {}, result)
    stored = _stored(ctx)
    assert stored is not None
    assert sorted(stored["missing_context"]) == ["unit_id", "unit_type"]


# =============================================================================
# G -- restart-RRU deterministic equivalence: RRU_ID=RRU-3 and unit_id=RRU-3
# produce the SAME execution-policy state
# =============================================================================


@pytest.mark.asyncio
async def test_g_alias_and_canonical_name_produce_identical_policy_state() -> None:
    async def _run(param_name: str) -> RequestExecutionStatus:
        result = await record_request_contract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            provided_context=[_param(param_name, "RRU-3")],
        )
        ctx = _observe("the RRU is RRU-3", {}, result)
        contract = _stored_contract(ctx)
        return derive_execution_decision(contract, _RUN_ID)

    decision_alias = await _run("RRU_ID")
    decision_canonical = await _run("unit_id")

    assert decision_alias.status == decision_canonical.status == RequestExecutionStatus.ALLOW
    assert decision_alias.may_emit_command == decision_canonical.may_emit_command is True
    assert decision_alias.missing_context == decision_canonical.missing_context == []


# =============================================================================
# H -- pending-request continuity survives alias variation across turns
# =============================================================================


@pytest.mark.asyncio
async def test_h_pending_continuity_survives_alias_variation_across_turns() -> None:
    """Turn 1: "give me a command to restart an RRU" -> unit_type=RRU
    supplied, unit_id still missing -> NEEDS_INFORMATION, persisted as an
    UNRESOLVED PendingGovernedRequest (canonical missing_context=["unit_id"]).
    Turn 2: "the RRU is RRU-3", but the MODEL this turn names the parameter
    `RRU_ID` instead of `unit_id` -- must still resolve the SAME pending
    operation, not be treated as two unrelated concepts."""
    turn1_result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("unit_type", "RRU")],
    )
    ctx1 = _observe("restart the RRU", {}, turn1_result)
    contract1 = _stored_contract(ctx1)
    decision1 = derive_execution_decision(contract1, _RUN_ID)
    assert decision1.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision1.missing_context == ["unit_id"]

    pending_update = build_pending_governed_request_state_update(decision1, contract1)
    pending_raw = pending_update[PENDING_GOVERNED_REQUEST_STATE_KEY]
    assert pending_raw is not None
    pending = parse_pending_governed_request(pending_raw)
    assert pending is not None
    assert pending.missing_context == ["unit_id"]  # already canonical, never "RRU_ID"

    turn2_result = await record_request_contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject="RRU ID",
        provided_context=[_param("RRU_ID", "RRU-3")],
    )
    state_after_turn1 = {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract1.model_dump(mode="json")}
    ctx2 = _observe("the RRU is RRU-3", state_after_turn1, turn2_result)
    contract2 = _stored_contract(ctx2)
    # This turn's own provided_context is already canonical -- the alias
    # was normalized at persist time, before pending-request matching ever runs.
    assert {p.name for p in contract2.provided_context} == {"unit_id"}

    decision2 = derive_execution_decision(contract2, _RUN_ID, pending_governed_request=pending)
    assert decision2.status == RequestExecutionStatus.ALLOW
    assert decision2.may_emit_command is True
    # `decision.missing_context` on an ALLOW outcome is purely informational
    # (echoes the effective contract's own `missing_context`, pre-existing,
    # unchanged-by-this-pass behavior -- see request_execution_policy.py's
    # own final ALLOW branch) -- the SAFETY-RELEVANT proof is `status`/
    # `may_emit_command` above, both correctly ALLOW/True despite the alias
    # variation.

    effective = resolve_effective_governed_contract(contract2, pending)
    assert {p.name: p.value for p in effective.provided_context} == {"unit_type": "RRU", "unit_id": "RRU-3"}


# =============================================================================
# I -- LIVE-CORR-9 preservation: target-independent alarm-listing command
# =============================================================================


def test_i_alarm_listing_still_requires_no_unit_context() -> None:
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND,
        RequestedOutput.EXACT_COMMAND,
        [_param("vendor", "Ericsson")],
        grounded_command_candidate=_ALARM_LISTING_COMMAND,
    )
    assert gaps == []

    contract = _contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="Alarm listing",
        provided_context=[_param("vendor", "Ericsson")],
        missing_context=[],
    )
    decision = derive_execution_decision(contract, _RUN_ID, grounded_command_candidate=_ALARM_LISTING_COMMAND)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is True


# =============================================================================
# J -- LIVE-CORR-5 preservation: general conversation is never operationally
# gated by a stray ambiguity flag
# =============================================================================


def test_j_general_conversation_ambiguity_does_not_trigger_operational_gate() -> None:
    contract = _contract(
        intent=RequestIntent.INFORMATION,
        requested_output=RequestedOutput.FACT,
        subject=None,
        ambiguity=True,
        missing_context=[],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW


# =============================================================================
# K -- LIVE-CORR-6 preservation: a fully-resolved troubleshooting request
# remains allowed and unaffected by the canonicalization change
# =============================================================================


def test_k_fully_resolved_troubleshooting_request_still_allowed() -> None:
    contract = _contract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="ESS Service Unavailable",
        missing_context=[],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is False
    assert decision.may_emit_operational_steps is True


# =============================================================================
# L -- observability: a stale contract now logs sufficient freshness metadata
# =============================================================================


def test_l_observability_exposes_contract_run_id_and_freshness() -> None:
    fresh_contract = _contract(run_id="run-current").model_dump(mode="json")
    stale_contract = _contract(run_id="run-PRIOR-turn").model_dump(mode="json")

    fresh_fields = safe_request_contract_observability_fields(fresh_contract, current_run_id="run-current")
    stale_fields = safe_request_contract_observability_fields(stale_contract, current_run_id="run-current")

    assert fresh_fields is not None and stale_fields is not None
    assert fresh_fields["contract_run_id"] == "run-current"
    assert fresh_fields["fresh"] is True
    assert stale_fields["contract_run_id"] == "run-PRIOR-turn"
    assert stale_fields["fresh"] is False

    # Backward-compatible default: omitting `current_run_id` still returns a
    # projection (every pre-existing call site is unaffected), just without
    # the `fresh` field.
    legacy_fields = safe_request_contract_observability_fields(fresh_contract)
    assert legacy_fields is not None
    assert "fresh" not in legacy_fields
    assert legacy_fields["contract_run_id"] == "run-current"
