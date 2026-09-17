"""POST-6A REPAIRS 1-5 -- focused coverage for exactly the boundaries
these repairs changed. Deliberately narrow: no live integration, no model
call, no full-stack turn -- each test exercises one deterministic seam.

  REPAIR 1 -- authoritative temporary context reaches the running agent
              through `Runner.run_async(state_delta=...)`, and is never
              durably retained.
  REPAIR 2 -- the bounded canonical context is assembled, rendered, and
              seeds the validation shim's prior-contract state.
  REPAIR 3 -- one effective current request, used consistently.
  REPAIR 4 -- clarification answers, corrections, cancellation, new
              requests, and the unresolved-relationship clarification.
  REPAIR 5 -- each operational instruction matches its variant's real
              toolset.
"""
from __future__ import annotations

from typing import Any

import pytest

from backend.agents.team_manager.authoritative_request_context import (
    AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY,
)
from backend.agents.team_manager.request_contract import (
    PENDING_GOVERNED_REQUEST_STATE_KEY,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    PendingGovernedRequest,
    PendingGovernedRequestStatus,
    PendingRequestRelationship,
    RequestClass,
    RequestContract,
    RequestedOutput,
    RequestIntent,
    RequestParameter,
    derive_request_class,
)
from backend.agents.team_manager.request_execution_policy import (
    PendingContinuationRelation,
    RequestExecutionStatus,
    build_pending_governed_request_state_update,
    command_suppression_fallback_text,
    derive_execution_decision,
    is_governed_evidence_continuity_permitted,
    resolve_effective_governed_contract,
    resolve_pending_continuation,
)
from backend.agents.team_manager.request_extraction_context import (
    CanonicalTurnBrief,
    RequestExtractionContext,
    build_request_extraction_context,
    prior_validated_contract_state,
    render_request_extraction_context,
)

_RUN_ID = "run-post6a"


def _param(name: str, value: str) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance="user")


def _classified(contract: RequestContract) -> RequestContract:
    """Stamps `request_class`/`run_id` exactly as `validate_and_persist_
    request_contract` would, so a directly-constructed contract behaves
    identically to a really-persisted one."""
    return contract.model_copy(
        update={
            "request_class": derive_request_class(
                contract.intent, contract.requested_output, contract.action_requested, contract.subject
            ),
            "run_id": _RUN_ID,
        }
    )


def _pending_restart_rru(
    *,
    status: str = PendingGovernedRequestStatus.UNRESOLVED,
    missing: list[str] | None = None,
    provided: list[RequestParameter] | None = None,
) -> PendingGovernedRequest:
    return PendingGovernedRequest(
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        intent=RequestIntent.COMMAND,
        status=status,
        subject="restart RRU",
        missing_context=["unit_id"] if missing is None else missing,
        provided_context=[_param("unit_type", "RRU")] if provided is None else provided,
    )


# =============================================================================
# REPAIR 1 -- authoritative temporary context is invocation state
# =============================================================================


def test_chat_service_never_persists_the_authoritative_context_key_before_the_runner() -> None:
    """The ONE structural guarantee this repair rests on: the
    authoritative `temp:` block must never again be written through a
    `persist_state_delta` call that the Runner's own session reload would
    discard. It must reach the turn through the Runner's own
    `state_delta` parameter instead."""
    import inspect

    from backend.api import chat_service

    source = inspect.getsource(chat_service)
    # The block is no longer written through any `persist_state_delta`
    # call -- it is assigned into the invocation delta instead, and that
    # delta is what the one `turn_runner.run_async(` call site forwards.
    assert "session, {AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY: authoritative_context_block}" not in source
    assert "turn_invocation_state_delta[AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY]" in source
    assert "state_delta=turn_invocation_state_delta or None," in source
    assert source.count("turn_runner.run_async(") == 1


def test_runner_protocol_accepts_state_delta() -> None:
    """The structural shape this module requires of a Runner (or a test
    double) now includes ADK's own `state_delta` parameter."""
    import inspect

    from backend.api.chat_service import _Runner

    assert "state_delta" in inspect.signature(_Runner.run_async).parameters


