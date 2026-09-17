"""Phase 6A.14 -- Request Parameter Consistency & Identifier Normalization.

Closes the proven RequestContract / execution-policy consistency defect:
`missing_context` was the model's own unmediated self-report, with no
deterministic reconciliation against VERIFIED `provided_context` --
`derive_execution_decision` trusted it as-is, so a model that omitted
`unit_id` from `missing_context` (correctly or not) let a governed
Knowledge EXAMPLE identifier (`RRU-9`) reach the user as though it were
the real, live target, even though `unit_id` was never genuinely
user-confirmed.

Also fixes safe, deterministic, token/boundary-safe normalization of
explicitly user-supplied operational identifiers ("RRU 5" <-> "RRU-5"),
which the pre-existing literal-substring-only provenance check silently
dropped purely due to formatting.

Deliberately does NOT implement command-template substitution -- see the
accompanying closure report's own read-only Cloud SQL source validation
(Section 13/14) for why generation of an RRU-5/AAS-X command remains
correctly withheld even after this pass.

Covers the milestone's own required test list (section 15), items 1-25.
"""
from __future__ import annotations

from typing import Optional

import pytest

from backend.agents.team_manager.request_contract import (
    ParameterProvenance,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    _verify_and_filter_provided_context_list,
    extract_canonical_identifiers,
    reconcile_missing_context,
    required_target_parameter_gaps,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionStatus,
    derive_execution_decision,
)

_RUN_ID = "run-6a14-param-consistency"


def _contract(**overrides) -> RequestContract:
    defaults = dict(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="HW Partial Fault",
        continuation=True,
        run_id=_RUN_ID,
    )
    defaults.update(overrides)
    return RequestContract(**defaults)


def _param(name: str, value: str, provenance: str = ParameterProvenance.USER) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance=provenance)


# =============================================================================
# 1/2: exact-command request with unit_type present, no unit_id -> gap added
# =============================================================================


def test_1_rru_unit_type_no_unit_id_requires_unit_id() -> None:
    provided = [_param("unit_type", "RRU")]
    gaps = required_target_parameter_gaps(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, provided)
    assert gaps == ["unit_id"]


def test_2_aas_unit_type_no_unit_id_requires_unit_id() -> None:
    provided = [_param("unit_type", "AAS")]
    gaps = required_target_parameter_gaps(RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND, provided)
    assert gaps == ["unit_id"]


def test_supportunit_never_requires_unit_id() -> None:
    # SupportUnit's own real governed branch is "No restart" -- there is
    # nothing to identify, and this must never be treated as a gap.
    provided = [_param("unit_type", "SupportUnit")]
    gaps = required_target_parameter_gaps(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, provided)
    assert gaps == []


def test_no_unit_type_at_all_does_not_fabricate_a_gap() -> None:
    assert required_target_parameter_gaps(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, []) == []


def test_non_target_specific_intent_never_requires_unit_id() -> None:
    provided = [_param("unit_type", "RRU")]
    assert required_target_parameter_gaps(RequestIntent.ACTION, RequestedOutput.ACTION, provided) == []
    assert required_target_parameter_gaps(RequestIntent.KNOWLEDGE_INVENTORY, RequestedOutput.KNOWLEDGE_LIST, provided) == []


# =============================================================================
# 3: model cannot erase a deterministically-required unit_id from
# missing_context
# =============================================================================


def test_3_model_cannot_erase_required_unit_id() -> None:
    provided = [_param("unit_type", "RRU")]
    # Model declares missing_context=[] despite unit_type=RRU / no unit_id.
    reconciled = reconcile_missing_context(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, provided, [])
    assert "unit_id" in reconciled


def test_3b_reconciliation_preserves_other_model_declared_keys() -> None:
    provided = [_param("unit_type", "RRU")]
    reconciled = reconcile_missing_context(
        RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, provided, ["some_other_fact"]
    )
    assert set(reconciled) == {"some_other_fact", "unit_id"}


def test_3c_reconciliation_removes_a_key_once_genuinely_verified() -> None:
    provided = [_param("unit_type", "RRU"), _param("unit_id", "RRU-5")]
    reconciled = reconcile_missing_context(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, provided, ["unit_id"])
    assert reconciled == []


def test_3d_reconciliation_is_deterministic_regardless_of_input_order() -> None:
    provided = [_param("unit_type", "RRU")]
    a = reconcile_missing_context(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, provided, ["z_fact", "a_fact"])
    b = reconcile_missing_context(RequestIntent.TROUBLESHOOTING, RequestedOutput.TROUBLESHOOTING_NEXT_STEP, provided, ["a_fact", "z_fact"])
    assert a == b == sorted({"a_fact", "z_fact", "unit_id"})


