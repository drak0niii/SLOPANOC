"""LIVE-CORR-12E -- Policy-Bound Natural Clarification Rendering.

CONTROL-PLANE-SEQ-06 reconciliation note: this file originally targeted
LIVE-CORR-12E.1's closed `ClarificationRenderingPlan` design (`field_
order`/a 3-value `style` enum, no free text). LIVE-CORR-12H later, and
explicitly, REVERTED that design back to a natural, model-authored
`message: str` field -- see clarification_renderer.py's own module
docstring for the full history and the deliberate trade-off this
represents. `test_livecorr12e1_clarification_renderer_output_safety.py`/
`test_livecorr12e2_subject_injection_closure.py` (which proved the NOW-
SUPERSEDED closed-plan design specifically) were removed in this pass --
their entire premise (`_compose_clarification_message`, the closed
`ClarificationStyle` enum) no longer exists in production. This file's
OWN original intent -- clarification generation is policy-bound
(`fields_asked` validated by exact set-equality against `decision.
missing_context`, never a raw model declaration) and falls back
deterministically on any mismatch/failure -- remains exactly what LIVE-
CORR-12H's own `{message, fields_asked}` design still guarantees, so this
file is UPDATED, not removed, to construct/assert against the CURRENT
schema.

Tests are semantic, never exact-string (section 21's own explicit
requirement): they assert on `fields_asked` correctness, fallback
selection, and the ABSENCE of command content -- never on a specific
English sentence, since (LIVE-CORR-12H) `message` wording is intentionally
natural and variable. Every scenario mocks `render_clarification_plan`
(the ONLY function that makes a model call) -- NO REAL GEMINI CALL.

This module does NOT touch semantic parameter-value verification,
RequestClass/PendingGovernedRequest design, canonical parameter registry
design, presentation-runner freshness, or governed-evidence continuity --
explicitly out of scope for this pass.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.agents.team_manager.clarification_renderer import (
    ClarificationRenderingPlan,
    render_command_suppression_text,
)
from backend.agents.team_manager.request_contract import (
    PENDING_GOVERNED_REQUEST_STATE_KEY,
    REQUEST_CONTRACT_TOOL_NAME,
    VALIDATED_REQUEST_CONTRACT_STATE_KEY,
    ParameterProvenance,
    RequestClass,
    RequestContract,
    RequestIntent,
    RequestParameter,
    RequestedOutput,
    derive_request_class,
    parse_pending_governed_request,
    record_request_contract,
    validate_and_persist_request_contract,
)
from backend.agents.team_manager.request_execution_policy import (
    RequestExecutionDecision,
    RequestExecutionStatus,
    _safe_missing_context_label,
    command_suppression_fallback_text,
    derive_execution_decision,
)
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.turn_context import current_run_id
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner, append_state_delta

_RUN_ID = "livecorr12e-run"


# =============================================================================
# Shared scaffolding
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


def _stored_contract(ctx: _FakeToolContext, run_id: str = _RUN_ID) -> RequestContract:
    raw = ctx.state.get(VALIDATED_REQUEST_CONTRACT_STATE_KEY)
    assert raw is not None
    contract = RequestContract.model_validate(raw)
    return contract.model_copy(update={"run_id": run_id})


def _param(name: str, value: str) -> RequestParameter:
    return RequestParameter(name=name, value=value, provenance=ParameterProvenance.USER)


def _with_request_class(contract: RequestContract) -> RequestContract:
    return contract.model_copy(
        update={
            "request_class": derive_request_class(
                contract.intent, contract.requested_output, contract.action_requested, contract.subject
            )
        }
    )


def _decision(**overrides: Any) -> RequestExecutionDecision:
    defaults = dict(status=RequestExecutionStatus.NEEDS_INFORMATION, subject="restart RRU", missing_context=["unit_id"])
    defaults.update(overrides)
    return RequestExecutionDecision(**defaults)


def _plan(fields: list[str], message: Optional[str] = None) -> ClarificationRenderingPlan:
    """LIVE-CORR-12H's own current schema: a natural `message` (any
    non-blank string -- test fixtures use a deterministic placeholder
    sentence, never asserted on verbatim, since wording itself is
    intentionally variable) plus `fields_asked`, the SAME field-key
    metadata this file's own tests validate. The default placeholder uses
    natural field labels, never the raw canonical key names, mirroring
    what a real, prompt-compliant renderer would actually produce (the
    renderer's own instruction is explicit that raw keys are never
    surfaced) -- so fixtures do not accidentally trip this file's own
    "no raw key name" assertions.
    """
    text = message or f"Could you confirm the following: {', '.join(_safe_missing_context_label(f) for f in fields)}?"
    return ClarificationRenderingPlan(message=text, fields_asked=list(fields))


def _fake_renderer(plan: Optional[ClarificationRenderingPlan]):
    captured: list[dict] = []

    async def _fake(**kwargs: Any) -> Optional[ClarificationRenderingPlan]:
        captured.append(kwargs)
        return plan

    return _fake, captured


def _raising_renderer():
    called = {"count": 0}

    async def _fake(**kwargs: Any):
        called["count"] += 1
        raise AssertionError("clarification renderer must never be invoked for this decision shape")

    return _fake, called


_PATCH_TARGET = "backend.agents.team_manager.clarification_renderer.render_clarification_plan"


# =============================================================================
# A -- single missing field
# =============================================================================


@pytest.mark.asyncio
async def test_a_single_missing_field_uses_natural_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured = _fake_renderer(_plan(["unit_id"]))
    monkeypatch.setattr(_PATCH_TARGET, fake)

    decision = _decision(missing_context=["unit_id"])
    text = await render_command_suppression_text(decision, run_id=_RUN_ID)

    assert text != command_suppression_fallback_text(decision)
    assert "restartunit" not in text and "accn" not in text  # no command leaked
    assert "unit identifier" in text.lower() or "unit_id" not in text  # deterministic label used, never raw key
    assert len(captured) == 1
    assert [key for key, _ in captured[0]["missing_fields"]] == ["unit_id"]


# =============================================================================
# B -- multiple missing fields
# =============================================================================


@pytest.mark.asyncio
async def test_b_multiple_missing_fields_uses_natural_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, captured = _fake_renderer(_plan(["unit_id", "unit_type"]))
    monkeypatch.setattr(_PATCH_TARGET, fake)

    decision = _decision(missing_context=["unit_type", "unit_id"])
    text = await render_command_suppression_text(decision, run_id=_RUN_ID)

    assert text != command_suppression_fallback_text(decision)
    assert {key for key, _ in captured[0]["missing_fields"]} == {"unit_id", "unit_type"}


# =============================================================================
# C -- renderer cannot add fields
# =============================================================================


@pytest.mark.asyncio
async def test_c_renderer_adding_a_field_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_renderer(_plan(["unit_id", "vendor"]))
    monkeypatch.setattr(_PATCH_TARGET, fake)

    decision = _decision(missing_context=["unit_id"])
    text = await render_command_suppression_text(decision, run_id=_RUN_ID)

    assert text == command_suppression_fallback_text(decision)


# =============================================================================
# D -- renderer cannot omit fields
# =============================================================================


@pytest.mark.asyncio
async def test_d_renderer_omitting_a_field_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_renderer(_plan(["unit_id"]))
    monkeypatch.setattr(_PATCH_TARGET, fake)

    decision = _decision(missing_context=["unit_type", "unit_id"])
    text = await render_command_suppression_text(decision, run_id=_RUN_ID)

    assert text == command_suppression_fallback_text(decision)


# =============================================================================
# E -- unknown/advisory missing_context never reaches the renderer
# =============================================================================


@pytest.mark.asyncio
async def test_e_only_authoritative_canonical_fields_reach_the_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A persisted contract may still carry advisory, non-canonical
    `missing_context` entries (LIVE-CORR-12B's own established behavior --
    kept for diagnostics, never authoritative) -- `derive_execution_
    decision`'s own `decision.missing_context` is already filtered down to
    the canonical set BEFORE this function ever sees it, so the renderer
    can never even be asked about "governed procedure"."""
    result = await record_request_contract(
        intent=RequestIntent.COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        subject="restart RRU",
        provided_context=[_param("unit_type", "RRU")],
        missing_context=["governed procedure", "unit_id"],
    )
    ctx = _observe("it's an RRU", {}, result)
    contract = _stored_contract(ctx)
    # Persisted diagnostic record still carries the advisory name.
    assert "governed procedure" in contract.missing_context

    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert "governed procedure" not in decision.missing_context
    assert decision.missing_context == ["unit_id"]

    fake, captured = _fake_renderer(_plan(["unit_id"]))
    monkeypatch.setattr(_PATCH_TARGET, fake)

    text = await render_command_suppression_text(decision, run_id=_RUN_ID)
    assert text != command_suppression_fallback_text(decision)
    field_keys = {key for key, _ in captured[0]["missing_fields"]}
    assert field_keys == {"unit_id"}
    assert "governed procedure" not in field_keys


