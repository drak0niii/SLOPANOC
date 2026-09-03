"""P4B.2 -- Incident Manager synthesis compression + Team Manager handoff
optimization.

P4B.1 collapsed the resolved-continuation call graph to exactly one
Incident Manager model call (frozen, unchanged by this pass -- see
test_p4b_incident_manager_call_graph.py). That one call's live-measured
cost was still high: the synthesis-only agent (tools=[]) inherited the
FULL generic ~39,707-char `INCIDENT_MANAGER_INSTRUCTION` (built for a
tool-calling agent that discovers its own destination and interprets time
ranges -- none of which applies once retrieval already happened and
tools=[]), and `IncidentManagerResponse`'s own JSON schema carried long
historical-rationale docstrings that reach the model on every call.

This module tests, without pinning full prompt text (semantic/section
checks only, per this pass's own testing guidance):
  - the new `INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION` is materially smaller
    than the generic instruction, and both drop/keep the right sections;
  - the trimmed `IncidentManagerResponse`/`TeamsEvidence`/
    `TeamsWriteActionResult` schemas are materially smaller without
    dropping any field;
  - the deterministic pipeline never truncates structured findings to a
    fixed count (section 13/29's "no arbitrary top-N limit");
  - `TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION` no longer claims `summary`
    already duplicates every category, and instead tells team_manager to
    present populated categories as their own sections without repeating
    the overview inside them (section 18/19).

Does NOT re-test what stays frozen: P4B.1's call graph (retrieval before
the one model call, tools=[], listChats=0) is covered by
test_p4b_incident_manager_call_graph.py; evidence validation/stripping
(evidence.py) is covered by test_r2_authoritative_retrieval_enforcement.py
and friends; the generic `INCIDENT_MANAGER_INSTRUCTION` used by the
normal/time-range paths is covered by
test_incident_manager_resolved_chat_id_prompt_contract.py and is asserted
here only to be byte-for-byte unchanged.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from google.genai import types

from backend.agents.incident_manager.prompts import (
    INCIDENT_MANAGER_INSTRUCTION,
    INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION,
)
from backend.agents.incident_manager.schemas import IncidentManagerResponse
from backend.agents.team_manager import read_continuation_execution as execution_module
from backend.agents.team_manager.prompts import TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION
from backend.agents.team_manager.read_continuation_execution import _SYNTHESIS_ONLY_INCIDENT_MANAGER
from backend.api.session_service import ApiSessionService
from backend.selection.schemas import ResolvedReadContinuation
from backend.tests._fakes import FakeResponse, message


class _FakeEvent:
    def __init__(self, content: Any) -> None:
        self.content = content


# --- Section 4/5: the generic instruction stays frozen -----------------


def test_generic_incident_manager_instruction_is_byte_for_byte_unchanged() -> None:
    """P4B.2 must not regress the heavily-prompt-tested normal/time-range
    path -- this is the same 39,707-char instruction measured earlier in
    this pass, confirmed unchanged.
    """
    assert len(INCIDENT_MANAGER_INSTRUCTION) == 39707


def test_synthesis_only_agent_no_longer_uses_the_generic_instruction() -> None:
    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.instruction is INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION
    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.instruction is not INCIDENT_MANAGER_INSTRUCTION


def test_time_range_continuation_agent_still_uses_the_generic_instruction() -> None:
    """The time-range path (section 25) is out of scope for this pass --
    it must keep reasoning about destination/time discovery, so it keeps
    the full generic instruction.
    """
    from backend.agents.team_manager.read_continuation_execution import _CONTINUATION_INCIDENT_MANAGER

    assert _CONTINUATION_INCIDENT_MANAGER.instruction is INCIDENT_MANAGER_INSTRUCTION


# --- Section 6/7: synthesis instruction is materially smaller ----------


def test_synthesis_instruction_is_materially_smaller_than_generic() -> None:
    generic_tokens = len(INCIDENT_MANAGER_INSTRUCTION) // 4
    synthesis_tokens = len(INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION) // 4
    assert synthesis_tokens < generic_tokens
    # Directional target (section 7): not a hard cutoff, but the pass
    # should not be a no-op -- require at least a third smaller.
    assert synthesis_tokens < generic_tokens * 0.7


# --- Section 5/31: synthesis instruction drops inapplicable sections,
# --- keeps classification-critical ones (semantic/section checks, never
# --- full-text pinning) --------------------------------------------------


@pytest.mark.parametrize(
    "forbidden",
    [
        "teams_list_chats",
        "teams_get_messages",
        "get_current_time_context",
        "TEAMS WRITE ACTIONS",
        "teams_propose_create_chat",
        "teams_propose_send_message",
        "teams_create_chat",
        "teams_send_message",
        "DETERMINISTICALLY-RESOLVED CHAT ID",
        "TIME RANGE INTERPRETATION",
        "pending_write_message",
        "selection_pending",
    ],
)
def test_synthesis_instruction_excludes_inapplicable_tool_and_write_content(forbidden: str) -> None:
    assert forbidden not in INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION


@pytest.mark.parametrize(
    "required",
    [
        "RESPONSE STRUCTURE",
        "MATERIALITY GATE",
        "SEMANTIC CLASSIFICATION",
        "MESSAGE REFERENCES",
        "MESSAGE CHRONOLOGY",
        "COVERAGE WORDING",
        "Decision confidence rule",
        "Consistency across framings",
        "no_result",
        "prefetched_evidence",
    ],
)
def test_synthesis_instruction_keeps_classification_critical_sections(required: str) -> None:
    assert required in INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION


# --- Section 11/12: redefined summary semantics + dense findings -------


def test_synthesis_instruction_redefines_summary_as_a_short_overview() -> None:
    text = INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION
    assert "SUMMARY IS AN OVERVIEW" in text
    assert "MUST NOT enumerate" in text
    assert "2-4 sentences" in text


def test_synthesis_instruction_has_dense_findings_guidance() -> None:
    text = INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION
    assert "DENSE FINDINGS" in text
    assert "The team discussed" in text  # cited as a filler example to avoid


def test_synthesis_instruction_never_weakens_semantic_classification_definitions() -> None:
    """Section 9: classification definitions must not be weakened or
    merged. Spot-check the five category definitions are all still
    present and distinct (never collapsed into one blob).
    """
    text = INCIDENT_MANAGER_SYNTHESIS_INSTRUCTION
    for label in ("- Decision:", "- Action:", "- Proposal:", "- Open question:", "- Risk/blocker:"):
        assert label in text


# --- Section 16: schema descriptions trimmed, no field dropped ---------


def test_response_schema_is_materially_smaller_but_keeps_every_field() -> None:
    schema = IncidentManagerResponse.model_json_schema()
    serialized = json.dumps(schema)
    # Measured before this pass's trim: 12,131 chars (~3,032 tokens).
    assert len(serialized) < 12131 * 0.8

    expected_fields = {
        "outcome",
        "chat_id",
        "chat_title",
        "summary",
        "evidence",
        "decisions",
        "actions",
        "proposals",
        "open_questions",
        "risks",
        "write_action",
        "candidate_titles",
        "detail",
    }
    assert set(schema["properties"].keys()) == expected_fields


def test_schema_no_longer_carries_removed_field_historical_rationale() -> None:
    """The verbose "why was `snippet` removed"/"Phase 4G hardening pass"
    docstrings explained design history the model has no need for -- they
    should be gone, while the functional constraints they also carried
    (no message body in evidence, no expiry field) remain stated concisely.
    """
    serialized = json.dumps(IncidentManagerResponse.model_json_schema())
    assert "HARDENING PASS" not in serialized
    assert "Phase 4G" not in serialized
    assert "briefly added an optional" not in serialized
    # Functional constraints survive in compact form.
    assert "expiry" in serialized.lower()


# --- Section 13/29: no arbitrary cap on structured findings -------------


class _ManyFindingsFakeRunner:
    """Simulates the synthesis-only model returning a dense response with
    many items in every category -- proves nothing in the deterministic
    pipeline (Python, not the model) silently truncates to a fixed count.
    """

    def __init__(self, *, app_name: str, agent: Any, session_service: Any, memory_service: Any = None) -> None:
        assert agent is _SYNTHESIS_ONLY_INCIDENT_MANAGER
        self._app_name = app_name
        self._session_service = session_service

    async def run_async(self, *, user_id: str, session_id: str, new_message: Any, run_config: Any = None):
        request = json.loads(new_message.parts[0].text)
        prefetched = request["prefetched_evidence"]
        evidence = [{"message_id": m["message_id"], "author": m["author"], "sent_at": m["sent_at"]} for m in prefetched]
        payload = {
            "outcome": "ok",
            "chat_id": request.get("chat_topic"),
            "chat_title": request.get("chat_topic"),
            "summary": "Wide-ranging planning discussion covering staffing, onboarding, and rollout sequencing.",
            "evidence": evidence,
            "decisions": [{"decision": f"Decision {i} confirmed."} for i in range(12)],
            "actions": [{"action": f"Action item {i}.", "owner": f"User{i % 5}"} for i in range(14)],
            "proposals": [{"proposal": f"Proposal {i} raised."} for i in range(6)],
            "open_questions": [{"question": f"Open question {i}?"} for i in range(9)],
            "risks": [{"risk": f"Risk {i} identified."} for i in range(7)],
        }
        yield _FakeEvent(types.Content(role="model", parts=[types.Part.from_text(text=json.dumps(payload))]))

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_deterministic_path_preserves_every_finding_no_matter_how_many(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.gateway import power_automate_client as pac_module

    many_messages = [message(f"m{i}", f"User{i % 5}", f"Message {i}.", f"2026-08-20T09:{i:02d}:00Z") for i in range(20)]

    def fake_post(url, json, timeout):
        if json.get("operation") == "teams.getMessages":
            return FakeResponse(200, many_messages)
        return FakeResponse(200, [])

    monkeypatch.setattr(pac_module.requests, "post", fake_post)
    monkeypatch.setattr(execution_module, "Runner", _ManyFindingsFakeRunner)

    service = ApiSessionService()
    session_id = await service.create_session()
    session = await service.get_session(session_id)

    result = await execution_module.execute_read_continuation(
        session_service=service.adk_session_service,
        user_id="api-user",
        parent_session_id=session_id,
        run_id="run-many-findings",
        parent_state=dict(session.state),
        continuation=ResolvedReadContinuation(
            selected_chat_id="chat-b2-id", selected_chat_topic="Chat B2", requested_time_range=None
        ),
    )

    assert result is not None
    assert result["outcome"] == "ok"
    assert len(result["decisions"]) == 12
    assert len(result["actions"]) == 14
    assert len(result["proposals"]) == 6
    assert len(result["open_questions"]) == 9
    assert len(result["risks"]) == 7

    # The same, unmodified data must survive re-validation into the
    # trusted envelope Team Manager actually consumes -- no separate
    # truncation point downstream either.
    from backend.agents.team_manager.read_continuation_presentation import build_trusted_specialist_result_envelope

    envelope = build_trusted_specialist_result_envelope("run-many-findings", result)
    assert envelope is not None
    assert len(envelope["result"]["actions"]) == 14
    assert len(envelope["result"]["decisions"]) == 12


# --- Section 22/23: trust-boundary mechanics inherited unmodified -------


def test_synthesis_agent_still_shares_the_same_evidence_and_timing_callbacks() -> None:
    """The `.model_copy` for P4B.2 only overrides `tools`/`instruction` --
    every deterministic guarantee (evidence stripping, P2 timing,
    output_schema/input_schema) must still come from the same shared
    `incident_manager` base agent, unmodified.
    """
    from backend.agents.incident_manager.agent import incident_manager

    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.after_agent_callback is incident_manager.after_agent_callback
    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.before_model_callback is incident_manager.before_model_callback
    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.after_model_callback is incident_manager.after_model_callback
    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.output_schema is incident_manager.output_schema
    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.input_schema is incident_manager.input_schema
    assert _SYNTHESIS_ONLY_INCIDENT_MANAGER.name == incident_manager.name


# --- Section 18/19: Team Manager presentation instruction updated ------


def test_trusted_result_instruction_no_longer_claims_summary_is_comprehensive() -> None:
    """Before this pass, the presentation instruction told team_manager
    that `summary` "already comprehensively covers every category" --
    that claim is now false, since the specialist's `summary` is a short
    overview by design (section 11). It must be gone.
    """
    assert "already comprehensively covers every category" not in TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION


@pytest.mark.parametrize(
    "required",
    [
        "brief situational",
        "does NOT enumerate",
        "own clearly labeled section",
        "never invent",
        "just to fill one",
        "appears exactly once",
    ],
)
def test_trusted_result_instruction_tells_team_manager_to_render_sections_not_reanalyze(required: str) -> None:
    assert required in TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION


def test_trusted_result_instruction_still_forbids_provenance_footer_and_internal_ids() -> None:
    """Section 19/23 -- these prohibitions predate this pass and must
    survive it unchanged in spirit.
    """
    text = TEAM_MANAGER_TRUSTED_RESULT_INSTRUCTION
    assert "provenance/citation" in text
    assert "Never mention tool names," in text
    assert "message ids, chat ids" in text
