"""POST-6A PROMPT 2 -- focused checks for the operational-authority repairs.

Deliberately narrow, per the repair instruction: wrong-target, negated/
prefix identifiers, and free-text bypasses. No live calls, no model, no
full-stack turn.
"""
from __future__ import annotations

import pytest

from backend.agents.incident_manager.schemas import (
    TroubleshootingGuidance,
    TroubleshootingInteractionMode,
    TroubleshootingStep,
)
from backend.agents.team_manager.command_construction import (
    AuthorizedCommand,
    CommandConstructionStatus,
    InvalidationReason,
    binding_for_verbatim_command,
    binding_supersedes,
    construct_command_from_template,
    enforce_verified_command_targets,
    invalidation_reasons,
    target_correction_invalidates,
    verify_command_targets,
)
from backend.agents.team_manager.governed_operation_resolution import (
    resolve_governed_operation_descriptor,
)
from backend.agents.team_manager.identifier_verification import (
    IdentifierVerificationStatus,
    extract_identifier_mentions,
    verify_identifier_against_text,
)
from backend.agents.team_manager.request_contract import (
    REQUEST_CONTRACT_TOOL_NAME,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    RequestContract,
    RequestedOutput,
    RequestIntent,
    RequestParameter,
    required_target_parameter_gaps,
    validate_and_persist_request_contract,
)
from backend.agents.team_manager.response_plan import (
    ResponseBlockKind,
    build_response_plan,
    render_response_plan,
)
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.domain.operation_descriptor import (
    ApprovedCommandTemplate,
    GovernedOperationDescriptor,
    OperationDescriptorAuthority,
    OperationEffect,
    OperationParameterDefinition,
    OperationParameterKind,
    OperationTargetScope,
    from_model_extraction,
    required_parameter_names_for_scope,
)
from backend.tests._governed_operation_fixtures import approved_descriptor
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem
from backend.knowledge.domain.contracts import KnowledgeEvidenceReference

RESTART_TEMPLATE = "accn FieldReplaceableUnit={unit_id} restartunit 1 1 1"
SOURCE_EXAMPLE_COMMAND = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"


def _param(name: str, value: str, **extra) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance="user", **extra)


def _restart_descriptor(*, approved: bool = True) -> GovernedOperationDescriptor:
    descriptor = GovernedOperationDescriptor(
        operation_id="restart-rru",
        target_scope=OperationTargetScope.SINGLE_TARGET,
        effect=OperationEffect.STATE_CHANGE,
        parameters=(
            OperationParameterDefinition(
                name="unit_id", kind=OperationParameterKind.TARGET_IDENTIFIER, identifier_class="RRU"
            ),
        ),
        command_templates=(
            ApprovedCommandTemplate(template_id="restart", template=RESTART_TEMPLATE, effect=OperationEffect.STATE_CHANGE),
        ),
    )
    if approved:
        return approved_descriptor(descriptor)
    return descriptor.bind_to_source(knowledge_id="k1", version_label="v1", section_id="s1")


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.parts = [type("P", (), {"text": text})()]


class _FakeToolContext:
    def __init__(self, text: str, state: dict | None = None) -> None:
        self.user_content = _FakeContent(text)
        self.state: dict = state if state is not None else {}


def _record(text: str, **contract_fields) -> RequestContract | None:
    ctx = _FakeToolContext(text)
    payload = RequestContract(**contract_fields).model_dump(mode="json")
    validate_and_persist_request_contract(_FakeTool(REQUEST_CONTRACT_TOOL_NAME), {}, ctx, payload)
    raw = ctx.state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY)
    return RequestContract.model_validate(raw) if raw is not None else None


# ===========================================================================
# REPAIR 1 -- prefix collision, negation/exclusion, quoted example, conflict
# ===========================================================================


def test_rru_9_does_not_match_rru_90() -> None:
    """The headline prefix-collision failure: a raw substring test made
    `RRU-9` verify against a user who said `RRU-90`."""
    assert "RRU-9" in "restart RRU-90"  # the old test, for the record
    verification = verify_identifier_against_text("RRU-9", "restart RRU-90 now")
    assert verification.status is not IdentifierVerificationStatus.VERIFIED
    assert verification.value != "RRU-9" or verification.status is IdentifierVerificationStatus.CONTESTED


def test_prefix_collision_never_produces_a_verified_parameter() -> None:
    contract = _record(
        "restart RRU-90 now",
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("unit_id", "RRU-9")],
    )
    assert contract is not None
    assert [p.value for p in contract.provided_context if p.name == "unit_id"] == []
    assert "unit_id" in contract.missing_context