@pytest.mark.asyncio
async def test_temp_state_delta_reaches_the_agent_and_is_not_persisted() -> None:
    """End-to-end against the REAL ADK mechanism (never a fake): a
    `temp:`-prefixed key supplied via `Runner.run_async(state_delta=...)`
    is live in the session the agent runs against, and is stripped from
    what is durably stored -- the exact property REPAIR 1 depends on.

    Uses ADK's own `InMemorySessionService`/`append_event` path, which is
    the SAME "apply, then trim" code `DatabaseSessionService` uses (both
    inherit `BaseSessionService._apply_temp_state`).
    """
    from google.adk.events import Event, EventActions
    from google.adk.sessions import InMemorySessionService

    service = InMemorySessionService()
    session = await service.create_session(app_name="t", user_id="u", session_id="s")
    await service.append_event(
        session,
        Event(
            author="user",
            invocation_id="inv-1",
            actions=EventActions(state_delta={AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY: "BLOCK", "durable": "yes"}),
        ),
    )

    # Live, in-memory (what the instruction provider reads this turn):
    assert session.state[AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY] == "BLOCK"
    # Durable (what a later turn would reload):
    reloaded = await service.get_session(app_name="t", user_id="u", session_id="s")
    assert reloaded is not None
    assert AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY not in reloaded.state
    assert reloaded.state["durable"] == "yes"


# =============================================================================
# REPAIR 2 -- bounded canonical context
# =============================================================================


class _FakePart:
    def __init__(self, text: str) -> None:
        self.text = text
        self.thought = None


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.role = "user"
        self.parts = [_FakePart(text)]


class _FakeUserEvent:
    def __init__(self, invocation_id: str, text: str) -> None:
        self.invocation_id = invocation_id
        self.author = "user"
        self.content = _FakeContent(text)
        self.actions = None
        self.partial = None


def test_extraction_context_carries_pending_prior_contract_and_canonical_turns() -> None:
    pending = _pending_restart_rru()
    prior = _classified(
        RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            provided_context=[_param("unit_type", "RRU")],
        )
    )
    state: dict[str, Any] = {
        PENDING_GOVERNED_REQUEST_STATE_KEY: pending.model_dump(mode="json"),
        VALIDATED_REQUEST_CONTRACT_STATE_KEY: prior.model_dump(mode="json"),
        "turn_source_references": {
            "inv-1": {"schema_version": "1.0", "final_text": "Which RRU should I restart?"}
        },
    }
    events = [_FakeUserEvent("inv-1", "give me the command to restart an RRU")]

    context = build_request_extraction_context(state, events)
    assert context.pending_request is not None
    assert context.pending_request.subject == "restart RRU"
    assert context.prior_contract is not None
    assert [t.user_text for t in context.recent_turns] == ["give me the command to restart an RRU"]
    assert context.recent_turns[0].assistant_text == "Which RRU should I restart?"

    rendered = render_request_extraction_context(context)
    assert "OUTSTANDING REQUEST" in rendered
    assert "restart RRU" in rendered
    assert "still waiting on: unit_id" in rendered
    assert "already confirmed by the user: unit_type=RRU" in rendered
    assert "RECENT TURNS" in rendered


def test_extraction_context_is_bounded_and_renders_nothing_when_empty() -> None:
    assert render_request_extraction_context(None) == ""
    assert render_request_extraction_context(build_request_extraction_context({})) == ""

    events = [_FakeUserEvent(f"inv-{i}", f"message {i}") for i in range(10)]
    context = build_request_extraction_context({}, events)
    assert len(context.recent_turns) == 3
    # Oldest-first, and only the MOST RECENT window.
    assert [t.user_text for t in context.recent_turns] == ["message 7", "message 8", "message 9"]


def test_extraction_context_never_invents_assistant_text_without_a_canonical_result() -> None:
    context = build_request_extraction_context({}, [_FakeUserEvent("inv-1", "hello")])
    assert context.recent_turns[0].assistant_text is None
    assert "assistant:" not in render_request_extraction_context(context)


def test_prior_contract_seeds_only_the_one_validation_key() -> None:
    """The validation shim's state must be seeded with the prior contract
    (restoring same-subject carry-forward) and with NOTHING else."""
    prior = _classified(
        RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            provided_context=[_param("unit_type", "RRU")],
        )
    )
    context = RequestExtractionContext(prior_contract=prior, recent_turns=[CanonicalTurnBrief(user_text="x")])
    seed = prior_validated_contract_state(context)
    assert list(seed) == [VALIDATED_REQUEST_CONTRACT_STATE_KEY]
    assert prior_validated_contract_state(None) == {}
    assert prior_validated_contract_state(RequestExtractionContext()) == {}


