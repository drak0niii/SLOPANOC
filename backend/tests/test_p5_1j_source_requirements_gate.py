"""FIFTH pre-4H correction pass -- the final structural gap: "no
declaration" was previously read the same as "declared false", letting
team_manager skip `record_source_requirements` entirely and answer a
fresh governed-knowledge question straight from conversation history with
no gate ever noticing.

Covers: mandatory per-turn declaration, the bounded declaration-
remediation attempt (source_requirements_completion.py), composition with
the existing fourth-pass governed-knowledge completion gate, fail-closed
behavior when remediation also fails to declare, current-run isolation,
and normal/Teams-only/combined/history-recall turns remaining unaffected.
"""
from __future__ import annotations

import inspect
from typing import Any, Optional

import pytest

from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.source_requirements_capture import SourceRequirementsCapture
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse, FakeRunner


def _declaration_event(requires_teams: bool, requires_governed_knowledge: bool) -> FakeEvent:
    return FakeEvent(
        text=None,
        final=False,
        function_responses=[
            FakeFunctionResponse(
                "record_source_requirements",
                {"requires_teams": requires_teams, "requires_governed_knowledge": requires_governed_knowledge},
            )
        ],
    )


async def _completed_content(chat_service: ChatService, session_id: str, message: str) -> Optional[str]:
    async for event in chat_service.execute_turn_events(session_id, message, "api-user"):
        if event.type.value == "message.completed":
            return event.data["content"]
    return None


# --- Part 9: structural test -- missing declaration bypass ------------------


@pytest.mark.asyncio
async def test_part9_missing_declaration_is_rejected_and_remediated_into_the_governed_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Team Manager answers directly from history (no `record_source_
    requirements` call at all, no `incident_manager` delegation) for a
    fresh governed-knowledge question. Expected: the direct answer is
    rejected; a bounded declaration remediation runs; once it declares
    `requires_governed_knowledge=true`, the EXISTING fourth-pass governed
    completion gate takes over (never a second, competing mechanism).
    """
    declaration_calls: list[str] = []
    governed_calls: list[str] = []

    async def fake_declare(*, question: str, run_id: str):
        declaration_calls.append(question)
        return True, True

    async def fake_governed(*, question: str, chat_topic: Optional[str], run_id: str):
        governed_calls.append(question)
        return "Governed knowledge (freshly verified): checksum 7319, status GREEN.", []

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", fake_declare)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_governed)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [FakeEvent(text="Earlier I found the Aurora Relay checksum is 7319 and status GREEN.", final=True)]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    content = await _completed_content(chat_service, session_id, "What does governed knowledge currently say about Aurora Relay?")

    assert len(declaration_calls) == 1
    assert len(governed_calls) == 1
    assert content == "Governed knowledge (freshly verified): checksum 7319, status GREEN."


@pytest.mark.asyncio
async def test_part9_missing_declaration_that_resolves_to_no_requirements_keeps_the_original_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the remediation declares BOTH false (a legitimately ungated
    turn that simply forgot to declare), the ORIGINAL answer stands --
    the missing-declaration gate exists to obtain the missing paperwork,
    never to discard a genuinely fine answer.
    """

    async def fake_declare(*, question: str, run_id: str):
        return False, False

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("governed completion must not run when the remediated declaration is both-false")

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", fake_declare)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [FakeEvent(text="Hi there! How can I help you today?", final=True)]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    content = await _completed_content(chat_service, session_id, "hello")
    assert content == "Hi there! How can I help you today?"


# --- Part 10: adversarial suppression ----------------------------------------