# =============================================================================
# 4: execution policy independently blocks a command even if missing_context
# was somehow malformed/empty (defense in depth, Section 7)
# =============================================================================


def test_4_execution_policy_blocks_even_with_malformed_empty_missing_context() -> None:
    contract = _contract(
        provided_context=[_param("unit_type", "RRU")],
        missing_context=[],  # malformed/stale state claiming nothing is missing
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False
    assert "unit_id" in decision.missing_context


# =============================================================================
# 5/6: Knowledge examples RRU-9/AAS-1 cannot satisfy unit_id
# =============================================================================


def test_5_knowledge_example_rru9_cannot_satisfy_unit_id() -> None:
    provided = [_param("unit_type", "RRU", ParameterProvenance.USER), _param("unit_id", "RRU-9", ParameterProvenance.USER)]
    verified = _verify_and_filter_provided_context_list(
        provided, current_turn_text="okie will do , also give me the cmd to restart the rru", session_confirmed={}
    )
    names = {p.name for p in verified}
    assert "unit_id" not in names  # RRU-9 never appears in the raw text -- correctly dropped


def test_6_knowledge_example_aas1_cannot_satisfy_unit_id() -> None:
    provided = [_param("unit_type", "AAS", ParameterProvenance.USER), _param("unit_id", "AAS-1", ParameterProvenance.USER)]
    verified = _verify_and_filter_provided_context_list(provided, current_turn_text="it's an AAS, give me the command", session_confirmed={})
    names = {p.name for p in verified}
    assert "unit_id" not in names


# =============================================================================
# 7/8/9: identifier normalization -- positive cases
# =============================================================================


@pytest.mark.parametrize("raw", ["RRU 5", "RRU-5", "rru 5", "rru-5", "RrU5"])
def test_7_8_9_various_spellings_verify_as_canonical_rru5(raw: str) -> None:
    ids = extract_canonical_identifiers(raw)
    assert ids == {"RRU-5"}


def test_provided_context_rru5_hyphen_verifies_against_raw_space_text() -> None:
    provided = [_param("unit_id", "RRU-5")]
    verified = _verify_and_filter_provided_context_list(provided, current_turn_text="but my issues is in RRU 5 not 9 ...", session_confirmed={})
    assert len(verified) == 1
    assert verified[0].value == "RRU-5"
    assert verified[0].provenance == ParameterProvenance.USER


def test_provided_context_rru5_space_form_verifies_and_canonicalizes() -> None:
    provided = [_param("unit_id", "RRU 5")]
    verified = _verify_and_filter_provided_context_list(provided, current_turn_text="my issue is rru 5", session_confirmed={})
    assert len(verified) == 1
    assert verified[0].value == "RRU-5"


# =============================================================================
# 10/11/12: negative identifier cases
# =============================================================================


def test_10_rru5_not9_verifies_rru5_never_rru9() -> None:
    ids = extract_canonical_identifiers("RRU 5 not 9")
    assert ids == {"RRU-5"}
    assert "RRU-9" not in ids


def test_11_aas3_never_verifies_rru3() -> None:
    provided = [_param("unit_id", "RRU-3")]
    verified = _verify_and_filter_provided_context_list(provided, current_turn_text="my target is AAS 3", session_confirmed={})
    assert verified == []


def test_12_rru15_never_verifies_rru5() -> None:
    provided = [_param("unit_id", "RRU-5")]
    verified = _verify_and_filter_provided_context_list(provided, current_turn_text="the unit is RRU 15", session_confirmed={})
    assert verified == []


# =============================================================================
# 13: bare "5" does not automatically verify RRU-5
# =============================================================================


def test_13_bare_five_does_not_become_rru5() -> None:
    ids = extract_canonical_identifiers("just give me unit 5 please")
    assert "RRU-5" not in ids
    assert ids == set()  # no adjacent recognized class prefix anywhere


def test_13b_bare_five_provided_context_never_canonicalizes_to_rru5() -> None:
    provided = [_param("unit_id", "5")]
    verified = _verify_and_filter_provided_context_list(provided, current_turn_text="the number is 5", session_confirmed={})
    # The literal "5" claim verifies via the pre-existing, unmodified
    # substring check (unchanged, orthogonal behavior) -- but it is never
    # PROMOTED to a fabricated "RRU-5"/"AAS-5".
    assert len(verified) == 1
    assert verified[0].value == "5"
    assert verified[0].value != "RRU-5"
    assert verified[0].value != "AAS-5"


# =============================================================================
# 14: canonical verified value retains USER provenance
# =============================================================================


def test_14_canonical_value_retains_user_provenance() -> None:
    provided = [_param("unit_id", "RRU-5", ParameterProvenance.USER)]
    verified = _verify_and_filter_provided_context_list(provided, current_turn_text="rru 5 is the one", session_confirmed={})
    assert verified[0].provenance == ParameterProvenance.USER


# =============================================================================
# 15/16: session continuation carries verified RRU-5 safely; different
# subject does not inherit it
# =============================================================================


def test_15_same_subject_continuation_carries_verified_rru5() -> None:
    provided = [_param("unit_id", "RRU-5")]
    # No current-turn text at all this time -- relies purely on session_confirmed.
    verified = _verify_and_filter_provided_context_list(provided, current_turn_text="", session_confirmed={"unit_id": "RRU-5"})
    assert len(verified) == 1
    assert verified[0].value == "RRU-5"


def test_15b_session_confirmed_space_form_still_matches_hyphen_claim() -> None:
    provided = [_param("unit_id", "RRU-5")]
    verified = _verify_and_filter_provided_context_list(provided, current_turn_text="", session_confirmed={"unit_id": "RRU 5"})
    assert len(verified) == 1
    assert verified[0].value == "RRU-5"


def test_16_different_subject_turn_does_not_inherit_rru5() -> None:
    # validate_and_persist_request_contract only populates session_confirmed
    # for a genuine same-subject continuation -- proven directly via the
    # real function using a fake tool_context. This is a plain, synchronous
    # function -- no event loop is needed or used.
    from backend.agents.team_manager.request_contract import (
        VALIDATED_REQUEST_CONTRACT_STATE_KEY,
        validate_and_persist_request_contract,
    )
    from backend.api.turn_context import bind_run_id, reset_run_id

    class _FakeUserContent:
        def __init__(self, text: str) -> None:
            self.parts = [type("P", (), {"text": text})()]

    class _FakeToolContext:
        def __init__(self, text: str, state: dict) -> None:
            self.user_content = _FakeUserContent(text)
            self.state = state

    class _FakeTool:
        name = "record_request_contract"

    run_id = "run-16-diff-subject"
    token = bind_run_id(run_id)
    try:
        prior_state = {
            VALIDATED_REQUEST_CONTRACT_STATE_KEY: RequestContract(
                intent=RequestIntent.TROUBLESHOOTING,
                requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
                subject="HW Partial Fault",
                continuation=True,
                provided_context=[_param("unit_id", "RRU-5")],
                run_id=run_id,
            ).model_dump(mode="json")
        }
        tool_context = _FakeToolContext("how do i troubleshoot VSWR Over Threshold?", prior_state)
        new_response = RequestContract(
            intent=RequestIntent.TROUBLESHOOTING,
            requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
            subject="VSWR Over Threshold",
            continuation=False,
            provided_context=[],
            missing_context=[],
        ).model_dump(mode="json")

        validate_and_persist_request_contract(_FakeTool(), {}, tool_context, new_response)
        stored = RequestContract.model_validate(tool_context.state[VALIDATED_REQUEST_CONTRACT_STATE_KEY])
        assert stored.subject == "VSWR Over Threshold"
        assert all(p.name != "unit_id" for p in stored.provided_context)
    finally:
        reset_run_id(token)


# =============================================================================
# 17: stale run_id cannot authorize context
# =============================================================================


def test_17_stale_run_id_cannot_authorize_context() -> None:
    contract = _contract(provided_context=[_param("unit_type", "RRU"), _param("unit_id", "RRU-5")], missing_context=[])
    decision = derive_execution_decision(contract, "a-different-current-run-id")
    assert decision.status == RequestExecutionStatus.INVALID_CONTRACT
    assert decision.may_emit_command is False


# =============================================================================
# 18/19: Turn 3 "give me cmd to restart the rru" remains NEEDS_INFORMATION,
# no RRU-9 in output
# =============================================================================


def test_18_turn3_no_unit_id_remains_needs_information() -> None:
    contract = _contract(
        provided_context=[_param("unit_type", "RRU")],
        missing_context=[],  # model incorrectly believes nothing is missing
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.may_emit_command is False


def test_19_no_rru9_command_reaches_output_in_that_state() -> None:
    from backend.agents.team_manager.request_execution_policy import command_suppression_fallback_text

    contract = _contract(provided_context=[_param("unit_type", "RRU")], missing_context=[])
    decision = derive_execution_decision(contract, _RUN_ID)
    fallback = command_suppression_fallback_text(decision)
    assert "RRU-9" not in fallback
    assert "restartunit" not in fallback


# =============================================================================
# 20: existing exact command grounding remains unchanged
# =============================================================================


def test_20_exact_command_grounding_unchanged() -> None:
    from backend.agents.incident_manager.evidence import enforce_procedure_scoped_command_grounding

    from backend.agents.incident_manager.schemas import TroubleshootingGuidance, TroubleshootingInteractionMode
    from backend.api.turn_context import bind_run_id, reset_run_id
    from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
    from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
    from backend.knowledge.provenance.contracts import (
        KnowledgeEvidenceItem,
        KnowledgeEvidenceReference,
        KnowledgeEvidenceSelectionKey,
        KnowledgeEvidenceSet,
    )
    from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
    from backend.tools.knowledge import runtime as rt

    knowledge_id = "6a14-param-consistency-doc"
    section = KnowledgeSection(
        section_id="s-hwpf", knowledge_id=knowledge_id, sequence=0, heading="HW Partial Fault",
        content="accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1",
    )
    source = KnowledgeSource(source_system="test", source_id="doc")
    reference = KnowledgeEvidenceReference(knowledge_id=knowledge_id, version_label="v1", section_id="s-hwpf", source_system="test", source_id="doc")
    item = KnowledgeEvidenceItem(reference=reference, title="t", document_type=KnowledgeDocumentType.MOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)

    run_id = "run-20-grounding-unchanged"
    token = bind_run_id(run_id)
    try:
        rt.get_or_init_run_state(run_id)
        execution = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=[item]))
        rt.record_search_result(run_id, execution)
        rt.select_evidence(run_id, [KnowledgeEvidenceSelectionKey(knowledge_id=knowledge_id, version_label="v1", section_id="s-hwpf")])

        # RRU-9 genuinely grounded -> still passes.
        guidance_ok = TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, command="accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1")
        corrected_ok, stripped_ok = enforce_procedure_scoped_command_grounding(guidance_ok, run_id, "how do i handle HW Partial Fault?")
        assert stripped_ok is False
        assert corrected_ok.command == "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"

        # RRU-5 never grounded -> still stripped (exact grounding unchanged).
        guidance_bad = TroubleshootingGuidance(interaction_mode=TroubleshootingInteractionMode.NEXT_STEP, command="accn FieldReplaceableUnit=RRU-5 restartunit 1 1 1")
        corrected_bad, stripped_bad = enforce_procedure_scoped_command_grounding(guidance_bad, run_id, "how do i handle HW Partial Fault?")
        assert stripped_bad is True
        assert corrected_bad.command is None
    finally:
        rt.discard_knowledge_run_evidence_state(run_id)
        reset_run_id(token)