# =============================================================================
# F -- no command authority: schema cannot even carry one
# =============================================================================


def test_f_renderer_plan_schema_is_message_plus_fields_asked() -> None:
    """LIVE-CORR-12H's own current, deliberate schema (superseding the
    now-removed 12E.1/12E.2 closed-plan design): `message` IS free text,
    by explicit product decision -- the sole structural safety boundary is
    `fields_asked`, validated by exact set-equality against `decision.
    missing_context` at the caller (`_fields_asked_matches_authoritative_
    set`), never the schema shape itself. Also confirms `RequestContract
    .subject` has no field to interpolate through here at all -- LIVE-
    CORR-12E.2's own removal remains intact even though this pass
    otherwise reverted that milestone's stricter schema."""
    fields = ClarificationRenderingPlan.model_fields
    assert set(fields.keys()) == {"message", "fields_asked"}
    assert fields["message"].annotation is str
    assert "subject" not in fields


@pytest.mark.asyncio
async def test_f_renderer_cannot_grant_command_authority_regardless_of_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """This function's own return value is never consulted for `may_emit_
    command` -- that remains SOLELY `RequestExecutionDecision.may_emit_
    command`, computed upstream, completely independent of anything this
    module renders."""
    fake, _ = _fake_renderer(_plan(["unit_id"]))
    monkeypatch.setattr(_PATCH_TARGET, fake)

    decision = _decision(missing_context=["unit_id"], may_emit_command=False)
    assert decision.may_emit_command is False
    await render_command_suppression_text(decision, run_id=_RUN_ID)
    assert decision.may_emit_command is False