@pytest.mark.parametrize(
    "text",
    [
        "the unit is not RRU-9",
        "any RRU except RRU-9",
        "restart the RRU, rather than RRU-9",
        "for example RRU-9",
        "the document says RRU-9",
    ],
)
def test_negated_excluded_and_example_identifiers_are_never_verified(text: str) -> None:
    verification = verify_identifier_against_text("RRU-9", text)
    assert verification.status is IdentifierVerificationStatus.EXCLUDED_BY_TEXT
    assert verification.requires_explicit_confirmation is True


def test_quoted_source_example_is_not_a_live_target() -> None:
    mentions = extract_identifier_mentions('the procedure shows "RRU-9" as the unit')
    assert [m.value for m in mentions] == ["RRU-9"]
    assert mentions[0].status.value == "quoted_example"


def test_a_negated_identifier_forces_explicit_confirmation_not_a_silent_drop() -> None:
    contract = _record(
        "restart the RRU, it is not RRU-9",
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("unit_id", "RRU-9")],
        missing_context=[],
    )
    assert contract is not None
    assert all(p.name != "unit_id" for p in contract.provided_context)
    assert "unit_id" in contract.missing_context


def test_conflicting_identifiers_are_contested_and_nothing_is_verified() -> None:
    verification = verify_identifier_against_text("RRU-3", "compare RRU-3 and RRU-5")
    assert verification.status is IdentifierVerificationStatus.CONTESTED
    assert verification.competing_values == ("RRU-3", "RRU-5")


def test_a_genuine_single_mention_still_verifies_with_its_source_span() -> None:
    contract = _record(
        "the unit is rru 5",
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("unit_id", "RRU-5")],
    )
    assert contract is not None
    (unit_id,) = [p for p in contract.provided_context if p.name == "unit_id"]
    assert unit_id.value == "RRU-5"
    assert unit_id.source_span is not None
    assert "the unit is rru 5"[unit_id.source_span.start : unit_id.source_span.end].lower() == "rru 5"


def test_correction_status_is_recorded_when_a_target_changes() -> None:
    prior = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("unit_id", "RRU-3")],
    )
    ctx = _FakeToolContext(
        "actually it is RRU-10",
        state={VALIDATED_REQUEST_CONTRACT_STATE_KEY: prior.model_dump(mode="json")},
    )
    payload = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        continuation=True,
        provided_context=[_param("unit_id", "RRU-10")],
    ).model_dump(mode="json")
    validate_and_persist_request_contract(_FakeTool(REQUEST_CONTRACT_TOOL_NAME), {}, ctx, payload)
    contract = RequestContract.model_validate(ctx.state[VALIDATED_REQUEST_CONTRACT_STATE_KEY])
    (unit_id,) = [p for p in contract.provided_context if p.name == "unit_id"]
    assert unit_id.value == "RRU-10"
    assert unit_id.corrects_prior_value == "RRU-3"
    assert target_correction_invalidates(contract.provided_context) is True


# ===========================================================================
# REPAIR 2 -- no target independence from token absence
# ===========================================================================


def test_token_absence_alone_no_longer_grants_target_independence() -> None:
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND,
        RequestedOutput.EXACT_COMMAND,
        [_param("vendor", "ericsson")],
        grounded_command_candidate="alt",  # contains no RRU/AAS token
    )
    assert gaps == ["unit_id", "unit_type"]


def test_unknown_scope_stays_unresolved() -> None:
    descriptor = approved_descriptor(
        GovernedOperationDescriptor(operation_id="mystery", target_scope=OperationTargetScope.UNKNOWN)
    )
    assert required_parameter_names_for_scope(descriptor) is None
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND, [], operation_descriptor=descriptor
    )
    assert gaps == ["unit_id", "unit_type"]


def test_positive_governed_metadata_is_what_grants_target_independence() -> None:
    descriptor = approved_descriptor(
        GovernedOperationDescriptor(
            operation_id="list-alarms", target_scope=OperationTargetScope.TARGET_INDEPENDENT
        )
    )
    gaps = required_target_parameter_gaps(
        RequestIntent.COMMAND, RequestedOutput.EXACT_COMMAND, [], operation_descriptor=descriptor
    )
    assert gaps == []


# ===========================================================================
# REPAIR 3 -- descriptor authority is never auto-promoted
# ===========================================================================