def test_seeded_shim_restores_same_subject_carry_forward() -> None:
    """The real, unmodified `validate_and_persist_request_contract`
    against the real shim: a parameter confirmed on an earlier turn of
    the SAME subject survives into this turn's contract when -- and only
    when -- the prior contract is seeded."""
    from google.genai import types

    from backend.agents.team_manager.request_contract import (
        REQUEST_CONTRACT_TOOL_NAME,
        validate_and_persist_request_contract,
    )
    from backend.agents.team_manager.request_contract_completion import _CapturedToolContext, _NamedTool

    prior = _classified(
        RequestContract(
            intent=RequestIntent.COMMAND,
            requested_output=RequestedOutput.EXACT_COMMAND,
            subject="restart RRU",
            provided_context=[_param("unit_type", "RRU")],
        )
    )
    user_content = types.Content(role="user", parts=[types.Part.from_text(text="RRU-3")])
    response = RequestContract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        continuation=True,
        provided_context=[_param("unit_id", "RRU-3")],
    ).model_dump(mode="json")

    seeded = _CapturedToolContext(user_content, seed_state=prior_validated_contract_state(RequestExtractionContext(prior_contract=prior)))
    validate_and_persist_request_contract(_NamedTool(REQUEST_CONTRACT_TOOL_NAME), {}, seeded, dict(response))
    carried = RequestContract.model_validate(seeded.state[VALIDATED_REQUEST_CONTRACT_STATE_KEY])
    assert {p.name for p in carried.provided_context} == {"unit_type", "unit_id"}

    # Without the seed (the pre-repair behavior) the earlier-confirmed
    # `unit_type` is silently lost.
    unseeded = _CapturedToolContext(user_content)
    validate_and_persist_request_contract(_NamedTool(REQUEST_CONTRACT_TOOL_NAME), {}, unseeded, dict(response))
    lost = RequestContract.model_validate(unseeded.state[VALIDATED_REQUEST_CONTRACT_STATE_KEY])
    assert {p.name for p in lost.provided_context} == {"unit_id"}


def test_context_block_is_labelled_and_the_user_message_is_last() -> None:
    """A value appearing only in the context block must be presented as
    background, with the real request clearly delimited after it."""
    import inspect

    from backend.agents.team_manager import request_contract_completion as rcc

    source = inspect.getsource(rcc)
    assert "USER MESSAGE TO CLASSIFY" in source
    assert "background, never a statement of verified fact" in render_request_extraction_context(
        RequestExtractionContext(recent_turns=[CanonicalTurnBrief(user_text="hi")])
    )


# =============================================================================
# REPAIR 3 -- one effective current request
# =============================================================================


def test_effective_request_is_resolved_once_and_agrees_across_consumers() -> None:
    """`resolve_pending_continuation` is pure, so the value `chat_service
    .py` computes for retrieval/prompt/continuity and the value
    `derive_execution_decision` computes internally are the same object
    value -- there is no second, drifting derivation."""
    pending = _pending_restart_rru()
    raw = _classified(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            provided_context=[_param("unit_id", "RRU-3")],
        )
    )

    resolution = resolve_pending_continuation(raw, pending)
    assert resolution.contract == resolve_effective_governed_contract(raw, pending)
    assert resolution.contract == resolve_pending_continuation(raw, pending).contract

    decision = derive_execution_decision(raw, _RUN_ID, pending_governed_request=pending)
    assert decision.request_class == RequestClass.EXACT_COMMAND
    assert decision.subject == "restart RRU"
    assert decision.pending_relationship == PendingContinuationRelation.ANSWERS


def test_raw_extraction_stays_distinct_from_effective_authority() -> None:
    pending = _pending_restart_rru()
    raw = _classified(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            provided_context=[_param("unit_id", "RRU-3")],
        )
    )
    effective = resolve_pending_continuation(raw, pending).contract

    # The raw proposal -- the value that is persisted -- is untouched.
    assert raw.request_class == RequestClass.GENERAL_CONVERSATION
    assert raw.subject is None
    assert [p.name for p in raw.provided_context] == ["unit_id"]
    # The effective authority carries the real operation.
    assert effective.request_class == RequestClass.EXACT_COMMAND
    assert effective.subject == "restart RRU"