# =============================================================================
# Non-fabrication proof: RRU-5 identifier accepted as live target context,
# but 6A.14 does NOT weaken grounding to emit a synthesized command
# (Section 12's own explicit "do not generate RRU-5 command yet")
# =============================================================================


def test_rru5_accepted_as_context_but_command_still_withheld_end_to_end() -> None:
    """LIVE-CORR-3B -- Operational Authority Boundary: this contract's own
    default `requested_output` is TROUBLESHOOTING_NEXT_STEP, which no
    longer implicitly grants exact-command permission (item 1) even once
    the parameter-consistency gate itself is satisfied -- command
    permission now requires a separately validated EXACT_COMMAND request.
    Actually emitting a grounded "RRU-5" command remains evidence.py's
    own, completely separate, unchanged job regardless -- proven in
    test_20 above that RRU-5 still cannot pass exact-verbatim grounding."""
    contract = _contract(
        provided_context=[_param("unit_type", "RRU"), _param("unit_id", "RRU-5")],
        missing_context=[],
    )
    decision = derive_execution_decision(contract, _RUN_ID)
    # unit_id IS present -> the parameter-consistency gate itself is satisfied.
    assert decision.status == RequestExecutionStatus.ALLOW
    assert decision.may_emit_command is False


# =============================================================================
# 21-25: existing DEF-0024/DEF-0026/DEF-0027/6A.13/6A.14 suites remain green
# -- enforced by running them explicitly in the same regression pass (see
# this milestone's own closure report), not duplicated here.
# =============================================================================