def test_model_extracted_metadata_is_always_candidate() -> None:
    extracted = from_model_extraction(
        GovernedOperationDescriptor(
            operation_id="restart-rru",
            authority=OperationDescriptorAuthority.APPROVED,  # a lie, from the extraction
            target_scope=OperationTargetScope.TARGET_INDEPENDENT,
        )
    )
    assert extracted.authority is OperationDescriptorAuthority.CANDIDATE
    assert required_parameter_names_for_scope(extracted) is None


def test_a_caller_supplied_boolean_can_no_longer_confer_authority() -> None:
    """POST-6A PROMPT 3: the former `approve_descriptor(...,
    governance_transition_approved=True)` is gone -- approval must go
    through the governance seam, bound to version and content."""
    import backend.knowledge.domain.operation_descriptor as module

    assert not hasattr(module, "approve_descriptor")


def test_approval_is_bound_to_version_and_descriptor_content() -> None:
    from backend.knowledge.governance.operation_approval import (
        OperationApprovalError,
        OperationApprovalRecord,
        approve_section_operation,
        author_section_operation,
        descriptor_fingerprint,
    )
    from backend.knowledge.governance.service import approve_version
    from backend.tests._governed_operation_fixtures import build_knowledge_object

    descriptor = GovernedOperationDescriptor(
        operation_id="restart-rru", target_scope=OperationTargetScope.SINGLE_TARGET
    )
    obj = approve_version(author_section_operation(build_knowledge_object(), "s1", descriptor))
    authored = obj.sections[0].operation
    assert authored is not None
    assert authored.authority is OperationDescriptorAuthority.CANDIDATE

    good = OperationApprovalRecord(
        knowledge_id="k1",
        version_label="v1",
        section_id="s1",
        descriptor_fingerprint=descriptor_fingerprint(authored),
        approved_by="reviewer",
    )
    assert approve_section_operation(obj, good).sections[0].operation.authority is (
        OperationDescriptorAuthority.APPROVED
    )

    # Wrong version -- an approval never carries across versions.
    with pytest.raises(OperationApprovalError):
        approve_section_operation(obj, good.model_copy(update={"version_label": "v2"}))

    # Content changed since approval -- the fingerprint no longer matches.
    edited = author_section_operation(
        obj, "s1", descriptor.model_copy(update={"target_scope": OperationTargetScope.TARGET_INDEPENDENT})
    )
    with pytest.raises(OperationApprovalError):
        approve_section_operation(edited, good)


def test_an_operation_cannot_be_approved_on_unapproved_knowledge() -> None:
    from backend.knowledge.governance.operation_approval import (
        OperationApprovalError,
        OperationApprovalRecord,
        approve_section_operation,
        author_section_operation,
        descriptor_fingerprint,
    )
    from backend.tests._governed_operation_fixtures import build_knowledge_object

    obj = author_section_operation(
        build_knowledge_object(), "s1", GovernedOperationDescriptor(operation_id="restart-rru")
    )
    authored = obj.sections[0].operation
    assert authored is not None
    with pytest.raises(OperationApprovalError):
        approve_section_operation(
            obj,
            OperationApprovalRecord(
                knowledge_id="k1",
                version_label="v1",
                section_id="s1",
                descriptor_fingerprint=descriptor_fingerprint(authored),
                approved_by="reviewer",
            ),
        )


def test_materialized_corpus_is_never_auto_approved() -> None:
    """Re-materializing a governed document attaches no descriptor at
    all, so nothing in the existing corpus gains authority implicitly."""
    import inspect

    from backend.knowledge.governance import service as governance_service

    assert "operation=None," in inspect.getsource(governance_service.materialize_candidate)


def test_descriptor_on_non_approved_knowledge_is_ignored() -> None:
    descriptor = _restart_descriptor()
    section = KnowledgeSection(
        section_id="s1", knowledge_id="k1", sequence=0, content="restart procedure", operation=descriptor
    )
    item = KnowledgeEvidenceItem(
        reference=KnowledgeEvidenceReference(
            knowledge_id="k1", version_label="v1", section_id="s1", source_system="local", source_id="x"
        ),
        title="Restart",
        document_type=KnowledgeDocumentType.OPERATIONAL_PROCEDURE,
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=KnowledgeSource(source_system="local", source_id="x"),
        section=section,
    )
    assert (
        resolve_governed_operation_descriptor(
            [item], knowledge_id="k1", version_label="v1", section_id="s1"
        )
        is None
    )

    approved_item = item.model_copy(update={"lifecycle_status": LifecycleStatus.APPROVED})
    resolved = resolve_governed_operation_descriptor(
        [approved_item], knowledge_id="k1", version_label="v1", section_id="s1"
    )
    assert resolved is not None
    assert resolved.section_id == "s1"