def test_chat_service_uses_the_effective_request_for_retrieval_and_prompt() -> None:
    import inspect

    from backend.api import chat_service

    source = inspect.getsource(chat_service)
    assert "preflight_request_contract_subject = (\n                            preflight_effective_contract.subject" in source
    assert "preflight_effective_contract,\n                    work_envelope," in source
    assert "effective_request_contract.subject" in source
    assert "build_pending_governed_request_state_update(execution_decision, effective_request_contract)" in source


# =============================================================================
# REPAIR 4 -- clarification, correction, cancellation, new request
# =============================================================================


def test_value_only_reply_keeps_the_pending_objective_despite_general_conversation() -> None:
    """The headline repair: a bare value answer classifies GENERAL_
    CONVERSATION, which previously dropped the pending objective."""
    pending = _pending_restart_rru()
    contract = _classified(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            provided_context=[_param("unit_id", "RRU-3")],
        )
    )
    assert contract.request_class == RequestClass.GENERAL_CONVERSATION

    resolution = resolve_pending_continuation(contract, pending)
    assert resolution.relation == PendingContinuationRelation.ANSWERS
    assert resolution.contract.subject == "restart RRU"
    assert {p.name: p.value for p in resolution.contract.provided_context} == {
        "unit_type": "RRU",
        "unit_id": "RRU-3",
    }


def test_correction_reopens_a_just_completed_request() -> None:
    pending = _pending_restart_rru(
        status=PendingGovernedRequestStatus.COMPLETED,
        missing=[],
        provided=[_param("unit_type", "RRU"), _param("unit_id", "RRU-3")],
    )
    contract = _classified(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            provided_context=[_param("unit_id", "RRU-10")],
        )
    )
    resolution = resolve_pending_continuation(contract, pending)
    assert resolution.relation == PendingContinuationRelation.ANSWERS
    assert {p.name: p.value for p in resolution.contract.provided_context}["unit_id"] == "RRU-10"


def test_explicit_cancellation_drops_the_pending_request() -> None:
    pending = _pending_restart_rru()
    contract = _classified(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            provided_context=[_param("unit_id", "RRU-3")],
        )
    ).model_copy(update={"pending_request_relationship": PendingRequestRelationship.CANCELS_PENDING})

    resolution = resolve_pending_continuation(contract, pending)
    assert resolution.relation == PendingContinuationRelation.CANCELLED
    # Nothing inherited...
    assert resolution.contract.request_class == RequestClass.GENERAL_CONVERSATION

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)
    assert decision.pending_relationship == PendingContinuationRelation.CANCELLED
    assert decision.may_emit_command is False
    # ...and the pending record is cleared by this turn's continuity write.
    update = build_pending_governed_request_state_update(decision, resolution.contract)
    assert update == {PENDING_GOVERNED_REQUEST_STATE_KEY: None}
    # Prior governed evidence must not be inherited either.
    assert is_governed_evidence_continuity_permitted(contract, _RUN_ID, pending) is False


def test_self_contained_new_question_is_not_resumed_and_is_not_questioned() -> None:
    """An independent question that happens to mention a same-named
    parameter resolves NEW -- never an inherited command, and never an
    unnecessary interruption."""
    pending = _pending_restart_rru()
    contract = _classified(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            subject="RRU status",
            requires_operational_context=True,
            provided_context=[_param("unit_id", "RRU-9")],
        )
    )
    resolution = resolve_pending_continuation(contract, pending)
    assert resolution.relation == PendingContinuationRelation.NEW
    assert resolution.contract is contract or resolution.contract == contract

    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)
    assert decision.request_class == RequestClass.OPERATIONAL_INFORMATION
    assert decision.may_emit_command is False


def test_declared_new_request_overrides_an_otherwise_unresolved_relationship() -> None:
    pending = _pending_restart_rru()
    ambiguous = _classified(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            requires_governed_knowledge=True,
            provided_context=[_param("unit_id", "RRU-9")],
        )
    )
    assert resolve_pending_continuation(ambiguous, pending).relation == PendingContinuationRelation.UNRESOLVED

    declared = ambiguous.model_copy(update={"pending_request_relationship": PendingRequestRelationship.NEW_REQUEST})
    assert resolve_pending_continuation(declared, pending).relation == PendingContinuationRelation.NEW


