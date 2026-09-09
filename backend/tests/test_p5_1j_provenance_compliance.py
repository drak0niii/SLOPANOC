"""THIRD pre-4H correction pass: SEARCH RESULT != EVIDENCE USED.

Covers `backend.agents.incident_manager.provenance_compliance` -- the
deterministic enforcement that a delegated request which structurally
REQUIRED governed knowledge (`requires_governed_knowledge=True`) and whose
`knowledge_search` produced AVAILABLE evidence must not complete with
SELECTED evidence still empty -- and its composition into evidence.py's
`enforce_incident_manager_response_integrity` (the new `after_agent_
callback`), alongside the pre-existing, unchanged Teams evidence-stripping
check.

Tests A-I map directly onto the correction task's own lettered
requirements (REQUIRED+AVAILABLE+SELECTED, REQUIRED+AVAILABLE+NO
SELECTION FIRST ATTEMPT -> one bounded retry -> success, REQUIRED+
AVAILABLE+NO SELECTION AFTER RETRY -> deterministic safe failure,
REQUIRED+ZERO AVAILABLE, KM OPTIONAL, COMBINED Teams+KM source behavior,
NO RETRIEVAL REPEAT, NO AUTOMATIC SELECTION, NO REGEX/ANSWER PARSING).
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Optional

import pytest
from google.adk.models import BaseLlm, LlmResponse
from google.genai import types

from backend.agents.incident_manager import evidence as evidence_module
from backend.agents.incident_manager import provenance_compliance as pc
from backend.agents.incident_manager.schemas import IncidentManagerRequest
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


def _evidence_item(
    knowledge_id: str = "aurora-relay", section_id: str = "aurora-relay:v1:s0", content: str = "checksum 7319, status GREEN"
) -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, content=content, heading="Verification")
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc", display_name="Aurora Relay Procedure")
    reference = KnowledgeEvidenceReference(
        knowledge_id=knowledge_id, version_label="v1", section_id=section_id, source_system="test", source_id=f"{knowledge_id}-doc"
    )
    return KnowledgeEvidenceItem(
        reference=reference, title="Aurora Relay Verification", document_type=KnowledgeDocumentType.SOP,
        lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section,
    )


def _execution(*items: KnowledgeEvidenceItem) -> KnowledgeSearchExecutionResult:
    return KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=list(items)))


def _key(item: KnowledgeEvidenceItem) -> dict[str, Any]:
    return {
        "knowledge_id": item.reference.knowledge_id,
        "version_label": item.reference.version_label,
        "section_id": item.reference.section_id,
    }


def _ctx(requires_governed_knowledge: bool, chat_topic: Optional[str] = None) -> Any:
    request = IncidentManagerRequest(chat_topic=chat_topic, requires_governed_knowledge=requires_governed_knowledge)
    content = types.Content(role="user", parts=[types.Part.from_text(text=request.model_dump_json(exclude_none=True))])
    return type("Ctx", (), {"user_content": content})()


def _function_call_parts(name: str, args: dict[str, Any], call_id: str):
    def _build() -> list:
        part = types.Part.from_function_call(name=name, args=args)
        part.function_call.id = call_id
        return [part]

    return _build


def _text_parts(text: str):
    return lambda: [types.Part.from_text(text=text)]


class _ScriptedLlm(BaseLlm):
    """Minimal scripted fake -- one response per real invocation, indexed
    by call count (pinned to the last entry if over-called, so an
    unexpected extra call surfaces as a visible assertion failure in the
    test rather than an IndexError).
    """

    parts_by_call: Any
    calls: int = 0

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        index = min(self.calls, len(self.parts_by_call) - 1)
        parts = self.parts_by_call[index]()
        self.calls += 1
        yield LlmResponse(content=types.Content(role="model", parts=parts))


def _install_retry_fake(monkeypatch: pytest.MonkeyPatch, fake_llm: _ScriptedLlm) -> None:
    base = pc._compliance_retry_incident_manager()
    monkeypatch.setattr(pc, "_compliance_retry_agent_cache", [base.model_copy(update={"model": fake_llm})])


@pytest.fixture
def run_id():
    rid = f"run-{id(object())}"
    token = bind_run_id(rid)
    try:
        yield rid
    finally:
        reset_run_id(token)
        rt.discard_knowledge_run_evidence_state(rid)


# --- A: REQUIRED + AVAILABLE + SELECTED -----------------------------------


@pytest.mark.asyncio
async def test_a_required_available_already_selected_is_compliant_noop(run_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _evidence_item()
    rt.get_or_init_run_state(run_id)
    rt.record_search_result(run_id, _execution(item))
    rt.select_evidence(run_id, [KnowledgeEvidenceSelectionKey(**_key(item))])

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("compliance retry must not run when already compliant")

    monkeypatch.setattr(pc, "_run_compliance_retry", _must_not_run)

    result = await pc.enforce_governed_knowledge_selection(_ctx(True), "original answer text")
    assert result is None


# --- B: REQUIRED + AVAILABLE + NO SELECTION FIRST ATTEMPT -> retry succeeds --


@pytest.mark.asyncio
async def test_b_retry_selects_evidence_then_succeeds(run_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _evidence_item()
    rt.get_or_init_run_state(run_id)
    rt.record_search_result(run_id, _execution(item))

    retry_final_text = json.dumps({"outcome": "ok", "summary": "Aurora relay checksum is 7319, status GREEN."})
    fake_llm = _ScriptedLlm(
        model="fake-compliance-retry",
        parts_by_call=[
            _function_call_parts("knowledge_select_evidence", {"selections": [_key(item)]}, "call-1"),
            _text_parts(retry_final_text),
        ],
    )
    _install_retry_fake(monkeypatch, fake_llm)

    result = await pc.enforce_governed_knowledge_selection(_ctx(True), "original answer text without selection")

    assert result == retry_final_text
    assert fake_llm.calls == 2
    selected = rt.snapshot_selected_knowledge_evidence(run_id)
    assert len(selected) == 1
    assert selected[0].reference.knowledge_id == item.reference.knowledge_id
    # NO RETRIEVAL REPEAT: still exactly the one item the original search produced.
    assert len(rt.get_available_knowledge_evidence(run_id).items) == 1


# --- C: REQUIRED + AVAILABLE + NO SELECTION AFTER RETRY -> safe failure -----


@pytest.mark.asyncio
async def test_c_retry_without_selection_fails_closed(run_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _evidence_item()
    rt.get_or_init_run_state(run_id)
    rt.record_search_result(run_id, _execution(item))

    fake_llm = _ScriptedLlm(
        model="fake-compliance-retry-noncompliant",
        parts_by_call=[_text_parts(json.dumps({"outcome": "ok", "summary": "Aurora relay checksum is 7319, status GREEN."}))],
    )
    _install_retry_fake(monkeypatch, fake_llm)

    result = await pc.enforce_governed_knowledge_selection(_ctx(True), "original answer text without selection")

    assert result == pc._SAFE_FAILURE_TEXT
    parsed = json.loads(result)
    assert parsed["outcome"] == "error"
    assert "could not be validated" in parsed["detail"]
    assert rt.snapshot_selected_knowledge_evidence(run_id) == []
    assert fake_llm.calls == 1  # exactly one bounded retry attempt, never a loop


# --- D: REQUIRED + ZERO AVAILABLE -------------------------------------------


@pytest.mark.asyncio
async def test_d_required_but_zero_available_never_forces_a_selection(run_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    rt.get_or_init_run_state(run_id)  # knowledge_search ran, found nothing

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("compliance retry must not run with zero available evidence")

    monkeypatch.setattr(pc, "_run_compliance_retry", _must_not_run)

    result = await pc.enforce_governed_knowledge_selection(_ctx(True), "No governed knowledge was found.")
    assert result is None
    assert rt.snapshot_selected_knowledge_evidence(run_id) == []


# --- E: KM OPTIONAL ----------------------------------------------------------


@pytest.mark.asyncio
async def test_e_km_optional_available_but_unselected_is_never_forced(run_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    item = _evidence_item()
    rt.get_or_init_run_state(run_id)
    rt.record_search_result(run_id, _execution(item))

    def _must_not_run(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("compliance retry must not run when governed knowledge was optional")

    monkeypatch.setattr(pc, "_run_compliance_retry", _must_not_run)

    result = await pc.enforce_governed_knowledge_selection(_ctx(False), "answer that happened to mention search results")
    assert result is None
    # NO AUTOMATIC SELECTION -- optional KM search leaves selected empty.
    assert rt.snapshot_selected_knowledge_evidence(run_id) == []


# --- F: COMBINED Teams + KM source behavior (full composed callback) -------


class _FakeEvent:
    def __init__(self, author: str, content: Any) -> None:
        self.author = author
        self.content = content


class _FakeSession:
    def __init__(self, events: list[Any]) -> None:
        self.events = events


class _FakeState(dict):
    pass


class _FakeCallbackContext:
    def __init__(self, *, session: Any, state: dict[str, Any], user_content: Any) -> None:
        self.session = session
        self.state = state
        self.user_content = user_content


@pytest.mark.asyncio
async def test_f_combined_teams_and_km_both_survive_the_composed_callback(
    run_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = _evidence_item()
    rt.get_or_init_run_state(run_id)
    rt.record_search_result(run_id, _execution(item))

    original_payload = {
        "outcome": "ok",
        "chat_id": "chat-1",
        "chat_title": "Knowledge Management Daily Sync up",
        "summary": "Teams: KM sync happened. Governed knowledge: Aurora relay checksum is 7319, status GREEN.",
        "evidence": [{"message_id": "m1", "author": "Alex", "sent_at": "2026-09-01T08:00:00Z"}],
    }
    original_text = json.dumps(original_payload)
    session = _FakeSession([_FakeEvent("incident_manager", types.Content(role="model", parts=[types.Part.from_text(text=original_text)]))])

    retry_payload = dict(original_payload)
    retry_payload["summary"] = original_payload["summary"] + " (confirmed after selecting evidence)"
    retry_final_text = json.dumps(retry_payload)
    fake_llm = _ScriptedLlm(
        model="fake-compliance-retry-combined",
        parts_by_call=[
            _function_call_parts("knowledge_select_evidence", {"selections": [_key(item)]}, "call-1"),
            _text_parts(retry_final_text),
        ],
    )
    _install_retry_fake(monkeypatch, fake_llm)

    callback_context = _FakeCallbackContext(
        session=session,
        state={"known_message_ids": ["m1"]},
        user_content=_ctx(True, chat_topic="Knowledge Management Daily Sync up").user_content,
    )
    # `KNOWN_MESSAGE_IDS_STATE_KEY` is the real state key evidence.py reads.
    from backend.tools.teams.get_messages import KNOWN_MESSAGE_IDS_STATE_KEY

    callback_context.state = {KNOWN_MESSAGE_IDS_STATE_KEY: ["m1"]}

    result = await evidence_module.enforce_incident_manager_response_integrity(callback_context)

    assert result is not None  # the retry's restated text differs from the original -- a real override
    result_text = "".join(p.text for p in result.parts if p.text)
    parsed = json.loads(result_text)
    assert parsed["evidence"] == [{"message_id": "m1", "author": "Alex", "sent_at": "2026-09-01T08:00:00Z"}]  # Teams evidence survives (m1 is known)
    assert "confirmed after selecting evidence" in parsed["summary"]  # the retry's own text is what is used
    assert rt.snapshot_selected_knowledge_evidence(run_id) != []  # governed knowledge now selected -- both sources provable downstream


# --- G: NO RETRIEVAL REPEAT --------------------------------------------------


def test_g_retry_agent_structurally_cannot_repeat_retrieval() -> None:
    retry_agent = pc._compliance_retry_incident_manager()
    tool_names = {getattr(t, "__name__", getattr(t, "name", None)) for t in retry_agent.tools}
    assert tool_names == {"knowledge_select_evidence"}
    assert retry_agent.after_agent_callback is None  # structural "at most one retry" guarantee


# --- H: NO AUTOMATIC SELECTION -----------------------------------------------


def test_h_module_has_no_direct_write_path_to_selected_evidence() -> None:
    assert not hasattr(pc, "select_evidence")  # only the real tool, never a direct backend write
    assert hasattr(pc, "knowledge_select_evidence")


@pytest.mark.asyncio
async def test_h_available_evidence_never_auto_copied_into_selected(run_id: str) -> None:
    item = _evidence_item()
    rt.get_or_init_run_state(run_id)
    rt.record_search_result(run_id, _execution(item))
    assert rt.get_available_knowledge_evidence(run_id).items
    assert rt.snapshot_selected_knowledge_evidence(run_id) == []


# --- I: NO REGEX/ANSWER PARSING ----------------------------------------------


def test_i_no_regex_import_in_provenance_compliance_module() -> None:
    import ast

    source_path = inspect.getfile(pc)
    tree = ast.parse(open(source_path, encoding="utf-8").read(), filename=source_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "re", "provenance_compliance.py must not import re"
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re", "provenance_compliance.py must not import re"


# --- J-M: A5 live UI governed-knowledge reliability corrective pass --------
#
# LIVE-REPORTED DEFECT: a real browser turn ("I have a VSWR Over Threshold
# alarm on an Ericsson 4G site. What should I do?") produced "I was unable
# to find any governed knowledge..." despite A5-VALIDATION-DOCUMENT1
# genuinely existing, being returned by knowledge_search with
# ApplicabilityOutcome.MATCH, and being cited in the authoritative log as
# available at every stage. Code audit of the ORIGINAL `_RETRY_INSTRUCTION`
# proved the retry could only ask "did your prior answer rely on this,"
# never "does this actually answer the question" -- a prior answer that
# itself concluded "nothing relevant" could never be corrected by that
# retry, only faithfully re-confirmed via `knowledge_select_evidence([])`.
# 21 live reproduction attempts (through the real stack, both before and
# after this fix) never forced the exact failure -- it is genuinely
# non-deterministic (model-sampling-dependent) -- so the tests below prove
# the STRUCTURAL correction deterministically, via a scripted LLM, rather
# than relying on forcing a rare live sample.


@pytest.mark.asyncio
async def test_j_retry_request_carries_the_original_question(run_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """The retry must be able to reassess `available_evidence` against
    what was ACTUALLY asked, not only against its own (possibly mistaken)
    prior answer -- this requires the original request's own `question`
    text to reach the retry request payload, which it did not before this
    pass (`_ProvenanceComplianceRetryRequest` had no `question` field at
    all).
    """
    item = _evidence_item()
    rt.get_or_init_run_state(run_id)
    rt.record_search_result(run_id, _execution(item))

    captured_requests: list[Any] = []

    async def _capture(*, run_id: str, question: Optional[str], prior_answer_text: str, available_items: Any) -> str:
        captured_requests.append(question)
        return json.dumps({"outcome": "ok", "summary": "corrected"})

    monkeypatch.setattr(pc, "_run_compliance_retry", _capture)

    request = IncidentManagerRequest(
        question="I have a VSWR Over Threshold alarm on an Ericsson 4G site. What should I do?",
        requires_governed_knowledge=True,
    )
    ctx = type(
        "Ctx", (), {"user_content": types.Content(role="user", parts=[types.Part.from_text(text=request.model_dump_json(exclude_none=True))])}
    )()

    await pc.enforce_governed_knowledge_selection(ctx, "I was unable to find any governed knowledge...")

    assert captured_requests == ["I have a VSWR Over Threshold alarm on an Ericsson 4G site. What should I do?"]


@pytest.mark.asyncio
async def test_k_retry_can_reassess_and_correct_a_false_negative_prior_answer(
    run_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact live-reported shape: the prior answer wrongly concluded
    no governed knowledge applied. The retry, given the SAME available
    evidence plus the original question, reassesses, selects the relevant
    item, and produces a materially DIFFERENT, corrected, grounded answer
    -- this must be accepted as the real result, not discarded merely
    because it differs from `prior_answer_text` (the old instruction's own
    "do not change its content" constraint no longer applies).
    """
    item = _evidence_item()
    rt.get_or_init_run_state(run_id)
    rt.record_search_result(run_id, _execution(item))

    corrected_text = json.dumps(
        {"outcome": "ok", "summary": "For a VSWR Over Threshold alarm, no restart is allowed -- diagnosis only."}
    )
    fake_llm = _ScriptedLlm(
        model="fake-compliance-retry-reassess",
        parts_by_call=[
            _function_call_parts("knowledge_select_evidence", {"selections": [_key(item)]}, "call-1"),
            _text_parts(corrected_text),
        ],
    )
    _install_retry_fake(monkeypatch, fake_llm)

    prior_false_negative = json.dumps(
        {"outcome": "no_result", "detail": "I was unable to find any governed knowledge for this alarm."}
    )
    result = await pc.enforce_governed_knowledge_selection(_ctx(True), prior_false_negative)

    assert result == corrected_text
    parsed = json.loads(result)
    assert parsed["outcome"] == "ok"
    assert "no restart is allowed" in parsed["summary"]
    selected = rt.snapshot_selected_knowledge_evidence(run_id)
    assert len(selected) == 1
    assert selected[0].reference.knowledge_id == item.reference.knowledge_id