# ===========================================================================
# REPAIR 4 -- wrong target must never be authorized
# ===========================================================================


def test_source_example_for_rru_9_is_never_authorized_for_user_target_rru_3() -> None:
    ok, unverified = verify_command_targets(SOURCE_EXAMPLE_COMMAND, [_param("unit_id", "RRU-3")])
    assert ok is False
    assert unverified == ("RRU-9",)


def test_verbatim_grounded_command_with_the_wrong_target_is_stripped_not_rewritten() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        next_action="Restart the unit.",
        command=SOURCE_EXAMPLE_COMMAND,
    )
    corrected, stripped, unverified = enforce_verified_command_targets(guidance, [_param("unit_id", "RRU-3")])
    assert stripped is True
    assert corrected.command is None
    assert unverified == ("RRU-9",)
    # Never rewritten into the user's target.
    assert "RRU-3" not in (corrected.command or "")


def test_template_construction_produces_the_user_target_without_string_replacement() -> None:
    result = construct_command_from_template(_restart_descriptor(), "restart", [_param("unit_id", "RRU-3")])
    assert result.status is CommandConstructionStatus.CONSTRUCTED
    assert result.command == "accn FieldReplaceableUnit=RRU-3 restartunit 1 1 1"
    assert result.binding is not None
    assert result.binding.target_parameters == (("unit_id", "RRU-3"),)


def test_template_construction_fails_closed_without_a_verified_parameter() -> None:
    result = construct_command_from_template(_restart_descriptor(), "restart", [])
    assert result.status is CommandConstructionStatus.MISSING_PARAMETER
    assert result.command is None


def test_candidate_descriptor_cannot_construct_a_command() -> None:
    result = construct_command_from_template(
        _restart_descriptor(approved=False), "restart", [_param("unit_id", "RRU-3")]
    )
    assert result.status is CommandConstructionStatus.NO_APPROVED_TEMPLATE


def test_a_template_that_hardcodes_an_identifier_is_rejected_by_the_target_check() -> None:
    bad = _restart_descriptor().model_copy(
        update={
            "command_templates": (
                ApprovedCommandTemplate(template_id="bad", template=SOURCE_EXAMPLE_COMMAND),
            )
        }
    )
    result = construct_command_from_template(bad, "bad", [_param("unit_id", "RRU-3")])
    assert result.status is CommandConstructionStatus.UNVERIFIED_TARGET_IN_COMMAND


def test_step_commands_are_target_checked_too() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[
            TroubleshootingStep(action="Check status", command="lt all"),
            TroubleshootingStep(action="Restart", command=SOURCE_EXAMPLE_COMMAND),
        ],
    )
    corrected, stripped, unverified = enforce_verified_command_targets(guidance, [_param("unit_id", "RRU-3")])
    assert stripped is True
    assert corrected.full_procedure_steps[0].command == "lt all"
    assert corrected.full_procedure_steps[1].command is None
    assert unverified == ("RRU-9",)


# ===========================================================================
# REPAIR 5 -- invalidation on change
# ===========================================================================


def test_target_change_invalidates_the_prior_candidate() -> None:
    descriptor = _restart_descriptor()
    first = construct_command_from_template(descriptor, "restart", [_param("unit_id", "RRU-3")]).binding
    second = construct_command_from_template(descriptor, "restart", [_param("unit_id", "RRU-10")]).binding
    assert binding_supersedes(first, second) is True
    assert InvalidationReason.TARGET_CHANGED in invalidation_reasons(first, second)
    assert InvalidationReason.PAYLOAD_CHANGED in invalidation_reasons(first, second)


def test_procedure_version_change_invalidates_the_prior_candidate() -> None:
    descriptor = _restart_descriptor()
    first = construct_command_from_template(descriptor, "restart", [_param("unit_id", "RRU-3")]).binding
    v2 = descriptor.bind_to_source(knowledge_id="k1", version_label="v2", section_id="s1")
    second = construct_command_from_template(v2, "restart", [_param("unit_id", "RRU-3")]).binding
    assert binding_supersedes(first, second) is True
    assert InvalidationReason.PROCEDURE_VERSION_CHANGED in invalidation_reasons(first, second)


def test_an_unchanged_binding_is_not_invalidated() -> None:
    descriptor = _restart_descriptor()
    first = construct_command_from_template(descriptor, "restart", [_param("unit_id", "RRU-3")]).binding
    second = construct_command_from_template(descriptor, "restart", [_param("unit_id", "RRU-3")]).binding
    assert binding_supersedes(first, second) is False
    assert invalidation_reasons(first, second) == ()