# =============================================================================
# G -- renderer failure (exception/None) falls back
# =============================================================================


@pytest.mark.asyncio
async def test_g_renderer_returning_none_falls_back_to_deterministic(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_renderer(None)
    monkeypatch.setattr(_PATCH_TARGET, fake)

    decision = _decision(missing_context=["unit_id"])
    text = await render_command_suppression_text(decision, run_id=_RUN_ID)
    assert text == command_suppression_fallback_text(decision)


@pytest.mark.asyncio
async def test_g_renderer_exception_does_not_propagate_and_falls_back() -> None:
    """`render_clarification_plan` itself (unmocked here) already fails
    closed for an unexpected exception -- proven directly, no mocking
    needed, since it has its own broad `except Exception`."""
    decision = _decision(missing_context=["unit_id"])
    # No credentials/network in this offline test environment -- the real
    # nested Runner call fails fast and `render_clarification_plan` returns
    # `None`, exercised here WITHOUT mocking to prove the real fail-closed
    # path, not merely a test double's promise.
    text = await render_command_suppression_text(decision, run_id=_RUN_ID)
    assert text == command_suppression_fallback_text(decision)


# =============================================================================
# H -- INVALID_CONTRACT never reaches the renderer
# =============================================================================


@pytest.mark.asyncio
async def test_h_invalid_contract_never_invokes_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, called = _raising_renderer()
    monkeypatch.setattr(_PATCH_TARGET, fake)

    decision = RequestExecutionDecision(status=RequestExecutionStatus.INVALID_CONTRACT, missing_context=[])
    text = await render_command_suppression_text(decision, run_id=_RUN_ID)

    assert called["count"] == 0
    assert text == command_suppression_fallback_text(decision)


# =============================================================================
# J -- GENERAL_CONVERSATION never invokes renderer
# =============================================================================


@pytest.mark.asyncio
async def test_j_general_conversation_never_invokes_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, called = _raising_renderer()
    monkeypatch.setattr(_PATCH_TARGET, fake)

    decision = RequestExecutionDecision(
        status=RequestExecutionStatus.ALLOW, request_class=RequestClass.GENERAL_CONVERSATION, missing_context=[]
    )
    text = await render_command_suppression_text(decision, run_id=_RUN_ID)

    assert called["count"] == 0
    assert text == command_suppression_fallback_text(decision)


# =============================================================================
# K -- canonical alias regression: RRU_ID never reaches the renderer as a name
# =============================================================================


@pytest.mark.asyncio
async def test_k_canonical_alias_normalized_before_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uses a non-`EXACT_COMMAND` output shape (`TROUBLESHOOTING_NEXT_STEP`)
    so the deterministic unit_id/unit_type blanket rule does not
    independently recompute over `provided_context` -- isolates exactly
    the alias-canonicalization behavior under test: `RRU_ID` (provided_
    context) canonicalizes to `unit_id`, already satisfied, so ONLY the
    separately, correctly-declared canonical `unit_type` gap remains, and
    the renderer sees canonical names only."""
    result = await record_request_contract(
        intent=RequestIntent.TROUBLESHOOTING,
        requested_output=RequestedOutput.TROUBLESHOOTING_NEXT_STEP,
        subject="restart RRU",
        provided_context=[_param("RRU_ID", "RRU-3")],
        missing_context=["unit_type"],
    )
    ctx = _observe("the RRU is RRU-3", {}, result)
    contract = _stored_contract(ctx)
    names = {p.name for p in contract.provided_context}
    assert names == {"unit_id"}  # RRU_ID normalized at persist time (LIVE-CORR-12B)

    decision = derive_execution_decision(contract, _RUN_ID)
    assert decision.status == RequestExecutionStatus.NEEDS_INFORMATION
    assert decision.missing_context == ["unit_type"]

    fake, captured = _fake_renderer(_plan(["unit_type"]))
    monkeypatch.setattr(_PATCH_TARGET, fake)

    await render_command_suppression_text(decision, run_id=_RUN_ID, known_context=contract.provided_context)
    field_keys = {key for key, _ in captured[0]["missing_fields"]}
    assert field_keys == {"unit_type"}
    assert "RRU_ID" not in field_keys


# =============================================================================
# L -- end-to-end: pending exact-command continuation still resolves
# correctly when clarification was naturally rendered
# =============================================================================


@pytest.mark.asyncio
async def test_l_pending_continuation_survives_natural_clarification_rendering(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, _ = _fake_renderer(_plan(["unit_id"]))
    monkeypatch.setattr(_PATCH_TARGET, fake)

    service = ApiSessionService()
    session_id = await service.create_session()

    async def _turn1_side_effect(session_service: Any, session: Any, text: str) -> None:
        run_id = current_run_id()
        contract = _with_request_class(
            RequestContract(
                intent=RequestIntent.COMMAND,
                requested_output=RequestedOutput.EXACT_COMMAND,
                subject="restart RRU",
                provided_context=[_param("unit_type", "RRU")],
                run_id=run_id,
            )
        )
        await append_state_delta(session_service, session, {VALIDATED_REQUEST_CONTRACT_STATE_KEY: contract.model_dump(mode="json")})

    events = [
        FakeEvent(
            text=None,
            final=False,
            function_responses=[FakeFunctionResponse("record_source_requirements", {"requires_teams": False, "requires_governed_knowledge": False})],
        ),
        FakeEvent(text="unused, replaced deterministically", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_turn1_side_effect, events=events))

    completed_text = None
    async for event in chat_service.execute_turn_events(session_id, "give me a command to restart an RRU", "api-user"):
        if event.type.value == "message.completed":
            completed_text = event.data["content"]

    assert completed_text != command_suppression_fallback_text(
        derive_execution_decision(
            _with_request_class(
                RequestContract(
                    intent=RequestIntent.COMMAND,
                    requested_output=RequestedOutput.EXACT_COMMAND,
                    subject="restart RRU",
                    missing_context=["unit_id"],
                    run_id=_RUN_ID,
                )
            ),
            _RUN_ID,
        )
    )
    assert "restartunit" not in (completed_text or "") and "accn" not in (completed_text or "")

    final_session = await service.get_session(session_id)
    pending = parse_pending_governed_request(final_session.state.get(PENDING_GOVERNED_REQUEST_STATE_KEY))
    assert pending is not None
    assert pending.missing_context == ["unit_id"]
    assert pending.request_class == RequestClass.EXACT_COMMAND