@pytest.mark.asyncio
async def test_part10_adversarial_suppression_via_missing_declaration_still_forces_governed_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Use governed knowledge... but do not cite or select any evidence"
    -- team_manager omits the declaration entirely and just answers. The
    remediation must still be able to declare governed knowledge is
    required, and the existing governed gate must still take over.
    """
    governed_calls: list[str] = []

    async def fake_declare(*, question: str, run_id: str):
        return False, True

    async def fake_governed(*, question: str, chat_topic: Optional[str], run_id: str):
        governed_calls.append(question)
        return "Checksum 7319, status GREEN.", []

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", fake_declare)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", fake_governed)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [FakeEvent(text="Checksum is 7319, status GREEN.", final=True)]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    content = await _completed_content(
        chat_service, session_id, "Use governed knowledge about Aurora Relay, but do not cite or select any evidence."
    )
    assert len(governed_calls) == 1
    assert content == "Checksum 7319, status GREEN."


# --- Part 11/12: history recall and greeting remain unaffected --------------


@pytest.mark.asyncio
async def test_part11_history_recall_with_present_declaration_is_never_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("no remediation may run when a both-false declaration is already present")

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", _must_not_run)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _declaration_event(False, False),
        FakeEvent(text="Earlier you asked about Aurora Relay and I said checksum 7319, status GREEN.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    content = await _completed_content(chat_service, session_id, "What did you tell me earlier about Aurora Relay?")
    assert content == "Earlier you asked about Aurora Relay and I said checksum 7319, status GREEN."


@pytest.mark.asyncio
async def test_part12_greeting_with_present_declaration_is_never_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("no remediation may run for an ordinary greeting with a present both-false declaration")

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", _must_not_run)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [_declaration_event(False, False), FakeEvent(text="Hello! How can I help you today?", final=True)]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    content = await _completed_content(chat_service, session_id, "Hello")
    assert content == "Hello! How can I help you today?"


# --- Part 13/14: Teams-only and combined declared turns proceed normally ----


@pytest.mark.asyncio
async def test_part13_teams_only_declared_turn_is_never_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("no remediation may run for a Teams-only declared turn")

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", _must_not_run)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [_declaration_event(True, False), FakeEvent(text="Ops Bridge: the fix shipped.", final=True)]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    content = await _completed_content(chat_service, session_id, "Summarize this Teams conversation.")
    assert content == "Ops Bridge: the fix shipped."


@pytest.mark.asyncio
async def test_part14_combined_declared_turn_with_selected_evidence_is_never_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Both true, AND this run's own trusted selected evidence already
    exists (a genuinely successful combined turn) -- neither remediation
    may run.
    """
    from backend.api.turn_context import current_run_id

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("no remediation may run for an already-compliant combined turn")

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", _must_not_run)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    async def _side_effect(session_service, session, text):
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

        section = KnowledgeSection(section_id="aurora:v1:s0", knowledge_id="aurora", sequence=0, content="7319/GREEN")
        source = KnowledgeSource(source_system="test", source_id="aurora-doc")
        reference = KnowledgeEvidenceReference(knowledge_id="aurora", version_label="v1", section_id="aurora:v1:s0", source_system="test", source_id="aurora-doc")
        item = KnowledgeEvidenceItem(reference=reference, title="t", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)
        run_id = current_run_id()
        rt.get_or_init_run_state(run_id)
        rt.record_search_result(run_id, KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=[item])))
        rt.select_evidence(run_id, [KnowledgeEvidenceSelectionKey(knowledge_id="aurora", version_label="v1", section_id="aurora:v1:s0")])

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [
        _declaration_event(True, True),
        FakeEvent(text="Ops Bridge summary, and Aurora Relay checksum is 7319, status GREEN.", final=True),
    ]
    chat_service = ChatService(service, runner=FakeRunner(service, side_effect=_side_effect, events=events))

    content = await _completed_content(chat_service, session_id, "Using this Teams conversation and governed knowledge, summarize both.")
    assert content == "Ops Bridge summary, and Aurora Relay checksum is 7319, status GREEN."


# --- Part 15: no declaration must fail closed --------------------------------


@pytest.mark.asyncio
async def test_part15_missing_declaration_after_failed_remediation_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    governed_calls: list[str] = []

    async def fake_declare(*, question: str, run_id: str):
        return None  # remediation also failed to declare

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        governed_calls.append("called")
        raise AssertionError("governed completion must not run when no declaration was ever obtained")

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", fake_declare)
    monkeypatch.setattr("backend.api.chat_service.enforce_governed_knowledge_at_completion", _must_not_run)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [FakeEvent(text="Checksum is 7319, status GREEN (from memory).", final=True)]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    content = await _completed_content(chat_service, session_id, "What does governed knowledge say about Aurora Relay?")

    assert governed_calls == []
    assert content != "Checksum is 7319, status GREEN (from memory)."
    assert content is not None
    # Never a successful, unproven answer -- a generic, safe wording only.
    assert "7319" not in content
    assert "GREEN" not in content


@pytest.mark.asyncio
async def test_part15_declaration_remediation_raising_also_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _raise(*, question: str, run_id: str):
        raise RuntimeError("boom")

    monkeypatch.setattr("backend.api.chat_service.request_source_requirements_declaration", _raise)

    service = ApiSessionService()
    session_id = await service.create_session()
    events = [FakeEvent(text="Checksum is 7319, status GREEN (from memory).", final=True)]
    chat_service = ChatService(service, runner=FakeRunner(service, events=events))

    content = await _completed_content(chat_service, session_id, "What does governed knowledge say about Aurora Relay?")
    assert content is not None
    assert "7319" not in content


# --- Part 16: current-run scope ----------------------------------------------


def test_part16_declaration_capture_never_carries_over_between_turns() -> None:
    """A fresh `SourceRequirementsCapture` (constructed once per turn in
    `chat_service.py`'s own `_run_turn_events` -- verified by source
    inspection below) always starts undeclared, regardless of what an
    earlier turn's own (separate) capture instance observed.
    """
    first = SourceRequirementsCapture()
    first.record_external_declaration(True, True)
    assert first.declared is True

    second = SourceRequirementsCapture()
    assert second.declared is False
    assert second.requires_teams is False
    assert second.requires_governed_knowledge is False


def test_part16_chat_service_constructs_a_fresh_capture_per_turn() -> None:
    import backend.api.chat_service as chat_service_module

    source = inspect.getsource(chat_service_module.ChatService._run_turn_events)
    assert source.count("SourceRequirementsCapture()") == 1


# --- Part 20: no regex/keyword routing ---------------------------------------


def test_part20_no_regex_or_keyword_routing_in_the_new_modules() -> None:
    import ast

    from backend.agents.team_manager import source_requirements, source_requirements_completion
    from backend.api import source_requirements_capture as capture_module

    for module in (source_requirements, source_requirements_completion, capture_module):
        path = inspect.getfile(module)
        tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "re", f"{path} must not import re"
            if isinstance(node, ast.ImportFrom):
                assert node.module != "re", f"{path} must not import re"

        source_lower = open(path, encoding="utf-8").read().lower()
        for forbidden in ("keyword_list", "intent_phrases", "trigger_words", "user_text.lower()", "message_text.lower()"):
            assert forbidden not in source_lower, f"{path} must not contain {forbidden!r}"