def test_unresolved_relationship_asks_and_keeps_the_pending_request_alive() -> None:
    pending = _pending_restart_rru()
    contract = _classified(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            requires_governed_knowledge=True,
            provided_context=[_param("unit_id", "RRU-9")],
        )
    )
    decision = derive_execution_decision(contract, _RUN_ID, pending_governed_request=pending)

    assert decision.status == RequestExecutionStatus.AMBIGUOUS
    assert decision.pending_relationship == PendingContinuationRelation.UNRESOLVED
    assert decision.may_emit_command is False

    text = command_suppression_fallback_text(decision)
    assert "restart RRU" in text
    assert "separate request" in text

    # The pending record survives, so the user's answer can resume it.
    effective = resolve_pending_continuation(contract, pending).contract
    update = build_pending_governed_request_state_update(decision, effective)
    preserved = PendingGovernedRequest.model_validate(update[PENDING_GOVERNED_REQUEST_STATE_KEY])
    assert preserved.status == PendingGovernedRequestStatus.UNRESOLVED
    assert preserved.subject == "restart RRU"
    assert preserved.missing_context == ["unit_id"]
    # The unrelated identifier is NOT adopted as an answer.
    assert [p.value for p in preserved.provided_context] == ["RRU"]


def test_a_declared_answer_alone_never_resumes_anything() -> None:
    """The model's declaration can narrow, never grant: a contract that
    claims to answer the pending request but supplies no verified
    parameter at all must not resume it."""
    pending = _pending_restart_rru()
    empty_claim = _classified(
        RequestContract(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT)
    ).model_copy(update={"pending_request_relationship": PendingRequestRelationship.ANSWERS_PENDING})

    resolution = resolve_pending_continuation(empty_claim, pending)
    assert resolution.relation == PendingContinuationRelation.NEW
    assert resolution.contract.request_class == RequestClass.GENERAL_CONVERSATION


def test_a_plain_greeting_is_never_promoted_into_command_governance() -> None:
    pending = _pending_restart_rru()
    greeting = _classified(RequestContract(intent=RequestIntent.INFORMATION, requested_output=RequestedOutput.FACT))
    decision = derive_execution_decision(greeting, _RUN_ID, pending_governed_request=pending)
    assert decision.request_class == RequestClass.GENERAL_CONVERSATION
    assert decision.pending_relationship == PendingContinuationRelation.NEW
    assert decision.may_emit_command is False


def test_unrelated_parameter_name_does_not_relate_at_all() -> None:
    pending = _pending_restart_rru()
    contract = _classified(
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            provided_context=[_param("vendor", "ericsson")],
        )
    )
    assert resolve_pending_continuation(contract, pending).relation == PendingContinuationRelation.NEW


def test_relationship_declaration_vocabulary_is_closed() -> None:
    with pytest.raises(Exception):
        RequestContract(
            intent=RequestIntent.INFORMATION,
            requested_output=RequestedOutput.FACT,
            pending_request_relationship="resume_everything",
        )


# =============================================================================
# REPAIR 5 -- instructions match toolsets
# =============================================================================


def test_team_manager_instruction_composition_is_byte_identical() -> None:
    """Splitting the instruction into sections must not have changed the
    base `team_manager`'s own prompt in any way."""
    from backend.agents.team_manager import prompts

    assert prompts.TEAM_MANAGER_INSTRUCTION == (
        prompts._TEAM_MANAGER_IDENTITY_SECTION
        + prompts._TEAM_MANAGER_TEAMS_CONTEXT_SECTION
        + prompts._TEAM_MANAGER_SOURCE_DECLARATION_SECTION
        + prompts._TEAM_MANAGER_REQUEST_CONTRACT_SECTION
        + prompts._TEAM_MANAGER_TEAMS_ORCHESTRATION_SECTION
        + prompts._TEAM_MANAGER_TROUBLESHOOTING_DELEGATION_SECTION
    )