@pytest.mark.asyncio
async def test_l_genuinely_irrelevant_evidence_still_fails_closed_after_reassessment(
    run_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Required case B/F: the retry may STILL legitimately conclude, after
    a genuine reassessment, that nothing in `available_evidence` answers
    the question -- an empty selection remains semantically valid and
    must not be forced into a fabricated selection. NO_RESULT-shaped safe
    failure is the correct outcome, exactly as before this pass.
    """
    item = _evidence_item(knowledge_id="unrelated-topic", section_id="unrelated-topic:v1:s0", content="unrelated content")
    rt.get_or_init_run_state(run_id)
    rt.record_search_result(run_id, _execution(item))

    fake_llm = _ScriptedLlm(
        model="fake-compliance-retry-genuinely-empty",
        parts_by_call=[
            _function_call_parts("knowledge_select_evidence", {"selections": []}, "call-1"),
            _text_parts(json.dumps({"outcome": "no_result", "detail": "No applicable governed knowledge was found."})),
        ],
    )
    _install_retry_fake(monkeypatch, fake_llm)

    result = await pc.enforce_governed_knowledge_selection(_ctx(True), "prior answer text")

    # An explicit, reassessed empty selection is NOT the same as "the
    # retry never selected anything" -- `enforce_governed_knowledge_
    # selection` still checks the TRUSTED run state (never the retry's own
    # text) to decide compliance, so a genuine empty selection after
    # reassessment still correctly falls through to the deterministic
    # safe-failure text -- backend code, not the model, owns this decision.
    assert result == pc._SAFE_FAILURE_TEXT
    assert rt.snapshot_selected_knowledge_evidence(run_id) == []


def test_m_retry_instruction_no_longer_forces_verbatim_repetition() -> None:
    """Structural proof the old self-consistency-only framing ("respond
    again with the SAME structured response... do not change its
    content") is gone -- the retry is now explicitly permitted (and
    instructed) to produce a genuinely corrected answer when its
    reassessment finds relevant evidence the prior answer missed.
    """
    assert "do not change its content" not in pc._RETRY_INSTRUCTION
    assert "question" in pc._RETRY_INSTRUCTION.lower()
    assert "re-examine" in pc._RETRY_INSTRUCTION.lower() or "reassess" in pc._RETRY_INSTRUCTION.lower()


def test_i_enforcement_decision_never_inspects_answer_content() -> None:
    """Structural proof: `enforce_governed_knowledge_selection`'s own
    source never branches on `original_text`/`retry_text` content -- both
    are only ever passed through opaquely (to the retry request, or back
    out as the return value), never parsed/matched/keyword-scanned. The
    real decision inputs are only `_requires_governed_knowledge` (a
    structured field) and the trusted `available`/`selected` run state.
    """
    source = inspect.getsource(pc.enforce_governed_knowledge_selection)
    for forbidden in ("original_text.lower()", "original_text.find(", " in original_text", "retry_text.lower()", " in retry_text"):
        assert forbidden not in source, f"enforce_governed_knowledge_selection must not inspect answer text ({forbidden!r} found)"