def test_a_verbatim_command_gets_an_equivalent_binding() -> None:
    binding = binding_for_verbatim_command(None, "lt all", [_param("unit_id", "RRU-3")])
    assert binding.payload == "lt all"
    assert binding.target_parameters == (("unit_id", "RRU-3"),)


# ===========================================================================
# REPAIR 6 -- typed plan, and free text is not a parallel route
# ===========================================================================


def test_command_text_is_rendered_only_from_a_validated_object() -> None:
    command = "accn FieldReplaceableUnit=RRU-3 restartunit 1 1 1"
    authorized = AuthorizedCommand(
        command=command,
        binding=binding_for_verbatim_command(None, command, [_param("unit_id", "RRU-3")]),
        grounding="template",
    )
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="The unit is faulty.",
        next_action="Restart it.",
        command=command,
        evidence_requested="Send the output.",
    )
    plan = build_response_plan(guidance, may_emit_command=True, authorized_commands=[authorized])
    kinds = [block.kind for block in plan.blocks]
    assert ResponseBlockKind.COMMAND in kinds
    assert command in render_response_plan(plan)


def test_an_unlisted_command_is_never_rendered_even_when_policy_permits_commands() -> None:
    """`may_emit_command=True` is not enough: the value must also be one
    of the validated objects the plan was given."""
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        next_action="Restart it.",
        command=SOURCE_EXAMPLE_COMMAND,
    )
    plan = build_response_plan(guidance, may_emit_command=True, authorized_commands=[])
    assert SOURCE_EXAMPLE_COMMAND not in render_response_plan(plan)
    assert all(block.kind is not ResponseBlockKind.COMMAND for block in plan.blocks)


def test_free_text_restating_an_unauthorized_command_is_dropped() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="The unit is faulty.",
        next_action=f"Run {SOURCE_EXAMPLE_COMMAND} to restart it.",
        evidence_requested="Send the output.",
    )
    plan = build_response_plan(
        guidance,
        may_emit_command=False,
        unauthorized_command_values=frozenset({SOURCE_EXAMPLE_COMMAND}),
    )
    rendered = render_response_plan(plan)
    assert SOURCE_EXAMPLE_COMMAND not in rendered
    assert plan.dropped_narrative_blocks == 1
    assert "The unit is faulty." in rendered


def test_free_text_in_a_step_action_is_dropped_too() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.FULL_PROCEDURE,
        full_procedure_steps=[
            TroubleshootingStep(action=f"Just run {SOURCE_EXAMPLE_COMMAND}"),
            TroubleshootingStep(action="Check the LEDs"),
        ],
    )
    plan = build_response_plan(
        guidance,
        may_emit_command=False,
        unauthorized_command_values=frozenset({SOURCE_EXAMPLE_COMMAND}),
    )
    rendered = render_response_plan(plan)
    assert SOURCE_EXAMPLE_COMMAND not in rendered
    assert "Check the LEDs" in rendered
    assert plan.dropped_narrative_blocks == 1


def test_a_command_block_cannot_be_built_without_a_validated_object() -> None:
    from backend.agents.team_manager.response_plan import ResponseBlock

    with pytest.raises(ValueError):
        ResponseBlock(kind=ResponseBlockKind.COMMAND, text=SOURCE_EXAMPLE_COMMAND)


def test_rendering_is_deterministic() -> None:
    guidance = TroubleshootingGuidance(
        interaction_mode=TroubleshootingInteractionMode.NEXT_STEP,
        interpretation="A",
        next_action="B",
        evidence_requested="C",
    )
    plan = build_response_plan(guidance, may_emit_command=False)
    assert render_response_plan(plan) == render_response_plan(plan) == "A\n\nB\n\nC"


# ===========================================================================
# REPAIR 7 -- an invalid contract must not pass operational prose
# ===========================================================================


def test_invalid_contract_operational_withholding_is_structural_not_textual() -> None:
    """The discriminator chat_service uses is the presence of structured
    operational artefacts, never an inspection of the prose."""
    import inspect

    from backend.api import chat_service

    source = inspect.getsource(chat_service)
    assert "operational_turn_without_contract = bool(" in source
    assert "captured_troubleshooting_guidance is not None" in source
    assert "unresolved_contract_operational_withheld" in source
    # And ordinary conversation still passes through unchanged.
    assert 'final_response_path = "unresolved_contract"' in source
