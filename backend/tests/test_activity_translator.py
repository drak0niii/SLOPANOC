"""Tests for backend/api/activity_translator.py -- the deterministic
ADK-event -> safe status translator (instruction sections 44-47).
Deterministic fixtures throughout (`FakeEvent`/`FakeFunctionCall`/
`FakeFunctionResponse`), never a real Gemini call.
"""
from __future__ import annotations

from backend.api.activity_translator import (
    RunTraceTranslator,
    StatusTranslator,
    case_context_loaded_trace_step,
    response_failed_trace_step,
    response_generated_trace_step,
    selection_prepared_trace_step,
    translate_case_context_status,
)
from backend.api.streaming_events import Stage, TraceCategory, TraceStepStatus
from backend.tests._api_fakes import FakeEvent, FakeFunctionCall, FakeFunctionResponse


# --- One-line status model (instruction section 44) -----------------------


def test_status_has_a_stable_stage() -> None:
    translator = StatusTranslator()
    event = FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")])
    status = translator.translate_event(event)
    assert status["stage"] == "teams_context"


def test_new_status_supersedes_previous_by_replace_presentation() -> None:
    translator = StatusTranslator()
    call_status = translator.translate_event(
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")])
    )
    assert call_status["presentation"] == "replace"


def test_case_context_status_is_a_status_event_shape() -> None:
    data = translate_case_context_status("Packet loss")
    assert data["stage"] == "case_context"
    assert "presentation" in data
    assert data["presentation"] == "replace"


# --- Dynamic activity (instruction section 45) -----------------------------


def test_teams_status_occurs_only_when_incident_manager_is_actually_called() -> None:
    translator = StatusTranslator()
    unrelated = translator.translate_event(FakeEvent(text="hello", final=True))
    assert unrelated is None


def test_run_without_teams_never_emits_teams_activity() -> None:
    translator = StatusTranslator()
    events = [
        FakeEvent(text="Hi there", final=False, partial=True),
        FakeEvent(text="Hi there", final=True),
    ]
    statuses = [translator.translate_event(e) for e in events]
    assert all(s is None for s in statuses)


def test_run_without_case_never_emits_case_activity() -> None:
    """Structural: `translate_case_context_status` is a separate function
    the caller (chat_service.py) only invokes when (1) `active_case_id`
    is present in session state AND (2) the safe title lookup for it
    actually succeeded -- i.e. an authorized Case actually exists for
    that hint, not merely that the (possibly stale) hint is present.
    Confirmed by inspecting the call site directly.
    """
    import inspect

    import backend.api.chat_service as chat_service_module

    source = inspect.getsource(chat_service_module)
    idx = source.index("translate_case_context_status(")
    guard_window = source[max(0, idx - 1500) : idx]
    assert "if case_id" in guard_window
    assert "if case_title is not None" in guard_window


def test_safe_runtime_metadata_affects_label_content() -> None:
    translator = StatusTranslator()
    translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    refined = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "ok", "evidence": [{"message_id": "m1", "author": "A", "sent_at": "x"}] * 5},
                )
            ],
        )
    )
    assert "5" in refined["label"]


def test_no_fixed_scripted_sequence_is_required() -> None:
    """A run with zero tool calls produces zero status refinements beyond
    the initial generic one the caller emits -- proving there is no
    hardcoded progression the translator forces through.
    """
    translator = StatusTranslator()
    status = translator.translate_event(FakeEvent(text="just chatting", final=True))
    assert status is None


def test_no_user_text_keyword_inference_drives_statuses() -> None:
    """Structural: `translate_event`'s signature takes only an ADK event
    -- there is no parameter for the user's message text at all.
    """
    import inspect

    params = list(inspect.signature(StatusTranslator.translate_event).parameters)
    assert params == ["self", "event"]


# --- Status safety (instruction section 46) ---------------------------------


def test_status_never_contains_raw_tool_arguments() -> None:
    translator = StatusTranslator()
    status = translator.translate_event(
        FakeEvent(
            final=False,
            function_calls=[FakeFunctionCall("incident_manager", {"chat_topic": "Ops Bridge", "question": "secret question text"})],
        )
    )
    assert "secret question text" not in str(status)
    assert "chat_topic" not in str(status)


def test_status_never_contains_raw_tool_responses() -> None:
    translator = StatusTranslator()
    translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    status = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "ok",
                        "summary": "Full sensitive summary text that should never leak into status",
                        "evidence": [],
                    },
                )
            ],
        )
    )
    if status is not None:
        assert "Full sensitive summary text" not in str(status)


def test_status_never_contains_internal_agent_names() -> None:
    translator = StatusTranslator()
    status = translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    assert "incident_manager" not in str(status)
    assert "AgentTool" not in str(status)
    assert "team_manager" not in str(status)


def test_status_never_contains_a_payload_hash_or_credential_like_string() -> None:
    translator = StatusTranslator()
    status = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "proposed", "write_action": {"payload_hash": "deadbeef", "proposal_id": "p1"}},
                )
            ],
        )
    )
    assert "deadbeef" not in str(status)
    assert "payload_hash" not in str(status)