@pytest.mark.parametrize(
    "instruction_name",
    [
        "OPERATIONAL_TEAM_MANAGER_INSTRUCTION",
        "OPERATIONAL_TEAM_MANAGER_INCIDENT_ONLY_INSTRUCTION",
        "OPERATIONAL_TEAM_MANAGER_TROUBLESHOOTING_ONLY_INSTRUCTION",
    ],
)
def test_operational_instructions_never_demand_the_removed_declaration_tools(instruction_name: str) -> None:
    from backend.agents.team_manager import prompts

    instruction = getattr(prompts, instruction_name)
    assert "record_source_requirements" not in instruction
    assert "record_request_contract" not in instruction


def test_incident_only_instruction_drops_troubleshooting_delegation() -> None:
    from backend.agents.team_manager import prompts

    assert "TROUBLESHOOTING DELEGATION" not in prompts.OPERATIONAL_TEAM_MANAGER_INCIDENT_ONLY_INSTRUCTION
    assert "TROUBLESHOOTING DELEGATION" in prompts.OPERATIONAL_TEAM_MANAGER_INSTRUCTION


def test_troubleshooting_only_instruction_drops_teams_orchestration() -> None:
    from backend.agents.team_manager import prompts

    instruction = prompts.OPERATIONAL_TEAM_MANAGER_TROUBLESHOOTING_ONLY_INSTRUCTION
    assert "CONVERSATION TARGET (semantic scope" not in instruction
    assert "TEAMS WRITE ACTIONS (createChat / sendMessage)" not in instruction
    assert "GOVERNED KNOWLEDGE DELEGATION" not in instruction
    assert "no Microsoft Teams capability at all" in instruction
    assert "TROUBLESHOOTING DELEGATION" in instruction


def test_every_variant_gets_the_provider_matching_its_toolset() -> None:
    from backend.agents.team_manager import agent as agent_module
    from backend.agents.team_manager.case_context import (
        presentation_team_manager_instruction_provider,
        team_manager_instruction_provider,
    )

    assert agent_module.team_manager.instruction is team_manager_instruction_provider
    assert agent_module.presentation_team_manager.instruction is presentation_team_manager_instruction_provider
    for name in (
        "operational_team_manager",
        "operational_team_manager_incident_manager_only",
        "operational_team_manager_troubleshooting_manager_only",
    ):
        variant = getattr(agent_module, name)
        assert variant.instruction is not team_manager_instruction_provider
        assert callable(variant.instruction)
    # The three variants must not share one provider between them.
    providers = {
        id(getattr(agent_module, name).instruction)
        for name in (
            "operational_team_manager",
            "operational_team_manager_incident_manager_only",
            "operational_team_manager_troubleshooting_manager_only",
        )
    }
    assert len(providers) == 3


@pytest.mark.asyncio
async def test_operational_provider_renders_the_matching_instruction() -> None:
    """The provider actually renders its own composition -- and still
    appends the authoritative-context block through the SAME unchanged
    `_finalize_instruction` tail every provider in that module shares."""

    from google.adk.agents.invocation_context import InvocationContext
    from google.adk.agents.readonly_context import ReadonlyContext
    from google.adk.sessions import InMemorySessionService

    from backend.agents.team_manager.agent import operational_team_manager
    from backend.agents.team_manager.case_context import (
        operational_team_manager_incident_only_instruction_provider,
        operational_team_manager_instruction_provider,
    )

    async def _ctx(state: dict[str, Any]) -> ReadonlyContext:
        from google.adk.events import Event, EventActions

        session_service = InMemorySessionService()
        session = await session_service.create_session(app_name="t", user_id="u")
        if state:
            # Applied the SAME way the real turn applies it -- through an
            # event state-delta, never `create_session(state=...)`, whose
            # separate code path is documented-broken for `temp:` keys
            # (see read_continuation_enforcement.py's own docstring).
            await session_service.append_event(
                session,
                Event(author="user", invocation_id="test-inv", actions=EventActions(state_delta=dict(state))),
            )
        return ReadonlyContext(
            InvocationContext(
                session_service=session_service,
                invocation_id="test-inv",
                agent=operational_team_manager,
                session=session,
            )
        )

    rendered = await operational_team_manager_instruction_provider(
        await _ctx({AUTHORITATIVE_REQUEST_CONTEXT_STATE_KEY: "AUTH-BLOCK"})
    )
    assert "record_request_contract" not in rendered
    assert "AUTH-BLOCK" in rendered

    incident_only = await operational_team_manager_incident_only_instruction_provider(await _ctx({}))
    assert "TROUBLESHOOTING DELEGATION" not in incident_only