def test_case_context_status_never_exposes_more_than_the_title() -> None:
    data = translate_case_context_status("Packet loss on core router")
    assert data["label"].count("Packet loss on core router") <= 1
    assert "problem_statement" not in str(data)
    assert "case_id" not in str(data)


# --- Status deduplication (instruction section 47) --------------------------


def test_repeated_events_mapping_to_the_same_stage_emit_only_once() -> None:
    translator = StatusTranslator()
    first = translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    second = translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    third = translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))

    assert first is not None
    assert second is None
    assert third is None


def test_stage_change_after_dedup_emits_again() -> None:
    translator = StatusTranslator()
    translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    refined = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager", {"outcome": "ok", "evidence": [{"message_id": "m1"}]}
                )
            ],
        )
    )
    assert refined is not None
    assert refined["stage"] == "evidence_processing"


def test_note_external_status_participates_in_deduplication() -> None:
    translator = StatusTranslator()
    translator.note_external_status(Stage.TEAMS_CONTEXT)
    duplicate = translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    assert duplicate is None


def test_partial_function_call_argument_chunks_are_ignored() -> None:
    """Progressive function-call-argument streaming chunks (`partial=True`)
    must never drive a status on their own -- only the aggregated,
    non-partial function-call event does.
    """
    translator = StatusTranslator()
    partial_status = translator.translate_event(
        FakeEvent(final=False, partial=True, function_calls=[FakeFunctionCall("incident_manager")])
    )
    assert partial_status is None


# --- RunTraceTranslator (expandable, sanitized run trace, pre-4H) ----------


def test_incident_manager_call_produces_a_used_conversation_trace_step() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager")]))
    assert step is not None
    assert step["category"] == TraceCategory.TEAMS
    assert step["label"] == "Used the selected Teams conversation"
    assert step["status"] == TraceStepStatus.COMPLETED


def test_unrelated_events_never_produce_a_trace_step() -> None:
    translator = RunTraceTranslator()
    assert translator.translate_event(FakeEvent(text="hello", final=True)) is None
    assert translator.translate_event(FakeEvent(text="chunk", final=False, partial=True)) is None


def test_partial_function_call_chunks_never_produce_a_trace_step() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(final=False, partial=True, function_calls=[FakeFunctionCall("incident_manager")])
    )
    assert step is None


def test_incident_manager_ok_response_with_evidence_produces_a_reviewed_step() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "ok", "evidence": [{"message_id": "m1", "author": "A", "sent_at": "x"}] * 5},
                )
            ],
        )
    )
    assert step is not None
    assert step["category"] == TraceCategory.EVIDENCE
    assert step["label"] == "Reviewed 5 retrieved messages"
    assert step["safe_metadata"] == {"message_count": 5}


def test_incident_manager_ok_response_with_no_evidence_produces_no_step() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(final=False, function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "ok", "evidence": []})])
    )
    assert step is None


def test_selection_needed_response_reports_a_safe_candidate_count() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "selection_needed", "candidate_titles": ["Ops Bridge", "Ops Bridge (Archived)", "Ops"]},
                )
            ],
        )
    )
    assert step is not None
    assert step["category"] == TraceCategory.SELECTION
    assert step["label"] == "Found 3 similar Teams conversations"
    assert step["safe_metadata"] == {"candidate_count": 3}
    # Never the actual candidate titles themselves.
    assert "Ops Bridge" not in str(step)


def test_not_found_and_no_result_outcomes_produce_a_safe_warning_step() -> None:
    translator = RunTraceTranslator()
    for outcome in ("not_found", "no_result"):
        step = translator.translate_event(
            FakeEvent(final=False, function_responses=[FakeFunctionResponse("incident_manager", {"outcome": outcome})])
        )
        assert step is not None
        assert step["status"] == TraceStepStatus.WARNING


def test_error_outcome_produces_a_safe_failed_step_never_a_raw_exception() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("incident_manager", {"outcome": "error", "detail": "raw internal trace: boom"})
            ],
        )
    )
    assert step is not None
    assert step["status"] == TraceStepStatus.FAILED
    assert "boom" not in str(step)


def test_proposed_outcome_distinguishes_create_chat_from_send_message() -> None:
    translator = RunTraceTranslator()
    create_step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "proposed", "write_action": {"operation": "teams.createChat", "proposal_id": "p1"}},
                )
            ],
        )
    )
    assert create_step["label"] == "Prepared a Teams chat for review"

    send_step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "proposed", "write_action": {"operation": "teams.sendMessage", "proposal_id": "p2"}},
                )
            ],
        )
    )
    assert send_step["label"] == "Prepared a Teams message for review"


def test_proposed_step_never_claims_the_message_was_sent() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "proposed", "write_action": {"operation": "teams.sendMessage", "proposal_id": "p1"}},
                )
            ],
        )
    )
    assert "sent" not in step["label"].lower()


def test_executed_outcome_reports_the_action_actually_completed() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "executed", "write_action": {"operation": "teams.sendMessage", "status": "consumed"}},
                )
            ],
        )
    )
    assert step["label"] == "Sent the Teams message"


def test_record_case_analysis_response_maps_kind_to_a_past_tense_step() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("record_case_analysis", {"item_id": "i1", "kind": "hypothesis", "content": "x"})
            ],
        )
    )
    assert step is not None
    assert step["category"] == TraceCategory.CASE
    assert step["label"] == "Recorded a hypothesis"


def test_record_case_analysis_error_produces_a_safe_failed_step() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[FakeFunctionResponse("record_case_analysis", {"error": {"code": "not_found"}})],
        )
    )
    assert step is not None
    assert step["status"] == TraceStepStatus.FAILED


def test_trace_step_never_contains_raw_tool_arguments_or_agent_names() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_calls=[FakeFunctionCall("incident_manager", {"chat_topic": "Ops Bridge", "question": "secret"})],
        )
    )
    assert "secret" not in str(step)
    assert "chat_topic" not in str(step)
    assert "incident_manager" not in str(step)
    assert "AgentTool" not in str(step)


def test_trace_step_never_contains_a_payload_hash() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "proposed",
                        "write_action": {"operation": "teams.sendMessage", "payload_hash": "deadbeef"},
                    },
                )
            ],
        )
    )
    assert "deadbeef" not in str(step)
    assert "payload_hash" not in str(step)


def test_boundary_trace_step_helpers_are_deterministic_and_safe() -> None:
    assert case_context_loaded_trace_step()["label"] == "Loaded active case context"
    assert response_generated_trace_step()["label"] == "Generated the response"
    failed = response_failed_trace_step()
    assert failed["status"] == TraceStepStatus.FAILED
    assert selection_prepared_trace_step()["category"] == TraceCategory.SELECTION


# --- More specific, still-deterministic trace labels (pre-4H refinement) --


def test_teams_call_step_names_the_chat_when_chat_topic_is_known() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager", {"chat_topic": "Ops Bridge"})])
    )
    assert step["label"] == 'Used the "Ops Bridge" conversation'


def test_teams_call_step_falls_back_to_generic_label_without_a_chat_topic() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(FakeEvent(final=False, function_calls=[FakeFunctionCall("incident_manager", {})]))
    assert step["label"] == "Used the selected Teams conversation"


def test_teams_call_step_never_leaks_the_question_argument() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_calls=[FakeFunctionCall("incident_manager", {"chat_topic": "Ops Bridge", "question": "secret detail"})],
        )
    )
    assert "secret detail" not in str(step)


def test_evidence_reviewed_step_names_the_chat_when_chat_title_is_known() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "ok",
                        "chat_title": "Ops Bridge",
                        "evidence": [{"message_id": "m1", "author": "A", "sent_at": "x"}] * 3,
                    },
                )
            ],
        )
    )
    assert step["label"] == 'Reviewed 3 retrieved messages from "Ops Bridge"'
    assert step["safe_metadata"] == {"message_count": 3}


def test_evidence_reviewed_step_falls_back_without_a_chat_title() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "ok", "evidence": [{"message_id": "m1", "author": "A", "sent_at": "x"}]},
                )
            ],
        )
    )
    assert step["label"] == "Reviewed 1 retrieved message"


def test_proposed_send_message_step_names_the_destination_when_known() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "proposed",
                        "write_action": {
                            "operation": "teams.sendMessage",
                            "proposal_id": "p1",
                            "target_display_name": "Ops Bridge",
                        },
                    },
                )
            ],
        )
    )
    assert step["label"] == 'Prepared a message for "Ops Bridge" for review'


def test_proposed_create_chat_step_names_the_new_chat_title_when_known() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "proposed",
                        "write_action": {"operation": "teams.createChat", "title": "Incident 42 Response"},
                    },
                )
            ],
        )
    )
    assert step["label"] == 'Prepared the Teams chat "Incident 42 Response" for review'


def test_executed_send_message_step_names_the_destination_when_known() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "executed",
                        "write_action": {"operation": "teams.sendMessage", "target_display_name": "Ops Bridge"},
                    },
                )
            ],
        )
    )
    assert step["label"] == 'Sent the Teams message to "Ops Bridge"'


def test_more_specific_labels_never_leak_a_payload_hash_or_chat_id() -> None:
    translator = RunTraceTranslator()
    step = translator.translate_event(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {
                        "outcome": "proposed",
                        "write_action": {
                            "operation": "teams.sendMessage",
                            "target_display_name": "Ops Bridge",
                            "chat_id": "19:abcdef@thread.v2",
                            "payload_hash": "deadbeef",
                        },
                    },
                )
            ],
        )
    )
    assert "19:abcdef@thread.v2" not in str(step)
    assert "deadbeef" not in str(step)
